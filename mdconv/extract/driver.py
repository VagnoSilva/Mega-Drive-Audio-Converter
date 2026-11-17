"""Interpretação do driver de som da Capcom CPS1 (Z80 + YM2151 + OKI MSM6295).

A lógica de formato (versões do driver, tabelas, comandos das sequências e instrumentos)
foi portada do VGMTrans (licença zlib, https://github.com/vgmtrans/vgmtrans), arquivos
src/main/formats/CPS/CPS1*.cpp. Veja LICENSE-VGMTrans.txt.
"""
import heapq

VERSIONS = {"1.00": 100, "2.00": 200, "3.50": 350, "4.25": 425, "5.00": 500, "5.02": 502}

DELTA_TABLE = ((2, 4, 8, 0x10, 0x20, 0x40, 0x80),
               (3, 6, 0xC, 0x18, 0x30, 0x60, 0xC0),
               (0, 9, 0x12, 0x24, 0x48, 0x90, 0))
OCTAVE_TABLE = (0x00, 0x0C, 0x18, 0x24, 0x30, 0x3C, 0x48, 0x54,
                0x18, 0x24, 0x30, 0x3C, 0x48, 0x54, 0x60, 0x6C)
DRIVER_RATE_HZ = 250 / 4.0
OKI_ATTEN = (32, 22, 16, 11, 8, 6, 4, 3, 2, 0, 0, 0, 0, 0, 0, 0)

END, LOOP, OK = 0, 1, 2
MAX_EVENTS_PER_TRACK = 400000
MAX_TICKS = 48 * 2 * 60 * 30   # proteção: ~30 min a 120 bpm


def s8(v):
    v &= 0xFF
    return v - 256 if v >= 128 else v


def s16(v):
    v &= 0xFFFF
    return v - 65536 if v >= 32768 else v


# ----------------------------------------------------------------------------------------
# Instrumentos FM (YM2151 / OPM)
# ----------------------------------------------------------------------------------------
class OPMInstrument:
    """Timbre FM do YM2151 (nomes de parâmetros do VOPM). Operadores na ordem dos registradores:
    M1, M2, C1, C2."""

    def __init__(self, index):
        self.index = index
        self.name = f"Instrument {index}"
        self.transpose = 0
        self.lfo = dict(LFRQ=0, AMD=0, PMD=0, WF=0, NFRQ=0)
        self.ch = dict(PAN=0xC0, FL=0, CON=0, AMS=0, PMS=0, SLOT_MASK=0x78, NE=0)
        self.op = [dict(AR=31, D1R=0, D2R=0, RR=15, D1L=0, TL=0, KS=0, MUL=1, DT1=0, DT2=0, AMS_EN=0)
                   for _ in range(4)]


_CON_LIMITS = (7, 5, 4, 0)   # operador i é portadora quando CON >= limite


def _vol_to_atten_v425(instr_vol):
    """Port literal de CPS1OPMInstrDataV4_25::volToAttenuation (aritmética de 8 bits)."""
    u = ((instr_vol << 8) | (instr_vol >> 4)) & 0xF07
    key_scale_atten = (u >> 8) & 0xFF
    b3 = (0x7F << 1) & 0xFF
    rot = ((b3 << 1) | (b3 >> 7)) & 0xFF
    b2 = (((rot & 0xF0) >> 4) * (((s8(u & 0xFF) << 1) & 0xFF) & 0x0F)) & 0xFF
    b2 = (b2 >> 4) & 0xFF
    if b3 & 0x80:
        b2 = (-b2) & 0xFF
    c = s8(b2) + 0x10 + key_scale_atten
    return 0x7F if c > 0x7F else c & 0xFF


def _fill_common(ins, fl_con, pms_ams, slot_mask, lfo_on, wf, lfrq, pmd, amd,
                 dt1_mul, ks_ar, amsen_d1r, dt2_d2r, d1l_rr):
    if lfo_on:
        ins.lfo.update(LFRQ=lfrq, AMD=amd & 0x7F, PMD=pmd & 0x7F, WF=wf & 3, NFRQ=0)
    ins.ch.update(PAN=0xC0, FL=(fl_con >> 3) & 7, CON=fl_con & 7,
                  AMS=(pms_ams & 3) if lfo_on else 0,
                  PMS=((pms_ams >> 4) & 0x7) if lfo_on else 0,
                  SLOT_MASK=slot_mask, NE=0)
    for i in range(4):
        o = ins.op[i]
        o.update(AR=ks_ar[i] & 31, D1R=amsen_d1r[i] & 31, D2R=dt2_d2r[i] & 31,
                 RR=d1l_rr[i] & 15, D1L=d1l_rr[i] >> 4, KS=ks_ar[i] >> 6,
                 MUL=dt1_mul[i] & 15, DT1=(dt1_mul[i] >> 4) & 7, DT2=dt2_d2r[i] >> 6,
                 AMS_EN=amsen_d1r[i] & 0x80)


