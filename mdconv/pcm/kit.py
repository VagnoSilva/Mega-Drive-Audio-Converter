"""Kit PCM para o XGM v1: uma amostra por peça de bateria usada na música.

* Formato: 8 bits com sinal, ≈14 kHz (taxa do mixer do XGM v1 — "about 14 kHz" na spec;
  o valor exato será conferido com o SGDK do usuário na Fase 6b).
* Cada peça é renderizada pelo sampler de referência (o som do SF2) na velocity mediana
  com que aparece na música, e reamostrada para a taxa do XGM.
* O XGM não controla volume por nota: as peças mantêm o equilíbrio entre si do original,
  e o kit inteiro é escalado para deixar margem na soma com os efeitos do jogo.
* Prioridade: peças mais fortes (bumbo/caixa costumam ser) vencem as mais fracas quando
  faltar canal PCM.
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


@dataclass
class Kit:
    samples: dict[int, np.ndarray] = field(default_factory=dict)      # id → int8
    ids: dict[tuple[int, int, int], int] = field(default_factory=dict)  # (banco, programa, tecla) → id
    priority: dict[int, int] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    rate: float = XGM_PCM_RATE
    headroom_db: float = 6.0

    @property
    def bytes_aligned(self) -> int:
        return sum(-(-len(s) // ALIGN) * ALIGN for s in self.samples.values())


def build_kit(song: Song, sf: SoundFont, parts: list[tuple[int, int]], rate: float = XGM_PCM_RATE,
              headroom_db: float = 6.0, max_len_s: float = 1.5, sr: int = 44100) -> Kit:
    kit = Kit(rate=rate, headroom_db=headroom_db)
    notes = [n for n in song.notes if (n.track, n.channel) in set(parts)]
    pieces: dict[tuple[int, int, int], list[int]] = {}
    for n in notes:
        pieces.setdefault((n.bank, n.program, n.key), []).append(n.velocity)
    raw: dict[tuple[int, int, int], np.ndarray] = {}
    for (bank, prog, key), vels in sorted(pieces.items()):
        preset = find_preset(sf, bank, prog).preset
        if preset is None:
            continue
        vel = int(median(vels))
        audio, voices, _ = render_note(sf, preset, key, vel, 0.3, sr)
        if not voices:
            continue
        # o m4a toca peças de bateria inteiras: a amostra vai até o fim (ou até o limite)
        audio = audio[: int(max_len_s * sr)]
        y = resample(audio, sr, int(round(rate)))
        # corta o silêncio do fim (< -50 dB do pico da peça)
        pk = np.abs(y).max() or 1.0
        nz = np.flatnonzero(np.abs(y) > pk * 10 ** (-50 / 20))
        y = y[: nz[-1] + 1] if len(nz) else y[:1]
        # o volume da nota (velocity) entra no nível da amostra: linear, como no m4a
        raw[(bank, prog, key)] = y * (vel / 127.0)
    if not raw:
        return kit
    peak = max(np.abs(y).max() for y in raw.values()) or 1.0
    scale = 10 ** (-headroom_db / 20) / peak
    loud = sorted(raw, key=lambda k: -np.sqrt(np.mean(raw[k] ** 2)))
    for sid, k in enumerate(sorted(raw), start=1):
        kit.samples[sid] = np.clip(np.round(raw[k] * scale * 127), -128, 127).astype(np.int8)
        kit.ids[k] = sid
        kit.priority[sid] = len(loud) - loud.index(k)        # mais forte = maior prioridade
        kit.names[sid] = f"{k[0]}:{k[1]} tecla {k[2]}"
    return kit
