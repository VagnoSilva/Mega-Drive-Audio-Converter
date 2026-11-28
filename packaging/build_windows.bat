@echo off
rem Gera o executavel do Mega Drive Audio Converter (Windows x64).
rem Rode na pasta raiz do projeto (onde esta o run_gui.py):  packaging\build_windows.bat
rem Precisa so do Python 3.10 ou mais novo, de 64 bits (recomendado 3.12) (python.org, marcando "Add python.exe to PATH").
rem As bibliotecas (numpy, scipy, PySide6, PyInstaller) sao instaladas num ambiente separado
rem (.venv-build), sem mexer no Python do sistema. Nao precisa de compilador de C: as DLLs ja vem prontas.
setlocal
cd /d "%~dp0.."

where python >nul 2>nul || (echo *** Python nao encontrado. Instale o Python 3.12 de 64 bits marcando "Add python.exe to PATH". & goto :erro)
python -c "import sys; print('Python', sys.version.split()[0], '(%d bits)' %% (64 if sys.maxsize > 2**32 else 32))"
python -c "import sys; sys.exit(0 if sys.maxsize > 2**32 else 1)" || (echo *** Este Python e de 32 bits: instale o Python de 64 bits ^(recomendado: 3.12^). & goto :erro)
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" || (echo *** Python antigo demais: use o 3.10 ou mais novo ^(recomendado: 3.12^). & goto :erro)
python -c "import sys; sys.exit(0 if sys.version_info[:2] <= (3,13) else 1)" || echo Aviso: Python mais novo que o 3.13. Deve funcionar; se o pip nao achar o PySide6 para ele, instale o 3.12.

rem DLLs dos emuladores: existem e foram geradas a partir dos fontes atuais?
python tools\check_native.py || goto :erro

if not exist .venv-build\Scripts\python.exe python -m venv .venv-build || goto :erro
set PY=.venv-build\Scripts\python.exe
%PY% -m pip install --upgrade pip
%PY% -m pip install numpy scipy PySide6 pyinstaller pytest || goto :erro

rem confere o conversor antes de empacotar
%PY% -m pytest -q -m "not slow" || goto :erro

%PY% -m PyInstaller --noconfirm --clean --windowed ^
  --name MegaDriveAudioConverter ^
  --add-data "mdconv\chips\native\mdchips.dll;mdconv\chips\native" ^
  --add-data "mdconv\chips\native\nuked_opn2\nuked_opn2.dll;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\ym3438.c;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\ym3438.h;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\nuked_wrap.c;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\LICENSE;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\nuked_opn2\README.txt;mdconv\chips\native\nuked_opn2" ^
  --add-data "mdconv\chips\native\md68k\md68k.dll;mdconv\chips\native\md68k" ^
  --add-data "mdconv\chips\native\md68k\musashi\LICENSE.txt;mdconv\chips\native\md68k\musashi" ^
  --add-data "perfis_megadrive;perfis_megadrive" ^
  --collect-submodules mdconv ^
  run_gui.py || goto :erro

set D=dist\MegaDriveAudioConverter
if not exist %D%\_internal\mdconv\chips\native\md68k\md68k.dll (echo *** md68k.dll ausente no pacote & goto :erro)
if not exist %D%\_internal\mdconv\chips\native\mdchips.dll (echo *** mdchips.dll ausente no pacote & goto :erro)
if not exist %D%\_internal\mdconv\chips\native\nuked_opn2\nuked_opn2.dll (echo *** nuked_opn2.dll ausente no pacote & goto :erro)
for %%f in (Manual_Mega_Drive_Audio_Converter.pdf Manual_Mega_Drive_Audio_Converter_EN.pdf LICENSE) do if exist %%f copy /y %%f %D%\ >nul

if exist dist\MegaDriveAudioConverter-windows-x64.zip del dist\MegaDriveAudioConverter-windows-x64.zip
powershell -NoProfile -Command "Compress-Archive -Path '%D%' -DestinationPath 'dist\MegaDriveAudioConverter-windows-x64.zip'"

echo.
echo Pronto: %D%\MegaDriveAudioConverter.exe
echo Para distribuir: dist\MegaDriveAudioConverter-windows-x64.zip (a pasta inteira, compactada)
exit /b 0

:erro
echo.
echo *** Falhou. Copie as mensagens acima e me envie. ***
exit /b 1
