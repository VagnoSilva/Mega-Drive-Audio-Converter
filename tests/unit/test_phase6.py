"""Fase 6: exportação VGM 1.71 e XGM v1 com leitura de volta."""
import struct

import numpy as np
import pytest

from mdconv.driver.driver import Stream
from mdconv.export.common import export_frames
from mdconv.export.validate import check_xgm, export_and_validate
from mdconv.export.vgm import write_vgm
from mdconv.export.vgm_reader import read_vgm, play_vgm
from mdconv.export.xgm import read_xgm, write_xgm, XgmError


def make_stream(n_frames=120, loop=None, loop_end=None, pcm=True):
    frames = [[] for _ in range(n_frames)]
    frames[0] = [("fm", 0, 0x22, 0), ("fm", 0, 0x27, 0), ("fm", 0, 0x2B, 0x80), ("fm", 1, 0xB6, 0xC0),
                 ("fm", 0, 0xB0, 7), ("fm", 0, 0xB4, 0xC0),
                 ("fm", 0, 0x30, 1), ("fm", 0, 0x34, 1), ("fm", 0, 0x38, 1), ("fm", 0, 0x3C, 1),
                 ("fm", 0, 0x40, 0x7F), ("fm", 0, 0x44, 0x7F), ("fm", 0, 0x48, 0x7F), ("fm", 0, 0x4C, 0),
                 ("fm", 0, 0x50, 31), ("fm", 0, 0x54, 31), ("fm", 0, 0x58, 31), ("fm", 0, 0x5C, 31),
                 ("fm", 0, 0x80, 0x0F), ("fm", 0, 0x84, 0x0F), ("fm", 0, 0x88, 0x0F), ("fm", 0, 0x8C, 0x0F),
                 ("fm", 0, 0xA4, 0x22), ("fm", 0, 0xA0, 0x69), ("fm", 0, 0x28, 0xF0),
                 ("psg", 0x80 | 0x0E), ("psg", 0x0F), ("psg", 0x90)]
    frames[30] = [("fm", 0, 0x28, 0x00), ("psg", 0x9F)]
    samples = {}
    if pcm:
        rng = np.random.default_rng(1)
        samples = {1: (rng.standard_normal(3000) * 30).astype(np.int8),
                   2: (rng.standard_normal(700) * 30).astype(np.int8)}
        frames[10].append(("pcm", 1, 1, 2))
        frames[40].append(("pcm", 2, 2, 1))
        frames[41].append(("pcm", 1, 2, 1))
    stats = {"loop_end_frame": loop_end}
    return Stream(frames=frames, fps=60, loop_frame=loop, pcm_samples=samples, pcm_rate=14000,
                  changes=[], stats=stats)


def test_xgm_roundtrip_identical(tmp_path):
    s = make_stream(loop=20, loop_end=100)
    p = tmp_path / "a.xgm"
    info = write_xgm(s, p)
    n, loop = check_xgm(s, p)
    assert n == 100 and loop == 20
    assert info.filtered_writes == 2                       # $27 e $2B ficam com o driver
    data = p.read_bytes()
    assert data[:4] == b"XGM " and data[0x102] == 1 and data[0x103] == 0
    # tabela: id 1 no endereço 0, 3000 bytes → 12 blocos de 256; id 2 logo depois
    assert struct.unpack_from("<HH", data, 4) == (0, 12)
    assert struct.unpack_from("<HH", data, 8) == (12, 3)
    assert struct.unpack_from("<HH", data, 12) == (0xFFFF, 0x0001)
    assert struct.unpack_from("<H", data, 0x100)[0] == 15


def test_xgm_commands_encoding(tmp_path):
    s = make_stream(pcm=True)
    p = tmp_path / "b.xgm"
    write_xgm(s, p)
    x = read_xgm(p.read_bytes())
    pcm = [(i, w) for i, f in enumerate(x.frames) for w in f if w[0] == "pcm"]
    # canal 1 → bits 0–1 = 0; prioridade única da música = 3
    assert pcm[0] == (10, ("pcm", 1, 1, 3))
    assert pcm[2] == (41, ("pcm", 1, 2, 3))
    assert x.loop_frame is None
    data = p.read_bytes()
    assert data[-1] == 0x7F                                # sem loop: termina com $7F
    # key on/off sai como $4X, não como escrita comum no $28
    assert all(not (w[0] == "fm" and w[2] == 0x28 and w[1] == 1) for f in x.frames for w in f)


