"""AY-3-8910 (VGM) → YM2612 + SN76489 (fluxo de quadros a 60 Hz → XGM / VGM / prévia).

Tradução:
* **Tons** dos canais A, B, C do 1º AY → canais de tom 1, 2, 3 do PSG (mesma frequência,
  volume convertido em dB). Um canal cujas notas descem abaixo do limite do PSG (~109 Hz) vai
  inteiro para o FM, com um patch de onda quadrada (soma de harmônicos ímpares, algoritmo 7).
  Os canais de um 2º AY também vão para o FM.
* **Ruído** → canal de ruído do PSG. Quando o tom 3 do PSG está livre, o ruído é cadenciado
  por ele (modo "tom 3"), com a mesma taxa do ruído do AY; senão usa a taxa fixa mais aguda
  (N/512).
* **Envelope de hardware** do AY (registradores 11–13) → recalculado quadro a quadro como
  volume (o PSG não tem envelope).
* **Volume**: o AY tem ~3 dB por nível; o PSG 2 dB por passo; o FM 0,75 dB por passo de TL.
* **Início**: o silêncio antes da primeira nota (comum nos VGMs gravados de arcades) é
  removido; o loop é deslocado junto.
* **Tempo**: cada escrita cai no quadro de 1/60 s mais próximo. Opcional: ``grid=True`` ajusta
  o andamento (até 6%) para o passo rítmico durar um número inteiro de quadros.
* **Rotas manuais** (``routing``): cada canal do AY pode ir para um canal escolhido do PSG ou do
  FM, ou ser omitido; os canais deixados em "automático" seguem as regras acima.
"""
from __future__ import annotations

import gzip
import math
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from mdconv.chips.emulator import NTSC_FM_CLOCK, NTSC_PSG_CLOCK
from mdconv.driver.driver import Stream
from mdconv.fm.patch import SLOT_OFS, Operator, Patch, freq_to_block_fnum

VGM_RATE = 44100
FPS = 60.0
AY_STEP_DB = 3.0           # cada nível de volume do AY ≈ 3 dB
PSG_STEP_DB = 2.0
TL_STEP_DB = 0.75
PSG_MAX_PERIOD = 1023
PSG_MIN_HZ = NTSC_PSG_CLOCK / (32 * PSG_MAX_PERIOD)


# ============================================================================ leitura do VGM
@dataclass
class AyVgm:
    clock: int
    dual: bool
    writes: list            # (amostra, chip, reg, valor)
    end: int                # em amostras de 44,1 kHz
    loop: int | None        # amostra do ponto de loop
    gd3: dict = field(default_factory=dict)


def _gd3(d: bytes, off: int) -> dict:
    if not off or off + 12 > len(d) or d[off:off + 4] != b"Gd3 ":
        return {}
    n = struct.unpack_from("<I", d, off + 8)[0]
    parts = d[off + 12: off + 12 + n].decode("utf-16-le", "replace").split("\0")
    keys = ["titulo", "titulo_jp", "jogo", "jogo_jp", "sistema", "sistema_jp", "autor", "autor_jp",
            "data", "ripador", "notas"]
    return {k: v for k, v in zip(keys, parts) if v}


def read_vgm(path) -> AyVgm:
    d = Path(path).read_bytes()
    if d[:2] == b"\x1f\x8b":
        d = gzip.decompress(d)
    if d[:4] != b"Vgm ":
        raise ValueError("Não é um arquivo VGM/VGZ.")
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    data = 0x34 + rel if rel else 0x40
    ay = struct.unpack_from("<I", d, 0x74)[0] if data > 0x77 else 0
    if not ay & 0x3FFFFFFF:
        raise ValueError("Este VGM não tem AY-3-8910 (o conversor direto só trata esse chip).")
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
        if c == 0xA0:
            writes.append((t, d[p + 1] >> 7, d[p + 1] & 0x0F, d[p + 2]))
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
        elif 0x40 <= c <= 0x5F or 0xA1 <= c <= 0xBF:
            p += 3
        elif 0xC0 <= c <= 0xDF:
            p += 4
        elif 0xE0 <= c <= 0xFF:
            p += 5
        else:
            p += 1
    return AyVgm(ay & 0x3FFFFFFF, bool(ay & 0x40000000), writes, t, loop_t,
                 _gd3(d, 0x14 + gd3_rel if gd3_rel else 0))


