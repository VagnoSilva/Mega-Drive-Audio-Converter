"""Leitor/validador de VGM independente do escritor: confere a estrutura e toca o arquivo
no emulador (YM2612 + PSG + DAC pelo data block), para comparar com a prévia."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np


class VgmError(ValueError):
    pass


@dataclass
class VgmFile:
    version: int
    total_samples: int
    loop_offset: int | None            # posição absoluta no arquivo
    loop_samples: int
    ym2612_clock: int
    sn76489_clock: int
    sn_feedback: int
    sn_width: int
    rate: int
    commands: list[tuple[int, tuple]]   # (amostra, comando)
    data_bank: bytes
    gd3: dict
    loop_command_index: int | None = None
    warnings: list[str] = field(default_factory=list)


def read_vgm(data: bytes) -> VgmFile:
    if data[:4] != b"Vgm ":
        raise VgmError("identificador 'Vgm ' ausente")
    eof = struct.unpack_from("<I", data, 4)[0] + 4
    if eof != len(data):
        raise VgmError(f"EoF offset {eof} ≠ tamanho {len(data)}")
    ver = struct.unpack_from("<I", data, 8)[0]
    sn_clk, _, gd3_rel, total, loop_rel, loop_n, rate = struct.unpack_from("<IIIIIII", data, 0x0C)
    sn_fb, sn_w, _ = struct.unpack_from("<HBB", data, 0x28)
    ym_clk = struct.unpack_from("<I", data, 0x2C)[0]
    data_ofs = 0x34 + struct.unpack_from("<I", data, 0x34)[0] if ver >= 0x150 else 0x40
    loop_abs = 0x1C + loop_rel if loop_rel else None
    gd3_abs = 0x14 + gd3_rel if gd3_rel else None
    pos = data_ofs
    t = 0
    cmds: list[tuple[int, tuple]] = []
    bank = b""
    loop_idx = None
    end = gd3_abs or len(data)
    while True:
        if pos >= end:
            raise VgmError("fim dos dados sem comando 0x66")
        if loop_abs is not None and pos == loop_abs:
            loop_idx = len(cmds)
        c = data[pos]
        if c in (0x52, 0x53):
            cmds.append((t, ("fm", c - 0x52, data[pos + 1], data[pos + 2])))
            pos += 3
        elif c == 0x50:
            cmds.append((t, ("psg", data[pos + 1])))
            pos += 2
        elif c == 0x61:
            t += struct.unpack_from("<H", data, pos + 1)[0]
            pos += 3
        elif c == 0x62:
            t += 735
            pos += 1
        elif c == 0x63:
            t += 882
            pos += 1
        elif 0x70 <= c <= 0x7F:
            t += (c & 15) + 1
            pos += 1
        elif 0x80 <= c <= 0x8F:
            cmds.append((t, ("dac_next",)))
            t += c & 15
            pos += 1
        elif c == 0xE0:
            cmds.append((t, ("dac_seek", struct.unpack_from("<I", data, pos + 1)[0])))
            pos += 5
        elif c == 0x67:
            if data[pos + 1] != 0x66:
                raise VgmError("data block sem 0x66 de compatibilidade")
            typ = data[pos + 2]
            size = struct.unpack_from("<I", data, pos + 3)[0]
            if typ == 0x00:
                bank += data[pos + 7: pos + 7 + size]
            pos += 7 + size
        elif c == 0x66:
            break
        else:
            raise VgmError(f"comando 0x{c:02X} não esperado em 0x{pos:X}")
    if t != total:
        raise VgmError(f"soma das esperas {t} ≠ total de amostras {total}")
    if loop_abs is not None and loop_idx is None:
        raise VgmError("offset de loop não cai no início de um comando")
    gd3 = {}
    if gd3_abs:
        if data[gd3_abs:gd3_abs + 4] != b"Gd3 ":
            raise VgmError("GD3 inválido")
        size = struct.unpack_from("<I", data, gd3_abs + 8)[0]
        parts = data[gd3_abs + 12: gd3_abs + 12 + size].decode("utf-16-le").split("\x00")
        keys = ["title", "title_jp", "game", "game_jp", "system", "system_jp", "author",
                "author_jp", "date", "converter", "notes"]
        gd3 = dict(zip(keys, parts))
    return VgmFile(ver, total, loop_abs, loop_n, ym_clk, sn_clk, sn_fb, sn_w, rate, cmds, bank, gd3, loop_idx)


def play_vgm(v: VgmFile, sr_out: int = 44100, fm_chip=None) -> np.ndarray:
    """Toca o VGM (uma passada) no emulador. O DAC é somado como no renderizador da prévia."""
    from mdconv.chips.emulator import MegaDriveSound, dc_block, resample
    fm = fm_chip or MegaDriveSound(discrete_ym2612=True)
    psg = fm if fm_chip is None else MegaDriveSound(discrete_ym2612=False)
    rate = fm.rate
    ratio = rate / 44100.0
    out = []
    t_prev = 0
    acc = 0.0
    dac = 0.0
    bank_pos = 0
    for t, cmd in v.commands + [(v.total_samples, ("end",))]:
        if t > t_prev:
            acc += (t - t_prev) * ratio
            n = int(acc)
            acc -= n
            if n:
                a = fm.render(n)
                if psg is not fm:
                    a = a + psg.render(n)
                a = a + dac
                out.append(a)
            t_prev = t
        k = cmd[0]
        if k == "fm":
            if cmd[2] not in (0x2A, 0x2B):
                fm.write_fm(cmd[1], cmd[2], cmd[3])
        elif k == "psg":
            psg.write_psg(cmd[1])
        elif k == "dac_seek":
            bank_pos = cmd[1]
        elif k == "dac_next":
            if bank_pos < len(v.data_bank):
                dac = (v.data_bank[bank_pos] - 128) / 128.0
                bank_pos += 1
    x = dc_block(np.concatenate(out), rate)
    return resample(x, rate, sr_out)
