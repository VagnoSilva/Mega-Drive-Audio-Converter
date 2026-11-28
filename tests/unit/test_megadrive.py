"""Mega Drive: núcleo do 68000 (Musashi + mapa do console), perfis, mixer de PCM e extração."""
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from mdconv.chips import md68k

pytestmark = pytest.mark.skipif(not md68k.available(), reason="núcleo do 68000 não compilou")


def _rom(code: dict, size=0x1000, serial=b"GM 00000000-00", checksum=0):
    d = bytearray(size)
    struct.pack_into(">II", d, 0, 0x00FFFE00, 0x200)
    d[0x100:0x110] = b"SEGA MEGA DRIVE "
    d[0x150:0x158] = b"TESTE MD"
    d[0x180:0x180 + len(serial)] = serial
    struct.pack_into(">H", d, 0x18E, checksum)
    for a, b in code.items():
        d[a:a + len(b)] = b
    return bytes(d)


def _w(*words):
    return b"".join(struct.pack(">H", w) for w in words)


PROG = {
    0x200: _w(0x13FC, 0x0028, 0x00A0, 0x4000,        # move.b #$28,$A04000
              0x13FC, 0x00F1, 0x00A0, 0x4001,        # move.b #$F1,$A04001  (key-on FM 2)
              0x13FC, 0x009F, 0x00C0, 0x0011,        # move.b #$9F,$C00011  (PSG)
              0x13FC, 0x0007, 0x00A0, 0x1FFE,        # move.b #7,$A01FFE    (RAM do Z80)
              0x4E75),                               # rts
    0x240: _w(0x60FE),                               # bra.s * (nunca volta)
    0x260: _w(0x13FC, 0x0030, 0x00A0, 0x0002 | 0x4000,   # move.b #$30,$A04002 (endereço, porta 1)
              0x13C0, 0x00A0, 0x4003,                # move.b D0,$A04003
              0x4E75),
}


def test_call_records_ym_psg_and_z80_writes():
    m = md68k.Machine(_rom(PROG))
    m.reset()
    m.set_frame(7)
    m.set_breaks([0x200 + 16])
    m.call(0x200)
    ev = m.events()
    got = [(int(e["frame"]), int(e["type"]), int(e["addr"]), int(e["val"])) for e in ev]
    assert (7, md68k.EV_YM0, 0x28, 0xF1) in got
    assert (7, md68k.EV_PSG, 0, 0x9F) in got
    assert (7, md68k.EV_Z80, 0x1FFE, 7) in got
    assert any(t == md68k.EV_BRK for _, t, _, _ in got)
    assert m.zram()[0x1FFE] == 7
    assert m.unmapped() == (0, 0)


def test_d0_is_passed_and_port1_is_used():
    m = md68k.Machine(_rom(PROG))
    m.reset()
    m.call(0x260, d0=0x5A)
    ev = m.events()
    assert [(int(e["type"]), int(e["addr"]), int(e["val"])) for e in ev] == [(md68k.EV_YM1, 0x30, 0x5A)]


def test_routine_that_never_returns_raises():
    m = md68k.Machine(_rom(PROG))
    m.reset()
    with pytest.raises(RuntimeError, match="não voltou"):
        m.call(0x240, max_cycles=200_000)


def test_new_events_only_returns_what_is_new():
    m = md68k.Machine(_rom(PROG))
    m.reset()
    m.call(0x200)
    assert len(m.new_events()) == 3                      # YM, PSG e RAM do Z80 (reset limpa os breakpoints)
    assert len(m.new_events()) == 0
    m.call(0x260, d0=1)
    assert len(m.new_events()) == 1


def test_header_and_unknown_game(tmp_path):
    from mdconv import megadrive
    p = tmp_path / "teste.md"
    p.write_bytes(_rom(PROG))
    assert megadrive.is_board_source(str(p))
    assert megadrive.header(p.read_bytes())["overseas"].startswith("TESTE MD")
    with pytest.raises(ValueError, match="sem perfil"):
        megadrive.load_board(str(p))
    q = tmp_path / "outra.bin"
    q.write_bytes(b"\0" * 0x400)
    assert not megadrive.is_board_source(str(q))


