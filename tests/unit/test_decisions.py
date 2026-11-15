"""Histórico de decisões: aprovar, recusar e ouvir ficam registrados com contexto."""
import pytest

from mdconv.app.project import Project, ProjectSettings
from mdconv.fm import instrument_fit
from mdconv.fm.fitter import Candidate, FitResult, eval_patch
from mdconv.fm.patch import Operator, Patch
from mdconv.library import decisions
from tests.unit.test_phase7 import CUT, sf_bytes, song_bytes


def _p(mul):
    return Patch(alg=7, fb=0, ops=[Operator(mul=mul, tl=0, ar=31, rr=7), Operator(tl=127),
                                   Operator(tl=127), Operator(tl=127)], name=f"m{mul}")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("MDCONV_DECISOES", str(tmp_path / "decisoes.jsonl"))

    def fake_fit(search, final, **kw):          # dois candidatos: o de MUL 1 é o mais barato
        return FitResult({}, [Candidate(_p(1), eval_patch(_p(1), final), "DE+refino alg7"),
                              Candidate(_p(5), eval_patch(_p(5), final), "triagem alg7")], {}, 0.0, [])
    monkeypatch.setattr(instrument_fit, "fit_instrument", fake_fit)
    (tmp_path / "m.mid").write_bytes(song_bytes(CUT))
    (tmp_path / "b.sf2").write_bytes(sf_bytes())
    p = Project(ProjectSettings(midi=str(tmp_path / "m.mid"), sf2=str(tmp_path / "b.sf2"),
                                library=str(tmp_path / "lib"), out_dir=str(tmp_path / "out")))
    p.load()
    return p


def test_approve_is_logged_with_context(proj):
    cands = proj.fit((0, 0), use_bank=False, use_library=False, budget="quick")
    proj.listened((0, 0), cands[0].rank)
    proj.approve((0, 0), cands[0].rank)
    rows = decisions.read()
    assert [r["evento"] for r in rows] == ["ouviu", "aprovado"]
    a = rows[-1]
    assert a["instrumento"]["banco"] == 0 and a["instrumento"]["preset"]
    assert a["candidato"]["origem"] and a["candidato"]["patch"]["alg"] == 7
    assert a["opcoes_busca"]["budget"] == "quick"
    assert len(a["alternativas"]) == len(cands) - 1
    assert a["musica"] == "m.mid" and a["versao"]


def test_reject_marks_and_auto_skips(proj):
    cands = proj.fit((0, 0), use_bank=False, use_library=False)
    cheapest = min(cands, key=lambda c: c.cost)
    proj.reject((0, 0), cheapest.rank)
    again = {c.rank: c for c in proj.candidates((0, 0))}
    assert again[cheapest.rank].rejected
    assert decisions.read()[-1]["evento"] == "recusado"
    proj.approve((0, 0), cheapest.rank)                 # aprovar desfaz a recusa
    again = {c.rank: c for c in proj.candidates((0, 0))}
    assert again[cheapest.rank].approved and not again[cheapest.rank].rejected


def test_auto_approve_ignores_rejected(proj, monkeypatch):
    cands = proj.fit((0, 0), use_bank=False, use_library=False)
    cheapest = min(cands, key=lambda c: c.cost)
    proj.reject((0, 0), cheapest.rank)
    # o rascunho gera os candidatos de novo: a busca devolve o mesmo patch recusado e a
    # recusa precisa sobreviver (vale para o patch, não para a posição na lista)
    done = proj.auto_approve(use_bank=False, use_library=False)
    assert len(done) == 1
    approved = next(c for c in proj.candidates((0, 0)) if c.approved)
    assert approved.rank != cheapest.rank
    assert decisions.read()[-1]["evento"] == "rascunho_auto"


def test_summary_counts_by_origin(proj):
    cands = proj.fit((0, 0), use_bank=False, use_library=False)
    proj.reject((0, 0), cands[1].rank)
    proj.approve((0, 0), cands[0].rank)
    s = decisions.summary()
    assert s["eventos"]["aprovado"] == 1 and s["eventos"]["recusado"] == 1
    assert sum(v.get("aprovado", 0) for v in s["por_origem"].values()) == 1


def test_logging_never_breaks(proj, monkeypatch, tmp_path):
    monkeypatch.setenv("MDCONV_DECISOES", str(tmp_path))      # é uma pasta: gravar falha
    cands = proj.fit((0, 0), use_bank=False, use_library=False)
    proj.approve((0, 0), cands[0].rank)                        # não pode levantar erro


def test_rejection_survives_regeneration(proj):
    cands = proj.fit((0, 0), use_bank=False, use_library=False)
    proj.reject((0, 0), cands[0].rank)
    again = proj.fit((0, 0), use_bank=False, use_library=False)
    assert again[0].rejected and not again[1].rejected
