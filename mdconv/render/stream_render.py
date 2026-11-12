"""Toca o fluxo do driver nos chips emulados e gera o áudio da prévia.

* FM: Nuked-OPN2 no modo YM2612 (padrão; LGPL, vai junto com o programa); se a biblioteca
  dele não estiver disponível, o emulador próprio (YM2612 discreto). O núcleo do Genesis
  Plus GX só é usado se pedido explicitamente e configurado localmente (MDCONV_GPGX_LIB).
* PSG: emulador próprio (o GPGX fornecido não inclui um PSG distribuível).
* PCM: mixer que imita o do XGM v1 — até 4 canais de 8 bits com sinal a ≈14 kHz, somados e
  limitados a 8 bits, saindo pelo DAC (canal FM6) com retenção de amostra até a próxima.
  A soma com saturação em 8 bits foi conferida no driver Z80 (drv_xgm.s80: estouro vira $7F/$80).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mdconv.chips.emulator import MegaDriveSound, dc_block, resample
from mdconv.driver.driver import Stream


@dataclass
class RenderInfo:
    fm_engine: str
    seconds: float
    normalize_db: float
    pcm_clipped_samples: int


FM_ENGINES = ("auto", "proprio", "nuked", "gpgx")


def _fm_chip(engine: str = "auto"):
    """``auto``: Nuked-OPN2 (modo YM2612); se indisponível, o emulador próprio."""
    from mdconv.chips import nuked
    from mdconv.chips import reference_gpgx as gpgx
    if engine not in FM_ENGINES:
        raise ValueError(f"motor FM desconhecido: {engine}")
    if engine == "nuked" or (engine == "auto" and nuked.available()):
        return nuked.NukedYM2612(ym2612=True), "Nuked-OPN2 (modo YM2612)"
    if engine == "gpgx":
        if not gpgx.available():
            raise RuntimeError("GPGX não configurado (MDCONV_GPGX_LIB)")
        return gpgx.GpgxYM2612(gpgx.DISCRETE), "Genesis Plus GX (YM2612 discreto)"
    return None, "emulador próprio (YM2612 discreto)"


def render_stream(stream: Stream, sr_out: int = 44100, normalize: bool = True,
                  solo: set[str] | None = None, fm_engine: str = "auto") -> tuple[np.ndarray, RenderInfo]:
    """``solo``: subconjunto de {'fm', 'psg', 'noise', 'pcm'} para ouvir partes isoladas
    ('psg' = os 3 canais de tom; 'noise' = o canal de ruído).
    ``fm_engine``: auto | proprio | nuked | gpgx."""
    gp, engine = _fm_chip(fm_engine)
    if gp is not None:
        fm = gp
        psg = MegaDriveSound(discrete_ym2612=False)      # só o PSG deste chip é usado
    else:
        fm = psg = MegaDriveSound(discrete_ym2612=True)
    rate = fm.rate
    spf = rate / stream.fps
    pcm_step = stream.pcm_rate / rate                    # avanço do PCM por amostra do chip
    playing: dict[int, list] = {}                        # canal → [id, posição (float)]
    out = []
    acc = 0.0
    clipped = 0
    use = solo or {"fm", "psg", "noise", "pcm"}
    latch = 0                                            # canal do PSG travado no último byte de comando
    for writes in stream.frames:
        for w in writes:
            if w[0] == "fm":
                if "fm" in use or w[2] in (0x2A, 0x2B):
                    fm.write_fm(w[1], w[2], w[3])
            elif w[0] == "psg":
                b = w[1]
                if b & 0x80:
                    latch = (b >> 5) & 3
                if ("noise" if latch == 3 else "psg") in use:
                    psg.write_psg(b)
            elif w[0] == "pcm":
                playing[w[1]] = [w[2], 0.0]
        acc += spf
        n = int(acc)
        acc -= n
        if n <= 0:
            continue
        a = fm.render(n)
        if psg is not fm:
            a = a + psg.render(n)
        if playing and "pcm" in use:
            mix = np.zeros(n, dtype=np.float32)
            for ch in list(playing):
                sid, pos = playing[ch]
                data = stream.pcm_samples[sid]
                idx = (pos + np.arange(n) * pcm_step).astype(np.int64)
                ok = idx < len(data)
                mix[ok] += data[idx[ok]]
                pos += n * pcm_step
                if pos >= len(data):
                    del playing[ch]
                else:
                    playing[ch][1] = pos
            over = np.abs(mix) > 127
            clipped += int(over.sum())
            mix = np.clip(mix, -128, 127) / 128.0       # DAC: 8 bits → escala de 1 canal
            a = a + mix[:, None]
        out.append(a)
    x = np.concatenate(out) if out else np.zeros((1, 2), np.float32)
    x = dc_block(x, rate)
    y = resample(x, rate, sr_out)
    g_db = 0.0
    if normalize:
        pk = float(np.abs(y).max()) or 1.0
        g = 10 ** (-1 / 20) / pk
        y *= g
        g_db = 20 * np.log10(g)
    return y, RenderInfo(engine, len(y) / sr_out, g_db, clipped)
