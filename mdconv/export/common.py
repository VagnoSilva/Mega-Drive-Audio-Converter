"""Partes comuns à exportação VGM e XGM: recorte do fluxo para o loop e mixagem do PCM."""
from __future__ import annotations

import numpy as np

from mdconv.driver.driver import Stream


def all_off_writes(fm_channels: int = 6) -> list[tuple]:
    """Encerra tudo: key-off nos 6 canais FM e volume mínimo nos 4 canais do PSG."""
    w = [("fm", 0, 0x28, (c & 3) | (4 if c >= 3 else 0)) for c in (0, 1, 2, 3, 4, 5)]
    w += [("psg", b) for b in (0x9F, 0xBF, 0xDF, 0xFF)]
    return w


def export_frames(stream: Stream) -> tuple[list[list[tuple]], int | None]:
    """Quadros a exportar e o quadro do loop.

    Com marcador ``loopEnd``, a música termina exatamente nele (o player volta para o
    ``loopStart``); notas que soariam além do ponto de volta são encerradas ali.
    Sem loop, exporta até o fim do último release.
    """
    end = stream.stats.get("loop_end_frame")
    frames = [list(f) for f in stream.frames]
    loop = stream.loop_frame
    if end is not None and loop is not None and end > loop:
        frames = frames[:end] + [[] for _ in range(max(0, end - len(frames)))]
        # Conversão direta de VGM: o estado no fim do loop é o que a música espera no começo dele
        # (o quadro do loop reescreve tudo), então não se desliga nada — a emenda fica sem buraco.
        if not stream.stats.get("seamless_loop"):
            frames[-1] = frames[-1] + all_off_writes()
    else:
        loop = None if (loop is None or loop >= len(frames)) else loop
        frames.append(all_off_writes())
    return frames, loop


_SETUP_FM = set(range(0x30, 0xA0)) | set(range(0xB0, 0xB7)) | {0x22, 0x27, 0x2B}


def loop_prefix(stream: Stream, frames, loop) -> int:
    """Quantas escritas do começo do quadro do loop são só preparação (patches, mudos) e ficam
    ANTES do ponto de loop — como num VGM bem feito: o loop não refaz a inicialização, e
    ferramentas como o xgmtool não perdem o marcador ao enxugar escritas repetidas.

    Só para a conversão direta de VGM; para no primeiro registro reescrito ou na primeira escrita
    musical (frequência, key-on, tom/volume do PSG que não seja mudo)."""
    if loop is None or not stream.stats.get("seamless_loop") or loop >= len(frames):
        return 0
    seen = set()
    n = 0
    for w in frames[loop]:
        if w[0] == "fm":
            key = ("fm", w[1], w[2])
            ok = w[2] in _SETUP_FM
        elif w[0] == "psg":
            b = w[1] & 0xFF
            key = ("psg", b & 0xF0)
            ok = (b & 0x90) == 0x90 and (b & 0x0F) == 0x0F       # volume mudo
        else:
            break
        if not ok or key in seen:
            break
        seen.add(key)
        n += 1
    return n


def pcm_mix(stream: Stream, frames: list[list[tuple]]) -> tuple[np.ndarray, int]:
    """Mistura os disparos de PCM numa só trilha de 8 bits com sinal, na taxa do XGM,
    somando e saturando (mesma regra da prévia). Retorna (trilha, nº de amostras saturadas)."""
    rate = stream.pcm_rate
    n = int(np.ceil(len(frames) / stream.fps * rate)) + 1
    mix = np.zeros(n, dtype=np.int32)
    events = []
    for f, writes in enumerate(frames):
        for w in writes:
            if w[0] == "pcm":
                events.append((int(round(f / stream.fps * rate)), w[1], w[2]))
    # cada disparo toca até acabar a amostra ou até outro disparo no mesmo canal
    by_ch: dict[int, list] = {}
    for start, ch, sid in events:
        by_ch.setdefault(ch, []).append((start, sid))
    for evs in by_ch.values():
        for i, (start, sid) in enumerate(evs):
            data = stream.pcm_samples[sid].astype(np.int32)
            stop = evs[i + 1][0] if i + 1 < len(evs) else n
            m = min(len(data), stop - start, n - start)
            if m > 0:
                mix[start:start + m] += data[:m]
    clipped = int(np.sum(np.abs(mix) > 127))
    return np.clip(mix, -128, 127).astype(np.int8), clipped
