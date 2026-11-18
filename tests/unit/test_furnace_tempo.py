"""O .fur tem de tocar na mesma velocidade do XGM/VGM, com qualquer resolução do MIDI.

Simula o relógio do Furnace (60 Hz, velocidade em ticks por linha, virtual tempo N/D, FDxx,
EDxx) a partir do que vai no arquivo e compara cada nota com o quadro em que o driver a tocou.
"""
import struct
import zlib

import pytest

from mdconv.app.project import Project, ProjectSettings
from mdconv.export import furnace
from mdconv.io_sf2 import gm
from tests.fixtures.builders import meta_text, midi_file, off, on, pc, track_bytes


@pytest.fixture(autouse=True)
def _gm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(gm, "_cache", None)


def _tempo(us):
    return b"\xFF\x51\x03" + us.to_bytes(3, "big")


def _midi(path, ppq, tempos, bars=8, swing=0):
    """Piano em colcheias (com 'swing' em ticks nas notas pares), baixo em semínimas.
    ``tempos``: [(tick, BPM)]."""
    beat = ppq
    conductor = [(t, _tempo(int(round(60_000_000 / b)))) for t, b in tempos]
    mel, bass = [(0, meta_text(3, "Piano")), (0, pc(0, 0))], [(0, meta_text(3, "Baixo")), (0, pc(1, 33))]
    for i in range(bars * 8):
        t = i * beat // 2 + (swing if i % 2 else 0)
        k = 60 + (i * 5) % 12
        mel += [(t, on(0, k, 100)), (t + beat // 3, off(0, k))]
    for i in range(bars * 4):
        bass += [(i * beat, on(1, 36 + (i % 3) * 5, 100)), (i * beat + beat * 3 // 4, off(1, 36 + (i % 3) * 5))]
    data = bytearray(midi_file([track_bytes(conductor), track_bytes(mel), track_bytes(bass)]))
    struct.pack_into(">H", data, 12, ppq)          # divisão do cabeçalho
    path.write_bytes(bytes(data))
    return path


def _furnace_times(meta, cells, n_rows):
    """Segundo em que cada nota começa no Furnace: {canal: [s, ...]}."""
    speed, D = meta["speed"], meta["vt_d"]
    N = meta["vt_n"]
    t, row_t = 0.0, []
    for r in range(n_rows):
        c0 = cells[0].get(r)
        if c0 is not None and furnace.EFF_VTEMPO_N in c0.fx:
            N = c0.fx[furnace.EFF_VTEMPO_N]
        row_t.append((t, D / (60.0 * N)))
        t += speed * D / (60.0 * N)
    out = {}
    for ch, rows in enumerate(cells):
        for r, c in rows.items():
            if c.note is not None and c.note < furnace.NOTE_OFF and r < n_rows:
                t0, tick = row_t[r]
                out.setdefault(ch, []).append(t0 + c.fx.get(furnace.EFF_DELAY, 0) * tick)
    return out, t


@pytest.mark.parametrize("ppq,tempos,swing", [
    (480, [(0, 170)], 0),                       # o caso do Pegasus Fantasy (MIDI da internet)
    (480, [(0, 170), (480 * 16, 140)], 40),      # mudança de andamento + notas entre linhas
    (96, [(0, 133.3333)], 0),
    (24, [(0, 120)], 0),                         # GBA
    (24, [(0, 180)], 1),                         # GBA rápido
    (48, [(0, 150), (48 * 8, 200)], 0),          # CPS1
])
def test_fur_plays_at_the_same_speed_as_the_xgm(tmp_path, ppq, tempos, swing):
    mid = _midi(tmp_path / "m.mid", ppq, tempos, swing=swing)
    p = Project(ProjectSettings(midi=str(mid), sf2="", library=str(tmp_path / "lib"),
                                out_dir=str(tmp_path / "out"), loop_mode="none"))
    p.load()
    conv = p.convert()
    meta, ins, smp, cells, orders, rep = furnace.build_fur_song(conv)
    assert 1 <= meta["vt_n"] <= 255 and 1 <= meta["vt_d"] <= 255
    assert all(c.fx.get(furnace.EFF_VTEMPO_N, 1) <= 255 for rows in cells for c in rows.values())
    times, total = _furnace_times(meta, cells, rep.rows)
    t_first = min(f for ev in conv.driver.timeline if ev[0] == "on" for f in [ev[1]]) / 60.0
    psg0 = rep.channels.index("PSG 1")
    worst = 0.0
    for ev in conv.driver.timeline:
        if ev[0] != "on":
            continue
        _, frame, typ, idx, _n = ev
        ch = idx if typ == "fm" else psg0 + idx
        want = frame / 60.0 - t_first
        got = min(times.get(ch, [1e9]), key=lambda x: abs(x - want))
        worst = max(worst, abs(got - want))
    assert worst <= 1.5 / 60, f"nota fora do lugar em {worst * 1000:.1f} ms ({rep.tempo})"
    # e o arquivo grava a velocidade e o virtual tempo que a simulação usou
    data = zlib.decompress(furnace.serialize(meta, ins, smp, cells, orders))
    info = struct.unpack_from("<I", data, 20)[0] + 8
    assert data[info + 1] == data[info + 2] == meta["speed"]
