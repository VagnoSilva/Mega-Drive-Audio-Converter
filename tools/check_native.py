"""Confere se as DLLs do Windows foram geradas a partir dos fontes em C atuais.

    python tools/check_native.py          # confere (o build_windows.bat roda isto)
    python tools/check_native.py --write  # grava as assinaturas (o tools/build_dll.sh roda isto)

As DLLs são geradas no Linux por tools/build_dll.sh (clang + lld). Se alguém mexer num .c e esquecer
de gerar as DLLs de novo, o executável do Windows sairia com o emulador antigo: este script avisa.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N = ROOT / "mdconv" / "chips" / "native"
DLLS = {
    "mdchips.dll": [N / "mdchips.c"],
    "nuked_opn2/nuked_opn2.dll": [N / "nuked_opn2" / "ym3438.c", N / "nuked_opn2" / "ym3438.h",
                                  N / "nuked_opn2" / "nuked_wrap.c"],
    "md68k/md68k.dll": [N / "md68k" / "mdsnd.c", N / "md68k" / "z80.c"]
    + sorted((N / "md68k" / "musashi").rglob("*.[ch]")),
}
SIG = N / "fontes_das_dlls.json"


def _hash(files) -> str:
    h = hashlib.sha1()
    for f in files:
        h.update(f.relative_to(N).as_posix().encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))      # igual no Windows e no Linux
    return h.hexdigest()


def main() -> int:
    now = {dll: _hash(src) for dll, src in DLLS.items()}
    if "--write" in sys.argv:
        SIG.write_text(json.dumps(now, indent=1) + "\n", encoding="utf-8")
        print("assinaturas das DLLs gravadas")
        return 0
    try:
        old = json.loads(SIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    bad = 0
    for dll in DLLS:
        if not (N / dll).is_file():
            print(f"*** falta {dll}: gere com  sh tools/build_dll.sh  (no Linux)")
            bad += 1
        elif old.get(dll) != now[dll]:
            print(f"*** {dll} foi gerada com outra versão dos fontes em C: gere de novo com  sh tools/build_dll.sh")
            bad += 1
    if not bad:
        print("DLLs ok (geradas a partir dos fontes atuais)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
