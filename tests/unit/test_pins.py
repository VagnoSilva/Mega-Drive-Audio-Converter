"""Patches travados no projeto: a música exporta igual mesmo que a biblioteca mude."""
import time

import pytest

from mdconv.app.project import Project, ProjectSettings
from mdconv.convert import approved_patches
from mdconv.fm.patch import Operator, Patch
from mdconv.library.patch_library import LibraryEntry, PatchLibrary
from tests.unit.test_phase7 import CUT, sf_bytes, song_bytes


def _patch(tl_mod: int) -> Patch:
    return Patch(alg=4, fb=3, ops=[Operator(mul=2, tl=tl_mod, ar=31, d1r=5, sl=3, rr=7),
                                   Operator(mul=1, tl=0, ar=31, d1r=4, sl=2, rr=7),
                                   Operator(tl=127), Operator(tl=127)], name=f"teste {tl_mod}")


def _approve(lib, tl_mod, when):
    return PatchLibrary(lib).add(LibraryEntry(
        id="", name="Lead", patch=_patch(tl_mod), approved=True,
        source={"bank": 0, "program": 0, "preset": "Lead", "approved_at": when}))


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "m.mid").write_bytes(song_bytes(CUT))
    (tmp_path / "b.sf2").write_bytes(sf_bytes())
    lib = tmp_path / "lib"
    _approve(lib, 20, "2026-01-01T10:00:00")
    s = ProjectSettings(midi=str(tmp_path / "m.mid"), sf2=str(tmp_path / "b.sf2"),
                        library=str(lib), out_dir=str(tmp_path / "out"))
    p = Project(s)
    p.load()
    return p


def _used_patch(p) -> dict:
    return p.convert().patches[(0, 0)].to_dict()


def test_pin_on_save_and_roundtrip(proj, tmp_path):
    assert not proj.settings.pinned_patches
    done = proj.pin_patches()
    assert done and "0:0" in proj.settings.pinned_patches
    path = proj.settings.save(tmp_path / "m.mdcproj")
    s2 = ProjectSettings.load(path)
    assert s2.pinned_patches == proj.settings.pinned_patches
    row = next(r for r in proj.instruments() if r.key == (0, 0))
    assert row.status == "aprovado" and row.pinned


def test_library_change_does_not_affect_pinned_song(proj):
    proj.pin_patches()
    before = _used_patch(proj)
    _approve(proj.library_dir, 60, "2026-02-01T10:00:00")      # aprovação nova em outra música
    assert approved_patches(proj.library_dir, proj.sf)[(0, 0)].ops[0].tl == 60
    proj.invalidate()
    assert _used_patch(proj) == before                          # esta música não mudou
    diffs = proj.pin_differences()
    assert len(diffs) == 1 and diffs[0]["key"] == (0, 0)


def test_update_pins_takes_library_version(proj):
    proj.pin_patches()
    _approve(proj.library_dir, 60, "2026-02-01T10:00:00")
    proj.update_pins()
    assert _used_patch(proj)["ops"][0]["tl"] == 60
    assert proj.pin_differences() == []


def test_pin_patches_never_replaces_existing_pin(proj):
    proj.pin_patches()
    _approve(proj.library_dir, 60, "2026-02-01T10:00:00")
    assert proj.pin_patches() == []                             # salvar de novo não troca a trava
    assert proj.settings.pinned_patches["0:0"]["patch"]["ops"][0]["tl"] == 20


def test_unpin_follows_library_again(proj):
    proj.pin_patches()
    _approve(proj.library_dir, 60, "2026-02-01T10:00:00")
    proj.unpin()
    assert _used_patch(proj)["ops"][0]["tl"] == 60


def test_pin_ignored_if_preset_name_changed(proj):
    proj.pin_patches()
    proj.settings.pinned_patches["0:0"]["preset"] = "Outro instrumento"
    proj.invalidate()
    assert _used_patch(proj)["ops"][0]["tl"] == 20              # vem da biblioteca, não da trava
    row = next(r for r in proj.instruments() if r.key == (0, 0))
    assert not row.pinned


def test_without_pins_behaves_as_before(proj):
    _approve(proj.library_dir, 60, "2026-02-01T10:00:00")
    assert _used_patch(proj)["ops"][0]["tl"] == 60
