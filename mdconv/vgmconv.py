"""Conversão direta de VGM/VGZ para o Mega Drive, chip a chip (sem MIDI).

Chips aceitos: AY-3-8910 (:mod:`mdconv.ay`) e YM2203 / OPN (:mod:`mdconv.opn`). O chip é
reconhecido pelo cabeçalho do VGM.
"""
from __future__ import annotations

import gzip
import struct
from pathlib import Path


def detect_chip(path) -> str:
    d = Path(path).read_bytes()
    if d[:2] == b"\x1f\x8b":
        d = gzip.decompress(d)
    if d[:4] != b"Vgm ":
        raise ValueError("Não é um arquivo VGM/VGZ.")
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    data = 0x34 + rel if rel else 0x40

    def clk(off):
        return struct.unpack_from("<I", d, off)[0] & 0x3FFFFFFF if data > off + 3 else 0
    if clk(0x44):
        return "YM2203"
    if clk(0x74):
        return "AY-3-8910"
    names = {0x0C: "SN76489", 0x10: "YM2413", 0x2C: "YM2612", 0x30: "YM2151", 0x48: "YM2608",
             0x4C: "YM2610", 0x50: "YM3812", 0x98: "OKIM6295", 0xA0: "K054539"}
    found = [n for off, n in names.items() if clk(off)]
    raise ValueError("Chip não suportado na conversão direta"
                     + (f" ({', '.join(found)})" if found else "") + ". Aceitos: AY-3-8910 e YM2203.")


def convert_file(path, out_dir=None, name=None, grid=False, xgm=True, vgm=True, wav=True, log=print,
                 trim=True, routing=None):
    """Converte um VGM/VGZ e grava XGM, VGM e prévia WAV. Devolve a lista de arquivos.
    ``routing``: rotas manuais dos canais do AY-3-8910 (ver :func:`mdconv.ay.convert.plan_routes`);
    ignoradas no YM2203."""
    path = Path(path)
    chip = detect_chip(path)
    if chip == "YM2203":
        from mdconv.opn.convert import convert, read_vgm
        src = read_vgm(path)
        conv = convert(src, trim=trim)
        if conv.exact:
            return write_outputs(conv.stream, conv.report, src.gd3, path, out_dir, name, xgm, vgm, wav,
                                 log, chip, exact=(conv.exact, conv.exact_end, conv.exact_loop))
    else:
        from mdconv.ay.convert import convert, read_vgm
        src = read_vgm(path)
        conv = convert(src, grid=grid, trim=trim, routing=routing)
    return write_outputs(conv.stream, conv.report, src.gd3, path, out_dir, name, xgm, vgm, wav, log, chip)


def write_exact_vgm(events, total, loop, path, title="", game="", author="", notes=""):
    """VGM com o tempo original de cada escrita (amostras de 44,1 kHz), sem passar pela grade de
    quadros. ``events``: (amostra, ('fm', porta, reg, valor) | ('psg', byte))."""
    from mdconv.chips.emulator import NTSC_FM_CLOCK, NTSC_PSG_CLOCK
    from mdconv.export.common import all_off_writes
    from mdconv.export.vgm import _gd3
    ev = sorted(((t, i, w) for i, (t, w) in enumerate(events) if t < total), key=lambda e: (e[0], e[1]))
    ev += [(total, len(ev) + k, w) for k, w in enumerate(all_off_writes())] if loop is None else []
    body = bytearray()
    cur, loop_ofs = 0, None

    def wait(n):
        while n > 0:
            if n <= 16:
                body.append(0x70 + n - 1)
                n = 0
            else:
                k = min(n, 65535)
                body.extend(b"\x61" + struct.pack("<H", k))
                n -= k

    for t, _, w in ev:
        if loop is not None and loop_ofs is None and t >= loop:
            wait(loop - cur)
            cur = loop
            loop_ofs = len(body)
        wait(t - cur)
        cur = t
        if w[0] == "fm":
            body.extend(bytes([0x52 + w[1], w[2], w[3] & 0xFF]))
        elif w[0] == "psg":
            body.extend(bytes([0x50, w[1] & 0xFF]))
    if loop is not None and loop_ofs is None:
        wait(loop - cur)
        cur = loop
        loop_ofs = len(body)
    wait(total - cur)
    body.append(0x66)
    header = bytearray(0x100)
    data_start = 0x100
    gd3 = _gd3(title, game, author, notes)
    eof = data_start + len(body) + len(gd3)
    struct.pack_into("<4sIIIIIIII", header, 0, b"Vgm ", eof - 4, 0x171, NTSC_PSG_CLOCK, 0,
                     (data_start + len(body)) - 0x14, total,
                     (data_start + loop_ofs - 0x1C) if loop_ofs is not None else 0,
                     (total - loop) if loop is not None else 0)
    struct.pack_into("<HBB", header, 0x28, 0x0009, 16, 0)
    struct.pack_into("<I", header, 0x2C, NTSC_FM_CLOCK)
    struct.pack_into("<I", header, 0x34, data_start - 0x34)
    Path(path).write_bytes(bytes(header + body + gd3))


def write_outputs(stream, report, gd3, path, out_dir, name, xgm, vgm, wav, log, chip, exact=None, fur=True):
    from mdconv.export.vgm import write_vgm
    from mdconv.export.xgm import write_xgm
    out = Path(out_dir) if out_dir else Path(path).parent
    out.mkdir(parents=True, exist_ok=True)
    stem = name or Path(path).name.split(".")[0]
    files = []
    for line in report:
        log("  " + line)
    if xgm:
        p = out / f"{stem}.xgm"
        write_xgm(stream, p)
        files.append(p)
        log(f"  XGM: {p.name} ({p.stat().st_size} bytes)")
    if vgm:
        p = out / f"{stem}.vgm"
        kw = dict(title=gd3.get("titulo", stem), game=gd3.get("jogo", ""), author=gd3.get("autor", ""),
                  notes=f"Convertido do {chip} pelo Mega Drive Audio Converter")
        if exact:
            write_exact_vgm(exact[0], exact[1], exact[2], p, **kw)
            log(f"  VGM: {p.name} (tempo exato do original)")
        else:
            write_vgm(stream, p, **kw)
            log(f"  VGM: {p.name}")
        files.append(p)
    if fur:
        from mdconv.export.fur_stream import write_stream_fur
        p = out / f"{stem}.fur"
        try:
            r = write_stream_fur(stream, p, name=gd3.get("titulo", gd3.get("title", stem)),
                                 author=gd3.get("autor", gd3.get("author", "")),
                                 game=gd3.get("jogo", gd3.get("game", "")))
            files.append(p)
            log(f"  Furnace: {p.name} ({r.notes} notas, {r.instruments} instrumentos, {r.samples} amostras; "
                "uma linha por quadro)")
            for w_ in r.warnings + ([f"{r.merged_pcm} golpes dos canais PCM 3–4 foram para o PCM 2"]
                                    if r.merged_pcm else []):
                log(f"  ⚠ Furnace: {w_}")
        except Exception as exc:                    # o .fur é extra: não impede o resto
            log(f"  ⚠ Furnace: não gerado ({exc})")
    if wav:
        import wave

        import numpy as np

        from mdconv.render.stream_render import render_stream
        audio = render_stream(stream)
        a = np.asarray(audio[0] if isinstance(audio, tuple) else audio, dtype=np.float32)
        p = out / f"{stem}_previa.wav"
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(a.shape[1] if a.ndim == 2 else 1)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
        files.append(p)
        log(f"  Prévia: {p.name}")
    return files
