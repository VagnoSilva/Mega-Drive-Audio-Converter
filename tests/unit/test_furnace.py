"""Exportação para o Furnace 0.6.8.3 (.fur).

Os testes de estrutura rodam sempre. Os que abrem e tocam o arquivo no próprio Furnace
precisam do executável dele (variável MDCONV_FURNACE apontando para o binário de linha de
comando) e são pulados sem ele.
"""
import os
import struct
import subprocess
import zlib

import numpy as np
import pytest

from mdconv.export.furnace import (FILE_TO_LOGICAL, PSG_NOTE_SHIFT, Cell, FurInstrument, FurSample,
                                   _psg_macro, _vibrato_params, read_fur, serialize)
from mdconv.fm.patch import Operator, Patch

FURNACE = os.environ.get("MDCONV_FURNACE")
needs_furnace = pytest.mark.skipif(not (FURNACE and os.path.exists(FURNACE)),
                                   reason="Furnace (linha de comando) não configurado em MDCONV_FURNACE")


def tiny(cells_fn, n_ch=11, use_pcm=True, samples=(), instruments=None):
    p = Patch(alg=7, fb=0, ops=[Operator(mul=1, tl=0, ar=31, rr=15)] + [Operator(tl=127) for _ in range(3)])
    ins = instruments or [FurInstrument("fm", "fm", patch=p),
                          FurInstrument("psg", "psg", vol_macro=_psg_macro({})[0])]
    cells = [dict() for _ in range(n_ch)]
    cells_fn(cells)
    meta = {"name": "teste", "author": "", "game": "", "g": 6, "t0": 0, "rows_per_beat": 4,
            "rows_per_bar": 16, "pat_len": 64, "vt_n": 150, "use_pcm": use_pcm, "n_ch": n_ch,
            "ch_names": []}
    return serialize(meta, ins, list(samples), cells, [(0, 64)])


def test_header_and_structure():
    data = tiny(lambda c: c[0].__setitem__(0, Cell(note=117, ins=0, vol=127)))
    raw = zlib.decompress(data)
    assert raw[:16] == b"-Furnace module-"
    assert struct.unpack_from("<H", raw, 16)[0] == 232
    chk = read_fur(data)
    assert chk.version == 232 and chk.systems == [0xBE, 0x03]
    assert chk.instruments == 2 and chk.patterns == 11 and chk.notes == 1
    assert chk.names[0] == "teste"


def test_fm_operator_order_and_detune():
    p = Patch(alg=4, fb=5, ops=[Operator(mul=1, dt=-3, tl=10), Operator(mul=2, dt=0, tl=20),
                                Operator(mul=3, dt=1, tl=30), Operator(mul=4, dt=3, tl=40)])
    raw = zlib.decompress(tiny(lambda c: None, instruments=[FurInstrument("x", "fm", patch=p)]))
    i = raw.index(b"FM", raw.index(b"INS2"))
    body = raw[i + 4:]
    assert body[0] == 0xF4 and body[1] == (4 << 4) | 5
    ops = [body[5 + 8 * j: 13 + 8 * j] for j in range(4)]
    # ordem dos registradores: S1, S3, S2, S4; DT gravado como dt+3
    for j, o in enumerate(ops):
        src = p.ops[FILE_TO_LOGICAL[j]]
        assert o[0] & 15 == src.mul and (o[0] >> 4) & 7 == src.dt + 3 and o[1] == src.tl


def test_vibrato_mapping():
    assert _vibrato_params(0, 30) is None
    (rate, depth), fine = _vibrato_params(64, 32)
    assert rate == 8
    assert abs(depth * fine - 64 * 15 / 16) <= 1


def test_psg_adsr_macro():
    vals, _ = _psg_macro({"attack_s": 0.0, "decay_s": 0.5, "sustain_db": -6.0, "release_s": 0.2})
    low, high, ar, ht, dr, sl, st, sr, rr = vals[:9]
    assert (low, high, ar) == (0, 15, 255)
    assert sl == 255 - 48                      # −6 dB = 3 passos de 2 dB = 48 unidades
    assert dr == round(800 / (0.5 * 60)) and rr == round(800 / (0.2 * 60))


def test_reader_rejects_garbage():
    from mdconv.export.furnace import FurError
    with pytest.raises(FurError):
        read_fur(b"nada disso")


def _render(tmp_path, data, name="t"):
    f = tmp_path / f"{name}.fur"
    f.write_bytes(data)
    out = tmp_path / f"{name}.wav"
    subprocess.run([FURNACE, "-loglevel", "error", "-noreport", "-loops", "0", "-outmode", "perchan",
                    "-output", str(out), str(f)], capture_output=True, timeout=120, check=True)
    from mdconv.reference.sampler import read_wav
    return lambda ch: read_wav(str(tmp_path / f"{name}_c{ch:02d}.wav"))[0].mean(1)


def _peak_hz(x, t0=0.1, n=16384):
    s = x[int(t0 * 44100):int(t0 * 44100) + n]
    S = np.abs(np.fft.rfft(s * np.hanning(len(s)), n=1 << 17))
    f = np.fft.rfftfreq(1 << 17, 1 / 44100)
    m = f > 50
    return f[m][np.argmax(S[m])]


@needs_furnace
def test_pitch_fm_and_psg_in_furnace(tmp_path):
    def cells(c):
        c[0][0] = Cell(note=69 + 48, ins=0, vol=127)                    # A4 no FM 1
        c[7][0] = Cell(note=69 + 48 - PSG_NOTE_SHIFT, ins=1, vol=15)    # A4 no PSG 1
    ch = _render(tmp_path, tiny(cells))
    assert abs(_peak_hz(ch(1)) - 440) < 3
    assert abs(_peak_hz(ch(8)) - 440) < 5


@needs_furnace
def test_pcm_kit_in_furnace(tmp_path):
    rng = np.random.default_rng(0)
    smp = [FurSample("ruido", (rng.standard_normal(2000) * 40).astype(np.int8), 14000)]
    ins = [FurInstrument("kit", "kit", note_map={35: 0})]

    def cells(c):
        for r in (0, 8, 16):
            c[5][r] = Cell(note=35 + 48, ins=0)
    ch = _render(tmp_path, tiny(cells, samples=smp, instruments=ins), "pcm")
    x = ch(6)
    for r in (0, 8, 16):
        i = int(r * 0.1 * 44100)
        assert np.sqrt((x[i:i + 1500] ** 2).mean()) > 0.01
