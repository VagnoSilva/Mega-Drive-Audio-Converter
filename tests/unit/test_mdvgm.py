"""VGM que já é do Mega Drive (YM2612 + SN76489) → XGM/VGM/.fur, com amostras do DAC e loop."""
import struct

import numpy as np
import pytest

from mdconv import mdvgm, vgmconv
from mdconv.export.vgm_reader import read_vgm as read_out_vgm  # noqa: F401  (só garante o import)


def _vgm(body: bytes, loop_at: int | None = None, total: int = 0, gd3_title="Teste") -> bytes:
    """VGM 1.60 com YM2612 e SN76489 nos clocks NTSC. ``loop_at``: posição do loop no corpo."""
    head = bytearray(0x100)
    gd3 = "\0".join([gd3_title, "", "Jogo", "", "Sega Mega Drive / Genesis", "", "", "", "", "", ""]) + "\0"
    gd3b = gd3.encode("utf-16-le")
    gd3blk = b"Gd3 " + struct.pack("<II", 0x100, len(gd3b)) + gd3b
    data = 0x100
    struct.pack_into("<4sIIII", head, 0, b"Vgm ", data + len(body) + len(gd3blk) - 4, 0x160, 3579545, 0)
    struct.pack_into("<I", head, 0x14, data + len(body) - 0x14)
    struct.pack_into("<I", head, 0x18, total)
    if loop_at is not None:
        struct.pack_into("<I", head, 0x1C, data + loop_at - 0x1C)
    struct.pack_into("<I", head, 0x2C, 7670453)
    struct.pack_into("<I", head, 0x34, data - 0x34)
    return bytes(head) + body + gd3blk


def _wait(n):
    out = b""
    while n > 0:
        k = min(n, 65535)
        out += b"\x61" + struct.pack("<H", k)
        n -= k
    return out


def _dpcm_block(values):
    """Bloco 0x40 com DPCM de 4 bits e a tabela 0x7F correspondente."""
    deltas = [0, 1, 2, 4, 8, 16, 32, 64, 128, 255, 254, 252, 248, 240, 224, 192]
    table = bytes([1, 0, 8, 4]) + struct.pack("<H", 16) + bytes(deltas)
    cur, codes = 0x80, []
    for v in values:
        d = (v - cur) & 0xFF
        code = deltas.index(d)
        codes.append(code)
        cur = v
    if len(codes) % 2:
        codes.append(0)
    packed = bytes((codes[i] << 4) | codes[i + 1] for i in range(0, len(codes), 2))
    blk = bytes([1]) + struct.pack("<I", len(values)) + bytes([8, 4, 0]) + struct.pack("<H", 0x80) + packed
    return (b"\x67\x66\x7F" + struct.pack("<I", len(table)) + table
            + b"\x67\x66\x40" + struct.pack("<I", len(blk)) + blk)


def _song(tmp_path, loop=True, compressed=False):
    kick = bytes(int(128 + 100 * np.sin(i / 3) * np.exp(-i / 300)) for i in range(1600))
    snare = bytes([0x80, 0x90, 0xA0, 0x90, 0x80, 0x70, 0x60, 0x70] * 100)
    blocks = b""
    if compressed:
        blocks += _dpcm_block([0x80, 0x81, 0x83, 0x87, 0x8F, 0x87, 0x83, 0x81] * 50)
    else:
        blocks += b"\x67\x66\x00" + struct.pack("<I", len(kick)) + kick
    blocks += b"\x67\x66\x00" + struct.pack("<I", len(snare)) + snare
    setup = (b"\x52\x2B\x80"                               # DAC ligado (sai do XGM)
             b"\x52\x27\x40"                               # modo do canal 3 + timers
             b"\x52\xB0\x32\x52\x40\x10\x52\xA4\x22\x52\xA0\x69"
             b"\x90\x00\x02\x00\x2A" b"\x91\x00\x00\x01\x00" b"\x92\x00" + struct.pack("<I", 8000))
    beat = b""
    for i in range(4):
        beat += b"\x52\x28\xF0" + (b"\x95\x00" + struct.pack("<H", i % 2) + b"\x00")
        beat += b"\x50\x90" + _wait(735 * 10) + b"\x52\x28\x00" + b"\x94\x00" + b"\x50\x9F" + _wait(735 * 5)
    body = blocks + setup + _wait(735 * 30)                 # meio segundo de silêncio antes
    loop_at = len(body)
    body += beat + b"\x66"
    total = 735 * 30 + 4 * 735 * 15
    p = tmp_path / "md.vgm"
    p.write_bytes(_vgm(body, loop_at if loop else None, total))
    return p


