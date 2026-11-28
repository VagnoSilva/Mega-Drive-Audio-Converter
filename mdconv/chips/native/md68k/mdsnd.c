/* Mega Drive "de som": 68000 (Musashi) + Z80 (z80.c) + mapa de memória mínimo do console.
 * Roda rotinas de som do próprio jogo e registra tudo que é escrito no YM2612, no PSG e na RAM do Z80.
 *
 * Tempo: um relógio mestre (53,693175 MHz no NTSC); o 68000 anda 7 ciclos do mestre por ciclo, o Z80
 * 15. Quando o 68000 roda (md_call), o Z80 roda junto, intercalado, se não estiver em reset nem com o
 * barramento pedido pelo 68000. md_run_frames roda só o Z80, quadro a quadro, com a interrupção do
 * VBlank (IM 1). Os timers A e B do YM2612 são emulados (o status lido pelo Z80 traz os flags). */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "m68k.h"

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif

#define SENTINEL 0x800000u

typedef struct { uint32_t frame; uint32_t t44; uint16_t addr; uint8_t type; uint8_t val; } ev_t;
enum { EV_YM0 = 0, EV_YM1 = 1, EV_PSG = 2, EV_Z80 = 3, EV_BRK = 4, EV_ZBRK = 5, EV_RAM = 6, EV_REGS = 7 };
#define MAX_BRK 16
static uint32_t brk[MAX_BRK]; static int nbrk;

static uint8_t *rom; static uint32_t romlen;
static uint8_t ram[0x10000];
static uint8_t zram[0x2000];
static uint8_t ymaddr[2];
static uint8_t ymregs[2][256];
static uint32_t frame;
static uint32_t vdp_toggle, hv;
static uint8_t vdp_reg1, vdp_reg0, vdp_reg10;  /* reg0 bit 4 / reg10: interrupção do HBlank (nível 4) */
static int irq_pend, hcount;                 /* bit 5: interrupção do VBlank ligada */
static int vdp_second;                   /* 2ª palavra de um comando de endereço pendente */
static int game_mode;                    /* 1: rodando o jogo inteiro (md_run_game): status do VDP pelo relógio */
static uint32_t ym68k, ymz80;            /* quantas escritas no YM vieram de cada CPU */
static uint32_t kon68k, konz80;          /* quantos key-on (registrador $28 com operadores) de cada CPU */
static int ks_arm, ks_zaddr = -1;                       /* foto da pilha no próximo key-on do 68000 (acha a rotina do driver) */
static uint32_t ks_sp, ks_isp, ks_pc, irq_sp;
static uint8_t ks_buf[512];
static int ram_log;                      /* registra as escritas do 68000 na própria RAM (EV_RAM) */
#define MAX_ROV 16
static int rov_n; static int rov_addr[MAX_ROV], rov_val[MAX_ROV], rov_skip[MAX_ROV], rov_lock[MAX_ROV];
static uint8_t pad_mask;                 /* botões apertados no controle 1: U D L R B C A Start (bits 0–7) */
static uint8_t pad_th = 0x40;            /* último TH escrito pelo jogo na porta de dados do controle 1 */
static uint8_t hw_version = 0xA0;        /* $A10001: bit 7 = console de exportação (0 = japonês), bit 6 = PAL */
static ev_t *evs; static uint32_t nev, capev;
static uint64_t mclk;                                   /* relógio mestre */
#define MCLK_HZ 53693175ull
#define MCLK_FRAME (3420ull * 262ull)
static int frame_auto;                                  /* md_run_frames conta os quadros sozinho */
static uint64_t slice_mclk;                             /* mclk no começo da fatia atual do 68000 */
static int in_slice;
static uint32_t unmapped_reads, unmapped_writes;

static void logev(uint8_t type, uint16_t addr, uint8_t val) {
    if (nev == capev) { capev = capev ? capev * 2 : 65536; evs = realloc(evs, capev * sizeof(ev_t)); }
    evs[nev].frame = frame_auto ? (uint32_t)(mclk / MCLK_FRAME) : frame;
    evs[nev].t44 = (uint32_t)(mclk * 44100ull / MCLK_HZ);
    evs[nev].addr = addr; evs[nev].type = type; evs[nev].val = val; nev++;
}

