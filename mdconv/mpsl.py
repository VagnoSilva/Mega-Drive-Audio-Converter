"""Scripts de som da Irem (Moon Patrol / M52, Kung-Fu Master / M62) → escritas no AY-3-8910.

A placa de som (6803 + 2 AY-3-8910) toca os efeitos e as músicas por uma linguagem de script
própria, descrita e desmontada em computerarcheology.com (Moon Patrol, Sound Code). Aqui ela é
interpretada para gerar exatamente as escritas que o jogo faz no chip, e o resultado passa pelo
conversor de efeitos (``mdconv.chipsfx``).

* Tique de 4 ms (NMI de 4 kHz do MSM5205 dividido por 16).
* ROM de som: ``mp-s1.1a`` (4 KB em F000–FFFF); tabela de comandos em F444 (comandos 10–1F).
* Kung-Fu Master (M62) usa a mesma linguagem, com uma diferença conferida no código do 6803: o
  comando ``0m1c_0nnn FF CC`` (nnn = 0–5) grava o par FF/CC nos registradores 2·nnn e 2·nnn+1
  (o THREEVOICE do Moon Patrol não existe lá).
* Vozes digitalizadas: ADPCM do MSM5205 a 4 kHz, nibble alto primeiro; tabela de 4 bytes por
  comando (endereço, tamanho).
* Comandos simples (bit 6 = continua no mesmo tique; senão o próximo byte é a espera):
  ``0m0r_rrrr VV`` REGISTER (r: bit 4 = chip, bits 0–3 = registrador);
  ``0m1c_0000 FF CC`` THREEVOICE (A = CCFF, B = CCFF/2, C = CCFF/2 + 1; reinicia o envelope);
  ``0m1c_011o VV`` MIXER (o = 0: AND, o = 1: OR);
  ``0m1c_10vv NN`` volume da voz vv = NN e reinício do decaimento;
  ``0m1c_11vv NN`` decaimento automático da voz: −1 a cada NN tiques.
* Complexos: ``100r_rrrr CNT RVAL s0..`` (amostras num registrador, uma a cada RVAL tiques);
  ``101r_rrrr CNT A B RVAL`` (alterna A/B); ``110r_0rrr CNT INI DELTA RVAL`` (varredura: o valor
  inicial e mais CNT passos de DELTA, um a cada RVAL tiques).
* Fluxo: ``FF`` STOP, ``FE MM LL`` CALL (1 nível), ``F0–FD`` RETURN.
"""
from __future__ import annotations

from mdconv.ay.convert import AyVgm

TICK_S = 0.004
AY_CLOCK = 894886
ROM_BASE = 0xF000
COMMAND_TABLE = 0xF444
COMMANDS = {
    0x10: "Passar ponto", 0x11: "Explosão do UFO", 0x12: "Tiro do carro", 0x13: "Moeda",
    0x14: "Pulo do carro", 0x16: "Planta espacial", 0x17: "UFO voando", 0x18: "Música de fundo",
    0x1B: "Música do fim", 0x1C: "Música de abertura", 0x1D: "Chegando ao ponto",
    0x1E: "Parabéns", 0x1F: "Explosão do carro",
}
MAX_TICKS = 250 * 300         # limite de segurança (os contínuos fecham o loop bem antes)
RESET_COMMANDS = range(0x1B, 0x20)   # reiniciam a placa e calam a música: podem usar todos os canais


class MpslError(Exception):
    pass


