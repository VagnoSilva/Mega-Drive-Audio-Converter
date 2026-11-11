/* Ponte simples para o Nuked-OPN2 usada pelo Mega Drive Audio Converter (GPLv3).
 * Chama somente a API pública do Nuked (ym3438.h, LGPL 2.1+). */
#include <stdlib.h>
#include "ym3438.h"

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif

/* Depois de uma escrita de dado o chip fica ocupado por 32 ciclos internos (ver
 * OPN2_DoIO); o endereço precisa de ao menos 1 ciclo para ser registrado. */
#define ADDR_CYCLES 2
#define DATA_CYCLES 32

typedef struct {
    ym3438_t chip;
    int acc_n;           /* ciclos acumulados na amostra atual (24 = 1 amostra) */
    int acc_l, acc_r;
    float *pending;      /* amostras geradas durante escritas, entregues no próximo render */
    int pend_n, pend_cap;
} NK;

static void push(NK *k, float l, float r) {
    if (k->pend_n + 1 > k->pend_cap) {
        int cap = k->pend_cap ? k->pend_cap * 2 : 1024;
        float *p = (float *)realloc(k->pending, sizeof(float) * 2 * cap);
        if (!p) return;
        k->pending = p;
        k->pend_cap = cap;
    }
    k->pending[2 * k->pend_n] = l;
    k->pending[2 * k->pend_n + 1] = r;
    k->pend_n++;
}

static void clock1(NK *k) {
    Bit16s buf[2];
    OPN2_Clock(&k->chip, buf);
    k->acc_l += buf[0];
    k->acc_r += buf[1];
    if (++k->acc_n == 24) {
        push(k, (float)k->acc_l, (float)k->acc_r);
        k->acc_n = k->acc_l = k->acc_r = 0;
    }
}

API NK *nk_create(int ym2612) {
    NK *k = (NK *)calloc(1, sizeof(NK));
    if (!k) return 0;
    OPN2_SetChipType(ym2612 ? ym3438_mode_ym2612 : 0);
    OPN2_Reset(&k->chip);
    return k;
}

API void nk_destroy(NK *k) {
    if (k) { free(k->pending); free(k); }
}

API void nk_write(NK *k, int port, int reg, int data) {
    int i;
    OPN2_Write(&k->chip, (Bit32u)(port * 2), (Bit8u)reg);
    for (i = 0; i < ADDR_CYCLES; i++) clock1(k);
    OPN2_Write(&k->chip, (Bit32u)(port * 2 + 1), (Bit8u)data);
    for (i = 0; i < DATA_CYCLES; i++) clock1(k);
}

/* Entrega n amostras estéreo (soma dos 24 ciclos = 6 canais × 4 fases). As amostras
 * geradas durante as escritas vêm primeiro, mantendo a linha do tempo exata. */
API void nk_render(NK *k, float *out, int n) {
    int i = 0, j;
    int take = k->pend_n < n ? k->pend_n : n;
    for (j = 0; j < 2 * take; j++) out[j] = k->pending[j];
    if (take < k->pend_n) {
        for (j = 0; j < 2 * (k->pend_n - take); j++) k->pending[j] = k->pending[2 * take + j];
    }
    k->pend_n -= take;
    i = take;
    while (i < n) {
        int before = k->pend_n;
        clock1(k);
        if (k->pend_n > before) {
            out[2 * i] = k->pending[2 * before];
            out[2 * i + 1] = k->pending[2 * before + 1];
            k->pend_n = before;
            i++;
        }
    }
}