/* ------------------------------------------------------------------ Z80 */
static uint8_t rd8(uint32_t a);
static void wr8(uint32_t a, uint8_t v);
#define MAX_ZOV 16
static int z_enabled = 1;
static int zov_n; static int zov_addr[MAX_ZOV], zov_val[MAX_ZOV], zov_skip[MAX_ZOV], zov_lock[MAX_ZOV];   /* troca o valor da próxima escrita do 68000 nesses endereços do Z80 */
static void ym_write(int port, uint8_t v);
static uint8_t ym_read(void);
static uint32_t z_bank;                        /* banco de 32 KB do 68000 visto em $8000 */
static int z_busreq, z_resetline = 1;
static uint64_t z_mclk;                        /* até onde o Z80 já rodou (relógio mestre) */
static uint64_t z_int_until;                   /* linha de interrupção ativa até este mclk */
static uint64_t z_next_vbl;                    /* próximo VBlank (mclk) */
static uint8_t Z80_RD(uint16_t a) {
    if (a < 0x4000) return zram[a & 0x1FFF];
    if (a < 0x6000) return ym_read();
    if (a >= 0x8000) return rd8((z_bank << 15) | (a & 0x7FFF));
    return 0xFF;
}
static void Z80_WR(uint16_t a, uint8_t v) {
    if (a < 0x4000) { zram[a & 0x1FFF] = v; return; }
    if (a < 0x6000) {
        int r = a & 3;
        if (r == 0) ymaddr[0] = v; else if (r == 2) ymaddr[1] = v;
        else { ymz80++; if (r == 1 && ymaddr[0] == 0x28 && (v & 0xF0)) konz80++; ym_write(r >> 1, v); }
        return;
    }
    if (a < 0x6100) { z_bank = ((z_bank >> 1) | ((uint32_t)(v & 1) << 8)) & 0x1FF; return; }
    if (a == 0x7F11 || a == 0x7F13 || a == 0x7F15 || a == 0x7F17) { logev(EV_PSG, 0, v); return; }
    if (a >= 0x8000) { wr8((z_bank << 15) | (a & 0x7FFF), v); return; }
}
static uint8_t Z80_IN(uint16_t p) { (void)p; return 0xFF; }
static void Z80_OUT(uint16_t p, uint8_t v) { (void)p; (void)v; }
#include "z80.c"
static Z80 zc;
static uint64_t z_steps;
static uint16_t zbrk[MAX_BRK]; static int nzbrk;      /* breakpoints do Z80 (registra IX) */

/* roda o Z80 até o relógio mestre `until` (se estiver livre); a interrupção do VBlank fica ativa por
 * uma linha (3420 mclk) e entra quando o Z80 a habilitar dentro dessa janela */
static void z_run_until(uint64_t until) {
    while (z_mclk < until) {
        if (z_mclk >= z_next_vbl) {
            z_int_until = z_next_vbl + 3420;
            z_next_vbl += MCLK_FRAME;
        }
        if (z_resetline || z_busreq) { z_mclk = until < z_next_vbl ? until : z_next_vbl; continue; }
        if (z_int_until && z_mclk < z_int_until && z80_irq(&zc, 0xFF)) z_int_until = 0;
        uint64_t save = mclk;
        mclk = z_mclk;                         /* eventos do Z80 com o tempo do Z80 */
        for (int b = 0; b < nzbrk; b++)
            if (zbrk[b] == zc.pc && !zc.halted) { logev(EV_ZBRK, zc.ix, (uint8_t)b); break; }
        int n = z80_step(&zc);
        z_steps++;
        z_mclk += (uint64_t)n * 15;
        mclk = save;
    }
}

/* ------------------------------------------------------------------ YM2612: escrita e timers */
static uint64_t tm_last;                       /* mclk da última atualização dos timers */
static int32_t tm_a, tm_b;                     /* mclk restantes até o estouro */
static uint8_t ym_status;

