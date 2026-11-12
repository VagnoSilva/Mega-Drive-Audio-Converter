"""Kit PCM para o XGM v1: uma amostra por peça de bateria usada na música.

* Formato: 8 bits com sinal, ≈14 kHz (taxa do mixer do XGM v1 — "about 14 kHz" na spec;
  o driver do SGDK toca a 3,58 MHz / 254 ciclos ≈ 14,09 kHz — diferença inaudível).
* Cada peça é renderizada pelo sampler de referência (o som do SF2) na velocity mediana
  com que aparece na música, e reamostrada para a taxa do XGM.
* O XGM não controla volume por nota: as peças mantêm o equilíbrio entre si do original,
  e o kit inteiro é escalado para deixar margem na soma com os efeitos do jogo.
* Prioridade: peças mais fortes (bumbo/caixa costumam ser) vencem as mais fracas quando
  faltar canal PCM.
* Opcional: peças metálicas (chimbal, prato…) tocadas pelo ruído do PSG, com o envelope de
  volume da própria amostra — libera os canais PCM para bumbo e caixa e economiza ROM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

import numpy as np

from mdconv.chips.emulator import resample
from mdconv.io_sf2.resolve import find_preset
from mdconv.io_sf2.sf2 import SoundFont
from mdconv.model.song import Song
from mdconv.reference.instruments import render_note

XGM_PCM_RATE = 14000.0
ALIGN = 256


# Ruído do PSG (SN76489 da Sega), branco, taxa fixa N/512 (NF=0, ≈7 kHz de deslocamento):
# nível RMS com atenuação 0 na escala da prévia (a mesma em que o PCM entra como amostra/128).
# Medido no emulador do projeto (mdchips): −6,2 dB; o volume do PSG anda 2 dB por passo.
NOISE_FULL_DB = -6.2
NOISE_STEP_DB = 2.0
NOISE_RANGE_DB = 30.0          # 15 passos de 2 dB; o passo 15 é silêncio
NOISE_NF = 0                   # 0 = N/512 (o mais agudo dos fixos)


@dataclass
class NoisePiece:
    """Peça de bateria tocada pelo ruído do PSG em vez do PCM.

    ``att_db[k]``: atenuação do ruído no quadro k (60 Hz) para soar com o nível que a
    amostra teria no PCM, na velocity de referência ``vel_ref``."""
    name: str
    att_db: np.ndarray
    vel_ref: int
    priority: int
    nf: int = NOISE_NF


@dataclass
class Kit:
    samples: dict[int, np.ndarray] = field(default_factory=dict)      # id → int8
    ids: dict[tuple[int, int, int], int] = field(default_factory=dict)  # (banco, programa, tecla) → id
    priority: dict[int, int] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    rate: float = XGM_PCM_RATE
    headroom_db: float = 6.0
    noise: dict[tuple[int, int, int], NoisePiece] = field(default_factory=dict)
    features: dict[tuple[int, int, int], dict] = field(default_factory=dict)

    @property
    def bytes_aligned(self) -> int:
        return sum(-(-len(s) // ALIGN) * ALIGN for s in self.samples.values())


def piece_features(audio: np.ndarray, sr: int) -> dict:
    """Timbre do começo (100 ms) de uma peça: centroide espectral, fração de energia abaixo
    de 400 Hz ("corpo") e acima de 5 kHz ("chiado")."""
    y = np.asarray(audio, np.float64)
    if y.ndim > 1:
        y = y.mean(axis=1)
    seg = y[: max(64, int(0.1 * sr))]
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 8192)) ** 2
    f = np.fft.rfftfreq(8192, 1.0 / sr)
    tot = float(S.sum()) or 1e-12
    return {"centroid_hz": float((S * f).sum() / tot),
            "low": float(S[f < 400].sum() / tot),
            "high": float(S[f > 5000].sum() / tot)}


def is_metal(feat: dict) -> bool:
    """Chimbal, prato, pandeirola, chocalho…: som agudo, sem corpo grave. Nos kits do GBA as
    teclas não seguem o mapa GM, então a decisão é pelo som. Bumbo e caixa têm corpo
    (energia grave) e ficam no PCM."""
    if feat["centroid_hz"] < 3500:
        return False
    # muito agudo (mais da metade acima de 5 kHz) tolera um "toque" grave no ataque
    return (feat["low"] < 0.10 and feat["high"] >= 0.25) or (feat["low"] < 0.20 and feat["high"] >= 0.50)


def _noise_envelope(y: np.ndarray, rate: float, fps: float = 60.0) -> np.ndarray:
    """Atenuação do ruído por quadro para igualar o RMS da amostra (escala da prévia)."""
    hop = rate / fps
    n = max(1, int(np.ceil(len(y) / hop)))
    att = np.empty(n)
    for k in range(n):
        a = y[int(k * hop): int((k + 1) * hop)]
        rms = float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0
        att[k] = NOISE_FULL_DB - 20 * np.log10(max(rms, 1e-6))
    # a amostra só acaba quando cai abaixo do alcance do PSG (o resto já seria silêncio)
    loud = np.flatnonzero(att < att.min() + NOISE_RANGE_DB)
    return att[: loud[-1] + 1] if len(loud) else att[:1]


def build_kit(song: Song, sf: SoundFont, parts: list[tuple[int, int]], rate: float = XGM_PCM_RATE,
              headroom_db: float = 6.0, max_len_s: float = 1.5, sr: int = 44100,
              noise_metals: bool = False, pcm: bool = True) -> Kit:
    """``noise_metals``: peças metálicas (ver :func:`is_metal`) vão para o ruído do PSG e
    saem do PCM. ``pcm=False``: só as peças de ruído são preparadas (modo sem PCM)."""
    kit = Kit(rate=rate, headroom_db=headroom_db)
    notes = [n for n in song.notes if (n.track, n.channel) in set(parts)]
    pieces: dict[tuple[int, int, int], list[int]] = {}
    for n in notes:
        pieces.setdefault((n.bank, n.program, n.key), []).append(n.velocity)
    raw: dict[tuple[int, int, int], np.ndarray] = {}
    vref: dict[tuple[int, int, int], int] = {}
    for (bank, prog, key), vels in sorted(pieces.items()):
        preset = find_preset(sf, bank, prog).preset
        if preset is None:
            continue
        vel = int(median(vels))
        audio, voices, _ = render_note(sf, preset, key, vel, 0.3, sr)
        if not voices:
            continue
        kit.features[(bank, prog, key)] = piece_features(audio, sr)
        # o m4a toca peças de bateria inteiras: a amostra vai até o fim (ou até o limite)
        audio = audio[: int(max_len_s * sr)]
        y = resample(audio, sr, int(round(rate)))
        # corta o silêncio do fim (< -50 dB do pico da peça)
        pk = np.abs(y).max() or 1.0
        nz = np.flatnonzero(np.abs(y) > pk * 10 ** (-50 / 20))
        y = y[: nz[-1] + 1] if len(nz) else y[:1]
        # o volume da nota (velocity) entra no nível da amostra: linear, como no m4a
        raw[(bank, prog, key)] = y * (vel / 127.0)
        vref[(bank, prog, key)] = vel
    if not raw:
        return kit
    # escala única para o kit inteiro (inclusive as peças que vão para o ruído): o nível das
    # outras peças não muda quando o modo de ruído é ligado ou desligado
    peak = max(np.abs(y).max() for y in raw.values()) or 1.0
    scale = 10 ** (-headroom_db / 20) / peak
    loud = sorted(raw, key=lambda k: -np.sqrt(np.mean(raw[k] ** 2)))
    sid = 0
    for k in sorted(raw):
        prio = len(loud) - loud.index(k)                    # mais forte = maior prioridade
        name = f"{k[0]}:{k[1]} tecla {k[2]}"
        if noise_metals and is_metal(kit.features[k]):
            pcm_level = np.round(raw[k] * scale * 127) / 128.0     # como o PCM entra na prévia
            kit.noise[k] = NoisePiece(name, _noise_envelope(pcm_level, rate), vref[k], prio)
            continue
        if not pcm:
            continue
        sid += 1
        kit.samples[sid] = np.clip(np.round(raw[k] * scale * 127), -128, 127).astype(np.int8)
        kit.ids[k] = sid
        kit.priority[sid] = prio
        kit.names[sid] = name
    return kit
