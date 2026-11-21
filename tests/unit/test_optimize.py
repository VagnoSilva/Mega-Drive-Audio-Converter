"""Redução de tamanho do XGM: o que sai, o que nunca sai, e o arquivo continua válido."""
import numpy as np
import pytest

from mdconv.driver.driver import Stream
from mdconv.export import optimize
from mdconv.export.optimize import optimize_stream
from mdconv.export.xgm import read_xgm, write_xgm


def _stream(frames, samples=None, loop=None):
    return Stream(frames=frames, fps=60.0, loop_frame=loop, pcm_samples=samples or {}, pcm_rate=14000.0)


def _tone(n, amp=100, decay=0.0, f=440.0):
    t = np.arange(n) / 14000.0
    return (amp * np.sin(2 * np.pi * f * t) * np.exp(-decay * t)).astype(np.int8)


def test_repeated_fm_and_psg_writes_are_removed():
    f = [("fm", 0, 0x40, 10), ("psg", 0x90 | 5), ("psg", 0x80 | 3), ("psg", 0x12)]
    s, lines = optimize_stream(_stream([list(f), list(f), [("fm", 0, 0x40, 11)]]), "max")
    assert s.frames[1] == []
    assert s.frames[2] == [("fm", 0, 0x40, 11)]
    assert any("4" in x for x in lines)


def test_key_on_dac_timer_and_noise_are_never_removed():
    f = [("fm", 0, 0x28, 0xF0), ("fm", 0, 0x2B, 0x80), ("fm", 0, 0x27, 0), ("psg", 0xE4)]
    s, _ = optimize_stream(_stream([list(f), list(f)]), "max")
    assert s.frames[1] == f


def test_frequency_latch_pairs_go_together():
    pair = [("fm", 0, 0xA4, 0x22), ("fm", 0, 0xA0, 0x69)]
    other = [("fm", 0, 0xA4, 0x22), ("fm", 0, 0xA0, 0x70)]
    s, _ = optimize_stream(_stream([list(pair), list(pair), list(other)]), "max")
    assert s.frames[1] == []
    assert s.frames[2] == other           # o latch igual não sai sozinho


def test_state_is_forgotten_at_loop_frame():
    f = [("fm", 0, 0x40, 10)]
    s, _ = optimize_stream(_stream([list(f), list(f), list(f)], loop=1), "max")
    assert s.frames == [f, f, []]


def test_identical_samples_are_stored_once_and_ids_remapped():
    a = _tone(2000)
    frames = [[("pcm", 0, 1, 0)], [("pcm", 1, 2, 0)], [("pcm", 0, 0, 0)]]
    s, _ = optimize_stream(_stream(frames, {1: a, 2: a.copy()}), "max")
    assert len(s.pcm_samples) == 1
    assert s.frames[0][0][2] == s.frames[1][0][2] == 1
    assert s.frames[2][0][2] == 0         # parar o canal continua 0


def test_modes_get_smaller_and_compact_caps_length():
    long_ = _tone(14000, 120, decay=4.0)
    soft = (long_.astype(np.float32) * 0.6).astype(np.int8)
    frames = [[("pcm", 0, 1, 0)], [("pcm", 0, 2, 0)]]
    sizes = {}
    for m in optimize.MODES:
        s, _ = optimize_stream(_stream(frames, {1: long_, 2: soft}), m)
        sizes[m] = sum(len(x) for x in s.pcm_samples.values())
        if m == "compact":
            assert all(len(x) <= int(0.55 * 14000) for x in s.pcm_samples.values())
            assert len(s.pcm_samples) == 1    # mesma forma a −4,4 dB: viram uma
    assert sizes["max"] >= sizes["balanced"] >= sizes["compact"]


@pytest.mark.parametrize("m", list(optimize.MODES))
def test_xgm_still_valid(tmp_path, m):
    frames = [[("fm", 0, 0x40, 1), ("pcm", 1, 1, 0)], [("fm", 0, 0x40, 1)], [("pcm", 1, 2, 0)]]
    st = _stream(frames, {1: _tone(3000), 2: _tone(3000, 60, f=880.0)}, loop=1)
    s, _ = optimize_stream(st, m)
    p = tmp_path / "t.xgm"
    write_xgm(s, str(p))
    x = read_xgm(p.read_bytes())
    assert x.loop_frame == 1
    assert x.frames[1] == [("fm", 0, 0x40, 1)]    # no loop a escrita fica (estado esquecido)


def test_set_mode_rejects_unknown():
    old = optimize.mode()
    try:
        optimize.set_mode("xyz")
        assert optimize.mode() == "balanced"
    finally:
        optimize.set_mode(old)
