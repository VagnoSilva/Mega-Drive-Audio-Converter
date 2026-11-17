"""Efeitos de chip: script do Moon Patrol → AY → PSG/FM nos canais livres + código C."""
from mdconv import chipsfx, mpsl

PULO = bytes.fromhex(
    "520053055500 76F9590F5A0FD408600104590E5A0ED408680104590D5A0DD408700104590C5A0CD408780104"
    "590B5A0BD408800104590A5A0AD40888010459095A09D4089001047706FF".replace(" ", ""))


def _rom():
    rom = bytearray(0x1000)
    rom[0x803:0x803 + len(PULO)] = PULO
    for c in range(0x10, 0x20):
        rom[0x444 + 2 * (c - 0x10)], rom[0x445 + 2 * (c - 0x10)] = 0xF8, 0x03
    return bytes(rom)


def test_mpsl_jump_script():
    ev, end, loop = mpsl.run(_rom(), 0xF803)
    assert loop is None
    sweep = [v for t, c, r, v in ev if r == 4]
    assert sweep[0] == 0x60 and sweep[-1] == 0x98 and len(sweep) == 63
    assert end == 7 * 9 * 4                                  # 252 tiques ≈ 1 s
    assert (0, 1, 3, 5) in ev                                # canal B = 0x0500 (43,7 Hz)


def test_jump_to_free_channels(tmp_path):
    sfx = chipsfx.from_ay(mpsl.effect(_rom(), 0x14), "pulo")
    psg_ch = {(w[1] >> 5) & 3 for f in sfx.frames for w in f if w[0] == "psg" and w[1] & 0x80}
    fm_ch = {chipsfx._fm_ch(w[1], w[2], w[3]) for f in sfx.frames for w in f if w[0] == "fm"}
    assert psg_ch == {0} and fm_ch == {3}
    assert 58 <= len(sfx.frames) <= 64
    paths = chipsfx.write_outputs(sfx, tmp_path)
    src = (tmp_path / "pulo.c").read_text(encoding="utf-8")
    assert "void pulo_play(void)" in src and "YM2612_writeRegSafe" in src
    assert len(paths) == 3


def test_mpsl_continuous_loop_and_threevoice():
    # F900: THREEVOICE AY0 = 0x0100 (sem continuar, espera 10) ; volume A = 12 (espera 10) ; CALL F900
    script = bytes([0x20, 0x00, 0x01, 10, 0x28, 12, 10, 0xFE, 0xF9, 0x00])
    rom = bytearray(0x1000)
    rom[0x900:0x900 + len(script)] = script
    ev, end, loop = mpsl.run(bytes(rom), 0xF900)
    assert (loop, end) == (20, 40)                           # 2ª passagem igual à 1ª: repete 20–40
    assert (0, 0, 2, 0x80) in ev and (0, 0, 4, 0x81) in ev    # B = 0x100/2, C = 0x100/2 + 1
    assert (0, 0, 13, 9) in ev                               # reinicia o envelope
    sfx = chipsfx.from_ay(mpsl.to_ay(ev, end, "teste", loop), "teste")
    assert sfx.loop is not None
    assert "teste_stop" in chipsfx.c_source(sfx)


def test_m62_tone_pair_and_adpcm():
    # M62: 0x31 FF CC grava o par no registrador 2/3 (voz B) do AY1 — não é o THREEVOICE do M52
    rom = bytearray(0x1000)
    rom[0x900:0x905] = bytes([0x31, 0x25, 0x01, 4, 0xFF])
    ev, end, loop = mpsl.run(bytes(rom), 0xF900, dialect="m62")
    assert (0, 1, 2, 0x25) in ev and (0, 1, 3, 0x01) in ev and end == 4
    import pytest
    with pytest.raises(mpsl.MpslError):                     # no M52 esse comando não existe
        mpsl.run(bytes(rom), 0xF900, dialect="m52")
    # ADPCM do MSM5205: nibbles 0x7 sobem, 0xF descem; dois por byte, o alto primeiro
    y = mpsl.adpcm_decode(bytes([0x77] * 8 + [0xFF] * 8))
    assert len(y) == 32 and y[15] > 0.05 and y[-1] < y[15]


def test_capcom_loop_finders():
    from mdconv.capcom_opn import find_note_loop, find_write_loop
    # 3 notas a cada 100 tiques, repetidas; uma escorrega 1 tique na 2ª volta
    log = []
    for k in range(6):
        for j, (t, fn) in enumerate(((10, 0x40), (40, 0x50), (70, 0x60))):
            tt = k * 100 + t + (1 if (k == 2 and j == 1) else 0)
            log += [(tt, 0, 0xA4, 0x22), (tt, 0, 0xA0, fn), (tt, 0, 0x28, 0xF0)]
    lp = find_note_loop(log, 600, 240, min_seconds=0.1)
    assert lp is not None and lp[1] - lp[0] == 100
    assert find_write_loop([], 600, 240, min_seconds=0.1) is None


def test_capcom_prescaler_address_write_is_logged():
    from mdconv.capcom_opn import PROFILES, Board
    b = Board("gng", bytes(PROFILES["gng"]["rom_size"]))
    b._wr(0xE000, 0x2D)          # só o endereço: no YM2203 isso já troca o divisor
    b._wr(0xE000, 0x2E)
    b._wr(0xE000, 0x40)
    b._wr(0xE001, 0x10)
    assert [(c, r) for _, c, r, _ in b.log] == [(0, 0x2D), (0, 0x2E), (0, 0x40)]


def test_effect_all_in_fm_when_no_psg_channel():
    # sem tom do PSG livre, o tom que caberia no PSG vai para um FM livre (pulo e tiro não brigam)
    sfx = chipsfx.from_ay(mpsl.effect(_rom(), 0x14), "pulo", psg_free=(), fm_free=(3, 4))
    psg_tone = {(w[1] >> 5) & 3 for f in sfx.frames for w in f
                if w[0] == "psg" and w[1] & 0x90 == 0x90 and w[1] & 0x0F != 0x0F}
    fm_ch = {chipsfx._fm_ch(w[1], w[2], w[3]) for f in sfx.frames for w in f if w[0] == "fm"}
    assert not psg_tone and fm_ch == {3, 4}


def test_moon_patrol_default_effect_channels_do_not_collide():
    b = mpsl.Board("mpatrol", _rom())
    jump, shot = b.channels(0x14), b.channels(0x12)
    assert not set(jump[0]) & set(shot[0])          # pulo e tiro: tons do PSG diferentes
    assert 5 not in jump[1] + shot[1]               # FM 6 fica com o PCM
