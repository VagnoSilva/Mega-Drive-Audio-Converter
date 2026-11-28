#!/bin/sh
# Gera o executável do Mega Drive Audio Converter para Linux x64 (Arch, Ubuntu, Fedora…).
# Rode na pasta raiz do projeto (onde está o run_gui.py):  sh packaging/build_linux.sh
# Precisa de: python (3.10+), gcc. No Arch:  sudo pacman -S --needed python gcc
# As bibliotecas Python (numpy, scipy, PySide6, PyInstaller) são instaladas num ambiente
# separado (.venv-build), sem mexer no Python do sistema.
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd)"            # usa sempre o mdconv desta pasta (nunca uma cópia antiga instalada)
[ -f mdconv/chips/md68k.py ] || { echo "*** esta cópia do projeto é antiga (falta mdconv/chips/md68k.py)"; exit 1; }

python3 -m venv .venv-build
. .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install numpy scipy PySide6 pyinstaller pytest

# compila os três emuladores em C (.so) e confere o conversor antes de empacotar
python -c "import mdconv.chips.emulator as e, mdconv.chips.nuked as n, mdconv.chips.md68k as m; e._load(); n._load(); m._load(); print('bibliotecas nativas ok')"
python -m pytest -q -m "not slow"

N=mdconv/chips/native
python -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name MegaDriveAudioConverter \
  --add-data "$N/libmdchips.so:$N" \
  --add-data "$N/nuked_opn2/libnuked_opn2.so:$N/nuked_opn2" \
  --add-data "$N/nuked_opn2/ym3438.c:$N/nuked_opn2" \
  --add-data "$N/nuked_opn2/ym3438.h:$N/nuked_opn2" \
  --add-data "$N/nuked_opn2/nuked_wrap.c:$N/nuked_opn2" \
  --add-data "$N/nuked_opn2/LICENSE:$N/nuked_opn2" \
  --add-data "$N/nuked_opn2/README.txt:$N/nuked_opn2" \
  --add-data "$N/md68k/libmd68k.so:$N/md68k" \
  --add-data "$N/md68k/musashi/LICENSE.txt:$N/md68k/musashi" \
  --add-data "perfis_megadrive:perfis_megadrive" \
  --collect-submodules mdconv \
  run_gui.py

D=dist/MegaDriveAudioConverter
for f in Manual_Mega_Drive_Audio_Converter.pdf Manual_Mega_Drive_Audio_Converter_EN.pdf LICENSE; do
  [ -f "$f" ] && cp "$f" "$D/"
done
cp packaging/linux/megadrive-audio-converter.desktop "$D/"
for so in libmdchips.so nuked_opn2/libnuked_opn2.so md68k/libmd68k.so; do
  [ -f "$D/_internal/$N/$so" ] || { echo "*** $so ausente no pacote"; exit 1; }
done
tar -C dist -czf dist/MegaDriveAudioConverter-linux-x64.tar.gz MegaDriveAudioConverter

echo
echo "Pronto: $D/MegaDriveAudioConverter"
echo "Para distribuir: dist/MegaDriveAudioConverter-linux-x64.tar.gz"
