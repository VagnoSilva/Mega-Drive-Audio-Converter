import pytest

from mdconv.analysis.midi_stats import analyze_song, max_polyphony
from mdconv.io_midi.smf import MidiFormatError, parse_smf
from mdconv.io_midi.song_builder import build_song
from tests.fixtures.builders import (bend, cc, meta_text, midi_file, off, on, pc, tempo,
                                     track_bytes)


def song_of(tracks, ppq=480, **kw):
    return build_song(parse_smf(midi_file(tracks, ppq), path="t.mid"))


def test_tempo_changes_map_ticks_to_seconds():
    t0 = track_bytes([(0, tempo(500_000)), (960, tempo(1_000_000))])  # 120 → 60 BPM
    t1 = track_bytes([(0, on(0, 60, 100)), (480, off(0, 60)),
                      (1440, on(0, 62, 100)), (1920, off(0, 62))])
    s = song_of([t0, t1])
    n1, n2 = s.notes
    assert n1.start_s == pytest.approx(0.0) and n1.end_s == pytest.approx(0.5)
    assert n2.start_s == pytest.approx(2.0) and n2.end_s == pytest.approx(3.0)
    st = analyze_song(s)
    assert [round(b, 3) for _, _, b in st.tempo_changes] == [120.0, 60.0]


def test_default_tempo_is_120_bpm():
    s = song_of([track_bytes([(0, on(0, 60, 1)), (480, off(0, 60))])])
    assert s.notes[0].end_s == pytest.approx(0.5)


def test_running_status_equals_explicit_status():
    evs = [(0, on(0, 60, 90)), (0, on(0, 64, 90)), (240, on(0, 60, 0)), (240, on(0, 64, 0)),
           (300, cc(0, 7, 100)), (300, cc(0, 10, 20))]
    a = song_of([track_bytes(evs, running_status=False)])
    b = song_of([track_bytes(evs, running_status=True)])
    sig = lambda s: [(n.key, n.start_tick, n.end_tick, n.velocity) for n in s.notes]
    assert sig(a) == sig(b) and len(a.notes) == 2
    assert [(e.number, e.value) for e in a.channel_events] == [(e.number, e.value) for e in b.channel_events]


def test_note_on_velocity_zero_is_note_off():
    s = song_of([track_bytes([(0, on(0, 60, 100)), (100, on(0, 60, 0))])])
    assert s.notes[0].end_tick == 100


def test_same_key_overlap_kept_and_paired_fifo():
    s = song_of([track_bytes([(0, on(0, 60, 100)), (100, on(0, 60, 80)),
                              (200, off(0, 60)), (300, off(0, 60))])])
    assert [(n.start_tick, n.end_tick, n.velocity) for n in s.notes] == [(0, 200, 100), (100, 300, 80)]
    assert any("sobreposição" in w for w in s.warnings)


def test_unclosed_note_closed_at_track_end_and_warned():
    s = song_of([track_bytes([(0, on(0, 60, 100)), (960, cc(0, 7, 100))])])
    assert s.notes[0].end_tick == 960 and s.notes[0].closed_at_track_end
    assert any("sem note-off" in w for w in s.warnings)


def test_orphan_note_off_reported_not_crashing():
    s = song_of([track_bytes([(0, off(0, 61)), (0, on(0, 60, 100)), (10, off(0, 60))])])
    assert len(s.notes) == 1
    assert any("sem note-on" in w for w in s.warnings)


def test_program_and_bank_from_other_track_same_tick():
    t0 = track_bytes([(0, cc(3, 0, 1)), (0, cc(3, 32, 2)), (0, pc(3, 41))])
    t1 = track_bytes([(0, on(3, 50, 100)), (10, off(3, 50))])
    n = song_of([t0, t1]).notes[0]
    assert (n.bank_msb, n.bank_lsb, n.program) == (1, 2, 41)
    assert n.bank == 1 * 128 + 2


def test_program_change_mid_song_applies_to_following_notes():
    s = song_of([track_bytes([(0, pc(0, 5)), (0, on(0, 60, 9)), (10, off(0, 60)),
                              (20, pc(0, 7)), (20, on(0, 60, 9)), (30, off(0, 60))])])
    assert [n.program for n in s.notes] == [5, 7]


