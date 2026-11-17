"""Efeitos sonoros de chip (AY-3-8910) → Mega Drive, sem amostra: PSG + FM.

O efeito vem de um VGM de AY (ou de um script interpretado, como os do Moon Patrol em
``mdconv.mpsl``) e passa pelo mesmo conversor das músicas (``mdconv.ay.convert``). Depois, as
escritas são presas aos canais que o jogo deixa livres para efeitos — por padrão os canais 1 e 2
do PSG e os FM 4 e 5, que as músicas convertidas do AY quase não usam — e sai:

* ``nome.vgm``: o efeito sozinho, para ouvir ou converter;
* ``nome_previa.wav``: o mesmo, renderizado pelo emulador;
* ``nome.c``: tabela de escritas por quadro (60 Hz) e duas funções para o SGDK,
  ``nome_play()`` e ``nome_update()`` (uma vez por quadro).

Tons graves demais para o PSG (abaixo de ~109 Hz) vão para o FM como onda quadrada.
"""
from __future__ import annotations

import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mdconv.ay.convert import AyVgm, convert, read_vgm
from mdconv.driver.driver import Stream

FPS = 60
DEFAULT_PSG = (0, 1)          # canais 1 e 2 do PSG
DEFAULT_FM = (3, 4)           # FM 4 e FM 5 (o FM 6 fica com o PCM do XGM)


class ChipSfxError(Exception):
    pass


@dataclass
class ChipSfx:
    name: str
    frames: list               # por quadro: ("psg", byte) / ("fm", porta, reg, valor)
    report: list = field(default_factory=list)
    loop: int | None = None    # quadro para onde volta (efeitos contínuos); None = toca uma vez
    stop: list = field(default_factory=list)    # escritas que calam os canais do efeito

    @property
    def n_writes(self) -> int:
        return sum(len(f) for f in self.frames)


def _c_name(name: str) -> str:
    import unicodedata
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"_+", "_", re.sub(r"[^0-9a-zA-Z_]", "_", name)).strip("_").lower() or "sfx"
    return "sfx_" + s if s[0].isdigit() else s


def _fm_ch(port: int, reg: int, val: int) -> int | None:
    """Canal FM (0–5) de uma escrita, ou None para registradores globais."""
    if port == 0 and reg == 0x28:
        c = val & 7
        return None if c == 3 or c == 7 else (c if c < 3 else c - 1)
    if reg < 0x30 or (reg & 3) == 3:
        return None
    return (reg & 3) + 3 * port


