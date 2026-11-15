"""Síntese aproximada do YM2151 (OPM) para gravar instrumentos FM como samples.

Não é uma emulação bit a bit: segue as fórmulas do chip (algoritmos, feedback, taxas de
envelope, DT1/DT2, MUL, TL) o suficiente para gerar samples com timbre próximo do original.
"""
import math

import numpy as np

CHIP_RATE = 3579545 / 64.0          # taxa de amostragem interna do chip
EG_RATE = CHIP_RATE / 3.0           # atualizações do gerador de envelope por segundo
EG_STEP_DB = 96.0 / 1024.0
DT2_FACTOR = (1.0, 2 ** (600 / 1200), 2 ** (781 / 1200), 2 ** (950 / 1200))
DT1_TAB = (
    (0,) * 32,
    (0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 6, 6, 7, 8, 8, 8, 8),
    (1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 6, 6, 7, 8, 8, 9, 10, 11, 12, 13, 14, 16, 16, 16, 16),
    (2, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 6, 6, 7, 8, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 20, 22, 22, 22, 22),
)
PMS_CENTS = (0, 5, 10, 20, 50, 100, 400, 700)
AMS_DB = (0, 23.9, 47.8, 95.6)
MOD_CYCLES = 8168 / 2 / 1024        # modulação máxima de um operador (em ciclos)
OUT_GAIN = 0.5                      # ganho de cada portadora no sample final

# ordem dos operadores: 0=M1 1=M2 2=C1 3=C2
# (fonte -> destino) e portadoras de cada algoritmo
ALGOS = {
    0: ([(0, 2), (2, 1), (1, 3)], [3]),
    1: ([(0, 1), (2, 1), (1, 3)], [3]),
    2: ([(0, 3), (2, 1), (1, 3)], [3]),
    3: ([(0, 2), (2, 3), (1, 3)], [3]),
    4: ([(0, 2), (1, 3)], [2, 3]),
    5: ([(0, 2), (0, 1), (0, 3)], [2, 1, 3]),
    6: ([(0, 2)], [2, 1, 3]),
    7: ([], [0, 2, 1, 3]),
}
EVAL_ORDER = (0, 2, 1, 3)           # M1, C1, M2, C2
SLOT_BITS = {0: 0x08, 2: 0x10, 1: 0x20, 3: 0x40}


def key_to_hz(key):
    return 440.0 * 2 ** ((key - 69) / 12.0)


