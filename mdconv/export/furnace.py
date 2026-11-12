"""Exportação para o Furnace (.fur), formato da versão 0.6.8.3 (versão de formato 232).

O arquivo é escrito seguindo ``DivEngine::saveFur`` do próprio Furnace 0.6.8.3, campo a
campo, com os valores padrão de uma música nova. O conteúdo é a MESMA distribuição que
vai no XGM: cada nota no canal do chip em que o driver a colocou (FM 1–5, PCM 1–2, PSG 1–3),
com os mesmos patches, volumes, pitch bend, vibrato e loop.

Sistema: "YM2612 DualPCM" (FM1–5 + 2 canais de amostras, o modo do Furnace mais próximo
do XGM com a bateria em 2 canais) + "SN76489" (PSG da Sega). Sem PCM: "YM2612" (6 FM)
+ "SN76489". O YM2612 vai configurado como YM2612 do Mega Drive 1 (com o efeito ladder).

Tempo
-----
Cada linha do pattern vale ``g`` ticks do MIDI (24 por semínima no GBA). O Furnace roda a
60 ticks por segundo (NTSC) com "virtual tempo" N/D = BPM/150 — o mesmo acumulador do
driver do GBA —, então as notas caem no mesmo quadro em que o jogo as tocaria. ``g`` é o
menor valor que respeita o limite do Furnace de uma linha por quadro (BPM ≤ 150·g). Notas
que caem entre linhas recebem atraso EDxx (em quadros). Mudanças de andamento: FDxx.

O que vira efeito
-----------------
* pitch bend → E5xx (±1 semitom; além disso a nota é ajustada e o resto vai no E5);
* vibrato do m4a (CC1 + velocidade) → 04xy com forma triangular (E303) e alcance E4xx;
* volume/expressão/velocity → coluna de volume (FM: 0–7F, mesmos passos de TL do driver;
  PSG: 0–F);
* pan → 08xy;
* fim de nota → OFF (FM) ou REL (PSG, dispara o release do envelope);
* loop → 0Bxx no fim; padrões cortados com 0D00 para o loop começar num padrão.
"""
from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mdconv.fm.patch import Patch

FUR_VERSION = 232                      # Furnace 0.6.8.3
MAGIC = b"-Furnace module-"
SYS_YM2612 = 0x83
SYS_YM2612_DUALPCM = 0xBE
SYS_SN76489 = 0x03
FILE_TO_LOGICAL = (0, 2, 1, 3)         # ordem dos operadores no Furnace (registradores)
NOTE_OFF, NOTE_RELEASE = 180, 181
PSG_NOTE_SHIFT = 24
FPS = 60.0
TL_STEP_DB = 0.75
PSG_STEP_DB = 2.0
EFF_PITCH, EFF_VIB, EFF_VIB_SHAPE, EFF_VIB_RANGE = 0xE5, 0x04, 0xE3, 0xE4
EFF_DELAY, EFF_CUT, EFF_PAN = 0xED, 0xEC, 0x08
EFF_NEXT_PAT, EFF_JUMP, EFF_STOP, EFF_VTEMPO_N = 0x0D, 0x0B, 0xFF, 0xFD


# ====================================================================== escrita binária
class _W:
    def __init__(self):
        self.b = bytearray()

    def tell(self):
        return len(self.b)

    def c(self, v):
        self.b += struct.pack("<B", v & 0xFF)

    def s(self, v):
        self.b += struct.pack("<H", v & 0xFFFF)

    def i(self, v):
        self.b += struct.pack("<I", v & 0xFFFFFFFF)

    def f(self, v):
        self.b += struct.pack("<f", float(v))

    def raw(self, data):
        self.b += data

    def string(self, txt):
        self.b += txt.encode("utf-8") + b"\x00"

    def put_i(self, pos, v):
        struct.pack_into("<I", self.b, pos, v & 0xFFFFFFFF)

    def put_s(self, pos, v):
        struct.pack_into("<H", self.b, pos, v & 0xFFFF)

    def block(self, ident: bytes):
        """Escreve ID + tamanho provisório; devolve a posição do tamanho."""
        self.raw(ident)
        p = self.tell()
        self.i(0)
        return p

    def end_block(self, p):
        self.put_i(p, self.tell() - p - 4)


# ====================================================================== modelo
@dataclass
class FurInstrument:
    name: str
    kind: str                           # 'fm' | 'psg' | 'kit' | 'noise'
    patch: Patch | None = None
    vol_macro: list[int] | None = None  # PSG: 0–15 por tick (ADSR); ruído: sequência por quadro
    vol_rel: int = 255
    note_map: dict[int, int] | None = None   # kit: tecla MIDI → índice da amostra


@dataclass
class FurSample:
    name: str
    data: np.ndarray                    # int8
    rate: int


@dataclass
class Cell:
    note: int | None = None             # nota do Furnace (0–179) ou NOTE_OFF/NOTE_RELEASE
    ins: int | None = None
    vol: int | None = None
    fx: dict = field(default_factory=dict)   # efeito → valor (ordem de inserção mantida)


