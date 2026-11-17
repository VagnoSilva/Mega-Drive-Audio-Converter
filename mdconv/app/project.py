"""Camada de aplicação: tudo o que a interface faz, sem depender de Qt.

A interface (``mdconv.gui``) só chama estes métodos em uma thread de trabalho e mostra o
resultado; a mesma lógica é testada sem interface gráfica.

Fluxo de uma música::

    p = Project(ProjectSettings(midi=..., sf2=..., library=..., out_dir=...))
    p.load()                         # analisa e monta o arranjo
    p.instruments()                  # tabela de instrumentos e status dos patches
    p.fit((10, 4), progress=...)     # gera candidatos FM (WAVs A/B)
    p.approve((10, 4), rank=1)       # grava o escolhido na biblioteca
    p.render_preview(...)            # prévia da música inteira
    p.export()                       # XGM v1 / VGM, validados
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from mdconv.arrange.loop import LoopPoint, bar_ticks, resolve_loop
from mdconv.arrange.plan import Plan, make_plan, plan_text

PROJECT_SUFFIX = ".mdcproj"
Progress = Callable[[str], None]


class Cancelled(Exception):
    """Levantada pelo callback de progresso quando o usuário cancela."""


def _quiet(_: str) -> None:
    pass


@dataclass
class ProjectSettings:
    midi: str = ""
    sf2: str = ""
    library: str = "biblioteca_patches"
    out_dir: str = "saida"
    name: str = ""                     # nome base dos arquivos (padrão: nome do MIDI)
    use_pcm: bool = True
    pcm_channels: list[int] = field(default_factory=lambda: [1, 2])
    vibrato: bool = True
    noise_metals: bool = False         # chimbais/pratos no ruído do PSG (libera o PCM)
    fm_alloc: str = "auto"             # canais FM: auto | dinamico (nota a nota) | fixo (por parte)
    snap_tempo: bool = False           # andamento travado na grade de quadros (60 Hz)
    trim_start: bool = True            # a música começa na primeira nota (tira o silêncio do início)
    fm_att_db: float | None = None     # None = equilíbrio automático
    loop_mode: str = "auto"            # auto | markers | none
    loop_start_tick: int | None = None # escolhido pelo usuário (sobrepõe a detecção)
    loop_end_tick: int | None = None
    formats: list[str] = field(default_factory=lambda: ["xgm", "vgm"])
    bank: str = ""                     # banco de patches prontos (.tfi importados); vazio = sem
    fm_engine: str = "auto"            # motor da prévia: auto (Nuked) | proprio | nuked
    title: str = ""
    game: str = ""
    # Patches FM travados nesta música: "banco:programa" -> {"preset", "library_id",
    # "approved_at", "patch"}. Têm prioridade sobre a biblioteca, então a música exporta
    # sempre igual mesmo que a biblioteca mude; só são trocados quando o usuário pede.
    pinned_patches: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "ProjectSettings":
        d = json.loads(text)
        # projetos salvos antes desta opção continuam exatamente como eram (com o silêncio)
        d.setdefault("trim_start", False)
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        if path.suffix != PROJECT_SUFFIX:
            path = path.with_suffix(PROJECT_SUFFIX)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "ProjectSettings":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def validate(self) -> list[str]:
        errs = []
        if not self.midi or not Path(self.midi).is_file():
            errs.append("escolha o arquivo MIDI")
        if not self.sf2 or not Path(self.sf2).is_file():
            errs.append("escolha o arquivo SF2")
        if self.use_pcm and not self.pcm_channels:
            errs.append("marque ao menos um canal PCM para a bateria (ou desligue o PCM)")
        if any(c not in (1, 2, 3, 4) for c in self.pcm_channels):
            errs.append("canais PCM vão de 1 a 4")
        if self.loop_mode not in ("auto", "markers", "none"):
            errs.append("modo de loop inválido")
        if self.fm_alloc not in ("auto", "dinamico", "fixo"):
            errs.append(f"modo de canais FM desconhecido: {self.fm_alloc}")
        if self.fm_engine not in ("auto", "proprio", "nuked", "gpgx"):
            errs.append(f"motor de prévia desconhecido: {self.fm_engine}")
        bad = set(self.formats) - {"xgm", "vgm", "fur"}
        if bad:
            errs.append(f"formato desconhecido: {', '.join(sorted(bad))}")
        return errs


@dataclass
class Candidate:
    rank: int
    wav: Path
    cost: float
    summary: str
    approved: bool = False
    rejected: bool = False
    origin: str = ""


@dataclass
class InstrumentRow:
    bank: int
    program: int
    name: str
    kind: str
    channels: list[int]            # canais MIDI 1–16
    notes: int
    roles: list[str]
    pools: list[str]
    status: str                    # aprovado | provisório | bateria (PCM) | sem patch (PSG)
    library_id: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    pinned: bool = False           # patch vem da trava do projeto (não da biblioteca)
    auto: bool = False             # aprovado automaticamente (rascunho, ainda não ouvido)
    chip_use: dict[str, int] = field(default_factory=dict)   # canal do chip → nº de notas (última conversão)

    def chip_text(self) -> str:
        """Ex.: "FM1 70% · FM4 30%". Vazio se ainda não houve conversão."""
        tot = sum(self.chip_use.values())
        if not tot:
            return ""
        order = sorted(self.chip_use.items(), key=lambda kv: (-kv[1], kv[0]))
        txt = " · ".join(f"{k} {round(100 * v / tot)}%" if len(order) > 1 else k for k, v in order[:4])
        return txt + (f" · +{len(order) - 4}" if len(order) > 4 else "")

    @property
    def key(self) -> tuple[int, int]:
        return (self.bank, self.program)

    @property
    def needs_fm(self) -> bool:
        return "FM" in self.pools

    @property
    def label(self) -> str:
        return f"{self.bank}:{self.program} {self.name}"


class Project:
    def __init__(self, settings: ProjectSettings):
        self.settings = settings
        self.song = None
        self.sf = None
        self.plan: Plan | None = None
        self._conversion = None
        self._conv_key = None
        self._raw_song = None          # a música como está no MIDI
        self.trim_ticks = 0            # silêncio removido do início (ticks do MIDI)
        self.trim_seconds = 0.0

    # ---------------------------------------------------------------- caminhos
    @property
    def stem(self) -> str:
        return self.settings.name or Path(self.settings.midi).stem

    @property
    def out_dir(self) -> Path:
        return Path(self.settings.out_dir)

    @property
    def fit_dir(self) -> Path:
        return self.out_dir / "ajustes_fm"

    @property
    def library_dir(self) -> Path:
        return Path(self.settings.library)

    # ---------------------------------------------------------------- carga
    def load(self, progress: Progress = _quiet) -> list[str]:
        from mdconv.io_midi.song_builder import load_song
        from mdconv.io_sf2.sf2 import read_sf2
        errs = [e for e in self.settings.validate() if "MIDI" in e or "SF2" in e]
        if errs:
            raise ValueError("; ".join(errs))
        progress(f"Lendo {Path(self.settings.midi).name}…")
        self._raw_song = load_song(self.settings.midi)
        self.trim_ticks, self.trim_seconds = 0, 0.0
        self.song = self._raw_song
        self._trim_on = False
        self._apply_trim(adjust_loop=False)    # o loop salvo já está na régua da música cortada
        progress(f"Lendo {Path(self.settings.sf2).name}…")
        self.sf = read_sf2(self.settings.sf2)
        self.invalidate()
        self.replan()
        return list(self.song.warnings)

    def _need(self):
        if self.song is None or self.sf is None:
            raise RuntimeError("abra o MIDI e o SF2 primeiro")

    def _apply_trim(self, adjust_loop: bool = True) -> None:
        """Aplica (ou desfaz) o corte do silêncio inicial conforme ``settings.trim_start``.
        O loop escolhido à mão acompanha o deslocamento."""
        from mdconv.model.trim import trim_leading_silence
        if self._raw_song is None:
            return
        want = bool(self.settings.trim_start)
        if want == getattr(self, "_trim_on", False):
            return
        s = self.settings
        if want:
            self.song, self.trim_ticks, self.trim_seconds = trim_leading_silence(self._raw_song)
            for k in ("loop_start_tick", "loop_end_tick"):
                if adjust_loop and getattr(s, k) is not None:
                    setattr(s, k, max(0, getattr(s, k) - self.trim_ticks))
        else:
            for k in ("loop_start_tick", "loop_end_tick"):
                if adjust_loop and getattr(s, k) is not None:
                    setattr(s, k, getattr(s, k) + self.trim_ticks)
            self.song, self.trim_ticks, self.trim_seconds = self._raw_song, 0, 0.0
        self._trim_on = want

    def replan(self) -> Plan:
        self._need()
        if self._raw_song is not None:
            self._apply_trim()
        s = self.settings
        self.plan = make_plan(self.song, self.sf, pcm_channels=s.pcm_channels if s.use_pcm else [],
                              use_pcm=s.use_pcm)
        self.invalidate()
        return self.plan

    def invalidate(self) -> None:
        self._conversion = None
        self._conv_key = None

    def plan_text(self) -> str:
        return plan_text(self.plan) if self.plan else ""

    # ---------------------------------------------------------------- loop
    def loop(self) -> LoopPoint | None:
        self._need()
        s = self.settings
        return resolve_loop(self.song, s.loop_mode, s.loop_start_tick, s.loop_end_tick)

    def bar_ticks(self) -> int:
        self._need()
        return bar_ticks(self.song)

    def bar_count(self) -> int:
        self._need()
        return self.song.length_ticks // self.bar_ticks() + 1

    def bar_to_tick(self, bar: int) -> int:
        """Compasso 1 = início do arquivo."""
        return max(0, (bar - 1) * self.bar_ticks())

    def tick_to_bar(self, tick: int) -> float:
        return tick / self.bar_ticks() + 1

    def set_loop_start_bar(self, bar: int | None) -> None:
        """``None`` volta para a detecção automática."""
        self._need()
        if bar is None:
            self.settings.loop_start_tick = None
        else:
            if not 1 <= bar <= self.bar_count():
                raise ValueError(f"compasso de 1 a {self.bar_count()}")
            first = min(n.start_tick for n in self.song.notes)
            tick = self.bar_to_tick(bar)
            # compasso que contém a primeira nota: começa nela (como os marcadores do ripper)
            self.settings.loop_start_tick = first if tick <= first < tick + self.bar_ticks() else tick
        self.invalidate()

    # ---------------------------------------------------------------- instrumentos
    def instruments(self) -> list[InstrumentRow]:
        from mdconv.convert import approved_entries
        self._need()
        if self.plan is None:
            self.replan()
        approved = approved_entries(self.library_dir, self.sf)
        fits = self._fit_report()
        rows: dict[tuple[int, int], InstrumentRow] = {}
        for p in self.plan.parts:
            for bp in p.programs:
                r = rows.get(bp)
                if r is None:
                    r = rows[bp] = InstrumentRow(bp[0], bp[1], p.preset, p.kind, [], 0, [], [], "")
                r.channels.append(p.channel + 1)
                r.notes += sum(1 for n in self.song.notes
                               if n.track == p.track and n.channel == p.channel and (n.bank, n.program) == bp)
                if p.role not in r.roles:
                    r.roles.append(p.role)
                pool = "FM" if (p.pool == "FM" or p.overflow_psg) else p.pool
                if pool not in r.pools:
                    r.pools.append(pool)
        pins = self.settings.pinned_patches or {}
        from mdconv.convert import apply_pinned
        valid_pins = set(apply_pinned({}, pins, self.sf))
        for r in rows.values():
            r.channels.sort()
            e = approved.get(r.key)
            if r.key in valid_pins:
                pin = pins[f"{r.key[0]}:{r.key[1]}"]
                r.status, r.library_id, r.pinned = "aprovado", pin.get("library_id"), True
                r.auto = bool(pin.get("auto"))
            elif e is not None:
                r.status, r.library_id = "aprovado", e.id
                r.auto = bool(e.source.get("auto"))
            elif r.needs_fm:
                r.status = "provisório"
            elif "PCM" in r.pools:
                r.status = "bateria (PCM)"
            else:
                r.status = "PSG"
            r.candidates = self._candidates_from(fits, r.key)
        for bp, use in self._chip_use().items():
            if bp in rows:
                rows[bp].chip_use = use
        return sorted(rows.values(), key=lambda r: (min(r.channels), r.bank, r.program))

    def _chip_use(self) -> dict:
        """Canais do chip em que o driver tocou cada instrumento, na última conversão (que
        ainda vale para as configurações atuais). O driver escolhe o canal nota a nota."""
        c = self._conversion
        # o patch aprovado não muda a escolha de canais: ignora biblioteca e travas na comparação
        if c is None or c.driver is None or self._conv_key is None or self._conv_key[:-2] != self._key()[:-2]:
            return {}
        use: dict[tuple[int, int], dict[str, int]] = {}
        for ev in c.driver.timeline:
            if ev[0] == "on":
                _, _, typ, idx, n = ev
                name = {"fm": f"FM{idx + 1}", "psg": f"PSG{idx + 1}", "noise": "Ruído"}[typ]
            elif ev[0] == "pcm":
                _, _, ch, n, _ = ev
                name = f"PCM{ch}"
            else:
                continue
            d = use.setdefault((n.bank, n.program), {})
            d[name] = d.get(name, 0) + 1
        return use

    def _fit_report(self) -> dict:
        path = self.fit_dir / "patches.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def _candidates_from(self, rep: dict, key) -> list[Candidate]:
        for e in rep.get("instruments", []):
            if (e["bank"], e["program"]) == tuple(key):
                out = []
                for c in e["candidates"]:
                    p = c["patch"]
                    stage = c.get("stage", "")
                    origin = stage if stage.startswith(("banco", "biblioteca")) else "busca do zero"
                    out.append(Candidate(
                        c["rank"], self.fit_dir / c["wav"], c["cost_total"],
                        f"{origin} · alg {p['alg']} · fb {p['fb']} · MUL {[o['mul'] for o in p['ops']]} · "
                        f"custo {c['cost_total']:.2f}", bool(c.get("approved")),
                        bool(c.get("rejected")), stage or origin))
                return out
        return []

    def candidates(self, key) -> list[Candidate]:
        return self._candidates_from(self._fit_report(), key)

    def patch_bank(self):
        from mdconv.library.bank import PatchBank
        b = self.settings.bank
        if not b or not PatchBank.exists(b):
            return None
        if getattr(self, "_bank", None) is None or self._bank_path != b:
            self._bank, self._bank_path = PatchBank(b), b
        if len(self._bank) == 0:
            return None                    # banco vazio (ex.: pasta sem .tfi) = sem banco
        return self._bank

    def fit(self, key: tuple[int, int], budget: str = "normal", max_fb: int = 5, detune: bool = False,
            progress: Progress = _quiet, use_bank: bool = True, bank_only: bool = False,
            use_library: bool = True, **fit_kw) -> list[Candidate]:
        """Gera candidatos FM para um instrumento. Padrão FB ≤ 5: FB 6–7 deu chiado no fim
        das notas nos testes do song001."""
        from mdconv.fm.instrument_fit import fit_song
        self._need()
        bank = self.patch_bank() if use_bank else None
        if bank_only and bank is None:
            raise ValueError("nenhum banco de patches prontos configurado (ou o banco está vazio: "
                             "a pasta escolhida não tinha arquivos .tfi)")
        rep = fit_song(self.song, self.sf, self.fit_dir, budget=budget, progress=progress,
                       max_fb=max_fb, detune=detune, only={tuple(key)}, bank=bank,
                       bank_only=bank_only,
                       library_dir=self.library_dir if use_library else None, **fit_kw)
        if not any((e["bank"], e["program"]) == tuple(key) for e in rep["instruments"]):
            raise ValueError(f"{key[0]}:{key[1]} não tem notas tonais para ajustar "
                             f"(kit de bateria ou sem preset no SF2)")
        return self.candidates(key)

    def approve(self, key: tuple[int, int], rank: int, auto: bool = False) -> dict:
        from mdconv.fm.instrument_fit import approve
        self._need()
        rep = self._fit_report()
        e = next((e for e in rep.get("instruments", []) if (e["bank"], e["program"]) == tuple(key)), None)
        if e is None:
            raise ValueError("gere os candidatos deste instrumento antes de aprovar")
        r = approve(self.fit_dir, e["label"], rank, self.sf, self.library_dir,
                    extra_source={"auto": True} if auto else None)
        self._decision("rascunho_auto" if auto else "aprovado", key, rank)
        # quem aprovou nesta música quer ouvir o patch novo nela: a trava desta música
        # é atualizada (as outras músicas continuam com as travas delas)
        k = f"{key[0]}:{key[1]}"
        if k in (self.settings.pinned_patches or {}):
            self._pin_from_library([tuple(key)])
        self.invalidate()
        return r

    def auto_approve(self, budget: str = "quick", max_fb: int = 5, use_bank: bool = True,
                     use_library: bool = True, progress: Progress = _quiet) -> list[str]:
        """Rascunho para a música inteira: para cada instrumento FM ainda sem patch aprovado,
        gera os candidatos e aprova o de menor custo, marcado como automático. Quem ouve e
        aprova outro candidato depois substitui o rascunho (vale o aprovado mais recente)."""
        self._need()
        todo = [r for r in self.instruments() if r.needs_fm and r.status == "provisório"]
        done = []
        for i, r in enumerate(todo, 1):
            progress(f"[{i}/{len(todo)}] {r.label}")
            try:
                cands = self.fit(r.key, budget=budget, max_fb=max_fb, progress=progress,
                                 use_bank=use_bank, use_library=use_library)
            except ValueError as e:
                progress(f"    pulado: {e}")
                continue
            cands = [c for c in cands if not c.rejected]
            if not cands:
                continue
            best = min(cands, key=lambda c: c.cost)
            self.approve(r.key, best.rank, auto=True)
            done.append(f"{r.label} → #{best.rank} ({best.summary.split(' · ')[0]}, custo {best.cost:.2f})")
        return done

    # ---------------------------------------------------------------- decisões do usuário
    def reject(self, key: tuple[int, int], rank: int) -> None:
        """O usuário ouviu e não gostou deste candidato."""
        from mdconv.fm.instrument_fit import reject
        e = self._fit_entry(key)
        if e is None:
            raise ValueError("gere os candidatos antes")
        reject(self.fit_dir, e["label"], rank)
        self._decision("recusado", key, rank)

    def listened(self, key: tuple[int, int], rank: int) -> None:
        """Registra que o candidato foi ouvido (dá peso às aprovações e recusas)."""
        self._decision("ouviu", key, rank)

    def _fit_entry(self, key):
        for e in self._fit_report().get("instruments", []):
            if (e["bank"], e["program"]) == tuple(key):
                return e
        return None

    def _decision(self, event: str, key, rank: int | None = None) -> None:
        """Grava a decisão com o contexto que permite aprender com ela depois."""
        from mdconv.library.decisions import record
        try:
            e = self._fit_entry(key) or {}
            cands = e.get("candidates", [])
            c = next((x for x in cands if x["rank"] == rank), None)
            roles = []
            if self.plan is not None:
                for p in self.plan.parts:
                    if tuple(key) in [tuple(bp) for bp in p.programs]:
                        roles.append(p.role)
            def cand_info(x):
                return {"rank": x["rank"], "origem": x.get("stage", ""), "custo": x.get("cost_total"),
                        "custo_espectro": x.get("cost_spectrum_db"),
                        "custo_envelope": x.get("cost_envelope_db"),
                        "recusado": bool(x.get("rejected")), "aprovado": bool(x.get("approved"))}
            record(event,
                   musica=Path(self.settings.midi).name, sf2=Path(self.settings.sf2).name,
                   instrumento={"banco": key[0], "programa": key[1], "preset": e.get("preset"),
                                "tipo": e.get("kind"), "papeis": sorted(set(roles)),
                                "notas": e.get("keys"), "velocity": e.get("velocity")},
                   opcoes_busca=e.get("options", {}),
                   candidato=({**cand_info(c), "patch": c.get("patch")} if c else None),
                   alternativas=[cand_info(x) for x in cands if x is not c])
        except Exception:
            pass                               # o histórico nunca atrapalha o trabalho

    # ---------------------------------------------------------------- patches travados
    def _fm_keys(self) -> list[tuple[int, int]]:
        """(banco, programa) dos instrumentos desta música que tocam no FM."""
        self._need()
        if self.plan is None:
            self.replan()
        keys = []
        for p in self.plan.parts:
            if p.pool == "FM" or p.overflow_psg:
                for bp in p.programs:
                    if bp not in keys:
                        keys.append(bp)
        return keys

    def _pin_from_library(self, keys) -> list[str]:
        from mdconv.convert import approved_entries, pin_key
        approved = approved_entries(self.library_dir, self.sf)
        pins = self.settings.pinned_patches
        done = []
        for key in keys:
            e = approved.get(tuple(key))
            if e is None:
                continue
            pins[pin_key(*key)] = {"preset": (e.source.get("preset") or e.name or "").strip(),
                                   "library_id": e.id, "auto": bool(e.source.get("auto")),
                                   "approved_at": e.source.get("approved_at", ""),
                                   "patch": e.patch.to_dict()}
            done.append(f"{key[0]}:{key[1]} {e.name}")
        self.invalidate()
        return done

    def pin_patches(self) -> list[str]:
        """Trava os patches aprovados que esta música usa e que ainda não estão travados.
        Chamado ao salvar o projeto. Travas existentes nunca são trocadas aqui."""
        from mdconv.convert import pin_key
        pins = self.settings.pinned_patches
        missing = [k for k in self._fm_keys() if pin_key(*k) not in pins]
        return self._pin_from_library(missing)

    def pin_differences(self) -> list[dict]:
        """Instrumentos cujo patch na biblioteca é diferente do travado no projeto."""
        from mdconv.convert import apply_pinned, approved_entries, pin_key
        self._need()
        approved = approved_entries(self.library_dir, self.sf)
        pins = self.settings.pinned_patches or {}
        valid = set(apply_pinned({}, pins, self.sf))
        out = []
        for key in self._fm_keys():
            pin = pins.get(pin_key(*key))
            e = approved.get(key)
            if pin is None or key not in valid or e is None:
                continue
            if e.patch.to_dict() != pin["patch"]:
                out.append({"key": key, "label": f"{key[0]}:{key[1]} {e.name}",
                            "pinned_id": pin.get("library_id"), "pinned_at": pin.get("approved_at", ""),
                            "library_id": e.id, "library_at": e.source.get("approved_at", "")})
        return out

    def update_pins(self, keys=None) -> list[str]:
        """Troca as travas pelos patches atuais da biblioteca (todas as diferentes, ou ``keys``)."""
        if keys is None:
            keys = [d["key"] for d in self.pin_differences()]
        return self._pin_from_library(keys)

    def unpin(self, keys=None) -> None:
        """Remove travas (todas, ou ``keys``): a música volta a seguir a biblioteca."""
        from mdconv.convert import pin_key
        if keys is None:
            self.settings.pinned_patches = {}
        else:
            for k in keys:
                self.settings.pinned_patches.pop(pin_key(*k), None)
        self.invalidate()

    # ---------------------------------------------------------------- conversão
    def _key(self):
        s = self.settings
        return (tuple(s.pcm_channels), s.use_pcm, s.vibrato, s.noise_metals, s.fm_alloc, s.snap_tempo,
                s.trim_start, self.trim_ticks,
                s.fm_att_db, s.loop_mode,
                s.loop_start_tick, s.loop_end_tick, self._library_stamp(),
                json.dumps(s.pinned_patches or {}, sort_keys=True))

    def _library_stamp(self):
        d = self.library_dir
        if not d.exists():
            return ()
        return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in d.glob("*.json")))

    def convert(self, progress: Progress = _quiet):
        from mdconv.convert import convert
        self._need()
        key = self._key()
        if self._conversion is not None and self._conv_key == key:
            return self._conversion
        s = self.settings
        progress("Convertendo (arranjo, patches, bateria, equilíbrio)…")
        c = convert(self.song, self.sf, self.library_dir, use_pcm=s.use_pcm,
                    pcm_channels=s.pcm_channels if s.use_pcm else [], vibrato=s.vibrato,
                    balance="manual" if s.fm_att_db is not None else "auto",
                    fm_att_db=s.fm_att_db or 0.0, loop_mode=s.loop_mode,
                    loop_start_tick=s.loop_start_tick, loop_end_tick=s.loop_end_tick,
                    noise_metals=s.noise_metals, fm_alloc=s.fm_alloc, snap_tempo=s.snap_tempo,
                    pinned=s.pinned_patches)
        self.plan = c.plan
        self._conversion, self._conv_key = c, key
        return c

    def report(self) -> str:
        from mdconv.convert import report_text
        return report_text(self.convert(), None)

    def render_preview(self, path: str | Path | None = None, progress: Progress = _quiet) -> Path:
        """Prévia do trecho exportado (uma passada, até o fim do loop)."""
        from mdconv.export.common import export_frames
        from mdconv.reference.sampler import write_wav
        from mdconv.render.stream_render import render_stream
        c = self.convert(progress)
        progress("Renderizando a prévia…")
        frames, _ = export_frames(c.stream)
        y, info = render_stream(dataclasses.replace(c.stream, frames=frames),
                                fm_engine=self.settings.fm_engine)
        progress(f"FM: {info.fm_engine}")
        path = Path(path) if path else self.out_dir / f"{self.stem}_previa.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_wav(path, y, 44100)
        return path

    def render_seam(self, path: str | Path | None = None, seconds: float = 5.0,
                    progress: Progress = _quiet) -> Path:
        """A emenda do loop como o jogo vai tocar: os últimos ``seconds`` antes do fim e,
        em seguida, os primeiros ``seconds`` a partir do início do loop."""
        from mdconv.export.common import export_frames
        from mdconv.reference.sampler import write_wav
        from mdconv.render.stream_render import render_stream
        c = self.convert(progress)
        frames, loop = export_frames(c.stream)
        if loop is None:
            raise ValueError("esta música está sem loop")
        n = int(round(seconds * c.stream.fps))
        progress("Renderizando a emenda do loop…")
        ext = frames + [list(f) for f in frames[loop:loop + n]]
        y, _ = render_stream(dataclasses.replace(c.stream, frames=ext), normalize=False,
                             fm_engine=self.settings.fm_engine)
        start = int(round(max(0, len(frames) - n) / c.stream.fps * 44100))
        y = y[start:]
        pk = float(np.abs(y).max()) or 1.0
        y = y * (10 ** (-1 / 20) / pk)
        path = Path(path) if path else self.out_dir / f"{self.stem}_emenda_loop.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_wav(path, y, 44100)
        return path

    def export(self, progress: Progress = _quiet, wav: bool = False) -> list[str]:
        from mdconv.convert import report_text
        from mdconv.export.validate import export_and_validate
        errs = self.settings.validate()
        if errs:
            raise ValueError("; ".join(errs))
        c = self.convert(progress)
        progress("Exportando e validando…")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        lines = export_and_validate(c.stream, self.out_dir, self.stem, set(self.settings.formats),
                                    title=self.settings.title or self.stem, game=self.settings.game,
                                    wav=wav, fm_engine=self.settings.fm_engine, conversion=c)
        rows = self.instruments()
        provisional = [r.label for r in rows if r.status == "provisório"]
        n_pin = sum(1 for r in rows if r.pinned)
        n_lib = sum(1 for r in rows if r.status == "aprovado" and not r.pinned)
        lines.append(f"patches FM: {n_pin} travado(s) no projeto, {n_lib} da biblioteca")
        if provisional:
            lines.append("⚠ com patch provisório (ainda sem aprovação): " + ", ".join(provisional))
        lp = c.loop
        lines.append(lp.text() if lp else "sem loop (toca uma vez)")
        if lp and lp.estimated:
            lines.append("⚠ loop estimado: ouça a emenda e, se precisar, escolha o compasso de início")
        (self.out_dir / f"{self.stem}_exportacao.txt").write_text(
            "\n".join(lines) + "\n\n" + report_text(c, None), encoding="utf-8")
        return lines
