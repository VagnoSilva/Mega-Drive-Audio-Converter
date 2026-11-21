"""Reduz o tamanho do fluxo antes de gravar XGM/VGM (e .fur das conversões diretas).

Três modos (menu Ferramentas → Tamanho do XGM):

* ``max`` — **Qualidade máxima**: só o que não muda nada no som: escritas que repetem o valor
  que o registrador já tem, amostras idênticas guardadas uma vez só e silêncio exato (zeros) no
  fim das amostras; amostras que nenhuma nota toca saem.
* ``balanced`` — **Equilibrado** (padrão): o de cima e mais: o fim das amostras abaixo de −42 dB
  do pico (o ruído do último bit do PCM de 8 bits) sai, e amostras quase iguais (mesma forma,
  volume a até 1,5 dB) viram uma só.
* ``compact`` — **Compacto**: o de cima e mais: as caudas abaixo de −30 dB do pico saem (com uma
  curta descida de volume para não estalar), nenhuma amostra passa de 0,55 s (as mais longas
  terminam numa descida de volume) e as cópias da mesma amostra em volumes diferentes (até
  6 dB) viram uma só — as notas mais fracas desse grupo ficam um pouco mais fortes.

Escritas repetidas: o estado dos registradores é esquecido no quadro do loop (na volta o chip
está como no fim da música, não como na primeira passada), e as escritas de frequência (latch
$A4–$A6/$AC–$AE + $A0–$AA) só saem em par. Key-on ($28), DAC, timers e o ruído do PSG (que
reinicia o gerador) nunca são removidos.
"""
from __future__ import annotations

import dataclasses
import hashlib

import numpy as np

from mdconv.i18n import tr

MODES = {"max": "Qualidade máxima", "balanced": "Equilibrado", "compact": "Compacto"}
HINTS = {
    "max": "Só tira o que não muda o som (escritas repetidas, amostras idênticas, silêncio).",
    "balanced": "Arquivo um pouco menor, som igual ao ouvido (recomendado).",
    "compact": "Arquivo bem menor: amostras mais curtas e juntadas; pequena perda nas batidas longas.",
}
_mode = "balanced"
ALIGN = 256

# (limiar da cauda em dB do pico ou None = só zeros, descida em amostras,
#  semelhança mínima e diferença de volume máxima em dB para juntar amostras ou None)
_PARAMS = {"max": (None, 0, None, None), "balanced": (-42.0, 0, (0.995, 1.5), None),
           "compact": (-30.0, 256, (0.985, 6.5), 0.55)}
_NEVER_FM = {0x24, 0x25, 0x26, 0x27, 0x28, 0x2A, 0x2B}


def set_mode(mode: str) -> None:
    global _mode
    _mode = mode if mode in MODES else "balanced"


def mode() -> str:
    return _mode


def _aligned(n: int) -> int:
    return (n + ALIGN - 1) // ALIGN * ALIGN


