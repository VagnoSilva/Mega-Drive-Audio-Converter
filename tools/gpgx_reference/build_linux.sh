#!/bin/sh
# Compila o YM2612 do Genesis Plus GX como referência LOCAL (não distribuir).
set -e
[ -f ym2612.c ] || { echo "Falta ym2612.c nesta pasta"; exit 1; }
gcc -O2 -fPIC -shared -o libgpgx_ym2612.so ym2612.c -lm
echo "export MDCONV_GPGX_LIB=$(pwd)/libgpgx_ym2612.so"
