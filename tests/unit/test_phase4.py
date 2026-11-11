"""Fase 4: plano de distribuição e driver (fluxo de registradores)."""
import numpy as np
import pytest

from mdconv.arrange.plan import detect_echoes, make_plan
from mdconv.convert import fallback_patch
from mdconv.driver.driver import Driver
from mdconv.fm.patch import block_fnum_to_freq, midi_to_hz
from mdconv.io_midi.smf import parse_smf
from mdconv.io_midi.song_builder import build_song
from mdconv.io_sf2.sf2 import GEN_ID as G
from mdconv.io_sf2.sf2 import parse_sf2
from tests.fixtures.builders import cc, gen, meta_text, midi_file, off, on, pc, sf2_file, track_bytes

RIP = (0, meta_text(0x06, "Converted by SequenceRipper 2.0"))


def tone_sf():
    data = (np.sin(np.arange(2000) * 2 * np.pi / 50) * 15000).astype(np.int16)
    s = [{"name": "T", "data": data, "rate": 22050, "root": 69, "loop": (0, 2000)}]
    insts = [{"name": "I", "zones": [[gen(G["sampleModes"], 1), gen(G["sampleID"], 0)]]}]
    presets = [{"name": f"P{p}", "bank": 0, "program": p, "zones": [[gen(G["instrument"], 0)]]}
               for p in range(8)]
    return parse_sf2(sf2_file(s, insts, presets))


def song(events_by_track):
    return build_song(parse_smf(midi_file([track_bytes(e) for e in events_by_track], ppq=24)))


def run(sng, sf, **kw):
    plan = make_plan(sng, sf, use_pcm=False)
    patches = {bp: fallback_patch() for p in plan.parts for bp in p.programs}
    return plan, Driver(sng, plan, patches, vibrato=False, **kw).run()


def replay(stream):
    """Reproduz as escritas e devolve, por quadro, quais canais FM/PSG estão ligados."""
    fm_on = [False] * 6
    psg_vol = [15] * 4
    keyed = []
    freq = {}
    for f, writes in enumerate(stream.frames):
        for w in writes:
            if w[0] == "fm" and w[2] == 0x28:
                c = w[3] & 7
                ch = (c & 3) + (3 if c & 4 else 0)
                fm_on[ch] = bool(w[3] & 0xF0)
                if fm_on[ch]:
                    keyed.append((f, ch, freq.get(ch)))
            elif w[0] == "fm" and (w[2] & 0xFC) in (0xA0, 0xA4):
                ch = (w[2] & 3) + 3 * w[1]
                lo, hi = freq.get(ch, (0, 0))
                freq[ch] = (lo, w[3]) if (w[2] & 0xFC) == 0xA4 else (w[3], hi)
            elif w[0] == "psg" and (w[1] & 0x90) == 0x90:
                psg_vol[(w[1] >> 5) & 3] = w[1] & 15
        yield f, list(fm_on), list(psg_vol), keyed


# ---------------------------------------------------------------- ecos
def test_echo_is_the_later_quieter_copy():
    line = [(t, on(0, k, 100)) for t, k in ((0, 60), (24, 62), (48, 64), (72, 65))] + \
           [(t + 10, off(0, k)) for t, k in ((0, 60), (24, 62), (48, 64), (72, 65))]
    echo = [(t + 12, on(1, k, 100)) for t, k in ((0, 60), (24, 62), (48, 64), (72, 65))] + \
           [(t + 22, off(1, k)) for t, k in ((0, 60), (24, 62), (48, 64), (72, 65))]
    s = song([[RIP, (0, cc(0, 7, 100)), (0, cc(1, 7, 50))] + line + echo])
    parts = {(0, ch): [n for n in s.notes if n.channel == ch] for ch in (0, 1)}
    e = detect_echoes(s, parts, {(0, 0): 100, (0, 1): 50})
    assert list(e) == [(0, 1)] and e[(0, 1)].delay_ticks == 12


def test_louder_copy_is_not_an_echo():
    line = [(t, on(0, 60 + i, 100)) for i, t in enumerate((0, 24, 48))] + \
           [(t + 10, off(0, 60 + i)) for i, t in enumerate((0, 24, 48))]
    other = [(t + 12, on(1, 60 + i, 100)) for i, t in enumerate((0, 24))] + \
            [(t + 22, off(1, 60 + i)) for i, t in enumerate((0, 24))] + [(100, on(1, 40, 90)), (110, off(1, 40))]
    s = song([[RIP] + line + other])
    parts = {(0, ch): [n for n in s.notes if n.channel == ch] for ch in (0, 1)}
    assert detect_echoes(s, parts, {(0, 0): 80, (0, 1): 100}) == {}


