/*
 * mdchips.c — emulação do YM2612 (OPN2) e do SN76489 (PSG da Sega) para o
 * Mega Drive Audio Converter.
 *
 * Escrito para o projeto (GPLv3). Segue o funcionamento documentado do OPN2
 * (estrutura equivalente à do núcleo fm.c do MAME): acumulador de fase de 20 bits,
 * detune por keycode, envelope de 10 bits (0,09375 dB por passo) com contador a cada
 * 3 amostras e as tabelas de incremento/deslocamento do OPN, 8 algoritmos, feedback
 * pela média das duas últimas saídas do operador 1, saída de 14 bits por operador,
 * soma por canal limitada a 14 bits e DAC de 9 bits.
 *
 * Simplificações conhecidas (documentadas para não esconder nada):
 *  - Sem os atrasos de uma amostra de algumas conexões internas do chip real.
 *  - Seno/exponencial calculados em ponto flutuante e quantizados para 14 bits,
 *    em vez das tabelas log-sin/exp de 12/10 bits do chip.
 *  - LFO de PM aplicado como desvio contínuo em cents (o chip usa uma tabela sobre
 *    os bits do F-number); profundidades por FMS iguais às documentadas.
 *  - SSG-EG, modo especial do canal 3 e timers não implementados.
 *  - Efeito "ladder" do YM2612 discreto (VA6 e outras placas com o chip separado):
 *    modelado como deslocamento de ±4 passos do DAC de 9 bits por canal — +4 quando a
 *    saída do canal é positiva; -3 (lado habilitado) ou -4 (lado mudo) quando negativa.
 *    Comportamento documentado a partir de testes em hardware (Nemesis/Eke-Eke).
 * A validação contra o Nuked-OPN2 (cycle-accurate) está prevista quando a rede
 * estiver liberada.
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif

enum { EG_ATT = 0, EG_DEC, EG_SUS, EG_REL, EG_OFF };

static const uint8_t dt_tab[4][32] = {
    {0},
    {0,0,0,0,1,1,1,1,1,1,1,1,2,2,2,2,2,3,3,3,4,4,4,5,5,6,6,7,8,8,8,8},
    {1,1,1,1,2,2,2,2,2,3,3,3,4,4,4,5,5,6,6,7,8,8,9,10,11,12,13,14,16,16,16,16},
    {2,2,2,2,2,3,3,3,4,4,4,5,5,6,6,7,8,8,9,10,11,12,13,14,16,17,19,20,22,22,22,22},
};

static const uint8_t eg_inc[19][8] = {
    {0,1,0,1,0,1,0,1}, {0,1,0,1,1,1,0,1}, {0,1,1,1,0,1,1,1}, {0,1,1,1,1,1,1,1},
    {1,1,1,1,1,1,1,1}, {1,1,1,2,1,1,1,2}, {1,2,1,2,1,2,1,2}, {1,2,2,2,1,2,2,2},
    {2,2,2,2,2,2,2,2}, {2,2,2,4,2,2,2,4}, {2,4,2,4,2,4,2,4}, {2,4,4,4,2,4,4,4},
    {4,4,4,4,4,4,4,4}, {4,4,4,8,4,4,4,8}, {4,8,4,8,4,8,4,8}, {4,8,8,8,4,8,8,8},
    {8,8,8,8,8,8,8,8}, {16,16,16,16,16,16,16,16}, {0,0,0,0,0,0,0,0},
};

/* bits 10..7 do F-number → 2 bits de nota para o keycode */
static const uint8_t note_tab[16] = {0,0,0,0,0,0,0,1,2,3,3,3,3,3,3,3};

/* Amostras por passo do LFO (128 passos por ciclo) para as 8 frequências */
static const int lfo_period[8] = {108, 77, 71, 67, 62, 44, 8, 5};
/* Profundidade de PM (cents de pico) por FMS */
static const double pm_cents[8] = {0, 3.4, 6.7, 10.0, 14.0, 20.0, 40.0, 80.0};
/* Deslocamento da AM (valor 0..126 em passos de envelope) por AMS */
static const int am_shift[4] = {8, 3, 1, 0};