def parse_instrument(data, ver, master_vol, index):
    ins = OPMInstrument(index)
    b = data
    mva = 0x7F - master_vol
    if ver == 200:
        lfo_on = b[1] != 0
        _fill_common(ins, b[7], b[6], b[0], lfo_on, b[2], b[3], b[4], b[5],
                     b[8:12], b[16:20], b[20:24], b[24:28], b[28:32])
        for i in range(4):
            carrier = not (ins.ch["CON"] < _CON_LIMITS[i])
            ins.op[i]["TL"] = min(0x7F, mva + (0 if carrier else (b[12 + i] & 0x7F)))
    elif ver in (500, 502):
        lfo_on = (b[1] & 0x80) != 0
        _fill_common(ins, b[7], b[6], b[0], lfo_on, (b[1] >> 5), b[2], b[3], b[4],
                     b[8:12], b[12:16], b[16:20], b[20:24], b[24:28])
        for i in range(4):
            carrier = not (ins.ch["CON"] < _CON_LIMITS[i])
            ins.op[i]["TL"] = min(0x7F, mva + (0 if carrier else (b[28 + i] & 0x7F)))
    else:  # 100, 350, 425
        ins.transpose = s8(b[0])
        lfo_on = (b[1] & 0x80) != 0
        _fill_common(ins, b[5], b[6], b[7], lfo_on, (b[1] >> 5), b[2], b[3], b[4],
                     b[20:24], b[24:28], b[28:32], b[32:36], b[36:40])
        for i in range(4):
            extra, _ks, vol = b[8 + 3 * i], b[9 + 3 * i], b[10 + 3 * i]
            atten = _vol_to_atten_v425(vol)
            if ins.ch["CON"] < _CON_LIMITS[i]:
                ins.op[i]["TL"] = (atten + extra) & 0x7F
            else:
                ins.op[i]["TL"] = min(atten + mva + extra, 0x7F)
    return ins


