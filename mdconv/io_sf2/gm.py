"""SoundFont General MIDI padrão, embutido no programa.

Usado em dois casos:

* o MIDI não veio com SoundFont (ex.: um MIDI General MIDI baixado da internet): o
  conversor usa este SF2 inteiro — instrumentos no banco 0 e kit de bateria no banco 128;
* o SF2 escolhido não tem um kit de bateria General MIDI de verdade (ex.: o SF2 de uma
  extração CPS1, em que o "kit" são as amostras do Oki, com bateria misturada às vozes):
  o kit do SF2 é trocado pelo kit padrão e o resto do SF2 continua valendo.

Os sons são sintetizados aqui mesmo (nenhum arquivo de terceiros), uma vez, e guardados em
``~/.mdconv/gm_padrao_v<N>.sf2``. Os instrumentos são formas de onda simples por família
General MIDI: servem de referência para a busca de patches FM, não para soar como um
sintetizador de verdade.
"""
from __future__ import annotations

import copy
import math
import re
import struct
from pathlib import Path

import numpy as np

from mdconv.i18n import tr
from mdconv.io_sf2.resolve import find_preset, resolve_voices
from mdconv.io_sf2.sf2 import Generator, SoundFont, read_sf2

VERSION = 1
RATE = 22050
KIT_NAME = "Kit padrao GM"

GM_NAMES = [
    "Acoustic Grand Piano", "Bright Piano", "Electric Grand", "Honky-tonk Piano", "Electric Piano 1",
    "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ", "Percussive Organ",
    "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Nylon Guitar", "Steel Guitar", "Jazz Guitar", "Clean Guitar", "Muted Guitar", "Overdrive Guitar",
    "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass", "Finger Bass", "Pick Bass",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin", "Viola",
    "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs",
    "Voice Oohs", "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2", "Soprano Sax", "Alto Sax",
    "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute",
    "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina", "Square Lead",
    "Sawtooth Lead", "Calliope Lead", "Chiff Lead", "Charang Lead", "Voice Lead", "Fifths Lead",
    "Bass + Lead", "New Age Pad", "Warm Pad", "Polysynth Pad", "Choir Pad", "Bowed Pad",
    "Metallic Pad", "Halo Pad", "Sweep Pad", "Rain FX", "Soundtrack FX", "Crystal FX",
    "Atmosphere FX", "Brightness FX", "Goblins FX", "Echoes FX", "Sci-fi FX", "Sitar", "Banjo",
    "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo",
    "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter",
    "Applause", "Gunshot",
]
assert len(GM_NAMES) == 128

# Peças do kit (teclas 35 a 81 do General MIDI).
DRUM_NAMES = {
    35: "Bumbo acustico", 36: "Bumbo", 37: "Aro", 38: "Caixa", 39: "Palmas", 40: "Caixa eletrica",
    41: "Tom grave 2", 42: "Chimbal fechado", 43: "Tom grave 1", 44: "Chimbal pedal", 45: "Tom medio 2",
    46: "Chimbal aberto", 47: "Tom medio 1", 48: "Tom agudo 2", 49: "Prato ataque 1", 50: "Tom agudo 1",
    51: "Prato condução 1", 52: "Prato china", 53: "Cupula", 54: "Pandeirola", 55: "Prato splash",
    56: "Cowbell", 57: "Prato ataque 2", 58: "Vibraslap", 59: "Prato condução 2", 60: "Bongo agudo",
    61: "Bongo grave", 62: "Conga abafada", 63: "Conga aguda", 64: "Conga grave", 65: "Timbal agudo",
    66: "Timbal grave", 67: "Agogo agudo", 68: "Agogo grave", 69: "Cabasa", 70: "Maracas",
    71: "Apito curto", 72: "Apito longo", 73: "Reco-reco curto", 74: "Reco-reco longo", 75: "Claves",
    76: "Bloco agudo", 77: "Bloco grave", 78: "Cuica abafada", 79: "Cuica aberta",
    80: "Triangulo abafado", 81: "Triangulo aberto",
}

# Geradores SF2 usados aqui (mesmos números do escritor da extração).
from mdconv.extract import sf2 as _w  # noqa: E402


# ============================================================================ síntese
def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(1000 + seed)


def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * RATE)) / RATE


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, len(x))


