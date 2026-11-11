"""Leitor de Standard MIDI File (SMF) próprio.

Lê formatos 0, 1 e 2 sem interpretar a música: devolve, por faixa, a lista
bruta de eventos com tick absoluto. A interpretação (notas, andamento,
estado de canal) fica em ``song_builder``.

Decisões:
- Running status é suportado para eventos de canal.
- SysEx (F0/F7) e meta-eventos são preservados com seus bytes.
- Erros de estrutura levantam ``MidiFormatError`` com a posição no arquivo;
  irregularidades toleráveis (faixa sem End of Track, bytes sobrando) viram
  avisos em ``SmfFile.warnings`` em vez de dados descartados em silêncio.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


class MidiFormatError(ValueError):
    pass


# Tipos de evento
NOTE_OFF = 0x80
NOTE_ON = 0x90
POLY_AFTERTOUCH = 0xA0
CONTROL_CHANGE = 0xB0
PROGRAM_CHANGE = 0xC0
CHANNEL_AFTERTOUCH = 0xD0
PITCH_BEND = 0xE0

META_SEQ_NUMBER = 0x00
META_TEXT = 0x01
META_COPYRIGHT = 0x02
META_TRACK_NAME = 0x03
META_INSTRUMENT_NAME = 0x04
META_LYRIC = 0x05
META_MARKER = 0x06
META_CUE = 0x07
META_CHANNEL_PREFIX = 0x20
META_PORT = 0x21
META_END_OF_TRACK = 0x2F
META_TEMPO = 0x51
META_SMPTE = 0x54
META_TIME_SIGNATURE = 0x58
META_KEY_SIGNATURE = 0x59


@dataclass(slots=True)
class RawEvent:
    tick: int               # tick absoluto dentro da faixa
    kind: str               # 'channel' | 'meta' | 'sysex'
    status: int             # byte de status (canal incluído) ou tipo meta
    data: bytes             # dados do evento
    order: int              # posição original na faixa (desempate estável)

    @property
    def channel(self) -> int:
        return self.status & 0x0F

    @property
    def type(self) -> int:
        return self.status & 0xF0


@dataclass
class SmfTrack:
    index: int
    events: list[RawEvent] = field(default_factory=list)
    length_ticks: int = 0


@dataclass
class SmfFile:
    format: int
    division: int            # ticks por semínima (PPQ) se > 0
    smpte: tuple[int, int] | None  # (fps, ticks por quadro) se divisão SMPTE
    tracks: list[SmfTrack]
    warnings: list[str] = field(default_factory=list)
    path: str | None = None


_CHANNEL_DATA_LEN = {
    NOTE_OFF: 2, NOTE_ON: 2, POLY_AFTERTOUCH: 2, CONTROL_CHANGE: 2,
    PROGRAM_CHANGE: 1, CHANNEL_AFTERTOUCH: 1, PITCH_BEND: 2,
}


def _read_vlq(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= end:
            raise MidiFormatError(f"VLQ truncado na posição {pos}")
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos
    raise MidiFormatError(f"VLQ com mais de 4 bytes na posição {pos}")


def _parse_track(buf: bytes, start: int, end: int, index: int,
                 warnings: list[str]) -> SmfTrack:
    track = SmfTrack(index=index)
    pos = start
    tick = 0
    running: int | None = None
    order = 0
    saw_eot = False
    while pos < end:
        delta, pos = _read_vlq(buf, pos, end)
        tick += delta
        if pos >= end:
            raise MidiFormatError(f"Faixa {index}: evento truncado em {pos}")
        status = buf[pos]
        if status == 0xFF:  # meta
            if pos + 1 >= end:
                raise MidiFormatError(f"Faixa {index}: meta truncado em {pos}")
            mtype = buf[pos + 1]
            length, p = _read_vlq(buf, pos + 2, end)
            if p + length > end:
                raise MidiFormatError(f"Faixa {index}: meta 0x{mtype:02X} excede a faixa")
            track.events.append(RawEvent(tick, "meta", mtype, bytes(buf[p:p + length]), order))
            pos = p + length
            # Meta e SysEx cancelam running status (prática de mercado).
            running = None
            if mtype == META_END_OF_TRACK:
                saw_eot = True
                if pos < end:
                    warnings.append(f"Faixa {index}: {end - pos} byte(s) após End of Track ignorados")
                break
        elif status in (0xF0, 0xF7):
            length, p = _read_vlq(buf, pos + 1, end)
            if p + length > end:
                raise MidiFormatError(f"Faixa {index}: SysEx excede a faixa")
            track.events.append(RawEvent(tick, "sysex", status, bytes(buf[p:p + length]), order))
            pos = p + length
            running = None
        else:
            if status & 0x80:
                if status >= 0xF0:
                    raise MidiFormatError(
                        f"Faixa {index}: status de sistema 0x{status:02X} inválido em SMF (posição {pos})")
                running = status
                pos += 1
            elif running is None:
                raise MidiFormatError(
                    f"Faixa {index}: byte de dados 0x{status:02X} sem running status (posição {pos})")
            n = _CHANNEL_DATA_LEN[running & 0xF0]
            if pos + n > end:
                raise MidiFormatError(f"Faixa {index}: evento de canal truncado em {pos}")
            data = bytes(buf[pos:pos + n])
            if any(b & 0x80 for b in data):
                raise MidiFormatError(f"Faixa {index}: byte de dados inválido em {pos}")
            track.events.append(RawEvent(tick, "channel", running, data, order))
            pos += n
        order += 1
    if not saw_eot:
        warnings.append(f"Faixa {index}: sem meta End of Track")
    track.length_ticks = tick
    return track


def parse_smf(data: bytes, path: str | None = None) -> SmfFile:
    if len(data) < 14 or data[:4] != b"MThd":
        # Alguns arquivos vêm embrulhados em RIFF RMID.
        if data[:4] == b"RIFF" and data[8:12] == b"RMID":
            idx = data.find(b"MThd")
            if idx < 0:
                raise MidiFormatError("RMID sem bloco MThd")
            data = data[idx:]
        else:
            raise MidiFormatError("Não é um arquivo MIDI (cabeçalho MThd ausente)")
    hlen = struct.unpack(">I", data[4:8])[0]
    if hlen < 6:
        raise MidiFormatError("Cabeçalho MThd curto demais")
    fmt, ntrks, division = struct.unpack(">HHH", data[8:14])
    warnings: list[str] = []
    if fmt not in (0, 1, 2):
        raise MidiFormatError(f"Formato SMF {fmt} desconhecido")
    smpte = None
    if division & 0x8000:
        fps = 256 - (division >> 8)
        smpte = (fps, division & 0xFF)
        ppq = 0
    else:
        ppq = division
        if ppq == 0:
            raise MidiFormatError("Divisão de tempo zero")
    pos = 8 + hlen
    tracks: list[SmfTrack] = []
    while pos + 8 <= len(data) and len(tracks) < ntrks:
        cid = data[pos:pos + 4]
        clen = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        start = pos + 8
        end = start + clen
        if end > len(data):
            warnings.append(f"Bloco {cid!r} declara {clen} bytes mas o arquivo termina antes; lido até o fim")
            end = len(data)
        if cid == b"MTrk":
            tracks.append(_parse_track(data, start, end, len(tracks), warnings))
        else:
            warnings.append(f"Bloco desconhecido {cid!r} ignorado")
        pos = end
    if len(tracks) != ntrks:
        warnings.append(f"Cabeçalho declara {ntrks} faixa(s), encontradas {len(tracks)}")
    return SmfFile(format=fmt, division=ppq, smpte=smpte, tracks=tracks,
                   warnings=warnings, path=path)


def read_smf(path: str | Path) -> SmfFile:
    p = Path(path)
    return parse_smf(p.read_bytes(), path=str(p))