# ----------------------------------------------------------------------------------------
# Driver: tabelas
# ----------------------------------------------------------------------------------------
class Driver:
    def __init__(self, rom, version, tables):
        self.rom = rom
        self.ver = VERSIONS[version] if isinstance(version, str) else version
        self.tables = tables
        r = rom
        self.sample_table = 0
        self.opm_len = None
        if self.ver == 100:
            self.num_seqs = r[tables]
            self.master_vol = 0x7F
            self.seq_table = tables + 3
            self.opm_table = self.seq_table + (self.num_seqs + 1) * 2
        elif self.ver == 200:
            self.num_seqs = r[tables]
            self.master_vol = 0x7F
            self.opm_table = self.be16(tables + 1)
            self.seq_table = tables + 3
        elif self.ver == 350:
            self.num_seqs = r[tables]
            self.master_vol = r[tables + 1]
            self.opm_table = self.be16(tables + 2)
            self.seq_table = tables + 4
        elif self.ver == 425:
            self.num_seqs = r[tables]
            self.master_vol = r[tables + 1]
            self.opm_table = self.be16(tables + 2)
            self.sample_table = self.be16(tables + 4)
            self.seq_table = tables + 6
        else:  # 500 / 502
            self.opm_len = self.be16(tables)
            self.opm_table = tables + 2
            info = self.opm_table + self.opm_len
            if self.ver == 500:
                self.num_seqs = r[info]
                self.master_vol = r[info + 1]
                self.seq_table = info + 2
            else:
                self.num_seqs = self.be16(info)
                self.master_vol = r[info + 2]
                self.seq_table = info + 4
        self.master_vol &= 0xFF
        self.ppqn = 24 if self.ver == 100 else 48
        self.instruments = self._parse_instruments()

    # leitura
    def be16(self, a):
        return (self.rom[a] << 8) | self.rom[a + 1]

    def le16(self, a):
        return self.rom[a] | (self.rom[a + 1] << 8)

    def seq_pointer(self, i):
        a = self.seq_table + 2 * i
        return self.le16(a) if self.ver == 100 else self.be16(a)

    def _parse_instruments(self):
        size = {200: 32, 500: 32, 502: 32}.get(self.ver, 40)
        if self.opm_len is not None:
            length = self.opm_len
        else:
            length = 127 * size
            if self.ver == 425 and self.sample_table > self.opm_table:
                length = min(length, self.sample_table - self.opm_table)
        count = min(length // size, 128)
        out = []
        for i in range(count):
            off = self.opm_table + i * size
            data = self.rom[off:off + size]
            if len(data) < size:
                break
            if data[0:4] == b"\0\0\0\0" and data[4:8] == b"\0\0\0\0":
                break
            out.append(parse_instrument(data, self.ver, self.master_vol, i))
        return out

    def transpose_for(self, prog):
        if self.ver in (100, 350, 425) and prog < len(self.instruments):
            return self.instruments[prog].transpose
        return 0

    def oki_preset_map(self, num_oki_samples):
        """programa MIDI (banco 1) -> número do sample no Oki (1..127)."""
        m = {}
        if self.ver == 425 and self.sample_table:
            for i in range(128):
                off = self.sample_table + i * 4
                if off + 1 >= len(self.rom) or not (self.rom[off] & 0x80):
                    break
                m[i] = self.rom[off + 1]
        else:
            for i in range(1, 128):
                m[i] = i
        return {p: s for p, s in m.items() if 1 <= s <= num_oki_samples}

    def song_list(self):
        """Lista (id, ponteiro) das músicas válidas (ignora efeitos e ponteiros nulos)."""
        out = []
        for i in range(self.num_seqs):
            try:
                p = self.seq_pointer(i)
                if p == 0 or p >= len(self.rom):
                    continue
                if self.rom[p] & 0x80:   # sequência de efeito sonoro
                    continue
                tracks = self.track_offsets(p)
                if tracks:
                    out.append((i, p, len(tracks)))
            except IndexError:
                continue
        return out

    def track_offsets(self, p):
        rd16 = self.le16 if self.ver == 100 else self.be16
        maxt = 8 if self.ver == 100 else 12
        res = []
        for i in range(maxt):
            t = rd16(p + 1 + 2 * i)
            if t == 0:
                continue
            if self.ver >= 500:
                t = (t + p) & 0xFFFF
            if t < len(self.rom):
                res.append((i, t))
        return res


# ----------------------------------------------------------------------------------------
# Sequências -> eventos
# ----------------------------------------------------------------------------------------
class SongState:
    def __init__(self, drv):
        self.drv = drv
        self.master_vol = drv.master_vol
        self.global_transpose = 0
        self.tempos = []       # (tick, microssegundos por semínima)


class TrackBase:
    def __init__(self, drv, song, idx, offset, oki):
        self.drv, self.song, self.idx, self.pos, self.oki = drv, song, idx, offset, oki
        self.start = offset
        self.t = 0
        self.events = []       # (tick, prioridade, tipo, a, b)
        self.open = {}         # tecla "crua" -> tecla MIDI (notas presas/ligadas)
        self.transpose = 0
        self.ckey = 0
        self.n_events = 0
        self.rom = drv.rom

    # --- emissão
    def _midikey(self, key):
        if self.oki:
            return 60
        k = key + self.ckey + self.transpose + self.song.global_transpose
        while k > 127:
            k -= 12
        while k < 0:
            k += 12
        return k

    def _vel(self):
        v = self.song.master_vol
        return max(1, min(127, v))

    def note_on(self, key):
        mk = self._midikey(key)
        self.open[key] = mk
        self.events.append((self.t, 1, "ont", mk, self._vel()))   # nota ligada (fim explícito)

    def note_off(self, key):
        mk = self.open.pop(key, None)
        if mk is not None:
            self.events.append((self.t, 0, "off", mk, 0))

    def note_dur(self, key, dur):
        dur = max(1, dur)
        mk = self._midikey(key)
        self.events.append((self.t, 1, "on", mk, self._vel()))
        self.events.append((self.t + dur, 0, "off", mk, 0))

    def ev(self, kind, a=0, b=0):
        self.events.append((self.t, 1, kind, a, b))

    def close_all(self):
        for k in list(self.open):
            self.note_off(k)

    def rd(self, a):
        return self.rom[a]

    def be16(self, a):
        return (self.rom[a] << 8) | self.rom[a + 1]

    def le16(self, a):
        return self.rom[a] | (self.rom[a + 1] << 8)


class TrackV2(TrackBase):
    """Faixas das versões 2.00 a 5.02."""

    def __init__(self, *a):
        super().__init__(*a)
        self.note_duration = 0xFF
        self.prev_tie = False
        self.prev_tie_note = 0
        self.ns = 0
        self.prog = -1
        self.loop = [0, 0, 0, 0]
        self.loop_off = [0, 0, 0, 0]

    def step(self):
        ver = self.drv.ver
        rd = self.rd
        pos = self.pos
        sb = rd(pos)
        pos += 1
        if sb >= 0x20:
            ns = self.ns
            if (ns & 0x30) == 0:
                cur = 1
            elif (ns & 0x10) == 0:
                cur = 0
            else:
                self.ns &= ~0x10
                cur = 2
            delta = DELTA_TABLE[cur][((sb >> 5) & 7) - 1]
            if sb & 0x1F:
                abs_dur = (((delta * self.note_duration) & 0xFFFF) >> 8) & 0xFF
                if self.oki:
                    if ver >= 500:
                        newp = s8((sb & 0x1F) + OCTAVE_TABLE[self.ns & 0x0F] - 1)
                        if newp != self.prog and newp >= 0:
                            self.prog = newp
                            self.ev("prog", newp)
                    key = 0x3C
                else:
                    key = (sb & 0x1F) + OCTAVE_TABLE[self.ns & 0x0F] + 12 - 1
                if self.ns & 0x40:
                    if not self.prev_tie:
                        self.note_on(key)
                    elif key != self.prev_tie_note:
                        self.note_off(self.prev_tie_note)
                        self.note_on(key)
                    self.prev_tie = True
                    self.prev_tie_note = key
                else:
                    if self.prev_tie:
                        if key != self.prev_tie_note:
                            self.note_off(self.prev_tie_note)
                            self.note_dur(key, abs_dur - 1)
                        else:
                            self.t += abs_dur
                            delta = max(0, delta - abs_dur)
                            self.note_off(self.prev_tie_note)
                    else:
                        self.note_dur(key, abs_dur - 1)
                    self.prev_tie = False
            self.t += delta
            self.pos = pos
            return OK

        if sb == 0x00:
            self.ns ^= 0x20
        elif sb == 0x01:
            self.ns ^= 0x40
        elif sb == 0x02:
            self.ns |= 0x10
        elif sb == 0x03:
            self.ns ^= 8
        elif sb == 0x04:
            self.ns = (self.ns & 0x97) | rd(pos)
            pos += 1
        elif sb == 0x05:
            tpi = self.be16(pos)
            pos += 2
            if tpi:
                ipb = (self.drv.ppqn << 8) / tpi
                self.song.tempos.append((self.t, int(round(ipb / DRIVER_RATE_HZ * 1e6))))
        elif sb == 0x06:
            self.note_duration = rd(pos)
            pos += 1
        elif sb == 0x07:
            v = rd(pos)
            pos += 1
            if self.oki:
                amp = OKI_ATTEN[(8 - v) & 0xF] / 32.0
                self.ev("cc", 7, int(round(127 * amp ** 0.5)))
            else:
                self.ev("cc", 7, min(127, v))
        elif sb == 0x08:
            prog = rd(pos)
            pos += 1
            self.ev("prog", prog % 128)
            if not self.oki:
                self.ckey = self.drv.transpose_for(prog)
        elif sb == 0x09:
            self.ns = (self.ns & 0xF8) | rd(pos)
            pos += 1
        elif sb == 0x0A:
            self.song.global_transpose = s8(rd(pos))
            pos += 1
        elif sb == 0x0B:
            self.transpose = s8(rd(pos))
            pos += 1
        elif sb == 0x0C:
            pb = rd(pos) >> 2
            pos += 1
            cents = pb * 1.587301587301587
            if pb >= 32:
                cents -= 100
            self.ev("bend", int(max(0, min(16383, 8192 + cents / 200.0 * 8192))))
        elif sb == 0x0D:
            pos += 1
        elif 0x0E <= sb <= 0x11:
            n = sb - 0x0E
            if self.loop[n] == 0 and self.loop_off[n] == 0:
                self.loop_off[n] = pos
                self.loop[n] = rd(pos)
                pos += 1
            elif self.loop_off[n] != pos:
                return END
            else:
                self.loop[n] = (self.loop[n] - 1) & 0xFF
                pos += 1
            if ver <= 425:
                jump = self.be16(pos)
                pos += 2
                if jump == 0:
                    return END
            else:
                if (rd(pos) & 0x80) == 0:
                    jb = rd(pos)
                    pos += 1
                    jump = pos - jb
                else:
                    jump = pos + 2 + s16(self.be16(pos))
                    pos += 2
            if self.loop[n] == 0:
                self.loop_off[n] = 0
            else:
                pos = jump
        elif 0x12 <= sb <= 0x15:
            n = sb - 0x12
            if self.loop[n] == 1:
                self.loop[n] = 0
                self.loop_off[n] = 0
                self.ns = (self.ns & 0x97) | rd(pos)
                pos += 1
                jump = self.be16(pos)
                pos += 2
                pos = jump if ver <= 425 else pos + s16(jump)
            else:
                pos += 3
        elif sb == 0x16:
            jump = self.be16(pos) if ver <= 425 else pos + 2 + s16(self.be16(pos))
            if jump == 0:
                return END
            self.pos = jump & 0xFFFF
            return LOOP
        elif sb == 0x17:
            return END
        elif sb == 0x18:
            pan = rd(pos) * 4
            pos += 1
            if pan:
                pan -= 1
            self.ev("cc", 10, max(0, min(127, pan)))
        elif sb == 0x1A:
            self.song.master_vol = rd(pos)
            pos += 1
        elif 0x19 <= sb <= 0x1F:
            pos += 1
        self.pos = pos & 0xFFFF
        return OK


class TrackV1(TrackBase):
    """Faixas da versão 1.00 (Ghouls'n Ghosts, Strider, Willow...)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.note_duration = 0
        self.prev_tie = False
        self.prev_tie_note = 0
        self.cps0transpose = 0
        self.instr_transpose = 0
        self.extend = False
        self.shorten = 0
        self.tie_counter = 0
        self.loop = [0, 0, 0]

    def step(self):
        rd = self.rd
        pos = self.pos
        sb = rd(pos)
        pos += 1
        if sb >= 0x40:
            shift = max(((sb >> 5) & 7) - 2, 0)
            delta = 3 << shift
            if self.shorten > 0:
                delta = int(delta / 1.5)
                self.shorten -= 1
            if self.extend:
                delta += delta // 2
                self.extend = False
            abs_dur = ((((delta * self.note_duration) & 0xFFFF) >> 8) + 1) & 0xFF
            abs_dur = max(1, abs_dur - 1)
            key = sb & 0x1F
            if key == 0:
                if self.prev_tie:
                    self.note_off(self.prev_tie_note)
                self.t += delta
                self.pos = pos
                return OK
            key = s8(key + self.cps0transpose + self.instr_transpose)
            while key > 0x60:
                key -= 12
            while key < 0:
                key += 12
            key += 12
            if self.tie_counter > 1:
                if not self.prev_tie:
                    self.note_on(key)
                elif key != self.prev_tie_note:
                    self.note_off(self.prev_tie_note)
                    self.note_on(key)
                self.prev_tie = True
                self.prev_tie_note = key
                self.tie_counter -= 1
            else:
                if self.prev_tie:
                    if key != self.prev_tie_note:
                        self.note_off(self.prev_tie_note)
                        self.note_dur(key, abs_dur)
                    else:
                        self.t += abs_dur
                        delta = max(0, delta - abs_dur)
                        self.note_off(self.prev_tie_note)
                else:
                    self.note_dur(key, abs_dur)
                self.prev_tie = False
            self.t += delta
            self.pos = pos
            return OK

        if sb >= 0x20:
            if sb >= 0x30:
                self.shorten = sb & 0xF
            else:
                self.tie_counter = (sb & 0xF) + 1
            self.pos = pos
            return OK

        if sb == 0x00:
            tb = (rd(pos) + 0x80) & 0xFF
            pos += 1
            new_tempo = (((tb >> 3) << 8) | ((tb >> 2) << 7)) & 0xFFFF
            if new_tempo:
                self.song.tempos.append((self.t, new_tempo << 7))
        elif sb == 0x01:
            self.note_duration = rd(pos)
            pos += 1
        elif sb == 0x02:
            if self.loop[0] == 1:
                self.loop[0] = 0
                pos = self.le16(pos)
            else:
                pos += 2
        elif sb in (0x03, 0x04, 0x05):
            n = sb - 3
            num = rd(pos)
            pos += 1
            target = self.le16(pos)
            if num == 0:
                self.pos = target
                return LOOP
            if self.loop[n] == 0:
                self.loop[n] = num
            else:
                self.loop[n] -= 1
                if self.loop[n] == 0:
                    self.pos = pos + 2
                    return OK
            pos = target
        elif sb == 0x06:
            self.extend = True
        elif sb == 0x07:
            self.cps0transpose = s8(rd(pos))
            pos += 1
        elif sb == 0x08:
            pb = rd(pos) >> 2
            pos += 1
            cents = pb * 1.587301587301587
            if pb >= 32:
                cents -= 100
            self.ev("bend", int(max(0, min(16383, 8192 + cents / 200.0 * 8192))))
        elif sb in (0x09, 0x0A, 0x0E):
            pos += 1
        elif sb == 0x0B:
            pos += 2
        elif sb == 0x0C:
            prog = rd(pos)
            pos += 1
            self.ev("prog", prog % 128)
            self.instr_transpose = self.drv.transpose_for(prog)
        elif sb == 0x0D:
            v = s8(0x7F + rd(pos))
            pos += 1
            self.ev("cc", 7, max(0, min(127, v)))
        elif sb == 0x0F:
            return END
        self.pos = pos & 0xFFFF
        return OK


class Song:
    def __init__(self, drv, song_id, pointer):
        self.drv, self.id, self.pointer = drv, song_id, pointer
        self.tempos = []
        self.tracks = []      # TrackBase concluídas
        self.length = 0
        self.loop_start = self.loop_end = None   # ticks (marcadores loopStart/loopEnd)

    def _make_tracks(self, state):
        tracks = []
        for i, off in self.drv.track_offsets(self.pointer):
            oki = self.drv.ver != 100 and i >= 8
            cls = TrackV1 if self.drv.ver == 100 else TrackV2
            tracks.append(cls(self.drv, state, i, off, oki))
        return tracks

    @staticmethod
    def _run(tracks, stop_tick=None):
        """Executa todas as faixas em ordem de tempo. Retorna dict idx -> instantes dos loops."""
        hits = {t.idx: [] for t in tracks}
        heap = [(0, n) for n in range(len(tracks))]
        heapq.heapify(heap)
        while heap:
            _, n = heapq.heappop(heap)
            tr = tracks[n]
            if stop_tick is not None and tr.t >= stop_tick:
                continue
            if tr.n_events > MAX_EVENTS_PER_TRACK or tr.t > MAX_TICKS:
                continue
            tr.n_events += 1
            try:
                r = tr.step()
            except IndexError:
                r = END
            if r == END:
                continue
            if r == LOOP:
                h = hits[tr.idx]
                if h and h[-1] == tr.t:   # loop sem avanço de tempo: encerra
                    continue
                h.append(tr.t)
                if stop_tick is None and len(h) >= 2:
                    continue
            heapq.heappush(heap, (tr.t, n))
        return hits

    def build(self, plays=2):
        """plays = quantas vezes a parte que repete (loop) é tocada."""
        # 1ª passada: mede introdução/loop de cada faixa
        tracks = self._make_tracks(SongState(self.drv))
        hits = self._run(tracks)
        # Loop da música: o maior período entre as faixas; começa quando todas as faixas
        # que repetem já entraram no trecho de loop.
        intros, periods, ends = [], [], [1]
        for tr in tracks:
            h = hits[tr.idx]
            if len(h) >= 2 and h[1] > h[0]:
                periods.append(h[1] - h[0])
                intros.append(h[0] - (h[1] - h[0]))
            elif h:
                ends.append(h[0])
            else:
                ends.append(tr.t)
        self.loop_start = self.loop_end = None
        if periods:
            period = max(periods)
            intro = max(0, max(intros))
            self.loop_start, self.loop_end = intro, intro + period
            end = max(intro + plays * period, max(ends))
        else:
            end = max(ends)
        # 2ª passada: gera os eventos até o fim calculado
        state = SongState(self.drv)
        tracks = self._make_tracks(state)
        self._run(tracks, stop_tick=end)
        for tr in tracks:
            tr.t = min(tr.t, end)
            tr.close_all()
            tr.events = [(min(e[0], end),) + e[1:] for e in tr.events
                         if e[2] == "off" or e[0] < end]
        self.tempos = sorted(state.tempos)
        self.tracks = tracks
        self.length = end
        return self
