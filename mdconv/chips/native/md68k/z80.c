/*
 * z80.c — Z80 em C para o Mega Drive Audio Converter (GPLv3).
 *
 * Tradução direta do Z80 em Python do projeto (mdconv/z80.py): mesmo decodificador x/y/z/p/q,
 * mesmos flags (S, Z, H, P/V, N, C como o Zilog; bits 3 e 5 não emulados), mesmas instruções
 * não documentadas que ele trata (IXH/IXL, SLL, cópia do resultado no DDCB). A diferença é o
 * tempo: aqui os ciclos são os T-states reais de cada instrução (o driver do Z80 conta ciclos
 * para tocar o PCM), não os aproximados do Python.
 *
 * Quem inclui este arquivo define antes:
 *   static uint8_t Z80_RD(uint16_t a);   static void Z80_WR(uint16_t a, uint8_t v);
 *   static uint8_t Z80_IN(uint16_t p);   static void Z80_OUT(uint16_t p, uint8_t v);
 */
#include <stdint.h>

#define ZSF 0x80
#define ZZF 0x40
#define ZHF 0x10
#define ZPF 0x04
#define ZNF 0x02
#define ZCF 0x01

typedef struct {
    uint8_t a, f, b, c, d, e, h, l;
    uint8_t a_, f_, b_, c_, d_, e_, h_, l_;
    uint16_t ix, iy, sp, pc;
    uint8_t i, r, iff1, iff2, im, halted, ei_delay;
    uint64_t cycles;
} Z80;

static uint8_t z_szp[256];
static int z_tables_ready;

static void z80_tables(void) {
    if (z_tables_ready) return;
    for (int i = 0; i < 256; i++) {
        int p = 0;
        for (int k = 0; k < 8; k++) p ^= (i >> k) & 1;
        z_szp[i] = (uint8_t)((i & ZSF) | (i == 0 ? ZZF : 0) | (p ? 0 : ZPF));
    }
    z_tables_ready = 1;
}

/* T-states das instruções sem prefixo (condicionais: valor do caso não tomado) */
static const uint8_t z_cyc[256] = {
    4,10,7,6,4,4,7,4, 4,11,7,6,4,4,7,4,
    8,10,7,6,4,4,7,4, 12,11,7,6,4,4,7,4,
    7,10,16,6,4,4,7,4, 7,11,16,6,4,4,7,4,
    7,10,13,6,11,11,10,4, 7,11,13,6,4,4,7,4,
    4,4,4,4,4,4,7,4, 4,4,4,4,4,4,7,4,
    4,4,4,4,4,4,7,4, 4,4,4,4,4,4,7,4,
    4,4,4,4,4,4,7,4, 4,4,4,4,4,4,7,4,
    7,7,7,7,7,7,4,7, 4,4,4,4,4,4,7,4,
    4,4,4,4,4,4,7,4, 4,4,4,4,4,4,7,4,
    4,4,4,4,4,4,7,4, 4,4,4,4,4,4,7,4,
    4,4,4,4,4,4,7,4, 4,4,4,4,4,4,7,4,
    4,4,4,4,4,4,7,4, 4,4,4,4,4,4,7,4,
    5,10,10,10,10,11,7,11, 5,10,10,0,10,17,7,11,
    5,10,10,11,10,11,7,11, 5,4,10,11,10,0,7,11,
    5,10,10,19,10,11,7,11, 5,4,10,4,10,0,7,11,
    5,10,10,4,10,11,7,11, 5,6,10,4,10,0,7,11,
};

static inline uint16_t z_bc(Z80 *z) { return (uint16_t)((z->b << 8) | z->c); }
static inline uint16_t z_de(Z80 *z) { return (uint16_t)((z->d << 8) | z->e); }
static inline uint16_t z_hl(Z80 *z) { return (uint16_t)((z->h << 8) | z->l); }
static inline void z_set_bc(Z80 *z, int v) { z->b = (uint8_t)(v >> 8); z->c = (uint8_t)v; }
static inline void z_set_de(Z80 *z, int v) { z->d = (uint8_t)(v >> 8); z->e = (uint8_t)v; }
static inline void z_set_hl(Z80 *z, int v) { z->h = (uint8_t)(v >> 8); z->l = (uint8_t)v; }