typedef struct {
    /* parâmetros */
    int dt, mul, tl, ks, ar, d1r, d2r, sl, rr, am, ssg;
    /* estado */
    uint32_t phase;
    uint32_t inc;
    int vol;          /* atenuação do envelope, 0..1023 */
    int state;
    int ksr;
    int key;
    int32_t out0, out1;   /* duas últimas saídas (para feedback no op1) */
} Op;

typedef struct {
    Op op[4];          /* op[0]=OP1 ... op[3]=OP4 */
    int fnum, block, kc;
    int fnum_latch_hi; /* bits altos escritos em A4 aguardando A0 */
    int alg, fb;
    int left, right;
    int ams, fms;
} Channel;

typedef struct {
    double fm_clock, psg_clock, rate;
    Channel ch[6];
    int lfo_on, lfo_freq, lfo_cnt, lfo_step;
    int eg_timer;
    uint32_t eg_cnt;
    int dac_on, dac_data;
    /* PSG */
    int psg_period[4], psg_vol[4], psg_cnt[4], psg_out[4];
    int psg_latch_ch, psg_latch_type;
    int noise_ctrl;
    uint16_t lfsr;
    double psg_step, psg_acc;
    double psg_gain, fm_gain;
    int ladder;        /* 1 = YM2612 discreto (efeito ladder), 0 = YM3438/integrado */
    float sin_tab[1024];
    float att_tab[1024 + 1];
    float psg_vol_tab[16];
} Chip;

static void op_update_inc(Channel *c, Op *o) {
    int kc = c->kc;
    uint32_t fc = ((uint32_t)c->fnum << c->block) >> 1;
    int d = dt_tab[o->dt & 3][kc];
    int32_t f = (int32_t)fc + ((o->dt & 4) ? -d : d);
    f &= 0x1FFFF;
    uint32_t mul2 = o->mul ? (uint32_t)o->mul * 2 : 1;
    o->inc = ((uint32_t)f * mul2) >> 1;
    o->ksr = kc >> (3 - o->ks);
}

static void ch_update(Channel *c) {
    c->kc = (c->block << 2) | note_tab[c->fnum >> 7];
    for (int i = 0; i < 4; i++) op_update_inc(c, &c->op[i]);
}

static void key_on(Op *o) {
    if (o->state == EG_OFF || o->state == EG_REL || !o->key) {
        o->phase = 0;
        o->state = EG_ATT;
        if (2 * o->ar + o->ksr >= 62 && o->ar) {
            o->vol = 0;
            o->state = EG_DEC;
        }
    }
    o->key = 1;
}

static void key_off(Op *o) {
    if (o->key && o->state != EG_OFF) o->state = EG_REL;
    o->key = 0;
}

static void eg_step(Chip *chip, Op *o) {
    int r;
    switch (o->state) {
        case EG_ATT: r = o->ar ? 2 * o->ar + o->ksr : 0; break;
        case EG_DEC: r = o->d1r ? 2 * o->d1r + o->ksr : 0; break;
        case EG_SUS: r = o->d2r ? 2 * o->d2r + o->ksr : 0; break;
        case EG_REL: r = 2 + 4 * o->rr + o->ksr; break;
        default: return;
    }
    if (r == 0) return;
    if (r > 63) r = 63;
    int shift = r < 48 ? 11 - (r >> 2) : 0;
    int sel = r < 48 ? (r & 3) : (r < 60 ? 4 + (r - 48) : 16);
    if (chip->eg_cnt & ((1u << shift) - 1)) return;
    int inc = eg_inc[sel][(chip->eg_cnt >> shift) & 7];
    int sl = o->sl == 15 ? 31 * 32 : o->sl * 32;
    switch (o->state) {
        case EG_ATT:
            if (r >= 62) { o->vol = 0; }
            else o->vol += ((~o->vol) * inc) >> 4;
            if (o->vol <= 0) { o->vol = 0; o->state = EG_DEC; if (sl == 0) o->state = EG_SUS; }
            break;
        case EG_DEC:
            o->vol += inc;
            if (o->vol >= sl) o->state = EG_SUS;
            break;
        case EG_SUS:
            o->vol += inc;
            if (o->vol > 1023) o->vol = 1023;
            break;
        case EG_REL:
            o->vol += inc;
            if (o->vol >= 1023) { o->vol = 1023; o->state = EG_OFF; }
            break;
    }
}