static void ym_timers(void) {
    uint64_t now = mclk;
    int64_t dt = (int64_t)(now - tm_last);
    tm_last = now;
    uint8_t ctl = ymregs[0][0x27];
    if (ctl & 1) {
        int32_t per = (int32_t)(1008 * (1024 - (((ymregs[0][0x24] << 2) | (ymregs[0][0x25] & 3)))));
        int64_t left = tm_a - dt;
        if (left <= 0) {
            if (ctl & 4) ym_status |= 1;
            left = per - ((-left) % per);
        }
        tm_a = (int32_t)left;
    }
    if (ctl & 2) {
        int32_t per = (int32_t)(1008 * 16 * (256 - ymregs[0][0x26]));
        int64_t left = tm_b - dt;
        if (left <= 0) {
            if (ctl & 8) ym_status |= 2;
            left = per - ((-left) % per);
        }
        tm_b = (int32_t)left;
    }
}

static void ym_write(int port, uint8_t v) {
    uint8_t reg = ymaddr[port];
    if (port == 0 && reg >= 0x24 && reg <= 0x27) {
        ym_timers();
        if (reg == 0x27) {
            uint8_t old = ymregs[0][0x27];
            if ((v & 1) && !(old & 1)) tm_a = (int32_t)(1008 * (1024 - (((ymregs[0][0x24] << 2) | (ymregs[0][0x25] & 3)))));
            if ((v & 2) && !(old & 2)) tm_b = (int32_t)(1008 * 16 * (256 - ymregs[0][0x26]));
            if (v & 0x10) ym_status &= (uint8_t)~1;
            if (v & 0x20) ym_status &= (uint8_t)~2;
        }
    }
    ymregs[port][reg] = v;
    logev(port ? EV_YM1 : EV_YM0, reg, v);
}

static uint8_t ym_read(void) { ym_timers(); return ym_status; }

static uint8_t rd8(uint32_t a) {
    a &= 0xFFFFFF;
    if (a < 0x400000) return a < romlen ? rom[a] : 0xFF;
    if (a >= 0xE00000) return ram[a & 0xFFFF];
    if (a >= 0xA00000 && a < 0xA04000) return zram[a & 0x1FFF];
    if (a >= 0xA04000 && a < 0xA06000) return ym_read();          /* status do YM (timers; nunca ocupado) */
    if (a == 0xA10001) return hw_version;                         /* versão: região, NTSC, sem Mega-CD */
    if (a == 0xA10003) {                                          /* controle de 3 botões */
        uint8_t m = pad_mask;
        if (pad_th) return (uint8_t)(0x40 | (~m & 0x3F));
        return (uint8_t)(~(((m & 3)) | ((m >> 2) & 0x30)) & 0x33);
    }
    if (a >= 0xA10002 && a < 0xA10020) return 0x7F;
    if (a == 0xA11100) return (uint8_t)(0xFE | (z_busreq ? 0 : 1)); /* bit 0: 0 = barramento com o 68000 */
    if (a == 0xA11101) return 0;
    if (a >= 0xC00000 && a < 0xC00020) {
        uint32_t r = a & 0x1F;
        if (r >= 4 && r < 8) {
            uint16_t s;
            if (game_mode) {
                uint32_t line = (uint32_t)((mclk % MCLK_FRAME) / 3420);
                s = 0x3600 | (line >= 224 ? 0x0008 : 0) | (((mclk % 3420) > 2900) ? 0x0004 : 0);
                vdp_second = 0;
            } else {
                vdp_toggle ^= 1;
                s = 0x3400 | (vdp_toggle ? 0x0008 : 0);
            }
            return (r & 1) ? (s & 0xFF) : (s >> 8);
        }
        if (r >= 8 && r < 0x10) {
            if (game_mode) {
                uint32_t line = (uint32_t)((mclk % MCLK_FRAME) / 3420);
                uint32_t vc = line < 0xEB ? line : line - 6;      /* contador V do NTSC: pula de $EA para $E5 */
                uint32_t hc = (uint32_t)((mclk % 3420) * 171 / 3420);
                return (r & 1) ? (uint8_t)hc : (uint8_t)vc;
            }
            hv++; return (r & 1) ? (hv & 0xFF) : ((hv >> 8) & 0xFF);
        }
        return 0;
    }
    if (a >= SENTINEL && a < SENTINEL + 0x100) return (a & 1) ? 0xFE : 0x60; /* bra.s * */
    unmapped_reads++;
    return 0xFF;
}

static void ks_snap(void) {
    ks_arm = 0;
    ks_sp = m68k_get_reg(NULL, M68K_REG_SP); ks_isp = irq_sp; ks_pc = m68k_get_reg(NULL, M68K_REG_PPC);
    for (int i = 0; i < 512; i++) ks_buf[i] = ram[(ks_sp + i) & 0xFFFF];
}