static void z80_reset(Z80 *z) {
    z80_tables();
    uint64_t cyc = z->cycles;
    for (unsigned k = 0; k < sizeof(Z80); k++) ((uint8_t *)z)[k] = 0;
    z->sp = 0xFFFF;
    z->cycles = cyc;
}

static inline uint8_t z_fetch(Z80 *z) { uint8_t v = Z80_RD(z->pc); z->pc++; return v; }
static inline uint16_t z_fetch16(Z80 *z) { uint16_t lo = z_fetch(z); return (uint16_t)(lo | (z_fetch(z) << 8)); }
static inline uint16_t z_rd16(uint16_t a) { return (uint16_t)(Z80_RD(a) | (Z80_RD((uint16_t)(a + 1)) << 8)); }
static inline void z_wr16(uint16_t a, uint16_t v) { Z80_WR(a, (uint8_t)v); Z80_WR((uint16_t)(a + 1), (uint8_t)(v >> 8)); }
static inline void z_push(Z80 *z, uint16_t v) { z->sp -= 2; z_wr16(z->sp, v); }
static inline uint16_t z_pop(Z80 *z) { uint16_t v = z_rd16(z->sp); z->sp += 2; return v; }

/* idx: 0 = HL, 1 = IX, 2 = IY */
static inline uint16_t z_idxreg(Z80 *z, int idx) { return idx == 1 ? z->ix : idx == 2 ? z->iy : z_hl(z); }
static inline void z_set_idxreg(Z80 *z, int idx, uint16_t v) {
    if (idx == 1) z->ix = v; else if (idx == 2) z->iy = v; else z_set_hl(z, v);
}

static uint16_t z_rp(Z80 *z, int p, int idx) {
    switch (p) {
        case 0: return z_bc(z);
        case 1: return z_de(z);
        case 2: return z_idxreg(z, idx);
        default: return z->sp;
    }
}

static void z_set_rp(Z80 *z, int p, int v, int idx) {
    v &= 0xFFFF;
    switch (p) {
        case 0: z_set_bc(z, v); break;
        case 1: z_set_de(z, v); break;
        case 2: z_set_idxreg(z, idx, (uint16_t)v); break;
        default: z->sp = (uint16_t)v;
    }
}

static inline uint16_t z_rp2(Z80 *z, int p, int idx) { return p == 3 ? (uint16_t)((z->a << 8) | z->f) : z_rp(z, p, idx); }
static inline void z_set_rp2(Z80 *z, int p, int v, int idx) {
    if (p == 3) { z->a = (uint8_t)(v >> 8); z->f = (uint8_t)v; } else z_set_rp(z, p, v, idx);
}

static uint8_t z_get_r(Z80 *z, int r, int idx, int disp) {
    switch (r) {
        case 0: return z->b;
        case 1: return z->c;
        case 2: return z->d;
        case 3: return z->e;
        case 4: return idx ? (uint8_t)(z_idxreg(z, idx) >> 8) : z->h;
        case 5: return idx ? (uint8_t)z_idxreg(z, idx) : z->l;
        case 6: return Z80_RD(idx ? (uint16_t)(z_idxreg(z, idx) + disp) : z_hl(z));
        default: return z->a;
    }
}

static void z_set_r(Z80 *z, int r, uint8_t v, int idx, int disp) {
    switch (r) {
        case 0: z->b = v; break;
        case 1: z->c = v; break;
        case 2: z->d = v; break;
        case 3: z->e = v; break;
        case 4: if (idx) z_set_idxreg(z, idx, (uint16_t)((z_idxreg(z, idx) & 0x00FF) | (v << 8))); else z->h = v; break;
        case 5: if (idx) z_set_idxreg(z, idx, (uint16_t)((z_idxreg(z, idx) & 0xFF00) | v)); else z->l = v; break;
        case 6: Z80_WR(idx ? (uint16_t)(z_idxreg(z, idx) + disp) : z_hl(z), v); break;
        default: z->a = v;
    }
}

