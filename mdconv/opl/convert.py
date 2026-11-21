"""OPL (YM3526 / YM3812 / Y8950) em VGM → YM2612 + SN76489, quadro a quadro.

O OPL tem 9 canais FM de **2 operadores** (modulador → portadora, ou os dois somados) e, no
OPL2, 4 formas de onda; o modo bateria troca os canais 7–9 por 5 peças (bumbo, caixa, tom,
prato e chimbal). O YM2612 tem 6 canais de 4 operadores, só com seno.

Tradução
--------
* **Vozes**: cada canal melódico (e, no modo bateria, o bumbo e o tom) é uma voz. As vozes vão
  para os 6 canais FM; se houver mais de 6, as menos importantes (menos tempo soando, nenhuma nota
  abaixo de ~110 Hz) vão para os tons do PSG, como onda quadrada; o que não couber fica de fora
  (o relatório diz). ``routing`` permite escolher à mão.
* **Timbre** (FM): o par modulador/portadora vira os operadores 1→2 do YM2612 (algoritmo 4; o
  algoritmo 7 quando o OPL soma os dois). MULT (0,5–15), TL, feedback, AR/DR/SL/RR, KSR, tremolo
  e vibrato são convertidos; as taxas de envelope são corrigidas pela diferença de taxa interna
  dos chips (OPL: clock/72, envelope a cada amostra; YM2612: clock/144, envelope a cada 3). O **KSL** (o volume cai nas notas agudas) entra
  no TL a cada nota. Sustentação: EGT=1 segura no SL; EGT=0 continua caindo na taxa do RR.
* **Formas de onda** (OPL2): o seno vai direto. Meio seno e seno retificado (e o "pulso") nas
  portadoras são aproximados com o 2º par de operadores (3→4) tocando o 2º/4º harmônico no
  volume da série de Fourier da onda; nos moduladores, o seno retificado dobra o MULT.
* **Frequência**: f = F-num × (clock/72) / 2^(20−bloco), recalculada em bloco/F-num do YM2612.
* **Vozes no PSG**: volume calculado quadro a quadro com o envelope do OPL (ataque, queda,
  sustentação e soltura, em dB), TL e KSL da portadora.
* **Bateria do modo ritmo**: caixa, prato e chimbal → ruído do PSG (um de cada vez, com
  prioridade caixa > prato > chimbal); bumbo e tom → vozes, como os canais melódicos.
* ADPCM do Y8950: ignorado (o relatório avisa).
"""
from __future__ import annotations

import gzip
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from mdconv.chips.emulator import NTSC_FM_CLOCK
from mdconv.driver.driver import Stream
from mdconv.fm.patch import freq_to_block_fnum
from mdconv.opn.convert import best_phase

VGM_RATE = 44100
FPS = 60.0
FS_2612 = NTSC_FM_CLOCK / 144.0
PSG_CLOCK = 3579545
PSG_MIN_HZ = PSG_CLOCK / (32 * 1023)          # ≈ 109,3 Hz

# chips: (nome, posição do clock no cabeçalho, comando 1º chip, comando 2º chip)
CHIPS = [("YM3812", 0x50, 0x5A, 0xAA), ("YM3526", 0x54, 0x5B, 0xAB), ("Y8950", 0x58, 0x5C, 0xAC)]

# deslocamento de registrador de cada operador ("slot") por canal: (modulador, portadora)
SLOTS = [(0x00, 0x03), (0x01, 0x04), (0x02, 0x05), (0x08, 0x0B), (0x09, 0x0C), (0x0A, 0x0D),
         (0x10, 0x13), (0x11, 0x14), (0x12, 0x15)]
SLOT_OF_OFS = {o: (ch, k) for ch, pair in enumerate(SLOTS) for k, o in enumerate(pair)}
MULT_OPL = [0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 12, 12, 15, 15]
MULT_YM = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 12, 12, 15, 15]   # valor do registrador do YM2612
# KSL: atenuação (dB) no bloco 7 pelo F-num alto (4 bits), para 6 dB/oitava
KSL_DB = [0, 9, 12, 13.875, 15, 16.125, 16.875, 17.625, 18, 18.75, 19.125, 19.5, 19.875, 20.25,
          20.625, 21]
