# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None

project_root = Path(r"D:\源码\自写工具\WeChat-H5-DevTools")
uverif_path = Path(r"E:\模块\uverif_auth")

added_datas = [
    (str(project_root / "wechat_h5_devtools" / "gui" / "index.html"), "wechat_h5_devtools/gui"),
    (str(project_root / "wechat_h5_devtools" / "gui" / "logo.ico"), "wechat_h5_devtools/gui"),
    (str(project_root / "wechat_h5_devtools" / "gui" / "logo.png"), "wechat_h5_devtools/gui"),
    (str(project_root / "assets" / "app.ico"), "assets"),
    (str(project_root / "resources"), "resources"),
]

hidden_imports = [
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr",
    "rich",
    "rich.console",
    "rich.panel",
    "rich.table",
    "click",
    "websockets",
    "wechat_h5_devtools.mcp.tools",
    "wechat_h5_devtools.mcp.server",
    "wechat_h5_devtools.mcp.resources",
    "wechat_h5_devtools.mcp.prompts",
    "wechat_h5_devtools.injector.wechat_finder",
    "wechat_h5_devtools.injector.article_devtools",
    "wechat_h5_devtools.sandbox.browser_launcher",
    "wechat_h5_devtools.extractor.project_dumper",
    "wechat_h5_devtools.extractor.sourcemap_rebuilder",
    "wechat_h5_devtools.extractor.api_analyzer",
    "wechat_h5_devtools.extractor.deobfuscator",
    "wechat_h5_devtools.auth.manager",
    "wechat_h5_devtools.auth.credentials",
    "wechat_h5_devtools.auth.security",
    "client",
    "fingerprint",
    "crypto",
]

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root), str(uverif_path)],
    binaries=[],
    datas=added_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "scipy",
        "pandas",
        "numpy",
        "IPython",
        "tkinter",
        "scratch",
        "tests",
        "pytest",
    ],
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
    name="WeChat-H5-DevTools",
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
    icon=str(project_root / "wechat_h5_devtools" / "gui" / "logo.ico"),
    uac_admin=False,
)