# ============================================================================ estado do AY
def env_level(shape: int, period: int, clock: int, dt: float) -> int:
    """Nível (0–15) do envelope do AY ``dt`` segundos depois do disparo (escrita no R13)."""
    step = 16 * max(1, period) / clock
    k = int(dt / step) if dt > 0 else 0
    att, cont, alt, hold = bool(shape & 4), bool(shape & 8), bool(shape & 2), bool(shape & 1)
    if not cont:                         # formas 0–7 = 9 ou 15
        hold, alt = True, att
    cycle, pos = divmod(k, 16)
    if hold and cycle >= 1:
        final = 15 if att else 0
        return (15 - final) if alt else final
    up = att ^ (alt and cycle % 2 == 1)
    return pos if up else 15 - pos


class _Ay:
    def __init__(self):
        self.r = [0] * 16
        self.r[7] = 0xFF
        self.env_t = None               # instante (s) do último disparo do envelope

    def write(self, reg, val, t):
        self.r[reg] = val
        if reg == 13:
            self.env_t = t

    def period(self, ch):
        return max(1, self.r[2 * ch] | ((self.r[2 * ch + 1] & 15) << 8))

    def level(self, ch, t, clock):
        v = self.r[8 + ch]
        if v & 0x10:
            if self.env_t is None:
                return 0
            ep = self.r[11] | (self.r[12] << 8)
            return env_level(self.r[13] & 15, ep, clock, t - self.env_t)
        return v & 15

    def tone_on(self, ch):
        return not (self.r[7] >> ch) & 1

    def noise_on(self, ch):
        return not (self.r[7] >> (ch + 3)) & 1


def _att_steps(level: int, step_db: float) -> int:
    """Nível do AY (0–15) → passos de atenuação do destino (None = mudo)."""
    if level <= 0:
        return None
    return int(round((15 - level) * AY_STEP_DB / step_db))


def square_patch() -> Patch:
    """Onda quadrada no FM: 4 portadoras com harmônicos 1, 3, 5, 7 em 1, 1/3, 1/5, 1/7."""
    ops = []
    for mul, amp in ((1, 1.0), (3, 1 / 3), (5, 1 / 5), (7, 1 / 7)):
        tl = int(round(-20 * math.log10(amp) / TL_STEP_DB)) + 6
        ops.append(Operator(mul=mul, tl=tl, ar=31, d1r=0, d2r=0, sl=0, rr=14))
    return Patch(alg=7, fb=0, ops=ops, name="Quadrada (AY)")


def first_sound(vgm: AyVgm) -> int:
    """Amostra (44,1 kHz) em que algum canal começa a soar (tom ou ruído com volume > 0)."""
    chips = [_Ay(), _Ay()]
    for t, c, r, v in sorted(vgm.writes, key=lambda x: x[0]):
        ay = chips[c & 1]
        ay.write(r, v, t / VGM_RATE)
        if r in (7, 8, 9, 10, 13):
            for ch in range(3):
                if (ay.tone_on(ch) or ay.noise_on(ch)) and ay.level(ch, t / VGM_RATE, vgm.clock) > 0:
                    return t
    return 0


def trim_start(vgm: AyVgm) -> tuple[AyVgm, float]:
    """Tira o silêncio antes da primeira nota. As escritas anteriores (afinação, mixer…) passam
    a valer no instante 0; o loop é deslocado junto. Devolve (vgm novo, segundos removidos)."""
    t0 = first_sound(vgm)
    if t0 <= 0:
        return vgm, 0.0
    writes = [(max(0, t - t0), c, r, v) for t, c, r, v in vgm.writes]
    loop = None if vgm.loop is None else max(0, vgm.loop - t0)
    return AyVgm(vgm.clock, vgm.dual, writes, max(1, vgm.end - t0), loop, vgm.gd3), t0 / VGM_RATE


# ============================================================================ rotas
AUTO = "auto"
CH_NAMES = "ABC"


def route_label(key) -> str:
    c, ch = key
    return f"AY{c + 1} canal {CH_NAMES[ch]}"


def dest_label(dest) -> str:
    if dest is None:
        return "omitido"
    if dest == AUTO:
        return "automático"
    return f"PSG tom {dest[1] + 1}" if dest[0] == "psg" else f"FM {dest[1] + 1}"


def check_routing(routing: dict) -> list[str]:
    """Erros de uma tabela de rotas manuais (destino repetido, canal inexistente)."""
    errs, seen = [], {}
    for key, dest in sorted((routing or {}).items()):
        if dest is None or dest == AUTO:
            continue
        kind, n = dest
        if kind not in ("psg", "fm") or not 0 <= n < (3 if kind == "psg" else 6):
            errs.append(f"{route_label(key)}: destino inválido {dest!r}")
        elif (kind, n) in seen:
            errs.append(f"{route_label(key)} e {route_label(seen[(kind, n)])} vão os dois para "
                        f"{dest_label(dest)}")
        else:
            seen[(kind, n)] = key
    return errs


