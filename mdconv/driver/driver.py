"""Driver virtual: transforma a música (notas + controles do m4a) num fluxo de escritas em
registradores do YM2612/SN76489 e comandos de PCM, quadro a quadro (60 Hz NTSC).

O mesmo fluxo é usado na prévia e (Fase 6) na exportação VGM/XGM: o que se ouve é o que
vai para o arquivo.

Alocação nota a nota:
1. FM: um canal livre; de preferência o último usado pela mesma parte (evita recarregar
   patch). Se não houver, uma nota FM de prioridade menor é deslocada: se couber no PSG,
   continua lá; senão é encurtada.
2. PSG (partes que podem transbordar, ou destinadas ao PSG): um canal livre; senão desloca
   a nota PSG de menor prioridade.
3. Sem lugar: a nota é omitida. Tudo isso entra no relatório de alterações.

Modo de canais fixos (``fm_alloc="fixo"``), como um músico faria no tracker: as partes FM
mais importantes ganham canal(is) próprio(s) — a quantidade segue a polifonia da parte — e
as outras dividem o que sobra. Uma parte toca sempre no seu canal (o patch não é recarregado
e o release de uma nota termina sob a seguinte); se precisar de mais vozes, usa um canal
livre de outra parte (o mais antigo em silêncio, para não cortar caudas), e só então rouba.
Em qualquer modo, entre os canais livres vale o que ficou em silêncio há mais tempo.

Parâmetros musicais:
* Volume: lei linear do m4a (amplitude = velocity/127 × volume/127), aplicada como
  atenuação no TL das portadoras (0,75 dB por passo) e no volume do PSG (2 dB por passo).
* Pan: CC10 → esquerda (< 40), direita (> 88) ou centro. O PSG é mono.
* Pitch bend com o bend range do canal (RPN 0, padrão 2); vibrato do m4a (CC1 + NRPN 1:8)
  por software, quadro a quadro — escala provisória, como no sampler.
* PSG: envelope de volume por software, a partir do envelope do instrumento no SF2.
* PCM: uma amostra por peça do kit (ver ``mdconv.pcm.kit``); sem controle de volume no
  XGM, então a velocity das notas de bateria não muda o volume (registrado).
* Ruído do PSG (opcional): peças metálicas do kit (chimbal, prato…) tocam no canal de ruído,
  com o envelope de volume da própria amostra, quadro a quadro, e a velocity da nota. Um golpe
  novo corta o anterior (como o chimbal fechado corta o aberto); no mesmo quadro, vence a
  peça mais forte.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from mdconv.arrange.plan import PSG_MAX_KEY, PSG_MIN_KEY, Plan
from mdconv.chips.emulator import NTSC_FM_CLOCK, NTSC_PSG_CLOCK
from mdconv.fm.patch import Patch, freq_to_block_fnum, midi_to_hz
from mdconv.model.song import Note, Song
from mdconv.reference.sampler import _channel_curves, _default_curves

FPS = 60.0
TL_STEP_DB = 0.75
PSG_STEP_DB = 2.0


@dataclass
class Stream:
    """Fluxo de saída: escritas por quadro + dados de PCM."""
    frames: list[list[tuple]]            # ('fm', porta, reg, valor) | ('psg', byte) | ('pcm', canal, id, prio)
    fps: float
    loop_frame: int | None
    pcm_samples: dict[int, np.ndarray]   # id → int8 a ``pcm_rate``
    pcm_rate: float
    changes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class _Voice:
    kind: str                  # 'fm' | 'psg'
    idx: int                   # canal FM 0..5 ou PSG 0..2
    note: Note | None = None
    part: tuple | None = None
    prio: int = 0
    end_frame: int = 0
    start_frame: int = 0
    patch_id: object = None    # patch carregado (FM)
    reload: bool = False       # recarregar o patch na próxima nota (ponto de loop)
    last_tl: tuple | None = None
    last_freq: tuple | None = None
    last_pan: str | None = None
    last_vol: int | None = None
    env: dict | None = None    # envelope (PSG)
    released_at: int | None = None
    off_frame: int = -10 ** 9  # quadro do último key-off (a cauda do release soa depois dele)


def _amp_db(vel: int, vol: float, expr: float = 127.0) -> float:
    amp = (vel / 127.0) * (vol / 127.0) * (expr / 127.0)
    return -20 * np.log10(max(amp, 1e-4))


def _pan_of(cc10: float) -> str:
    return "L" if cc10 < 40 else "R" if cc10 > 88 else "C"


def _psg_period(hz: float) -> int:
    return int(min(1023, max(1, round(NTSC_PSG_CLOCK / (32.0 * hz)))))


# Registradores que podem ser omitidos quando o valor já está no chip: os dos operadores
# ($30–$9F) e algoritmo/pan ($B0–$B6). Ficam de fora: $28 (key), $2A/$2B (DAC), $22/$27
# (LFO, timer/CH3) e a frequência ($A0–$AF) — no YM2612 a parte alta da frequência fica num
# registrador de trava compartilhado, então a escrita de $A4–$A6 tem de vir sempre antes da $A0.
def _cacheable(reg: int) -> bool:
    return 0x30 <= reg <= 0x9F or 0xB0 <= reg <= 0xB6


def _drop_redundant_fm(frames: list[list[tuple]], loop_frame: int | None) -> int:
    """Tira escritas FM que repetem o valor que o registrador já tem (como o Furnace faz).
    No ponto de loop a memória é zerada: ao voltar do fim, o chip está no estado do fim da
    música, não no do começo do loop. Devolve quantas escritas saíram."""
    state: dict[tuple[int, int], int] = {}
    dropped = 0
    for f, ws in enumerate(frames):
        if f == loop_frame:
            state.clear()
        out = []
        for w in ws:
            if w[0] == "fm" and _cacheable(w[2]):
                k = (w[1], w[2])
                if state.get(k) == w[3]:
                    dropped += 1
                    continue
                state[k] = w[3]
            out.append(w)
        ws[:] = out
    return dropped


class Driver:
    def __init__(self, song: Song, plan: Plan, patches: dict[tuple[int, int], Patch],
                 psg_envelopes: dict[tuple[int, int], dict] | None = None,
                 kit=None, fm_clock: int = NTSC_FM_CLOCK, vibrato: bool = True,
                 fm_att_db: float = 0.0, psg_att_db: float = 0.0, loop="markers",
                 fm_alloc: str = "dinamico"):
        """``loop``: um :class:`~mdconv.arrange.loop.LoopPoint`, ``None`` (sem loop) ou
        ``"markers"`` (lê loopStart/loopEnd do MIDI — compatibilidade)."""
        if loop == "markers":
            from mdconv.arrange.loop import resolve_loop
            loop = resolve_loop(song, "markers")
        self.loop = loop
        if fm_alloc not in ("dinamico", "fixo"):
            raise ValueError(f"alocação FM desconhecida: {fm_alloc}")
        self.fm_alloc = fm_alloc
        self.homes: dict[tuple, list[int]] = {}      # parte → canais FM próprios (modo fixo)
        self.home_of: dict[int, tuple] = {}
        # Linha do tempo por canal do chip (para exportar a mesma distribuição para
        # trackers): ('on', quadro, tipo, canal, nota) · ('off', quadro, tipo, canal) ·
        # ('release', quadro, 'psg', canal) · ('pcm', quadro, canal_pcm, nota, id_amostra)
        self.timeline: list[tuple] = []
        self._frame = 0
        self.song = song
        self.plan = plan
        self.patches = patches
        self.psg_env = psg_envelopes or {}
        self.kit = kit
        self.fm_clock = fm_clock
        self.vibrato = vibrato
        self.fm_att_db = fm_att_db          # atenuação global (mixagem) do FM, em dB
        self.psg_att_db = psg_att_db
        self.curves = _channel_curves(song)
        self.changes: list[str] = []
        self.counts = {"fm": 0, "psg_planned": 0, "psg_overflow": 0, "migrated": 0, "shortened": 0,
                       "omitted": 0, "pcm": 0, "pcm_stolen": 0, "pcm_cut_tail": 0, "pcm_missing": 0, "patch_loads": 0,
                       "noise": 0, "noise_cut": 0, "noise_lost": 0, "noise_clamped": 0, "fm_borrowed": 0,
                       "fm_to_psg": 0}
        self._nz: dict | None = None      # voz de ruído: peça, início, nota, prioridade, delta
        self._nz_vol: int | None = None

    # ---------------------------------------------------------------- utilidades
    def _curve(self, n: Note):
        return self.curves.get((n.track, n.channel)) or _default_curves()

    def _cents(self, n: Note, frame: int) -> float:
        c = self._curve(n)
        t = frame / FPS
        cents = c["bend"].at(t) / 8192.0 * c["bend_range"].at(t) * 100
        if self.vibrato:
            mod = c["mod"].at(t)
            if mod > 0:
                k = frame - int(round(n.start_s * FPS))
                phase = k * c["lfos"].at(t) / 256.0
                tri = 1 - 4 * abs((phase % 1.0) - 0.5)
                cents += tri * mod * 100.0 / 256.0
        return cents

    def _level_db(self, n: Note, frame: int) -> float:
        c = self._curve(n)
        t = frame / FPS
        return _amp_db(n.velocity, c["vol"].at(t), c["expr"].at(t))

    def _when(self, frame: int) -> str:
        s = frame / FPS
        return f"{int(s // 60)}:{s % 60:05.2f}"

    # ---------------------------------------------------------------- escrita de voz FM
    def _fm_start(self, w: list, v: _Voice, n: Note, frame: int) -> None:
        part = (n.track, n.channel)
        patch = self.patches.get((n.bank, n.program))
        ch = v.idx
        port, c = (0, ch) if ch < 3 else (1, ch - 3)
        keyreg = c + (4 if ch >= 3 else 0)
        # key-off antes (se o canal estava ocupado), para reiniciar o envelope
        w.append(("fm", 0, 0x28, keyreg))
        pan = _pan_of(self._curve(n)["pan"].at(frame / FPS))
        if v.patch_id is not patch or v.reload:
            v.reload = False
            for wr in patch.channel_writes(ch, pan):
                w.append(("fm",) + wr)
            v.patch_id = patch
            v.last_tl = None
            self.counts["patch_loads"] += 1
        elif pan != v.last_pan:
            w.append(("fm", port, 0xB4 + c, {"L": 0x80, "R": 0x40, "C": 0xC0}[pan] | (patch.ams << 4) | patch.fms))
        v.last_pan = pan
        v.note, v.part, v.start_frame = n, part, frame
        self.timeline.append(("on", frame, "fm", ch, n))
        v.last_freq = None
        self._fm_update(w, v, frame)
        w.append(("fm", 0, 0x28, 0xF0 | keyreg))

    def _fm_update(self, w: list, v: _Voice, frame: int) -> None:
        n, patch, ch = v.note, v.patch_id, v.idx
        port, c = (0, ch) if ch < 3 else (1, ch - 3)
        att = int(round((self._level_db(n, frame) + self.fm_att_db) / TL_STEP_DB))
        tls = tuple(min(127, max(0, patch.ops[i].tl + att)) for i in patch.carriers)
        if tls != v.last_tl:
            from mdconv.fm.patch import SLOT_OFS
            for i, tl in zip(patch.carriers, tls):
                w.append(("fm", port, 0x40 + c + SLOT_OFS[i], tl))
            v.last_tl = tls
        hz = midi_to_hz(n.key) * 2 ** (self._cents(n, frame) / 1200) * 2.0 ** patch.octave
        block, fnum = freq_to_block_fnum(hz, self.fm_clock)
        if (block, fnum) != v.last_freq:
            w.append(("fm", port, 0xA4 + c, (block << 3) | (fnum >> 8)))
            w.append(("fm", port, 0xA0 + c, fnum & 0xFF))
            v.last_freq = (block, fnum)

    def _fm_stop(self, w: list, v: _Voice) -> None:
        ch = v.idx
        keyreg = (ch if ch < 3 else ch - 3) + (4 if ch >= 3 else 0)
        w.append(("fm", 0, 0x28, keyreg))
        if v.note is not None:
            self.timeline.append(("off", self._frame, "fm", ch))
        v.note = None
        v.part = None
        v.off_frame = self._frame

    # ---------------------------------------------------------------- escrita de voz PSG
    def _psg_env_db(self, v: _Voice, frame: int) -> float:
        e = v.env or {}
        t = (frame - v.start_frame) / FPS
        att = e.get("attack_s", 0.0)
        db = 0.0
        if t < att and att > 1.0 / FPS:
            db = 20 * np.log10(max(t / att, 1e-3))
        else:
            dec = e.get("decay_s", 0.0)
            sus = e.get("sustain_db", 0.0)
            td = t - att - e.get("hold_s", 0.0)
            if td > 0 and dec > 0:
                db = max(-100.0 * td / dec, sus)
        if v.released_at is not None:
            rel = max(e.get("release_s", 0.0), 1e-3)
            db -= 100.0 * ((frame - v.released_at) / FPS) / rel
        return db

    def _psg_start(self, w: list, v: _Voice, n: Note, frame: int) -> None:
        v.note, v.part, v.start_frame = n, (n.track, n.channel), frame
        self.timeline.append(("on", frame, "psg", v.idx, n))
        v.env = self.psg_env.get((n.bank, n.program), {})
        v.released_at = None
        v.last_freq = None
        v.last_vol = None
        self._psg_update(w, v, frame)

    def _psg_update(self, w: list, v: _Voice, frame: int) -> None:
        n = v.note
        hz = midi_to_hz(n.key) * 2 ** (self._cents(n, frame) / 1200)
        per = _psg_period(hz)
        if per != v.last_freq:
            w.append(("psg", 0x80 | (v.idx << 5) | (per & 0x0F)))
            w.append(("psg", (per >> 4) & 0x3F))
            v.last_freq = per
        db = self._level_db(n, frame) + self.psg_att_db - self._psg_env_db(v, frame)
        vol = int(min(15, max(0, round(db / PSG_STEP_DB))))
        if vol != v.last_vol:
            w.append(("psg", 0x90 | (v.idx << 5) | vol))
            v.last_vol = vol

    def _psg_stop(self, w: list, v: _Voice) -> None:
        w.append(("psg", 0x9F | (v.idx << 5)))
        if v.note is not None:
            self.timeline.append(("off", self._frame, "psg", v.idx))
        v.note = None
        v.part = None
        v.released_at = None

    # ---------------------------------------------------------------- execução
    def run(self) -> Stream:
        song, plan = self.song, self.plan
        pplan = {p.key: p for p in plan.parts}
        n_fm = plan.fm_capacity
        fm = [_Voice("fm", i) for i in range(n_fm)]
        if self.fm_alloc == "fixo":
            self._assign_homes(plan, n_fm)
        psg = [_Voice("psg", i) for i in range(plan.psg_capacity)]
        pcm_ch = list(plan.pcm_channels)
        pcm_busy: dict[int, tuple[int, int, int]] = {}  # canal → (fim, prioridade, início)
        total = int(np.ceil(song.length_s * FPS)) + 2
        frames: list[list[tuple]] = [[] for _ in range(total + int(FPS * 2))]

        # Inicialização dos chips (todos os canais em silêncio)
        init = frames[0]
        init += [("fm", 0, 0x22, 0x00), ("fm", 0, 0x27, 0x00),
                 ("fm", 0, 0x2B, 0x80 if pcm_ch else 0x00)]
        for ch in range(6):
            port, c = (0, ch) if ch < 3 else (1, ch - 3)
            init.append(("fm", 0, 0x28, c + (4 if ch >= 3 else 0)))
            for slot in (0x0, 0x4, 0x8, 0xC):
                init.append(("fm", port, 0x40 + c + slot, 0x7F))
                init.append(("fm", port, 0x80 + c + slot, 0xFF))
        init.append(("fm", 1, 0xB6, 0xC0))           # canal 6 / DAC no centro (efeitos do jogo)
        init += [("psg", b) for b in (0x9F, 0xBF, 0xDF, 0xFF)]
        loop_frame = int(round(self.loop.start_s * FPS)) if self.loop else None

        starts: dict[int, list[Note]] = {}
        for n in song.notes:
            f = int(round(n.start_s * FPS))
            starts.setdefault(f, []).append(n)

        def end_frame(n: Note) -> int:
            return max(int(round(n.start_s * FPS)) + 1, int(round(n.end_s * FPS)))

        for f in range(len(frames)):
            self._frame = f
            w = frames[f]
            if f == loop_frame:
                # Ponto de loop: esquece o estado em cache dos canais. Ao voltar do fim da
                # música para cá, cada nota recarrega patch, volume, pan e frequência.
                for v in fm + psg:
                    v.reload = True       # a nota que ainda soa continua com o patch atual
                    v.last_tl = v.last_freq = v.last_pan = v.last_vol = None
                self._nz_vol = None
            # fins de nota
            for v in fm:
                if v.note is not None and f >= v.end_frame:
                    self._fm_stop(w, v)
            for v in psg:
                if v.note is not None and f >= v.end_frame and v.released_at is None:
                    v.released_at = f
                    self.timeline.append(("release", f, "psg", v.idx))
                if v.note is not None and v.released_at is not None:
                    if self._psg_env_db(v, f) < -30 or f - v.released_at > FPS:
                        self._psg_stop(w, v)
            for chn, (fe, _, _) in list(pcm_busy.items()):
                if f >= fe:
                    del pcm_busy[chn]
            # inícios de nota, da maior para a menor prioridade
            new = sorted(starts.get(f, []), key=lambda n: -pplan[(n.track, n.channel)].priority)
            for n in new:
                p = pplan[(n.track, n.channel)]
                ef = end_frame(n)
                if self.kit is not None and (n.bank, n.program, n.key) in self.kit.noise \
                        and p.kind.startswith("kit"):
                    self._start_noise(w, n, f, p)
                    continue
                if p.pool == "PCM":
                    self._start_pcm(w, n, f, pcm_ch, pcm_busy, p)
                    continue
                placed = False
                if p.pool == "FM":
                    placed = self._place_fm(w, fm, psg, n, p, f, ef, pplan)
                    if not placed and p.overflow_psg:
                        placed = self._place_psg(w, psg, n, p, f, ef, overflow=True)
                elif p.pool == "PSG":
                    placed = self._place_psg(w, psg, n, p, f, ef, overflow=False)
                if not placed:
                    self.counts["omitted"] += 1
                    self.changes.append(f"{self._when(f)} {p.label} {self._nn(n)}: omitida "
                                        f"(sem canal livre; prioridade {p.priority})")
            # atualizações contínuas (bend, vibrato, volume, envelope do PSG)
            for v in fm:
                if v.note is not None:
                    self._fm_update(w, v, f)
            for v in psg:
                if v.note is not None:
                    self._psg_update(w, v, f)
            if self._nz is not None:
                self._noise_update(w, f)

        while frames and not frames[-1]:
            frames.pop()
        self.counts["fm_redundant"] = _drop_redundant_fm(frames, loop_frame)
        stats = dict(self.counts)
        stats["notes_total"] = len(song.notes)
        stats["frames"] = len(frames)
        stats["loop_end_frame"] = int(round(self.loop.end_s * FPS)) if self.loop else None
        return Stream(frames, FPS, loop_frame,
                      self.kit.samples if self.kit else {}, self.kit.rate if self.kit else 14000.0,
                      self.changes, stats)

    @staticmethod
    def _nn(n: Note) -> str:
        from mdconv.analysis.midi_stats import note_name
        return note_name(n.key)

    def _place_fm(self, w, fm, psg, n, p, f, ef, pplan) -> bool:
        if (n.bank, n.program) not in self.patches:
            self.changes.append(f"{self._when(f)} {p.label}: sem patch FM aprovado para "
                                f"{n.bank}:{n.program} — nota omitida")
            return False
        patch = self.patches.get((n.bank, n.program))
        free = [v for v in fm if v.note is None]
        if self.fm_alloc == "fixo":
            v = self._choose_fixed(w, fm, psg, n, p, f, ef, pplan, free, patch)
            if v is None:
                return False
        elif free:
            same = [v for v in free if v.patch_id is patch and not v.reload]
            v = (same or free)[0]
        else:
            v = self._steal_fm(w, psg, p, f, pplan, min(fm, key=lambda v: (v.prio, -v.start_frame)))
            if v is None:
                return False
        v.prio = p.priority
        v.end_frame = ef
        self._fm_start(w, v, n, f)
        self.counts["fm"] += 1
        return True

    def _choose_fixed(self, w, fm, psg, n, p, f, ef, pplan, free, patch):
        home = self.homes.get(p.key) or []

        def oldest_silent(vs):
            same = [v for v in vs if v.patch_id is patch and not v.reload]
            return min(same or vs, key=lambda v: v.off_frame)
        free_home = [v for v in free if v.idx in home]
        if free_home:
            return oldest_silent(free_home)
        shared = [v for v in free if v.idx not in self.home_of]
        if shared:
            return oldest_silent(shared)
        # como no tracker: a voz que não cabe nos canais da parte vai para o PSG, se a parte
        # puder transbordar para lá (o chamador cuida disso) — antes de mexer no canal alheio
        if p.overflow_psg and PSG_MIN_KEY <= n.key <= PSG_MAX_KEY and any(v.note is None for v in psg):
            self.counts["fm_to_psg"] += 1
            return None
        # canal livre de outra parte: o dono que não está tocando há mais tempo
        borrowed = [v for v in free if v.idx in self.home_of]
        if borrowed:
            self.counts["fm_borrowed"] += 1
            return oldest_silent(borrowed)
        # sem canal livre: recupera o próprio canal de quem o pegou emprestado
        lenders = [fm[c] for c in home if fm[c].part != p.key and fm[c].prio <= p.priority]
        if lenders:
            return self._steal_fm(w, psg, p, f, pplan, min(lenders, key=lambda v: (v.prio, -v.start_frame)),
                                  allow_equal=True)
        victim = min(fm, key=lambda v: (v.prio, -v.start_frame))
        if victim.prio < p.priority:
            return self._steal_fm(w, psg, p, f, pplan, victim)
        if home:
            # a parte já ocupa todos os seus canais: a nota mais antiga dela dá lugar à nova
            own = min((fm[c] for c in home), key=lambda v: v.start_frame)
            if own.part == p.key:
                self.counts["shortened"] += 1
                self.changes.append(f"{self._when(f)} {p.label} {self._nn(own.note)}: encurtada "
                                    f"(a parte já ocupa os seus {len(home)} canal(is) FM)")
                self._fm_stop(w, own)
                return own
        return None

    def _steal_fm(self, w, psg, p, f, pplan, victim, allow_equal: bool = False):
        """Tira o canal de ``victim`` (a nota tenta continuar no PSG); ``None`` se ela vale
        mais que a nota nova."""
        if victim.prio > p.priority or (victim.prio == p.priority and not allow_equal):
            return None
        vp = pplan[victim.part]
        moved = False
        if vp.overflow_psg or vp.pool == "PSG":
            if PSG_MIN_KEY <= victim.note.key <= PSG_MAX_KEY:
                moved = self._place_psg(w, psg, victim.note, vp, f, victim.end_frame,
                                        overflow=True, migrated=True)
        if moved:
            self.counts["migrated"] += 1
        else:
            self.counts["shortened"] += 1
            self.changes.append(f"{self._when(f)} {vp.label} {self._nn(victim.note)}: encurtada "
                                f"para dar o canal FM a {p.label} (prioridade {p.priority} ≥ {vp.priority})")
        self._fm_stop(w, victim)
        return victim

    # ---------------------------------------------------------------- canais fixos
    def _assign_homes(self, plan, n_fm: int) -> None:
        """Canais FM próprios: as partes mais importantes primeiro, um canal cada; depois
        canais extras para as partes polifônicas (até 3 vozes). Se houver mais partes que
        canais, sobra ao menos um canal sem dono para as outras dividirem."""
        fm_parts = [p for p in plan.parts if p.pool == "FM"]
        if not fm_parts or n_fm <= 0:
            return
        # donos de canal: melodia e baixo primeiro; depois as partes que mais tempo tocam.
        # Ecos e partes quase sem notas dividem o canal compartilhado.
        total = sum(p.note_count for p in fm_parts) or 1
        cands = [p for p in fm_parts if p.role != "eco" and p.note_count >= 0.05 * total]
        cands.sort(key=lambda p: (-(p.role in ("melodia", "baixo")), -p.active, -p.note_count, p.channel))
        rest = [p for p in fm_parts if p not in cands]
        n_own = len(cands) if (len(cands) < n_fm or (len(cands) == n_fm and not rest)) else n_fm - 1
        free = list(range(n_fm))
        owners = cands[:n_own]
        parts = fm_parts
        keep = 1 if len(fm_parts) > n_own else 0
        for p in owners:
            self.homes[p.key] = [free.pop(0)]
        for p in owners:
            want = min(max(p.polyphony, 1), 3) - 1
            while want > 0 and len(free) > keep:
                self.homes[p.key].append(free.pop(0))
                want -= 1
        for k, chs in self.homes.items():
            for c in chs:
                self.home_of[c] = k
        self._home_labels = {p.key: p.label for p in parts}

    def homes_text(self) -> list[str]:
        L = []
        for k, chs in sorted(self.homes.items(), key=lambda kv: kv[1]):
            L.append(f"  {', '.join(f'FM{c + 1}' for c in chs)}: {self._home_labels.get(k, k)}")
        shared = [c for c in range(self.plan.fm_capacity) if c not in self.home_of]
        if shared:
            L.append(f"  {', '.join(f'FM{c + 1}' for c in shared)}: compartilhado pelas outras partes FM")
        L.append(f"  vozes extras no PSG: {self.counts['fm_to_psg']} · empréstimos de canal: "
                 f"{self.counts['fm_borrowed']} · cargas de patch: {self.counts['patch_loads']}")
        return L

    def _place_psg(self, w, psg, n, p, f, ef, overflow: bool, migrated: bool = False) -> bool:
        if not (PSG_MIN_KEY <= n.key <= PSG_MAX_KEY):
            return False
        free = [v for v in psg if v.note is None]
        if free:
            v = free[0]
        else:
            victim = min(psg, key=lambda v: (v.prio, -v.start_frame))
            if victim.prio >= p.priority:
                return False
            self.counts["shortened"] += 1
            self.changes.append(f"{self._when(f)} C{victim.note.channel + 1:02d} {self._nn(victim.note)}"
                                f" (PSG): encurtada para {p.label}")
            self._psg_stop(w, victim)
            v = victim
        v.prio = p.priority
        v.end_frame = ef
        self._psg_start(w, v, n, f)
        if not migrated:
            self.counts["psg_overflow" if overflow else "psg_planned"] += 1
        return True

    # ---------------------------------------------------------------- ruído do PSG
    def _start_noise(self, w, n, f, p) -> None:
        piece = self.kit.noise[(n.bank, n.program, n.key)]
        cur = self._nz
        if cur is not None:
            if cur["start"] == f and cur["prio"] > piece.priority:
                self.counts["noise_lost"] += 1
                self.changes.append(f"{self._when(f)} {p.label} {self._nn(n)} (ruído): omitida — "
                                    f"peça mais forte no mesmo instante")
                return
            self.counts["noise_cut"] += 1
            self.timeline.append(("off", f, "noise", 0))
        # velocity da nota em relação à da amostra de referência (amplitude linear, como no m4a)
        delta = 20 * np.log10(piece.vel_ref / max(1, n.velocity))
        self._nz = {"piece": piece, "start": f, "note": n, "prio": piece.priority, "delta": delta}
        w.append(("psg", 0xE0 | 0x04 | (piece.nf & 3)))     # ruído branco; reinicia o LFSR
        self._nz_vol = None
        self.timeline.append(("on", f, "noise", 0, n))
        self.counts["noise"] += 1

    def _noise_update(self, w, f) -> None:
        nz = self._nz
        k = f - nz["start"]
        att = nz["piece"].att_db
        vol = 15
        if k < len(att):
            x = (att[k] + nz["delta"]) / 2.0
            if x < 0:
                self.counts["noise_clamped"] += 1
            vol = int(min(15, max(0, round(x))))
        if vol >= 15:
            w.append(("psg", 0xFF))
            self.timeline.append(("off", f, "noise", 0))
            self._nz = None
            self._nz_vol = 15
            return
        if vol != self._nz_vol:
            w.append(("psg", 0xF0 | vol))
            self._nz_vol = vol

    def _start_pcm(self, w, n, f, pcm_ch, pcm_busy, p) -> None:
        if not self.kit or (n.bank, n.program, n.key) not in self.kit.ids:
            self.counts["pcm_missing"] += 1
            return
        sid = self.kit.ids[(n.bank, n.program, n.key)]
        prio = self.kit.priority[sid]
        dur = int(np.ceil(len(self.kit.samples[sid]) / self.kit.rate * FPS))
        free = [c for c in pcm_ch if c not in pcm_busy]
        if free:
            ch = free[0]
        else:
            # Como no m4a, o golpe novo vale mais que a cauda de um golpe antigo: substitui o som
            # que começou há mais tempo. Exceção: se todos começaram agora (mesmo instante) e
            # são mais importantes, o novo é que fica de fora.
            ch = min(pcm_busy, key=lambda c: pcm_busy[c][2])
            end_, prio_, start_ = pcm_busy[ch]
            if f - start_ <= 1:
                ch = min(pcm_busy, key=lambda c: pcm_busy[c][1])
                if pcm_busy[ch][1] > prio:
                    self.counts["pcm_stolen"] += 1
                    self.changes.append(f"{self._when(f)} {p.label} {self._nn(n)} (PCM): omitida — "
                                        f"{len(pcm_ch)} sons mais importantes começaram no mesmo instante")
                    return
            self.counts["pcm_cut_tail"] += 1
        pcm_busy[ch] = (f + dur, prio, f)
        w.append(("pcm", ch, sid, prio))
        self.timeline.append(("pcm", f, ch, n, sid))
        self.counts["pcm"] += 1
