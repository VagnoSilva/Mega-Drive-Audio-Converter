"""MIDI sem SoundFont (banco General MIDI padrão) e verificação do kit de bateria."""
import numpy as np
import pytest

from mdconv.app.project import Project, ProjectSettings
from mdconv.io_midi.song_builder import load_song
from mdconv.io_sf2 import gm
from mdconv.io_sf2.resolve import find_preset, resolve_voices
from mdconv.io_sf2.sf2 import GEN_ID as G
from tests.fixtures.builders import cc, gen, meta_text, midi_file, off, on, pc, sf2_file, track_bytes


@pytest.fixture(autouse=True)
def _gm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(gm, "_cache", None)


def _gm_midi(path, names=("Piano", "Baixo", "Bateria")):
    """MIDI General MIDI: piano no canal 1, baixo no 2, bateria no canal 10."""
    mel = [(0, meta_text(3, names[0])), (0, pc(0, 0))]
    bass = [(0, meta_text(3, names[1])), (0, pc(1, 33))]
    drums = [(0, meta_text(3, names[2]))]
    for i in range(8):
        t, k, d = i * 240, 60 + (i % 4) * 2, (36, 42, 38, 42)[i % 4]
        mel += [(t, on(0, k, 100)), (t + 200, off(0, k))]
        bass += [(t, on(1, 36, 100)), (t + 200, off(1, 36))]
        drums += [(t, on(9, d, 110)), (t + 100, off(9, d))]
    path.write_bytes(midi_file([track_bytes(mel), track_bytes(bass), track_bytes(drums)]))
    return path


def _cps1_like_sf2(path):
    """Imita o SF2 da extração CPS1: timbres FM no banco 0, uma amostra do Oki (uma voz) no
    banco 1 e a mesma cópia no banco 128."""
    t = np.arange(8000) / 8000
    voice = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)
    tone = (np.sin(2 * np.pi * 261.6 * np.arange(400) / 16000) * 12000).astype(np.int16)
    path.write_bytes(sf2_file(
        [{"name": "FM000", "data": tone, "rate": 16000}, {"name": "Oki000", "data": voice, "rate": 8000}],
        [{"name": "fm", "zones": [[gen(G["sampleID"], 0)]]}, {"name": "oki", "zones": [[gen(G["sampleID"], 1)]]}],
        [{"name": "sf2 FM 000", "bank": 0, "program": 0, "zones": [[gen(G["instrument"], 0)]]},
         {"name": "sf2 FM 033", "bank": 0, "program": 33, "zones": [[gen(G["instrument"], 0)]]},
         {"name": "sf2 Oki 000", "bank": 1, "program": 0, "zones": [[gen(G["instrument"], 1)]]},
         {"name": "sf2 Oki 000", "bank": 128, "program": 0, "zones": [[gen(G["instrument"], 1)]]}]))
    return path


def test_default_soundfont_has_kit_and_128_instruments():
    sf = gm.default_soundfont()
    pm = sf.preset_map()
    assert all((0, p) in pm for p in range(128)) and (128, 0) in pm and (128, 25) in pm
    kit = pm[(128, 0)]
    names = {k: resolve_voices(sf, kit, k, 100)[0].sample.name for k in (36, 38, 42, 49)}
    assert len(set(names.values())) == 4                       # uma amostra por peça
    v = resolve_voices(sf, pm[(0, 33)], 40, 100)[0]
    assert v.loop_mode == 1 and abs(v.tuning_cents - 400) < 10  # baixo afinado e em loop
    assert gm.default_sf2_path().is_file()


def test_project_without_soundfont_uses_general_midi(tmp_path):
    mid = _gm_midi(tmp_path / "alpha.mid")
    p = Project(ProjectSettings(midi=str(mid), sf2="", library=str(tmp_path / "lib"),
                                out_dir=str(tmp_path / "out")))
    assert not [e for e in p.settings.validate() if "SF2" in e]
    warnings = p.load()
    assert any("General MIDI" in w for w in warnings) and "sem SoundFont" in p.sf_notice
    assert "C10 Kit padrao GM" in p.plan_text() and "kit de bateria → PCM" in p.plan_text()
    names = {r.name for r in p.instruments()}
    assert {"Acoustic Grand Pian", "Finger Bass"} <= names
    lines = p.export()
    assert (tmp_path / "out" / "alpha.xgm").is_file() and any("amostras" in l for l in lines)


