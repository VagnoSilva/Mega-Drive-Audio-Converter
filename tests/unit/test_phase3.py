"""Fase 3: emulador YM2612/PSG, modelo de patch, métrica e otimizador."""
import numpy as np
import pytest

from mdconv.analysis.features import analyze_note, measure_envelope
from mdconv.chips.emulator import NTSC_FM_CLOCK, NTSC_PSG_CLOCK, MegaDriveSound
from mdconv.fm.metrics import describe, distance
from mdconv.fm.patch import (Operator, Patch, block_fnum_to_freq, freq_to_block_fnum, midi_to_hz,
                             render_note)

SR = 44100


def rn(*a, **k):
    """Render no modo integrado (sem ladder): testes da aritmética do chip."""
    return render_note(*a, chip=MegaDriveSound(discrete_ym2612=False), **k)


def sine(op4=None, **kw):
    o4 = Operator(mul=1, tl=0, ar=31, rr=10)
    if op4:
        for k, v in op4.items():
            setattr(o4, k, v)
    return Patch(alg=7, ops=[Operator(tl=127), Operator(tl=127), Operator(tl=127), o4], **kw)


def level_db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.asarray(x) ** 2)) + 1e-12)


# ---------------------------------------------------------------- frequência
@pytest.mark.parametrize("key", [24, 45, 57, 69, 81, 96, 108])
def test_pitch_within_one_cent(key):
    hz = midi_to_hz(key)
    b, f = freq_to_block_fnum(hz, NTSC_FM_CLOCK)
    assert abs(1200 * np.log2(block_fnum_to_freq(b, f, NTSC_FM_CLOCK) / hz)) < 1.0
    x = rn(sine(), hz, 0.5)
    feat = analyze_note(x, SR, hz, 0.5)
    assert abs(feat.detune_cents) < 1.5


def test_mul_and_half_mul():
    for mul, ratio in ((2, 2.0), (3, 3.0), (0, 0.5)):
        x = rn(sine({"mul": mul}), 220, 0.5)
        f = analyze_note(x, SR, 220 * ratio, 0.5)
        assert abs(f.detune_cents) < 1.5


def test_detune_is_small_and_symmetric():
    up = analyze_note(rn(sine({"dt": 3}), 880, 0.5), SR, 880, 0.5).detune_cents
    dn = analyze_note(rn(sine({"dt": -3}), 880, 0.5), SR, 880, 0.5).detune_cents
    assert 0.5 < up < 15 and -15 < dn < -0.5 and abs(up + dn) < 1.0


# ---------------------------------------------------------------- níveis e envelope
def test_tl_step_is_075_db():
    a = rn(sine(), 440, 0.3)[2000:10000]
    b = rn(sine({"tl": 16}), 440, 0.3)[2000:10000]
    assert level_db(b) - level_db(a) == pytest.approx(-12.0, abs=0.15)


@pytest.mark.parametrize("sl", [2, 5, 10])
def test_sustain_level_3db_steps(sl):
    full = rn(sine(), 440, 1.0)[int(0.7 * SR):int(0.95 * SR)]
    x = rn(sine({"d1r": 20, "sl": sl, "d2r": 0}), 440, 1.0)[int(0.7 * SR):int(0.95 * SR)]
    # o DEC para no primeiro passo ≥ SL, então pode passar até 1 incremento (≤ 0,75 dB)
    assert level_db(x) - level_db(full) == pytest.approx(-3.0 * sl - 0.35, abs=0.45)


def test_attack_rate_order_and_instant_ar31():
    times = [measure_envelope(rn(sine({"ar": ar}), 440, 0.8), SR, 0.8)[0].attack_s
             for ar in (31, 20, 14, 10)]
    assert times[0] < 0.003
    assert times == sorted(times) and times[-1] > 0.05


