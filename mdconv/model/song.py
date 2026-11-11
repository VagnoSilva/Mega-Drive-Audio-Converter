"""Modelo de dados da música, independente de formato de origem."""
from __future__ import annotations

from dataclasses import dataclass, field

from mdconv.io_midi.tempo import TempoMap


@dataclass(slots=True)
class Note:
    track: int
    channel: int
    key: int
    velocity: int
    start_tick: int
    end_tick: int
    start_s: float
    end_s: float
    # Fim efetivo considerando o pedal de sustain (CC64); igual a end_* sem pedal.
    sustain_end_tick: int
    sustain_end_s: float
    bank_msb: int
    bank_lsb: int
    program: int
    off_velocity: int = 64
    # Marcada quando a nota não tinha note-off e foi fechada no fim da faixa.
    closed_at_track_end: bool = False

    @property
    def bank(self) -> int:
        """Banco combinado (MSB*128 + LSB), como no SF2 quando LSB é usado."""
        return self.bank_msb * 128 + self.bank_lsb

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(slots=True)
class ChannelEvent:
    """Evento de canal que não é nota: CC, program change, pitch bend, aftertouch."""
    track: int
    channel: int
    tick: int
    seconds: float
    kind: str          # 'cc' | 'program' | 'pitch_bend' | 'channel_pressure' | 'poly_pressure'
                       # | 'rpn' | 'nrpn' (valor do Data Entry MSB para o parâmetro selecionado)
    number: int        # nº do CC, programa, tecla (poly), parâmetro (MSB*128+LSB) ou 0
    value: int         # valor; pitch bend em -8192..8191


@dataclass(slots=True)
class MetaText:
    track: int
    tick: int
    seconds: float
    kind: str          # 'marker' | 'text' | 'cue' | 'lyric'
    text: str


@dataclass
class TrackInfo:
    index: int
    name: str = ""
    instrument_names: list[str] = field(default_factory=list)
    channels: set[int] = field(default_factory=set)
    length_ticks: int = 0
    event_count: int = 0


@dataclass
class Song:
    source_path: str | None
    smf_format: int
    ppq: int
    tempo_map: TempoMap
    tracks: list[TrackInfo]
    notes: list[Note]
    channel_events: list[ChannelEvent]
    texts: list[MetaText]
    time_signatures: list[tuple[int, int, int]]  # (tick, numerador, denominador)
    sysex_count: int
    length_ticks: int
    length_s: float
    warnings: list[str] = field(default_factory=list)

    def notes_of(self, track: int | None = None, channel: int | None = None) -> list[Note]:
        return [n for n in self.notes
                if (track is None or n.track == track)
                and (channel is None or n.channel == channel)]
