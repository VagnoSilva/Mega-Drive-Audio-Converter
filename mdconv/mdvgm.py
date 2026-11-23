"""VGM que já é do Mega Drive (YM2612 + SN76489) → XGM / VGM / Furnace.

Não há tradução de chip: as escritas no FM e no PSG passam como estão, agrupadas por quadro
(1/60 s), como faz o xgmtool do SGDK. O que muda:

* **Amostras do DAC** (canal 6 do FM): os VGMs do DefleMask, do Furnace e de rips de jogos
  guardam as amostras em blocos de dados (comando 0x67, tipo 0) e as tocam pelos comandos de
  stream (0x90–0x95) ou escrevendo byte a byte no registrador 0x2A (comandos 0x80–0x8F com
  posição em 0xE0). Cada disparo vira uma amostra PCM do XGM (8 bits a 14 kHz), no canal PCM
  escolhido; uma parada vira o id 0 (parar o canal).
* Registradores que o driver do XGM controla sozinho (timers 0x24–0x27, DAC 0x2A/0x2B) saem.
* O loop do VGM vira o loop do XGM; ``loop_start``/``loop_end`` (segundos) permitem pôr ou
  mudar o loop.

O driver do XGM toca 60 quadros por segundo: as escritas de um quadro saem juntas (até 8 ms de
diferença, imperceptível na maioria das músicas).
"""
from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

VGM_RATE = 44100
FPS = 60.0
PCM_RATE = 14000.0
NTSC_FM = 7670453
NTSC_PSG = 3579545
MAX_SAMPLES = 63

# tamanho dos comandos (bytes, incluindo o código) que só pulamos
_SKIP = {0x4F: 2, 0x51: 3, 0x53: 3, 0x54: 3, 0x55: 3, 0x56: 3, 0x57: 3, 0x58: 3, 0x59: 3, 0x5A: 3,
         0x5B: 3, 0x5C: 3, 0x5D: 3, 0x5E: 3, 0x5F: 3, 0xA0: 3, 0xC0: 4, 0xC1: 4, 0xC2: 4, 0xC3: 4,
         0xC4: 4, 0xD0: 4, 0xD1: 4, 0xD2: 4, 0xD3: 4, 0xD4: 4, 0xD5: 4, 0xD6: 4}
for _c in range(0x30, 0x40):
    _SKIP[_c] = 2
for _c in range(0x40, 0x4F):
    _SKIP[_c] = 3
for _c in range(0xA1, 0xC0):
    _SKIP.setdefault(_c, 3)
for _c in range(0xC5, 0xE0):
    _SKIP.setdefault(_c, 4)
for _c in range(0xE1, 0x100):
    _SKIP[_c] = 5


# posições dos clocks dos outros chips no cabeçalho do VGM (1.71)
_OTHER_CLOCKS = (0x10, 0x30, 0x38, 0x40, 0x44, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x5C, 0x60, 0x64, 0x68,
                 0x6C, 0x70, 0x74, 0x80, 0x84, 0x88, 0x8C, 0x90, 0x98, 0x9C, 0xA0, 0xA4, 0xA8, 0xAC,
                 0xB0, 0xB4, 0xB8, 0xC0, 0xC4, 0xC8, 0xCC, 0xD0, 0xD8, 0xDC, 0xE0)


@dataclass
class MdVgm:
    writes: list = field(default_factory=list)      # (amostra, ('fm', porta, reg, val) | ('psg', b))
    hits: list = field(default_factory=list)        # (amostra, bytes sem sinal, taxa Hz) ou (amostra, None)
    total: int = 0
    loop: int | None = None
    gd3: dict = field(default_factory=dict)
    fm_clock: int = 0
    psg_clock: int = 0
    warnings: list = field(default_factory=list)


def is_md_vgm(d: bytes) -> bool:
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    data = 0x34 + rel if rel else 0x40

    def clk(off):
        return struct.unpack_from("<I", d, off)[0] & 0x3FFFFFFF if data > off + 3 else 0
    others = [off for off in _OTHER_CLOCKS if clk(off)]
    return bool(clk(0x2C) or clk(0x0C)) and not others


