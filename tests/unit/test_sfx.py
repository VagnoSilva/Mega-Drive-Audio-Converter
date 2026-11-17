"""Efeitos sonoros: WAV → 8 bits na taxa do driver, sem aliasing e com o volume aproveitado."""
import wave

import numpy as np

from mdconv.sfx import convert_batch, convert_file, read_wav


def _wav(path, x, sr=44100, ch=2):
    y = np.repeat(x[:, None], ch, axis=1) if ch > 1 else x
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype("<i2").tobytes())


def test_quiet_noisy_wav_gets_clean(tmp_path):
    sr = 44100
    t = np.arange(int(0.5 * sr)) / sr
    x = 0.25 * np.sin(2 * np.pi * 440 * t) * np.exp(-t * 4) + 0.05 * np.sin(2 * np.pi * 11000 * t)
    x = np.concatenate([np.zeros(sr // 10), x, np.zeros(sr // 2)])          # silêncio antes e depois
    _wav(tmp_path / "a.wav", x)
    r = convert_file(tmp_path / "a.wav", tmp_path / "o")
    y, rate, ch, bits = read_wav(tmp_path / "o" / "a.wav")
    assert (rate, ch, bits) == (14000, 1, 8)
    assert np.max(np.abs(y)) > 0.9                      # volume aproveitado
    assert r.trimmed_s > 0.5                            # silêncio cortado
    assert r.snr_db > r.snr_naive_db + 10               # bem menos ruído que a conversão simples
    spec = np.abs(np.fft.rfft(y))
    f = np.fft.rfftfreq(len(y), 1 / rate)
    # o tom de 11 kHz (acima de 7 kHz) não pode reaparecer dobrado em 3 kHz
    assert spec[(f > 2900) & (f < 3100)].max() < 0.02 * spec.max()
    assert (tmp_path / "o" / "previa" / "a.wav").exists()


def test_same_gain_keeps_balance(tmp_path):
    sr = 44100
    t = np.arange(sr // 4) / sr
    _wav(tmp_path / "forte.wav", 0.8 * np.sin(2 * np.pi * 300 * t))
    _wav(tmp_path / "fraco.wav", 0.2 * np.sin(2 * np.pi * 300 * t))
    rs = convert_batch([tmp_path / "forte.wav", tmp_path / "fraco.wav"], tmp_path / "o", compress=False,
                       same_gain=True, log=lambda m: None)
    assert abs(rs[0].gain_db - rs[1].gain_db) < 1e-6
    a = read_wav(tmp_path / "o" / "forte.wav")[0]
    b = read_wav(tmp_path / "o" / "fraco.wav")[0]
    assert 3.0 < np.max(np.abs(a)) / np.max(np.abs(b)) < 5.0
