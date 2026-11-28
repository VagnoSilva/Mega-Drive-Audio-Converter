"""Catálogo de compatibilidade dos jogos de Mega Drive.

O catálogo (``perfis_megadrive/catalogo_megadrive.json``, vem com o projeto) lista os jogos. O que cada
pessoa marca — funcionou (verde), não funcionou (vermelho), analisado mas falta ouvir (amarelo), não
testado (cinza) — e as ROMs que ela vinculou a cada jogo ficam na pasta de usuário
(``catalogo_status.json``), para uma atualização do conversor não apagar nada. ``export_csv`` gera a
planilha que o usuário manda para quem mantém o conversor.
"""
from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from pathlib import Path

CINZA, AMARELO, VERDE, VERMELHO = "cinza", "amarelo", "verde", "vermelho"
STATUS = (CINZA, AMARELO, VERDE, VERMELHO)
STATUS_TEXT = {CINZA: "não testado", AMARELO: "analisado, falta ouvir", VERDE: "funciona",
               VERMELHO: "não funciona"}

REGIONS = {"world": "W", "usa": "U", "europe": "E", "japan": "J", "brazil": "B", "korea": "K",
           "asia": "A", "taiwan": "T", "china": "C", "australia": "E", "france": "E", "germany": "E",
           "spain": "E", "italy": "E", "sweden": "E", "uk": "E", "russia": "E", "canada": "U"}
REGION_ORDER = "WUEJBKATC"

# franquias que o nome sozinho não entrega (ou que mudam de nome entre regiões)
KNOWN = [
    (r"^(sonic|knuckles|tails|dr\. robotnik)", "Sonic"),
    (r"streets of rage|bare knuckle", "Streets of Rage"),
    (r"mortal kombat|^mk5", "Mortal Kombat"),
    (r"street fighter", "Street Fighter"),
    (r"thunder force", "Thunder Force"),
    (r"golden axe", "Golden Axe"),
    (r"shinobi", "Shinobi"),
    (r"phantasy star", "Phantasy Star"),
    (r"castlevania|vampire killer", "Castlevania"),
    (r"contra|probotector", "Contra"),
    (r"ecco", "Ecco the Dolphin"),
    (r"toejam", "ToeJam & Earl"),
    (r"vectorman", "Vectorman"),
    (r"earthworm jim", "Earthworm Jim"),
    (r"road rash", "Road Rash"),
    (r"shining", "Shining"),
    (r"wonder boy|monster world", "Wonder Boy"),
    (r"alex kidd", "Alex Kidd"),
    (r"teenage mutant|tmnt", "Tartarugas Ninja"),
    (r"spider-?man", "Homem-Aranha"),
    (r"batman", "Batman"),
    (r"x-men|wolverine", "X-Men"),
    (r"jurassic park", "Jurassic Park"),
    (r"bomberman", "Bomberman"),
    (r"puyo puyo|mean bean", "Puyo Puyo"),
    (r"columns", "Columns"),
    (r"(desert|jungle|urban) strike", "Strike"),
    (r"mickey|donald|quackshot|goofy|fantasia|world of illusion", "Disney"),
    (r"aladdin|lion king|jungle book|little mermaid|pocahontas|toy story", "Disney"),
    (r"^(nba|nfl|nhl|fifa|madden|pga|nhlpa|ncaa|mlbpa|world series baseball)", "Esportes"),
    (r"langrisser|warsong", "Langrisser"),
    (r"valis", "Valis"),
    (r"splatterhouse", "Splatterhouse"),
    (r"rocket knight|sparkster", "Rocket Knight"),
    (r"gunstar", "Gunstar Heroes"),
    (r"pac-?man", "Pac-Man"),
    (r"lemmings", "Lemmings"),
    (r"super monaco", "Super Monaco GP"),
    (r"out ?run", "OutRun"),
    (r"after burner", "After Burner"),
    (r"zero wing|truxton|tatsujin", "Toaplan"),
    (r"power rangers", "Power Rangers"),
    (r"dragon ball", "Dragon Ball"),
    (r"yu yu hakusho", "Yu Yu Hakusho"),
    (r"samurai shodown|samurai spirits", "Samurai Shodown"),
    (r"fatal fury|garou densetsu", "Fatal Fury"),
    (r"king of (the )?fighters", "King of Fighters"),
    (r"art of fighting|ryuuko no ken", "Art of Fighting"),
]

