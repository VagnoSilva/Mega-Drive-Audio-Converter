"""Capcom pré-CPS (Z80 + 2× YM2203): música e efeitos rodando o próprio programa de som do jogo.

Ninguém documentou o formato das partituras dessa família (Commando, Ghosts 'n Goblins…). Em vez
de decifrá-lo, a placa de som é emulada: o código Z80 original roda em ``mdconv.z80`` com a
memória e a interrupção da placa (as do MAME), recebe cada comando de som pelo latch e o que
ele escreve nos dois YM2203 vira a linha do tempo de um VGM — que segue pelo conversor do YM2203
(``mdconv.opn``) ou, nos efeitos só de SSG, pelo de efeitos de chip (``mdconv.chipsfx``).

O loop é achado sozinho: a placa é determinística, então quando a RAM e os registradores do Z80
voltam a um estado já visto no início de uma interrupção, tudo dali em diante se repete.
"""
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from mdconv.z80 import Z80

VGM_RATE = 44100

PROFILES = {
    "gng": dict(
        title="Ghosts 'n Goblins", board="Capcom (Z80 + 2× YM2203)",
        files=(("gg2.bin",),), rom_size=0x8000, ram=(0xC000, 0x800), latch=0xC800, ym=0xE000,
        cpu_clock=3_000_000, irq_hz=240, ym_clock=1_500_000, commands=range(0x01, 0x40),
        names={0x1C: "1st Lap Clear", 0x25: "1st Place Entry End", 0x26: "1st Place Name Registration",
               0x27: "Below 2nd Place Entry End", 0x28: "Below 2nd Place Name Registration",
               0x29: "Final Stage BGM (5, 6 Stage)", 0x2A: "Great Demon King Stage (7 Stage Boss)",
               0x2B: "Ground BGM (1, 2 Stage)", 0x2D: "Theme of Giant (1, 2 Stage Boss)",
               0x2E: "Theme of Satan (5, 6 Stage Boss)", 0x2F: "Game Over",
               0x30: "Stage Introduction Map", 0x31: "Player Out", 0x32: "2nd Lap Clear",
               0x33: "Cave BGM (3, 4 Stage)", 0x34: "Theme of Dragon (3, 4 Stage Boss)", 0x35: "Credit",
               0x36: "Start Demo", 0x37: "Extend", 0x38: "Theme of Great Demon King",
               0x39: "Unused Jingle", 0x3B: "Stage Clear (2)", 0x3E: "Stage Clear (1)"}),
}


@dataclass
class Capture:
    cmd: int
    writes: list                 # (tique, chip, reg, valor); as do pré-rolo ficam no tique 0
    end: int                     # tique final
    loop: int | None             # tique do loop (None = toca uma vez)
    fm_notes: int = 0
    ssg_notes: int = 0
    report: list = field(default_factory=list)


NOTE_REGS = {0x28, 0xA0, 0xA1, 0xA2, 0xA4, 0xA5, 0xA6, 0, 1, 2, 3, 4, 5, 6}


