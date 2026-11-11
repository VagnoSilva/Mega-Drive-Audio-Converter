"""Leitor SoundFont 2 próprio (especificação SoundFont 2.04).

Lê INFO, a localização dos dados de amostra (sem carregá-los inteiros) e todo
o bloco ``pdta``: presets, instrumentos, zonas, geradores, moduladores e
cabeçalhos de amostra. A resolução de uma nota para as vozes que tocam fica
em ``resolve.py``.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


class Sf2FormatError(ValueError):
    pass


# Nomes dos geradores (índice = operador SF2).
GEN_NAMES = {
    0: "startAddrsOffset", 1: "endAddrsOffset", 2: "startloopAddrsOffset",
    3: "endloopAddrsOffset", 4: "startAddrsCoarseOffset", 5: "modLfoToPitch",
    6: "vibLfoToPitch", 7: "modEnvToPitch", 8: "initialFilterFc", 9: "initialFilterQ",
    10: "modLfoToFilterFc", 11: "modEnvToFilterFc", 12: "endAddrsCoarseOffset",
    13: "modLfoToVolume", 15: "chorusEffectsSend", 16: "reverbEffectsSend", 17: "pan",
    21: "delayModLFO", 22: "freqModLFO", 23: "delayVibLFO", 24: "freqVibLFO",
    25: "delayModEnv", 26: "attackModEnv", 27: "holdModEnv", 28: "decayModEnv",
    29: "sustainModEnv", 30: "releaseModEnv", 31: "keynumToModEnvHold",
    32: "keynumToModEnvDecay", 33: "delayVolEnv", 34: "attackVolEnv", 35: "holdVolEnv",
    36: "decayVolEnv", 37: "sustainVolEnv", 38: "releaseVolEnv", 39: "keynumToVolEnvHold",
    40: "keynumToVolEnvDecay", 41: "instrument", 43: "keyRange", 44: "velRange",
    45: "startloopAddrsCoarseOffset", 46: "keynum", 47: "velocity",
    48: "initialAttenuation", 50: "endloopAddrsCoarseOffset", 51: "coarseTune",
    52: "fineTune", 53: "sampleID", 54: "sampleModes", 56: "scaleTuning",
    57: "exclusiveClass", 58: "overridingRootKey",
}
GEN_ID = {v: k for k, v in GEN_NAMES.items()}

KEY_RANGE, VEL_RANGE, INSTRUMENT, SAMPLE_ID = 43, 44, 41, 53
RANGE_GENS = (KEY_RANGE, VEL_RANGE)
UNSIGNED_GENS = (INSTRUMENT, SAMPLE_ID, 54)  # sampleModes também é palavra sem sinal
# Geradores que só podem aparecer no nível de instrumento (spec 8.1.3).
INSTRUMENT_ONLY_GENS = {0, 1, 2, 3, 4, 12, 45, 46, 47, 50, 54, 57, 58}

# Valores padrão (spec 8.1.3); o que não está aqui vale 0.
GEN_DEFAULTS = {
    8: 13500, 21: -12000, 22: 0, 23: -12000, 24: 0, 25: -12000, 26: -12000, 27: -12000,
    28: -12000, 30: -12000, 33: -12000, 34: -12000, 35: -12000, 36: -12000, 38: -12000,
    46: -1, 47: -1, 56: 100, 58: -1,
}

SAMPLE_TYPES = {1: "mono", 2: "right", 4: "left", 8: "linked",
                0x8001: "rom mono", 0x8002: "rom right", 0x8004: "rom left", 0x8008: "rom linked"}


@dataclass(slots=True)
class Generator:
    oper: int
    amount: int                    # inteiro com sinal, sem sinal ou par (lo, hi) codificado
    range: tuple[int, int] | None = None

    @property
    def name(self) -> str:
        return GEN_NAMES.get(self.oper, f"gen{self.oper}")


@dataclass(slots=True)
class Modulator:
    src: int
    dest: int
    amount: int
    amount_src: int
    transform: int

    def describe(self) -> str:
        return (f"src=0x{self.src:04X} → {GEN_NAMES.get(self.dest, self.dest)} "
                f"amount={self.amount} amtsrc=0x{self.amount_src:04X} trans={self.transform}")


@dataclass
class Zone:
    generators: dict[int, Generator] = field(default_factory=dict)
    modulators: list[Modulator] = field(default_factory=list)
    is_global: bool = False

    def get(self, oper: int, default: int | None = None) -> int | None:
        g = self.generators.get(oper)
        return g.amount if g else default

    @property
    def key_range(self) -> tuple[int, int]:
        g = self.generators.get(KEY_RANGE)
        return g.range if g and g.range else (0, 127)

    @property
    def vel_range(self) -> tuple[int, int]:
        g = self.generators.get(VEL_RANGE)
        return g.range if g and g.range else (0, 127)


@dataclass
class SampleHeader:
    index: int
    name: str
    start: int
    end: int
    loop_start: int
    loop_end: int
    sample_rate: int
    original_pitch: int
    pitch_correction: int
    sample_link: int
    sample_type: int

    @property
    def type_name(self) -> str:
        return SAMPLE_TYPES.get(self.sample_type, f"0x{self.sample_type:04X}")

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class Instrument:
    index: int
    name: str
    global_zone: Zone | None
    zones: list[Zone]


@dataclass
class Preset:
    index: int
    name: str
    program: int
    bank: int
    library: int
    genre: int
    morphology: int
    global_zone: Zone | None
    zones: list[Zone]


@dataclass
class SoundFont:
    path: str | None
    info: dict[str, str]
    presets: list[Preset]
    instruments: list[Instrument]
    samples: list[SampleHeader]
    smpl_offset: int | None         # posição dos dados 16 bits no arquivo
    smpl_size: int                  # em bytes
    sm24_offset: int | None
    warnings: list[str] = field(default_factory=list)
    _data: bytes | None = None      # conteúdo do arquivo (para ler amostras)

    def preset_map(self) -> dict[tuple[int, int], Preset]:
        out: dict[tuple[int, int], Preset] = {}
        for p in self.presets:
            out.setdefault((p.bank, p.program), p)
        return out

    def sample_data(self, sh: SampleHeader):
        """Amostras PCM 16 bits do sample como array numpy int16 (sem cópia extra)."""
        import numpy as np
        if self._data is None or self.smpl_offset is None:
            raise Sf2FormatError("Dados de amostra indisponíveis")
        a = self.smpl_offset + 2 * sh.start
        b = self.smpl_offset + 2 * sh.end
        return np.frombuffer(self._data, dtype="<i2", count=(b - a) // 2, offset=a)


def _chunks(buf: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        cid = buf[pos:pos + 4].decode("latin-1")
        size = struct.unpack("<I", buf[pos + 4:pos + 8])[0]
        body = pos + 8
        yield cid, body, min(size, end - body)
        pos = body + size + (size & 1)


def _name(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("latin-1").rstrip()


def _records(buf: bytes, off: int, size: int, fmt: str, rec_size: int, label: str,
             warnings: list[str]) -> list[tuple]:
    if size % rec_size:
        warnings.append(f"{label}: tamanho {size} não é múltiplo de {rec_size}; registros extras ignorados")
    n = size // rec_size
    return [struct.unpack_from(fmt, buf, off + i * rec_size) for i in range(n)]


def _make_gen(oper: int, raw: int) -> Generator:
    if oper in RANGE_GENS:
        lo, hi = raw & 0xFF, (raw >> 8) & 0xFF
        return Generator(oper, raw, (lo, hi))
    if oper in UNSIGNED_GENS:
        return Generator(oper, raw)
    return Generator(oper, raw - 0x10000 if raw & 0x8000 else raw)


def _build_zones(bags, gens, mods, first: int, last: int, terminal_gen: int,
                 owner: str, warnings: list[str]) -> tuple[Zone | None, list[Zone]]:
    zones: list[Zone] = []
    for bi in range(first, last):
        g0, m0 = bags[bi]
        g1, m1 = bags[bi + 1]
        if g1 < g0 or m1 < m0:
            warnings.append(f"{owner}: índices de bag decrescentes na zona {bi - first}; zona ignorada")
            continue
        z = Zone()
        for oper, raw in gens[g0:g1]:
            if oper in z.generators:
                # Spec: duplicata — vale a última.
                warnings.append(f"{owner}: gerador {GEN_NAMES.get(oper, oper)} duplicado na zona; vale o último")
            z.generators[oper] = _make_gen(oper, raw)
        for src, dest, amt, amtsrc, trans in mods[m0:m1]:
            z.modulators.append(Modulator(src, dest, amt, amtsrc, trans))
        zones.append(z)
    gzone = None
    if zones and terminal_gen not in zones[0].generators:
        # Primeira zona sem instrumento/sample = zona global (se houver mais zonas
        # ou se ela não contém um terminal). Spec 7.3 / 7.7.
        gzone = zones.pop(0)
        gzone.is_global = True
    bad = [i for i, z in enumerate(zones) if terminal_gen not in z.generators]
    if bad:
        warnings.append(f"{owner}: {len(bad)} zona(s) sem "
                        f"{GEN_NAMES[terminal_gen]} além da global; ignoradas pela spec")
        zones = [z for z in zones if terminal_gen in z.generators]
    return gzone, zones


def parse_sf2(data: bytes, path: str | None = None) -> SoundFont:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"sfbk":
        raise Sf2FormatError("Não é um SoundFont 2 (RIFF sfbk ausente)")
    riff_size = struct.unpack("<I", data[4:8])[0]
    end = min(len(data), 8 + riff_size)
    warnings: list[str] = []
    if 8 + riff_size > len(data):
        warnings.append("Arquivo menor que o tamanho declarado no RIFF; lido até o fim")

    info: dict[str, str] = {}
    smpl_off = None
    smpl_size = 0
    sm24_off = None
    pdta: dict[str, tuple[int, int]] = {}

    for cid, body, size in _chunks(data, 12, end):
        if cid != "LIST":
            warnings.append(f"Bloco de topo {cid!r} ignorado")
            continue
        ltype = data[body:body + 4].decode("latin-1")
        for sid, sbody, ssize in _chunks(data, body + 4, body + size):
            if ltype == "INFO":
                if sid == "ifil" and ssize >= 4:
                    major, minor = struct.unpack_from("<HH", data, sbody)
                    info["ifil"] = f"{major}.{minor:02d}"
                else:
                    info[sid] = _name(data[sbody:sbody + ssize])
            elif ltype == "sdta":
                if sid == "smpl":
                    smpl_off, smpl_size = sbody, ssize
                elif sid == "sm24":
                    sm24_off = sbody
            elif ltype == "pdta":
                pdta[sid] = (sbody, ssize)

    need = ["phdr", "pbag", "pmod", "pgen", "inst", "ibag", "imod", "igen", "shdr"]
    missing = [n for n in need if n not in pdta]
    if missing:
        raise Sf2FormatError(f"pdta incompleto: faltam {', '.join(missing)}")

    phdr = _records(data, *pdta["phdr"], "<20sHHHIII", 38, "phdr", warnings)
    pbag = _records(data, *pdta["pbag"], "<HH", 4, "pbag", warnings)
    pmod = _records(data, *pdta["pmod"], "<HHhHH", 10, "pmod", warnings)
    pgen = _records(data, *pdta["pgen"], "<HH", 4, "pgen", warnings)
    inst = _records(data, *pdta["inst"], "<20sH", 22, "inst", warnings)
    ibag = _records(data, *pdta["ibag"], "<HH", 4, "ibag", warnings)
    imod = _records(data, *pdta["imod"], "<HHhHH", 10, "imod", warnings)
    igen = _records(data, *pdta["igen"], "<HH", 4, "igen", warnings)
    shdr = _records(data, *pdta["shdr"], "<20sIIIIIBbHH", 46, "shdr", warnings)

    if len(phdr) < 2 or len(inst) < 2 or len(shdr) < 1:
        raise Sf2FormatError("pdta sem registros terminais (EOP/EOI/EOS)")

    samples = [
        SampleHeader(i, _name(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9])
        for i, r in enumerate(shdr[:-1])
    ]
    total_points = smpl_size // 2
    for s in samples:
        if s.sample_type & 0x8000:
            continue  # amostra em ROM: dados não estão no arquivo
        if not (0 <= s.start <= s.end <= total_points):
            warnings.append(f"Sample '{s.name}': limites fora dos dados ({s.start}..{s.end} de {total_points})")
        if s.loop_end > s.loop_start and not (s.start <= s.loop_start and s.loop_end <= s.end):
            warnings.append(f"Sample '{s.name}': loop fora dos limites da amostra")
        if s.sample_rate == 0:
            warnings.append(f"Sample '{s.name}': taxa de amostragem 0")

    instruments: list[Instrument] = []
    for i in range(len(inst) - 1):
        name = _name(inst[i][0])
        gz, zones = _build_zones(ibag, igen, imod, inst[i][1], inst[i + 1][1], SAMPLE_ID,
                                 f"Instrumento '{name}'", warnings)
        for z in zones:
            sid = z.get(SAMPLE_ID)
            if sid is None or sid >= len(samples):
                warnings.append(f"Instrumento '{name}': sampleID {sid} inexistente")
        instruments.append(Instrument(i, name, gz, zones))

    presets: list[Preset] = []
    for i in range(len(phdr) - 1):
        r = phdr[i]
        name = _name(r[0])
        gz, zones = _build_zones(pbag, pgen, pmod, r[3], phdr[i + 1][3], INSTRUMENT,
                                 f"Preset '{name}'", warnings)
        for z in zones:
            iid = z.get(INSTRUMENT)
            if iid is None or iid >= len(instruments):
                warnings.append(f"Preset '{name}': instrumento {iid} inexistente")
            for oper in z.generators:
                if oper in INSTRUMENT_ONLY_GENS:
                    warnings.append(f"Preset '{name}': gerador {GEN_NAMES.get(oper, oper)} "
                                    "proibido no nível de preset; ignorado na resolução")
        presets.append(Preset(i, name, r[1], r[2], r[4], r[5], r[6], gz, zones))

    seen: dict[tuple[int, int], str] = {}
    for p in presets:
        k = (p.bank, p.program)
        if k in seen:
            warnings.append(f"Preset duplicado banco {p.bank} programa {p.program}: "
                            f"'{seen[k]}' e '{p.name}' (vale o primeiro)")
        else:
            seen[k] = p.name

    return SoundFont(path=path, info=info, presets=presets, instruments=instruments,
                     samples=samples, smpl_offset=smpl_off, smpl_size=smpl_size,
                     sm24_offset=sm24_off, warnings=warnings, _data=data)


def read_sf2(path: str | Path) -> SoundFont:
    p = Path(path)
    return parse_sf2(p.read_bytes(), path=str(p))
