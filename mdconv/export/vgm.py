"""Exportação VGM 1.71 (especificação do vgmrips).

* Clocks NTSC: YM2612 7.670.453 Hz, SN76489 3.579.545 Hz; ruído com realimentação 0x0009 e
  registrador de 16 bits (PSG da Sega); taxa de gravação 60 Hz.
* Comandos: 0x52/0x53 (YM2612 porta 0/1), 0x50 (PSG), esperas 0x61/0x62/0x63/0x7n,
  0x66 (fim).
* PCM: o VGM tem um único DAC, então os canais PCM da música (que o XGM mistura no Z80)
  vão pré-misturados num data block (tipo 0x00) e tocados com 0xE0 (posição) + 0x8n
  (escreve no DAC e espera n amostras). A mistura é a mesma da prévia.
* Loop: offset e duração no cabeçalho; no ponto de loop o driver recarrega o estado dos
  canais, e um 0xE0 reposiciona o PCM.
* GD3 com título, jogo, sistema e ferramenta.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from mdconv.chips.emulator import NTSC_FM_CLOCK, NTSC_PSG_CLOCK
from mdconv.driver.driver import Stream
from mdconv.export.common import export_frames, pcm_mix

VGM_RATE = 44100


@dataclass
class VgmInfo:
    size: int
    total_samples: int
    loop_samples: int
    pcm_bytes: int
    pcm_clipped: int
    commands: int


def _gd3(title: str, game: str, author: str, notes: str) -> bytes:
    fields = [title, "", game, "", "Sega Mega Drive / Genesis", "", author, "",
              "", "Mega Drive Audio Converter", notes]
    body = b"".join(f.encode("utf-16-le") + b"\x00\x00" for f in fields)
    return b"Gd3 " + struct.pack("<II", 0x100, len(body)) + body


def write_vgm(stream: Stream, path, title: str = "", game: str = "", author: str = "",
              notes: str = "") -> VgmInfo:
    frames, loop_frame = export_frames(stream)
    spf = VGM_RATE / stream.fps
    has_pcm = any(w[0] == "pcm" for fr in frames for w in fr)
    pcm = np.zeros(0, np.int8)
    clipped = 0
    if has_pcm:
        pcm, clipped = pcm_mix(stream, frames)
    pcm_u8 = (pcm.astype(np.int16) + 128).astype(np.uint8)

    # Linha do tempo em amostras de 44,1 kHz: (amostra, ordem, comando)
    events: list[tuple[int, int, bytes]] = []
    for f, writes in enumerate(frames):
        t = int(round(f * spf))
        for w in writes:
            if w[0] == "fm":
                if w[2] == 0x2A:          # DAC direto não é usado: o PCM vem do data block
                    continue
                events.append((t, 0, bytes([0x52 + w[1], w[2], w[3] & 0xFF])))
            elif w[0] == "psg":
                events.append((t, 0, bytes([0x50, w[1] & 0xFF])))
    if has_pcm:
        step = VGM_RATE / stream.pcm_rate
        # só onde há som PCM (economiza bytes); fora disso o DAC fica em 0x80 (silêncio)
        active = np.flatnonzero(pcm != 0)
        regions = []
        if len(active):
            start = prev = active[0]
            for i in active[1:]:
                if i - prev > int(stream.pcm_rate * 0.05):
                    regions.append((start, prev + 2))
                    start = i
                prev = i
            regions.append((start, prev + 2))
        loop_pcm = None if loop_frame is None else int(round(loop_frame / stream.fps * stream.pcm_rate))
        if loop_pcm is not None:
            # o loop precisa reposicionar o banco: corta a região que atravessa o ponto de loop
            new = []
            for a, b in regions:
                if a < loop_pcm < b:
                    new += [(a, loop_pcm), (loop_pcm, b)]
                else:
                    new.append((a, b))
            regions = new
        for a, b in regions:
            b = min(b, len(pcm_u8))
            events.append((int(round(a * step)), 1, b"\xE0" + struct.pack("<I", int(a))))
            for j in range(a, b):
                events.append((int(round(j * step)), 2, b"\x80"))   # 0x8n: n preenchido depois
    total = int(round(len(frames) * spf))
    loop_t = None if loop_frame is None else int(round(loop_frame * spf))
    # nada depois do fim (a trilha PCM é arredondada para cima)
    events = [e for e in events if e[0] < total]
    events.sort(key=lambda e: (e[0], e[1]))
    body = bytearray()
    if has_pcm:
        body += b"\x67\x66\x00" + struct.pack("<I", len(pcm_u8)) + pcm_u8.tobytes()
    loop_ofs = None
    cur = 0
    n_cmd = 0

    def wait(n):
        nonlocal body
        while n > 0:
            if n <= 16:
                body.append(0x70 + n - 1)
                n = 0
            elif n == 735:
                body.append(0x62)
                n = 0
            elif n == 882:
                body.append(0x63)
                n = 0
            else:
                k = min(n, 65535)
                body += b"\x61" + struct.pack("<H", k)
                n -= k

    i = 0
    while i < len(events):
        t = events[i][0]
        if loop_t is not None and loop_ofs is None and t >= loop_t:
            wait(loop_t - cur)
            cur = loop_t
            loop_ofs = len(body)
        wait(t - cur)
        cur = t
        while i < len(events) and events[i][0] == t:
            _, kind, cmd = events[i]
            if kind == 2:
                # 0x8n: escreve o próximo byte do banco no DAC e espera n (0..15) amostras;
                # a espera até o próximo evento vai embutida quando cabe.
                nxt = events[i + 1][0] if i + 1 < len(events) else total
                n = max(0, min(15, nxt - t))
                if loop_t is not None and loop_ofs is None and t < loop_t < t + n:
                    n = loop_t - t          # o ponto de loop precisa cair entre comandos
                body.append(0x80 | n)
                cur = t + n
            else:
                body += cmd
            n_cmd += 1
            i += 1
    if loop_t is not None and loop_ofs is None:
        wait(loop_t - cur)
        cur = loop_t
        loop_ofs = len(body)
    wait(total - cur)
    body.append(0x66)

    header = bytearray(0x100)
    data_start = 0x100
    gd3 = _gd3(title, game, author, notes)
    eof = data_start + len(body) + len(gd3)
    struct.pack_into("<4sIIIIIIII", header, 0, b"Vgm ", eof - 4, 0x171, NTSC_PSG_CLOCK, 0,
                     (data_start + len(body)) - 0x14, total,
                     (data_start + loop_ofs - 0x1C) if loop_ofs is not None else 0,
                     (total - loop_t) if loop_t is not None else 0)
    struct.pack_into("<I", header, 0x24, 60)
    struct.pack_into("<HBB", header, 0x28, 0x0009, 16, 0)
    struct.pack_into("<I", header, 0x2C, NTSC_FM_CLOCK)
    struct.pack_into("<I", header, 0x34, data_start - 0x34)
    with open(path, "wb") as fh:
        fh.write(header + body + gd3)
    return VgmInfo(eof, total, (total - loop_t) if loop_t is not None else 0, len(pcm_u8), clipped, n_cmd)