KSL_SCALE = {0: 0.0, 1: 0.5, 2: 0.25, 3: 1.0}       # 3 / 1,5 / 6 dB por oitava (ordem do chip)
# harmônicos extras das ondas do OPL2 nas portadoras: (MULT do fundamental, MULT extra, dB do extra)
WAVE_HARM = {1: (1, 2, -7.4), 2: (2, 4, -14.0), 3: (2, 4, -14.0)}
WAVE_NAMES = {0: "seno", 1: "meio seno", 2: "seno retificado", 3: "pulso de seno"}

# envelope do OPL a 49 716 Hz (clock 3,58 MHz), taxa 1, KSR 0: ataque 0→100 % e queda 0→96 dB
ATTACK_S1 = 2.826
DECAY_S1 = 39.28


@dataclass
class OplVgm:
    chip: str
    clock: int
    dual: bool
    writes: list            # (amostra, chip, reg, valor)
    end: int
    loop: int | None
    gd3: dict = field(default_factory=dict)


def detect(d: bytes):
    """(nome, clock, cmd1, cmd2) do chip OPL do VGM, ou None."""
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    data = 0x34 + rel if rel else 0x40
    for name, off, c1, c2 in CHIPS:
        if data > off + 3:
            clk = struct.unpack_from("<I", d, off)[0]
            if clk & 0x3FFFFFFF:
                return name, clk, c1, c2
    return None


def read_vgm(path) -> OplVgm:
    from mdconv.ay.convert import _gd3
    d = Path(path).read_bytes()
    if d[:2] == b"\x1f\x8b":
        d = gzip.decompress(d)
    if d[:4] != b"Vgm ":
        raise ValueError("Não é um arquivo VGM/VGZ.")
    found = detect(d)
    if not found:
        raise ValueError("Este VGM não tem chip OPL (YM3526, YM3812 ou Y8950).")
    name, clk, c1, c2 = found
    ver = struct.unpack_from("<I", d, 8)[0]
    rel = struct.unpack_from("<I", d, 0x34)[0] if ver >= 0x150 else 0
    data = 0x34 + rel if rel else 0x40
    loop_rel = struct.unpack_from("<I", d, 0x1C)[0]
    loop_off = 0x1C + loop_rel if loop_rel else None
    gd3_rel = struct.unpack_from("<I", d, 0x14)[0]
    from mdconv.mdvgm import _SKIP
    p, t, writes, loop_t = data, 0, [], None
    while p < len(d):
        if loop_off is not None and p == loop_off:
            loop_t = t
        c = d[p]
        if c == 0x66:
            break
        if c in (c1, c2):
            writes.append((t, 0 if c == c1 else 1, d[p + 1], d[p + 2]))
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
        elif 0x80 <= c <= 0x8F:
            t, p = t + (c & 15), p + 1
        elif c == 0x67:
            p += 7 + (struct.unpack_from("<I", d, p + 3)[0] & 0x7FFFFFFF)
        elif c in (0x50, 0x52, 0x53):
            p += 2 if c == 0x50 else 3
        elif c in _SKIP:
            p += _SKIP[c]
        elif c in (0x90, 0x91, 0x95):
            p += 5
        elif c == 0x92:
            p += 6
        elif c == 0x93:
            p += 11
        elif c == 0x94:
            p += 2
        else:
            raise ValueError(f"comando 0x{c:02X} desconhecido na posição 0x{p:X} do VGM")
    end = max(t, struct.unpack_from("<I", d, 0x18)[0])
    return OplVgm(name, clk & 0x3FFFFFFF, bool(clk & 0x40000000), writes, end, loop_t,
                  _gd3(d, 0x14 + gd3_rel if gd3_rel else 0))


# ============================================================================ estado do chip
class OplState:
    """Registradores de um chip OPL e o que mudou desde o último quadro."""

    def __init__(self):
        self.r = [0] * 256

    def op(self, ofs: int) -> dict:
        r = self.r
        a, b, c, d, e = r[0x20 + ofs], r[0x40 + ofs], r[0x60 + ofs], r[0x80 + ofs], r[0xE0 + ofs]
        return {"am": a >> 7 & 1, "vib": a >> 6 & 1, "egt": a >> 5 & 1, "ksr": a >> 4 & 1, "mult": a & 15,
                "ksl": b >> 6, "tl": b & 63, "ar": c >> 4, "dr": c & 15, "sl": d >> 4, "rr": d & 15,
                "wave": (e & 3) if (r[0x01] & 0x20) else 0}

    def fnum_block(self, ch: int) -> tuple[int, int]:
        return self.r[0xA0 + ch] | ((self.r[0xB0 + ch] & 3) << 8), (self.r[0xB0 + ch] >> 2) & 7

    def fb_cnt(self, ch: int) -> tuple[int, int]:
        v = self.r[0xC0 + ch]
        return (v >> 1) & 7, v & 1


