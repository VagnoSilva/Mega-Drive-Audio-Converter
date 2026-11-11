"""Banco de *patches de partida* importados (milhares de patches, um arquivo só).

Cada patch guarda, além dos parâmetros, um descritor do próprio som FM (nota C4, 0,5 s):
perfil médio das faixas de 1/6 de oitava relativas à nota e o envelope nos primeiros
0,6 s — os mesmos descritores que o ajuste usa (``mdconv.fm.metrics``). Assim dá para
achar rapidamente, entre dezenas de milhares, os patches que mais lembram um instrumento
do SF2, e só esses passam pela avaliação completa.

Formato: ``banco.npz`` (arrays) + ``banco.json`` (nomes, origem, avisos).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mdconv.fm.metrics import FLOOR_DB, N_BANDS, describe
from mdconv.fm.patch import Operator, Patch, midi_to_hz, render_note

PROFILE_KEY = 60
HOLD_S = 0.5
ENV_FRAMES = 60                    # 0,6 s em quadros de 10 ms
OP_FIELDS = ("mul", "dt", "tl", "ks", "ar", "d1r", "d2r", "sl", "rr")
OCTAVES = (-2, -1, 0, 1)           # faixa de Patch.octave


def descriptor(audio: np.ndarray, sr: float, hz: float) -> tuple[np.ndarray, np.ndarray]:
    """(perfil médio [36], envelope [60]) em dB relativos ao pico, piso −60."""
    d = describe(audio, sr, hz, max_s=HOLD_S + 0.3)
    w = 10 ** (d.env_db / 20)
    prof = (d.bands_db * w[:, None]).sum(0) / max(float(w.sum()), 1e-12)
    prof = np.where(d.valid_bands, prof, FLOOR_DB)
    prof = np.maximum(prof - prof.max(), FLOOR_DB)
    env = np.full(ENV_FRAMES, FLOOR_DB, np.float32)
    k = min(ENV_FRAMES, len(d.env_db))
    env[:k] = d.env_db[:k]
    return prof.astype(np.float32), env


def patch_descriptor(patch: Patch) -> tuple[np.ndarray, np.ndarray] | None:
    from mdconv.chips.emulator import NTSC_FM_CLOCK
    p = Patch.from_dict(patch.to_dict())
    p.octave = 0
    hz = midi_to_hz(PROFILE_KEY)
    x = render_note(p, hz, HOLD_S, sr_out=None, max_release_s=0.3)
    x = x if x.ndim == 1 else x.mean(1)
    if not np.any(np.abs(x) > 1e-4):
        return None
    return descriptor(x, NTSC_FM_CLOCK / 144.0, hz)


def patch_to_row(p: Patch) -> np.ndarray:
    head = [p.alg, p.fb]
    ops = [getattr(o, f) for o in p.ops for f in OP_FIELDS]
    return np.array(head + ops, np.int16)


def row_to_patch(r: np.ndarray, name: str = "") -> Patch:
    ops = []
    for i in range(4):
        v = r[2 + i * len(OP_FIELDS): 2 + (i + 1) * len(OP_FIELDS)]
        ops.append(Operator(**{f: int(x) for f, x in zip(OP_FIELDS, v, strict=True)}))
    return Patch(alg=int(r[0]), fb=int(r[1]), ops=ops, name=name)


@dataclass
class Match:
    index: int
    name: str
    source: str
    octave: int
    distance: float
    patch: Patch


class PatchBank:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        z = np.load(self.folder / "banco.npz")
        self.rows, self.profiles, self.envs = z["rows"], z["profiles"], z["envs"]
        meta = json.loads((self.folder / "banco.json").read_text(encoding="utf-8"))
        self.names: list[str] = meta["names"]
        self.sources: list[str] = meta["sources"]
        self.info: dict = meta.get("info", {})

    def __len__(self) -> int:
        return len(self.names)

    @staticmethod
    def exists(folder) -> bool:
        f = Path(folder)
        return (f / "banco.npz").exists() and (f / "banco.json").exists()

    def patch(self, i: int, octave: int = 0) -> Patch:
        p = row_to_patch(self.rows[i], self.names[i])
        p.octave = octave
        return p

    def nearest(self, profile: np.ndarray, env: np.ndarray, k: int = 40,
                w_env: float = 0.5, max_fb: int = 7) -> list[Match]:
        """Os ``k`` patches mais próximos. Cada um é testado também em outras oitavas
        (``Patch.octave``): deslocar a altura é deslocar o perfil 6 faixas por oitava."""
        P = self.profiles
        best = np.full(len(P), np.inf, np.float32)
        best_oct = np.zeros(len(P), np.int8)
        ref = np.asarray(profile, np.float32)
        e = np.mean(np.abs(self.envs - np.asarray(env, np.float32)[None, :]), axis=1)
        for o in OCTAVES:
            s = 6 * o
            shifted = np.full_like(P, FLOOR_DB)
            if s >= 0:
                shifted[:, s:] = P[:, :N_BANDS - s] if s else P
            else:
                shifted[:, :N_BANDS + s] = P[:, -s:]
            aud = np.maximum(shifted, ref[None, :]) > -45
            d = np.where(aud, np.abs(shifted - ref[None, :]), 0).sum(1) / np.maximum(aud.sum(1), 1)
            tot = d + w_env * e
            better = tot < best
            best[better] = tot[better]
            best_oct[better] = o
        if max_fb < 7:
            best[self.rows[:, 1] > max_fb] = np.inf
        order = np.argsort(best)[:k]
        return [Match(int(i), self.names[i], self.sources[i], int(best_oct[i]), float(best[i]),
                      self.patch(int(i), int(best_oct[i]))) for i in order if np.isfinite(best[i])]


def _work(args):
    path_bytes, name = args
    from mdconv.library.tfi import parse_tfi
    info = parse_tfi(path_bytes, name)
    d = patch_descriptor(info.patch)
    return info, d


def build_bank(folder: str | Path, out_dir: str | Path, workers: int = 1,
               progress=lambda s: None) -> dict:
    """Lê todos os .tfi de ``folder`` (subpastas incluídas), remove repetidos e grava o banco.
    Retorna um resumo."""
    from mdconv.library.tfi import TfiError, carrier_order_score
    folder, out_dir = Path(folder), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".tfi")
    seen: dict[bytes, str] = {}
    jobs, errors = [], []
    for p in files:
        try:
            b = p.read_bytes()
        except OSError as e:
            errors.append(f"{p}: {e}")
            continue
        if len(b) != 42:
            errors.append(f"{p.relative_to(folder)}: {len(b)} bytes (TFI tem 42)")
            continue
        if b in seen:
            continue
        rel = str(p.relative_to(folder).as_posix())
        seen[b] = rel
        jobs.append((b, rel))
    progress(f"{len(files)} arquivos .tfi, {len(jobs)} patches diferentes")
    results = []
    if workers > 1:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(workers) as pool:
            for i, r in enumerate(pool.imap(_work, jobs, chunksize=64), 1):
                results.append(r)
                if i % 1000 == 0:
                    progress(f"{i}/{len(jobs)}")
    else:
        for i, j in enumerate(jobs, 1):
            try:
                results.append(_work(j))
            except TfiError as e:
                errors.append(f"{j[1]}: {e}")
                continue
            if i % 1000 == 0:
                progress(f"{i}/{len(jobs)}")
    rows, profs, envs, names, sources = [], [], [], [], []
    silent = ssg = 0
    infos = []
    for (b, rel), (info, d) in zip(jobs, results, strict=True):
        infos.append(info)
        if d is None:
            silent += 1
            continue
        ssg += info.uses_ssg_eg
        rows.append(patch_to_row(info.patch))
        profs.append(d[0])
        envs.append(d[1])
        names.append(Path(rel).stem)
        sources.append(rel)
    ok, alt, n4 = carrier_order_score(infos)
    summary = {"files": len(files), "unique": len(jobs), "in_bank": len(names), "silent": silent,
               "ssg_eg_ignored": ssg, "errors": errors[:200], "n_errors": len(errors),
               "operator_order_check": {"alg4": n4, "S1S3S2S4": round(ok, 3), "S1S2S3S4": round(alt, 3)}}
    np.savez_compressed(out_dir / "banco.npz", rows=np.array(rows, np.int16),
                        profiles=np.array(profs, np.float32), envs=np.array(envs, np.float32))
    (out_dir / "banco.json").write_text(json.dumps(
        {"names": names, "sources": sources, "info": summary}, ensure_ascii=False), encoding="utf-8")
    return summary
