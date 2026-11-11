"""Transforma eventos SMF brutos em ``Song`` (notas pareadas, CCs, andamento).

Regras de interpretação (documentadas porque afetam o resultado musical):

* Estado de canal (banco, programa, pedal) é compartilhado entre faixas, como
  num sintetizador MIDI real. Eventos de todas as faixas são intercalados por
  tick; no mesmo tick, eventos não-nota vêm antes de note-off, que vêm antes
  de note-on — assim um Program Change e a nota no mesmo tick se combinam.
* Note-on com velocity 0 é note-off.
* Pareamento por (faixa, canal, tecla), em ordem FIFO: o primeiro note-on
  aberto é fechado pelo primeiro note-off. Note-ons repetidos na mesma tecla
  sem note-off entre eles são contados como "sobreposições de mesma nota" e
  mantidos (nenhuma nota é descartada).
* Notas sem note-off são fechadas no fim da faixa e marcadas.
* Note-off sem note-on correspondente é contado em aviso e ignorado
  (não há nota para encerrar).
* Pedal de sustain: valor >= 64 = pressionado. Nota solta com o pedal
  pressionado ganha ``sustain_end`` no instante em que o pedal sobe.
"""
from __future__ import annotations

from collections import defaultdict, deque

from mdconv.io_midi import smf as S
from mdconv.io_midi.tempo import TempoMap
from mdconv.model.song import ChannelEvent, MetaText, Note, Song, TrackInfo

_TEXT_KINDS = {
    S.META_TEXT: "text", S.META_MARKER: "marker", S.META_CUE: "cue", S.META_LYRIC: "lyric",
}


