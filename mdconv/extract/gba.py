"""Game Boy Advance: motor de som da Nintendo (MP2k / "Sappy" / m4a).

Lê a tabela de músicas, as sequências e os bancos de instrumentos (voicegroups) e gera
MIDI + SoundFont no mesmo padrão do GBA Mus Ripper (banco = índice do voicegroup, programa =
posição no voicegroup, presets "Type N @0x…", amostras "Sample @0x…", "square 12.5%",
"GB3 @0x…", "Noise …"). Assim os patches já aprovados no Mega Drive Audio Converter para
arquivos do Ripper continuam valendo.
"""
import math
import os
import struct
import zipfile

from . import sf2 as SF
from .midi import _chunk, _meta, _vlq

BASE = 0x08000000
PPQ = 24

# durações dos comandos de espera (0x80-0xB0) e de nota (0xD0-0xFF)
LENGTHS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
           24, 28, 30, 32, 36, 40, 42, 44, 48, 52, 54, 56, 60, 64, 66, 68, 72, 76, 78, 80, 84, 88,
           90, 92, 96]

# argumentos dos comandos simples
ARGS = {0xB9: 3, 0xBA: 1, 0xBB: 1, 0xBC: 1, 0xBD: 1, 0xBE: 1, 0xBF: 1, 0xC0: 1, 0xC1: 1,
        0xC2: 1, 0xC3: 1, 0xC4: 1, 0xC5: 1, 0xC8: 1, 0xCD: 2}

MAX_TICKS = PPQ * 4 * 1000        # trava de segurança (~1000 compassos)


