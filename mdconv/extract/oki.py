"""Samples do OKI MSM6295 (ADPCM Dialogic/OKI de 4 bits).

Decodificador adaptado do MAME (okiadpcm.cpp, BSD-3-Clause, Andrew Gardner e Aaron Giles).
"""
import struct

SAMPLE_RATE = 7576       # 1 MHz / 132 (pino 7 em nível alto nas placas CPS1)

_INDEX_SHIFT = (-1, -1, -1, -1, 2, 4, 6, 8)
_NBL2BIT = [(1, 0, 0, 0), (1, 0, 0, 1), (1, 0, 1, 0), (1, 0, 1, 1),
            (1, 1, 0, 0), (1, 1, 0, 1), (1, 1, 1, 0), (1, 1, 1, 1),
            (-1, 0, 0, 0), (-1, 0, 0, 1), (-1, 0, 1, 0), (-1, 0, 1, 1),
            (-1, 1, 0, 0), (-1, 1, 0, 1), (-1, 1, 1, 0), (-1, 1, 1, 1)]
_DIFF = []
for _step in range(49):
    _sv = int(16.0 * (11.0 / 10.0) ** _step)
    for _n in range(16):
        b = _NBL2BIT[_n]
        _DIFF.append(b[0] * (_sv * b[1] + _sv // 2 * b[2] + _sv // 4 * b[3] + _sv // 8))


def decode_adpcm(data):
    """Bytes ADPCM -> lista de amostras int16 (nibble alto primeiro)."""
    sig, step = 0, 0
    out = []
    diff, shift = _DIFF, _INDEX_SHIFT
    for byte in data:
        for nib in (byte >> 4, byte & 15):
            sig += diff[step * 16 + nib]
            if sig > 2047:
                sig = 2047
            elif sig < -2048:
                sig = -2048
            step += shift[nib & 7]
            if step > 48:
                step = 48
            elif step < 0:
                step = 0
            out.append(sig * 16)
    return out


def parse_samples(rom):
    """Retorna dict: número do sample (1..127) -> (início, fim) em bytes."""
    res = {}
    for i in range(1, 128):
        off = i * 8
        if off + 6 > len(rom) or off >= 0x400:
            break
        start = int.from_bytes(rom[off:off + 3], "big")
        end = int.from_bytes(rom[off + 3:off + 6], "big")
        if start in (0xFFFFFF, 0) or end in (0xFFFFFF,):
            continue
        if start < 0x400 or end <= start or end >= len(rom):
            continue
        res[i] = (start, end)
    return res


def write_wav(path, samples, rate=SAMPLE_RATE):
    data = struct.pack("<%dh" % len(samples), *samples)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
        f.write(b"data" + struct.pack("<I", len(data)) + data)


def extract(rom):
    """Retorna dict número -> lista de amostras PCM16."""
    out = {}
    for n, (s, e) in parse_samples(rom).items():
        out[n] = decode_adpcm(rom[s:e + 1])
    return out
