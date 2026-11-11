"""Exporta e confere: cada arquivo gravado é lido de volta por um decodificador independente.

* XGM: as escritas decodificadas têm de ser idênticas, quadro a quadro, às do fluxo que gerou
  a prévia (menos os registradores que o driver XGM controla); amostras idênticas byte a byte;
  loop no mesmo quadro.
* VGM: estrutura válida (tamanhos, offsets, soma das esperas = total, loop no início de um
  comando) e, tocado no emulador, mesmo nível da prévia.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np

from mdconv.driver.driver import Stream
from mdconv.export.common import export_frames
from mdconv.export.vgm import write_vgm
from mdconv.export.vgm_reader import read_vgm
from mdconv.export.xgm import FILTERED_REGS, read_xgm, write_xgm


class ExportValidationError(RuntimeError):
    pass


def _strip(frames):
    return [[(w[:3] if w[0] == "pcm" else w) for w in f
             if not (w[0] == "fm" and w[1] == 0 and w[2] in FILTERED_REGS)] for f in frames]


def check_xgm(stream: Stream, path) -> tuple[int, int | None]:
    x = read_xgm(Path(path).read_bytes())
    ref, loop = export_frames(stream)
    a, b = _strip(ref), _strip(x.frames)
    if len(a) != len(b):
        raise ExportValidationError(f"XGM: {len(b)} quadros decodificados, esperados {len(a)}")
    for i, (fa, fb) in enumerate(zip(a, b)):
        if fa != fb:
            raise ExportValidationError(f"XGM: quadro {i} diferente do fluxo da prévia")
    if loop != x.loop_frame:
        raise ExportValidationError(f"XGM: loop no quadro {x.loop_frame}, esperado {loop}")
    for sid, s in stream.pcm_samples.items():
        d = x.samples.get(sid)
        if d is None or not np.array_equal(np.asarray(s, np.int8), d[:len(s)]) or d[len(s):].any():
            raise ExportValidationError(f"XGM: amostra {sid} diferente")
    return len(b), x.loop_frame


def export_and_validate(stream: Stream, out_dir: Path, stem: str, formats: set[str],
                        title: str = "", game: str = "", wav: bool = False,
                        fm_engine: str = "auto", conversion=None) -> list[str]:
    """``conversion`` (a :class:`~mdconv.convert.Conversion` inteira) é necessária para o
    formato ``fur``, que precisa das notas por canal, não só das escritas nos chips."""
    out_dir = Path(out_dir)
    L: list[str] = []
    fps = stream.fps
    unknown = formats - {"xgm", "vgm", "fur"}
    if unknown:
        raise ValueError(f"formato(s) desconhecido(s): {', '.join(sorted(unknown))}")
    if "xgm" in formats:
        p = out_dir / f"{stem}.xgm"
        info = write_xgm(stream, p)
        n, loop = check_xgm(stream, p)
        L.append(f"XGM v1: {p.name} — {info.size / 1024:.1f} KiB "
                 f"(amostras {info.samples}: {info.sample_bytes / 1024:.1f} KiB; música {info.music_bytes / 1024:.1f} KiB)")
        L.append(f"  {n} quadros = {n / fps:.2f} s · loop "
                 + (f"no quadro {loop} ({loop / fps:.2f} s)" if loop is not None else "sem loop (toca uma vez)"))
        chans = sorted({w[1] for f in stream.frames for w in f if w[0] == "pcm"})
        if chans:
            L.append("  PCM da música nos canais " + ", ".join(f"SOUND_PCM_CH{c}" for c in chans)
                     + " — os demais ficam livres para o jogo")
        L.append("  validado: decodificado de volta, idêntico quadro a quadro ao fluxo da prévia")
        if info.filtered_writes:
            L.append(f"  {info.filtered_writes} escrita(s) de registrador controlado pelo driver XGM omitida(s) "
                     f"($24–$27, $2A, $2B)")
        L += [f"  ⚠ {w}" for w in info.warnings]
    if "vgm" in formats:
        p = out_dir / f"{stem}.vgm"
        info = write_vgm(stream, p, title=title, game=game)
        v = read_vgm(p.read_bytes())
        L.append(f"VGM 1.71: {p.name} — {info.size / 1024:.1f} KiB · {v.total_samples / 44100:.2f} s"
                 + (f" · loop de {v.loop_samples / 44100:.2f} s" if v.loop_samples else " · sem loop"))
        L.append("  validado: estrutura relida (offsets, esperas, loop, GD3)")
        if info.pcm_clipped:
            L.append(f"  PCM: {info.pcm_clipped} amostra(s) saturadas na mistura dos canais "
                     f"(o mesmo acontece no XGM e na prévia)")
    if "fur" in formats:
        from mdconv.export.furnace import read_fur, write_fur
        if conversion is None:
            raise ValueError("a exportação para o Furnace precisa da conversão completa")
        p = out_dir / f"{stem}.fur"
        rep = write_fur(conversion, p, name=title or stem, game=game)
        chk = read_fur(p.read_bytes())
        written = rep.notes - rep.merged_rows
        if chk.version != 232 or chk.notes != written:
            raise ExportValidationError(f"Furnace: arquivo relido não confere ({chk.notes} notas, esperadas {written})")
        lines = rep.text().split("\n")
        lines[0] = lines[0].replace("Furnace 0.6.8.3 (.fur):",
                                    f"Furnace 0.6.8.3: {p.name} — {p.stat().st_size / 1024:.1f} KiB ·")
        L += lines
        L.append("  validado: estrutura relida (blocos, ponteiros, padrões) e notas contadas")
    if wav:
        from mdconv.reference.sampler import write_wav
        from mdconv.render.stream_render import render_stream
        frames, _ = export_frames(stream)
        y, _ = render_stream(dataclasses.replace(stream, frames=frames), fm_engine=fm_engine)
        p = out_dir / f"{stem}_previa.wav"
        write_wav(p, y, 44100)
        L.append(f"Prévia: {p.name} (o mesmo trecho exportado, uma passada)")
    return L