def remap(frames: list, psg_free=DEFAULT_PSG, fm_free=DEFAULT_FM, loop: bool = False):
    """Prende as escritas aos canais livres. Os canais de tom do PSG usados pelo efeito são
    levados, em ordem, para ``psg_free``; escritas em outros canais (os "mudos" que o conversor
    põe no começo) e registradores globais do FM são descartados."""
    report = []
    def sounding(b):           # latch de volume audível (não mudo)
        return b & 0x90 == 0x90 and b & 0x0F != 0x0F

    tone_used = sorted({(w[1] >> 5) & 3 for f in frames for w in f
                        if w[0] == "psg" and sounding(w[1]) and ((w[1] >> 5) & 3) < 3})
    noise_used = any(w[0] == "psg" and sounding(w[1]) and (w[1] >> 5) & 3 == 3 for f in frames for w in f)
    psg_free = list(psg_free)
    clock3 = noise_used and any(w[0] == "psg" and w[1] & 0xF0 == 0xE0 and w[1] & 3 == 3 for f in frames for w in f)
    cmap = {}
    if clock3:
        # o ruído é cadenciado pelo tom do canal 3: esse canal fica no lugar (se também soar, o tom
        # e o ruído saem juntos, como o conversor montou) e os outros tons vão para os livres
        cmap[2] = 2
        report.append("Atenção: o ruído usa o tom do canal 3 do PSG como relógio")
        psg_free = [c for c in psg_free if c != 2]
    rest = [c for c in tone_used if c not in cmap]
    if len(rest) > len(psg_free):
        extra = [c for c in (0, 1, 2) if c not in psg_free and c not in cmap.values()][:len(rest) - len(psg_free)]
        psg_free += extra
        report.append("Atenção: o efeito precisa de mais canais de tom do PSG; usa também o(s) canal(is) "
                      + ", ".join(str(c + 1) for c in extra) + ", que a música também pode usar")
    if len(rest) > len(psg_free):
        raise ChipSfxError("o efeito usa mais canais de tom do que o PSG tem")
    cmap.update({c: psg_free[i] for i, c in enumerate(rest)})
    if noise_used:
        cmap[3] = 3
        report.append("Atenção: o efeito usa o canal de ruído do PSG (o 4º), que a música também pode usar")
    fm_used = sorted({c for f in frames for w in f if w[0] == "fm"
                      for c in [_fm_ch(w[1], w[2], w[3])] if c is not None})
    if any(c not in fm_free for c in fm_used):
        raise ChipSfxError(f"o efeito usa os canais FM {[c + 1 for c in fm_used]}, fora dos livres "
                           f"{[c + 1 for c in fm_free]}")
    out = []
    for f in frames:
        nf = []
        keep = False
        for w in f:
            if w[0] == "psg":
                b = w[1] & 0xFF
                if b & 0x80:
                    ch = (b >> 5) & 3
                    keep = ch in cmap
                    if keep:
                        nf.append(("psg", (b & 0x9F) | (cmap[ch] << 5)))
                elif keep:
                    nf.append(("psg", b))
            elif w[0] == "fm":
                if _fm_ch(w[1], w[2], w[3]) is not None:
                    nf.append(w)
        out.append(nf)
    # fim garantido: solta as notas e cala os canais usados
    end = [("psg", 0x90 | (c << 5) | 0x0F) for c in sorted(set(cmap.values()))]
    end += [("fm", 0, 0x28, c if c < 3 else c + 1) for c in fm_used]
    if not loop:
        while out and not out[-1]:
            out.pop()
        out.append(end)
    for c, d in sorted(cmap.items()):
        if c < 3:
            report.append(f"PSG: tom → canal {d + 1}")
    for c in fm_used:
        report.append(f"FM: canal {c + 1} (onda quadrada)")
    return out, report, end


def from_ay(vgm: AyVgm, name: str, psg_free=DEFAULT_PSG, fm_free=DEFAULT_FM) -> ChipSfx:
    """Converte o efeito e o prende aos canais livres. Tons que caberiam no PSG mas não acham canal
    de tom livre (``psg_free`` curto ou vazio) vão para um FM livre, como onda quadrada — assim um
    efeito pode ficar todo no FM e não brigar com outro pelo mesmo canal do PSG."""
    psg_free, fm_free = list(psg_free), list(fm_free)
    trim = vgm.loop is None
    conv = convert(vgm, trim=trim, fm_slots=fm_free)
    on_psg = [k for k, r in sorted(conv.routes.items()) if r and r[0] == "psg"]
    if len(on_psg) > len(psg_free):
        used_fm = {r[1] for r in conv.routes.values() if r and r[0] == "fm"}
        spare = [c for c in fm_free if c not in used_fm]
        routing = dict(conv.routes)
        moved = False
        for k in on_psg[len(psg_free):]:
            if not spare:
                break
            routing[k] = ("fm", spare.pop(0))
            moved = True
        if moved:
            conv = convert(vgm, trim=trim, fm_slots=fm_free, routing=routing)
    frames = [list(f) for f in conv.stream.frames]
    loop = conv.stream.loop_frame
    if loop is not None:
        frames = frames[:conv.stream.stats.get("loop_end_frame", len(frames))]
    frames, rep, stop = remap(frames, psg_free, fm_free, loop=loop is not None)
    if loop is not None:
        rep.append(f"Contínuo: repete a partir do quadro {loop} (a cada {len(frames) - loop} quadros) "
                   "até chamar _stop()")
    return ChipSfx(name, frames, conv.report[1:] + rep, loop, stop)


def from_vgm(path, name: str | None = None, **kw) -> ChipSfx:
    return from_ay(read_vgm(path), name or Path(path).stem, **kw)


def stream(sfx: ChipSfx) -> Stream:
    if sfx.loop is not None:
        st = Stream(frames=[list(f) for f in sfx.frames], fps=FPS, loop_frame=sfx.loop, pcm_samples={},
                    pcm_rate=14000.0)
        st.stats["loop_end_frame"] = len(sfx.frames)
        st.stats["seamless_loop"] = True
        return st
    return Stream(frames=[list(f) for f in sfx.frames] + [[] for _ in range(6)], fps=FPS, loop_frame=None,
                  pcm_samples={}, pcm_rate=14000.0)