def _env(dur: float, decay: float, attack: float = 0.001) -> np.ndarray:
    t = _t(dur)
    e = np.exp(-t / max(decay, 1e-4))
    a = np.minimum(1.0, t / max(attack, 1e-4))
    return e * a


def _sweep(f0: float, f1: float, dur: float, speed: float) -> np.ndarray:
    t = _t(dur)
    f = f1 + (f0 - f1) * np.exp(-t / speed)
    return np.sin(2 * np.pi * np.cumsum(f) / RATE)


def _noise(dur: float, seed: int) -> np.ndarray:
    return _rng(seed).uniform(-1, 1, int(dur * RATE))


def _metal(dur: float, base: float, seed: int, decay: float, lo: float = 3000) -> np.ndarray:
    """Prato/chimbal: quadradas inarmônicas (como os circuitos 808) + ruído, no agudo."""
    t = _t(dur)
    ratios = (1.0, 1.342, 1.2312, 1.6532, 1.9523, 2.1523)
    x = sum(np.sign(np.sin(2 * np.pi * base * r * t + i)) for i, r in enumerate(ratios)) / 6
    x = 0.6 * x + 0.5 * _noise(dur, seed)
    return _band(x, lo, RATE / 2) * _env(dur, decay)


def _drum(key: int) -> np.ndarray:
    k = key
    if k in (35, 36):
        d = 0.45
        x = _sweep(130 if k == 36 else 110, 45, d, 0.03) * _env(d, 0.16)
        click = np.zeros_like(x)
        n = int(0.004 * RATE)
        click[:n] = _band(_noise(0.004, k), 1500, 8000)[:n] * 0.5
        return x + click
    if k in (38, 40):
        d = 0.3
        tone = _sweep(240, 180, d, 0.02) * _env(d, 0.06)
        nz = _band(_noise(d, k), 1200 if k == 38 else 2000, 9500) * _env(d, 0.09 if k == 38 else 0.07)
        return 0.55 * tone + 0.9 * nz
    if k == 37:
        d = 0.08
        return (_band(_noise(d, k), 1500, 6000) * _env(d, 0.012)
                + 0.6 * np.sin(2 * np.pi * 820 * _t(d)) * _env(d, 0.015))
    if k == 39:
        d = 0.3
        nz = _band(_noise(d, k), 900, 5000)
        e = np.zeros(int(d * RATE))
        for off in (0.0, 0.011, 0.022):
            i = int(off * RATE)
            seg = _env(d, 0.008 if off < 0.02 else 0.07)[: len(e) - i]
            e[i:i + len(seg)] += seg
        return nz * e
    if k in (41, 43, 45, 47, 48, 50):
        f = {41: 82, 43: 98, 45: 116, 47: 137, 48: 162, 50: 192}[k]
        d = 0.55
        hit = np.zeros(int(d * RATE))
        m = int(0.03 * RATE)
        hit[:m] = 0.25 * _band(_noise(0.03, k), 300, 4000)[:m] * _env(0.03, 0.01)[:m]
        return _sweep(f * 1.6, f, d, 0.04) * _env(d, 0.2) + hit
    if k == 42:
        return _metal(0.12, 320, k, 0.03, 6000)
    if k == 44:
        return _metal(0.18, 320, k, 0.05, 5500)
    if k == 46:
        return _metal(0.6, 320, k, 0.22, 6000)
    if k in (49, 57):
        return _metal(1.8, 280 if k == 49 else 300, k, 0.6, 3500)
    if k == 52:
        return _metal(1.4, 230, k, 0.45, 2500)
    if k == 55:
        return _metal(0.8, 340, k, 0.25, 4500)
    if k in (51, 59):
        d = 1.4
        bell = sum(np.sin(2 * np.pi * f * _t(d)) for f in (3100, 4420, 5250)) / 3
        return 0.5 * _metal(d, 300, k, 0.5, 5000) + 0.25 * bell * _env(d, 0.5)
    if k == 53:
        d = 1.0
        return sum(np.sin(2 * np.pi * f * _t(d)) for f in (1650, 2340, 3020, 4410)) / 4 * _env(d, 0.35)
    if k == 54:
        d = 0.3
        return _band(_noise(d, k), 5000, 10500) * _env(d, 0.08) * (1 + 0.5 * np.sin(2 * np.pi * 25 * _t(d)))
    if k == 56:
        d = 0.35
        t = _t(d)
        x = np.sign(np.sin(2 * np.pi * 562 * t)) + np.sign(np.sin(2 * np.pi * 845 * t))
        return _band(x, 400, 3000) * _env(d, 0.08) * 0.6
    if k == 58:
        d = 0.7
        return _band(_noise(d, k), 1500, 7000) * _env(d, 0.25) * (np.sin(2 * np.pi * 30 * _t(d)) > 0)
    if k in (60, 61, 62, 63, 64, 65, 66):
        f = {60: 420, 61: 300, 62: 340, 63: 300, 64: 205, 65: 470, 66: 330}[k]
        dec = {62: 0.05, 65: 0.12, 66: 0.14}.get(k, 0.1)
        d = 0.4
        x = _sweep(f * 1.15, f, d, 0.01) * _env(d, dec)
        if k in (65, 66):
            x += 0.3 * _band(_noise(d, k), 2000, 8000) * _env(d, 0.06)
        return x
    if k in (67, 68):
        d = 0.4
        f = 900 if k == 67 else 640
        return (np.sin(2 * np.pi * f * _t(d)) + 0.4 * np.sin(2 * np.pi * f * 2.7 * _t(d))) * _env(d, 0.12)
    if k in (69, 70):
        d = 0.1 if k == 69 else 0.08
        return _band(_noise(d, k), 4500, 10500) * _env(d, 0.03, 0.005)
    if k in (71, 72):
        d = 0.15 if k == 71 else 0.6
        t = _t(d)
        return np.sin(2 * np.pi * np.cumsum(2400 + 60 * np.sin(2 * np.pi * 9 * t)) / RATE) * \
            np.minimum(1, t / 0.01) * np.minimum(1, (d - t) / 0.03)
    if k in (73, 74):
        d = 0.15 if k == 73 else 0.45
        return _band(_noise(d, k), 2000, 7000) * (np.sin(2 * np.pi * 55 * _t(d)) > 0.3) * _env(d, d)
    if k == 75:
        d = 0.08
        return np.sin(2 * np.pi * 2500 * _t(d)) * _env(d, 0.02)
    if k in (76, 77):
        d = 0.1
        return np.sin(2 * np.pi * (1150 if k == 76 else 800) * _t(d)) * _env(d, 0.025)
    if k in (78, 79):
        d = 0.2 if k == 78 else 0.4
        return _sweep(620 if k == 78 else 420, 380 if k == 78 else 250, d, 0.08) * _env(d, d * 0.4, 0.01)
    if k in (80, 81):
        d = 0.25 if k == 80 else 1.6
        return sum(np.sin(2 * np.pi * f * _t(d)) for f in (4100, 5270, 6830)) / 3 * _env(d, 0.06 if k == 80 else 0.6)
    raise KeyError(key)