@dataclass
class FurReport:
    rows: int
    rows_per_beat: int
    ticks_per_row: int
    pattern_len: int
    orders: int
    loop_row: int | None
    channels: list[str]
    instruments: int
    samples: int
    notes: int
    delayed_notes: int = 0
    clamped_bends: int = 0
    clamped_tempos: int = 0
    merged_pcm: int = 0
    merged_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    def text(self) -> str:
        L = [f"Furnace 0.6.8.3 (.fur): {self.notes} notas, {self.instruments} instrumentos, "
             f"{self.samples} amostras",
             f"  {self.rows} linhas · {self.ticks_per_row} tick(s) do MIDI por linha "
             f"({self.rows_per_beat} linhas por tempo) · padrões de {self.pattern_len} linhas · "
             f"{self.orders} ordens",
             "  canais: " + ", ".join(self.channels),
             ("  loop na linha %d" % self.loop_row) if self.loop_row is not None else "  sem loop"]
        if self.delayed_notes:
            L.append(f"  {self.delayed_notes} nota(s) entre linhas: tocadas no quadro certo com EDxx")
        if self.clamped_bends:
            L.append(f"  ⚠ {self.clamped_bends} pitch bend(s) além de ±1 semitom ajustados pela nota + E5xx")
        if self.clamped_tempos:
            L.append(f"  ⚠ {self.clamped_tempos} mudança(s) de andamento acima do limite foram limitadas")
        if self.merged_pcm:
            L.append(f"  ⚠ {self.merged_pcm} golpe(s) de bateria de canais PCM extras foram para PCM 2")
        if self.merged_rows:
            L.append(f"  ⚠ {self.merged_rows} nota(s) muito curtas no mesmo canal caíram na mesma linha")
        L += [f"  ⚠ {w}" for w in self.warnings]
        return "\n".join(L)


# ====================================================================== tempo
class _Clock:
    """Conversões tick ↔ segundos ↔ quadros, com o mapa de andamento da música."""

    def __init__(self, song):
        self.tm = song.tempo_map
        self.changes = self.tm.changes

    def seconds(self, tick):
        return self.tm.seconds(tick)

    def tick_at(self, s: float) -> int:
        c = self.changes[0]
        for ch in self.changes:
            if ch.seconds <= s + 1e-12:
                c = ch
            else:
                break
        return int(round(c.tick + (s - c.seconds) * 1e6 * self.tm.ppq / c.us_per_quarter))

    def bpm(self, tick) -> float:
        return self.tm.bpm_at(tick)