def read_vgm(path) -> MdVgm:
    from mdconv.ay.convert import _gd3
    d = Path(path).read_bytes()
    if d[:2] == b"\x1f\x8b":
        d = gzip.decompress(d)
    if d[:4] != b"Vgm ":
        raise ValueError("Não é um arquivo VGM/VGZ.")
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    data = 0x34 + rel if rel else 0x40
    v = MdVgm()
    v.psg_clock = struct.unpack_from("<I", d, 0x0C)[0] & 0x3FFFFFFF
    v.fm_clock = struct.unpack_from("<I", d, 0x2C)[0] & 0x3FFFFFFF if data > 0x2F else 0
    loop_rel = struct.unpack_from("<I", d, 0x1C)[0]
    loop_off = 0x1C + loop_rel if loop_rel else None
    gd3_rel = struct.unpack_from("<I", d, 0x14)[0]
    if gd3_rel:
        try:
            v.gd3 = _gd3(d, 0x14 + gd3_rel)
        except Exception:
            v.gd3 = {}

    bank = bytearray()                    # blocos de dados tipo 0 (PCM do YM2612), em sequência
    blocks: list[tuple[int, int]] = []    # (início, tamanho) de cada bloco
    streams: dict[int, dict] = {}         # stream id → {freq, active, start, pos, len}
    pcm_pos = 0                           # ponteiro do 0xE0 / 0x8n
    direct: list[tuple[int, int]] = []    # (amostra, byte) escritos direto no 0x2A
    table = b""                           # tabela de descompressão (bloco 0x7F)
    p, t = data, 0

    def stream_end(sid, at):
        s = streams.get(sid)
        if s and s.get("active"):
            s["active"] = False
            v.hits.append((at, None))

    while p < len(d):
        if loop_off is not None and p == loop_off:
            v.loop = t
        c = d[p]
        if c == 0x66:
            break
        if c in (0x52, 0x53):
            port, reg, val = c - 0x52, d[p + 1], d[p + 2]
            if port == 0 and reg == 0x2A:
                direct.append((t, val))
            else:
                v.writes.append((t, ("fm", port, reg, val)))
            p += 3
        elif c == 0x50:
            v.writes.append((t, ("psg", d[p + 1])))
            p += 2
        elif c == 0x61:
            t += struct.unpack_from("<H", d, p + 1)[0]
            p += 3
        elif c == 0x62:
            t, p = t + 735, p + 1
        elif c == 0x63:
            t, p = t + 882, p + 1
        elif 0x70 <= c <= 0x7F:
            t, p = t + (c & 15) + 1, p + 1
        elif 0x80 <= c <= 0x8F:                     # escreve no DAC o byte do banco e espera n
            if pcm_pos < len(bank):
                direct.append((t, bank[pcm_pos]))
            pcm_pos += 1
            t, p = t + (c & 15), p + 1
        elif c == 0xE0:
            new = struct.unpack_from("<I", d, p + 1)[0]
            if new != pcm_pos and direct and direct[-1] is not None:
                direct.append(None)                 # salto no banco = começa outra amostra
            pcm_pos = new
            p += 5
        elif c == 0x67:
            typ, size = d[p + 2], struct.unpack_from("<I", d, p + 3)[0] & 0x7FFFFFFF
            raw = d[p + 7:p + 7 + size]
            if typ == 0x7F:                         # tabela de descompressão
                table = raw
            elif typ in (0x00, 0x40):               # PCM do YM2612 (0x40 = comprimido)
                pcm = raw if typ == 0x00 else _decompress(raw, table)
                blocks.append((len(bank), len(pcm)))
                bank += pcm
            elif typ < 0x40 or 0x80 <= typ < 0xC0:
                v.warnings.append(f"bloco de dados tipo 0x{typ:02X} ignorado")
            p += 7 + size
        elif c == 0x90:                             # configurar stream
            streams.setdefault(d[p + 1], {}).update(chip=d[p + 2], port=d[p + 3], cmd=d[p + 4])
            p += 5
        elif c == 0x91:
            streams.setdefault(d[p + 1], {}).update(bank=d[p + 2], step=max(1, d[p + 3]))
            p += 5
        elif c == 0x92:
            streams.setdefault(d[p + 1], {})["freq"] = struct.unpack_from("<I", d, p + 2)[0]
            p += 6
        elif c == 0x93:                             # iniciar stream: deslocamento e tamanho
            sid = d[p + 1]
            ofs, mode, ln = struct.unpack_from("<IBI", d, p + 2)
            s = streams.setdefault(sid, {})
            stream_end(sid, t)
            if ofs != 0xFFFFFFFF:
                s["pos"] = ofs
            kind = mode & 3
            freq = s.get("freq", 0) or 1
            if kind == 1:                           # comandos (≈ amostras)
                n = ln
            elif kind == 2:                         # milissegundos
                n = int(ln * freq / 1000)
            elif kind == 3:                         # até o fim dos dados
                n = len(bank) - s.get("pos", 0)
            else:
                n = 0
            if mode & 0x80:
                v.warnings.append("stream de DAC em loop: tocado uma vez")
            data_ = bytes(bank[s.get("pos", 0): s.get("pos", 0) + n * s.get("step", 1): s.get("step", 1)])
            if data_:
                s["active"] = True
                v.hits.append((t, data_, freq))
            p += 11
        elif c == 0x94:
            stream_end(d[p + 1], t)
            p += 2
        elif c == 0x95:                             # iniciar stream rápido: nº do bloco
            sid, blk, flags = d[p + 1], struct.unpack_from("<H", d, p + 2)[0], d[p + 4]
            s = streams.setdefault(sid, {})
            stream_end(sid, t)
            if blk < len(blocks):
                a, n = blocks[blk]
                data_ = bytes(bank[a:a + n])
                if flags & 1:
                    v.warnings.append("stream de DAC em loop: tocado uma vez")
                if flags & 0x10:
                    data_ = data_[::-1]
                s["active"] = True
                v.hits.append((t, data_, s.get("freq", 0) or 1))
            p += 5
        elif c in _SKIP:
            p += _SKIP[c]
        else:
            raise ValueError(f"comando 0x{c:02X} desconhecido na posição 0x{p:X} do VGM")
    v.total = max(t, struct.unpack_from("<I", d, 0x18)[0])
    if direct:
        v.hits += _direct_hits(direct)
    v.hits.sort(key=lambda h: h[0])
    return v


