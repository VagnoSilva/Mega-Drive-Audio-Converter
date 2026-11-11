"""Ajuste de patches FM para todos os instrumentos tonais de uma música, com prévias A/B.

Instrumentos com o mesmo (banco, programa) em canais diferentes são ajustados uma vez
só, com as notas de todos os canais. Kits de bateria não passam pelo FM aqui: o plano
para eles é PCM (Fase 5); isso é dito no relatório.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import numpy as np

from mdconv.analysis.instrument_match import match_instruments
from mdconv.analysis.midi_stats import note_name
from mdconv.fm.fitter import FitResult, RefNote, fit_instrument
from mdconv.fm.patch import render_note
from mdconv.io_sf2.resolve import find_preset
from mdconv.io_sf2.sf2 import SoundFont
from mdconv.model.song import Song
from mdconv.reference.instruments import render_note as ref_render
from mdconv.reference.sampler import write_wav

SR = 44100


@dataclass
class InstrumentJob:
    bank: int
    program: int
    name: str
    kind: str
    channels: list[int]
    keys: list[int]
    velocity: int
    hold_s: float


def plan_jobs(song: Song, sf: SoundFont) -> tuple[list[InstrumentJob], list[str]]:
    groups: dict[tuple[int, int], list] = defaultdict(list)
    skipped: list[str] = []
    for m in match_instruments(song, sf):
        if m.preset_name is None:
            skipped.append(f"C{m.channel + 1} {m.bank}:{m.program}: sem preset no SF2")
            continue
        if m.kind_hint.startswith("kit"):
            skipped.append(f"C{m.channel + 1} '{m.preset_name}': kit de bateria → PCM (Fase 5)")
            continue
        groups[(m.bank, m.program)].append(m)
    jobs = []
    for (bank, prog), ms in groups.items():
        notes = [n for n in song.notes if n.bank == bank and n.program == prog
                 and n.channel in {m.channel for m in ms}]
        ks = sorted(n.key for n in notes)
        keys = sorted({ks[0], ks[len(ks) // 2], ks[-1]})
        vel = int(median(n.velocity for n in notes))
        hold = float(np.clip(median(n.sustain_end_s - n.start_s for n in notes), 0.25, 1.0))
        jobs.append(InstrumentJob(bank, prog, ms[0].preset_name, ms[0].kind_hint,
                                  sorted(m.channel + 1 for m in ms), keys, vel, hold))
    return jobs, skipped


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")[:24]


def preview_chip():
    """Motor das prévias A/B: Nuked-OPN2 no modo YM2612 (o mesmo da prévia da música);
    sem ele, o emulador próprio em modo discreto. A busca dos patches continua no
    emulador próprio, que é bem mais rápido."""
    from mdconv.chips import nuked
    if nuked.available():
        return nuked.NukedYM2612(ym2612=True), "Nuked-OPN2 (modo YM2612)"
    from mdconv.chips.emulator import MegaDriveSound
    return MegaDriveSound(discrete_ym2612=True), "emulador próprio (YM2612 discreto)"


def ab_audio(refs: list[RefNote], patch) -> np.ndarray:
    """Original e FM alternados, nota por nota, cada um normalizado (-2 dBFS)."""
    gap = np.zeros(int(0.25 * SR), np.float32)
    parts = []
    for r in refs:
        fm = render_note(patch, r.hz, r.hold_s, SR, chip=preview_chip()[0])
        parts += [r.audio / (np.abs(r.audio).max() or 1) * 0.79, gap,
                  fm / (np.abs(fm).max() or 1) * 0.79, gap, gap]
    return np.concatenate(parts)


def fit_song(song: Song, sf: SoundFont, out_dir: Path, budget: str = "normal",
             only_programs: set[int] | None = None, progress=print,
             max_fb: int = 7, detune: bool = False,
             only: set[tuple[int, int]] | None = None, bank=None, bank_only: bool = False,
             **fit_kw) -> dict:
    """``only``: só estes (banco, programa). Ajustes anteriores na mesma pasta são mantidos
    (o patches.json é mesclado), então dá para ajustar um instrumento por vez.
    ``fit_kw`` vai direto para :func:`fit_instrument` (ex.: carrier_min_mul, mod_tl_min).
    ``bank``: :class:`~mdconv.library.bank.PatchBank` — acrescenta candidatos vindos de
    patches prontos (``bank_only`` pula a busca do zero)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs, skipped = plan_jobs(song, sf)
    old_path = out_dir / "patches.json"
    previous = []
    if old_path.exists():
        try:
            previous = json.loads(old_path.read_text(encoding="utf-8")).get("instruments", [])
        except (ValueError, OSError):
            previous = []
    cfg = {"quick": dict(n_screen=20, de_iter=8, de_pop=4),
           "normal": dict(n_screen=60, de_iter=30, de_pop=4),
           "thorough": dict(n_screen=120, de_iter=60, de_pop=6)}[budget]
    report = {"skipped": skipped, "instruments": [], "preview_engine": preview_chip()[1],
              "search_engine": "emulador próprio"}
    def save():
        labels = {e["label"] for e in report["instruments"]}
        merged = dict(report, instruments=[e for e in previous if e["label"] not in labels]
                      + report["instruments"])
        old_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    for job in jobs:
        if only_programs is not None and job.program not in only_programs:
            continue
        if only is not None and (job.bank, job.program) not in only:
            continue
        preset = find_preset(sf, job.bank, job.program).preset
        refs = []
        for k in job.keys:
            a, voices, f0 = ref_render(sf, preset, k, job.velocity, job.hold_s, SR)
            if voices:
                refs.append(RefNote(f0, job.hold_s, a, SR))
        if not refs:
            continue
        label = f"{job.bank}-{job.program}_{_slug(job.name)}"
        progress(f"[{label}] canais {job.channels}, notas "
                 f"{', '.join(note_name(k) for k in job.keys)}")
        search = [refs[0], refs[-1]] if len(refs) > 1 else refs
        import time as _time
        t0 = _time.time()
        if bank_only:
            res = FitResult({}, [], {}, 0.0, ["busca do zero pulada (só banco)"])
        else:
            res = fit_instrument(search, refs, progress=lambda s: progress(f"    {s}"),
                                 max_fb=max_fb, detune=detune, **cfg, **fit_kw)
        if bank is not None:
            from mdconv.fm.fitter import fit_from_bank
            bc = fit_from_bank(bank, search, refs, max_fb=max_fb,
                               carrier_min_mul=fit_kw.get("carrier_min_mul", 0),
                               mod_tl_min=fit_kw.get("mod_tl_min", 0),
                               progress=lambda s: progress(f"    {s}"))
            res.candidates = list(res.candidates) + bc
        res.seconds = _time.time() - t0
        entry = {"label": label, "preset": job.name, "bank": job.bank, "program": job.program,
                 "kind": job.kind, "channels": job.channels,
                 "keys": [note_name(k) for k in job.keys], "velocity": job.velocity,
                 "seconds": round(res.seconds, 1), "log": res.log, "candidates": []}
        for i, c in enumerate(res.candidates, 1):
            c.patch.name = f"{job.name} #{i}"
            wav = out_dir / f"{label}_cand{i}_AB.wav"
            write_wav(wav, ab_audio(refs, c.patch), SR)
            entry["candidates"].append({
                "rank": i, "stage": c.stage, "cost_total": round(c.cost.total, 2),
                "cost_spectrum_db": round(c.cost.spectrum, 2),
                "cost_envelope_db": round(c.cost.envelope, 2),
                "wav": wav.name, "patch": c.patch.to_dict()})
        report["instruments"].append(entry)
        save()
        if res.candidates:
            best = min(res.candidates, key=lambda c: c.cost.total)
            progress(f"    pronto em {res.seconds:.0f}s; melhor custo {best.cost.total:.2f} ({best.stage})")
    return report


