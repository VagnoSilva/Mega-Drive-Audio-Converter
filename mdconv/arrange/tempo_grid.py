"""Andamento travado na grade de quadros (60 Hz).

O XGM toca em quadros de 1/60 s. Se o passo rítmico da música (por exemplo, a semicolcheia)
dura 5,88 quadros, o arredondamento alterna passos de 6 e de 5 quadros — uma oscilação de
17 ms que se percebe num ritmo regular. Músicos que compõem direto para o Mega Drive escolhem
um andamento em que o passo dura um número inteiro de quadros.

Aqui o mapa de andamento é ajustado (cada trecho de andamento separadamente) para que o passo
dure exatamente N quadros, desde que a mudança de andamento seja pequena (``tol``). Todos os
tempos em segundos da música (notas, controles, textos) são recalculados a partir do mapa
novo; os ticks não mudam, então loop, compassos e exportação para trackers continuam certos.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from mdconv.io_midi.tempo import TempoChange, TempoMap
from mdconv.model.song import Song

FPS = 60.0


@dataclass
class GridReport:
    step_ticks: int | None = None
    on_grid: float = 0.0
    segments: list[dict] = field(default_factory=list)   # tick, bpm antes/depois, quadros por passo
    applied: bool = False
    reason: str = ""

    def text(self) -> str:
        if not self.step_ticks:
            return f"andamento mantido ({self.reason})"
        L = [f"grade rítmica: {self.step_ticks} ticks ({self.on_grid:.0%} das notas começam nela)"]
        for s in self.segments:
            if s["snapped"]:
                L.append(f"  a partir do tick {s['tick']}: {s['bpm']:.2f} → {s['new_bpm']:.2f} BPM "
                         f"({s['frames']:.2f} → {s['n']} quadros a cada {s['step']} ticks, {s['change']:+.1%})")
            else:
                L.append(f"  a partir do tick {s['tick']}: {s['bpm']:.2f} BPM mantido "
                         f"({s['frames']:.2f} quadros por passo; ajuste de {s['change']:+.1%} passaria do limite)")
        return "\n".join(L)


def _on_grid(starts, g) -> float:
    return sum(1 for t in starts if t % g == 0) / len(starts)


def find_step(song: Song, min_on_grid: float = 0.85) -> tuple[int | None, float]:
    """Passo rítmico principal: o maior passo (em ticks) em que a grande maioria das notas
    começa. Algumas notas fora dele (ornamentos, quiálteras) não impedem a escolha."""
    starts = [n.start_tick for n in song.notes]
    if not starts:
        return None, 0.0
    ppq = song.ppq
    cands = sorted({ppq * 4 // d for d in (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48) if ppq * 4 % d == 0}, reverse=True)
    best = (None, 0.0)
    for g in cands:
        if g < 1:
            continue
        frac = _on_grid(starts, g)
        if frac >= min_on_grid:
            return g, frac
        if frac > best[1]:
            best = (g, frac)
    return None, best[1]


def snap_tempo(song: Song, tol: float = 0.06, fps: float = FPS) -> tuple[Song, GridReport]:
    """Devolve uma cópia da música com o andamento ajustado à grade de quadros."""
    rep = GridReport()
    tm = song.tempo_map
    if tm.smpte is not None:
        rep.reason = "arquivo com tempo SMPTE"
        return song, rep
    g, frac = find_step(song)
    rep.on_grid = frac
    if g is None:
        rep.reason = f"sem grade rítmica clara (no máximo {frac:.0%} das notas numa grade)"
        return song, rep
    rep.step_ticks = g
    # notas na metade do passo também em número inteiro de quadros: N par, se couber
    starts = [n.start_tick for n in song.notes]
    want_even = g % 2 == 0 and _on_grid(starts, g // 2) - frac >= 0.02
    raw = []
    for c in tm.changes:
        seg = None
        # a grade fina primeiro; se não couber, o dobro do passo (a pulsação principal fica
        # regular e só as notas no meio dela oscilam), até uma semínima
        for mult in (1, 2, 4):
            G = g * mult
            if G > tm.ppq and mult > 1:
                break
            frames = G * c.us_per_quarter / 1e6 / tm.ppq * fps
            n = max(1, round(frames))
            if (want_even or mult > 1) and n % 2:
                alt = min((n - 1, n + 1), key=lambda m: abs(frames / m - 1.0))
                if alt >= 2 and abs(frames / alt - 1.0) <= tol:
                    n = alt
            change = frames / n - 1.0           # >0: a música fica mais rápida
            ok = abs(change) <= tol and n >= 2
            cand = {"tick": c.tick, "bpm": c.bpm, "frames": frames, "n": n, "change": change,
                    "snapped": ok, "step": G}
            if seg is None:
                seg = cand
            if ok:
                seg = cand
                break
        us = int(round(seg["n"] / fps * 1e6 * tm.ppq / seg["step"])) if seg["snapped"] else c.us_per_quarter
        seg["new_bpm"] = 60e6 / us
        raw.append((c.tick, us))
        rep.segments.append(seg)
    rep.applied = any(s["snapped"] and abs(s["change"]) > 1e-9 for s in rep.segments)
    if not rep.applied:
        if not any(s["snapped"] for s in rep.segments):
            rep.reason = "o ajuste mudaria demais o andamento"
        return song, rep
    new_tm = TempoMap.build(tm.ppq, raw, None)
    return retime(song, new_tm), rep


def retime(song: Song, tm: TempoMap) -> Song:
    """Cópia da música com todos os tempos em segundos recalculados por ``tm``."""
    sec = tm.seconds
    notes = [dataclasses.replace(n, start_s=sec(n.start_tick), end_s=sec(n.end_tick),
                                 sustain_end_s=sec(n.sustain_end_tick)) for n in song.notes]
    events = [dataclasses.replace(e, seconds=sec(e.tick)) for e in song.channel_events]
    texts = [dataclasses.replace(t, seconds=sec(t.tick)) for t in song.texts]
    return dataclasses.replace(song, tempo_map=tm, notes=notes, channel_events=events, texts=texts,
                               length_s=sec(song.length_ticks))