static void z_alu(Z80 *z, int op, uint8_t v) {
    int a = z->a, r, c;
    uint8_t f;
    switch (op) {
        case 0: case 1:
            c = op == 1 ? (z->f & ZCF) : 0;
            r = a + v + c;
            f = z_szp[r & 255] & (ZSF | ZZF);
            if ((a ^ v ^ r) & 0x10) f |= ZHF;
            if ((~(a ^ v) & (a ^ r)) & 0x80) f |= ZPF;
            if (r > 255) f |= ZCF;
            z->a = (uint8_t)r; z->f = f;
            break;
        case 2: case 3: case 7:
            c = op == 3 ? (z->f & ZCF) : 0;
            r = a - v - c;
            f = (z_szp[r & 255] & (ZSF | ZZF)) | ZNF;
            if ((a ^ v ^ r) & 0x10) f |= ZHF;
            if (((a ^ v) & (a ^ r)) & 0x80) f |= ZPF;
            if (r < 0) f |= ZCF;
            z->f = f;
            if (op != 7) z->a = (uint8_t)r;
            break;
        case 4: z->a = (uint8_t)(a & v); z->f = z_szp[z->a] | ZHF; break;
        case 5: z->a = (uint8_t)(a ^ v); z->f = z_szp[z->a]; break;
        default: z->a = (uint8_t)(a | v); z->f = z_szp[z->a]; break;
    }
}

static uint8_t z_inc8(Z80 *z, uint8_t v) {
    uint8_t r = (uint8_t)(v + 1);
    z->f = (z->f & ZCF) | (z_szp[r] & (ZSF | ZZF)) | ((v & 0x0F) == 0x0F ? ZHF : 0) | (v == 0x7F ? ZPF : 0);
    return r;
}

static uint8_t z_dec8(Z80 *z, uint8_t v) {
    uint8_t r = (uint8_t)(v - 1);
    z->f = (z->f & ZCF) | (z_szp[r] & (ZSF | ZZF)) | ZNF | ((v & 0x0F) == 0 ? ZHF : 0) | (v == 0x80 ? ZPF : 0);
    return r;
}

static uint8_t z_rot(Z80 *z, int y, uint8_t v) {
    int c = z->f & ZCF, r, nc;
    switch (y) {
        case 0: r = (v << 1) | (v >> 7); nc = v >> 7; break;
        case 1: r = (v >> 1) | (v << 7); nc = v & 1; break;
        case 2: r = (v << 1) | c; nc = v >> 7; break;
        case 3: r = (v >> 1) | (c << 7); nc = v & 1; break;
        case 4: r = v << 1; nc = v >> 7; break;
        case 5: r = (v >> 1) | (v & 0x80); nc = v & 1; break;
        case 6: r = (v << 1) | 1; nc = v >> 7; break;
        default: r = v >> 1; nc = v & 1; break;
    }
    r &= 255;
    z->f = z_szp[r] | (nc ? ZCF : 0);
    return (uint8_t)r;
}

static uint16_t z_add16(Z80 *z, int x, int y) {
    int r = x + y;
    z->f = (z->f & (ZSF | ZZF | ZPF)) | (((x ^ y ^ r) & 0x1000) ? ZHF : 0) | (r > 0xFFFF ? ZCF : 0);
    return (uint16_t)r;
}

static uint16_t z_adc16(Z80 *z, int x, int y) {
    int c = z->f & ZCF, r = x + y + c;
    uint8_t f = (r & 0x8000 ? ZSF : 0) | ((r & 0xFFFF) == 0 ? ZZF : 0);
    if ((x ^ y ^ r) & 0x1000) f |= ZHF;
    if ((~(x ^ y) & (x ^ r)) & 0x8000) f |= ZPF;
    if (r > 0xFFFF) f |= ZCF;
    z->f = f;
    return (uint16_t)r;
}