def report_text(report: dict) -> str:
    L = ["Prévias FM (A/B): em cada WAV, para cada nota, toca o ORIGINAL, depois o FM.",
         f"Motor da prévia: {report.get('preview_engine', '?')}; busca feita no "
         f"{report.get('search_engine', '?')}.",
         "Custo = diferença média em dB (espectro + ½·envelope). Menor é mais parecido pela",
         "medição — mas quem decide é o ouvido.", ""]
    for e in report["instruments"]:
        L.append(f"{e['label']}  '{e['preset']}' [{e['kind']}] canais {e['channels']} "
                 f"notas {', '.join(e['keys'])}  ({e['seconds']}s)")
        for c in e["candidates"]:
            p = c["patch"]
            L.append(f"   #{c['rank']} alg {p['alg']} fb {p['fb']} oitava {p['octave']:+d} "
                     f"MUL {[o['mul'] for o in p['ops']]}  custo {c['cost_total']:.2f} "
                     f"(espectro {c['cost_spectrum_db']:.1f} dB, envelope {c['cost_envelope_db']:.1f} dB)"
                     f"  → {c['wav']}")
    if report["skipped"]:
        L += ["", "Não ajustados em FM:"] + [f"   {s}" for s in report["skipped"]]
    return "\n".join(L)


def approve(out_dir: Path, label: str, rank: int, sf: SoundFont, library_dir: Path) -> dict:
    """Marca o candidato escolhido pelo usuário como aprovado e o grava na biblioteca, com
    o perfil espectral médio do original (para sugerir este patch a instrumentos
    parecidos em outras músicas)."""
    from mdconv.fm.metrics import describe
    from mdconv.fm.patch import Patch
    from mdconv.library.patch_library import LibraryEntry, PatchLibrary
    path = out_dir / "patches.json"
    rep = json.loads(path.read_text(encoding="utf-8"))
    inst = next(e for e in rep["instruments"] if e["label"] == label)
    cand = next(c for c in inst["candidates"] if c["rank"] == rank)
    patch = Patch.from_dict(cand["patch"])
    preset = find_preset(sf, inst["bank"], inst["program"]).preset
    from mdconv.analysis.midi_stats import NOTE_NAMES
    def key_of(name):
        n, o = name[:-1], int(name[-1])
        return NOTE_NAMES.index(n) + 12 * (o + 1)
    mid = key_of(inst["keys"][len(inst["keys"]) // 2])
    a, voices, f0 = ref_render(sf, preset, mid, inst["velocity"], 0.5, SR)
    d = describe(a, SR, f0)
    w = 10 ** (d.env_db / 20)
    profile = ((d.bands_db * w[:, None]).sum(0) / w.sum()).round(2).tolist()
    entry = PatchLibrary(library_dir).add(LibraryEntry(
        id="", name=inst["preset"], patch=patch, approved=True,
        source={"sf2": getattr(sf, "path", None), "bank": inst["bank"], "program": inst["program"],
                "preset": inst["preset"], "samples": sorted({v.sample.name for v in voices}),
                "fit_label": label, "candidate": rank, "cost": cand["cost_total"],
                "approved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds")},
        profile=profile, tags=[inst["kind"]]))
    for c in inst["candidates"]:
        c["approved"] = c["rank"] == rank
    inst["approved_rank"] = rank
    inst["library_id"] = entry.id
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"label": label, "rank": rank, "library_id": entry.id, "patch": cand["patch"]}
