"""Histórico das decisões do usuário sobre os candidatos FM (aprovar, recusar, ouvir).

Cada decisão vira uma linha JSON (JSONL) num arquivo só, compartilhado por todas as
músicas e jogos, com o contexto necessário para, no futuro, ajustar a ordem dos
candidatos, as opções de busca por tipo de instrumento e os pesos da medida de
semelhança ao ouvido do usuário. Gravar nunca interrompe o programa: se falhar, ignora.

Local: ``%USERPROFILE%\\.mdconv\\decisoes.jsonl`` (ou a variável ``MDCONV_DECISOES``).
"""
from __future__ import annotations

import datetime
import json
import os
from collections import Counter, defaultdict
from pathlib import Path


def log_path() -> Path:
    env = os.environ.get("MDCONV_DECISOES")
    return Path(env) if env else Path.home() / ".mdconv" / "decisoes.jsonl"


def record(event: str, **fields) -> bool:
    from mdconv import __version__
    row = {"quando": datetime.datetime.now().isoformat(timespec="seconds"),
           "versao": __version__, "evento": event, **fields}
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read(path: Path | None = None) -> list[dict]:
    p = path or log_path()
    out = []
    try:
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def origin_family(origin: str) -> str:
    """"biblioteca: X + refino" -> "biblioteca"; "banco: …" -> "banco"; resto -> "busca do zero"."""
    o = (origin or "").strip()
    for fam in ("biblioteca", "banco"):
        if o.startswith(fam):
            return fam
    return "busca do zero"


def summary(path: Path | None = None) -> dict:
    rows = read(path)
    by_event = Counter(r.get("evento") for r in rows)
    by_origin = defaultdict(Counter)        # família da origem -> {aprovado, recusado}
    for r in rows:
        if r.get("evento") in ("aprovado", "recusado"):
            by_origin[origin_family(r.get("candidato", {}).get("origem", ""))][r["evento"]] += 1
    return {"total": len(rows), "eventos": dict(by_event),
            "por_origem": {k: dict(v) for k, v in by_origin.items()}, "arquivo": str(path or log_path())}
