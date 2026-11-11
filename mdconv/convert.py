"""Conversão completa de uma música: plano → patches aprovados → kit PCM → driver → prévia."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mdconv.arrange.loop import LoopPoint, resolve_loop
from mdconv.arrange.plan import Plan, make_plan, plan_text
from mdconv.driver.driver import Driver, Stream
from mdconv.fm.patch import Operator, Patch
from mdconv.io_sf2.resolve import find_preset, resolve_voices
from mdconv.io_sf2.sf2 import SoundFont
from mdconv.library.patch_library import PatchLibrary
from mdconv.model.song import Song
from mdconv.pcm.kit import Kit, build_kit


@dataclass
class Conversion:
    plan: Plan
    patches: dict
    missing_patches: list
    kit: Kit | None
    stream: Stream
    balance: dict | None = None
    loop: LoopPoint | None = None
    driver: object = None          # o Driver que gerou o fluxo (linha do tempo por canal)
    song: object = None
    psg_envelopes: dict | None = None


def approved_patches(library_dir: Path | None, sf: SoundFont) -> dict[tuple[int, int], Patch]:
    """Patches aprovados para os instrumentos deste SF2.

    Vale para (banco, programa) quando o preset tem o mesmo nome no SF2 atual — assim um
    patch aprovado numa música serve para as outras músicas do mesmo jogo, mesmo vindo de
    outro arquivo .sf2, mas não é aplicado a um instrumento diferente que por acaso use o
    mesmo número de programa. Se houver mais de um aprovado, vale o mais recente."""
    return {k: e.patch for k, e in approved_entries(library_dir, sf).items()}


def approved_entries(library_dir: Path | None, sf: SoundFont) -> dict:
    out = {}
    if not (library_dir and Path(library_dir).exists()):
        return out
    names = {(p.bank, p.program): p.name.strip() for p in sf.presets}
    entries = [e for e in PatchLibrary(library_dir).entries() if e.approved and "bank" in e.source]
    entries.sort(key=lambda e: e.source.get("approved_at", "") or "")
    for e in entries:
        k = (e.source["bank"], e.source["program"])
        want = (e.source.get("preset") or "").strip()
        if k in names and (not want or names[k] == want):
            out[k] = e
    return out


def fallback_patch() -> Patch:
    """Patch neutro (seno levemente brilhante) para instrumentos ainda sem patch aprovado."""
    return Patch(alg=4, fb=2, ops=[Operator(mul=1, tl=36, ar=31, d1r=6, sl=4, rr=7),
                                   Operator(mul=1, tl=0, ar=31, d1r=4, sl=2, d2r=2, rr=7),
                                   Operator(tl=127), Operator(tl=127)], name="provisório")


def psg_envelopes(song: Song, sf: SoundFont) -> dict:
    envs = {}
    for n in song.notes:
        k = (n.bank, n.program)
        if k in envs:
            continue
        pr = find_preset(sf, n.bank, n.program).preset
        vs = resolve_voices(sf, pr, n.key, n.velocity) if pr else []
        envs[k] = vs[0].vol_envelope() if vs else {}
    return envs


def _rms_db(x) -> float:
    import numpy as np
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x) ** 2)) + 1e-12)


def convert(song: Song, sf: SoundFont, library_dir: Path | None, use_pcm: bool = True,
            pcm_channels: list[int] | None = None, vibrato: bool = True,
            balance: str = "auto", fm_att_db: float = 0.0, pcm_headroom_db: float = 3.0,
            loop_mode: str = "auto", loop_start_tick: int | None = None,
            loop_end_tick: int | None = None) -> Conversion:
    """``balance='auto'``: o XGM não tem volume para o PCM, então o equilíbrio bateria × resto
    do original é recuperado abaixando FM e PSG (calculado comparando os grupos de partes
    no original e na conversão). ``balance='manual'`` usa ``fm_att_db``."""
    loop = resolve_loop(song, loop_mode, loop_start_tick, loop_end_tick)
    plan = make_plan(song, sf, pcm_channels=pcm_channels, use_pcm=use_pcm)
    patches = approved_patches(library_dir, sf)
    missing = []
    for p in plan.parts:
        if p.pool == "FM" or p.overflow_psg:
            for bp in p.programs:
                if bp not in patches:
                    patches[bp] = fallback_patch()
                    missing.append(f"{p.label} ({bp[0]}:{bp[1]}): sem patch aprovado — usando patch provisório")
    kit = None
    kit_parts = [p.key for p in plan.parts if p.pool == "PCM"]
    if kit_parts:
        kit = build_kit(song, sf, kit_parts, headroom_db=pcm_headroom_db)
    envs = psg_envelopes(song, sf)

    last = {}

    def run(att):
        d = Driver(song, plan, patches, envs, kit, vibrato=vibrato,
                   fm_att_db=att, psg_att_db=att, loop=loop)
        last["driver"] = d
        return d.run()

    info = None
    att = fm_att_db
    if balance == "auto" and kit is not None:
        from mdconv.reference.sampler import RenderOptions, render_song
        from mdconv.render.stream_render import render_stream
        tonal = {p.channel for p in plan.parts if p.pool != "PCM"}
        drums = {p.channel for p in plan.parts if p.pool == "PCM"}
        o_t, _ = render_song(song, sf, RenderOptions(), only_channels=tonal, normalize=False)
        o_d, _ = render_song(song, sf, RenderOptions(), only_channels=drums, normalize=False)
        target = _rms_db(o_d) - _rms_db(o_t)                     # bateria − resto, no original
        st0 = run(0.0)
        # medição de nível: o emulador próprio é ~4× mais rápido e tem o mesmo nível do Nuked
        m_t, _ = render_stream(st0, normalize=False, solo={"fm", "psg"}, fm_engine="proprio")
        m_d, _ = render_stream(st0, normalize=False, solo={"pcm"}, fm_engine="proprio")
        current = _rms_db(m_d) - _rms_db(m_t)
        att = max(0.0, target - current)
        info = {"target_db": round(target, 1), "before_db": round(current, 1), "fm_psg_att_db": round(att, 1),
                "pcm_headroom_db": pcm_headroom_db}
    stream = run(att)
    return Conversion(plan, patches, missing, kit, stream, info, loop, last["driver"], song, envs)


def report_text(c: Conversion, render_info=None) -> str:
    s = c.stream.stats
    L = ["=== Loop ===", c.loop.text() if c.loop else "sem loop (toca uma vez)", "",
         "=== Distribuição ===", plan_text(c.plan), "", "=== Resultado do driver ==="]
    L.append(f"notas: {s['notes_total']} · FM {s['fm']} · PSG planejado {s['psg_planned']} · "
             f"PSG por transbordo {s['psg_overflow']} · migradas FM→PSG {s['migrated']} · "
             f"encurtadas {s['shortened']} · omitidas {s['omitted']}")
    if c.kit:
        L.append(f"PCM: {s['pcm']} disparos · {s['pcm_cut_tail']} caudas cortadas por golpe novo · "
                 f"{s['pcm_stolen']} golpes omitidos · "
                 f"{len(c.kit.samples)} amostras, {c.kit.bytes_aligned / 1024:.1f} KB (alinhado a 256)")
        L.append("     o XGM não tem volume por nota: a velocity das notas de bateria não altera o volume")
    L.append(f"cargas de patch FM: {s['patch_loads']}")
    if c.balance:
        b = c.balance
        L.append(f"equilíbrio bateria × resto: original {b['target_db']:+.1f} dB; sem ajuste "
                 f"{b['before_db']:+.1f} dB → FM e PSG atenuados em {b['fm_psg_att_db']:.1f} dB "
                 f"(amostras PCM com {b['pcm_headroom_db']:.0f} dB de margem para os efeitos do jogo)")
    if c.missing_patches:
        L += ["", "Sem patch aprovado:"] + [f"  {m}" for m in c.missing_patches]
    if render_info:
        L += ["", f"Prévia: FM em {render_info.fm_engine}; PSG e PCM no emulador próprio; "
                  f"{render_info.seconds:.1f} s; normalizada em {render_info.normalize_db:+.1f} dB"]
        if render_info.pcm_clipped_samples:
            L.append(f"  PCM saturou em {render_info.pcm_clipped_samples} amostras (soma acima de 8 bits)")
    if c.stream.changes:
        L += ["", f"=== Alterações nota a nota ({len(c.stream.changes)}) ==="] + c.stream.changes
    return "\n".join(L)
