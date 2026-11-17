"""Extrator de áudio (aba "Extração"): MIDI + SoundFont de jogos Capcom CPS1 e Game Boy Advance, e
Moon Patrol, Kung-Fu Master e Lode Runner (scripts da ROM de som), Ghosts 'n Goblins, Nemesis e
Neo Geo (placa de som emulada) direto para o Mega Drive.

Mesmo código do *Extrator de Áudio CPS1/GBA* independente (pasta ``cps1_extractor``), embutido
no conversor. A parte de CPS1 é um port do VGMTrans (licença zlib, ver LICENSE-VGMTrans.txt).
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

__version__ = "1.2.0"


class IremGame:
    """Jogos Irem com a linguagem de som da placa M52/M62 (Moon Patrol, Kung-Fu Master): músicas,
    efeitos e vozes lidos da ROM de som (mdconv.mpsl). Saem direto para o Mega Drive (XGM/VGM,
    código C, WAV de 8 bits), sem MIDI nem SoundFont."""
    format = "Direto"
    VOICE = 0x100                      # ids das vozes digitalizadas na lista: 0x100 + número

    def __init__(self, path: str):
        from mdconv import mpsl
        self.path = path
        self.board = mpsl.load_board(path)
        self.name = self.board.title
        self.warnings: list[str] = []

    def songs(self):
        p = self.board.p
        return ([(c, None, 1) for c in list(p["musics"]) + list(p["effects"])]
                + [(self.VOICE + i, None, 1) for i, _, _ in self.board.voices()])

    def label(self, sid: int) -> str:
        p = self.board.p
        if sid >= self.VOICE:
            return "Voz/amostra: " + p["voices"][sid - self.VOICE]
        return ("Música: " if sid in p["musics"] else "Efeito: ") + self.board.name(sid)

    def effects(self):
        """[(comando, nome, (tons do PSG, canais FM) padrão)] dos efeitos de chip."""
        return [(c, self.board.name(c), self.board.channels(c)) for c in self.board.p["effects"]]

    def export(self, out_dir: str, song_ids=None, log=print, progress=None, sfx_channels=None,
               **_opts) -> str:
        from mdconv import mpsl
        base = os.path.join(out_dir, self.name.replace(" ", "_").replace("-", "_"))
        sel = None if song_ids is None else {("v", s - self.VOICE) if s >= self.VOICE else s for s in song_ids}
        mpsl.export_board(self.board, base, select=sel, log=log, progress=progress, channels=sfx_channels)
        log(f"✔ Pronto em {base}: musicas (XGM, VGM, prévia), efeitos (VGM, .c para o SGDK, prévia) "
            "e vozes (WAV de 8 bits a 14 kHz)")
        return base


MoonPatrolGame = IremGame


class CapcomOpnGame:
    """Capcom pré-CPS com Z80 + 2× YM2203 (Ghosts 'n Goblins): a placa de som é emulada rodando o
    próprio programa do jogo (mdconv.capcom_opn) e cada comando de som vira VGM/XGM ou efeito."""
    format = "Direto"

    def __init__(self, path: str):
        from mdconv import capcom_opn
        self.path = path
        self.board = capcom_opn.load_board(path)
        self.name = self.board.title
        self.warnings = ["Cada som é gravado rodando o programa da placa: a extração completa leva alguns minutos."]

    def songs(self):
        return [(c, None, 1) for c in self.board.p["commands"]]

    def label(self, sid: int) -> str:
        return f"Som {sid:02X}: {self.board.name(sid)}" if sid in self.board.p["names"] else f"Som {sid:02X}"

    def summary(self) -> str:
        p = self.board.p
        return (f"{self.name} ({p['board']}) · {len(p['commands'])} comandos de som · a placa de som é emulada "
                "e cada comando sai como música (XGM/VGM) ou efeito (VGM + código C)")

    def export(self, out_dir: str, song_ids=None, log=print, progress=None, **_opts) -> str:
        from mdconv import capcom_opn
        base = os.path.join(out_dir, self.name.replace(" ", "_").replace("'", ""))
        capcom_opn.export_board(self.board, base, select=song_ids, log=log, progress=progress)
        log(f"✔ Pronto em {base}: musicas (XGM, VGM, prévia) e efeitos (VGM, .c para o SGDK, prévia)")
        return base


class KonamiGame:
    """Konami Bubble System / GX400 (Nemesis): Z80 + 2× AY-3-8910 + K005289. A placa de som é
    emulada (mdconv.konami); músicas saem em XGM/VGM (K005289 no FM) e efeitos como .c para o SGDK."""
    format = "Direto"

    def __init__(self, path: str):
        from mdconv import konami
        self.path = path
        self.board = konami.load_board(path)
        self.name = self.board.title
        self.warnings = ["Cada som é gravado rodando o programa da placa: a extração completa leva alguns "
                         "minutos (as músicas longas, até meio minuto cada)."]

    def songs(self):
        return [(c, None, 1) for c in self.board.commands]

    def label(self, sid: int) -> str:
        return self.board.name(sid)

    def summary(self) -> str:
        p = self.board.p
        return (f"{self.name} ({p['board']}) · {len(p['musics'])} músicas e {len(p['effects'])} efeitos · "
                "a placa de som é emulada")

    def effects(self):
        from mdconv import konami
        return [(c, self.board.name(c), konami.channels_for(c)) for c in self.board.p["effects"]]

    def export(self, out_dir: str, song_ids=None, log=print, progress=None, sfx_channels=None, **_opts) -> str:
        from mdconv import konami
        base = os.path.join(out_dir, self.name.replace(" ", "_"))
        konami.export_board(self.board, base, select=song_ids, log=log, progress=progress, channels=sfx_channels)
        log(f"✔ Pronto em {base}: musicas (XGM, VGM, prévia) e efeitos (VGM, .c para o SGDK, prévia)")
        return base


class NeoGeoGame:
    """Neo Geo (Z80 + YM2610): o programa de som da ROM M1 é emulado (mdconv.neogeo) e cada música
    sai em XGM/VGM — FM 1 para 1, SSG no PSG, ADPCM-A no PCM do XGM, ADPCM-B no FM 5."""
    format = "Direto"

    def __init__(self, path: str):
        from mdconv import neogeo
        self.path = path
        self.board = neogeo.load_board(path)
        self.name = self.board.p.get("short", self.board.title)
        self.warnings = ["Cada música é gravada rodando o programa de som do jogo até o loop: as longas levam "
                         "1 a 2 minutos cada, e o jogo inteiro, perto de meia hora."]

    def songs(self):
        return [(c, None, 1) for c in self.board.musics]

    def label(self, sid: int) -> str:
        return self.board.name(sid)

    def summary(self) -> str:
        return (f"{self.board.title} ({self.board.p['board']}) · {len(self.board.musics)} músicas e jingles · "
                "a placa de som é emulada; amostras ADPCM viram PCM do XGM (veja \"Opções do Neo Geo…\")")

    def neo_options(self):
        from dataclasses import asdict

        from mdconv import neogeo
        return asdict(neogeo.Options())

    def export(self, out_dir: str, song_ids=None, log=print, progress=None, neo_options=None, **_opts) -> str:
        from mdconv import neogeo
        base = os.path.join(out_dir, self.name.replace(" ", "_").replace("'", ""))
        opt = neogeo.Options(**{k: (tuple(v) if k == "pcm_channels" else v) for k, v in (neo_options or {}).items()})
        neogeo.export_board(self.board, base, select=song_ids, log=log, progress=progress, options=opt)
        log(f"✔ Pronto em {base}: musicas (XGM, VGM e prévia de cada uma)")
        return base


def is_neogeo(path: str) -> bool:
    if not path or os.path.isdir(path) or not path.lower().endswith(".zip"):
        return False
    from mdconv import neogeo
    return neogeo.is_board_source(path)


def is_konami(path: str) -> bool:
    if not path or os.path.isdir(path) or not path.lower().endswith(".zip"):
        return False
    from mdconv import konami
    return konami.is_board_source(path)


def is_capcom_opn(path: str) -> bool:
    if not path or os.path.isdir(path) or not path.lower().endswith(".zip"):
        return False
    from mdconv import capcom_opn
    return capcom_opn.is_board_source(path)


def is_direct(path: str) -> bool:
    return is_irem(path) or is_capcom_opn(path) or is_konami(path) or is_neogeo(path)


def is_irem(path: str) -> bool:
    if not path or os.path.isdir(path) or path.lower().endswith(".gba"):
        return False
    from mdconv import mpsl
    return mpsl.is_board_source(path)


is_moon_patrol = is_irem


def open_rom(path: str, game: str | None = None):
    """Abre a ROM (zip/pasta da CPS1, .gba ou Moon Patrol). Devolve o objeto do jogo."""
    if is_irem(path):
        return IremGame(path)
    if is_capcom_opn(path):
        return CapcomOpnGame(path)
    if is_konami(path):
        return KonamiGame(path)
    if is_neogeo(path):
        return NeoGeoGame(path)
    from . import export
    return export.open_game(path, game or None)


def is_gba(path: str) -> bool:
    from . import gba
    return gba.is_gba(path)


def cps1_games() -> list[str]:
    from . import export
    return export.list_games()


def guess_cps1_game(path: str) -> str | None:
    from . import export, romload
    return romload.guess_game(path, export.all_games())


def platform(game) -> str:
    return getattr(game, "format", "CPS1")


def song_label(game, sid: int) -> str:
    if platform(game) == "Direto":
        return game.label(sid)
    return f"song{sid:03d}" if platform(game) == "GBA" else f"Música {sid:02X}"


def summary(game) -> str:
    if isinstance(game, (CapcomOpnGame, KonamiGame, NeoGeoGame)):
        return game.summary()
    if platform(game) == "Direto":
        p = game.board.p
        return (f"{game.name} ({p['board']}, AY-3-8910) · {len(p['musics'])} músicas, {len(p['effects'])} efeitos "
                f"e {len(game.board.voices())} vozes lidos da ROM de som · sai direto em XGM/VGM, código C e WAV")
    if platform(game) == "GBA":
        return (f"GBA · {game.name} · {len(game.songs())} músicas · "
                f"{len(game.bank_numbers())} bancos de instrumentos")
    d = game.driver
    return (f"CPS1 · {game.name} · driver v{game.entry['version']} · {len(game.songs())} sequências · "
            f"{len(d.instruments)} instr. FM · {len(game.oki_samples)} samples")


def run_export(game, out_dir: str, **opts) -> str:
    """Extrai para ``out_dir/<jogo>/``. Opções iguais às do extrator independente."""
    if platform(game) == "Direto":
        return game.export(out_dir, **opts)
    from . import export
    return export.export_all(game, out_dir, **opts)


@dataclass
class Outputs:
    base: str
    midis: list[str] = field(default_factory=list)
    sf2: str | None = None
    library: str | None = None        # biblioteca de patches FM (só CPS1)


def outputs(base: str) -> Outputs:
    """O que uma extração deixou na pasta ``base`` (para levar à aba Conversão)."""
    o = Outputs(base)
    o.midis = sorted(glob.glob(os.path.join(base, "midi", "*.mid")))
    sf2s = sorted(glob.glob(os.path.join(base, "*.sf2")))
    o.sf2 = sf2s[0] if sf2s else None
    lib = os.path.join(base, "biblioteca_megadrive")
    o.library = lib if os.path.isdir(lib) else None
    return o