static uint16_t z_sbc16(Z80 *z, int x, int y) {
    int c = z->f & ZCF, r = x - y - c;
    uint8_t f = ZNF | (r & 0x8000 ? ZSF : 0) | ((r & 0xFFFF) == 0 ? ZZF : 0);
    if ((x ^ y ^ r) & 0x1000) f |= ZHF;
    if (((x ^ y) & (x ^ r)) & 0x8000) f |= ZPF;
    if (r < 0) f |= ZCF;
    z->f = f;
    return (uint16_t)r;
}

static int z_cond(Z80 *z, int y) {
    uint8_t f = z->f;
    switch (y) {
        case 0: return !(f & ZZF);
        case 1: return !!(f & ZZF);
        case 2: return !(f & ZCF);
        case 3: return !!(f & ZCF);
        case 4: return !(f & ZPF);
        case 5: return !!(f & ZPF);
        case 6: return !(f & ZSF);
        default: return !!(f & ZSF);
    }
}

static int z80_irq(Z80 *z, uint8_t vector) {
    if (!z->iff1 || z->ei_delay) return 0;
    z->halted = 0;
    z->iff1 = z->iff2 = 0;
    z_push(z, z->pc);
    if (z->im == 2) { z->pc = z_rd16((uint16_t)((z->i << 8) | (vector & 0xFE))); z->cycles += 19; }
    else if (z->im == 1) { z->pc = 0x38; z->cycles += 13; }
    else { z->pc = vector & 0x38; z->cycles += 13; }
    return 1;
}

static void z80_nmi(Z80 *z) {
    z->halted = 0;
    z->iff2 = z->iff1;
    z->iff1 = 0;
    z_push(z, z->pc);
    z->pc = 0x66;
    z->cycles += 11;
}

static inline int z_disp(Z80 *z, int idx, int needs) {
    if (!idx || !needs) return 0;
    return (int8_t)z_fetch(z);
}