# ====================================================================== conversão
def build_fur_song(conv, name: str = "", author: str = "", game: str = ""):
    """Monta o modelo do .fur a partir de uma :class:`~mdconv.convert.Conversion`."""
    song, drv, plan, stream = conv.song, conv.driver, conv.plan, conv.stream
    if drv is None or song is None:
        raise ValueError("a conversão não guardou a linha do tempo do driver")
    clk = _Clock(song)
    ppq = song.ppq
    use_pcm = bool(plan.pcm_channels)
    pcm_list = sorted(plan.pcm_channels)
    warnings: list[str] = []

    # ---- canais do Furnace
    n_fm = 5 if use_pcm else 6
    ch_names = [f"FM {i + 1}" for i in range(n_fm)]
    if use_pcm:
        ch_names += ["PCM 1", "PCM 2"]
    psg0 = len(ch_names)
    ch_names += ["PSG 1", "PSG 2", "PSG 3", "Ruído"]
    n_ch = len(ch_names)

    def fur_channel(kind, idx):
        if kind == "fm":
            return idx
        if kind == "psg":
            return psg0 + idx
        if kind == "noise":
            return psg0 + 3
        raise ValueError(kind)

    # ---- intervalo de tempo exportado (sem a introdução vazia do ripper)
    bar = ppq * 4
    if song.time_signatures:
        _, num, den = song.time_signatures[0]
        bar = ppq * 4 * num // den
    first_note = min(n.start_tick for n in song.notes)
    loop = conv.loop
    loop_tick = None
    if loop is not None:
        loop_tick = loop.start_tick
        # o ripper costuma marcar o loop 1 tick antes das notas: sem nada no meio, alinha
        nxt = -(-loop_tick // (ppq // 4)) * (ppq // 4)
        busy = any(loop_tick <= n.start_tick < nxt for n in song.notes) or \
            any(loop_tick <= e.tick < nxt for e in song.channel_events)
        if not busy:
            loop_tick = nxt
    t0 = (min(first_note, loop_tick if loop_tick is not None else first_note) // bar) * bar
    end_tick = loop.end_tick if loop is not None else max(
        clk.tick_at(len(stream.frames) / FPS), max(n.end_tick for n in song.notes))
    max_bpm = max([clk.bpm(t0)] + [c.bpm for c in clk.changes if t0 < c.tick < end_tick])
    g = max(1, math.ceil(max_bpm / 150.0 - 1e-9))
    while ppq % g:
        g += 1
    rows_per_beat = ppq // g
    rows_per_bar = bar // g
    pat_len = max(rows_per_bar, (256 // rows_per_bar) * rows_per_bar) if rows_per_bar <= 256 else 256
    n_rows = max(1, (end_tick - t0 + g - 1) // g)   # a última linha termina em end_tick

    cells: list[dict[int, Cell]] = [dict() for _ in range(n_ch)]
    rep = FurReport(0, rows_per_beat, g, pat_len, 0, None, ch_names, 0, 0, 0)

    def cell(ch, row) -> Cell:
        c = cells[ch].get(row)
        if c is None:
            c = cells[ch][row] = Cell()
        return c

    def place(tick):
        """(linha, atraso em quadros) para um tick do MIDI."""
        off = tick - t0
        row, rem = divmod(off, g)
        delay = int(round(rem * 150.0 / clk.bpm(tick))) if rem else 0
        return row, delay

    def frame_of_tick(tick):
        return int(round(clk.seconds(tick) * FPS))

    # ---- instrumentos
    instruments: list[FurInstrument] = []
    ins_index: dict[tuple, int] = {}

    def label_of(bp):
        part = next((p for p in plan.parts if bp in p.programs), None)
        return part.preset if part else f"{bp[0]}:{bp[1]}"

    def fm_ins(bp):
        key = ("fm", bp)
        if key not in ins_index:
            ins_index[key] = len(instruments)
            instruments.append(FurInstrument(f"FM {label_of(bp)}", "fm", patch=conv.patches[bp]))
        return ins_index[key]

    def psg_ins(bp):
        key = ("psg", bp)
        if key not in ins_index:
            env = (conv.psg_envelopes or {}).get(bp, {})
            vals, rel = _psg_macro(env)
            ins_index[key] = len(instruments)
            instruments.append(FurInstrument(f"PSG {label_of(bp)}", "psg", vol_macro=vals, vol_rel=rel))
        return ins_index[key]

    noise_pieces = {}                    # instrumento → atenuação mínima (pico) da peça

    def noise_ins(n):
        """Peça de ruído: macro de volume = envelope relativo ao pico (quadro a quadro, como no
        driver) e macro de "duty" = 1 (ruído branco, taxa fixa)."""
        k = (n.bank, n.program, n.key)
        key = ("noise", k)
        if key not in ins_index:
            pc = conv.kit.noise[k]
            a0 = float(np.min(pc.att_db))
            mac = [max(0, min(15, 15 - int(round((a - a0) / PSG_STEP_DB)))) for a in pc.att_db][:254]
            mac.append(0)                                    # fim da peça: silêncio
            ins_index[key] = len(instruments)
            instruments.append(FurInstrument(f"Ruído {pc.name}", "noise", vol_macro=mac))
            noise_pieces[ins_index[key]] = (a0, pc.vel_ref)
        return ins_index[key]

    # ---- amostras e kits (PCM)
    samples: list[FurSample] = []
    sid_to_idx: dict[int, int] = {}
    if use_pcm and conv.kit is not None:
        for sid in sorted(conv.kit.samples):
            sid_to_idx[sid] = len(samples)
            samples.append(FurSample(conv.kit.names.get(sid, f"amostra {sid}"),
                                     np.asarray(conv.kit.samples[sid], np.int8), int(round(conv.kit.rate))))

    def kit_ins(bp):
        key = ("kit", bp)
        if key not in ins_index:
            nm = {}
            for (b, p, k), sid in conv.kit.ids.items():
                if (b, p) == bp and sid in sid_to_idx:
                    nm[k] = sid_to_idx[sid]
            ins_index[key] = len(instruments)
            instruments.append(FurInstrument(f"Bateria {label_of(bp)}", "kit", note_map=nm))
        return ins_index[key]

    # ---- linha do tempo do driver → notas por canal
    events = []                          # (canal fur, tick_ini, tick_fim | None, nota, tipo, modo_fim)
    open_notes: dict[tuple, dict] = {}
    for ev in drv.timeline:
        kind = ev[0]
        if kind == "on":
            _, f, typ, idx, n = ev
            k = (typ, idx)
            if k in open_notes:                          # roubo: fecha a anterior aqui
                open_notes.pop(k)["end_f"] = f
            nominal = int(round(n.start_s * FPS))
            st = n.start_tick if abs(f - nominal) <= 1 else clk.tick_at(f / FPS)
            rec = {"ch": fur_channel(typ, idx), "typ": typ, "n": n, "start": st, "end_f": None,
                   "rel_f": None}
            open_notes[k] = rec
            events.append(rec)
        elif kind == "release":
            _, f, typ, idx = ev
            rec = open_notes.get((typ, idx))
            if rec is not None:
                rec["rel_f"] = f
        elif kind == "off":
            _, f, typ, idx = ev
            rec = open_notes.pop((typ, idx), None)
            if rec is not None:
                rec["end_f"] = f
        elif kind == "pcm" and use_pcm:
            _, f, pch, n, sid = ev
            slot = pcm_list.index(pch) if pch in pcm_list else 1
            if slot > 1:
                slot = 1
                rep.merged_pcm += 1
            events.append({"ch": n_fm + slot, "typ": "pcm", "n": n, "start": n.start_tick, "end_f": None,
                           "rel_f": None, "sid": sid})

    def tick_of_end(rec, f):
        n = rec["n"]
        for t in (n.end_tick, getattr(n, "sustain_end_tick", n.end_tick)):
            if abs(frame_of_tick(t) - f) <= 1:
                return t
        return clk.tick_at(f / FPS)

    # ---- escrita das notas
    events.sort(key=lambda r: (r["start"], r["ch"]))
    last_ins = [None] * n_ch
    for rec in events:
        ch, n, typ = rec["ch"], rec["n"], rec["typ"]
        if rec["start"] < t0 or rec["start"] > end_tick:
            continue
        row, delay = place(rec["start"])
        if row >= n_rows:
            continue
        c = cell(ch, row)
        if c.note is not None and c.note < NOTE_OFF:
            rep.merged_rows += 1
        bp = (n.bank, n.program)
        if typ == "fm":
            ins = fm_ins(bp)
            key = n.key + 12 * conv.patches[bp].octave
        elif typ == "psg":
            ins = psg_ins(bp)
            # convenção do Furnace (herdada do DefleMask): no SN76489 a nota escrita soa
            # 2 oitavas acima — o "A-4" do PSG toca 1760 Hz. Medido no Furnace 0.6.8.3.
            key = n.key - PSG_NOTE_SHIFT
        elif typ == "noise":
            ins = noise_ins(n)
            # no modo de taxa fixa o Furnace escolhe a taxa pela nota: C = N/2048, C# = N/1024,
            # D = N/512 (o ruído do driver)
            key = 62
        else:
            ins = kit_ins(bp)
            key = n.key
        c.note = max(0, min(179, key + 48))
        c.fx.pop(EFF_DELAY, None)
        if delay:
            c.fx[EFF_DELAY] = min(255, delay)
            rep.delayed_notes += 1
        if ins != last_ins[ch]:
            c.ins = ins
            last_ins[ch] = ins
        else:
            c.ins = ins                    # explícito: facilita editar/copiar trechos
        rep.notes += 1
        rec["row"] = row
        if typ == "noise":
            # volume da nota (coluna) = pico da peça + velocity, como no driver; o envelope
            # está no macro e termina em silêncio — sem OFF
            a0, vref = noise_pieces[ins]
            att = a0 + 20 * math.log10(vref / max(1, n.velocity))
            c.vol = max(0, min(15, 15 - int(round(att / PSG_STEP_DB))))
            continue
        # fim
        if typ == "pcm":
            continue
        end_f = rec["rel_f"] if typ == "psg" and rec["rel_f"] is not None else rec["end_f"]
        if end_f is None:
            continue
        et = tick_of_end(rec, end_f)
        if et > end_tick:
            continue
        erow, edelay = place(et)
        mark = NOTE_RELEASE if typ == "psg" else NOTE_OFF
        if erow <= row:
            # nota mais curta que uma linha: corta depois de N quadros
            frames = max(1, frame_of_tick(et) - frame_of_tick(rec["start"]))
            c.fx[EFF_CUT] = min(255, frames)
            continue
        ec = cell(ch, erow)
        if ec.note is None:
            ec.note = mark
            if edelay:
                ec.fx[EFF_DELAY] = min(255, edelay)
        rec["erow"] = erow

    # ---- parâmetros contínuos por nota (volume, bend, vibrato, pan)
    ev_ticks: dict[tuple, list[int]] = {}
    for e in song.channel_events:
        ev_ticks.setdefault((e.track, e.channel), []).append(e.tick)
    for k in ev_ticks:
        ev_ticks[k] = sorted(set(ev_ticks[k]))
    state = [{"pitch": 0x80, "vib": None, "range": None, "pan": None} for _ in range(n_ch)]
    for rec in sorted((r for r in events if "row" in r and r["typ"] not in ("pcm", "noise")),
                      key=lambda r: r["row"]):
        ch, n, typ = rec["ch"], rec["n"], rec["typ"]
        row0 = rec["row"]
        row1 = rec.get("erow", row0 + 1)
        ticks = [rec["start"]] + [t for t in ev_ticks.get((n.track, n.channel), [])
                                  if rec["start"] < t < t0 + row1 * g]
        last_vol = None
        st = state[ch]
        for t in ticks:
            r, _ = place(t)
            r = max(r, row0)
            if r >= n_rows:
                break
            fr = frame_of_tick(t)
            c = cell(ch, r)
            lvl = drv._level_db(n, fr)
            if typ == "fm":
                vol = max(0, min(127, 127 - int(round((lvl + drv.fm_att_db) / TL_STEP_DB))))
            else:
                vol = max(0, min(15, 15 - int(round((lvl + drv.psg_att_db) / PSG_STEP_DB))))
            if vol != last_vol or r == row0:
                c.vol = vol
                last_vol = vol
            cur = drv._curve(n)
            tt = fr / FPS
            bend_c = cur["bend"].at(tt) / 8192.0 * cur["bend_range"].at(tt) * 100
            if r == row0 and abs(bend_c) > 100:
                semis = int(round(bend_c / 100))
                if c.note is not None and c.note < NOTE_OFF:
                    c.note = max(0, min(179, c.note + semis))
                bend_c -= semis * 100
                rep.clamped_bends += 1
            pv = max(0, min(255, 0x80 + int(round(bend_c * 128 / 100))))
            if abs(bend_c) > 100:
                rep.clamped_bends += 1
            if pv != st["pitch"]:
                c.fx[EFF_PITCH] = pv
                st["pitch"] = pv
            if drv.vibrato:
                mod, lfos = cur["mod"].at(tt), cur["lfos"].at(tt)
                vib = _vibrato_params(mod, lfos)
                if vib != st["vib"]:
                    if vib is None:
                        c.fx[EFF_VIB] = 0
                    else:
                        (rate, depth), fine = vib
                        if fine != st["range"]:
                            c.fx[EFF_VIB_RANGE] = fine
                            st["range"] = fine
                        c.fx[EFF_VIB] = (rate << 4) | depth
                    st["vib"] = vib
            if typ == "fm":
                pan = _pan_fx(cur["pan"].at(tt))
                if pan != st["pan"]:
                    if st["pan"] is not None or pan != 0xFF:
                        c.fx[EFF_PAN] = pan
                    st["pan"] = pan

    # ---- notas que ainda soam no fim: corta ao final da última linha (como o XGM, que
    # desliga tudo no fim antes de voltar ao loop)
    last_row = n_rows - 1
    frames_last = max(1, int(round(g * 150.0 / clk.bpm(t0 + last_row * g))))
    for rec in events:
        if rec["typ"] == "pcm" or "row" not in rec or rec["row"] > last_row:
            continue
        if rec.get("erow") is not None and rec["erow"] <= last_row:
            continue
        if EFF_CUT in cell(rec["ch"], rec["row"]).fx:
            continue
        cl = cell(rec["ch"], last_row)
        if cl.note is None or cl.note >= NOTE_OFF:
            cl.note = None
            cl.fx[EFF_CUT] = frames_last

    # ---- forma do vibrato (triangular, como no GBA) no começo de cada canal tonal
    for ch in range(n_ch):
        if ch < n_fm or ch >= psg0:
            cell(ch, 0).fx.setdefault(EFF_VIB_SHAPE, 3)

    # ---- andamento
    tempo_fx = {}
    for chg in clk.changes:
        if t0 < chg.tick < end_tick:
            r, _ = place(chg.tick)
            N = int(round(chg.bpm))
            if N > min(255, 150 * g):
                N = min(255, 150 * g)
                rep.clamped_tempos += 1
            tempo_fx[r] = N
    for r, N in tempo_fx.items():
        cell(0, r).fx[EFF_VTEMPO_N] = N
    vt_n = int(round(clk.bpm(t0)))
    if vt_n > 150 * g:
        vt_n = 150 * g
        rep.clamped_tempos += 1

    # ---- padrões, ordens e loop
    loop_row = None
    if loop_tick is not None and t0 <= loop_tick < end_tick:
        loop_row, _ = place(loop_tick)
    segments = []                        # (linha inicial, número de linhas)
    if loop_row is not None and loop_row > 0:
        segments.append((0, loop_row))
        segments.append((loop_row, n_rows - loop_row))
    else:
        segments.append((0, n_rows))
    orders = []                          # cada ordem: (linha inicial, linhas)
    loop_order = None
    for si, (r0, length) in enumerate(segments):
        if loop_row is not None and r0 == loop_row:
            loop_order = len(orders)
        if loop_row == 0 and si == 0:
            loop_order = 0
        for k in range(0, length, pat_len):
            orders.append((r0 + k, min(pat_len, length - k)))
    if len(orders) > 256:
        raise ValueError(f"música longa demais para o Furnace: {len(orders)} ordens (máx. 256)")
    # 0D00 no fim de padrões encurtados e 0Bxx/FF00 no último
    for oi, (r0, length) in enumerate(orders):
        last = oi == len(orders) - 1
        if last:
            fx = (EFF_JUMP, loop_order) if loop_order is not None else (EFF_STOP, 0)
        elif length < pat_len:
            fx = (EFF_NEXT_PAT, 0)
        else:
            continue
        cell(0, r0 + length - 1).fx[fx[0]] = fx[1]

    rep.rows = n_rows
    rep.orders = len(orders)
    rep.loop_row = loop_row
    rep.instruments = len(instruments)
    rep.samples = len(samples)
    rep.warnings = warnings
    meta = {"name": name or "", "author": author, "game": game, "g": g, "t0": t0,
            "rows_per_beat": rows_per_beat, "rows_per_bar": rows_per_bar, "pat_len": pat_len,
            "vt_n": vt_n, "use_pcm": use_pcm, "n_ch": n_ch, "ch_names": ch_names}
    return meta, instruments, samples, cells, orders, rep


def _pan_fx(cc10: float) -> int:
    if cc10 < 32:
        return 0xF0
    if cc10 > 96:
        return 0x0F
    return 0xFF


def _vibrato_params(mod: float, lfos: float):
    """CC1 (profundidade) + velocidade do m4a → ((velocidade, profundidade), alcance E4xx).

    m4a: triângulo de ±mod·100/256 cents a lfos·60/256 Hz. Furnace (pitch linear): saída
    triangular de pico 128 → desvio = prof·128·alcance/16/15 unidades de 1/128 semitom;
    velocidade x avança x/64 de ciclo por tick (60 Hz)."""
    if mod <= 0:
        return None
    target = mod * 15.0 / 16.0                       # prof·alcance desejado
    best = None
    for d in range(1, 16):
        fine = max(1, min(255, int(round(target / d))))
        err = abs(d * fine - target)
        if best is None or err < best[0] - 1e-9 or (abs(err - best[0]) < 1e-9 and d > best[1]):
            best = (err, d, fine)
    _, d, fine = best
    rate = max(1, min(15, int(round(lfos / 4.0))))
    return (rate, d), fine


def _psg_macro(env: dict) -> tuple[list[int], int]:
    """Envelope do PSG (o mesmo do driver) → macro de volume do Furnace em modo ADSR.

    No ADSR do Furnace a posição vai de 0 a 255 e o volume é posição/16 (0–15): cada passo
    de volume do PSG (2 dB) são 16 unidades, ou 8 por dB. Decaimento e release do driver são
    retas em dB (100 dB em ``decay_s``/``release_s``), que no ADSR viram decrementos fixos por
    tick — e o release parte do volume em que a nota está, como no driver. (Um macro em
    sequência pularia para o nível de sustentação ao soltar a nota.)
    Retorna ([low, high, AR, HT, DR, SL, ST, SR, RR] + zeros, -1)."""
    def rate(seconds):
        if seconds <= 1.0 / FPS:
            return 255
        return max(1, min(255, int(round(800.0 / (seconds * FPS)))))
    att = env.get("attack_s", 0.0)
    ar = 255 if att <= 1.0 / FPS else max(1, min(255, int(round(255.0 / (att * FPS)))))
    ht = max(0, min(255, int(round(env.get("hold_s", 0.0) * FPS))))
    dec = env.get("decay_s", 0.0)
    sus = env.get("sustain_db", 0.0)
    dr = rate(dec) if dec > 0 else 0
    sl = max(0, min(255, int(round(255 + 8 * sus)))) if dec > 0 else 255
    rr = rate(max(env.get("release_s", 0.0), 1e-3))
    vals = [0, 15, ar, ht, dr, sl, 0, 0, rr] + [0] * 7
    return vals, -1


# ====================================================================== serialização
def _ins_block(w: _W, ins: FurInstrument):
    p = w.block(b"INS2")
    w.s(FUR_VERSION)
    typ = {"fm": 1, "psg": 0, "noise": 0, "kit": 4}[ins.kind]
    w.s(typ)

    def feature(code: bytes, body):
        w.raw(code)
        q = w.tell()
        w.s(0)
        body()
        w.put_s(q, w.tell() - q - 2)

    feature(b"NA", lambda: w.string(ins.name))
    if ins.kind == "fm":
        pt = ins.patch

        def fm():
            w.c(0xF0 | 4)
            w.c(((pt.alg & 7) << 4) | (pt.fb & 7))
            w.c(((pt.ams & 3) << 3) | (pt.fms & 7))
            w.c(32)
            w.c(0)
            for j in range(4):
                o = pt.ops[FILE_TO_LOGICAL[j]]
                w.c(((o.dt + 3) & 7) << 4 | (o.mul & 15))
                w.c(o.tl & 127)
                w.c(((o.ks & 3) << 6) | (o.ar & 31))
                w.c((128 if getattr(o, "am", 0) else 0) | (o.d1r & 31))
                w.c((2 << 5) | (o.d2r & 31))
                w.c(((o.sl & 15) << 4) | (o.rr & 15))
                w.c(getattr(o, "ssg", 0) & 15)
                w.c(0)
        feature(b"FM", fm)
    elif ins.kind == "psg":
        def ma():
            w.s(8)
            v = ins.vol_macro
            w.c(0)                      # macro de volume
            w.c(len(v))
            w.c(255)                    # sem loop
            w.c(255)                    # (release do ADSR: valor RR)
            w.c(0)                      # modo
            w.c(1 | 2)                  # aberto + tipo ADSR; 8 bits sem sinal
            w.c(0)                      # atraso
            w.c(1)                      # velocidade
            for x in v:
                w.c(x)
            w.c(0xFF)
        feature(b"MA", ma)
    elif ins.kind == "noise":
        def ma_noise():
            w.s(8)
            v = ins.vol_macro
            for code, data in ((0, v), (2, [1])):       # volume; duty = modo de ruído (1 = branco)
                w.c(code)
                w.c(len(data))
                w.c(255)                    # sem loop
                w.c(255)                    # sem release
                w.c(0)                      # modo
                w.c(0)                      # sequência; 8 bits sem sinal
                w.c(0)                      # atraso
                w.c(1)                      # velocidade
                for x in data:
                    w.c(x)
            w.c(0xFF)
        feature(b"MA", ma_noise)
    else:
        def sm():
            nm = ins.note_map or {}
            first = next(iter(sorted(nm.values())), 0)
            w.s(first)
            w.c(1)                      # usa mapa de notas
            w.c(32)
            for note in range(120):
                key = note + 12          # índice do Furnace (C-0 = 0) → tecla MIDI
                if key in nm:
                    w.s(48)              # toca na altura C-4 = taxa original da amostra
                    w.s(nm[key])
                else:
                    w.s(note)
                    w.s(0xFFFF)          # −1: sem amostra
        feature(b"SM", sm)
    w.raw(b"EN")
    w.end_block(p)


def _sample_block(w: _W, smp: FurSample):
    p = w.block(b"SMP2")
    w.string(smp.name)
    w.i(len(smp.data))
    w.i(smp.rate)                      # taxa
    w.i(smp.rate)                      # taxa em C-4
    w.c(8)                             # 8 bits
    w.c(0)                             # modo de loop
    w.c(0)
    w.c(0)
    w.i(0xFFFFFFFF)
    w.i(0xFFFFFFFF)
    for _ in range(4):
        w.i(0xFFFFFFFF)                # renderOn: todos os chips
    w.raw(np.asarray(smp.data, np.int8).tobytes())
    w.end_block(p)


def _pattern_block(w: _W, chan: int, pat: int, rows: list[Cell | None], eff_cols: int):
    p = w.block(b"PATN")
    w.c(0)                             # subsong
    w.c(chan)
    w.s(pat)
    w.string("")
    empty = 0
    for cl in rows:
        if cl is None or (cl.note is None and cl.ins is None and cl.vol is None and not cl.fx):
            empty += 1
            if empty > 127:
                w.c(128 | (empty - 2))
                empty = 0
            continue
        if empty > 1:
            w.c(128 | (empty - 2))
        elif empty:
            w.c(0)
        empty = 0
        fx = list(cl.fx.items())[:eff_cols]
        mask = 0
        emask = 0
        if cl.note is not None:
            mask |= 1
        if cl.ins is not None:
            mask |= 2
        if cl.vol is not None:
            mask |= 4
        for k, _ in enumerate(fx):
            kk = 2 * k
            emask |= (1 << kk) | (2 << kk)
            if k == 0:
                mask |= 8 | 16
            elif k < 4:
                mask |= 32
            else:
                mask |= 64
        w.c(mask)
        if mask & 32:
            w.c(emask & 0xFF)
        if mask & 64:
            w.c((emask >> 8) & 0xFF)
        if mask & 1:
            w.c(cl.note)
        if mask & 2:
            w.c(cl.ins)
        if mask & 4:
            w.c(cl.vol)
        if fx:
            w.c(fx[0][0])
            w.c(fx[0][1])
        for e, v in fx[1:4]:
            w.c(e)
            w.c(v)
        for e, v in fx[4:8]:
            w.c(e)
            w.c(v)
    w.c(0xFF)
    w.end_block(p)


def serialize(meta, instruments, samples, cells, orders, compress: bool = True) -> bytes:
    n_ch = meta["n_ch"]
    pat_len = meta["pat_len"]
    systems = [SYS_YM2612_DUALPCM if meta["use_pcm"] else SYS_YM2612, SYS_SN76489]
    chip_flags = ["chipType=1\n", ""]   # YM2612 do Mega Drive 1 (com ladder); PSG da Sega
    # colunas de efeito por canal
    eff_cols = []
    for ch in range(n_ch):
        m = max([len(c.fx) for c in cells[ch].values()] + [1])
        eff_cols.append(min(8, max(1, m)))

    w = _W()
    w.raw(MAGIC)
    w.s(FUR_VERSION)
    w.s(0)
    w.i(32)
    w.i(0)
    w.i(0)
    n_pat = n_ch * len(orders)

    # ---- INFO
    info = w.block(b"INFO")
    w.c(0)                              # time base
    w.c(meta["g"])                      # speed 1
    w.c(meta["g"])                      # speed 2
    w.c(1)                              # arp
    w.f(60.0)
    w.s(pat_len)
    w.s(len(orders))
    w.c(meta["rows_per_beat"])
    w.c(meta["rows_per_bar"])
    w.s(len(instruments))
    w.s(0)
    w.s(len(samples))
    w.i(n_pat)
    for k in range(32):
        w.c(systems[k] if k < len(systems) else 0)
    for k in range(32):
        w.c(64 if k < len(systems) else 0)
    for k in range(32):
        w.c(0)
    flags_seek = w.tell()
    for _ in range(32):
        w.i(0)
    w.string(meta["name"])
    w.string(meta["author"])
    w.f(440.0)
    # compat flags (padrões de uma música nova no 0.6.8.3)
    for v in (0, 2, 2, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1):
        w.c(v)
    ptr_seek = w.tell()
    for _ in range(len(instruments) + len(samples) + n_pat):
        w.i(0)
    for ch in range(n_ch):
        for oi in range(len(orders)):
            w.c(oi)
    for ch in range(n_ch):
        w.c(eff_cols[ch])
    for ch in range(n_ch):
        w.c(3)                          # visível no pattern e no osciloscópio
    for ch in range(n_ch):
        w.c(0)
    for ch in range(n_ch):
        w.string("")
    for ch in range(n_ch):
        w.string("")
    w.string("Exportado pelo Mega Drive Audio Converter.")
    w.f(1.0)
    # extended compat flags
    for v in (0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 0, 0, 1, 4, 0, 0, 1, 1, 0, 0, 0, 0, 2, 0, 1, 0, 0, 0):
        w.c(v)
    w.s(meta["vt_n"])
    w.s(150)
    w.string("")
    w.string("")
    w.c(0)
    w.c(0)
    w.c(0)
    w.c(0)
    w.string("")                        # nome do sistema (automático)
    w.string(meta["game"])
    for _ in range(4):
        w.string("")
    for _ in systems:
        w.f(1.0)
        w.f(0.0)
        w.f(0.0)
    w.i(0)                              # patchbay
    w.c(1)                              # patchbay automático
    for _ in range(8):
        w.c(0)
    w.c(1)                              # padrão de velocidade: 1 valor
    for k in range(16):
        w.c(meta["g"] if k == 0 else 0)
    w.c(0)                              # grooves
    adir_seek = w.tell()
    w.i(0)
    w.i(0)
    w.i(0)
    w.end_block(info)

    # ---- flags dos chips
    flag_ptrs = []
    for fl in chip_flags:
        if not fl:
            flag_ptrs.append(0)
            continue
        flag_ptrs.append(w.tell())
        q = w.block(b"FLAG")
        w.string(fl)
        w.end_block(q)
    # ---- pastas (vazias)
    adir = []
    for _ in range(3):
        adir.append(w.tell())
        q = w.block(b"ADIR")
        w.i(0)
        w.end_block(q)
    # ---- instrumentos, amostras, padrões
    ins_ptrs, smp_ptrs, pat_ptrs = [], [], []
    for ins in instruments:
        ins_ptrs.append(w.tell())
        _ins_block(w, ins)
    for smp in samples:
        smp_ptrs.append(w.tell())
        _sample_block(w, smp)
    for ch in range(n_ch):
        for oi, (r0, length) in enumerate(orders):
            rows = [cells[ch].get(r0 + k) for k in range(length)] + [None] * (pat_len - length)
            pat_ptrs.append(w.tell())
            _pattern_block(w, ch, oi, rows, eff_cols[ch])
    # ---- ponteiros
    pos = ptr_seek
    for v in ins_ptrs + smp_ptrs + pat_ptrs:
        w.put_i(pos, v)
        pos += 4
    for k, v in enumerate(flag_ptrs):
        w.put_i(flags_seek + 4 * k, v)
    for k, v in enumerate(adir):
        w.put_i(adir_seek + 4 * k, v)
    data = bytes(w.b)
    return zlib.compress(data, 9) if compress else data


def write_fur(conv, path, name: str = "", author: str = "", game: str = "") -> FurReport:
    meta, instruments, samples, cells, orders, rep = build_fur_song(conv, name, author, game)
    if len(instruments) > 256 or len(samples) > 256:
        raise ValueError("o Furnace aceita até 256 instrumentos e 256 amostras")
    Path(path).write_bytes(serialize(meta, instruments, samples, cells, orders))
    return rep


# ====================================================================== leitura (validação)
@dataclass
class FurCheck:
    version: int
    systems: list[int]
    instruments: int
    samples: int
    patterns: int
    orders: int
    pattern_len: int
    notes: int
    names: list[str]


class FurError(ValueError):
    pass


def read_fur(data: bytes) -> FurCheck:
    """Leitor independente do escritor: confere a estrutura (blocos, ponteiros, tamanhos)
    e conta as notas dos padrões. Não substitui abrir no Furnace, mas pega arquivo
    corrompido ou fora do formato."""
    if not data.startswith(MAGIC):
        try:
            data = zlib.decompress(data)
        except zlib.error as e:
            raise FurError(f"não é .fur nem zlib: {e}") from None
    if not data.startswith(MAGIC):
        raise FurError("assinatura '-Furnace module-' ausente")
    ver, _, info = struct.unpack_from("<HHI", data, 16)
    if data[info:info + 4] != b"INFO":
        raise FurError("bloco INFO não encontrado")
    size = struct.unpack_from("<I", data, info + 4)[0]
    if info + 8 + size > len(data):
        raise FurError("bloco INFO passa do fim do arquivo")
    p = info + 8
    _tb, _s1, _s2, _arp = data[p:p + 4]
    hz = struct.unpack_from("<f", data, p + 4)[0]
    pat_len, ord_len = struct.unpack_from("<HH", data, p + 8)
    n_ins, n_wave, n_smp = struct.unpack_from("<HHH", data, p + 14)
    n_pat = struct.unpack_from("<I", data, p + 20)[0]
    systems = [x for x in data[p + 24:p + 56] if x]
    if abs(hz - 60.0) > 1e-3:
        raise FurError(f"taxa de ticks {hz} ≠ 60 (NTSC)")
    q = p + 56 + 32 + 32 + 128
    names = []
    for _ in range(2):
        e = data.index(b"\x00", q)
        names.append(data[q:e].decode("utf-8", "replace"))
        q = e + 1
    q += 4 + 20                                   # afinação + flags de compatibilidade
    ptrs = struct.unpack_from(f"<{n_ins + n_wave + n_smp + n_pat}I", data, q)

    def check(ptr, magic):
        if data[ptr:ptr + 4] != magic:
            raise FurError(f"ponteiro {ptr} não aponta para {magic.decode()}")
        sz = struct.unpack_from("<I", data, ptr + 4)[0]
        if ptr + 8 + sz > len(data):
            raise FurError(f"bloco {magic.decode()} em {ptr} passa do fim do arquivo")
        return ptr + 8, sz
    for k in range(n_ins):
        b, sz = check(ptrs[k], b"INS2")
        if data[b + sz - 2:b + sz] != b"EN":
            raise FurError(f"instrumento {k} sem marcador de fim")
    for k in range(n_smp):
        check(ptrs[n_ins + n_wave + k], b"SMP2")
    notes = 0
    for k in range(n_pat):
        b, sz = check(ptrs[n_ins + n_wave + n_smp + k], b"PATN")
        r = b + 4
        r = data.index(b"\x00", r) + 1
        row = 0
        while True:
            m = data[r]
            r += 1
            if m == 0xFF:
                break
            if m & 0x80:
                row += (m & 0x7F) + 2
                continue
            if m == 0:
                row += 1
                continue
            emask = 0
            if m & 32:
                emask |= data[r]
                r += 1
            if m & 64:
                emask |= data[r] << 8
                r += 1
            if m & 1:
                if data[r] < 180:
                    notes += 1
                r += 1
            r += bool(m & 2) + bool(m & 4) + bool(m & 8) + bool(m & 16)
            r += bin(emask & ~3 & 0xFFFF).count("1")
            row += 1
            if row > pat_len:
                raise FurError(f"padrão {k} com mais linhas que {pat_len}")
        if r > b + sz:
            raise FurError(f"padrão {k} lido além do bloco")
    return FurCheck(ver, systems, n_ins, n_smp, n_pat, ord_len, pat_len, notes, names)
