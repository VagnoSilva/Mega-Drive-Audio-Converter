"""Furnace (.fur) a partir do fluxo de quadros já convertido para o Mega Drive.

Usado nas conversões diretas (VGM de AY/YM2203, Irem, Capcom, Konami, Neo Geo), que não passam
por MIDI: o que existe é a sequência de escritas no YM2612, no PSG e no PCM a cada 1/60 s. Aqui
essas escritas são lidas como um "ouvinte" do chip e viram notas de tracker:

* uma linha por quadro (velocidade 1, 60 linhas por segundo — o tempo exato do XGM);
* FM: cada key-on vira uma nota com o instrumento = timbre do canal naquele instante (os mesmos
  registradores); volume das portadoras → coluna de volume; mudança de altura sem key-on → E5xx
  (até ±1 semitom) ou nova nota em legato (EA01); key-off → OFF; pan → 08xy;
* PSG: volume saindo de mudo → nota; altura e volume acompanham quadro a quadro; mudo → OFF;
  ruído: modo por 20xy, taxa pela nota (como o Furnace faz no SN76489);
* PCM (XGM): cada amostra numa tecla de um instrumento de bateria, nos canais PCM 1 e 2 do
  sistema "YM2612 DualPCM" (os canais PCM 3 e 4 do XGM vão para o PCM 2).

A serialização é a do exportador principal (:mod:`mdconv.export.furnace`).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from mdconv.chips.emulator import NTSC_FM_CLOCK, NTSC_PSG_CLOCK
from mdconv.export.furnace import (
    NOTE_OFF, Cell, FurInstrument, FurReport, FurSample, serialize,
)
from mdconv.fm.patch import CARRIERS, SLOT_OFS, Operator, Patch, block_fnum_to_freq

EFF_PITCH, EFF_LEGATO, EFF_PAN, EFF_NOISE = 0xE5, 0xEA, 0x08, 0x20
EFF_NEXT_PAT, EFF_JUMP, EFF_STOP = 0x0D, 0x0B, 0xFF
PAT_LEN = 240                       # 4 s por padrão (60 linhas por segundo)


def _midi(hz: float) -> float:
    return 69 + 12 * math.log2(hz / 440.0)


def _pitch_cells(c: Cell, m: float, base: int):
    """Nota mais próxima + E5xx com o resto (1/128 de semitom; 80 = afinado). O E5 vai sempre,
    para não herdar a afinação da nota anterior do canal."""
    n = int(round(m))
    c.note = max(0, min(179, n + base))
    c.fx[EFF_PITCH] = max(0, min(255, 0x80 + int(round((m - n) * 128))))
    return n


def _patch_from_regs(regs, cc: int) -> tuple[Patch, int]:
    """Timbre do canal (porta já escolhida) → Patch com as portadoras normalizadas (a mais forte
    com TL 0) e a atenuação tirada delas, que vai para a coluna de volume."""
    alg_fb = regs[0xB0 + cc]
    alg, fb = alg_fb & 7, (alg_fb >> 3) & 7
    ams_fms = regs[0xB4 + cc]
    ops = []
    for i in range(4):
        b = cc + SLOT_OFS[i]
        dm = regs[0x30 + b]
        dt = (dm >> 4) & 7
        ops.append(Operator(mul=dm & 15, dt=dt if dt < 4 else 4 - dt, tl=regs[0x40 + b] & 127,
                            ks=regs[0x50 + b] >> 6, ar=regs[0x50 + b] & 31, d1r=regs[0x60 + b] & 31,
                            d2r=regs[0x70 + b] & 31, sl=regs[0x80 + b] >> 4, rr=regs[0x80 + b] & 15,
                            am=regs[0x60 + b] >> 7))
    car = CARRIERS[alg]
    d = min(ops[i].tl for i in car)
    for i in car:
        ops[i].tl -= d
    return Patch(alg=alg, fb=fb, ams=(ams_fms >> 4) & 3, fms=ams_fms & 7, ops=ops), d


def _patch_key(p: Patch):
    return (p.alg, p.fb, p.ams, p.fms) + tuple(
        (o.mul, o.dt, o.tl, o.ks, o.ar, o.d1r, o.d2r, o.sl, o.rr, o.am) for o in p.ops)


def build(stream, name: str = "", author: str = "", game: str = ""):
    frames = stream.frames
    loop_f = stream.loop_frame
    n_rows = stream.stats.get("loop_end_frame", len(frames)) if loop_f is not None else len(frames)
    n_rows = max(1, min(n_rows, len(frames)))
    use_pcm = any(w[0] == "pcm" for f in frames[:n_rows] for w in f)
    n_fm = 5 if use_pcm else 6
    ch_names = [f"FM {i + 1}" for i in range(n_fm)] + (["PCM 1", "PCM 2"] if use_pcm else [])
    psg0 = len(ch_names)
    ch_names += ["PSG 1", "PSG 2", "PSG 3", "Ruído"]
    n_ch = len(ch_names)
    cells: list[dict[int, Cell]] = [dict() for _ in range(n_ch)]
    rep = FurReport(n_rows, 15, 1, PAT_LEN, 0, None, ch_names, 0, 0, 0)
    warnings = []

    def cell(ch, row) -> Cell:
        c = cells[ch].get(row)
        if c is None:
            c = cells[ch][row] = Cell()
        return c

    instruments: list[FurInstrument] = []
    ins_index: dict = {}
    full = [False]

    def fm_ins(p: Patch) -> int:
        k = _patch_key(p)
        if k not in ins_index:
            if len(instruments) >= 250:
                full[0] = True
                return min(ins_index.values(), key=lambda i: 0) if ins_index else 0
            ins_index[k] = len(instruments)
            instruments.append(FurInstrument(f"FM {len(ins_index):02d}", "fm", patch=p))
        return ins_index[k]

    def simple_ins(kind):
        k = ("simple", kind)
        if k not in ins_index:
            ins_index[k] = len(instruments)
            if kind == "psg":
                instruments.append(FurInstrument("PSG", "psg", vol_macro=[0, 15, 255, 0, 0, 255, 0, 0, 255]
                                                 + [0] * 7, vol_rel=-1))
            else:
                instruments.append(FurInstrument("Ruído", "noise", vol_macro=[15]))
        return ins_index[k]

    # ---- amostras (um instrumento de bateria, uma tecla por amostra)
    samples: list[FurSample] = []
    sid_key = {}
    if use_pcm:
        for k, sid in enumerate(sorted(stream.pcm_samples)):
            sid_key[sid] = 24 + k
            samples.append(FurSample(f"amostra {sid}", np.asarray(stream.pcm_samples[sid], np.int8),
                                     int(round(stream.pcm_rate))))
        kit = len(instruments)
        instruments.append(FurInstrument("Amostras", "kit", note_map={24 + k: k for k in range(len(samples))}))

    # ---- estado dos chips
    regs = [[0] * 256, [0] * 256]
    fm_on = [False] * 6
    fm_pitch = [None] * 6           # última altura escrita (MIDI fracionário)
    fm_vol = [None] * 6
    fm_pan = [0xC0] * 6
    fm_ins_cur = [None] * 6
    fm_d = [0] * 6                  # atenuação das portadoras tirada do instrumento
    legato = [False] * n_ch
    psg_per = [0] * 3
    psg_att = [15] * 4
    psg_on = [False] * 4
    psg_pitch = [None] * 4
    psg_vol = [None] * 4
    noise_mode = 0
    latch = 0
    merged = 0

    def fm_chan(port, reg):
        return (reg & 3) + 3 * port

    def fm_hz(k):
        port, cc = (0, k) if k < 3 else (1, k - 3)
        hi, lo = regs[port][0xA4 + cc], regs[port][0xA0 + cc]
        return block_fnum_to_freq((hi >> 3) & 7, ((hi & 7) << 8) | lo, NTSC_FM_CLOCK)

    def fm_carrier_tl(k):
        port, cc = (0, k) if k < 3 else (1, k - 3)
        alg = regs[port][0xB0 + cc] & 7
        return min(regs[port][0x40 + cc + SLOT_OFS[i]] & 127 for i in CARRIERS[alg])

    base_note = [None] * n_ch       # nota inteira escrita no canal (a E5xx é relativa a ela)
    pitch_fx = [0x80] * n_ch

    def set_note(ch, row, m, base, ins=None, new=True):
        c = cell(ch, row)
        if not new and not legato[ch]:
            c.fx[EFF_LEGATO] = 1
            legato[ch] = True
        if new and legato[ch]:
            c.fx[EFF_LEGATO] = 0
            legato[ch] = False
        base_note[ch] = _pitch_cells(c, m, base)
        pitch_fx[ch] = c.fx.get(EFF_PITCH, 0x80)
        if pitch_fx[ch] == 0x80 and EFF_PITCH not in c.fx and new is False:
            pass
        if ins is not None:
            c.ins = ins
        if new:
            rep.notes += 1
        return c

    def follow_pitch(ch, row, m, last, base):
        """Altura mudou sem nota nova: E5xx se couber em ±1 semitom da nota escrita, senão legato."""
        if last is None or abs(m - last) < 0.02:
            return last
        ref = base_note[ch]
        if ref is not None and abs(m - ref) <= 0.99:
            e5 = max(0, min(255, 0x80 + int(round((m - ref) * 128))))
            if e5 != pitch_fx[ch]:
                cell(ch, row).fx[EFF_PITCH] = e5
                pitch_fx[ch] = e5
            return m
        # FM: legato (sem reiniciar o envelope, como no chip); PSG: nota nova (o PSG não tem envelope)
        set_note(ch, row, m, base, new=ch >= n_fm and ch >= psg0)
        c = cell(ch, row)
        if EFF_PITCH not in c.fx and pitch_fx[ch] != 0x80:
            c.fx[EFF_PITCH] = 0x80
        pitch_fx[ch] = c.fx.get(EFF_PITCH, 0x80)
        return m

    for row in range(n_rows):
        keyed = set()
        released = set()
        for w in frames[row]:
            if w[0] == "fm":
                _, port, reg, val = w
                if port == 0 and reg == 0x28:
                    c = val & 7
                    k = c if c < 3 else c - 1
                    if c in (3, 7) or k >= n_fm:
                        continue
                    if val & 0xF0:
                        keyed.add(k)
                        released.discard(k)
                        port_, cc = (0, k) if k < 3 else (1, k - 3)
                        p, d = _patch_from_regs(regs[port_], cc)
                        fm_ins_cur[k] = (fm_ins(p), d)
                    else:
                        if k not in keyed:
                            released.add(k)
                    continue
                regs[port][reg] = val
            elif w[0] == "psg":
                b = w[1] & 0xFF
                if b & 0x80:
                    latch = (b >> 5) & 3
                    if b & 0x10:
                        psg_att[latch] = b & 15
                    elif latch < 3:
                        psg_per[latch] = (psg_per[latch] & 0x3F0) | (b & 15)
                    else:
                        noise_mode = b & 7
                elif latch < 3 and not (b & 0x80):
                    psg_per[latch] = (psg_per[latch] & 15) | ((b & 0x3F) << 4)
            elif w[0] == "pcm" and use_pcm:
                _, pch, sid, _prio = w
                slot = 0 if pch == 1 else 1
                if pch > 2:
                    merged += 1
                c = cell(n_fm + slot, row)
                if sid == 0:                   # parar o canal
                    c.note = NOTE_OFF
                    continue
                c.note = sid_key.get(sid, 24) + 48
                c.ins = kit
                rep.notes += 1

        # ---- FM
        for k in range(n_fm):
            port, cc = (0, k) if k < 3 else (1, k - 3)
            pan = regs[port][0xB4 + cc] & 0xC0
            if k in keyed:
                hz = fm_hz(k)
                ins, d = fm_ins_cur[k]
                if hz > 0:
                    m = _midi(hz)
                    c = set_note(k, row, m, 48, ins)
                    fm_pitch[k] = m
                    fm_d[k] = d
                    vol = max(0, min(127, 127 - d))
                    c.vol = vol
                    fm_vol[k] = vol
                    fm_on[k] = True
            elif fm_on[k] and k in released:
                cell(k, row).note = NOTE_OFF
                fm_on[k] = False
            elif fm_on[k]:
                hz = fm_hz(k)
                if hz > 0:
                    fm_pitch[k] = follow_pitch(k, row, _midi(hz), fm_pitch[k], 48)
                vol = max(0, min(127, 127 - fm_carrier_tl(k)))
                if vol != fm_vol[k]:
                    cell(k, row).vol = vol
                    fm_vol[k] = vol
            if pan and pan != fm_pan[k]:
                cell(k, row).fx[EFF_PAN] = (0xF0 if pan & 0x80 else 0) | (0x0F if pan & 0x40 else 0)
                fm_pan[k] = pan

        # ---- PSG (tons)
        for k in range(3):
            ch = psg0 + k
            att = psg_att[k]
            per = psg_per[k] or 1
            sounding = att < 15 and per > 1
            if sounding:
                m = _midi(NTSC_PSG_CLOCK / (32 * per))
                if not psg_on[k]:
                    c = set_note(ch, row, m, 24, simple_ins("psg"))
                    c.vol = 15 - att
                    psg_pitch[k], psg_vol[k], psg_on[k] = m, 15 - att, True
                else:
                    psg_pitch[k] = follow_pitch(ch, row, m, psg_pitch[k], 24)
                    if 15 - att != psg_vol[k]:
                        cell(ch, row).vol = 15 - att
                        psg_vol[k] = 15 - att
            elif psg_on[k]:
                cell(ch, row).note = NOTE_OFF
                psg_on[k] = False

        # ---- ruído
        ch = psg0 + 3
        att = psg_att[3]
        if att < 15:
            rate = noise_mode & 3
            mode_fx = (0x10 if rate == 3 else 0) | (1 if noise_mode & 4 else 0)
            if rate == 3:
                m = _midi(NTSC_PSG_CLOCK / (32 * (psg_per[2] or 1)))
            else:
                m = 62 - rate                                   # D = N/512, C# = N/1024, C = N/2048
            if not psg_on[3] or (noise_mode, round(m)) != psg_pitch[3]:
                c = cell(ch, row)
                c.note = max(0, min(179, int(round(m)) + (24 if rate == 3 else 48)))
                c.ins = simple_ins("psg")
                c.fx[EFF_NOISE] = mode_fx
                c.vol = 15 - att
                psg_vol[3] = 15 - att
                psg_pitch[3] = (noise_mode, round(m))
                psg_on[3] = True
                rep.notes += 1
            elif 15 - att != psg_vol[3]:
                cell(ch, row).vol = 15 - att
                psg_vol[3] = 15 - att
        elif psg_on[3]:
            cell(ch, row).note = NOTE_OFF
            psg_on[3] = False

    if merged:
        rep.merged_pcm = merged
    if full[0]:
        warnings.append("mais de 250 timbres FM diferentes: os extras usam o primeiro instrumento")

    # ---- padrões, ordens e loop
    loop_row = loop_f if loop_f is not None and 0 <= loop_f < n_rows else None
    segments = [(0, loop_row), (loop_row, n_rows - loop_row)] if loop_row else [(0, n_rows)]
    orders, loop_order = [], (0 if loop_row == 0 else None)
    for r0, length in segments:
        if loop_row and r0 == loop_row:
            loop_order = len(orders)
        for k in range(0, length, PAT_LEN):
            orders.append((r0 + k, min(PAT_LEN, length - k)))
    if len(orders) > 256:
        raise ValueError(f"música longa demais para o Furnace: {len(orders)} ordens (máx. 256)")
    for oi, (r0, length) in enumerate(orders):
        last = oi == len(orders) - 1
        if last:
            fx = (EFF_JUMP, loop_order) if loop_order is not None else (EFF_STOP, 0)
        elif length < PAT_LEN:
            fx = (EFF_NEXT_PAT, 0)
        else:
            continue
        cell(0, r0 + length - 1).fx[fx[0]] = fx[1]
    rep.orders = len(orders)
    rep.loop_row = loop_row
    rep.instruments = len(instruments)
    rep.samples = len(samples)
    rep.warnings = warnings
    meta = {"name": name or "", "author": author, "game": game, "g": 1, "t0": 0,
            "rows_per_beat": 15, "rows_per_bar": 60, "pat_len": PAT_LEN,
            "vt_n": 150, "vt_d": 150, "use_pcm": use_pcm, "n_ch": n_ch, "ch_names": ch_names}
    return meta, instruments, samples, cells, orders, rep


def write_stream_fur(stream, path, name: str = "", author: str = "", game: str = "") -> FurReport:
    meta, instruments, samples, cells, orders, rep = build(stream, name, author, game)
    Path(path).write_bytes(serialize(meta, instruments, samples, cells, orders))
    return rep