def _decode_text(b: bytes) -> str:
    for enc in ("utf-8", "shift_jis", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", errors="replace")


def _priority(ev: S.RawEvent) -> int:
    if ev.kind != "channel":
        return 0
    t = ev.type
    if t == S.NOTE_OFF or (t == S.NOTE_ON and ev.data[1] == 0):
        return 1
    if t == S.NOTE_ON:
        return 2
    return 0


def build_song(smf: S.SmfFile) -> Song:
    warnings = list(smf.warnings)
    if smf.format == 2:
        warnings.append("SMF formato 2 (faixas independentes): o andamento foi unificado entre faixas")
    if smf.smpte is not None:
        warnings.append(f"Divisão SMPTE {smf.smpte}: andamento em BPM não se aplica")

    tempo_raw: list[tuple[int, int]] = []
    tracks: list[TrackInfo] = []
    for tr in smf.tracks:
        info = TrackInfo(index=tr.index, length_ticks=tr.length_ticks, event_count=len(tr.events))
        for ev in tr.events:
            if ev.kind == "meta":
                if ev.status == S.META_TEMPO and len(ev.data) == 3:
                    tempo_raw.append((ev.tick, int.from_bytes(ev.data, "big")))
                elif ev.status == S.META_TRACK_NAME and not info.name:
                    info.name = _decode_text(ev.data).strip("\x00 ")
                elif ev.status == S.META_INSTRUMENT_NAME:
                    info.instrument_names.append(_decode_text(ev.data).strip("\x00 "))
            elif ev.kind == "channel":
                info.channels.add(ev.channel)
        tracks.append(info)

    tmap = TempoMap.build(smf.division or 1, tempo_raw, smf.smpte)
    sec = tmap.seconds

    merged = sorted(
        ((ev.tick, _priority(ev), tr.index, ev.order, ev) for tr in smf.tracks for ev in tr.events),
        key=lambda x: x[:4],
    )

    bank_msb = [0] * 16
    bank_lsb = [0] * 16
    program = [0] * 16
    pedal_down = [False] * 16
    param_kind: list[str | None] = [None] * 16      # 'rpn' | 'nrpn' selecionado por canal
    param_sel = [[127, 127] for _ in range(16)]
    held_by_pedal: list[list[int]] = [[] for _ in range(16)]  # índices em ``notes``
    open_notes: dict[tuple[int, int, int], deque[int]] = defaultdict(deque)

    notes: list[Note] = []
    cevents: list[ChannelEvent] = []
    texts: list[MetaText] = []
    tsigs: list[tuple[int, int, int]] = []
    sysex = 0
    orphan_offs = 0
    same_key_overlaps = 0

    def release(idx: int, tick: int) -> None:
        n = notes[idx]
        n.end_tick = tick
        n.end_s = sec(tick)
        ch = n.channel
        if pedal_down[ch]:
            held_by_pedal[ch].append(idx)
        else:
            n.sustain_end_tick = tick
            n.sustain_end_s = n.end_s

    for tick, _p, tidx, _o, ev in merged:
        if ev.kind == "sysex":
            sysex += 1
            continue
        if ev.kind == "meta":
            if ev.status in _TEXT_KINDS:
                texts.append(MetaText(tidx, tick, sec(tick), _TEXT_KINDS[ev.status], _decode_text(ev.data)))
            elif ev.status == S.META_TIME_SIGNATURE and len(ev.data) >= 2:
                tsigs.append((tick, ev.data[0], 2 ** ev.data[1]))
            continue

        ch = ev.channel
        t = ev.type
        d = ev.data
        if t == S.NOTE_ON and d[1] > 0:
            key = (tidx, ch, d[0])
            if open_notes[key]:
                same_key_overlaps += 1
            notes.append(Note(
                track=tidx, channel=ch, key=d[0], velocity=d[1],
                start_tick=tick, end_tick=tick, start_s=sec(tick), end_s=sec(tick),
                sustain_end_tick=tick, sustain_end_s=sec(tick),
                bank_msb=bank_msb[ch], bank_lsb=bank_lsb[ch], program=program[ch],
            ))
            open_notes[key].append(len(notes) - 1)
        elif t == S.NOTE_OFF or t == S.NOTE_ON:
            key = (tidx, ch, d[0])
            if open_notes[key]:
                idx = open_notes[key].popleft()
                notes[idx].off_velocity = d[1] if t == S.NOTE_OFF else 64
                release(idx, tick)
            else:
                orphan_offs += 1
        elif t == S.CONTROL_CHANGE:
            num, val = d
            cevents.append(ChannelEvent(tidx, ch, tick, sec(tick), "cc", num, val))
            if num in (99, 98):            # NRPN MSB/LSB
                param_kind[ch] = "nrpn"
                param_sel[ch][0 if num == 99 else 1] = val
            elif num in (101, 100):        # RPN MSB/LSB
                param_kind[ch] = "rpn"
                param_sel[ch][0 if num == 101 else 1] = val
            elif num == 6 and param_kind[ch]:
                msb, lsb = param_sel[ch]
                if not (param_kind[ch] == "rpn" and (msb, lsb) == (127, 127)):  # RPN nulo
                    cevents.append(ChannelEvent(tidx, ch, tick, sec(tick), param_kind[ch],
                                                msb * 128 + lsb, val))
            if num == 0:
                bank_msb[ch] = val
            elif num == 32:
                bank_lsb[ch] = val
            elif num == 64:
                down = val >= 64
                if pedal_down[ch] and not down:
                    for idx in held_by_pedal[ch]:
                        notes[idx].sustain_end_tick = tick
                        notes[idx].sustain_end_s = sec(tick)
                    held_by_pedal[ch].clear()
                pedal_down[ch] = down
            elif num in (120, 123):  # All Sound Off / All Notes Off
                for (otr, och, _k), q in open_notes.items():
                    if och == ch and otr == tidx:
                        while q:
                            release(q.popleft(), tick)
        elif t == S.PROGRAM_CHANGE:
            program[ch] = d[0]
            cevents.append(ChannelEvent(tidx, ch, tick, sec(tick), "program", d[0], d[0]))
        elif t == S.PITCH_BEND:
            val = (d[0] | (d[1] << 7)) - 8192
            cevents.append(ChannelEvent(tidx, ch, tick, sec(tick), "pitch_bend", 0, val))
        elif t == S.CHANNEL_AFTERTOUCH:
            cevents.append(ChannelEvent(tidx, ch, tick, sec(tick), "channel_pressure", 0, d[0]))
        elif t == S.POLY_AFTERTOUCH:
            cevents.append(ChannelEvent(tidx, ch, tick, sec(tick), "poly_pressure", d[0], d[1]))

    # Fecha notas pendentes no fim de cada faixa.
    unclosed = 0
    for (tidx, _ch, _k), q in open_notes.items():
        end = smf.tracks[tidx].length_ticks
        while q:
            idx = q.popleft()
            notes[idx].closed_at_track_end = True
            release(idx, max(end, notes[idx].start_tick))
            unclosed += 1
    song_end = max([tr.length_ticks for tr in smf.tracks] + [0])
    for ch in range(16):
        for idx in held_by_pedal[ch]:
            notes[idx].sustain_end_tick = max(song_end, notes[idx].end_tick)
            notes[idx].sustain_end_s = sec(notes[idx].sustain_end_tick)
        held_by_pedal[ch].clear()

    if unclosed:
        warnings.append(f"{unclosed} nota(s) sem note-off fechadas no fim da faixa")
    if orphan_offs:
        warnings.append(f"{orphan_offs} note-off sem note-on correspondente")
    if same_key_overlaps:
        warnings.append(f"{same_key_overlaps} sobreposição(ões) de mesma nota no mesmo canal (mantidas)")

    notes.sort(key=lambda n: (n.start_tick, n.track, n.channel, n.key))
    last = max([song_end] + [n.sustain_end_tick for n in notes])
    return Song(
        source_path=smf.path, smf_format=smf.format, ppq=smf.division, tempo_map=tmap,
        tracks=tracks, notes=notes, channel_events=cevents, texts=texts,
        time_signatures=tsigs, sysex_count=sysex, length_ticks=last, length_s=sec(last),
        warnings=warnings,
    )


def load_song(path) -> Song:
    return build_song(S.read_smf(path))
