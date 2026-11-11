"""Resolução (banco, programa, nota, velocity) → vozes do SoundFont.

Segue a spec SF2 2.04, seção 9.4:
* Zonas de preset e de instrumento são selecionadas pela faixa de nota e de
  velocity; a zona global fornece valores padrão para as zonas locais
  (inclusive faixas).
* Geradores de instrumento são absolutos (local > global > padrão da spec).
* Geradores de preset são somados aos do instrumento, exceto faixas,
  índices e geradores exclusivos de instrumento.

O programa NÃO assume que Program Change = índice de preset: a busca é por
(banco, programa) e, sem correspondência exata, devolve candidatos com o
motivo, para o usuário decidir.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from mdconv.io_sf2.sf2 import (
    GEN_DEFAULTS, GEN_NAMES, INSTRUMENT, INSTRUMENT_ONLY_GENS, KEY_RANGE, SAMPLE_ID,
    VEL_RANGE, Preset, SampleHeader, SoundFont, Zone,
)

NON_ADDITIVE = {KEY_RANGE, VEL_RANGE, INSTRUMENT, SAMPLE_ID} | INSTRUMENT_ONLY_GENS

LOOP_MODES = {0: "sem loop", 1: "loop contínuo", 2: "sem loop (reservado)", 3: "loop até soltar"}


def timecents_to_s(tc: int) -> float:
    return 0.0 if tc <= -32768 else 2.0 ** (tc / 1200.0)


def _in(rng: tuple[int, int], v: int) -> bool:
    return rng[0] <= v <= rng[1]


def _zone_range(z: Zone, g: Zone | None, oper: int) -> tuple[int, int]:
    gen = z.generators.get(oper) or (g.generators.get(oper) if g else None)
    return gen.range if gen is not None and gen.range else (0, 127)


@dataclass
class Voice:
    preset: Preset
    instrument_name: str
    sample: SampleHeader
    gens: dict[int, int]              # valores finais por operador
    key: int
    velocity: int
    modulator_count: int

    def g(self, name_or_id) -> int:
        oper = name_or_id if isinstance(name_or_id, int) else _GEN_ID[name_or_id]
        return self.gens.get(oper, GEN_DEFAULTS.get(oper, 0))

    @property
    def root_key(self) -> int:
        ork = self.g("overridingRootKey")
        return ork if ork >= 0 else self.sample.original_pitch

    @property
    def tuning_cents(self) -> float:
        """Afinação total (cents) aplicada à nota tocada, relativa ao root key."""
        return (self.g("coarseTune") * 100 + self.g("fineTune") + self.sample.pitch_correction
                + (self.key - self.root_key) * self.g("scaleTuning"))

    @property
    def playback_rate(self) -> float:
        """Taxa de leitura (Hz) da amostra para soar na altura certa."""
        return self.sample.sample_rate * 2.0 ** (self.tuning_cents / 1200.0)

    @property
    def loop_mode(self) -> int:
        return self.g("sampleModes") & 3

    @property
    def loop_points(self) -> tuple[int, int]:
        ls = self.sample.loop_start + self.g("startloopAddrsOffset") + 32768 * self.g("startloopAddrsCoarseOffset")
        le = self.sample.loop_end + self.g("endloopAddrsOffset") + 32768 * self.g("endloopAddrsCoarseOffset")
        return ls, le

    @property
    def sample_bounds(self) -> tuple[int, int]:
        st = self.sample.start + self.g("startAddrsOffset") + 32768 * self.g("startAddrsCoarseOffset")
        en = self.sample.end + self.g("endAddrsOffset") + 32768 * self.g("endAddrsCoarseOffset")
        return st, en

    def vol_envelope(self) -> dict[str, float]:
        """Envelope de volume em segundos / dB, com o efeito de keynumTo* aplicado."""
        k = 60 - self.key
        return {
            "delay_s": timecents_to_s(self.g("delayVolEnv")),
            "attack_s": timecents_to_s(self.g("attackVolEnv")),
            "hold_s": timecents_to_s(self.g("holdVolEnv") + k * self.g("keynumToVolEnvHold")),
            "decay_s": timecents_to_s(self.g("decayVolEnv") + k * self.g("keynumToVolEnvDecay")),
            "sustain_db": -max(0, min(1440, self.g("sustainVolEnv"))) / 10.0,
            "release_s": timecents_to_s(self.g("releaseVolEnv")),
        }

    def describe(self) -> dict:
        ls, le = self.loop_points
        return {
            "preset": f"{self.preset.bank}:{self.preset.program} {self.preset.name}",
            "instrument": self.instrument_name,
            "sample": self.sample.name,
            "sample_rate": self.sample.sample_rate,
            "root_key": self.root_key,
            "tuning_cents": round(self.tuning_cents, 2),
            "loop_mode": LOOP_MODES[self.loop_mode],
            "loop": [ls, le],
            "attenuation_db": self.g("initialAttenuation") / 10.0,
            "pan": self.g("pan") / 10.0,
            "vol_env": {k: round(v, 4) for k, v in self.vol_envelope().items()},
            "vib_lfo": {"to_pitch_cents": self.g("vibLfoToPitch"),
                        "freq_hz": round(8.176 * 2 ** (self.g("freqVibLFO") / 1200), 3),
                        "delay_s": round(timecents_to_s(self.g("delayVibLFO")), 4)},
            "filter_fc": self.g("initialFilterFc"),
            "exclusive_class": self.g("exclusiveClass"),
            "modulators": self.modulator_count,
        }


_GEN_ID = {v: k for k, v in GEN_NAMES.items()}


@dataclass
class PresetLookup:
    preset: Preset | None
    exact: bool
    reason: str
    candidates: list[Preset] = field(default_factory=list)


def find_preset(sf: SoundFont, bank: int, program: int, drum_channel: bool = False) -> PresetLookup:
    pm = sf.preset_map()
    if drum_channel and (128, program) in pm:
        return PresetLookup(pm[(128, program)], True, "canal de percussão → banco 128")
    if (bank, program) in pm:
        return PresetLookup(pm[(bank, program)], True, "banco e programa idênticos")
    # Banco MIDI pode ter vindo como MSB*128+LSB; tenta só o MSB e só o LSB.
    msb, lsb = divmod(bank, 128)
    for b, why in ((msb, "banco = MSB"), (lsb, "banco = LSB")):
        if b != bank and (b, program) in pm:
            return PresetLookup(pm[(b, program)], False, f"aproximado: {why}")
    same_prog = [p for p in sf.presets if p.program == program]
    if same_prog:
        return PresetLookup(None, False,
                            f"sem preset {bank}:{program}; mesmo programa em outros bancos",
                            same_prog)
    return PresetLookup(None, False, f"sem preset {bank}:{program} no SoundFont", [])


def resolve_voices(sf: SoundFont, preset: Preset, key: int, velocity: int) -> list[Voice]:
    voices: list[Voice] = []
    pg = preset.global_zone
    for pz in preset.zones:
        if not (_in(_zone_range(pz, pg, KEY_RANGE), key) and _in(_zone_range(pz, pg, VEL_RANGE), velocity)):
            continue
        iid = pz.get(INSTRUMENT)
        if iid is None or iid >= len(sf.instruments):
            continue
        inst = sf.instruments[iid]
        ig = inst.global_zone
        for iz in inst.zones:
            if not (_in(_zone_range(iz, ig, KEY_RANGE), key) and _in(_zone_range(iz, ig, VEL_RANGE), velocity)):
                continue
            sid = iz.get(SAMPLE_ID)
            if sid is None or sid >= len(sf.samples):
                continue
            gens: dict[int, int] = {}
            for src in (ig, iz):
                if src:
                    for oper, gen in src.generators.items():
                        if oper not in (KEY_RANGE, VEL_RANGE):
                            gens[oper] = gen.amount
            # Preset: zona local substitui a global, e o resultado é somado.
            padd: dict[int, int] = {}
            for src in (pg, pz):
                if src:
                    for oper, gen in src.generators.items():
                        if oper not in NON_ADDITIVE:
                            padd[oper] = gen.amount
            for oper, amt in padd.items():
                gens[oper] = gens.get(oper, GEN_DEFAULTS.get(oper, 0)) + amt
            nmods = sum(len(z.modulators) for z in (pg, pz, ig, iz) if z)
            voices.append(Voice(preset, inst.name, sf.samples[sid], gens, key, velocity, nmods))
    return voices
