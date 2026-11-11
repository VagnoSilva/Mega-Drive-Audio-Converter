"""Testes com arquivos reais do jogo (não versionados no repositório).

Defina MDCONV_REAL_DIR apontando para a pasta com song001.mid + db.sf2 e
soundbank_000.sf2 + song*.mid. Sem a variável, os testes são pulados.
"""
import os
from pathlib import Path

import pytest

from mdconv.analysis.instrument_match import match_instruments
from mdconv.analysis.midi_stats import analyze_song
from mdconv.io_midi.song_builder import load_song
from mdconv.io_sf2.sf2 import read_sf2

REAL = os.environ.get("MDCONV_REAL_DIR")
pytestmark = pytest.mark.skipif(not REAL, reason="MDCONV_REAL_DIR não definido")


def _find(pattern):
    hits = sorted(Path(REAL).glob(pattern))
    if not hits:
        pytest.skip(f"{pattern} não encontrado")
    return hits[0]


def test_song001_db_reference():
    song = load_song(_find("*song001.mid"))
    st = analyze_song(song)
    assert (st.part_count, st.note_count, st.max_polyphony) == (14, 1855, 14)
    assert [round(b) for _, _, b in st.tempo_changes] == [140, 146]
    loop = [lp for lp in st.loops if lp.source == "marker"][0]
    assert loop.start_s == pytest.approx(10.286, abs=1e-3)
    m = match_instruments(song, read_sf2(_find("*db.sf2")))
    assert all(x.exact for x in m)
    assert {x.program for x in m if x.kind_hint == "kit (keysplit)"} == {127}
    # Canal 12 = eco do canal 5: mesmas notas 12 ticks depois.
    c5 = {(n.start_tick, n.key) for n in song.notes if n.channel == 4}
    c12 = {(n.start_tick - 12, n.key) for n in song.notes if n.channel == 11}
    assert c5 == c12


def test_soundbank_000_songs_all_parse():
    sf = read_sf2(_find("*soundbank_000.sf2"))
    for f in sorted(Path(REAL).glob("*song0[0-2]*.mid")):
        if "song001" in f.name:
            continue
        song = load_song(f)
        m = match_instruments(song, sf)
        missing = [x for x in m if x.preset_name is None]
        if "song005" in f.name:
            assert [(x.channel, x.program) for x in missing] == [(12, 0)]
        else:
            assert not missing, f.name


def test_song001_full_conversion_accounts_for_every_note(tmp_path):
    from mdconv.convert import convert
    song = load_song(_find("*song001.mid"))
    c = convert(song, read_sf2(_find("*db.sf2")), None, balance="manual")
    s = c.stream.stats
    accounted = (s["fm"] + s["psg_planned"] + s["psg_overflow"] + s["omitted"]
                 + s["pcm"] + s["pcm_stolen"] + s["pcm_missing"])
    assert accounted == len(song.notes)
    # baixo e melodia nunca perdem notas
    lost = [x for x in c.stream.changes if x.split()[1] in ("C04", "C01")]
    assert not lost
