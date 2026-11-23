"""Pastas da extração: pasta nova escolhida pelo usuário e uma pasta para cada música."""
from mdconv import extract


def _fake(tmp_path):
    out = tmp_path / "saida" / "kof97"
    m = out / "KOF_97" / "musicas"
    m.mkdir(parents=True)
    for stem in ("44_Bloody", "21_Esaka"):
        for suf in (".xgm", ".vgm", ".fur", "_previa.wav"):
            (m / f"{stem}{suf}").write_bytes(b"x")
    (out / "KOF_97" / "efeitos").mkdir()
    (out / "KOF_97" / "efeitos" / "e.c").write_text("c")
    return out


def test_new_folder_and_one_folder_per_song(tmp_path):
    out = _fake(tmp_path)
    base = extract.organize(str(out / "KOF_97"), str(out), per_song=True)
    assert base == str(out)
    assert sorted(p.name for p in (out / "44_Bloody").iterdir()) == [
        "44_Bloody.fur", "44_Bloody.vgm", "44_Bloody.xgm", "44_Bloody_previa.wav"]
    assert (out / "21_Esaka" / "21_Esaka.xgm").exists() and (out / "efeitos" / "e.c").exists()
    assert not (out / "KOF_97").exists() and not (out / "musicas").exists()


def test_output_folder_keeps_game_folder(tmp_path):
    out = _fake(tmp_path)
    base = extract.organize(str(out / "KOF_97"), None, per_song=True)
    assert base == str(out / "KOF_97") and (out / "KOF_97" / "44_Bloody" / "44_Bloody.xgm").exists()


def test_flat_when_not_per_song(tmp_path):
    out = _fake(tmp_path)
    extract.organize(str(out / "KOF_97"), str(out), per_song=False)
    assert (out / "musicas" / "44_Bloody.xgm").exists()