def run(mem: bytes, addr: int, base: int = ROM_BASE, max_ticks: int = MAX_TICKS, dialect: str = "m52"):
    """Interpreta o script em ``addr``. Devolve ([(tique, chip, reg, valor)], tique final, tique do
    loop ou None). Efeitos contínuos (planta, UFO, música de fundo) repetem para sempre: o loop é
    achado quando um salto volta a um ponto com o estado inteiro igual (registradores, volumes,
    decaimentos)."""
    def rd(a):
        i = a - base
        if not 0 <= i < len(mem):
            raise MpslError(f"script lê fora da ROM em {a:04X}")
        return mem[i]

    regs = [[0] * 16 for _ in range(2)]
    regs[0][7] = regs[1][7] = 0xBF
    regs[0][13] = regs[1][13] = 0x09        # forma de envelope deixada pela inicialização
    vol = [[0] * 3 for _ in range(2)]
    decay = [[0] * 3 for _ in range(2)]        # período (0 = sem decaimento)
    count = [[0] * 3 for _ in range(2)]
    active = [[False] * 3 for _ in range(2)]   # a voz recebeu volume pelo comando de volume
    out = []
    t = 0
    pc = addr
    ret = None
    seen = {}
    loop_tick = None

    def jumped():
        """Depois de um salto: devolve True se este estado já ocorreu (loop fechado)."""
        nonlocal loop_tick
        key = (pc, ret, tuple(map(tuple, regs)), tuple(map(tuple, decay)), tuple(map(tuple, count)))
        if key in seen:
            loop_tick = seen[key]
            return True
        seen[key] = t
        return False

    def w(c, r, v):
        v &= 0xFF
        regs[c][r] = v
        out.append((t, c, r, v))

    def advance(n):
        nonlocal t
        for _ in range(n):
            # o decaimento roda no fim do tique, depois dos comandos do script
            for c in range(2):
                for v in range(3):
                    if decay[c][v] and active[c][v]:
                        count[c][v] -= 1
                        if count[c][v] <= 0:
                            count[c][v] = decay[c][v]
                            cur = regs[c][8 + v]               # o jogo decai o valor do registrador:
                            if 0 < cur < 0x10:                 # zerado (ou em modo envelope) fica
                                w(c, 8 + v, cur - 1)
            t += 1

    while t < max_ticks:
        op = rd(pc)
        if op == 0xFF:
            break
        if op == 0xFE:
            ret = pc + 3
            pc = (rd(pc + 1) << 8) | rd(pc + 2)
            if jumped():
                break
            continue
        if 0xF0 <= op <= 0xFD:
            if ret is None:
                break
            pc, ret = ret, None
            if jumped():
                break
            continue
        if op < 0x80:
            cont = op & 0x40
            if not op & 0x20:                                  # REGISTER
                c, r = (op >> 4) & 1, op & 0x0F
                w(c, r, rd(pc + 1))
                pc += 2
            else:
                c = (op >> 4) & 1
                sub = op & 0x0F
                if dialect == "m62" and sub < 6:               # par de tom num registrador (M62)
                    w(c, 2 * sub, rd(pc + 1))
                    w(c, 2 * sub + 1, rd(pc + 2))
                    pc += 3
                elif sub == 0:                                 # THREEVOICE
                    val = rd(pc + 1) | (rd(pc + 2) << 8)
                    for v, p in enumerate((val, val // 2, val // 2 + 1)):
                        w(c, 2 * v, p & 0xFF)
                        w(c, 2 * v + 1, (p >> 8) & 0x0F)
                    w(c, 13, 0x09)                 # forma 9 (\___): reinicia o envelope do AY
                    pc += 3
                elif sub in (6, 7):                            # MIXER AND / OR
                    m = rd(pc + 1)
                    w(c, 7, (regs[c][7] | m) if sub == 7 else (regs[c][7] & m))
                    pc += 2
                elif sub & 0x0C == 0x08:                       # volume + reinicia decaimento
                    v = sub & 3
                    vol[c][v] = rd(pc + 1) & 0x1F
                    count[c][v] = decay[c][v]
                    active[c][v] = True
                    w(c, 8 + v, vol[c][v])
                    pc += 2
                elif sub & 0x0C == 0x0C:                       # velocidade do decaimento
                    v = sub & 3
                    decay[c][v] = count[c][v] = rd(pc + 1)
                    pc += 2
                else:
                    raise MpslError(f"comando simples desconhecido {op:02X} em {pc:04X}")
            if not cont:
                advance(rd(pc))
                pc += 1
            continue
        kind = (op >> 5) & 3
        c, r = (op >> 4) & 1, op & 0x0F
        if kind == 0:                                          # amostras num registrador
            cnt, rv = rd(pc + 1), rd(pc + 2)
            for i in range(cnt):
                w(c, r, rd(pc + 3 + i))
                advance(rv)
            pc += 3 + cnt
        elif kind == 1:                                        # alterna dois valores
            cnt, a, b, rv = rd(pc + 1), rd(pc + 2), rd(pc + 3), rd(pc + 4)
            for i in range(cnt):
                w(c, r, a if i % 2 == 0 else b)
                advance(rv)
            pc += 5
        elif kind == 2 and not op & 0x08:                      # varredura de um registrador
            cnt, ini, delta, rv = rd(pc + 1), rd(pc + 2), rd(pc + 3), rd(pc + 4)
            d = delta - 256 if delta & 0x80 else delta
            r = op & 0x07
            for i in range(cnt + 1):
                w(c, r, ini + i * d)
                advance(rv)
            pc += 5
        else:
            raise MpslError(f"comando {op:02X} em {pc:04X} não suportado (varredura de par: bug no jogo)")
    # final: o jogo para de escrever; os canais seguem como estão (os scripts calam a voz antes)
    return out, t, loop_tick


def to_ay(events, end_tick: int, name: str = "", loop_tick: int | None = None) -> AyVgm:
    chips = {c for _, c, _, _ in events}
    # estado deixado pela inicialização da placa: mixer 10_111_111 e forma de envelope 9 (\___),
    # da qual dependem os scripts que usam o envelope do AY sem escrever o registrador 13
    init = [(0, c, r, v) for c in sorted(chips) for r, v in ((7, 0xBF), (13, 0x09))]
    writes = [(int(round(t * TICK_S * 44100)), c, r, v) for t, c, r, v in init + list(events)]
    if chips == {1}:                                           # um chip só: vira o chip 0
        writes = [(s, 0, r, v) for s, _, r, v in writes]
    if loop_tick is not None:
        end = int(round(end_tick * TICK_S * 44100))
        loop = int(round(loop_tick * TICK_S * 44100))
    else:
        end, loop = int(round((end_tick * TICK_S + 0.02) * 44100)), None
    return AyVgm(AY_CLOCK, len(chips) > 1, writes, end, loop, {"title": name, "game": "Moon Patrol"})


def command_address(rom: bytes, cmd: int) -> int:
    i = COMMAND_TABLE - ROM_BASE + 2 * (cmd - 0x10)
    return (rom[i] << 8) | rom[i + 1]


def effect(rom: bytes, cmd: int):
    """AyVgm do comando ``cmd`` (0x10–0x1F) da ROM de som."""
    ev, end, loop = run(rom, command_address(rom, cmd))
    return to_ay(ev, end, COMMANDS.get(cmd, f"Comando {cmd:02X}"), loop)


def channels_for(cmd: int):
    """(canais livres do PSG, canais FM) para o comando: os que reiniciam a placa (músicas curtas,
    explosão do carro) calam a música no jogo original e podem usar tudo."""
    if cmd in RESET_COMMANDS:
        return (0, 1, 2), (0, 1, 2, 3, 4)
    return (0, 1), (3, 4)


def load_sound_rom(path) -> bytes:
    """``mp-s1.1a`` solto ou dentro do mpatrol.zip (ou de um clone)."""
    import zipfile
    from pathlib import Path
    p = Path(path)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if Path(n).name.lower() in ("mp-s1.1a", "mps1.1a", "mp-s1.1a.bin"):
                    return z.read(n)
            for n in z.namelist():           # pela tabela de comandos
                d = z.read(n)
                if len(d) == 0x1000 and _looks_like_sound_rom(d):
                    return d
        raise MpslError("não achei a ROM de som (mp-s1.1a) no zip")
    d = p.read_bytes()
    if len(d) != 0x1000:
        raise MpslError("a ROM de som do Moon Patrol tem 4 KB")
    return d


def is_sound_rom_source(path) -> bool:
    """Zip (ou arquivo) com a ROM de som do Moon Patrol?"""
    try:
        load_sound_rom(path)
        return True
    except Exception:
        return False


def _looks_like_sound_rom(d: bytes) -> bool:
    try:
        return all(0xF000 <= command_address(d, c) < 0x10000 for c in range(0x10, 0x20))
    except IndexError:
        return False


MUSICS = {0x1C: "01_Abertura", 0x18: "02_Musica_de_fundo", 0x1D: "03_Chegando_ao_ponto",
          0x1E: "04_Parabens", 0x1B: "05_Fim"}
EFFECTS = (0x10, 0x11, 0x12, 0x13, 0x14, 0x16, 0x17, 0x1F)


def export_all(rom_path, out_dir, musics: bool = True, effects: bool = True, grid: bool = False,
               log=print, select=None, progress=None) -> list:
    """Moon Patrol direto da ROM: músicas em ``musicas/`` (XGM, VGM e prévia, como a conversão de
    VGM) e efeitos em ``efeitos/`` (VGM, .c para o SGDK e prévia). Devolve os arquivos gravados."""
    from pathlib import Path

    from mdconv import chipsfx
    from mdconv.ay.convert import convert
    from mdconv.vgmconv import write_outputs
    rom = load_sound_rom(rom_path)
    out = Path(out_dir)
    files = []
    todo = [c for c in list(MUSICS) * musics + list(EFFECTS) * effects if select is None or c in select]
    done = 0

    def step():
        nonlocal done
        done += 1
        if progress:
            progress("som", done, len(todo))

    if musics:
        for cmd, name in MUSICS.items():
            if cmd not in todo:
                continue
            log(f"▶ {name.replace('_', ' ')}")
            src = effect(rom, cmd)
            src.gd3.update({"title": name[3:].replace("_", " "), "game": "Moon Patrol",
                            "system": "Arcade (Irem M52)", "author": "Irem"})
            conv = convert(src, grid=grid, trim=True)
            files += write_outputs(conv.stream, conv.report, src.gd3, out / "musicas" / (name + ".vgm"),
                                   out / "musicas", name, True, True, True, log, "AY-3-8910")
            step()
    if effects:
        for cmd in EFFECTS:
            if cmd not in todo:
                continue
            step()
            name = f"{cmd:02X}_{COMMANDS[cmd]}"
            psg, fm = channels_for(cmd)
            try:
                sfx = chipsfx.from_ay(effect(rom, cmd), name, psg_free=psg, fm_free=fm)
                files += chipsfx.write_outputs(sfx, out / "efeitos", game="Moon Patrol")
                log(f"✔ efeito {name}: {len(sfx.frames)} quadros" + (" (contínuo)" if sfx.loop is not None else ""))
            except Exception as exc:
                log(f"✖ efeito {name}: {exc}")
    return files


# ---------------------------------------------------------------- placas / jogos
ADPCM_STEPS = (16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
               143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
               876, 963, 1060, 1166, 1282, 1411, 1552)
ADPCM_RATE = 4000


def adpcm_decode(data: bytes):
    """ADPCM do MSM5205 (nibble alto primeiro) → float −1..1."""
    import numpy as np
    out = np.zeros(2 * len(data))
    sig, si = 0, 0
    adj = (-1, -1, -1, -1, 2, 4, 6, 8)
    for i, b in enumerate(data):
        for k, n in enumerate((b >> 4, b & 15)):
            st = ADPCM_STEPS[si]
            d = st >> 3
            if n & 1:
                d += st >> 2
            if n & 2:
                d += st >> 1
            if n & 4:
                d += st
            sig = max(-2048, min(2047, sig - d if n & 8 else sig + d))
            si = max(0, min(48, si + adj[n & 7]))
            out[2 * i + k] = sig / 2048
    return out


PROFILES = {
    "mpatrol": dict(
        title="Moon Patrol", board="Irem M52", dialect="m52", base=0xF000, table=0xF424, samples=0xF400,
        files=(("mp-s1.1a", "mps1.1a"),),
        musics=MUSICS, effects=EFFECTS, names=COMMANDS,
        voices={1: "Explosão (pedras)", 2: "Explosão (mísseis 1)", 3: "Explosão (mísseis 2)"},
        reset=tuple(RESET_COMMANDS),
        # canais padrão de cada efeito (PSG tons 0–2, FM 0–5). A música de fundo convertida usa o
        # FM 1, o ruído do PSG e o tom 3 (relógio do ruído); o FM 6 fica com o PCM do XGM. Efeitos
        # que tocam juntos no jogo ficam em canais diferentes: o UFO voando (contínuo) no PSG 1, o
        # tiro no PSG 2, o pulo nos FM 4–5, a planta no FM 3, a explosão do UFO no FM 2.
        sfx_channels={0x10: ((), (1,)), 0x11: ((), (1,)), 0x12: ((1,), (1,)), 0x13: ((1,), (1,)),
                      0x14: ((), (3, 4)), 0x16: ((), (2,)), 0x17: ((0,), (2,))}),
    "kungfum": dict(
        title="Kung-Fu Master", board="Irem M62", dialect="m62", base=0xA000, table=0xE934, samples=0xE900,
        files=(("a-3e-.bin",), ("a-3f-.bin",), ("a-3h-.bin",)),
        musics={0x20: "01_Inicio", 0x24: "02_Musica_principal", 0x22: "03_Fase_completa",
                0x23: "04_Jogo_completo", 0x21: "05_Game_Over"},
        effects=tuple(range(0x10, 0x1B)),
        names={0x20: "Início", 0x21: "Game Over", 0x22: "Fase completa", 0x23: "Jogo completo",
               0x24: "Música principal", **{c: f"Efeito {c:02X}" for c in range(0x10, 0x1B)}},
        voices={i: f"Voz {i}" for i in range(1, 8)},
        reset=()),
    "ldrun": dict(
        title="Lode Runner", board="Irem M62", dialect="m52", base=0xC000, table=0xEA30, samples=0xEA00,
        files=(("lr-a-3f",), ("lr-a-3h",)),
        musics={c: f"{c - 0x1F:02d}_Musica_{c:02X}" for c in range(0x20, 0x28)},
        effects=tuple(c for c in range(0x10, 0x20) if c != 0x16),
        names={**{c: f"Música {c:02X}" for c in range(0x20, 0x28)},
               **{c: f"Efeito {c:02X}" for c in range(0x10, 0x20)}},
        voices={i: f"Voz {i}" for i in range(1, 7)},
        reset=()),
}


class Board:
    """ROM de som de um jogo Irem com essa linguagem: memória, tabelas e nomes."""

    def __init__(self, key: str, mem: bytes):
        self.key = key
        self.p = PROFILES[key]
        self.mem = mem
        self.title = self.p["title"]

    def rd16(self, a: int) -> int:
        i = a - self.p["base"]
        return (self.mem[i] << 8) | self.mem[i + 1]

    def address(self, cmd: int) -> int:
        return self.rd16(self.p["table"] + 2 * cmd)

    def name(self, cmd: int) -> str:
        return self.p["names"].get(cmd, f"Comando {cmd:02X}")

    def ay(self, cmd: int) -> AyVgm:
        ev, end, loop = run(self.mem, self.address(cmd), base=self.p["base"], dialect=self.p["dialect"])
        v = to_ay(ev, end, self.name(cmd), loop)
        v.gd3["game"] = self.title
        return v

    def channels(self, cmd: int):
        """Canais padrão do efeito: (tons do PSG 0–2, FM 0–5)."""
        if cmd in self.p.get("sfx_channels", {}):
            return self.p["sfx_channels"][cmd]
        return ((0, 1, 2), (0, 1, 2, 3, 4)) if cmd in self.p["reset"] else ((0, 1), (3, 4))

    def voices(self):
        """[(número, nome, amostras float a 4 kHz)] das vozes/explosões digitalizadas."""
        out = []
        for i, nm in self.p["voices"].items():
            a, n = self.rd16(self.p["samples"] + 4 * i), self.rd16(self.p["samples"] + 4 * i + 2)
            lo = a - self.p["base"]
            if n > 1 and 0 <= lo and lo + n <= len(self.mem):
                out.append((i, nm, adpcm_decode(self.mem[lo:lo + n])))
        return out


def load_board(path) -> Board:
    """Reconhece o jogo pelo zip (ou pela ROM solta do Moon Patrol)."""
    import zipfile
    from pathlib import Path
    p = Path(path)
    if p.suffix.lower() == ".zip":
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
                    return Board(key, b"".join(parts))
    return Board("mpatrol", load_sound_rom(path))


def is_board_source(path) -> bool:
    try:
        load_board(path)
        return True
    except Exception:
        return False


def export_board(board: Board, out_dir, select=None, grid: bool = False, log=print, progress=None,
                 channels=None) -> list:
    """Músicas (musicas/: XGM, VGM, prévia), efeitos (efeitos/: VGM, .c, prévia) e vozes
    digitalizadas (vozes/: WAV de 8 bits a 14 kHz para o XGM, como a ferramenta de efeitos).
    ``channels``: {comando: (tons do PSG, canais FM)} escolhidos para os efeitos; os que faltarem
    usam o padrão do jogo (:meth:`Board.channels`)."""
    from pathlib import Path

    import numpy as np

    from mdconv import chipsfx, sfx
    from mdconv.ay.convert import convert
    from mdconv.vgmconv import write_outputs
    p = board.p
    out = Path(out_dir)
    files = []
    vox = board.voices()
    todo = ([c for c in p["musics"] if select is None or c in select]
            + [c for c in p["effects"] if select is None or c in select]
            + [("v", i) for i, _, _ in vox if select is None or ("v", i) in select])
    for k, item in enumerate(todo):
        if progress:
            progress("som", k, len(todo))
        try:
            if isinstance(item, tuple):
                i = item[1]
                nm, x = next((n, x) for j, n, x in vox if j == i)
                pcm, gain, cut, _hf = sfx.process(np.asarray(x), ADPCM_RATE)
                d = out / "vozes"
                d.mkdir(parents=True, exist_ok=True)
                f = d / f"{chipsfx._c_name(f'voz_{i:02d}_{nm}')}.wav"
                sfx.write_wav8(f, pcm, sfx.XGM_RATE)
                files.append(f)
                log(f"✔ voz {nm}: {len(pcm) / sfx.XGM_RATE:.2f} s → {f.name}")
            elif item in p["musics"]:
                name = p["musics"][item]
                log(f"▶ {board.name(item)}")
                src = board.ay(item)
                src.gd3.update({"title": board.name(item), "system": f"Arcade ({p['board']})", "author": "Irem",
                                "titulo": board.name(item), "jogo": board.title, "autor": "Irem"})
                conv = convert(src, grid=grid, trim=True)
                files += write_outputs(conv.stream, conv.report, src.gd3, out / "musicas" / (name + ".vgm"),
                                       out / "musicas", name, True, True, True, log, "AY-3-8910")
            else:
                name = f"{item:02X}_{board.name(item)}"
                psg, fm = (channels or {}).get(item) or board.channels(item)
                sx = chipsfx.from_ay(board.ay(item), name, psg_free=psg, fm_free=fm)
                log(f"  canais: PSG {', '.join(str(c + 1) for c in psg) or '—'} · "
                    f"FM {', '.join(str(c + 1) for c in fm) or '—'}")
                for line in sx.report:
                    log("   " + line)
                files += chipsfx.write_outputs(sx, out / "efeitos", game=board.title)
                log(f"✔ efeito {name}: {len(sx.frames)} quadros" + (" (contínuo)" if sx.loop is not None else ""))
        except Exception as exc:
            log(f"✖ {item if isinstance(item, tuple) else f'{item:02X}'}: {exc}")
    if progress:
        progress("som", len(todo), len(todo))
    return files
