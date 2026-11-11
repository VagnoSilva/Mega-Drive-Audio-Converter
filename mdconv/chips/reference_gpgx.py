"""Referência LOCAL de validação: o núcleo YM2612 do Genesis Plus GX (derivado do fm.c
do MAME, com correções verificadas em hardware).

Esse código NÃO faz parte do projeto nem é distribuído com ele: sua licença tem
restrição não comercial, incompatível com a GPLv3. O usuário fornece a biblioteca
compilada e aponta ``MDCONV_GPGX_LIB`` para ela; sem isso, os testes de validação são
pulados. Serve só para comparar e corrigir o emulador próprio.

Interface igual à de ``MegaDriveSound`` (só a parte FM).
"""
from __future__ import annotations

import ctypes
import os

import numpy as np

from mdconv.chips.emulator import NTSC_FM_CLOCK

DISCRETE, INTEGRATED, ENHANCED = 0, 1, 2


def available() -> bool:
    p = os.environ.get("MDCONV_GPGX_LIB")
    return bool(p) and os.path.exists(p)


class GpgxYM2612:
    _lib = None

    def __init__(self, chip_type: int = DISCRETE):
        if GpgxYM2612._lib is None:
            lib = ctypes.CDLL(os.environ["MDCONV_GPGX_LIB"])
            lib.YM2612Write.argtypes = [ctypes.c_uint, ctypes.c_uint]
            lib.YM2612Update.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
            lib.YM2612Config.argtypes = [ctypes.c_int]
            GpgxYM2612._lib = lib
        self._lib = GpgxYM2612._lib
        # O núcleo do GPGX tem estado global: uma instância por vez.
        self._lib.YM2612Init()
        self._lib.YM2612Config(chip_type)
        self._lib.YM2612ResetChip()
        self.fm_clock = NTSC_FM_CLOCK
        self.rate = NTSC_FM_CLOCK / 144.0

    def write_fm(self, port: int, reg: int, data: int) -> None:
        self._lib.YM2612Write(2 * port, reg)
        self._lib.YM2612Write(2 * port + 1, data & 0xFF)

    def render(self, n: int) -> np.ndarray:
        buf = (ctypes.c_int * (2 * n))()
        self._lib.YM2612Update(buf, n)
        a = np.frombuffer(buf, dtype=np.int32).astype(np.float32).reshape(n, 2)
        # saída do GPGX: 9 bits do DAC deslocados 5 bits (escala de 14 bits por canal)
        return a / (256.0 * 32.0)
