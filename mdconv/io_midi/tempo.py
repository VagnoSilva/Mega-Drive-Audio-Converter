"""Mapa de andamento: conversão tick ↔ segundos com todas as mudanças de tempo."""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

DEFAULT_TEMPO_US = 500_000  # 120 BPM, valor padrão do SMF


@dataclass(slots=True)
class TempoChange:
    tick: int
    us_per_quarter: int
    seconds: float = 0.0   # instante (s) em que a mudança começa

    @property
    def bpm(self) -> float:
        return 60_000_000 / self.us_per_quarter


@dataclass
class TempoMap:
    ppq: int
    changes: list[TempoChange] = field(default_factory=list)
    smpte: tuple[int, int] | None = None

    @classmethod
    def build(cls, ppq: int, raw: list[tuple[int, int]],
              smpte: tuple[int, int] | None = None) -> "TempoMap":
        """``raw``: lista (tick, microssegundos por semínima), em qualquer ordem.

        Várias mudanças no mesmo tick: vale a última (ordem do arquivo).
        """
        dedup: dict[int, int] = {}
        for tick, us in raw:
            dedup[tick] = us
        if 0 not in dedup:
            dedup[0] = DEFAULT_TEMPO_US
        ticks = sorted(dedup)
        changes: list[TempoChange] = []
        secs = 0.0
        for i, t in enumerate(ticks):
            if i:
                prev = changes[-1]
                secs += (t - prev.tick) * prev.us_per_quarter / 1e6 / ppq
            changes.append(TempoChange(t, dedup[t], secs))
        return cls(ppq=ppq, changes=changes, smpte=smpte)

    def __post_init__(self) -> None:
        self._ticks = [c.tick for c in self.changes]

    def _at(self, tick: int) -> TempoChange:
        i = bisect.bisect_right(self._ticks, tick) - 1
        return self.changes[max(i, 0)]

    def seconds(self, tick: int) -> float:
        if self.smpte is not None:
            fps, tpf = self.smpte
            real_fps = 29.97 if fps == 29 else float(fps)
            return tick / (real_fps * tpf)
        c = self._at(tick)
        return c.seconds + (tick - c.tick) * c.us_per_quarter / 1e6 / self.ppq

    def bpm_at(self, tick: int) -> float:
        return self._at(tick).bpm

    @property
    def has_tempo_changes(self) -> bool:
        return len({c.us_per_quarter for c in self.changes}) > 1
