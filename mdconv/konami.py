"""Konami Bubble System / GX400 (Nemesis, Gradius): música e efeitos rodando o programa de som.

A placa de som tem um Z80 a 1,789772 MHz, dois AY-3-8910 e o K005289 (2 vozes de tabela de onda
de 32 passos, lidas de duas PROMs de 4 bits). Mapa de memória e ligações conferidos no driver
``nemesis.cpp`` do MAME:

* 0000–3FFF ROM, 4000–47FF RAM; E001 lê o latch de comandos (o 68000 escreve e dispara a IRQ);
* E006 / E106 / E086: endereço / escrita / leitura do AY 1; E005 / E405 / E205: o mesmo no AY 2;
* A000–AFFF e C000–CFFF: o *endereço* da escrita é a altura das vozes 1 e 2 do K005289
  (``pitch = 0xFFF − deslocamento``); E003 / E004 aplicam a altura (``tg1``/``tg2``);
* portas A e B do AY 2 (registradores 14 e 15) → controle das vozes 1 e 2 do K005289: volume nos
  4 bits de baixo (linear), forma de onda nos 3 de cima;
* porta A do AY 1 (registrador 14) → relógio: ``(ciclos / 512) & 15 | 0xD0``. Não há interrupção
  periódica: o programa marca o andamento lendo esse relógio, e a IRQ só vem com um comando.

Altura do K005289: f = 3 579 545 / (32 · pitch) Hz. Os efeitos (só AY 1) saem como efeito de
chip (.c para o SGDK); as músicas (AY 2 + K005289) saem em XGM/VGM, com o K005289 no FM — cada
forma de onda vira um patch de 4 portadoras com os 4 harmônicos mais fortes da tabela.
"""
from __future__ import annotations

import hashlib
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from mdconv.z80 import Z80

VGM_RATE = 44100
CPU_CLOCK = 1_789_772
AY_CLOCK = 1_789_772
K005289_CLOCK = 3_579_545

PROFILES = {
    "nemesis": dict(
        title="Nemesis", board="Konami Bubble System / GX400 (Z80 + 2× AY-3-8910 + K005289)",
        sound=("456-d09.9c",), proms=("400-a01.fse", "400-a02.fse"),
        musics=(0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x33, 0x81),
        # comandos que tocam alguma coisa (os outros de 01–2A não soam)
        effects=(0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x12,
                 0x18, 0x1A, 0x1C, 0x1E, 0x20, 0x22, 0x24, 0x27, 0x2A),
        names={0x33: "Jingle 33", 0x81: "Jingle 81"}),
}


@dataclass
class Capture:
    cmd: int
    ay: list                     # (amostra, chip, reg, valor) — chip 0 = AY 1 (efeitos), 1 = AY 2
    k: list                      # (amostra, voz, pitch, volume, onda) a cada mudança do K005289
    end: int                     # amostra final
    loop: int | None             # amostra do loop (None = toca uma vez)
    report: list = field(default_factory=list)

    @property
    def is_music(self) -> bool:
        return any(vl for _s, _v, _p, vl, _w in self.k) or any(
            s > 0 and c == 1 and r in (8, 9, 10) and v & 0x1F for s, c, r, v in self.ay)


