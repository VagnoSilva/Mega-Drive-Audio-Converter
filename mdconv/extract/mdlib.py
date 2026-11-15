"""Biblioteca de patches para o Mega Drive Audio Converter (mdconv).

Converte os timbres FM do YM2151 (OPM) do jogo em patches do YM2612 (OPN2) no formato
de ``mdconv.library.patch_library.LibraryEntry``, já aprovados. O mdconv usa um patch
aprovado quando (banco, programa) e o nome do preset no SF2 batem com os da entrada.

Correspondência OPM -> OPN2 (os dois chips são da mesma família):
* operadores: OP1=M1, OP2=C1, OP3=M2, OP4=C2 (mesmos 8 algoritmos e mesma ordem de
  registradores 1,3,2,4);
* AR, D1R, D2R(=SR), RR, D1L(=SL), TL, KS, MUL, FB, CON(=ALG), AMS-EN: iguais;
* DT1: 0..3 positivo, 5..7 negativo (4 = 0), igual nos dois;
* DT2 (só no OPM): aproximado multiplicando o MUL pela razão do DT2 e arredondando;
* LFO: PMS->FMS e AMS->AMS (a frequência do LFO do YM2612 é global, fica com o driver).
"""
import datetime
import json
import os
import re
import time

DT2_RATIO = (1.0, 1.41, 1.57, 1.73)
# índice do operador no OPM (M1, M2, C1, C2) para OP1..OP4 do YM2612
OPN_FROM_OPM = (0, 2, 1, 3)


def opm_to_patch(ins, name):
    """Retorna (dict do Patch do mdconv, lista de avisos)."""
    notes = []
    ops = []
    for opm_idx in OPN_FROM_OPM:
        o = ins.op[opm_idx]
        mul = o["MUL"]
        if o["DT2"]:
            eff = (0.5 if mul == 0 else mul) * DT2_RATIO[o["DT2"]]
            new = 0 if eff < 0.75 else min(15, int(round(eff)))
            notes.append(f"DT2={o['DT2']} aproximado (MUL {mul} -> {new})")
            mul = new
        d = o["DT1"] & 7
        dt = d if d < 4 else (0 if d == 4 else -(d - 4))
        ops.append(dict(mul=mul, dt=dt, tl=min(127, o["TL"]), ks=o["KS"] & 3, ar=o["AR"] & 31,
                        d1r=o["D1R"] & 31, d2r=o["D2R"] & 31, sl=o["D1L"] & 15, rr=o["RR"] & 15,
                        am=1 if o["AMS_EN"] else 0))
    patch = dict(alg=ins.ch["CON"] & 7, fb=ins.ch["FL"] & 7, ams=ins.ch["AMS"] & 3,
                 fms=ins.ch["PMS"] & 7, ops=ops, name=name, octave=0)
    mask = ins.ch["SLOT_MASK"] & 0x78
    if mask and mask != 0x78:
        # operadores que o jogo não liga (KON) ficam mudos
        bits = {0: 0x08, 1: 0x10, 2: 0x20, 3: 0x40}   # OP1=M1, OP2=C1, OP3=M2, OP4=C2
        for i in range(4):
            if not (mask & bits[i]):
                ops[i]["tl"] = 127
        notes.append("operadores desligados no jogo ficaram com TL 127")
    return patch, notes


def preset_name(game, kind, num):
    """Nome do preset no SF2 (máx. 19 caracteres), único por jogo."""
    return f"{game[:10]} {kind} {num:03d}"


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")[:40] or "patch"


def write_library(gd, folder, sf2_path=None, log=None):
    """Grava um JSON por instrumento FM em ``folder``. Retorna o número de patches."""
    os.makedirs(folder, exist_ok=True)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    count = 0
    for ins in gd.driver.instruments:
        pname = preset_name(gd.name, "FM", ins.index)
        patch, notes = opm_to_patch(ins, f"{gd.name} FM {ins.index:03d}")
        if all(op["tl"] >= 127 for i, op in enumerate(patch["ops"])
               if i in _carriers(patch["alg"])):
            continue    # instrumento mudo
        entry = dict(
            id=_slug(f"{gd.name}_FM_{ins.index:03d}"),
            name=pname,
            patch=patch,
            approved=True,
            source=dict(sf2=sf2_path or "", bank=0, program=ins.index, preset=pname,
                        samples=[], origin="CPS1 YM2151 (original do jogo)", game=gd.name,
                        driver_version=gd.entry["version"], approved_at=now,
                        conversion_notes=notes),
            profile=[], centroid=None, attack_s=None,
            tags=["tonal", "cps1", "original"], created=time.time())
        with open(os.path.join(folder, entry["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
        count += 1
    if log:
        log(f"  {count} patches YM2612 (convertidos dos originais) salvos em {os.path.basename(folder)}")
    return count


def _carriers(alg):
    return {0: (3,), 1: (3,), 2: (3,), 3: (3,), 4: (1, 3), 5: (1, 2, 3), 6: (1, 2, 3), 7: (0, 1, 2, 3)}[alg]
