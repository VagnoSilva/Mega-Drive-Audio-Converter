"""Mega Drive: músicas tiradas da ROM rodando o driver de som do próprio jogo no 68000 emulado.

O núcleo (``mdconv.chips.md68k``, Musashi + mapa de memória do console) chama as rotinas de som
da ROM como o jogo faz: a inicialização uma vez, "tocar música" com o número em D0 e a
atualização uma vez por quadro (59,92 Hz, o VBlank do NTSC). Tudo o que o jogo escreve no YM2612
e no PSG é gravado sem alteração — a música já é do Mega Drive, não há conversão de chip.

Cada jogo precisa de um **perfil** pequeno: os endereços dessas três rotinas, o endereço do
comando de loop do driver (um breakpoint: quando cada faixa executa o "goto", sabemos onde a
música volta) e onde ficam as faixas na RAM.

Há dois tipos de perfil:

* ``driver="68k"`` — a música roda no 68000 (Street Fighter II' SCE): a atualização é chamada a
  cada quadro e o loop vem de um breakpoint no 68000. O Z80 só toca o PCM; o programa dele é
  reproduzido em Python (``Mix2``: 2 canais somados e divididos por 2, ~5,2 kHz).
* ``driver="z80"`` — a música roda no Z80 (Thunder Force IV): o 68000 só carrega o programa e manda
  os comandos; o Z80 (em C, ``z80.c``) roda sozinho, quadro a quadro, com a interrupção do VBlank e
  os timers do YM2612. O loop vem de um breakpoint no Z80 (o IX diz qual faixa voltou). As escritas
  no DAC feitas pelo Z80 viram amostras PCM.

Saem XGM (as amostras originais nos canais PCM do XGM), VGM (tempo exato e o DAC misturado como
no console), .fur e prévia.
"""
from __future__ import annotations

import json
import os
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mdconv.chips.md68k import EVENT, EV_BRK, EV_PSG, EV_YM0, EV_YM1, EV_Z80, EV_ZBRK, Machine

VGM_RATE = 44100
FRAME_HZ = 53693175 / (3420 * 262)          # 59,92 Hz (NTSC)
FM_CLOCK = 7670453
PSG_CLOCK = 3579545
MAX_SECONDS = 300
EXTS = (".md", ".bin", ".gen", ".smd")

PROFILES = {
    "sf2sce": dict(
        title="Street Fighter II' Special Champion Edition", short="SF2_Special_Champion_Edition",
        board="Mega Drive, driver da Capcom no 68000", author="Capcom",
        serial="GM T-12016", checksum=0x1ADD, driver="68k",
        init=((0x8FC4, 0),), play=((0x90FE, "song"),), update=0x934C, loop_pc=0x9686,
        song_table=0x2D0000,
        tracks=tuple([0xB836 + 0x40 * k for k in range(5)] + [0xB976 + 0x20 * k for k in range(5)]),
        active=(0, 0xFF),
        pcm=dict(kind="mix2", table=0x2CF000, regs=(0x1FFE, 0x1FFF), z80_tstates=687),
        names={},
    ),
    "sonic2": dict(
        # SMPS no Z80 (Sega). Perfil da análise automática (mdconv.mdanalyze): o jogo é ligado até o quadro
        # 239; a 2ª escrita do 68000 em $1B88 (fila de música do driver) dali em diante é o pedido da música
        # da abertura ($99), trocado pelo número escolhido. Músicas $81–$9F.
        title="Sonic The Hedgehog 2", short="Sonic_The_Hedgehog_2",
        board="Mega Drive, SMPS no Z80 (Sega)", author="Sega",
        serial="GM 0000105", checksum=0xDEB8, driver="boot",
        boot_frame=239, field=(0x1B88, 1), songs=list(range(0x81, 0xA0)),
        names={},
    ),
    "tf4": dict(
        # Z80 em $16000 (8 KB); caixa de correio na RAM do Z80 em $0003. 0x6C60 escolhe o banco da música
        # (comando $0A) e manda o comando 9 (tocar); 0x6C42 com $FF é o comando 1, que liga o loop ($000E),
        # como o jogo faz logo depois de pedir a música. Música por timers do YM2612 (status lido em $4000).
        title="Thunder Force IV", short="Thunder_Force_IV",
        board="Mega Drive, driver da Technosoft no Z80", author="Technosoft",
        serial="GM T-18063", checksum=0x02F7, driver="z80",
        init=((0x6B3C, 0),), settle=5, play=((0x6C60, "song"), (0x6C42, 0xFFFFFFFF)),
        loop_pc_z80=0x10BE,                    # a faixa terminou e volta para o início do loop (ix+3/4)
        # faixa k (IX = $1992 + $54·k) está tocando enquanto o bit k de $0020 (k < 8) ou o bit 0 de $0021
        # (k = 8) estiver ligado; é o que o 68000 consulta para saber se a música acabou
        tracks=tuple(0x1992 + 0x54 * k for k in range(9)),
        active_bits=tuple([(0x20, 1 << k) for k in range(8)] + [(0x21, 1)]),
        order_ptr=1,                           # (ix+1/2): ponteiro da lista de padrões; o loop volta para (ix+3/4)
        songs=0x30,
        names={},
    ),
}


