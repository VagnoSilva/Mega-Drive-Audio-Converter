"""Fase 2: sampler de referência e extração de características (sinais com resposta conhecida)."""
import numpy as np
import pytest

from mdconv.analysis.features import analyze_note, measure_envelope, midi_to_hz
from mdconv.io_midi.smf import parse_smf
from mdconv.io_midi.song_builder import build_song
from mdconv.io_sf2.resolve import resolve_voices
from mdconv.io_sf2.sf2 import GEN_ID as G
from mdconv.io_sf2.sf2 import parse_sf2
from mdconv.reference.sampler import RenderOptions, render_song, render_voice
from tests.fixtures.builders import (bend, cc, gen, meta_text, midi_file, off, on, pc, sf2_file,
                                     track_bytes)

SR = 44100
SRATE = 22050


def tc(seconds):          # segundos → timecents
    return int(round(1200 * np.log2(seconds)))


def sine_sample(freq=440.0, periods=200):
    n = int(round(SRATE / freq * periods))
    f = periods * SRATE / n                       # frequência exata com períodos inteiros
    x = np.sin(2 * np.pi * f * np.arange(n) / SRATE)
    return (x * 20000).astype(np.int16), n


def make_sf(inst_gens, data=None, loop=True, root=69):
    if data is None:
        data, n = sine_sample()
    else:
        n = len(data)
    s = {"name": "S", "data": data, "rate": SRATE, "root": root}
    if loop:
        s["loop"] = (0, n)
    sf = parse_sf2(sf2_file([s], [{"name": "I", "zones": [inst_gens + [gen(G["sampleID"], 0)]]}],
                            [{"name": "P", "bank": 0, "program": 0, "zones": [[gen(G["instrument"], 0)]]}]))
    return sf


def voice(sf, key=69, vel=127):
    return resolve_voices(sf, sf.presets[0], key, vel)[0]


# ---------------------------------------------------------------- reamostragem e loop
def test_pitch_follows_key():
    sf = make_sf([gen(G["sampleModes"], 1)])
    x = render_voice(voice(sf, 81), sf, 1.0, SR)
    f = analyze_note(x, SR, midi_to_hz(81), 1.0)
    assert f.f0_measured_hz == pytest.approx(880, rel=0.002)
    assert abs(f.detune_cents) < 3


def test_continuous_loop_has_no_gap_or_click():
    sf = make_sf([gen(G["sampleModes"], 1)])
    x = render_voice(voice(sf), sf, 2.0, SR)
    body = x[int(0.1 * SR): int(1.9 * SR)]
    frames = body[: len(body) // 441 * 441].reshape(-1, 441)
    rms = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)))
    assert np.ptp(rms) < 0.2
    # sem degraus: a maior variação entre amostras não passa da de um seno puro
    assert np.max(np.abs(np.diff(body))) < 2 * np.pi * 440 / SR * 0.62


def test_unlooped_sample_stops_at_its_end():
    data, n = sine_sample(periods=20)              # ~45 ms
    sf = make_sf([gen(G["sampleModes"], 0)], data=data, loop=False)
    x = render_voice(voice(sf), sf, 1.0, SR)
    assert len(x) == pytest.approx(n * SR / SRATE, abs=3)


