"""Plano de distribuição: papel e prioridade de cada parte, ecos, e destino (FM/PSG/PCM).

Regras (todas registradas no relatório, nenhuma nota removida aqui):
* Kits de bateria → PCM (canais PCM do XGM que não estão reservados para o jogo).
* Instrumentos CGB (quadrada do Game Boy) → PSG, se a faixa de notas couber.
* Ecos (mesma linha de outra parte, atrasada, mais baixa) → PSG, se couber; senão FM com
  prioridade baixa.
* O resto → FM. Se a demanda das partes essenciais (todas menos os ecos) passar da
  capacidade FM com frequência, partes de menor prioridade que caibam no PSG são movidas
  para lá, uma por vez. Baixo e melodia nunca são movidos. Ecos que ficam no FM só usam
  canais livres (o driver os interrompe primeiro).

A prioridade orienta o roubo de voz no driver quando ainda faltar canal.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from mdconv.analysis.instrument_match import match_instruments
from mdconv.analysis.midi_stats import max_polyphony, note_name
from mdconv.io_sf2.sf2 import SoundFont
from mdconv.model.song import Note, Song

PSG_MIN_KEY = 45          # Lá2 ≈ 110 Hz; o PSG NTSC chega a ≈109 Hz (clock/(32·1023))
PSG_MAX_KEY = 108         # acima disso o período de 10 bits perde afinação


@dataclass
class EchoInfo:
    source: tuple[int, int]      # (faixa, canal) da linha original
    delay_ticks: int
    match: float                 # fração das notas do eco que casam com o original


@dataclass
class PartPlan:
    track: int
    channel: int
    programs: list[tuple[int, int]]
    preset: str
    kind: str                    # tonal / kit (keysplit) / CGB ...
    note_count: int
    key_min: int
    key_max: int
    key_mean: float
    key_low: float               # 15º percentil das notas: "onde a parte mora" no grave
    polyphony: int
    active: float
    volume: int                  # CC7 inicial (100 se ausente)
    role: str = "harmonia"
    priority: int = 50
    pool: str = "FM"             # FM | PSG | PCM
    overflow_psg: bool = False   # parte FM pode transbordar para o PSG nos picos
    echo: EchoInfo | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[int, int]:
        return (self.track, self.channel)

    @property
    def label(self) -> str:
        return f"C{self.channel + 1:02d} {self.preset}"


@dataclass
class Plan:
    parts: list[PartPlan]
    fm_capacity: int
    psg_capacity: int
    pcm_channels: list[int]      # canais PCM da música (1..4)
    fm_overload: float           # fração do tempo com demanda FM > capacidade
    psg_overload: float
    log: list[str] = field(default_factory=list)

    def part(self, track: int, channel: int) -> PartPlan:
        return next(p for p in self.parts if p.track == track and p.channel == channel)


def detect_echoes(song: Song, parts: dict[tuple[int, int], list[Note]],
                  volumes: dict[tuple[int, int], int] | None = None, max_delay: int = 48,
                  min_match: float = 0.5) -> dict[tuple[int, int], EchoInfo]:
    """Parte B é eco de A se a maioria das notas de B aparece em A ``d`` ticks antes (d > 0)
    e B não é mais alta que A. Em pares "mútuos" (frases repetidas fazem A parecer eco de B
    e vice-versa) fica como eco só a parte cujo casamento é mais forte e que entra depois.
    """
    volumes = volumes or {}
    keysets = {k: Counter((n.start_tick, n.key) for n in ns) for k, ns in parts.items()}
    first = {k: min(n.start_tick for n in ns) for k, ns in parts.items()}
    cand: dict[tuple[int, int], EchoInfo] = {}
    for b, nb in parts.items():
        best: EchoInfo | None = None
        for a, ca in keysets.items():
            if a == b or len(parts[a]) < len(nb) * 0.8:
                continue
            vb, va = volumes.get(b, 100), volumes.get(a, 100)
            if vb > va:
                continue                      # eco nunca é mais alto que a linha original
            for d in range(1, max_delay + 1):
                hit = sum(1 for n in nb if ca[(n.start_tick - d, n.key)])
                f = hit / len(nb)
                # cópia quase exata (≥ 90%) basta; casamento parcial exige volume claramente menor
                ok = f >= 0.9 or (f >= min_match and vb <= 0.9 * va)
                if ok and (best is None or f > best.match):
                    best = EchoInfo(a, d, f)
        if best:
            cand[b] = best
    out = {}
    for b, e in cand.items():
        back = cand.get(e.source)
        if back is not None and back.source == b:
            # par mútuo: o eco é o de casamento mais forte; empate → o que começa depois
            if (back.match, first[e.source]) > (e.match, first[b]):
                continue
        out[b] = e
    return out


def _initial_volume(song: Song, track: int, ch: int) -> int:
    for e in song.channel_events:
        if e.track == track and e.channel == ch and e.kind == "cc" and e.number == 7:
            return e.value
    return 100


def _demand(notes: list[Note], frames: int, fps: float = 60.0) -> np.ndarray:
    d = np.zeros(frames + 2, dtype=np.int32)
    for n in notes:
        a = int(round(n.start_s * fps))
        b = max(a + 1, int(round(n.end_s * fps)))
        d[a] += 1
        d[min(b, frames + 1)] -= 1
    return np.cumsum(d)[:frames]


def make_plan(song: Song, sf: SoundFont, pcm_channels: list[int] | None = None,
              use_pcm: bool = True, overload_target: float = 0.02) -> Plan:
    pcm_channels = [1, 2] if pcm_channels is None else pcm_channels
    matches = match_instruments(song, sf)
    kind_of = {}
    name_of = {}
    for m in matches:
        kind_of.setdefault((m.track, m.channel), m.kind_hint)
        name_of.setdefault((m.track, m.channel), m.preset_name or f"programa {m.program}")
    parts_notes: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for n in song.notes:
        parts_notes[(n.track, n.channel)].append(n)
    total = max(song.length_ticks, 1)

    parts: list[PartPlan] = []
    for (tr, ch), ns in sorted(parts_notes.items()):
        ks = [n.key for n in ns]
        progs = list(dict.fromkeys((n.bank, n.program) for n in ns))
        iv = sorted((n.start_tick, n.end_tick) for n in ns)
        covered, cur_s, cur_e = 0, None, None
        for s, e in iv:
            if cur_e is None or s > cur_e:
                covered += (cur_e - cur_s) if cur_e is not None else 0
                cur_s, cur_e = s, e
            else:
                cur_e = max(cur_e, e)
        covered += (cur_e - cur_s) if cur_e is not None else 0
        parts.append(PartPlan(tr, ch, progs, name_of.get((tr, ch), "?"), kind_of.get((tr, ch), "tonal"),
                              len(ns), min(ks), max(ks), float(np.mean(ks)),
                              float(np.percentile(ks, 15)), max_polyphony(ns)[0],
                              covered / total, _initial_volume(song, tr, ch)))

    tonal = [p for p in parts if not p.kind.startswith("kit")]
    echoes = detect_echoes(song, {p.key: parts_notes[p.key] for p in tonal},
                           {p.key: p.volume for p in tonal})
    log: list[str] = []

    # Papéis
    if tonal:
        nonecho = [p for p in tonal if p.key not in echoes] or tonal
        bass = min(nonecho, key=lambda p: p.key_low)
        if bass.key_low < 52:
            bass.role, bass.priority = "baixo", 90
            bass.reasons.append(f"registro mais grave (15% das notas abaixo de {note_name(int(bass.key_low))})")
        cands = [p for p in tonal if p.role == "harmonia" and p.key not in echoes and p.polyphony <= 2
                 and p.active > 0.15]
        if cands:
            mel = max(cands, key=lambda p: (p.key_mean * 0.5 + p.active * 40 + p.volume * 0.2))
            mel.role, mel.priority = "melodia", 88
            mel.reasons.append(f"linha aguda e ativa ({mel.active:.0%} do tempo)")
    for p in parts:
        if p.kind.startswith("kit"):
            p.role, p.priority = "bateria", 80
            p.pool = "PCM" if use_pcm and pcm_channels else "PSG"
            p.reasons.append("kit de bateria → PCM" if p.pool == "PCM" else "kit → ruído do PSG (PCM desligado)")
        elif p.key in echoes:
            e = echoes[p.key]
            p.echo = e
            p.role, p.priority = "eco", 30
            src = next(q for q in parts if q.key == e.source)
            p.reasons.append(f"eco de C{src.channel + 1:02d} ({e.delay_ticks} ticks depois, "
                             f"{e.match:.0%} das notas)")
        elif p.role == "harmonia":
            p.priority = int(40 + 25 * min(p.active, 1) + 10 * p.volume / 127)
        if p.kind.startswith("CGB") and PSG_MIN_KEY <= p.key_min and p.key_max <= PSG_MAX_KEY:
            p.pool = "PSG"
            p.reasons.append("instrumento quadrado do Game Boy → PSG")
        if p.role == "eco" and PSG_MIN_KEY <= p.key_min and p.key_max <= PSG_MAX_KEY:
            p.pool = "PSG"
            p.reasons.append("eco → PSG (libera FM para as linhas principais)")

    fm_cap = 5 if any(p.pool == "PCM" for p in parts) else 6
    psg_cap = 3
    frames = int(np.ceil(song.length_s * 60)) + 1
    # Partes FM podem transbordar para o PSG nota a nota (nos picos) quando a maior parte
    # das notas cabe na faixa dele; cada nota é conferida na hora (fora da faixa não vai).
    # Antes exigia a parte inteira na faixa: um único A1 num órgão impedia o transbordo e
    # as notas eram omitidas com o PSG livre.
    for p in parts:
        if p.pool == "FM":
            keys = [n.key for n in parts_notes[p.key]]
            inside = sum(PSG_MIN_KEY <= k <= PSG_MAX_KEY for k in keys)
            p.overflow_psg = bool(keys) and inside / len(keys) >= 0.6
    ess = [n for p in parts if p.pool in ("FM",) and p.role != "eco" for n in parts_notes[p.key]]
    d = _demand(ess, frames)
    psg_fixed = _demand([n for p in parts if p.pool == "PSG" for n in parts_notes[p.key]], frames)
    fm_ov = float(np.mean(d > fm_cap))
    both_ov = float(np.mean(d + psg_fixed > fm_cap + psg_cap))
    psg_ov = float(np.mean(psg_fixed > psg_cap))
    log.append(f"capacidade: {fm_cap} FM, {psg_cap} PSG tom, 1 ruído, "
               f"PCM {pcm_channels if use_pcm else '—'}")
    log.append(f"demanda das partes essenciais (sem ecos): acima de {fm_cap} FM em {fm_ov:.1%} do "
               f"tempo; acima de FM+PSG ({fm_cap + psg_cap}) em {both_ov:.1%}")
    log.append("estratégia: nota a nota — FM com o patch aprovado quando houver canal; nos picos, "
               "a nota de menor prioridade vai para o PSG (se couber na faixa); se nem assim couber, "
               "a de menor prioridade é encurtada/omitida e registrada")
    return Plan(parts, fm_cap, psg_cap, pcm_channels if use_pcm else [], fm_ov, psg_ov, log)


def plan_text(plan: Plan) -> str:
    L = ["Parte                        papel      prior. destino  notas  faixa      poli  motivo"]
    for p in sorted(plan.parts, key=lambda p: (-p.priority, p.channel)):
        L.append(f"{p.label[:28]:<28} {p.role:<10} {p.priority:>5}  {p.pool:<7} {p.note_count:>5}  "
                 f"{note_name(p.key_min)}–{note_name(p.key_max):<5} {p.polyphony:>4}  "
                 f"{'; '.join(p.reasons)}")
    return "\n".join(L + [""] + plan.log)
