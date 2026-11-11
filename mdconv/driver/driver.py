"""Driver virtual: transforma a música (notas + controles do m4a) num fluxo de escritas em
registradores do YM2612/SN76489 e comandos de PCM, quadro a quadro (60 Hz NTSC).

O mesmo fluxo é usado na prévia e (Fase 6) na exportação VGM/XGM: o que se ouve é o que
vai para o arquivo.

Alocação nota a nota:
1. FM: um canal livre; de preferência o último usado pela mesma parte (evita recarregar
   patch). Se não houver, uma nota FM de prioridade menor é deslocada: se couber no PSG,
   continua lá; senão é encurtada.
2. PSG (partes que podem transbordar, ou destinadas ao PSG): um canal livre; senão desloca
   a nota PSG de menor prioridade.
3. Sem lugar: a nota é omitida. Tudo isso entra no relatório de alterações.

Parâmetros musicais:
* Volume: lei linear do m4a (amplitude = velocity/127 × volume/127), aplicada como
  atenuação no TL das portadoras (0,75 dB por passo) e no volume do PSG (2 dB por passo).
* Pan: CC10 → esquerda (< 40), direita (> 88) ou centro. O PSG é mono.
* Pitch bend com o bend range do canal (RPN 0, padrão 2); vibrato do m4a (CC1 + NRPN 1:8)
  por software, quadro a quadro — escala provisória, como no sampler.
* PSG: envelope de volume por software, a partir do envelope do instrumento no SF2.
* PCM: uma amostra por peça do kit (ver ``mdconv.pcm.kit``); sem controle de volume no
  XGM, então a velocity das notas de bateria não muda o volume (registrado).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from mdconv.arrange.plan import PSG_MAX_KEY, PSG_MIN_KEY, Plan
from mdconv.chips.emulator import NTSC_FM_CLOCK, NTSC_PSG_CLOCK
from mdconv.fm.patch import Patch, freq_to_block_fnum, midi_to_hz
from mdconv.model.song import Note, Song
from mdconv.reference.sampler import _channel_curves, _default_curves

FPS = 60.0
TL_STEP_DB = 0.75
PSG_STEP_DB = 2.0


@dataclass
class Stream:
    """Fluxo de saída: escritas por quadro + dados de PCM."""
    frames: list[list[tuple]]            # ('fm', porta, reg, valor) | ('psg', byte) | ('pcm', canal, id, prio)
    fps: float
    loop_frame: int | None
    pcm_samples: dict[int, np.ndarray]   # id → int8 a ``pcm_rate``
    pcm_rate: float
    changes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class _Voice:
    kind: str                  # 'fm' | 'psg'
    idx: int                   # canal FM 0..5 ou PSG 0..2
    note: Note | None = None
    part: tuple | None = None
    prio: int = 0
    end_frame: int = 0
    start_frame: int = 0
    patch_id: object = None    # patch carregado (FM)
    reload: bool = False       # recarregar o patch na próxima nota (ponto de loop)
    last_tl: tuple | None = None
    last_freq: tuple | None = None
    last_pan: str | None = None
    last_vol: int | None = None
    env: dict | None = None    # envelope (PSG)
    released_at: int | None = None


def _amp_db(vel: int, vol: float, expr: float = 127.0) -> float:
    amp = (vel / 127.0) * (vol / 127.0) * (expr / 127.0)
    return -20 * np.log10(max(amp, 1e-4))


def _pan_of(cc10: float) -> str:
    return "L" if cc10 < 40 else "R" if cc10 > 88 else "C"


def _psg_period(hz: float) -> int:
    return int(min(1023, max(1, round(NTSC_PSG_CLOCK / (32.0 * hz)))))


class Driver:
    def __init__(self, song: Song, plan: Plan, patches: dict[tuple[int, int], Patch],
                 psg_envelopes: dict[tuple[int, int], dict] | None = None,
                 kit=None, fm_clock: int = NTSC_FM_CLOCK, vibrato: bool = True,
                 fm_att_db: float = 0.0, psg_att_db: float = 0.0, loop="markers"):
        """``loop``: um :class:`~mdconv.arrange.loop.LoopPoint`, ``None`` (sem loop) ou
        ``"markers"`` (lê loopStart/loopEnd do MIDI — compatibilidade)."""
        if loop == "markers":
            from mdconv.arrange.loop import resolve_loop
            loop = resolve_loop(song, "markers")
        self.loop = loop
        # Linha do tempo por canal do chip (para exportar a mesma distribuição para
        # trackers): ('on', quadro, tipo, canal, nota) · ('off', quadro, tipo, canal) ·
        # ('release', quadro, 'psg', canal) · ('pcm', quadro, canal_pcm, nota, id_amostra)
        self.timeline: list[tuple] = []
        self._frame = 0
        self.song = song
        self.plan = plan
        self.patches = patches
        self.psg_env = psg_envelopes or {}
        self.kit = kit
        self.fm_clock = fm_clock
        self.vibrato = vibrato
        self.fm_att_db = fm_att_db          # atenuação global (mixagem) do FM, em dB
        self.psg_att_db = psg_att_db
        self.curves = _channel_curves(song)
        self.changes: list[str] = []
        self.counts = {"fm": 0, "psg_planned": 0, "psg_overflow": 0, "migrated": 0, "shortened": 0,
                       "omitted": 0, "pcm": 0, "pcm_stolen": 0, "pcm_cut_tail": 0, "pcm_missing": 0, "patch_loads": 0}

    # ---------------------------------------------------------------- utilidades
    def _curve(self, n: Note):
        return self.curves.get((n.track, n.channel)) or _default_curves()

    def _cents(self, n: Note, frame: int) -> float:
        c = self._curve(n)
        t = frame / FPS
        cents = c["bend"].at(t) / 8192.0 * c["bend_range"].at(t) * 100
        if self.vibrato:
            mod = c["mod"].at(t)
            if mod > 0:
                k = frame - int(round(n.start_s * FPS))
                phase = k * c["lfos"].at(t) / 256.0
                tri = 1 - 4 * abs((phase % 1.0) - 0.5)
                cents += tri * mod * 100.0 / 256.0
        return cents

    def _level_db(self, n: Note, frame: int) -> float:
        c = self._curve(n)
        t = frame / FPS
        return _amp_db(n.velocity, c["vol"].at(t), c["expr"].at(t))

    def _when(self, frame: int) -> str:
        s = frame / FPS
        return f"{int(s // 60)}:{s % 60:05.2f}"

    # ---------------------------------------------------------------- escrita de voz FM
    def _fm_start(self, w: list, v: _Voice, n: Note, frame: int) -> None:
        part = (n.track, n.channel)
        patch = self.patches.get((n.bank, n.program))
        ch = v.idx
        port, c = (0, ch) if ch < 3 else (1, ch - 3)
        keyreg = c + (4 if ch >= 3 else 0)
        # key-off antes (se o canal estava ocupado), para reiniciar o envelope
        w.append(("fm", 0, 0x28, keyreg))
        pan = _pan_of(self._curve(n)["pan"].at(frame / FPS))
        if v.patch_id is not patch or v.reload:
            v.reload = False
            for wr in patch.channel_writes(ch, pan):
                w.append(("fm",) + wr)
            v.patch_id = patch
            v.last_tl = None
            self.counts["patch_loads"] += 1
        elif pan != v.last_pan:
            w.append(("fm", port, 0xB4 + c, {"L": 0x80, "R": 0x40, "C": 0xC0}[pan] | (patch.ams << 4) | patch.fms))
        v.last_pan = pan
        v.note, v.part, v.start_frame = n, part, frame
        self.timeline.append(("on", frame, "fm", ch, n))
        v.last_freq = None
        self._fm_update(w, v, frame)
        w.append(("fm", 0, 0x28, 0xF0 | keyreg))

    def _fm_update(self, w: list, v: _Voice, frame: int) -> None:
        n, patch, ch = v.note, v.patch_id, v.idx
        port, c = (0, ch) if ch < 3 else (1, ch - 3)
        att = int(round((self._level_db(n, frame) + self.fm_att_db) / TL_STEP_DB))
        tls = tuple(min(127, max(0, patch.ops[i].tl + att)) for i in patch.carriers)
        if tls != v.last_tl:
            from mdconv.fm.patch import SLOT_OFS
            for i, tl in zip(patch.carriers, tls):
                w.append(("fm", port, 0x40 + c + SLOT_OFS[i], tl))
            v.last_tl = tls
        hz = midi_to_hz(n.key) * 2 ** (self._cents(n, frame) / 1200) * 2.0 ** patch.octave
        block, fnum = freq_to_block_fnum(hz, self.fm_clock)
        if (block, fnum) != v.last_freq:
            w.append(("fm", port, 0xA4 + c, (block << 3) | (fnum >> 8)))
            w.append(("fm", port, 0xA0 + c, fnum & 0xFF))
            v.last_freq = (block, fnum)

    def _fm_stop(self, w: list, v: _Voice) -> None:
        ch = v.idx
        keyreg = (ch if ch < 3 else ch - 3) + (4 if ch >= 3 else 0)
        w.append(("fm", 0, 0x28, keyreg))
        if v.note is not None:
            self.timeline.append(("off", self._frame, "fm", ch))
        v.note = None
        v.part = None

    # ---------------------------------------------------------------- escrita de voz PSG
    def _psg_env_db(self, v: _Voice, frame: int) -> float:
        e = v.env or {}
        t = (frame - v.start_frame) / FPS
        att = e.get("attack_s", 0.0)
        db = 0.0
        if t < att and att > 1.0 / FPS:
            db = 20 * np.log10(max(t / att, 1e-3))
        else:
            dec = e.get("decay_s", 0.0)
            sus = e.get("sustain_db", 0.0)
            td = t - att - e.get("hold_s", 0.0)
            if td > 0 and dec > 0:
                db = max(-100.0 * td / dec, sus)
        if v.released_at is not None:
            rel = max(e.get("release_s", 0.0), 1e-3)
            db -= 100.0 * ((frame - v.released_at) / FPS) / rel
        return db

    def _psg_start(self, w: list, v: _Voice, n: Note, frame: int) -> None:
        v.note, v.part, v.start_frame = n, (n.track, n.channel), frame
        self.timeline.append(("on", frame, "psg", v.idx, n))
        v.env = self.psg_env.get((n.bank, n.program), {})
        v.released_at = None
        v.last_freq = None
        v.last_vol = None
        self._psg_update(w, v, frame)

    def _psg_update(self, w: list, v: _Voice, frame: int) -> None:
        n = v.note
        hz = midi_to_hz(n.key) * 2 ** (self._cents(n, frame) / 1200)
        per = _psg_period(hz)
        if per != v.last_freq:
            w.append(("psg", 0x80 | (v.idx << 5) | (per & 0x0F)))
            w.append(("psg", (per >> 4) & 0x3F))
            v.last_freq = per
        db = self._level_db(n, frame) + self.psg_att_db - self._psg_env_db(v, frame)
        vol = int(min(15, max(0, round(db / PSG_STEP_DB))))
        if vol != v.last_vol:
            w.append(("psg", 0x90 | (v.idx << 5) | vol))
            v.last_vol = vol

    def _psg_stop(self, w: list, v: _Voice) -> None:
        w.append(("psg", 0x9F | (v.idx << 5)))
        if v.note is not None:
            self.timeline.append(("off", self._frame, "psg", v.idx))
        v.note = None
        v.part = None
        v.released_at = None

    # ---------------------------------------------------------------- execução
    def run(self) -> Stream:
        song, plan = self.song, self.plan
        pplan = {p.key: p for p in plan.parts}
        n_fm = plan.fm_capacity
        fm = [_Voice("fm", i) for i in range(n_fm)]
        psg = [_Voice("psg", i) for i in range(plan.psg_capacity)]
        pcm_ch = list(plan.pcm_channels)
        pcm_busy: dict[int, tuple[int, int, int]] = {}  # canal → (fim, prioridade, início)
        total = int(np.ceil(song.length_s * FPS)) + 2
        frames: list[list[tuple]] = [[] for _ in range(total + int(FPS * 2))]

        # Inicialização dos chips (todos os canais em silêncio)
        init = frames[0]
        init += [("fm", 0, 0x22, 0x00), ("fm", 0, 0x27, 0x00),
                 ("fm", 0, 0x2B, 0x80 if pcm_ch else 0x00)]
        for ch in range(6):
            port, c = (0, ch) if ch < 3 else (1, ch - 3)
            init.append(("fm", 0, 0x28, c + (4 if ch >= 3 else 0)))
            for slot in (0x0, 0x4, 0x8, 0xC):
                init.append(("fm", port, 0x40 + c + slot, 0x7F))
                init.append(("fm", port, 0x80 + c + slot, 0xFF))
        init.append(("fm", 1, 0xB6, 0xC0))           # canal 6 / DAC no centro (efeitos do jogo)
        init += [("psg", b) for b in (0x9F, 0xBF, 0xDF, 0xFF)]
        loop_frame = int(round(self.loop.start_s * FPS)) if self.loop else None

        starts: dict[int, list[Note]] = {}
        for n in song.notes:
            f = int(round(n.start_s * FPS))
            starts.setdefault(f, []).append(n)

        def end_frame(n: Note) -> int:
            return max(int(round(n.start_s * FPS)) + 1, int(round(n.end_s * FPS)))

        for f in range(len(frames)):
            self._frame = f
            w = frames[f]
            if f == loop_frame:
                # Ponto de loop: esquece o estado em cache dos canais. Ao voltar do fim da
                # música para cá, cada nota recarrega patch, volume, pan e frequência.
                for v in fm + psg:
                    v.reload = True       # a nota que ainda soa continua com o patch atual
                    v.last_tl = v.last_freq = v.last_pan = v.last_vol = None
            # fins de nota
            for v in fm:
                if v.note is not None and f >= v.end_frame:
                    self._fm_stop(w, v)
            for v in psg:
                if v.note is not None and f >= v.end_frame and v.released_at is None:
                    v.released_at = f
                    self.timeline.append(("release", f, "psg", v.idx))
                if v.note is not None and v.released_at is not None:
                    if self._psg_env_db(v, f) < -30 or f - v.released_at > FPS:
                        self._psg_stop(w, v)
            for chn, (fe, _, _) in list(pcm_busy.items()):
                if f >= fe:
                    del pcm_busy[chn]
            # inícios de nota, da maior para a menor prioridade
            new = sorted(starts.get(f, []), key=lambda n: -pplan[(n.track, n.channel)].priority)
            for n in new:
                p = pplan[(n.track, n.channel)]
                ef = end_frame(n)
                if p.pool == "PCM":
                    self._start_pcm(w, n, f, pcm_ch, pcm_busy, p)
                    continue
                placed = False
                if p.pool == "FM":
                    placed = self._place_fm(w, fm, psg, n, p, f, ef, pplan)
                    if not placed and p.overflow_psg:
                        placed = self._place_psg(w, psg, n, p, f, ef, overflow=True)
                elif p.pool == "PSG":
                    placed = self._place_psg(w, psg, n, p, f, ef, overflow=False)
                if not placed:
                    self.counts["omitted"] += 1
                    self.changes.append(f"{self._when(f)} {p.label} {self._nn(n)}: omitida "
                                        f"(sem canal livre; prioridade {p.priority})")
            # atualizações contínuas (bend, vibrato, volume, envelope do PSG)
            for v in fm:
                if v.note is not None:
                    self._fm_update(w, v, f)
            for v in psg:
                if v.note is not None:
                    self._psg_update(w, v, f)

        while frames and not frames[-1]:
            frames.pop()
        stats = dict(self.counts)
        stats["notes_total"] = len(song.notes)
        stats["frames"] = len(frames)
        stats["loop_end_frame"] = int(round(self.loop.end_s * FPS)) if self.loop else None
        return Stream(frames, FPS, loop_frame,
                      self.kit.samples if self.kit else {}, self.kit.rate if self.kit else 14000.0,
                      self.changes, stats)

    @staticmethod
    def _nn(n: Note) -> str:
        from mdconv.analysis.midi_stats import note_name
        return note_name(n.key)

    def _place_fm(self, w, fm, psg, n, p, f, ef, pplan) -> bool:
        if (n.bank, n.program) not in self.patches:
            self.changes.append(f"{self._when(f)} {p.label}: sem patch FM aprovado para "
                                f"{n.bank}:{n.program} — nota omitida")
            return False
        free = [v for v in fm if v.note is None]
        if free:
            same = [v for v in free if v.patch_id is self.patches.get((n.bank, n.program)) and not v.reload]
            v = (same or free)[0]
        else:
            victim = min(fm, key=lambda v: (v.prio, -v.start_frame))
            if victim.prio >= p.priority:
                return False
            vp = pplan[victim.part]
            # a nota deslocada tenta continuar no PSG
            moved = False
            if vp.overflow_psg or vp.pool == "PSG":
                if PSG_MIN_KEY <= victim.note.key <= PSG_MAX_KEY:
                    moved = self._place_psg(w, psg, victim.note, vp, f, victim.end_frame,
                                            overflow=True, migrated=True)
            if moved:
                self.counts["migrated"] += 1
            else:
                self.counts["shortened"] += 1
                self.changes.append(f"{self._when(f)} {vp.label} {self._nn(victim.note)}: encurtada "
                                    f"para dar o canal FM a {p.label} (prioridade {p.priority} > {vp.priority})")
            self._fm_stop(w, victim)
            v = victim
        v.prio = p.priority
        v.end_frame = ef
        self._fm_start(w, v, n, f)
        self.counts["fm"] += 1
        return True

    def _place_psg(self, w, psg, n, p, f, ef, overflow: bool, migrated: bool = False) -> bool:
        if not (PSG_MIN_KEY <= n.key <= PSG_MAX_KEY):
            return False
        free = [v for v in psg if v.note is None]
        if free:
            v = free[0]
        else:
            victim = min(psg, key=lambda v: (v.prio, -v.start_frame))
            if victim.prio >= p.priority:
                return False
            self.counts["shortened"] += 1
            self.changes.append(f"{self._when(f)} C{victim.note.channel + 1:02d} {self._nn(victim.note)}"
                                f" (PSG): encurtada para {p.label}")
            self._psg_stop(w, victim)
            v = victim
        v.prio = p.priority
        v.end_frame = ef
        self._psg_start(w, v, n, f)
        if not migrated:
            self.counts["psg_overflow" if overflow else "psg_planned"] += 1
        return True

    def _start_pcm(self, w, n, f, pcm_ch, pcm_busy, p) -> None:
        if not self.kit or (n.bank, n.program, n.key) not in self.kit.ids:
            self.counts["pcm_missing"] += 1
            return
        sid = self.kit.ids[(n.bank, n.program, n.key)]
        prio = self.kit.priority[sid]
        dur = int(np.ceil(len(self.kit.samples[sid]) / self.kit.rate * FPS))
        free = [c for c in pcm_ch if c not in pcm_busy]
        if free:
            ch = free[0]
        else:
            # Como no m4a, o golpe novo vale mais que a cauda de um golpe antigo: substitui o som
            # que começou há mais tempo. Exceção: se todos começaram agora (mesmo instante) e
            # são mais importantes, o novo é que fica de fora.
            ch = min(pcm_busy, key=lambda c: pcm_busy[c][2])
            end_, prio_, start_ = pcm_busy[ch]
            if f - start_ <= 1:
                ch = min(pcm_busy, key=lambda c: pcm_busy[c][1])
                if pcm_busy[ch][1] > prio:
                    self.counts["pcm_stolen"] += 1
                    self.changes.append(f"{self._when(f)} {p.label} {self._nn(n)} (PCM): omitida — "
                                        f"{len(pcm_ch)} sons mais importantes começaram no mesmo instante")
                    return
            self.counts["pcm_cut_tail"] += 1
        pcm_busy[ch] = (f + dur, prio, f)
        w.append(("pcm", ch, sid, prio))
        self.timeline.append(("pcm", f, ch, n, sid))
        self.counts["pcm"] += 1
