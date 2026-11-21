"""Neo Geo (YM2610): FM 1 para 1, ADPCM-A no PCM do XGM, ADPCM-B no FM e altura das amostras."""
import numpy as np

from mdconv import neogeo


def _board(vrom=b""):
    m1 = bytearray(0x10000)
    m1[0x6907 + 0x22] = 2                       # comando 22 = música (tipo 2)
    return neogeo.Board("kof94", bytes(m1), vrom)


def test_music_commands_from_type_table():
    assert _board().musics == (0x22,)


def test_f0_of_periodic_signal():
    t = np.arange(8000)
    x = np.sign(np.sin(2 * np.pi * t / 91.9)).astype(np.float32)
    assert abs(neogeo._f0(x) - 91.9) < 1.0


def test_fm_remap_and_adpcm_a_to_pcm():
    # nibbles alternando subida forte e descida: uma amostra ADPCM-A de 2 KB que soa
    vrom = bytes([0x7F, 0xF7] * 1024)
    b = _board(vrom)
    w = [(0, 0, 0xA6, 0x22), (0, 0, 0xA2, 0x69),          # canal 2 (porta A) do YM2610: lá 440 Hz
         (100, 0, 0x28, 0xF2),                            # key on do canal 2
         (100, 1, 0x01, 0x3F), (100, 1, 0x08, 0xDF),      # ADPCM-A: volume total e do canal 0
         (100, 1, 0x10, 0x00), (100, 1, 0x18, 0x00), (100, 1, 0x20, 0x07), (100, 1, 0x28, 0x00),
         (200, 1, 0x00, 0x01)]                            # key on do ADPCM-A canal 0
    cap = neogeo.Capture(0x22, w, 44100, None)
    st, rep = neogeo.to_stream(b, cap, neogeo.Options(metals_to_noise=False))
    flat = [x for f in st.frames for x in f]
    assert ("fm", 0, 0x28, 0xF1) in flat                  # canal 2 do YM2610 → FM 2 do YM2612
    assert any(x[0] == "fm" and x[2] == 0xA5 for x in flat)
    pcm = [x for x in flat if x[0] == "pcm"]
    assert pcm and pcm[0][1] == 1 and len(st.pcm_samples) == 1
    assert ("fm", 0, 0x2B, 0x80) in flat                  # DAC ligado no FM 6
    assert any("KB" in r for r in rep)


def test_adpcm_b_decoder_follows_signal():
    # nibble 7 (subida máxima) repetido: o acumulador sobe rápido, como no YM2610
    y = neogeo.decode_adpcm_b(bytes([0x77] * 64))
    assert 0 < y[0] < y[1] < y[2] and y[-1] > 0.99            # satura no máximo, sem dar a volta


def test_find_type_table_in_dispatch_code():
    # trecho do despacho do programa "Ver 3.0 by MAKOTO" com a tabela de tipos em 0x72B4
    code = bytes.fromhex("5f160021542c197e32b7fe21b472197e323ffeb7c83dca6d0c3dcaec01")
    m1 = bytes(0x100) + code + bytes(0x10000)
    assert neogeo.find_type_table(m1) == 0x72B4


def test_stream_to_furnace(tmp_path):
    from mdconv.driver.driver import Stream
    from mdconv.export.fur_stream import write_stream_fur
    from mdconv.export.furnace import read_fur
    frames = [[] for _ in range(120)]
    # FM 1: timbre simples, lá 440 Hz, liga no quadro 0 e solta no 30
    frames[0] += [("fm", 0, 0xB0, 0x07), ("fm", 0, 0xB4, 0xC0)]
    frames[0] += [("fm", 0, 0x40 + o, 0x10) for o in (0, 4, 8, 12)]
    frames[0] += [("fm", 0, 0x30 + o, 0x01) for o in (0, 4, 8, 12)]
    frames[0] += [("fm", 0, 0xA4, 0x22), ("fm", 0, 0xA0, 0x69), ("fm", 0, 0x28, 0xF0)]
    frames[30].append(("fm", 0, 0x28, 0x00))
    # PSG 1: liga no quadro 10 (período 254 ≈ 440 Hz) e cala no 40; PCM no quadro 5
    frames[10] += [("psg", 0x80 | 14), ("psg", 15), ("psg", 0x90 | 2)]
    frames[40].append(("psg", 0x9F))
    frames[5].append(("pcm", 1, 1, 2))
    st = Stream(frames=frames, fps=60.0, loop_frame=60, pcm_samples={1: np.zeros(100, np.int8)},
                pcm_rate=14000.0)
    st.stats["loop_end_frame"] = 120
    rep = write_stream_fur(st, tmp_path / "t.fur", name="teste")
    chk = read_fur((tmp_path / "t.fur").read_bytes())
    assert chk.systems == [0xBE, 0x03] and chk.samples == 1 and chk.notes >= 3
    assert rep.loop_row == 60