def _release_s(x, off_s, drop_db=40):
    x = np.concatenate([x, np.zeros(SR // 2, np.float32)])
    hop = SR // 200
    n = len(x) // hop
    db = 20 * np.log10(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-9)
    i = int(off_s * 200) - 1
    after = np.flatnonzero(db[i:] <= db[i] - drop_db)
    return after[0] * hop / SR


def test_release_rate_order():
    rels = [_release_s(rn(sine({"rr": rr}), 440, 0.3), 0.3) for rr in (15, 10, 6, 3)]
    assert rels == sorted(rels) and rels[0] < 0.02 and rels[-1] > 0.3


def test_key_scaling_speeds_up_high_notes():
    def decay(ks, key):
        x = rn(sine({"d1r": 8, "sl": 15, "ks": ks}), midi_to_hz(key), 1.0)
        return measure_envelope(x, SR, 1.0)[0].decay_rate_db_s
    # KS=0 ainda escala um pouco (keycode ÷ 8, como no chip real); KS=3 escala muito mais
    ratio0 = decay(0, 84) / decay(0, 48)
    ratio3 = decay(3, 84) / decay(3, 48)
    assert 1.0 <= ratio0 < 1.4 and ratio3 > 2.0


# ---------------------------------------------------------------- algoritmos e feedback
def test_feedback_adds_harmonics():
    pure = analyze_note(rn(sine(), 220, 0.5), SR, 220, 0.5)
    p = sine()
    p.ops = [Operator(mul=1, tl=0, ar=31, rr=10), Operator(tl=127), Operator(tl=127), Operator(tl=127)]
    p.fb = 6
    fb = analyze_note(rn(p, 220, 0.5), SR, 220, 0.5)
    assert pure.harmonics_mean_db[1] - pure.harmonics_mean_db[0] < -50
    assert fb.harmonics_mean_db[1] - fb.harmonics_mean_db[0] > -20


def test_alg7_sums_four_carriers():
    # TL 16 (-12 dB) cada: a soma das 4 não estoura os 14 bits do canal
    ops = [Operator(mul=m, tl=16, ar=31, rr=10) for m in (1, 2, 3, 4)]
    f = analyze_note(rn(Patch(alg=7, ops=ops), 220, 0.5), SR, 220, 0.5)
    h = np.array(f.harmonics_mean_db[:4]) - f.harmonics_mean_db[0]
    assert np.all(np.abs(h) < 1.0)


def test_modulation_index_grows_with_modulator_level():
    def bright(tl):
        # alg 4: OP1 modula OP2 (portadora); OP3/OP4 mudos
        ops = [Operator(mul=1, tl=tl, ar=31), Operator(mul=1, tl=0, ar=31, rr=10),
               Operator(tl=127), Operator(tl=127)]
        return analyze_note(rn(Patch(alg=4, ops=ops), 220, 0.5), SR, 220, 0.5).centroid_mean
    assert bright(10) > bright(30) > bright(60)


def test_octave_field_shifts_base_frequency():
    p = sine(octave=-1)
    assert abs(analyze_note(rn(p, 440, 0.5), SR, 220, 0.5).detune_cents) < 1.5


def test_major_triad_in_one_channel():
    ops = [Operator(tl=127), Operator(mul=4, tl=14, ar=31), Operator(mul=5, tl=14, ar=31),
           Operator(mul=6, tl=14, ar=31)]
    x = rn(Patch(alg=5, ops=ops, octave=-2), 440, 0.5)
    sp = np.abs(np.fft.rfft(x[:16384] * np.hanning(16384)))
    fr = np.fft.rfftfreq(16384, 1 / SR)
    peaks = sorted(fr[np.argsort(sp)[-3:]])
    np.testing.assert_allclose(peaks, [440, 550, 660], atol=3)


# ---------------------------------------------------------------- LFO, pan, DAC
def _chip_note(patch, hz=440, n_s=1.5, pan="C", lfo=None, discrete=False):
    m = MegaDriveSound(discrete_ym2612=discrete)
    if lfo is not None:
        m.write_fm(0, 0x22, 0x08 | lfo)
    for port, reg, val in patch.channel_writes(0, pan):
        m.write_fm(port, reg, val)
    b, f = freq_to_block_fnum(hz, NTSC_FM_CLOCK)
    m.write_fm(0, 0xA4, (b << 3) | (f >> 8))
    m.write_fm(0, 0xA0, f & 255)
    m.write_fm(0, 0x28, 0xF0)
    return m, m.render(int(n_s * m.rate))


def test_lfo_pm_vibrato():
    m, x = _chip_note(sine(fms=7), lfo=3)            # 6,02 Hz, ±80 cents
    from mdconv.chips.emulator import resample
    y = resample(x[:, 0], m.rate, SR)
    f = analyze_note(y, SR, 440, 1.5)
    assert f.vibrato_rate_hz == pytest.approx(6.0, abs=0.7)
    assert f.vibrato_depth_cents == pytest.approx(80, rel=0.3)


def test_lfo_am_tremolo_depth():
    m, x = _chip_note(sine({"am": 1}, ams=3), lfo=3)
    from mdconv.chips.emulator import resample
    env, db = measure_envelope(resample(x[:, 0], m.rate, SR), SR, 1.5)
    body = db[40:280]
    assert np.ptp(body) == pytest.approx(11.8, abs=2.0)


def test_pan_left_only():
    _, x = _chip_note(sine(), pan="L", n_s=0.2)
    assert np.abs(x[:, 1]).max() == 0 and np.abs(x[:, 0]).max() > 0.5


def test_ladder_effect_offsets_only_on_discrete_chip():
    _, integ = _chip_note(sine(), pan="L", n_s=0.2)
    _, disc = _chip_note(sine(), pan="L", n_s=0.2, discrete=True)
    assert np.abs(integ[:, 1]).max() == 0                    # lado mudo: silêncio total
    assert np.abs(disc[:, 1]).max() > 0                      # discreto: deslocamento do ladder
    assert np.abs(disc[:, 1]).max() <= 6 * 4 / 256 + 1e-6    # no máximo ±4 passos por canal


def test_dac_replaces_channel_6():
    m = MegaDriveSound(discrete_ym2612=False)
    m.write_fm(0, 0x2B, 0x80)
    m.write_fm(0, 0x2A, 0xC0)
    x = m.render(100)
    assert np.allclose(x[:, 0], (0xC0 - 128) * 2 / 256)


# ---------------------------------------------------------------- PSG
def _psg(writes, n_s=0.5):
    m = MegaDriveSound(discrete_ym2612=False)
    for w in writes:
        m.write_psg(w)
    from mdconv.chips.emulator import resample
    return resample(m.render(int(n_s * m.rate))[:, 0], m.rate, SR)


def test_psg_tone_frequency_and_volume_steps():
    n = 254                                            # clock/(32·254) ≈ 440,4 Hz
    hz = NTSC_PSG_CLOCK / (32 * n)
    x = _psg([0x80 | (n & 15), n >> 4, 0x90 | 0])
    f = analyze_note(x, SR, hz, 0.5)
    assert abs(f.detune_cents) < 2
    assert f.harmonics_mean_db[1] - f.harmonics_mean_db[0] < -30      # quadrada: sem pares
    assert f.harmonics_mean_db[2] - f.harmonics_mean_db[0] == pytest.approx(-9.5, abs=1.0)
    y = _psg([0x80 | (n & 15), n >> 4, 0x90 | 3])
    assert level_db(y[1000:]) - level_db(x[1000:]) == pytest.approx(-6.0, abs=0.2)


def test_psg_noise_white_vs_periodic():
    white = _psg([0xE4, 0xF0])
    periodic = _psg([0xE0, 0xF0])
    fw = analyze_note(white, SR, 200, 0.5, pitched=False)
    fp = analyze_note(periodic, SR, 200, 0.5, pitched=False)
    assert fw.spectral_flatness > 3 * fp.spectral_flatness


def test_psg_silent_at_volume_15():
    assert np.abs(_psg([0x80 | 4, 0x10, 0x9F])).max() < 1e-6


# ---------------------------------------------------------------- patch e registradores
def test_register_encoding():
    p = Patch(alg=4, fb=5, ams=2, fms=3, ops=[Operator(mul=3, dt=-2, tl=40, ks=2, ar=25, d1r=7,
                                                         d2r=4, sl=9, rr=6, am=1)] + [Operator()] * 3)
    w = {(port, reg): val for port, reg, val in p.channel_writes(4, pan="R")}
    assert w[(1, 0x31)] == (6 << 4) | 3                 # dt -2 → 6
    assert w[(1, 0x41)] == 40 and w[(1, 0x51)] == (2 << 6) | 25
    assert w[(1, 0x61)] == 0x80 | 7 and w[(1, 0x81)] == (9 << 4) | 6
    assert w[(1, 0xB1)] == (5 << 3) | 4 and w[(1, 0xB5)] == 0x40 | (2 << 4) | 3


def test_validate_rejects_out_of_range():
    p = Patch()
    p.ops[0].ar = 32
    with pytest.raises(ValueError):
        p.validate()


def test_patch_json_round_trip():
    p = Patch(alg=3, fb=2, octave=-1, ops=[Operator(mul=i + 1, tl=10 * i) for i in range(4)])
    assert Patch.from_dict(p.to_dict()) == p


# ---------------------------------------------------------------- métrica e otimizador
def test_metric_zero_for_identical_and_gain_invariant():
    x = rn(sine(), 330, 0.4)
    d1 = describe(x, SR, 330)
    d2 = describe(x * 0.1, SR, 330)
    assert distance(d1, d2).total == pytest.approx(0, abs=1e-3)


def test_metric_orders_by_similarity():
    ref = describe(rn(sine(), 330, 0.4), SR, 330)
    near = Patch(alg=7, ops=[Operator(tl=127), Operator(tl=127), Operator(mul=2, tl=30, ar=31, rr=10),
                             Operator(mul=1, tl=0, ar=31, rr=10)])
    far = Patch(alg=0, fb=7, ops=[Operator(mul=7, tl=0, ar=31), Operator(mul=3, tl=0, ar=31),
                                  Operator(mul=5, tl=0, ar=31), Operator(mul=1, tl=0, ar=31, rr=10)])
    dn = distance(ref, describe(rn(near, 330, 0.4), SR, 330)).total
    df = distance(ref, describe(rn(far, 330, 0.4), SR, 330)).total
    assert dn < df


@pytest.mark.slow
def test_optimizer_recovers_a_known_fm_sound():
    """O original é um patch FM conhecido; o otimizador deve chegar perto dele só pelo som."""
    from mdconv.fm.fitter import RefNote, fit_instrument
    target = Patch(alg=4, fb=0, ops=[Operator(mul=2, tl=28, ar=31, d1r=8, sl=6, rr=8),
                                     Operator(mul=1, tl=0, ar=31, d1r=5, sl=3, rr=8),
                                     Operator(tl=127), Operator(tl=127)])
    # mesmo motor do otimizador (YM2612 discreto), para não medir o ladder como "erro"
    refs = [RefNote(hz, 0.4, render_note(target, hz, 0.4, SR), SR) for hz in (220.0, 440.0)]
    res = fit_instrument(refs, n_screen=25, de_iter=12, de_pop=4, seed=1)
    assert res.candidates[0].cost.total < 3.0


# ---------------------------------------------------------------- validação contra o GPGX (local)
from mdconv.chips import reference_gpgx as gpgx  # noqa: E402


@pytest.mark.skipif(not gpgx.available(), reason="MDCONV_GPGX_LIB não definido (referência local)")
@pytest.mark.parametrize("name,patch,hz", [
    ("seno", sine(), 440.0),
    ("decaimento", sine({"d1r": 10, "sl": 15}), 440.0),
    ("release", sine({"rr": 6}), 440.0),
    ("ks3", sine({"d1r": 8, "sl": 15, "ks": 3}), 2093.0),
    ("fb5", Patch(alg=7, fb=5, ops=[Operator(mul=1, tl=0, ar=31, rr=8)] + [Operator(tl=127)] * 3), 220.0),
] + [(f"alg{a}", Patch(alg=a, fb=3, ops=[Operator(mul=2, tl=30, ar=31, d1r=6, sl=4, rr=8),
                                          Operator(mul=1, tl=24, ar=31, rr=8),
                                          Operator(mul=3, tl=34, ar=31, d1r=4, sl=6, rr=8),
                                          Operator(mul=1, tl=8, ar=31, rr=8)]), 220.0) for a in range(8)])
def test_matches_genesis_plus_gx(name, patch, hz):
    from mdconv.chips.emulator import NTSC_FM_CLOCK
    sr = NTSC_FM_CLOCK / 144
    mine = render_note(patch, hz, 0.6, sr_out=None, chip=MegaDriveSound(discrete_ym2612=False))
    ref = render_note(patch, hz, 0.6, sr_out=None, chip=gpgx.GpgxYM2612(gpgx.INTEGRATED))
    d = distance(describe(ref, sr, hz), describe(mine, sr, hz))
    assert d.spectrum < 1.0 and d.envelope < 1.0, d


# ---------------------------------------------------------------- biblioteca
def test_patch_library_round_trip_and_nearest(tmp_path):
    from mdconv.library.patch_library import LibraryEntry, PatchLibrary
    lib = PatchLibrary(tmp_path / "lib")
    a = lib.add(LibraryEntry("", "Rhodes GBA", sine(), approved=True, profile=[0, -10, -20]))
    b = lib.add(LibraryEntry("", "Rhodes GBA", sine(octave=-1), profile=[0, -30, -40]))
    assert a.id == "Rhodes_GBA" and b.id == "Rhodes_GBA_2"
    assert lib.get(a.id).patch == a.patch
    near = lib.nearest([0, -11, -19])
    assert near[0][1].id == a.id
    assert [e.id for _, e in lib.nearest([0, -30, -40], approved_only=True)] == [a.id]
    lib.remove(b.id)
    assert [e.id for e in lib.entries()] == [a.id]
