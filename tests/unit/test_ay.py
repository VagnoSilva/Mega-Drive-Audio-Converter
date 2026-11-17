"""Conversão direta de VGM com AY-3-8910 para o Mega Drive."""
import struct

from mdconv.ay.convert import convert, env_level, read_vgm
from mdconv.export.xgm import read_xgm, write_xgm

CLOCK = 894886


def _vgm(tmp_path, cmds, loop_at=None):
    body = bytearray()
    loop_pos = None
    for i, c in enumerate(cmds):
        if loop_at == i:
            loop_pos = len(body)
        if c[0] == "w":
            body += bytes([0xA0, c[1], c[2]])
        else:
            n = c[1]
            while n > 0:
                k = min(n, 65535)
                body += bytes([0x61]) + struct.pack("<H", k)
                n -= k
    body += b"\x66"
    hdr = bytearray(0x80)
    hdr[0:4] = b"Vgm "
    struct.pack_into("<I", hdr, 0x08, 0x151)
    struct.pack_into("<I", hdr, 0x34, 0x80 - 0x34)
    struct.pack_into("<I", hdr, 0x74, CLOCK)
    total = sum(c[1] for c in cmds if c[0] == "t")
    struct.pack_into("<I", hdr, 0x18, total)
    if loop_pos is not None:
        struct.pack_into("<I", hdr, 0x1C, 0x80 + loop_pos - 0x1C)
    data = bytes(hdr) + bytes(body)
    struct.pack_into("<I", bytearray(data), 0x04, len(data) - 4)
    p = tmp_path / "t.vgm"
    p.write_bytes(data)
    return p


def _period(hz):
    return int(round(CLOCK / (16 * hz)))


def test_envelope_shapes():
    assert env_level(9, 100, CLOCK, 0.0) == 15
    assert env_level(9, 100, CLOCK, 10.0) == 0          # decai e fica em 0
    assert env_level(13, 100, CLOCK, 10.0) == 15        # sobe e fica em 15
    assert env_level(14, 1000, CLOCK, 16 * 1000 * 16 / CLOCK + 1e-6) == 15   # triângulo: desce no 2º ciclo


def test_tone_noise_and_low_channel(tmp_path):
    pa, pb = _period(440), _period(80)
    cmds = [("w", 0, pa & 255), ("w", 1, pa >> 8), ("w", 2, pb & 255), ("w", 3, pb >> 8),
            ("w", 6, 0), ("w", 7, 0b10011100), ("w", 8, 15), ("w", 9, 12), ("w", 10, 0x10),
            ("w", 11, 0), ("w", 12, 4), ("w", 13, 9),
            ("t", 44100)]
    v = read_vgm(_vgm(tmp_path, cmds))
    conv = convert(v)
    assert conv.routes[(0, 0)] == ("psg", 0)
    assert conv.routes[(0, 1)] == ("fm", 0)                # 80 Hz não cabe no PSG
    flat = [w for f in conv.stream.frames for w in f]
    n = int(round(3579545 / (32 * 440)))
    assert ("psg", 0x80 | (n & 15)) in flat
    assert ("psg", 0xE7) in flat                           # ruído cadenciado pelo tom 3
    assert any(w[0] == "fm" and w[2] == 0x28 and w[3] & 0xF0 for w in flat)   # key-on no FM
    x = read_xgm(write_xgm(conv.stream, tmp_path / "t.xgm") and (tmp_path / "t.xgm").read_bytes())
    assert len(x.frames) >= 60


def test_loop_point(tmp_path):
    p = _period(440)
    cmds = [("w", 0, p & 255), ("w", 1, p >> 8), ("w", 7, 0b111110), ("w", 8, 15), ("t", 22050),
            ("w", 8, 10), ("t", 22050)]
    conv = convert(read_vgm(_vgm(tmp_path, cmds, loop_at=5)))
    assert conv.stream.loop_frame == 30