def test_smd_is_deinterleaved(tmp_path):
    from mdconv import megadrive
    rom = _rom(PROG, size=0x4000)
    blk = bytes(rom[1::2]) + bytes(rom[0::2])            # formato .smd: ímpares, depois pares
    hdr = bytearray(512)
    hdr[8:10] = b"\xaa\xbb"
    p = tmp_path / "teste.smd"
    p.write_bytes(bytes(hdr) + blk)
    assert megadrive._read_rom(str(p)) == rom


def test_mix2_sums_two_channels_and_respects_priority():
    from mdconv.megadrive import Mix2
    rom = bytearray(0x400)
    # amostra 0: prioridade 1, 4 bytes em 0x100; amostra 1: prioridade 0, 3 bytes em 0x200
    rom[0x000:0x008] = bytes([1, 0x00, 0x01, 0, 4, 0, 0, 0])
    rom[0x008:0x010] = bytes([0, 0x00, 0x02, 0, 3, 0, 0, 0])
    rom[0x100:0x104] = bytes([200, 100, 50, 128])
    rom[0x200:0x203] = bytes([10, 20, 30])
    mx = Mix2(bytes(rom), table=0, regs=(0x1FFE, 0x1FFF), z80_tstates=687)
    assert abs(mx.rate - 5210.4) < 1
    mx.command(0, 0)
    assert mx.poll() == [(0, 0)]
    out = mx.render(5)
    idle = rom[0x100]                                    # o boot deixa os dois canais na amostra 0
    assert list(out[:4]) == [(200 + idle) >> 1, (100 + idle) >> 1, (50 + idle) >> 1, (128 + idle) >> 1]
    assert out[4] == (128 + idle) >> 1                   # parado no último byte
    mx.command(0, 1)                                     # prioridade 0 ≥ 0 (canal livre): entra
    assert mx.poll() == [(0, 1)]
    mx.command(1, 0)
    mx.poll()
    mx.command(1, 1)                                     # prioridade 0 < 1 tocando: ignorado
    assert mx.poll() == []


def test_mdvgm_hits_keep_their_own_pcm_channel():
    from mdconv.mdvgm import MdVgm, convert
    v = MdVgm(fm_clock=7670453, psg_clock=3579545)
    v.writes.append((0, ("fm", 0, 0x28, 0xF0)))
    s1 = bytes(range(100, 200)) * 3
    s2 = bytes(range(0, 100)) * 3
    v.hits = [(0, s1, 14000.0, 1), (735, s2, 14000.0, 2), (1470, s1, 14000.0)]
    v.total = 44100
    stream, report = convert(v, pcm_channel=3, trim=False)
    pcm = [(i, w[1], w[2]) for i, fr in enumerate(stream.frames) for w in fr if w[0] == "pcm" and w[2]]
    assert [c for _, c, _ in pcm] == [1, 2, 3]
    assert any("canais PCM 1, 2, 3" in r for r in report)


SF2 = os.environ.get("MDCONV_SF2_ROM", r"C:\Users\Vagno\Documents\roms\Street Fighter II' - Special Champion Edition (USA).md")


