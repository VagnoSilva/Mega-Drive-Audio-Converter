"""Exportação XGM v1 (driver XGM do SGDK, formato de bin/xgm.txt).

Estrutura do arquivo::

    0000  "XGM "
    0004  tabela de amostras: 63 entradas × 4 bytes (endereço/256, tamanho/256, LE);
          entrada vazia = $FFFF $0001
    0100  tamanho do bloco de amostras / 256 (2 bytes)
    0102  versão (1)
    0103  flags: bit0 = 0 NTSC / 1 PAL
    0104  bloco de amostras (PCM 8 bits com sinal, cada amostra alinhada a 256 bytes)
    ....  tamanho do bloco de música (4 bytes)
    ....  bloco de música

Comandos de música: $00 espera um quadro; $1X PSG (X+1 bytes); $2X/$3X YM2612 porta 0/1
(X+1 pares reg/valor); $4X key on/off no $28 (X+1 valores); $5X id toca PCM (X bits 0–1 =
canal, bits 2–3 = prioridade; id 0 = parar); $7E dddddd loop (offset de 24 bits a partir
do início do bloco de música); $7F fim.

Regras deste conversor:
* os canais PCM da música são os escolhidos no arranjo (padrão 1 e 2 → canais 0 e 1 do
  XGM); os outros ficam livres para o jogo (CH3/CH4 dos lutadores no seu sound_engine);
* todas as batidas da música usam a mesma prioridade (3), para que cada golpe novo
  substitua o anterior no mesmo canal exatamente como na prévia — quem escolhe qual
  golpe sai é o arranjador, não o driver;
* escritas que o driver XGM controla sozinho são filtradas ($24–$27 timers/modo do
  canal 3, $2A dado do DAC, $2B liga DAC).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from mdconv.driver.driver import Stream
from mdconv.export.common import export_frames

ALIGN = 256
MAX_SAMPLES = 63
MUSIC_PCM_PRIORITY = 3
FILTERED_REGS = {0x24, 0x25, 0x26, 0x27, 0x2A, 0x2B}


class XgmError(ValueError):
    pass


@dataclass
class XgmInfo:
    size: int
    frames: int
    loop_frame: int | None
    samples: int
    sample_bytes: int
    music_bytes: int
    filtered_writes: int
    warnings: list[str] = field(default_factory=list)


def _encode_frame(writes: list[tuple], out: bytearray) -> int:
    """Codifica as escritas de um quadro, preservando a ordem; agrupa sequências do mesmo tipo."""
    filtered = 0
    groups: list[tuple[str, list]] = []
    for w in writes:
        if w[0] == "fm":
            port, reg, val = w[1], w[2], w[3] & 0xFF
            if port == 0 and reg in FILTERED_REGS:
                filtered += 1
                continue
            if port == 0 and reg == 0x28:
                key, item = "key", [val]
            else:
                key, item = f"ym{port}", [reg, val]
        elif w[0] == "psg":
            key, item = "psg", [w[1] & 0xFF]
        elif w[0] == "pcm":
            key, item = "pcm", [w[1], w[2]]
        else:
            continue
        if groups and groups[-1][0] == key and key != "pcm" and len(groups[-1][1]) < 16:
            groups[-1][1].append(item)
        else:
            groups.append((key, [item]))
    for key, items in groups:
        n = len(items) - 1
        if key == "psg":
            out.append(0x10 | n)
            out += bytes(i[0] for i in items)
        elif key in ("ym0", "ym1"):
            out.append((0x20 if key == "ym0" else 0x30) | n)
            for reg, val in items:
                out += bytes((reg, val))
        elif key == "key":
            out.append(0x40 | n)
            out += bytes(i[0] for i in items)
        elif key == "pcm":
            ch, sid = items[0]
            out += bytes((0x50 | (MUSIC_PCM_PRIORITY << 2) | ((ch - 1) & 3), sid))
    return filtered


def write_xgm(stream: Stream, path, pal: bool = False) -> XgmInfo:
    frames, loop_frame = export_frames(stream)
    warnings: list[str] = []
    ids = sorted(stream.pcm_samples)
    if len(ids) > MAX_SAMPLES:
        raise XgmError(f"{len(ids)} amostras PCM; o XGM aceita no máximo {MAX_SAMPLES}")
    if ids and (ids[0] < 1 or ids[-1] > MAX_SAMPLES):
        raise XgmError("ids de amostra fora de 1..63")
    if abs(stream.pcm_rate - 14000) > 1:
        warnings.append(f"taxa PCM {stream.pcm_rate} Hz ≠ 14000 Hz do XGM: a altura da bateria muda")
    for f, ws in enumerate(frames):
        for w in ws:
            if w[0] == "pcm" and not 1 <= w[1] <= 4:
                raise XgmError(f"canal PCM {w[1]} inválido no quadro {f}")

    table = bytearray()
    sdata = bytearray()
    for sid in range(1, MAX_SAMPLES + 1):
        s = stream.pcm_samples.get(sid)
        if s is None:
            table += struct.pack("<HH", 0xFFFF, 0x0001)
            continue
        raw = np.asarray(s, dtype=np.int8).tobytes()
        raw += b"\x00" * (-len(raw) % ALIGN)
        if len(sdata) // ALIGN > 0xFFFF or len(raw) // ALIGN > 0xFFFF:
            raise XgmError("bloco de amostras grande demais para a tabela do XGM")
        table += struct.pack("<HH", len(sdata) // ALIGN, len(raw) // ALIGN)
        sdata += raw

    music = bytearray()
    filtered = 0
    loop_ofs = None
    for f, ws in enumerate(frames):
        if f == loop_frame:
            loop_ofs = len(music)
        filtered += _encode_frame(ws, music)
        music.append(0x00)
    if loop_ofs is not None:
        if loop_ofs > 0xFFFFFF:
            raise XgmError("offset de loop não cabe em 24 bits")
        music += b"\x7E" + struct.pack("<I", loop_ofs)[:3]
    else:
        music.append(0x7F)

    data = (b"XGM " + bytes(table) + struct.pack("<HBB", len(sdata) // ALIGN, 1, 1 if pal else 0)
            + bytes(sdata) + struct.pack("<I", len(music)) + bytes(music))
    with open(path, "wb") as fh:
        fh.write(data)
    return XgmInfo(len(data), len(frames), loop_frame, len(ids), len(sdata), len(music), filtered, warnings)


@dataclass
class XgmFile:
    pal: bool
    version: int
    samples: dict[int, np.ndarray]
    frames: list[list[tuple]]
    loop_frame: int | None


def read_xgm(data: bytes) -> XgmFile:
    """Decodificador independente: confere a estrutura e devolve as escritas por quadro."""
    if data[:4] != b"XGM ":
        raise XgmError("identificador 'XGM ' ausente")
    slen = struct.unpack_from("<H", data, 0x100)[0] * ALIGN
    ver, flags = data[0x102], data[0x103]
    sblock = data[0x104:0x104 + slen]
    samples = {}
    for i in range(MAX_SAMPLES):
        a, n = struct.unpack_from("<HH", data, 4 + 4 * i)
        if (a, n) == (0xFFFF, 0x0001):
            continue
        if (a + n) * ALIGN > slen:
            raise XgmError(f"amostra {i + 1} fora do bloco de amostras")
        samples[i + 1] = np.frombuffer(sblock[a * ALIGN:(a + n) * ALIGN], dtype=np.int8).copy()
    mlen = struct.unpack_from("<I", data, 0x104 + slen)[0]
    m0 = 0x108 + slen
    if m0 + mlen != len(data):
        raise XgmError(f"tamanho do bloco de música {mlen} não bate com o arquivo")
    music = data[m0:m0 + mlen]
    frames: list[list[tuple]] = [[]]
    frame_at: dict[int, int] = {}
    pos = 0
    loop_frame = None
    while True:
        if pos >= len(music):
            raise XgmError("bloco de música sem $7E/$7F")
        frame_at.setdefault(pos, len(frames) - 1)
        c = music[pos]
        hi, n = c & 0xF0, (c & 0x0F) + 1
        if c == 0x00:
            frames.append([])
            pos += 1
        elif hi == 0x10:
            frames[-1] += [("psg", b) for b in music[pos + 1:pos + 1 + n]]
            pos += 1 + n
        elif hi in (0x20, 0x30):
            for k in range(n):
                frames[-1].append(("fm", (hi >> 4) - 2, music[pos + 1 + 2 * k], music[pos + 2 + 2 * k]))
            pos += 1 + 2 * n
        elif hi == 0x40:
            frames[-1] += [("fm", 0, 0x28, b) for b in music[pos + 1:pos + 1 + n]]
            pos += 1 + n
        elif hi == 0x50:
            sid = music[pos + 1]
            if sid and sid not in samples:
                raise XgmError(f"$5X toca amostra {sid} que não está na tabela")
            frames[-1].append(("pcm", (c & 3) + 1, sid, (c >> 2) & 3))
            pos += 2
        elif c == 0x7E:
            ofs = int.from_bytes(music[pos + 1:pos + 4], "little")
            if ofs not in frame_at:
                raise XgmError(f"loop aponta para {ofs}, que não é início de comando")
            loop_frame = frame_at[ofs]
            break
        elif c == 0x7F:
            break
        else:
            raise XgmError(f"comando ${c:02X} desconhecido em {pos}")
    if frames and not frames[-1]:
        frames.pop()
    return XgmFile(bool(flags & 1), ver, samples, frames, loop_frame)
