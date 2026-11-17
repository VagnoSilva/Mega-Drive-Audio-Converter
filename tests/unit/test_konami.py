"""Konami Bubble System (Nemesis): K005289 no FM e loop pelas notas."""
from mdconv import konami


def test_wave_patch_picks_strongest_harmonics():
    import math
    # seno puro (harmônico 1) + metade no 3º harmônico
    table = bytes(int(round(7.5 + 5 * math.sin(2 * math.pi * i / 32) + 2.5 * math.sin(6 * math.pi * i / 32)))
                  for i in range(32))
    p = konami.wave_patch(table)
    assert p.alg == 7 and len(p.ops) == 4
    by_mul = {o.mul: o.tl for o in p.ops}
    assert 1 in by_mul and 3 in by_mul and by_mul[1] < by_mul[3]       # o fundamental mais forte


def test_note_loop_with_tempo_jitter():
    # intro de 3 notas, depois um ciclo de 4 notas que se repete; cada volta dura 400 ± 2 ciclos
    ev, t = [], 0
    for n in (9, 8, 7):
        ev.append((t, n)); t += 100
    start = t
    for k in range(5):
        for n in (1, 2, 3, 4):
            ev.append((t, n)); t += 100 + (1 if k % 2 else -1) * (n == 4)
    s, p = konami.find_note_loop({"v": ev}, min_period=100)
    assert s == start and 396 <= p <= 404


def test_k005289_goes_to_fm():
    from mdconv.driver.driver import Stream
    st = Stream(frames=[[] for _ in range(30)], fps=60.0, loop_frame=None, pcm_samples={}, pcm_rate=14000.0)
    proms = bytes(([0] * 16 + [15] * 16) * 16)                          # onda quadrada em todas
    cap = konami.Capture(0x40, [], [(0, 0, 380, 15, 0), (44100 // 4, 0, 380, 0, 0)], 44100 // 2, None)
    konami.k_to_stream(st, cap, proms)
    flat = [w for f in st.frames for w in f]
    keyon = [w for w in flat if w[0] == "fm" and w[2] == 0x28]
    assert keyon[0][3] == 0xF0 | 2 and keyon[-1][3] == 2                 # FM 3: liga e solta
    hz = konami.K005289_CLOCK / (32 * 380)                              # ≈ 294 Hz (ré)
    assert 290 < hz < 298
    assert any(w[0] == "fm" and w[2] == 0xA6 for w in flat)
