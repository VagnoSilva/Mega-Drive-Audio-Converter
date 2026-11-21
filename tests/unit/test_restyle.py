"""Estilo de referência: copiar volume das amostras, volume do FM, algoritmo/feedback e LFO."""
import numpy as np

from mdconv import vgmconv
from mdconv.driver.driver import Stream
from mdconv.export.restyle import apply_style, read_style, style_from_stream
from mdconv.export.xgm import read_xgm, write_xgm


def _s(frames, samples=None, loop=None):
    return Stream(frames=frames, fps=60.0, loop_frame=loop, pcm_samples=samples or {}, pcm_rate=14000.0)


def _ref():
    frames = [[("fm", 0, 0x22, 0x08), ("fm", 0, 0xB0, (7 << 3) | 2), ("fm", 1, 0xB0, (5 << 3) | 5),
               ("fm", 0, 0xB4, 0xC0)], [("pcm", 1, 1, 0)]]
    return _s(frames, {1: np.array([0, 120, -124, 50], dtype=np.int8)})


def test_style_from_stream():
    st = style_from_stream(_ref(), "ref")
    assert st.sample_peak == 124 and st.lfo == 0x08
    assert st.algfb == {1: (2, 7), 4: (5, 5)}


def test_algorithm_and_feedback_follow_reference_per_channel():
    song = _s([[("fm", 0, 0xB0, (2 << 3) | 4), ("fm", 0, 0xB1, (1 << 3) | 4), ("fm", 1, 0xB0, 7)]])
    out, lines = apply_style(song, style_from_stream(_ref(), "ref"), {"algorithm"})
    assert out.frames[0] == [("fm", 0, 0xB0, (7 << 3) | 2), ("fm", 0, 0xB1, (1 << 3) | 4),   # FM2: a ref. não usa
                             ("fm", 1, 0xB0, (5 << 3) | 5)]
    assert song.frames[0][0] == ("fm", 0, 0xB0, (2 << 3) | 4)                                 # original intacto
    assert lines and "ref" in lines[0]


def test_fixed_fm_volume_uses_loudest_carrier_and_usual_modulator():
    # algoritmo 4: portadoras S2 ($48) e S4 ($4C); S1 ($40) é modulador
    f0 = [("fm", 0, 0xB0, 4), ("fm", 0, 0x40, 30), ("fm", 0, 0x48, 20), ("fm", 0, 0x4C, 20)]
    song = _s([f0, [("fm", 0, 0x48, 40), ("fm", 0, 0x4C, 10)], [("fm", 0, 0x40, 30), ("fm", 0, 0x40, 50)]])
    out, _ = apply_style(song, style_from_stream(_ref(), "ref"), {"fm_volume"})
    tl = [(w[2], w[3]) for f in out.frames for w in f if 0x40 <= w[2] <= 0x4F]
    assert all(v == 20 for r, v in tl if r == 0x48)          # a mais forte das portadoras S2
    assert all(v == 10 for r, v in tl if r == 0x4C)
    assert all(v == 30 for r, v in tl if r == 0x40)          # modulador: o valor mais comum


def test_sample_gain_and_lfo():
    song = _s([[("fm", 0, 0xB4, 0x80), ("pcm", 1, 1, 0)]], {1: np.array([0, 60, -62, 31], dtype=np.int8)})
    out, _ = apply_style(song, style_from_stream(_ref(), "ref"), {"samples", "lfo"})
    assert int(np.abs(out.pcm_samples[1].astype(int)).max()) == 124
    assert out.frames[0][0] == ("fm", 0, 0x22, 0x08)
    assert ("fm", 0, 0xB4, 0x80) in out.frames[0]            # pan mantido, AMS/FMS da ref. (0)


def test_convert_file_with_style(tmp_path):
    ref = tmp_path / "ref.xgm"
    write_xgm(_ref(), ref)
    assert read_style(ref).sample_peak == 124
    song = _s([[("fm", 0, 0xB0, 4), ("fm", 0, 0x4C, 20), ("pcm", 1, 1, 0)], [("fm", 0, 0x4C, 40)]],
              {1: np.array([0, 60, -62, 31] * 64, dtype=np.int8)}, loop=0)
    src = tmp_path / "song.xgm"
    write_xgm(song, src)
    files = vgmconv.convert_file(src, tmp_path / "out", wav=False, log=lambda *_: None,
                                 style=(ref, {"samples", "fm_volume", "algorithm", "lfo"}))
    x = read_xgm((tmp_path / "out" / "song.xgm").read_bytes())
    ws = [w for f in x.frames for w in f if w[0] == "fm"]
    assert ("fm", 0, 0xB0, (7 << 3) | 2) in ws
    assert int(np.abs(x.samples[1].astype(int)).max()) == 124
    assert any(f.suffix == ".vgm" for f in files)