# ---------------------------------------------------------------- driver
def busy_song(n_parts=9, notes=12):
    """``n_parts`` partes tocando acordes sustentados ao mesmo tempo (mais que 6 vozes FM)."""
    ev = [RIP]
    for ch in range(n_parts):
        ev.append((0, pc(ch, ch % 8)))
        for i in range(notes):
            k = 57 + ch * 2 + (i % 3)
            ev += [(i * 24, on(ch, k, 100)), (i * 24 + 20, off(ch, k))]
    return song([ev])


def test_every_note_is_accounted_for():
    sf = tone_sf()
    s = busy_song()
    plan, st = run(s, sf)
    c = st.stats
    placed = c["fm"] + c["psg_planned"] + c["psg_overflow"] + c["omitted"]
    assert placed == len(s.notes)
    assert c["omitted"] + c["shortened"] == len([x for x in st.changes if "omitida" in x or "encurtada" in x])


def test_capacity_is_never_exceeded():
    sf = tone_sf()
    plan, st = run(busy_song(), sf)
    for f, fm_on, psg_vol, _ in replay(st):
        assert sum(fm_on) <= 6
        assert sum(v < 15 for v in psg_vol[:3]) <= 3


def test_dac_channel_never_keyed_when_pcm_is_on():
    from mdconv.driver.driver import Driver as D
    sf = tone_sf()
    s = busy_song()
    plan = make_plan(s, sf, use_pcm=False)
    plan.fm_capacity = 5                       # simula o FM6 ocupado pelo DAC
    patches = {bp: fallback_patch() for p in plan.parts for bp in p.programs}
    st = D(s, plan, patches, vibrato=False).run()
    for f, fm_on, _, _ in replay(st):
        assert not fm_on[5]


def test_pitch_and_timing_preserved():
    sf = tone_sf()
    keys = [45, 57, 69, 81]
    ev = [RIP, (0, pc(0, 0))] + [x for i, k in enumerate(keys) for x in ((i * 48, on(0, k, 100)), (i * 48 + 24, off(0, k)))]
    s = song([ev])
    plan, st = run(s, sf)
    keyed = list(replay(st))[-1][3]
    assert [f for f, _, _ in keyed] == [round(n.start_s * 60) for n in s.notes]
    for (f, ch, (lo, hi)), k in zip(keyed, keys):
        hz = block_fnum_to_freq(hi >> 3, ((hi & 7) << 8) | lo, 7670453)
        assert abs(1200 * np.log2(hz / midi_to_hz(k))) < 1.5


def test_velocity_maps_to_carrier_tl_linearly():
    sf = tone_sf()
    s = song([[RIP, (0, pc(0, 0)), (0, cc(0, 7, 127)), (0, on(0, 60, 127)), (24, off(0, 60)),
               (48, on(0, 60, 64)), (72, off(0, 60))]])
    plan, st = run(s, sf)
    # TL da portadora OP2 (registrador 0x48, offset 0x8) vigente em cada key-on do canal 0
    tl, at_keyon = None, []
    for fr in st.frames:
        for w in fr:
            if w[0] == "fm" and w[1] == 0 and w[2] == 0x48:
                tl = w[3]
            if w[0] == "fm" and w[2] == 0x28 and w[3] == 0xF0:
                at_keyon.append(tl)
    assert at_keyon[1] - at_keyon[0] == round(-20 * np.log10(64 / 127) / 0.75)


def test_low_notes_never_go_to_psg():
    sf = tone_sf()
    st = run(busy_song(n_parts=12), sf)[1]
    periods = []
    lat = {}
    for fr in st.frames:
        for w in fr:
            if w[0] != "psg":
                continue
            b = w[1]
            if b & 0x80 and not (b & 0x10) and ((b >> 5) & 3) < 3:
                lat["ch"] = (b >> 5) & 3
                lat["lo"] = b & 15
            elif not (b & 0x80) and "lo" in lat:
                periods.append(((b & 0x3F) << 4) | lat.pop("lo"))
    assert periods and max(periods) <= 1023
    assert all(3579545 / (32 * p) >= 109 for p in periods)
