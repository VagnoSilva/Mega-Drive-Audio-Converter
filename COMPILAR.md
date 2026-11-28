# Como compilar o Mega Drive Audio Converter

O mesmo código gera o programa para **Windows** e para **Linux**. Rode sempre os comandos na pasta
raiz do projeto (onde está o `run_gui.py`). Os scripts criam um ambiente Python separado
(`.venv-build`), instalam as bibliotecas (numpy, scipy, PySide6, PyInstaller, pytest), rodam os
testes e só então empacotam. Na primeira vez é preciso internet.

## Windows (x64)

Precisa só do **Python 3.10 a 3.13 de 64 bits** (recomendado 3.12, de python.org, marcando
"Add python.exe to PATH"). Não precisa de compilador de C: os emuladores vão prontos em DLL.

```
packaging\build_windows.bat
```

Sai `dist\MegaDriveAudioConverter\MegaDriveAudioConverter.exe` e, para distribuir,
`dist\MegaDriveAudioConverter-windows-x64.zip`. Roda no Windows 8.1, 10 e 11 de 64 bits.

## Linux (x64: Arch, Ubuntu, Fedora…)

Precisa de **python** (3.10+) e **gcc**; os emuladores em C são compilados na hora (viram `.so`).
No Arch: `sudo pacman -S --needed python gcc curl`; no Ubuntu: `sudo apt install python3-venv gcc curl`.

| Para ter | Comando | Resultado |
|---|---|---|
| AppImage (um arquivo, roda em quase qualquer distribuição) | `sh packaging/build_appimage.sh` | `dist/MegaDriveAudioConverter-<versão>-x86_64.AppImage` |
| Programa em pasta | `sh packaging/build_linux.sh` | `dist/MegaDriveAudioConverter-linux-x64.tar.gz` |
| Pacote do Arch (menu + comando `megadrive-audio-converter`) | `cd packaging/arch && makepkg -si` | instalado no sistema |

Sem empacotar, também dá para rodar direto do código: `python3 run_gui.py`.

## Pelo GitHub (sem instalar nada)

O workflow `.github/workflows/build-windows.yml` gera os dois de uma vez numa máquina da nuvem:
aba **Actions → Executável Windows → Run workflow**. No fim saem `MegaDriveAudioConverter-windows-x64`
e `MegaDriveAudioConverter-linux-x64` para baixar; com uma tag `v*` eles vão para o Release.

## Emuladores em C (para quem mexer neles)

| Emulador | Fonte | Windows | Linux |
|---|---|---|---|
| YM2612/PSG próprio | `mdconv/chips/native/mdchips.c` | `mdchips.dll` | `libmdchips.so` |
| Nuked-OPN2 (prévia fiel) | `mdconv/chips/native/nuked_opn2/` | `nuked_opn2.dll` | `libnuked_opn2.so` |
| 68000 + Z80 do Mega Drive (extração) | `mdconv/chips/native/md68k/` | `md68k.dll` | `libmd68k.so` |

- **Linux:** os `.so` se recompilam sozinhos quando um `.c` muda.
- **Windows:** as DLLs são geradas **no Linux** com `sh tools/build_dll.sh` (precisa de `clang`,
  `lld` e `llvm-dlltool`). Depois de mudar um `.c`, rode esse script antes de compilar para Windows:
  o `build_windows.bat` (via `tools/check_native.py`) se recusa a empacotar DLLs geradas com fontes
  antigos.

## Finais de linha

O `.gitattributes` mantém os scripts `.sh` e o `PKGBUILD` com finais de linha do Linux (LF) e o
`.bat` com os do Windows (CRLF). Se você copiar o projeto sem o git e editar um `.sh` no Windows,
salve com LF (no Notepad++: Editar → Conversão de fim de linha → Unix), senão o Linux reclama de
`$'\r': command not found`.
