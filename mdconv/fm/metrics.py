"""Distância entre o som original (sampler SF2) e um candidato FM.

Representação comum aos dois: energia em faixas de 1/6 de oitava, relativas à altura
da nota (de f0/2 até 32·f0), quadro a quadro (10 ms), mais o envelope RMS. Faixas
relativas a f0 funcionam também para sons sem harmônicos limpos (acordes, ensembles,
sinos), onde uma comparação "harmônico a harmônico" falharia.

Tudo em dB, normalizado ao pico de cada som (o volume absoluto é ajustado depois, pelo
TL das portadoras), e limitado a -60 dB.

O custo é uma média de diferenças em dB:
* espectro: |Δ| por faixa audível (> -45 dB em um dos dois), média ponderada pela
  intensidade do quadro no original;
* envelope: |Δ| do RMS nos quadros audíveis de qualquer um dos dois.

Um custo baixo indica semelhança medida, não equivalência auditiva — a escolha final é
sempre pela escuta.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FLOOR_DB = -60.0
N_BANDS = 36                 # 6 oitavas × 6 faixas
HOP_S = 0.01
WIN_S = 0.0464               # 46 ms (2048 pontos a 44,1 kHz)
AUDIBLE_DB = -45.0          # limiar de audibilidade relativo ao pico do som


@dataclass
class Descriptor:
    bands_db: np.ndarray      # [quadros, faixas]
    env_db: np.ndarray        # [quadros]
    valid_bands: np.ndarray   # faixas abaixo de Nyquist
    air_db: np.ndarray | None = None   # [quadros] energia ACIMA das faixas (>32·f0), relativa ao quadro


def describe(x: np.ndarray, sr: float, f0: float, max_s: float | None = None) -> Descriptor:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if max_s is not None:
        x = x[: int(max_s * sr)]
    # Janela com a MESMA duração em qualquer taxa de amostragem (referência a 44,1 kHz,
    # candidato na taxa do chip): assim o vazamento espectral é igual nos dois e um som
    # idêntico dá distância ~0.
    n_win = int(round(WIN_S * sr))
    n_fft = n_win          # sem zeros extras: bins de 1/WIN_S Hz em qualquer taxa
    hop = max(1, int(round(HOP_S * sr)))
    if len(x) < n_win:
        x = np.pad(x, (0, n_win - len(x)))
    n = 1 + (len(x) - n_win) // hop
    idx = np.arange(n_win)[None, :] + hop * np.arange(n)[:, None]
    win = np.hanning(n_win).astype(np.float32)
    P = np.abs(np.fft.rfft(x[idx] * win, n=n_fft, axis=1)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    edges = f0 / 2 * 2 ** (np.arange(N_BANDS + 1) / 6)
    valid = edges[1:] < sr / 2 * 0.95
    # soma por faixa via soma acumulada (rápido)
    cs = np.concatenate([np.zeros((n, 1)), np.cumsum(P, axis=1)], axis=1)
    bi = np.searchsorted(freqs, edges)
    bi = np.clip(bi, 0, P.shape[1])
    E = cs[:, bi[1:]] - cs[:, bi[:-1]]
    # faixa estreita demais para conter um bin: usa o bin mais próximo
    narrow = (bi[1:] - bi[:-1]) == 0
    if narrow.any():
        cidx = np.clip(np.round(((edges[:-1] + edges[1:]) / 2) / (sr / n_fft)).astype(int), 0, P.shape[1] - 1)
        E[:, narrow] = P[:, cidx[narrow]]
    bdb = 10 * np.log10(E + 1e-20)
    bdb[:, ~valid] = -200
    bdb = np.maximum(bdb - bdb.max(), FLOOR_DB)
    rms = 10 * np.log10(P.sum(axis=1) + 1e-20)
    env = np.maximum(rms - rms.max(), FLOOR_DB)
    # "Ar": o que fica acima da última faixa (32·f0, no mínimo 2 kHz) até 16 kHz. As faixas
    # relativas à nota não enxergam isso em notas graves (32·55 Hz = 1,8 kHz), e é ali que
    # aparece o chiado de feedback alto / MUL alto.
    lo = max(edges[-1], 2000.0)
    hi = min(16000.0, sr / 2 * 0.95)
    if hi > lo:
        k0, k1 = np.searchsorted(freqs, [lo, hi])
        air = 10 * np.log10(cs[:, k1] - cs[:, k0] + 1e-20) - 10 * np.log10(cs[:, -1] + 1e-20)
        air = np.maximum(air, FLOOR_DB)
    else:
        air = np.full(n, FLOOR_DB)
    return Descriptor(bdb.astype(np.float32), env.astype(np.float32), valid, air.astype(np.float32))


def _pad(a: np.ndarray, n: int) -> np.ndarray:
    if len(a) >= n:
        return a[:n]
    pad_shape = (n - len(a),) + a.shape[1:]
    return np.concatenate([a, np.full(pad_shape, FLOOR_DB, dtype=a.dtype)])


@dataclass
class Cost:
    total: float
    spectrum: float
    envelope: float
    air: float = 0.0          # excesso de agudos acima das faixas (chiado), dB


W_AIR = 0.4


def distance(ref: Descriptor, cand: Descriptor, w_env: float = 0.5, w_air: float = W_AIR) -> Cost:
    n = max(len(ref.env_db), len(cand.env_db))
    re, ce = _pad(ref.env_db, n), _pad(cand.env_db, n)
    rb, cb = _pad(ref.bands_db, n), _pad(cand.bands_db, n)
    valid = ref.valid_bands & cand.valid_bands
    w = 10 ** (re / 20) + 0.02
    rv, cv = rb[:, valid], cb[:, valid]
    # Só faixas audíveis em pelo menos um dos dois sons: abaixo de -45 dB (relativo ao pico)
    # a diferença é dominada pelo vazamento da janela de análise, não pelo timbre.
    audible = np.maximum(rv, cv) > AUDIBLE_DB
    diff = np.where(audible, np.abs(rv - cv), 0.0)
    per_frame = diff.sum(axis=1) / np.maximum(audible.sum(axis=1), 1)
    has = audible.any(axis=1)
    spec = float((per_frame * w * has).sum() / max((w * has).sum(), 1e-9))
    aud = (re > -50) | (ce > -50)
    env = float(np.abs(re[aud] - ce[aud]).mean()) if aud.any() else 0.0
    air = 0.0
    if ref.air_db is not None and cand.air_db is not None:
        ra, ca = _pad(ref.air_db, n), _pad(cand.air_db, n)
        loud = ce > -40                      # só onde o candidato soa de fato
        if loud.any():
            # só o EXCESSO conta (agudo a menos já aparece nas faixas); quadros pesam pelo
            # volume do candidato, pois o chiado incomoda onde ele está audível
            wc = 10 ** (ce[loud] / 20)
            air = float((np.maximum(0.0, ca[loud] - ra[loud]) * wc).sum() / wc.sum())
    return Cost(spec + w_env * env + w_air * air, spec, env, air)
