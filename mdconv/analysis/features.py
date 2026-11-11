"""Características sonoras de uma nota renderizada, usadas para guiar e avaliar a
reconstrução FM (Fase 3).

Todas as medidas partem do áudio, com a altura esperada (``f0_hz``) conhecida pela nota
MIDI e pela afinação do SF2. Nada aqui afirma que dois sons são equivalentes: são
medidas para ordenar candidatos, e a decisão final é pela escuta.

Medidas:
* f0 medida e desvio em cents em relação à esperada;
* amplitude dos harmônicos 1..N ao longo do tempo (dB relativo ao pico global);
* razão harmônico/ruído (quanto da energia está fora dos harmônicos);
* envelope RMS em dB e ADSR estimado: ataque (10%→90% do pico), decaimento até o
  patamar, nível de sustentação, tempo de release após o note-off;
* centroide espectral em múltiplos de f0 (brilho) e sua trajetória;
* vibrato: taxa (Hz) e profundidade (cents) a partir da f0 quadro a quadro;
* transiente: energia de ruído nos primeiros 30 ms em relação ao corpo.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


def midi_to_hz(key: float) -> float:
    return 440.0 * 2 ** ((key - 69) / 12)


@dataclass
class Envelope:
    peak_db: float
    attack_s: float            # 10% → 90% do pico (em amplitude)
    peak_time_s: float
    sustain_db: float          # nível do patamar relativo ao pico (dB); -inf se decai a zero
    decay_s: float | None      # pico → patamar (ou → -40 dB se não há patamar)
    decay_rate_db_s: float     # inclinação média após o pico (dB/s; negativo)
    release_s: float | None    # note-off → -60 dB do nível no note-off
    looped_like: bool          # tem patamar estável (sample com loop)


@dataclass
class Features:
    sr: int
    f0_expected_hz: float
    f0_measured_hz: float | None
    detune_cents: float | None
    n_harmonics: int
    frame_times_s: list[float]
    harmonics_db: list[list[float]]        # [quadro][harmônico], dB relativo ao pico global
    harmonics_mean_db: list[float]         # média no corpo da nota
    noise_ratio: float                     # 0 = só harmônicos, 1 = só ruído
    centroid_harm: list[float]             # centroide em múltiplos de f0, por quadro
    centroid_mean: float
    centroid_hz: float                     # centroide espectral médio em Hz
    spectral_flatness: float               # 0 = tonal, 1 = ruído branco
    envelope: Envelope
    rms_db: list[float]
    vibrato_rate_hz: float | None
    vibrato_depth_cents: float | None
    attack_noise_ratio: float
    harmonicity: float = 0.0              # fração da energia nos harmônicos de f0 (0–1)
    notes: list[str] = field(default_factory=list)

    def to_dict(self, compact: bool = True) -> dict:
        d = asdict(self)
        if compact:
            d.pop("harmonics_db")
            d.pop("rms_db")
            d.pop("frame_times_s")
            d["centroid_harm"] = d["centroid_harm"][:: max(1, len(d["centroid_harm"]) // 20)]
        return d


def f0_harmonic_sieve(x: np.ndarray, sr: int, approx_hz: float, search_cents: float = 100.0,
                      seg_s: float = 0.5) -> tuple[float | None, float]:
    """f0 perto da esperada que melhor explica os picos do espectro (peneira harmônica).

    Usa o espectro médio de até ``seg_s`` a partir do pico do sinal. Procura em
    ±``search_cents`` em torno de ``approx_hz`` (o SF2 diz qual deveria ser a altura;
    queremos medir o desvio, não adivinhar a nota). Funciona com fundamental fraca
    (ex.: marimba com energia no 4º harmônico).

    Retorna (f0, fração da energia explicada pelos harmônicos de f0). Fração baixa
    = som de ensemble/inarmônico; nesse caso f0 = None.
    """
    hop = max(1, int(sr * 0.005))
    n = len(x) // hop
    if n < 4:
        return None, 0.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    start = int(np.argmax(rms)) * hop
    seg = x[start: start + int(seg_s * sr)]
    if len(seg) < int(0.05 * sr):
        seg = x[: int(seg_s * sr)]
    n_fft = int(2 ** np.ceil(np.log2(max(len(seg), 8192))))
    P = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n_fft)) ** 2
    bin_hz = sr / n_fft
    nyq = sr / 2
    cents = np.arange(-search_cents, search_cents + 1, 1.0)
    cand = approx_hz * 2 ** (cents / 1200)
    kmax = max(1, int(min(nyq * 0.9, 8000) / approx_hz))
    k = np.arange(1, kmax + 1)
    fk = cand[:, None] * k[None, :]
    idx = np.clip(np.round(fk / bin_hz).astype(np.int64), 1, len(P) - 2)
    # energia nos 3 bins em torno de cada harmônico
    e = (P[idx - 1] + P[idx] + P[idx + 1]).sum(axis=1)
    i = int(np.argmax(e))
    if 0 < i < len(e) - 1:
        y0, y1, y2 = np.log(e[i - 1] + 1e-30), np.log(e[i] + 1e-30), np.log(e[i + 1] + 1e-30)
        den = y0 - 2 * y1 + y2
        sh = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    else:
        sh = 0.0
    f0 = approx_hz * 2 ** ((cents[i] + sh) / 1200)
    # Refino: pico interpolado dos harmônicos mais fortes, média de f_k/k ponderada pela energia
    ests, wts = [], []
    for kk in k:
        c = int(round(kk * f0 / bin_hz))
        lo, hi = max(1, c - 2), min(len(P) - 2, c + 2)
        if hi <= lo:
            continue
        j = lo + int(np.argmax(P[lo:hi + 1]))
        a, b, cc = np.log(P[j - 1] + 1e-30), np.log(P[j] + 1e-30), np.log(P[j + 1] + 1e-30)
        den = a - 2 * b + cc
        dd = float(np.clip(0.5 * (a - cc) / den, -0.5, 0.5)) if den != 0 else 0.0
        ests.append((j + dd) * bin_hz / kk)
        wts.append(P[j])
    if ests:
        order = np.argsort(wts)[::-1][:6]
        f_ref = float(np.average(np.asarray(ests)[order], weights=np.asarray(wts)[order]))
        # Aceita o refino dentro da faixa de busca: em notas muito graves os 3 bins somados
        # cobrem dezenas de cents e a etapa grossa fica imprecisa; o pico interpolado não.
        if abs(1200 * np.log2(f_ref / approx_hz)) <= search_cents:
            f0 = f_ref
    # fração explicada: energia dentro de ±2% de cada harmônico / energia total até kmax·f0
    top = min(len(P), int(kmax * f0 * 1.02 / bin_hz) + 2)
    fr = np.arange(top) * bin_hz
    harm = np.zeros(top, bool)
    for kk in k:
        harm |= np.abs(fr - kk * f0) <= max(0.02 * kk * f0, 1.5 * bin_hz)
    total = P[1:top].sum() + 1e-30
    explained = float(P[1:top][harm[1:]].sum() / total)
    return (f0 if explained >= 0.5 else None), explained


def _stft(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    win = np.hanning(n_fft).astype(np.float32)
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    return np.fft.rfft(x[idx] * win, axis=1)


def _rms_db(x: np.ndarray, sr: int, hop_s: float = 0.005) -> tuple[np.ndarray, np.ndarray]:
    hop = max(1, int(sr * hop_s))
    n = max(1, len(x) // hop)
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-20)
    return np.arange(n) * hop / sr, 20 * np.log10(rms)


def measure_envelope(x: np.ndarray, sr: int, note_off_s: float) -> tuple[Envelope, np.ndarray]:
    t, db = _rms_db(x, sr)
    peak_i = int(np.argmax(db))
    peak_db = float(db[peak_i])
    amp = 10 ** ((db - peak_db) / 20)
    # Ataque 10% → 90%
    pre = amp[: peak_i + 1]
    i10 = int(np.argmax(pre >= 0.1))
    i90 = int(np.argmax(pre >= 0.9))
    attack = max(0.0, float(t[i90] - t[i10]))
    # Corpo: do pico ao note-off
    off_i = min(int(note_off_s / (t[1] - t[0] if len(t) > 1 else 1)), len(db) - 1)
    body = db[peak_i: max(off_i, peak_i + 1)] - peak_db
    decay_rate = 0.0
    if len(body) > 4:
        tt = np.arange(len(body)) * (t[1] - t[0])
        decay_rate = float(np.polyfit(tt, body, 1)[0])
    # Patamar: últimos 30% do corpo variam menos de 1,5 dB e estão acima de -40 dB
    looped = False
    sustain = -np.inf
    decay_s = None
    if len(body) >= 10:
        tail = body[int(len(body) * 0.7):]
        if np.ptp(tail) < 1.5 and np.mean(tail) > -40:
            looped = True
            sustain = float(np.mean(tail))
            reach = np.flatnonzero(body <= sustain + 1.0)
            decay_s = float(reach[0] * (t[1] - t[0])) if len(reach) else 0.0
        else:
            reach = np.flatnonzero(body <= -40)
            decay_s = float(reach[0] * (t[1] - t[0])) if len(reach) else None
    # Release: do note-off até cair 60 dB abaixo do nível no note-off
    rel = None
    if off_i < len(db) - 1:
        lvl = db[off_i]
        after = np.flatnonzero(db[off_i:] <= lvl - 60)
        rel = float(after[0] * (t[1] - t[0])) if len(after) else float((len(db) - off_i) * (t[1] - t[0]))
    env = Envelope(peak_db, attack, float(t[peak_i]), sustain, decay_s, decay_rate, rel, looped)
    return env, db


def analyze_note(x: np.ndarray, sr: int, f0_hz: float, note_off_s: float,
                 n_harmonics: int = 24, hop_s: float = 0.01, pitched: bool = True) -> Features:
    """``pitched=False`` (peças de bateria): não mede altura nem vibrato; os harmônicos
    são relativos à altura nominal do sample e servem só como descrição grosseira."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    notes: list[str] = []
    # Janela com pelo menos 4 períodos da fundamental, para separar harmônicos
    n_fft = int(2 ** np.ceil(np.log2(max(2048, 4 * sr / f0_hz))))
    hop = max(1, int(sr * hop_s))
    S = np.abs(_stft(x, n_fft, hop))
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    bin_hz = sr / n_fft
    nyq_h = int((sr / 2 - bin_hz) // f0_hz)
    nh = max(1, min(n_harmonics, nyq_h))
    if nh < n_harmonics:
        notes.append(f"só {nh} harmônicos abaixo de Nyquist")

    # f0 medida: pico do 1º harmônico (interpolação parabólica) no quadro mais forte
    frame_energy = S.sum(axis=1)
    loud = int(np.argmax(frame_energy))

    def peak_freq(spec, approx):
        lo = max(1, int((approx * 0.97) / bin_hz))
        hi = min(len(spec) - 2, int(np.ceil(approx * 1.03 / bin_hz)) + 1)
        if hi <= lo:
            return None, 0.0
        k = lo + int(np.argmax(spec[lo:hi + 1]))
        a, b, c = np.log(spec[k - 1] + 1e-12), np.log(spec[k] + 1e-12), np.log(spec[k + 1] + 1e-12)
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        d = float(np.clip(d, -0.5, 0.5))          # interpolação nunca sai do bin vizinho
        return (k + d) * bin_hz, spec[k]

    f0m, harmonicity = f0_harmonic_sieve(x, sr, f0_hz) if pitched else (None, 0.0)
    detune = 1200 * np.log2(f0m / f0_hz) if f0m else None
    if pitched and f0m is None:
        notes.append(f"só {harmonicity:.0%} da energia é harmônica: som de ensemble/inarmônico "
                     "(várias vozes desafinadas ou ruído); altura não medida")
    f0 = f0m or f0_hz

    # Harmônicos por quadro: máximo numa janela de ±3% em torno de k·f0
    H = np.zeros((len(S), nh), dtype=np.float32)
    harm_mask = np.zeros(len(freqs), bool)
    for k in range(1, nh + 1):
        lo = max(0, int(k * f0 * 0.97 / bin_hz))
        hi = min(len(freqs) - 1, int(np.ceil(k * f0 * 1.03 / bin_hz)))
        seg = S[:, max(lo - 1, 0):min(hi + 2, len(freqs))]
        j = np.clip(np.argmax(seg[:, 1:-1], axis=1) + 1, 1, seg.shape[1] - 2)
        r = np.arange(len(S))
        a = np.log(seg[r, j - 1] + 1e-12)
        b = np.log(seg[r, j] + 1e-12)
        c = np.log(seg[r, j + 1] + 1e-12)
        den = a - 2 * b + c
        d = np.clip(np.where(den != 0, 0.5 * (a - c) / np.where(den != 0, den, 1), 0.0), -0.5, 0.5)
        # Pico interpolado (parabólico em log): corrige a perda entre bins da janela Hann
        H[:, k - 1] = np.nan_to_num(np.exp(b - 0.25 * (a - c) * d), nan=0.0, posinf=0.0)
        harm_mask[max(0, lo - 1):hi + 2] = True
    ref = H.max() + 1e-12
    Hdb = 20 * np.log10(H / ref + 1e-9)

    power = S ** 2
    body = frame_energy > frame_energy.max() * 1e-3
    total_p = power[body].sum() + 1e-20
    noise_p = power[body][:, ~harm_mask].sum()
    noise_ratio = float(noise_p / total_p)

    hk = np.arange(1, nh + 1)
    cen = (H * hk).sum(axis=1) / (H.sum(axis=1) + 1e-12)
    pw_body = power[body] if body.any() else power
    spec_mean = pw_body.mean(axis=0)
    centroid_hz = float((spec_mean * freqs).sum() / (spec_mean.sum() + 1e-20))
    sm = spec_mean[1:] + 1e-20
    flatness = float(np.exp(np.mean(np.log(sm))) / np.mean(sm))
    times = np.arange(len(S)) * hop / sr

    env, rms_db = measure_envelope(x, sr, note_off_s)

    # Vibrato: f0 por quadro (no corpo da nota), FFT da variação entre 2 e 12 Hz
    vib_rate = vib_depth = None
    strong = frame_energy > frame_energy.max() * 10 ** (-20 / 10)
    body_idx = np.flatnonzero(strong & (times < note_off_s))
    if pitched and len(body_idx) >= int(0.6 / hop_s):
        f_track = []
        for i in body_idx:
            f, _ = peak_freq(S[i], f0)
            f_track.append(f if f else np.nan)
        f_track = np.asarray(f_track)
        f_track = np.where(np.abs(1200 * np.log2(np.maximum(f_track, 1e-6) / f0)) <= 100, f_track, np.nan)
        ok = np.isfinite(f_track)
        if ok.sum() >= int(0.6 / hop_s):
            cents = 1200 * np.log2(f_track[ok] / np.nanmedian(f_track[ok]))
            cents = cents - np.polyval(np.polyfit(np.arange(len(cents)), cents, 1), np.arange(len(cents)))
            spec = np.abs(np.fft.rfft(cents * np.hanning(len(cents))))
            fr = np.fft.rfftfreq(len(cents), hop_s)
            band = (fr >= 2) & (fr <= 12)
            if band.any():
                j = np.flatnonzero(band)[int(np.argmax(spec[band]))]
                depth = float(np.std(cents) * np.sqrt(2))
                if depth >= 3.0:
                    vib_rate, vib_depth = float(fr[j]), depth

    # Transiente: fração de ruído nos primeiros 30 ms do som
    first = np.flatnonzero(body)
    attack_noise = 0.0
    if len(first):
        a0 = first[0]
        a1 = a0 + max(1, int(0.03 / hop_s))
        pa = power[a0:a1]
        attack_noise = float(pa[:, ~harm_mask].sum() / (pa.sum() + 1e-20))

    body_frames = body & (times < max(note_off_s, hop_s))
    hmean = (Hdb[body_frames].mean(axis=0) if body_frames.any() else Hdb.mean(axis=0))
    return Features(
        sr=sr, f0_expected_hz=f0_hz, f0_measured_hz=f0m, detune_cents=detune, n_harmonics=nh,
        frame_times_s=times.tolist(), harmonics_db=Hdb.tolist(),
        harmonics_mean_db=[round(float(v), 2) for v in hmean],
        noise_ratio=noise_ratio, centroid_harm=[round(float(v), 3) for v in cen],
        centroid_mean=float(np.nanmean(cen[body])) if body.any() else float(np.nanmean(cen)),
        centroid_hz=centroid_hz, spectral_flatness=flatness,
        envelope=env, rms_db=[round(float(v), 2) for v in rms_db],
        vibrato_rate_hz=vib_rate, vibrato_depth_cents=vib_depth,
        attack_noise_ratio=attack_noise, harmonicity=harmonicity, notes=notes,
    )