def test_loop_until_release_plays_tail_after_note_off():
    data, n = sine_sample(periods=100)
    sf = make_sf([gen(G["sampleModes"], 3), gen(G["releaseVolEnv"], tc(5.0))], data=data, loop=True)
    v = voice(sf)
    v.sample.loop_start, v.sample.loop_end = v.sample.start + 0, v.sample.start + n // 2
    x = render_voice(v, sf, 0.5, SR)
    # Ao soltar, a leitura continua de onde estava dentro do loop até o fim da amostra.
    pos_release = (0.5 * SRATE) % (n // 2)
    assert len(x) == pytest.approx(0.5 * SR + (n - pos_release) * SR / SRATE, abs=4)


# ---------------------------------------------------------------- envelope
def test_attack_decay_sustain_release_match_generators():
    A, D, R = 0.2, 1.0, 0.5
    sf = make_sf([gen(G["sampleModes"], 1), gen(G["attackVolEnv"], tc(A)), gen(G["decayVolEnv"], tc(D)),
                  gen(G["sustainVolEnv"], 200), gen(G["releaseVolEnv"], tc(R))])
    x = render_voice(voice(sf), sf, 1.5, SR)
    env, _ = measure_envelope(x, SR, 1.5)
    assert env.attack_s == pytest.approx(0.8 * A, abs=0.012)       # 10%→90% de rampa linear
    assert env.sustain_db == pytest.approx(-20, abs=0.6)
    assert env.decay_s == pytest.approx(0.2 * D * 0.95, abs=0.04)   # até 1 dB do patamar
    assert env.looped_like
    assert env.release_s == pytest.approx(0.6 * R, abs=0.02)        # 60 dB de 100 dB em R


def test_initial_attenuation_in_centibels():
    sf0 = make_sf([gen(G["sampleModes"], 1)])
    sf6 = make_sf([gen(G["sampleModes"], 1), gen(G["initialAttenuation"], 60)])
    a = np.max(np.abs(render_voice(voice(sf0), sf0, 0.3, SR)))
    b = np.max(np.abs(render_voice(voice(sf6), sf6, 0.3, SR)))
    assert 20 * np.log10(b / a) == pytest.approx(-6.0, abs=0.05)


# ---------------------------------------------------------------- características
def test_harmonics_of_sawtooth():
    f0 = 220.0
    t = np.arange(int(0.8 * SR)) / SR
    x = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, 30)).astype(np.float32) * 0.2
    f = analyze_note(x, SR, f0, 0.8)
    expected = [-20 * np.log10(k) for k in range(1, 9)]
    got = np.array(f.harmonics_mean_db[:8]) - f.harmonics_mean_db[0]
    np.testing.assert_allclose(got, expected, atol=1.0)
    assert f.noise_ratio < 0.02
    assert f.centroid_mean > 2


def test_noise_vs_tone():
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.2, int(0.5 * SR)).astype(np.float32)
    tone = (0.3 * np.sin(2 * np.pi * 300 * np.arange(int(0.5 * SR)) / SR)).astype(np.float32)
    assert analyze_note(noise, SR, 300, 0.5).noise_ratio > 0.7
    assert analyze_note(tone, SR, 300, 0.5).noise_ratio < 0.02


def test_vibrato_rate_and_depth():
    t = np.arange(int(2.0 * SR)) / SR
    cents = 30 * np.sin(2 * np.pi * 5.0 * t)
    phase = 2 * np.pi * np.cumsum(440 * 2 ** (cents / 1200)) / SR
    f = analyze_note((0.3 * np.sin(phase)).astype(np.float32), SR, 440, 2.0)
    assert f.vibrato_rate_hz == pytest.approx(5.0, abs=0.6)
    assert f.vibrato_depth_cents == pytest.approx(30, abs=6)


def test_no_vibrato_on_steady_tone():
    t = np.arange(int(1.5 * SR)) / SR
    f = analyze_note((0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), SR, 440, 1.5)
    assert f.vibrato_rate_hz is None


# ---------------------------------------------------------------- música
def song_with(events):
    return build_song(parse_smf(midi_file([track_bytes(events)], ppq=24)))


