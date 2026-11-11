"""Ponte Python ↔ emulador C (YM2612 + SN76489) via ctypes.

No Linux/macOS a biblioteca é compilada na primeira importação (gcc). No Windows o
projeto já traz ``native/mdchips.dll`` (compilada de ``mdchips.c`` com clang, só depende
do msvcrt.dll do próprio Windows); ver ``tools/build_dll.sh``.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

NTSC_FM_CLOCK = 7670453
NTSC_PSG_CLOCK = 3579545
PAL_FM_CLOCK = 7600489
PAL_PSG_CLOCK = 3546893

_HERE = Path(__file__).parent / "native"
_SRC = _HERE / "mdchips.c"
_LIB_NAME = "mdchips.dll" if sys.platform == "win32" else "libmdchips.so"
_LIB_PATH = _HERE / _LIB_NAME
_lib = None


def build(force: bool = False) -> Path:
    """Caminho da biblioteca nativa, compilando se preciso.

    No Windows o projeto já traz ``mdchips.dll`` pronta (o usuário não precisa de
    compilador); ela só é recompilada com ``MDCONV_REBUILD=1``. No executável
    (PyInstaller) o fonte nem existe: usa-se sempre a DLL empacotada.
    """
    rebuild = force or os.environ.get("MDCONV_REBUILD") == "1"
    if _LIB_PATH.exists() and not rebuild:
        if sys.platform == "win32" or getattr(sys, "frozen", False) or not _SRC.exists():
            return _LIB_PATH
        if _LIB_PATH.stat().st_mtime >= _SRC.stat().st_mtime:
            return _LIB_PATH
    if not _SRC.exists():
        raise RuntimeError(f"biblioteca do emulador ausente: {_LIB_PATH}")
    cc = os.environ.get("CC", "gcc")
    cmd = [cc, "-O2", "-shared", "-o", str(_LIB_PATH), str(_SRC), "-lm"]
    if sys.platform != "win32":
        cmd.insert(2, "-fPIC")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        if _LIB_PATH.exists():
            return _LIB_PATH
        raise RuntimeError(f"compilador '{cc}' não encontrado para gerar {_LIB_NAME}") from None
    if r.returncode != 0:
        if _LIB_PATH.exists() and not force:
            return _LIB_PATH
        raise RuntimeError(f"Falha ao compilar o emulador ({' '.join(cmd)}):\n{r.stderr}")
    return _LIB_PATH


def _load():
    global _lib
    if _lib is None:
        lib = ctypes.CDLL(str(build()))
        lib.md_create.restype = ctypes.c_void_p
        lib.md_create.argtypes = [ctypes.c_double, ctypes.c_double]
        lib.md_destroy.argtypes = [ctypes.c_void_p]
        lib.md_rate.restype = ctypes.c_double
        lib.md_rate.argtypes = [ctypes.c_void_p]
        lib.md_set_gains.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]
        lib.md_write_fm.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.md_write_psg.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.md_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        lib.md_set_ladder.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.md_op_state.restype = ctypes.c_int
        lib.md_op_state.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        _lib = lib
    return _lib


class MegaDriveSound:
    """YM2612 + SN76489 emulados. Saída estéreo float na taxa nativa (clock/144 ≈ 53267 Hz)."""

    def __init__(self, fm_clock: int = NTSC_FM_CLOCK, psg_clock: int = NTSC_PSG_CLOCK,
                 discrete_ym2612: bool = True):
        """``discrete_ym2612``: True = YM2612 separado (Mega Drive 1, ex. VA6), com o efeito
        ladder; False = YM3438/ASIC integrado (modelos posteriores)."""
        self._lib = _load()
        self._h = self._lib.md_create(float(fm_clock), float(psg_clock))
        self._lib.md_set_ladder(self._h, 1 if discrete_ym2612 else 0)
        self.fm_clock = fm_clock
        self.psg_clock = psg_clock

    def __del__(self):
        h = getattr(self, "_h", None)
        if h:
            self._lib.md_destroy(h)
            self._h = None

    @property
    def rate(self) -> float:
        return self._lib.md_rate(self._h)

    def set_gains(self, fm: float = 1.0, psg: float = 0.5) -> None:
        self._lib.md_set_gains(self._h, fm, psg)

    def write_fm(self, port: int, reg: int, data: int) -> None:
        self._lib.md_write_fm(self._h, port, reg, data)

    def write_psg(self, data: int) -> None:
        self._lib.md_write_psg(self._h, data)

    def render(self, n: int) -> np.ndarray:
        buf = np.zeros(2 * n, dtype=np.float32)
        self._lib.md_render(self._h, buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), n)
        return buf.reshape(n, 2)

    def op_state(self, ch: int, op: int) -> tuple[int, int]:
        v = ctypes.c_int(0)
        st = self._lib.md_op_state(self._h, ch, op, ctypes.byref(v))
        return st, v.value


def dc_block(x: np.ndarray, sr: float, cutoff_hz: float = 10.0) -> np.ndarray:
    """Remove o nível DC (o Mega Drive tem saída acoplada por capacitor; o efeito ladder
    gera DC). Filtro passa-altas de 1 polo."""
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff_hz / sr)
    return lfilter([1, -1], [1, -a], x, axis=0).astype(np.float32)


def resample(x: np.ndarray, sr_in: float, sr_out: int) -> np.ndarray:
    """Reamostragem de alta qualidade (domínio da frequência) para comparar com 44,1 kHz."""
    from scipy.signal import resample as _rs
    n_out = int(round(len(x) * sr_out / sr_in))
    if n_out <= 0:
        return np.zeros((0,) + x.shape[1:], dtype=np.float32)
    return _rs(x, n_out, axis=0).astype(np.float32)
