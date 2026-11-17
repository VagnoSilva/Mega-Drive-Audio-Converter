"""Silêncio do início: a conversão começa na primeira nota."""
from mdconv.app.project import ProjectSettings
from mdconv.model.trim import trim_leading_silence


def test_trim_shifts_everything():
    from mdconv.io_midi.song_builder import load_song
    # MIDI sintético com 1 compasso mudo (como o GBA Mus Ripper)
    import struct
    def vlq(v):
        out = [v & 0x7F]
        v >>= 7
        while v:
            out.insert(0, 0x80 | (v & 0x7F)); v >>= 7
        return bytes(out)
    ev = (vlq(0) + b"\xff\x51\x03\x07\xa1\x20" + vlq(0) + b"\xc0\x05" + vlq(48) + b"\xff\x06\x09loopStart"
          + vlq(48) + b"\x90\x3c\x64" + vlq(24) + b"\x80\x3c\x00" + vlq(24) + b"\xff\x06\x07loopEnd" + vlq(0) + b"\xff\x2f\x00")
    data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 24) + b"MTrk" + struct.pack(">I", len(ev)) + ev
    import tempfile, os
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.mid")
    open(p, "wb").write(data)
    s = load_song(p)
    assert s.notes[0].start_tick == 96
    t, off, secs = trim_leading_silence(s)
    assert off == 96 and abs(secs - 2.0) < 1e-6           # 4 tempos a 120 BPM
    assert t.notes[0].start_tick == 0 and t.notes[0].start_s == 0
    assert [x.tick for x in t.texts if x.text == "loopStart"] == [0]
    assert [x.tick for x in t.texts if x.text == "loopEnd"] == [48]
    assert any(e.kind == "program" and e.tick == 0 for e in t.channel_events)


def test_old_projects_keep_silence():
    s = ProjectSettings.from_json('{"midi": "a.mid"}')
    assert s.trim_start is False
    assert ProjectSettings().trim_start is True
