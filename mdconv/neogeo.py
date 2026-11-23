"""Neo Geo (MVS/AES): música rodando o programa de som do jogo (Z80 + YM2610).

A placa de som do Neo Geo é um Z80 a 4 MHz com o YM2610 (8 MHz): 4 canais FM (a mesma família
do YM2612), 3 de SSG (um AY embutido), 6 de ADPCM-A (amostras a 18,5 kHz) e 1 de ADPCM-B
(amostra com altura variável). O programa de som fica na ROM M1 e as amostras nas ROMs V.

Mapa conferido no driver ``neogeo.cpp`` do MAME:

* 0000–7FFF ROM fixa; 8000–BFFF, C000–DFFF, E000–EFFF e F000–F7FF são janelas de 16, 8, 4 e
  2 KB da M1, escolhidas lendo as portas 0B, 0A, 09 e 08 (o byte alto do endereço da porta é o
  número do banco); F800–FFFF RAM.
* Porta 00 (leitura): comando do 68000 (chega por NMI); 0C (escrita): resposta.
  Portas 08/18 (escrita): liga/desliga a NMI.
* Portas 04–07: YM2610 (endereço/dado das portas A e B; 04 lê o status dos timers).
* O andamento vem dos timers A e B do YM2610 (IRQ do Z80, modo 1).

O comando 07 libera a música no programa de som (o jogo manda no início); os comandos de
música são os do tipo 2 da tabela de comandos do próprio programa.

Conversão para o Mega Drive (``to_stream``):

* FM: os 4 canais vão 1 para 1 para os FM 1–4, com a frequência recalculada para o clock do
  YM2612 (timbres como estão, pan mantido).
* SSG → PSG, pelo conversor do AY-3-8910 (clock do SSG = 2 MHz); partes graves no FM 5.
* ADPCM-A/B → PCM do XGM (8 bits a 14 kHz) nos canais escolhidos (padrão 1 e 2, deixando 3 e 4
  para as vozes e golpes do jogo). O volume de cada nota vai gravado na amostra (o XGM não tem
  volume de PCM). Peças metálicas (chimbal, prato) podem ir para o ruído do PSG.
"""
from __future__ import annotations

import hashlib
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mdconv.z80 import Z80

VGM_RATE = 44100
CPU_CLOCK = 4_000_000
YM_CLOCK = 8_000_000
SSG_CLOCK = YM_CLOCK // 4
ADPCMA_RATE = YM_CLOCK / 432          # 18 518 Hz
FPS = 60.0
PCM_RATE = 14000.0

PROFILES = {
    "kof94": dict(
        title="The King of Fighters '94", short="KOF 94", board="Neo Geo (Z80 + YM2610)",
        m1=("055-m1.bin", "055-m1.m1"), v=(("055-v1.bin", "055-v1.v1"), ("055-v2.bin", "055-v2.v2"),
                                          ("055-v3.bin", "055-v3.v3")),
        enable=0x07, type_table=0x6907, music_type=2, names={}),
    "kof95": dict(
        title="The King of Fighters '95", short="KOF 95", board="Neo Geo (Z80 + YM2610)",
        m1=("084-m1.bin", "084-m1.m1"), v=(("084-v1.bin", "084-v1.v1"), ("084-v2.bin", "084-v2.v2"),
                                          ("084-v3.bin", "084-v3.v3")),
        v_offsets=(0x000000, 0x400000, 0x800000),        # como no MAME: a v3 começa em 8 MB
        music_type=2, names={}),
    "kof96": dict(
        title="The King of Fighters '96", short="KOF 96", board="Neo Geo (Z80 + YM2610)",
        m1=("214-m1.bin", "214-m1.m1"), v=(("214-v1.bin", "214-v1.v1"), ("214-v2.bin", "214-v2.v2"),
                                          ("214-v3.bin", "214-v3.v3")),
        music_type=2, names={}),
    "kof97": dict(
        title="The King of Fighters '97", short="KOF 97", board="Neo Geo (Z80 + YM2610)",
        m1=("232-m1.bin", "232-m1.m1"), v=(("232-v1.bin", "232-v1.v1"), ("232-v2.bin", "232-v2.v2"),
                                          ("232-v3.bin", "232-v3.v3")),
        music_type=2, names={}),
    "kof98": dict(
        title="The King of Fighters '98", short="KOF 98", board="Neo Geo (Z80 + YM2610)",
        m1=("242-m1.m1", "242-mg1.m1", "242-m1.bin", "kof98_m1.rom"),
        v=(("242-v1.v1", "242-v1.bin", "kof98_v1.rom"), ("242-v2.v2", "242-v2.bin", "kof98_v2.rom"),
           ("242-v3.v3", "242-v3.bin", "kof98_v3.rom"), ("242-v4.v4", "242-v4.bin", "kof98_v4.rom")),
        music_type=2, names={}),
    "garou": dict(
        title="Garou: Mark of the Wolves", short="Garou", board="Neo Geo (Z80 + YM2610)",
        m1=("253-m1.bin", "253-m1.m1", "garou_m1.rom"),
        v=(("253-v1.bin", "253-v1.v1", "garou_v1.rom"), ("253-v2.bin", "253-v2.v2", "garou_v2.rom"),
           ("253-v3.bin", "253-v3.v3", "garou_v3.rom"), ("253-v4.bin", "253-v4.v4", "garou_v4.rom")),
        music_type=2,
        # nomes mapeados ouvindo as extrações (temas dos lutadores e faixas da trilha)
        names={0x23: "Victory Message", 0x25: "Dark Lightning -Emergence-", 0x2C: "Do You Do?",
               0x30: "Tema de Terry Bogard", 0x31: "Tema de Rock Howard", 0x32: "Tema de Dong Hwan",
               0x33: "Tema de Kim Jae Hoon", 0x34: "Tema de Hotaru Futaba", 0x35: "Tema de Gato Futaba",
               0x36: "Tema de B. Jenet", 0x37: "Tema de Marco", 0x38: "Tema de Hokutomaru",
               0x39: "Tema de Freeman", 0x3A: "Tema de Tizoc", 0x3B: "Tema de Kevin",
               0x3C: "Tema de Grant", 0x3D: "Tema de Kain", 0x3E: "Sunset Sky Again"}),
}

# Programa de som "Ver 3.0 by MAKOTO" (KOF 94, KOF 95…): o despacho dos comandos lê o tipo de cada
# um numa tabela de 256 bytes — ld e,a / ld d,0 / ld hl,T1 / add hl,de / ld a,(hl) / ld (nn),a /
# ld hl,TIPOS / add hl,de / ld a,(hl) / ld (nn),a / or a / ret z / dec a / jp z,… / dec a / jp z,…
_DISPATCH = (rb"\x5f\x16\x00\x21(..)\x19\x7e\x32.\xfe\x21(..)\x19\x7e\x32.\xfe\xb7\xc8\x3d\xca..\x3d\xca")


# "Sound Driver Ver 0.1 96/07/03" (KOF 96…): comandos abaixo de 20 são de sistema; o tipo dos
# outros fica numa tabela indexada por (comando − 20): cp 20 / jr nc / jp … / ld d,a / sub 20 /
# ld e,a / ld a,d / ld d,0 / ld hl,TIPOS / add hl,de / ld e,a / ld a,(hl) / or a / ret z
_DISPATCH_96 = rb"\xfe\x20\x30\x03\xc3..\x57\xd6\x20\x5f\x7a\x16\x00\x21(..)\x19\x5f\x7e\xb7\xc8"


def find_type_table(m1: bytes) -> int | None:
    """Endereço da tabela de tipos de comando (o comando 0 corresponde ao byte ``endereço``)."""
    t = find_dispatch(m1)
    return None if t is None else t[0] + t[1]