KIND_ORDER = ("Oficial", "Tradução", "Hack", "Protótipo", "Não oficial", "Homebrew", "Multijogos", "Extra")


# ----------------------------------------------------------------------------- nomes
def clean_title(stem: str) -> str:
    """Nome do jogo sem as marcações No-Intro: "Lion King, The (World) (Rev A)" → "Lion King, The"."""
    s = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", stem)
    s = s.replace("_", " ")
    return " ".join(s.split()).strip(" -")


def regions_of(stem: str) -> list[str]:
    out = []
    for grp in re.findall(r"\(([^)]*)\)", stem):
        for part in grp.split(","):
            r = REGIONS.get(part.strip().lower())
            if r and r not in out:
                out.append(r)
    return out


def kind_of(stem: str, rel: str = "") -> str:
    s, top = stem.lower(), rel.replace("\\", "/").split("/")[0].lower()
    if "(beta" in s or "(proto" in s or "(demo" in s or "(sample" in s or "prototype" in top:
        return "Protótipo"
    if "tradu" in top or "[t+" in s or "[t-" in s or "(traduzido" in s:
        return "Tradução"
    if "(hack" in s or "[h" in s:
        return "Hack"
    if "(unl)" in s or "(pirate" in s or "(bootleg" in s:
        return "Não oficial"
    if "novos" in top or "homebrew" in top or "(homebrew" in s:
        return "Homebrew"
    if "multi" in top:
        return "Multijogos"
    if "extra" in top:
        return "Extra"
    return "Oficial"


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def norm(s: str) -> str:
    """Forma para comparar nomes: sem marcações, sem acento, sem "The", só letras e números."""
    s = _ascii(clean_title(s)).lower()
    s = re.sub(r",\s*the\b", "", s)
    s = re.sub(r"^the\s+", "", s)
    s = s.replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", s)


def catalog_key(name: str, kind: str = "Oficial") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _ascii(name).lower()).strip("-") or "jogo"
    if kind != "Oficial":
        slug += "--" + re.sub(r"[^a-z0-9]+", "-", _ascii(kind).lower()).strip("-")
    return slug


def _base(name: str) -> str:
    b = re.split(r"\s+-\s+|:\s", name)[0]
    b = re.sub(r",\s*The$", "", b)
    b = re.sub(r"\s+(\d+|[IVX]+|II|III|IV)(\s.*)?$", "", b)
    b = re.sub(r"\s+'?\d\d$", "", b)                  # NBA Live '95 → NBA Live
    return b.strip()


def detect_franchise(name: str, titles: list[str] | None = None) -> str:
    low = _ascii(name).lower()
    for pat, fr in KNOWN:
        if re.search(pat, low):
            return fr
    if titles:
        b = _base(name)
        if len(b) >= 4:
            same = {t for t in titles if _base(t).lower() == b.lower()}
            if len(same) >= 2:
                return b
    return ""


# ----------------------------------------------------------------------------- arquivos
def catalog_file() -> Path:
    from mdconv.mdanalyze import project_profiles_dir
    return project_profiles_dir() / "catalogo_megadrive.json"


def state_file() -> Path:
    from mdconv.mdanalyze import profiles_dir
    return profiles_dir() / "catalogo_status.json"


def rom_info(path: str) -> dict:
    """Código do cartucho, checksum e título da ROM (para vincular e para o CSV)."""
    from mdconv.megadrive import _read_rom, header
    d = _read_rom(path)
    h = header(d)
    return dict(serial=h["serial"], checksum=f"{h['checksum']:04X}",
                titulo=" ".join((h["overseas"] or h["domestic"]).split()), arquivo=Path(path).name)


