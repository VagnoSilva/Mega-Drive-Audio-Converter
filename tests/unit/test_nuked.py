"""Nuked-OPN2 (LGPL) como motor alternativo de prévia."""
import numpy as np

from mdconv.chips.emulator import MegaDriveSound
from mdconv.chips.nuked import NukedYM2612
from mdconv.fm.patch import Operator, Patch, midi_to_hz, render_note


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)


def test_same_level_and_pitch_as_own_emulator():
    p = Patch(alg=4, fb=3, ops=[Operator(mul=2, tl=30, ar=31, d1r=5, sl=3, rr=8),
                                Operator(mul=1, tl=0, ar=31, d1r=3, sl=2, d2r=1, rr=8),
                                Operator(mul=1, tl=40, ar=31, d1r=5, sl=3, rr=8),
                                Operator(mul=1, tl=4, ar=31, d1r=3, sl=2, d2r=1, rr=8)])
    a = render_note(p, midi_to_hz(57), 0.5, 44100, chip=MegaDriveSound(discrete_ym2612=True))
    b = render_note(p, midi_to_hz(57), 0.5, 44100, chip=NukedYM2612())
    a, b = (x if x.ndim == 1 else x[:, 0] for x in (a, b))
    n = min(len(a), len(b))
    assert abs(rms_db(a[:n]) - rms_db(b[:n])) < 0.5
    seg = slice(2000, 2000 + 8192)
    fa = np.argmax(np.abs(np.fft.rfft(a[seg] * np.hanning(8192))))
    fb = np.argmax(np.abs(np.fft.rfft(b[seg] * np.hanning(8192))))
    assert abs(int(fa) - int(fb)) <= 1


def test_silence_after_reset():
    x = NukedYM2612().render(4000)
    assert np.ptp(x) < 1e-3          # só o deslocamento DC do DAC, sem ruído