def ksl_db(ksl: int, fnum: int, block: int) -> float:
    if not ksl:
        return 0.0
    base = KSL_DB[fnum >> 6] - 6.0 * (7 - block)
    return max(0.0, base) * KSL_SCALE[ksl]


def opl_hz(fnum: int, block: int, clock: int) -> float:
    return fnum * (clock / 72.0) / 2 ** (20 - block)


# ============================================================================ vozes
@dataclass
class Voice:
    """Uma voz do OPL: canal melódico (0–17) ou peça do modo bateria ('BD'/'TOM')."""
    key: tuple                      # (chip, canal) ou (chip, 'BD') / (chip, 'TOM')
    label: str
    notes: list = field(default_factory=list)   # (início, fim, Hz)
    score: float = 0.0
    min_hz: float = 1e9
    dest: tuple | None = None        # ('fm', 0–5) | ('psg', 0–2) | None (omitida)


def _voice_label(key) -> str:
    chip, ch = key
    pre = f"chip {chip + 1} " if chip else ""
    return f"{pre}{'bumbo' if ch == 'BD' else 'tom' if ch == 'TOM' else f'canal {ch + 1}'}"


def _scan(vgm: OplVgm, nchips: int, rhythm: list[bool]) -> dict:
    """Percorre as escritas e anota as notas de cada voz (para escolher os destinos)."""
    st = [OplState() for _ in range(nchips)]
    voices: dict = {}
    on: dict = {}

    def voice(key):
        if key not in voices:
            voices[key] = Voice(key, _voice_label(key))
        return voices[key]

    def note_on(key, t, hz):
        on[key] = (t, hz)

    def note_off(key, t, sustained, rel_s):
        if key in on:
            t0, hz = on.pop(key)
            v = voice(key)
            v.notes.append((t0, t, hz))
            v.min_hz = min(v.min_hz, hz)
            v.score += (t - t0) / VGM_RATE if sustained else min((t - t0) / VGM_RATE, rel_s)

    for t, c, reg, val in vgm.writes:
        if c >= nchips:
            continue
        s = st[c]
        old = s.r[reg]
        s.r[reg] = val
        if 0xB0 <= reg <= 0xB8:
            ch = reg - 0xB0
            if rhythm[c] and ch >= 6:
                continue
            fnum, block = s.fnum_block(ch)
            hz = opl_hz(fnum, block, vgm.clock)
            car = s.op(SLOTS[ch][1])
            if val & 0x20 and not old & 0x20:
                note_on((c, ch), t, hz)
            elif old & 0x20 and not val & 0x20:
                note_off((c, ch), t, car["egt"], _decay_s(car["dr"], vgm.clock) * 0.5)
        elif reg == 0xBD and rhythm[c]:
            for bit, key, ch in ((0x10, "BD", 6), (0x04, "TOM", 8)):
                fnum, block = s.fnum_block(ch)
                hz = opl_hz(fnum, block, vgm.clock)
                if val & bit and not old & bit:
                    note_on((c, key), t, hz)
                elif old & bit and not val & bit:
                    note_off((c, key), t, False, 0.4)
    for key, (t0, hz) in list(on.items()):
        v = voice(key)
        v.notes.append((t0, vgm.end, hz))
        v.min_hz = min(v.min_hz, hz)
        v.score += min((vgm.end - t0) / VGM_RATE, 4.0)
    return voices


def _attack_s(r: int, clock: int) -> float:
    if r == 0:
        return 1e9
    if r >= 15:
        return 0.0
    return ATTACK_S1 / 2 ** (r - 1) * 3579545 / clock


def _decay_s(r: int, clock: int) -> float:
    """Tempo para cair 96 dB."""
    if r == 0:
        return 1e9
    return DECAY_S1 / 2 ** (r - 1) * 3579545 / clock