class Catalog:
    def __init__(self, catalog_path: str | Path | None = None, state_path: str | Path | None = None):
        self.catalog_path = Path(catalog_path) if catalog_path else catalog_file()
        self.state_path = Path(state_path) if state_path else state_file()
        try:
            base = json.loads(self.catalog_path.read_text(encoding="utf-8")).get("jogos", [])
        except (OSError, ValueError):
            base = []
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("jogos", {})
        self.state.setdefault("vinculos", {})
        self.state.setdefault("extras", [])
        self.games: list[dict] = [dict(g, extra=False) for g in base]
        self.games += [dict(g, extra=True) for g in self.state["extras"]]
        self.by_id = {g["id"]: g for g in self.games}
        self._by_file = {}
        self._by_name = {}
        for g in self.games:
            for a in g.get("arquivos", ()):
                self._by_file.setdefault(a.lower(), g["id"])
            self._by_name.setdefault(norm(g["nome"]), g["id"])
        self._profile_names = None

    # ---------------------------------------------------------------- estado
    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    def info(self, gid: str) -> dict:
        return self.state["jogos"].get(gid, {})

    def status(self, gid: str) -> str:
        return self.info(gid).get("status", CINZA)

    def _entry(self, gid: str) -> dict:
        return self.state["jogos"].setdefault(gid, {})

    def set_status(self, gid: str, status: str) -> None:
        if status not in STATUS:
            raise ValueError(status)
        e = self._entry(gid)
        e["status"] = status
        e["quando"] = time.strftime("%Y-%m-%d %H:%M")
        self.save()

    def set_obs(self, gid: str, obs: str) -> None:
        self._entry(gid)["obs"] = obs.strip()
        self.save()

    def add_game(self, nome: str, regiao: str = "", tipo: str = "Oficial") -> str:
        nome = " ".join(nome.split())
        if not nome:
            raise ValueError("nome vazio")
        gid = "usuario--" + catalog_key(nome, tipo)
        if gid not in self.by_id:
            g = dict(id=gid, nome=nome, regiao=regiao, tipo=tipo, franquia=detect_franchise(nome), arquivos=[])
            self.state["extras"].append(g)
            g = dict(g, extra=True)
            self.games.append(g)
            self.by_id[gid] = g
            self._by_name.setdefault(norm(nome), gid)
            self.save()
        return gid

    def remove_game(self, gid: str) -> None:
        """Só jogos que o usuário adicionou."""
        g = self.by_id.get(gid)
        if not g or not g.get("extra"):
            return
        self.state["extras"] = [x for x in self.state["extras"] if x["id"] != gid]
        self.games = [x for x in self.games if x["id"] != gid]
        del self.by_id[gid]
        self.state["jogos"].pop(gid, None)
        self.state["vinculos"] = {k: v for k, v in self.state["vinculos"].items() if v != gid}
        self.save()

    # ---------------------------------------------------------------- ROM ↔ jogo
    @staticmethod
    def rom_key(info: dict) -> str:
        return f"{info.get('serial', '')}|{info.get('checksum', '')}"

    def link(self, gid: str, info: dict) -> None:
        """Esta ROM (código + checksum) é este jogo: guarda o vínculo e os dados da ROM no jogo."""
        key = self.rom_key(info)
        old = self.state["vinculos"].get(key)
        if old and old != gid:
            e = self._entry(old)
            e["roms"] = [r for r in e.get("roms", []) if self.rom_key(r) != key]
        self.state["vinculos"][key] = gid
        e = self._entry(gid)
        roms = [r for r in e.get("roms", []) if self.rom_key(r) != key]
        roms.append({k: info.get(k, "") for k in ("serial", "checksum", "titulo", "arquivo")})
        e["roms"] = roms
        self.save()

    def unlink(self, gid: str) -> None:
        self.state["vinculos"] = {k: v for k, v in self.state["vinculos"].items() if v != gid}
        self._entry(gid).pop("roms", None)
        self.save()

    def find(self, info: dict) -> tuple[str | None, str]:
        """Jogo desta ROM: ("id", "vinculo") se o usuário já vinculou; ("id", "arquivo") pelo nome
        exato do arquivo; ("id", "nome") só uma sugestão pelo nome; (None, "") se nada."""
        gid = self.state["vinculos"].get(self.rom_key(info))
        if gid in self.by_id:
            return gid, "vinculo"
        stem = Path(info.get("arquivo", "")).stem
        gid = self._by_file.get(stem.lower())
        if gid:
            return gid, "arquivo"
        for cand in (stem, info.get("titulo", "")):
            n = norm(cand)
            if len(n) >= 3 and n in self._by_name:
                return self._by_name[n], "nome"
        return None, ""

    def record(self, gid: str, event: str, message: str = "", info: dict | None = None) -> None:
        """Resultado automático de abrir/analisar a ROM vinculada a ``gid``:
        ``"perfil"`` (abriu com perfil), ``"analise_ok"``, ``"analise_falhou"``. Nunca desfaz um verde ou
        vermelho que a pessoa marcou à mão, a não ser que a análise falhe agora."""
        e = self._entry(gid)
        st = e.get("status", CINZA)
        if event in ("perfil", "analise_ok"):
            if st in (CINZA, VERMELHO) and not e.get("manual"):
                e["status"] = AMARELO
            e.pop("erro", None)
        elif event == "analise_falhou":
            if st != VERDE or not e.get("manual"):
                e["status"] = VERMELHO
            e["erro"] = message
        e["quando"] = time.strftime("%Y-%m-%d %H:%M")
        if info:
            e["ultima_rom"] = {k: info.get(k, "") for k in ("serial", "checksum", "titulo", "arquivo")}
        self.save()

    def mark(self, gid: str, status: str) -> None:
        """Marcação feita pela pessoa (clique na bolinha / menu)."""
        e = self._entry(gid)
        e["manual"] = status != CINZA
        self.set_status(gid, status)

    # ---------------------------------------------------------------- perfis
    def has_profile(self, g: dict) -> bool:
        if self._profile_names is None:
            from mdconv.megadrive import all_profiles
            names, keys = set(), set()
            for p in all_profiles().values():
                names.add(norm(p.get("title", "")))
                keys.add(f"{p.get('serial', '')}")
            self._profile_names = (names, keys)
        names, keys = self._profile_names
        if norm(g["nome"]) in names:
            return True
        for r in self.info(g["id"]).get("roms", []):
            if any(r.get("serial", "").startswith(k) for k in keys if k):
                return True
        return False

    def refresh_profiles(self) -> None:
        self._profile_names = None

    # ---------------------------------------------------------------- contagem / CSV
    def counts(self) -> dict:
        c = {s: 0 for s in STATUS}
        for g in self.games:
            c[self.status(g["id"])] += 1
        return c

    def export_csv(self, path: str | Path, only_marked: bool = True) -> int:
        """Planilha (separada por ;, abre direto no Excel em português). Devolve quantas linhas."""
        rows = 0
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["nome", "regiao", "tipo", "franquia", "status", "situacao", "observacao", "erro_da_analise",
                        "codigo_cartucho", "checksum", "titulo_no_cartucho", "arquivo_rom", "tem_perfil",
                        "marcado_em"])
            for g in sorted(self.games, key=lambda g: g["nome"].lower()):
                e = self.info(g["id"])
                st = e.get("status", CINZA)
                if only_marked and st == CINZA and not e.get("roms") and not e.get("obs"):
                    continue
                roms = e.get("roms") or ([e["ultima_rom"]] if e.get("ultima_rom") else [{}])
                for r in roms:
                    w.writerow([g["nome"], g.get("regiao", ""), g.get("tipo", ""), g.get("franquia", ""), st,
                                STATUS_TEXT[st], e.get("obs", ""), e.get("erro", ""), r.get("serial", ""),
                                r.get("checksum", ""), r.get("titulo", ""), r.get("arquivo", ""),
                                "sim" if self.has_profile(g) else "não", e.get("quando", "")])
                    rows += 1
        return rows

    def franchises(self) -> list[str]:
        return sorted({g.get("franquia", "") for g in self.games if g.get("franquia")}, key=str.lower)

    def kinds(self) -> list[str]:
        ks = {g.get("tipo", "") for g in self.games if g.get("tipo")}
        return [k for k in KIND_ORDER if k in ks] + sorted(ks - set(KIND_ORDER))
