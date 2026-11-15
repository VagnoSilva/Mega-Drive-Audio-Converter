"""Carregamento de ROMs CPS1 (arquivo .zip do MAME, pasta ou arquivos soltos)."""
import json
import os
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_game_db():
    path = os.path.join(_HERE, "games.json")
    with open(path, "r", encoding="utf-8") as f:
        db = json.load(f)
    games = {g["name"]: g for g in db["games"]}
    # Jogos personalizados do usuário (pasta "jogos_extra" ao lado do executável)
    for extra_dir in _extra_dirs():
        if not os.path.isdir(extra_dir):
            continue
        for fn in os.listdir(extra_dir):
            if fn.lower().endswith(".json"):
                try:
                    with open(os.path.join(extra_dir, fn), "r", encoding="utf-8") as f:
                        g = json.load(f)
                    games[g["name"]] = g
                except Exception:
                    pass
    return games


def _extra_dirs():
    import sys
    dirs = [os.path.join(os.path.dirname(_HERE), "jogos_extra")]
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "jogos_extra"))
    return dirs


class RomSet:
    """Conjunto de arquivos disponíveis (nome em minúsculas -> função que lê os bytes)."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.files = {}
        self.sizes = {}
        self.own = set()      # arquivos do próprio jogo (não de outros zips da pasta)
        if os.path.isdir(self.path):
            self._add_dir(self.path, own=True)
            for fn in sorted(os.listdir(self.path)):
                if fn.lower().endswith(".zip"):
                    self._add_zip(os.path.join(self.path, fn), override=False)
        elif self.path.lower().endswith(".zip"):
            self._add_zip(self.path, override=True, own=True)
            # Clones do MAME guardam só os arquivos diferentes: procura o zip "pai" na mesma pasta.
            folder = os.path.dirname(self.path)
            for fn in sorted(os.listdir(folder)):
                p = os.path.join(folder, fn)
                if fn.lower().endswith(".zip") and os.path.abspath(p) != self.path:
                    self._add_zip(p, override=False)
        else:
            self._add_dir(os.path.dirname(self.path), own=True)

    def _add_dir(self, folder, own=False):
        for fn in os.listdir(folder):
            p = os.path.join(folder, fn)
            if os.path.isfile(p) and not fn.lower().endswith(".zip"):
                key = fn.lower()
                if key not in self.files:
                    self.files[key] = (lambda p=p: open(p, "rb").read())
                    self.sizes[key] = os.path.getsize(p)
                    if own:
                        self.own.add(key)

    def _add_zip(self, zpath, override, own=False):
        try:
            z = zipfile.ZipFile(zpath)
        except (zipfile.BadZipFile, OSError):
            return
        for info in z.infolist():
            if info.is_dir():
                continue
            key = os.path.basename(info.filename).lower()
            if override or key not in self.files:
                self.files[key] = (lambda z=z, n=info.filename: z.read(n))
                self.sizes[key] = info.file_size
                if own:
                    self.own.add(key)
                else:
                    self.own.discard(key)

    def has(self, name):
        return name.lower() in self.files

    def read(self, name):
        return self.files[name.lower()]()


def guess_game(path, games):
    """Descobre o jogo pelo nome do zip/pasta (aceita clones: sf2ua -> sf2)."""
    stem = os.path.splitext(os.path.basename(os.path.normpath(path)))[0].lower()
    if stem in games:
        return stem
    best = None
    for name in games:
        if stem.startswith(name) and (best is None or len(name) > len(best)):
            best = name
    return best


def score_audio_rom(audio, version, tables):
    """Nota de 0 a 1: quanto a ROM parece ter as tabelas do driver CPS1 no endereço esperado."""
    try:
        from .driver import Driver
        d = Driver(audio, version, tables)
        if d.num_seqs < 3 or not d.instruments:
            return 0.0
        # Programa de Z80: começa com DI / JP / LD SP (gráficos e código 68000 não)
        if audio[0] not in (0xF3, 0xC3, 0x31):
            return 0.0
        ptrs = {d.seq_pointer(i) for i in range(d.num_seqs)} - {0}
        if len(ptrs) < 3:
            return 0.0
        good = total = 0
        for i in range(d.num_seqs):
            p = d.seq_pointer(i)
            if p == 0:
                continue
            total += 1
            if not (0x100 <= p < len(audio) - 25):
                continue
            tr = d.track_offsets(p)
            if tr and all(0x100 <= o < len(audio) for _, o in tr):
                good += 1
        return good / total if total else 0.0
    except Exception:
        return 0.0


def load_game_roms(romset, game, audio_override=None, oki_override=None):
    """Retorna (audio_bytes, oki_bytes_ou_None, avisos)."""
    warnings = []
    version, tables = game["version"], int(game["tables"], 16)

    # ---- ROM de código do Z80 (contém músicas e instrumentos FM)
    names = audio_override or game["audio_roms"]
    if all(romset.has(n) for n in names):
        audio = b"".join(romset.read(n) for n in names)
    else:
        audio, best, best_k = None, 0.6, None
        # só arquivos do próprio jogo: os outros zips da pasta são de outros jogos
        cands = sorted(k for k, s in romset.sizes.items()
                       if s in (0x8000, 0x10000, 0x20000) and k in romset.own)
        for k in cands:
            data = romset.read(k)
            sc = score_audio_rom(data, version, tables)
            if sc > best:
                audio, best, best_k = data, sc, k
        if audio is not None:
            warnings.append(f"ROM de som '{', '.join(names)}' não encontrada pelo nome; "
                            f"usando '{best_k}' (detectada pelo conteúdo).")
        else:
            raise FileNotFoundError(
                "ROM de som não encontrada: " + ", ".join(names) +
                "\nSe o jogo for um clone do MAME (ex.: sf2hf), a ROM de som fica no zip do jogo pai "
                "(ex.: sf2ce.zip): deixe os dois zips na mesma pasta."
                "\nArquivos do jogo: " + ", ".join(sorted(romset.own)))

    # ---- ROMs de samples do Oki MSM6295
    names = oki_override or game.get("oki_roms") or []
    oki = None
    if names and all(romset.has(n) for n in names):
        oki = b"".join(romset.read(n) for n in names)
    elif names:
        # Fallback: arquivos de 128 KB com "18"/"19" no nome (padrão das placas CPS1)
        cands = sorted(k for k, s in romset.sizes.items() if s == 0x20000 and k in romset.own)
        pick = [k for k in cands if "18" in k] + [k for k in cands if "19" in k]
        if pick:
            oki = b"".join(romset.read(k) for k in pick[:2])
            warnings.append(f"ROMs do Oki não encontradas pelo nome; usando {', '.join(pick[:2])}.")
        else:
            warnings.append("ROMs de samples (Oki) não encontradas: o SoundFont terá só instrumentos FM.")
    return audio, oki, warnings