def plan_routes(voices: dict, fm_slots: int = 6, psg_slots: int = 3, routing: dict | None = None):
    """Escolhe o destino de cada voz: as mais importantes no FM; as que sobram (e que não têm
    notas abaixo do PSG) nos tons do PSG; o resto fica de fora. ``routing``: {chave: destino}."""
    routing = routing or {}
    vs = [v for v in voices.values() if v.notes]
    free_fm = list(range(fm_slots))
    free_psg = list(range(psg_slots))
    for v in vs:                                       # escolhas manuais primeiro
        d = routing.get(v.key)
        if d is None:
            continue
        if d == "omit":
            v.dest = ("omit",)
        elif d[0] == "fm" and d[1] in free_fm:
            v.dest = d
            free_fm.remove(d[1])
        elif d[0] == "psg" and d[1] in free_psg:
            v.dest = d
            free_psg.remove(d[1])
    auto = [v for v in vs if v.dest is None]
    # as vozes graves (que o PSG não toca) e as mais importantes primeiro no FM
    auto.sort(key=lambda v: (v.min_hz >= PSG_MIN_HZ * 1.01, -v.score))
    for v in auto:
        if free_fm:
            v.dest = ("fm", free_fm.pop(0))
    rest = [v for v in auto if v.dest is None]
    # no PSG, as que cabem; se uma voz grave sobrou, ela troca com a menos importante do FM que caiba no PSG
    for v in sorted(rest, key=lambda v: -v.score):
        if v.min_hz >= PSG_MIN_HZ * 1.01 and free_psg:
            v.dest = ("psg", free_psg.pop(0))
        else:
            swap = [u for u in vs if u.dest and u.dest[0] == "fm" and u.min_hz >= PSG_MIN_HZ * 1.01
                    and u.key not in routing]
            if swap and free_psg:
                u = min(swap, key=lambda u: u.score)
                v.dest, u.dest = u.dest, ("psg", free_psg.pop(0))
            else:
                v.dest = None
    for v in vs:
        if v.dest == ("omit",):
            v.dest = None
    return vs


# ============================================================================ conversão
@dataclass
class OplConversion:
    stream: Stream
    report: list[str]
    vgm: OplVgm
    voices: list = field(default_factory=list)


def _rate5(r: int, delta: float) -> int:
    if r <= 0:
        return 0
    return max(1, min(31, int(round(2 * r + delta))))


def _ym_ops(st: OplState, ch: int, fnum: int, block: int, delta: float, tl_shift: int = 0):
    """Os 4 operadores do YM2612 (dicionários) + algoritmo/feedback para o canal do OPL.
    Devolve (ops[1..4], alg, fb, am_used, vib_used, waves)."""
    mod, car = st.op(SLOTS[ch][0]), st.op(SLOTS[ch][1])
    fb, cnt = st.fb_cnt(ch)

    def ym(o, mult=None, tl_add=0.0):
        att = o["tl"] * 0.75 + ksl_db(o["ksl"], fnum, block) + tl_add
        tl = min(127, int(round(att / 0.75)))
        sus = bool(o["egt"])
        rr = 0 if o["rr"] == 0 else max(1, min(15, int(round(o["rr"] - 0.25 + delta / 2))))
        return {"mul": MULT_YM[o["mult"]] if mult is None else mult, "tl": tl,
                "ks": 2 if o["ksr"] else 0,
                "ar": 31 if o["ar"] >= 15 else _rate5(o["ar"], delta),
                "d1r": _rate5(o["dr"], delta), "d2r": 0 if sus else _rate5(o["rr"], delta),
                "sl": o["sl"], "rr": rr, "am": o["am"]}

    off = {"mul": 1, "tl": 127, "ks": 0, "ar": 31, "d1r": 0, "d2r": 0, "sl": 0, "rr": 15, "am": 0}
    waves = (mod["wave"], car["wave"])

    def carrier(o):
        """(portadora principal, extra ou None) com a aproximação da forma de onda."""
        w = o["wave"]
        if w == 0:
            return ym(o, tl_add=tl_shift * 0.75), None
        base_mul, extra_mul, db = WAVE_HARM[w]
        m = MULT_OPL[o["mult"]]
        main = ym(o, mult=min(15, max(0, int(round(m * base_mul)) if m * base_mul >= 1 else 0)),
                  tl_add=tl_shift * 0.75)
        extra = ym(o, mult=min(15, max(1, int(round(m * extra_mul)))), tl_add=-db + tl_shift * 0.75)
        return main, extra

    if cnt == 0:                     # FM: modulador → portadora
        mmul = MULT_OPL[mod["mult"]] * (2 if mod["wave"] in (2, 3) else 1)
        m1 = ym(mod, mult=min(15, int(round(mmul))) if mmul >= 1 else 0)
        c2, extra = carrier(car)
        if extra is None:
            return [m1, c2, off, off], 4, fb, mod["am"] or car["am"], mod["vib"] or car["vib"], waves
        m3 = dict(m1)
        m3["mul"] = min(15, max(1, int(round(mmul * WAVE_HARM[car["wave"]][1] / WAVE_HARM[car["wave"]][0]))))
        return [m1, c2, m3, extra], 4, fb, mod["am"] or car["am"], mod["vib"] or car["vib"], waves
    # soma: os dois soam (algoritmo 7); os extras das ondas nos operadores 3 e 4
    c1, e1 = carrier(mod)
    c2, e2 = carrier(car)
    return ([c1, c2, e1 or off, e2 or off], 7, fb, mod["am"] or car["am"], mod["vib"] or car["vib"], waves)


