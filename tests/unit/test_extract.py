"""Aba Extração: extração de GBA (motor MP2k) com uma ROM sintética e o pacote embutido."""
import struct

from mdconv import extract
from mdconv.extract import gba
from mdconv.io_midi.song_builder import load_song
from mdconv.io_sf2.sf2 import read_sf2

B = 0x08000000


def _rom(tmp_path):
    rom = bytearray(0x4000)
    sample, vg, track, header, table = 0x1000, 0x2000, 0x2800, 0x3000, 0x3100
    # amostra DirectSound: laço, 13379 Hz, 64 amostras de serrote
    rom[sample:sample + 16] = struct.pack("<IIII", 0x40000000, 13379 * 1024, 0, 64)
    rom[sample + 16:sample + 80] = bytes((i * 4) & 0xFF for i in range(64))
    # voicegroup: programa 0 = DirectSound, programa 1 = quadrada 50%
    rom[vg:vg + 12] = bytes([0x00, 60, 0, 0]) + struct.pack("<I", B + sample) + bytes([255, 0, 255, 165])
    rom[vg + 12:vg + 24] = bytes([0x01, 60, 0, 0]) + struct.pack("<I", 2) + bytes([0, 0, 15, 0])
    # faixa: TEMPO 60 (120 BPM), VOICE 0, VOL 100, [laço] N24 C4 v100, W24, N24 (running) E4, W24, GOTO laço
    loop = track + 6
    seq = bytes([0xBB, 60, 0xBD, 0, 0xBE, 100,
                 0xE7, 60, 100, 0x98,
                 0xE7, 64, 0x98,
                 0xB2]) + struct.pack("<I", B + loop)
    rom[track:track + len(seq)] = seq
    # cabeçalho: 1 faixa, voicegroup, ponteiro da faixa
    rom[header:header + 12] = bytes([1, 0, 0, 0]) + struct.pack("<II", B + vg, B + track)
    for i in range(6):                     # tabela com 6 entradas iguais
        rom[table + 8 * i: table + 8 * i + 8] = struct.pack("<IHH", B + header, 0, 0)
    p = tmp_path / "teste.gba"
    p.write_bytes(bytes(rom))
    return p, table


def test_gba_song_table_and_sequence(tmp_path):
    p, table = _rom(tmp_path)
    assert extract.is_gba(str(p))
    g = extract.open_rom(str(p))
    assert extract.platform(g) == "GBA"
    assert g.table == table and g.count == 6
    song = g.song(0)
    notes = [e for e in song.tracks[0] if e[2] == "on"]
    assert [n[3] for n in notes] == [60, 64]          # running status repete o comando de nota
    assert (song.loop_start, song.loop_end) == (0, 48)


def test_gba_export_midi_and_sf2(tmp_path):
    p, _ = _rom(tmp_path)
    g = extract.open_rom(str(p))
    base = extract.run_export(g, str(tmp_path / "out"), song_ids={0})
    o = extract.outputs(base)
    assert [x.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for x in o.midis] == ["song000.mid"]
    assert o.library is None and o.sf2
    s = load_song(o.midis[0])
    assert [n.key for n in s.notes] == [60, 64]
    assert s.notes[0].velocity == int((100 * 127) ** 0.5)  # curva do GBA Mus Ripper
    sf = read_sf2(o.sf2)
    names = {(p.bank, p.program): p.name for p in sf.presets}
    assert names[(0, 0)].startswith("Type 0 @0x")
    assert any(sm.name.startswith("Sample @0x") for sm in sf.samples)


def test_curve_matches_ripper():
    assert gba._curve(120) == 123 and gba._curve(60) == 87 and gba._curve(127) == 127
