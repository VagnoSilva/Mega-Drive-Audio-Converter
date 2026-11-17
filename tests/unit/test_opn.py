"""Conversão direta de VGM com YM2203 (OPN) para o YM2612."""
import struct

from mdconv.fm.patch import block_fnum_to_freq
from mdconv.chips.emulator import NTSC_FM_CLOCK
from mdconv.opn.convert import convert, read_vgm
from mdconv.vgmconv import convert_file, detect_chip

CLOCK = 1500000


def _vgm(tmp_path, cmds, loop_at=None, dual=True):
    body, loop_pos = bytearray(), None
    for i, c in enumerate(cmds):
        if loop_at == i:
            loop_pos = len(body)
        if c[0] == "w":
            body += bytes([0x55 if c[1] == 0 else 0xA5, c[2], c[3]])
        else:
            body += bytes([0x61]) + struct.pack("<H", c[1])
    body += b"\x66"
    hdr = bytearray(0x80)
    hdr[0:4] = b"Vgm "
    struct.pack_into("<I", hdr, 0x08, 0x151)
    struct.pack_into("<I", hdr, 0x34, 0x80 - 0x34)
    struct.pack_into("<I", hdr, 0x44, CLOCK | (0x40000000 if dual else 0))
    struct.pack_into("<I", hdr, 0x18, sum(c[1] for c in cmds if c[0] == "t"))
    if loop_pos is not None:
        struct.pack_into("<I", hdr, 0x1C, 0x80 + loop_pos - 0x1C)
    p = tmp_path / "t.vgm"
    p.write_bytes(bytes(hdr) + bytes(body))
    return p


def test_note_pitch_patch_and_channels(tmp_path):
    cmds = [("w", 0, 0x2D, 0), ("w", 0, 0x2E, 0), ("w", 1, 0x2D, 0), ("w", 1, 0x2E, 0),
            ("w", 0, 0xB0, 0x07), ("w", 0, 0x40, 0x00), ("w", 0, 0x50, 0x1F), ("w", 0, 0x60, 0x85),
            ("w", 0, 0xA4, 0x22), ("w", 0, 0xA0, 0x6A), ("w", 0, 0x28, 0xF0),
            ("w", 1, 0xA5, 0x22), ("w", 1, 0xA1, 0x6A), ("w", 1, 0x28, 0xF1),
            ("t", 44100), ("w", 0, 0x28, 0x00), ("t", 4410)]
    p = _vgm(tmp_path, cmds)
    assert detect_chip(p) == "YM2203"
    c = convert(read_vgm(p))
    flat = [w for f in c.stream.frames for w in f]
    assert ("fm", 0, 0x28, 0xF0) in flat and ("fm", 0, 0x28, 0xF5) in flat   # 2º chip → FM 5
    assert ("fm", 0, 0x60, 0x04) in flat                  # AM zerado, D1R 5 → 4 (taxa interna menor)
    a4 = next(w[3] for w in flat if w[:3] == ("fm", 0, 0xA4))
    a0 = next(w[3] for w in flat if w[:3] == ("fm", 0, 0xA0))
    hz = block_fnum_to_freq(a4 >> 3, ((a4 & 7) << 8) | a0, NTSC_FM_CLOCK)
    ref = block_fnum_to_freq(4, 0x26A, CLOCK * 144 / 36)
    assert abs(hz / ref - 1) < 0.002
    assert c.exact and c.exact[0][0] == 0


def test_outputs_exact_vgm(tmp_path):
    cmds = [("w", 0, 0x2D, 0), ("w", 0, 0x2E, 0), ("t", 100), ("w", 0, 0xA4, 0x22), ("w", 0, 0xA0, 0x6A),
            ("w", 0, 0x28, 0xF0), ("t", 30000), ("w", 0, 0x28, 0x00), ("t", 30000)]
    files = convert_file(_vgm(tmp_path, cmds, loop_at=2), tmp_path / "o", wav=False, log=lambda m: None)
    names = sorted(f.name for f in files)
    assert names == ["t.fur", "t.vgm", "t.xgm"]            # o .fur do Furnace sai junto
    from mdconv.export.vgm_reader import read_vgm as rv
    v = rv((tmp_path / "o" / "t.vgm").read_bytes())
    assert v is not None
