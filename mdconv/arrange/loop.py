"""Ponto de loop da música.

Prioridade:
1. marcadores ``loopStart``/``loopEnd`` (o ripper os grava quando a trilha do m4a tem GOTO);
2. CC111 (convenção RPG Maker) ou CC116/117 (EMIDI);
3. *estimado*: sem marcador, mas a música termina "cortada" — as últimas notas acabam
   exatamente no fim do arquivo, sem acorde final sustentado. É o que acontece quando o
   jogo volta para o começo e o ripper não registrou o salto. Nesse caso o loop vai do
   início da primeira nota até o fim, e o resultado é marcado como estimado para o
   usuário ouvir a emenda e, se precisar, escolher outro compasso;
4. sem loop: música com final de verdade (toca uma vez).

Nada aqui é inventado em silêncio: o resultado diz de onde veio e quanta confiança há.
"""
from __future__ import annotations

from dataclasses import dataclass

from mdconv.analysis.midi_stats import find_loops
from mdconv.model.song import Song

MODES = ("auto", "markers", "none")


@dataclass
class LoopPoint:
    start_tick: int
    end_tick: int
    start_s: float
    end_s: float
    source: str            # 'marcador' | 'cc111' | 'emidi' | 'estimado' | 'manual'
    detail: str

    @property
    def estimated(self) -> bool:
        return self.source == "estimado"

    def text(self) -> str:
        return (f"loop {self.start_s:.2f} s → {self.end_s:.2f} s ({self.source}: {self.detail})")


def bar_ticks(song: Song) -> int:
    if song.time_signatures:
        _, num, den = song.time_signatures[0]
        return song.ppq * 4 * num // den
    return song.ppq * 4


def ends_abruptly(song: Song, tail_ticks: int | None = None) -> tuple[bool, str]:
    """A música para no meio da frase? (notas curtas terminando exatamente no fim)"""
    if not song.notes:
        return False, "sem notas"
    end = max(n.end_tick for n in song.notes)
    tail = tail_ticks or song.ppq // 2
    last = [n for n in song.notes if n.end_tick >= end - 1]
    # final de verdade: alguma nota final longa (≥ 1 tempo) sustentada até o fim
    longest = max(n.end_tick - n.start_tick for n in last)
    recent = [n for n in song.notes if n.start_tick >= end - tail]
    if longest >= song.ppq and not recent:
        return False, f"termina com nota longa ({longest} ticks) — final de verdade"
    if recent:
        return True, f"{len(recent)} nota(s) começam no último meio tempo e são cortadas no fim"
    return False, "termina sem notas cortadas"


def resolve_loop(song: Song, mode: str = "auto", start_tick: int | None = None,
                 end_tick: int | None = None) -> LoopPoint | None:
    if mode not in MODES:
        raise ValueError(f"modo de loop desconhecido: {mode}")
    sec = song.tempo_map.seconds
    song_end = max([n.end_tick for n in song.notes] + [song.length_ticks])
    if start_tick is not None:
        e = end_tick if end_tick is not None else song_end
        if not 0 <= start_tick < e:
            raise ValueError("início do loop precisa ser antes do fim")
        return LoopPoint(start_tick, e, sec(start_tick), sec(e), "manual", "escolhido pelo usuário")
    if mode == "none":
        return None
    for c in find_loops(song):
        src = {"marker": "marcador", "cc111": "cc111", "emidi": "emidi"}.get(c.source)
        if src is None:
            continue
        e = c.end_tick if c.end_tick is not None else song_end
        if e > c.start_tick:
            return LoopPoint(c.start_tick, e, sec(c.start_tick), sec(e), src, c.detail)
    if mode == "markers":
        return None
    abrupt, why = ends_abruptly(song)
    if not abrupt:
        return None
    s = min(n.start_tick for n in song.notes)
    return LoopPoint(s, song_end, sec(s), sec(song_end), "estimado",
                     f"sem marcador; {why} — volta para a primeira nota (confira a emenda)")
