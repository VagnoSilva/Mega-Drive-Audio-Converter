@echo off
rem Gera o executavel do Mega Drive Audio Converter (Windows x64).
rem Rode na pasta raiz do projeto (onde esta o run_gui.py):  packaging\build_windows.bat
setlocal
cd /d "%~dp0.."

python -m pip install --upgrade pip
python -m pip install numpy scipy PySide6 pyinstaller || goto :erro

rem confere o conversor antes de empacotar
python -m pip install pytest
python -m pytest -q -m "not slow" || goto :erro

python -m PyInstaller --noconfirm --clean --windowed ^
  --name MegaDriveAudioConverter ^
  --add-data "mdconv\chips\native\mdchips.dll;mdconv\chips\native" ^
  --add-data "mdconv\chips\native\nuked_opn2\nuked_opn2.dll;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\ym3438.c;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\ym3438.h;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\nuked_wrap.c;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\LICENSE;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\README.txt;mdconv\chips\native\nuked_opn2" ^
  --collect-submodules mdconv ^
  run_gui.py || goto :erro

echo.
echo Pronto: dist\MegaDriveAudioConverter\MegaDriveAudioConverter.exe
echo (distribua a pasta dist\MegaDriveAudioConverter inteira)
exit /b 0

:erro
echo.
echo *** Falhou. Copie as mensagens acima e me envie. ***
exit /b 1
