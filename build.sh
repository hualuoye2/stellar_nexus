#!/usr/bin/env bash
# ============================================================================
#  build.sh —— STELLAR NEXUS 星图终端 macOS / Linux 打包脚本
# ----------------------------------------------------------------------------
#  用法：
#      chmod +x build.sh
#      ./build.sh
#  产物：
#      dist/StellarNexus           （Linux 单文件可执行程序）
#      dist/StellarNexus.app       （macOS 应用包，由 build.spec 中的 BUNDLE 生成）
#      dist/StellarNexus           （macOS 下同时保留命令行可执行文件）
# ============================================================================

set -euo pipefail
cd "$(dirname "$0")"

echo "============================================================================"
echo "  STELLAR NEXUS 星图终端 - macOS / Linux 打包脚本"
echo "============================================================================"
echo

# ---------------------------------------------------------------- 1. Python
PYTHON=""
for cand in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        PYTHON="$cand"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "[错误] 未检测到 Python 3.10+，请先安装："
    echo "        macOS : brew install python@3.12"
    echo "        Ubuntu: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

echo "[1/6] 检查 Python 版本..."
"$PYTHON" - <<'PY'
import sys
assert sys.version_info >= (3, 10), "需要 Python 3.10 或更高版本"
print("    Python", sys.version.split()[0], "OK")
PY
echo

# ---------------------------------------------------------------- 2. 依赖
echo "[2/6] 安装 / 校验依赖..."
"$PYTHON" -m pip install --upgrade pip --quiet || true
if ! "$PYTHON" -m pip install --disable-pip-version-check --quiet \
        "PySide6>=6.6,<6.10" "numpy>=1.24" "pyinstaller>=6.3"; then
    echo "[警告] 依赖安装出现问题，若本机已安装则可继续。"
fi
if ! "$PYTHON" -c "import PySide6; print('    PySide6', PySide6.__version__, 'OK')"; then
    echo "[错误] PySide6 不可用，请手动执行："
    echo "        $PYTHON -m pip install -r requirements.txt"
    exit 1
fi
echo

# ---------------------------------------------------------------- 3. 资源
echo "[3/6] 生成应用图标与贴图..."
if [ -f "资产/make_assets.py" ]; then
    QT_QPA_PLATFORM=offscreen "$PYTHON" "资产/make_assets.py" || echo "[警告] 图标生成失败，使用已有资源继续"
elif [ -f "assets/make_assets.py" ]; then
    QT_QPA_PLATFORM=offscreen "$PYTHON" "assets/make_assets.py" || echo "[警告] 图标生成失败，使用已有资源继续"
else
    echo "[警告] 未找到 make_assets.py，跳过图标生成。"
fi
echo

# 资源目录（兼容中文名与 assets 别名）
ASSET_DIR="资产"
if [ ! -d "$ASSET_DIR" ]; then
    if [ -d "assets" ]; then ASSET_DIR="assets"; fi
fi
ICON_ARG=""
if [ -f "$ASSET_DIR/icon.icns" ]; then
    ICON_ARG="--icon=$ASSET_DIR/icon.icns"
elif [ -f "$ASSET_DIR/icon.ico" ]; then
    ICON_ARG="--icon=$ASSET_DIR/icon.ico"
elif [ -f "$ASSET_DIR/icon.png" ]; then
    ICON_ARG="--icon=$ASSET_DIR/icon.png"
fi
echo "    资源目录：$ASSET_DIR"
echo

# ---------------------------------------------------------- 4. 文档审计
# 这个项目已经三次出现「文档承诺了某行为、代码其实没接上」（信息面板按钮不存在、
# 选中近星后观察点传不进去、星表计数三处互相矛盾）。这类问题不抛异常也不写日志，
# 只会让人排查时从错误的前提出发，所以打包前先跑一遍 README 断言审计。
# 用 STELLAR_SKIP_AUDIT=1 ./build.sh 可跳过；审计失败时交互确认（非交互环境直接继续）。
echo "[4/6] 文档审计：核对 README 里的数字 / 快捷键 / 菜单 / 模块清单..."
if [ "${STELLAR_SKIP_AUDIT:-0}" = "1" ]; then
    echo "    （STELLAR_SKIP_AUDIT=1，已跳过）"
