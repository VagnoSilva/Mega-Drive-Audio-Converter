"""OPL (YM3812 / YM3526 / Y8950) → YM2612 + PSG."""
import struct

import pytest

from mdconv import vgmconv
from mdconv.chips.emulator import NTSC_FM_CLOCK
from mdconv.fm.patch import block_fnum_to_freq
from mdconv.opl import convert as oc

CLOCK = 3579545


def _vgm(body: bytes, total: int, chip_off=0x50, loop_at=None) -> bytes:
    head = bytearray(0x100)
    struct.pack_into("<4sII", head, 0, b"Vgm ", 0x100 + len(body) - 4, 0x151)
    struct.pack_into("<I", head, 0x18, total)
    if loop_at is not None:
        struct.pack_into("<I", head, 0x1C, 0x100 + loop_at - 0x1C)
    struct.pack_into("<I", head, chip_off, CLOCK)
    struct.pack_into("<I", head, 0x34, 0x100 - 0x34)
    return bytes(head) + body


def W(reg, val, cmd=0x5A):
    return bytes([cmd, reg, val])


def wait(n):
    return b"\x61" + struct.pack("<H", n)


def fnum_for(hz, block):
    return round(hz * 2 ** (20 - block) / (CLOCK / 72))


def patch(ch, mult_m=1, mult_c=1, tl_m=20, tl_c=0, ar=15, dr=4, sl=2, rr=6, egt=1, fb=3, cnt=0,
          wave_c=0, ksl_c=0):
    m, c = oc.SLOTS[ch]
    b = W(0x20 + m, (egt << 5) | mult_m) + W(0x20 + c, (egt << 5) | mult_c)
    b += W(0x40 + m, tl_m) + W(0x40 + c, (ksl_c << 6) | tl_c)
    b += W(0x60 + m, (ar << 4) | dr) + W(0x60 + c, (ar << 4) | dr)
    b += W(0x80 + m, (sl << 4) | rr) + W(0x80 + c, (sl << 4) | rr)
    b += W(0xE0 + c, wave_c) + W(0xC0 + ch, (fb << 1) | cnt)
    return b


def note(ch, hz, block=4, on=True):
    f = fnum_for(hz, block)
    return W(0xA0 + ch, f & 0xFF) + W(0xB0 + ch, (0x20 if on else 0) | (block << 2) | (f >> 8))


def _fm_writes(conv):
    return [(i, w) for i, f in enumerate(conv.stream.frames) for w in f if w[0] == "fm"]


def test_single_voice_pitch_patch_and_keys(tmp_path):
    body = W(0x01, 0x20) + patch(0, tl_c=4) + wait(735 * 2) + note(0, 440) + wait(735 * 30) \
        + note(0, 440, on=False) + wait(735 * 10) + b"\x66"
    p = tmp_path / "a.vgm"
    p.write_bytes(_vgm(body, 735 * 42))
    assert vgmconv.detect_chip(p) == "YM3812"
    conv = oc.convert(oc.read_vgm(p))
    fm = _fm_writes(conv)
    regs = {}
    for i, w in fm:
        regs[(w[1], w[2])] = w[3]
    # frequência no YM2612
    hi, lo = regs[(0, 0xA4)], regs[(0, 0xA0)]
    hz = block_fnum_to_freq(hi >> 3, ((hi & 7) << 8) | lo, NTSC_FM_CLOCK)
    assert abs(hz / 440 - 1) < 0.003
    assert regs[(0, 0xB0)] == (3 << 3) | 4                 # FB 3, algoritmo 4
    assert regs[(0, 0x48)] == 4                            # TL da portadora (op2 = +8)
    assert regs[(0, 0x40)] == 20                           # modulador (op1)
    assert regs[(0, 0x50)] & 31 == 31 and regs[(0, 0x58)] & 31 == 31   # AR 15 → 31
    assert regs[(0, 0x70)] == 0                            # EGT=1: sem D2R
    assert regs[(0, 0x44)] == 127 and regs[(0, 0x4C)] == 127           # ops 3 e 4 mudos
    keys = [(i, w[3]) for i, w in fm if w[2] == 0x28]
    assert keys[0][1] == 0xF0 and keys[-1][1] == 0x00
    assert keys[-1][0] - keys[0][0] == 30
    assert conv.stream.frames[0][0] == ("fm", 0, 0x22, 0)  # sem tremolo/vibrato: LFO desligado