/* instrução sem prefixo (ou com DD/FD em idx). Devolve os T-states sem o prefixo. */
static int z_main(Z80 *z, uint8_t op, int idx) {
    int x = op >> 6, y = (op >> 3) & 7, zz = op & 7, p = y >> 1, q = y & 1;
    int base = z_cyc[op];
    int mem = idx ? 8 : 0;                  /* (IX+d): +8 em relação ao (HL) */
    if (x == 1) {
        if (op == 0x76) { z->halted = 1; return 4; }
        int disp = z_disp(z, idx, y == 6 || zz == 6);
        if (y == 6) z_set_r(z, 6, z_get_r(z, zz, 0, 0), idx, disp);
        else if (zz == 6) z_set_r(z, y, z_get_r(z, 6, idx, disp), 0, 0);
        else z_set_r(z, y, z_get_r(z, zz, idx, 0), idx, 0);
        return base + ((y == 6 || zz == 6) ? mem : 0);
    }
    if (x == 2) {
        int disp = z_disp(z, idx, zz == 6);
        z_alu(z, y, z_get_r(z, zz, idx, disp));
        return base + (zz == 6 ? mem : 0);
    }
    if (x == 0) {
        switch (zz) {
            case 0: {
                if (y == 0) return 4;
                if (y == 1) {
                    uint8_t t = z->a; z->a = z->a_; z->a_ = t;
                    t = z->f; z->f = z->f_; z->f_ = t;
                    return 4;
                }
                int d = (int8_t)z_fetch(z);
                if (y == 2) {
                    z->b--;
                    if (z->b) { z->pc = (uint16_t)(z->pc + d); return 13; }
                    return 8;
                }
                if (y == 3 || z_cond(z, y - 4)) { z->pc = (uint16_t)(z->pc + d); return 12; }
                return 7;
            }
            case 1:
                if (q == 0) { z_set_rp(z, p, z_fetch16(z), idx); return base; }
                z_set_rp(z, 2, z_add16(z, z_rp(z, 2, idx), z_rp(z, p, idx)), idx);
                return base;
            case 2:
                if (q == 0) {
                    if (p == 0) Z80_WR(z_bc(z), z->a);
                    else if (p == 1) Z80_WR(z_de(z), z->a);
                    else if (p == 2) z_wr16(z_fetch16(z), z_rp(z, 2, idx));
                    else Z80_WR(z_fetch16(z), z->a);
                } else {
                    if (p == 0) z->a = Z80_RD(z_bc(z));
                    else if (p == 1) z->a = Z80_RD(z_de(z));
                    else if (p == 2) z_set_rp(z, 2, z_rd16(z_fetch16(z)), idx);
                    else z->a = Z80_RD(z_fetch16(z));
                }
                return base;
            case 3:
                z_set_rp(z, p, z_rp(z, p, idx) + (q == 0 ? 1 : -1), idx);
                return base;
            case 4: case 5: {
                int disp = z_disp(z, idx, y == 6);
                uint8_t v = z_get_r(z, y, idx, disp);
                z_set_r(z, y, zz == 4 ? z_inc8(z, v) : z_dec8(z, v), idx, disp);
                return base + (y == 6 ? mem : 0);
            }
            case 6: {
                int disp = z_disp(z, idx, y == 6);
                z_set_r(z, y, z_fetch(z), idx, disp);
                return base + (y == 6 && idx ? 5 : 0);
            }
            default: {
                uint8_t a = z->a, f = z->f;
                switch (y) {
                    case 0: z->a = (uint8_t)((a << 1) | (a >> 7)); z->f = (f & (ZSF | ZZF | ZPF)) | (a >> 7); break;
                    case 1: z->a = (uint8_t)((a >> 1) | (a << 7)); z->f = (f & (ZSF | ZZF | ZPF)) | (a & 1); break;
                    case 2: z->a = (uint8_t)((a << 1) | (f & ZCF)); z->f = (f & (ZSF | ZZF | ZPF)) | (a >> 7); break;
                    case 3: z->a = (uint8_t)((a >> 1) | ((f & ZCF) << 7)); z->f = (f & (ZSF | ZZF | ZPF)) | (a & 1); break;
                    case 4: {
                        int c = f & ZCF, h = f & ZHF, n = f & ZNF, corr = 0, nc = c;
                        if (h || (a & 0x0F) > 9) corr |= 0x06;
                        if (c || a > 0x99) { corr |= 0x60; nc = ZCF; }
                        uint8_t r = n ? (uint8_t)(a - corr) : (uint8_t)(a + corr);
                        z->a = r;
                        z->f = (uint8_t)(z_szp[r] | n | (((a ^ r) & 0x10) ? ZHF : 0) | nc);
                        break;
                    }
                    case 5: z->a = a ^ 255; z->f = f | ZHF | ZNF; break;
                    case 6: z->f = (f & (ZSF | ZZF | ZPF)) | ZCF; break;
                    default: z->f = (uint8_t)(((f & (ZSF | ZZF | ZPF)) | ((f & ZCF) ? ZHF : 0)) | ((f & ZCF) ^ ZCF)); break;
                }
                return 4;
            }
        }
    }
    /* x == 3 */
    switch (zz) {
        case 0:
            if (z_cond(z, y)) { z->pc = z_pop(z); return 11; }
            return 5;
        case 1:
            if (q == 0) { z_set_rp2(z, p, z_pop(z), idx); return base; }
            if (p == 0) { z->pc = z_pop(z); return 10; }
            if (p == 1) {
                uint8_t t;
                t = z->b; z->b = z->b_; z->b_ = t; t = z->c; z->c = z->c_; z->c_ = t;
                t = z->d; z->d = z->d_; z->d_ = t; t = z->e; z->e = z->e_; z->e_ = t;
                t = z->h; z->h = z->h_; z->h_ = t; t = z->l; z->l = z->l_; z->l_ = t;
                return 4;
            }
            if (p == 2) { z->pc = z_rp(z, 2, idx); return 4; }
            z->sp = z_rp(z, 2, idx);
            return 6;
        case 2: {
            uint16_t nn = z_fetch16(z);
            if (z_cond(z, y)) z->pc = nn;
            return 10;
        }
        case 3:
            switch (y) {
                case 0: z->pc = z_fetch16(z); return 10;
                case 2: { uint8_t n = z_fetch(z); Z80_OUT((uint16_t)((z->a << 8) | n), z->a); return 11; }
                case 3: { uint8_t n = z_fetch(z); z->a = Z80_IN((uint16_t)((z->a << 8) | n)); return 11; }
                case 4: {
                    uint16_t v = z_rd16(z->sp);
                    z_wr16(z->sp, z_rp(z, 2, idx));
                    z_set_rp(z, 2, v, idx);
                    return 19;
                }
                case 5: { uint16_t de = z_de(z), hl = z_hl(z); z_set_de(z, hl); z_set_hl(z, de); return 4; }
                case 6: z->iff1 = z->iff2 = 0; return 4;
                default: z->iff1 = z->iff2 = 1; z->ei_delay = 1; return 4;
            }
        case 4: {
            uint16_t nn = z_fetch16(z);
            if (z_cond(z, y)) { z_push(z, z->pc); z->pc = nn; return 17; }
            return 10;
        }
        case 5:
            if (q == 0) { z_push(z, z_rp2(z, p, idx)); return 11; }
            { uint16_t nn = z_fetch16(z); z_push(z, z->pc); z->pc = nn; return 17; }
        case 6:
            z_alu(z, y, z_fetch(z));
            return 7;
        default:
            z_push(z, z->pc);
            z->pc = (uint16_t)(y * 8);
            return 11;
    }
}

