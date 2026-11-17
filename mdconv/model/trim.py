"""Tira o silêncio antes da primeira nota da música.

Os MIDIs extraídos de jogos costumam começar com um trecho mudo (o GBA Mus Ripper põe um
compasso inteiro antes da primeira nota). No jogo isso só atrasa a música, então a conversão
começa na primeira nota: tudo é deslocado para trás; eventos de antes dela (andamento, programa,
volume, marcadores) passam a valer no tick 0, na mesma ordem.
"""
from __future__ import annotations

import dataclasses

from mdconv.io_midi.tempo import TempoMap
from mdconv.model.song import Song


def first_note_tick(song: Song) -> int:
    return min((n.start_tick for n in song.notes), default=0)


def trim_leading_silence(song: Song) -> tuple[Song, int, float]:
    """Devolve (música deslocada, ticks removidos, segundos removidos). Sem silêncio: a mesma."""
    off = first_note_tick(song)
    if off <= 0:
        return song, 0, 0.0
    secs = song.tempo_map.seconds(off)
    old = song.tempo_map
    raw = [(max(0, c.tick - off), c.us_per_quarter) for c in sorted(old.changes, key=lambda c: c.tick)]
    tm = TempoMap.build(old.ppq, raw, old.smpte)

    def sh(t):
        return max(0, t - off)

    notes = []
    for n in song.notes:
        st, et, se = sh(n.start_tick), sh(n.end_tick), sh(n.sustain_end_tick)
        notes.append(dataclasses.replace(n, start_tick=st, end_tick=et, sustain_end_tick=se,
                                         start_s=tm.seconds(st), end_s=tm.seconds(et),
                                         sustain_end_s=tm.seconds(se)))
    events = [dataclasses.replace(e, tick=sh(e.tick), seconds=tm.seconds(sh(e.tick)))
              for e in song.channel_events]
    texts = [dataclasses.replace(t, tick=sh(t.tick), seconds=tm.seconds(sh(t.tick))) for t in song.texts]
    sigs = []
    for tick, num, den in sorted(song.time_signatures):
        t = sh(tick)
        sigs = [s for s in sigs if s[0] != t] + [(t, num, den)]
    length = sh(song.length_ticks)
    tracks = [dataclasses.replace(tr, length_ticks=sh(tr.length_ticks)) for tr in song.tracks]
    new = dataclasses.replace(song, tempo_map=tm, notes=notes, channel_events=events, texts=texts,
                              time_signatures=sigs, length_ticks=length, length_s=tm.seconds(length),
                              tracks=tracks, warnings=list(song.warnings))
    return new, off, secs