def test_find_dispatch_of_1996_driver():
    # "Sound Driver Ver 0.1 96/07/03": tabela indexada por (comando − 20h), em 0x2E6C
    code = bytes.fromhex("fe203003c3080c57d6205f7a1600216c2e195f7eb7c8")
    m1 = bytes(0x140) + code + bytes(0x10000)
    assert neogeo.find_dispatch(m1) == (0x2E6C - 0x20, 0x20)


def test_garou_profile():
    """Garou: mesmo programa de som da família KOF 96–98 (Sound Driver Ver 1.8)."""
    p = neogeo.PROFILES["garou"]
    assert "253-m1.bin" in p["m1"] and len(p["v"]) == 4
    assert p["names"][0x30] == "Tema de Terry Bogard" and len(p["names"]) == 18
    # o despacho dos comandos do Ver 1.8 tem o mesmo formato do Ver 0.1/1.1/1.7
    code = b"\x00" * 0x175 + bytes.fromhex("fe20300 3c36b0d57d6205f7a1600213830195f7eb7c8".replace(" ", ""))
    assert neogeo.find_dispatch(code) == (0x3038 - 0x20, 0x20)


def test_psg_free_frames_follow_ssg_volume():
    frames = [[("psg", 0x90 | 3)], [], [("psg", 0x90 | 15)], []]       # tom 1 soa nos quadros 0–1
    free = neogeo._psg_free_frames(frames, 4)
    assert list(free[0]) == [False, False, True, True]
    assert free[1].all() and free[2].all()


def test_overflow_imitates_pitched_note_and_bass_drum():
    t = np.arange(5600) / neogeo.PCM_RATE
    tone = (0.8 * np.sign(np.sin(2 * np.pi * 440 * t)) * np.exp(-3 * t)).astype(np.float32)
    kick = (0.9 * np.sin(2 * np.pi * (60 * t + 200 * (1 - np.exp(-30 * t)) / 30)) * np.exp(-8 * t)).astype(np.float32)
    free = np.ones((3, 100), dtype=bool)
    free[0, :] = False                                                 # o SSG usa o tom 1
    ov = neogeo._Overflow(100, free, noise_free=False)
    assert ov.place(10, tone, "a")
    assert ov.place(10, kick, "b")
    ws = [w for f in ov.frames for w in f]
    chans = {(w[1] >> 5) & 3 for w in ws if w[1] & 0x90 == 0x90}
    assert chans == {1, 2}                                             # nunca o tom do SSG
    per = [(ws[i][1] & 15) | (ws[i + 1][1] << 4) for i in range(len(ws) - 1)
           if ws[i][1] & 0xF0 == 0xA0]                                 # latch do tom 2: a nota de 440 Hz
    assert per and abs(3579545 / (32 * per[0]) - 440) < 10
    assert sum(ov.count.values()) == 2


def test_overflow_option_keeps_notes_from_vanishing():
    vrom = bytes(([0x77] * 4 + [0xFF] * 4) * 256)                      # onda com altura (~1,2 kHz)
    b = _board(vrom)
    w = [(0, 1, 0x01, 0x3F)]
    for ch in (0, 1):
        w += [(0, 1, 0x08 + ch, 0xDF), (0, 1, 0x10 + ch, 0), (0, 1, 0x18 + ch, 0), (0, 1, 0x20 + ch, 7),
              (0, 1, 0x28 + ch, 0)]
    w += [(200, 1, 0x00, 0x03)]                                        # duas notas no mesmo instante
    cap = neogeo.Capture(0x22, w, 44100, None)
    for over in (False, True):
        st, rep = neogeo.to_stream(b, cap, neogeo.Options(metals_to_noise=False, pcm_channels=(1,),
                                                          psg_overflow=over))
        pcm = [x for f in st.frames for x in f if x[0] == "pcm"]
        psg = [x for f in st.frames for x in f if x[0] == "psg"]
        if over:
            assert len(pcm) == 1 and psg and any("Transbordo" in r for r in rep)
        else:
            assert len(pcm) == 2 and not psg