class Board:
    def __init__(self, key: str, rom: bytes, proms: bytes):
        self.key = key
        self.p = PROFILES[key]
        self.title = self.p["title"]
        self.rom = rom
        self.proms = proms
        self.ram = bytearray(0x800)
        self.cpu = Z80(self._rd, self._wr)
        self._boot = None

    # ------------------------------------------------------------- barramento
    def _rd(self, a):
        if a < 0x4000:
            return self.rom[a]
        if 0x4000 <= a < 0x4800:
            return self.ram[a - 0x4000]
        if a == 0xE001:
            return self.latch
        if a == 0xE086:
            if self.addr[0] == 14:
                v = ((self.cpu.cycles // 512) & 0x0F) | 0xD0
                if self.watch:
                    self._poll(v)
                return v
            return self.regs[0][self.addr[0] & 15]
        if a == 0xE205:
            return self.regs[1][self.addr[1] & 15]
        return 0xFF

    def _wr(self, a, v):
        if 0x4000 <= a < 0x4800:
            self.ram[a - 0x4000] = v
            return
        t = self.cpu.cycles
        if a == 0xE006:
            self.addr[0] = v
        elif a == 0xE005:
            self.addr[1] = v
        elif a == 0xE106 or a == 0xE405:
            c = 0 if a == 0xE106 else 1
            r = self.addr[c] & 15
            self.regs[c][r] = v
            if c == 1 and r in (14, 15):                     # portas do AY 2 → K005289
                k = self.kv[r - 14]
                k["vol"], k["wave"] = v & 0x0F, (v >> 5) & 7
                self._k_event(t, r - 14)
            elif r < 14:
                self.log.append((t, c, r, v))
        elif 0xA000 <= a < 0xB000:
            self.kv[0]["next"] = 0xFFF - (a - 0xA000)
        elif 0xC000 <= a < 0xD000:
            self.kv[1]["next"] = 0xFFF - (a - 0xC000)
        elif a in (0xE003, 0xE004):
            k = self.kv[a - 0xE003]
            k["pitch"] = k["next"]
            self._k_event(t, a - 0xE003)

    def _k_event(self, t, voice):
        k = self.kv[voice]
        self.klog.append((t, voice, k["pitch"], k["vol"], k["wave"]))

    def _poll(self, timer):
        """Leitura do relógio: guarda o estado (PC, relógio, RAM) — repetido = loop exato."""
        key = (self.cpu.pc, timer, hashlib.blake2b(bytes(self.ram), digest_size=12).digest())
        if key == self.last_key:
            return
        self.last_key = key
        if self.exact_loop is None:
            if key in self.seen:
                self.exact_loop = (self.seen[key], self.cpu.cycles)
            else:
                self.seen[key] = self.cpu.cycles

    # --------------------------------------------------------------- execução
    def _reset_chips(self):
        self.latch = 0
        self.addr = [0, 0]
        self.regs = [[0] * 16 for _ in range(2)]
        self.kv = [dict(pitch=0, next=0, vol=0, wave=0) for _ in range(2)]
        self.log, self.klog = [], []
        self.watch = False
        self.seen, self.last_key, self.exact_loop = {}, None, None

    def _run(self, cycles: int, irq: bool = False):
        cpu = self.cpu
        target = cpu.cycles + cycles
        pending = irq
        while cpu.cycles < target:
            if pending and cpu.iff1 and not cpu.ei_delay:
                cpu.irq()
                pending = False
                continue
            if cpu.halted:
                cpu.cycles = target
                break
            cpu.step()

    _CPU_FIELDS = ("a", "f", "b", "c", "d", "e", "h", "l", "a_", "f_", "b_", "c_", "d_", "e_", "h_", "l_",
                   "ix", "iy", "sp", "pc", "i", "r", "iff1", "iff2", "im", "halted", "ei_delay", "cycles")

    def boot(self):
        """Liga a placa uma vez (teste de RAM, soma da ROM, inicialização: ~0,9 s) e guarda o estado."""
        if self._boot is None:
            self.ram[:] = bytes(len(self.ram))
            self.cpu.reset()
            self._reset_chips()
            self._run(int(1.2 * CPU_CLOCK))
            self._boot = (bytes(self.ram), {f: getattr(self.cpu, f) for f in self._CPU_FIELDS},
                          [list(r) for r in self.regs], [dict(k) for k in self.kv], list(self.addr))
        ram, cpu, regs, kv, addr = self._boot
        self.ram[:] = ram
        for f, v in cpu.items():
            setattr(self.cpu, f, v)
        self._reset_chips()
        self.regs, self.kv, self.addr = [list(r) for r in regs], [dict(k) for k in kv], list(addr)

    def _audible(self) -> bool:
        for c in (0, 1):
            mix = self.regs[c][7]
            for ch in range(3):
                if self.regs[c][8 + ch] & 0x1F and (not mix >> ch & 1 or not mix >> (ch + 3) & 1):
                    return True
        return any(k["vol"] and k["pitch"] for k in self.kv)

    def capture(self, cmd: int, max_seconds: float = 240.0, chunk: float = 8.0) -> Capture:
        self.boot()
        t0 = self.cpu.cycles
        # estado dos chips na hora do comando (deixado pela inicialização)
        init_ay = [(t0, c, r, self.regs[c][r]) for c in (0, 1) for r in range(14)]
        self.watch = True
        self.latch = cmd
        self._run(int(0.002 * CPU_CLOCK), irq=True)
        last_sound = t0
        end_c = loop_c = None
        report = []
        while self.cpu.cycles - t0 < max_seconds * CPU_CLOCK:
            self._run(int(chunk * CPU_CLOCK))
            now = self.cpu.cycles
            if self._audible():
                last_sound = now
            if self.exact_loop is not None:
                a, b = self.exact_loop
                if self._sounds_between(a, b):
                    loop_c, end_c = a, b
                    report.append("loop exato (o estado da placa se repete)")
                else:
                    end_c = self._last_activity(t0, a) + int(0.3 * CPU_CLOCK)
                break
            if now - last_sound > 3 * CPU_CLOCK and not self._audible():
                end_c = self._last_activity(t0, now) + int(0.3 * CPU_CLOCK)
                break
            nl = find_note_loop(self._notes(t0))
            if nl is not None:
                s, p = nl
                loop_c, end_c = t0 + s, t0 + s + p
                report.append(f"loop pelas notas (período de {p / CPU_CLOCK:.2f} s)")
                break
        if end_c is None:
            end_c = self.cpu.cycles
            report.append(f"sem loop nem fim em {max_seconds:.0f} s: gravado até aí")
        self.watch = False

        def smp(t):
            return int(round((t - t0) * VGM_RATE / CPU_CLOCK))
        ay = [(0, c, r, v) for _, c, r, v in init_ay] + [(smp(t), c, r, v) for t, c, r, v in self.log if t < end_c]
        k = [(smp(t), vo, pi, vl, wv) for t, vo, pi, vl, wv in self.klog if t < end_c]
        return Capture(cmd, ay, k, smp(end_c), None if loop_c is None else smp(loop_c), report)

    def _sounds_between(self, a, b) -> bool:
        if any(a <= t < b for t, *_ in self.klog) and any(vl for t, _v, _p, vl, _w in self.klog if t < b):
            return True
        return any(a <= t < b for t, *_ in self.log) and self._audible()

    def _last_activity(self, t0, upto) -> int:
        ts = [t for t, c, r, v in self.log if t < upto and (r in (8, 9, 10) and v & 0x1F or r < 6)]
        ts += [t for t, *_ in self.klog if t < upto]
        return max(ts, default=t0)

    def _notes(self, t0):
        """Eventos de nota por voz (para achar o loop): alturas do AY (com volume) e do K005289."""
        ev = {}
        for t, c, r, v in self.log:
            if r < 6:
                ev.setdefault(("ay", c, r >> 1), []).append((t - t0, (r, v)))
        for t, vo, pi, vl, wv in self.klog:
            ev.setdefault(("k", vo), []).append((t - t0, (pi, vl, wv)))
        return ev

    def name(self, cmd: int) -> str:
        if cmd in self.p["names"]:
            return self.p["names"][cmd]
        return f"Música {cmd:02X}" if cmd in self.p["musics"] else f"Efeito {cmd:02X}"

    @property
    def commands(self):
        return tuple(self.p["musics"]) + tuple(self.p["effects"])


def find_note_loop(voices: dict, min_period: float = 2.0 * CPU_CLOCK):
    """(início, período) em ciclos, pelas sequências de notas de cada voz: cada voz tem o seu menor
    período que se repete (pelo menos 2 voltas inteiras no fim da gravação); o da música é o maior
    deles, e o loop começa onde todas as vozes já estão repetindo. O andamento da placa oscila um
    pouco (o relógio é lido por *polling*), então o período é a mediana das voltas."""
    found = []
    for key, ev in voices.items():
        seq = [k for _, k in ev]
        ts = [t for t, _ in ev]
        n = len(seq)
        best = None
        for p in range(2, n // 2 + 1):
            if seq[n - p:] == seq[n - 2 * p:n - p]:
                spans = sorted(ts[j + p] - ts[j] for j in range(n - 2 * p, n - p))
                best = (p, spans[len(spans) // 2])
                break
        if best is None:
            if n >= 8:
                return None                     # voz ativa que ainda não repetiu: grava mais
            continue
        p, per = best
        i = n - 2 * p
        while i > 0 and seq[i - 1] == seq[i - 1 + p]:
            i -= 1
        found.append((per, ts[i], ts[-1]))
    if not found:
        return None
    period = max(f[0] for f in found)
    if period < min_period:
        return None
    for per, _s, _l in found:                    # os períodos menores têm de caber no maior
        if abs(period / per - round(period / per)) > 0.05:
            return None
    start = max(f[1] for f in found)
    last = max(f[2] for f in found)
    if last - start < 2 * period * 0.98:
        return None
    return start, period


def load_board(path) -> Board:
    p = Path(path)
    if p.suffix.lower() != ".zip":
        raise ValueError("abra o .zip do MAME")
    with zipfile.ZipFile(p) as z:
        names = {Path(n).name.lower(): n for n in z.namelist()}
        for key, prof in PROFILES.items():
            if all(f in names for f in prof["sound"] + prof["proms"]):
                rom = b"".join(z.read(names[f]) for f in prof["sound"])
                proms = b"".join(z.read(names[f]) for f in prof["proms"])
                if len(rom) == 0x4000 and len(proms) == 0x200:
                    return Board(key, rom, proms)
    raise ValueError("não é um jogo Konami com som conhecido")


def is_board_source(path) -> bool:
    try:
        load_board(path)
        return True
    except Exception:
        return False


# ============================================================================ K005289 → FM
def wave_patch(table: bytes):
    """Patch FM (algoritmo 7, 4 portadoras) com os 4 harmônicos mais fortes da forma de onda."""
    import numpy as np

    from mdconv.fm.patch import Operator, Patch
    s = np.array([b & 15 for b in table], dtype=float)
    s -= s.mean()
    h = np.abs(np.fft.rfft(s))[1:16]                     # harmônicos 1–15 (MUL do YM2612)
    if h.max() <= 0:
        h[0] = 1.0
    top = sorted(np.argsort(h)[::-1][:4] + 1)
    amax = max(h[m - 1] for m in top)
    ops = []
    for m in top:
        a = h[m - 1] / amax
        tl = 10 + (127 if a < 1e-3 else int(round(-20 * math.log10(a) / 0.75)))
        ops.append(Operator(mul=int(m), tl=min(127, tl), ar=31, d1r=0, d2r=0, sl=0, rr=15))
    return Patch(alg=7, fb=0, ops=ops, name="K005289")


def k_to_stream(stream, cap: Capture, proms: bytes, fm_channels=(2, 3), fps: float = 60.0):
    """Acrescenta as 2 vozes do K005289 ao fluxo de quadros (canais FM ``fm_channels``)."""
    from mdconv.chips.emulator import NTSC_FM_CLOCK
    from mdconv.fm.patch import SLOT_OFS, freq_to_block_fnum
    frames = stream.frames
    n = len(frames)
    patches = [[wave_patch(proms[v * 256 + w * 32: v * 256 + w * 32 + 32]) for w in range(8)] for v in range(2)]
    state = [dict(pitch=0, vol=0, wave=0) for _ in range(2)]
    events = sorted(cap.k)
    last = {}
    on = [False, False]
    loop_f = stream.loop_frame
    ei = 0
    for f in range(n):
        if loop_f is not None and f == loop_f and f > 0:
            last.clear()
            for v in range(2):
                fc = fm_channels[v]
                frames[f].append(("fm", 0, 0x28, (fc if fc < 3 else fc - 3) + (4 if fc >= 3 else 0)))
            on = [False, False]
        while ei < len(events) and int(round(events[ei][0] / VGM_RATE * fps)) <= f:
            _, v, pi, vl, wv = events[ei]
            state[v].update(pitch=pi, vol=vl, wave=wv)
            ei += 1
        for v in range(2):
            fc = fm_channels[v]
            port, cc = (0, fc) if fc < 3 else (1, fc - 3)
            keyreg = cc + (4 if fc >= 3 else 0)
            st = state[v]
            pt = patches[v][st["wave"]]
            sounding = st["vol"] > 0 and st["pitch"] > 0
            if sounding:
                if last.get(("p", v)) != st["wave"]:
                    last[("p", v)] = st["wave"]
                    last.pop(("tl", v), None)
                    frames[f].extend(("fm",) + w for w in pt.channel_writes(fc, "C"))
                att = int(round(-20 * math.log10(st["vol"] / 15) / 0.75))
                tls = tuple(min(127, pt.ops[i].tl + att) for i in pt.carriers)
                if last.get(("tl", v)) != tls:
                    last[("tl", v)] = tls
                    frames[f].extend(("fm", port, 0x40 + cc + SLOT_OFS[i], tl) for i, tl in zip(pt.carriers, tls))
                hz = K005289_CLOCK / (32 * st["pitch"])
                bf = freq_to_block_fnum(hz, NTSC_FM_CLOCK)
                if last.get(("f", v)) != bf:
                    last[("f", v)] = bf
                    frames[f] += [("fm", port, 0xA4 + cc, (bf[0] << 3) | (bf[1] >> 8)),
                                  ("fm", port, 0xA0 + cc, bf[1] & 0xFF)]
                if not on[v]:
                    frames[f].append(("fm", 0, 0x28, 0xF0 | keyreg))
                    on[v] = True
            elif on[v]:
                frames[f].append(("fm", 0, 0x28, keyreg))
                on[v] = False
    return stream


# ============================================================================ exportação
def to_ay(board: Board, cap: Capture, chips=(0, 1)):
    from mdconv.ay.convert import AyVgm
    w = [x for x in cap.ay if x[1] in chips]
    if len(chips) == 1:
        w = [(s, 0, r, v) for s, _, r, v in w]
    return AyVgm(AY_CLOCK, len(chips) > 1, w, max(1, cap.end), cap.loop,
                 {"title": board.name(cap.cmd), "game": board.title, "titulo": board.name(cap.cmd),
                  "jogo": board.title, "autor": "Konami", "system": "Arcade (Konami Bubble System)"})


def trim_capture(cap: Capture) -> Capture:
    """Tira o silêncio antes do primeiro som (AY com volume ou K005289 com volume)."""
    starts = [s for s, c, r, v in cap.ay if r in (8, 9, 10) and v & 0x1F and s > 0]
    starts += [s for s, _v, _p, vl, _w in cap.k if vl]
    if not starts:
        return cap
    t0 = max(0, min(starts) - 1)
    if cap.loop is not None and cap.loop < t0:
        t0 = cap.loop
    ay = [(max(0, s - t0), c, r, v) for s, c, r, v in cap.ay]
    k = [(max(0, s - t0), *x) for s, *x in cap.k]
    return Capture(cap.cmd, ay, k, cap.end - t0, None if cap.loop is None else cap.loop - t0, cap.report)


def channels_for(cmd: int):
    """Canais padrão dos efeitos (tons do PSG, FM). As músicas usam os 3 tons do PSG (AY 2), os
    FM 1–2 (partes graves do AY) e 3–4 (K005289); o FM 6 fica com o PCM do XGM."""
    return (), (4,)


def export_board(board: Board, out_dir, select=None, log=print, progress=None, channels=None) -> list:
    from mdconv import chipsfx
    from mdconv.ay.convert import convert
    from mdconv.capcom_opn import file_name
    from mdconv.vgmconv import write_outputs
    out = Path(out_dir)
    files = []
    cmds = [c for c in board.commands if select is None or c in select]
    for k, cmd in enumerate(cmds):
        if progress:
            progress("som", k, len(cmds))
        try:
            cap = trim_capture(board.capture(cmd))
            name = board.name(cmd)
            stem = file_name(f"{cmd:02X}_{name}")
            if cap.is_music:
                used = tuple(sorted({c for s, c, r, v in cap.ay if s > 0 and r in (8, 9, 10) and v & 0x1F}))
                src = to_ay(board, cap, chips=used or (0, 1))
                conv = convert(src, trim=False, fm_slots=[0, 1])
                k_to_stream(conv.stream, cap, board.proms)
                report = conv.report + cap.report
                if used == (1,):                     # o conversor numera os chips de 1; aqui é o AY 2
                    report = [r.replace("AY1 canal", "AY2 canal") for r in report]
                if cap.k:
                    report.append("K005289 (2 vozes de tabela de onda) → FM 3 e 4, um patch por forma de onda")
                log(f"▶ {cmd:02X} {name}: {cap.end / VGM_RATE:.1f} s"
                    + (f", loop a partir de {cap.loop / VGM_RATE:.1f} s" if cap.loop is not None else ""))
                files += write_outputs(conv.stream, report, src.gd3, out / "musicas" / (stem + ".vgm"),
                                       out / "musicas", stem, True, True, True, log, "AY-3-8910 + K005289")
            else:
                psg, fm = (channels or {}).get(cmd) or channels_for(cmd)
                sx = chipsfx.from_ay(to_ay(board, cap, chips=(0,)), f"{cmd:02X}_{name}", psg_free=psg, fm_free=fm)
                files += chipsfx.write_outputs(sx, out / "efeitos", game=board.title)
                log(f"✔ efeito {cmd:02X}: {len(sx.frames)} quadros" + (" (contínuo)" if sx.loop is not None else ""))
        except Exception as exc:
            log(f"✖ {cmd:02X}: {exc}")
    if progress:
        progress("som", len(cmds), len(cmds))
    return files