static inline int32_t op_out(Chip *chip, Op *o, int32_t mod, int am_att) {
    int att = o->vol + (o->tl << 3) + (o->am ? am_att : 0);
    if (o->state == EG_OFF || att >= 1023) return 0;
    uint32_t idx = ((o->phase >> 10) + (uint32_t)mod) & 1023;
    float v = chip->sin_tab[idx] * chip->att_tab[att] * 8191.0f;
    return (int32_t)v;   /* truncamento em direção a zero, como a saída de 14 bits */
}

API Chip *md_create(double fm_clock, double psg_clock) {
    Chip *c = (Chip *)calloc(1, sizeof(Chip));
    c->fm_clock = fm_clock;
    c->psg_clock = psg_clock;
    c->rate = fm_clock / 144.0;
    for (int i = 0; i < 1024; i++) c->sin_tab[i] = (float)sin((i + 0.5) * 3.14159265358979323846 / 512.0);
    for (int i = 0; i < 1024; i++) c->att_tab[i] = (float)pow(10.0, -(i * 0.09375) / 20.0);
    c->att_tab[1024] = 0;
    for (int v = 0; v < 15; v++) c->psg_vol_tab[v] = (float)pow(10.0, -2.0 * v / 20.0);
    c->psg_vol_tab[15] = 0;
    c->psg_step = psg_clock / 16.0 / c->rate;
    c->psg_gain = 0.5;
    c->fm_gain = 1.0;
    c->ladder = 1;     /* padrão: YM2612 discreto, como no Mega Drive 1 VA6 */
    for (int i = 0; i < 6; i++) {
        c->ch[i].left = c->ch[i].right = 1;
        for (int j = 0; j < 4; j++) { c->ch[i].op[j].state = EG_OFF; c->ch[i].op[j].vol = 1023; c->ch[i].op[j].tl = 127; }
        ch_update(&c->ch[i]);
    }
    for (int i = 0; i < 4; i++) { c->psg_vol[i] = 15; c->psg_period[i] = 1; }
    c->lfsr = 0x8000;
    return c;
}

API void md_destroy(Chip *c) { free(c); }
API double md_rate(Chip *c) { return c->rate; }
API void md_set_gains(Chip *c, double fm, double psg) { c->fm_gain = fm; c->psg_gain = psg; }
API void md_set_ladder(Chip *c, int on) { c->ladder = on ? 1 : 0; }

static const int slot_of[4] = {0, 2, 1, 3}; /* offset de registrador 0,4,8,12 → OP1,OP3,OP2,OP4 */

API void md_write_fm(Chip *chip, int port, int reg, int data) {
    data &= 0xFF;
    if (port == 0 && reg < 0x30) {
        switch (reg) {
            case 0x22: chip->lfo_on = (data >> 3) & 1; chip->lfo_freq = data & 7;
                       if (!chip->lfo_on) { chip->lfo_step = 0; chip->lfo_cnt = 0; } break;
            case 0x28: {
                int c = data & 7;
                if ((c & 3) == 3) return;
                int ci = (c & 3) + ((c & 4) ? 3 : 0);
                Channel *ch = &chip->ch[ci];
                for (int k = 0; k < 4; k++) {
                    if (data & (0x10 << k)) key_on(&ch->op[k]); else key_off(&ch->op[k]);
                }
                break;
            }
            case 0x2A: chip->dac_data = data; break;
            case 0x2B: chip->dac_on = (data >> 7) & 1; break;
            default: break; /* 0x24-0x27 timers/modo canal 3: ignorados */
        }
        return;
    }
    int cofs = reg & 3;
    if (cofs == 3) return;
    int ci = cofs + (port ? 3 : 0);
    Channel *ch = &chip->ch[ci];
    if (reg >= 0x30 && reg < 0xA0) {
        Op *o = &ch->op[slot_of[(reg >> 2) & 3]];
        switch (reg & 0xF0) {
            case 0x30: o->dt = (data >> 4) & 7; o->mul = data & 15; op_update_inc(ch, o); break;
            case 0x40: o->tl = data & 127; break;
            case 0x50: o->ks = data >> 6; o->ar = data & 31; op_update_inc(ch, o); break;
            case 0x60: o->am = data >> 7; o->d1r = data & 31; break;
            case 0x70: o->d2r = data & 31; break;
            case 0x80: o->sl = data >> 4; o->rr = data & 15; break;
            case 0x90: o->ssg = data & 15; break;
        }
        return;
    }
    switch (reg & 0xFC) {
        case 0xA4: ch->fnum_latch_hi = data; break;
        case 0xA0:
            ch->block = (ch->fnum_latch_hi >> 3) & 7;
            ch->fnum = ((ch->fnum_latch_hi & 7) << 8) | data;
            ch_update(ch);
            break;
        case 0xB0: ch->fb = (data >> 3) & 7; ch->alg = data & 7; break;
        case 0xB4: ch->left = (data >> 7) & 1; ch->right = (data >> 6) & 1;
                   ch->ams = (data >> 4) & 3; ch->fms = data & 7; break;
    }
}

