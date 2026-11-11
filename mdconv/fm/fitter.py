"""Busca de patches YM2612 que aproximem um instrumento do SF2.

Etapas (o emulador do chip está no laço em todas):
A. Envelope das portadoras: ataque, decaimento, sustentação, release ajustados a uma
   onda senoidal para casar o envelope RMS do original.
B. Triagem de algoritmos: patches aleatórios plausíveis nos 8 algoritmos; ficam os
   mais promissores.
C. Evolução diferencial (scipy, parâmetros inteiros) por algoritmo escolhido: oitava de
   base, feedback, multiplicadores, nível e envelope dos moduladores, nível relativo
   das portadoras extras.
D. Refinamento local coordenada a coordenada (todos os parâmetros, inclusive detune,
   key-scale e envelopes individuais), depois reavaliação em todas as notas de
   referência; retorna os N primeiros (estruturas diferentes) para escuta.

Limitações atuais (declaradas): na evolução, detune/key-scale ficam em 0 e as portadoras
compartilham um envelope (o refinamento local libera esses parâmetros); AM/LFO não
usados; velocity tratada depois, pelo driver.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import differential_evolution

from mdconv.fm.metrics import Cost, Descriptor, describe, distance
from mdconv.fm.patch import CARRIERS, Operator, Patch, render_note

RELEASE_CAP_S = 0.8


@dataclass
class RefNote:
    hz: float
    hold_s: float
    audio: np.ndarray
    sr: int
    desc: Descriptor = field(init=False)

    def __post_init__(self):
        self.desc = describe(self.audio, self.sr, self.hz, max_s=self.hold_s + RELEASE_CAP_S)


def eval_patch(patch: Patch, refs: list[RefNote]) -> Cost:
    from mdconv.chips.emulator import NTSC_FM_CLOCK
    tot = spec = env = air = 0.0
    for r in refs:
        x = render_note(patch, r.hz, r.hold_s, sr_out=None, max_release_s=RELEASE_CAP_S)
        d = describe(x, NTSC_FM_CLOCK / 144.0, r.hz, max_s=r.hold_s + RELEASE_CAP_S)
        c = distance(r.desc, d)
        tot += c.total
        spec += c.spectrum
        env += c.envelope
        air += c.air
    n = len(refs)
    return Cost(tot / n, spec / n, env / n, air / n)


# ------------------------------------------------------------------ etapa A
ENV_KEYS = ("ar", "d1r", "sl", "d2r", "rr")
ENV_BOUNDS = [(1, 31), (0, 31), (0, 15), (0, 31), (0, 15)]


def _sine_patch(env: dict) -> Patch:
    ops = [Operator(tl=127), Operator(tl=127), Operator(tl=127), Operator(mul=1, tl=0, **env)]
    return Patch(alg=7, ops=ops)


class _EnvObjective:
    def __init__(self, refs):
        self.refs = refs

    def __call__(self, v):
        env = {k: int(round(x)) for k, x in zip(ENV_KEYS, v)}
        return eval_patch(_sine_patch(env), self.refs).envelope


def fit_carrier_envelope(refs: list[RefNote], seed: int = 0, workers: int = 1) -> dict:
    res = differential_evolution(_EnvObjective(refs), ENV_BOUNDS, integrality=[True] * 5,
                                 popsize=6, maxiter=20, tol=0.01, seed=seed, polish=False,
                                 workers=workers, updating="deferred" if workers != 1 else "immediate")
    return {k: int(round(x)) for k, x in zip(ENV_KEYS, res.x)}


# ------------------------------------------------------------------ codificação dos patches
class Layout:
    """Mapeia um vetor de inteiros ↔ Patch para um algoritmo, com envelope de portadora fixo."""

    def __init__(self, alg: int, carrier_env: dict, allow_octave: bool = True,
                 max_fb: int = 7, detune: bool = False, carrier_min_mul: int = 0,
                 mod_tl_min: int = 0):
        self.alg = alg
        self.carrier_env = carrier_env
        self.car = CARRIERS[alg]
        self.mods = [i for i in range(4) if i not in self.car]
        self.bounds: list[tuple[int, int]] = []
        self.names: list[str] = []

        def add(name, lo, hi):
            self.names.append(name)
            self.bounds.append((lo, hi))
        add("octave", -2 if allow_octave else 0, 0)
        add("fb", 0, max_fb)
        for i in range(4):
            # MUL 0 (meia frequência) numa portadora soa uma oitava abaixo: pode virar "zumbido"
            add(f"mul{i}", carrier_min_mul if i in self.car else 0, 15)
        if detune:
            # detune entre portadoras = efeito de coro (ensembles/strings) sem ruído
            for i in range(4):
                add(f"dt{i}", -3, 3)
        for i in self.mods:
            add(f"tl{i}", mod_tl_min, 100)          # TL alto = modulação mais contida
            for k, b in zip(ENV_KEYS, ENV_BOUNDS):
                add(f"{k}{i}", *b)
        for i in self.car[1:]:
            add(f"tl{i}", 0, 127)        # 127 = portadora muda (o algoritmo vira "menor")

    def decode(self, v) -> Patch:
        d = {n: int(round(x)) for n, x in zip(self.names, v)}
        ops = []
        for i in range(4):
            dt = d.get(f"dt{i}", 0)
            if i in self.car:
                ops.append(Operator(mul=d[f"mul{i}"], dt=dt, tl=d.get(f"tl{i}", 0), **self.carrier_env))
            else:
                ops.append(Operator(mul=d[f"mul{i}"], dt=dt, tl=d[f"tl{i}"],
                                    **{k: d[f"{k}{i}"] for k in ENV_KEYS}))
        return Patch(alg=self.alg, fb=d["fb"], ops=ops, octave=d["octave"]).validate()

    def encode(self, p: Patch) -> np.ndarray:
        v = []
        for n in self.names:
            if n == "octave":
                v.append(p.octave)
            elif n == "fb":
                v.append(p.fb)
            else:
                key, i = n[:-1], int(n[-1])
                v.append(getattr(p.ops[i], key))
        return np.asarray(v, dtype=float)

    def random(self, rng: np.random.Generator) -> np.ndarray:
        v = np.array([rng.integers(lo, hi + 1) for lo, hi in self.bounds], dtype=float)
        # moduladores com nível moderado são mais plausíveis que o espaço uniforme
        for j, n in enumerate(self.names):
            if n.startswith("tl") and int(n[2:]) in self.mods:
                lo, hi = self.bounds[j]
                v[j] = rng.integers(max(lo, 10), max(max(lo, 10) + 1, min(hi, 70)))
            if n.startswith("mul"):
                lo, hi = self.bounds[j]
                v[j] = max(lo, rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6, 0, 7, 8]))
            if n == "octave":
                v[j] = rng.choice([0, 0, 0, 0, -1, -2])
        return v


class _SpecObjective:
    def __init__(self, layout, refs):
        self.layout = layout
        self.refs = refs

    def __call__(self, v):
        return eval_patch(self.layout.decode(v), self.refs).total


# ------------------------------------------------------------------ etapa D: refinamento local
OP_FIELDS = {"mul": (0, 15), "tl": (0, 127), "ar": (1, 31), "d1r": (0, 31), "sl": (0, 15),
             "d2r": (0, 31), "rr": (0, 15), "ks": (0, 3), "dt": (-3, 3)}


def polish(patch: Patch, refs: list[RefNote], max_evals: int = 600, max_fb: int = 7,
           carrier_min_mul: int = 0, mod_tl_min: int = 0) -> tuple[Patch, Cost, int]:
    """Busca local coordenada a coordenada sobre TODOS os parâmetros (inclusive envelopes
    individuais das portadoras, detune e key-scale, que a evolução mantém fixos).
    Para MUL testa os 16 valores; para os outros, passos ±1, ±2, ±4, ±8."""
    best = patch.copy()
    best_c = eval_patch(best, refs)
    evals = 1
    def bounds(i, f, lo, hi):
        if f == "mul" and i in patch.carriers:
            lo = max(lo, carrier_min_mul)
        if f == "tl" and i not in patch.carriers:
            lo = max(lo, mod_tl_min)
        return lo, hi
    params = [(None, "fb", 0, max_fb)] + [(i, f) + bounds(i, f, lo, hi)
                                          for i in range(4) for f, (lo, hi) in OP_FIELDS.items()]
    improved = True
    while improved and evals < max_evals:
        improved = False
        for op, field_, lo, hi in params:
            if evals >= max_evals:
                break
            cur = getattr(best, field_) if op is None else getattr(best.ops[op], field_)
            if field_ == "mul":
                tries = [v for v in range(lo, 16) if v != cur]
            else:
                tries = sorted({min(hi, max(lo, cur + d)) for d in (-8, -4, -2, -1, 1, 2, 4, 8)} - {cur})
            for v in tries:
                cand = best.copy()
                if op is None:
                    setattr(cand, field_, v)
                else:
                    setattr(cand.ops[op], field_, v)
                c = eval_patch(cand, refs)
                evals += 1
                if c.total < best_c.total - 1e-3:
                    best, best_c, improved = cand, c, True
                    cur = v
    return best, best_c, evals


@dataclass
class Candidate:
    patch: Patch
    cost: Cost
    stage: str


@dataclass
class FitResult:
    carrier_env: dict
    candidates: list[Candidate]
    screening: dict[int, float]
    seconds: float
    log: list[str] = field(default_factory=list)


def fit_instrument(search_refs: list[RefNote], final_refs: list[RefNote] | None = None,
                   n_screen: int = 60, top_algs: int = 2, de_iter: int = 25, de_pop: int = 5,
                   n_results: int = 3, seed: int = 0, workers: int = 1, polish_evals: int = 600,
                   max_fb: int = 7, detune: bool = False, carrier_min_mul: int = 0,
                   mod_tl_min: int = 0,
                   progress=lambda s: None) -> FitResult:
    t0 = time.time()
    rng = np.random.default_rng(seed)
    final_refs = final_refs or search_refs
    log: list[str] = []

    progress("envelope das portadoras")
    cenv = fit_carrier_envelope(search_refs, seed, workers)
    log.append(f"envelope das portadoras: {cenv}")

    progress("triagem dos 8 algoritmos")
    screen: dict[int, tuple[float, np.ndarray]] = {}
    for alg in range(8):
        lay = Layout(alg, cenv, max_fb=max_fb, detune=detune, carrier_min_mul=carrier_min_mul,
                     mod_tl_min=mod_tl_min)
        best = (np.inf, None)
        for _ in range(n_screen):
            v = lay.random(rng)
            c = eval_patch(lay.decode(v), search_refs).total
            if c < best[0]:
                best = (c, v)
        screen[alg] = best
    ranked = sorted(screen, key=lambda a: screen[a][0])
    log.append("triagem: " + ", ".join(f"alg{a}={screen[a][0]:.2f}" for a in ranked))

    pool: list[Candidate] = []
    for alg in ranked[:top_algs]:
        progress(f"evolução do algoritmo {alg}")
        lay = Layout(alg, cenv, max_fb=max_fb, detune=detune, carrier_min_mul=carrier_min_mul,
                     mod_tl_min=mod_tl_min)
        res = differential_evolution(
            _SpecObjective(lay, search_refs), lay.bounds, integrality=[True] * len(lay.bounds),
            popsize=de_pop, maxiter=de_iter, tol=0.0, atol=0.0, seed=int(rng.integers(1 << 30)),
            x0=screen[alg][1], polish=False, workers=workers,
            updating="deferred" if workers != 1 else "immediate", init="latinhypercube")
        p = lay.decode(res.x)
        progress(f"refinamento local do algoritmo {alg}")
        p, pc, n_pol = polish(p, search_refs, max_evals=polish_evals, max_fb=max_fb,
                              carrier_min_mul=carrier_min_mul, mod_tl_min=mod_tl_min)
        pool.append(Candidate(p, eval_patch(p, final_refs), f"DE+refino alg{alg}"))
        log.append(f"alg{alg}: evolução {res.fun:.2f} ({res.nfev} aval.) → refino {pc.total:.2f} "
                   f"({n_pol} aval.)")
        # também guarda o melhor da triagem, como alternativa de outra "família"
        ps = lay.decode(screen[alg][1])  # (já respeita os limites do Layout)
        pool.append(Candidate(ps, eval_patch(ps, final_refs), f"triagem alg{alg}"))

    pool.sort(key=lambda c: c.cost.total)
    chosen: list[Candidate] = []
    for c in pool:
        if all(np.abs(c.patch.to_vector() - o.patch.to_vector()).sum() > 3 for o in chosen):
            chosen.append(c)
        if len(chosen) == n_results:
            break
    return FitResult(cenv, chosen, {a: screen[a][0] for a in ranked}, time.time() - t0, log)


# ------------------------------------------------------------------ patches de partida
def normalize_carrier_level(patch: Patch) -> Patch:
    """Patches de jogos vêm com o volume embutido no TL das portadoras. O driver controla
    o volume pela velocity/CC7 somando atenuação ao TL, então a portadora mais forte vai a
    TL 0 (as outras mantêm a diferença entre si)."""
    p = patch.copy()
    car = list(p.carriers)
    m = min(p.ops[i].tl for i in car)
    for i in car:
        p.ops[i].tl -= m
    return p


def fit_from_bank(bank, search_refs: list[RefNote], final_refs: list[RefNote] | None = None,
                  k: int = 40, n_polish: int = 3, n_results: int = 3, polish_evals: int = 400,
                  max_fb: int = 7, carrier_min_mul: int = 0, mod_tl_min: int = 0,
                  progress=lambda s: None) -> list[Candidate]:
    """Candidatos a partir do banco de patches prontos:
    1. pré-seleção rápida pelos descritores guardados no banco (perfil + envelope),
       testando oitavas;
    2. avaliação completa (mesma medida do ajuste) dos ``k`` pré-selecionados;
    3. os ``n_polish`` melhores passam pelo refinamento local, partindo do patch original.
    Devolve, intercalados, o original (só com volume normalizado) e o refinado de cada um,
    para o ouvido comparar; ``stage`` diz a origem."""
    from mdconv.library.bank import descriptor
    final_refs = final_refs or search_refs
    profs, envs = [], []
    for r in search_refs:
        p, e = descriptor(r.audio, r.sr, r.hz)
        profs.append(p)
        envs.append(e)
    matches = bank.nearest(np.mean(profs, 0), np.mean(envs, 0), k=k, max_fb=max_fb)
    progress(f"banco: {len(matches)} pré-selecionados de {len(bank)}")
    scored = []
    for m in matches:
        p = normalize_carrier_level(m.patch)
        if any(p.ops[i].mul < carrier_min_mul for i in p.carriers):
            continue
        scored.append((eval_patch(p, search_refs).total, m, p))
    scored.sort(key=lambda t: t[0])
    out: list[Candidate] = []
    for c0, m, p in scored[:n_polish]:
        tag = f"banco: {m.source}" + (f" (oitava {m.octave:+d})" if m.octave else "")
        progress(f"refinando {m.source} (custo {c0:.2f})")
        pp, _, _ = polish(p, search_refs, max_evals=polish_evals, max_fb=max_fb,
                          carrier_min_mul=carrier_min_mul, mod_tl_min=mod_tl_min)
        orig = Candidate(p, eval_patch(p, final_refs), tag)
        ref = Candidate(pp, eval_patch(pp, final_refs), tag + " + refino")
        out += [ref, orig]
    out.sort(key=lambda c: c.cost.total)
    chosen: list[Candidate] = []
    for c in out:
        if all(np.abs(c.patch.to_vector() - o.patch.to_vector()).sum() > 3 for o in chosen):
            chosen.append(c)
        if len(chosen) == n_results:
            break
    return chosen