# Famílias General MIDI: (amplitudes dos harmônicos por n, envelope SF2)
#   env = (ataque s, decaimento s, sustentação em dB abaixo do pico, relaxamento s)
def _family(prog: int):
    fam, sub = divmod(prog, 8)
    v = sub / 7.0                         # variação dentro da família (0..1)
    n = np.arange(1, 33, dtype=float)
    odd = (n % 2 == 1)
    if fam == 0:                          # piano
        a = n ** -(1.4 - 0.3 * v) * (n <= 18)
        env = (0.002, 2.5, 30, 0.3)
    elif fam == 1:                        # percussão cromática
        a = np.where(n == 1, 1.0, 0) + np.where(n == 4, 0.3, 0) + np.where(n == 3, 0.25 * v, 0)
        env = (0.001, 0.9 + v, 60, 0.4)
    elif fam == 2:                        # órgão
        a = (np.where(n == 1, 1, 0) + np.where(n == 2, .8, 0) + np.where(n == 3, .6, 0)
             + np.where(n == 4, .5 - .2 * v, 0) + np.where(n == 6, .35, 0) + np.where(n == 8, .3, 0))
        env = (0.01, 0.1, 0, 0.08)
    elif fam == 3:                        # violão / guitarra
        a = n ** -(1.2 - 0.4 * v) * (n <= 14 + 10 * (sub >= 5))
        env = (0.002, 1.6, 35 if sub < 5 else 8, 0.15)
    elif fam == 4:                        # baixo
        a = n ** -(1.8 - 0.6 * v) * (n <= 10 + 6 * (sub >= 4))
        env = (0.003, 1.2, 25, 0.08)
    elif fam == 5:                        # cordas solo
        a = n ** -(1.0 + 0.2 * v) * (n <= 20)
        env = (0.06 if sub not in (5, 6, 7) else 0.002, 0.5 if sub in (5, 6, 7) else 0.1,
               40 if sub in (5, 6, 7) else 0, 0.25)
    elif fam == 6:                        # naipes, coro
        if sub in (4, 5, 6):
            a = np.exp(-((n - 3) ** 2) / 4) + 0.6 * (n == 1)
        else:
            a = n ** -(1.2 + 0.2 * v) * (n <= 20)
        env = (0.002 if sub == 7 else 0.15, 0.6 if sub == 7 else 0.1, 50 if sub == 7 else 0, 0.35)
    elif fam == 7:                        # metais
        a = n ** -(0.8 + 0.4 * v) * (n <= 16)
        env = (0.03, 0.2, 3, 0.12)
    elif fam == 8:                        # palhetas (sax, oboé, clarinete)
        a = n ** -(1.0 + 0.3 * v) * (n <= 14) * np.where(odd | (sub < 4), 1.0, 0.35)
        env = (0.03, 0.2, 2, 0.1)
    elif fam == 9:                        # flautas
        a = np.where(n == 1, 1, 0) + np.where(n == 2, 0.2 + 0.2 * v, 0) + np.where(n == 3, 0.1, 0)
        env = (0.05, 0.2, 2, 0.12)
    elif fam == 10:                       # synth lead
        a = (1 / n) * (odd if sub in (0, 2) else 1) * (n <= 24)
        env = (0.005, 0.2, 2, 0.08)
    elif fam == 11:                       # pads
        a = n ** -(1.3 + 0.3 * v) * (n <= 16)
        env = (0.35, 0.5, 2, 0.8)
    elif fam == 12:                       # synth fx
        a = n ** -(1.1 + 0.4 * v) * (n <= 16)
        env = (0.2, 1.5, 12, 0.8)
    elif fam == 13:                       # étnicos
        a = n ** -(0.9 + 0.5 * v) * (n <= 20)
        env = (0.002, 1.2, 30, 0.15) if sub not in (5, 6, 7) else (0.04, 0.2, 2, 0.12)
    elif fam == 14:                       # percussivos
        a = np.where(n == 1, 1, 0) + np.where(n == 2, 0.5 * v, 0) + np.where(n == 3, 0.3, 0)
        env = (0.001, 0.35 + 0.3 * v, 60, 0.2)
    else:                                 # efeitos sonoros
        a = None
        env = (0.01, 0.6, 20, 0.3)
    return a, env


