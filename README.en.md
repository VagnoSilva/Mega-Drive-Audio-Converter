# Mega Drive Audio Converter — version 0.11.2

*Leia em português: [README.md](README.md)*

Converts game music to the Sega Mega Drive / Genesis sound (YM2612 FM + SN76489 PSG + PCM), with
export to **XGM v1** (for games made with SGDK), **VGM** and **Furnace** (.fur) projects.

The interface is available in **Portuguese and English** (Help → Idioma / Language). The full
user manual is `Manual_Mega_Drive_Audio_Converter_EN.pdf` (English) and
`Manual_Mega_Drive_Audio_Converter.pdf` (Portuguese).

## What it does

The window has two tabs:

- **Extraction** — takes the music straight from the game ROM:
  - **Capcom CPS1** arcades (MAME .zip, 32 games) and **Game Boy Advance** games (.gba, Nintendo's
    MP2k/Sappy engine): generates MIDI + SoundFont for the Conversion tab. On CPS1 the original
    YM2151 FM voices come out already converted to YM2612 as a library of approved patches.
  - Games converted **directly to the Mega Drive** (XGM, VGM, Furnace and preview; sound effects as
    C code for SGDK): **Moon Patrol, Kung-Fu Master, Lode Runner** (Irem), **Ghosts 'n Goblins**
    (Capcom), **Nemesis** (Konami) and **The King of Fighters '94 to '98** (Neo Geo). The sound
    program of the board runs emulated (or the ROM's sound scripts are interpreted) and what it
    plays is translated chip by chip.
- **Conversion** — turns MIDI + SoundFont into Mega Drive music. It also accepts MIDI + SF2 from
  GBA Mus Ripper and regular **General MIDI** files **without a SoundFont** (the program uses its own
  default General MIDI bank). The FM sound of each instrument is fitted by a search with the chip
  emulator in the loop; you listen to A/B candidates and approve them, and the approved patches
  are reused by every song with the same instrument.

**Tools → Converters and effects…** (Ctrl+T) opens a window with the converters outside the
MIDI + SF2 flow:

- **VGM music (AY / YM2203 / Mega Drive)** — AY-3-8910 (arcades, MSX, ZX Spectrum…) and YM2203 VGMs
  translated register by register, with a choice of destination for each AY channel; VGMs that are
  already Mega Drive music (YM2612 + SN76489) repackaged as XGM/.fur, with the DAC samples as XGM PCM.
  A ready-made SGDK XGM can also be turned into VGM, .fur and a preview. The loop can be added or
  changed.
- **Chip effects (AY)** — AY effect VGMs → PSG/FM + C code for SGDK, on the channels you mark as free.
- **Sound effects and voices (WAV)** — 8-bit PCM at the driver rate, without the "hoarse" sound.
- **.tfi patch bank** — gathers .tfi patches into a bank of FM candidates.

## Download (Windows)

Get the latest `MegaDriveAudioConverter-windows-x64.zip` from the GitHub **Releases** page, extract
it and run `MegaDriveAudioConverter.exe` **from inside the extracted folder** (next to the
`_internal` folder). Requires Windows 10 or 11, 64-bit.

## Running from source

Requirements: Python 3.10 or newer, numpy, scipy, PySide6 (interface) and pytest (tests). The
native chip emulator ships prebuilt as `mdconv/chips/native/mdchips.dll` on Windows; elsewhere it
is compiled with gcc on first use.

```
pip install numpy scipy PySide6 pytest
python -m mdconv.gui          # interface
python -m mdconv.cli --help   # command line
```

### Command line

```
python -m mdconv.cli analyze song.mid bank.sf2 --json report.json
python -m mdconv.cli render-song song.mid bank.sf2 original.wav
python -m mdconv.cli fm-fit song.mid bank.sf2 out_folder [--budget quick|normal|thorough]
python -m mdconv.cli export song.mid bank.sf2 out_folder --library patch_library [--format xgm,vgm] [--wav]
```

Use `gm` instead of `bank.sf2` for a MIDI without a SoundFont (default General MIDI bank).

### Tests

```
python -m pytest
```

The tests build synthetic MIDI and SF2 files with known content (`tests/fixtures/builders.py`).

### Windows executable

```
packaging\build_windows.bat
```

Installs numpy, scipy, PySide6 and PyInstaller, runs the tests and builds
`dist\MegaDriveAudioConverter\MegaDriveAudioConverter.exe` plus the release zip
`dist\MegaDriveAudioConverter-windows-x64.zip`. Publish the zip (not the bare .exe) in a GitHub
Release.

## Using the output in SGDK

```
XGM song001 "music/song001.xgm" -1        // .res
```

```c
XGM_setForceDelayDMA(TRUE);   // once, at startup
XGM_startPlay(song001);
XGM_setLoopNumber(-1);
```

The music only uses the PCM channels checked in the arrangement (default CH1 and CH2) and the
sample ids 1–63; the game's own effects can use the other channels and ids from 64 on. Chip effects
come out as `.c` files with `name_play()`, `name_update()` (once per frame) and `name_stop()`.

## Languages

The interface text lives in the code in Portuguese and goes through `tr()` (`mdconv/i18n.py`); the
English catalog is `mdconv/i18n_en.py`. The log, the arrangement report, the exported `.txt`
summaries and error messages from the converter are still in Portuguese. To add a translation,
add the exact Portuguese sentence as a key in the catalog; `tests/unit/test_i18n.py` checks that
every interface text is translated and that placeholders match.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) (in Portuguese).

## Credits and licenses

- CPS1 extraction based on VGMTrans — zlib license (`mdconv/extract/LICENSE-VGMTrans.txt`).
- Nuked-OPN2 © Alexey Khokholov (Nuke.YKT) — GNU LGPL 2.1 or later, shipped as a separate library
  with its source code (`mdconv/chips/native/nuked_opn2`).
- Sound board memory maps and wiring (Ghosts 'n Goblins, Nemesis, Neo Geo) checked against the MAME
  drivers, used as documentation. Moon Patrol: disassembly from computerarcheology.com.
- TFI format: TFM Music Maker (Shiru). XGM format and driver: SGDK (Stephane Dallongeville). .fur
  format: Furnace (tildearrow). GBA MIDI + SF2 conventions: GBA Mus Ripper.
- Project license: GPLv3 proposed, pending confirmation.
