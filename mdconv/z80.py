"""Z80 em Python puro (conjunto completo de instruções documentadas) para emular placas de som.

Usado para rodar o próprio programa de som de um arcade e gravar o que ele escreve nos chips —
o caminho genérico para extrair música de placas cujo formato ninguém documentou.
Decodificação pelo esquema x/y/z/p/q (http://www.z80.info/decoding.htm). Os flags S, Z, H, P/V,
N e C seguem o Zilog; os bits 3 e 5 não documentados não são emulados.
"""
from __future__ import annotations

SF, ZF, HF, PF, NF, CF = 0x80, 0x40, 0x10, 0x04, 0x02, 0x01
PARITY = [0] * 256
for _i in range(256):
    PARITY[_i] = PF if bin(_i).count("1") % 2 == 0 else 0
SZP = [(_i & SF) | (ZF if _i == 0 else 0) | PARITY[_i] for _i in range(256)]


class Z80:
    def __init__(self, read, write, port_in=None, port_out=None):
        self.read = read
        self.write = write
        self.port_in = port_in or (lambda p: 0xFF)
        self.port_out = port_out or (lambda p, v: None)
        self.reset()

    def reset(self):
        self.a = self.f = self.b = self.c = self.d = self.e = self.h = self.l = 0
        self.a_ = self.f_ = self.b_ = self.c_ = self.d_ = self.e_ = self.h_ = self.l_ = 0
        self.ix = self.iy = 0
        self.sp = 0xFFFF
        self.pc = 0
        self.i = self.r = 0
        self.iff1 = self.iff2 = 0
        self.im = 0
        self.halted = False
        self.ei_delay = False
        self.cycles = 0

    # ------------------------------------------------------------------ pares
    def _bc(self): return (self.b << 8) | self.c
    def _de(self): return (self.d << 8) | self.e
    def _hl(self): return (self.h << 8) | self.l

    def _set_bc(self, v): self.b, self.c = (v >> 8) & 255, v & 255
    def _set_de(self, v): self.d, self.e = (v >> 8) & 255, v & 255
    def _set_hl(self, v): self.h, self.l = (v >> 8) & 255, v & 255

    def rp(self, p, idx=None):
        if p == 0: return self._bc()
        if p == 1: return self._de()
        if p == 2: return self._hl() if idx is None else getattr(self, idx)
        return self.sp

    def set_rp(self, p, v, idx=None):
        v &= 0xFFFF
        if p == 0: self._set_bc(v)
        elif p == 1: self._set_de(v)
        elif p == 2:
            if idx is None: self._set_hl(v)
            else: setattr(self, idx, v)
        else: self.sp = v

    def rp2(self, p, idx=None):
        return ((self.a << 8) | self.f) if p == 3 else self.rp(p, idx)

    def set_rp2(self, p, v, idx=None):
        if p == 3:
            self.a, self.f = (v >> 8) & 255, v & 255
        else:
            self.set_rp(p, v, idx)

    # ------------------------------------------------------------- memória
    def fetch(self):
        v = self.read(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def fetch16(self):
        lo = self.fetch()
        return lo | (self.fetch() << 8)

    def rd16(self, a):
        return self.read(a) | (self.read((a + 1) & 0xFFFF) << 8)

    def wr16(self, a, v):
        self.write(a, v & 255)
        self.write((a + 1) & 0xFFFF, (v >> 8) & 255)

    def push(self, v):
        self.sp = (self.sp - 2) & 0xFFFF
        self.wr16(self.sp, v)

    def pop(self):
        v = self.rd16(self.sp)
        self.sp = (self.sp + 2) & 0xFFFF
        return v

    # ------------------------------------------------- registradores de 8 bits
    def get_r(self, r, idx=None, disp=0):
        if r == 0: return self.b
        if r == 1: return self.c
        if r == 2: return self.d
        if r == 3: return self.e
        if r == 4: return self.h if idx is None else (getattr(self, idx) >> 8)
        if r == 5: return self.l if idx is None else (getattr(self, idx) & 255)
        if r == 6:
            addr = self._hl() if idx is None else (getattr(self, idx) + disp) & 0xFFFF
            return self.read(addr)
        return self.a

    def set_r(self, r, v, idx=None, disp=0):
        v &= 255
        if r == 0: self.b = v
        elif r == 1: self.c = v
        elif r == 2: self.d = v
        elif r == 3: self.e = v
        elif r == 4:
            if idx is None: self.h = v
            else: setattr(self, idx, (getattr(self, idx) & 0x00FF) | (v << 8))
        elif r == 5:
            if idx is None: self.l = v
            else: setattr(self, idx, (getattr(self, idx) & 0xFF00) | v)
        elif r == 6:
            addr = self._hl() if idx is None else (getattr(self, idx) + disp) & 0xFFFF
            self.write(addr, v)
        else: self.a = v

    # ---------------------------------------------------------------- ULA
    def alu(self, op, v):
        a = self.a
        if op in (0, 1):                                   # ADD, ADC
            c = (self.f & CF) if op == 1 else 0
            r = a + v + c
            f = SZP[r & 255] & (SF | ZF)
            if (a ^ v ^ r) & 0x10: f |= HF
            if (~(a ^ v) & (a ^ r)) & 0x80: f |= PF
            if r > 255: f |= CF
            self.a, self.f = r & 255, f
        elif op in (2, 3, 7):                              # SUB, SBC, CP
            c = (self.f & CF) if op == 3 else 0
            r = a - v - c
            f = (SZP[r & 255] & (SF | ZF)) | NF
            if (a ^ v ^ r) & 0x10: f |= HF
            if ((a ^ v) & (a ^ r)) & 0x80: f |= PF
            if r < 0: f |= CF
            self.f = f
            if op != 7:
                self.a = r & 255
        elif op == 4:                                      # AND
            self.a = a & v
            self.f = SZP[self.a] | HF
        elif op == 5:                                      # XOR
            self.a = (a ^ v) & 255
            self.f = SZP[self.a]
        else:                                              # OR
            self.a = (a | v) & 255
            self.f = SZP[self.a]

    def inc8(self, v):
        r = (v + 1) & 255
        self.f = (self.f & CF) | (SZP[r] & (SF | ZF)) | (HF if (v & 0x0F) == 0x0F else 0) | (PF if v == 0x7F else 0)
        return r

    def dec8(self, v):
        r = (v - 1) & 255
        self.f = (self.f & CF) | (SZP[r] & (SF | ZF)) | NF | (HF if (v & 0x0F) == 0 else 0) | (PF if v == 0x80 else 0)
        return r

    def rot(self, y, v):
        c = self.f & CF
        if y == 0:   r = ((v << 1) | (v >> 7)) & 255; nc = v >> 7          # RLC
        elif y == 1: r = ((v >> 1) | (v << 7)) & 255; nc = v & 1           # RRC
        elif y == 2: r = ((v << 1) | c) & 255; nc = v >> 7                 # RL
        elif y == 3: r = (v >> 1) | (c << 7); nc = v & 1                   # RR
        elif y == 4: r = (v << 1) & 255; nc = v >> 7                       # SLA
        elif y == 5: r = (v >> 1) | (v & 0x80); nc = v & 1                 # SRA
        elif y == 6: r = ((v << 1) | 1) & 255; nc = v >> 7                 # SLL
        else:        r = v >> 1; nc = v & 1                                # SRL
        self.f = SZP[r] | (CF if nc else 0)
        return r

    def add16(self, x, y):
        r = x + y
        self.f = (self.f & (SF | ZF | PF)) | (HF if ((x ^ y ^ r) & 0x1000) else 0) | (CF if r > 0xFFFF else 0)
        return r & 0xFFFF

    def adc16(self, x, y):
        c = self.f & CF
        r = x + y + c
        f = (SF if r & 0x8000 else 0) | (ZF if (r & 0xFFFF) == 0 else 0)
        if (x ^ y ^ r) & 0x1000: f |= HF
        if (~(x ^ y) & (x ^ r)) & 0x8000: f |= PF
        if r > 0xFFFF: f |= CF
        self.f = f
        return r & 0xFFFF

    def sbc16(self, x, y):
        c = self.f & CF
        r = x - y - c
        f = NF | (SF if r & 0x8000 else 0) | (ZF if (r & 0xFFFF) == 0 else 0)
        if (x ^ y ^ r) & 0x1000: f |= HF
        if ((x ^ y) & (x ^ r)) & 0x8000: f |= PF
        if r < 0: f |= CF
        self.f = f
        return r & 0xFFFF

    def cond(self, y):
        f = self.f
        return ((not f & ZF), bool(f & ZF), (not f & CF), bool(f & CF),
                (not f & PF), bool(f & PF), (not f & SF), bool(f & SF))[y]

    # ---------------------------------------------------------- interrupção
    def irq(self, vector=0xFF):
        if not self.iff1 or self.ei_delay:
            return False
        self.halted = False
        self.iff1 = self.iff2 = 0
        self.push(self.pc)
        if self.im == 2:
            self.pc = self.rd16((self.i << 8) | (vector & 0xFE))
        elif self.im == 1:
            self.pc = 0x38
        else:
            self.pc = vector & 0x38
        self.cycles += 13
        return True

    def nmi(self):
        self.halted = False
        self.iff2 = self.iff1
        self.iff1 = 0
        self.push(self.pc)
        self.pc = 0x66
        self.cycles += 11

    # ---------------------------------------------------------------- passo
    def step(self):
        """Executa uma instrução. Devolve os ciclos (aproximados)."""
        self.ei_delay = False
        if self.halted:
            self.cycles += 4
            return 4
        self.r = (self.r & 0x80) | ((self.r + 1) & 0x7F)
        op = self.fetch()
        if op == 0xDD:
            n = self._indexed("ix")
        elif op == 0xFD:
            n = self._indexed("iy")
        elif op == 0xCB:
            n = self._cb(None, 0)
        elif op == 0xED:
            n = self._ed()
        else:
            n = self._main(op, None)
        self.cycles += n
        return n

    def _indexed(self, idx):
        op = self.fetch()
        if op == 0xCB:
            disp = self.fetch()
            disp = disp - 256 if disp & 0x80 else disp
            return self._cb(idx, disp) + 8
        if op in (0xDD, 0xFD, 0xED):
            self.pc = (self.pc - 1) & 0xFFFF
            return 4
        return self._main(op, idx) + 4

    def _disp(self, idx, needs):
        if idx is None or not needs:
            return 0
        d = self.fetch()
        return d - 256 if d & 0x80 else d

    def _main(self, op, idx):
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        p, q = y >> 1, y & 1
        if x == 1:
            if op == 0x76:
                self.halted = True
                return 4
            disp = self._disp(idx, y == 6 or z == 6)
            if y == 6:                                   # LD (HL/IX+d), r  (r usa H/L reais)
                self.set_r(6, self.get_r(z), idx, disp)
            elif z == 6:                                 # LD r, (HL/IX+d)
                self.set_r(y, self.get_r(6, idx, disp))
            else:
                self.set_r(y, self.get_r(z, idx), idx)
            return 7 if (y == 6 or z == 6) else 4
        if x == 2:
            disp = self._disp(idx, z == 6)
            self.alu(y, self.get_r(z, idx, disp))
            return 7 if z == 6 else 4
        if x == 0:
            if z == 0:
                if y == 0: return 4                                       # NOP
                if y == 1:                                                # EX AF,AF'
                    self.a, self.a_ = self.a_, self.a
                    self.f, self.f_ = self.f_, self.f
                    return 4
                d = self.fetch()
                d = d - 256 if d & 0x80 else d
                if y == 2:                                                # DJNZ
                    self.b = (self.b - 1) & 255
                    if self.b:
                        self.pc = (self.pc + d) & 0xFFFF
                        return 13
                    return 8
                if y == 3 or self.cond(y - 4):                            # JR / JR cc
                    self.pc = (self.pc + d) & 0xFFFF
                    return 12
                return 7
            if z == 1:
                if q == 0:
                    self.set_rp(p, self.fetch16(), idx)
                    return 10
                hl = self.rp(2, idx)
                self.set_rp(2, self.add16(hl, self.rp(p, idx)), idx)
                return 11
            if z == 2:
                if q == 0:
                    if p == 0: self.write(self._bc(), self.a)
                    elif p == 1: self.write(self._de(), self.a)
                    elif p == 2: self.wr16(self.fetch16(), self.rp(2, idx))
                    else: self.write(self.fetch16(), self.a)
                else:
                    if p == 0: self.a = self.read(self._bc())
                    elif p == 1: self.a = self.read(self._de())
                    elif p == 2: self.set_rp(2, self.rd16(self.fetch16()), idx)
                    else: self.a = self.read(self.fetch16())
                return 7
            if z == 3:
                self.set_rp(p, self.rp(p, idx) + (1 if q == 0 else -1), idx)
                return 6
            if z in (4, 5):
                disp = self._disp(idx, y == 6)
                v = self.get_r(y, idx, disp)
                self.set_r(y, self.inc8(v) if z == 4 else self.dec8(v), idx, disp)
                return 11 if y == 6 else 4
            if z == 6:
                disp = self._disp(idx, y == 6)
                self.set_r(y, self.fetch(), idx, disp)
                return 10 if y == 6 else 7
            # z == 7
            a, f = self.a, self.f
            if y == 0:
                self.a = ((a << 1) | (a >> 7)) & 255
                self.f = (f & (SF | ZF | PF)) | (a >> 7)
            elif y == 1:
                self.a = ((a >> 1) | (a << 7)) & 255
                self.f = (f & (SF | ZF | PF)) | (a & 1)
            elif y == 2:
                self.a = ((a << 1) | (f & CF)) & 255
                self.f = (f & (SF | ZF | PF)) | (a >> 7)
            elif y == 3:
                self.a = (a >> 1) | ((f & CF) << 7)
                self.f = (f & (SF | ZF | PF)) | (a & 1)
            elif y == 4:                                                  # DAA
                c, h, n = f & CF, f & HF, f & NF
                corr, nc = 0, c
                if h or (a & 0x0F) > 9:
                    corr |= 0x06
                if c or a > 0x99:
                    corr |= 0x60
                    nc = CF
                r = (a - corr) & 255 if n else (a + corr) & 255
                nh = ((a ^ r) & 0x10)
                self.a = r
                self.f = SZP[r] | n | (HF if nh else 0) | nc
            elif y == 5:                                                  # CPL
                self.a = a ^ 255
                self.f = f | HF | NF
            elif y == 6:                                                  # SCF
                self.f = (f & (SF | ZF | PF)) | CF
            else:                                                         # CCF
                self.f = ((f & (SF | ZF | PF)) | (HF if f & CF else 0)) | ((f & CF) ^ CF)
            return 4
        # x == 3
        if z == 0:                                                        # RET cc
            if self.cond(y):
                self.pc = self.pop()
                return 11
            return 5
        if z == 1:
            if q == 0:
                self.set_rp2(p, self.pop(), idx)
                return 10
            if p == 0:
                self.pc = self.pop()
                return 10
            if p == 1:                                                    # EXX
                self.b, self.b_ = self.b_, self.b
                self.c, self.c_ = self.c_, self.c
                self.d, self.d_ = self.d_, self.d
                self.e, self.e_ = self.e_, self.e
                self.h, self.h_ = self.h_, self.h
                self.l, self.l_ = self.l_, self.l
                return 4
            if p == 2:
                self.pc = self.rp(2, idx)
                return 4
            self.sp = self.rp(2, idx)
            return 6
        if z == 2:
            nn = self.fetch16()
            if self.cond(y):
                self.pc = nn
            return 10
        if z == 3:
            if y == 0:
                self.pc = self.fetch16()
                return 10
            if y == 2:
                self.port_out((self.a << 8) | self.fetch(), self.a)
                return 11
            if y == 3:
                self.a = self.port_in((self.a << 8) | self.fetch()) & 255
                return 11
            if y == 4:                                                    # EX (SP),HL
                v = self.rd16(self.sp)
                self.wr16(self.sp, self.rp(2, idx))
                self.set_rp(2, v, idx)
                return 19
            if y == 5:                                                    # EX DE,HL
                de, hl = self._de(), self._hl()
                self._set_de(hl)
                self._set_hl(de)
                return 4
            if y == 6:
                self.iff1 = self.iff2 = 0
                return 4
            self.iff1 = self.iff2 = 1
            self.ei_delay = True
            return 4
        if z == 4:
            nn = self.fetch16()
            if self.cond(y):
                self.push(self.pc)
                self.pc = nn
                return 17
            return 10
        if z == 5:
            if q == 0:
                self.push(self.rp2(p, idx))
                return 11
            # p == 0: CALL nn (os prefixos já foram tratados)
            nn = self.fetch16()
            self.push(self.pc)
            self.pc = nn
            return 17
        if z == 6:
            self.alu(y, self.fetch())
            return 7
        self.push(self.pc)                                                # RST
        self.pc = y * 8
        return 11

    def _cb(self, idx, disp):
        op = self.fetch()
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        if idx is not None:
            addr = (getattr(self, idx) + disp) & 0xFFFF
            v = self.read(addr)
        else:
            v = self.get_r(z)
        if x == 0:
            r = self.rot(y, v)
        elif x == 1:                                                      # BIT
            b = v & (1 << y)
            self.f = (self.f & CF) | HF | (ZF | PF if not b else 0) | (SF if (y == 7 and b) else 0)
            return 12 if (idx is not None or z == 6) else 8
        elif x == 2:
            r = v & ~(1 << y) & 255
        else:
            r = v | (1 << y)
        if idx is not None:
            self.write(addr, r)
            if z != 6:
                self.set_r(z, r)
        else:
            self.set_r(z, r)
        return 15 if (idx is not None or z == 6) else 8

    def _ed(self):
        op = self.fetch()
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        p, q = y >> 1, y & 1
        if x == 1:
            if z == 0:                                                    # IN r,(C)
                v = self.port_in(self._bc()) & 255
                if y != 6:
                    self.set_r(y, v)
                self.f = (self.f & CF) | SZP[v]
                return 12
            if z == 1:
                self.port_out(self._bc(), 0 if y == 6 else self.get_r(y))
                return 12
            if z == 2:
                hl = self._hl()
                self._set_hl(self.sbc16(hl, self.rp(p)) if q == 0 else self.adc16(hl, self.rp(p)))
                return 15
            if z == 3:
                nn = self.fetch16()
                if q == 0: self.wr16(nn, self.rp(p))
                else: self.set_rp(p, self.rd16(nn))
                return 20
            if z == 4:                                                    # NEG
                v = self.a
                self.a = 0
                self.alu(2, v)
                return 8
            if z == 5:                                                    # RETN / RETI
                self.pc = self.pop()
                self.iff1 = self.iff2
                return 14
            if z == 6:
                self.im = (0, 0, 1, 2, 0, 0, 1, 2)[y]
                return 8
            if y == 0: self.i = self.a
            elif y == 1: self.r = self.a
            elif y == 2:
                self.a = self.i
                self.f = (self.f & CF) | (SZP[self.a] & (SF | ZF)) | (PF if self.iff2 else 0)
            elif y == 3:
                self.a = self.r
                self.f = (self.f & CF) | (SZP[self.a] & (SF | ZF)) | (PF if self.iff2 else 0)
            elif y == 4:                                                  # RRD
                m = self.read(self._hl())
                self.write(self._hl(), ((self.a << 4) | (m >> 4)) & 255)
                self.a = (self.a & 0xF0) | (m & 0x0F)
                self.f = (self.f & CF) | SZP[self.a]
            elif y == 5:                                                  # RLD
                m = self.read(self._hl())
                self.write(self._hl(), ((m << 4) | (self.a & 0x0F)) & 255)
                self.a = (self.a & 0xF0) | (m >> 4)
                self.f = (self.f & CF) | SZP[self.a]
            return 9
        if x == 2 and z <= 3 and y >= 4:                                  # bloco
            inc = 1 if y in (4, 6) else -1
            rep = y >= 6
            if z == 0:                                                    # LDI/LDD/LDIR/LDDR
                self.write(self._de(), self.read(self._hl()))
                self._set_de(self._de() + inc)
                self._set_hl(self._hl() + inc)
                self._set_bc(self._bc() - 1)
                self.f = (self.f & (SF | ZF | CF)) | (PF if self._bc() else 0)
                if rep and self._bc():
                    self.pc = (self.pc - 2) & 0xFFFF
                    return 21
                return 16
            if z == 1:                                                    # CPI/CPD/CPIR/CPDR
                v = self.read(self._hl())
                r = (self.a - v) & 255
                self._set_hl(self._hl() + inc)
                self._set_bc(self._bc() - 1)
                self.f = (self.f & CF) | NF | (SZP[r] & (SF | ZF)) | (HF if (self.a ^ v ^ r) & 0x10 else 0) \
                    | (PF if self._bc() else 0)
                if rep and self._bc() and r != 0:
                    self.pc = (self.pc - 2) & 0xFFFF
                    return 21
                return 16
            if z == 2:                                                    # INI...
                self.write(self._hl(), self.port_in(self._bc()) & 255)
            else:                                                         # OUTI...
                self.port_out(self._bc(), self.read(self._hl()))
            self._set_hl(self._hl() + inc)
            self.b = (self.b - 1) & 255
            self.f = (self.f & CF) | NF | (ZF if self.b == 0 else 0)
            if rep and self.b:
                self.pc = (self.pc - 2) & 0xFFFF
                return 21
            return 16
        return 8                                                          # NOP de ED