@pytest.mark.skipif(not Path(SF2).exists(), reason="ROM do SF2' SCE (Mega Drive) não encontrada")
def test_sf2_sce_song_12_matches_the_reference_capture(tmp_path):
    from mdconv import megadrive
    b = megadrive.load_board(SF2)
    assert b.key == "sf2sce" and len(b.musics) == 40
    cap = b.capture(12)
    assert abs(cap.end / megadrive.FRAME_HZ - 53.5) < 0.2
    assert abs(cap.loop / megadrive.FRAME_HZ - 3.4) < 0.2
    assert cap.fm_notes == 1211
    assert len(cap.pcm_hits) > 100
    # afinação: todas as notas FM a menos de 10 cents do temperamento igual
    hi, cents = {}, []
    for e in cap.events[cap.events["type"] <= 1]:
        p, r, v = int(e["type"]), int(e["addr"]), int(e["val"])
        if r & 0xFC == 0xA4:
            hi[(p, r & 3)] = v
        elif r & 0xFC == 0xA0 and (p, r & 3) in hi:
            h = hi[(p, r & 3)]
            fn, bl = ((h & 7) << 8) | v, (h >> 3) & 7
            if fn:
                f = fn * 2 ** bl * 7670453 / 144 / 2 ** 21
                m = 69 + 12 * np.log2(f / 440)
                cents.append((m - round(m)) * 100)
    assert cents and max(abs(c) for c in cents) < 10
    files = megadrive.export_board(b, tmp_path, select={20}, log=lambda *_: None)
    names = sorted(f.name for f in files)
    assert names == ["20_Musica_20.fur", "20_Musica_20.vgm", "20_Musica_20.xgm", "20_Musica_20_previa.wav"]
    from mdconv.mdvgm import read_vgm
    v = read_vgm(tmp_path / "musicas" / "20_Musica_20.vgm")
    assert v.writes and v.hits


# ------------------------------------------------------------------ Z80 em C
def test_z80_runs_from_68000_and_writes_ym_and_psg():
    """O 68000 copia um programa para o Z80, solta o reset e o Z80 escreve no YM, no PSG e lê a ROM
    pela janela de banco."""
    z80 = bytes([
        0x3E, 0x28, 0x32, 0x00, 0x40,        # ld a,$28 ; ld ($4000),a
        0x3E, 0xF0, 0x32, 0x01, 0x40,        # ld a,$F0 ; ld ($4001),a   (key-on FM 1)
        0x3E, 0x9F, 0x32, 0x11, 0x7F,        # ld a,$9F ; ld ($7F11),a   (PSG)
        0x3A, 0x00, 0x81,                    # ld a,($8100)  (ROM $000100 pela janela, banco 0)
        0x32, 0x00, 0x10,                    # ld ($1000),a
        0x76,                                # halt
    ])
    code = {0x300: z80}
    # 68000 em 0x200: copia 0x300..0x320 para $A00000, solta o reset do Z80 e volta
    code[0x200] = _w(0x33FC, 0x0100, 0x00A1, 0x1100,    # move.w #$100,$A11100 (pede o barramento)
                     0x33FC, 0x0000, 0x00A1, 0x1200,    # move.w #0,$A11200   (reset)
                     0x41F9, 0x0000, 0x0300,            # lea $300,a0
                     0x43F9, 0x00A0, 0x0000,            # lea $A00000,a1
                     0x303C, 0x001F,                    # move.w #31,d0
                     0x12D8, 0x51C8, 0xFFFC,            # move.b (a0)+,(a1)+ ; dbra d0
                     0x33FC, 0x0100, 0x00A1, 0x1200,    # move.w #$100,$A11200 (solta o reset)
                     0x33FC, 0x0000, 0x00A1, 0x1100,    # move.w #0,$A11100   (devolve o barramento)
                     0x4E75)
    m = md68k.Machine(_rom(code))
    m.reset()
    m.set_frame_auto(True)
    m.call(0x200)
    m.run_frames(1)
    ev = m.events()
    got = {(int(e["type"]), int(e["addr"]), int(e["val"])) for e in ev}
    assert (md68k.EV_YM0, 0x28, 0xF0) in got
    assert (md68k.EV_PSG, 0, 0x9F) in got
    assert m.zram()[0x1000] == ord("S")                  # "SEGA MEGA DRIVE" em $000100
    assert m.z80_steps > 0