def test_sustain_pedal_extends_sounding_end():
    s = song_of([track_bytes([(0, cc(0, 64, 127)), (0, on(0, 60, 100)), (100, off(0, 60)),
                              (400, cc(0, 64, 0))])])
    n = s.notes[0]
    assert n.end_tick == 100 and n.sustain_end_tick == 400


def test_polyphony_counts_chord_not_sequence():
    evs = []
    for k in (60, 64, 67):
        evs += [(0, on(0, k, 100)), (480, off(0, k))]
    evs += [(480, on(0, 72, 100)), (960, off(0, 72))]  # começa quando o acorde termina
    s = song_of([track_bytes(evs)])
    assert max_polyphony(s.notes)[0] == 3
    st = analyze_song(s)
    assert st.max_polyphony == 3 and st.parts[0].note_count == 4


def test_pitch_bend_and_cc_stats():
    s = song_of([track_bytes([(0, bend(1, -8192)), (10, bend(1, 8191)), (0, cc(1, 7, 90)),
                              (0, cc(1, 21, 30)), (0, on(1, 40, 100)), (20, off(1, 40))])])
    p = analyze_song(s).parts[0]
    assert p.pitch_bend_range == (-8192, 8191)
    assert p.volume_values == [90]
    assert 21 in analyze_song(s).unknown_ccs


def test_loop_markers_detected():
    t0 = track_bytes([(0, meta_text(0x06, "loopStart")), (1920, meta_text(0x06, "loopEnd"))])
    t1 = track_bytes([(0, on(0, 60, 1)), (2000, off(0, 60))])
    loops = analyze_song(song_of([t0, t1])).loops
    m = [lp for lp in loops if lp.source == "marker"]
    assert m and m[0].start_tick == 0 and m[0].end_tick == 1920


def test_repetition_detected_by_bars():
    bar = 1920  # 4/4 a 480 PPQ
    evs = []
    phrase = [60, 62, 64, 65, 67, 69, 71, 72]
    for rep in range(2):
        for b in range(4):
            base = (rep * 4 + b) * bar
            k = phrase[b * 2]
            evs += [(base, on(0, k, 100)), (base + 240, off(0, k))]
    loops = analyze_song(song_of([track_bytes(evs)])).loops
    r = [lp for lp in loops if lp.source == "repetição"]
    assert r and r[0].start_tick == 0 and r[0].end_tick == 4 * bar


def test_rejects_data_byte_without_status():
    bad = midi_file([b"MTrk" + (4).to_bytes(4, "big") + b"\x00\x3C\x40\x00"])
    with pytest.raises(MidiFormatError):
        parse_smf(bad)


def test_rejects_non_midi():
    with pytest.raises(MidiFormatError):
        parse_smf(b"RIFF....WAVE")


def test_missing_end_of_track_is_warning():
    s = parse_smf(midi_file([track_bytes([(0, on(0, 60, 1))], end_of_track=False)]))
    assert any("End of Track" in w for w in s.warnings)


def test_rpn_nrpn_decoded_like_gba_mus_ripper():
    # Padrão gravado pelo GBA Mus Ripper: NRPN 1:8 (taxa de vibrato) e RPN 0:1 (afinação fina).
    s = song_of([track_bytes([(0, cc(4, 99, 1)), (0, cc(4, 98, 8)), (0, cc(4, 6, 25)),
                              (1, cc(4, 101, 0)), (1, cc(4, 100, 1)), (1, cc(4, 6, 69)),
                              (2, cc(4, 101, 0)), (2, cc(4, 100, 0)), (2, cc(4, 6, 12)),
                              (3, on(4, 60, 100)), (9, off(4, 60))])])
    kinds = [(e.kind, e.number, e.value) for e in s.channel_events if e.kind in ("rpn", "nrpn")]
    assert kinds == [("nrpn", 136, 25), ("rpn", 1, 69), ("rpn", 0, 12)]
    p = analyze_song(s).parts[0]
    assert p.params["Vibrato Rate (GS/XG)"] == [25]
    assert p.bend_range_semitones == 12