static void wr8(uint32_t a, uint8_t v) {
    a &= 0xFFFFFF;
    if (a >= 0xE00000) {
        uint16_t ra = a & 0xFFFF;
        for (int k = 0; k < rov_n; k++)
            if (rov_addr[k] == (int)ra) {
                if (rov_lock[k] == -4) return;                                          /* bloqueada */
                if (rov_skip[k] > 0) { rov_skip[k]--; continue; }
                v = (uint8_t)rov_val[k];
                if (rov_lock[k] == -3) { rov_lock[k] = -4; break; }   /* troca e ignora as próximas */
                if (rov_lock[k] >= 0) { rov_val[k] = rov_lock[k]; rov_lock[k] = -2; }  /* daqui em diante: trava */
                else if (rov_lock[k] == -1) rov_addr[k] = -1;                         /* troca de uma vez só */
                break;
            }
        ram[ra] = v;
        if (ram_log) logev(EV_RAM, ra, v);
        return;
    }
    if (a >= 0xA00000 && a < 0xA04000) {
        uint16_t za = a & 0x1FFF;
        for (int k = 0; k < zov_n; k++)
            if (zov_addr[k] == (int)za) {
                if (zov_lock[k] == -4) return;                                          /* bloqueada */
                if (zov_skip[k] > 0) { zov_skip[k]--; continue; }
                v = (uint8_t)zov_val[k];
                if (zov_lock[k] == -3) { zov_lock[k] = -4; break; }   /* troca e ignora as próximas */
                if (zov_lock[k] >= 0) { zov_val[k] = zov_lock[k]; zov_lock[k] = -2; }
                else if (zov_lock[k] == -1) zov_addr[k] = -1;
                break;
            }
        if (ks_arm && (int)za == ks_zaddr) ks_snap();
        zram[za] = v; logev(EV_Z80, za, v); return;
    }
    if (a >= 0xA04000 && a < 0xA06000) {
        uint32_t r = a & 3;
        if (r == 0) ymaddr[0] = v;
        else if (r == 2) ymaddr[1] = v;
        else { ym68k++; if (r == 1 && ymaddr[0] == 0x28 && (v & 0xF0)) {
            kon68k++;
            if (ks_arm && ks_zaddr < 0) ks_snap();
        } ym_write(r >> 1, v); }
        return;
    }
    if (a >= 0xC00010 && a < 0xC00018) { if (a & 1) logev(EV_PSG, 0, v); return; }
    if (a >= 0xA06000 && a < 0xA06100) return;                    /* banco do Z80 */
    if (a == 0xA10003) { pad_th = v & 0x40; return; }
    if (a == 0xA11100 || a == 0xA11200) {
        /* antes de mudar o BUSREQ ou o RESET, o Z80 anda até o instante exato desta escrita: jogos que
         * soltam o barramento por poucos ciclos (Sonic 1) dependem disso */
        if (in_slice && z_enabled) z_run_until(slice_mclk + (uint64_t)m68k_cycles_run() * 7);
        if (a == 0xA11100) z_busreq = v & 1;
        else if (!(v & 1)) z_resetline = 1;
        else if (z_resetline) { z_resetline = 0; z80_reset(&zc); }
        return;
    }
    if (a >= 0xA10000 && a < 0xA14100) return;                    /* E/S, TMSS */
    if (a >= 0xC00000 && a < 0xC00020) return;                    /* VDP */
    unmapped_writes++;
}

void md_instr_hook(unsigned int pc) {
    for (int i = 0; i < nbrk; i++)
        if (brk[i] == pc) {
            uint32_t a3 = m68k_get_reg(NULL, M68K_REG_A3);
            logev(EV_BRK, (uint16_t)a3, (uint8_t)i);
            /* logo depois: D0 (16 bits) e D1 (8 bits) no ponto de parada — o "pedido" de quem chamou */
            logev(EV_REGS, (uint16_t)m68k_get_reg(NULL, M68K_REG_D0), (uint8_t)m68k_get_reg(NULL, M68K_REG_D1));
        }
}