API void md_write_psg(Chip *c, int data) {
    data &= 0xFF;
    if (data & 0x80) {
        c->psg_latch_ch = (data >> 5) & 3;
        c->psg_latch_type = (data >> 4) & 1;
        int ch = c->psg_latch_ch;
        if (c->psg_latch_type) c->psg_vol[ch] = data & 15;
        else if (ch == 3) { c->noise_ctrl = data & 7; c->lfsr = 0x8000; }
        else c->psg_period[ch] = (c->psg_period[ch] & 0x3F0) | (data & 15);
    } else {
        int ch = c->psg_latch_ch;
        if (c->psg_latch_type) c->psg_vol[ch] = data & 15;
        else if (ch == 3) { c->noise_ctrl = data & 7; c->lfsr = 0x8000; }
        else c->psg_period[ch] = (c->psg_period[ch] & 15) | ((data & 0x3F) << 4);
    }
}

static void psg_tick(Chip *c) {
    for (int i = 0; i < 3; i++) {
        int p = c->psg_period[i] ? c->psg_period[i] : 1;
        if (--c->psg_cnt[i] <= 0) { c->psg_cnt[i] = p; c->psg_out[i] ^= 1; }
    }
    int nf = c->noise_ctrl & 3;
    int np = nf == 3 ? (c->psg_period[2] ? c->psg_period[2] : 1) : (0x10 << nf);
    if (--c->psg_cnt[3] <= 0) {
        c->psg_cnt[3] = np;
        c->psg_out[3] ^= 1;
        if (c->psg_out[3]) { /* o LFSR avança na borda de subida do contador */
            int fb = (c->noise_ctrl & 4) ? (((c->lfsr >> 0) ^ (c->lfsr >> 3)) & 1) : (c->lfsr & 1);
            c->lfsr = (uint16_t)((c->lfsr >> 1) | (fb << 15));
        }
    }
}

