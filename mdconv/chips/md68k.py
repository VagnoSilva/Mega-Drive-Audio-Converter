"""68000 (núcleo Musashi, licença MIT) e Z80 do Mega Drive para rodar o driver de som do próprio jogo.

A biblioteca (``native/md68k/``) junta o Musashi e um Z80 em C (``z80.c``, tradução do ``mdconv.z80``
com os T-states reais) a um mapa de memória mínimo do console: ROM, RAM, área do Z80 (RAM, YM2612
com os timers, banco da ROM em $8000, BUSREQ/RESET), PSG e um VDP que só responde ao status. Ela chama
sub-rotinas da ROM (``call``) e registra tudo o que o jogo escreve no YM2612, no PSG e na RAM do
Z80, com o número do quadro, além de breakpoints (endereços de instrução) usados para achar o loop.
"""
from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

_DIR = Path(__file__).parent / "native" / "md68k"
_M = _DIR / "musashi"
_SRCS = [_DIR / "mdsnd.c", _M / "m68kcpu.c", _M / "m68kops.c", _M / "softfloat" / "softfloat.c"]
_LIB = _DIR / ("md68k.dll" if sys.platform == "win32" else "libmd68k.so")
_lib = None

EV_YM0, EV_YM1, EV_PSG, EV_Z80, EV_BRK, EV_ZBRK, EV_RAM, EV_REGS = range(8)
EVENT = np.dtype([("frame", "<u4"), ("t44", "<u4"), ("addr", "<u2"), ("type", "u1"), ("val", "u1")])
REG_D0, REG_A3 = 0, 11


def _lock(lock) -> int:
    if lock is None:
        return -1
    if lock == "block":
        return -3
    return int(lock) & 0xFF


def _build() -> Path:
    deps = _SRCS + [_DIR / "z80.c", _M / "m68kconf.h", _M / "m68kcpu.h", _M / "m68kfpu.c"]
    if _LIB.exists() and (sys.platform == "win32" or getattr(sys, "frozen", False)
                          or all(_LIB.stat().st_mtime >= s.stat().st_mtime for s in deps if s.exists())):
        return _LIB
    cc = os.environ.get("CC", "gcc")
    cmd = [cc, "-O2", "-shared", "-w", f"-I{_M}", "-o", str(_LIB)] + [str(s) for s in _SRCS] + ["-lm"]
    if sys.platform != "win32":
        cmd.insert(2, "-fPIC")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(f"compilador '{cc}' não encontrado para gerar {_LIB.name}") from None
    if r.returncode != 0:
        raise RuntimeError(f"Falha ao compilar o núcleo do 68000:\n{r.stderr}")
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
        u32 = ctypes.c_uint32
        lib.md_load.argtypes = [ctypes.c_char_p, u32]
        lib.md_reset.argtypes = []
        lib.md_call.argtypes = [u32, u32, u32, u32]
        lib.md_call.restype = ctypes.c_int
        lib.md_set_frame.argtypes = [u32]
        lib.md_event_count.restype = u32
        lib.md_events.restype = ctypes.c_void_p
        lib.md_clear_events.argtypes = []
        lib.md_ram.restype = ctypes.c_void_p
        lib.md_zram.restype = ctypes.c_void_p
        lib.md_unmapped.argtypes = [ctypes.c_int]
        lib.md_unmapped.restype = u32
        lib.md_get_reg.argtypes = [ctypes.c_int]
        lib.md_get_reg.restype = u32
        lib.md_set_reg.argtypes = [ctypes.c_int, ctypes.c_uint32]
        lib.md_keystack_arm.argtypes = [ctypes.c_int]
        lib.md_keystack.argtypes = [ctypes.c_int, ctypes.c_void_p]
        lib.md_keystack.restype = u32
        lib.md_set_breaks.argtypes = [ctypes.POINTER(u32), ctypes.c_int]
        lib.md_set_zbreaks.argtypes = [ctypes.POINTER(u32), ctypes.c_int]
        lib.md_enable_z80.argtypes = [ctypes.c_int]
        lib.md_set_frame_auto.argtypes = [ctypes.c_int]
        lib.md_run_frames.argtypes = [u32]
        lib.md_mclk.restype = ctypes.c_uint64
        lib.md_run_game.argtypes = [u32]
        lib.md_run_game.restype = ctypes.c_int
        lib.md_ym_regs.restype = ctypes.c_void_p
        lib.md_free_z80.argtypes = []
        lib.md_set_pad.argtypes = [ctypes.c_int]
        lib.md_set_version.argtypes = [ctypes.c_int]
        lib.md_set_zoverride.argtypes = [ctypes.c_int] * 4
        lib.md_state_size.restype = u32
        lib.md_save_state.argtypes = [ctypes.c_char_p]
        lib.md_load_state.argtypes = [ctypes.c_char_p]
        lib.md_keyons.argtypes = [ctypes.c_int]
        lib.md_keyons.restype = u32
        lib.md_log_ram.argtypes = [ctypes.c_int]
        lib.md_set_roverride.argtypes = [ctypes.c_int] * 4
        lib.md_ym_writes.argtypes = [ctypes.c_int]
        lib.md_ym_writes.restype = u32
        lib.md_z80_steps.restype = ctypes.c_uint64
        lib.md_z80_pc.restype = u32
        lib.md_z80_bank.restype = u32
        _lib = lib
    return _lib


