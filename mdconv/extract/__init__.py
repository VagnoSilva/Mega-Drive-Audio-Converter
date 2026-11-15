"""Extrator de áudio (aba "Extração"): MIDI + SoundFont de jogos Capcom CPS1 e Game Boy Advance.

Mesmo código do *Extrator de Áudio CPS1/GBA* independente (pasta ``cps1_extractor``), embutido
no conversor. A parte de CPS1 é um port do VGMTrans (licença zlib, ver LICENSE-VGMTrans.txt).
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

__version__ = "1.2.0"


def open_rom(path: str, game: str | None = None):
    """Abre a ROM (zip/pasta da CPS1 ou .gba). Devolve o objeto do jogo."""
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
    return f"song{sid:03d}" if platform(game) == "GBA" else f"Música {sid:02X}"


def summary(game) -> str:
    if platform(game) == "GBA":
        return (f"GBA · {game.name} · {len(game.songs())} músicas · "
                f"{len(game.bank_numbers())} bancos de instrumentos")
    d = game.driver
    return (f"CPS1 · {game.name} · driver v{game.entry['version']} · {len(game.songs())} sequências · "
            f"{len(d.instruments)} instr. FM · {len(game.oki_samples)} samples")


def run_export(game, out_dir: str, **opts) -> str:
    """Extrai para ``out_dir/<jogo>/``. Opções iguais às do extrator independente."""
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
