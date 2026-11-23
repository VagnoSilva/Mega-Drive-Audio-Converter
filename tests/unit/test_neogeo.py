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


def _ym2610_vgm(tmp_path, gz=False):
    """VGM mínimo de YM2610: amostra de ADPCM-A no bloco 0x82, uma nota FM e uma batida de ADPCM-A."""
    import gzip
    import struct
    rom = bytes([0x77] * 0x100 + [0xFF] * 0x100)
    body = bytearray()
    body += b"\x67\x66\x82" + struct.pack("<III", 8 + len(rom), 0x400, 0) + rom
    body += b"\x58\x24\x00\x58\x1C\xBF"                        # timer e flags: não entram
    body += b"\x58\xA4\x22\x58\xA0\x69\x58\x28\xF1"             # nota no canal FM 1 do YM2610
    body += b"\x59\x10\x00\x59\x18\x00\x59\x20\x00\x59\x28\x00"  # ADPCM-A canal 1: 0x000–0x1FF
    body += b"\x59\x01\x3F\x59\x08\xDF\x59\x00\x01"             # volume e disparo
    loop_at = len(body)
    body += b"\x61\x44\xAC\x62\x66"                             # 44 100 + 735 amostras
    head = bytearray(0x100)
    head[0:4] = b"Vgm "
    struct.pack_into("<I", head, 0x08, 0x171)
    struct.pack_into("<I", head, 0x18, 44100 + 735)
    struct.pack_into("<I", head, 0x1C, 0x100 + loop_at - 0x1C)
    struct.pack_into("<I", head, 0x34, 0x100 - 0x34)
    struct.pack_into("<I", head, 0x4C, 8_000_000)
    data = bytes(head) + bytes(body)
    p = tmp_path / ("t.vgz" if gz else "t.vgm")
    p.write_bytes(gzip.compress(data) if gz else data)
    return p


def test_read_ym2610_vgm(tmp_path):
    from mdconv.vgmconv import detect_chip
    p = _ym2610_vgm(tmp_path, gz=True)
    assert detect_chip(p) == "YM2610"
    board, cap, _ = neogeo.read_vgm(p)
    assert len(board.vrom) == 0x400 and board.vrom[:2] == b"\x77\x77" and board.vrom_b == board.vrom
    assert not any(ym == 0 and (r == 0x1C or r == 0x24) for _, ym, r, _ in cap.writes)
    assert (0, 0, 0x28, 0xF1) in cap.writes and cap.end == 44100 + 735 and cap.loop == 0
    stream, report = neogeo.to_stream(board, cap, neogeo.Options(metals_to_noise=False))
    pcm = [w for f in stream.frames for w in f if w[0] == "pcm" and w[2]]
    assert pcm and any("FM 1" in r for r in report)


def test_convert_ym2610_vgm_writes_xgm(tmp_path):
    from mdconv.vgmconv import convert_file
    p = _ym2610_vgm(tmp_path)
    out = convert_file(p, tmp_path / "out", wav=False, log=lambda *_: None,
                       neo_options={"pcm_channels": [3], "nao_existe": 1})
    assert any(f.suffix == ".xgm" for f in out)


def test_fit_sample_slots_merges_volumes_instead_of_dropping_notes():
    # 3 fontes com 30 volumes cada (90 chaves) → 63: nenhuma fonte some, volumes vizinhos juntam
    keys = [("a", s, 0, 18518.0, v) for s in range(3) for v in range(30)] + [None]
    remap = neogeo.fit_sample_slots(keys, 63)
    left = {remap.get(k, k) for k in keys if k is not None}
    assert len(left) == 63 and {k[1] for k in left} == {0, 1, 2}
    assert all(abs(remap[k][-1] - k[-1]) <= 3 and remap[k][:-1] == k[:-1] for k in remap)
    assert neogeo.fit_sample_slots(keys[:10], 63) == {}


def test_volume_step_only_6_or_12_db():
    assert neogeo.Options(volume_step_db=3.0).volume_step_db == 6.0      # opção antiga salva
    assert neogeo.Options(volume_step_db=12.0).volume_step_db == 12.0
    assert neogeo.Options().volume_step_db == 6.0