def _decompress(raw: bytes, table: bytes) -> bytes:
    """Bloco comprimido do VGM (tipo 0x40): compressão de n bits (0) ou DPCM (1), com a
    tabela do bloco 0x7F. Os bits são lidos do mais alto para o mais baixo."""
    ctype = raw[0]
    size = struct.unpack_from("<I", raw, 1)[0]
    bits_d, bits_c, sub = raw[5], raw[6], raw[7]
    add = struct.unpack_from("<H", raw, 8)[0]
    src = raw[10:]
    tvals: list[int] = []
    if table and len(table) >= 6:
        cnt = struct.unpack_from("<H", table, 4)[0]
        w = 1 if table[2] <= 8 else 2
        tvals = [table[6 + i] if w == 1 else struct.unpack_from("<H", table, 6 + 2 * i)[0]
                 for i in range(min(cnt, (len(table) - 6) // w))]
    out_w = 1 if bits_d <= 8 else 2
    n = size // out_w
    vals = []
    acc = bitpos = 0
    mask_d = (1 << bits_d) - 1
    cur = add                                   # DPCM: valor inicial
    total_bits = len(src) * 8
    for _ in range(n):
        if bitpos + bits_c > total_bits:
            break
        code = 0
        for _b in range(bits_c):
            byte = src[bitpos >> 3]
            code = (code << 1) | ((byte >> (7 - (bitpos & 7))) & 1)
            bitpos += 1
        if ctype == 0:                          # n bits
            if sub == 0:
                v = code + add
            elif sub == 1:
                v = (code << (bits_d - bits_c)) + add
            else:
                v = tvals[code] if code < len(tvals) else 0
            vals.append(v & mask_d)
        else:                                   # DPCM
            cur = (cur + (tvals[code] if code < len(tvals) else 0)) & mask_d
            vals.append(cur)
    if out_w == 1:
        return bytes(vals)
    return b"".join(struct.pack("<H", x) for x in vals)


def _direct_hits(direct):
    """Bytes escritos um a um no DAC (0x2A ou 0x8n) → disparos de amostra: cada trecho contínuo (sem
    pausa maior que 2 ms e sem salto do 0xE0 no banco, marcado com None) vira uma amostra, na taxa média
    em que os bytes chegaram."""
    if None in direct:
        out, run = [], []
        for x in direct + [None]:
            if x is None:
                out += _direct_hits(run)
                run = []
            else:
                run.append(x)
        return out
    out, i = [], 0
    while i < len(direct):
        j = i + 1
        while j < len(direct) and direct[j][0] - direct[j - 1][0] <= 88:
            j += 1
        seg = direct[i:j]
        if len(seg) >= 16:
            span = seg[-1][0] - seg[0][0]
            rate = (len(seg) - 1) * VGM_RATE / span if span else 8000
            out.append((seg[0][0], bytes(b for _, b in seg), rate))
        i = j
    return out


EXACT_RATE_TOL = 0.003


def _resample(data: bytes, rate: float) -> np.ndarray:
    x = (np.frombuffer(data, np.uint8).astype(np.float32) - 128.0)
    n = max(1, int(round(len(x) * PCM_RATE / rate)))
    # taxa a até 0,3% dos 14 kHz do XGM (ex.: 14 015 Hz do Furnace): cópia exata, byte a byte — a
    # interpolação apagaria agudos, e a diferença de afinação (< 5 cents) não se ouve
    if n == len(x) or abs(rate / PCM_RATE - 1) <= EXACT_RATE_TOL:
        y = x
    else:
        pos = np.arange(n) * (len(x) / n)
        y = np.interp(pos, np.arange(len(x)), x)
    return np.clip(np.round(y), -128, 127).astype(np.int8)


def convert(v: MdVgm, pcm_channel: int = 1, trim: bool = True, loop_start: float | None = None,
            loop_end: float | None = None):
    """MdVgm → (Stream, relatório)."""
    from mdconv.driver.driver import Stream
    report = [f"Mega Drive (YM2612 + SN76489), {len(v.writes)} escritas, {len(v.hits)} disparos de PCM, "
              f"{v.total / VGM_RATE:.1f} s"]
    for clk, ref, nome in ((v.fm_clock, NTSC_FM, "YM2612"), (v.psg_clock, NTSC_PSG, "SN76489")):
        if clk and abs(clk / ref - 1) > 0.003:
            report.append(f"⚠ clock do {nome} {clk} Hz ≠ {ref} Hz (NTSC): a altura muda "
                          f"{1200 * np.log2(ref / clk):+.0f} cents")
    report += [f"⚠ {w}" for w in dict.fromkeys(v.warnings)]

    def frame(t):
        return int(t * FPS / VGM_RATE)

    # início: primeira escrita que faz som (key-on, volume do PSG, PCM)
    first = 0
    if trim:
        cands = [t for t, w in v.writes if (w[0] == "fm" and w[1] == 0 and w[2] == 0x28 and w[3] & 0xF0)
                 or (w[0] == "psg" and w[1] & 0x90 == 0x90 and w[1] & 15 != 15)]
        cands += [h[0] for h in v.hits if h[1] is not None]
        first = frame(min(cands)) if cands else 0
        if v.loop is not None:
            first = min(first, frame(v.loop))
    n_frames = frame(v.total) + 1
    frames: list[list[tuple]] = [[] for _ in range(n_frames)]
    pre: list[tuple] = []                      # escritas antes do início: vão para o quadro 0
    for t, w in v.writes:
        if w[0] == "fm" and w[2] in (0x24, 0x25, 0x26, 0x2A, 0x2B):
            continue
        if w[0] == "fm" and w[2] == 0x27:
            w = ("fm", w[1], 0x27, w[3] & 0xC0)   # só o modo do canal 3
        f = frame(t)
        (pre if f < first else frames[min(f, n_frames - 1)]).append(w)
    # amostras: mesmo dado + mesma taxa → mesmo id
    ids: dict[tuple, int] = {}
    samples: dict[int, np.ndarray] = {}
    dropped = 0
    # um disparo cortado (pelo seguinte, ou um VGM que grava só o trecho tocado) usa a amostra inteira
    # de que ele é o começo, e para no mesmo ponto: o XGM não ganha uma cópia encurtada por disparo
    rk = lambda r: int(round(r / 50.0)) * 50    # taxa medida pelos 0x8n oscila alguns Hz: 13 996 ≈ 14 000
    keys = {(h[1].rstrip(b"\x80") or h[1], rk(h[2])) for h in v.hits if h[1] is not None}
    longest = sorted(keys, key=lambda k: -len(k[0]))
    canon = {}
    for k in keys:
        for big in longest:
            if len(big[0]) <= len(k[0]):
                break
            if big[1] == k[1] and big[0].startswith(k[0]):
                canon[k] = big
                break
    for h in v.hits:
        f = max(first, frame(h[0]))
        if f >= n_frames:
            continue
        if h[1] is None:
            frames[f].append(("pcm", pcm_channel, 0, 3))
            continue
        key = (h[1].rstrip(b"\x80") or h[1], rk(h[2]))
        if key in canon:
            stop = int(round((h[0] + len(key[0]) * VGM_RATE / h[2]) * FPS / VGM_RATE))
            if f < stop < n_frames:
                frames[max(first, stop)].append(("pcm", pcm_channel, 0, 3))
            key = canon[key]
        if key not in ids:
            if len(ids) >= MAX_SAMPLES:
                dropped += 1
                continue
            ids[key] = len(ids) + 1
            samples[ids[key]] = _resample(key[0], h[2])
        frames[f].append(("pcm", pcm_channel, ids[key], 3))
    frames = frames[first:]
    if frames:
        frames[0] = pre + frames[0]
    loop_frame = frame(v.loop) - first if v.loop is not None else None
    if first:
        report.append(f"silêncio do início removido: {first / FPS:.2f} s")
    if samples:
        kb = sum(len(s) for s in samples.values()) / 1024
        rates = sorted({round(k[1]) for k in ids})
        report.append(f"PCM: {len(samples)} amostra(s), {kb:.0f} KB a 14 kHz, no canal PCM {pcm_channel} "
                      f"(taxas originais: {', '.join(map(str, rates))} Hz"
                      + ("; cópia exata, sem reamostrar" if all(abs(r / PCM_RATE - 1) <= EXACT_RATE_TOL for r in rates)
                         else "") + ")")
    fm6 = sum(1 for _, w in v.writes if w[0] == "fm" and w[1] == 0 and w[2] == 0x28
              and (w[3] & 7) == 6 and w[3] & 0xF0)
    if fm6 and samples:
        report.append(f"⚠ o canal FM 6 toca {fm6} nota(s) FM, mas no XGM ele é o DAC das amostras: "
                      "essas notas não soam no XGM")
    if dropped:
        report.append(f"⚠ {dropped} disparo(s) sem lugar: o XGM aceita {MAX_SAMPLES} amostras por música")
    stream = Stream(frames=frames, fps=FPS, loop_frame=loop_frame, pcm_samples=samples, pcm_rate=PCM_RATE)
    apply_loop(stream, loop_start, loop_end, report)
    return stream, report


def apply_loop(stream, loop_start: float | None, loop_end: float | None, report: list) -> None:
    """Põe ou muda o loop de um fluxo já pronto (segundos a partir do começo do fluxo).

    ``loop_end`` corta a música nesse ponto (ela volta para o início do loop dali); sem
    ``loop_start``, só corta e o loop original (se houver e couber) continua."""
    if loop_start is None and loop_end is None:
        return
    n = len(stream.frames)
    if loop_end is not None:
        end = max(1, min(n, int(round(loop_end * stream.fps))))
        if end < n:
            del stream.frames[end:]
            report.append(f"música cortada em {end / stream.fps:.2f} s")
        n = len(stream.frames)
    if loop_start is not None:
        lf = max(0, min(n - 1, int(round(loop_start * stream.fps))))
        stream.loop_frame = lf
        report.append(f"loop: volta para {lf / stream.fps:.2f} s ao chegar a {n / stream.fps:.2f} s (escolhido)")
    elif stream.loop_frame is not None and stream.loop_frame >= n:
        stream.loop_frame = None
        report.append("⚠ o loop original ficou depois do corte: sem loop")
    if stream.loop_frame is not None and loop_end is not None and loop_start is None:
        report.append(f"loop: volta para {stream.loop_frame / stream.fps:.2f} s ao chegar a "
                      f"{n / stream.fps:.2f} s")
