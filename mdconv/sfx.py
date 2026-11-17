"""Efeitos sonoros e vozes (WAV) → PCM de 8 bits pronto para o SGDK, sem o som "rouco".

O som fica rouco na conversão comum por três motivos, e cada um tem sua etapa aqui:

1. **Aliasing**: reduzir 44,1 kHz para ~14 kHz sem cortar antes o que passa de ~7 kHz faz os
   agudos "dobrarem" para dentro da faixa audível (chiado áspero). → filtro anti-aliasing de
   alta qualidade junto com a reamostragem (polifásica, janela de Kaiser).
2. **Poucos níveis de 8 bits**: um som gravado baixo (pico em 30%) usa ~40 dos 256 níveis e o
   erro de arredondamento vira chiado grosso. → normaliza o pico e, opcionalmente, comprime a
   dinâmica para as partes fracas não caírem nos últimos níveis.
3. **DC e graves inaudíveis** gastam níveis e dão estalo. → filtro passa-altas.

Também: mono, corte do silêncio do começo e do fim (economiza ROM) com uma rampa curta para
não estalar, e dither opcional.

Saída: WAV mono de 8 bits na taxa do driver (padrão 14.000 Hz, a do XGM v1), para o rescomp
não precisar reamostrar: ``WAV nome "sfx/nome.wav" XGM`` no arquivo .res.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

XGM_RATE = 14000


@dataclass
class SfxResult:
    name: str
    src_rate: int
    src_channels: int
    src_bits: int
    src_seconds: float
    seconds: float
    rate: int
    bytes: int
    rom_bytes: int            # alinhado a 256 bytes, como no bloco de amostras do XGM
    peak_in_db: float
    gain_db: float
    trimmed_s: float
    hf_percent: float         # energia acima da metade da taxa nova (o que viraria aliasing)
    snr_db: float             # sinal / erro do 8 bits (depois do tratamento)
    snr_naive_db: float       # idem, convertendo do jeito simples (sem filtro e sem normalizar)


def read_wav(path) -> tuple[np.ndarray, int, int, int]:
    """Lê WAV PCM 8/16/24/32 bits (inteiro) ou float 32. Devolve (mono float −1..1, taxa, canais, bits)."""
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        a = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128
    elif sw == 2:
        a = np.frombuffer(raw, "<i2").astype(np.float64) / 32768
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        a = v.astype(np.float64) / 8388608
    elif sw == 4:
        a = np.frombuffer(raw, "<i4").astype(np.float64) / 2147483648
    else:
        raise ValueError(f"{Path(path).name}: WAV de {8 * sw} bits não suportado")
    a = a.reshape(-1, ch).mean(axis=1) if ch > 1 else a
    return a, sr, ch, 8 * sw


def _db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    from scipy.signal import resample_poly
    if sr_in == sr_out:
        return x.copy()
    fr = Fraction(sr_out, sr_in).limit_denominator(1000)
    # filtro anti-aliasing: Kaiser β=8.6 (~−90 dB) com corte um pouco abaixo de Nyquist
    return resample_poly(x, fr.numerator, fr.denominator, window=("kaiser", 8.6))


def _compress(x: np.ndarray, sr: int, threshold_db: float = -20.0, ratio: float = 2.5) -> np.ndarray:
    """Compressor leve (envelope RMS de 10 ms, ataque/soltura suaves): aproxima as partes fracas
    das fortes para elas usarem mais níveis de 8 bits."""
    from scipy.ndimage import uniform_filter1d
    if len(x) < 4:
        return x
    win = max(1, min(len(x), int(sr * 0.010)))
    env = np.sqrt(uniform_filter1d(x * x, win, mode="nearest") + 1e-12)
    # suaviza o ganho (soltura ~60 ms) para não "bombear"
    level_db = 20 * np.log10(env / (np.max(env) + 1e-12) + 1e-12)
    over = np.maximum(0.0, level_db - threshold_db)
    gain_db = -over * (1 - 1 / ratio)
    k = max(1, min(len(x), int(sr * 0.060)))
    gain_db = uniform_filter1d(gain_db, k, mode="nearest")
    return x * 10 ** (gain_db / 20)


def _quantize8(x: np.ndarray, dither: bool, rng=None) -> np.ndarray:
    """float −1..1 → inteiros com sinal −128..127."""
    y = x * 127.0
    if dither:
        rng = rng or np.random.default_rng(0)
        y = y + rng.uniform(-0.5, 0.5, len(y)) + rng.uniform(-0.5, 0.5, len(y))   # TPDF
    return np.clip(np.round(y), -128, 127).astype(np.int16)


def _trim(x: np.ndarray, sr: int, rel_db: float = -48.0) -> tuple[np.ndarray, float]:
    peak = np.max(np.abs(x)) if len(x) else 0
    if peak <= 0:
        return x[:1], len(x) / sr
    thr = peak * 10 ** (rel_db / 20)
    idx = np.flatnonzero(np.abs(x) > thr)
    a, b = max(0, idx[0] - int(0.002 * sr)), min(len(x), idx[-1] + int(0.010 * sr))
    y = x[a:b].copy()
    fade = min(len(y), int(0.008 * sr))                  # rampa curta no fim: sem estalo
    if fade > 1:
        y[-fade:] *= np.linspace(1, 0, fade)
    return y, (len(x) - len(y)) / sr


def process(x: np.ndarray, sr: int, rate: int = XGM_RATE, compress: bool = True, dither: bool = False,
            trim: bool = True, headroom_db: float = 0.5, fixed_gain: float | None = None):
    """Devolve (pcm 8 bits com sinal, ganho aplicado em dB, segundos cortados, % de energia acima
    de Nyquist da taxa nova)."""
    from scipy.signal import butter, sosfiltfilt
    x = x - np.mean(x)
    if len(x) > 32:
        sos = butter(2, 30.0 / (sr / 2), btype="highpass", output="sos")
        x = sosfiltfilt(sos, x)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2 if len(x) > 8 else np.zeros(1)
    freqs = np.fft.rfftfreq(len(x), 1 / sr) if len(x) > 8 else np.zeros(1)
    hf = float(spec[freqs > rate / 2].sum() / max(spec.sum(), 1e-18)) * 100
    y = _resample(x, sr, rate)
    trimmed = 0.0
    if trim:
        y, trimmed = _trim(y, rate)
    if compress:
        y = _compress(y, rate)
    peak = np.max(np.abs(y)) if len(y) else 0
    target = 10 ** (-headroom_db / 20)
    gain = fixed_gain if fixed_gain is not None else (target / peak if peak > 0 else 1.0)
    y = y * gain
    pcm = _quantize8(y, dither)
    return pcm, _db(gain), trimmed, hf


def _snr(ref: np.ndarray, pcm8: np.ndarray, scale: float) -> float:
    n = min(len(ref), len(pcm8))
    sig = ref[:n] * scale
    err = pcm8[:n] / 127.0 - sig
    return _db(np.sqrt(np.mean(sig ** 2)) / max(np.sqrt(np.mean(err ** 2)), 1e-12))


def write_wav8(path, pcm: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes((pcm + 128).astype(np.uint8).tobytes())


def convert_file(path, out_dir, rate: int = XGM_RATE, compress: bool = True, dither: bool = False,
                 trim: bool = True, preview: bool = True, fixed_gain: float | None = None) -> SfxResult:
    path = Path(path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x, sr, ch, bits = read_wav(path)
    pcm, gain_db, trimmed, hf = process(x, sr, rate, compress=compress, dither=dither, trim=trim,
                                        fixed_gain=fixed_gain)
    write_wav8(out / path.name, pcm, rate)
    if preview:
        # como vai soar: o PCM de 8 bits tocado de volta em 44,1 kHz (só para ouvir no PC)
        from scipy.signal import resample_poly
        fr = Fraction(44100, rate).limit_denominator(1000)
        pv = resample_poly(pcm / 128.0, fr.numerator, fr.denominator)
        prev = out / "previa"
        prev.mkdir(exist_ok=True)
        with wave.open(str(prev / path.name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes((np.clip(pv, -1, 1) * 32767).astype("<i2").tobytes())
    # qualidade: erro do 8 bits medido contra o sinal ideal (mesmo tratamento, sem arredondar)
    ideal_f = _ideal_float(x, sr, rate, compress, trim)
    snr = _snr(ideal_f, pcm, 10 ** (gain_db / 20))
    naive = _naive(x, sr, rate)
    ref_naive = _resample(x - np.mean(x), sr, rate)
    snr_naive = _snr(ref_naive, naive, 1.0)
    n = len(pcm)
    return SfxResult(path.name, sr, ch, bits, len(x) / sr, n / rate, rate, n, ((n + 255) // 256) * 256,
                     _db(float(np.max(np.abs(x))) if len(x) else 0), gain_db, trimmed, hf, snr, snr_naive)


def _ideal_float(x, sr, rate, compress, trim):
    from scipy.signal import butter, sosfiltfilt
    x = x - np.mean(x)
    if len(x) > 32:
        x = sosfiltfilt(butter(2, 30.0 / (sr / 2), btype="highpass", output="sos"), x)
    y = _resample(x, sr, rate)
    if trim:
        y, _ = _trim(y, rate)
    if compress:
        y = _compress(y, rate)
    return y


def _naive(x, sr, rate):
    """O jeito simples (o que deixa rouco): pega amostras sem filtro e arredonda para 8 bits sem
    ajustar o volume."""
    idx = (np.arange(int(len(x) * rate / sr)) * sr / rate).astype(int)
    return _quantize8(x[idx], dither=False)


def report_line(r: SfxResult) -> str:
    return (f"{r.name}: {r.src_rate} Hz {r.src_bits} bits {'estéreo' if r.src_channels > 1 else 'mono'} "
            f"{r.src_seconds:.2f} s → {r.rate} Hz 8 bits mono {r.seconds:.2f} s ({r.rom_bytes} bytes na ROM)"
            f" · ganho {r.gain_db:+.1f} dB · silêncio cortado {r.trimmed_s:.2f} s"
            f" · agudos filtrados {r.hf_percent:.0f}%"
            f" · relação sinal/ruído {r.snr_db:.0f} dB (conversão simples: {r.snr_naive_db:.0f} dB)")


def peak_after(path, rate: int = XGM_RATE, compress: bool = True, trim: bool = True) -> float:
    """Pico do sinal tratado, antes do ganho final (para o ganho comum de um lote)."""
    x, sr, _, _ = read_wav(path)
    y = _ideal_float(x, sr, rate, compress, trim)
    return float(np.max(np.abs(y))) if len(y) else 0.0


def convert_batch(paths, out_dir, rate: int = XGM_RATE, compress: bool = True, dither: bool = False,
                  trim: bool = True, same_gain: bool = False, headroom_db: float = 0.5, log=print):
    """Converte vários WAVs. ``same_gain``: o mesmo ganho para todos (o mais alto do lote chega ao
    máximo e os outros mantêm a proporção — um passo continua mais baixo que uma explosão)."""
    paths = [Path(p) for p in paths]
    fixed = None
    if same_gain:
        peak = max((peak_after(p, rate, compress, trim) for p in paths), default=0.0)
        fixed = 10 ** (-headroom_db / 20) / peak if peak > 0 else 1.0
        log(f"Ganho comum do lote: {_db(fixed):+.1f} dB")
    results = []
    for p in paths:
        r = convert_file(p, out_dir, rate, compress, dither, trim, fixed_gain=fixed)
        results.append(r)
        log(report_line(r))
    total = sum(r.rom_bytes for r in results)
    log(f"{len(results)} arquivo(s) → {out_dir} · {total / 1024:.0f} KB na ROM no total")
    return results
