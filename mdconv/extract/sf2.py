"""Escritor mínimo de SoundFont 2.01 (.sf2)."""
import math
import struct

# Geradores SF2
G_MODLFO_TO_PITCH = 5
G_VIBLFO_TO_PITCH = 6
G_MODLFO_TO_VOL = 13
G_PAN = 17
G_DELAY_MODLFO = 21
G_FREQ_MODLFO = 22
G_DELAY_VIBLFO = 23
G_FREQ_VIBLFO = 24
G_ATTACK_VOL = 34
G_HOLD_VOL = 35
G_DECAY_VOL = 36
G_SUSTAIN_VOL = 37
G_RELEASE_VOL = 38
G_KEY_TO_HOLD = 39
G_INSTRUMENT = 41
G_FINE_TUNE = 52
G_KEYRANGE = 43
G_INIT_ATTEN = 48
G_SCALE_TUNING = 56
G_SAMPLE_ID = 53
G_SAMPLE_MODES = 54
G_ROOTKEY = 58


def timecents(seconds):
    if seconds <= 0.001:
        return -12000
    return max(-12000, min(8000, int(round(1200 * math.log2(seconds)))))


def abs_cents(hz):
    hz = max(0.001, hz)
    return max(-16000, min(4500, int(round(1200 * math.log2(hz / 8.176)))))


def _name(s, n=20):
    b = s.encode("latin-1", "replace")[:n - 1]
    return b + b"\0" * (n - len(b))


def _chunk(cid, data):
    if len(data) % 2:
        data += b"\0"
    return cid + struct.pack("<I", len(data)) + data


class SoundFont:
    def __init__(self, name="CPS1"):
        self.name = name
        self.samples = []      # dict(name, pcm(bytes int16 LE), rate, key, corr, loop)
        self.instruments = []  # (name, global_gens, zones[(gens, sample_idx)])
        self.presets = []      # (name, bank, program, inst_idx)

    def add_sample(self, name, pcm_bytes, rate, root_key=60, pitch_corr=0, loop=None):
        self.samples.append(dict(name=name, pcm=pcm_bytes, rate=int(rate), key=root_key,
                                 corr=pitch_corr, loop=loop))
        return len(self.samples) - 1

    def add_instrument(self, name, zones, global_gens=None):
        """zones: lista de (gens: lista de (id, valor), sample_idx). keyRange em gens como (lo, hi)."""
        self.instruments.append((name, global_gens or [], zones))
        return len(self.instruments) - 1

    def add_preset(self, name, bank, program, inst):
        self.presets.append((name, bank, program, inst))

    @staticmethod
    def _gen(gid, val):
        if gid == G_KEYRANGE:
            lo, hi = val
            return struct.pack("<HBB", gid, lo, hi)
        if val < 0:
            return struct.pack("<Hh", gid, int(val))
        return struct.pack("<HH", gid, int(val) & 0xFFFF)

    @staticmethod
    def _order(gens):
        kr = [g for g in gens if g[0] == G_KEYRANGE]
        other = [g for g in gens if g[0] not in (G_KEYRANGE, G_SAMPLE_ID, G_INSTRUMENT)]
        return kr + other

    def write(self, path):
        # --- sdta
        smpl = bytearray()
        shdr = bytearray()
        for s in self.samples:
            start = len(smpl) // 2
            smpl += s["pcm"]
            n = len(s["pcm"]) // 2
            smpl += b"\0" * 92          # 46 amostras de silêncio (exigido pela especificação)
            end = start + n
            if s["loop"]:
                ls, le = s["loop"]
                ls, le = start + ls, start + le
            else:
                ls, le = start, max(start, end - 1)
            shdr += _name(s["name"]) + struct.pack("<IIIIIBbHH", start, end, ls, le, s["rate"],
                                                   s["key"] & 0x7F, s["corr"], 0, 1)
        shdr += _name("EOS") + struct.pack("<IIIIIBbHH", 0, 0, 0, 0, 0, 0, 0, 0, 0)

        # --- instrumentos
        inst, ibag, igen = bytearray(), bytearray(), bytearray()
        bag_i = gen_i = 0
        for name, gglobal, zones in self.instruments:
            inst += _name(name) + struct.pack("<H", bag_i)
            if gglobal:
                ibag += struct.pack("<HH", gen_i, 0)
                bag_i += 1
                for g in self._order(gglobal):
                    igen += self._gen(*g)
                    gen_i += 1
            for gens, sidx in zones:
                ibag += struct.pack("<HH", gen_i, 0)
                bag_i += 1
                for g in self._order(gens):
                    igen += self._gen(*g)
                    gen_i += 1
                igen += self._gen(G_SAMPLE_ID, sidx)
                gen_i += 1
        inst += _name("EOI") + struct.pack("<H", bag_i)
        ibag += struct.pack("<HH", gen_i, 0)
        igen += struct.pack("<HH", 0, 0)
        imod = b"\0" * 10

        # --- presets
        phdr, pbag, pgen = bytearray(), bytearray(), bytearray()
        bag_i = gen_i = 0
        for name, bank, prog, idx in sorted(self.presets, key=lambda p: (p[1], p[2])):
            phdr += _name(name) + struct.pack("<HHHIII", prog, bank, bag_i, 0, 0, 0)
            pbag += struct.pack("<HH", gen_i, 0)
            bag_i += 1
            pgen += self._gen(G_INSTRUMENT, idx)
            gen_i += 1
        phdr += _name("EOP") + struct.pack("<HHHIII", 0, 0, bag_i, 0, 0, 0)
        pbag += struct.pack("<HH", gen_i, 0)
        pgen += struct.pack("<HH", 0, 0)
        pmod = b"\0" * 10

        info = (_chunk(b"ifil", struct.pack("<HH", 2, 1)) + _chunk(b"isng", b"EMU8000\0") +
                _chunk(b"INAM", self.name.encode("latin-1", "replace") + b"\0") +
                _chunk(b"ISFT", b"CPS1 Extractor\0"))
        sdta = _chunk(b"smpl", bytes(smpl))
        pdta = b"".join(_chunk(c, bytes(d)) for c, d in (
            (b"phdr", phdr), (b"pbag", pbag), (b"pmod", pmod), (b"pgen", pgen),
            (b"inst", inst), (b"ibag", ibag), (b"imod", imod), (b"igen", igen), (b"shdr", shdr)))
        body = (b"sfbk" + _chunk(b"LIST", b"INFO" + info) + _chunk(b"LIST", b"sdta" + sdta) +
                _chunk(b"LIST", b"pdta" + pdta))
        with open(path, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