elif [ -f "../tools/audit_readme.py" ]; then
    if ! "$PYTHON" "../tools/audit_readme.py"; then
        echo
        echo "[警告] README 与代码不一致（详见上面的 FAIL 列表）。"
        echo "        这不会影响打包结果，但文档错会让下次排查从错误的前提出发。"
        if [ -t 0 ]; then
            read -r -p "        仍要继续打包吗？(y/N) " ans
            case "$ans" in
                [yY]*) ;;
                *) echo "        已取消打包。请先修正 README。"; exit 1 ;;
            esac
        else
            echo "        （非交互环境，自动继续）"
        fi
    fi
else
    echo "[警告] 未找到 ../tools/audit_readme.py，跳过文档审计。"
fi
echo

# ---------------------------------------------------------------- 5. 清理
echo "[5/6] 清理旧的构建产物..."
rm -rf build dist
echo

# ---------------------------------------------------------------- 6. 打包
echo "[6/6] 开始打包（单文件，首次约需 1-3 分钟）..."
"$PYTHON" -m PyInstaller --noconfirm --clean \
    --onefile \
    --windowed \
    $ICON_ARG \
    --name "StellarNexus" \
    --add-data "$ASSET_DIR:$ASSET_DIR" \
    --hidden-import "核心" \
    --hidden-import "核心.star_catalog_data" \
    --hidden-import "ui.main_window" \
    --hidden-import "ui.real_sky_view" \
    --hidden-import "ui.fiction_view" \
    --hidden-import "ui.body_renderer" \
    --hidden-import "ui.deepseek_panel" \
    --hidden-import "ui.object_browser" \
    --hidden-import "核心.deepseek_client" \
    --collect-submodules "核心" \
    --collect-submodules "ui" \
    --exclude-module tkinter \
    --exclude-module matplotlib \
    --exclude-module scipy \
    --exclude-module pandas \
    main.py

echo
echo "============================================================================"
echo "  打包完成！"
if [ "$(uname)" = "Darwin" ]; then
    echo "  macOS 应用包： $(pwd)/dist/StellarNexus.app"
    echo "  命令行可执行： $(pwd)/dist/StellarNexus"
    echo
    echo "  首次打开若提示「无法验证开发者」，请执行："
    echo "      xattr -cr dist/StellarNexus.app"
    echo "  或在「系统设置 → 隐私与安全性」中点击「仍要打开」。"
else
    echo "  可执行文件： $(pwd)/dist/StellarNexus"
    echo "  运行：      ./dist/StellarNexus"
fi
echo "============================================================================"
echo
cat <<'TIPS'
---------------------- 常见打包问题与解决方案 ----------------------
1) macOS 报错 "Qt platform plugin cocoa could not be loaded"
   解决：确保 PySide6 是通过 pip 安装的官方版本，不要与 conda 混用；
         必要时加入 --collect-all PySide6 重新打包。

2) Linux 报错 "qt.qpa.plugin: Could not load the Qt platform plugin xcb"
   解决：安装系统依赖（Ubuntu 为例）：
         sudo apt install libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 \
                          libgl1 libegl1 libfontconfig1
         若在无桌面环境下运行，需保证有 X11 或 Wayland 会话。

3) Linux 下中文字体缺失（界面显示方块）
   解决：安装中文字体：
         sudo apt install fonts-noto-cjk fonts-wqy-microhei

4) 报错 "No module named 核心" / "No module named ui"
   解决：确认在项目根目录执行脚本；命令已包含 --collect-submodules。

5) 双击无反应
   解决：改用终端运行可执行文件查看报错信息，或参考 build.spec
         把 SHOW_CONSOLE 设为 True 重新打包。
--------------------------------------------------------------------
TIPS
