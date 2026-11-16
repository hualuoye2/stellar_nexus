# -*- mode: python ; coding: utf-8 -*-
"""
build.spec —— PyInstaller 打包配置（STELLAR NEXUS 星图终端）
================================================================================
用法（Windows）：
    pyinstaller --noconfirm build.spec

用法（macOS / Linux）：
    pyinstaller --noconfirm build.spec

等价的命令行写法（不依赖本文件）：
    pyinstaller --noconfirm --onefile --windowed \
        --icon=资产/icon.ico --name="StellarNexus" \
        --add-data="资产;资产" main.py
    （macOS / Linux 下 --add-data 的分隔符是冒号：--add-data="资产:资产"）

说明：
    * 资源目录同时兼容「资产」与「assets」两种命名，脚本会自动挑选存在的那个
    * 采用 --onefile 单文件模式：双击 EXE 即可运行，无需安装 Python
    * 若要加快启动速度，可把 ONE_FILE 改为 False（生成 dist/StellarNexus/ 目录版）
"""

import os
import sys

# ---- 基本参数 ----
ONE_FILE = True                 # True = 单文件 EXE；False = 目录版（启动更快）
SHOW_CONSOLE = False            # True = 保留控制台窗口（排查启动问题时可临时打开）

APP_NAME = "StellarNexus"
PROJECT_DIR = os.path.abspath(os.path.dirname(SPEC))      # noqa: F821  (SPEC 由 PyInstaller 注入)
ENTRY = os.path.join(PROJECT_DIR, "main.py")

# ---- 资源目录自动识别（兼容中文目录名与 assets 别名） ----
ASSET_DIR = None
for candidate in ("资产", "assets", "Assets"):
    path = os.path.join(PROJECT_DIR, candidate)
    if os.path.isdir(path):
        ASSET_DIR = path
        break
if ASSET_DIR is None:
    ASSET_DIR = os.path.join(PROJECT_DIR, "资产")

ICON_CANDIDATES = [
    os.path.join(ASSET_DIR, "icon.ico"),
    os.path.join(ASSET_DIR, "icon.png"),
]
ICON_FILE = next((p for p in ICON_CANDIDATES if os.path.isfile(p)), None)

print(f"[build.spec] 项目目录 : {PROJECT_DIR}")
print(f"[build.spec] 资源目录 : {ASSET_DIR}")
print(f"[build.spec] 图标文件 : {ICON_FILE or '（未找到，将使用默认图标）'}")

# ---- 数据文件：把整个资产目录原样放进包内 ----
datas = []
if os.path.isdir(ASSET_DIR):
    datas.append((ASSET_DIR, os.path.basename(ASSET_DIR)))
# 开发辅助脚本一并打包（可选，方便在目标机器上重新生成图标）
for extra in ("README.md", "requirements.txt"):
    path = os.path.join(PROJECT_DIR, extra)
    if os.path.isfile(path):
        datas.append((path, "."))

# ---- 隐藏导入：确保核心包被完整收集 ----
# 注意：这份清单曾经严重落后于实际代码——只有 6 个核心模块和 8 个 ui 模块，
# 而项目实际有 17 + 20 个。PyInstaller 的静态分析虽然多数情况下能自动跟上
# import，但「按字符串动态导入」「只在异常分支里 import」这类写法会漏，
# 单文件 EXE 一旦漏模块就是启动即崩。所以这里逐个列全，改代码时同步维护。
hiddenimports = [
    # 核心
    "核心", "核心.astronomy", "核心.star_data", "核心.star_catalog_data",
    "核心.planet_data", "核心.world_generator", "核心.utils",
    "核心.deepseek_client",
    "核心.nearby_data", "核心.nearby_stars",
    "核心.inverse_sky",
    "核心.cities", "核心.city_data",
    "核心.sky_events", "核心.history_sim",
    "核心.light_pollution", "核心.observation_planner", "核心.export_media",
    # 界面
    "ui", "ui.main_window", "ui.real_sky_view", "ui.fiction_view",
    "ui.info_panel", "ui.widgets", "ui.body_renderer", "ui.deepseek_panel",
    "ui.object_browser",
    "ui.nearby_view", "ui.inverse_sky_view", "ui.inverse_sky_panel",
    "ui.orrery_view", "ui.charts", "ui.history_panel",
    "ui.planner_panel", "ui.export_panel", "ui.tour_panel", "ui.sky_calendar",
    # PySide6 中按需加载的模块
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
]

# ---- 排除不需要的重型模块 ----
excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "jupyter",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNetworkAuth",
    "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
]

# ---- 分析 ----
a = Analysis(                                   # noqa: F821
    [ENTRY],
    pathex=[PROJECT_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)                               # noqa: F821

# ---- 打包 ----
if ONE_FILE:
    exe = EXE(                                  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,                              # PySide6 用 UPX 压缩容易出问题，保持关闭
        upx_exclude=[],
        runtime_tmpdir=None,
        console=SHOW_CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON_FILE,
    )
else:
    exe = EXE(                                  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=SHOW_CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON_FILE,
    )
    coll = COLLECT(                             # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )

# ---- macOS 应用包（在 macOS 上打包时自动生成 .app） ----
if sys.platform == "darwin":
    app = BUNDLE(                               # noqa: F821
        exe,
        name=f"{APP_NAME}.app",
        icon=ICON_FILE,
        bundle_identifier="com.stellarnexus.skymap",
        info_plist={
            "CFBundleName": "StellarNexus",
            "CFBundleDisplayName": "STELLAR NEXUS 星图终端",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.15",
        },
    )
