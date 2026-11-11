"""Geradores de MIDI e SF2 sintéticos com conteúdo conhecido, para os testes."""
from __future__ import annotations

import struct

import numpy as np


# ---------------------------------------------------------------- MIDI
def _vlq(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    return bytes(reversed(out))


def track_bytes(events: list[tuple[int, bytes]], running_status: bool = False,
                end_of_track: bool = True) -> bytes:
    """``events``: (tick absoluto, bytes do evento com status completo)."""
    body = bytearray()
    last = 0
    prev_status = None
    for tick, ev in sorted(events, key=lambda e: e[0]):
        body += _vlq(tick - last)
        last = tick
        st = ev[0]
        if running_status and 0x80 <= st < 0xF0 and st == prev_status:
            body += ev[1:]
        else:
            body += ev
        prev_status = st if 0x80 <= st < 0xF0 else None
    if end_of_track:
        body += _vlq(0) + b"\xFF\x2F\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def midi_file(tracks: list[bytes], ppq: int = 480, fmt: int = 1) -> bytes:
    return b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), ppq) + b"".join(tracks)


def on(ch, key, vel):
    return bytes([0x90 | ch, key, vel])


def off(ch, key, vel=64):
    return bytes([0x80 | ch, key, vel])


def cc(ch, num, val):
    return bytes([0xB0 | ch, num, val])


def pc(ch, prog):
    return bytes([0xC0 | ch, prog])


def bend(ch, value):  # value -8192..8191
    v = value + 8192
    return bytes([0xE0 | ch, v & 0x7F, v >> 7])


def tempo(us):
    return b"\xFF\x51\x03" + us.to_bytes(3, "big")


def meta_text(kind, text):
    b = text.encode()
    return bytes([0xFF, kind]) + _vlq(len(b)) + b


# ---------------------------------------------------------------- SF2
def _chunk(cid: str, body: bytes) -> bytes:
    pad = b"\x00" if len(body) & 1 else b""
    return cid.encode() + struct.pack("<I", len(body)) + body + pad


def _list(ltype: str, chunks: bytes) -> bytes:
    return _chunk("LIST", ltype.encode() + chunks)


def _name20(s: str) -> bytes:
    return s.encode("latin-1")[:20].ljust(20, b"\x00")


def gen(oper: int, amount: int | tuple[int, int]) -> tuple[int, int]:
    if isinstance(amount, tuple):
        lo, hi = amount
        return oper, lo | (hi << 8)
    return oper, amount & 0xFFFF


def sf2_file(samples: list[dict], instruments: list[dict], presets: list[dict]) -> bytes:
    """
    samples: {name, data(np.int16), rate, root, loop:(ls,le) relativo ao início, type}
    instruments: {name, zones:[[gens...]], global:[gens]|None}
    presets: {name, bank, program, zones:[[gens...]], global:[gens]|None}
    Gens no formato de ``gen()``; os builders colocam keyRange/velRange primeiro
    e o terminal (instrument/sampleID) por último, como manda a spec.
    """
    pcm = bytearray()
    shdr = bytearray()
    for s in samples:
        start = len(pcm) // 2
        pcm += np.asarray(s["data"], dtype="<i2").tobytes()
        end = len(pcm) // 2
        pcm += b"\x00\x00" * 46  # 46 zeros obrigatórios após cada sample
        ls, le = s.get("loop", (0, 0))
        shdr += struct.pack("<20sIIIIIBbHH", _name20(s["name"]), start, end, start + ls, start + le,
                            s.get("rate", 22050), s.get("root", 60), s.get("corr", 0), 0, s.get("type", 1))
    shdr += struct.pack("<20sIIIIIBbHH", _name20("EOS"), 0, 0, 0, 0, 0, 0, 0, 0, 0)

    def order(gens):
        first = [g for g in gens if g[0] == 43] + [g for g in gens if g[0] == 44]
        last = [g for g in gens if g[0] in (41, 53)]
        mid = [g for g in gens if g[0] not in (43, 44, 41, 53)]
        return first + mid + last

    def build(items, kind):
        hdr = bytearray()
        bag = bytearray()
        gens = bytearray()
        mods = bytearray()
        nbag = ngen = 0
        for it in items:
            zones = ([it["global"]] if it.get("global") else []) + it["zones"]
            if kind == "p":
                hdr += struct.pack("<20sHHHIII", _name20(it["name"]), it["program"], it["bank"], nbag, 0, 0, 0)
            else:
                hdr += struct.pack("<20sH", _name20(it["name"]), nbag)
            for z in zones:
                bag += struct.pack("<HH", ngen, 0)
                nbag += 1
                for oper, amt in order(z):
                    gens += struct.pack("<HH", oper, amt)
                    ngen += 1
        if kind == "p":
            hdr += struct.pack("<20sHHHIII", _name20("EOP"), 0, 0, nbag, 0, 0, 0)
        else:
            hdr += struct.pack("<20sH", _name20("EOI"), nbag)
        bag += struct.pack("<HH", ngen, 0)
        gens += struct.pack("<HH", 0, 0)
        mods += b"\x00" * 10
        return bytes(hdr), bytes(bag), bytes(mods), bytes(gens)

    phdr, pbag, pmod, pgen = build(presets, "p")
    inst, ibag, imod, igen = build(instruments, "i")
    info = _list("INFO", _chunk("ifil", struct.pack("<HH", 2, 1)) + _chunk("isng", b"EMU8000\x00")
                 + _chunk("INAM", b"Teste\x00"))
    sdta = _list("sdta", _chunk("smpl", bytes(pcm)))
    pdta = _list("pdta", _chunk("phdr", phdr) + _chunk("pbag", pbag) + _chunk("pmod", pmod)
                 + _chunk("pgen", pgen) + _chunk("inst", inst) + _chunk("ibag", ibag)
                 + _chunk("imod", imod) + _chunk("igen", igen) + _chunk("shdr", bytes(shdr)))
    body = b"sfbk" + info + sdta + pdta
    return b"RIFF" + struct.pack("<I", len(body)) + body
