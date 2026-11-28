"""Monta o catálogo de jogos de Mega Drive (perfis_megadrive/catalogo_megadrive.json) a partir de uma
pasta de ROMs com nomes no padrão No-Intro ("Nome (Região) (Rev A).md").

    python tools/make_catalog.py "D:/roms/Mega Drive"          # lê a pasta (e subpastas)
    python tools/make_catalog.py lista.txt                      # ou uma lista de nomes, um por linha

Cada jogo vira uma linha (as versões do mesmo jogo — revisões, regiões — ficam juntas); protótipos,
não oficiais e traduções ficam em linhas separadas, com o tipo certo. A franquia é detectada pelo nome.
Os nomes dos arquivos ficam guardados para o conversor reconhecer a ROM aberta pelo nome.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mdconv.catalog import catalog_key, clean_title, detect_franchise, regions_of, kind_of  # noqa: E402

EXTS = (".md", ".bin", ".gen", ".smd")


def names_from(src: str) -> list[tuple[str, str]]:
    """(caminho relativo, nome do arquivo sem extensão)."""
    p = Path(src)
    out = []
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.suffix.lower() in EXTS:
                out.append((f.relative_to(p).as_posix(), f.stem))
    else:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip().replace("\\", "/")
            if line.lower().endswith(EXTS):
                out.append((line, Path(line).stem))
    return out


def build(entries: list[tuple[str, str]]) -> dict:
    games: dict[str, dict] = {}
    for rel, stem in entries:
        name = clean_title(stem)
        if not name:
            continue
        kind = kind_of(stem, rel)
        key = catalog_key(name, kind)
        g = games.get(key)
        if g is None:
            g = games[key] = dict(id=key, nome=name, regiao="", tipo=kind, franquia="", arquivos=[])
        g["arquivos"].append(stem)
        regs = set(g["regiao"].split(",")) - {""}
        regs |= set(regions_of(stem))
        order = "WUEJBK"
        g["regiao"] = ",".join(sorted(regs, key=lambda r: order.index(r) if r in order else 9))
    titles = [g["nome"] for g in games.values()]
    for g in games.values():
        g["franquia"] = detect_franchise(g["nome"], titles)
        g["arquivos"] = sorted(set(g["arquivos"]))
    jogos = sorted(games.values(), key=lambda g: (g["nome"].lower(), g["tipo"]))
    return {"versao": 1, "jogos": jogos}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cat = build(names_from(sys.argv[1]))
    out = ROOT / "perfis_megadrive" / "catalogo_megadrive.json"
    out.write_text(json.dumps(cat, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cat['jogos'])} jogos em {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