unsigned int m68k_read_memory_8(unsigned int a) { return rd8(a); }
unsigned int m68k_read_memory_16(unsigned int a) { return (rd8(a) << 8) | rd8(a + 1); }
unsigned int m68k_read_memory_32(unsigned int a) { return (m68k_read_memory_16(a) << 16) | m68k_read_memory_16(a + 2); }
unsigned int m68k_read_disassembler_16(unsigned int a) { return m68k_read_memory_16(a); }
unsigned int m68k_read_disassembler_32(unsigned int a) { return m68k_read_memory_32(a); }
void m68k_write_memory_8(unsigned int a, unsigned int v) { wr8(a, v); }
static void vdp_ctrl(uint16_t v) {
    if (vdp_second) { vdp_second = 0; return; }
    if ((v & 0xC000) == 0x8000) {
        int r = (v >> 8) & 0x1F;
        if (r == 1) vdp_reg1 = (uint8_t)v; else if (r == 0) vdp_reg0 = (uint8_t)v; else if (r == 10) vdp_reg10 = (uint8_t)v;
        return;
    }
    vdp_second = 1;
}

void m68k_write_memory_16(unsigned int a, unsigned int v) {
    a &= 0xFFFFFF;
    if (a >= 0xC00004 && a < 0xC00008) { vdp_ctrl((uint16_t)v); return; }
    if (a >= 0xA00000 && a < 0xA06000) { wr8(a, v >> 8); return; }  /* área do Z80: só o byte alto */
    if (a >= 0xC00010 && a < 0xC00018) { wr8(a | 1, v & 0xFF); return; }
    wr8(a, v >> 8); wr8(a + 1, v);
}
void m68k_write_memory_32(unsigned int a, unsigned int v) {
    if ((a & 0xFFFFFF) >= 0xC00004 && (a & 0xFFFFFF) < 0xC00008) { vdp_ctrl((uint16_t)(v >> 16)); vdp_ctrl((uint16_t)v); return; }
    m68k_write_memory_16(a, v >> 16); m68k_write_memory_16(a + 2, v & 0xFFFF);
}
int md_int_ack(int level) { irq_sp = m68k_get_reg(NULL, M68K_REG_SP); irq_pend = 0; m68k_set_irq(0); (void)level; return M68K_INT_ACK_AUTOVECTOR; }

API int md_load(const uint8_t *data, uint32_t len) {
    free(rom); rom = malloc(len); memcpy(rom, data, len); romlen = len;
    m68k_init(); m68k_set_cpu_type(M68K_CPU_TYPE_68000);
    return 0;
}

API void md_reset(void) {
    memset(ram, 0, sizeof ram); memset(zram, 0, sizeof zram); memset(ymregs, 0, sizeof ymregs);
    nev = 0; frame = 0; unmapped_reads = unmapped_writes = 0;
    mclk = z_mclk = tm_last = 0; z_next_vbl = MCLK_FRAME; z_int_until = 0;
    tm_a = tm_b = 0; ym_status = 0; ymaddr[0] = ymaddr[1] = 0;
    z_bank = 0; z_busreq = 0; z_resetline = 1; z_steps = 0; frame_auto = 0;
    z_enabled = 1; nbrk = 0; nzbrk = 0;              /* o reset volta tudo ao padrão */
    vdp_reg1 = 0; vdp_reg0 = 0; vdp_reg10 = 0xFF; irq_pend = 0; vdp_second = 0; game_mode = 0; ym68k = ymz80 = 0; zov_n = 0; pad_mask = 0; pad_th = 0x40;
    kon68k = konz80 = 0; ram_log = 0; rov_n = 0; ks_arm = 0;
    z_next_vbl = 224ull * 3420ull;                   /* o VBlank começa na linha 224 */
    zc.cycles = 0; z80_reset(&zc);
    m68k_pulse_reset();
    m68k_set_reg(M68K_REG_SR, 0x2700);
}

/* liga/desliga o Z80 (perfis cujo PCM é reproduzido em Python desligam) */
API void md_enable_z80(int on) { z_enabled = on ? 1 : 0; }
/* 1: o número do quadro dos eventos vem do relógio mestre (perfis com o driver no Z80) */
API void md_set_frame_auto(int on) { frame_auto = on ? 1 : 0; }

