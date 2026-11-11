# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('mdconv')


a = Analysis(
    ['run_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('mdconv/chips/native/mdchips.dll', 'mdconv/chips/native'), ('mdconv/chips/native/nuked_opn2/nuked_opn2.dll', 'mdconv/chips/native/nuked_opn2'), ('mdconv/chips/native/nuked_opn2/ym3438.c', 'mdconv/chips/native/nuked_opn2'), ('mdconv/chips/native/nuked_opn2/ym3438.h', 'mdconv/chips/native/nuked_opn2'), ('mdconv/chips/native/nuked_opn2/nuked_wrap.c', 'mdconv/chips/native/nuked_opn2'), ('mdconv/chips/native/nuked_opn2/LICENSE', 'mdconv/chips/native/nuked_opn2'), ('mdconv/chips/native/nuked_opn2/README.txt', 'mdconv/chips/native/nuked_opn2')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MegaDriveAudioConverter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MegaDriveAudioConverter',
)