@pytest.mark.parametrize("compressed", [False, True])
def test_reads_fm_psg_and_dac_streams(tmp_path, compressed):
    p = _song(tmp_path, compressed=compressed)
    assert vgmconv.detect_chip(p) == "Mega Drive"
    v = mdvgm.read_vgm(p)
    starts = [h for h in v.hits if h[1] is not None]
    assert len(starts) == 4 and all(h[2] == 8000 for h in starts)
    assert len([h for h in v.hits if h[1] is None]) == 4             # paradas (0x94)
    if compressed:
        assert starts[0][1][:8] == bytes([0x80, 0x81, 0x83, 0x87, 0x8F, 0x87, 0x83, 0x81])
    st, rep = mdvgm.convert(v)
    assert st.loop_frame == 0                                        # silêncio do início removido
    assert len(st.pcm_samples) == 2
    # 8000 Hz → 14 kHz: a amostra fica 1,75× mais longa
    assert abs(len(st.pcm_samples[2]) - 800 * 14000 / 8000) <= 2
    flat = [w for f in st.frames for w in f]
    assert not any(w[0] == "fm" and w[2] in (0x24, 0x25, 0x26, 0x2A, 0x2B) for w in flat)
    assert ("fm", 0, 0x27, 0x40) in flat
    pcm = [w for w in flat if w[0] == "pcm"]
    assert [w[2] for w in pcm if w[2]] == [1, 2, 1, 2] and sum(1 for w in pcm if w[2] == 0) == 4
    # quadros: cada batida dura 15 quadros
    on = [i for i, f in enumerate(st.frames) if any(w[0] == "pcm" and w[2] for w in f)]
    assert on == [0, 15, 30, 45]


def test_outputs_and_loop_edit(tmp_path):
    p = _song(tmp_path, loop=False)
    files = vgmconv.convert_file(p, tmp_path / "out", log=lambda *_: None, wav=False,
                                 loop_start=0.25, loop_end=0.75, pcm_channel=3)
    names = sorted(f.name for f in files)
    assert names == ["md.fur", "md.vgm", "md.xgm"]
    v = mdvgm.read_vgm(p)
    st, rep = mdvgm.convert(v, pcm_channel=3, loop_start=0.25, loop_end=0.75)
    assert len(st.frames) == 45 and st.loop_frame == 15
    assert all(w[1] == 3 for f in st.frames for w in f if w[0] == "pcm")
    assert any("loop: volta para 0.25 s" in r for r in rep)
    # o XGM gravado volta no quadro escolhido
    from mdconv.export.xgm import read_xgm
    info = read_xgm((tmp_path / "out" / "md.xgm").read_bytes())
    assert info.loop_frame == 15


def test_loop_edit_on_ay_vgm_path(tmp_path):
    """O loop escolhido também vale nas outras conversões diretas."""
    from mdconv.driver.driver import Stream
    st = Stream(frames=[[] for _ in range(600)], fps=60.0, loop_frame=None, pcm_samples={}, pcm_rate=14000.0)
    rep = []
    mdvgm.apply_loop(st, 2.0, 8.0, rep)
    assert len(st.frames) == 480 and st.loop_frame == 120
    st2 = Stream(frames=[[] for _ in range(600)], fps=60.0, loop_frame=500, pcm_samples={}, pcm_rate=14000.0)
    mdvgm.apply_loop(st2, None, 5.0, rep)
    assert st2.loop_frame is None and len(st2.frames) == 300


def test_other_chips_still_rejected(tmp_path):
    d = bytearray(_vgm(b"\x66"))
    struct.pack_into("<I", d, 0x30, 3579545)            # YM2151
    p = tmp_path / "x.vgm"
    p.write_bytes(bytes(d))
    with pytest.raises(ValueError, match="YM2151"):
        vgmconv.detect_chip(p)


def test_xgm_input_gives_vgm_and_fur(tmp_path):
    """Um XGM pronto (do SGDK ou de outro programa) → XGM reduzido, VGM, .fur e prévia; o XGM
    original nunca é sobrescrito."""
    from mdconv.export.xgm import read_xgm, write_xgm
    v = mdvgm.read_vgm(_song(tmp_path))
    st, _ = mdvgm.convert(v, pcm_channel=2)
    src = tmp_path / "intro.xgm"
    write_xgm(st, src)
    assert vgmconv.detect_chip(src) == "XGM"
    out = tmp_path / "out"
    files = vgmconv.convert_file(src, out, log=lambda *_: None, wav=False)
    assert sorted(f.name for f in files) == ["intro.fur", "intro.vgm", "intro.xgm"]
    back = mdvgm.read_vgm(out / "intro.vgm")
    assert back.loop is not None and abs(back.total / 44100 - len(st.frames) / 60) < 0.05
    files = vgmconv.convert_file(src, tmp_path / "out2", log=lambda *_: None, wav=False, loop_start=0.5)
    assert "intro.xgm" in [f.name for f in files]
    assert read_xgm((tmp_path / "out2" / "intro.xgm").read_bytes()).loop_frame == 30
    before = src.read_bytes()
    files = vgmconv.convert_file(src, log=lambda *_: None, wav=False, vgm=False)   # mesma pasta
    assert src.read_bytes() == before
    assert any(f.name.startswith("intro_") and f.suffix == ".xgm" for f in files)
