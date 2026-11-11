/* Cabeçalho mínimo (escrito para o mdconv, GPLv3) para compilar o ym2612.c do Genesis
 * Plus GX FORA do emulador. O ym2612.c/.h NÃO acompanham o projeto (licença não
 * comercial): copie-os do seu Genesis Plus GX para esta pasta. */
/* shared.h mínimo, só para compilar o ym2612.c do Genesis Plus GX fora do emulador */
#include <stdint.h>
#include <string.h>
#include <math.h>
typedef uint8_t uint8; typedef uint16_t uint16; typedef uint32_t uint32;
typedef int8_t int8; typedef int16_t int16; typedef int32_t int32;
#define INLINE static inline
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#define load_param(p, s) { memcpy(p, &state[bufferptr], s); bufferptr += s; }
#define save_param(p, s) { memcpy(&state[bufferptr], p, s); bufferptr += s; }
typedef uint8_t UINT8; typedef uint16_t UINT16; typedef uint32_t UINT32;
typedef int8_t INT8; typedef int16_t INT16; typedef int32_t INT32;
#include "ym2612.h"
