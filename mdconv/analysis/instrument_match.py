"""Correspondência entre os instrumentos usados no MIDI e os presets do SF2.

Para cada parte (faixa × canal) e cada (banco, programa) usado, resolve as
notas/velocities realmente tocadas e relata: preset encontrado (ou motivo de
não encontrar e candidatos), samples envolvidos, camadas, e notas que não
caem em nenhuma zona (essas notas ficariam mudas no SF2 original — não são
removidas, apenas sinalizadas).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from mdconv.io_sf2.resolve import find_preset, resolve_voices
from mdconv.io_sf2.sf2 import SoundFont
from mdconv.model.song import Song


@dataclass
class InstrumentMatch:
    track: int
    channel: int
    bank: int
    program: int
    preset_name: str | None
    exact: bool
    reason: str
    candidates: list[str]
    note_count: int
    keys_used: list[int]
    samples: dict[str, dict]                  # nome → resumo da voz (primeira ocorrência)
    keys_without_voice: list[int]
    max_layers: int
    kind_hint: str                            # 'kit (keysplit)' | 'tonal' | 'sem preset'
    voices_by_key: dict[int, list[str]] = field(default_factory=dict)


def classify_kind(voices, gm_drum: bool) -> str:
    """Tipo do instrumento a partir das vozes realmente tocadas.

    * kit: as zonas não acompanham a altura da nota (scaleTuning = 0), que é como
      o GBA Mus Ripper exporta os keysplits de bateria do m4a;
    * CGB: amostras sintetizadas pelo ripper para os canais estilo Game Boy
      ("square 12.5%", "square 25%", ..., "GB3 …" = canal de onda programável);
    * tonal: o resto.
    """
    if not voices:
        return "sem voz"
    if gm_drum:
        return "kit (keysplit)"
    fixed = sum(1 for v in voices if v.g("scaleTuning") == 0)
    if fixed > len(voices) / 2:
        return "kit (keysplit)"
    names = [v.sample.name.lower() for v in voices]
    if all(n.startswith("square") for n in names):
        duty = sorted({n.split()[1].rstrip("abcdefgh").rstrip() for n in names if len(n.split()) > 1})
        return "CGB quadrada " + "/".join(duty)
    if all(n.startswith("gb3") for n in names):
        return "CGB onda programável"
    if all(n.startswith("noise") for n in names):
        return "CGB ruído"
    return "tonal"


def match_instruments(song: Song, sf: SoundFont, gm_drum_channel: bool | None = None) -> list[InstrumentMatch]:
    """``gm_drum_channel``: tratar o canal 10 como bateria GM (banco 128).

    ``None`` = automático: desligado para MIDI do GBA Mus Ripper / SequenceRipper,
    porque no m4a qualquer canal pode ter kit e o canal 10 não tem papel especial.
    """
    if gm_drum_channel is None:
        gm_drum_channel = not any("SequenceRipper" in t.text or "Mus Rip" in t.text for t in song.texts)
    used: dict[tuple[int, int, int, int], list] = defaultdict(list)
    for n in song.notes:
        used[(n.track, n.channel, n.bank, n.program)].append(n)

    out: list[InstrumentMatch] = []
    for (tr, ch, bank, prog), notes in sorted(used.items()):
        look = find_preset(sf, bank, prog, drum_channel=(gm_drum_channel and ch == 9))
        keys = sorted({n.key for n in notes})
        if look.preset is None:
            out.append(InstrumentMatch(
                tr, ch, bank, prog, None, False, look.reason,
                [f"{p.bank}:{p.program} {p.name}" for p in look.candidates],
                len(notes), keys, {}, keys, 0, "sem preset"))
            continue
        samples: dict[str, dict] = {}
        missing: set[int] = set()
        max_layers = 0
        by_key: dict[int, list[str]] = {}
        voices_seen: list = []
        for k, v in sorted({(n.key, n.velocity) for n in notes}):
            voices = resolve_voices(sf, look.preset, k, v)
            if not voices:
                missing.add(k)
                continue
            max_layers = max(max_layers, len(voices))
            voices_seen.extend(voices)
            by_key.setdefault(k, sorted({vc.sample.name for vc in voices}))
            for vc in voices:
                samples.setdefault(vc.sample.name, vc.describe())
        kind = classify_kind(voices_seen, gm_drum_channel and ch == 9)
        out.append(InstrumentMatch(
            tr, ch, bank, prog, look.preset.name, look.exact, look.reason,
            [f"{p.bank}:{p.program} {p.name}" for p in look.candidates],
            len(notes), keys, samples, sorted(missing), max_layers, kind, by_key))
    return out
