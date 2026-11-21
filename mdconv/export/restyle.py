"""Estilo de referência: deixa uma música com as características técnicas de outra.

Ferramentas → Música VGM → "Estilo de um XGM de referência". Da música de referência (XGM ou VGM
do Mega Drive) saem:

* ``samples`` — **volume das amostras**: o pico das amostras passa a ser o da referência. Um ganho
  só para todas, então o equilíbrio entre elas não muda (limitado a 127).
* ``fm_volume`` — **volume do FM fixo**: cada instrumento toca sempre no volume mais forte que já
  usava (as portadoras ficam no TL mais baixo visto com o mesmo patch; os moduladores, no valor
  mais comum). Somem acentos, ecos mais fracos e fades — como num driver que não mexe no volume.
* ``algorithm`` — **algoritmo e feedback** de cada canal FM (o par mais usado na referência).
  Os operadores continuam os da música: muda o timbre (operadores que eram ouvidos podem virar
  moduladores). Canais que a referência não usa ficam como estão.
* ``lfo`` — **LFO**: a frequência global ($22) e a sensibilidade AMS/FMS de cada canal ($B4,
  mantendo o pan da música).

A ordem é algoritmo → volume do FM (as portadoras dependem do algoritmo novo) → LFO → amostras.
"""
from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mdconv.i18n import tr

OPTIONS = {
    "samples": "Volume das amostras",
    "fm_volume": "Volume do FM fixo",
    "algorithm": "Algoritmo e feedback",
    "lfo": "LFO",
}
# operadores ouvidos (portadoras) por algoritmo, pelo deslocamento do registrador: S1=0, S3=4, S2=8, S4=C
CARRIERS = {0: (0xC,), 1: (0xC,), 2: (0xC,), 3: (0xC,), 4: (0x8, 0xC), 5: (0x8, 0x4, 0xC),
            6: (0x8, 0x4, 0xC), 7: (0x0, 0x4, 0x8, 0xC)}


@dataclass
class Style:
    name: str = ""
    sample_peak: int | None = None                      # pico das amostras (1..127)
    algfb: dict = field(default_factory=dict)            # canal 1..6 → (algoritmo, feedback)
    lfo: int | None = None                               # valor de $22
    amsfms: dict = field(default_factory=dict)           # canal 1..6 → bits 0–5 de $B4
    tl_changes: int = 0


def _ch(port: int, reg: int) -> int:
    return port * 3 + (reg & 3) + 1


def style_from_stream(stream, name: str = "") -> Style:
    st = Style(name=name)
    if stream.pcm_samples:
        st.sample_peak = max(int(np.abs(np.asarray(s, dtype=np.int16)).max()) for s in stream.pcm_samples.values()
                             if len(s)) or None
    alg: dict[int, Counter] = defaultdict(Counter)
    ams: dict[int, Counter] = defaultdict(Counter)
    lfo = None
    for f in stream.frames:
        for w in f:
            if w[0] != "fm":
                continue
            port, reg, val = w[1], w[2], w[3]
            if reg == 0x22 and port == 0:
                lfo = val
            elif 0xB0 <= reg <= 0xB2:
                alg[_ch(port, reg)][(val & 7, (val >> 3) & 7)] += 1
            elif 0xB4 <= reg <= 0xB6:
                ams[_ch(port, reg)][val & 0x3F] += 1
            elif 0x40 <= reg <= 0x4F:
                st.tl_changes += 1
    st.algfb = {c: n.most_common(1)[0][0] for c, n in alg.items()}
    st.amsfms = {c: n.most_common(1)[0][0] for c, n in ams.items()}
    st.lfo = lfo
    return st


def read_style(path) -> Style:
    """Estilo de um XGM, ou de um VGM do Mega Drive."""
    path = Path(path)
    from mdconv.vgmconv import detect_chip, read_xgm_stream
    chip = detect_chip(path)
    if chip == "XGM":
        stream, _, _ = read_xgm_stream(path)
    elif chip == "Mega Drive":
        from mdconv import mdvgm
        stream, _ = mdvgm.convert(mdvgm.read_vgm(path))
    else:
        raise ValueError(tr("A referência de estilo tem de ser um XGM ou um VGM do Mega Drive: {0}").format(path.name))
    return style_from_stream(stream, path.name)


