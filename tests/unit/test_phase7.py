"""Fase 7: detecção de loop e camada de aplicação usada pela interface (sem Qt)."""
import numpy as np
import pytest

from mdconv.app.project import Project, ProjectSettings
from mdconv.arrange.loop import ends_abruptly, resolve_loop
from mdconv.io_midi.smf import parse_smf
from mdconv.io_midi.song_builder import build_song
from mdconv.io_sf2.sf2 import GEN_ID as G
from tests.fixtures.builders import gen, meta_text, midi_file, off, on, pc, sf2_file, track_bytes

RIP = (0, meta_text(0x06, "Converted by SequenceRipper 2.0"))


def song_bytes(notes, markers=(), ppq=24):
    """notes: (início, duração, tecla) no canal 0, ppq 24 (como o ripper)."""
    ev = [RIP] + [(t, meta_text(0x06, m)) for t, m in markers] + [(0, pc(0, 0))]
    for start, dur, key in notes:
        ev += [(start, on(0, key, 100)), (start + dur, off(0, key))]
    return midi_file([track_bytes(ev)], ppq=ppq)


def song(notes, markers=()):
    return build_song(parse_smf(song_bytes(notes, markers)))


def sf_bytes():
    data = (np.sin(np.arange(2000) * 2 * np.pi / 50) * 15000).astype(np.int16)
    s = [{"name": "T", "data": data, "rate": 22050, "root": 69, "loop": (0, 2000)}]
    insts = [{"name": "I", "zones": [[gen(G["sampleModes"], 1), gen(G["sampleID"], 0)]]}]
    presets = [{"name": "Lead", "bank": 0, "program": 0, "zones": [[gen(G["instrument"], 0)]]}]
    return sf2_file(s, insts, presets)


CUT = [(96 + 24 * i, 20, 60 + i % 5) for i in range(31)] + [(96 + 24 * 31 - 4, 4, 67)]  # nota cortada no fim
FINAL = [(96 + 24 * i, 20, 60 + i % 5) for i in range(30)] + [(96 + 24 * 30, 96, 60)]  # acorde final


def test_markers_win():
    s = song(CUT, markers=[(95, "loopStart"), (96 + 24 * 31, "loopEnd")])
    lp = resolve_loop(s)
    assert lp.source == "marcador" and lp.start_tick == 95 and not lp.estimated


def test_estimated_when_song_is_cut():
    s = song(CUT)
    assert ends_abruptly(s)[0]
    lp = resolve_loop(s)
    assert lp.estimated and lp.start_tick == 96
    assert resolve_loop(s, "markers") is None
    assert resolve_loop(s, "none") is None


def test_no_loop_when_song_has_real_ending():
    s = song(FINAL)
    assert not ends_abruptly(s)[0]
    assert resolve_loop(s) is None


def test_manual_loop_and_validation():
    s = song(CUT)
    lp = resolve_loop(s, start_tick=192)
    assert lp.source == "manual" and lp.start_tick == 192
    with pytest.raises(ValueError):
        resolve_loop(s, start_tick=10**6)


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "m.mid").write_bytes(song_bytes(CUT))
    (tmp_path / "b.sf2").write_bytes(sf_bytes())
    s = ProjectSettings(midi=str(tmp_path / "m.mid"), sf2=str(tmp_path / "b.sf2"),
                        library=str(tmp_path / "lib"), out_dir=str(tmp_path / "out"))
    p = Project(s)
    p.load()
    return p


def test_settings_roundtrip(tmp_path):
    s = ProjectSettings(midi="a.mid", sf2="b.sf2", pcm_channels=[1], fm_att_db=6.0, loop_start_tick=96)
    path = s.save(tmp_path / "x")
    assert path.suffix == ".mdcproj"
    t = ProjectSettings.load(path)
    assert t == s
    assert "escolha o arquivo MIDI" in ProjectSettings().validate()
    assert ProjectSettings(midi=__file__, sf2=__file__, pcm_channels=[5]).validate() == ["canais PCM vão de 1 a 4"]


def test_project_instruments_and_loop(proj):
    rows = proj.instruments()
    assert len(rows) == 1
    r = rows[0]
    assert r.key == (0, 0) and r.needs_fm and r.status == "provisório" and r.notes == 32
    assert proj.loop().estimated
    proj.set_loop_start_bar(3)                        # compasso 3 = tick 192 (ppq 24, 4/4)
    assert proj.loop().start_tick == 192 and proj.loop().source == "manual"
    proj.set_loop_start_bar(2)                        # compasso da primeira nota: começa nela
    assert proj.loop().start_tick == 96
    proj.set_loop_start_bar(None)
    assert proj.loop().estimated
    with pytest.raises(ValueError):
        proj.set_loop_start_bar(999)


def test_project_export_preview_seam(proj):
    lines = proj.export()
    out = proj.out_dir
    assert (out / "m.xgm").exists() and (out / "m.vgm").exists()
    assert any("patch provisório" in line for line in lines)
    assert any("loop estimado" in line for line in lines)
    assert proj.render_preview().exists()
    assert proj.render_seam(seconds=1.0).exists()
    # a conversão fica em cache até alguma opção mudar
    c1 = proj.convert()
    assert proj.convert() is c1
    proj.settings.vibrato = False
    assert proj.convert() is not c1


def test_approve_requires_candidates(proj):
    with pytest.raises(ValueError):
        proj.approve((0, 0), 1)


def test_load_requires_files(tmp_path):
    with pytest.raises(ValueError):
        Project(ProjectSettings(midi=str(tmp_path / "nada.mid"))).load()


def test_note_held_across_loop_start_does_not_crash():
    """Regressão: uma nota soando no ponto de loop não pode perder o patch."""
    from mdconv.arrange.plan import make_plan
    from mdconv.convert import fallback_patch
    from mdconv.driver.driver import Driver
    from mdconv.io_sf2.sf2 import parse_sf2
    s = song([(96, 200, 60), (300, 20, 62), (340, 20, 64)],
             markers=[(150, "loopStart"), (400, "loopEnd")])
    sf = parse_sf2(sf_bytes())
    plan = make_plan(s, sf, use_pcm=False)
    st = Driver(s, plan, {(0, 0): fallback_patch()}, vibrato=False).run()
    assert st.loop_frame is not None
    loads = [f for f, ws in enumerate(st.frames) for w in ws if w[0] == "fm" and 0x30 <= w[2] < 0x40]
    assert any(f > st.loop_frame for f in loads)      # depois do loop o patch é recarregado
