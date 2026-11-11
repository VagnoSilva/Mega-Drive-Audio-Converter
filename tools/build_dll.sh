#!/bin/sh
# Gera as DLLs Windows x64 do projeto no Linux, com clang + lld (sem MinGW):
#   mdconv/chips/native/mdchips.dll                 (emulador próprio, GPLv3)
#   mdconv/chips/native/nuked_opn2/nuked_opn2.dll   (Nuked-OPN2, LGPL 2.1+, biblioteca separada)
# As DLLs só importam funções básicas do msvcrt.dll, que vem com o Windows.
# No Windows, alternativa: MDCONV_REBUILD=1 com gcc (MinGW-w64) no PATH.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$(mktemp -d)
mkdir -p "$W/inc"
printf '#pragma once\ndouble pow(double,double); double sin(double);\n' > "$W/inc/math.h"
printf '#pragma once\n#include <stddef.h>\nvoid *calloc(size_t,size_t); void *realloc(void*,size_t); void free(void*);\n' > "$W/inc/stdlib.h"
printf '#pragma once\n#include <stddef.h>\nvoid *memset(void*,int,size_t); void *memcpy(void*,const void*,size_t);\n' > "$W/inc/string.h"
echo 'int _fltused = 0;' > "$W/shim.c"
printf 'LIBRARY msvcrt.dll\nEXPORTS\ncalloc\nrealloc\nfree\npow\nsin\nmemset\nmemcpy\n' > "$W/msvcrt.def"
llvm-dlltool -m i386:x86-64 -d "$W/msvcrt.def" -l "$W/msvcrt.lib"
T="--target=x86_64-pc-windows-msvc -O2 -fno-stack-protector -nostdlibinc -I$W/inc -fno-builtin -D_WIN32"
clang $T -c "$W/shim.c" -o "$W/shim.obj"
link() {  # saída, objetos...
    out=$1; shift
    lld-link /dll /noentry /nodefaultlib /machine:x64 /out:"$out" "$@" "$W/shim.obj" "$W/msvcrt.lib"
    rm -f "${out%.dll}.lib"
}
N="$ROOT/mdconv/chips/native"
clang $T -c "$N/mdchips.c" -o "$W/mdchips.obj"
link "$N/mdchips.dll" "$W/mdchips.obj"
clang $T -c "$N/nuked_opn2/ym3438.c" -o "$W/ym3438.obj"
clang $T -c "$N/nuked_opn2/nuked_wrap.c" -o "$W/nuked_wrap.obj"
link "$N/nuked_opn2/nuked_opn2.dll" "$W/ym3438.obj" "$W/nuked_wrap.obj"
rm -rf "$W"
echo "ok: mdchips.dll e nuked_opn2/nuked_opn2.dll"