def describe(st: Style) -> str:
    alg = ", ".join(f"FM{c} {a}/{fb}" for c, (a, fb) in sorted(st.algfb.items()))
    return tr("{0}: pico das amostras {1}, algoritmo/feedback {2}, LFO {3}").format(
        st.name, st.sample_peak if st.sample_peak else "—", alg or "—",
        tr("ligado") if st.lfo is not None and st.lfo & 8 else tr("desligado"))


def apply_style(stream, st: Style, options) -> tuple:
    """Devolve (fluxo novo, linhas para o relatório). O fluxo original não é alterado."""
    options = set(options)
    frames = [list(f) for f in stream.frames]
    samples = stream.pcm_samples
    lines = []

    if "algorithm" in options and st.algfb:
        n = 0
        for f in frames:
            for i, w in enumerate(f):
                if w[0] == "fm" and 0xB0 <= w[2] <= 0xB2 and _ch(w[1], w[2]) in st.algfb:
                    a, fb = st.algfb[_ch(w[1], w[2])]
                    new = (fb << 3) | a
                    if new != w[3]:
                        f[i] = ("fm", w[1], w[2], new)
                        n += 1
        lines.append(tr("algoritmo/feedback da referência: {0}").format(
            ", ".join(f"FM{c} {a}/{fb}" for c, (a, fb) in sorted(st.algfb.items()))))

    if "fm_volume" in options:
        before = sum(1 for f in frames for w in f if w[0] == "fm" and 0x40 <= w[2] <= 0x4F)
        regs: dict[int, dict] = defaultdict(dict)

        def sig(c):
            return tuple(sorted((k, v) for k, v in regs[c].items() if not 0x40 <= k <= 0x4F and not 0xA0 <= k <= 0xAE))

        seen: dict = {}
        for pass_ in (0, 1):
            regs.clear()
            for f in frames:
                for i, w in enumerate(f):
                    if w[0] != "fm" or w[2] < 0x30 or (w[2] & 3) == 3:
                        continue
                    c = _ch(w[1], w[2])
                    regs[c][w[2] & 0xFC] = w[3]
                    if 0x40 <= w[2] <= 0x4F:
                        carrier = (w[2] & 0xC) in CARRIERS[regs[c].get(0xB0, 0) & 7]
                        k = (c, w[2] & 0xC, sig(c), carrier)
                        if pass_ == 0:
                            seen.setdefault(k, []).append(w[3] & 0x7F)
                        else:
                            v = seen[k]
                            f[i] = ("fm", w[1], w[2], min(v) if carrier else max(set(v), key=v.count))
        # as escritas que ficaram iguais saem na redução de tamanho (export/optimize.py)
        after, last = 0, {}
        for f in frames:
            for w in f:
                if w[0] == "fm" and 0x40 <= w[2] <= 0x4F:
                    if last.get((w[1], w[2])) != w[3]:
                        after += 1
                    last[(w[1], w[2])] = w[3]
        lines.append(tr("volume do FM fixo: {0} → {1} mudanças de volume").format(before, after))

    if "lfo" in options:
        val = st.lfo if st.lfo is not None else 0
        for f in frames:
            f[:] = [w for w in f if not (w[0] == "fm" and w[1] == 0 and w[2] == 0x22)]
        frames[0].insert(0, ("fm", 0, 0x22, val))
        for f in frames:
            for i, w in enumerate(f):
                if w[0] == "fm" and 0xB4 <= w[2] <= 0xB6:
                    c = _ch(w[1], w[2])
                    if c in st.amsfms:
                        f[i] = ("fm", w[1], w[2], (w[3] & 0xC0) | st.amsfms[c])
        lines.append(tr("LFO da referência: {0}").format(
            tr("ligado") if val & 8 else tr("desligado")))

    if "samples" in options and samples and st.sample_peak:
        peak = max(int(np.abs(np.asarray(s, dtype=np.int16)).max()) for s in samples.values() if len(s))
        if peak:
            g = min(st.sample_peak, 127) / peak
            samples = {k: np.clip(np.round(np.asarray(s, dtype=np.float64) * g), -128, 127).astype(np.int8)
                       for k, s in samples.items()}
            lines.append(tr("volume das amostras: pico {0} → {1} ({2:+.1f} dB)").format(
                peak, min(st.sample_peak, 127), 20 * np.log10(g)))

    new = dataclasses.replace(stream, frames=frames, pcm_samples=samples)
    if lines:
        lines[0] = tr("estilo de {0}: ").format(st.name) + lines[0]
    return new, lines