def test_z80_c_matches_python_z80_on_random_code():
    """O Z80 em C é a tradução do mdconv.z80: mesmos registradores e memória instrução a instrução."""
    import ctypes
    import random
    import shutil
    import subprocess
    import sys
    import tempfile
    from mdconv.z80 import Z80 as PyZ80
    if sys.platform == "win32" or not shutil.which("gcc"):
        pytest.skip("precisa do gcc (compila o Z80 em C só para o teste)")
    src = Path(md68k.__file__).parent / "native" / "md68k" / "z80.c"
    shim = """#include <stdint.h>
static uint8_t mem[65536];
static uint8_t Z80_RD(uint16_t a) { return mem[a]; }
static void Z80_WR(uint16_t a, uint8_t v) { mem[a] = v; }
static uint8_t Z80_IN(uint16_t p) { (void)p; return 0xFF; }
static void Z80_OUT(uint16_t p, uint8_t v) { (void)p; (void)v; }
#include "%s"
static Z80 cpu;
void zt_reset(void) { z80_reset(&cpu); }
uint8_t *zt_mem(void) { return mem; }
Z80 *zt_cpu(void) { return &cpu; }
int zt_step(void) { return z80_step(&cpu); }
""" % str(src).replace("\\", "/")
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "zt.c"
        c.write_text(shim)
        so = Path(td) / "zt.so"
        r = subprocess.run(["gcc", "-O1", "-shared", "-fPIC", "-o", str(so), str(c)], capture_output=True, text=True)
        if r.returncode:
            pytest.skip("sem gcc para compilar o teste")
        lib = ctypes.CDLL(str(so))

        class CZ(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint8) for n in "a f b c d e h l a_ f_ b_ c_ d_ e_ h_ l_".split()] + \
                       [(n, ctypes.c_uint16) for n in "ix iy sp pc".split()] + \
                       [(n, ctypes.c_uint8) for n in "i r iff1 iff2 im halted ei_delay".split()] + \
                       [("cycles", ctypes.c_uint64)]
        lib.zt_mem.restype = ctypes.POINTER(ctypes.c_uint8 * 65536)
        lib.zt_cpu.restype = ctypes.POINTER(CZ)
        cmem, cpu = lib.zt_mem().contents, lib.zt_cpu().contents
        regs = "a f b c d e h l a_ f_ b_ c_ d_ e_ h_ l_ ix iy sp pc i r iff1 iff2 im".split()
        for seed in range(40):
            rnd = random.Random(seed)
            data = bytearray(rnd.getrandbits(8) for _ in range(65536))
            for i in range(0, 65536, 3):
                if rnd.random() < 0.4:
                    data[i] = rnd.choice([0xCB, 0xDD, 0xFD, 0xED])
            pm = bytearray(data)
            py = PyZ80(lambda a: pm[a], lambda a, v: pm.__setitem__(a, v))
            lib.zt_reset()
            ctypes.memmove(ctypes.addressof(cmem), bytes(data), 65536)
            for k in regs[:20]:
                v = rnd.getrandbits(16 if k in ("ix", "iy", "sp", "pc") else 8)
                setattr(py, k, v)
                setattr(cpu, k, v)
            for _ in range(1500):
                py.step()
                lib.zt_step()
                assert all(getattr(py, k) == getattr(cpu, k) for k in regs), seed
                if py.halted:
                    break
            assert bytes(pm) == bytes(cmem), seed


TF4 = os.environ.get("MDCONV_TF4_ROM", r"C:\Users\Vagno\Documents\roms\Thunder Force IV (Japan) (En).md")


@pytest.mark.skipif(not Path(TF4).exists(), reason="ROM do Thunder Force IV não encontrada")
def test_tf4_music_runs_on_the_z80_and_loops(tmp_path):
    from mdconv import megadrive
    b = megadrive.load_board(TF4)
    assert b.key == "tf4" and len(b.musics) == 48
    cap = b.capture(0x12)
    assert cap.t0 is not None
    assert abs(cap.end / megadrive.FRAME_HZ - 59.9) < 0.5 and cap.loop is not None and cap.loop < 30
    assert cap.fm_notes > 2000
    files = megadrive.export_board(b, tmp_path, select={0x0A}, log=lambda *_: None)
    assert sorted(f.name for f in files) == ["10_Musica_10.fur", "10_Musica_10.vgm", "10_Musica_10.xgm",
                                             "10_Musica_10_previa.wav"]