def plan_routes(lowest: dict, fm_slots, routing: dict | None = None) -> dict:
    """Destino de cada canal do AY que toca. ``lowest``: {(chip, canal): Hz mais grave}.
    ``routing``: {(chip, canal): "auto" | None (omitir) | ("psg", 0–2) | ("fm", 0–5)}.
    Os destinos manuais vêm primeiro; os automáticos ocupam o que sobrou: tom grave o bastante
    do 1º AY → PSG (de preferência o tom de mesmo número); senão FM livre; senão PSG livre."""
    routing = routing or {}
    errs = check_routing({k: v for k, v in routing.items() if k in lowest})
    if errs:
        raise ValueError("Rotas dos canais: " + "; ".join(errs))
    routes, used_psg, used_fm = {}, set(), set()
    for key in sorted(lowest):
        dest = routing.get(key, AUTO)
        if dest == AUTO:
            continue
        if dest is not None:
            dest = (dest[0], int(dest[1]))
            (used_psg if dest[0] == "psg" else used_fm).add(dest[1])
        routes[key] = dest
    free_fm = [s for s in fm_slots if s not in used_fm]
    for key in sorted(lowest):
        if key in routes:
            continue
        c, ch = key
        fits = lowest[key] >= PSG_MIN_HZ * 0.995
        free_psg = [p for p in (ch, 0, 1, 2) if p not in used_psg]
        if c == 0 and fits and free_psg:
            dest = ("psg", free_psg[0])
        elif free_fm:
            dest = ("fm", free_fm.pop(0))
        elif free_psg:
            dest = ("psg", free_psg[0])
        else:
            dest = None
        if dest is not None:
            (used_psg if dest[0] == "psg" else used_fm).add(dest[1])
        routes[key] = dest
    return routes


# ============================================================================ conversão
@dataclass
class AyConversion:
    stream: Stream
    report: list[str]
    vgm: AyVgm
    routes: dict


def _grid_scale(vgm: AyVgm) -> tuple[float, str]:
    """Fator de tempo para o passo rítmico durar um número inteiro de quadros (até ±6%)."""
    onsets = sorted({t for t, c, r, v in vgm.writes if r == 13 or (r in (8, 9, 10) and v)})
    gaps = Counter()
    for a, b in zip(onsets, onsets[1:]):
        g = b - a
        if g > VGM_RATE / 30:                 # ignora rajadas dentro do mesmo quadro
            gaps[round(g / 50) * 50] += 1
    if not gaps:
        return 1.0, "sem passo rítmico claro; andamento mantido"
    unit = min(g for g, n in gaps.most_common(4))
    frames = unit * FPS / VGM_RATE
    for mult in (1, 2):
        target = round(frames * mult)
        if target >= 1 and abs(target / (frames * mult) - 1) <= 0.06:
            s = target / (frames * mult)
            return s, (f"passo de {frames:.2f} quadros → {target / mult:g} quadros "
                       f"(andamento {100 * (1 / s - 1):+.1f}%)")
    return 1.0, f"passo de {frames:.2f} quadros: ajuste maior que 6%, andamento mantido"


