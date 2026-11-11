"""Estatísticas musicais do MIDI por parte (faixa × canal) e da música inteira."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from mdconv.model.song import Note, Song

CC_NAMES = {
    0: "Bank Select MSB", 1: "Modulation", 2: "Breath", 5: "Portamento Time",
    6: "Data Entry MSB", 7: "Volume", 10: "Pan", 11: "Expression", 32: "Bank Select LSB",
    38: "Data Entry LSB", 64: "Sustain", 65: "Portamento", 71: "Resonance",
    72: "Release Time", 73: "Attack Time", 74: "Brightness", 91: "Reverb", 93: "Chorus",
    98: "NRPN LSB", 99: "NRPN MSB", 100: "RPN LSB", 101: "RPN MSB",
    111: "Loop (convenção RPG Maker)", 116: "Loop início (EMIDI)", 117: "Loop fim (EMIDI)",
    120: "All Sound Off", 121: "Reset All Controllers", 123: "All Notes Off",
}

PARAM_NAMES = {
    ("rpn", 0): "Pitch Bend Range (semitons)", ("rpn", 1): "Fine Tuning",
    ("rpn", 2): "Coarse Tuning",
    ("nrpn", 1 * 128 + 8): "Vibrato Rate (GS/XG)", ("nrpn", 1 * 128 + 9): "Vibrato Depth (GS/XG)",
    ("nrpn", 1 * 128 + 10): "Vibrato Delay (GS/XG)",
}


def param_name(kind: str, number: int) -> str:
    return PARAM_NAMES.get((kind, number), f"{kind.upper()} {number >> 7}:{number & 127}")


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(key: int) -> str:
    return f"{NOTE_NAMES[key % 12]}{key // 12 - 1}"


def max_polyphony(notes: list[Note], use_sustain: bool = False) -> tuple[int, int | None]:
    """Maior número de notas soando ao mesmo tempo e o tick onde ocorre."""
    ev = []
    for n in notes:
        end = n.sustain_end_tick if use_sustain else n.end_tick
        if end <= n.start_tick:
            continue  # nota de duração zero não ocupa voz
        ev.append((n.start_tick, 1))
        ev.append((end, -1))
    ev.sort(key=lambda x: (x[0], x[1]))  # fins antes de inícios no mesmo tick
    cur = best = 0
    at = None
    for t, d in ev:
        cur += d
        if cur > best:
            best, at = cur, t
    return best, at


def _active_ticks(notes: list[Note]) -> int:
    iv = sorted((n.start_tick, n.end_tick) for n in notes if n.end_tick > n.start_tick)
    total = 0
    cs = ce = None
    for s, e in iv:
        if ce is None or s > ce:
            if ce is not None:
                total += ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    if ce is not None:
        total += ce - cs
    return total


@dataclass
class PartStats:
    track: int
    channel: int
    track_name: str
    programs: list[tuple[int, int]]          # (banco, programa) na ordem de uso
    note_count: int
    key_min: int | None
    key_max: int | None
    key_mean: float | None
    vel_min: int | None
    vel_max: int | None
    vel_mean: float | None
    max_polyphony: int
    max_polyphony_at_s: float | None
    max_polyphony_sustain: int
    dur_min_s: float | None
    dur_max_s: float | None
    dur_mean_s: float | None
    active_fraction: float                   # fração da música em que a parte soa
    cc_counts: dict[int, int]
    volume_values: list[int]
    expression_values: list[int]
    pan_values: list[int]
    pitch_bend_count: int
    pitch_bend_range: tuple[int, int] | None
    sustain_events: int
    params: dict[str, list[int]]             # RPN/NRPN decodificados → valores usados
    bend_range_semitones: int                # RPN 0 ou o padrão (2, igual ao m4a e ao GM)
    zero_length_notes: int
    is_drum_channel: bool

    def summary_line(self) -> str:
        rng = f"{note_name(self.key_min)}–{note_name(self.key_max)}" if self.note_count else "—"
        progs = ", ".join(f"{b}:{p}" for b, p in self.programs) or "—"
        return (f"T{self.track:02d} C{self.channel + 1:02d} {self.track_name[:18]:<18} "
                f"prog {progs:<10} notas {self.note_count:>5}  faixa {rng:<9} "
                f"poli {self.max_polyphony}  ativo {self.active_fraction:5.1%}")


@dataclass
class LoopCandidate:
    source: str          # 'marker' | 'cc111' | 'emidi' | 'repetição'
    start_tick: int
    end_tick: int | None
    start_s: float
    end_s: float | None
    detail: str


@dataclass
class SongStats:
    length_s: float
    length_ticks: int
    ppq: int
    bpm_initial: float
    tempo_changes: list[tuple[int, float, float]]   # (tick, segundos, bpm)
    time_signatures: list[tuple[int, int, int]]
    track_count: int
    part_count: int
    note_count: int
    max_polyphony: int
    max_polyphony_at_s: float | None
    max_polyphony_sustain: int
    parts: list[PartStats]
    loops: list[LoopCandidate]
    unknown_ccs: dict[int, int]
    warnings: list[str] = field(default_factory=list)


_LOOP_RE = re.compile(r"loop|^\s*\[\s*$|^\s*\]\s*$|^\s*(start|end)\s*$", re.I)


def find_loops(song: Song, bar_ticks: int | None = None) -> list[LoopCandidate]:
    sec = song.tempo_map.seconds
    out: list[LoopCandidate] = []
    starts = [t for t in song.texts if t.kind in ("marker", "text", "cue") and _LOOP_RE.search(t.text)]
    begin = [t for t in starts if re.search(r"start|begin|\[|^loop$", t.text, re.I)]
    finish = [t for t in starts if re.search(r"end|\]", t.text, re.I)]
    for b in begin:
        e = next((f for f in finish if f.tick > b.tick), None)
        out.append(LoopCandidate("marker", b.tick, e.tick if e else None, b.seconds,
                                 e.seconds if e else None, f"'{b.text}'" + (f" … '{e.text}'" if e else "")))
    for ce in song.channel_events:
        if ce.kind == "cc" and ce.number == 111:
            out.append(LoopCandidate("cc111", ce.tick, None, ce.seconds, None,
                                     f"CC111 na faixa {ce.track}, canal {ce.channel + 1}"))
    emidi_s = [c for c in song.channel_events if c.kind == "cc" and c.number == 116]
    emidi_e = [c for c in song.channel_events if c.kind == "cc" and c.number == 117]
    for b in emidi_s:
        e = next((f for f in emidi_e if f.tick > b.tick), None)
        out.append(LoopCandidate("emidi", b.tick, e.tick if e else None, b.seconds,
                                 sec(e.tick) if e else None, "CC116/117"))
    if bar_ticks:
        rep = find_repetition(song, bar_ticks)
        if rep:
            out.append(rep)
    return out


def find_repetition(song: Song, bar_ticks: int, min_bars: int = 4) -> LoopCandidate | None:
    """Maior bloco de compassos que se repete integralmente mais adiante.

    Heurística: compara o conteúdo de cada compasso (posição relativa, faixa,
    canal, tecla, velocity, duração). Indica uma *possível* repetição; não
    prova que o jogo faz loop ali.
    """
    if not song.notes or bar_ticks <= 0:
        return None
    nbars = song.length_ticks // bar_ticks + 1
    bars: list[list] = [[] for _ in range(nbars)]
    for n in song.notes:
        b = n.start_tick // bar_ticks
        bars[b].append((n.start_tick - b * bar_ticks, n.track, n.channel, n.key, n.velocity,
                        n.end_tick - n.start_tick))
    sig = [hash(tuple(sorted(b))) if b else None for b in bars]
    best = None  # (comprimento, i, j)
    for i in range(nbars):
        if sig[i] is None:
            continue
        for j in range(i + min_bars, nbars):
            if sig[j] != sig[i]:
                continue
            L = 0
            while j + L < nbars and i + L < j and sig[i + L] == sig[j + L]:
                L += 1
            if L >= min_bars and (best is None or L > best[0]):
                best = (L, i, j)
    if not best:
        return None
    L, i, j = best
    sec = song.tempo_map.seconds
    return LoopCandidate("repetição", i * bar_ticks, j * bar_ticks, sec(i * bar_ticks), sec(j * bar_ticks),
                         f"{L} compassos a partir do compasso {i + 1} se repetem a partir do compasso {j + 1}")


def analyze_song(song: Song) -> SongStats:
    groups: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for n in song.notes:
        groups[(n.track, n.channel)].append(n)
    ev_by_part: dict[tuple[int, int], list] = defaultdict(list)
    for e in song.channel_events:
        ev_by_part[(e.track, e.channel)].append(e)
    keys = sorted(set(groups) | {k for k, evs in ev_by_part.items() if any(e.kind == "program" for e in evs)})

    unknown: Counter = Counter()
    parts: list[PartStats] = []
    total_ticks = max(song.length_ticks, 1)
    for tr, ch in keys:
        notes = groups.get((tr, ch), [])
        evs = ev_by_part.get((tr, ch), [])
        progs: list[tuple[int, int]] = []
        for n in notes:
            bp = (n.bank, n.program)
            if bp not in progs:
                progs.append(bp)
        ccs = Counter(e.number for e in evs if e.kind == "cc")
        for num, c in ccs.items():
            if num not in CC_NAMES:
                unknown[num] += c
        bends = [e.value for e in evs if e.kind == "pitch_bend"]
        mp, mp_at = max_polyphony(notes)
        mps, _ = max_polyphony(notes, use_sustain=True)
        durs = [n.duration_s for n in notes]
        ks = [n.key for n in notes]
        vs = [n.velocity for n in notes]
        parts.append(PartStats(
            track=tr, channel=ch, track_name=song.tracks[tr].name if tr < len(song.tracks) else "",
            programs=progs, note_count=len(notes),
            key_min=min(ks) if ks else None, key_max=max(ks) if ks else None,
            key_mean=sum(ks) / len(ks) if ks else None,
            vel_min=min(vs) if vs else None, vel_max=max(vs) if vs else None,
            vel_mean=sum(vs) / len(vs) if vs else None,
            max_polyphony=mp, max_polyphony_at_s=song.tempo_map.seconds(mp_at) if mp_at is not None else None,
            max_polyphony_sustain=mps,
            dur_min_s=min(durs) if durs else None, dur_max_s=max(durs) if durs else None,
            dur_mean_s=sum(durs) / len(durs) if durs else None,
            active_fraction=_active_ticks(notes) / total_ticks,
            cc_counts=dict(sorted(ccs.items())),
            volume_values=sorted({e.value for e in evs if e.kind == "cc" and e.number == 7}),
            expression_values=sorted({e.value for e in evs if e.kind == "cc" and e.number == 11}),
            pan_values=sorted({e.value for e in evs if e.kind == "cc" and e.number == 10}),
            pitch_bend_count=len(bends),
            pitch_bend_range=(min(bends), max(bends)) if bends else None,
            sustain_events=ccs.get(64, 0),
            params={param_name(e.kind, e.number): sorted({x.value for x in evs
                                                          if x.kind == e.kind and x.number == e.number})
                    for e in evs if e.kind in ("rpn", "nrpn")},
            bend_range_semitones=next((e.value for e in evs if e.kind == "rpn" and e.number == 0), 2),
            zero_length_notes=sum(1 for n in notes if n.end_tick <= n.start_tick),
            is_drum_channel=(ch == 9),
        ))

    tm = song.tempo_map
    tsig = song.time_signatures[0] if song.time_signatures else (0, 4, 4)
    bar_ticks = song.ppq * 4 * tsig[1] // tsig[2] if song.ppq else None
    mp, mp_at = max_polyphony(song.notes)
    mps, _ = max_polyphony(song.notes, use_sustain=True)
    warnings = list(song.warnings)
    zl = sum(p.zero_length_notes for p in parts)
    if zl:
        warnings.append(f"{zl} nota(s) com duração zero (mantidas; não ocupam voz na contagem de polifonia)")
    return SongStats(
        length_s=song.length_s, length_ticks=song.length_ticks, ppq=song.ppq,
        bpm_initial=tm.changes[0].bpm,
        tempo_changes=[(c.tick, c.seconds, c.bpm) for c in tm.changes],
        time_signatures=song.time_signatures,
        track_count=len(song.tracks), part_count=len(parts), note_count=len(song.notes),
        max_polyphony=mp, max_polyphony_at_s=tm.seconds(mp_at) if mp_at is not None else None,
        max_polyphony_sustain=mps, parts=parts,
        loops=find_loops(song, bar_ticks), unknown_ccs=dict(unknown), warnings=warnings,
    )
