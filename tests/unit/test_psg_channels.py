"""Arranjo: escolher quais tons do PSG a música pode usar."""
import pytest

from mdconv.app.project import Project, ProjectSettings
from mdconv.io_sf2 import gm
from tests.fixtures.builders import meta_text, midi_file, off, on, pc, track_bytes


@pytest.fixture(autouse=True)
def _gm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(gm, "_cache", None)


def _dense_midi(path):
    """8 partes tonais ao mesmo tempo (o FM não dá conta e o PSG é usado) e bateria no canal
    10 — sem PCM, o kit vai para o PSG de tom."""
    drums = [(0, meta_text(3, "Bateria"))]
    for i in range(16):
        k = (36, 42, 38, 42)[i % 4]
        drums += [(i * 240, on(9, k, 110)), (i * 240 + 100, off(9, k))]
    tracks = [track_bytes(drums)]
    for ch in range(8):
        ev = [(0, meta_text(3, f"Parte {ch + 1}")), (0, pc(ch, ch * 8))]
        for i in range(16):
            k = 57 + ch * 2 + (i % 3)
            ev += [(i * 240, on(ch, k, 100)), (i * 240 + 220, off(ch, k))]
        tracks.append(track_bytes(ev))
    path.write_bytes(midi_file(tracks))
    return path


def _psg_used(conv):
    return {ev[3] + 1 for ev in conv.driver.timeline if ev[0] == "on" and ev[2] == "psg"}


@pytest.mark.parametrize("alloc", ["auto", "fixo", "dinamico"])
@pytest.mark.parametrize("chans", [[1, 2, 3], [2], [1, 3], []])
def test_only_the_chosen_psg_tones_are_used(tmp_path, chans, alloc):
    p = Project(ProjectSettings(midi=str(_dense_midi(tmp_path / "d.mid")), sf2="",
                                library=str(tmp_path / "lib"), out_dir=str(tmp_path / "out"),
                                psg_channels=chans, loop_mode="none", fm_alloc=alloc,
                                use_pcm=False))
    p.load()
    c = p.convert()
    used = _psg_used(c)
    assert used <= set(chans)
    if not chans:
        assert "kit sem destino" in p.plan_text()
    if chans:
        assert used                                   # com o FM lotado, o PSG liberado é usado
    else:
        assert "PSG de tom desligado" in "\n".join(c.plan.log)
        assert not any((pp.pool == "PSG" and not pp.kind.startswith("kit")) or pp.overflow_psg
                       for pp in c.plan.parts)
    # os tons desmarcados não recebem nenhuma escrita de volume diferente de mudo
    for f in c.stream.frames:
        for w in f:
            if w[0] == "psg" and w[1] & 0x90 == 0x90:
                ch = ((w[1] >> 5) & 3) + 1
                if ch <= 3 and ch not in chans:
                    assert w[1] & 0x0F == 0x0F
    p.export()
    assert (tmp_path / "out" / "d.xgm").is_file()


def test_setting_survives_save_and_load(tmp_path):
    s = ProjectSettings(midi="m.mid", psg_channels=[1, 3])
    assert ProjectSettings.from_json(s.to_json()).psg_channels == [1, 3]
    assert ProjectSettings.from_json('{"midi": "m.mid"}').psg_channels == [1, 2, 3]   # projetos antigos
