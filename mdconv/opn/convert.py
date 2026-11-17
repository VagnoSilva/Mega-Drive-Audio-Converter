"""YM2203 (OPN) em VGM → YM2612 (OPN2) + SN76489, quadro a quadro (XGM / VGM / prévia).

O YM2203 é da mesma família do chip do Mega Drive: 3 canais FM de 4 operadores com os mesmos
registradores de timbre, mais um gerador de onda quadrada compatível com o AY-3-8910 (SSG).
Dois YM2203 (ex.: Ghosts 'n Goblins) = 6 canais FM, exatamente o que o YM2612 tem.

Tradução:
* **Timbres** ($30–$9F, $B0): copiados como estão, com dois ajustes: o bit AM (o YM2203 não tem
  LFO) é zerado, e as taxas de envelope são corrigidas pela diferença da taxa interna dos chips
  (o envelope do OPN anda no ritmo da taxa de amostragem interna; cada passo de taxa = ×√2).
* **Notas** ($A0–$A6): a frequência é recalculada para o clock do YM2612 (bloco/F-number novos).
* **Key on/off** ($28): o 1º chip vai para os canais FM 1–3, o 2º para os FM 4–6.
* **Pan** ($B4 no YM2612, que o YM2203 não tem): centro.
* **SSG**: convertido pelo conversor do AY-3-8910 para o PSG (só se tocar alguma coisa).
* **Divisor de clock** ($2D/$2E/$2F) acompanhado como no MAME: 1/6, 1/3 ou 1/2 (FM).
* Silêncio antes da primeira nota removido; loop do VGM mantido.
"""
from __future__ import annotations

import gzip
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from mdconv.chips.emulator import NTSC_FM_CLOCK
from mdconv.driver.driver import Stream
from mdconv.fm.patch import block_fnum_to_freq, freq_to_block_fnum

VGM_RATE = 44100
FPS = 60.0
FS_2612 = NTSC_FM_CLOCK / 144.0
OPN_PRES = {0: 2 * 12, 1: 2 * 12, 2: 6 * 12, 3: 3 * 12}      # divisor do FM por seletor (MAME)
SSG_PRES = {0: 1, 1: 1, 2: 4, 3: 2}


@dataclass
class OpnVgm:
    clock: int
    dual: bool
    writes: list            # (amostra, chip, reg, valor)
    end: int
    loop: int | None
    gd3: dict = field(default_factory=dict)


def read_vgm(path) -> OpnVgm:
    from mdconv.ay.convert import _gd3
    d = Path(path).read_bytes()
    if d[:2] == b"\x1f\x8b":
        d = gzip.decompress(d)
    if d[:4] != b"Vgm ":
        raise ValueError("Não é um arquivo VGM/VGZ.")
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    data = 0x34 + rel if rel else 0x40
    clk = struct.unpack_from("<I", d, 0x44)[0] if data > 0x47 else 0
    if not clk & 0x3FFFFFFF:
        raise ValueError("Este VGM não tem YM2203.")
    loop_rel = struct.unpack_from("<I", d, 0x1C)[0]
    loop_off = 0x1C + loop_rel if loop_rel else None
    gd3_rel = struct.unpack_from("<I", d, 0x14)[0]
    p, t, writes, loop_t = data, 0, [], None
    while p < len(d):
        if loop_off is not None and p == loop_off:
            loop_t = t
        c = d[p]
        if c == 0x66:
            break
        if c in (0x55, 0xA5):
            writes.append((t, 0 if c == 0x55 else 1, d[p + 1], d[p + 2]))
            p += 3
        elif c == 0x61:
            t += struct.unpack_from("<H", d, p + 1)[0]
            p += 3
        elif c == 0x62:
            t, p = t + 735, p + 1
        elif c == 0x63:
            t, p = t + 882, p + 1
        elif 0x70 <= c <= 0x7F:
            t, p = t + (c & 15) + 1, p + 1
        elif c == 0x67:
            p += 7 + struct.unpack_from("<I", d, p + 3)[0]
        elif 0x30 <= c <= 0x3F:
            p += 2
        elif 0x40 <= c <= 0x5F or 0xA0 <= c <= 0xBF:
            p += 3
        elif 0xC0 <= c <= 0xDF:
            p += 4
        elif 0xE0 <= c <= 0xFF:
            p += 5
        else:
            p += 1
    return OpnVgm(clk & 0x3FFFFFFF, bool(clk & 0x40000000), writes, t, loop_t,
                  _gd3(d, 0x14 + gd3_rel if gd3_rel else 0))


