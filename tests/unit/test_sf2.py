import numpy as np
import pytest

from mdconv.analysis.instrument_match import match_instruments
from mdconv.io_midi.smf import parse_smf
from mdconv.io_midi.song_builder import build_song
from mdconv.io_sf2.resolve import find_preset, resolve_voices
from mdconv.io_sf2.sf2 import GEN_ID as G
from mdconv.io_sf2.sf2 import Sf2FormatError, parse_sf2
from tests.fixtures.builders import gen, midi_file, off, on, pc, sf2_file, track_bytes


def tone(n=1000, f=0.05):
    return (np.sin(np.arange(n) * f) * 12000).astype(np.int16)


@pytest.fixture
def sf():
    samples = [
        {"name": "Grave", "data": tone(800), "rate": 13379, "root": 48, "loop": (100, 700)},
        {"name": "Agudo", "data": tone(600, 0.1), "rate": 13379, "root": 72, "loop": (50, 550)},
        {"name": "Forte", "data": tone(400, 0.2), "rate": 22050, "root": 60},
        {"name": "Bumbo", "data": tone(300, 0.01), "rate": 10512, "root": 36},
        {"name": "Caixa", "data": tone(300, 0.3), "rate": 10512, "root": 38},
    ]
    instruments = [
        {"name": "Piano", "global": [gen(G["releaseVolEnv"], 0), gen(G["sampleModes"], 1)],
         "zones": [[gen(G["keyRange"], (0, 59)), gen(G["sampleID"], 0)],
                   [gen(G["keyRange"], (60, 100)), gen(G["velRange"], (0, 99)), gen(G["sampleID"], 1)],
                   [gen(G["keyRange"], (60, 100)), gen(G["velRange"], (100, 127)),
                    gen(G["initialAttenuation"], 30), gen(G["sampleID"], 2)]]},
        {"name": "Kit", "global": [gen(G["scaleTuning"], 0)], "zones": [[gen(G["keyRange"], (36, 36)), gen(G["sampleID"], 3)],
                                  [gen(G["keyRange"], (38, 38)), gen(G["sampleID"], 4)],
                                  [gen(G["keyRange"], (40, 40)), gen(G["overridingRootKey"], 40),
                                   gen(G["sampleID"], 4)]]},
    ]
    presets = [
        # Índice 0 tem programa 5: Program Change ≠ índice de preset.
        {"name": "Piano GBA", "bank": 0, "program": 5,
         "global": [gen(G["initialAttenuation"], 20)],
         "zones": [[gen(G["coarseTune"], 1), gen(G["instrument"], 0)]]},
        {"name": "Bateria", "bank": 128, "program": 0, "zones": [[gen(G["instrument"], 1)]]},
        {"name": "Bateria m4a", "bank": 0, "program": 127, "zones": [[gen(G["instrument"], 1)]]},
    ]
    return parse_sf2(sf2_file(samples, instruments, presets), path="t.sf2")


def test_structure_and_info(sf):
    assert [p.name for p in sf.presets] == ["Piano GBA", "Bateria", "Bateria m4a"]
    assert len(sf.instruments) == 2 and len(sf.samples) == 5
    assert sf.info["ifil"] == "2.01" and sf.info["INAM"] == "Teste"
    assert sf.instruments[0].global_zone is not None and len(sf.instruments[0].zones) == 3
    assert not sf.warnings


def test_sample_data_and_loop_read_back(sf):
    s = sf.samples[0]
    assert s.sample_rate == 13379 and s.original_pitch == 48
    np.testing.assert_array_equal(sf.sample_data(s), tone(800))
    assert (s.loop_start - s.start, s.loop_end - s.start) == (100, 700)


def test_preset_found_by_bank_program_not_index(sf):
    look = find_preset(sf, 0, 5)
    assert look.exact and look.preset.name == "Piano GBA"
    miss = find_preset(sf, 0, 0)
    assert miss.preset is None and "sem preset" in miss.reason


def test_bank_msb_lsb_fallback(sf):
    look = find_preset(sf, 0 * 128 + 0, 127)
    assert look.preset.name == "Bateria m4a"
    look = find_preset(sf, 128 * 128, 0)   # MSB=128 não existe em MIDI, mas testa o caminho LSB/MSB
    assert look.preset is not None and not look.exact