def test_waveforms_use_extra_operator_pair(tmp_path):
    # seno retificado na portadora: fundamental com MULT×2 e o 4º harmônico no par 3→4
    body = W(0x01, 0x20) + patch(0, mult_c=1, wave_c=2) + note(0, 220) + wait(735 * 5) + b"\x66"
    p = tmp_path / "w.vgm"
    p.write_bytes(_vgm(body, 735 * 5))
    conv = oc.convert(oc.read_vgm(p))
    regs = {(w[1], w[2]): w[3] for _, w in _fm_writes(conv)}
    assert regs[(0, 0x38)] & 15 == 2                       # op2 (portadora): MULT 2
    assert regs[(0, 0x3C)] & 15 == 4                       # op4 (extra): MULT 4
    assert regs[(0, 0x4C)] == regs[(0, 0x48)] + round(14 / 0.75)
    assert any("seno retificado" in r for r in conv.report)
    # sem o bit WSE ($01 bit 5) a onda é sempre seno
    body = patch(0, mult_c=1, wave_c=2) + note(0, 220) + wait(735 * 5) + b"\x66"
    p.write_bytes(_vgm(body, 735 * 5))
    regs = {(w[1], w[2]): w[3] for _, w in _fm_writes(oc.convert(oc.read_vgm(p)))}
    assert regs[(0, 0x38)] & 15 == 1 and regs[(0, 0x4C)] == 127


def test_ksl_lowers_high_notes(tmp_path):
    body = patch(0, ksl_c=3) + note(0, 110, block=3) + wait(735 * 5) + note(0, 110, 3, on=False) \
        + note(0, 1760, block=7) + wait(735 * 5) + b"\x66"
    p = tmp_path / "k.vgm"
    p.write_bytes(_vgm(body, 735 * 10))
    tls = [w[3] for _, w in _fm_writes(oc.convert(oc.read_vgm(p))) if w[2] == 0x48]
    assert tls[-1] > tls[0] + 10


def test_more_than_six_voices_go_to_psg_but_not_the_bass(tmp_path):
    body = W(0x01, 0x20)
    for ch in range(8):
        body += patch(ch)
    seq = b""
    for k in range(8):                         # 8 batidas; o canal 7 só toca em 2, o canal 8 é o baixo
        for ch in range(8):
            if ch == 6 and k % 4:
                continue
            hz = 55.0 if ch == 7 else 262.0 * (1 + ch / 8)
            seq += note(ch, hz, block=2 if ch == 7 else 4)
        seq += wait(735 * 10)
        for ch in range(8):
            hz = 55.0 if ch == 7 else 262.0 * (1 + ch / 8)
            seq += note(ch, hz, block=2 if ch == 7 else 4, on=False)
        seq += wait(735 * 2)
    p = tmp_path / "m.vgm"
    p.write_bytes(_vgm(body + seq + b"\x66", 735 * 12 * 8))
    conv = oc.convert(oc.read_vgm(p))
    dest = {v.key: v.dest for v in conv.voices}
    assert sum(1 for d in dest.values() if d and d[0] == "fm") == 6
    assert sum(1 for d in dest.values() if d and d[0] == "psg") == 2
    assert dest[(0, 7)][0] == "fm"                         # baixo (55 Hz) nunca no PSG
    assert dest[(0, 6)][0] == "psg"                        # a voz que menos toca vai para o PSG
    psg = [w for f in conv.stream.frames for w in f if w[0] == "psg"]
    assert any(w[1] & 0x90 == 0x90 and w[1] & 15 < 15 for w in psg)
    # rotas manuais: canal 1 para o PSG 3, canal 2 de fora
    conv = oc.convert(oc.read_vgm(p), routing={(0, 0): ("psg", 2), (0, 1): "omit"})
    dest = {v.key: v.dest for v in conv.voices}
    assert dest[(0, 0)] == ("psg", 2) and dest[(0, 1)] is None


def test_rhythm_mode_and_outputs(tmp_path):
    body = W(0x01, 0x20) + patch(6) + patch(7) + patch(8) + note(6, 60, 2, on=False)
    body += W(0xBD, 0x20) + wait(10)
    for k in range(4):
        body += W(0xBD, 0x20 | 0x10 | (0x08 if k % 2 else 0x01)) + wait(735 * 6) + W(0xBD, 0x20) + wait(735 * 6)
    body += b"\x66"
    p = tmp_path / "r.vgm"
    p.write_bytes(_vgm(body, 735 * 48 + 10, loop_at=None))
    conv = oc.convert(oc.read_vgm(p))
    assert any("modo bateria" in r for r in conv.report)
    keys = {v.key for v in conv.voices}
    assert (0, "BD") in keys
    noise = [w for f in conv.stream.frames for w in f if w[0] == "psg" and w[1] & 0xF0 == 0xE0]
    assert len(noise) == 4
    files = vgmconv.convert_file(p, tmp_path / "out", log=lambda *_: None, wav=False)
    assert sorted(f.name for f in files) == ["r.fur", "r.vgm", "r.xgm"]


def test_y8950_and_ym3526_detected(tmp_path):
    for off, cmd, name in ((0x54, 0x5B, "YM3526"), (0x58, 0x5C, "Y8950")):
        body = patch(0).replace(b"\x5a", bytes([cmd])) + note(0, 440).replace(b"\x5a", bytes([cmd])) \
            + wait(735 * 3) + b"\x66"
        p = tmp_path / f"{name}.vgm"
        p.write_bytes(_vgm(body, 735 * 3, chip_off=off))
        assert vgmconv.detect_chip(p) == name
        conv = oc.convert(oc.read_vgm(p))
        assert any(w[2] == 0x28 and w[3] == 0xF0 for f in conv.stream.frames for w in f if w[0] == "fm")
