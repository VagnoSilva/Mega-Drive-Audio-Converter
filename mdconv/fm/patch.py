"""Patch FM do YM2612: 4 operadores + algoritmo, feedback e LFO, com conversão para
registradores e render de notas no emulador.

Convenções:
* ``ops[0..3]`` = OP1..OP4 (numeração do manual da Yamaha; OP4 é sempre portadora).
* ``dt`` vai de -3 a +3 (o registrador usa 0..3 positivo e 5..7 negativo).
* Todos os valores ficam dentro dos limites reais do chip; ``validate()`` garante isso.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np

# Quais operadores saem no áudio em cada algoritmo (índices 0..3 = OP1..OP4)
CARRIERS = {0: (3,), 1: (3,), 2: (3,), 3: (3,), 4: (1, 3), 5: (1, 2, 3), 6: (1, 2, 3), 7: (0, 1, 2, 3)}
SLOT_OFS = (0x0, 0x8, 0x4, 0xC)          # OP1, OP2, OP3, OP4 → offset de registrador

LIMITS = {"mul": (0, 15), "dt": (-3, 3), "tl": (0, 127), "ks": (0, 3), "ar": (0, 31),
          "d1r": (0, 31), "d2r": (0, 31), "sl": (0, 15), "rr": (0, 15), "am": (0, 1)}


@dataclass
class Operator:
    mul: int = 1
    dt: int = 0
    tl: int = 127
    ks: int = 0
    ar: int = 31
    d1r: int = 0
    d2r: int = 0
    sl: int = 0
    rr: int = 15
    am: int = 0


@dataclass
class Patch:
    alg: int = 7
    fb: int = 0
    ams: int = 0
    fms: int = 0
    ops: list[Operator] = field(default_factory=lambda: [Operator() for _ in range(4)])
    name: str = ""
    # Oitava da frequência base em relação à nota (-2..+1). Com -2 e MUL 4/5/6 nas
    # portadoras, um canal toca um acorde maior (proporção 4:5:6) — o driver aplica isso.
    octave: int = 0

    # ------------------------------------------------------------------ validação
    def validate(self) -> "Patch":
        assert 0 <= self.alg <= 7 and 0 <= self.fb <= 7
        assert 0 <= self.ams <= 3 and 0 <= self.fms <= 7
        assert -2 <= self.octave <= 1
        assert len(self.ops) == 4
        for o in self.ops:
            for k, (lo, hi) in LIMITS.items():
                v = getattr(o, k)
                if not (lo <= v <= hi) or int(v) != v:
                    raise ValueError(f"{k}={v} fora de {lo}..{hi}")
        return self

    @property
    def carriers(self) -> tuple[int, ...]:
        return CARRIERS[self.alg]

    # ------------------------------------------------------------------ registradores
    def channel_writes(self, ch: int, pan: str = "C") -> list[tuple[int, int, int]]:
        """Escritas (porta, registrador, valor) que configuram o canal ``ch`` (0..5)."""
        port, c = (0, ch) if ch < 3 else (1, ch - 3)
        out = []
        for i, o in enumerate(self.ops):
            base = c + SLOT_OFS[i]
            dt = o.dt if o.dt >= 0 else 4 - o.dt
            out += [
                (port, 0x30 + base, (dt << 4) | o.mul),
                (port, 0x40 + base, o.tl),
                (port, 0x50 + base, (o.ks << 6) | o.ar),
                (port, 0x60 + base, (o.am << 7) | o.d1r),
                (port, 0x70 + base, o.d2r),
                (port, 0x80 + base, (o.sl << 4) | o.rr),
                (port, 0x90 + base, 0),
            ]
        out.append((port, 0xB0 + c, (self.fb << 3) | self.alg))
        lr = {"L": 0x80, "R": 0x40, "C": 0xC0}[pan]
        out.append((port, 0xB4 + c, lr | (self.ams << 4) | self.fms))
        return out

    # ------------------------------------------------------------------ serialização
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Patch":
        ops = [Operator(**o) for o in d["ops"]]
        return cls(alg=d["alg"], fb=d["fb"], ams=d.get("ams", 0), fms=d.get("fms", 0),
                   ops=ops, name=d.get("name", ""), octave=d.get("octave", 0)).validate()

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def copy(self) -> "Patch":
        return Patch.from_dict(self.to_dict())

    def to_vector(self) -> np.ndarray:
        v = [self.alg, self.fb]
        for o in self.ops:
            v += [o.mul, o.dt, o.tl, o.ks, o.ar, o.d1r, o.d2r, o.sl, o.rr]
        return np.asarray(v, dtype=float)


def freq_to_block_fnum(hz: float, fm_clock: float) -> tuple[int, int]:
    """Bloco e F-number para a frequência, com a maior precisão possível (fnum alto)."""
    fs = fm_clock / 144.0
    for block in range(8):
        fnum = round(hz * 2 ** 20 / (fs * 2 ** (block - 1)))
        if fnum < 2048:
            return block, max(fnum, 0)
    return 7, 2047


def block_fnum_to_freq(block: int, fnum: int, fm_clock: float) -> float:
    return fnum * (fm_clock / 144.0) * 2 ** (block - 1) / 2 ** 20


def midi_to_hz(key: float) -> float:
    return 440.0 * 2 ** ((key - 69) / 12)


def render_note(patch: Patch, hz: float, hold_s: float, sr_out: int | None = 44100,
                carrier_tl_offset: int = 0, max_release_s: float = 3.0, chip=None) -> np.ndarray:
    """Toca uma nota no canal 0 do emulador; devolve áudio mono (float32).

    ``carrier_tl_offset`` soma atenuação (passos de 0,75 dB) às portadoras — é assim que o
    driver aplicará velocity e volume. ``sr_out=None`` devolve na taxa nativa do chip.
    """
    from mdconv.chips.emulator import MegaDriveSound, dc_block, resample
    m = chip or MegaDriveSound()
    p = patch
    if carrier_tl_offset:
        p = patch.copy()
        for i in p.carriers:
            p.ops[i].tl = min(127, p.ops[i].tl + carrier_tl_offset)
    for port, reg, val in p.channel_writes(0):
        m.write_fm(port, reg, val)
    block, fnum = freq_to_block_fnum(hz * 2.0 ** p.octave, m.fm_clock)
    m.write_fm(0, 0xA4, (block << 3) | (fnum >> 8))
    m.write_fm(0, 0xA0, fnum & 0xFF)
    m.write_fm(0, 0x28, 0xF0)
    n_on = max(1, int(round(hold_s * m.rate)))
    a = m.render(n_on)[:, 0]
    m.write_fm(0, 0x28, 0x00)
    tail = []
    block_n = int(m.rate * 0.05)
    total = 0
    while total < max_release_s * m.rate:
        t = m.render(block_n)[:, 0]
        tail.append(t)
        total += block_n
        if hasattr(m, "op_state") and all(m.op_state(0, i)[0] == 4 for i in p.carriers):
            break                                                  # 4 = EG_OFF
    x = dc_block(np.concatenate([a] + tail), m.rate)
    nz = np.flatnonzero(np.abs(x) > 1e-4)
    x = x[: nz[-1] + 1] if len(nz) else x[:1]
    if sr_out is None:
        return x
    return resample(x, m.rate, sr_out)
