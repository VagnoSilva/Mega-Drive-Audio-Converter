"""Gravação de arquivos Standard MIDI (formato 1)."""
import struct

# faixas do driver -> canais MIDI, evitando o canal 10 (índice 9, bateria GM); ele só é
# usado se houver uma 16ª faixa
CHANNELS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 9)


def _vlq(n):
    n = max(0, int(n))
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _chunk(events):
    """events: lista de (tick, bytes) já ordenada."""
    data = bytearray()
    last = 0
    for t, b in events:
        data += _vlq(t - last) + b
        last = t
    data += _vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(data)) + bytes(data)


def _meta(kind, payload):
    return bytes([0xFF, kind]) + _vlq(len(payload)) + payload


def _monophonic(events):
    """Cada faixa do driver toca uma nota por vez: corta a nota anterior quando outra começa."""
    events = sorted(events, key=lambda e: (e[0], e[1]))
    sounding = {}           # tecla -> True se a nota tem "off" agendado (nota com duração)
    stale = {}              # tecla -> nº de "off" agendados que devem ser ignorados
    result = []
    for e in events:
        t, pri, kind, a, b = e
        if kind in ("on", "ont"):
            for k, has_off in list(sounding.items()):
                result.append((t, 0, "off", k, 0))
                if has_off:
                    stale[k] = stale.get(k, 0) + 1
                del sounding[k]
            sounding[a] = (kind == "on")
            result.append((t, pri, "on", a, b))
        elif kind == "off":
            if stale.get(a):
                stale[a] -= 1
                continue
            if a in sounding:
                del sounding[a]
                result.append(e)
        else:
            result.append(e)
    return sorted(result, key=lambda e: (e[0], e[1]))


def write_midi(path, song, title="", track_names=None, oki_bank=1):
    ppqn = song.drv.ppqn
    tracks = []
    # Faixa 0: andamento
    cond = [(0, _meta(0x03, title.encode("latin-1", "replace")))]
    tempos = song.tempos or [(0, 500000)]
    if tempos[0][0] != 0:
        tempos = [(0, tempos[0][1])] + tempos
    for t, us in tempos:
        us = max(1, min(0xFFFFFF, us))
        cond.append((t, _meta(0x51, us.to_bytes(3, "big"))))
    cond.append((0, _meta(0x58, bytes([4, 2, 24, 8]))))
    # Marcadores de loop (mesma convenção do GBA Mus Ripper; lidos pelo Mega Drive Audio Converter)
    if song.loop_start is not None and song.loop_end is not None and song.loop_end <= song.length:
        cond.append((song.loop_start, _meta(0x06, b"loopStart")))
        cond.append((song.loop_end, _meta(0x06, b"loopEnd")))
    tracks.append(_chunk(sorted(cond, key=lambda x: x[0])))

    for tr in song.tracks:
        ch = CHANNELS[tr.idx % len(CHANNELS)]
        default = f"Oki {tr.idx - 7}" if tr.oki else f"FM {tr.idx + 1}"
        if getattr(song.drv, "ver", 0) >= 1000:
            default = f"Faixa {tr.idx + 1}"
        name = (track_names or {}).get(tr.idx) or default
        ev = [(0, _meta(0x03, name.encode("latin-1", "replace")))]
        bank = oki_bank if tr.oki else 0
        pbr = getattr(song, "pb_range", 2)
        ev += [(0, bytes([0xB0 | ch, 0, bank])), (0, bytes([0xB0 | ch, 32, 0])),
               # faixa de pitch bend (RPN 0)
               (0, bytes([0xB0 | ch, 101, 0])), (0, bytes([0xB0 | ch, 100, 0])),
               (0, bytes([0xB0 | ch, 6, pbr])), (0, bytes([0xB0 | ch, 38, 0])),
               (0, bytes([0xB0 | ch, 7, 127]))]
        if not any(e[2] == "prog" and e[0] == 0 for e in tr.events):
            ev.append((0, bytes([0xC0 | ch, 0])))
        for t, pri, kind, a, b in _monophonic(tr.events):
            if kind == "on":
                ev.append((t, bytes([0x90 | ch, a & 0x7F, b & 0x7F])))
            elif kind == "off":
                ev.append((t, bytes([0x80 | ch, a & 0x7F, 0])))
            elif kind == "prog":
                ev.append((t, bytes([0xC0 | ch, a & 0x7F])))
            elif kind == "cc":
                ev.append((t, bytes([0xB0 | ch, a & 0x7F, b & 0x7F])))
            elif kind == "bend":
                ev.append((t, bytes([0xE0 | ch, a & 0x7F, (a >> 7) & 0x7F])))
            elif kind == "bank":
                ev.append((t, bytes([0xB0 | ch, 0, a & 0x7F])))
                ev.append((t, bytes([0xB0 | ch, 32, 0])))
            elif kind == "pbr":
                ev += [(t, bytes([0xB0 | ch, 101, 0])), (t, bytes([0xB0 | ch, 100, 0])),
                       (t, bytes([0xB0 | ch, 6, a & 0x7F])), (t, bytes([0xB0 | ch, 38, 0]))]
        ev.sort(key=lambda x: x[0])   # sort estável: preserva off antes de on
        tracks.append(_chunk(ev))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), ppqn)
    with open(path, "wb") as f:
        f.write(header + b"".join(tracks))


def song_seconds(song):
    """Duração aproximada em segundos."""
    ppqn = song.drv.ppqn
    tempos = song.tempos or [(0, 500000)]
    if tempos[0][0] != 0:
        tempos = [(0, tempos[0][1])] + tempos
    total = 0.0
    for i, (t, us) in enumerate(tempos):
        nxt = tempos[i + 1][0] if i + 1 < len(tempos) else song.length
        if nxt > t:
            total += (min(nxt, song.length) - t) * us / 1e6 / ppqn
    return total
