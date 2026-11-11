"""Importa patches prontos (por enquanto TFI) para uma biblioteca de *patches de partida*.

Os importados não são "aprovados": servem de candidatos e de ponto de partida para o
ajuste. Cada um recebe o perfil espectral do próprio som FM (nota C4), para ser comparado
com o perfil dos instrumentos do SF2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mdconv.library.patch_library import LibraryEntry, PatchLibrary
from mdconv.library.tfi import TfiError, carrier_order_score, read_tfi

PROFILE_KEY = 60


@dataclass
class ImportReport:
    imported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)       # duplicados idênticos
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    carrier_order: tuple = (1.0, 0.0, 0)

    def text(self) -> str:
        L = [f"{len(self.imported)} patch(es) importado(s), {len(self.skipped)} repetido(s) ignorado(s), "
             f"{len(self.errors)} com erro."]
        ok, alt, n = self.carrier_order
        if n:
            L.append(f"Ordem dos operadores (algoritmo 4, {n} patches): portadoras claramente mais fortes "
                     f"em {ok:.0%} lendo S1,S3,S2,S4 contra {alt:.0%} na ordem trocada"
                     + (" — confere." if ok >= alt else " — ⚠ estes arquivos parecem usar outra ordem!"))
        L += [f"  ⚠ {w}" for w in self.warnings]
        L += [f"  ✖ {e}" for e in self.errors]
        return "\n".join(L)


def fm_profile(patch) -> tuple[list[float], float]:
    """Perfil de faixas (dB, ponderado pelo envelope) e centroide do patch tocando C4."""
    from mdconv.fm.metrics import describe
    from mdconv.fm.patch import midi_to_hz, render_note
    sr = 44100
    hz = midi_to_hz(PROFILE_KEY)
    a = render_note(patch, hz, 0.5, sr)
    a = a if a.ndim == 1 else a.mean(1)
    if not np.any(a):
        return [], 0.0
    d = describe(a, sr, hz)
    w = 10 ** (d.env_db / 20)
    prof = (d.bands_db * w[:, None]).sum(0) / max(w.sum(), 1e-12)
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    f = np.fft.rfftfreq(len(a), 1 / sr)
    centroid = float((spec * f).sum() / max(spec.sum(), 1e-12))
    return prof.round(2).tolist(), centroid


def _fingerprint(patch) -> str:
    return patch.to_dict().__repr__().replace(repr(patch.name), "")


def import_folder(folder: str | Path, library_dir: str | Path, tag: str = "",
                  progress=lambda s: None) -> ImportReport:
    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".tfi")
    lib = PatchLibrary(library_dir)
    seen = {_fingerprint(e.patch) for e in lib.entries()}
    rep = ImportReport()
    infos = []
    for i, p in enumerate(files, 1):
        rel = p.relative_to(folder)
        try:
            info = read_tfi(p)
        except (TfiError, OSError) as e:
            rep.errors.append(f"{rel}: {e}")
            continue
        infos.append(info)
        rep.warnings += [f"{rel}: {w}" for w in info.warnings]
        fp = _fingerprint(info.patch)
        if fp in seen:
            rep.skipped.append(str(rel))
            continue
        seen.add(fp)
        prof, cen = fm_profile(info.patch)
        tags = ["importado", "tfi"] + ([tag] if tag else []) + list(rel.parent.parts)
        lib.add(LibraryEntry(id="", name=p.stem, patch=info.patch, approved=False,
                             source={"format": "tfi", "file": str(rel), "ssg_eg": info.ssg_eg},
                             profile=prof, centroid=cen, tags=tags))
        rep.imported.append(str(rel))
        if i % 25 == 0:
            progress(f"{i}/{len(files)}…")
    rep.carrier_order = carrier_order_score(infos)
    return rep
