"""YM2612 pelo Nuked-OPN2 (emulação ciclo a ciclo, feita a partir do chip; LGPL 2.1+).

Mais fiel na parte interna (fase, envelopes, LFO, SSG-EG) que o emulador próprio, porém
bem mais lento: usado para a prévia e para conferir candidatos, não na busca de patches.
A saída do YM2612 discreto (o "ladder effect" do DAC) é a aproximação do próprio autor,
marcada por ele como não verificada.

A biblioteca (``native/nuked_opn2/``) é compilada à parte do resto do projeto, como pede
a LGPL. Interface igual à de ``MegaDriveSound`` (só a parte FM).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from mdconv.chips.emulator import NTSC_FM_CLOCK

_DIR = Path(__file__).parent / "native" / "nuked_opn2"
_SRCS = [_DIR / "ym3438.c", _DIR / "nuked_wrap.c"]
_LIB = _DIR / ("nuked_opn2.dll" if sys.platform == "win32" else "libnuked_opn2.so")
_lib = None

# Escala para o mesmo nível do emulador próprio (medida em tests/unit/test_nuked.py):
# soma de 24 ciclos com saída de 9 bits por canal.
SCALE = 10 ** (8.56 / 20) / 2048.0   # medido: mesmo nível do GPGX/emulador próprio (±0,1 dB)


def _build() -> Path:
    if _LIB.exists() and (sys.platform == "win32" or getattr(sys, "frozen", False)
                          or all(_LIB.stat().st_mtime >= s.stat().st_mtime for s in _SRCS if s.exists())):
        return _LIB
    cc = os.environ.get("CC", "gcc")
    cmd = [cc, "-O2", "-shared", "-o", str(_LIB)] + [str(s) for s in _SRCS]
    if sys.platform != "win32":
        cmd.insert(2, "-fPIC")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(f"compilador '{cc}' não encontrado para gerar {_LIB.name}") from None
    if r.returncode != 0:
        raise RuntimeError(f"Falha ao compilar o Nuked-OPN2:\n{r.stderr}")
    return _LIB


def available() -> bool:
    try:
        _load()
        return True
    except (OSError, RuntimeError):
        return False


def _load():
    global _lib
    if _lib is None:
        lib = ctypes.CDLL(str(_build()))
        lib.nk_create.restype = ctypes.c_void_p
        lib.nk_create.argtypes = [ctypes.c_int]
        lib.nk_destroy.argtypes = [ctypes.c_void_p]
        lib.nk_write.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.nk_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        _lib = lib
    return _lib


class NukedYM2612:
    def __init__(self, ym2612: bool = True):
        self._lib = _load()
        self._h = self._lib.nk_create(1 if ym2612 else 0)
        if not self._h:
            raise MemoryError("Nuked-OPN2: sem memória")
        self.fm_clock = NTSC_FM_CLOCK
        self.rate = NTSC_FM_CLOCK / 144.0

    def __del__(self):
        h, self._h = getattr(self, "_h", None), None
        if h:
            self._lib.nk_destroy(h)

    def write_fm(self, port: int, reg: int, data: int) -> None:
        self._lib.nk_write(self._h, port, reg, data & 0xFF)

    def render(self, n: int) -> np.ndarray:
        buf = np.empty(2 * n, dtype=np.float32)
        self._lib.nk_render(self._h, buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), n)
        return buf.reshape(n, 2) * SCALE
