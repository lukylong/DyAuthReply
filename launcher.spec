# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path('backend-django').resolve()))
sys.path.insert(0, str(Path('dyauthreply-client/launcher').resolve()))

from PyInstaller.utils.hooks import collect_submodules

hidden_modules = []
hidden_modules.extend(collect_submodules('application'))
hidden_modules.extend(collect_submodules('core'))
hidden_modules.extend(collect_submodules('scheduler'))
hidden_modules.extend(collect_submodules('common'))
hidden_modules.extend(collect_submodules('env'))
hidden_modules.extend(['launcher_logging', 'core.client.sign_probe'])

# 打包时内嵌 Node.js（抖音 JS 签名必需；Tauri sidecar / macOS .app 无 shell PATH）。
# 复用运行时同一套稳健发现逻辑（node_runtime.resolve_node_bin），避免 build 机 nvm/homebrew
# node 不在裸 PATH 时 shutil.which 漏判 → mac 包静默缺 node → 运行时回退 Java 引擎报错。
from node_runtime import resolve_node_bin

node_binaries = []
_node = resolve_node_bin()
if _node:
    print(f'[launcher.spec] 内嵌 Node.js -> runtime/: {_node}')
    node_binaries = [(str(Path(_node).resolve()), 'runtime')]
else:
    # 直接失败：宁可让构建中断，也不要产出一个无法签名/收消息的残缺客户端。
    raise SystemExit(
        '[launcher.spec] FATAL: 构建机未找到 Node.js，无法内嵌（抖音 JS 签名必需）。\n'
        '  请安装 Node.js 18+，或在打包前设置 DOUYIN_NODE_BIN=<node 绝对路径>。\n'
        '  注意 macOS 分发：建议指向官方/nvm 的 node（自包含 ICU、可移植）；\n'
        '  homebrew 的 /opt/homebrew/bin/node 依赖 homebrew dylib，拷到无 homebrew 的机器会起不来。'
    )

a = Analysis(
    ['dyauthreply-client/launcher/launcher_bundled.py'],
    pathex=['backend-django', 'dyauthreply-client/launcher'],
    binaries=node_binaries,
    datas=[
        ('backend-django/core/douyin/runtime/transport/sign/js', 'core/douyin/runtime/transport/sign/js'),
        ('dyauthreply-client/launcher/generated_defaults.json', 'launcher'),
    ],
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
