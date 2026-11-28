"""Conversão direta de VGM/VGZ para o Mega Drive, chip a chip (sem MIDI).

Chips aceitos: AY-3-8910 (:mod:`mdconv.ay`), YM2203 / OPN (:mod:`mdconv.opn`), YM2610 do Neo Geo
(:mod:`mdconv.neogeo`, a mesma conversão da extração da ROM), OPL — YM3526,
YM3812, Y8950 (:mod:`mdconv.opl`) — e VGMs que já
são do Mega Drive — YM2612 + SN76489 (:mod:`mdconv.mdvgm`, só reempacota). O chip é
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
    if d[:4] == b"XGM ":
        return "XGM"
    if d[:4] != b"Vgm ":
        raise ValueError("Não é um arquivo VGM/VGZ.")
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    data = 0x34 + rel if rel else 0x40

    def clk(off):
        return struct.unpack_from("<I", d, off)[0] & 0x3FFFFFFF if data > off + 3 else 0
    if clk(0x44):
        return "YM2203"
    if clk(0x4C):
        return "YM2610"
    if clk(0x74):
        return "AY-3-8910"
    from mdconv.mdvgm import is_md_vgm
    if is_md_vgm(d):
        return "Mega Drive"
    from mdconv.opl.convert import detect as opl_detect
    if opl_detect(d):
        return opl_detect(d)[0]
    names = {0x0C: "SN76489", 0x10: "YM2413", 0x2C: "YM2612", 0x30: "YM2151", 0x48: "YM2608",
             0x4C: "YM2610", 0x50: "YM3812", 0x98: "OKIM6295", 0xA0: "K054539"}
    found = [n for off, n in names.items() if clk(off)]
    raise ValueError("Chip não suportado na conversão direta"
                     + (f" ({', '.join(found)})" if found else "") + ". Aceitos: AY-3-8910, YM2203, YM2610 (Neo Geo), OPL (YM3526, YM3812, Y8950) e Mega Drive "
                     "(YM2612 + SN76489).")


_PCM_ATTEN_DB = 0.0          # atenuação do PCM da conversão em andamento (ver convert_file)


def convert_file(path, out_dir=None, name=None, grid=False, xgm=True, vgm=True, wav=True, log=print,
                 trim=True, routing=None, loop_start=None, loop_end=None, pcm_channel=1, opl_routing=None,
                 style=None, neo_options=None, subfolder=False, pcm_atten_db=0.0):
    """Ver :func:`_convert_file`. ``pcm_atten_db``: abaixa as amostras PCM (DAC) em tantos dB, para
    equilibrar a bateria com a melodia (o XGM não tem volume por canal PCM: o ganho vai na amostra)."""
    global _PCM_ATTEN_DB
    old, _PCM_ATTEN_DB = _PCM_ATTEN_DB, float(pcm_atten_db or 0.0)
    try:
        return _convert_file(path, out_dir, name, grid, xgm, vgm, wav, log, trim, routing, loop_start, loop_end,
                             pcm_channel, opl_routing, style, neo_options, subfolder)
    finally:
        _PCM_ATTEN_DB = old


def _convert_file(path, out_dir=None, name=None, grid=False, xgm=True, vgm=True, wav=True, log=print,
                  trim=True, routing=None, loop_start=None, loop_end=None, pcm_channel=1, opl_routing=None,
                  style=None, neo_options=None, subfolder=False):
    """Converte um VGM/VGZ e grava XGM, VGM, .fur e prévia WAV. Devolve a lista de arquivos.
    ``routing``: rotas manuais dos canais do AY-3-8910 (ver :func:`mdconv.ay.convert.plan_routes`);
    ignoradas nos outros chips. ``loop_start``/``loop_end`` (segundos, contados do começo da música
    convertida): põem ou mudam o loop; ``loop_end`` corta a música ali. ``pcm_channel``: canal PCM
    do XGM das amostras de um VGM do Mega Drive. ``style``: (arquivo de referência, opções) — ver
    :mod:`mdconv.export.restyle`. ``neo_options``: opções do Neo Geo (dicionário de
    :class:`mdconv.neogeo.Options`, as mesmas da extração) para VGM de YM2610. ``subfolder``: grava os
    arquivos numa pasta com o nome da música, dentro de ``out_dir``."""
    if subfolder:
        out_dir = Path(out_dir or Path(path).parent) / (name or Path(path).name.split(".")[0])
    from mdconv.mdvgm import apply_loop
    path = Path(path)
    chip = detect_chip(path)
    edit = loop_start is not None or loop_end is not None or bool(style)
    if chip == "XGM":
        stream, report, gd3 = read_xgm_stream(path)
        apply_loop(stream, loop_start, loop_end, report)
        # o XGM é regravado com a redução de tamanho escolhida (e o loop, se mudou), sem
        # sobrescrever o original: se cair na mesma pasta, ganha o modo no nome
        return write_outputs(stream, report, gd3, path, out_dir, name, xgm, vgm, wav, log, chip, style=style)
    if chip == "Mega Drive":
        from mdconv import mdvgm
        src = mdvgm.read_vgm(path)
        stream, report = mdvgm.convert(src, pcm_channel=pcm_channel, trim=trim, loop_start=loop_start,
                                       loop_end=loop_end)
        return write_outputs(stream, report, src.gd3, path, out_dir, name, xgm, vgm, wav, log, chip, style=style)
    if chip == "YM2610":
        from mdconv import neogeo
        board, cap, gd3 = neogeo.read_vgm(path)
        opt = neogeo.Options(**{k: (tuple(v) if k == "pcm_channels" else v)
                                for k, v in (neo_options or {}).items()
                                if k in neogeo.Options.__dataclass_fields__})
        stream, report = neogeo.to_stream(board, cap, opt)
        report = list(report) + list(cap.report)
        apply_loop(stream, loop_start, loop_end, report)
        return write_outputs(stream, report, gd3, path, out_dir, name, xgm, vgm, wav, log, chip, style=style)
    if chip in ("YM3812", "YM3526", "Y8950"):
        from mdconv.opl.convert import convert as opl_convert, read_vgm as opl_read
        src = opl_read(path)
        conv = opl_convert(src, trim=trim, routing=opl_routing)
        report = list(conv.report)
        apply_loop(conv.stream, loop_start, loop_end, report)
        return write_outputs(conv.stream, report, src.gd3, path, out_dir, name, xgm, vgm, wav, log, chip, style=style)
    if chip == "YM2203":
        from mdconv.opn.convert import convert, read_vgm
        src = read_vgm(path)
        conv = convert(src, trim=trim)
        if conv.exact and not edit:
            return write_outputs(conv.stream, conv.report, src.gd3, path, out_dir, name, xgm, vgm, wav,
                                 log, chip, exact=(conv.exact, conv.exact_end, conv.exact_loop))
    else:
        from mdconv.ay.convert import convert, read_vgm
        src = read_vgm(path)
        conv = convert(src, grid=grid, trim=trim, routing=routing)
    report = list(conv.report)
    apply_loop(conv.stream, loop_start, loop_end, report)
    return write_outputs(conv.stream, report, src.gd3, path, out_dir, name, xgm, vgm, wav, log, chip, style=style)


def read_xgm_stream(path):
    """XGM v1 (SGDK) → (Stream, relatório, gd3): as mesmas escritas e amostras, para gerar VGM,
    .fur e prévia. O XGM não guarda título: o nome do arquivo vira o título."""
    from mdconv.driver.driver import Stream
    from mdconv.export.xgm import read_xgm
    x = read_xgm(Path(path).read_bytes())
    fps = 50.0 if x.pal else 60.0
    stream = Stream(frames=x.frames, fps=fps, loop_frame=x.loop_frame, pcm_samples=x.samples,
                    pcm_rate=14000.0)
    used = sorted({w[1] for f in x.frames for w in f if w[0] == "pcm" and w[2]})
    report = [f"XGM v{x.version} ({'PAL, 50' if x.pal else 'NTSC, 60'} quadros/s): {len(x.frames)} quadros "
              f"({len(x.frames) / fps:.1f} s), {len(x.samples)} amostra(s) "
              f"({sum(len(s) for s in x.samples.values()) / 1024:.0f} KB), canais PCM "
              f"{', '.join(map(str, used)) or '—'}"
              + (f"; loop em {x.loop_frame / fps:.2f} s" if x.loop_frame is not None else "; sem loop")]
    return stream, report, {"titulo": Path(path).stem}


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


ALL_FORMATS = ("xgm", "vgm", "fur", "wav")
_FORMATS = set(ALL_FORMATS)


def set_output_formats(formats) -> None:
    """Quais arquivos cada música gera (o usuário escolhe nas caixas "XGM / VGM / Furnace / Prévia"):
    vale para todas as conversões e extrações seguintes."""
    global _FORMATS
    _FORMATS = {f for f in formats if f in ALL_FORMATS}


def output_formats() -> set:
    return set(_FORMATS)


def write_outputs(stream, report, gd3, path, out_dir, name, xgm, vgm, wav, log, chip, exact=None, fur=True,
                  style=None, pcm_atten_db=None):
    xgm, vgm, fur, wav = (xgm and "xgm" in _FORMATS, vgm and "vgm" in _FORMATS, fur and "fur" in _FORMATS,
                          wav and "wav" in _FORMATS)
    from mdconv.export.vgm import write_vgm
    from mdconv.export.xgm import write_xgm
    out = Path(out_dir) if out_dir else Path(path).parent
    out.mkdir(parents=True, exist_ok=True)
    stem = name or Path(path).name.split(".")[0]
    files = []
    styled = []
    if style and style[1]:
        from mdconv.export.restyle import apply_style, read_style
        ref = style[0] if hasattr(style[0], "algfb") else read_style(style[0])
        stream, styled = apply_style(stream, ref, style[1])
    atten = _PCM_ATTEN_DB if pcm_atten_db is None else float(pcm_atten_db)
    if atten > 0 and getattr(stream, "pcm_samples", None):
        import numpy as np
        g = 10 ** (-atten / 20)
        stream.pcm_samples = {k: np.clip(np.round(np.asarray(v, np.float32) * g), -128, 127).astype(np.int8)
                              for k, v in stream.pcm_samples.items()}
        report = list(report) + [f"PCM (DAC) atenuado em {atten:g} dB"]
    from mdconv.export.optimize import optimize_stream
    stream, opt = optimize_stream(stream)
    for line in list(report) + styled + opt:
        log("  " + line)
    if xgm:
        p = out / f"{stem}.xgm"
        if p.resolve() == Path(path).resolve():
            from mdconv.export import optimize
            p = out / f"{stem}_{optimize.mode()}.xgm"
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
