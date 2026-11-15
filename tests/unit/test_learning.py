"""O conversor aproveita os patches já aprovados (biblioteca) e o rascunho automático."""
import numpy as np
import pytest

from mdconv.app.project import Project, ProjectSettings
from mdconv.fm import instrument_fit
from mdconv.fm.fitter import FitResult, RefNote, fit_from_library
from mdconv.fm.patch import Operator, Patch, render_note
from mdconv.library.patch_library import LibraryEntry, PatchLibrary
from tests.unit.test_phase7 import CUT, sf_bytes, song_bytes


def _bright() -> Patch:
    return Patch(alg=4, fb=4, ops=[Operator(mul=3, tl=18, ar=31, d1r=6, sl=4, rr=7),
                                   Operator(mul=1, tl=0, ar=31, d1r=3, sl=2, rr=7),
                                   Operator(tl=127), Operator(tl=127)], name="brilhante")


def _sine() -> Patch:
    return Patch(alg=7, fb=0, ops=[Operator(mul=1, tl=0, ar=31, rr=7), Operator(tl=127),
                                   Operator(tl=127), Operator(tl=127)], name="seno")


def _refs(patch, hz=(220.0, 440.0)):
    return [RefNote(h, 0.4, render_note(patch, h, 0.4, 44100), 44100) for h in hz]


def _entry(name, patch, **src):
    return LibraryEntry(id=name, name=name, patch=patch, approved=True,
                        source={"bank": 0, "program": 9, "preset": name, **src})


def test_library_suggests_the_similar_patch():
    refs = _refs(_bright())
    cands = fit_from_library([_entry("seno", _sine()), _entry("brilhante", _bright(), game="sf2")],
                             refs, refs, n_polish=2)
    assert cands, "sem candidatos da biblioteca"
    best = cands[0]
    assert best.stage.startswith("biblioteca: brilhante")
    assert best.cost.total < 1.0                     # o mesmo patch: praticamente idêntico


def test_library_respects_max_fb():
    refs = _refs(_bright())
    assert fit_from_library([_entry("brilhante", _bright())], refs, refs, max_fb=2) == []


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "m.mid").write_bytes(song_bytes(CUT))
    (tmp_path / "b.sf2").write_bytes(sf_bytes())
    s = ProjectSettings(midi=str(tmp_path / "m.mid"), sf2=str(tmp_path / "b.sf2"),
                        library=str(tmp_path / "lib"), out_dir=str(tmp_path / "out"))
    p = Project(s)
    p.load()
    return p


def _no_search(monkeypatch):
    monkeypatch.setattr(instrument_fit, "fit_instrument",
                        lambda *a, **k: FitResult({}, [], {}, 0.0, []))


def test_fit_includes_library_candidates(proj, monkeypatch):
    _no_search(monkeypatch)                             # só o que vem da biblioteca
    PatchLibrary(proj.library_dir).add(_entry("outro jogo", _sine(), game="dino"))
    cands = proj.fit((0, 0), use_bank=False)
    assert cands and all(c.summary.startswith("biblioteca") for c in cands)


def test_fit_without_library_option(proj, monkeypatch):
    _no_search(monkeypatch)
    PatchLibrary(proj.library_dir).add(_entry("outro jogo", _sine()))
    assert proj.fit((0, 0), use_bank=False, use_library=False) == []


def test_auto_approve_marks_draft(proj, monkeypatch):
    from mdconv.fm.fitter import Candidate, eval_patch

    def fake_fit(search, final, **kw):                 # busca rápida e determinística
        return FitResult({}, [Candidate(_sine(), eval_patch(_sine(), final), "teste")], {}, 0.0, [])
    monkeypatch.setattr(instrument_fit, "fit_instrument", fake_fit)
    done = proj.auto_approve(use_bank=False, use_library=False)
    assert len(done) == 1
    row = next(r for r in proj.instruments() if r.key == (0, 0))
    assert row.status == "aprovado" and row.auto
    e = [x for x in PatchLibrary(proj.library_dir).entries() if x.approved]
    assert len(e) == 1 and e[0].source.get("auto") is True
    # nada mais a fazer na segunda vez
    assert proj.auto_approve(use_bank=False, use_library=False) == []
    # aprovar manualmente depois substitui o rascunho
    proj.approve((0, 0), 1)
    row = next(r for r in proj.instruments() if r.key == (0, 0))
    assert row.status == "aprovado" and not row.auto
