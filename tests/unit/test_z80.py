"""Z80 em Python: instruções básicas, flags, pilha, blocos e interrupção."""
from mdconv.z80 import Z80, CF, ZF


def _cpu(prog: bytes):
    mem = bytearray(0x10000)
    mem[:len(prog)] = prog
    cpu = Z80(lambda a: mem[a], lambda a, v: mem.__setitem__(a, v))
    cpu.sp = 0xF000
    return cpu, mem


def _run(cpu, n):
    for _ in range(n):
        cpu.step()


def test_arith_flags_and_loop():
    # LD A,0F0h ; ADD A,20h -> 10h com carry ; LD B,3 ; DJNZ $ ; HALT
    cpu, _ = _cpu(bytes([0x3E, 0xF0, 0xC6, 0x20, 0x06, 0x03, 0x10, 0xFE, 0x76]))
    _run(cpu, 2)
    assert cpu.a == 0x10 and cpu.f & CF and not cpu.f & ZF
    _run(cpu, 5)
    assert cpu.b == 0 and cpu.halted


def test_call_ret_ldir_and_indexed():
    prog = bytes([
        0x21, 0x00, 0x20, 0x11, 0x00, 0x30, 0x01, 0x04, 0x00, 0xED, 0xB0,   # LDIR 4 bytes 2000->3000
        0xDD, 0x21, 0x00, 0x30, 0xDD, 0x7E, 0x02,                         # LD IX,3000 ; LD A,(IX+2)
        0xCD, 0x20, 0x00, 0x76])                                          # CALL 0020 ; HALT
    cpu, mem = _cpu(prog)
    mem[0x2000:0x2004] = b"\x11\x22\x33\x44"
    mem[0x20:0x22] = bytes([0x3C, 0xC9])                                  # INC A ; RET
    while not cpu.halted:
        cpu.step()
    assert bytes(mem[0x3000:0x3004]) == b"\x11\x22\x33\x44" and cpu.a == 0x34 and cpu.sp == 0xF000


def test_irq_mode1():
    cpu, mem = _cpu(bytes([0xED, 0x56, 0xFB, 0x76]))                       # IM 1 ; EI ; HALT
    mem[0x38:0x3A] = bytes([0x3C, 0xC9])
    _run(cpu, 3)
    assert cpu.halted and cpu.irq() and cpu.pc == 0x38