def test_render_song_volume_laws_and_silent_notes():
    sf = make_sf([gen(G["sampleModes"], 1)])
    evs = [(0, meta_text(0x06, "Converted by SequenceRipper 2.0")), (0, pc(0, 0)), (0, cc(0, 7, 127)),
           (0, on(0, 69, 127)), (24, off(0, 69)), (48, on(0, 69, 64)), (72, off(0, 69)),
           (0, pc(1, 9)), (0, on(1, 60, 100)), (10, off(1, 60))]               # programa inexistente
    song = song_with(evs)
    res = {}
    for law in ("m4a", "gm"):
        a, rep = render_song(song, sf, RenderOptions(law=law, vibrato=False), normalize=False)
        q = 0.5  # 24 ticks a 120 BPM
        r1 = np.sqrt((a[int(0.1 * SR):int(0.4 * SR), 0] ** 2).mean())
        r2 = np.sqrt((a[int(1.1 * SR):int(1.4 * SR), 0] ** 2).mean())
        res[law] = 20 * np.log10(r2 / r1)
        assert rep.notes_silent == 1 and rep.notes_rendered == 2
    assert res["m4a"] == pytest.approx(20 * np.log10(64 / 127), abs=0.2)
    assert res["gm"] == pytest.approx(40 * np.log10(64 / 127), abs=0.2)


def test_pitch_bend_uses_range():
    sf = make_sf([gen(G["sampleModes"], 1)])
    song = song_with([(0, bend(0, 8191)), (0, on(0, 69, 127)), (48, off(0, 69))])
    a, _ = render_song(song, sf, RenderOptions(vibrato=False))
    f = analyze_note(a[: int(0.9 * SR), 0], SR, midi_to_hz(71), 0.9)
    assert abs(f.detune_cents) < 4                      # +2 semitons = range padrão


def test_m4a_vibrato_from_cc1_and_nrpn():
    sf = make_sf([gen(G["sampleModes"], 1)])
    lfos = 22                                            # → 22·60/256 = 5.16 Hz
    song = song_with([(0, cc(0, 99, 1)), (0, cc(0, 98, 8)), (0, cc(0, 6, lfos)), (0, cc(0, 1, 60)),
                      (0, on(0, 69, 127)), (96, off(0, 69))])
    a, _ = render_song(song, sf, RenderOptions(vibrato=True))
    f = analyze_note(a[: int(1.9 * SR), 0], SR, 440, 1.9)
    assert f.vibrato_rate_hz == pytest.approx(lfos * 60 / 256, abs=0.6)
    assert f.vibrato_depth_cents == pytest.approx(60 * 100 / 256 / np.sqrt(3) * np.sqrt(2), rel=0.35)


def test_solo_renders_only_selected_channels():
    sf = make_sf([gen(G["sampleModes"], 1)])
    song = song_with([(0, on(0, 69, 127)), (24, off(0, 69)), (48, on(1, 69, 127)), (72, off(1, 69))])
    a, rep = render_song(song, sf, RenderOptions(vibrato=False), only_channels={1}, normalize=False)
    assert rep.notes_rendered == 1
    assert np.abs(a[int(0.1 * SR):int(0.4 * SR)]).max() < 1e-6


def test_low_short_bass_pitch_is_accurate():
    f0 = midi_to_hz(39)                                    # Ré#2 ≈ 77.8 Hz, nota curta
    t = np.arange(int(0.3 * SR)) / SR
    x = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, 12)) * np.exp(-t * 6)
    f = analyze_note(x.astype(np.float32) * 0.3, SR, f0, 0.3)
    assert abs(f.detune_cents) < 3


def test_detects_mistuned_sample():
    f0 = 440 * 2 ** (35 / 1200)                            # 35 cents acima
    t = np.arange(int(0.5 * SR)) / SR
    f = analyze_note((0.3 * np.sin(2 * np.pi * f0 * t)).astype(np.float32), SR, 440, 0.5)
    assert f.detune_cents == pytest.approx(35, abs=2)


def test_percussion_skips_pitch():
    rng = np.random.default_rng(2)
    t = np.arange(int(0.3 * SR)) / SR
    x = (rng.normal(0, 0.3, len(t)) * np.exp(-t * 20)).astype(np.float32)
    f = analyze_note(x, SR, 200, 0.3, pitched=False)
    assert f.f0_measured_hz is None and f.vibrato_rate_hz is None
    assert f.spectral_flatness > 0.3 and f.centroid_hz > 3000