def test_save_state_and_override_replay_the_same_and_change_the_write():
    m = md68k.Machine(_rom(PROG))
    m.reset()
    st = m.save_state()
    m.call(0x200)
    a = m.events()
    m.load_state(st)
    m.call(0x200)
    b = m.events()
    assert a.tobytes() == b.tobytes()
    m.load_state(st)
    m.set_zoverride(0x1FFE, 0x42)
    m.call(0x200)
    assert m.zram()[0x1FFE] == 0x42


S2 = os.environ.get("MDCONV_S2_ROM", r"C:\Users\Vagno\Documents\roms\Sonic The Hedgehog 2 (World).md")


@pytest.mark.skipif(not Path(S2).exists(), reason="ROM do Sonic 2 não encontrada")
def test_sonic2_boot_profile_plays_and_loops():
    from mdconv import megadrive
    b = megadrive.load_board(S2)
    assert b.key == "sonic2"
    cap = b.capture(0x82)
    assert cap.fm_notes > 300 and cap.loop is not None
    assert 40 < cap.end / megadrive.FRAME_HZ < 47


def test_override_block_ignores_later_writes():
    m = md68k.Machine(_rom(PROG))
    m.reset()
    m.set_zoverride(0x1FFE, 0x42, lock="block")
    m.call(0x200)
    assert m.zram()[0x1FFE] == 0x42
    m.zram()[0x1FFE] = 0
    m.call(0x200)                                  # o jogo escreve de novo: ignorado
    assert m.zram()[0x1FFE] == 0


def test_keystack_finds_the_routine_that_called_the_key_on():
    from mdconv.mdanalyze import _call_target
    prog = dict(PROG)
    prog[0x300] = _w(0x4EB9, 0x0000, 0x0200, 0x4E75)        # jsr $200 ; rts
    rom = _rom(prog)
    m = md68k.Machine(rom)
    m.reset()
    m.keystack_arm()
    m.call(0x300)
    sp, _, pc, stack = m.keystack()
    assert sp and 0x200 <= pc < 0x220
    ret = int.from_bytes(stack[:4], "big")
    assert ret == 0x306 and _call_target(rom, ret) == 0x200


@pytest.mark.skipif(not os.environ.get("MDCONV_TF4_ROM"), reason="ROM do Thunder Force IV ausente")
def test_other_version_of_a_game_reuses_its_profile(tmp_path, monkeypatch):
    """Outra versão da mesma ROM (checksum diferente, driver de som igual) usa o perfil depois de testá-lo;
    uma versão em que o driver não roda cai em "sem perfil"."""
    from mdconv import megadrive
    monkeypatch.setenv("APPDATA", str(tmp_path))
    rom = bytearray(megadrive._read_rom(os.environ["MDCONV_TF4_ROM"]))
    patched = bytearray(rom)
    patched[0x18E:0x190] = b"\x4A\x20"                  # como uma tradução/patch: checksum novo
    patched[0xF0000:0xF0010] = b"\xAA" * 16              # bytes fora do driver de som
    f = tmp_path / "tf4_patch.md"
    f.write_bytes(bytes(patched))
    assert megadrive.find_profile(bytes(patched)) is None
    b = megadrive.load_board(str(f))
    assert b.key == "tf4" and "outra versão" in b.compat_note
    assert megadrive.find_profile(bytes(patched)) == "tf4"           # aprovado: fica guardado
    broken = bytearray(patched)
    broken[0x18E:0x190] = b"\x12\x34"
    broken[0x400:] = bytes(len(broken) - 0x400)          # outro jogo por dentro: o perfil não toca nada
    g = tmp_path / "tf4_broken.md"
    g.write_bytes(bytes(broken))
    with pytest.raises(ValueError, match="sem perfil"):
        megadrive.load_board(str(g))


