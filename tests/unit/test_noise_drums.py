"""Chimbais e pratos no ruído do PSG (opção ``noise_metals``)."""
import numpy as np
import pytest

from mdconv.arrange.plan import make_plan
from mdconv.convert import convert
from mdconv.export.furnace import read_fur, write_fur
from mdconv.io_midi.smf import parse_smf
from mdconv.io_midi.song_builder import build_song
from mdconv.io_sf2.sf2 import GEN_ID as G
from mdconv.io_sf2.sf2 import parse_sf2
from mdconv.pcm.kit import NOISE_FULL_DB, _noise_envelope, build_kit, is_metal, piece_features
from mdconv.render.stream_render import render_stream
from tests.fixtures.builders import gen, meta_text, midi_file, off, on, pc, sf2_file, track_bytes

SR = 22050
RIP = (0, meta_text(0x06, "Converted by SequenceRipper 2.0"))


def _kick(n=6000):
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * 60 * t) * np.exp(-t * 12)


def _hat(n=4000, seed=1):
    t = np.arange(n) / SR
    x = np.random.default_rng(seed).standard_normal(n)
    for _ in range(3):                                   # passa-altas simples (diferenças)
        x = np.diff(x, prepend=0.0)
    return x / np.abs(x).max() * np.exp(-t * 25)


def _snare(n=5000, seed=2):
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 20)
    nz = np.random.default_rng(seed).standard_normal(n) * 0.3 * np.exp(-t * 15)
    return body + nz


def test_features_separate_metals_from_kick_and_snare():
    assert is_metal(piece_features(_hat(), SR))
    assert not is_metal(piece_features(_kick(), SR))
    assert not is_metal(piece_features(_snare(), SR))


def test_noise_envelope_matches_the_pcm_level():
    rate = 14000.0
    y = np.random.default_rng(0).standard_normal(int(rate)) * 0.1     # RMS 0,1 (−20 dB)
    att = _noise_envelope(y, rate)
    assert len(att) == pytest.approx(60, abs=1)
    assert np.median(att) == pytest.approx(NOISE_FULL_DB + 20, abs=0.3)


@pytest.fixture
def kit_song():
    samples = [{"name": "Bumbo", "data": (_kick() * 20000).astype(np.int16), "rate": SR, "root": 60},
               {"name": "Chimbal", "data": (_hat() * 20000).astype(np.int16), "rate": SR, "root": 60}]
    insts = [{"name": "Kit", "global": [gen(G["scaleTuning"], 0)], "zones": [[gen(G["keyRange"], (36, 36)), gen(G["overridingRootKey"], 36),
                                        gen(G["sampleID"], 0)],
                                       [gen(G["keyRange"], (42, 42)), gen(G["overridingRootKey"], 42),
                                        gen(G["sampleID"], 1)]]}]
    presets = [{"name": "Bateria", "bank": 0, "program": 127, "zones": [[gen(G["instrument"], 0)]]}]
    sf = parse_sf2(sf2_file(samples, insts, presets), path="k.sf2")
    ev = [RIP, (0, pc(0, 127))]
    for i in range(16):                    # 16 colcheias de chimbal (velocity crescente), bumbo a cada 4
        t = 12 * i
        if i % 4 == 0:
            ev += [(t, on(0, 36, 110)), (t + 6, off(0, 36))]
        ev += [(t, on(0, 42, 60 + 4 * i)), (t + 6, off(0, 42))]
    song = build_song(parse_smf(midi_file([track_bytes(ev)], ppq=24)))
    return song, sf


def test_build_kit_sends_only_the_hat_to_noise(kit_song):
    song, sf = kit_song
    parts = [p.key for p in make_plan(song, sf).parts if p.kind.startswith("kit")]
    assert parts
    kit = build_kit(song, sf, parts, noise_metals=True)
    assert set(kit.noise) == {(0, 127, 42)}
    assert set(kit.ids) == {(0, 127, 36)} and len(kit.samples) == 1
    off_kit = build_kit(song, sf, parts, noise_metals=False)
    assert not off_kit.noise and len(off_kit.samples) == 2
    # o bumbo fica idêntico com ou sem o modo de ruído (mesma escala do kit)
    np.testing.assert_array_equal(kit.samples[kit.ids[(0, 127, 36)]],
                                  off_kit.samples[off_kit.ids[(0, 127, 36)]])