static int z_cb(Z80 *z, int idx, int disp) {
    uint8_t op = z_fetch(z);
    int x = op >> 6, y = (op >> 3) & 7, zz = op & 7;
    uint16_t addr = 0;
    uint8_t v, r;
    if (idx) { addr = (uint16_t)(z_idxreg(z, idx) + disp); v = Z80_RD(addr); }
    else v = z_get_r(z, zz, 0, 0);
    if (x == 0) r = z_rot(z, y, v);
    else if (x == 1) {
        int b = v & (1 << y);
        z->f = (z->f & ZCF) | ZHF | (!b ? (ZZF | ZPF) : 0) | ((y == 7 && b) ? ZSF : 0);
        return idx ? 20 : (zz == 6 ? 12 : 8);
    } else if (x == 2) r = (uint8_t)(v & ~(1 << y));
    else r = (uint8_t)(v | (1 << y));
    if (idx) {
        Z80_WR(addr, r);
        if (zz != 6) z_set_r(z, zz, r, 0, 0);
        return 23;
    }
    z_set_r(z, zz, r, 0, 0);
    return zz == 6 ? 15 : 8;
}

static int z_ed(Z80 *z) {
    uint8_t op = z_fetch(z);
    int x = op >> 6, y = (op >> 3) & 7, zz = op & 7, p = y >> 1, q = y & 1;
    if (x == 1) {
        switch (zz) {
            case 0: {
                uint8_t v = Z80_IN(z_bc(z));
                if (y != 6) z_set_r(z, y, v, 0, 0);
                z->f = (z->f & ZCF) | z_szp[v];
                return 12;
            }
            case 1: Z80_OUT(z_bc(z), y == 6 ? 0 : z_get_r(z, y, 0, 0)); return 12;
            case 2: {
                uint16_t hl = z_hl(z);
                z_set_hl(z, q == 0 ? z_sbc16(z, hl, z_rp(z, p, 0)) : z_adc16(z, hl, z_rp(z, p, 0)));
                return 15;
            }
            case 3: {
                uint16_t nn = z_fetch16(z);
                if (q == 0) z_wr16(nn, z_rp(z, p, 0)); else z_set_rp(z, p, z_rd16(nn), 0);
                return 20;
            }
            case 4: { uint8_t v = z->a; z->a = 0; z_alu(z, 2, v); return 8; }
            case 5: z->pc = z_pop(z); z->iff1 = z->iff2; return 14;
            case 6: { static const uint8_t ims[8] = {0, 0, 1, 2, 0, 0, 1, 2}; z->im = ims[y]; return 8; }
            default:
                switch (y) {
                    case 0: z->i = z->a; return 9;
                    case 1: z->r = z->a; return 9;
                    case 2: z->a = z->i; z->f = (z->f & ZCF) | (z_szp[z->a] & (ZSF | ZZF)) | (z->iff2 ? ZPF : 0); return 9;
                    case 3: z->a = z->r; z->f = (z->f & ZCF) | (z_szp[z->a] & (ZSF | ZZF)) | (z->iff2 ? ZPF : 0); return 9;
                    case 4: {
                        uint8_t m = Z80_RD(z_hl(z));
                        Z80_WR(z_hl(z), (uint8_t)((z->a << 4) | (m >> 4)));
                        z->a = (z->a & 0xF0) | (m & 0x0F);
                        z->f = (z->f & ZCF) | z_szp[z->a];
                        return 18;
                    }
                    case 5: {
                        uint8_t m = Z80_RD(z_hl(z));
                        Z80_WR(z_hl(z), (uint8_t)((m << 4) | (z->a & 0x0F)));
                        z->a = (z->a & 0xF0) | (m >> 4);
                        z->f = (z->f & ZCF) | z_szp[z->a];
                        return 18;
                    }
                    default: return 9;
                }
        }
    }
    if (x == 2 && zz <= 3 && y >= 4) {
        int inc = (y == 4 || y == 6) ? 1 : -1, rep = y >= 6;
        if (zz == 0) {
            Z80_WR(z_de(z), Z80_RD(z_hl(z)));
            z_set_de(z, z_de(z) + inc);
            z_set_hl(z, z_hl(z) + inc);
            z_set_bc(z, z_bc(z) - 1);
            z->f = (z->f & (ZSF | ZZF | ZCF)) | (z_bc(z) ? ZPF : 0);
            if (rep && z_bc(z)) { z->pc -= 2; return 21; }
            return 16;
        }
        if (zz == 1) {
            uint8_t v = Z80_RD(z_hl(z));
            uint8_t r = (uint8_t)(z->a - v);
            z_set_hl(z, z_hl(z) + inc);
            z_set_bc(z, z_bc(z) - 1);
            z->f = (z->f & ZCF) | ZNF | (z_szp[r] & (ZSF | ZZF)) | (((z->a ^ v ^ r) & 0x10) ? ZHF : 0) | (z_bc(z) ? ZPF : 0);
            if (rep && z_bc(z) && r != 0) { z->pc -= 2; return 21; }
            return 16;
        }
        if (zz == 2) Z80_WR(z_hl(z), Z80_IN(z_bc(z)));
        else Z80_OUT(z_bc(z), Z80_RD(z_hl(z)));
        z_set_hl(z, z_hl(z) + inc);
        z->b--;
        z->f = (z->f & ZCF) | ZNF | (z->b == 0 ? ZZF : 0);
        if (rep && z->b) { z->pc -= 2; return 21; }
        return 16;
    }
    return 8;
}

static int z_indexed(Z80 *z, int idx) {
    uint8_t op = z_fetch(z);
    if (op == 0xCB) {
        int disp = (int8_t)z_fetch(z);
        return z_cb(z, idx, disp);
    }
    if (op == 0xDD || op == 0xFD || op == 0xED) { z->pc--; return 4; }
    return z_main(z, op, idx) + 4;
}

/* Executa uma instrução. Devolve os T-states. */
static int z80_step(Z80 *z) {
    int n;
    z->ei_delay = 0;
    if (z->halted) { z->cycles += 4; return 4; }
    z->r = (uint8_t)((z->r & 0x80) | ((z->r + 1) & 0x7F));
    uint8_t op = z_fetch(z);
    if (op == 0xDD) n = z_indexed(z, 1);
    else if (op == 0xFD) n = z_indexed(z, 2);
    else if (op == 0xCB) n = z_cb(z, 0, 0);
    else if (op == 0xED) n = z_ed(z);
    else n = z_main(z, op, 0);
    z->cycles += (uint64_t)n;
    return n;
}
