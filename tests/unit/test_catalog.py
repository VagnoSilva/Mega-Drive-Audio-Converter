"""Catálogo de compatibilidade (aba Catálogo Mega Drive): nomes, vínculo ROM ↔ jogo, marcação e CSV."""
import csv

from mdconv import catalog as cat


def test_names_regions_kinds_and_franchises():
    assert cat.clean_title("Lion King, The (World) (Rev A)") == "Lion King, The"
    assert cat.regions_of("Sonic (Japan, Europe, Korea) (En)") == ["J", "E", "K"]
    assert cat.kind_of("Streets of Rage 3 (USA) (Beta) (1994-01-01)") == "Protótipo"
    assert cat.kind_of("Light Crusader (USA)", "GAMES TRADUZIDOS/Light Crusader (USA).gen") == "Tradução"
    assert cat.kind_of("Mortal Kombat 5 (Taiwan) (Unl)") == "Não oficial"
    assert cat.detect_franchise("Bare Knuckle III") == "Streets of Rage"
    assert cat.detect_franchise("Chuck Rock II - Son of Chuck", ["Chuck Rock", "Chuck Rock II - Son of Chuck"]) == "Chuck Rock"
    assert cat.norm("Lion King, The (World)") == cat.norm("The Lion King")


def _catalog(tmp_path):
    import json
    c = tmp_path / "catalogo.json"
    c.write_text(json.dumps({"jogos": [
        dict(id="golden-axe", nome="Golden Axe", regiao="W", tipo="Oficial", franquia="Golden Axe",
             arquivos=["Golden Axe (World) (Rev A)"]),
        dict(id="altered-beast", nome="Altered Beast", regiao="U,E", tipo="Oficial", franquia="",
             arquivos=["Altered Beast (USA, Europe)"]),
    ]}), encoding="utf-8")
    return cat.Catalog(c, tmp_path / "status.json")


def test_find_link_and_record(tmp_path):
    c = _catalog(tmp_path)
    rom = dict(serial="GM 00005401-00", checksum="C17B", titulo="fnkcdm@`wd", arquivo="ga.md")
    assert c.find(rom) == (None, "")                                    # título ilegível, nome diferente
    by_file = dict(rom, arquivo="Altered Beast (USA, Europe).md")
    assert c.find(by_file) == ("altered-beast", "arquivo")
    by_name = dict(rom, arquivo="altered_beast.bin")
    assert c.find(by_name) == ("altered-beast", "nome")                 # só sugestão
    c.link("golden-axe", rom)                                           # a pessoa escolheu na lista
    assert c.find(rom) == ("golden-axe", "vinculo")
    c.record("golden-axe", "analise_ok", info=rom)
    assert c.status("golden-axe") == cat.AMARELO
    c.mark("golden-axe", cat.VERDE)
    c.record("golden-axe", "perfil", info=rom)                          # não desfaz o verde marcado à mão
    assert c.status("golden-axe") == cat.VERDE
    c.record("altered-beast", "analise_falhou", "o 68000 não escreveu na RAM do Z80")
    assert c.status("altered-beast") == cat.VERMELHO
    again = cat.Catalog(c.catalog_path, c.state_path)                   # ficou gravado
    assert again.status("golden-axe") == cat.VERDE and again.find(rom)[1] == "vinculo"


def test_add_game_and_export_csv(tmp_path):
    c = _catalog(tmp_path)
    gid = c.add_game("Jogo Que Não Estava")
    c.set_obs(gid, "tela preta no boot")
    c.link("golden-axe", dict(serial="GM 00005401-00", checksum="C17B", titulo="", arquivo="ga.md"))
    c.mark("golden-axe", cat.VERDE)
    out = tmp_path / "x.csv"
    n = c.export_csv(out)
    rows = list(csv.reader(open(out, encoding="utf-8-sig"), delimiter=";"))
    assert n == 2 and rows[0][0] == "nome"
    ga = next(r for r in rows if r[0] == "Golden Axe")
    assert ga[4] == "verde" and ga[8] == "GM 00005401-00" and ga[9] == "C17B"
    assert any(r[0] == "Jogo Que Não Estava" and r[6] == "tela preta no boot" for r in rows)
    assert c.export_csv(out, only_marked=False) == 3


def test_shipped_catalog_loads():
    c = cat.Catalog(state_path="/nonexistent/status.json")
    assert len(c.games) > 1000
    assert any(g["nome"] == "Streets of Rage 2" for g in c.games)
