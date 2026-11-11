"""Renderização de referência por instrumento + medição de características.

Para cada instrumento realmente usado (parte × programa), renderiza notas
representativas da música — grave, mediana e aguda para instrumentos tonais; cada
peça usada nos kits — com a velocity e a duração típicas da própria música, sem
vibrato e sem controles de canal (o timbre "seco" do SF2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import numpy as np

from mdconv.analysis.features import Features, analyze_note, midi_to_hz
from mdconv.analysis.instrument_match import InstrumentMatch, match_instruments
from mdconv.analysis.midi_stats import note_name
from mdconv.io_sf2.resolve import find_preset, resolve_voices
from mdconv.io_sf2.sf2 import SoundFont
from mdconv.model.song import Song
from mdconv.reference.sampler import render_voice, write_wav


@dataclass
class NoteRender:
    key: int
    velocity: int
    hold_s: float
    samples: list[str]
    f0_expected_hz: float
    wav: str | None
    features: Features


@dataclass
class InstrumentRender:
    channel: int
    track: int
    bank: int
    program: int
    preset_name: str
    kind: str
    notes: list[NoteRender] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"C{self.channel + 1:02d}_{self.bank}-{self.program}"


def render_note(sf: SoundFont, preset, key: int, vel: int, hold_s: float, sr: int,
                interp: str = "linear") -> tuple[np.ndarray, list, float]:
    voices = resolve_voices(sf, preset, key, vel)
    if not voices:
        return np.zeros(1, np.float32), [], midi_to_hz(key)
    parts = [render_voice(v, sf, hold_s, sr, None, interp) for v in voices]
    n = max(len(p) for p in parts)
    out = np.zeros(n, np.float32)
    for p in parts:
        out[: len(p)] += p
    v0 = voices[0]
    f0 = midi_to_hz(v0.root_key + v0.tuning_cents / 100.0)
    return out, voices, f0


def render_instruments(song: Song, sf: SoundFont, out_dir: Path | None = None, sr: int = 44100,
                       interp: str = "linear", max_kit_pieces: int = 16,
                       gm_drum_channel: bool | None = None) -> list[InstrumentRender]:
    matches: list[InstrumentMatch] = match_instruments(song, sf, gm_drum_channel)
    results: list[InstrumentRender] = []
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for m in matches:
        if m.preset_name is None:
            continue
        drum = bool(gm_drum_channel) and m.channel == 9
        preset = find_preset(sf, m.bank, m.program, drum_channel=drum).preset
        notes = [n for n in song.notes if n.channel == m.channel and n.track == m.track
                 and n.bank == m.bank and n.program == m.program]
        vel = int(median(n.velocity for n in notes))
        hold = float(np.clip(median(n.sustain_end_s - n.start_s for n in notes), 0.3, 2.0))
        if m.kind_hint.startswith("kit"):
            keys = sorted(m.voices_by_key)[:max_kit_pieces]
            hold_k = 0.3
        else:
            ks = sorted(n.key for n in notes)
            keys = sorted({ks[0], ks[len(ks) // 2], ks[-1]})
            hold_k = hold
        ir = InstrumentRender(m.channel, m.track, m.bank, m.program, m.preset_name, m.kind_hint)
        for k in keys:
            audio, voices, f0 = render_note(sf, preset, k, vel, hold_k, sr, interp)
            if not voices:
                continue
            feat = analyze_note(audio, sr, f0, hold_k, pitched=not m.kind_hint.startswith("kit"))
            wav = None
            if out_dir:
                wav = str(out_dir / f"{ir.label}_{note_name(k).replace('#', 's')}_v{vel}.wav")
                peak = float(np.max(np.abs(audio))) or 1.0
                write_wav(wav, audio / peak * 0.89, sr)       # -1 dBFS por arquivo
            ir.notes.append(NoteRender(k, vel, hold_k, sorted({v.sample.name for v in voices}),
                                       f0, wav, feat))
        results.append(ir)
    return results


def summary_text(results: list[InstrumentRender]) -> str:
    L = ["Instrumento                       nota  f0 medida  desvio  ataque  sustent.  decai.   "
         "release  harm.   brilho  vibrato",
         "(harm.: fração da energia nos harmônicos — baixa = ensemble/inarmônico; "
         "brilho: centroide em harmônicos (h) nos tonais, em kHz nas peças de bateria)"]
    for ir in results:
        L.append(f"{ir.label}  '{ir.preset_name}' [{ir.kind}]")
        for nr in ir.notes:
            f = nr.features
            e = f.envelope
            sus = "—" if e.sustain_db == -np.inf else f"{e.sustain_db:5.1f}dB"
            dec = "—" if e.decay_s is None else f"{e.decay_s:5.2f}s"
            rel = "—" if e.release_s is None else f"{e.release_s:5.2f}s"
            vib = (f"{f.vibrato_rate_hz:.1f}Hz ±{f.vibrato_depth_cents:.0f}c"
                   if f.vibrato_rate_hz else "—")
            bright = (f"{f.centroid_mean:5.2f}h" if ir.kind != "kit (keysplit)"
                      else f"{f.centroid_hz / 1000:4.1f}kHz")
            f0m = f"{f.f0_measured_hz:7.1f}Hz" if f.f0_measured_hz else "     —   "
            det = f"{f.detune_cents:+5.0f}c" if f.detune_cents is not None else "   — "
            L.append(f"    {note_name(nr.key):>5} v{nr.velocity:<3}           {f0m} {det}  "
                     f"{e.attack_s * 1000:5.0f}ms  {sus:>8}  {dec:>6}  {rel:>7}  "
                     f"{f.harmonicity:5.0%}  {bright:>7}  {vib}")
    return "\n".join(L)