def _patch_writes(port: int, cc: int, ops, alg: int, fb: int, ams: int, pms: int) -> list:
    out = []
    # ordem dos registradores do YM2612: op1 +0, op3 +4, op2 +8, op4 +12
    for i, o in enumerate(ops):
        slot = (0, 8, 4, 12)[i]
        out += [("fm", port, 0x30 + slot + cc, o["mul"] & 15),
                ("fm", port, 0x40 + slot + cc, o["tl"]),
                ("fm", port, 0x50 + slot + cc, (o["ks"] << 6) | o["ar"]),
                ("fm", port, 0x60 + slot + cc, (o["am"] << 7) | o["d1r"]),
                ("fm", port, 0x70 + slot + cc, o["d2r"]),
                ("fm", port, 0x80 + slot + cc, (o["sl"] << 4) | o["rr"]),
                ("fm", port, 0x90 + slot + cc, 0)]
    out.append(("fm", port, 0xB0 + cc, (fb << 3) | alg))
    out.append(("fm", port, 0xB4 + cc, 0xC0 | (ams << 4) | pms))
    return out


class _PsgEnv:
    """Envelope do OPL simulado em dB, para as vozes que tocam no PSG."""

    def __init__(self):
        self.db = 96.0
        self.stage = "off"

    def key_on(self):
        self.stage = "attack"

    def key_off(self):
        if self.stage != "off":
            self.stage = "release"

    def step(self, o: dict, dt: float, clock: int) -> float:
        if self.stage == "attack":
            at = _attack_s(o["ar"], clock)
            if at <= dt:
                self.db = 0.0
                self.stage = "decay"
            else:                                # ataque exponencial: rápido no começo
                self.db = max(0.0, self.db - 96.0 * dt / at * (1 + self.db / 24))
                if self.db <= 0.05:
                    self.db, self.stage = 0.0, "decay"
        elif self.stage == "decay":
            sl = 93.0 if o["sl"] == 15 else o["sl"] * 3.0
            self.db = min(sl, self.db + 96.0 * dt / _decay_s(o["dr"], clock))
            if self.db >= sl:
                self.stage = "sustain"
        elif self.stage == "sustain":
            if not o["egt"]:
                self.db = min(96.0, self.db + 96.0 * dt / _decay_s(o["rr"], clock))
        elif self.stage == "release":
            self.db = min(96.0, self.db + 96.0 * dt / _decay_s(o["rr"], clock))
            if self.db >= 96.0:
                self.stage = "off"
        return self.db if self.stage != "off" else 96.0


