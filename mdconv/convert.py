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
    tempo_grid: object = None      # GridReport (andamento travado na grade de quadros)
    fm_alloc: dict | None = None   # modo de canais FM usado (e, no automático, a comparação)


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
    # Mesmo critério do resto do programa: o preset que ``find_preset`` encontra para
    # (banco, programa), inclusive quando o banco pedido pelo MIDI não existe no SF2 e o
    # instrumento vem de outro banco (ex.: MIDI pede o banco 10, o SF2 só tem o 0).
    names: dict[tuple[int, int], str | None] = {}

    def name_of(k):
        if k not in names:
            pr = find_preset(sf, k[0], k[1]).preset
            names[k] = pr.name.strip() if pr is not None else None
        return names[k]

    entries = [e for e in PatchLibrary(library_dir).entries() if e.approved and "bank" in e.source]
    entries.sort(key=lambda e: e.source.get("approved_at", "") or "")
    for e in entries:
        k = (e.source["bank"], e.source["program"])
        want = (e.source.get("preset") or "").strip()
        have = name_of(k)
        if have is not None and (not want or have == want):
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
            loop_end_tick: int | None = None, noise_metals: bool = False,
            snap_tempo: bool = False, fm_alloc: str = "dinamico") -> Conversion:
    """``balance='auto'``: o XGM não tem volume para o PCM, então o equilíbrio bateria × resto
    do original é recuperado abaixando FM e PSG (calculado comparando os grupos de partes
    no original e na conversão). ``balance='manual'`` usa ``fm_att_db``.
    ``noise_metals``: chimbais, pratos e afins tocam no ruído do PSG (ver ``mdconv.pcm.kit``).
    ``snap_tempo``: andamento ajustado para o passo rítmico durar um número inteiro de quadros
    (ver ``mdconv.arrange.tempo_grid``).
    ``fm_alloc``: ``"dinamico"`` (canal FM escolhido nota a nota) ou ``"fixo"`` (cada parte
    principal com o seu canal, como um músico faria no tracker) ou ``"auto"`` (roda os dois e
    fica com o que perde menos notas e recarrega menos patches)."""
    grid = None
    if snap_tempo:
        from mdconv.arrange.tempo_grid import snap_tempo as _snap
        song, grid = _snap(song)
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
        kit = build_kit(song, sf, kit_parts, headroom_db=pcm_headroom_db, noise_metals=noise_metals)
    elif noise_metals:
        # sem PCM: só as peças metálicas ganham o ruído; o resto do kit segue no PSG de tom
        drum_parts = [p.key for p in plan.parts if p.kind.startswith("kit")]
        if drum_parts:
            kit = build_kit(song, sf, drum_parts, headroom_db=pcm_headroom_db, noise_metals=True, pcm=False)
    envs = psg_envelopes(song, sf)

    last = {}
    alloc_info = {"mode": fm_alloc}
    if fm_alloc == "auto":
        scores = {}
        for mode in ("dinamico", "fixo"):
            st = Driver(song, plan, patches, envs, kit, vibrato=vibrato, loop=loop, fm_alloc=mode).run().stats
            scores[mode] = {"omitted": st["omitted"], "shortened": st["shortened"], "patch_loads": st["patch_loads"],
                            "score": round(4 * st["omitted"] + st["shortened"] + 0.25 * st["patch_loads"], 1)}
        fm_alloc = min(scores, key=lambda m: (scores[m]["score"], m != "dinamico"))
        alloc_info = {"mode": fm_alloc, "auto": True, "scores": scores}

    def run(att):
        d = Driver(song, plan, patches, envs, kit, vibrato=vibrato,
                   fm_att_db=att, psg_att_db=att, loop=loop, fm_alloc=fm_alloc)
        last["driver"] = d
        return d.run()

    info = None
    att = fm_att_db
    if balance == "auto" and kit is not None and kit_parts:
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
        m_d, _ = render_stream(st0, normalize=False, solo={"pcm", "noise"}, fm_engine="proprio")
        current = _rms_db(m_d) - _rms_db(m_t)
        att = max(0.0, target - current)
        info = {"target_db": round(target, 1), "before_db": round(current, 1), "fm_psg_att_db": round(att, 1),
                "pcm_headroom_db": pcm_headroom_db}
    stream = run(att)
    return Conversion(plan, patches, missing, kit, stream, info, loop, last["driver"], song, envs, grid, alloc_info)


def report_text(c: Conversion, render_info=None) -> str:
    s = c.stream.stats
    L = ["=== Loop ===", c.loop.text() if c.loop else "sem loop (toca uma vez)", ""]
    if c.tempo_grid is not None:
        L += ["=== Andamento na grade de quadros ===", c.tempo_grid.text(), ""]
    if c.fm_alloc and c.fm_alloc.get("auto"):
        sc = c.fm_alloc["scores"]
        L += ["=== Canais FM (automático) ==="]
        for m, v in sc.items():
            L.append(f"  {m}: {v['omitted']} omitidas · {v['shortened']} encurtadas · "
                     f"{v['patch_loads']} cargas de patch")
        L.append(f"  escolhido: {'canais fixos' if c.fm_alloc['mode'] == 'fixo' else 'nota a nota (dinâmico)'}")
        L.append("")
    if c.driver is not None and getattr(c.driver, "homes", None):
        L += ["=== Canais FM fixos ==="] + c.driver.homes_text() + [""]
    L += [
         "=== Distribuição ===", plan_text(c.plan), "", "=== Resultado do driver ==="]
    L.append(f"notas: {s['notes_total']} · FM {s['fm']} · PSG planejado {s['psg_planned']} · "
             f"PSG por transbordo {s['psg_overflow']} · migradas FM→PSG {s['migrated']} · "
             f"encurtadas {s['shortened']} · omitidas {s['omitted']}")
    if c.kit:
        L.append(f"PCM: {s['pcm']} disparos · {s['pcm_cut_tail']} caudas cortadas por golpe novo · "
                 f"{s['pcm_stolen']} golpes omitidos · "
                 f"{len(c.kit.samples)} amostras, {c.kit.bytes_aligned / 1024:.1f} KB (alinhado a 256)")
        if c.kit.samples:
            L.append("     o XGM não tem volume por nota: a velocity das notas de bateria não altera o volume")
    if c.kit and c.kit.noise:
        L.append(f"Ruído do PSG: {s['noise']} golpes · {s['noise_cut']} cortados por golpe novo · "
                 f"{s['noise_lost']} omitidos (peça mais forte no mesmo quadro)"
                 + (f" · {s['noise_clamped']} quadros no volume máximo do PSG" if s['noise_clamped'] else ""))
        for k, pc in sorted(c.kit.noise.items()):
            ft = c.kit.features.get(k, {})
            L.append(f"     {pc.name}: {len(pc.att_db) / 60:.2f} s · centroide {ft.get('centroid_hz', 0):.0f} Hz"
                     f" · ruído branco N/512")
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