/* Chama a sub-rotina em `addr` com D0 = d0. Devolve 0 se voltou, 1 se estourou o limite de ciclos. */
API int md_call(uint32_t addr, uint32_t d0, uint32_t sp, uint32_t max_cycles) {
    uint32_t used = 0;
    sp -= 4;
    m68k_write_memory_32(sp, SENTINEL);
    m68k_set_reg(M68K_REG_SP, sp);
    m68k_set_reg(M68K_REG_A7, sp);
    m68k_set_reg(M68K_REG_D0, d0);
    m68k_set_reg(M68K_REG_SR, 0x2700);
    m68k_set_reg(M68K_REG_PC, addr);
    while (m68k_get_reg(NULL, M68K_REG_PC) != SENTINEL) {
        slice_mclk = mclk; in_slice = 1;
        int n = m68k_execute(64);
        in_slice = 0;
        used += (uint32_t)n;
        mclk += (uint64_t)n * 7;
        if (z_enabled) z_run_until(mclk);
        if (used > max_cycles) return 1;
    }
    return 0;
}

/* Roda só o Z80 por `n` quadros (o 68000 fica parado), com a interrupção do VBlank. */
API void md_run_frames(uint32_t n) {
    for (uint32_t i = 0; i < n; i++) {
        uint64_t target = (mclk / MCLK_FRAME + 1) * MCLK_FRAME;
        if (z_enabled) z_run_until(target);
        mclk = target;
    }
}

/* Roda o jogo inteiro a partir do reset por `n` quadros: 68000 + Z80, interrupção do VBlank (nível 6)
 * na linha 224 quando o VDP a liga. Devolve 0, ou 1 se o 68000 parou (exceção dupla etc.). */
API int md_run_game(uint32_t n) {
    game_mode = 1; frame_auto = 1;
    for (uint32_t i = 0; i < n; i++) {
        uint64_t f0 = (mclk / MCLK_FRAME) * MCLK_FRAME;
        uint64_t vbl = f0 + 224ull * 3420ull, end = f0 + MCLK_FRAME;
        int fired = mclk >= vbl;
        uint32_t line = (uint32_t)((mclk - f0) / 3420);          /* próxima linha cujo HBlank vamos tratar */
        if ((mclk - f0) % 3420 > 2900) line++;
        while (mclk < end) {
            if (!fired && mclk >= vbl) { fired = 1; if (vdp_reg1 & 0x20) { irq_pend = 6; m68k_set_irq(6); } }
            while (line < 262 && mclk >= f0 + line * 3420ull + 2900) {   /* contador do HBlank */
                if (line == 0 || line > 224) hcount = vdp_reg10;
                else if (hcount-- == 0) {
                    hcount = vdp_reg10;
                    if ((vdp_reg0 & 0x10) && irq_pend < 4) { irq_pend = 4; m68k_set_irq(4); }
                }
                line++;
            }
            slice_mclk = mclk; in_slice = 1;
            int c = m68k_execute(64);
            in_slice = 0;
            if (c <= 0) c = 64;
            mclk += (uint64_t)c * 7;
            if (z_enabled) z_run_until(mclk);
        }
    }
    return 0;
}

API uint32_t md_ym_writes(int z80) { return z80 ? ymz80 : ym68k; }
API uint32_t md_keyons(int z80) { return z80 ? konz80 : kon68k; }
API int md_vdp_reg1(void) { return vdp_reg1; }
API void md_log_ram(int on) { ram_log = on ? 1 : 0; }
/* zaddr < 0: no próximo key-on do 68000; senão, na próxima escrita dele nesse endereço da RAM do Z80 */
API void md_keystack_arm(int zaddr) { ks_arm = 1; ks_sp = 0; ks_zaddr = zaddr; }
/* foto: [0]=SP no key-on, [1]=SP quando a interrupção foi aceita, [2]=PC da escrita; a pilha vai em out */
API uint32_t md_keystack(int which, uint8_t *out) {
    if (out) memcpy(out, ks_buf, 512);
    return which == 0 ? ks_sp : which == 1 ? ks_isp : ks_pc;
}
/* como md_set_zoverride, para a RAM do 68000 ($FFxxxx) */
/* lock ≥ 0: depois da troca, todas as escritas seguintes do jogo nesse endereço viram `lock` (o jogo não
 * consegue pedir outra música por cima da escolhida); lock = -1: troca uma vez só */
