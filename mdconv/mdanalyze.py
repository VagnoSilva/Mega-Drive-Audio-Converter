"""Análise de compatibilidade de ROMs de Mega Drive: monta sozinha um perfil de extração.

Para jogos cujo driver de som roda no **Z80** (a maioria). O jogo é ligado no emulador (68000 + Z80,
VBlank, VDP mínimo) e roda até começar a música da abertura. Nesse trecho, o 68000 escreve o pedido
de música na RAM do Z80 (a "caixa de correio" do driver). A análise:

1. acha o começo da música (notas em pelo menos 4 canais durante 5 s) e as escritas do 68000 na RAM
   do Z80 perto dele (fora a cópia do programa do driver, que são centenas de bytes de uma vez);
2. salva o estado da máquina antes dessas escritas e, voltando a ele várias vezes, troca o valor de
   cada escrita: a que muda a música é o **campo do número da música**;
3. testa os 256 valores desse campo e guarda os que tocam uma música diferente do "silêncio" (o
   valor que menos toca nota), agrupando os que dão a mesma música.

O resultado é um perfil ``driver="boot"`` (JSON) que ``mdconv.megadrive`` usa para extrair: liga o
jogo até o mesmo ponto, troca o campo pelo número da música, deixa o 68000 entregar o pedido e
grava o que o Z80 toca sozinho.

Não funciona para todo jogo: drivers que mandam blocos de vários bytes ou que precisam do banco da
música em outro campo dão poucos resultados, e jogos que só tocam depois de apertar Start ainda não
são cobertos. O relatório diz o que foi achado.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from mdconv.chips.md68k import EV_BRK, EV_RAM, EV_REGS, EV_YM0, EV_Z80, Machine

FRAME_HZ = 53693175 / (3420 * 262)
BOOT_SECONDS = 60
TEST_SECONDS = 10


def press_start(m: Machine, f: int) -> None:
    """Controle durante o boot: a partir de 8 s, aperta Start por 2 quadros a cada 3 s (passa telas de
    título que esperam o botão). Sempre o mesmo, para a extração repetir o boot igual."""
    sec = f / FRAME_HZ
    if sec > 8 and int(sec * 20) % 60 < 2:
        m.set_pad("start")
    else:
        m.set_pad()


def boot(m: Machine, frames: int, pad: bool = True, start: int = 0) -> None:
    """Roda o jogo quadro a quadro (com o controle, se ``pad``) do quadro ``start`` até ``frames``."""
    if not pad:
        m.set_pad()
        m.run_game(frames - start)
        return
    for f in range(start, frames):
        press_start(m, f)
        m.run_game(1)


def profiles_dir() -> Path:
    """Pasta dos perfis criados pela análise (um .json por jogo)."""
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    d = Path(base) / "MegaDriveAudioConverter" / "perfis_megadrive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _keyons(ev):
    return ev[(ev["type"] == EV_YM0) & (ev["addr"] == 0x28) & ((ev["val"] & 0xF0) != 0)]


def _keynotes(ev):
    """(canal, F-number) de cada key-on, na ordem — a "impressão digital" de uma música."""
    out, fn = [], {}
    for e in ev[ev["type"] <= 1]:
        p, r, v = int(e["type"]), int(e["addr"]), int(e["val"])
        if r & 0xFC == 0xA4:
            fn[(p, r & 3)] = ((v & 0x3F) << 8) | (fn.get((p, r & 3), 0) & 0xFF)
        elif r & 0xFC == 0xA0:
            fn[(p, r & 3)] = (fn.get((p, r & 3), 0) & 0x3F00) | v
        elif p == 0 and r == 0x28 and v & 0xF0:
            c = v & 7
            out.append((c, fn.get((1 if c & 4 else 0, c & 3), 0)))
    return out


def _music_start(ev):
    """Quadro em que começa música de verdade: ≥30 notas em ≥3 canais nos 5 s seguintes."""
    k = _keyons(ev)
    fr = k["frame"].astype(int)
    ch = (k["val"] & 7).astype(int)
    win = int(FRAME_HZ * 5)
    for i in range(len(k)):
        sel = (fr >= fr[i]) & (fr < fr[i] + win)
        if sel.sum() >= 30 and len(set(ch[sel])) >= 3:
            return int(fr[i])
    return None


class _Probe:
    """Volta ao estado salvo, troca escritas e mede a música que sai a partir de ``skip`` segundos."""

    def __init__(self, m: Machine, state: bytes, secs: float, skip: float = 0.0, f0: int = 0, ram: bool = False):
        self.m, self.state, self.secs, self.f0, self.ram = m, state, secs, f0, ram
        m.load_state(state)
        self.t0 = int(m.lib.md_mclk() * 44100 // 53693175) + int(max(0.0, skip) * 44100)

    def run(self, overrides: dict):
        m = self.m
        m.load_state(self.state)
        m.set_zoverride(-1, 0)
        m.set_roverride(-1, 0)
        for (a, k), v in overrides.items():
            (m.set_roverride if self.ram else m.set_zoverride)(a, v, k)
        boot(m, self.f0 + int(FRAME_HZ * self.secs), start=self.f0)
        return _fingerprint(m.events(), self.t0)


def _jac(a, b):
    return len(a & b) / max(1, len(a | b))


def _seqmatch(a, b):
    n = min(len(a), len(b))
    return sum(1 for i in range(n) if a[i] == b[i]) / n if n else 1.0


def _differs(o, ref):
    """Outra música: notas diferentes (conjunto) ou outra ordem de notas."""
    return _jac(o["set"], ref["set"]) < 0.5 or _seqmatch(o["seq"], ref["seq"]) < 0.4


def _call_target(rom: bytes, ret: int):
    """Destino do jsr/bsr que deixou o endereço de retorno ``ret`` na pilha (None se não der para saber)."""
    if not (8 < ret < len(rom) and ret % 2 == 0):
        return None
    w = lambda a: (rom[a] << 8) | rom[a + 1]
    if w(ret - 6) == 0x4EB9:
        return int.from_bytes(rom[ret - 4:ret], "big") & 0xFFFFFF
    if w(ret - 4) == 0x4EB8:
        return w(ret - 2)
    if w(ret - 4) in (0x4EBA, 0x6100):
        return ret - 2 + int.from_bytes(rom[ret - 2:ret], "big", signed=True)
    if (w(ret - 2) & 0xFF00) == 0x6100 and w(ret - 2) & 0xFF:
        return ret + int.from_bytes(rom[ret - 1:ret], "big", signed=True)
    return None


def _outer_call(rom: bytes, sp: int, isp: int, stack: bytes, with_ret: bool = False):
    """Na pilha de um key-on, a chamada mais de fora dentro da interrupção: a rotina do driver que o
    VBlank chama a cada quadro (com ``with_ret``: (rotina, endereço de volta no VBlank))."""
    out = ret = None
    for i in range(0, min(508, (isp - sp) & 0xFFFF), 2):
        r = int.from_bytes(stack[i:i + 4], "big")
        t = _call_target(rom, r)
        if t is not None:
            out, ret = t, r
    return (out, ret) if with_ret else out


class _Boot:
    """O boot do jogo com o controle, guardando estados a cada segundo para voltar a qualquer quadro."""

    def __init__(self, m: Machine, frames: int, rom: bytes):
        self.m, self.states, self.updates, self.rets = m, {}, {}, {}
        m.reset()
        for f in range(frames):
            if f % 60 == 0:
                self.states[f] = m.save_state()
            if f % 15 == 0:
                m.keystack_arm()
            press_start(m, f)
            m.run_game(1)
            sp, isp, _, st = m.keystack()
            if sp:
                u, r = _outer_call(rom, sp, isp, st, with_ret=True)
                if u is not None:
                    self.updates[u] = self.updates.get(u, 0) + 1
                    self.rets[u] = r
                m.keystack_arm()

    def goto(self, f: int) -> None:
        b = max(k for k in self.states if k <= f)
        self.m.load_state(self.states[b])
        boot(self.m, f, start=b)


def _fingerprint(ev, t0: int) -> dict:
    k = _keyons(ev)
    late = int((k["t44"] >= t0 + 44100 * 5).sum())
    kn = _keynotes(ev[ev["t44"] >= t0 + 22050])
    return dict(notes=len(k), late=late, seq=tuple(kn[:40]), set=frozenset(kn))


def _sp(m: Machine) -> int:
    sp = m.lib.md_get_reg(15) & 0xFFFFFF
    return sp - 0x100 if sp > 0xFF0200 else 0xFFFF00


def play_request(m: Machine, entry: int, d0: int, d1: int = 0) -> None:
    """Pede o som do jeito do jogo que chama o driver direto (jsr entrada, com D0 = número, D1 = parâmetro)."""
    m.set_reg(1, d1)
    m.call(entry, d0, max_cycles=4_000_000, sp=_sp(m))


def play68(m: Machine, update, frames: int, on_frame=None) -> None:
    """Toca o driver do 68000 sozinho: chama a(s) rotina(s) ``update`` uma vez por quadro (o jogo fica
    parado)."""
    m.set_frame_auto(True)
    sp = _sp(m)
    ups = list(update) if isinstance(update, (list, tuple)) else [update]
    for f in range(frames):
        for u in ups:
            m.call(u, 0, max_cycles=1_000_000, sp=sp)
        m.run_frames(1)
        if on_frame and on_frame(f):
            break


class _Probe68:
    """Põe o número da música direto na RAM (no quadro antes do pedido do jogo) e toca só o driver."""

    def __init__(self, bt: _Boot, update: int, fw: int, secs: float):
        self.bt, self.update, self.fw, self.secs = bt, update, fw, secs
        bt.goto(fw)
        self.state = bt.m.save_state()

    def run(self, addr: int, x: int, secs: float | None = None):
        m = self.bt.m
        m.load_state(self.state)
        m.clear_events()
        t0 = int(m.lib.md_mclk() * 44100 // 53693175)
        m.ram()[addr & 0xFFFF] = x
        try:
            play68(m, self.update, int(FRAME_HZ * (secs or self.secs)))
        except RuntimeError:                       # valor que trava o driver: não é música
            return dict(notes=0, late=0, seq=(), set=frozenset())
        return _fingerprint(m.events(), t0)


def _group(runs: dict):
    """Valor sem som e as músicas diferentes (agrupa os valores que tocam a mesma)."""
    silent = min(runs, key=lambda x: runs[x]["notes"])
    quiet = runs[silent]
    reps: list[tuple[dict, list[int]]] = []
    for x, o in runs.items():
        chans = len({c for c, _ in o["set"]})
        tune = o["late"] >= 10 or (o["notes"] >= 16 and chans >= 3)      # música com loop ou vinheta curta
        if not (tune and (not quiet["set"] or _differs(o, quiet)) and o["seq"]):
            continue
        for rep, vals in reps:
            if not _differs(o, rep):
                vals.append(x)
                break
        else:
            reps.append((o, [x]))
    return silent, sorted(vals[0] for _, vals in reps)


def _title(h: dict, path: str) -> str:
    """Nome do jogo: o do cabeçalho, se for legível; senão o do arquivo, sem "(Japan)", "(En)"…
    (alguns cartuchos japoneses gravam o título com caracteres próprios que viram lixo)."""
    import re
    for t in (h["overseas"], h["domestic"]):
        t = " ".join(t.split())
        ok = sum(c.isalnum() or c in " '&-:.!,?" for c in t)
        if t and ok >= 0.9 * len(t) and sum(c.isalpha() for c in t) >= 3 and "@" not in t:
            return t
    stem = re.sub(r"\s*[\(\[].*?[\)\]]", "", Path(path).stem)
    return " ".join(stem.replace("_", " ").split()) or "Jogo sem nome"


def analyze(path: str, log=print, progress=None) -> dict:
    """Analisa a ROM e devolve o perfil (também gravado em ``profiles_dir()``)."""
    from mdconv.megadrive import _read_rom, header, is_md_rom
    rom = _read_rom(path)
    if not is_md_rom(rom):
        raise ValueError("não é uma ROM de Mega Drive")
    h = header(rom)
    title = _title(h, path)
    log(f"Analisando {title} ({h['serial']}, checksum {h['checksum']:04X})")
    t_start = time.time()
    m = Machine(rom)
    bt = _Boot(m, int(FRAME_HZ * BOOT_SECONDS), rom)
    k68, kz = m.keyons()
    ev = m.events()
    log(f"  {BOOT_SECONDS} s de jogo (apertando Start de vez em quando): {len(_keyons(ev))} notas FM; "
        f"tocadas pelo 68000: {k68}, pelo Z80: {kz}")
    if k68 == 0 and kz == 0:
        raise ValueError("o jogo não tocou nenhuma nota FM nos primeiros 60 s (o emulador ainda não roda "
                         "este jogo, ou a música só começa mais adiante)")
    T = _music_start(ev)
    if T is None:
        raise ValueError("não achei música na abertura (só efeitos ou silêncio nos primeiros 60 s)")
    log(f"  música começa em {T / FRAME_HZ:.1f} s")
    seq68 = kz >= k68 and _z80_needs_68k(bt, ev, T)
    if seq68:
        log("  o Z80 toca as notas, mas quem segue a partitura é o 68000 (manda os comandos quadro a quadro)")
        _find_sender(bt, ev, T, rom)
    if kz >= k68 and not seq68:
        res = _analyze_z80(bt, ev, T, log, progress)
        board = "Mega Drive, driver no Z80 (perfil criado pela análise)"
    else:
        res = _analyze_68k(bt, rom, T, log, progress)
        board = "Mega Drive, driver no 68000 (perfil criado pela análise)"
    log(f"  {len(res['songs'])} música(s) diferente(s) (valor sem som: {res['silent']}); "
        f"{time.time() - t_start:.0f} s de análise")
    prof = dict(
        title=title.title() if title.isupper() else title,
        short="_".join("".join(c if c.isalnum() else " " for c in title).split())[:40],
        board=board, author="",
        serial=h["serial"][:10], checksum=h["checksum"], driver="boot",
        sha1=hashlib.sha1(rom).hexdigest(), pad=True,
        names={}, analyzed=time.strftime("%Y-%m-%d %H:%M"), **res,
    )
    p = profiles_dir() / f"{prof['short']}_{h['checksum']:04X}.json"
    p.write_text(json.dumps(prof, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"  perfil gravado em {p}")
    log("  para compartilhar com todo mundo: copie o arquivo para a pasta perfis_megadrive do projeto")
    return prof


def _z80_needs_68k(bt: _Boot, ev, T: int) -> bool:
    """Com a música tocando, para o 68000 por 5 s: se o Z80 também para de tocar notas, a sequência
    da música está no 68000 (Mortal Kombat II e outros) e o Z80 só executa os comandos."""
    m = bt.m
    bt.goto(T + 120)
    m.clear_events()
    boot(m, T + 420, start=T + 120)
    playing = len(_keyons(m.events()))
    bt.goto(T + 120)
    m.clear_events()
    m.run_frames(300)
    frozen = len(_keyons(m.events()))
    return playing >= 10 and frozen < playing // 5


def _find_sender(bt: _Boot, ev, T: int, rom: bytes) -> None:
    """Acha a rotina do 68000 que manda os comandos para o Z80: pilha na escrita mais frequente."""
    m = bt.m
    z = ev[(ev["type"] == EV_Z80) & (ev["frame"] >= T) & (ev["frame"] < T + 600)]
    if not len(z):
        return
    a, c = np.unique(z["addr"], return_counts=True)
    za = int(a[np.argmax(c)])
    bt.goto(T)
    bt.updates = {}
    for f in range(T, T + 600):
        if f % 3 == 0:
            m.keystack_arm(za)
        press_start(m, f)
        m.run_game(1)
        sp, isp, _, st = m.keystack()
        if sp:
            u = _outer_call(rom, sp, isp, st)
            if u is not None:
                bt.updates[u] = bt.updates.get(u, 0) + 1


def _analyze_68k(bt: _Boot, rom: bytes, T: int, log, progress) -> dict:
    """Driver no 68000: acha a rotina que o VBlank chama (pela pilha dos key-ons) e o byte da RAM onde o
    jogo pede a música (escrito e zerado logo depois pelo driver, perto do começo da música)."""
    m = bt.m
    if not bt.updates:
        raise ValueError("a música roda no 68000, mas não achei a rotina do driver chamada pelo VBlank")
    upd = max(bt.updates, key=bt.updates.get)
    log(f"  driver no 68000: rotina ${upd:06X} chamada a cada quadro")
    f1 = max(0, T - 180)
    bt.goto(f1)
    m.clear_events()
    m.log_ram(True)
    boot(m, T + 180, start=f1)
    m.log_ram(False)
    r = m.events()
    r = r[r["type"] == EV_RAM]
    r = r[np.lexsort((np.arange(len(r)), r["addr"]))]
    a, v, fr = r["addr"].astype(int), r["val"].astype(int), r["frame"].astype(int)
    cands = []
    for A, i0, c in zip(*np.unique(a, return_index=True, return_counts=True)):
        va, fa = v[i0:i0 + c], fr[i0:i0 + c]
        keep = np.ones(c, bool)
        keep[1:] = va[1:] != va[:-1]           # só as mudanças (o driver pode reescrever 0 todo quadro)
        va, fa = va[keep], fa[keep]
        if len(va) > 40:
            continue
        for i in range(len(va) - 1):
            idle = int(va[i - 1]) if i > 0 else 0                # valor de "nada pedido" (0, $80…)
            if va[i] != idle and va[i + 1] == idle and fa[i + 1] - fa[i] <= 2:   # pedido que o driver consumiu
                cands.append((int(A), int(va[i]), int(fa[i]), idle))
    if not cands:
        raise ValueError("não achei o pedido de música na RAM perto do começo da música")
    log(f"  {len(cands)} byte(s) candidato(s) na RAM; testando qual escolhe a música…")
    fields = []
    for i, (A, V, fw, idle) in enumerate(cands):
        if progress:
            progress("campo", i, len(cands))
        pr = _Probe68(bt, upd, fw, 4.0)
        base = pr.run(A, idle)
        outs = set()
        for x in (V, (V + 1) & 255, (V + 2) & 255):
            o = pr.run(A, x)
            if o["notes"] >= 10 and _differs(o, base):
                outs.add(o["seq"])
        if outs:
            fields.append((len(outs), -fw, A, V, fw, idle))
    if not fields:
        res = _analyze_call(bt, rom, upd, T, log, progress)
        if res:
            return res
        raise ValueError("nenhum byte da RAM escolhe a música: o driver deste jogo usa um protocolo que a "
                         "análise automática ainda não entende")
    fields.sort(reverse=True)
    _, _, A, V, fw, idle = fields[0]
    log(f"  pedido de música: byte $FF{A:04X} da RAM, valor da abertura {V}")
    pr = _Probe68(bt, upd, fw, TEST_SECONDS)
    runs = {}
    for x in range(256):
        if progress:
            progress("músicas", x, 256)
        runs[x] = pr.run(A, x)
    silent, songs = _group(runs)
    return dict(cpu="68k", update=upd, boot_frame=fw, field=[0xFF0000 | A, 0], songs=songs, silent=silent,
                opening=V)


def _jump_table(rom: bytes, addr: int) -> list[int]:
    """Entradas da tabela de saltos do driver em que ``addr`` está (bra.w de 4 em 4 bytes, ou jmp abs.l
    de 6 em 6). Vazio se ``addr`` não for uma entrada de tabela."""
    w = lambda a: (rom[a] << 8) | rom[a + 1] if 0 <= a < len(rom) - 1 else 0
    for op, step in ((0x6000, 4), (0x4EF9, 6)):
        if w(addr) != op:
            continue
        a = addr
        while w(a - step) == op:
            a -= step
        out = []
        while w(a) == op:
            out.append(a)
            a += step
        if len(out) >= 2:
            return out
    return []


def _analyze_call(bt: _Boot, rom: bytes, upd: int, T: int, log, progress):
    """Driver que o jogo chama direto (``jsr entrada`` com o número do som em D0), como o de Streets of
    Rage 2: o driver tem uma tabela de entradas; o VBlank chama uma ou mais delas a cada quadro e outra
    recebe os pedidos. Acha a entrada dos pedidos, o número da música da abertura e varre a família."""
    m = bt.m
    table = _jump_table(rom, upd)
    if not table:
        return None
    ups = [upd]
    r = bt.rets.get(upd)
    while r is not None and r + 6 <= len(rom) and rom[r:r + 2] == b"\x4E\xB9":     # jsr seguidos no VBlank
        t = int.from_bytes(rom[r + 2:r + 6], "big") & 0xFFFFFF
        if t not in table or t in ups:
            break
        ups.append(t)
        r += 6
    others = [e for e in table if e not in ups]
    if not others:
        return None
    log(f"  o jogo chama o driver direto: tabela em ${table[0]:06X}, a cada quadro "
        + ", ".join(f"${u:06X}" for u in ups))
    k = _keyons(m.events())
    first = int(k["frame"][(k["frame"] >= max(0, T - 900))][0]) if len(k) else T
    f1 = max(0, first - 300)
    bt.goto(f1)
    m.clear_events()
    m.set_breaks(others)
    boot(m, T + 600, start=f1)
    m.set_breaks([])
    ev = m.events()
    calls = []
    for i in np.nonzero(ev["type"] == EV_BRK)[0]:
        if i + 1 < len(ev) and ev["type"][i + 1] == EV_REGS:
            calls.append((int(ev["frame"][i]), others[int(ev["val"][i])], int(ev["addr"][i + 1]),
                          int(ev["val"][i + 1])))
    # uma chamada de cada "família" (entrada + byte alto de D0): música, efeitos e vozes costumam ter
    # famílias diferentes; fica a que toca mais música
    fams = {}
    for c in calls:
        if c[2] > 0xFF:
            fams.setdefault((c[1], c[2] & 0xFF00), c)
    if not fams:
        return None

    def probe_at(fw, entry, d0, d1, secs):
        bt.goto(fw)
        m.clear_events()
        t0 = int(m.lib.md_mclk() * 44100 // 53693175)
        try:
            play_request(m, entry, d0, d1)
            play68(m, ups, int(FRAME_HZ * secs))
        except RuntimeError:
            return dict(notes=0, late=0, seq=(), set=frozenset())
        return _fingerprint(m.events(), t0)

    best = max(fams.values(), key=lambda c: probe_at(c[0], c[1], c[2], c[3], 8)["late"])
    fw, entry, V, d1 = best
    log(f"  pedido de música: entrada ${entry:06X}, D0 = ${V:04X} (quadro {fw})")
    bt.goto(fw)
    state = m.save_state()

    def probe(d0, secs):
        m.load_state(state)
        m.clear_events()
        t0 = int(m.lib.md_mclk() * 44100 // 53693175)
        try:
            play_request(m, entry, d0, d1)
            play68(m, ups, int(FRAME_HZ * secs))
        except RuntimeError:
            return dict(notes=0, late=0, seq=(), set=frozenset())
        return _fingerprint(m.events(), t0)

    if probe(V, 6)["notes"] < 10:
        log("  chamando o driver sozinho, a música não toca")
        return None
    hi = V & 0xFF00
    runs = {}
    for x in range(256):
        if progress:
            progress("músicas", x, 256)
        runs[hi | x] = probe(hi | x, TEST_SECONDS)
    silent, songs = _group(runs)
    # depois do fim da tabela de músicas o driver lê lixo (toca ruído): corta no primeiro buraco de 3+
    # números seguidos sem música acima do da abertura
    keep, gap = [], 0
    for x in range(hi, hi + 256):
        if x in songs:
            keep.append(x)
            gap = 0
        elif x > V:
            gap += 1
            if gap >= 3:
                break
    songs = keep
    return dict(cpu="68k", update=ups[0], updates=ups, request=dict(entry=entry, d1=d1), boot_frame=fw,
                field=[0, 0], songs=songs, silent=silent, opening=V)


def _analyze_z80(bt: _Boot, ev, T: int, log, progress) -> dict:
    m = bt.m
    z = ev[ev["type"] == EV_Z80]
    fr, cnt = np.unique(z["frame"], return_counts=True)
    heavy = set(int(x) for x in fr[cnt > 64])            # cópia do programa do driver
    # num quadro de cópia, só valem os endereços escritos de novo depois da cópia (alguns jogos, como o
    # Altered Beast, mandam o número da música junto com o programa do driver)
    redo = set()
    for f in heavy:
        za = z["addr"][z["frame"] == f]
        a, c = np.unique(za, return_counts=True)
        redo |= {(f, int(x)) for x in a[c >= 2]}
        redo |= {(f, int(x)) for x in za[-8:]}              # escritas logo depois da cópia
    occ, last = {}, {}
    for e in z:
        a, f, v = int(e["addr"]), int(e["frame"]), int(e["val"])
        k = occ.get(a, 0)
        occ[a] = k + 1
        if (f in heavy and (f, a) not in redo) or not (T - 600 <= f <= T + 120):
            continue
        last[(a, v)] = (a, k, v, f)          # a mesma escrita repetida: vale a mais perto da música
    cands = sorted(last.values(), key=lambda c: -c[3])[:40]
    if not cands:
        raise ValueError("o 68000 não escreveu na RAM do Z80 perto do começo da música")
    f0 = min(c[3] for c in cands)
    before = {}
    for e in z[z["frame"] < f0]:
        before[int(e["addr"])] = before.get(int(e["addr"]), 0) + 1
    cands = [(a, k - before.get(a, 0), v) for a, k, v, _ in cands]
    bt.goto(f0)
    # a comparação olha só do começo da música da abertura em diante (o que tocava antes é igual em todos)
    probe = _Probe(m, m.save_state(), min(40.0, (T + 120 - f0) / FRAME_HZ + TEST_SECONDS),
                   skip=(T - f0) / FRAME_HZ - 0.5, f0=f0)
    base = probe.run({})
    log(f"  {len(cands)} escrita(s) candidata(s) na RAM do Z80; testando qual muda a música…")
    fields = []
    for i, (a, k, v) in enumerate(cands):
        if progress:
            progress("campo", i, len(cands))
        outs = set()
        for x in ((v ^ 1) & 255, (v + 1) & 255, (v + 2) & 255, (v + 5) & 255):
            o = probe.run({(a, k): x})
            if o["late"] >= 10 and _differs(o, base):
                outs.add(o["seq"])
        if outs:
            fields.append((len(outs), a, k, v))
    if not fields:
        raise ValueError("nenhuma das escritas muda a música: o driver deste jogo usa um protocolo que a análise "
                         "automática ainda não entende")
    fields.sort(key=lambda t: -t[0])
    _, A, K, V = fields[0]
    log(f"  campo do número da música: ${A:04X} (escrita {K + 1} nesse endereço), valor da abertura {V}")
    runs = {}
    for x in range(256):
        if progress:
            progress("músicas", x, 256)
        runs[x] = probe.run({(A, K): x})
    silent, songs = _group(runs)
    return dict(cpu="z80", boot_frame=f0, field=[A, K], songs=songs, silent=silent, opening=V)


def project_profiles_dir() -> Path:
    """Perfis que vêm com o projeto (pasta ``perfis_megadrive`` ao lado do ``run_gui.py``; no executável,
    dentro dele). É onde a galera junta os perfis para todo mundo que baixar o conversor."""
    return Path(__file__).resolve().parent.parent / "perfis_megadrive"


def _read_profiles(d: Path, prefix: str) -> dict:
    out = {}
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(p, dict) or "serial" not in p:          # ex.: versoes_testadas.json
            continue
        p["names"] = {int(k): v for k, v in p.get("names", {}).items()}
        p["_file"] = str(f)
        out[prefix + f.stem] = p
    return out


def load_user_profiles() -> dict:
    """Perfis em .json: os do projeto (``perfis_megadrive``) e os seus (pasta do usuário, onde a análise
    grava). Se os dois tiverem o mesmo jogo (mesmo código e checksum), vale o seu."""
    proj = _read_profiles(project_profiles_dir(), "proj:")
    user = _read_profiles(profiles_dir(), "user:")
    mine = {(p["serial"], p.get("checksum")) for p in user.values()}
    out = {k: p for k, p in proj.items() if (p["serial"], p.get("checksum")) not in mine}
    out.update(user)
    return out


# ------------------------------------------------------------------------- compartilhar perfis
def profile_json(prof: dict) -> str:
    """O perfil como .json para compartilhar (sem os campos internos do programa)."""
    out = {k: v for k, v in prof.items() if not k.startswith("_")}
    out["names"] = {str(k): v for k, v in prof.get("names", {}).items()}
    return json.dumps(out, indent=1, ensure_ascii=False) + "\n"


def profile_filename(prof: dict) -> str:
    return f"{prof.get('short') or 'perfil'}_{int(prof.get('checksum', 0)):04X}.json"


def export_profile(prof: dict, dest: str | Path) -> Path:
    """Grava uma cópia do perfil em ``dest`` (arquivo, ou pasta: usa o nome padrão)."""
    d = Path(dest)
    if d.is_dir():
        d = d / profile_filename(prof)
    d.write_text(profile_json(prof), encoding="utf-8")
    return d


def export_all_profiles(dest: str | Path) -> list[Path]:
    """Copia todos os perfis da pasta do usuário para a pasta ``dest`` (para mandar de uma vez)."""
    d = Path(dest)
    d.mkdir(parents=True, exist_ok=True)
    return [export_profile(p, d / profile_filename(p)) for p in _read_profiles(profiles_dir(), "").values()]


def import_profile(src: str | Path) -> Path:
    """Copia um perfil recebido (.json) para a pasta de perfis do usuário. Erro se não for um perfil."""
    try:
        prof = json.loads(Path(src).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"não consegui ler {Path(src).name}: {e}") from e
    if not isinstance(prof, dict) or not {"serial", "checksum", "songs", "driver"} <= prof.keys():
        raise ValueError(f"{Path(src).name} não é um perfil de jogo do conversor")
    return export_profile(prof, profiles_dir() / profile_filename(prof))


def project_dir_writable() -> bool:
    """Rodando do código-fonte (não do executável) e com a pasta perfis_megadrive gravável?"""
    import sys
    d = project_profiles_dir()
    return not getattr(sys, "frozen", False) and d.is_dir() and os.access(d, os.W_OK)