def _trim(x: np.ndarray, tail_db, fade: int, max_len: int | None = None) -> np.ndarray:
    if len(x) == 0:
        return x
    a = np.abs(x.astype(np.int16))
    peak = int(a.max())
    if peak == 0:
        return x[:1]
    thr = 0 if tail_db is None else max(1, int(peak * 10 ** (tail_db / 20)))
    idx = np.nonzero(a > thr)[0]
    end = int(idx[-1]) + 1 if len(idx) else 1
    cut = max_len is not None and end > max_len
    if cut:
        end = max_len
        fade = max(fade, max_len // 5)
    y = x[:end].copy()
    if fade and end > fade * 2 and (thr > 1 or cut):
        ramp = np.linspace(1.0, 0.0, fade)
        y[-fade:] = np.round(y[-fade:].astype(np.float64) * ramp).astype(np.int8)
    return y


def _similar(a: np.ndarray, b: np.ndarray, min_corr: float, max_db: float) -> bool:
    la, lb = len(a), len(b)
    if min(la, lb) < 0.9 * max(la, lb):
        return False
    n = min(la, lb)
    x, y = a[:n].astype(np.float64), b[:n].astype(np.float64)
    ex, ey = float(np.dot(x, x)), float(np.dot(y, y))
    if ex <= 0 or ey <= 0:
        return False
    if abs(10 * np.log10(ex / ey)) > max_db:
        return False
    return float(np.dot(x, y)) / np.sqrt(ex * ey) >= min_corr


def optimize_stream(stream, mode: str | None = None):
    """Devolve (fluxo novo, linhas para o relatório). O fluxo original não é alterado."""
    mode = mode or _mode
    tail_db, fade, merge, max_s = _PARAMS.get(mode, _PARAMS["balanced"])
    max_len = int(max_s * stream.pcm_rate) if max_s else None
    lines = []

    # ---- amostras: cauda, idênticas, parecidas
    samples = stream.pcm_samples or {}
    uses: dict[int, int] = {}
    for f in stream.frames:
        for w in f:
            if w[0] == "pcm" and w[2]:
                uses[w[2]] = uses.get(w[2], 0) + 1
    before = sum(_aligned(len(s)) for s in samples.values())
    # amostra que nenhuma nota toca não vai para o arquivo
    trimmed = {sid: _trim(np.asarray(s, dtype=np.int8), tail_db, fade, max_len) for sid, s in samples.items()
               if uses.get(sid)}
    remap: dict[int, int] = {}
    keep: list[int] = []
    by_hash: dict[bytes, int] = {}
    # as mais usadas primeiro: viram a representante do grupo
    for sid in sorted(trimmed, key=lambda s: (-uses.get(s, 0), s)):
        x = trimmed[sid]
        h = hashlib.blake2b(x.tobytes(), digest_size=16).digest()
        if h in by_hash:
            remap[sid] = by_hash[h]
            continue
        if merge:
            twin = next((k for k in keep if _similar(trimmed[k], x, *merge)), None)
            if twin is not None:
                remap[sid] = twin
                continue
        by_hash[h] = sid
        keep.append(sid)
        remap[sid] = sid
    new_ids = {sid: i + 1 for i, sid in enumerate(sorted(keep))}
    new_samples = {new_ids[s]: trimmed[s] for s in keep}
    after = sum(_aligned(len(s)) for s in new_samples.values())
    if samples:
        merged = len(trimmed) - len(keep)
        lines.append(tr("amostras: {0} → {1}").format(len(samples), len(new_samples))
                     + (tr(" ({0} junta(s))").format(merged) if merged else "")
                     + f", {before / 1024:.0f} → {after / 1024:.0f} KB")

    # ---- escritas repetidas
    fm_state: dict = {}
    psg_vol: dict = {}
    psg_tone: dict = {}
    dropped = 0
    frames_out = []
    for fi, f in enumerate(stream.frames):
        if stream.loop_frame is not None and fi == stream.loop_frame:
            fm_state.clear()
            psg_vol.clear()
            psg_tone.clear()
        out = []
        i = 0
        n = len(f)
        while i < n:
            w = f[i]
            kind = w[0]
            if kind == "pcm":
                sid = w[2]
                if sid and sid in remap:
                    out.append((w[0], w[1], new_ids[remap[sid]], *w[3:]))
                else:
                    out.append(w)                  # 0 = parar o canal (ou id sem amostra)
                i += 1
                continue
            if kind == "fm":
                port, reg, val = w[1], w[2], w[3]
                if reg in _NEVER_FM:
                    out.append(w)
                    i += 1
                    continue
                if 0xA4 <= reg <= 0xA6 or 0xAC <= reg <= 0xAE:
                    nxt = f[i + 1] if i + 1 < n else None
                    if nxt and nxt[0] == "fm" and nxt[1] == port and nxt[2] == reg - 4:
                        k1, k2 = (port, reg), (port, reg - 4)
                        if fm_state.get(k1) == val and fm_state.get(k2) == nxt[3]:
                            dropped += 2
                        else:
                            out += [w, nxt]
                            fm_state[k1], fm_state[k2] = val, nxt[3]
                        i += 2
                        continue
                    out.append(w)                  # latch sozinho: mantém e esquece o par
                    fm_state.pop((port, reg), None)
                    fm_state.pop((port, reg - 4), None)
                    i += 1
                    continue
                if 0xA0 <= reg <= 0xAE:
                    out.append(w)                  # frequência sem o latch antes: depende do latch
                    fm_state.pop((port, reg), None)
                    i += 1
                    continue
                if fm_state.get((port, reg)) == val:
                    dropped += 1
                else:
                    out.append(w)
                    fm_state[(port, reg)] = val
                i += 1
                continue
            if kind == "psg":
                b = w[1]
                if b & 0x90 == 0x90:              # volume (byte único)
                    ch = (b >> 5) & 3
                    if psg_vol.get(ch) == b & 15:
                        dropped += 1
                    else:
                        out.append(w)
                        psg_vol[ch] = b & 15
                    i += 1
                    continue
                if b & 0xF0 in (0x80, 0xA0, 0xC0):  # tom: latch + byte de dados
                    ch = (b >> 5) & 3
                    nxt = f[i + 1] if i + 1 < n else None
                    if nxt and nxt[0] == "psg" and not nxt[1] & 0x80:
                        val = (b & 15) | ((nxt[1] & 0x3F) << 4)
                        if psg_tone.get(ch) == val:
                            dropped += 2
                        else:
                            out += [w, nxt]
                            psg_tone[ch] = val
                        i += 2
                        continue
                    psg_tone.pop(ch, None)
                out.append(w)                      # ruído e o resto: sempre
                i += 1
                continue
            out.append(w)
            i += 1
        frames_out.append(out)
    if dropped:
        lines.append(tr("{0} escrita(s) repetida(s) removida(s)").format(dropped))
    new = dataclasses.replace(stream, frames=frames_out, pcm_samples=new_samples)
    if lines:
        lines[0] = tr("tamanho ({0}): ").format(tr(MODES.get(mode, mode))) + lines[0]
    return new, lines
