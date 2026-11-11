"""Relatório da Fase 1 em JSON (para a GUI/testes) e em texto (para a CLI)."""
from __future__ import annotations

import dataclasses
import json
from collections import Counter

from mdconv.analysis.instrument_match import InstrumentMatch
from mdconv.analysis.midi_stats import CC_NAMES, SongStats, note_name
from mdconv.io_sf2.sf2 import SoundFont


def sf2_summary(sf: SoundFont) -> dict:
    rates = Counter(s.sample_rate for s in sf.samples)
    looped = sum(1 for s in sf.samples if s.loop_end > s.loop_start)
    return {
        "path": sf.path,
        "info": sf.info,
        "preset_count": len(sf.presets),
        "instrument_count": len(sf.instruments),
        "sample_count": len(sf.samples),
        "sample_rates": dict(sorted(rates.items())),
        "samples_with_loop": looped,
        "has_24bit": sf.sm24_offset is not None,
        "presets": [
            {"bank": p.bank, "program": p.program, "name": p.name,
             "zones": len(p.zones),
             "instruments": sorted({sf.instruments[z.get(41)].name for z in p.zones
                                    if z.get(41) is not None and z.get(41) < len(sf.instruments)})}
            for p in sorted(sf.presets, key=lambda p: (p.bank, p.program))
        ],
        "warnings": sf.warnings,
    }


def _plain(o):
    if dataclasses.is_dataclass(o):
        return {k: _plain(v) for k, v in dataclasses.asdict(o).items()}
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_plain(v) for v in o]
    return o


def to_json(stats: SongStats, sf_sum: dict | None, matches: list[InstrumentMatch] | None) -> str:
    return json.dumps({"midi": _plain(stats), "sf2": sf_sum, "matches": _plain(matches)},
                      ensure_ascii=False, indent=2)


def to_text(stats: SongStats, sf_sum: dict | None, matches: list[InstrumentMatch] | None) -> str:
    L: list[str] = []
    m, s = divmod(stats.length_s, 60)
    L.append("=== MIDI ===")
    L.append(f"Duração {int(m)}:{s:05.2f}  ·  PPQ {stats.ppq}  ·  faixas {stats.track_count}  ·  "
             f"partes {stats.part_count}  ·  notas {stats.note_count}")
    L.append(f"Andamento inicial {stats.bpm_initial:.2f} BPM  ·  mudanças de andamento: "
             f"{max(0, len(stats.tempo_changes) - 1)}")
    for tick, sec, bpm in stats.tempo_changes[1:21]:
        L.append(f"    tick {tick:>7}  {sec:8.3f}s  → {bpm:.2f} BPM")
    if len(stats.tempo_changes) > 21:
        L.append(f"    … mais {len(stats.tempo_changes) - 21}")
    L.append(f"Polifonia máxima da música: {stats.max_polyphony} vozes "
             f"(em {stats.max_polyphony_at_s or 0:.2f}s); com pedal: {stats.max_polyphony_sustain}")
    L.append("  Mega Drive: 6 FM + 3 PSG tom + 1 ruído (5 FM se usar DAC)")
    L.append("")
    L.append("Partes (faixa × canal):")
    for p in stats.parts:
        L.append("  " + p.summary_line())
        extra = []
        if p.pitch_bend_count:
            extra.append(f"pitch bend ×{p.pitch_bend_count} {p.pitch_bend_range}")
        if p.params:
            extra.append("; ".join(f"{k} = {v}" for k, v in p.params.items()))
        if p.cc_counts:
            extra.append("CCs " + ", ".join(
                f"{n}{'' if n in CC_NAMES else '?'}×{c}" for n, c in p.cc_counts.items()))
        if extra:
            L.append("      " + " · ".join(extra))
    if stats.unknown_ccs:
        L.append("")
        L.append("CCs fora do padrão GM (provavelmente controles do m4a; serão mapeados na Fase 1 "
                 "com seus arquivos): " + ", ".join(f"CC{n}×{c}" for n, c in sorted(stats.unknown_ccs.items())))
    L.append("")
    L.append("Loops / repetições:")
    if not stats.loops:
        L.append("  nenhum marcador ou repetição encontrada")
    for lp in stats.loops:
        end = f"{lp.end_s:.2f}s" if lp.end_s is not None else "?"
        L.append(f"  [{lp.source}] {lp.start_s:.2f}s → {end}  {lp.detail}")

    if sf_sum:
        L.append("")
        L.append("=== SoundFont ===")
        L.append(f"{sf_sum['preset_count']} presets · {sf_sum['instrument_count']} instrumentos · "
                 f"{sf_sum['sample_count']} samples ({sf_sum['samples_with_loop']} com loop)")
        L.append("Taxas de amostragem: " + ", ".join(f"{r} Hz×{c}" for r, c in sf_sum["sample_rates"].items()))
        for w in sf_sum["warnings"][:20]:
            L.append(f"  aviso: {w}")

    if matches is not None:
        L.append("")
        L.append("=== Instrumentos usados → SoundFont ===")
        for mt in matches:
            head = f"T{mt.track:02d} C{mt.channel + 1:02d} {mt.bank}:{mt.program}"
            if mt.preset_name is None:
                L.append(f"  {head}  ✗ {mt.reason}")
                if mt.candidates:
                    L.append(f"      candidatos: {'; '.join(mt.candidates[:6])}")
                continue
            mark = "✓" if mt.exact else "≈"
            rng = f"{note_name(mt.keys_used[0])}–{note_name(mt.keys_used[-1])}" if mt.keys_used else ""
            L.append(f"  {head}  {mark} '{mt.preset_name}' [{mt.kind_hint}] notas {mt.note_count} {rng}  "
                     f"samples {len(mt.samples)}  camadas até {mt.max_layers}")
            if not mt.exact:
                L.append(f"      {mt.reason}")
            if mt.keys_without_voice:
                L.append(f"      ⚠ notas sem zona no SF2 (mudas no original): "
                         f"{', '.join(note_name(k) for k in mt.keys_without_voice[:12])}")
    if stats.warnings:
        L.append("")
        L.append("Avisos do MIDI:")
        for w in stats.warnings:
            L.append(f"  - {w}")
    return "\n".join(L)