API void md_set_roverride(int addr, int val, int skip, int lock) {
    if (addr < 0) { rov_n = 0; return; }
    if (rov_n < MAX_ROV) { rov_addr[rov_n] = addr & 0xFFFF; rov_val[rov_n] = val; rov_skip[rov_n] = skip; rov_lock[rov_n] = lock; rov_n++; }
}
/* registradores do YM2612 como estão agora (2 × 256 bytes): o estado inicial de uma música pega no meio */
API const uint8_t *md_ym_regs(void) { return &ymregs[0][0]; }
/* devolve o barramento e solta o reset do Z80 (antes de rodar só o Z80 com o 68000 parado) */
/* botões do controle 1 (bits: U D L R B C A Start) */
API void md_set_pad(int mask) { pad_mask = (uint8_t)mask; }
API void md_set_version(int v) { hw_version = (uint8_t)v; }
API void md_free_z80(void) { z_busreq = 0; if (z_resetline) { z_resetline = 0; } }

/* A próxima escrita do 68000 em zram[addr] grava `val` no lugar do valor do jogo (addr < 0 desliga). */
/* ... depois de deixar passar `skip` escritas do jogo nesse endereço */
API void md_set_zoverride(int addr, int val, int skip, int lock) {
    if (addr < 0) { zov_n = 0; return; }
    if (zov_n < MAX_ZOV) { zov_addr[zov_n] = addr; zov_val[zov_n] = val; zov_skip[zov_n] = skip; zov_lock[zov_n] = lock; zov_n++; }
}

/* Estado salvo (sem os eventos): para testar vários comandos a partir do mesmo ponto do jogo. */
#define ST_FIELDS(X) X(ram) X(zram) X(ymaddr) X(ymregs) X(frame) X(vdp_toggle) X(hv) X(vdp_reg1) X(vdp_reg0) X(vdp_reg10) X(irq_pend) X(hcount) X(vdp_second) \
    X(game_mode) X(ym68k) X(ymz80) X(mclk) X(frame_auto) X(tm_last) X(tm_a) X(tm_b) X(ym_status) X(z_bank) \
    X(z_busreq) X(z_resetline) X(z_mclk) X(z_int_until) X(z_next_vbl) X(zc) X(z_steps) X(z_enabled) X(pad_mask) X(pad_th)
#define ST_SIZE(v) + sizeof(v)
API uint32_t md_state_size(void) { return (uint32_t)(0 ST_FIELDS(ST_SIZE)) + m68k_context_size(); }
API void md_save_state(uint8_t *buf) {
#define ST_SAVE(v) memcpy(buf, &v, sizeof(v)); buf += sizeof(v);
    ST_FIELDS(ST_SAVE)
    m68k_get_context(buf);
}
API void md_load_state(const uint8_t *buf) {
#define ST_LOAD(v) memcpy(&v, buf, sizeof(v)); buf += sizeof(v);
    ST_FIELDS(ST_LOAD)
    m68k_set_context((void *)buf);
    nev = 0;
}
API uint64_t md_mclk(void) { return mclk; }
API uint64_t md_z80_steps(void) { return z_steps; }
API uint32_t md_z80_pc(void) { return zc.pc; }
API uint32_t md_z80_bank(void) { return z_bank; }

API void md_set_breaks(const uint32_t *a, int n) { nbrk = n > MAX_BRK ? MAX_BRK : n; for (int i = 0; i < nbrk; i++) brk[i] = a[i]; }
API void md_set_zbreaks(const uint32_t *a, int n) { nzbrk = n > MAX_BRK ? MAX_BRK : n; for (int i = 0; i < nzbrk; i++) zbrk[i] = (uint16_t)a[i]; }
API void md_set_frame(uint32_t f) { frame = f; }
API uint32_t md_event_count(void) { return nev; }
API const ev_t *md_events(void) { return evs; }
API void md_clear_events(void) { nev = 0; }
API uint8_t *md_ram(void) { return ram; }
API uint8_t *md_zram(void) { return zram; }
API uint32_t md_unmapped(int writes) { return writes ? unmapped_writes : unmapped_reads; }
API uint32_t md_get_reg(int r) { return m68k_get_reg(NULL, (m68k_register_t)r); }
API void md_set_reg(int r, uint32_t v) { m68k_set_reg((m68k_register_t)r, v); }