def find_write_loop(log, n_ticks: int, irq_hz: int, min_seconds: float = 2.0, notes_only: bool = False):
    """(início, fim) do menor período em que as escritas por tique se repetem até o fim da
    gravação (pelo menos duas voltas inteiras), ou None."""
    import numpy as np
    sig = np.zeros(n_ticks, dtype=np.int64)
    per_tick: dict = {}
    for t, c, r, v in log:
        if t < n_ticks and (not notes_only or r in NOTE_REGS):
            per_tick.setdefault(t, []).append((c, r, v))
    for t, ws in per_tick.items():
        sig[t] = hash(tuple(ws)) & 0x7FFFFFFFFFFFFFFF
    n = n_ticks
    for p in range(int(min_seconds * irq_hz), n // 2):
        if np.array_equal(sig[n - p:], sig[n - 2 * p:n - p]):
            i = n - 2 * p
            while i > 0 and sig[i - 1] == sig[i - 1 + p]:
                i -= 1
            if not per_tick.keys() & set(range(i, i + p)):
                return None                     # o período é silêncio: não é loop de música
            return i, i + p
    return None


def find_note_loop(log, n_ticks: int, irq_hz: int, min_seconds: float = 2.0, tol: int = 1):
    """Loop pelas notas (key-on com a frequência do canal), aceitando ±``tol`` tique de diferença:
    alguns drivers deixam uma voz cair um tique antes ou depois de uma volta para a outra."""
    freq = {}
    ev = []
    for t, c, r, v in log:
        if r in (0xA0, 0xA1, 0xA2, 0xA4, 0xA5, 0xA6):
            freq[(c, r)] = v
        elif r == 0x28 and v & 0xF0 and t < n_ticks:
            ch = v & 3
            ev.append((t, (c, ch, freq.get((c, 0xA4 + ch)), freq.get((c, 0xA0 + ch)))))
    if len(ev) < 16:
        return None
    occ = {}
    for t, k in ev:
        occ.setdefault(k, set()).add(t)

    def has(k, t):
        s_ = occ.get(k, ())
        return any((t + d) in s_ for d in range(-tol, tol + 1))

    last_t, last_k = ev[-1]
    cands = sorted({last_t - t for t in occ[last_k] if last_t - t >= min_seconds * irq_hz})
    for p in cands:
        window = [(t, k) for t, k in ev if t >= n_ticks - p]
        if window and sum(has(k, t - p) for t, k in window) >= 0.95 * len(window):
            # volta no tempo enquanto as notas continuam se repetindo
            start = n_ticks - p
            for t, k in reversed([e for e in ev if e[0] < n_ticks - p]):
                if t - p < 0 or not has(k, t + p):
                    break
                start = t
            first = min((t for t, _ in ev if t >= start), default=start)
            return first, first + p
    return None


def earliest_loop_start(log, start: int, period: int, irq_hz: int) -> int:
    """Com o período já achado, o primeiro ponto a partir do qual as notas de uma volta se repetem
    na seguinte (≥95%, ±1 tique) — encurta loops achados tarde."""
    freq = {}
    ev = []
    for t, c, r, v in log:
        if r in (0xA0, 0xA1, 0xA2, 0xA4, 0xA5, 0xA6):
            freq[(c, r)] = v
        elif r == 0x28 and v & 0xF0:
            ch = v & 3
            ev.append((t, (c, ch, freq.get((c, 0xA4 + ch)), freq.get((c, 0xA0 + ch)))))
    if not ev:
        return start
    occ = {}
    for t, k in ev:
        occ.setdefault(k, set()).add(t)
    step = max(1, irq_hz // 8)
    for s0 in range(0, start, step):
        win = [(t, k) for t, k in ev if s0 <= t < s0 + period]
        if not win or win[-1][0] + period >= ev[-1][0] + 1:
            break
        ok = sum(any((t + period + d) in occ.get(k, ()) for d in (-1, 0, 1)) for t, k in win)
        if ok >= 0.95 * len(win):
            return min(t for t, _ in win)
    return start


class Board:
    def __init__(self, key: str, rom: bytes):
        self.key = key
        self.p = PROFILES[key]
        self.title = self.p["title"]
        self.rom = rom
        base, size = self.p["ram"]
        self.ram_base = base
        self.ram = bytearray(size)
        self.latch = 0
        self.addr = [0, 0]
        self.tick = 0
        self.log: list = []
        self.cpu = Z80(self._rd, self._wr)

    # ------------------------------------------------------------- barramento
    def _rd(self, a):
        if a < len(self.rom):
            return self.rom[a]
        o = a - self.ram_base
        if 0 <= o < len(self.ram):
            return self.ram[o]
        if a == self.p["latch"]:
            return self.latch
        return 0x00 if self.p["ym"] <= a < self.p["ym"] + 4 else 0xFF

    def _wr(self, a, v):
        o = a - self.ram_base
        if 0 <= o < len(self.ram):
            self.ram[o] = v
            return
        y = a - self.p["ym"]
        if 0 <= y < 4:
            chip = y >> 1
            if y & 1 == 0:
                self.addr[chip] = v
                if 0x2D <= v <= 0x2F:           # no YM2203, só escrever o endereço já troca o divisor
                    self.log.append((self.tick, chip, v, 0))
            else:
                self.log.append((self.tick, chip, self.addr[chip], v))

    # --------------------------------------------------------------- execução
    def _run_tick(self):
        # IRQ "line hold" (MAME): a linha fica ativa até a CPU aceitar — se ela estiver com as
        # interrupções desligadas, aceita assim que religar.
        per = self.p["cpu_clock"] / self.p["irq_hz"]
        cpu = self.cpu
        self.pending = True
        self.t_next = getattr(self, "t_next", 0.0) + per
        target = int(self.t_next)
        while cpu.cycles < target:
            if self.pending and cpu.iff1 and not cpu.ei_delay:
                cpu.irq()
                self.pending = False
                continue
            if cpu.halted:                      # parado esperando a interrupção: pula direto
                cpu.cycles = target
                break
            cpu.step()
        self.tick += 1

    def _state(self):
        c = self.cpu
        regs = bytes([c.a, c.f, c.b, c.c, c.d, c.e, c.h, c.l, c.a_, c.f_, c.b_, c.c_, c.d_, c.e_, c.h_, c.l_,
                      c.ix >> 8, c.ix & 255, c.iy >> 8, c.iy & 255, c.sp >> 8, c.sp & 255, c.pc >> 8, c.pc & 255,
                      c.iff1, int(c.halted)])
        return hashlib.blake2b(bytes(self.ram) + regs + bytes([int(self.pending)]), digest_size=16).digest()

    def capture(self, cmd: int, max_seconds: float = 150.0, pre_ticks: int = 60) -> Capture:
        """Liga a placa, espera a inicialização, manda ``cmd`` e grava até fechar o loop ou acabar."""
        self.ram[:] = bytes(len(self.ram))
        self.cpu.reset()
        self.t_next = 0.0
        self.pending = False
        self.latch = 0
        self.tick = 0
        self.log = []
        for _ in range(pre_ticks):
            self._run_tick()
        init = [(0, c, r, v) for _, c, r, v in self.log]
        self.log = []
        self.tick = 0
        self.latch = cmd
        seen = {}
        loop = None
        limit = int(max_seconds * self.p["irq_hz"])
        while self.tick < limit:
            st = self._state()
            if st in seen:
                loop = seen[st]
                break
            seen[st] = self.tick
            self._run_tick()
        end = self.tick
        # o estado exato pode demorar a repetir (um contador que corre à parte): procura também a
        # repetição das escritas — todas e, se não houver, só as notas — e fica com o menor loop
        best = None if loop is None else (loop, end)
        for notes_only in (False, True):
            pl = find_write_loop(self.log, end, self.p["irq_hz"], notes_only=notes_only)
            if pl is not None and (best is None or pl[1] - pl[0] < best[1] - best[0] - 2):
                best = pl
            if pl is not None:
                break
        nl = find_note_loop(self.log, end, self.p["irq_hz"])
        if nl is not None and (best is None or nl[1] - nl[0] < 0.9 * (best[1] - best[0])):
            best = nl
        if best is not None:
            loop, end = best
            period = end - loop
            if loop > 0 and self.tick < end + period:      # roda mais uma volta para conferir o começo
                for _ in range(end + period - self.tick):
                    self._run_tick()
            loop = earliest_loop_start(self.log, loop, period, self.p["irq_hz"])
            end = loop + period
            self.log = [x for x in self.log if x[0] < end]
        w = init + self.log
        fm = sum(1 for _, c, r, v in self.log if r == 0x28 and v & 0xF0)
        ssg = sum(1 for _, c, r, v in self.log if r in (8, 9, 10) and v & 0x1F)
        cap = Capture(cmd, w, end, loop, fm, ssg)
        if loop is not None:
            active = [t for t, c, r, v in self.log if t >= loop and ((r == 0x28 and v & 0xF0) or
                                                                     (r in (8, 9, 10) and v & 0x1F))]
            if not active:                      # o "loop" é o silêncio depois do fim: toca uma vez
                last = max((t for t, c, r, v in self.log if (r == 0x28 and v & 0xF0) or
                            (r in (8, 9, 10) and v & 0x1F)), default=0)
                cap.end = min(end, last + self.p["irq_hz"] * 2)
                cap.loop = None
                cap.writes = [x for x in w if x[0] <= cap.end]
        return cap

    def name(self, cmd: int) -> str:
        return self.p["names"].get(cmd, f"Comando {cmd:02X}")


def load_board(path) -> Board:
    p = Path(path)
    if p.suffix.lower() != ".zip":
        raise ValueError("abra o .zip do MAME")
    with zipfile.ZipFile(p) as z:
        names = {Path(n).name.lower(): n for n in z.namelist()}
        for key, prof in PROFILES.items():
            parts = []
            for alts in prof["files"]:
                hit = next((names[a] for a in alts if a in names), None)
                if hit is None:
                    break
                parts.append(z.read(hit))
            else:
                rom = b"".join(parts)
                if len(rom) == prof["rom_size"]:
                    return Board(key, rom)
    raise ValueError("não é um jogo Capcom Z80 + YM2203 conhecido")


def is_board_source(path) -> bool:
    try:
        load_board(path)
        return True
    except Exception:
        return False


def file_name(s: str) -> str:
    """Nome de arquivo legível: sem acento nem símbolo, com o número do comando na frente."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"_+", "_", re.sub(r"[^0-9A-Za-z_]", "_", s)).strip("_")


def to_opn(board: Board, cap: Capture):
    from mdconv.opn.convert import OpnVgm
    k = VGM_RATE / board.p["irq_hz"]
    writes = [(int(round(t * k)), c, r, v) for t, c, r, v in cap.writes]
    end = int(round(cap.end * k))
    loop = None if cap.loop is None else int(round(cap.loop * k))
    return OpnVgm(board.p["ym_clock"], True, writes, end, loop,
                  {"title": board.name(cap.cmd), "game": board.title, "system": f"Arcade ({board.p['board']})",
                   "author": "Capcom", "titulo": board.name(cap.cmd), "jogo": board.title, "autor": "Capcom"})


def ssg_to_ay(board: Board, cap: Capture):
    """Efeito só de SSG → AyVgm (o SSG do YM2203 é um AY com clock = clock/divisor)."""
    from mdconv.ay.convert import AyVgm
    from mdconv.opn.convert import SSG_PRES
    sel = 2
    for _, c, r, v in cap.writes:
        if c == 0 and r in (0x2D, 0x2E, 0x2F):
            sel = {0x2D: sel | 2, 0x2E: sel | 1, 0x2F: 0}[r]
    k = VGM_RATE / board.p["irq_hz"]
    w = [(int(round(t * k)), c, r, v) for t, c, r, v in cap.writes if r < 0x10]
    loop = None if cap.loop is None else int(round(cap.loop * k))
    return AyVgm(int(board.p["ym_clock"] / SSG_PRES[sel]), any(c == 1 for _, c, _, _ in w), w,
                 int(round(cap.end * k)), loop, {"title": board.name(cap.cmd), "game": board.title})


def export_board(board: Board, out_dir, select=None, log=print, progress=None) -> list:
    """musicas/: XGM, VGM e prévia (pelo conversor do YM2203); efeitos/: os comandos só de SSG
    como efeito de chip (VGM, .c para o SGDK, prévia)."""
    from mdconv import chipsfx
    from mdconv.opn.convert import convert
    from mdconv.vgmconv import write_outputs
    out = Path(out_dir)
    files = []
    cmds = [c for c in board.p["commands"] if select is None or c in select]
    for k, cmd in enumerate(cmds):
        if progress:
            progress("som", k, len(cmds))
        try:
            cap = board.capture(cmd)
            if not cap.fm_notes and not cap.ssg_notes:
                continue
            name = board.name(cmd)
            stem = file_name(f"{cmd:02X}_{name}")
            if cap.fm_notes:
                src = to_opn(board, cap)
                conv = convert(src, trim=True)
                log(f"▶ {cmd:02X} {name}: {cap.end / board.p['irq_hz']:.1f} s"
                    + (f", loop a partir de {cap.loop / board.p['irq_hz']:.1f} s" if cap.loop is not None else ""))
                exact = (conv.exact, conv.exact_end, conv.exact_loop) if conv.exact else None
                files += write_outputs(conv.stream, conv.report, src.gd3, out / "musicas" / (stem + ".vgm"),
                                       out / "musicas", stem, True, True, True, log, "YM2203", exact=exact)
            else:
                sx = chipsfx.from_ay(ssg_to_ay(board, cap), f"{cmd:02X}_{name}")
                files += chipsfx.write_outputs(sx, out / "efeitos", game=board.title)
                log(f"✔ efeito {cmd:02X}: {len(sx.frames)} quadros" + (" (contínuo)" if sx.loop is not None else ""))
        except Exception as exc:
            log(f"✖ {cmd:02X}: {exc}")
    if progress:
        progress("som", len(cmds), len(cmds))
    return files