def test_xgm_loop_offset_points_to_loop_frame(tmp_path):
    s = make_stream(loop=37, loop_end=90)
    p = tmp_path / "c.xgm"
    write_xgm(s, p)
    data = p.read_bytes()
    # como no xgmtool: ... comandos do último quadro, $7E + offset, $00 (sem quadro mudo extra)
    assert data[-5] == 0x7E and data[-1] == 0x00
    x = read_xgm(data)
    assert x.loop_frame == 37
    assert len(x.frames) == 90                             # nenhum quadro extra no loop


def test_xgm_rejects_invalid(tmp_path):
    s = make_stream()
    s.frames[5].append(("pcm", 5, 1, 0))
    with pytest.raises(XgmError):
        write_xgm(s, tmp_path / "d.xgm")
    s = make_stream()
    s.pcm_samples = {i: np.zeros(10, np.int8) for i in range(1, 65)}
    with pytest.raises(XgmError):
        write_xgm(s, tmp_path / "e.xgm")


def test_loop_end_truncates_and_silences():
    s = make_stream(n_frames=200, loop=20, loop_end=100)
    frames, loop = export_frames(s)
    assert len(frames) == 100 and loop == 20
    last = frames[-1]
    assert sum(1 for w in last if w[0] == "fm" and w[2] == 0x28) == 6
    assert sum(1 for w in last if w[0] == "psg") == 4


def test_vgm_structure_and_loop(tmp_path):
    s = make_stream(loop=20, loop_end=100)
    p = tmp_path / "a.vgm"
    info = write_vgm(s, p, title="teste")
    v = read_vgm(p.read_bytes())
    assert v.version == 0x171
    assert v.total_samples == 100 * 735
    assert v.loop_samples == 80 * 735
    assert v.ym2612_clock == 7670453 and v.sn76489_clock == 3579545
    assert v.sn_feedback == 0x0009 and v.sn_width == 16
    assert v.gd3["title"] == "teste"
    # o loop cai exatamente no tempo do quadro 20
    t_loop = v.commands[v.loop_command_index][0]
    assert t_loop >= 20 * 735
    assert not any(c[1][0] == "fm" and c[1][2] == 0x2A for c in v.commands)
    # banco PCM = mistura dos canais (sem sinal)
    assert len(v.data_bank) == info.pcm_bytes > 0


def test_vgm_pcm_bank_is_the_mix(tmp_path):
    s = make_stream(loop=None)
    p = tmp_path / "b.vgm"
    write_vgm(s, p)
    v = read_vgm(p.read_bytes())
    bank = np.frombuffer(v.data_bank, np.uint8).astype(np.int16) - 128
    start = round(10 / 60 * 14000)
    np.testing.assert_array_equal(bank[start:start + 100], s.pcm_samples[1][:100])


def test_vgm_plays_and_matches_writes(tmp_path):
    s = make_stream(pcm=False)
    p = tmp_path / "c.vgm"
    write_vgm(s, p)
    y = play_vgm(read_vgm(p.read_bytes()))
    on = y[int(0.1 * 44100):int(0.4 * 44100)]
    off = y[int(0.8 * 44100):int(1.0 * 44100)]
    assert np.sqrt((on ** 2).mean()) > 10 * np.sqrt((off ** 2).mean()) + 1e-6


def test_export_and_validate(tmp_path):
    s = make_stream(loop=20, loop_end=100)
    lines = export_and_validate(s, tmp_path, "m", {"xgm", "vgm"})
    assert (tmp_path / "m.xgm").exists() and (tmp_path / "m.vgm").exists()
    assert any("idêntico quadro a quadro" in l for l in lines)
    assert any("SOUND_PCM_CH1" in l for l in lines)


def test_xgm_reader_accepts_xgmtool_empty_entries(tmp_path):
    # o xgmtool do SGDK marca entradas vazias como FFFF/0000 (nós usamos FFFF/0001)
    s = make_stream(pcm=True)
    p = tmp_path / "f.xgm"
    write_xgm(s, p)
    data = bytearray(p.read_bytes())
    for i in range(63):
        if data[4 + 4 * i:8 + 4 * i] == b"\xff\xff\x01\x00":
            data[6 + 4 * i] = 0
    x = read_xgm(bytes(data))
    assert set(x.samples) == set(s.pcm_samples)