def test_trim_leading_silence(tmp_path):
    p = _period(440)
    cmds = [("w", 0, p & 255), ("w", 1, p >> 8), ("w", 7, 0b111110), ("t", 44100),   # 1 s mudo
            ("w", 8, 15), ("t", 22050), ("w", 8, 12), ("t", 22050)]
    v = read_vgm(_vgm(tmp_path, cmds, loop_at=6))
    conv = convert(v)
    assert any("Silêncio do início removido: 1.00 s" in r for r in conv.report)
    assert ("psg", 0x90) in conv.stream.frames[0]          # já soa no primeiro quadro
    assert conv.stream.loop_frame == 30                   # loop deslocado junto
    assert convert(v, trim=False).stream.loop_frame == 90


def test_loop_length_from_vgm_and_seamless(tmp_path):
    from mdconv.export.common import export_frames
    p = _period(440)
    # loop começa em 0,5 s e dura 1,234 s (não é múltiplo de quadro)
    cmds = [("w", 0, p & 255), ("w", 1, p >> 8), ("w", 7, 0b111110), ("w", 8, 15), ("t", 22050),
            ("w", 8, 12), ("t", 54419)]
    conv = convert(read_vgm(_vgm(tmp_path, cmds, loop_at=5)), trim=False)
    frames, loop = export_frames(conv.stream)
    assert loop == 30
    dur = (len(frames) - loop) / 60
    assert 54419 / 44100 - 1 / 60 < dur <= 54419 / 44100                # quadros inteiros dentro do loop original
    assert ("psg", 0xFF) not in frames[-1]                               # sem "desligar tudo" na emenda


def test_loop_marker_after_setup(tmp_path):
    import struct
    from mdconv.export.vgm import write_vgm
    p = _period(440)
    cmds = [("w", 0, p & 255), ("w", 1, p >> 8), ("w", 7, 0b111110), ("w", 8, 15), ("t", 22050),
            ("w", 8, 12), ("t", 22050)]
    conv = convert(read_vgm(_vgm(tmp_path, cmds, loop_at=0)))
    write_vgm(conv.stream, tmp_path / "o.vgm")
    d = (tmp_path / "o.vgm").read_bytes()
    data = 0x34 + struct.unpack_from("<I", d, 0x34)[0]
    loop = 0x1C + struct.unpack_from("<I", d, 0x1C)[0]
    assert loop > data                       # a inicialização fica antes do ponto de loop
    assert d[loop] in (0x50, 0x52)


def _two_channels(tmp_path):
    pa, pb = _period(440), _period(80)
    cmds = [("w", 0, pa & 255), ("w", 1, pa >> 8), ("w", 2, pb & 255), ("w", 3, pb >> 8),
            ("w", 7, 0b11111100), ("w", 8, 15), ("w", 9, 12), ("t", 44100)]
    return read_vgm(_vgm(tmp_path, cmds))


def test_manual_routing(tmp_path):
    import pytest
    v = _two_channels(tmp_path)
    conv = convert(v, routing={(0, 0): ("fm", 4), (0, 1): ("psg", 2)})
    assert conv.routes == {(0, 0): ("fm", 4), (0, 1): ("psg", 2)}
    flat = [w for f in conv.stream.frames for w in f]
    assert any(w[0] == "fm" and w[1] == 1 and w[2] == 0xA5 for w in flat)      # FM 5 = porta 1, canal 1
    assert any(w[0] == "psg" and w[1] & 0xF0 == 0xC0 for w in flat)            # tom 3 do PSG
    assert any("(escolhido)" in line for line in conv.report)
    # canal omitido e o resto automático, desviando do destino já ocupado
    conv = convert(v, routing={(0, 0): None, (0, 1): "auto"})
    assert conv.routes[(0, 0)] is None and conv.routes[(0, 1)] == ("fm", 0)
    conv = convert(v, routing={(0, 1): ("psg", 0)})
    assert conv.routes == {(0, 1): ("psg", 0), (0, 0): ("psg", 1)}
    with pytest.raises(ValueError):
        convert(v, routing={(0, 0): ("fm", 2), (0, 1): ("fm", 2)})