def test_project_profiles_ship_with_the_converter_and_user_ones_win(tmp_path, monkeypatch):
    """Os perfis da pasta perfis_megadrive do projeto são carregados; um perfil do usuário para o mesmo
    jogo (mesmo código e checksum) tem prioridade."""
    import json
    from mdconv import mdanalyze
    monkeypatch.setenv("APPDATA", str(tmp_path))
    proj = mdanalyze.load_user_profiles()
    mk2 = [k for k, p in proj.items() if p["serial"] == "GM T-08134" and p["checksum"] == 0x4AEE]
    assert mk2 and mk2[0].startswith("proj:")
    mine = dict(json.loads(open(proj[mk2[0]]["_file"], encoding="utf-8").read()), songs=[3])
    (mdanalyze.profiles_dir() / "meu_mk2.json").write_text(json.dumps(mine), encoding="utf-8")
    both = mdanalyze.load_user_profiles()
    mk2 = [k for k, p in both.items() if p["serial"] == "GM T-08134" and p["checksum"] == 0x4AEE]
    assert mk2 == ["user:meu_mk2"] and both[mk2[0]]["songs"] == [3]


def test_profile_export_and_import(tmp_path, monkeypatch):
    """Salvar um perfil para compartilhar e importar o que alguém mandou (o botão "Perfis do Mega Drive…")."""
    from mdconv import mdanalyze
    monkeypatch.setenv("APPDATA", str(tmp_path / "eu"))
    prof = next(p for p in mdanalyze.load_user_profiles().values() if p["serial"] == "GM T-08134")
    out = mdanalyze.export_profile(prof, tmp_path)                 # pasta: usa o nome padrão
    assert out.name == "Mortal_Kombat_II_4AEE.json" or out.name.endswith("_4AEE.json")
    assert "_file" not in out.read_text(encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "amigo"))
    got = mdanalyze.import_profile(out)
    assert got.parent == mdanalyze.profiles_dir()
    assert any(k.startswith("user:") and p["serial"] == "GM T-08134"
               for k, p in mdanalyze.load_user_profiles().items())
    bad = tmp_path / "x.json"
    bad.write_text("{\"a\": 1}", encoding="utf-8")
    with pytest.raises(ValueError, match="não é um perfil"):
        mdanalyze.import_profile(bad)


def test_export_all_profiles(tmp_path, monkeypatch):
    import json
    from mdconv import mdanalyze
    monkeypatch.setenv("APPDATA", str(tmp_path / "eu"))
    for i in (1, 2):
        (mdanalyze.profiles_dir() / f"p{i}.json").write_text(json.dumps(dict(
            serial=f"GM X-{i}", checksum=i, songs=[1], driver="boot", short=f"Jogo{i}")), encoding="utf-8")
    out = mdanalyze.export_all_profiles(tmp_path / "saida")
    assert sorted(p.name for p in out) == ["Jogo1_0001.json", "Jogo2_0002.json"]


@pytest.mark.skipif(not os.environ.get("MDCONV_TF4_ROM"), reason="ROM do Thunder Force IV ausente")
def test_only_the_chosen_formats_are_written(tmp_path):
    """Caixas XGM / VGM / Furnace / Prévia: só os arquivos marcados são gravados."""
    from mdconv import extract, vgmconv
    g = extract.open_rom(os.environ["MDCONV_TF4_ROM"], None)
    try:
        vgmconv.set_output_formats({"xgm", "wav"})
        base = extract.run_export(g, str(tmp_path), song_ids={g.board.musics[1]}, log=lambda m: None)
    finally:
        vgmconv.set_output_formats(vgmconv.ALL_FORMATS)
    names = sorted(p.name for p in (Path(base) / "musicas").iterdir())
    assert len(names) == 2 and names[0].endswith(".xgm") and names[1].endswith("_previa.wav")
