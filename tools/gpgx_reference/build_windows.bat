@echo off
rem Compila o YM2612 do Genesis Plus GX como referencia LOCAL (nao distribuir).
rem Requer MinGW-w64 (gcc) no PATH. Copie ym2612.c e ym2612.h do Genesis Plus GX
rem (core/sound/) para esta pasta antes de rodar.
if not exist ym2612.c ( echo Falta ym2612.c nesta pasta & exit /b 1 )
gcc -O2 -shared -o gpgx_ym2612.dll ym2612.c -lm
if errorlevel 1 exit /b 1
echo.
echo Pronto. Para usar nas previas e nos testes:
echo   set MDCONV_GPGX_LIB=%CD%\gpgx_ym2612.dll
