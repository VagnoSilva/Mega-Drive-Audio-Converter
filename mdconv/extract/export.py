"""Fluxo completo: ROM -> MIDI + SoundFont + samples WAV + banco .opm."""
import math
import os
import struct

from . import mdlib, midi, oki, romload, sf2
from .driver import Driver, Song

FM_ZONES_FULL = ((36, 0, 47), (60, 48, 71), (84, 72, 127))
FM_ZONES_QUICK = ((60, 0, 127),)


class GameData:
    def __init__(self, name, entry, driver, oki_rom, warnings):
        self.name, self.entry, self.driver, self.oki_rom, self.warnings = name, entry, driver, oki_rom, warnings
        self.oki_samples = oki.extract(oki_rom) if oki_rom else {}

    def songs(self):
        return self.driver.song_list()


def all_games():
    return romload.load_game_db()


def list_games():
    return sorted(all_games())


def open_game(path, game_name=None, audio_override=None, oki_override=None):
    from . import gba
    if gba.is_gba(path):
        return gba.GBAGame(path)
    games = romload.load_game_db()
    name = game_name or romload.guess_game(path, games)
    if not name or name not in games:
        raise ValueError("Não consegui identificar o jogo pelo nome do arquivo. Escolha o jogo na lista.")
    entry = games[name]
    rs = romload.RomSet(path)
    audio, oki_rom, warnings = romload.load_game_roms(rs, entry, audio_override, oki_override)
    drv = Driver(audio, entry["version"], int(entry["tables"], 16))
    return GameData(name, entry, drv, oki_rom, warnings)


def _log(cb, msg):
    if cb:
        cb(msg)


def export_midis(gd, out_dir, song_ids=None, plays=2, log=None, progress=None):
    os.makedirs(out_dir, exist_ok=True)
    done = []
    songs = gd.songs()
    if song_ids is not None:
        songs = [s for s in songs if s[0] in song_ids]
    for n, (sid, ptr, ntracks) in enumerate(songs):
        try:
            song = Song(gd.driver, sid, ptr).build(plays=plays)
            notes = sum(1 for tr in song.tracks for e in tr.events if e[2] in ("on", "ont"))
            if notes == 0:
                _log(log, f"  música {sid:02X}: sem notas, ignorada")
                continue
            fn = os.path.join(out_dir, f"{gd.name}_{sid:02X}.mid")
            midi.write_midi(fn, song, title=f"{gd.name} song {sid:02X}")
            secs = midi.song_seconds(song)
            _log(log, f"  música {sid:02X}: {ntracks} faixas, {notes} notas, {secs:.0f}s -> {os.path.basename(fn)}")
            done.append(fn)
        except Exception as e:   # uma música com problema não interrompe as outras
            _log(log, f"  música {sid:02X}: erro ({e})")
        if progress:
            progress(n + 1, len(songs))
    return done


def export_oki_wavs(gd, out_dir, log=None):
    os.makedirs(out_dir, exist_ok=True)
    for num, pcm in gd.oki_samples.items():
        oki.write_wav(os.path.join(out_dir, f"oki_{num:03d}.wav"), pcm)
    _log(log, f"  {len(gd.oki_samples)} samples do Oki salvos em WAV")


def export_opm(gd, path):
    with open(path, "w", encoding="latin-1", newline="\r\n") as f:
        f.write(f"//MiOPMdrv sound bank Paramer Ver2002.04.22\n//{gd.name} - CPS1 YM2151\n")
        f.write("//LFO: LFRQ AMD PMD WF NFRQ\n//@:[Num] [Name]\n//CH: PAN FL CON AMS PMS SLOT NE\n")
        f.write("//[OPname]: AR D1R D2R RR D1L TL KS MUL DT1 DT2 AMS-EN\n\n")
        for ins in gd.driver.instruments:
            f.write(ins.to_opm(ins.index) + "\n")