class Machine:
    """Um Mega Drive "de som". Só existe um por processo (o Musashi guarda o estado em globais)."""

    def __init__(self, rom: bytes):
        self.lib = _load()
        self.rom = bytes(rom)
        self.lib.md_load(self.rom, len(self.rom))
        # região do console pelo cabeçalho ($1F0): jogo só japonês → console japonês (alguns se recusam a rodar)
        reg = self.rom[0x1F0:0x1F3].decode("latin-1", "replace").upper() if len(self.rom) > 0x1F3 else ""
        jp_only = ("J" in reg or reg[:1] in ("1", "3")) and "U" not in reg and "E" not in reg and reg[:1] not in ("4", "5", "D", "F")
        self.lib.md_set_version(0x20 if jp_only else 0xA0)
        sp = struct.unpack(">I", self.rom[0:4])[0] & 0xFFFFFF
        self.sp = sp if 0xE00000 <= sp <= 0x1000000 else 0xFFFE00
        self._seen = 0

    def reset(self) -> None:
        self.lib.md_reset()
        self._seen = 0

    def call(self, addr: int, d0: int = 0, max_cycles: int = 20_000_000, sp: int | None = None) -> None:
        if self.lib.md_call(addr, d0 & 0xFFFFFFFF, self.sp if sp is None else sp, max_cycles):
            raise RuntimeError(f"a rotina {addr:06X} da ROM não voltou (laço infinito ou perfil errado)")

    def set_zbreaks(self, addrs) -> None:
        """Breakpoints no Z80: cada vez que o PC passa por um deles, um evento EV_ZBRK com o IX."""
        a = list(addrs)[:16]
        arr = (ctypes.c_uint32 * max(1, len(a)))(*a)
        self.lib.md_set_zbreaks(arr, len(a))

    def enable_z80(self, on: bool) -> None:
        self.lib.md_enable_z80(1 if on else 0)

    def set_frame_auto(self, on: bool) -> None:
        self.lib.md_set_frame_auto(1 if on else 0)

    def run_frames(self, n: int = 1) -> None:
        """Roda só o Z80 por ``n`` quadros (driver de som no Z80)."""
        self.lib.md_run_frames(n)

    def run_game(self, n: int = 1) -> None:
        """Roda o jogo inteiro a partir do reset (68000 + Z80, VBlank) por ``n`` quadros."""
        self.lib.md_run_game(n)

    def ym_regs(self) -> bytes:
        """Os registradores do YM2612 agora (porta 0: 256 bytes, depois a porta 1)."""
        return ctypes.string_at(self.lib.md_ym_regs(), 512)

    PAD = dict(up=1, down=2, left=4, right=8, b=16, c=32, a=64, start=128)

    def set_pad(self, *buttons: str) -> None:
        """Aperta (e mantém) os botões do controle 1; sem argumentos, solta todos."""
        self.lib.md_set_pad(sum(self.PAD[b] for b in buttons))

    def free_z80(self) -> None:
        """Solta o barramento e o reset do Z80 (para rodar só o Z80 com o 68000 parado)."""
        self.lib.md_free_z80()

    def save_state(self) -> bytes:
        """Estado completo da máquina (sem os eventos), para voltar a este ponto com load_state."""
        buf = ctypes.create_string_buffer(self.lib.md_state_size())
        self.lib.md_save_state(buf)
        return buf.raw

    def load_state(self, st: bytes) -> None:
        self.lib.md_load_state(st)
        self._seen = 0

    def set_zoverride(self, addr: int, val: int, skip: int = 0, lock: int | None = None) -> None:
        """Depois de ``skip`` escritas do jogo em zram[addr], a seguinte grava ``val`` no lugar do valor do
        jogo; com ``lock``, todas as escritas seguintes nesse endereço viram ``lock`` (``"block"``: são
        ignoradas). ``addr`` = -1 apaga
        todas as trocas."""
        self.lib.md_set_zoverride(addr, val, skip, _lock(lock))

    def keyons(self) -> tuple[int, int]:
        """(key-on feitos pelo 68000, feitos pelo Z80) desde o reset: diz quem toca a música FM."""
        return self.lib.md_keyons(0), self.lib.md_keyons(1)

    def set_reg(self, r: int, v: int) -> None:
        """Muda um registrador do 68000 (0–7 = D0–D7, 8–15 = A0–A7)."""
        self.lib.md_set_reg(r, v & 0xFFFFFFFF)

    def keystack_arm(self, zaddr: int = -1) -> None:
        """Tira uma foto da pilha do 68000 no próximo key-on que ele fizer (ver ``keystack``) ou, com
        ``zaddr``, na próxima vez que ele escrever nesse endereço da RAM do Z80."""
        self.lib.md_keystack_arm(zaddr)

    def keystack(self):
        """(SP no key-on, SP quando a última interrupção foi aceita, PC da escrita, 512 bytes da pilha);
        SP 0 se ainda não houve key-on desde ``keystack_arm``."""
        buf = (ctypes.c_uint8 * 512)()
        sp = self.lib.md_keystack(0, buf)
        return sp, self.lib.md_keystack(1, None), self.lib.md_keystack(2, None), bytes(buf)

    def log_ram(self, on: bool) -> None:
        """Registra (EV_RAM) as escritas do 68000 na própria RAM."""
        self.lib.md_log_ram(1 if on else 0)

    def set_roverride(self, addr: int, val: int, skip: int = 0, lock: int | None = None) -> None:
        """Como set_zoverride, para a RAM do 68000 (endereço $FFxxxx ou só os 16 bits de baixo)."""
        self.lib.md_set_roverride(addr, val, skip, -1 if lock is None else lock)

    def ym_writes(self) -> tuple[int, int]:
        """(escritas no YM feitas pelo 68000, feitas pelo Z80) desde o reset."""
        return self.lib.md_ym_writes(0), self.lib.md_ym_writes(1)

    @property
    def z80_steps(self) -> int:
        return self.lib.md_z80_steps()

    @property
    def z80_pc(self) -> int:
        return self.lib.md_z80_pc()

    def set_frame(self, f: int) -> None:
        self.lib.md_set_frame(f)

    def set_breaks(self, addrs) -> None:
        a = list(addrs)[:16]
        arr = (ctypes.c_uint32 * max(1, len(a)))(*a)
        self.lib.md_set_breaks(arr, len(a))

    def clear_events(self) -> None:
        self.lib.md_clear_events()
        self._seen = 0

    def events(self) -> np.ndarray:
        n = self.lib.md_event_count()
        if not n:
            return np.zeros(0, EVENT)
        raw = ctypes.string_at(self.lib.md_events(), n * EVENT.itemsize)
        return np.frombuffer(raw, EVENT).copy()

    def new_events(self) -> np.ndarray:
        """Só os eventos desde a última chamada (barato: não copia o que já foi visto)."""
        n = self.lib.md_event_count()
        if n <= self._seen:
            return np.zeros(0, EVENT)
        raw = ctypes.string_at(self.lib.md_events() + self._seen * EVENT.itemsize, (n - self._seen) * EVENT.itemsize)
        self._seen = n
        return np.frombuffer(raw, EVENT).copy()

    def ram(self):
        return (ctypes.c_uint8 * 0x10000).from_address(self.lib.md_ram())

    def zram(self):
        return (ctypes.c_uint8 * 0x2000).from_address(self.lib.md_zram())

    def unmapped(self) -> tuple[int, int]:
        return self.lib.md_unmapped(0), self.lib.md_unmapped(1)