def test_cps1_soundfont_drums_replaced_by_default_kit(tmp_path):
    mid = _gm_midi(tmp_path / "alpha.mid")
    sf2 = _cps1_like_sf2(tmp_path / "sf2.sf2")
    p = Project(ProjectSettings(midi=str(mid), sf2=str(sf2), library=str(tmp_path / "lib"),
                                out_dir=str(tmp_path / "out")))
    warnings = p.load()
    assert any("kit de bateria padrão" in w for w in warnings)
    assert "kit de bateria padrão" in p.sf_notice and "Oki" in p.sf_notice
    assert "deixe o campo SoundFont vazio" in p.sf_notice      # instrumentos de outro jogo
    kit = find_preset(p.sf, 0, 0, drum_channel=True).preset
    assert kit.name.startswith("Kit padrao")
    assert resolve_voices(p.sf, kit, 38, 100)[0].sample.name == "GM Caixa"
    # o resto do SF2 continua: o piano é o timbre do SF2 e as amostras dele ainda tocam
    piano = find_preset(p.sf, 0, 0).preset
    assert piano.name == "sf2 FM 000"
    v = resolve_voices(p.sf, piano, 60, 100)[0]
    assert np.array_equal(p.sf.sample_data(v.sample)[:50], np.asarray(
        (np.sin(2 * np.pi * 261.6 * np.arange(400) / 16000) * 12000).astype(np.int16)[:50]))
    snare = resolve_voices(p.sf, kit, 38, 100)[0].sample
    assert np.abs(p.sf.sample_data(snare).astype(int)).max() > 10000
    p.export()
    assert (tmp_path / "out" / "alpha.xgm").is_file()


def test_extracted_midi_keeps_its_own_soundfont(tmp_path):
    mid = _gm_midi(tmp_path / "x.mid", names=("FM 1", "FM 2", "Oki 1"))
    sf2 = _cps1_like_sf2(tmp_path / "sf2.sf2")
    from mdconv.io_sf2.sf2 import read_sf2
    s = read_sf2(sf2)
    song = load_song(mid)
    assert gm.drum_problem(song, s) is None and gm.instruments_problem(song, s) is None


def test_general_midi_soundfont_passes_check(tmp_path):
    """Um SF2 General MIDI de verdade (o padrão do programa) não dispara a troca."""
    song = load_song(_gm_midi(tmp_path / "m.mid"))
    sf = gm.default_soundfont()
    assert gm.drum_problem(song, sf) is None and gm.instruments_problem(song, sf) is None
    out, notes, msg = gm.prepare(song, sf)
    assert out is sf and notes == [] and msg is None


def test_approve_after_switching_soundfont(tmp_path):
    """Candidatos gerados com um SF2 e aprovação com outro (mesmo banco:programa, outro preset):
    a aprovação usa os candidatos do SF2 atual e o instrumento aparece como aprovado."""
    import json
    mid = _gm_midi(tmp_path / "alpha.mid")
    lib, out = tmp_path / "lib", tmp_path / "out"
    # 1) candidatos do baixo com o SF2 "de jogo" (preset 'sf2 FM 033')
    p1 = Project(ProjectSettings(midi=str(mid), sf2=str(_cps1_like_sf2(tmp_path / "sf2.sf2")),
                                 library=str(lib), out_dir=str(out)))
    p1.load()
    p1.fit((0, 33), budget="quick", use_bank=False, use_library=False)
    # 2) o mesmo MIDI com outro SoundFont (aqui: sem SF2 → 'Finger Bass'), mesma pasta de saída
    p2 = Project(ProjectSettings(midi=str(mid), sf2="", library=str(lib), out_dir=str(out)))
    p2.load()
    assert p2.candidates((0, 33)) == []             # os do outro SF2 não valem
    with pytest.raises(ValueError):
        p2.approve((0, 33), 1)
    cands = p2.fit((0, 33), budget="quick", use_bank=False, use_library=False)
    rep = json.loads((out / "ajustes_fm" / "patches.json").read_text(encoding="utf-8"))
    assert len([e for e in rep["instruments"] if (e["bank"], e["program"]) == (0, 33)]) == 2
    p2.approve((0, 33), cands[0].rank)
    row = next(r for r in p2.instruments() if r.key == (0, 33))
    assert row.status == "aprovado"
    # e o primeiro SF2 continua vendo os candidatos dele
    assert p1.candidates((0, 33))
