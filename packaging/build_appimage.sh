#!/bin/sh
# Gera o AppImage do Mega Drive Audio Converter (Linux x64): um arquivo só, que roda em quase
# qualquer distribuição sem instalar nada.
# Rode na pasta raiz do projeto (onde está o run_gui.py):  sh packaging/build_appimage.sh
# Precisa de: python (3.10+), gcc, internet (baixa as bibliotecas Python e o appimagetool).
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)

# 1) o programa (compila os emuladores em C e empacota com o PyInstaller)
sh packaging/build_linux.sh

# 2) a pasta do AppImage
A="$ROOT/dist/AppDir"
rm -rf "$A"
mkdir -p "$A/usr/lib"
cp -r dist/MegaDriveAudioConverter "$A/usr/lib/MegaDriveAudioConverter"
cp packaging/linux/megadrive-audio-converter.desktop "$A/"
cp packaging/linux/megadrive-audio-converter.png "$A/"
cp packaging/linux/megadrive-audio-converter.png "$A/.DirIcon"
cat > "$A/AppRun" <<'SH'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
exec "$HERE/usr/lib/MegaDriveAudioConverter/MegaDriveAudioConverter" "$@"
SH
chmod +x "$A/AppRun"

# 3) appimagetool (baixado uma vez para tools/)
T="$ROOT/tools/appimagetool-x86_64.AppImage"
if [ ! -x "$T" ]; then
  echo "Baixando o appimagetool…"
  curl -L -o "$T" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$T"
fi
VER=$(python3 -c "import re;print(re.search(r'__version__ = \"(.*)\"', open('mdconv/__init__.py').read()).group(1))")
OUT="$ROOT/dist/MegaDriveAudioConverter-$VER-x86_64.AppImage"
# --appimage-extract-and-run: funciona mesmo sem o FUSE instalado
ARCH=x86_64 "$T" --appimage-extract-and-run "$A" "$OUT"

echo
echo "Pronto: $OUT"
echo "Para abrir: chmod +x \"$OUT\" e dê dois cliques (ou rode pelo terminal)."