API void md_render(Chip *chip, float *out, int n) {
    for (int s = 0; s < n; s++) {
        /* LFO */
        int am_val = 0;
        double pm_tri = 0;
        if (chip->lfo_on) {
            if (++chip->lfo_cnt >= lfo_period[chip->lfo_freq]) {
                chip->lfo_cnt = 0;
                chip->lfo_step = (chip->lfo_step + 1) & 127;
            }
            int st = chip->lfo_step;
            am_val = st < 64 ? (63 - st) * 2 : (st - 64) * 2;          /* triângulo 0..126 */
            pm_tri = sin(st * 3.14159265358979323846 / 64.0);
        }
        /* Envelope a cada 3 amostras */
        if (++chip->eg_timer >= 3) {
            chip->eg_timer = 0;
            chip->eg_cnt++;
            for (int i = 0; i < 6; i++)
                for (int k = 0; k < 4; k++) eg_step(chip, &chip->ch[i].op[k]);
        }
        float L = 0, R = 0;
        for (int i = 0; i < 6; i++) {
            Channel *c = &chip->ch[i];
            int am_att = chip->lfo_on ? (am_val >> am_shift[c->ams]) : 0;
            int32_t o1, o2, o3, o4, acc;
            Op *op = c->op;
            int32_t fbmod = c->fb ? (op[0].out0 + op[0].out1) >> (10 - c->fb) : 0;
            o1 = op_out(chip, &op[0], fbmod, am_att);
            op[0].out1 = op[0].out0; op[0].out0 = o1;
            switch (c->alg) {
                case 0: o2 = op_out(chip, &op[1], o1 >> 1, am_att); o3 = op_out(chip, &op[2], o2 >> 1, am_att);
                        o4 = op_out(chip, &op[3], o3 >> 1, am_att); acc = o4; break;
                case 1: o2 = op_out(chip, &op[1], 0, am_att); o3 = op_out(chip, &op[2], (o1 + o2) >> 1, am_att);
                        o4 = op_out(chip, &op[3], o3 >> 1, am_att); acc = o4; break;
                case 2: o2 = op_out(chip, &op[1], 0, am_att); o3 = op_out(chip, &op[2], o2 >> 1, am_att);
                        o4 = op_out(chip, &op[3], (o1 + o3) >> 1, am_att); acc = o4; break;
                case 3: o2 = op_out(chip, &op[1], o1 >> 1, am_att); o3 = op_out(chip, &op[2], 0, am_att);
                        o4 = op_out(chip, &op[3], (o2 + o3) >> 1, am_att); acc = o4; break;
                case 4: o2 = op_out(chip, &op[1], o1 >> 1, am_att); o3 = op_out(chip, &op[2], 0, am_att);
                        o4 = op_out(chip, &op[3], o3 >> 1, am_att); acc = o2 + o4; break;
                case 5: o2 = op_out(chip, &op[1], o1 >> 1, am_att); o3 = op_out(chip, &op[2], o1 >> 1, am_att);
                        o4 = op_out(chip, &op[3], o1 >> 1, am_att); acc = o2 + o3 + o4; break;
                case 6: o2 = op_out(chip, &op[1], o1 >> 1, am_att); o3 = op_out(chip, &op[2], 0, am_att);
                        o4 = op_out(chip, &op[3], 0, am_att); acc = o2 + o3 + o4; break;
                default: o2 = op_out(chip, &op[1], 0, am_att); o3 = op_out(chip, &op[2], 0, am_att);
                         o4 = op_out(chip, &op[3], 0, am_att); acc = o1 + o2 + o3 + o4; break;
            }
            if (acc > 8191) acc = 8191;
            if (acc < -8192) acc = -8192;
            int v9;
            if (i == 5 && chip->dac_on) v9 = (chip->dac_data - 128) * 2;
            else v9 = acc >> 5;                     /* DAC de 9 bits */
            float g = (float)chip->fm_gain / 256.0f;
            float vl = c->left ? (float)v9 : 0.0f, vr = c->right ? (float)v9 : 0.0f;
            if (chip->ladder) {
                if (v9 < 0) { vl -= c->left ? 3.0f : 4.0f; vr -= c->right ? 3.0f : 4.0f; }
                else { vl += 4.0f; vr += 4.0f; }
            }
            L += vl * g;
            R += vr * g;
            /* avança fases (com PM do LFO, se houver) */
            double pm = (chip->lfo_on && c->fms) ? pow(2.0, pm_tri * pm_cents[c->fms] / 1200.0) : 1.0;
            for (int k = 0; k < 4; k++) {
                uint32_t inc = pm == 1.0 ? op[k].inc : (uint32_t)(op[k].inc * pm);
                op[k].phase = (op[k].phase + inc) & 0xFFFFF;
            }
        }
        /* PSG: média das saídas nos ticks deste período (filtro de caixa) */
        chip->psg_acc += chip->psg_step;
        float ps[4] = {0, 0, 0, 0};
        int ticks = 0;
        while (chip->psg_acc >= 1.0) {
            chip->psg_acc -= 1.0;
            psg_tick(chip);
            for (int i = 0; i < 3; i++) ps[i] += chip->psg_out[i] ? 1.0f : -1.0f;
            ps[3] += (chip->lfsr & 1) ? 1.0f : -1.0f;
            ticks++;
        }
        if (ticks) {
            float p = 0;
            for (int i = 0; i < 4; i++) p += ps[i] / ticks * chip->psg_vol_tab[chip->psg_vol[i]];
            p *= (float)chip->psg_gain;
            L += p; R += p;
        }
        out[2 * s] = L;
        out[2 * s + 1] = R;
    }
}

/* Estado de envelope de um operador (para testes e para a interface) */
API int md_op_state(Chip *c, int ch, int op, int *vol) {
    if (vol) *vol = c->ch[ch].op[op].vol;
    return c->ch[ch].op[op].state;
}