def _rate(v: int, bits: int, delta: float) -> int:
    """Taxa de envelope corrigida (0 = parado e o máximo = instantâneo ficam como estão)."""
    top = (1 << bits) - 1
    if v <= 0 or v >= top:
        return v
    return max(1, min(top, int(round(v + delta))))


@dataclass
class OpnConversion:
    stream: Stream
    report: list[str]
    vgm: OpnVgm
    exact: list = field(default_factory=list)     # (amostra 44,1 kHz, escrita) sem arredondar
    exact_end: int = 0
    exact_loop: int | None = None


def best_phase(onsets: list[int], spf: float) -> float:
    """Deslocamento (em amostras, 0..1 quadro) que deixa as notas mais perto do início de um quadro.

    O programa de som do jogo não roda a 60 Hz; sem isso, notas com o mesmo intervalo caem ora
    num quadro, ora no seguinte, e o ritmo oscila."""
    if not onsets:
        return 0.0
    best, best_err = 0.0, 1e18
    for k in range(32):
        ph = k * spf / 32
        err = sum(abs(((t - ph) / spf) - round((t - ph) / spf)) for t in onsets)
        if err < best_err:
            best, best_err = ph, err
    return best


def convert(vgm: OpnVgm, trim: bool = True) -> OpnConversion:
    nchips = 2 if vgm.dual or any(c == 1 for _, c, _, _ in vgm.writes) else 1
    writes = sorted(vgm.writes, key=lambda x: x[0])
    report = [f"YM2203 a {vgm.clock} Hz, {nchips} chip(s), {len(writes)} escritas, {vgm.end / VGM_RATE:.1f} s"
              + (f", loop em {vgm.loop / VGM_RATE:.2f} s" if vgm.loop is not None else ", sem loop")]

    # ---- silêncio do início: até o 1º key-on
    t0 = 0
    if trim:
        t0 = next((t for t, c, r, v in writes if r == 0x28 and v & 0xF0 and (v & 3) < 3), 0)
        if t0 > 0:
            report.append(f"Silêncio do início removido: {t0 / VGM_RATE:.2f} s (a música começa na primeira nota)")
    writes = [(max(0, t - t0), c, r, v) for t, c, r, v in writes]
    end = max(1, vgm.end - t0)
    loop = None if vgm.loop is None else max(0, vgm.loop - t0)

    spf = VGM_RATE / FPS
    onsets = sorted({t for t, c, r, v in writes if r == 0x28 and v & 0xF0})
    phase = best_phase(onsets, spf)

    def frame_of(t):
        return max(0, int(round((t - phase) / spf)))

    n_frames = frame_of(end) + 1
    frames = [[] for _ in range(n_frames)]
    # pan central e DAC desligado nos 6 canais
    for ch in range(6):
        port, cc = (0, ch) if ch < 3 else (1, ch - 3)
        frames[0].append(("fm", port, 0xB4 + cc, 0xC0))

    sel = [2] * nchips                      # seletor do divisor (reset = 1/6)
    latch = [[0] * 3 for _ in range(nchips)]   # $A4–$A6 escritos (bloco + F-num alto)
    fm_used = set()
    ssg = []                                # escritas do SSG para o conversor do AY
    pres_seen = set()
    deltas = {}

    def delta(c):
        fs = vgm.clock / OPN_PRES[sel[c]]
        d = 2 * math.log2(fs / FS_2612)     # passos de taxa (5 bits) equivalentes
        deltas[c] = (fs, d)
        return d

    exact = [(0, w) for w in frames[0]]          # (amostra, escrita) com o tempo original, para o VGM

    def emit(f, t, w):
        frames[f].append(w)
        exact.append((t, w))

    for t, c, r, v in writes:
        if c >= nchips:
            continue
        f = min(n_frames - 1, frame_of(t))
        port = c                            # 1º chip → porta 0 (FM 1–3); 2º → porta 1 (FM 4–6)
        if r < 0x10:
            ssg.append((t, c, r, v))
            continue
        if r in (0x2D, 0x2E, 0x2F):
            sel[c] = {0x2D: sel[c] | 2, 0x2E: sel[c] | 1, 0x2F: 0}[r]
            pres_seen.add(c)
            continue
        if r == 0x28:
            ch = v & 3
            if ch == 3:
                continue
            fm_used.add(c * 3 + ch)
            emit(f, t, ("fm", 0, 0x28, (v & 0xF0) | ch | (4 if c else 0)))
            continue
        if 0x30 <= r <= 0x9F:
            d = delta(c)
            hi = r & 0xF0
            if hi == 0x50:                  # KS | AR
                v = (v & 0xC0) | _rate(v & 0x1F, 5, d)
            elif hi == 0x60:                # AM | D1R (sem AM no YM2203)
                v = _rate(v & 0x1F, 5, d)
            elif hi == 0x70:                # D2R
                v = _rate(v & 0x1F, 5, d)
            elif hi == 0x80:                # SL | RR (RR: cada passo = 2 passos de taxa)
                v = (v & 0xF0) | _rate(v & 0x0F, 4, d / 2)
            emit(f, t, ("fm", port, r, v))
            continue
        if 0xA4 <= r <= 0xA6:
            latch[c][r - 0xA4] = v
            continue
        if 0xA0 <= r <= 0xA2:
            ch = r - 0xA0
            hi = latch[c][ch]
            block, fnum = (hi >> 3) & 7, ((hi & 7) << 8) | v
            eff_clock = vgm.clock * 144.0 / OPN_PRES[sel[c]]
            hz = block_fnum_to_freq(block, fnum, eff_clock)
            b2, f2 = freq_to_block_fnum(hz, NTSC_FM_CLOCK) if hz > 0 else (0, 0)
            emit(f, t, ("fm", port, 0xA4 + ch, (b2 << 3) | (f2 >> 8)))
            emit(f, t, ("fm", port, 0xA0 + ch, f2 & 0xFF))
            continue
        if 0xB0 <= r <= 0xB2:
            emit(f, t, ("fm", port, r, v & 0x3F))
            continue
        # $24–$27 (timers / modo do canal 3) e o resto: ignorados

    for c in sorted(pres_seen):
        pres = OPN_PRES[sel[c]]
        fs = vgm.clock / pres
        report.append(f"Chip {c + 1}: divisor 1/{pres // 12} → taxa interna {fs:.0f} Hz "
                      f"(YM2612: {FS_2612:.0f} Hz; envelopes corrigidos em {2 * math.log2(fs / FS_2612):+.2f} passos)")
    report.append("FM: " + ", ".join(f"chip {ch // 3 + 1} canal {ch % 3 + 1} → FM {ch + 1}" for ch in sorted(fm_used)))

    # ---- SSG → PSG (pelo conversor do AY), se tocar alguma coisa
    if ssg:
        from mdconv.ay.convert import AyVgm, convert as ay_convert
        # SSG do YM2203: tom = clock / (divisor × 16 × TP) — igual a um AY com clock = clock / divisor
        ssg_clock = int(vgm.clock / SSG_PRES[sel[0]])
        ay = AyVgm(ssg_clock, nchips > 1, ssg, end, loop, {})
        conv_ay = ay_convert(ay, trim=False, fm_slots=[k for k in range(6) if k not in fm_used])
        audible = any(w[0] == "psg" and (w[1] & 0x90) == 0x90 and (w[1] & 15) < 15
                      for fr in conv_ay.stream.frames for w in fr) or \
            any(w[0] == "fm" and w[2] == 0x28 and w[3] & 0xF0 for fr in conv_ay.stream.frames for w in fr)
        if audible:
            exact = None                      # o SSG vem em quadros: o VGM também sai em quadros
            report += ["SSG: " + x for x in conv_ay.report[1:]]
            for i, fr in enumerate(conv_ay.stream.frames[:n_frames]):
                frames[i] = fr + frames[i] if i == 0 else frames[i] + fr
        else:
            report.append("SSG (onda quadrada do YM2203): não toca nesta música")

    loop_frame = frame_of(loop) if loop is not None else None
    stream = Stream(frames=frames, fps=FPS, loop_frame=loop_frame, pcm_samples={}, pcm_rate=14000.0)
    if loop_frame is not None:
        loop_len = max(1, int((end - loop) / spf + 1e-6))
        stream.stats["loop_end_frame"] = loop_frame + loop_len
        stream.stats["seamless_loop"] = True
        report.append(f"Loop: volta ao quadro {loop_frame}, a cada {loop_len} quadros ({loop_len / FPS:.3f} s; "
                      f"no original {(end - loop) / VGM_RATE:.3f} s — no VGM convertido, exato)")
    conv = OpnConversion(stream, report, vgm)
    conv.exact = exact
    conv.exact_end, conv.exact_loop = end, loop
    return conv