# ------------------------------------------------------------------------------------- ROM
def _read_rom(path) -> bytes:
    p = str(path)
    if p.lower().endswith(".zip"):
        with zipfile.ZipFile(p) as z:
            cands = [n for n in z.namelist() if n.lower().endswith(EXTS)]
            if not cands:
                raise ValueError("o zip não tem ROM de Mega Drive (.md, .bin, .gen)")
            d = z.read(cands[0])
    else:
        d = Path(p).read_bytes()
    if len(d) % 0x4000 == 512 and d[8:10] == b"\xaa\xbb":      # .smd: cabeçalho de 512 bytes, intercalado
        d = _deinterleave_smd(d[512:])
    return d


def _deinterleave_smd(d: bytes) -> bytes:
    out = bytearray(len(d))
    for b in range(0, len(d), 0x4000):
        blk = d[b:b + 0x4000]
        out[b + 1:b + 0x4000:2] = blk[:0x2000]
        out[b:b + 0x4000:2] = blk[0x2000:]
    return bytes(out)


def header(d: bytes) -> dict:
    def txt(a, n):
        return d[a:a + n].decode("latin-1", "replace").strip()
    return dict(system=txt(0x100, 16), domestic=txt(0x120, 48), overseas=txt(0x150, 48),
                serial=txt(0x180, 14), checksum=struct.unpack_from(">H", d, 0x18E)[0] if len(d) > 0x190 else 0)


def is_md_rom(d: bytes) -> bool:
    return len(d) >= 0x200 and d[0x100:0x104] in (b"SEGA", b" SEG")


def all_profiles() -> dict:
    """Os perfis do programa e os criados pela análise (mdconv.mdanalyze)."""
    from mdconv.mdanalyze import load_user_profiles
    out = dict(PROFILES)
    out.update(load_user_profiles())
    return out


def find_profile(d: bytes) -> str | None:
    """Perfil feito para esta ROM: mesmo código do cartucho e mesmo checksum (ou uma versão já testada e
    aprovada antes por ``verify_profile``)."""
    h = header(d)
    profs = all_profiles()
    for key, p in profs.items():
        if h["serial"].startswith(p["serial"]) and (h["checksum"] == p["checksum"]
                                                   or h["checksum"] in p.get("checksums", ())):
            return key
    c = _load_compat().get(_compat_id(h))
    if c and c.get("ok") and c.get("key") in profs:
        return c["key"]
    return None


def similar_profiles(d: bytes) -> list[str]:
    """Perfis do mesmo jogo (mesmo código do cartucho) feitos com outra versão da ROM: tradução, patch de
    região, revisão… O checksum muda com qualquer byte alterado, mas o driver de som quase nunca."""
    h = header(d)
    sid = h["serial"].split()[1] if len(h["serial"].split()) > 1 else h["serial"]
    if len(sid) < 4:
        return []
    return [k for k, p in all_profiles().items() if sid in p["serial"]]


def _compat_id(h: dict) -> str:
    return f"{h['serial']}|{h['checksum']:04X}"


def _compat_file() -> Path:
    from mdconv.mdanalyze import profiles_dir
    return profiles_dir() / "versoes_testadas.json"