# ============================================================================ ROM
def load_rom(path):
    """Bytes da ROM (.gba, .agb, .bin ou .zip com um desses dentro)."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".gba", ".agb", ".bin"))]
            if not names:
                raise FileNotFoundError("Nenhum arquivo .gba dentro do zip.")
            names.sort(key=lambda n: -z.getinfo(n).file_size)
            return z.read(names[0])
    with open(path, "rb") as f:
        return f.read()


def is_gba(path):
    p = path.lower()
    if p.endswith((".gba", ".agb")):
        return True
    if p.endswith(".zip") and os.path.isfile(path):
        try:
            with zipfile.ZipFile(path) as z:
                return any(n.lower().endswith((".gba", ".agb")) for n in z.namelist())
        except zipfile.BadZipFile:
            return False
    return False


class Rom:
    def __init__(self, data):
        self.d = data
        self.n = len(data)

    def u8(self, o):
        return self.d[o]

    def u32(self, o):
        return struct.unpack_from("<I", self.d, o)[0]

    def ptr(self, o):
        """Lê um ponteiro GBA em o e devolve o deslocamento na ROM (ou None)."""
        if o < 0 or o + 4 > self.n:
            return None
        p = self.u32(o)
        if BASE <= p < BASE + self.n:
            return p - BASE
        return None

    def valid(self, off, size=1):
        return off is not None and 0 <= off and off + size <= self.n


def find_song_table(rom):
    """Procura a tabela de músicas (entradas de 8 bytes: ponteiro do cabeçalho + 2 × u16 pequenos).

    Devolve (deslocamento, nº de entradas). Escolhe a maior sequência cujos cabeçalhos são
    válidos."""
    import numpy as np
    n = rom.n // 4 * 4
    w = np.frombuffer(rom.d[:n], dtype="<u4")
    valid = (w >= BASE) & (w < BASE + rom.n) & (w % 4 == 0)
    small = ((w & 0xFFFF) < 32) & ((w >> 16) < 32)
    ok = np.zeros(len(w), bool)
    ok[:-1] = valid[:-1] & small[1:]
    runs = []
    for par in (0, 1):
        o = ok[par::2].astype(np.int8)
        edges = np.flatnonzero(np.diff(np.concatenate(([0], o, [0]))))
        for a, b in zip(edges[::2], edges[1::2]):
            if b - a >= 4:
                runs.append((int(b - a), (par + 2 * int(a)) * 4))
    runs.sort(reverse=True)
    for count, off in runs[:20]:
        good = sum(1 for i in range(min(count, 64)) if _header_ok(rom, rom.ptr(off + 8 * i)))
        if good >= min(count, 64) * 0.8:
            return off, count
    raise ValueError("Tabela de músicas do motor de som da Nintendo (MP2k/Sappy) não encontrada. "
                     "O jogo pode usar outro motor de som.")


def _header_ok(rom, h):
    if not rom.valid(h, 8):
        return False
    nt = rom.u8(h)
    if nt > 16:
        return False
    if nt == 0:
        return True
    if rom.ptr(h + 4) is None or not rom.valid(h + 8, 4 * nt):
        return False
    return all(rom.ptr(h + 8 + 4 * i) is not None for i in range(nt))


# ============================================================================ sequências
class GBASong:
    def __init__(self, sid, header, ntracks, voicegroup, reverb, priority):
        self.sid, self.header, self.ntracks = sid, header, ntracks
        self.voicegroup, self.reverb, self.priority = voicegroup, reverb, priority
        self.tracks = []          # por faixa: lista de eventos (tick, pri, kind, a, b)
        self.loop_start = None
        self.loop_end = None
        self.end = 0
        self.programs = set()     # programas usados
        self.drum_keys = {}       # programa -> teclas tocadas


def parse_song(rom, sid, header):
    nt = rom.u8(header)
    vg = rom.ptr(header + 4) if nt else None
    song = GBASong(sid, header, nt, vg, rom.u8(header + 3), rom.u8(header + 2))
    loops = []
    for t in range(nt):
        start = rom.ptr(header + 8 + 4 * t)
        ev, loop, end = _parse_track(rom, start, song)
        song.tracks.append(ev)
        if loop:
            loops.append(loop)
        song.end = max(song.end, end)
    if loops:
        song.loop_start = max(a for a, b in loops)
        song.loop_end = max(b for a, b in loops)
        song.end = max(song.end, song.loop_end)
    return song


def _parse_track(rom, pos, song):
    """Executa uma faixa até FINE ou GOTO (laço). Devolve (eventos, (início, fim) do laço, fim)."""
    ev = []
    tick = 0
    seen = {}                 # endereço -> tick da primeira passagem (para achar o início do laço)
    stack = []                # retorno de PATT
    rept = {}                 # endereço do REPT -> repetições restantes
    last_cmd = None
    key, vel, gate_len = 60, 100, 0
    keysh = 0
    on = {}                   # notas com TIE abertas: tecla -> True
    program = 0
    loop = None
    steps = 0

    def note_on(k, v, length):
        k2 = max(0, min(127, k + keysh))
        ev.append((tick, 2, "on", k2, max(1, min(127, v))))
        if length is not None:
            ev.append((tick + length, 0, "off", k2, 0))
        song.programs.add(program)
        song.drum_keys.setdefault(program, set()).add(k2)
        return k2

    while rom.valid(pos) and steps < 200000 and tick < MAX_TICKS:
        steps += 1
        if pos not in seen and not stack:
            seen[pos] = tick
        b = rom.u8(pos)
        if b < 0x80:
            if last_cmd is None or last_cmd < 0xBD:
                pos += 1
                continue
            cmd = last_cmd                       # "running status": o byte já é o 1º argumento
        else:
            cmd = b
            pos += 1
            if cmd >= 0xBD:
                last_cmd = cmd

        def arg():
            nonlocal pos
            v = rom.u8(pos) if rom.valid(pos) else 0
            pos += 1
            return v

        def opt():
            nonlocal pos
            if rom.valid(pos) and rom.u8(pos) < 0x80:
                v = rom.u8(pos)
                pos += 1
                return v
            return None

        if 0x80 <= cmd <= 0xB0:                  # espera
            tick += LENGTHS[cmd - 0x80]
        elif cmd >= 0xD0 or cmd == 0xCF:         # nota (Nxx) ou TIE
            k = opt()
            if k is not None:
                key = k
                v = opt()
                if v is not None:
                    vel = v
                    g = opt() if cmd != 0xCF else None
                    gate_len = g or 0
                else:
                    gate_len = 0
            else:
                gate_len = 0
            if cmd == 0xCF:
                k2 = note_on(key, vel, None)
                on[k2] = True
            else:
                note_on(key, vel, LENGTHS[cmd - 0xCF] + gate_len)
        elif cmd == 0xCE:                        # EOT: termina nota com TIE
            k = opt()
            if k is not None:
                key = k
            k2 = max(0, min(127, key + keysh))
            if k2 in on:
                ev.append((tick, 0, "off", k2, 0))
                del on[k2]
        elif cmd == 0xB1:                        # FINE
            break
        elif cmd == 0xB2:                        # GOTO
            dest = rom.ptr(pos)
            pos += 4
            if dest is not None and dest in seen:
                loop = (seen[dest], tick)
            break
        elif cmd == 0xB3:                        # PATT (chamada)
            dest = rom.ptr(pos)
            pos += 4
            if dest is None or len(stack) > 3:
                continue
            stack.append(pos)
            pos = dest
        elif cmd == 0xB4:                        # PEND (retorno)
            if stack:
                pos = stack.pop()
        elif cmd == 0xB5:                        # REPT n, endereço
            here = pos - 1
            n = arg()
            dest = rom.ptr(pos)
            pos += 4
            left = rept.get(here, n)
            if left > 0 and dest is not None:
                rept[here] = left - 1
                pos = dest
            else:
                rept.pop(here, None)
        elif cmd in ARGS:
            vals = [arg() for _ in range(ARGS[cmd])]
            v = vals[0]
            if cmd == 0xBB:
                ev.append((tick, 0, "tempo", v * 2, 0))
            elif cmd == 0xBC:
                keysh = v - 256 if v > 127 else v
            elif cmd == 0xBD:
                program = v & 0x7F
                ev.append((tick, 1, "prog", program, 0))
            elif cmd == 0xBE:
                ev.append((tick, 1, "cc", 7, v & 0x7F))
            elif cmd == 0xBF:
                ev.append((tick, 1, "cc", 10, v & 0x7F))
            elif cmd == 0xC0:
                ev.append((tick, 1, "bend", ((v & 0x7F) - 64) * 128, 0))
            elif cmd == 0xC1:
                ev.append((tick, 1, "pbr", v & 0x7F, 0))
            elif cmd == 0xC4:
                ev.append((tick, 1, "cc", 1, v & 0x7F))
        else:
            pass                                 # comando desconhecido: ignora
    for k in list(on):
        ev.append((tick, 0, "off", k, 0))
    return ev, loop, tick


# ============================================================================ jogo
class GBAGame:
    format = "GBA"

    def __init__(self, path):
        self.path = path
        self.rom = Rom(load_rom(path))
        self.name = _game_name(path, self.rom)
        self.warnings = []
        self.table, self.count = find_song_table(self.rom)
        self.entry = {"version": "MP2k"}
        self._songs = {}
        self.voicegroups = None

    # --- lista de músicas (id, endereço, nº de faixas)
    def songs(self):
        out = []
        for i in range(self.count):
            h = self.rom.ptr(self.table + 8 * i)
            if h is None or not _header_ok(self.rom, h):
                continue
            nt = self.rom.u8(h)
            if nt:
                out.append((i, h, nt))
        return out

    def song(self, sid):
        if sid not in self._songs:
            h = self.rom.ptr(self.table + 8 * sid)
            self._songs[sid] = parse_song(self.rom, sid, h)
        return self._songs[sid]

    def bank_numbers(self):
        """Banco de cada voicegroup: posição na lista ordenada por endereço (como o GBA Mus Ripper)."""
        if self.voicegroups is None:
            vgs = set()
            for sid, h, nt in self.songs():
                vg = self.rom.ptr(h + 4)
                if vg is not None:
                    vgs.add(vg)
            self.voicegroups = {vg: i for i, vg in enumerate(sorted(vgs))}
        return self.voicegroups


def _game_name(path, rom):
    stem = os.path.splitext(os.path.basename(path))[0]
    if rom.n >= 0xB0:
        title = rom.d[0xA0:0xAC].split(b"\0")[0].decode("ascii", "replace").strip()
        if title and not stem:
            return title
    return stem


# ============================================================================ MIDI
def _curve(v):
    """Volume linear do GBA -> escala MIDI (raiz quadrada, como o GBA Mus Ripper)."""
    return max(0, min(127, int(math.sqrt(max(0, v) * 127))))


def write_midi(path, game, song, banks):
    bank = banks.get(song.voicegroup, 0)
    conductor = [(0, _meta(0x03, f"{game.name} song {song.sid:03d}".encode("latin-1", "replace")))]
    has_tempo = any(e[2] == "tempo" and e[0] == 0 for tr in song.tracks for e in tr)
    if not has_tempo:
        conductor.append((0, _meta(0x51, struct.pack(">I", 60000000 // 150)[1:])))
    for tr in song.tracks:
        for e in tr:
            if e[2] == "tempo" and e[3] > 0:
                conductor.append((e[0], _meta(0x51, struct.pack(">I", 60000000 // e[3])[1:])))
    if song.loop_start is not None and song.loop_end is not None and song.loop_end > song.loop_start:
        conductor.append((song.loop_start, _meta(0x06, b"loopStart")))
        conductor.append((song.loop_end, _meta(0x06, b"loopEnd")))
    end = song.loop_end if song.loop_end else song.end
    conductor.sort(key=lambda x: x[0])
    chunks = [_chunk(conductor)]
    for ti, tr in enumerate(song.tracks):
        ch = ti & 15
        out = [(0, _meta(0x03, f"Faixa {ti + 1}".encode())),
               (0, bytes([0xB0 | ch, 0, bank & 0x7F])),
               (0, bytes([0xB0 | ch, 91, min(127, song.reverb & 0x7F)]))]
        held = {}
        for t, pri, kind, a, b in sorted(tr, key=lambda e: (e[0], e[1])):
            if t > end:
                continue
            if kind == "on":
                if held.get(a):           # mesma tecla ainda soando: fecha antes
                    out.append((t, bytes([0x80 | ch, a, 0])))
                held[a] = held.get(a, 0) + 1
                out.append((t, bytes([0x90 | ch, a, max(1, _curve(b))])))
            elif kind == "off":
                if held.get(a):
                    held[a] -= 1
                    if held[a] == 0:
                        out.append((min(t, end), bytes([0x80 | ch, a, 0])))
            elif kind == "prog":
                out.append((t, bytes([0xC0 | ch, a])))
            elif kind == "cc":
                out.append((t, bytes([0xB0 | ch, a, _curve(b) if a == 7 else b])))
            elif kind == "bend":
                v = max(0, min(16383, 8192 + a))
                out.append((t, bytes([0xE0 | ch, v & 0x7F, v >> 7])))
            elif kind == "pbr":
                out += [(t, bytes([0xB0 | ch, 101, 0])), (t, bytes([0xB0 | ch, 100, 0])),
                        (t, bytes([0xB0 | ch, 6, a])), (t, bytes([0xB0 | ch, 38, 0]))]
        for k, n in held.items():
            if n:
                out.append((end, bytes([0x80 | ch, k, 0])))
        out.sort(key=lambda x: x[0])
        chunks.append(_chunk(out))
    data = b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), PPQ) + b"".join(chunks)
    with open(path, "wb") as f:
        f.write(data)


# ============================================================================ SoundFont
def _env_ds(att, dec, sus, rel):
    """Envelope DirectSound (por quadro de 1/60 s) -> geradores SF2."""
    g = []
    if att < 255:
        g.append((SF.G_ATTACK_VOL, SF.timecents((255 / max(1, att)) / 60)))
    if sus < 255:
        if dec == 0:
            dt = 0.001
        else:
            db = -20 * math.log10(dec / 256)
            dt = (100 / db) / 60 if db > 0 else 100
        g.append((SF.G_DECAY_VOL, SF.timecents(min(dt, 100))))
        g.append((SF.G_SUSTAIN_VOL, 1440 if sus == 0 else min(1440, int(-200 * math.log10(sus / 255)))))
    if rel == 0:
        rt = 0.001
    else:
        db = -20 * math.log10(rel / 256)
        rt = (100 / db) / 60 if db > 0 else 100
    g.append((SF.G_RELEASE_VOL, SF.timecents(min(rt, 100))))
    return g


def _env_cgb(att, dec, sus, rel):
    """Envelope dos canais estilo Game Boy (passos de 1/60 s em 16 níveis)."""
    att, dec, sus, rel = att & 7, dec & 7, sus & 15, rel & 7
    g = []
    if att:
        g.append((SF.G_ATTACK_VOL, SF.timecents(att * 15 / 60)))
    if sus < 15:
        g.append((SF.G_DECAY_VOL, SF.timecents(max(0.001, dec * (15 - sus) / 60) * 3)))
        g.append((SF.G_SUSTAIN_VOL, 1440 if sus == 0 else min(1440, int(-200 * math.log10(sus / 15)))))
    g.append((SF.G_RELEASE_VOL, SF.timecents(max(0.001, rel * max(sus, 1) / 60) * 3)))
    return g


class _Builder:
    def __init__(self, game, sf, log=None):
        self.g, self.rom, self.sf, self.log = game, game.rom, sf, log
        self.samples = {}         # chave -> índice no SF2
        self.bad = set()

    # --- amostras
    def ds_sample(self, addr):
        key = ("ds", addr)
        if key in self.samples:
            return self.samples[key]
        rom = self.rom
        if not rom.valid(addr, 16):
            return None
        flags = rom.u32(addr)
        pitch = rom.u32(addr + 4)
        loop_start = rom.u32(addr + 8)
        size = rom.u32(addr + 12)
        if flags & 0xFF == 0x01:
            if addr not in self.bad:
                self.bad.add(addr)
                self.g.warnings.append(f"amostra comprimida em 0x{addr:x} não suportada")
            return None
        if size == 0 or size > 4_000_000 or not rom.valid(addr + 16, size):
            return None
        raw = rom.d[addr + 16: addr + 16 + size]
        pcm = bytearray(2 * size)
        for i, v in enumerate(raw):
            s = (v - 256 if v > 127 else v) << 8
            pcm[2 * i] = s & 0xFF
            pcm[2 * i + 1] = (s >> 8) & 0xFF
        rate_f = pitch / 1024 if pitch else 13379
        rate = max(400, int(round(rate_f)))
        corr = int(round(1200 * math.log2(rate / rate_f))) if rate_f > 0 else 0
        corr = max(-99, min(99, corr))
        looped = bool(flags & 0x40000000) and loop_start < size
        idx = self.sf.add_sample(f"Sample @0x{addr:x}", bytes(pcm), rate, 60, corr,
                                 (loop_start, size) if looped else None)
        self.samples[key] = idx
        return idx

    def square(self, duty):
        key = ("sq", duty)
        if key not in self.samples:
            period = 64
            high = {0: 8, 1: 16, 2: 32, 3: 48}[duty]
            pcm = b"".join(struct.pack("<h", 12000 if i < high else -12000) for i in range(period)) * 4
            rate = int(round(period * 261.626))
            name = {0: "square 12.5%", 1: "square 25%", 2: "square 50%", 3: "square 75%"}[duty]
            self.samples[key] = self.sf.add_sample(name, pcm, rate, 60, 0, (0, len(pcm) // 2))
        return self.samples[key]

    def wave(self, addr):
        key = ("wave", addr)
        if key not in self.samples:
            if not self.rom.valid(addr, 16):
                return None
            vals = []
            for b in self.rom.d[addr:addr + 16]:
                vals += [b >> 4, b & 15]
            pcm = b"".join(struct.pack("<h", int((v - 7.5) * 3000)) for v in vals) * 8
            rate = int(round(32 * 261.626))
            self.samples[key] = self.sf.add_sample(f"GB3 @0x{addr:x}", pcm, rate, 60, 0,
                                                   (0, len(pcm) // 2))
        return self.samples[key]

    def noise(self, metallic):
        key = ("noise", metallic)
        if key not in self.samples:
            bits = 7 if metallic else 15
            lfsr = (1 << bits) - 1
            out = bytearray()
            for _ in range(16384):
                bit = (lfsr ^ (lfsr >> 1)) & 1
                lfsr = (lfsr >> 1) | (bit << (bits - 1))
                out += struct.pack("<h", 10000 if lfsr & 1 else -10000)
            name = "Noise metallic 60" if metallic else "Noise normal 60"
            self.samples[key] = self.sf.add_sample(name, bytes(out), 16384, 60, 0, (0, 16384))
        return self.samples[key]

    # --- uma voz (12 bytes) -> lista de zonas (geradores, amostra)
    def voice_zones(self, addr, krange=(0, 127), drum_key=None, depth=0):
        rom = self.rom
        if not rom.valid(addr, 12) or depth > 2:
            return []
        t = rom.u8(addr)
        base_key = rom.u8(addr + 1)
        pan = rom.u8(addr + 3)
        att, dec, sus, rel = rom.d[addr + 8: addr + 12]
        kind = t & 0xC7
        zones = []
        gens = [(SF.G_KEYRANGE, krange)]
        if kind & 0x07 == 0 and not t & 0xC0:                 # DirectSound
            s = self.ds_sample(rom.ptr(addr + 4))
            if s is None:
                return []
            gens += _env_ds(att, dec, sus, rel)
            if pan & 0x80:
                gens.append((SF.G_PAN, int(((pan & 0x7F) - 64) / 64 * 500)))
            if t & 0x08 or drum_key is not None:                 # altura fixa / bateria
                gens.append((SF.G_SCALE_TUNING, 0))
                if drum_key is not None and not t & 0x08:
                    gens.append((51, base_key - 60))            # coarseTune: tecla da voz
            loop = self.sf.samples[s]["loop"]
            gens.append((SF.G_SAMPLE_MODES, 1 if loop else 0))
            zones.append((gens, s))
        elif kind & 0x07 in (1, 2):                              # quadrada
            duty = rom.u32(addr + 4) & 3
            gens += _env_cgb(att, dec, sus, rel) + [(SF.G_SAMPLE_MODES, 1)]
            zones.append((gens, self.square(duty)))
        elif kind & 0x07 == 3:                                   # onda programável
            s = self.wave(rom.ptr(addr + 4))
            if s is None:
                return []
            gens += _env_cgb(att, dec, sus, rel) + [(SF.G_SAMPLE_MODES, 1)]
            zones.append((gens, s))
        elif kind & 0x07 == 4:                                   # ruído
            gens += _env_cgb(att, dec, sus, rel) + [(SF.G_SAMPLE_MODES, 1)]
            zones.append((gens, self.noise(rom.u32(addr + 4) & 1)))
        if drum_key is not None:
            for gz, _ in zones:
                gz.append((SF.G_ROOTKEY, drum_key))
        return zones

    def preset_zones(self, addr):
        """Zonas de um programa do voicegroup (trata keysplit e kit de bateria)."""
        rom = self.rom
        t = rom.u8(addr)
        if t == 0x80:                                            # kit: cada tecla é uma voz
            sub = rom.ptr(addr + 4)
            zones = []
            if sub is None:
                return []
            for k in range(128):
                va = sub + 12 * k
                if not rom.valid(va, 12) or rom.u8(va) in (0x40, 0x80):
                    continue
                zones += self.voice_zones(va, (k, k), drum_key=k, depth=1)
            return zones
        if t == 0x40:                                            # keysplit
            sub, table = rom.ptr(addr + 4), rom.ptr(addr + 8)
            if sub is None or table is None or not rom.valid(table, 128):
                return []
            zones, k = [], 0
            while k < 128:
                idx = rom.u8(table + k)
                k2 = k
                while k2 + 1 < 128 and rom.u8(table + k2 + 1) == idx:
                    k2 += 1
                zones += self.voice_zones(sub + 12 * idx, (k, k2), depth=1)
                k = k2 + 1
            return zones
        return self.voice_zones(addr)


def build_soundfont(game, path, song_ids=None, log=None, progress=None):
    sf = SF.SoundFont(game.name)
    sf_name = sf.name
    b = _Builder(game, sf, log)
    banks = game.bank_numbers()
    used = {}                       # (voicegroup, programa)
    ids = [s for s, h, n in game.songs() if song_ids is None or s in song_ids]
    for i, sid in enumerate(ids):
        song = game.song(sid)
        if song.voicegroup is None:
            continue
        for p in song.programs:
            used.setdefault((song.voicegroup, p), True)
    total = len(used)
    done = 0
    for (vg, prog) in sorted(used, key=lambda x: (banks.get(x[0], 0), x[1])):
        addr = vg + 12 * prog
        if not game.rom.valid(addr, 12):
            continue
        zones = b.preset_zones(addr)
        done += 1
        if progress:
            progress(done, total)
        if not zones:
            continue
        t = game.rom.u8(addr)
        inst = sf.add_instrument(f"inst @0x{addr:x}", zones)
        sf.add_preset(f"Type {t} @0x{addr:x}", banks.get(vg, 0), prog, inst)
    sf.write(path)
    if log:
        log(f"  SoundFont salvo: {os.path.basename(path)} ({os.path.getsize(path) / 1e6:.1f} MB, "
            f"{len(sf.presets)} instrumentos, {len(sf.samples)} amostras) [{sf_name}]")


def export_all(game, out_dir, song_ids=None, do_midi=True, do_sf2=True, log=None, progress=None, **_):
    def L(m):
        if log:
            log(m)
    base = os.path.join(out_dir, game.name)
    os.makedirs(base, exist_ok=True)
    songs = [s for s in game.songs() if song_ids is None or s[0] in song_ids]
    banks = game.bank_numbers()
    L(f"Jogo: {game.name} | GBA (motor MP2k/Sappy) | tabela em 0x{game.table:x} | "
      f"{game.count} entradas | {len(game.songs())} músicas com faixas | {len(banks)} bancos de instrumentos")
    if do_midi:
        L("Gerando MIDIs...")
        os.makedirs(os.path.join(base, "midi"), exist_ok=True)
        for i, (sid, h, nt) in enumerate(songs):
            try:
                song = game.song(sid)
            except Exception as e:                       # noqa: BLE001
                L(f"  música {sid:03d}: erro ({e})")
                continue
            notes = sum(1 for tr in song.tracks for e in tr if e[2] == "on")
            fn = f"song{sid:03d}.mid"
            write_midi(os.path.join(base, "midi", fn), game, song, banks)
            loop = " (com loop)" if song.loop_end else ""
            L(f"  música {sid:03d}: {nt} faixas, {notes} notas, banco {banks.get(song.voicegroup, 0)}{loop} -> {fn}")
            if progress:
                progress("MIDI", i + 1, len(songs))
    if do_sf2:
        L("Gerando SoundFont...")
        build_soundfont(game, os.path.join(base, f"{game.name}.sf2"),
                        {s[0] for s in songs} if song_ids is not None else None, L,
                        (lambda a, b: progress("SF2", a, b)) if progress else None)
    for w in game.warnings:
        L("Aviso: " + w)
    L(f"Concluído. Arquivos em: {base}")
    return base