def convert(vgm: OplVgm, trim: bool = True, routing: dict | None = None, fm_slots: int = 6,
            psg_slots: int = 3) -> OplConversion:
    nchips = 2 if vgm.dual or any(c == 1 for _, c, _, _ in vgm.writes) else 1
    writes = sorted(vgm.writes, key=lambda x: x[0])
    rhythm = [any(c == k and r == 0xBD and v & 0x20 for _, c, r, v in writes) for k in range(nchips)]
    report = [f"{vgm.chip} (OPL) a {vgm.clock} Hz, {nchips} chip(s), {len(writes)} escritas, "
              f"{vgm.end / VGM_RATE:.1f} s" + (f", loop em {vgm.loop / VGM_RATE:.2f} s" if vgm.loop is not None
                                               else ", sem loop")]
    if vgm.chip == "Y8950" and any(0x07 <= r <= 0x12 for _, _, r, _ in writes):
        report.append("⚠ ADPCM do Y8950: não convertido")
    if any(rhythm):
        report.append("modo bateria: bumbo e tom como vozes; caixa, prato e chimbal no ruído do PSG")

    t0 = 0
    if trim:
        t0 = next((t for t, c, r, v in writes if (0xB0 <= r <= 0xB8 and v & 0x20)
                   or (r == 0xBD and v & 0x3F and v & 0x20)), 0)
        if t0 > 0:
            report.append(f"Silêncio do início removido: {t0 / VGM_RATE:.2f} s (a música começa na primeira nota)")
    writes = [(max(0, t - t0), c, r, v) for t, c, r, v in writes]
    end = max(1, vgm.end - t0)
    loop = None if vgm.loop is None else max(0, vgm.loop - t0)
    vgm_t = OplVgm(vgm.chip, vgm.clock, vgm.dual, writes, end, loop, vgm.gd3)

    voices = _scan(vgm_t, nchips, rhythm)
    vs = plan_routes(voices, fm_slots, psg_slots, routing)
    by_key = {v.key: v for v in vs}
    for v in sorted(vs, key=lambda v: str(v.key)):
        dest = ("omitida" if v.dest is None else f"FM {v.dest[1] + 1}" if v.dest[0] == "fm"
                else f"PSG tom {v.dest[1] + 1}")
        low = f", nota mais grave {v.min_hz:.0f} Hz" if v.min_hz < 1e8 else ""
        report.append(f"{v.label} → {dest} ({len(v.notes)} notas, {v.score:.0f} s soando{low})")
    omitted = [v for v in vs if v.dest is None]
    if omitted:
        report.append(f"⚠ {len(omitted)} voz(es) sem canal livre: ficam de fora (use as rotas para escolher)")

    spf = VGM_RATE / FPS
    onsets = sorted({t for t, c, r, v in writes if 0xB0 <= r <= 0xB8 and v & 0x20})
    phase = best_phase(onsets, spf)

    def frame_of(t):
        return max(0, int(round((t - phase) / spf)))

    n_frames = frame_of(end) + 1
    frames: list[list[tuple]] = [[] for _ in range(n_frames)]
    fs_opl = vgm.clock / 72.0
    # passos de taxa (5 bits do YM2612) equivalentes: o envelope do OPL anda a cada amostra
    # (clock/72); o do YM2612, a cada 3 amostras (clock/144/3) — na mesma taxa, o OPL é ~3× mais
    # rápido (ex.: queda de 96 dB na taxa 4: 39 s no OPL, 124 s no YM2612)
    delta = 2 * math.log2(fs_opl * 3 / FS_2612)
    report.append(f"taxa interna do OPL {fs_opl:.0f} Hz, envelope a cada amostra (YM2612: {FS_2612:.0f} Hz, a "
                  f"cada 3): envelopes corrigidos em {delta:+.2f} passos")

    st = [OplState() for _ in range(nchips)]
    lfo_used = {"am": False, "vib": False}
    waves_seen: dict = {}
    ksl_seen = False
    # estado de saída por voz
    keyed: dict = {}            # chave → bool (key on no destino)
    last_patch: dict = {}       # chave → escritas do timbre (para não repetir)
    last_freq: dict = {}
    psg_env: dict = {}
    psg_last: dict = {}         # (canal psg) → (período, volume)
    noise = {"stage": None, "db": 96.0, "who": None, "last": None}

    def voice_ch(key):
        """Canal do OPL cujos registradores formam o timbre da voz."""
        c, ch = key
        return c, (6 if ch == "BD" else 8 if ch == "TOM" else ch)

    def fm_out(f, key, events):
        v = by_key.get(key)
        if not v or not v.dest or v.dest[0] != "fm":
            return
        c, ch = voice_ch(key)
        s = st[c]
        slot = v.dest[1]
        port, cc = (0, slot) if slot < 3 else (1, slot - 3)
        fnum, block = s.fnum_block(ch)
        if key[1] == "TOM":                   # tom: só o operador 1 do canal 9, como portadora
            o = s.op(SLOTS[8][0])
            ops = [{"mul": MULT_YM[o["mult"]], "tl": min(127, int(round((o["tl"] * 0.75 + ksl_db(o["ksl"], fnum, block)) / 0.75))),
                    "ks": 2 if o["ksr"] else 0, "ar": 31 if o["ar"] >= 15 else _rate5(o["ar"], delta),
                    "d1r": _rate5(o["dr"], delta), "d2r": _rate5(o["rr"], delta), "sl": o["sl"],
                    "rr": max(1, min(15, int(round(o["rr"] - 0.25 + delta / 2)))) if o["rr"] else 0, "am": 0}]
            off = {"mul": 1, "tl": 127, "ks": 0, "ar": 31, "d1r": 0, "d2r": 0, "sl": 0, "rr": 15, "am": 0}
            ops, alg, fb, am, vib, waves = ops + [off, off, off], 7, 0, 0, 0, (0, 0)
        else:
            ops, alg, fb, am, vib, waves = _ym_ops(s, ch, fnum, block, delta)
        for w in waves:
            if w:
                waves_seen.setdefault(w, set()).add(key)
        ams = (2 if s.r[0xBD] & 0x80 else 1) if am else 0
        pms = (4 if s.r[0xBD] & 0x40 else 2) if vib else 0
        lfo_used["am"] |= bool(am)
        lfo_used["vib"] |= bool(vib)
        hz = opl_hz(fnum, block, vgm.clock)
        b2, f2 = freq_to_block_fnum(hz, NTSC_FM_CLOCK) if hz > 0 else (0, 0)
        want_on = events.get("on", keyed.get(key, False))
        # ordem: soltar (se a nota recomeça), timbre, frequência, prender
        if events.get("retrig") and keyed.get(key):
            frames[f].append(("fm", 0, 0x28, (4 if port else 0) | cc))
            keyed[key] = False
        pw = _patch_writes(port, cc, ops, alg, fb, ams, pms)
        prev = last_patch.get(key)
        if prev != pw:
            frames[f].extend(w for i, w in enumerate(pw) if prev is None or prev[i] != w)
            last_patch[key] = pw
        fw = (b2, f2)
        if last_freq.get(key) != fw:
            frames[f] += [("fm", port, 0xA4 + cc, (b2 << 3) | (f2 >> 8)), ("fm", port, 0xA0 + cc, f2 & 0xFF)]
            last_freq[key] = fw
        if want_on != keyed.get(key, False):
            frames[f].append(("fm", 0, 0x28, (0xF0 if want_on else 0) | (4 if port else 0) | cc))
            keyed[key] = want_on

    def psg_out(f, key, events, dt):
        v = by_key.get(key)
        if not v or not v.dest or v.dest[0] != "psg":
            return
        c, ch = voice_ch(key)
        s = st[c]
        pc = v.dest[1]
        env = psg_env.setdefault(key, _PsgEnv())
        if events.get("retrig") or (events.get("on") and not keyed.get(key)):
            env.key_on()
        elif events.get("on") is False:
            env.key_off()
        keyed[key] = events.get("on", keyed.get(key, False))
        fnum, block = s.fnum_block(ch)
        car = s.op(SLOTS[ch][0] if key[1] == "TOM" else SLOTS[ch][1])
        db = env.step(car, dt, vgm.clock) + car["tl"] * 0.75 + ksl_db(car["ksl"], fnum, block)
        if key[1] != "TOM" and s.fb_cnt(ch)[1]:       # soma: o modulador também soa
            mod = s.op(SLOTS[ch][0])
            db = min(db, mod["tl"] * 0.75 + ksl_db(mod["ksl"], fnum, block) + env.db)
        hz = opl_hz(fnum, block, vgm.clock)
        if car["wave"] in (2, 3):
            hz *= 2
        period = max(1, min(1023, int(round(PSG_CLOCK / (32 * hz))))) if hz > 0 else 1023
        vol = 15 if db >= 30 else max(0, min(15, int(round(db / 2)) + 1))
        last = psg_last.get(pc)
        if last is None or last[0] != period:
            frames[f] += [("psg", 0x80 | (pc << 5) | (period & 15)), ("psg", (period >> 4) & 0x3F)]
        if last is None or last[1] != vol:
            frames[f].append(("psg", 0x90 | (pc << 5) | vol))
        psg_last[pc] = (period, vol)

    # ---- quadro a quadro
    wi = 0
    dt = 1.0 / FPS
    all_keys = [v.key for v in vs]
    for f in range(n_frames):
        events: dict = {}
        drum_hits = []
        while wi < len(writes) and frame_of(writes[wi][0]) <= f:
            t, c, reg, val = writes[wi]
            wi += 1
            if c >= nchips:
                continue
            s = st[c]
            old = s.r[reg]
            s.r[reg] = val
            if reg == 0xBD:
                if rhythm[c]:
                    for bit, key in ((0x10, "BD"), (0x04, "TOM")):
                        if val & bit and not old & bit:
                            e = events.setdefault((c, key), {})
                            e["retrig"] = e.get("on") is False or keyed.get((c, key), False)
                            e["on"] = True
                        elif old & bit and not val & bit:
                            events.setdefault((c, key), {})["on"] = False
                    for bit, name in ((0x08, "SD"), (0x02, "CY"), (0x01, "HH")):
                        if val & bit and not old & bit:
                            drum_hits.append((c, name))
                continue
            if 0xB0 <= reg <= 0xB8:
                ch = reg - 0xB0
                if rhythm[c] and ch >= 6:
                    continue
                key = (c, ch)
                if val & 0x20 and not old & 0x20:
                    e = events.setdefault(key, {})
                    e["retrig"] = e.get("on") is False or keyed.get(key, False)
                    e["on"] = True
                elif old & 0x20 and not val & 0x20:
                    events.setdefault(key, {})["on"] = False
            if 0x40 <= reg <= 0x55 and val >> 6:
                ksl_seen = True
        # FM: só quando algo mudou (timbre, nota) ou há evento; o timbre é comparado e só o que mudou sai
        for key in all_keys:
            v = by_key[key]
            if not v.dest:
                continue
            if v.dest[0] == "fm":
                fm_out(f, key, events.get(key, {}))
            else:
                psg_out(f, key, events.get(key, {}), dt)
        # bateria no ruído
        for c, name in drum_hits:
            prio = {"SD": 3, "CY": 2, "HH": 1}[name]
            if noise["who"] is None or noise["db"] > 30 or prio >= {"SD": 3, "CY": 2, "HH": 1}[noise["who"][1]]:
                s = st[c]
                o = s.op({"SD": 0x14, "CY": 0x15, "HH": 0x11}[name])
                noise.update(who=(c, name), db=o["tl"] * 0.75, rr=o["rr"] or o["dr"], egt=o["egt"])
                rate = {"SD": 1, "CY": 0, "HH": 0}[name]
                frames[f].append(("psg", 0xE0 | 0x04 | rate))
        if noise["who"] is not None:
            decay_s = _decay_s(noise["rr"], vgm.clock) * (0.35 if noise["who"][1] != "CY" else 1.0)
            vol = 15 if noise["db"] >= 30 else max(0, min(15, int(round(noise["db"] / 2)) + 1))
            if vol != noise["last"]:
                frames[f].append(("psg", 0xF0 | vol))
                noise["last"] = vol
            noise["db"] += 96.0 * dt / max(decay_s, 1e-3)
            if noise["db"] >= 30:
                noise["who"] = None
        elif noise["last"] not in (None, 15):
            frames[f].append(("psg", 0xFF))
            noise["last"] = 15

    # LFO do YM2612 se tremolo/vibrato foram usados (6,02 Hz, perto dos 6,1 Hz do vibrato do OPL)
    frames[0].insert(0, ("fm", 0, 0x22, 0x0A if (lfo_used["am"] or lfo_used["vib"]) else 0))
    frames[0][1:1] = [("fm", 0, 0x2B, 0x00)]          # DAC desligado: o FM 6 toca FM
    for pc in range(3):
        if pc not in psg_last:
            frames[0].append(("psg", 0x90 | (pc << 5) | 15))
    frames[0].append(("psg", 0xFF))

    if waves_seen:
        report.append("formas de onda do OPL2 aproximadas: " + ", ".join(
            f"{WAVE_NAMES[w]} ({len(n)} voz(es))" for w, n in sorted(waves_seen.items())))
    if ksl_seen:
        report.append("KSL (volume menor nas notas agudas) aplicado no TL a cada nota")
    if lfo_used["am"] or lfo_used["vib"]:
        report.append("tremolo/vibrato do OPL → LFO do YM2612 (6 Hz)")

    loop_frame = frame_of(loop) if loop is not None else None
    stream = Stream(frames=frames, fps=FPS, loop_frame=loop_frame, pcm_samples={}, pcm_rate=14000.0)
    if loop_frame is not None:
        loop_len = max(1, int((end - loop) / spf + 1e-6))
        stream.stats["loop_end_frame"] = loop_frame + loop_len
        stream.stats["seamless_loop"] = True
    return OplConversion(stream, report, vgm, vs)