def _load_compat() -> dict:
    try:
        return json.loads(_compat_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_compat(h: dict, key: str, ok: bool) -> None:
    try:
        c = _load_compat()
        c[_compat_id(h)] = dict(key=key, ok=ok, title=h["overseas"] or h["domestic"])
        _compat_file().write_text(json.dumps(c, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def verify_profile(d: bytes, key: str, log=None) -> bool:
    """Testa o perfil ``key`` nesta ROM: toca duas músicas por alguns segundos no emulador. Serve se as
    duas tocam notas de verdade e são diferentes uma da outra (o perfil escolhe a música certa)."""
    from mdconv.mdanalyze import _keynotes, _keyons
    try:
        b = Board(d, key)
        songs = b.musics[:1] + b.musics[len(b.musics) // 2:len(b.musics) // 2 + 1]
        prints = []
        for s in dict.fromkeys(songs):
            c = b.capture(s, 15)
            if len(_keyons(c.events)) < 15:
                return False
            prints.append(tuple(_keynotes(c.events)[:60]))
        return len(prints) == 1 or len(set(prints)) == len(prints)
    except Exception as e:  # noqa: BLE001 — qualquer falha do perfil nesta ROM = não serve
        if log:
            log(f"  o perfil não roda nesta versão: {e}")
        return False


def is_board_source(path: str) -> bool:
    if not path or os.path.isdir(path):
        return False
    low = path.lower()
    if not low.endswith(EXTS + (".zip",)):
        return False
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                if not any(n.lower().endswith(EXTS) for n in z.namelist()):
                    return False
        return is_md_rom(_read_rom(path))
    except (OSError, zipfile.BadZipFile, ValueError):
        return False


# ------------------------------------------------------------------------------------- PCM (Z80)
class Mix2:
    """Mixer de 2 canais do Z80 do SF2' SCE: amostras de 8 bits sem sinal, uma tabela de 8 bytes por
    amostra (prioridade, ponteiro LE, banco, tamanho LE); a cada laço o Z80 lê um byte de cada canal,
    soma os dois e divide por 2 (``add a,(hl)`` + ``rra``) e escreve no DAC. Um comando só entra se a
    prioridade da amostra for ≥ à da que está tocando no canal; no fim, o canal fica parado no último
    byte."""

    def __init__(self, rom: bytes, table: int, regs, z80_tstates: int, **_):
        self.rom = rom
        self.regs = tuple(regs)
        self.rate = 3579545 / z80_tstates
        self.ents = []
        for i in range(128):
            e = rom[table + i * 8: table + i * 8 + 8]
            if len(e) < 8:
                break
            self.ents.append((e[0], (e[3] << 16) | e[1] | (e[2] << 8), e[4] | (e[5] << 8)))
        self.reset()

    def reset(self):
        p = self.ents[0][1] if self.ents else 0
        self.ch = [dict(pri=0, ptr=p, left=0) for _ in range(2)]
        self.pending = [None, None]

    def command(self, ch: int, v: int):
        self.pending[ch] = v

    def poll(self):
        """Aplica os comandos pendentes. Devolve [(canal, nº da amostra)] dos que começaram."""
        started = []
        for k in range(2):
            v, self.pending[k] = self.pending[k], None
            if v is None or v & 0x80 or v >= len(self.ents):
                continue
            pri, ptr, ln = self.ents[v]
            if ln and ptr + ln <= len(self.rom) and pri >= self.ch[k]["pri"]:
                self.ch[k].update(pri=pri, ptr=ptr, left=ln)
                started.append((k, v))
        return started

    def render(self, n: int) -> np.ndarray:
        out = np.empty(n, np.uint8)
        rom = self.rom
        a, b = self.ch
        for i in range(n):
            out[i] = (rom[a["ptr"]] + rom[b["ptr"]]) >> 1
            for c in (a, b):
                if c["left"]:
                    c["left"] -= 1
                    if c["left"]:
                        c["ptr"] += 1
                    else:
                        c["pri"] = 0
        return out

    def sample(self, idx: int) -> bytes:
        _, ptr, ln = self.ents[idx]
        return self.rom[ptr:ptr + ln]


PCM_KINDS = {"mix2": Mix2}


# ------------------------------------------------------------------------------------- captura
@dataclass
class Capture:
    song: int
    init: np.ndarray                   # escritas no YM/PSG da inicialização (tempo 0)
    events: np.ndarray                 # escritas no YM/PSG/RAM do Z80 a partir do pedido da música
    end: int                           # quadro final
    loop: int | None                   # quadro do loop
    t0: int | None = None              # driver no Z80: t44 do pedido da música (tempo exato por evento)
    pcm_hits: list = field(default_factory=list)     # (quadro, canal, nº da amostra)
    dac: np.ndarray | None = None      # DAC misturado como no console (taxa do mixer)
    dac_rate: float = 0.0
    report: list = field(default_factory=list)

    def times(self, ev: np.ndarray) -> np.ndarray:
        """Tempo de cada evento em amostras de 44,1 kHz a partir do pedido da música."""
        if self.t0 is None:
            return np.round(ev["frame"].astype(np.float64) * VGM_RATE / FRAME_HZ).astype(np.int64)
        return np.maximum(0, ev["t44"].astype(np.int64) - self.t0)

    @property
    def fm_notes(self) -> int:
        e = self.events
        return int(((e["type"] == EV_YM0) & (e["addr"] == 0x28) & ((e["val"] & 0xF0) != 0)).sum())

    @property
    def psg_writes(self) -> int:
        return int((self.events["type"] == EV_PSG).sum())


class Board:
    def __init__(self, rom: bytes, key: str, path: str = ""):
        self.rom, self.key, self.path = rom, key, path
        self.p = all_profiles()[key]
        self.title = self.p["title"]
        self.hdr = header(rom)
        t = self.p.get("song_table")
        songs = self.p.get("songs", 0)
        if isinstance(songs, list):
            self.musics = list(songs)
        else:
            self.musics = list(range(rom[t] if t is not None else songs))
        self._m: Machine | None = None
        self.compat_note = ""

    def name(self, cmd: int) -> str:
        return self.p["names"].get(cmd, f"Musica {cmd:02d}" if self.p.get("driver") != "boot" else f"Musica {cmd:03d}")

    def machine(self) -> Machine:
        if self._m is None:
            self._m = Machine(self.rom)
        return self._m

    def capture(self, song: int, max_seconds: float = MAX_SECONDS) -> Capture:
        if self.p.get("driver") == "boot":
            return self._capture_boot(song, max_seconds)
        if self.p.get("driver") == "z80":
            return self._capture_z80(song, max_seconds)
        return self._capture_68k(song, max_seconds)

    @staticmethod
    def _loop_from_gotos(gotos: dict, active: list):
        """(fim, loop, aviso) quando todas as faixas ativas já voltaram duas vezes; senão None."""
        if not active or not all(len(gotos.get(b, ())) >= 2 for b in active):
            return None
        per = {b: gotos[b][1] - gotos[b][0] for b in active}
        ok = [b for b in active if gotos[b][0] + 1 - per[b] >= 0] or active
        period = max(per[b] for b in ok)
        end = max(gotos[b][0] for b in ok if per[b] == period) + 1
        warn = None
        if len(set(per.values())) > 1:
            warn = ("faixas com loops de tamanhos diferentes: vale o maior "
                    f"({period / FRAME_HZ:.1f} s); as outras podem não fechar o loop certinho")
        return end, end - period, warn

    def _calls(self, m: Machine, calls, song: int) -> None:
        for addr, d0 in calls:
            m.call(addr, song if d0 == "song" else d0)

    def _capture_68k(self, song: int, max_seconds: float) -> Capture:
        p = self.p
        m = self.machine()
        m.reset()
        m.enable_z80(False)                    # o PCM deste perfil é reproduzido em Python (Mix2)
        m.set_frame_auto(False)
        m.set_breaks([])
        m.set_zbreaks([])
        self._calls(m, p["init"], song)
        init = m.events()
        init = init[init["type"] <= EV_PSG]
        m.clear_events()
        m.set_breaks([p["loop_pc"]] if p.get("loop_pc") else [])
        m.set_frame(0)
        self._calls(m, p["play"], song)
        ram = m.ram()
        off, mask = p.get("active", (0, 0xFF))
        tracks = p["tracks"]
        maxf = int(max_seconds * FRAME_HZ)
        gotos: dict[int, list[int]] = {}
        loop, end, quiet = None, maxf, 0
        report = []
        for f in range(maxf):
            m.set_frame(f)
            m.call(p["update"])
            new = m.new_events()
            for e in new[new["type"] == EV_BRK]:
                gotos.setdefault(int(e["addr"]), []).append(f)
            active = [b for b in tracks if ram[b + off] & mask]
            if not active:
                quiet += 1
                if quiet > 2:
                    end = f + int(FRAME_HZ)            # 1 s para as caudas
                    break
                continue
            quiet = 0
            r = self._loop_from_gotos(gotos, active)
            if r:
                end, loop, warn = r
                if warn:
                    report.append(warn)
                break
        else:
            report.append(f"sem loop encontrado: gravados {max_seconds:.0f} s")
        ev = m.events()
        ev = ev[(ev["type"] <= EV_Z80) & (ev["frame"] < end)]
        cap = Capture(song, init, ev, end, loop, report=report)
        self._pcm(cap)
        return cap

    def _capture_z80(self, song: int, max_seconds: float) -> Capture:
        p = self.p
        m = self.machine()
        m.reset()
        m.enable_z80(True)
        m.set_frame_auto(True)
        m.set_breaks([])
        m.set_zbreaks([])
        self._calls(m, p["init"], song)
        m.run_frames(p.get("settle", 5))
        init = m.events()
        init = init[init["type"] <= EV_PSG]
        m.clear_events()
        lp = p.get("loop_pc_z80")
        m.set_zbreaks([lp] if lp else [])
        self._calls(m, p["play"], song)
        t0 = int(m.lib.md_mclk() * VGM_RATE // 53693175)     # os quadros abaixo contam a partir daqui
        zram = m.zram()
        off, mask = p.get("active", (0, 0xFF))
        bits = p.get("active_bits")
        tracks = p["tracks"]
        maxf = int(max_seconds * FRAME_HZ)
        gotos: dict[int, list[int]] = {}
        optr = p.get("order_ptr")
        first_seen = [dict() for _ in tracks]       # valor do ponteiro da lista → quadro em que apareceu
        periods: dict[int, tuple[int, int]] = {}    # faixa → (início do loop, período) em quadros
        loop, end, quiet, started = None, maxf, 0, False
        report = []

        def ptr(b):
            return zram[b + optr] | (zram[b + optr + 1] << 8)

        for f in range(maxf):
            m.run_frames(1)
            new = m.new_events()
            hit = {int(e["addr"]) for e in new[new["type"] == EV_ZBRK]}
            for b in hit:
                gotos.setdefault(b, []).append(f)
            if optr is not None:
                for k, b in enumerate(tracks):
                    v = ptr(b)
                    if b in hit and b not in periods and v in first_seen[k]:
                        periods[b] = (first_seen[k][v], f - first_seen[k][v])
                    first_seen[k].setdefault(v, f)
            if bits:
                active = [b for b, (a, bit) in zip(tracks, bits) if zram[a] & bit]
            else:
                active = [b for b in tracks if zram[b + off] & mask]
            if not active:
                if started or f > 2 * FRAME_HZ:         # o Z80 pode levar alguns quadros para começar
                    quiet += 1
                if quiet > 2:
                    end = f + int(FRAME_HZ)
                    m.run_frames(int(FRAME_HZ))
                    break
                continue
            started = True
            if optr is not None:
                if all(b in periods for b in active):
                    pmax = max(periods[b][1] for b in active)
                    start = max(periods[b][0] for b in active if periods[b][1] == pmax)
                    loop, end = start, start + pmax
                    # as faixas andam pelos timers do YM, não pelos quadros: tolera 2% de diferença
                    if any(abs(pmax - round(pmax / periods[b][1]) * periods[b][1]) > max(3, 0.02 * pmax)
                           for b in active):
                        report.append("faixas com loops de tamanhos diferentes: vale o maior "
                                      f"({pmax / FRAME_HZ:.1f} s); as outras podem não fechar o loop certinho")
                    break
                continue
            r = self._loop_from_gotos(gotos, active)
            if r:
                end, loop, warn = r
                if warn:
                    report.append(warn)
                break
        else:
            report.append(f"sem loop encontrado: gravados {max_seconds:.0f} s")
        ev = m.events()
        ev = ev[ev["type"] <= EV_Z80]
        cap = Capture(song, init, ev, end, loop, t0=t0, report=report)
        cap.events = ev[cap.times(ev) < frame_t(end)]
        return cap

    def _capture_boot(self, song: int, max_seconds: float) -> Capture:
        """Perfil da análise: liga o jogo até o pedido da música da abertura, troca o número pela música
        escolhida, deixa o 68000 entregar o pedido e grava o que o Z80 toca sozinho."""
        from mdconv.capcom_opn import earliest_loop_start, find_note_loop
        from mdconv.mdanalyze import boot, play_request, press_start
        p = self.p
        m = self.machine()
        if getattr(self, "_boot_state", None) is None:
            m.reset()
            if p.get("pad"):
                boot(m, int(p["boot_frame"]))
            else:
                m.run_game(int(p["boot_frame"]))
            self._boot_state = m.save_state()
        A, K = p["field"]
        m.load_state(self._boot_state)
        m.set_breaks([])
        m.set_zbreaks([])
        m.set_zoverride(-1, 0)
        m.set_roverride(-1, 0)
        m.set_pad()
        if p.get("cpu") == "68k":
            # driver no 68000: o número vai direto no byte do pedido e só a rotina do driver roda
            m.set_frame_auto(True)
            sp = m.lib.md_get_reg(15) & 0xFFFFFF
            sp = sp - 0x100 if sp > 0xFF0200 else 0xFFFF00
            ups = [int(u) for u in p.get("updates") or [p["update"]]]
            req = p.get("request")
            if req:                                 # o jogo chama o driver direto (jsr, número em D0)
                play_request(m, int(req["entry"]), song, int(req.get("d1", 0)))
            else:
                m.ram()[A & 0xFFFF] = song

            def step():
                for u in ups:
                    m.call(u, 0, max_cycles=1_000_000, sp=sp)
                m.run_frames(1)
        else:
            run68 = bool(p.get("run68"))           # o 68000 continua rodando (drivers que dependem dele)
            m.set_zoverride(A, song, K, lock="block" if run68 else None)
            delivered = False
            f = int(p["boot_frame"])
            for _ in range(int(FRAME_HZ * 20)):
                if p.get("pad"):
                    press_start(m, f)
                m.run_game(1)
                f += 1
                new = m.new_events()
                if ((new["type"] == EV_Z80) & (new["addr"] == A) & (new["val"] == song)).any():
                    delivered = True
                    break
            if not delivered:
                raise RuntimeError("o pedido da música não chegou ao Z80")
            m.run_game(2)

            def step():
                if run68:
                    m.run_game(1)
                else:
                    m.run_frames(1)
        regs = m.ym_regs()
        init = []
        for port in (0, 1):
            order = ([0x22, 0x2B] if port == 0 else []) + [r for r in range(0x30, 0xA0) if r & 3 != 3] + \
                [0xA4, 0xA0, 0xA5, 0xA1, 0xA6, 0xA2, 0xB0, 0xB1, 0xB2, 0xB4, 0xB5, 0xB6]
            for r in order:
                init.append((0, 0, r, port, regs[port * 256 + r]))
        init = np.array(init, dtype=EVENT)
        m.clear_events()
        if p.get("cpu") != "68k" and not p.get("run68"):
            m.free_z80()
        t0 = int(m.lib.md_mclk() * VGM_RATE // 53693175)
        maxf = int(max_seconds * FRAME_HZ)
        loop, end, report = None, maxf, []
        log = []
        last_note, f = None, 0
        for f in range(maxf):
            step()
            new = m.new_events()
            for e in new[new["type"] <= EV_YM1]:
                c, r, v = int(e["type"]), int(e["addr"]), int(e["val"])
                if r == 0x28 and c == 0:
                    c = 1 if v & 4 else 0
                log.append((f, c, r, v))
                if r == 0x28 and v & 0xF0:
                    last_note = f
            if last_note is None:
                if f > 15 * FRAME_HZ:                            # nem começou: não é música
                    end = int(FRAME_HZ)
                    break
            elif f - last_note > 4 * FRAME_HZ:
                end = last_note + int(FRAME_HZ)                  # a música acabou (não tem loop)
                break
            if f >= 20 * FRAME_HZ and f % int(10 * FRAME_HZ) == 0:
                r_ = find_note_loop(log, f + 1, int(round(FRAME_HZ)), min_seconds=4.0)
                if r_:
                    s0, e0 = r_
                    s0 = earliest_loop_start(log, s0, e0 - s0, int(round(FRAME_HZ)))
                    loop, end = s0, s0 + (e0 - r_[0])
                    break
        else:
            report.append(f"sem loop encontrado: gravados {max_seconds:.0f} s")
        ev = m.events()
        ev = ev[ev["type"] <= EV_Z80]
        cap = Capture(song, init, ev, end, loop, t0=t0, report=report)
        cap.events = ev[cap.times(ev) < frame_t(end)]
        return cap

    def _pcm(self, cap: Capture) -> None:
        spec = self.p.get("pcm")
        if not spec:
            return
        mix = PCM_KINDS[spec["kind"]](self.rom, **spec)
        regs = mix.regs
        z = cap.events[(cap.events["type"] == EV_Z80) & np.isin(cap.events["addr"], regs)]
        n = int(cap.end / FRAME_HZ * mix.rate)
        dac = np.empty(n, np.uint8)
        pos = zi = 0
        for f in range(cap.end + 1):
            tgt = min(n, int(f / FRAME_HZ * mix.rate))
            if tgt > pos:
                dac[pos:tgt] = mix.render(tgt - pos)
                pos = tgt
            while zi < len(z) and z[zi]["frame"] <= f:
                mix.command(regs.index(int(z[zi]["addr"])), int(z[zi]["val"]))
                zi += 1
            for ch, idx in mix.poll():
                cap.pcm_hits.append((f, ch, idx))
        if pos < n:
            dac[pos:] = mix.render(n - pos)
        cap.dac, cap.dac_rate = dac, mix.rate
        self._mix = mix


def load_board(path: str) -> Board:
    d = _read_rom(path)
    if not is_md_rom(d):
        raise ValueError("não é uma ROM de Mega Drive (falta \"SEGA\" no cabeçalho)")
    key = find_profile(d)
    h = header(d)
    note = ""
    if key is None:
        tried = []
        known = _load_compat().get(_compat_id(h))
        for k in similar_profiles(d):
            if known and known.get("key") == k and not known.get("ok"):
                tried.append(k)                 # já testado antes nesta versão: não serve
                continue
            ok = verify_profile(d, k)
            _save_compat(h, k, ok)
            if ok:
                key = k
                break
            tried.append(k)
        if key is None:
            other = ""
            if tried:
                other = (f" Existe perfil de {all_profiles()[tried[0]]['title']} feito com outra versão da ROM, "
                         "mas ele não toca nesta (o driver de som mudou de lugar).")
            raise ValueError(f"Jogo de Mega Drive ainda sem perfil: {h['overseas'] or h['domestic']} "
                             f"({h['serial']}, checksum {h['checksum']:04X}). Cada jogo precisa dos endereços "
                             "do driver de som." + other)
    p = all_profiles()[key]
    if h["checksum"] != p["checksum"] and h["checksum"] not in p.get("checksums", ()):
        note = (f"Esta ROM é outra versão de {p['title']} (checksum {h['checksum']:04X}, o perfil foi feito com "
                f"{p['checksum']:04X}); o perfil foi testado nela e funciona.")
    b = Board(d, key, str(path))
    b.compat_note = note
    return b


# ------------------------------------------------------------------------------------- saída
def frame_t(f: float) -> int:
    return int(round(f * VGM_RATE / FRAME_HZ))


HEAD = 24
DAC_GAP = 320            # amostras de 44,1 kHz (~7 ms): o Z80 pode parar o DAC por alguns ms no VBlank


def _dac_runs(direct):
    """Escritas no DAC → disparos: trechos sem pausa maior que DAC_GAP, na taxa média do trecho. A pausa é
    maior que a da aba Música VGM porque aqui quem escreve é o próprio driver, que para de vez em quando
    para atualizar a música (com 2 ms, uma batida virava duas)."""
    out, i = [], 0
    while i < len(direct):
        j = i + 1
        while j < len(direct) and direct[j][0] - direct[j - 1][0] <= DAC_GAP:
            j += 1
        seg = direct[i:j]
        if len(seg) >= 16:
            ts = np.array([t for t, _ in seg], np.float64)
            d = np.diff(ts)
            typical = float(np.median(d)) if len(d) else 1.0
            span = float(d[d <= 4 * typical].sum()) if len(d) else 0.0   # sem contar as pausas
            n = int((d <= 4 * typical).sum())
            rate = n * 44100 / span if span else 8000.0
            out.append((seg[0][0], bytes(v for _, v in seg), rate))
        i = j
    return out


def _split_dac_hits(hits):
    """O Z80 escreve o DAC byte a byte e às vezes emenda uma batida na outra sem pausa: um trecho contínuo
    pode conter várias amostras. Os começos que aparecem em mais de um disparo são amostras conhecidas;
    cada trecho é cortado onde um desses começos aparece de novo, virando um disparo por amostra."""
    from collections import Counter
    data_hits = [h for h in hits if h[1] is not None and len(h[1]) > HEAD]
    heads = {k for k, n in Counter(h[1][:HEAD] for h in data_hits).items()
             if n >= 2 and np.frombuffer(k, np.uint8).std() > 6}        # começo com som (não silêncio/onda fixa)
    if not heads:
        return hits
    out = []
    for h in hits:
        if h[1] is None or len(h[1]) <= HEAD:
            out.append(h)
            continue
        d, rate = h[1], h[2]
        cuts = sorted({i for hd in heads for i in _find_all(d, hd) if i >= 64})
        starts = [0]
        for c in cuts:
            if c - starts[-1] >= 64:                     # pedaços curtos demais: é a mesma batida
                starts.append(c)
        for a, b in zip(starts, starts[1:] + [len(d)]):
            out.append((h[0] + int(round(a * 44100 / rate)), d[a:b], rate) + tuple(h[3:]))
    # cada amostra conhecida fica com um tamanho só (o mais comum): o que vem depois disso num disparo
    # é outra batida emendada, que o XGM não teria como juntar
    lens: dict[bytes, list[int]] = {}
    for x in out:
        if x[1] is not None and x[1][:HEAD] in heads:
            lens.setdefault(x[1][:HEAD], []).append(len(x[1]))
    canon = {}
    for hd, ls in lens.items():
        mode, n = Counter(ls).most_common(1)[0]
        canon[hd] = mode if n >= 2 else int(np.median(ls))
    out = [(x[0], x[1][:canon[x[1][:HEAD]]], x[2]) + tuple(x[3:])
           if x[1] is not None and x[1][:HEAD] in canon else x for x in out]
    out.sort(key=lambda x: x[0])
    return out


def _find_all(d: bytes, sub: bytes):
    i = d.find(sub, 1)
    while i >= 0:
        yield i
        i = d.find(sub, i + 1)


def _snap_rates(hits, tol: float = 0.15):
    """A taxa de cada disparo de DAC é medida pelo tempo entre as escritas do Z80 e oscila alguns %: os
    disparos da mesma amostra com taxas a menos de 15% umas das outras ficam com a mesma (a mediana), para
    virar uma amostra só no XGM em vez de várias quase iguais."""
    by = {}
    for i, h in enumerate(hits):
        if h[1] is not None:
            by.setdefault(h[1][:24], []).append(i)        # mesmo começo = mesma amostra (cortada ou não)
    out = list(hits)
    for idx in by.values():
        idx.sort(key=lambda i: hits[i][2])
        group = [idx[0]]
        for i in idx[1:] + [None]:
            if i is not None and hits[i][2] <= hits[group[0]][2] * (1 + tol):
                group.append(i)
                continue
            med = float(np.median([hits[j][2] for j in group]))
            for j in group:
                out[j] = (hits[j][0], hits[j][1], med) + tuple(hits[j][3:])
            if i is not None:
                group = [i]
    return out


def to_mdvgm(board: Board, cap: Capture):
    """Capture → MdVgm (o mesmo caminho da aba Música VGM), com cada disparo de PCM no seu canal."""
    from mdconv.mdvgm import MdVgm, _direct_hits
    v = MdVgm(fm_clock=FM_CLOCK, psg_clock=PSG_CLOCK)
    evs = np.concatenate([cap.init, cap.events[cap.events["type"] <= EV_PSG]])
    ts = np.concatenate([np.zeros(len(cap.init), np.int64), cap.times(cap.events[cap.events["type"] <= EV_PSG])])
    direct = []
    for e, t in zip(evs, ts):
        t = int(t)
        ty = int(e["type"])
        if ty == EV_PSG:
            v.writes.append((t, ("psg", int(e["val"]))))
        elif ty == EV_YM0 and int(e["addr"]) == 0x2A:
            direct.append((t, int(e["val"])))          # DAC escrito pelo Z80 do jogo
        else:
            v.writes.append((t, ("fm", ty, int(e["addr"]), int(e["val"]))))
    if direct:
        v.hits += _snap_rates(_split_dac_hits(_dac_runs(direct)))
    mix = getattr(board, "_mix", None)
    for f, ch, idx in cap.pcm_hits:
        v.hits.append((frame_t(f), mix.sample(idx), cap.dac_rate, ch + 1))
    v.hits.sort(key=lambda h: h[0])
    v.total = frame_t(cap.end)
    v.loop = frame_t(cap.loop) if cap.loop is not None else None
    v.gd3 = {"title": board.name(cap.song), "game": board.title}
    return v


def write_exact_vgm(board: Board, cap: Capture, path, title: str = "") -> None:
    """VGM com o tempo exato do console (59,92 quadros/s) e o DAC misturado como o Z80 do jogo faz."""
    cmds = []
    sel = cap.events[cap.events["type"] <= EV_PSG]
    evs = np.concatenate([cap.init, sel])
    ts = np.concatenate([np.zeros(len(cap.init), np.int64), cap.times(sel)])
    mixed = cap.dac is not None                 # DAC refeito em Python: as escritas no 0x2A não valem
    for e, t in zip(evs, ts):
        t = int(t)
        ty = int(e["type"])
        if ty == EV_PSG:
            cmds.append((t, 0, bytes([0x50, int(e["val"])])))
        elif not (mixed and ty == EV_YM0 and int(e["addr"]) == 0x2A):
            cmds.append((t, 0, bytes([0x52 + ty, int(e["addr"]), int(e["val"])])))
    dac = cap.dac if cap.dac is not None else np.zeros(0, np.uint8)
    cmds.sort(key=lambda c: c[0])
    step = VGM_RATE / cap.dac_rate if len(dac) else 0
    for i in range(len(dac)):
        cmds.append((int(i * step), 1, i))
    total = frame_t(cap.end)
    loop_t = frame_t(cap.loop) if cap.loop is not None else None
    if loop_t is not None:
        cmds.append((loop_t, -1, None))
    cmds.sort(key=lambda c: (c[0], c[1]))

    body = bytearray()
    if len(dac):
        body += bytes([0x67, 0x66, 0x00]) + struct.pack("<I", len(dac)) + dac.tobytes()
        body += bytes([0xE0]) + struct.pack("<I", 0)
    now, loop_ofs, dac_pos = 0, None, 0

    def wait(n):
        while n > 0:
            k = min(n, 65535)
            body.extend(bytes([0x61]) + struct.pack("<H", k))
            n -= k

    for i, (t, kind, data) in enumerate(cmds):
        if t > now:
            wait(t - now)
            now = t
        if kind == -1:
            if loop_ofs is None:
                loop_ofs = len(body)
                if len(dac):
                    body += bytes([0xE0]) + struct.pack("<I", dac_pos)
        elif kind == 1:
            dac_pos = data + 1
            gap = max(0, (cmds[i + 1][0] if i + 1 < len(cmds) else total) - t)
            if gap <= 15:
                body.append(0x80 | gap)
                now += gap
            else:
                body.append(0x80)
        else:
            body += data
    if total > now:
        wait(total - now)
    body.append(0x66)

    hdr = bytearray(0x100)
    hdr[0:4] = b"Vgm "
    struct.pack_into("<I", hdr, 0x08, 0x171)
    struct.pack_into("<I", hdr, 0x0C, PSG_CLOCK)
    struct.pack_into("<I", hdr, 0x18, total)
    if loop_ofs is not None:
        struct.pack_into("<I", hdr, 0x1C, 0x100 + loop_ofs - 0x1C)
        struct.pack_into("<I", hdr, 0x20, total - loop_t)
    struct.pack_into("<I", hdr, 0x24, 60)
    struct.pack_into("<H", hdr, 0x28, 0x0009)
    hdr[0x2A] = 16
    struct.pack_into("<I", hdr, 0x2C, FM_CLOCK)
    struct.pack_into("<I", hdr, 0x34, 0x100 - 0x34)

    def u(s):
        return (s + "\0").encode("utf-16-le")
    txt = (u(title) + u("") + u(board.title) + u("") + u("Sega Mega Drive") + u("") + u(board.p.get("author", ""))
           + u("") + u("") + u("Mega Drive Audio Converter") + u("Extraído da ROM (68000/Z80 emulados)"))
    gd3 = b"Gd3 " + struct.pack("<II", 0x100, len(txt)) + txt
    data = bytearray(hdr + body)
    struct.pack_into("<I", data, 0x14, len(data) - 0x14)
    data += gd3
    struct.pack_into("<I", data, 0x04, len(data) - 4)
    Path(path).write_bytes(bytes(data))


def export_board(board: Board, out_dir, select=None, log=print, progress=None) -> list:
    from mdconv.capcom_opn import file_name
    from mdconv.mdvgm import convert
    from mdconv.vgmconv import output_formats, write_outputs
    out = Path(out_dir)
    files = []
    cmds = [c for c in board.musics if select is None or c in select]
    seen = {}
    for k, cmd in enumerate(cmds):
        if progress:
            progress("som", k, len(cmds))
        try:
            cap = board.capture(cmd)
            if not cap.fm_notes and not cap.pcm_hits and not any(
                    (v & 0x90) == 0x90 and (v & 15) != 15 for v in cap.events["val"][cap.events["type"] == EV_PSG]):
                log(f"· {cmd:02d}: não toca nada")
                continue
            sig = hash((cap.events.tobytes(), cap.end, cap.loop))
            if sig in seen:
                log(f"· {cmd:02d}: igual à {seen[sig]:02d} (não gravada de novo)")
                continue
            seen[sig] = cmd
            name = board.name(cmd)
            stem = file_name(f"{cmd:02d}_{name}")
            log(f"▶ {cmd:02d} {name}: {cap.end / FRAME_HZ:.1f} s"
                + (f", loop a partir de {cap.loop / FRAME_HZ:.1f} s" if cap.loop is not None else "")
                + f" · {cap.fm_notes} notas FM, {len(cap.pcm_hits)} disparos de PCM")
            stream, report = convert(to_mdvgm(board, cap), pcm_channel=1)
            gd3 = {"titulo": name, "jogo": board.title, "autor": board.p.get("author", ""),
                   "title": name, "game": board.title}
            dest = out / "musicas"
            files += write_outputs(stream, report + cap.report, gd3, dest / (stem + ".vgm"), dest, stem,
                                   True, False, True, log, "YM2612")
            vp = dest / f"{stem}.vgm"
            if "vgm" in output_formats():
                write_exact_vgm(board, cap, vp, name)
                files.append(vp)
                log(f"  VGM: {vp.name} (tempo exato do console"
                    + (", DAC misturado como no jogo)" if cap.dac is not None else ")"))
        except Exception as exc:
            log(f"✖ {cmd:02d}: {exc}")
    if progress:
        progress("som", len(cmds), len(cmds))
    return files