def find_dispatch(m1: bytes):
    """(endereço da tabela, primeiro comando que ela cobre) nos programas de som conhecidos."""
    import re
    m = re.search(_DISPATCH, m1[:0x10000], re.S)
    if m:
        return int.from_bytes(m.group(2), "little"), 0
    m = re.search(_DISPATCH_96, m1[:0x10000], re.S)
    if m:
        return int.from_bytes(m.group(1), "little") - 0x20, 0x20
    return None


@dataclass
class Capture:
    cmd: int
    writes: list                 # (amostra 44,1 kHz, porta, reg, valor)
    end: int
    loop: int | None
    report: list = field(default_factory=list)


# ============================================================================ placa de som
class Board:
    def __init__(self, key: str, m1: bytes, vrom: bytes):
        self.key = key
        self.p = PROFILES[key]
        self.title = self.p["title"]
        self.m1 = m1
        self.vrom = vrom
        self.ram = bytearray(0x800)
        self.cpu = Z80(self._rd, self._wr, self._in, self._out)
        self._boot = None
        if self.p.get("type_table"):
            tt, first = self.p["type_table"], 0
        else:
            d = find_dispatch(m1)
            if d is None:
                raise ValueError("programa de som do Neo Geo não reconhecido (tabela de comandos não achada)")
            tt, first = d
        self.musics = tuple(c for c in range(first, 256) if m1[tt + c] == self.p["music_type"])
        self.enable = self.p.get("enable")

    # ------------------------------------------------------------- barramento
    def _rd(self, a):
        m = self.m1
        if a < 0x8000:
            return m[a]
        if a < 0xC000:
            return m[(self.bank[3] * 0x4000 + a - 0x8000) % len(m)]
        if a < 0xE000:
            return m[(self.bank[2] * 0x2000 + a - 0xC000) % len(m)]
        if a < 0xF000:
            return m[(self.bank[1] * 0x1000 + a - 0xE000) % len(m)]
        if a < 0xF800:
            return m[(self.bank[0] * 0x800 + a - 0xF000) % len(m)]
        return self.ram[a - 0xF800]

    def _wr(self, a, v):
        if a >= 0xF800:
            self.ram[a - 0xF800] = v

    def _in(self, port):
        lo = port & 0xFF
        if lo == 0x00:
            self.nmi_pending = False
            return self.latch
        if lo == 0x04:
            return self.flags
        if lo == 0x05:
            return self.regs[0][self.addr[0]] if self.addr[0] < 0x10 else 0
        if 0x08 <= lo <= 0x0B:
            self.bank[lo - 8] = port >> 8
        return 0

    def _out(self, port, v):
        lo = port & 0xFF
        if lo in (0x04, 0x06):
            self.addr[(lo - 4) >> 1] = v
        elif lo in (0x05, 0x07):
            ym = (lo - 5) >> 1
            r = self.addr[ym]
            self.regs[ym][r] = v
            if ym == 0 and 0x24 <= r <= 0x27:
                self._timer_reg(r, v)
            elif not (ym == 0 and r == 0x1C):
                self.log.append((self.cpu.cycles, ym, r, v))
        elif lo == 0x08:
            self.nmi_enabled = True
            self._try_nmi()
        elif lo == 0x18:
            self.nmi_enabled = False

    def _try_nmi(self):
        if self.nmi_enabled and self.nmi_pending:
            self.nmi_pending = False
            self.cpu.nmi()

    # ---------------------------------------------------------- timers do YM
    def _period_a(self):
        r = self.regs[0]
        return 72 * (1024 - ((r[0x24] << 2) | (r[0x25] & 3)))       # em ciclos do Z80

    def _period_b(self):
        return 1152 * (256 - self.regs[0][0x26])

    def _timer_reg(self, r, v):
        if r != 0x27:
            return
        old, self.ctrl = self.ctrl, v
        t = self.cpu.cycles
        if v & 1 and not old & 1:
            self.next_a = t + self._period_a()
        elif not v & 1:
            self.next_a = None
        if v & 2 and not old & 2:
            self.next_b = t + self._period_b()
        elif not v & 2:
            self.next_b = None
        if v & 0x10:
            self.flags &= ~1
        if v & 0x20:
            self.flags &= ~2

    def _timers(self):
        t = self.cpu.cycles
        while self.next_a is not None and t >= self.next_a:
            if self.ctrl & 4:
                self.flags |= 1
            self.next_a += self._period_a()
        while self.next_b is not None and t >= self.next_b:
            if self.ctrl & 8:
                self.flags |= 2
            self.next_b += self._period_b()

    # --------------------------------------------------------------- execução
    def _reset_state(self):
        self.latch = 0
        self.bank = [0x1E, 0x0E, 0x06, 0x02]
        self.addr = [0, 0]
        self.regs = [[0] * 256, [0] * 256]
        self.log = []
        self.nmi_enabled = self.nmi_pending = False
        self.ctrl = self.flags = 0
        self.next_a = self.next_b = None
        self.on_irq = None

    def _run(self, cycles: int):
        cpu = self.cpu
        target = cpu.cycles + cycles
        while cpu.cycles < target:
            self._timers()
            if (self.flags & 1 and self.ctrl & 4) or (self.flags & 2 and self.ctrl & 8):
                if cpu.iff1 and not cpu.ei_delay:
                    if self.on_irq:
                        self.on_irq()
                    cpu.irq()
                    continue
            if cpu.halted:
                nxt = min([x for x in (self.next_a, self.next_b) if x is not None] + [target])
                cpu.cycles = max(cpu.cycles + 4, nxt)
                continue
            cpu.step()

    def command(self, cmd: int):
        self.latch = cmd
        self.nmi_pending = True
        self._try_nmi()

    _FIELDS = ("a", "f", "b", "c", "d", "e", "h", "l", "a_", "f_", "b_", "c_", "d_", "e_", "h_", "l_",
               "ix", "iy", "sp", "pc", "i", "r", "iff1", "iff2", "im", "halted", "ei_delay", "cycles")
    _BOARD = ("latch", "nmi_enabled", "nmi_pending", "ctrl", "flags", "next_a", "next_b")

    def _power_on(self, enable: int):
        self.ram[:] = bytes(len(self.ram))
        self.cpu.reset()
        self._reset_state()
        self._run(int(0.5 * CPU_CLOCK))
        self.command(enable)
        self._run(int(0.2 * CPU_CLOCK))

    def find_enable(self) -> int:
        """O comando que libera a música (o jogo manda no início): o primeiro, entre os comandos de
        sistema, depois do qual uma música toca de fato."""
        if self.enable is not None:
            return self.enable
        probe = self.musics[len(self.musics) // 2]
        for cand in [0x07] + [c for c in range(0x02, 0x20) if c != 0x07]:
            self._power_on(cand)
            self.log = []
            self.command(probe)
            self._run(int(2.0 * CPU_CLOCK))
            if any((ym == 0 and r == 0x28 and v & 0xF0) or (ym == 1 and r == 0 and not v & 0x80 and v & 0x3F)
                   for _, ym, r, v in self.log):
                self.enable = cand
                return cand
        raise ValueError("não achei o comando que libera a música neste programa de som")

    def boot(self):
        """Liga a placa, libera a música e guarda o estado para cada captura."""
        if self._boot is None:
            self._power_on(self.find_enable())
            self._boot = (bytes(self.ram), {f: getattr(self.cpu, f) for f in self._FIELDS},
                          {f: getattr(self, f) for f in self._BOARD}, [list(r) for r in self.regs],
                          list(self.bank), list(self.addr))
        ram, cpu, board, regs, bank, addr = self._boot
        self._reset_state()
        self.ram[:] = ram
        for f, v in cpu.items():
            setattr(self.cpu, f, v)
        for f, v in board.items():
            setattr(self, f, v)
        self.regs, self.bank, self.addr = [list(r) for r in regs], list(bank), list(addr)

    def _sounding(self) -> bool:
        """Alguma coisa soando agora? (nota FM ligada, SSG com volume, ADPCM recente)."""
        return any(self.keys) or any(self.regs[0][8 + c] & 0x1F for c in range(3)) or \
            self.cpu.cycles - self.last_adpcm < CPU_CLOCK

    def capture(self, cmd: int, max_seconds: float = 300.0, chunk: float = 5.0) -> Capture:
        self.boot()
        t0 = self.cpu.cycles
        seen = {}
        loop = []
        self.keys = [0] * 8
        self.last_adpcm = -10 * CPU_CLOCK

        def on_irq():
            if loop:
                return
            st = hashlib.blake2b(bytes(self.ram) + bytes([self.flags, self.ctrl]) + bytes(
                [getattr(self.cpu, f) & 0xFF for f in ("a", "f", "b", "c", "d", "e", "h", "l")]) +
                self.cpu.sp.to_bytes(2, "little") + self.cpu.pc.to_bytes(2, "little") + bytes(self.bank),
                digest_size=16).digest()
            if st in seen:
                loop.extend((seen[st], self.cpu.cycles))
            else:
                seen[st] = self.cpu.cycles
        self.on_irq = on_irq
        init = [(t0, 0, r, self.regs[0][r]) for r in range(0x10)]
        self.command(cmd)
        report = []
        end_c = loop_c = None
        quiet_since = None
        n_log = 0
        while self.cpu.cycles - t0 < max_seconds * CPU_CLOCK:
            self._run(int(chunk * CPU_CLOCK))
            for _, ym, r, v in self.log[n_log:]:
                if ym == 0 and r == 0x28:
                    self.keys[v & 7] = v & 0xF0
                elif (ym == 1 and r == 0 and not v & 0x80 and v & 0x3F) or (ym == 0 and r == 0x10 and v & 0x80):
                    self.last_adpcm = self.cpu.cycles
            n_log = len(self.log)
            if loop:
                a, b = loop
                if any(a <= t < b and (r == 0x28 and v & 0xF0 or (ym == 1 and r == 0 and not v & 0x80))
                       for t, ym, r, v in self.log):
                    loop_c, end_c = a, b
                    report.append("loop exato (o estado da placa se repete)")
                    break
                loop.clear()
                seen.clear()
            if not loop and self.cpu.cycles - t0 >= 20 * CPU_CLOCK:
                # o estado não se repete (algum contador corre à parte): loop pela repetição das notas
                nl = find_loop(self._voices(t0))
                if nl is not None:
                    s0, per, odd = nl
                    loop_c, end_c = t0 + s0, t0 + s0 + per
                    report.append(f"loop pela repetição das notas (período de {per / CPU_CLOCK:.2f} s)"
                                  + (f"; {', '.join(odd)} não se repete exatamente (variação do próprio jogo)"
                                     if odd else ""))
                    break
            if self._sounding():
                quiet_since = None
            else:
                quiet_since = quiet_since or self.cpu.cycles
                if self.cpu.cycles - quiet_since >= 2 * CPU_CLOCK:
                    last = max((t for t, ym, r, v in self.log if (r == 0x28 and v & 0xF0) or
                                (ym == 1 and r == 0 and not v & 0x80) or (ym == 0 and r in (8, 9, 10) and v)),
                               default=t0)
                    end_c = last + int(2.0 * CPU_CLOCK)
                    break
        self.on_irq = None
        if end_c is None:
            end_c = self.cpu.cycles
            report.append(f"sem loop nem fim em {max_seconds:.0f} s: gravado até aí")

        def smp(t):
            return int(round((t - t0) * VGM_RATE / CPU_CLOCK))
        w = [(0, ym, r, v) for _, ym, r, v in init] + [(smp(t), ym, r, v) for t, ym, r, v in self.log if t < end_c]
        return Capture(cmd, w, smp(end_c), None if loop_c is None else smp(loop_c), report)

    def _voices(self, t0):
        """Eventos de nota por voz (FM com a altura, ADPCM-A/B com a amostra) para achar o loop."""
        ev = {}
        fm = {}
        ra = [0] * 0x30
        rb = [0] * 0x20
        for t, ym, r, v in self.log:
            if 0xA0 <= r <= 0xA6:
                fm[(ym, r)] = v
            elif ym == 0 and r == 0x28 and v & 0xF0:
                c = v & 7
                yy, cc = (0, c) if c < 4 else (1, c - 4)
                ev.setdefault(("fm", c), []).append((t - t0, (fm.get((yy, 0xA4 + cc)), fm.get((yy, 0xA0 + cc)))))
            elif ym == 1 and r < 0x30:
                ra[r] = v
                if r == 0 and not v & 0x80:
                    for ch in range(6):
                        if v >> ch & 1:
                            ev.setdefault(("a", ch), []).append((t - t0, (ra[0x10 + ch], ra[0x18 + ch])))
            elif ym == 0 and 0x10 <= r < 0x20:
                rb[r - 0x10] = v
                if r == 0x10 and v & 0x80:
                    ev.setdefault(("b",), []).append((t - t0, (rb[2], rb[3], rb[9], rb[10])))
        return ev

    def name(self, cmd: int) -> str:
        return self.p["names"].get(cmd, f"Música {cmd:02X}")


_KIND = {"fm": "FM", "a": "ADPCM-A", "b": "ADPCM-B"}


def find_loop(voices: dict, min_period: float = 4 * CPU_CLOCK):
    """Loop pelas notas, com as vozes juntadas por tipo (FM, ADPCM-A, ADPCM-B): o programa de som
    reparte as notas entre os canais livres, então um canal sozinho não se repete com a música,
    mas a sequência de notas de cada tipo sim. Um tipo que não se repete (variação aleatória do
    jogo) não impede o loop se os outros, com a maioria das notas, se repetirem.
    Devolve (início, período, [tipos que não se repetem]) em ciclos, ou None."""
    from mdconv.konami import find_note_loop
    merged = {}
    for k, e in voices.items():
        merged.setdefault(k[0], []).extend(e)
    found, odd, total, ok_events = [], [], 0, 0
    for kind, ev in merged.items():
        ev.sort(key=lambda x: (x[0], str(x[1])))
        total += len(ev)
        if len(ev) < 8:
            continue
        r = find_note_loop({kind: ev}, min_period=min_period)
        if r is None:                   # canais fixos: canal a canal
            r = find_note_loop({k: e for k, e in voices.items() if k[0] == kind}, min_period=min_period)
        if r is None:
            odd.append(_KIND.get(kind, kind))
        else:
            found.append(r)
            ok_events += len(ev)
    if not found or ok_events < 0.5 * total:
        return None
    base = max(p for _, p in found)
    if any(abs(base / p - round(base / p)) > 0.05 for _, p in found):
        return None
    start = max(s for s, _ in found)
    last = max(e[-1][0] for e in merged.values() if e)
    import bisect
    tol = 0.05 * CPU_CLOCK

    def repeats(ev, period):
        """As notas de uma volta aparecem na seguinte (±50 ms)? (a ordem de notas simultâneas
        pode variar, então não dá para exigir a sequência exata)"""
        ts = [t for t, _ in ev]
        win = [(t, k) for t, k in ev if start <= t < start + period]
        if not win:
            return True
        hits = 0
        for t, k in win:
            i = bisect.bisect_left(ts, t + period - tol)
            while i < len(ev) and ts[i] <= t + period + tol:
                if ev[i][1] == k:
                    hits += 1
                    break
                i += 1
        return hits >= 0.95 * len(win)

    # o período da música é um múltiplo do achado (ex.: a bateria repete a cada compasso e a
    # melodia a cada 8): o menor múltiplo em que TODOS os tipos se repetem
    for mult in range(1, 33):
        period = base * mult
        if start + 2 * period > last + tol:
            return None                     # ainda não há duas voltas gravadas: grava mais
        if all(repeats(ev, period) for kind, ev in merged.items() if len(ev) >= 8):
            return start, period, []
    return None



def load_board(path) -> Board:
    p = Path(path)
    if p.suffix.lower() != ".zip":
        raise ValueError("abra o .zip do MAME")
    with zipfile.ZipFile(p) as z:
        names = {Path(n).name.lower(): n for n in z.namelist()}
        for key, prof in PROFILES.items():
            m1 = next((names[a] for a in prof["m1"] if a in names), None)
            vs = [next((names[a] for a in alts if a in names), None) for alts in prof["v"]]
            if m1 and all(vs):
                parts = [z.read(n) for n in vs]
                offs = prof.get("v_offsets")
                if offs:
                    vrom = bytearray(max(o + len(d) for o, d in zip(offs, parts)))
                    for o, d in zip(offs, parts):
                        vrom[o:o + len(d)] = d
                    vrom = bytes(vrom)
                else:
                    vrom = b"".join(parts)
                return Board(key, z.read(m1), vrom)
    raise ValueError("não é um jogo de Neo Geo conhecido")


def is_board_source(path) -> bool:
    try:
        p = Path(path)
        if p.suffix.lower() != ".zip":
            return False
        with zipfile.ZipFile(p) as z:
            names = {Path(n).name.lower() for n in z.namelist()}
        return any(any(a in names for a in prof["m1"]) for prof in PROFILES.values())
    except Exception:
        return False


def _rom_b(board) -> bytes:
    """ROM do ADPCM-B. Na placa é a mesma das V (``vrom``); um VGM pode trazer as duas separadas."""
    return getattr(board, "vrom_b", None) or board.vrom


# ============================================================================ VGM do YM2610
@dataclass
class VgmBoard:
    """O que :func:`to_stream` precisa de uma "placa" quando a música vem de um VGM gravado
    (vgmrips, MAME): as ROMs de ADPCM que vêm dentro do próprio VGM."""
    title: str
    vrom: bytes                           # ADPCM-A (bloco 0x82)
    vrom_b: bytes                         # ADPCM-B / DELTA-T (bloco 0x83)
    clock: int = YM_CLOCK
    ym2610b: bool = False


# tamanho dos operandos de cada comando do VGM (os que não são do YM2610 são pulados)
def _vgm_cmd_len(c: int) -> int:
    if c in (0x4F, 0x50) or 0x30 <= c <= 0x3F:
        return 1
    if 0x40 <= c <= 0x4E or 0x51 <= c <= 0x5F or c == 0x61 or 0xA0 <= c <= 0xBF:
        return 2
    if c in (0x62, 0x63) or 0x70 <= c <= 0x8F:
        return 0
    if c == 0x68:
        return 11
    if 0xC0 <= c <= 0xDF:
        return 3
    if 0xE0 <= c <= 0xFF:
        return 4
    return {0x90: 4, 0x91: 4, 0x92: 5, 0x93: 10, 0x94: 1, 0x95: 4}.get(c, -1)


def read_vgm(path):
    """VGM/VGZ com YM2610 (Neo Geo) → (VgmBoard, Capture, gd3).

    As escritas saem no mesmo formato da captura da ROM — (amostra, porta, reg, valor) —, então a
    conversão para o Mega Drive é a mesma (:func:`to_stream`): a diferença entre os dois caminhos
    fica só na origem das escritas (o VGM gravado do MAME × o programa de som rodando aqui)."""
    import gzip
    import struct
    from mdconv.ay.convert import _gd3
    d = Path(path).read_bytes()
    if d[:2] == b"\x1f\x8b":
        d = gzip.decompress(d)
    if d[:4] != b"Vgm ":
        raise ValueError("Não é um arquivo VGM/VGZ.")
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    pos = 0x34 + rel if rel else 0x40
    clk = struct.unpack_from("<I", d, 0x4C)[0] if pos > 0x4F else 0
    if not clk & 0x3FFFFFFF:
        raise ValueError("Este VGM não tem YM2610.")
    total = struct.unpack_from("<I", d, 0x18)[0]
    loop_rel = struct.unpack_from("<I", d, 0x1C)[0]
    loop_ofs = 0x1C + loop_rel if loop_rel else None
    gd3_rel = struct.unpack_from("<I", d, 0x14)[0]
    gd3 = _gd3(d, 0x14 + gd3_rel if gd3_rel else 0)

    roms = {0x82: bytearray(), 0x83: bytearray()}
    writes = []
    t = 0
    loop = None
    second = 0
    while pos < len(d):
        if loop_ofs is not None and loop is None and pos >= loop_ofs:
            loop = t
        c = d[pos]
        if c == 0x66:
            break
        if c in (0x58, 0x59):
            r, v = d[pos + 1], d[pos + 2]
            ym = c - 0x58
            # o registro de flags do ADPCM ($1C) e os timers ($24–$27) não são música (a captura
            # da ROM também não os grava)
            if not (ym == 0 and (r == 0x1C or 0x24 <= r <= 0x27)):
                writes.append((t, ym, r, v))
            pos += 3
            continue
        if c in (0xA8, 0xA9):                   # 2º YM2610: o Neo Geo só tem um
            second += 1
            pos += 3
            continue
        if c == 0x61:
            t += struct.unpack_from("<H", d, pos + 1)[0]
            pos += 3
            continue
        if c == 0x62:
            t += 735
            pos += 1
            continue
        if c == 0x63:
            t += 882
            pos += 1
            continue
        if 0x70 <= c <= 0x7F:
            t += (c & 15) + 1
            pos += 1
            continue
        if 0x80 <= c <= 0x8F:
            t += c & 15
            pos += 1
            continue
        if c == 0x67:
            typ = d[pos + 2]
            size = struct.unpack_from("<I", d, pos + 3)[0] & 0x7FFFFFFF
            body = d[pos + 7: pos + 7 + size]
            if typ in roms and len(body) >= 8:
                rom_size, start = struct.unpack_from("<II", body, 0)
                chunk = body[8:]
                buf = roms[typ]
                need = max(rom_size, start + len(chunk))
                if len(buf) < need:
                    buf.extend(bytes(need - len(buf)))
                buf[start:start + len(chunk)] = chunk
            pos += 7 + size
            continue
        n = _vgm_cmd_len(c)
        if n < 0:
            raise ValueError(f"comando 0x{c:02X} desconhecido no VGM (posição 0x{pos:X})")
        pos += 1 + n
    end = max(total, t)
    report = []
    if second:
        report.append(f"⚠ {second} escritas para um 2º YM2610 ignoradas")
    clock = clk & 0x3FFFFFFF
    b = bool(clk & 0x80000000)
    if b:
        report.append("⚠ YM2610B: os canais FM 1 e 4 (que o YM2610 do Neo Geo não tem) ficam de fora")
    if abs(clock - YM_CLOCK) > YM_CLOCK * 0.01:
        report.append(f"⚠ clock do YM2610 = {clock} Hz (o Neo Geo usa {YM_CLOCK}); conversão feita como 8 MHz")
    a_rom, b_rom = bytes(roms[0x82]), bytes(roms[0x83])
    if not a_rom and not b_rom:
        report.append("⚠ o VGM não traz as amostras de ADPCM: só FM e SSG")
    report.append(f"ROMs de ADPCM no VGM: A {len(a_rom) // 1024} KB, B {len(b_rom) // 1024} KB")
    title = gd3.get("jogo") or Path(path).stem
    board = VgmBoard(title, a_rom or b_rom, b_rom or a_rom, clock, b)
    cap = Capture(-1, writes, end, loop, report)
    return board, cap, gd3


# ============================================================================ ADPCM
_A_STEPS = (16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
            157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
            1060, 1166, 1282, 1411, 1552)
_A_ADJ = (-1, -1, -1, -1, 2, 5, 7, 9)
_B_ADJ = (57, 57, 57, 57, 77, 102, 128, 153)


def decode_adpcm_a(data: bytes) -> np.ndarray:
    """ADPCM-A do YM2610 (nibble alto primeiro, acumulador de 12 bits) → float −1..1."""
    out = np.empty(2 * len(data), dtype=np.float32)
    acc, si = 0, 0
    k = 0
    for b in data:
        for n in (b >> 4, b & 15):
            st = _A_STEPS[si]
            d = ((2 * (n & 7) + 1) * st) >> 3
            acc = (acc - d if n & 8 else acc + d) & 0xFFF
            if acc & 0x800:
                acc -= 0x1000
            si = max(0, min(48, si + _A_ADJ[n & 7]))
            out[k] = acc / 2048.0
            acc &= 0xFFF
            k += 1
    return out


def decode_adpcm_b(data: bytes) -> np.ndarray:
    """ADPCM-B (DELTA-T) do YM2610 → float −1..1."""
    out = np.empty(2 * len(data), dtype=np.float32)
    acc, step = 0, 127
    k = 0
    for b in data:
        for n in (b >> 4, b & 15):
            d = ((2 * (n & 7) + 1) * step) >> 3
            acc = max(-32768, min(32767, acc - d if n & 8 else acc + d))
            step = max(127, min(24576, (step * _B_ADJ[n & 7]) >> 6))
            out[k] = acc / 32768.0
            k += 1
    return out


def _resample(x: np.ndarray, sr_in: float, sr_out: float) -> np.ndarray:
    if len(x) == 0:
        return x
    n = max(1, int(round(len(x) * sr_out / sr_in)))
    if sr_out < sr_in:                      # passa-baixa simples antes de reduzir a taxa
        k = max(1, int(round(sr_in / sr_out)))
        if k > 1:
            x = np.convolve(x, np.ones(k) / k, mode="same")
    return np.interp(np.arange(n) * (sr_in / sr_out), np.arange(len(x)), x).astype(np.float32)


# ============================================================================ conversão
@dataclass
class Options:
    pcm_channels: tuple = (1, 2)          # canais PCM do XGM para a música (3–4 livres para o jogo)
    metals_to_noise: bool = True          # chimbal/prato no ruído do PSG (se o SSG não usar ruído)
    max_sample_s: float = 0.8             # corta caudas longas (economiza ROM)
    volume_step_db: float = 6.0           # variações de volume gravadas na amostra (passos)
    adpcm_b_to_fm: bool = True            # ADPCM-B (instrumento com altura) no FM 5
    min_level_db: float = -15.0           # notas de ADPCM mais baixas que isso (ecos) ficam de fora
    psg_overflow: bool = False            # nota que sumiria (canal PCM ocupado) vai para o PSG, imitada

    def __post_init__(self):
        # passos aceitos: 6 e 12 dB (o de 3 dB saiu: só aumentava a ROM). Opções salvas com 3 dB viram 6.
        self.volume_step_db = 12.0 if float(self.volume_step_db) >= 9 else 6.0


def to_stream(board: Board, cap: Capture, opt: Options | None = None):
    """Capture → (Stream de quadros a 60 Hz, relatório)."""
    from mdconv.ay.convert import AyVgm, convert as ay_convert
    from mdconv.chips.emulator import NTSC_FM_CLOCK
    from mdconv.driver.driver import Stream
    from mdconv.fm.patch import block_fnum_to_freq, freq_to_block_fnum
    from mdconv.opn.convert import best_phase
    from mdconv.pcm.kit import NOISE_FULL_DB, _noise_envelope, is_metal, piece_features
    opt = opt or Options()
    writes = sorted(cap.writes, key=lambda x: x[0])
    report = [f"YM2610 (Neo Geo), {len(writes)} escritas, {cap.end / VGM_RATE:.1f} s"
              + (f", loop em {cap.loop / VGM_RATE:.2f} s" if cap.loop is not None else ", sem loop")]

    def is_start(ym, r, v):
        return (ym == 0 and r == 0x28 and v & 0xF0) or (ym == 1 and r == 0 and not v & 0x80 and v & 0x3F) or \
            (ym == 0 and r == 0x10 and v & 0x80) or (ym == 0 and r in (8, 9, 10) and v & 0x1F)
    t0 = next((t for t, ym, r, v in writes if t > 0 and is_start(ym, r, v)), 0)
    if cap.loop is not None:
        t0 = min(t0, cap.loop)
    if t0 > 0:
        report.append(f"Silêncio do início removido: {t0 / VGM_RATE:.2f} s")
    writes = [(max(0, t - t0), ym, r, v) for t, ym, r, v in writes]
    end = max(1, cap.end - t0)
    loop = None if cap.loop is None else cap.loop - t0
    spf = VGM_RATE / FPS
    phase = best_phase(sorted({t for t, ym, r, v in writes if is_start(ym, r, v)}), spf)

    def frame_of(t):
        return max(0, int(round((t - phase) / spf)))
    n_frames = frame_of(end) + 1
    frames = [[] for _ in range(n_frames)]

    # ---- FM: (porta YM, canal 1/2) → FM 1–4 do YM2612
    fm_map = {(0, 1): 0, (0, 2): 1, (1, 1): 2, (1, 2): 3}

    def md(k):
        return (0, k) if k < 3 else (1, k - 3)
    latch = {}
    fm_used = set()
    ssg = []
    pcm_events = []                       # (amostra, tipo, dados)
    for t, ym, r, v in writes:
        f = min(n_frames - 1, frame_of(t))
        if ym == 0 and r < 0x10:
            ssg.append((t, 0, r, v))
            continue
        if ym == 0 and r == 0x22:
            frames[f].append(("fm", 0, 0x22, v))
            continue
        if ym == 0 and r == 0x28:
            c = v & 7
            key = (0, c) if c in (1, 2) else (1, c - 4) if c in (5, 6) else None
            if key in fm_map:
                k = fm_map[key]
                fm_used.add(k)
                frames[f].append(("fm", 0, 0x28, (v & 0xF0) | (k if k < 3 else k + 1)))
            continue
        if ym == 1 and r < 0x30:
            pcm_events.append((t, "a", r, v))
            continue
        if ym == 0 and 0x10 <= r < 0x20:
            pcm_events.append((t, "b", r, v))
            continue
        if r < 0x30 or (r & 3) not in (1, 2) or 0xA8 <= r <= 0xAE:
            continue
        k = fm_map[(ym, r & 3)]
        port, cc = md(k)
        nr = (r & ~3) | cc
        if 0xA4 <= r <= 0xA6:
            latch[k] = v
            continue
        if 0xA0 <= r <= 0xA2:
            hi = latch.get(k, 0)
            block, fnum = (hi >> 3) & 7, ((hi & 7) << 8) | v
            hz = block_fnum_to_freq(block, fnum, YM_CLOCK)
            b2, f2 = freq_to_block_fnum(hz, NTSC_FM_CLOCK) if hz > 0 else (0, 0)
            frames[f] += [("fm", port, 0xA4 + cc, (b2 << 3) | (f2 >> 8)), ("fm", port, 0xA0 + cc, f2 & 0xFF)]
            continue
        frames[f].append(("fm", port, nr, v))
    report.append("FM: " + (", ".join(f"canal {k + 1} → FM {k + 1}" for k in sorted(fm_used)) or "não toca"))

    # ---- SSG → PSG (convertido antes do ADPCM: o transbordo precisa saber que canais ele usa)
    ssg_conv = None
    if any(r in (8, 9, 10) and v & 0x1F for _, _, r, v in ssg):
        ay = AyVgm(SSG_CLOCK, False, ssg, end, loop, {})
        ssg_conv = ay_convert(ay, trim=False, fm_slots=[4])
    psg_free = _psg_free_frames(ssg_conv.stream.frames if ssg_conv else [], n_frames)

    # ---- ADPCM → PCM do XGM
    samples = {}                          # chave → id
    data = {}                             # id → int8
    cache = {}
    metal_noise = []                      # (quadro, envelope de atenuação)
    regs_a = [0] * 0x30
    regs_b = [0] * 0x20
    busy = {c: (-1, 0) for c in opt.pcm_channels}     # canal → (quadro final, quadro inicial)
    dropped = quiet = 0
    ssg_noise = any(r == 7 and (~v) & 0x38 for _, _, r, v in ssg) and any(
        r in (8, 9, 10) and v & 0x1F for _, _, r, v in ssg)
    use_noise = opt.metals_to_noise and not ssg_noise
    n_metal = 0
    over = _Overflow(n_frames, psg_free, noise_free=not use_noise and not ssg_noise) if opt.psg_overflow else None

    def sample_for(kind, start, stop, level_db, rate):
        nonlocal n_metal
        raw_key = (kind, start, stop, rate)
        if raw_key not in cache:
            rom = board.vrom if kind == "a" else _rom_b(board)
            seg = rom[start:min(stop + 1, len(rom))]
            x = decode_adpcm_a(seg) if kind == "a" else decode_adpcm_b(seg)
            x = x[: int(opt.max_sample_s * rate)]
            y = _resample(x, rate, PCM_RATE)
            feat = piece_features(y, int(PCM_RATE)) if len(y) > 64 else None
            cache[raw_key] = (y, feat is not None and is_metal(feat))
        y, metal = cache[raw_key]
        key = raw_key + (int(round(level_db / opt.volume_step_db)),)    # volume em passos
        return key, y, metal

    remap = {}                            # (fonte, volume) → (fonte, volume) que cabe nas 63 amostras

    def note_key(t, kind, start, stop, level_db, rate):
        """A amostra que a nota vai usar no PCM (None: fica de fora, vai para o ruído ou é curta)."""
        if -level_db < opt.min_level_db:
            return None
        key, y, metal = sample_for(kind, start, stop, level_db, rate)
        if len(y) < 8 or (metal and use_noise):
            return None
        return key

    def start_pcm(t, kind, start, stop, level_db, rate):
        nonlocal dropped, quiet
        if -level_db < opt.min_level_db:
            quiet += 1
            return
        f = min(n_frames - 1, frame_of(t))
        key, y, metal = sample_for(kind, start, stop, level_db, rate)
        if len(y) < 8:
            return
        key = remap.get(key, key)
        gain = 10 ** (-key[-1] * opt.volume_step_db / 20)
        if metal and use_noise:
            env = _noise_envelope(y * gain * 0.7, PCM_RATE)
            metal_noise.append((f, env))
            return
        free = [c for c in opt.pcm_channels if busy[c][0] <= f]
        ch = free[0] if free else min(opt.pcm_channels, key=lambda c: busy[c][1])
        if not free and over is not None and f - busy[ch][1] <= OVERFLOW_FRAMES:
            # a nota que está no canal acabou de começar e sumiria: a nova vai imitada para o PSG
            if over.place(f, y * gain, key[:-1]):
                return
        if key not in samples:
            if len(samples) >= 63:
                dropped += 1
                return
            sid = len(samples) + 1
            samples[key] = sid
            data[sid] = np.clip(np.round(y * gain * 127), -128, 127).astype(np.int8)
        sid = samples[key]
        dur = int(math.ceil(len(data[sid]) / PCM_RATE * FPS))
        busy[ch] = (f + dur, f)
        frames[f].append(("pcm", ch, sid, 2))

    n_a = n_b = 0
    b_notes, b_stops, a_notes = [], [], []
    for t, kind, r, v in pcm_events:
        if kind == "a":
            regs_a[r] = v
            if r == 0 and not v & 0x80:
                for ch in range(6):
                    if v >> ch & 1:
                        start = ((regs_a[0x18 + ch] << 8) | regs_a[0x10 + ch]) << 8
                        stop = (((regs_a[0x28 + ch] << 8) | regs_a[0x20 + ch]) << 8) | 0xFF
                        att = (63 - (regs_a[1] & 0x3F)) * 0.75 + (31 - (regs_a[8 + ch] & 0x1F)) * 0.75
                        if regs_a[8 + ch] & 0xC0:
                            a_notes.append((t, start, stop, att))
                            n_a += 1
        else:
            regs_b[r - 0x10] = v
            if r == 0x10 and v & 0x80:
                start = ((regs_b[3] << 8) | regs_b[2]) << 8
                stop = (((regs_b[5] << 8) | regs_b[4]) << 8) | 0xFF
                delta_n = (regs_b[0x0A] << 8) | regs_b[0x09]
                rate = delta_n / 65536 * (YM_CLOCK / 144)
                level = regs_b[0x0B]
                if rate > 500 and level:
                    if opt.adpcm_b_to_fm:
                        b_notes.append((t, start, stop, rate, level, bool(v & 0x10)))
                    else:
                        start_pcm(t, "b", start, stop, -20 * math.log10(level / 255), rate)
                    n_b += 1
            elif r == 0x10 and (v & 1 or not v & 0x80) and b_notes and b_notes[-1][-1] is not None:
                b_stops.append(t)
    pcm_notes = []
    if a_notes:
        ref = min(a for *_, a in a_notes)          # a nota mais forte da música = volume cheio
        pcm_notes += [(t, "a", start, stop, att - ref, ADPCMA_RATE) for t, start, stop, att in a_notes]
    if b_notes:
        msg, rest = adpcm_b_fm(board, b_notes, b_stops, frames, frame_of, n_frames, end)
        report.append(msg)
        # sem altura definida (voz, efeito, batida): vai como amostra, na taxa em que toca
        pcm_notes += [(t, "b", start, stop, -20 * math.log10(max(level, 1) / 255), rate)
                      for t, start, stop, rate, level, _rep in rest]
    pcm_notes.sort(key=lambda x: x[0])
    remap.update(fit_sample_slots([note_key(*a) for a in pcm_notes], 63))
    if remap:
        report.append(f"ADPCM: {len(remap)} variações de volume juntadas à mais próxima da mesma amostra "
                      "(o XGM aceita 63 amostras por música)")
    for args in pcm_notes:
        start_pcm(*args)
    if data:
        frames[0].insert(0, ("fm", 0, 0x2B, 0x80))
        kb = sum(-(-len(s) // 256) * 256 for s in data.values()) / 1024
        report.append(f"ADPCM: {len(pcm_notes)} notas → PCM nos canais {', '.join(map(str, opt.pcm_channels))} do XGM, "
                      f"{len(data)} amostras, {kb:.0f} KB na ROM")
    if quiet:
        report.append(f"ADPCM: {quiet} notas abaixo de {opt.min_level_db:.0f} dB (ecos) ficaram de fora")
    if dropped:
        report.append(f"Atenção: {dropped} notas de ADPCM sem lugar (o XGM aceita 63 amostras por música)")

    # ---- peças metálicas → ruído do PSG
    if metal_noise:
        # um golpe novo corta o anterior (um canal de ruído só): volume do ruído quadro a quadro
        vol = np.full(n_frames + 1, 15, dtype=int)
        hits = set()
        for f, env in sorted(metal_noise, key=lambda x: x[0]):
            seg = [max(0, min(15, int(round((att - NOISE_FULL_DB) / 2)))) for att in env]
            seg = seg[: n_frames - f]
            vol[f:f + len(seg)] = seg
            vol[f + len(seg):] = 15
            hits.add(f)
        last = None
        for f in range(n_frames):
            if f in hits:
                frames[f].append(("psg", 0xE4))      # ruído branco, o mais agudo
            if vol[f] != last or f in hits:
                frames[f].append(("psg", 0xF0 | int(vol[f])))
                last = vol[f]
        report.append(f"Chimbais e pratos: {len(metal_noise)} notas no ruído do PSG")

    if over is not None:
        report.append(over.summary())

    # ---- SSG → PSG
    if ssg_conv is not None:
        conv = ssg_conv
        report += ["SSG: " + x for x in conv.report[1:]]
        for i, fr in enumerate(conv.stream.frames[:n_frames]):
            if metal_noise:            # o ruído é das peças metálicas
                fr = [w for w in fr if not (w[0] == "psg" and (w[1] & 0xE0) in (0xE0,) and w[1] & 0x80)]
            frames[i] = fr + frames[i] if i == 0 else frames[i] + fr
    if over is not None:                  # por último: vale sobre o que o SSG escreveu no mesmo quadro
        for i, ws in enumerate(over.frames):
            frames[i] += ws

    loop_frame = None if loop is None else frame_of(loop)
    stream = Stream(frames=frames, fps=FPS, loop_frame=loop_frame, pcm_samples=data, pcm_rate=PCM_RATE)
    if loop_frame is not None:
        loop_len = max(1, int((end - loop) / spf + 1e-6))
        stream.stats["loop_end_frame"] = loop_frame + loop_len
        stream.stats["seamless_loop"] = True
        report.append(f"Loop: volta ao quadro {loop_frame}, a cada {loop_len} quadros ({loop_len / FPS:.2f} s)")
    return stream, report


# ============================================================================ transbordo para o PSG
OVERFLOW_FRAMES = 4                       # nota no canal PCM há até 4 quadros (67 ms): seria cortada
PSG_MIN_HZ = 3579545 / (32 * 1023)        # ≈ 109 Hz, a nota mais grave do tom do PSG


def fit_sample_slots(keys, limit: int = 63) -> dict:
    """Faz as amostras caberem no limite do XGM juntando volumes da mesma fonte, em vez de deixar de
    fora as notas cujas amostras aparecem por último na música.

    ``keys``: chave de cada nota, ``(…fonte, passo de volume)`` (None = não usa PCM). Enquanto houver
    chaves demais, na fonte com mais variações de volume os dois passos mais próximos viram um só (o
    mais tocado fica). Devolve {chave juntada: chave que fica}."""
    from collections import Counter
    use = Counter(k for k in keys if k is not None)
    groups = {}
    for k in use:
        groups.setdefault(k[:-1], set()).add(k[-1])
    remap = {}
    total = len(use)
    while total > limit:
        cand = [g for g, v in groups.items() if len(v) > 1]
        if not cand:
            break                                  # fontes demais: o resto fica de fora
        g = max(cand, key=lambda g: (len(groups[g]), -sum(use[g + (v,)] for v in groups[g])))
        steps = sorted(groups[g])
        i = min(range(len(steps) - 1),
                key=lambda i: (steps[i + 1] - steps[i], use[g + (steps[i],)] + use[g + (steps[i + 1],)]))
        a, b = g + (steps[i],), g + (steps[i + 1],)
        keep, drop = (a, b) if use[a] >= use[b] else (b, a)
        groups[g].discard(drop[-1])
        use[keep] += use.pop(drop)
        for k, v in remap.items():
            if v == drop:
                remap[k] = keep
        remap[drop] = keep
        total -= 1
    return remap


def _psg_free_frames(ssg_frames, n_frames: int) -> np.ndarray:
    """free[canal, quadro]: o tom do PSG (0–2) está mudo na conversão do SSG."""
    free = np.ones((3, n_frames), dtype=bool)
    vol = [15, 15, 15]
    noise_on_tone3 = False
    for f in range(n_frames):
        for w in ssg_frames[f] if f < len(ssg_frames) else ():
            if w[0] != "psg":
                continue
            b = w[1]
            if b & 0x90 == 0x90 and (b >> 5) & 3 < 3:
                vol[(b >> 5) & 3] = b & 15
            elif b & 0xF0 == 0xE0 and b & 3 == 3:
                noise_on_tone3 = True         # o ruído segue a frequência do tom 3
        for c in range(3):
            free[c, f] = vol[c] == 15
    if noise_on_tone3:
        free[2, :] = False
    return free


class _Overflow:
    """Imitação no PSG das notas de ADPCM que sumiriam por falta de canal PCM:

    * com altura definida (sinos, ataques de orquestra, baixo…): onda quadrada na nota da amostra;
    * bumbo e tons (som grave, sem altura estável): onda quadrada descendo de 330 para 110 Hz;
    * caixa (ruidosa, com corpo): ruído branco, se o ruído do PSG estiver livre.

    O volume segue o da amostra quadro a quadro. Vozes e efeitos (sem altura, agudos) não têm
    imitação que preste e continuam no PCM."""

    def __init__(self, n_frames: int, psg_free: np.ndarray, noise_free: bool):
        self.n = n_frames
        self.free = psg_free.copy()
        self.noise_until = 0 if noise_free else n_frames + 1
        self.frames = [[] for _ in range(n_frames)]
        self.kind_cache: dict = {}
        self.count = {"tom": 0, "bumbo": 0, "caixa": 0}
        self.failed = 0

    def _kind(self, y: np.ndarray, key):
        if key not in self.kind_cache:
            from mdconv.pcm.kit import is_metal, piece_features
            kind, hz = None, 0.0
            if len(y) > 256:
                feat = piece_features(y, int(PCM_RATE))
                per = _f0(np.asarray(y, dtype=np.float64))
                if per > 0 and 60.0 <= PCM_RATE / per <= 4000.0:
                    kind, hz = "tom", PCM_RATE / per
                    while hz < PSG_MIN_HZ:
                        hz *= 2
                elif feat["low"] >= 0.4 and feat["centroid_hz"] < 1200:
                    kind = "bumbo"
                elif not is_metal(feat) and 0.05 <= feat["low"] < 0.4 and feat["centroid_hz"] < 4000:
                    kind = "caixa"
            self.kind_cache[key] = (kind, hz)
        return self.kind_cache[key]

    def place(self, f: int, y: np.ndarray, key) -> bool:
        from mdconv.pcm.kit import NOISE_FULL_DB, _noise_envelope
        kind, hz = self._kind(y, key)
        if kind is None:
            self.failed += 1
            return False
        env = _noise_envelope(np.asarray(y, dtype=np.float64) * 0.7, PCM_RATE)
        vols = [max(0, min(15, int(round((a - NOISE_FULL_DB) / 2)))) for a in env][: self.n - f]
        while vols and vols[-1] == 15:
            vols.pop()
        if not vols:
            self.failed += 1
            return False
        if kind == "caixa":
            if self.noise_until > f:
                self.failed += 1
                return False
            self.noise_until = f + len(vols) + 1
            self.frames[f].append(("psg", 0xE4))
            for k, v in enumerate(vols):
                self.frames[f + k].append(("psg", 0xF0 | v))
            if f + len(vols) < self.n:
                self.frames[f + len(vols)].append(("psg", 0xFF))
            self.count[kind] += 1
            return True
        # canal de tom livre (do SSG e de outro transbordo) pelo maior trecho
        best, run = None, 0
        for c in range(3):
            r = 0
            while f + r < self.n and r < len(vols) + 1 and self.free[c, f + r]:
                r += 1
            if r > run:
                best, run = c, r
        if best is None or run < 2:
            self.failed += 1
            return False
        vols = vols[: run - 1]
        for k, v in enumerate(vols):
            h = hz if kind == "tom" else max(PSG_MIN_HZ, 330.0 * 2 ** (-k / 3))
            per = max(1, min(1023, int(round(3579545 / (32 * h)))))
            self.frames[f + k] += [("psg", 0x80 | (best << 5) | (per & 15)), ("psg", (per >> 4) & 0x3F),
                                   ("psg", 0x90 | (best << 5) | v)]
            self.free[best, f + k] = False
        self.frames[f + len(vols)].append(("psg", 0x90 | (best << 5) | 15))
        self.free[best, f + len(vols)] = False
        self.count[kind] += 1
        return True

    def summary(self) -> str:
        n = sum(self.count.values())
        return (f"Transbordo para o PSG: {n} nota(s) que seriam cortadas no PCM foram imitadas no PSG "
                f"({self.count['tom']} com altura, {self.count['bumbo']} bumbo/tom, {self.count['caixa']} caixa)"
                + (f"; {self.failed} sem imitação ou sem canal livre continuaram no PCM" if self.failed else ""))


def _f0(x: np.ndarray) -> float:
    """Período fundamental (em amostras) por autocorrelação, num trecho depois do ataque."""
    n = len(x)
    if n < 256:
        return 0.0
    seg = x[min(n // 4, 2048): min(n // 4, 2048) + 4096]
    seg = seg - seg.mean()
    if len(seg) < 256 or not np.any(seg):
        return 0.0
    ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
    hi = min(len(ac) - 1, 1500)
    neg = np.flatnonzero(ac[1:hi] < 0)
    if not len(neg):
        return 0.0
    lo = int(neg[0]) + 1                      # depois do primeiro vale: fora do pico do lag 0
    k = lo + int(np.argmax(ac[lo:hi]))
    if ac[k] < 0.3 * ac[0]:
        return 0.0
    # o primeiro pico com pelo menos 85% do maior (evita pegar uma oitava abaixo)
    for j in range(lo + 1, k):
        if ac[j] >= 0.85 * ac[k] and ac[j] >= ac[j - 1] and ac[j] >= ac[j + 1]:
            k = j
            break
    # interpolação parabólica do pico
    if 0 < k < len(ac) - 1:
        a, b_, c = ac[k - 1], ac[k], ac[k + 1]
        den = a - 2 * b_ + c
        if den:
            return float(k + 0.5 * (a - c) / den)
    return float(k)


def sample_patch(x: np.ndarray, period: float):
    """Patch FM (algoritmo 7) com os 4 harmônicos mais fortes de um período da amostra e um
    decaimento parecido com o dela."""
    from mdconv.fm.patch import Operator, Patch
    n = int(round(period))
    seg = x[min(len(x) // 4, 2048):][: n * 16]
    if len(seg) < n * 4:
        seg = x[: n * 16]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    reps = len(seg) / n
    h = np.array([spec[int(round(m * reps))] if int(round(m * reps)) < len(spec) else 0 for m in range(1, 16)])
    if h.max() <= 0:
        h[0] = 1
    top = sorted(np.argsort(h)[::-1][:4] + 1)
    amax = max(h[m - 1] for m in top)
    # decaimento: queda do RMS em 0,3 s → D2R aproximado
    env = np.sqrt(np.convolve(x ** 2, np.ones(256) / 256, mode="valid")) if len(x) > 512 else np.abs(x)
    peak = float(env.max()) or 1.0
    tail = float(env[-1]) / peak
    d2r = 0 if tail > 0.5 else 4 if tail > 0.2 else 8 if tail > 0.05 else 12
    ops = [Operator(mul=int(m), tl=min(127, 8 + int(round(-20 * math.log10(max(h[m - 1] / amax, 1e-4)) / 0.75))),
                    ar=31, d1r=0, d2r=d2r, sl=0, rr=8) for m in top]
    return Patch(alg=7, fb=0, ops=ops, name="ADPCM-B")


def adpcm_b_fm(board: Board, notes, stops, frames, frame_of, n_frames, end, fc: int = 4) -> str:
    """Notas do ADPCM-B → FM ``fc`` (FM 5): altura pela taxa de reprodução e o período da amostra,
    timbre pelos harmônicos da própria amostra."""
    from mdconv.chips.emulator import NTSC_FM_CLOCK
    from mdconv.fm.patch import SLOT_OFS, freq_to_block_fnum
    port, cc = (0, fc) if fc < 3 else (1, fc - 3)
    keyreg = cc + (4 if fc >= 3 else 0)
    info = {}
    cur_patch = None
    played = 0
    rest = []
    for i, (t, start, stop, rate, level, rep) in enumerate(notes):
        src = (start, stop)
        if src not in info:
            rom = _rom_b(board)
            x = decode_adpcm_b(rom[start:min(stop + 1, len(rom))])
            per = _f0(x)
            info[src] = (per, sample_patch(x, per) if per else None, len(x))
        per, patch, n = info[src]
        if not per:
            rest.append(notes[i])
            continue
        hz = rate / per
        f = min(n_frames - 1, frame_of(t))
        nxt = notes[i + 1][0] if i + 1 < len(notes) else end
        stop_t = min([nxt] + [s for s in stops if s > t] + ([t + int(n / rate * VGM_RATE)] if not rep else []))
        fo = min(n_frames - 1, max(f + 1, frame_of(stop_t)))
        w = frames[f]
        w.append(("fm", 0, 0x28, keyreg))
        if cur_patch is not patch:
            w.extend(("fm",) + x for x in patch.channel_writes(fc, "C"))
            cur_patch = patch
        att = int(round(-20 * math.log10(max(level, 1) / 255) / 0.75))
        w.extend(("fm", port, 0x40 + cc + SLOT_OFS[k], min(127, patch.ops[k].tl + att)) for k in patch.carriers)
        b, fn = freq_to_block_fnum(hz, NTSC_FM_CLOCK)
        w += [("fm", port, 0xA4 + cc, (b << 3) | (fn >> 8)), ("fm", port, 0xA0 + cc, fn & 0xFF),
              ("fm", 0, 0x28, 0xF0 | keyreg)]
        frames[fo].append(("fm", 0, 0x28, keyreg))
        played += 1
    msg = f"ADPCM-B: {played} notas com altura no FM {fc + 1} (timbre tirado da própria amostra)"
    if rest:
        msg += f"; {len(rest)} sem altura definida vão como amostra PCM"
    return msg, rest


def export_board(board: Board, out_dir, select=None, log=print, progress=None, options: Options | None = None) -> list:
    from mdconv.capcom_opn import file_name
    from mdconv.vgmconv import write_outputs
    out = Path(out_dir)
    files = []
    cmds = [c for c in board.musics if select is None or c in select]
    seen = {}
    for k, cmd in enumerate(cmds):
        if progress:
            progress("som", k, len(cmds))
        try:
            cap = board.capture(cmd)
            sig = hash((tuple(cap.writes), cap.end, cap.loop))
            if sig in seen:
                log(f"· {cmd:02X}: igual à {seen[sig]:02X} (não gravada de novo)")
                continue
            seen[sig] = cmd
            if not any(r == 0x28 and v & 0xF0 or (ym == 1 and r == 0 and not v & 0x80 and v & 0x3F)
                       for _, ym, r, v in cap.writes):
                log(f"· {cmd:02X}: não toca nada")
                continue
            stream, report = to_stream(board, cap, options)
            name = board.name(cmd)
            stem = file_name(f"{cmd:02X}_{name}")
            log(f"▶ {cmd:02X} {name}: {cap.end / VGM_RATE:.1f} s"
                + (f", loop a partir de {cap.loop / VGM_RATE:.1f} s" if cap.loop is not None else ""))
            gd3 = {"titulo": name, "jogo": board.title, "autor": "SNK", "title": name, "game": board.title}
            files += write_outputs(stream, report + cap.report, gd3, out / "musicas" / (stem + ".vgm"),
                                   out / "musicas", stem, True, True, True, log, "YM2610")
        except Exception as exc:
            log(f"✖ {cmd:02X}: {exc}")
    if progress:
        progress("som", len(cmds), len(cmds))
    return files