def c_source(sfx: ChipSfx) -> str:
    n = _c_name(sfx.name)
    rows, idx = [], [0]
    for f in sfx.frames:
        for w in f:
            rows.append((0, 0, w[1] & 0xFF) if w[0] == "psg" else (1 + w[1], w[2], w[3] & 0xFF))
        idx.append(len(rows))
    stop = ", ".join("{%d,0x%02X,0x%02X}" % ((0, 0, w[1] & 0xFF) if w[0] == "psg" else (1 + w[1], w[2], w[3]))
                     for w in sfx.stop)
    body = ",\n    ".join(", ".join("{%d,0x%02X,0x%02X}" % r for r in rows[i:i + 8]) for i in range(0, len(rows), 8))
    lines = [f"// Efeito \"{sfx.name}\" para o Mega Drive (Mega Drive Audio Converter)"]
    lines += [f"//   {r}" for r in sfx.report]
    lines += [
        "// Uso: chame " + n + "_play() para disparar e " + n + "_update() uma vez por quadro",
        "// (por exemplo logo depois de SYS_doVBlankProcess()); " + n + "_stop() cala na hora.",
        "#include <genesis.h>", "",
        "#ifndef MDC_PSG_PORT",
        "#define MDC_PSG_PORT (*((vu8*) 0xC00011))",
        "#endif", "",
        f"#define {n.upper()}_FRAMES {len(sfx.frames)}",
        "// {destino, registrador, valor}: destino 0 = PSG (byte cru), 1 = YM2612 porta 0, 2 = YM2612 porta 1",
        f"static const u8 {n}_w[][3] = {{\n    {body}\n}};",
        f"static const u16 {n}_idx[{n.upper()}_FRAMES + 1] = {{{', '.join(map(str, idx))}}};", "",
        f"static s16 {n}_pos = -1;", "",
        f"static const u8 {n}_stop_w[][3] = {{{stop}}};", "",
        f"void {n}_play(void) {{ {n}_pos = 0; }}", "",
        f"void {n}_stop(void)", "{",
        f"    for (u16 i = 0; i < sizeof({n}_stop_w) / 3; i++) {{",
        f"        const u8 *w = {n}_stop_w[i];",
        "        if (w[0] == 0) MDC_PSG_PORT = w[2];",
        "        else YM2612_writeRegSafe(w[0] - 1, w[1], w[2]);",
        "    }",
        f"    {n}_pos = -1;", "}", "",
        f"void {n}_update(void)", "{",
        f"    if ({n}_pos < 0) return;",
        f"    for (u16 i = {n}_idx[{n}_pos]; i < {n}_idx[{n}_pos + 1]; i++) {{",
        f"        const u8 *w = {n}_w[i];",
        "        if (w[0] == 0) MDC_PSG_PORT = w[2];",
        "        else YM2612_writeRegSafe(w[0] - 1, w[1], w[2]);",
        "    }",
        f"    if (++{n}_pos >= {n.upper()}_FRAMES) {n}_pos = " + ("-1;" if sfx.loop is None else f"{sfx.loop};   // contínuo: volta ao loop"),
        "}", ""]
    return "\n".join(lines)


def write_outputs(sfx: ChipSfx, out_dir, preview: bool = True, game: str = "") -> list[Path]:
    from mdconv.export.vgm import write_vgm
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = out / _c_name(sfx.name)
    st = stream(sfx)
    paths = [base.with_suffix(".vgm"), base.with_suffix(".c")]
    write_vgm(st, paths[0], title=sfx.name, game=game, notes="Efeito convertido pelo Mega Drive Audio Converter")
    paths[1].write_text(c_source(sfx), encoding="utf-8")
    if preview:
        from mdconv.render.stream_render import render_stream
        a = render_stream(st)[0]
        a = a.mean(1) if a.ndim == 2 else a
        pk = float(np.abs(a).max()) or 1.0
        p = base.parent / (base.name + "_previa.wav")
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes((a / pk * 0.9 * 32767).astype("<i2").tobytes())
        paths.append(p)
    return paths