def build_soundfont(gd, path, quality="full", log=None, progress=None):
    from . import ym2151   # numpy só é necessário aqui
    sf = sf2.SoundFont(f"{gd.name} (CPS1)")
    zones_def = FM_ZONES_FULL if quality == "full" else FM_ZONES_QUICK
    instrs = gd.driver.instruments
    total = len(instrs) + 1
    for n, ins in enumerate(instrs):
        zones = []
        for root, lo, hi in zones_def:
            try:
                r = ym2151.render_sample(ins, root)
            except Exception as e:
                _log(log, f"  instrumento FM {ins.index}: erro na síntese ({e})")
                continue
            if r["silent"]:
                continue
            sidx = sf.add_sample(f"FM{ins.index:03d}_k{root}", r["pcm"], r["rate"], root, 0, r["loop"])
            gens = [(sf2.G_KEYRANGE, (lo, hi)), (sf2.G_SAMPLE_MODES, 1)]
            hold_tc = sf2.timecents(r["hold"]) + 100 * root - 6000
            gens += [(sf2.G_HOLD_VOL, max(-12000, min(5000, hold_tc))), (sf2.G_KEY_TO_HOLD, 100)]
            if r["decay_db_s"] > 0.05 and r["sustain_db"]:
                # no SF2 o tempo de decay é o tempo para cair 100 dB
                gens += [(sf2.G_DECAY_VOL, sf2.timecents(min(100.0, 100.0 / r["decay_db_s"]))),
                         (sf2.G_SUSTAIN_VOL, int(min(1000, r["sustain_db"] * 10)))]
            rel = 100.0 / r["rr_db_s"] if r["rr_db_s"] > 0 else 20.0
            gens.append((sf2.G_RELEASE_VOL, sf2.timecents(min(20.0, max(0.005, rel)))))
            try:
                lfo = ym2151.lfo_params(ins)
            except Exception:
                lfo = {}
            if "vib_cents" in lfo:
                gens += [(sf2.G_FREQ_VIBLFO, sf2.abs_cents(lfo["vib_hz"])),
                         (sf2.G_VIBLFO_TO_PITCH, min(12000, lfo["vib_cents"]))]
            if "trem_cb" in lfo:
                gens += [(sf2.G_FREQ_MODLFO, sf2.abs_cents(lfo["trem_hz"])),
                         (sf2.G_MODLFO_TO_VOL, lfo["trem_cb"])]
            zones.append((gens, sidx))
        if zones:
            pname = mdlib.preset_name(gd.name, "FM", ins.index)
            inst = sf.add_instrument(pname, zones)
            sf.add_preset(pname, 0, ins.index, inst)
        if progress:
            progress(n + 1, total)

    # Oki: banco 1 (as faixas Oki do MIDI selecionam o banco 1)
    if gd.oki_samples:
        smp_idx = {}
        for num, pcm in gd.oki_samples.items():
            data = struct.pack("<%dh" % len(pcm), *pcm)
            smp_idx[num] = sf.add_sample(f"Oki{num:03d}", data, oki.SAMPLE_RATE, 60, 0, None)
        inst_for = {}
        for prog, num in sorted(gd.driver.oki_preset_map(127).items()):
            if num not in smp_idx:
                continue
            if num not in inst_for:
                secs = len(gd.oki_samples[num]) / oki.SAMPLE_RATE
                gens = [(sf2.G_KEYRANGE, (0, 127)), (sf2.G_SCALE_TUNING, 0),
                        (sf2.G_RELEASE_VOL, sf2.timecents(max(1.0, secs * 8)))]
                inst_for[num] = sf.add_instrument(f"{gd.name[:10]} smp {num:03d}", [(gens, smp_idx[num])])
            sf.add_preset(mdlib.preset_name(gd.name, "Oki", prog), 1, prog, inst_for[num])
            # Cópia no banco 128: programas que leem o banco como CC0*128+CC32 (como o
            # Mega Drive Audio Converter) pedem o banco 128 quando o MIDI manda CC0=1.
            sf.add_preset(mdlib.preset_name(gd.name, "Oki", prog), 128, prog, inst_for[num])
        _log(log, f"  {len(gd.oki_samples)} samples do Oki adicionados (banco 1)")
    if progress:
        progress(total, total)
    sf.write(path)
    _log(log, f"  SoundFont salvo: {os.path.basename(path)} ({os.path.getsize(path) / 1e6:.1f} MB)")


def export_all(gd, out_dir, plays=2, do_midi=True, do_sf2=True, do_wav=True, do_opm=True,
               quality="full", song_ids=None, log=None, progress=None, do_mdlib=True):
    if getattr(gd, "format", "CPS1") == "GBA":
        from . import gba
        return gba.export_all(gd, out_dir, song_ids=song_ids, do_midi=do_midi, do_sf2=do_sf2,
                              log=log, progress=progress)
    base = os.path.join(out_dir, gd.name)
    os.makedirs(base, exist_ok=True)
    for w in gd.warnings:
        _log(log, "Aviso: " + w)
    d = gd.driver
    _log(log, f"Jogo: {gd.name} | driver v{gd.entry['version']} | {d.num_seqs} entradas na tabela | "
              f"{len(d.instruments)} instrumentos FM | {len(gd.oki_samples)} samples Oki")
    if do_midi:
        _log(log, "Gerando MIDIs...")
        export_midis(gd, os.path.join(base, "midi"), song_ids, plays, log,
                     (lambda a, b: progress("MIDI", a, b)) if progress else None)
    if do_opm and d.instruments:
        p = os.path.join(base, f"{gd.name}.opm")
        export_opm(gd, p)
        _log(log, f"Banco de timbres FM (VOPM) salvo: {os.path.basename(p)}")
    if do_mdlib and d.instruments:
        _log(log, "Gerando biblioteca de patches para o Mega Drive Audio Converter...")
        mdlib.write_library(gd, os.path.join(base, "biblioteca_megadrive"),
                            os.path.join(base, f"{gd.name}.sf2"), log)
    if do_wav and gd.oki_samples:
        _log(log, "Salvando samples do Oki...")
        export_oki_wavs(gd, os.path.join(base, "samples_oki"), log)
    if do_sf2:
        _log(log, "Gerando SoundFont (sintetizando os instrumentos FM)...")
        build_soundfont(gd, os.path.join(base, f"{gd.name}.sf2"), quality, log,
                        (lambda a, b: progress("SF2", a, b)) if progress else None)
    _log(log, f"Concluído. Arquivos em: {base}")
    return base
