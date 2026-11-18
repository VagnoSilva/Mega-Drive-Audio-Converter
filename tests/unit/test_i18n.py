"""Idioma da interface: todo texto com tr() tem tradução, e as traduções preservam as partes
variáveis ({0}, {1:.2f}…) e os espaços/quebras das pontas."""
import ast
import re
from pathlib import Path

import pytest

from mdconv import i18n
from mdconv.i18n_en import EN

ROOT = Path(__file__).resolve().parents[2] / "mdconv"
# textos que são iguais nos dois idiomas
SAME = {"Mega Drive Audio Converter", "XGM v1 (SGDK)", "MIDI (*.mid *.midi)", "SoundFont (*.sf2)",
        "SoundFont (.sf2)", "VGM (*.vgm *.vgz)", "WAV (*.wav)", "Normal", "PSG", " dB", " Hz", "6 dB",
        "14000 Hz = XGM v1.", "Furnace 0.6.8.3 (.fur)", "FM {0}", "PSG {0}", "FM {}", "{0}: {1}",
        "Idioma / Language", "AY{0} {1} → {2}", "3. Loop", "SoundFont (.sf2):", " ({0} cand.)",
        "\nSoundFont: {0}"}


def _keys():
    out = {}
    for f in ROOT.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        if "tr(" not in src:
            continue
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" and n.args \
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                out.setdefault(n.args[0].value, f"{f.name}:{n.lineno}")
    return out


def test_every_interface_text_has_an_english_translation():
    missing = [f"{where}: {k!r}" for k, where in _keys().items() if k not in EN and k not in SAME]
    assert not missing, "sem tradução:\n" + "\n".join(missing)


@pytest.mark.parametrize("pt,en", list(EN.items()))
def test_translation_keeps_placeholders_and_edges(pt, en):
    ph = lambda s: sorted(re.findall(r"\{[^{}]*\}", s))    # noqa: E731
    assert ph(pt) == ph(en)
    assert pt[:len(pt) - len(pt.lstrip())] == en[:len(en) - len(en.lstrip())]
    assert pt[len(pt.rstrip()):] == en[len(en.rstrip()):]
    assert ("&" in pt) == ("&" in en)                       # atalhos de menu


def test_tr_follows_the_language():
    try:
        i18n.set_language("en")
        assert i18n.tr("Abrir música") == "Open song"
        assert i18n.tr("texto sem tradução") == "texto sem tradução"
        assert i18n.tr("{0}: {1} nota(s)").format("FM1", 3) == "FM1: 3 note(s)"
        i18n.set_language("pt")
        assert i18n.tr("Abrir música") == "Abrir música"
        i18n.set_language("xx")
        assert i18n.language() == "pt"
    finally:
        i18n.set_language("pt")


def test_settings_and_data_strings_are_not_translated():
    """Nome das configurações, chaves e dados dos combos não podem passar por tr()."""
    for f in (ROOT / "gui").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert 'QSettings("mdconv", tr(' not in src
        assert not re.search(r"\.(setValue|value)\(tr\(", src)
