"""Biblioteca de patches FM reutilizável entre músicas.

Cada entrada guarda o patch, de onde ele veio (SoundFont, preset, sample) e um resumo
das características do som original. Assim, ao converter outra música, um instrumento
parecido começa a busca a partir de patches já aprovados pelo ouvido.

Formato: uma pasta com um arquivo JSON por patch (fácil de versionar, copiar e editar).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from mdconv.fm.patch import Patch


@dataclass
class LibraryEntry:
    id: str
    name: str
    patch: Patch
    approved: bool = False                 # aprovado pela escuta do usuário
    source: dict = field(default_factory=dict)      # sf2, banco, programa, preset, samples
    profile: list[float] = field(default_factory=list)  # perfil de faixas médio do original (dB)
    centroid: float | None = None
    attack_s: float | None = None
    tags: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["patch"] = self.patch.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LibraryEntry":
        d = dict(d)
        d["patch"] = Patch.from_dict(d["patch"])
        return cls(**d)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")[:40] or "patch"


class PatchLibrary:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, entry_id: str) -> Path:
        return self.folder / f"{entry_id}.json"

    def add(self, entry: LibraryEntry) -> LibraryEntry:
        if not entry.id:
            base = _slug(entry.name)
            i = 1
            entry.id = base
            while self._path(entry.id).exists():
                i += 1
                entry.id = f"{base}_{i}"
        self._path(entry.id).write_text(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        return entry

    def get(self, entry_id: str) -> LibraryEntry:
        return LibraryEntry.from_dict(json.loads(self._path(entry_id).read_text(encoding="utf-8")))

    def entries(self) -> list[LibraryEntry]:
        out = []
        for p in sorted(self.folder.glob("*.json")):
            try:
                out.append(LibraryEntry.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (ValueError, KeyError, TypeError):
                continue          # arquivo inválido é ignorado, não apaga nada
        return out

    def remove(self, entry_id: str) -> None:
        self._path(entry_id).unlink(missing_ok=True)

    def nearest(self, profile: list[float], k: int = 5, approved_only: bool = False
                ) -> list[tuple[float, LibraryEntry]]:
        """Entradas cujo perfil espectral médio mais se parece com ``profile`` (dB médio de |Δ|)."""
        ref = np.asarray(profile, dtype=float)
        scored = []
        for e in self.entries():
            if approved_only and not e.approved:
                continue
            if not e.profile or len(e.profile) != len(ref):
                continue
            d = float(np.mean(np.abs(np.asarray(e.profile) - ref)))
            scored.append((d, e))
        scored.sort(key=lambda t: t[0])
        return scored[:k]