def _tone(prog: int) -> tuple[np.ndarray, int, int, tuple]:
    """Forma de onda em loop, nota-raiz e correção (cents) para o programa."""
    a, env = _family(prog)
    root = 36 if prog // 8 == 4 or prog in (42, 43, 58, 70) else 60
    f = 440.0 * 2 ** ((root - 69) / 12)
    if a is None:                         # efeitos: ruído em loop (0,2 s)
        x = _band(_noise(0.2, prog), 200, 7000)
        return x, root, 0, env
    period = int(round(RATE / f))
    corr = -int(round(1200 * math.log2((RATE / period) / f)))
    cycles = 4
    t = np.arange(period * cycles) / period          # em ciclos
    nmax = int(min(len(a), (RATE / 2 * 0.45) // (RATE / period)))
    x = sum(a[i] * np.sin(2 * np.pi * (i + 1) * t + 0.3 * i) for i in range(nmax) if a[i])
    return x, root, corr, env


def _pcm16(x: np.ndarray, peak: float = 0.8) -> bytes:
    x = np.asarray(x, dtype=float)
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    if m > 0:
        x = x * (peak / m)
    return np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes()


def build_default_sf2(path: str | Path) -> Path:
    """Sintetiza e grava o SoundFont General MIDI padrão."""
    path = Path(path)
    sf = _w.SoundFont(name="GM padrao")
    # --- kit de bateria: uma zona por tecla, sem transposição
    zones = []
    for key in sorted(DRUM_NAMES):
        x = _drum(key)
        secs = len(x) / RATE
        sid = sf.add_sample(f"GM {DRUM_NAMES[key][:16]}", _pcm16(x), RATE, key, 0, None)
        zones.append(([(_w.G_KEYRANGE, (key, key)), (_w.G_RELEASE_VOL, _w.timecents(secs))], sid))
    kit = sf.add_instrument(KIT_NAME, zones)
    for prog in range(128):           # todos os kits GM/GS usam o kit padrão
        sf.add_preset(KIT_NAME if prog == 0 else f"Kit padrao {prog:03d}", 128, prog, kit)
    # --- instrumentos: banco 0
    for prog, name in enumerate(GM_NAMES):
        x, root, corr, (att, dec, sus_db, rel) = _tone(prog)
        sid = sf.add_sample(f"GM{prog:03d}", _pcm16(x, 0.7), RATE, root, corr, (0, len(x)))
        gens = [(_w.G_SAMPLE_MODES, 1), (_w.G_ATTACK_VOL, _w.timecents(att)),
                (_w.G_DECAY_VOL, _w.timecents(dec)), (_w.G_SUSTAIN_VOL, int(sus_db * 10)),
                (_w.G_RELEASE_VOL, _w.timecents(rel))]
        inst = sf.add_instrument(name[:19], [([(_w.G_KEYRANGE, (0, 127))] + gens, sid)])
        sf.add_preset(name[:19], 0, prog, inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    sf.write(str(tmp))
    tmp.replace(path)
    return path


def default_sf2_path() -> Path:
    return Path.home() / ".mdconv" / f"gm_padrao_v{VERSION}.sf2"


_cache: SoundFont | None = None


def default_soundfont() -> SoundFont:
    """O SoundFont General MIDI padrão (gerado na primeira vez)."""
    global _cache
    if _cache is None:
        p = default_sf2_path()
        if not p.is_file():
            build_default_sf2(p)
        _cache = read_sf2(p)
    return _cache


# ============================================================================ verificação
_OUR_TRACK = re.compile(r"^(FM|Oki|Faixa) \d+$")
_GAME_PRESET = re.compile(r"( (FM|Oki) \d{3}$)|(^Type \d+ @0x)")


def extracted_midi(song) -> bool:
    """MIDI gerado pela aba Extração (CPS1/GBA) ou pelo GBA Mus Ripper: tem o SF2 dele."""
    if any("SequenceRipper" in t.text or "Mus Rip" in t.text for t in song.texts):
        return True
    names = [t.name.strip() for t in song.tracks if t.channels]
    return bool(names) and all(_OUR_TRACK.match(n) for n in names)


def game_soundfont(sf: SoundFont) -> bool:
    """SF2 feito pela aba Extração a partir da ROM de um jogo."""
    named = [p for p in sf.presets if p.bank in (0, 1)]
    return bool(named) and sum(1 for p in named if _GAME_PRESET.search(p.name.strip())) >= len(named) * 0.8


def drum_problem(song, sf: SoundFont) -> str | None:
    """Motivo para trocar o kit de bateria do SF2 pelo padrão, ou None se o kit serve.

    Só vale para MIDI General MIDI (bateria no canal 10). MIDIs da extração e do GBA Mus
    Ripper ficam com o SF2 deles, que é o certo."""
    if extracted_midi(song):
        return None
    drums = [n for n in song.notes if n.channel == 9]
    if not drums:
        return None
    pm = sf.preset_map()
    progs = sorted({n.program for n in drums})
    kits = [pm.get((128, p)) or pm.get((128, 0)) for p in progs]
    if any(k is None for k in kits):
        return tr("o SoundFont não tem kit de bateria General MIDI (banco 128)")
    if any(re.search(r" Oki \d{3}$", k.name.strip()) for k in kits):
        return tr("o “kit” deste SoundFont são as amostras do Oki de um jogo CPS1 (bateria misturada com "
                  "vozes e efeitos), não um kit General MIDI")
    keys = sorted({n.key for n in drums})
    missing, samples = 0, set()
    for key in keys:
        vs = []
        for k in kits:
            vs = resolve_voices(sf, k, key, 100)
            if vs:
                break
        if not vs:
            missing += 1
        samples.update(v.sample.index for v in vs)
    if missing > max(1, len(keys) * 0.3):
        return tr("o kit de bateria deste SoundFont não tem {0} das {1} peças que a música usa").format(
            missing, len(keys))
    if len(keys) >= 3 and len(samples) <= 1:
        return tr("no kit de bateria deste SoundFont todas as peças tocam a mesma amostra")
    return None


def instruments_problem(song, sf: SoundFont) -> str | None:
    """Aviso (sem troca) quando um MIDI comum vem com o SF2 extraído de um jogo."""
    if extracted_midi(song) or not game_soundfont(sf):
        return None
    return tr("os instrumentos deste SoundFont são de um jogo extraído, não de um banco General MIDI: "
              "cada instrumento do MIDI vai soar como o timbre do jogo que tiver o mesmo número")


# ============================================================================ troca do kit
def with_default_drums(sf: SoundFont, gm: SoundFont | None = None) -> SoundFont:
    """Cópia de ``sf`` com o banco 128 substituído pelo kit padrão (o resto fica igual)."""
    from mdconv.io_sf2.sf2 import INSTRUMENT, SAMPLE_ID   # números dos geradores
    gm = gm or default_soundfont()
    data = bytearray(sf._data or b"")
    base = sf.smpl_offset if sf.smpl_offset is not None else len(data)
    if (len(data) - base) % 2:
        data += b"\0"
    shift = (len(data) - base) // 2
    gm_bytes = gm._data[gm.smpl_offset: gm.smpl_offset + gm.smpl_size]
    data += gm_bytes

    out = copy.copy(sf)
    out.smpl_offset = base
    out.smpl_size = len(data) - base
    out._data = bytes(data)
    s0, i0 = len(sf.samples), len(sf.instruments)
    samples = list(sf.samples)
    for s in gm.samples:
        c = copy.copy(s)
        c.index = s0 + s.index
        for f in ("start", "end", "loop_start", "loop_end"):
            setattr(c, f, getattr(s, f) + shift)
        samples.append(c)

    def _shift_zone(z, oper, by):
        if z is None:
            return None
        c = copy.copy(z)
        c.generators = dict(z.generators)
        g = c.generators.get(oper)
        if g is not None:
            c.generators[oper] = Generator(g.oper, g.amount + by, g.range)
        return c

    instruments = list(sf.instruments)
    for ins in gm.instruments:
        c = copy.copy(ins)
        c.index = i0 + ins.index
        c.global_zone = _shift_zone(ins.global_zone, SAMPLE_ID, s0)
        c.zones = [_shift_zone(z, SAMPLE_ID, s0) for z in ins.zones]
        instruments.append(c)
    presets = [p for p in sf.presets if p.bank != 128]
    for p in gm.presets:
        if p.bank != 128:
            continue
        c = copy.copy(p)
        c.index = len(presets)
        c.global_zone = _shift_zone(p.global_zone, INSTRUMENT, i0)
        c.zones = [_shift_zone(z, INSTRUMENT, i0) for z in p.zones]
        presets.append(c)
    out.samples, out.instruments, out.presets = samples, instruments, presets
    out.warnings = list(sf.warnings)
    return out


def prepare(song, sf: SoundFont | None) -> tuple[SoundFont, list[str], str | None]:
    """SoundFont a usar na conversão, avisos para o registro e a mensagem para a janela.

    * ``sf`` None → o SoundFont General MIDI padrão inteiro;
    * kit de bateria incompatível → o do SF2 trocado pelo kit padrão.
    """
    if sf is None:
        msg = tr("Esta música está sem SoundFont: os instrumentos e a bateria vêm do banco General MIDI "
                 "padrão do programa. A bateria usa o kit padrão; os instrumentos começam com timbres "
                 "genéricos — gere candidatos e aprove os patches FM normalmente.")
        return default_soundfont(), ["sem SoundFont: usando o banco General MIDI padrão"], msg
    notes, msg = [], None
    why = drum_problem(song, sf)
    if why:
        sf = with_default_drums(sf)
        notes.append(f"bateria: {why} → usando o kit de bateria padrão (General MIDI)")
        msg = tr("O SoundFont escolhido não é compatível com a bateria desta música: {0}.\n\n"
                 "Será usado o kit de bateria padrão do programa (General MIDI). Os outros instrumentos "
                 "continuam vindo do SoundFont.").format(why)
    other = instruments_problem(song, sf)
    if other:
        notes.append(other)
        msg = (msg + "\n\n" if msg else "") + tr(
            "Atenção: {0}. Se este MIDI não veio da aba Extração, deixe o campo SoundFont vazio "
            "para usar o banco General MIDI padrão.").format(other)
    return sf, notes, msg