def test_driver_plays_hats_on_noise_channel(kit_song, tmp_path):
    song, sf = kit_song
    c = convert(song, sf, None, noise_metals=True, balance="manual")
    st = c.stream.stats
    assert st["noise"] == 16 and st["pcm"] == 4
    writes = [w[1] for fr in c.stream.frames for w in fr if w[0] == "psg"]
    assert writes.count(0xE4) == 16                     # ruído branco N/512 a cada golpe
    vols = [b & 15 for b in writes if (b & 0xF0) == 0xF0]
    assert min(vols) < 15 and 15 in vols                # toca e depois silencia
    # a velocity muda o volume: o último golpe (mais forte) é mais alto que o primeiro
    starts = [i for i, fr in enumerate(c.stream.frames) if any(w == ("psg", 0xE4) for w in fr)]
    first = [w[1] & 15 for w in c.stream.frames[starts[0]] if w[0] == "psg" and (w[1] & 0xF0) == 0xF0]
    last = [w[1] & 15 for w in c.stream.frames[starts[-1]] if w[0] == "psg" and (w[1] & 0xF0) == 0xF0]
    assert last[0] < first[0]
    # a prévia separa o ruído dos canais de tom
    y_noise, _ = render_stream(c.stream, normalize=False, solo={"noise"}, fm_engine="proprio")
    y_psg, _ = render_stream(c.stream, normalize=False, solo={"psg"}, fm_engine="proprio")
    rms = lambda y: float(np.sqrt(np.mean(y[4410:] ** 2)))   # sem o transitório do DAC no início
    assert rms(y_noise) > 30 * rms(y_psg)
    # Furnace: as 16 notas de ruído no canal "Ruído"
    rep = write_fur(c, tmp_path / "k.fur", "k", "", "")
    chk = read_fur((tmp_path / "k.fur").read_bytes())
    assert chk.notes == rep.notes - rep.merged_rows
    assert "Ruído" in rep.channels


def test_noise_off_keeps_previous_behaviour(kit_song):
    song, sf = kit_song
    c = convert(song, sf, None, noise_metals=False, balance="manual")
    assert c.stream.stats["noise"] == 0 and c.stream.stats["pcm"] == 20
    assert not any(w[0] == "psg" and (w[1] & 0xF0) == 0xE0 for fr in c.stream.frames for w in fr)


def test_approved_patch_found_when_sf2_lacks_the_midi_bank(tmp_path):
    """O MIDI pede o banco 10, o SF2 só tem o instrumento no banco 0 (o programa acha pelo
    número do programa). O patch aprovado para 10:9 tem de valer — antes era gravado e
    ignorado ("continua sem patch aprovado")."""
    from mdconv.convert import approved_entries
    from mdconv.fm.patch import Operator, Patch
    from mdconv.library.patch_library import LibraryEntry, PatchLibrary
    samples = [{"name": "S", "data": (np.sin(np.arange(2000) * 0.1) * 9000).astype(np.int16),
                "rate": SR, "root": 60, "loop": (0, 2000)}]
    insts = [{"name": "I", "zones": [[gen(G["sampleModes"], 1), gen(G["sampleID"], 0)]]}]
    presets = [{"name": "Glockenspiel", "bank": 0, "program": 9, "zones": [[gen(G["instrument"], 0)]]}]
    sf = parse_sf2(sf2_file(samples, insts, presets), path="g.sf2")
    lib = PatchLibrary(tmp_path / "lib")
    lib.add(LibraryEntry(id="", name="Glockenspiel", approved=True,
                         patch=Patch(alg=7, fb=0, ops=[Operator() for _ in range(4)]),
                         source={"bank": 10, "program": 9, "preset": "Glockenspiel",
                                 "approved_at": "2026-11-11T23:00:00"}))
    lib.add(LibraryEntry(id="", name="Outro", approved=True,
                         patch=Patch(alg=7, fb=0, ops=[Operator() for _ in range(4)]),
                         source={"bank": 10, "program": 9, "preset": "Celesta",
                                 "approved_at": "2026-11-11T23:01:00"}))
    got = approved_entries(tmp_path / "lib", sf)
    assert (10, 9) in got and got[(10, 9)].source["preset"] == "Glockenspiel"
