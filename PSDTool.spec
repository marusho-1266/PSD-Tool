# -*- mode: python ; coding: utf-8 -*-
"""ワンファイル exe（GUI・コンソール非表示）。ルートで `pyinstaller PSDTool.spec` を実行。"""
from pathlib import Path

block_cipher = None

root = Path(SPEC).resolve().parent

hiddenimports = [
    "PIL.ImageQt",
    "psd_tools",
    "psd_tools.constants",
    "psd_tools.psd.image_resources",
]

a = Analysis(
    [str(root / "run_exe_entry.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PSDResizeTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
