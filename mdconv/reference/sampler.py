"""Sampler SF2 de referência: toca o SoundFont como o original, para comparação A/B
e para medir as características de cada instrumento.

O que é reproduzido (tudo o que os SoundFonts do GBA Mus Ripper usam):
reamostragem pela afinação do SF2 (root key, scaleTuning, coarse/fine, correção),
loop contínuo / loop até soltar / sem loop, envelope de volume DAHDSR (ataque linear em
amplitude; decaimento e release lineares em dB, como na spec SF2), initialAttenuation,
pan do SF2 e do canal.

O que NÃO é reproduzido, e por quê:
* Filtros, LFOs e moduladores do SF2: os SoundFonts do ripper não os usam (inventário
  feito nos dois SoundFonts do projeto).
* Reverb/chorus (CC91/CC93): efeitos do player MIDI, não do GBA.
* O mixer real do GBA (saída de 8 bits, taxa de mixagem do jogo): a interpolação
  'nearest' aproxima o som "áspero" do GBA, mas não é uma emulação do hardware.

Leis de volume (escolha em ``RenderOptions.law``):
* 'm4a' (padrão): ganho = vel/127 × vol/127, pan linear — o m4a combina velocity e volume
  de forma linear.  **Fórmula derivada de documentação de terceiros; a confirmar pelo
  ouvido / agbplay.**
* 'gm': lei quadrática do General MIDI / SF2 (≈ 40·log10 dB) e pan de potência
  constante — como um player SF2 comum tocaria o MIDI do ripper.

Vibrato do m4a (CC1 = MOD, NRPN 1:8 = LFOS, gravados pelo ripper): LFO triangular com
frequência LFOS × 60 / 256 Hz e profundidade MOD × 100/256 cents.  **Escala provisória,
a confirmar pelo ouvido.**  Pode ser desligado (``vibrato=False``).

Controles de canal (volume, pan, pitch bend, MOD) são aplicados em passos de 1/60 s,
como o m4a faz (ele avança a sequência a cada quadro de vídeo).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

import numpy as np

from mdconv.io_sf2.resolve import Voice, find_preset, resolve_voices
from mdconv.io_sf2.sf2 import SoundFont
from mdconv.model.song import Song

FRAME_HZ = 60.0
NRPN_VIB_RATE = 1 * 128 + 8


@dataclass
class RenderOptions:
    sample_rate: int = 44100
    interp: str = "linear"          # 'linear' | 'nearest' (aproxima o GBA)
    law: str = "m4a"                # 'm4a' | 'gm'
    vibrato: bool = True
    gm_drum_channel: bool = False   # canal 10 = bateria GM (falso para MIDI do ripper)
    max_release_s: float = 8.0      # teto de segurança para releases muito longos


# ----------------------------------------------------------------------------- envelope
def envelope(voice: Voice, on_s: float, sr: int, max_release_s: float = 8.0) -> np.ndarray:
    """Envelope de volume (amplitude linear) com nota pressionada por ``on_s`` segundos."""
    env = voice.vol_envelope()
    sus_db = env["sustain_db"]                      # 0 a -144
    n_on = max(1, int(round(on_s * sr)))
    t = np.arange(n_on) / sr
    d, a, h, dec = env["delay_s"], env["attack_s"], env["hold_s"], env["decay_s"]
    db = np.empty(n_on)
    amp_att = np.ones(n_on)
    # delay
    in_delay = t < d
    # ataque: rampa linear de amplitude
    ta = t - d
    in_att = (~in_delay) & (ta < a)
    amp_att[in_att] = ta[in_att] / a if a > 0 else 1.0
    amp_att[in_delay] = 0.0
    # decaimento: -100 dB por "decay_s", parando no sustain
    td = t - d - a - h
    db[:] = 0.0
    in_dec = td > 0
    rate = 100.0 / dec if dec > 0 else np.inf
    db[in_dec] = np.maximum(-td[in_dec] * rate, sus_db)
    on_env = amp_att * 10 ** (db / 20)
    # release: -100 dB por "release_s" a partir do nível atual
    level = on_env[-1]
    rel_s = min(env["release_s"], max_release_s)
    if level <= 1e-5:
        return on_env
    level_db = 20 * np.log10(level)
    rel_rate = 100.0 / max(rel_s, 1e-4)
    n_rel = int(np.ceil(min((level_db + 100) / rel_rate, max_release_s) * sr)) + 1
    tr = np.arange(1, n_rel + 1) / sr
    rel = 10 ** ((level_db - tr * rel_rate) / 20)
    return np.concatenate([on_env, rel])


# ----------------------------------------------------------------------------- sample playback
def _positions(step: np.ndarray, start: float, loop: tuple[int, int] | None,
               loop_until: int | None) -> np.ndarray:
    """Posição de leitura (em pontos da amostra) para cada amostra de saída.

    ``loop``: (ls, le) em pontos, já relativos ao início do sample.
    ``loop_until``: índice de saída a partir do qual o loop deixa de valer (modo 3).
    """
    pos = start + np.concatenate([[0.0], np.cumsum(step[:-1])])
    if loop is None:
        return pos
    ls, le = loop
    L = le - ls
    if L <= 0:
        return pos
    n_loop = len(pos) if loop_until is None else min(loop_until, len(pos))
    p = pos[:n_loop].copy()          # cópia: ``pos`` (sem loop) ainda é usado para a cauda
    over = p >= le
    p[over] = ls + np.mod(p[over] - ls, L)
    if 0 < n_loop < len(pos):
        # Após soltar (modo 3): continua linearmente a partir de onde estava no loop.
        tail = pos[n_loop:] - pos[n_loop - 1] + p[-1]
        return np.concatenate([p, tail])
    return p if n_loop else pos


def play_sample(voice: Voice, sf: SoundFont, n_out: int, sr: int, on_samples: int,
                pitch_cents: np.ndarray | None = None, interp: str = "linear") -> np.ndarray:
    data = sf.sample_data(voice.sample).astype(np.float32) / 32768.0
    st, en = voice.sample_bounds
    s0 = voice.sample.start
    lo, hi = max(st - s0, 0), min(en - s0, len(data))
    base = voice.playback_rate / sr
    step = np.full(n_out, base, dtype=np.float64)
    if pitch_cents is not None:
        step *= 2.0 ** (pitch_cents[:n_out] / 1200.0)
    loop = None
    if voice.loop_mode in (1, 3):
        ls, le = voice.loop_points
        ls, le = ls - s0, le - s0
        if lo <= ls < le <= hi:
            loop = (ls, le)
    until = on_samples if voice.loop_mode == 3 else None
    pos = _positions(step, float(lo), loop, until)
    out = np.zeros(n_out, dtype=np.float32)
    idx = np.arange(n_out)
    if loop is None:
        valid = pos <= hi - 1                       # sem loop: toca até o fim do sample
    elif until is None:
        valid = np.ones(n_out, bool)                # loop contínuo
    else:
        valid = (idx < until) | (pos <= hi - 1)     # loop até soltar, depois vai ao fim
    p = pos[valid]
    if interp == "nearest":
        out[valid] = data[np.minimum(p.astype(np.int64), hi - 1)]
    else:
        i0 = p.astype(np.int64)
        frac = (p - i0).astype(np.float32)
        i1 = i0 + 1
        if loop is not None:
            ls, le = loop
            looping = np.ones(len(p), bool) if until is None else (idx[valid] < until)
            i1 = np.where((i1 >= le) & looping, i1 - (le - ls), i1)
        i1 = np.minimum(i1, hi - 1)
        i0 = np.minimum(i0, hi - 1)
        out[valid] = data[i0] * (1 - frac) + data[i1] * frac
    return out


def render_voice(voice: Voice, sf: SoundFont, on_s: float, sr: int = 44100,
                 pitch_cents: np.ndarray | None = None, interp: str = "linear",
                 max_release_s: float = 8.0) -> np.ndarray:
    """Uma voz (sample + envelope + atenuação), mono, com a nota pressionada por ``on_s``."""
    env = envelope(voice, on_s, sr, max_release_s)
    n = len(env)
    on_samples = max(1, int(round(on_s * sr)))
    sig = play_sample(voice, sf, n, sr, on_samples, pitch_cents, interp)
    gain = 10 ** (-voice.g("initialAttenuation") / 200.0)
    out = sig * env.astype(np.float32) * np.float32(gain)
    # Corta o silêncio do fim (amostra sem loop terminou antes do envelope).
    nz = np.flatnonzero(np.abs(out) > 1e-6)
    return out[: nz[-1] + 1] if len(nz) else out[:1]


# ----------------------------------------------------------------------------- canal / música
@dataclass
class _Curve:
    """Valor de controle por canal ao longo do tempo (função degrau)."""
    times: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    default: float = 0.0

    def add(self, t: float, v: float) -> None:
        self.times.append(t)
        self.values.append(v)

    def at(self, t: float) -> float:
        i = bisect.bisect_right(self.times, t) - 1
        return self.values[i] if i >= 0 else self.default

    def frames(self, t0: float, n_frames: int) -> np.ndarray:
        ts = t0 + np.arange(n_frames) / FRAME_HZ
        if not self.times:
            return np.full(n_frames, self.default)
        idx = np.searchsorted(self.times, ts, side="right") - 1
        vals = np.asarray(self.values)
        return np.where(idx >= 0, vals[np.maximum(idx, 0)], self.default)


def _default_curves() -> dict[str, _Curve]:
    # Padrões: volume 100 e pan central (GM/m4a), bend range 2 semitons (m4a e GM).
    # LFOS 22 é o padrão do m4a (só importa se houver MOD > 0 sem LFOS gravado).
    return {"vol": _Curve(default=100), "expr": _Curve(default=127), "pan": _Curve(default=64),
            "bend": _Curve(default=0), "bend_range": _Curve(default=2),
            "mod": _Curve(default=0), "lfos": _Curve(default=22)}


def _channel_curves(song: Song) -> dict[tuple[int, int], dict[str, _Curve]]:
    curves: dict[tuple[int, int], dict[str, _Curve]] = {}

    def get(tr, ch):
        return curves.setdefault((tr, ch), _default_curves())
    # Estado de canal é compartilhado entre faixas, mas os MIDI do ripper usam uma faixa;
    # agrupamos por canal e replicamos para cada faixa que o usa.
    by_ch: dict[int, list] = {}
    for e in song.channel_events:
        by_ch.setdefault(e.channel, []).append(e)
    tracks_of = {}
    for n in song.notes:
        tracks_of.setdefault(n.channel, set()).add(n.track)
    for ch, evs in by_ch.items():
        for tr in tracks_of.get(ch, {evs[0].track}):
            c = get(tr, ch)
            for e in sorted(evs, key=lambda e: e.tick):
                if e.kind == "cc":
                    key = {7: "vol", 11: "expr", 10: "pan", 1: "mod"}.get(e.number)
                    if key:
                        c[key].add(e.seconds, e.value)
                elif e.kind == "pitch_bend":
                    c["bend"].add(e.seconds, e.value)
                elif e.kind == "rpn" and e.number == 0:
                    c["bend_range"].add(e.seconds, e.value)
                elif e.kind == "nrpn" and e.number == NRPN_VIB_RATE:
                    c["lfos"].add(e.seconds, e.value)
    return curves


def _frame_to_samples(frames: np.ndarray, n: int, sr: int) -> np.ndarray:
    idx = np.minimum((np.arange(n) * FRAME_HZ / sr).astype(np.int64), len(frames) - 1)
    return frames[idx]


@dataclass
class RenderReport:
    notes_rendered: int = 0
    notes_silent: int = 0               # sem preset/zona no SF2 (mantidas como silêncio)
    silent_detail: dict[str, int] = field(default_factory=dict)
    peak_before_normalize: float = 0.0
    normalize_gain_db: float = 0.0


def render_song(song: Song, sf: SoundFont, opt: RenderOptions | None = None,
                only_channels: set[int] | None = None, normalize: bool = True
                ) -> tuple[np.ndarray, RenderReport]:
    """Renderiza a música com o SF2 (estéreo, float32). ``only_channels``: solo de canais."""
    opt = opt or RenderOptions()
    sr = opt.sample_rate
    curves = _channel_curves(song)
    total = int((song.length_s + opt.max_release_s) * sr) + 1
    mix = np.zeros((total, 2), dtype=np.float32)
    rep = RenderReport()
    preset_cache: dict[tuple[int, int, bool], object] = {}
    end_used = 0
    for n in song.notes:
        if only_channels is not None and n.channel not in only_channels:
            continue
        drum = opt.gm_drum_channel and n.channel == 9
        pk = (n.bank, n.program, drum)
        if pk not in preset_cache:
            preset_cache[pk] = find_preset(sf, n.bank, n.program, drum_channel=drum).preset
        preset = preset_cache[pk]
        voices = resolve_voices(sf, preset, n.key, n.velocity) if preset else []
        if not voices:
            rep.notes_silent += 1
            k = f"canal {n.channel + 1} programa {n.bank}:{n.program}"
            rep.silent_detail[k] = rep.silent_detail.get(k, 0) + 1
            continue
        c = curves.get((n.track, n.channel)) or _default_curves()
        on_s = max(n.sustain_end_s - n.start_s, 1.0 / sr)
        max_len = on_s + opt.max_release_s
        n_frames = int(np.ceil(max_len * FRAME_HZ)) + 2
        bend = c["bend"].frames(n.start_s, n_frames) / 8192.0 * c["bend_range"].frames(n.start_s, n_frames) * 100
        cents = bend
        if opt.vibrato:
            mod = c["mod"].frames(n.start_s, n_frames)
            if np.any(mod > 0):
                lfos = c["lfos"].frames(n.start_s, n_frames)
                phase = np.cumsum(lfos / 256.0)                      # ciclos, por quadro
                tri = 1 - 4 * np.abs(np.mod(phase, 1.0) - 0.5)       # -1..1
                cents = cents + tri * mod * 100.0 / 256.0
        vol = c["vol"].frames(n.start_s, n_frames) / 127.0
        expr = c["expr"].frames(n.start_s, n_frames) / 127.0
        pan = c["pan"].frames(n.start_s, n_frames)
        vel = n.velocity / 127.0
        if opt.law == "gm":
            gain_f = (vel ** 2) * (vol ** 2) * (expr ** 2)
        else:
            gain_f = vel * vol * expr
        start = int(round(n.start_s * sr))
        for v in voices:
            n_est = int(max_len * sr) + sr          # folga: o envelope nunca passa disso
            pc = _frame_to_samples(cents, n_est, sr)
            sig = render_voice(v, sf, on_s, sr, pc, opt.interp, opt.max_release_s)
            m = len(sig)
            g = _frame_to_samples(gain_f, m, sr).astype(np.float32)
            p = np.clip((_frame_to_samples(pan, m, sr) - 64) / 64.0 + v.g("pan") / 500.0, -1, 1)
            if opt.law == "gm":
                ang = (p + 1) * np.pi / 4
                l, r = np.cos(ang), np.sin(ang)
            else:
                l, r = np.minimum(1, 1 - p), np.minimum(1, 1 + p)
            seg = sig * g
            e = min(start + m, total)
            mix[start:e, 0] += (seg * l.astype(np.float32))[: e - start]
            mix[start:e, 1] += (seg * r.astype(np.float32))[: e - start]
            end_used = max(end_used, e)
        rep.notes_rendered += 1
    mix = mix[: max(end_used, int(song.length_s * sr) + 1)]
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    rep.peak_before_normalize = peak
    if normalize and peak > 0:
        g = 10 ** (-1 / 20) / peak                      # pico em -1 dBFS
        mix *= np.float32(g)
        rep.normalize_gain_db = 20 * np.log10(g)
    return mix, rep


def write_wav(path, audio: np.ndarray, sr: int) -> None:
    import wave
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        a = a[:, None]
    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(a.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav(path) -> tuple[np.ndarray, int]:
    import wave
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    return (raw.reshape(-1, ch) if ch > 1 else raw), sr
