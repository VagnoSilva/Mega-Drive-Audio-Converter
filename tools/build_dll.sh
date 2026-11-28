#!/bin/sh
# Gera as DLLs Windows x64 do projeto no Linux, com clang + lld (sem MinGW):
#   mdconv/chips/native/mdchips.dll                 (emulador próprio, GPLv3)
#   mdconv/chips/native/nuked_opn2/nuked_opn2.dll   (Nuked-OPN2, LGPL 2.1+, biblioteca separada)
#   mdconv/chips/native/md68k/md68k.dll             (68000 do Musashi, MIT, + mapa do Mega Drive)
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
# md68k: o Musashi usa stdio/setjmp/math só em caminhos que o 68000 do Mega Drive nunca percorre
# (FPU do 68040, erro de barramento): esses viram stubs; malloc/memcpy/sin/cos vêm do msvcrt.dll.
M="$N/md68k"
mkdir -p "$W/inc68"
cp "$W/inc/"*.h "$W/inc68/"
printf '#pragma once\ndouble pow(double,double); double sin(double); double cos(double);\n' > "$W/inc68/math.h"
printf '#pragma once\n#include <stddef.h>\nvoid *calloc(size_t,size_t); void *realloc(void*,size_t); void *malloc(size_t); void free(void*); void exit(int);\n' > "$W/inc68/stdlib.h"
printf '#pragma once\n#include <stdarg.h>\n#include <stddef.h>\ntypedef struct _FILE FILE;\n#define stderr ((FILE*)0)\n#define stdout ((FILE*)0)\nint printf(const char*,...); int sprintf(char*,const char*,...); int fprintf(FILE*,const char*,...); int vfprintf(FILE*,const char*,va_list); int sscanf(const char*,const char*,...);\n' > "$W/inc68/stdio.h"
printf '#pragma once\n#define assert(x) ((void)0)\n' > "$W/inc68/assert.h"
printf '#pragma once\ntypedef long long jmp_buf[32];\nint _setjmp(jmp_buf); void longjmp(jmp_buf,int);\n#define setjmp(b) _setjmp(b)\n' > "$W/inc68/setjmp.h"
cat > "$W/stubs68.c" <<'STUBS'
#include <stdarg.h>
typedef struct _FILE FILE;
typedef long long jmp_buf[32];
int _setjmp(jmp_buf b) { (void)b; return 0; }
void longjmp(jmp_buf b, int v) { (void)b; (void)v; __builtin_trap(); }
void exit(int c) { (void)c; __builtin_trap(); }
int printf(const char *f, ...) { (void)f; return 0; }
int fprintf(FILE *s, const char *f, ...) { (void)s; (void)f; return 0; }
int vfprintf(FILE *s, const char *f, va_list a) { (void)s; (void)f; (void)a; return 0; }
int sprintf(char *o, const char *f, ...) { (void)f; if (o) o[0] = 0; return 0; }
int sscanf(const char *s, const char *f, ...) { (void)s; (void)f; return 0; }
STUBS
printf 'LIBRARY msvcrt.dll\nEXPORTS\nmalloc\nrealloc\nfree\nmemset\nmemcpy\nsin\ncos\n' > "$W/msvcrt68.def"
llvm-dlltool -m i386:x86-64 -d "$W/msvcrt68.def" -l "$W/msvcrt68.lib"
T68="--target=x86_64-pc-windows-msvc -O2 -fno-stack-protector -nostdlibinc -ffreestanding -I$W/inc68 -I$M/musashi -fno-builtin -D_WIN32 -w"
for f in "$M/mdsnd.c" "$M/musashi/m68kcpu.c" "$M/musashi/m68kops.c" "$M/musashi/softfloat/softfloat.c" "$W/stubs68.c"; do
    clang $T68 -c "$f" -o "$W/$(basename "$f" .c).o68"
done
lld-link /dll /noentry /nodefaultlib /machine:x64 /out:"$M/md68k.dll" "$W/"*.o68 "$W/shim.obj" "$W/msvcrt68.lib"
rm -f "$M/md68k.lib"
rm -rf "$W"
echo "ok: mdchips.dll, nuked_opn2/nuked_opn2.dll e md68k/md68k.dll"

# guarda a "assinatura" dos fontes de cada DLL: o build do Windows avisa se um .c mudou depois
python3 "$ROOT/tools/check_native.py" --write
