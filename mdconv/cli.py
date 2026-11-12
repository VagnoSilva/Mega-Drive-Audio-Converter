"""Linha de comando do Mega Drive Audio Converter.

    python -m mdconv.cli analyze musica.mid [banco.sf2] [--json saida.json]
    python -m mdconv.cli render-song musica.mid banco.sf2 saida.wav [--solo 1,5] [--law m4a|gm]
    python -m mdconv.cli render-instruments musica.mid banco.sf2 pasta_saida
    python -m mdconv.cli fm-fit musica.mid banco.sf2 pasta_saida [--budget quick|normal|thorough]
    python -m mdconv.cli fm-approve pasta_saida 10-4_Rhodes_Piano 1 banco.sf2 [--library pasta]
    python -m mdconv.cli preview musica.mid banco.sf2 saida.wav [--library pasta] [--pcm-channels 1,2]
    python -m mdconv.cli import-tfi pasta_tfi [--bank banco_tfi]
    python -m mdconv.cli export musica.mid banco.sf2 pasta_saida [--format xgm,vgm] [--wav]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mdconv.analysis.instrument_match import match_instruments
from mdconv.analysis.midi_stats import analyze_song
from mdconv.analysis.report import sf2_summary, to_json, to_text
from mdconv.io_midi.song_builder import load_song
from mdconv.io_sf2.sf2 import read_sf2


def _finite(o):
    """JSON válido: troca NaN/±inf por null (ex.: sustentação -inf = decai até o silêncio)."""
    import math
    import numpy as np
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def _is_ripper(song) -> bool:
    return any("SequenceRipper" in t.text or "Mus Rip" in t.text for t in song.texts)


def cmd_analyze(args) -> int:
    song = load_song(args.midi)
    stats = analyze_song(song)
    sf_sum = matches = None
    if args.sf2:
        sf = read_sf2(args.sf2)
        sf_sum = sf2_summary(sf)
        matches = match_instruments(song, sf)
    print(to_text(stats, sf_sum, matches))
    if args.json:
        Path(args.json).write_text(to_json(stats, sf_sum, matches), encoding="utf-8")
        print(f"\nRelatório JSON: {args.json}")
    return 0


def cmd_render_song(args) -> int:
    from mdconv.reference.sampler import RenderOptions, render_song, write_wav
    song = load_song(args.midi)
    sf = read_sf2(args.sf2)
    opt = RenderOptions(sample_rate=args.rate, interp=args.interp, law=args.law,
                        vibrato=not args.no_vibrato, gm_drum_channel=not _is_ripper(song))
    solo = {int(c) - 1 for c in args.solo.split(",")} if args.solo else None
    audio, rep = render_song(song, sf, opt, only_channels=solo)
    write_wav(args.out, audio, args.rate)
    print(f"Gravado {args.out}: {len(audio) / args.rate:.1f} s, {rep.notes_rendered} notas")
    print(f"Lei de volume: {opt.law} · interpolação: {opt.interp} · vibrato: "
          f"{'sim (escala provisória)' if opt.vibrato else 'não'}")
    print(f"Normalizado em {rep.normalize_gain_db:+.1f} dB (pico em -1 dBFS)")
    if rep.notes_silent:
        print(f"⚠ {rep.notes_silent} nota(s) sem preset/zona no SF2 ficaram mudas (como no SF2 original):")
        for k, v in rep.silent_detail.items():
            print(f"    {k}: {v}")
    return 0


def cmd_render_instruments(args) -> int:
    from mdconv.reference.instruments import render_instruments, summary_text
    song = load_song(args.midi)
    sf = read_sf2(args.sf2)
    out = Path(args.out_dir)
    res = render_instruments(song, sf, out, sr=args.rate, interp=args.interp,
                             gm_drum_channel=not _is_ripper(song))
    text = summary_text(res)
    print(text)
    (out / "caracteristicas.txt").write_text(text, encoding="utf-8")
    data = [{"label": r.label, "channel": r.channel + 1, "bank": r.bank, "program": r.program,
             "preset": r.preset_name, "kind": r.kind,
             "notes": [{"key": n.key, "velocity": n.velocity, "hold_s": n.hold_s, "samples": n.samples,
                        "wav": Path(n.wav).name if n.wav else None,
                        "features": n.features.to_dict()} for n in r.notes]} for r in res]
    (out / "caracteristicas.json").write_text(
        json.dumps(_finite(data), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8")
    print(f"\n{sum(len(r.notes) for r in res)} WAVs e caracteristicas.json/txt em {out}")
    return 0


def cmd_fm_fit(args) -> int:
    from mdconv.fm.instrument_fit import fit_song, report_text
    song = load_song(args.midi)
    sf = read_sf2(args.sf2)
    out = Path(args.out_dir)
    progs = {int(p) for p in args.programs.split(",")} if args.programs else None
    bank = None
    if args.bank:
        from mdconv.library.bank import PatchBank
        bank = PatchBank(args.bank)
    rep = fit_song(song, sf, out, budget=args.budget, only_programs=progs,
                   progress=lambda s: print(s, flush=True), max_fb=args.max_fb, detune=args.detune,
                   bank=bank, bank_only=args.bank_only)
    text = report_text(rep)
    (out / "previas.txt").write_text(text, encoding="utf-8")
    print("\n" + text)
    return 0


def cmd_fm_approve(args) -> int:
    from mdconv.fm.instrument_fit import approve
    r = approve(Path(args.out_dir), args.label, args.rank, read_sf2(args.sf2), Path(args.library))
    p = r["patch"]
    print(f"Aprovado: {r['label']} candidato #{r['rank']} → biblioteca '{r['library_id']}' "
          f"(alg {p['alg']}, fb {p['fb']}, MUL {[o['mul'] for o in p['ops']]})")
    return 0


def _convert(args):
    from mdconv.convert import convert
    song = load_song(args.midi)
    sf = read_sf2(args.sf2)
    pcm = [int(c) for c in args.pcm_channels.split(",")] if args.pcm_channels else [1, 2]
    return song, convert(song, sf, Path(args.library), use_pcm=not args.no_pcm, pcm_channels=pcm,
                         vibrato=not args.no_vibrato,
                         balance="manual" if args.fm_att is not None else "auto",
                         fm_att_db=args.fm_att or 0.0, noise_metals=args.noise_metals,
                         fm_alloc=args.fm_alloc, snap_tempo=args.tempo_grid)


def cmd_import_tfi(args) -> int:
    """Monta o banco de patches de partida a partir de uma pasta de .tfi."""
    import json as _json
    import os as _os
    from mdconv.library.bank import build_bank
    w = args.workers or max(1, (_os.cpu_count() or 2))
    s = build_bank(args.folder, args.bank, workers=w, progress=lambda m: print(m, flush=True))
    print(_json.dumps(s, ensure_ascii=False, indent=2))
    return 0


def cmd_export(args) -> int:
    from mdconv.convert import report_text
    from mdconv.export.validate import export_and_validate
    song, c = _convert(args)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = args.name or Path(args.midi).stem
    fmts = {f.strip().lower() for f in args.format.split(",")}
    lines = export_and_validate(c.stream, out, stem, fmts, title=args.title or stem, game=args.game or "",
                                wav=args.wav, conversion=c)
    text = "\n".join(lines)
    print(text)
    (out / f"{stem}_exportacao.txt").write_text(text + "\n\n" + report_text(c, None), encoding="utf-8")
    return 0


def cmd_preview(args) -> int:
    from mdconv.convert import report_text
    from mdconv.reference.sampler import write_wav
    from mdconv.render.stream_render import render_stream
    song, c = _convert(args)
    y, info = render_stream(c.stream, fm_engine=args.engine)
    write_wav(args.out, y, 44100)
    text = report_text(c, info)
    rep = Path(args.out).with_suffix(".txt")
    rep.write_text(text, encoding="utf-8")
    head = text.split("=== Alterações")[0]
    print(head)
    print(f"Prévia: {args.out}\nRelatório completo (com cada nota alterada): {rep}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mdconv", description="Mega Drive Audio Converter")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="Analisa MIDI (e SF2, se informado)")
    a.add_argument("midi")
    a.add_argument("sf2", nargs="?")
    a.add_argument("--json")
    a.set_defaults(func=cmd_analyze)

    def common(p):
        p.add_argument("--rate", type=int, default=44100)
        p.add_argument("--interp", choices=["linear", "nearest"], default="linear",
                       help="nearest aproxima o som 'áspero' do mixer do GBA")

    r = sub.add_parser("render-song", help="Renderiza a música original com o SF2 (WAV)")
    r.add_argument("midi")
    r.add_argument("sf2")
    r.add_argument("out")
    r.add_argument("--solo", help="canais MIDI (1-16) separados por vírgula")
    r.add_argument("--law", choices=["m4a", "gm"], default="m4a")
    r.add_argument("--no-vibrato", action="store_true")
    common(r)
    r.set_defaults(func=cmd_render_song)

    i = sub.add_parser("render-instruments", help="Renderiza e mede cada instrumento usado")
    i.add_argument("midi")
    i.add_argument("sf2")
    i.add_argument("out_dir")
    common(i)
    i.set_defaults(func=cmd_render_instruments)

    f = sub.add_parser("fm-fit", help="Gera patches YM2612 para os instrumentos e prévias A/B")
    f.add_argument("midi")
    f.add_argument("sf2")
    f.add_argument("out_dir")
    f.add_argument("--budget", choices=["quick", "normal", "thorough"], default="normal")
    f.add_argument("--programs", help="só estes números de programa, separados por vírgula")
    f.add_argument("--max-fb", type=int, default=7, choices=range(8),
                   help="feedback máximo (FB 6-7 gera ruído; use 5 para evitar chiado)")
    f.add_argument("--detune", action="store_true",
                   help="libera detune na busca (efeito de coro para ensembles/strings)")
    f.add_argument("--bank", help="banco de patches prontos (pasta criada por import-tfi)")
    f.add_argument("--bank-only", action="store_true", help="só candidatos do banco (pula a busca do zero)")
    f.set_defaults(func=cmd_fm_fit)

    ap_ = sub.add_parser("fm-approve", help="Aprova um candidato e o grava na biblioteca")
    ap_.add_argument("out_dir")
    ap_.add_argument("label", help="ex.: 10-4_Rhodes_Piano")
    ap_.add_argument("rank", type=int)
    ap_.add_argument("sf2")
    ap_.add_argument("--library", default="biblioteca_patches")
    ap_.set_defaults(func=cmd_fm_approve)

    pv = sub.add_parser("preview", help="Converte a música inteira e gera a prévia Mega Drive (WAV)")
    pv.add_argument("midi")
    pv.add_argument("sf2")
    pv.add_argument("out")
    pv.add_argument("--library", default="biblioteca_patches", help="pasta da biblioteca de patches")
    pv.add_argument("--pcm-channels", default="1,2",
                    help="canais PCM do XGM para a música (os outros ficam para o jogo)")
    pv.add_argument("--no-pcm", action="store_true", help="sem PCM (FM6 livre; bateria no PSG)")
    pv.add_argument("--no-vibrato", action="store_true")
    pv.add_argument("--fm-alloc", choices=["auto", "dinamico", "fixo"], default="auto",
                    help="canais FM: fixo por parte (como no tracker), nota a nota, ou o melhor dos dois")
    pv.add_argument("--tempo-grid", action="store_true",
                    help="trava o andamento na grade de quadros (passo rítmico com nº inteiro de quadros)")
    pv.add_argument("--noise-metals", action="store_true",
                    help="chimbais, pratos e afins no ruído do PSG (libera o PCM para bumbo e caixa)")
    pv.add_argument("--fm-att", type=float, help="atenuação FM/PSG manual em dB (padrão: automática)")
    pv.add_argument("--engine", choices=["auto", "nuked", "proprio", "gpgx"], default="auto",
                    help="emulador do FM na prévia (auto = Nuked-OPN2)")
    pv.set_defaults(func=cmd_preview)

    it = sub.add_parser("import-tfi", help="Monta o banco de patches de partida a partir de .tfi")
    it.add_argument("folder", help="pasta com os .tfi (subpastas incluídas)")
    it.add_argument("--bank", default="banco_tfi", help="pasta do banco (separada da dos aprovados)")
    it.add_argument("--workers", type=int, help="processos em paralelo (padrão: nº de núcleos)")
    it.set_defaults(func=cmd_import_tfi)

    ex = sub.add_parser("export", help="Converte e exporta XGM v1 (SGDK) e/ou VGM, validando os arquivos")
    ex.add_argument("midi")
    ex.add_argument("sf2")
    ex.add_argument("out_dir")
    ex.add_argument("--format", default="xgm,vgm", help="xgm, vgm e/ou fur (Furnace 0.6.8.3), separados por vírgula")
    ex.add_argument("--name", help="nome base dos arquivos (padrão: nome do MIDI)")
    ex.add_argument("--title", help="título no GD3 do VGM")
    ex.add_argument("--game", help="jogo no GD3 do VGM")
    ex.add_argument("--wav", action="store_true", help="também grava a prévia WAV do que foi exportado")
    ex.add_argument("--library", default="biblioteca_patches", help="pasta da biblioteca de patches")
    ex.add_argument("--pcm-channels", default="1,2",
                    help="canais PCM do XGM para a música (os outros ficam para o jogo)")
    ex.add_argument("--no-pcm", action="store_true", help="sem PCM (FM6 livre; bateria no PSG)")
    ex.add_argument("--no-vibrato", action="store_true")
    ex.add_argument("--fm-alloc", choices=["auto", "dinamico", "fixo"], default="auto",
                    help="canais FM: fixo por parte (como no tracker), nota a nota, ou o melhor dos dois")
    ex.add_argument("--tempo-grid", action="store_true",
                    help="trava o andamento na grade de quadros (passo rítmico com nº inteiro de quadros)")
    ex.add_argument("--noise-metals", action="store_true",
                    help="chimbais, pratos e afins no ruído do PSG (libera o PCM para bumbo e caixa)")
    ex.add_argument("--fm-att", type=float, help="atenuação FM/PSG manual em dB (padrão: automática)")
    ex.set_defaults(func=cmd_export)

    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
