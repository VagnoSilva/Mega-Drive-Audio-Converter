"""Patches no formato TFI (TFM Music Maker), lidos e gravados.

Formato (Shiru, autor do TFM Music Maker): sempre 42 bytes, 1 byte por parâmetro::

    0  algoritmo (0–7)
    1  feedback (0–7)
    2… 4 operadores × 10 bytes, na ordem dos registradores: S1, S3, S2, S4:
       MUL (0–15), DT (0–7: 0 = −3, 3 = 0, 6 = +3), TL (0–127), RS (0–3), AR (0–31),
       DR/D1R (0–31), SR/D2R (0–31), RR (0–15), SL (0–15), SSG-EG (0–15; < 8 = desligado)

A descrição original fala em "operadores 1, 2, 3, 4", mas é a ordem dos registradores
($30, $34, $38, $3C = S1, S3, S2, S4), não a numeração lógica. Conferido em 30 mil
patches únicos (bancos de 638 jogos e instrumentos do próprio autor do TFM): no algoritmo 4,
o único em que a diferença aparece, as portadoras (S2 e S4) só ficam mais fortes que os
moduladores lendo assim. ``Patch.ops`` usa a numeração lógica (S1, S2, S3, S4).
O TFI não guarda AMS/FMS/AM (LFO): ficam desligados.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mdconv.fm.patch import Operator, Patch

TFI_SIZE = 42
FILE_TO_LOGICAL = (0, 2, 1, 3)       # posição no arquivo → índice em Patch.ops


class TfiError(ValueError):
    pass


@dataclass
class TfiInfo:
    patch: Patch
    ssg_eg: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    warnings: list[str] = field(default_factory=list)

    @property
    def uses_ssg_eg(self) -> bool:
        return any(v >= 8 for v in self.ssg_eg)


def _rng(name, v, hi, warnings, lo=0):
    if not lo <= v <= hi:
        warnings.append(f"{name}={v} fora de {lo}–{hi}; ajustado")
        return max(lo, min(hi, v))
    return v


def parse_tfi(data: bytes, name: str = "") -> TfiInfo:
    if len(data) != TFI_SIZE:
        raise TfiError(f"TFI deve ter {TFI_SIZE} bytes (tem {len(data)})")
    w: list[str] = []
    alg = _rng("algoritmo", data[0], 7, w)
    fb = _rng("feedback", data[1], 7, w)
    ops: list = [None] * 4
    ssg = [0] * 4
    for pos in range(4):
        i = FILE_TO_LOGICAL[pos]
        b = data[2 + 10 * pos: 12 + 10 * pos]
        tag = f"op{i + 1}."
        dt_raw = b[1]
        if dt_raw > 6:
            w.append(f"{tag}DT={dt_raw} (fora de 0–6); tratado como +3")
        ops[i] = (Operator(
            mul=_rng(tag + "MUL", b[0], 15, w),
            dt=max(-3, min(3, dt_raw - 3)),
            tl=_rng(tag + "TL", b[2], 127, w),
            ks=_rng(tag + "RS", b[3], 3, w),
            ar=_rng(tag + "AR", b[4], 31, w),
            d1r=_rng(tag + "DR", b[5], 31, w),
            d2r=_rng(tag + "SR", b[6], 31, w),
            rr=_rng(tag + "RR", b[7], 15, w),
            sl=_rng(tag + "SL", b[8], 15, w)))
        ssg[i] = b[9] & 15
    info = TfiInfo(Patch(alg=alg, fb=fb, ops=ops, name=name), ssg, w)
    if info.uses_ssg_eg:
        w.append("usa SSG-EG (envelope repetido): o conversor ainda não reproduz isso; "
                 "o patch foi importado sem ele")
    return info


def read_tfi(path: str | Path) -> TfiInfo:
    path = Path(path)
    return parse_tfi(path.read_bytes(), name=path.stem)


def to_tfi(patch: Patch, ssg_eg: list[int] | None = None) -> bytes:
    ssg = ssg_eg or [0, 0, 0, 0]
    out = bytearray([patch.alg & 7, patch.fb & 7])
    for pos in range(4):
        i = FILE_TO_LOGICAL[pos]
        o, s = patch.ops[i], ssg[i]
        out += bytes([o.mul & 15, max(0, min(6, o.dt + 3)), o.tl & 127, o.ks & 3, o.ar & 31,
                      o.d1r & 31, o.d2r & 31, o.rr & 15, o.sl & 15, s & 15])
    return bytes(out)


def write_tfi(patch: Patch, path: str | Path) -> Path:
    path = Path(path)
    path.write_bytes(to_tfi(patch))
    return path


def carrier_order_score(infos: list[TfiInfo]) -> tuple[float, float, int]:
    """Confere a ordem dos operadores pelos patches de algoritmo 4 (S1→S2, S3→S4), o único
    em que trocar S2 com S3 muda quem é portadora. Devolve (fração com separação clara —
    portadoras mais fortes que os dois moduladores — na leitura usada, idem na leitura
    trocada, nº de patches de algoritmo 4). A leitura certa tem a fração bem maior."""
    ok = alt = n = 0
    for i in infos:
        if i.patch.alg != 4:
            continue
        t = [o.tl for o in i.patch.ops]
        n += 1
        ok += max(t[1], t[3]) < min(t[0], t[2])
        alt += max(t[2], t[3]) < min(t[0], t[1])
    return (ok / n, alt / n, n) if n else (1.0, 0.0, 0)