def test_drum_channel_uses_bank_128(sf):
    look = find_preset(sf, 0, 0, drum_channel=True)
    assert look.preset.name == "Bateria"


@pytest.mark.parametrize("key,vel,sample", [(40, 64, "Grave"), (64, 64, "Agudo"), (64, 110, "Forte")])
def test_key_and_velocity_splits(sf, key, vel, sample):
    v = resolve_voices(sf, sf.presets[0], key, vel)
    assert [x.sample.name for x in v] == [sample]


def test_global_inheritance_and_preset_additive(sf):
    v = resolve_voices(sf, sf.presets[0], 64, 110)[0]
    # instrumento: atenuação 30 cB local; preset: +20 cB global → 50 cB
    assert v.g("initialAttenuation") == 50
    assert v.g("sampleModes") == 1 and v.loop_mode == 1          # herdado da zona global do instrumento
    assert v.g("coarseTune") == 1                                  # do preset, somado ao padrão 0
    assert v.vol_envelope()["release_s"] == pytest.approx(1.0)    # 0 timecents = 1 s


def test_tuning_and_playback_rate(sf):
    v = resolve_voices(sf, sf.presets[0], 60, 64)[0]   # sample Agudo, root 72, coarse +1
    assert v.tuning_cents == pytest.approx(100 + (60 - 72) * 100)
    assert v.playback_rate == pytest.approx(13379 * 2 ** (-11 / 12))


def test_overriding_root_key(sf):
    v = resolve_voices(sf, sf.presets[1], 40, 100)[0]
    assert v.root_key == 40 and v.sample.name == "Caixa"


def test_instrument_match_reports_kit_missing_and_unmatched(sf):
    t0 = track_bytes([(0, pc(0, 5)), (0, on(0, 40, 90)), (10, off(0, 40)),
                      (0, on(0, 110, 90)), (10, off(0, 110))])          # 110 fora das zonas
    t1 = track_bytes([(0, pc(1, 127))] + [x for k in (36, 38, 40) for x in ((0, on(1, k, 100)), (5, off(1, k)))])
    t2 = track_bytes([(0, pc(2, 9)), (0, on(2, 60, 100)), (5, off(2, 60))])  # programa inexistente
    song = build_song(parse_smf(midi_file([t0, t1, t2])))
    m = {(x.track, x.program): x for x in match_instruments(song, sf)}
    assert m[(0, 5)].preset_name == "Piano GBA" and m[(0, 5)].keys_without_voice == [110]
    assert m[(1, 127)].kind_hint == "kit (keysplit)"
    assert m[(2, 9)].preset_name is None and m[(2, 9)].keys_without_voice == [60]


def test_rejects_non_sf2():
    with pytest.raises(Sf2FormatError):
        parse_sf2(b"RIFF\x04\x00\x00\x00WAVE")


def test_channel_10_not_gm_drums_for_gba_ripper_files(sf):
    from tests.fixtures.builders import meta_text
    t = track_bytes([(0, meta_text(0x06, "Converted by SequenceRipper 2.0")), (0, pc(9, 5)),
                     (0, on(9, 64, 90)), (5, off(9, 64)), (5, on(9, 40, 90)), (9, off(9, 40)),
                     (9, on(9, 70, 90)), (12, off(9, 70))])
    m = match_instruments(build_song(parse_smf(midi_file([t]))), sf)[0]
    assert m.preset_name == "Piano GBA" and m.kind_hint == "tonal"


def test_cgb_square_detected_from_ripper_sample_names():
    from mdconv.analysis.instrument_match import classify_kind
    from types import SimpleNamespace as NS
    v = lambda name: NS(sample=NS(name=name), g=lambda k: 100)
    assert classify_kind([v("square 12.5%A"), v("square 12.5%B")], False) == "CGB quadrada 12.5%"
    assert classify_kind([v("GB3 @0xbc45a0A")], False) == "CGB onda programável"
    assert classify_kind([v("Sample @0x1234")], False) == "tonal"
