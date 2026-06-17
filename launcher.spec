# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path('backend-django').resolve()))

from PyInstaller.utils.hooks import collect_submodules

hidden_modules = []
hidden_modules.extend(collect_submodules('application'))
hidden_modules.extend(collect_submodules('core'))
hidden_modules.extend(collect_submodules('scheduler'))
hidden_modules.extend(collect_submodules('common'))

a = Analysis(
    ['dyauthreply-client/launcher/launcher_bundled.py'],
    pathex=['backend-django'],
    binaries=[],
    datas=[('backend-django/core/douyin/runtime/transport/sign/js', 'core/douyin/runtime/transport/sign/js')],
    hiddenimports=hidden_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pyodbc', 'psycopg2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
