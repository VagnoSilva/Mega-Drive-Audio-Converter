"""Canais FM fixos por parte e andamento travado na grade de quadros."""
import pytest

from mdconv.arrange.plan import make_plan
from mdconv.arrange.tempo_grid import find_step, snap_tempo
from mdconv.convert import convert, fallback_patch
from mdconv.driver.driver import Driver
from tests.fixtures.builders import off, on, pc, tempo as meta_tempo
from tests.unit.test_phase4 import RIP, song, tone_sf


def _tempo_us(bpm):
    return int(round(60e6 / bpm))


def grid_song(bpm=102.0, n=64, step=4):
    ev = [RIP, (0, meta_tempo(_tempo_us(bpm))), (0, pc(0, 1))]
    for i in range(n):
        ev += [(i * step, on(0, 60 + i % 5, 100)), (i * step + step - 1, off(0, 60 + i % 5))]
    return song([ev])


def test_snap_tempo_makes_steps_whole_frames():
    s = grid_song()
    assert find_step(s)[0] == 4
    s2, rep = snap_tempo(s)
    assert rep.applied and rep.segments[0]["n"] == 6           # 5,88 → 6 quadros por passo
    assert s2.tempo_map.bpm_at(0) == pytest.approx(100.0, abs=0.01)
    frames = [n.start_s * 60 for n in s2.notes]
    assert all(abs(f - round(f)) < 1e-6 for f in frames)
    assert {round(b - a) for a, b in zip(frames, frames[1:])} == {6}
    # o original oscila entre 5 e 6 quadros
    orig = [round(n.start_s * 60) for n in s.notes]
    assert {b - a for a, b in zip(orig, orig[1:])} == {5, 6}
    assert s2.length_s > s.length_s                             # 2% mais lento


def test_snap_tempo_refuses_big_changes():
    s = grid_song(bpm=176.0, step=3)          # 2,56 quadros por passo; o dobro (5,11) cabe
    s2, rep = snap_tempo(s)
    assert rep.segments[0]["step"] == 6 and rep.segments[0]["n"] == 5
    s3, rep3 = snap_tempo(grid_song(bpm=176.0, step=3), tol=0.01)
    assert not rep3.applied and s3 is not None


def _parts_song():
    """Duas partes monofônicas que se alternam com uma terceira (eco curto)."""
    evs = []
    for ch, prog, base in ((0, 1, 48), (1, 2, 60), (2, 3, 72), (3, 4, 55), (4, 5, 67), (5, 6, 64)):
        ev = [(0, pc(ch, prog))]
        for i in range(24):
            t = i * 12 + ch
            ev += [(t, on(ch, base + i % 3, 100)), (t + 10, off(ch, base + i % 3))]
        evs.append(ev)
    evs[0] = [RIP] + evs[0]
    return song(evs)


def test_fixed_channels_keep_each_part_on_its_channel():
    s, sf = _parts_song(), tone_sf()
    plan = make_plan(s, sf, use_pcm=True, pcm_channels=[1, 2])
    patches = {bp: fallback_patch() for p in plan.parts for bp in p.programs}
    dyn = Driver(s, plan, patches, vibrato=False, fm_alloc="dinamico")
    st_d = dyn.run()
    fix = Driver(s, plan, patches, vibrato=False, fm_alloc="fixo")
    st_f = fix.run()
    assert fix.homes and len(fix.homes) <= plan.fm_capacity
    for key, chs in fix.homes.items():
        used = {ev[3] for ev in fix.timeline if ev[0] == "on" and ev[2] == "fm" and (ev[4].track, ev[4].channel) == key}
        assert used and used <= set(chs) | {c for c in range(plan.fm_capacity) if c not in fix.home_of}
    assert st_f.stats["patch_loads"] <= st_d.stats["patch_loads"]


def test_auto_mode_reports_choice():
    s, sf = _parts_song(), tone_sf()
    c = convert(s, sf, None, use_pcm=False, balance="manual", fm_alloc="auto", snap_tempo=True)
    assert c.fm_alloc["auto"] and c.fm_alloc["mode"] in ("dinamico", "fixo")
    assert set(c.fm_alloc["scores"]) == {"dinamico", "fixo"}
    from mdconv.convert import report_text
    txt = report_text(c)
    assert "Canais FM (automático)" in txt and "Andamento na grade" in txt


def test_redundant_fm_writes_dropped_but_not_frequency_or_after_loop():
    from mdconv.driver.driver import _drop_redundant_fm
    frames = [
        [("fm", 0, 0x30, 1), ("fm", 0, 0xA4, 0x22), ("fm", 0, 0xA0, 0x10), ("fm", 0, 0x28, 0xF0)],
        [("fm", 0, 0x30, 1), ("fm", 0, 0xA4, 0x22), ("fm", 0, 0xA0, 0x10), ("fm", 0, 0x28, 0xF0)],
        [("fm", 0, 0x30, 1), ("fm", 0, 0xB0, 7)],                    # quadro do loop
        [("fm", 0, 0xB0, 7), ("fm", 1, 0x30, 1)],
    ]
    n = _drop_redundant_fm(frames, loop_frame=2)
    assert frames[1] == [("fm", 0, 0xA4, 0x22), ("fm", 0, 0xA0, 0x10), ("fm", 0, 0x28, 0xF0)]
    assert frames[2] == [("fm", 0, 0x30, 1), ("fm", 0, 0xB0, 7)]     # no loop tudo é reescrito
    assert frames[3] == [("fm", 1, 0x30, 1)]                         # porta 1 é outro registrador
    assert n == 2