def keycode5(key):
    k = max(0, key - 13)
    octv = min(7, k // 12)
    return octv * 4 + (k % 12) // 3


def _avg_inc(rate):
    """Incremento médio (unidades de 0,09375 dB) por atualização do envelope."""
    if rate < 2:
        return 0.0
    if rate >= 60:
        return 8.0
    return (4 + rate % 4) / 8.0 * 2 ** (rate // 4 - 11)


def _rate(r, ks, kc5, release=False):
    if not release and r == 0:
        return 0
    base = 4 * r + 2 if release else 2 * r
    return min(63, base + (kc5 >> (3 - ks)))


def db_per_sec(rate):
    return _avg_inc(rate) * EG_RATE * EG_STEP_DB


def envelope(op, key, t):
    """Atenuação do envelope (em unidades de 0,09375 dB) nos instantes t (segundos), tecla pressionada.
    Retorna (array, instante em que entra na fase de sustentação D2R)."""
    kc = keycode5(key)
    ar = _rate(op["AR"], op["KS"], kc)
    d1 = _rate(op["D1R"], op["KS"], kc)
    d2 = _rate(op["D2R"], op["KS"], kc)
    sl = 1023.0 if op["D1L"] == 15 else op["D1L"] * 32.0
    env = np.full(t.shape, 1023.0)
    if ar == 0:
        return env, float("inf")
    # ataque exponencial: v(n) = 1024*(1-inc/16)^n - 1
    if ar >= 62:
        t_att = 0.0
    else:
        inc = _avg_inc(ar)
        if inc <= 0:
            return env, float("inf")
        k = math.log(1 - min(inc, 15.9) / 16.0)
        n_att = math.log(1.0 / 1024.0) / k
        t_att = n_att / EG_RATE
        m = t < t_att
        env[m] = np.maximum(0.0, 1024.0 * np.exp(k * t[m] * EG_RATE) - 1.0)
    # decaimento 1 até D1L
    v1 = _avg_inc(d1) * EG_RATE
    t_d1 = t_att + (sl / v1 if v1 > 0 else float("inf"))
    m = (t >= t_att) & (t < t_d1)
    env[m] = (t[m] - t_att) * v1
    # decaimento 2
    v2 = _avg_inc(d2) * EG_RATE
    m = t >= t_d1
    env[m] = sl + (t[m] - t_d1) * v2
    return np.minimum(env, 1023.0), t_d1


def _op_freq(op, f):
    mul = 0.5 if op["MUL"] == 0 else float(op["MUL"])
    return f * DT2_FACTOR[op["DT2"]] * mul


def _dt1_hz(op, key):
    d = op["DT1"]
    v = DT1_TAB[d & 3][min(31, keycode5(key))] * CHIP_RATE / (1 << 20)
    return -v if d & 4 else v


def active_ops(ins):
    mask = ins.ch["SLOT_MASK"] & 0x78
    if mask == 0:
        mask = 0x78
    return {i for i in range(4) if mask & SLOT_BITS[i]}


def render(ins, key, f, fs, seconds):
    """Gera a forma de onda (float, -1..1) com a nota pressionada durante `seconds`."""
    n = int(seconds * fs)
    t = np.arange(n) / fs
    con = ins.ch["CON"]
    links, carriers = ALGOS[con]
    act = active_ops(ins)
    amps = {}
    sustain_start = {}
    for i in range(4):
        env, t_s = envelope(ins.op[i], key, t)
        db = env * EG_STEP_DB + ins.op[i]["TL"] * 0.75
        a = np.where(db >= 96.0, 0.0, 10 ** (-db / 20.0))
        if i not in act:
            a = np.zeros(n)
        amps[i] = a
        sustain_start[i] = t_s
    out = {}
    fl = ins.ch["FL"]
    for i in EVAL_ORDER:
        op = ins.op[i]
        freq = _op_freq(op, f) + _dt1_hz(op, key) * (0.5 if op["MUL"] == 0 else op["MUL"])
        phase = freq * t   # em ciclos
        mod = np.zeros(n)
        for src, dst in links:
            if dst == i:
                mod += out[src] * MOD_CYCLES
        if i == 0 and fl > 0:
            # feedback do M1: precisa ser calculado amostra por amostra
            fbk = 8168 * 2.0 ** (fl - 10) / 1024.0
            a = amps[0]
            res = np.empty(n)
            p1 = p2 = 0.0
            two_pi = 2 * math.pi
            ph = phase.tolist()
            al = a.tolist()
            sin = math.sin
            for j in range(n):
                v = al[j] * sin(two_pi * (ph[j] + (p1 + p2) * fbk))
                res[j] = v
                p2, p1 = p1, v
            out[0] = res
        else:
            out[i] = amps[i] * np.sin(2 * np.pi * (phase + mod))
    wave = np.zeros(n)
    for c in carriers:
        wave += out[c]
    wave *= OUT_GAIN
    car_times = [sustain_start[c] for c in carriers if c in act]
    return wave, amps, car_times


def lfo_params(ins):
    """Converte o LFO do OPM em parâmetros de vibrato/tremolo do SF2 (aproximação)."""
    res = {}
    lfo, ch = ins.lfo, ins.ch
    if lfo["LFRQ"] == 0 and lfo["PMD"] == 0 and lfo["AMD"] == 0:
        return res
    hz = 52.9 * 2 ** ((lfo["LFRQ"] - 255) / 16.0)
    if ch["PMS"] and lfo["PMD"]:
        res["vib_cents"] = int(PMS_CENTS[ch["PMS"]] * lfo["PMD"] / 127.0)
        res["vib_hz"] = hz
    if ch["AMS"] and lfo["AMD"] and any(o["AMS_EN"] for o in ins.op):
        res["trem_cb"] = int(min(960, AMS_DB[ch["AMS"]] * 10 * lfo["AMD"] / 127.0 / 2))
        res["trem_hz"] = hz
    return res


def render_sample(ins, key, target_rate=32000, max_seconds=1.2, oversample=2):
    """Renderiza a tecla `key` com loop perfeito no período da nota.

    Retorna dict com pcm (int16 bytes), rate, root key, loop, tempos e taxas de envelope."""
    f_nominal = key_to_hz(key)
    period = max(16, int(round(target_rate / f_nominal)))
    fs = int(round(f_nominal * period))
    f = fs / period                           # frequência exata: período inteiro em amostras
    act = active_ops(ins)
    _, carriers = ALGOS[ins.ch["CON"]]
    carriers = [c for c in carriers if c in act]

    # instante em que todas as portadoras e moduladores entraram na sustentação
    tt = np.array([0.0])
    starts = []
    for i in act:
        _, ts = envelope(ins.op[i], key, tt)
        starts.append(ts)
    t_loop = max([s for s in starts if math.isfinite(s)] + [0.0]) + 0.03
    t_loop = min(max(t_loop, 0.12), max_seconds)
    loop_cycles = max(1, int(round(0.25 * f)))
    loop_len = loop_cycles * period
    ls = int(round(t_loop * fs / period)) * period
    le = ls + loop_len
    total = le + 64

    fs_hi = fs * oversample
    wave, amps, _ = render(ins, key, f, fs_hi, total / fs + 1.0 / fs)
    if oversample > 1:
        k = np.array([1, 3, 3, 1], dtype=float) / 8.0
        wave = np.convolve(wave, k, mode="same")[::oversample]
    wave = wave[:total]
    if len(wave) < total:
        wave = np.pad(wave, (0, total - len(wave)))

    # crossfade para suavizar o ponto do loop (detune/DT2 não são periódicos)
    xf = min(loop_len // 2, int(0.05 * fs), ls)
    if xf > 8:
        r = np.linspace(0.0, 1.0, xf)
        a = wave[le - xf:le].copy()
        b = wave[ls - xf:ls]
        wave[le - xf:le] = a * (1 - r) + b * r
    wave[le:le + 64] = wave[ls:ls + 64]

    pcm = np.clip(np.round(wave * 32767), -32768, 32767).astype("<i2").tobytes()

    # Volume depois do ponto de loop: o envelope do SF2 continua o decaimento da portadora
    # mais forte (resto da fase D1R até D1L, ou a fase D2R) e faz o release (RR).
    decay_db_s, sustain_db, rr_db_s = 0.0, None, 1000.0
    if carriers:
        idx = min(ls * oversample, len(amps[carriers[0]]) - 1)
        strongest = max(carriers, key=lambda c: amps[c][idx])
        op = ins.op[strongest]
        kc = keycode5(key)
        env_now, t_d1 = envelope(op, key, np.array([ls / fs]))
        env_now = float(env_now[0])
        rr_db_s = db_per_sec(_rate(op["RR"], op["KS"], kc, release=True))
        if ls / fs < t_d1:                      # ainda na fase D1R
            sl = 1023.0 if op["D1L"] == 15 else op["D1L"] * 32.0
            decay_db_s = db_per_sec(_rate(op["D1R"], op["KS"], kc))
            sustain_db = max(0.0, (sl - env_now) * EG_STEP_DB)
        else:                                   # fase D2R
            decay_db_s = db_per_sec(_rate(op["D2R"], op["KS"], kc))
            sustain_db = 100.0
    silent = not carriers or float(np.max(np.abs(wave))) < 1e-4
    return dict(pcm=pcm, rate=fs, key=key, loop=(ls, le), hold=ls / fs,
                decay_db_s=decay_db_s, sustain_db=sustain_db, rr_db_s=rr_db_s, silent=silent)