def convert(vgm: AyVgm, grid: bool = False, trim: bool = True, fm_slots=None,
            routing: dict | None = None) -> AyConversion:
    """``fm_slots``: canais FM livres para as partes graves / 2º chip (padrão: os 6).
    Sem canal FM livre, a parte fica no PSG (notas graves demais sobem uma oitava).
    ``routing``: rotas manuais por canal do AY (ver :func:`plan_routes`)."""
    fm_slots = list(range(6)) if fm_slots is None else list(fm_slots)
    cut = 0.0
    if trim:
        vgm, cut = trim_start(vgm)
    clock = vgm.clock
    nchips = 2 if vgm.dual or any(c == 1 for _, c, _, _ in vgm.writes) else 1
    report = [f"AY-3-8910 a {clock} Hz, {nchips} chip(s), {len(vgm.writes)} escritas, "
              f"{vgm.end / VGM_RATE:.1f} s" + (f", loop em {vgm.loop / VGM_RATE:.2f} s" if vgm.loop else ", sem loop")]
    if cut > 0:
        report.append(f"Silêncio do início removido: {cut:.2f} s (a música começa na primeira nota)")
    scale, why = _grid_scale(vgm) if grid else (1.0, "")
    if grid:
        report.append("Grade de 60 Hz: " + why)

    def frame_of(t):
        return int(round(t / VGM_RATE * FPS * scale))

    # ---- 1ª passada: onde cada canal toca (menor frequência audível)
    chips = [_Ay() for _ in range(nchips)]
    lowest = {}
    used_noise = False
    wi = 0
    w_sorted = sorted(vgm.writes, key=lambda x: x[0])
    n_frames = frame_of(vgm.end) + 1
    frame_writes = [[] for _ in range(n_frames + 1)]
    for t, c, r, v in w_sorted:
        if c < nchips:
            frame_writes[min(n_frames, frame_of(t))].append((t, c, r, v))
    for f in range(n_frames):
        for t, c, r, v in frame_writes[f]:
            chips[c].write(r, v, t / VGM_RATE * scale)
        tf = f / FPS
        for c, ay in enumerate(chips):
            for ch in range(3):
                if ay.level(ch, tf, clock * scale) > 0:
                    if ay.tone_on(ch):
                        hz = clock / (16 * ay.period(ch))
                        lowest[(c, ch)] = min(lowest.get((c, ch), 1e9), hz)
                    if ay.noise_on(ch):
                        used_noise = True
    routes = plan_routes(lowest, fm_slots, routing)
    manual = {k for k in routes if (routing or {}).get(k, AUTO) != AUTO}
    for (c, ch), rt in sorted(routes.items()):
        label = route_label((c, ch)) + (" (escolhido)" if (c, ch) in manual else "")
        low = lowest[(c, ch)]
        if rt is None:
            report.append(f"{label}: omitido" if (c, ch) in manual
                          else f"{label}: sem canal livre no Mega Drive (omitido)")
        elif rt[0] == "psg":
            up = " — notas abaixo de 109 Hz sobem uma oitava" if low < PSG_MIN_HZ * 0.995 else ""
            report.append(f"{label}: PSG tom {rt[1] + 1} (mais grave: {low:.0f} Hz){up}")
        else:
            if (c, ch) in manual:
                why_fm = f"mais grave: {low:.0f} Hz"
            elif c == 0:
                why_fm = f"desce a {low:.0f} Hz, abaixo do limite do PSG"
            else:
                why_fm = "2º AY"
            report.append(f"{label}: FM {rt[1] + 1}, onda quadrada ({why_fm})")
    if used_noise:
        report.append("Ruído: canal de ruído do PSG")

    # ---- 2ª passada: escritas no Mega Drive
    patch = square_patch()
    chips = [_Ay() for _ in range(nchips)]
    frames = [[] for _ in range(n_frames)]
    init = [("psg", b) for b in (0x9F, 0xBF, 0xDF, 0xFF)]
    for (c, ch), rt in routes.items():
        if rt and rt[0] == "fm":
            init += [("fm",) + w for w in patch.channel_writes(rt[1], "C")]
    frames[0] += init
    last = {}
    fm_on = {}
    noise_mode_used = Counter()

    def put(f, key, writes, value):
        if last.get(key) != value:
            last[key] = value
            frames[f].extend(writes)

    loop_frame = frame_of(vgm.loop) if vgm.loop is not None else None
    for f in range(n_frames):
        for t, c, r, v in frame_writes[f]:
            chips[c].write(r, v, t / VGM_RATE * scale)
        if f == loop_frame and f > 0:
            # ponto de loop: reescreve o estado inteiro (o player volta para cá vindo do fim)
            last.clear()
            for (c2, ch2), rt in routes.items():
                if rt and rt[0] == "fm":
                    fc = rt[1]
                    frames[f].append(("fm", 0, 0x28, (fc if fc < 3 else fc - 3) + (4 if fc >= 3 else 0)))
            fm_on.clear()
        tf = f / FPS
        psg_tone_busy = set()
        noise_level, noise_np = 0, 1
        for c, ay in enumerate(chips):
            for ch in range(3):
                lv = ay.level(ch, tf, clock * scale)
                rt = routes.get((c, ch))
                tone = lv > 0 and ay.tone_on(ch)
                if ay.noise_on(ch) and lv > noise_level:
                    noise_level, noise_np = lv, max(1, ay.r[6] & 31)
                if rt is None:
                    continue
                hz = clock / (16 * ay.period(ch))
                if rt[0] == "psg":
                    pc = rt[1]
                    if tone:
                        psg_tone_busy.add(pc)
                        while hz < PSG_MIN_HZ:          # grave demais para o PSG: uma oitava acima
                            hz *= 2
                        n = max(1, min(PSG_MAX_PERIOD, int(round(NTSC_PSG_CLOCK / (32 * hz)))))
                        put(f, ("psgp", pc), [("psg", 0x80 | (pc << 5) | (n & 15)), ("psg", (n >> 4) & 0x3F)], n)
                        att = min(15, _att_steps(lv, PSG_STEP_DB))
                    else:
                        att = 15
                    if pc == 2 and not tone:
                        continue                       # tom 3 pode estar servindo de relógio do ruído
                    put(f, ("psgv", pc), [("psg", 0x90 | (pc << 5) | att)], att)
                else:
                    fc = rt[1]
                    port, cc = (0, fc) if fc < 3 else (1, fc - 3)
                    keyreg = cc + (4 if fc >= 3 else 0)
                    if tone:
                        att = _att_steps(lv, TL_STEP_DB)
                        tls = tuple(min(127, patch.ops[i].tl + att) for i in patch.carriers)
                        put(f, ("fmtl", fc), [("fm", port, 0x40 + cc + SLOT_OFS[i], tl)
                                              for i, tl in zip(patch.carriers, tls)], tls)
                        block, fnum = freq_to_block_fnum(hz, NTSC_FM_CLOCK)
                        put(f, ("fmf", fc), [("fm", port, 0xA4 + cc, (block << 3) | (fnum >> 8)),
                                             ("fm", port, 0xA0 + cc, fnum & 0xFF)], (block, fnum))
                        if not fm_on.get(fc):
                            frames[f].append(("fm", 0, 0x28, 0xF0 | keyreg))
                            fm_on[fc] = True
                    elif fm_on.get(fc):
                        frames[f].append(("fm", 0, 0x28, keyreg))
                        fm_on[fc] = False
        # ruído
        if noise_level > 0:
            if 2 not in psg_tone_busy and routes.get((0, 2), ("psg", 2)) is not None:
                n = max(1, min(PSG_MAX_PERIOD, int(round(NTSC_PSG_CLOCK * noise_np / (2 * clock)))))
                put(f, ("psgp", 2), [("psg", 0xC0 | (n & 15)), ("psg", (n >> 4) & 0x3F)], n)
                put(f, ("psgv", 2), [("psg", 0xDF)], 15)
                put(f, "nmode", [("psg", 0xE7)], 0xE7)
                noise_mode_used["tom 3"] += 1
            else:
                put(f, "nmode", [("psg", 0xE4)], 0xE4)
                noise_mode_used["N/512"] += 1
            att = min(15, _att_steps(noise_level, PSG_STEP_DB))
            put(f, "nvol", [("psg", 0xF0 | att)], att)
        else:
            put(f, "nvol", [("psg", 0xFF)], 15)
            if 2 not in psg_tone_busy:
                put(f, ("psgv", 2), [("psg", 0xDF)], 15)
    if noise_mode_used:
        report.append("Ruído cadenciado por: " + ", ".join(f"{k} ({v} quadros)" for k, v in noise_mode_used.items()))

    stream = Stream(frames=frames, fps=FPS, loop_frame=loop_frame, pcm_samples={}, pcm_rate=14000.0)
    if loop_frame is not None:
        # duração do loop tirada do próprio VGM (fim − ponto de loop), não do fim arredondado
        # quadros inteiros que cabem no loop original (sem passar dele)
        loop_len = max(1, int((vgm.end - vgm.loop) / VGM_RATE * FPS * scale + 1e-6))
        stream.stats["loop_end_frame"] = loop_frame + loop_len
        stream.stats["seamless_loop"] = True
        report.append(f"Loop: volta ao quadro {loop_frame}, a cada {loop_len} quadros "
                      f"({loop_len / FPS:.3f} s; no original {(vgm.end - vgm.loop) / VGM_RATE:.3f} s)")
    return AyConversion(stream, report, vgm, routes)


def convert_file(path, out_dir=None, name=None, grid=False, xgm=True, vgm=True, wav=True, log=print,
                 trim=True, routing=None):
    """Converte um .vgm/.vgz de AY e grava XGM, VGM e prévia WAV. Devolve a lista de arquivos."""
    from mdconv.vgmconv import write_outputs
    src = read_vgm(path)
    conv = convert(src, grid=grid, trim=trim, routing=routing)
    return write_outputs(conv.stream, conv.report, src.gd3, path, out_dir, name, xgm, vgm, wav, log,
                         "AY-3-8910")
