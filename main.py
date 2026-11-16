# -*- coding: utf-8 -*-
"""
main.py —— STELLAR NEXUS 星图终端 程序入口
================================================================================
启动流程：
    1. 高 DPI 适配（必须在 QApplication 创建之前设置）
    2. 安装全局异常钩子（任何异常都进日志 + 状态栏，绝不在控制台抛出 traceback）
    3. 创建 QApplication、加载中文字体与科幻样式表
    4. 生成/加载应用图标（缺失时自动程序化生成）
    5. 显示主窗口并进入事件循环

直接运行：
    python main.py
"""

from __future__ import annotations

import os
import sys
import traceback

# ---- 保证项目根目录在导入路径中（打包后同样成立） ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ==============================================================================
# 〇、音频子进程入口（必须在任何 Qt/窗口初始化**之前**判断并返回）
# ==============================================================================
# 「星图之声」的音频合成跑在子进程里（否则主线程重绘会抢走 GIL，音乐卡顿）。
# 打包成单文件 EXE 之后，`sys.executable` 就是这个 EXE，而 `核心` 包也不在
# 文件系统上（在 EXE 内部），所以**不能再 spawn 一个 python 去 import 它**。
# 正确做法是：设一个环境变量，用同一个可执行文件再起一次进程，
# 在入口处看到标志就直接跑音频循环、绝不打开窗口。
if os.environ.get("_SNX_AUDIO_CHILD"):
    def _run_audio_child() -> int:
        try:
            from multiprocessing import shared_memory
            import 核心.sonify as _S
            engine = _S.SonifyEngine()
            engine._child_shm = shared_memory.SharedMemory(
                name=os.environ["_SNX_SHM"])
            engine._child_handle = 1
            engine._pipe_fd = int(os.environ.get("_SNX_PIPE_FD", "-1"))
            engine._status_w = int(os.environ.get("_SNX_STATUS_FD", "-1"))
            return engine.run_audio_process()
        except Exception as exc:                    # pragma: no cover
            sys.stderr.write("音频子进程启动失败: %r\n" % (exc,))
            return 9

    raise SystemExit(_run_audio_child())

from 核心.utils import (APP_NAME, APP_SHORT, APP_VERSION, LOG, apply_dark_theme, asset_dir,
                        asset_path, ensure_dir, is_frozen)

# ==============================================================================
# 一、Qt 导入与高 DPI 设置（必须在创建 QApplication 之前完成）
# ==============================================================================

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"缺少 PySide6 依赖：{exc}\n请先执行：pip install -r requirements.txt\n")
    raise SystemExit(2)


def _enable_high_dpi() -> None:
    """开启高 DPI 缩放，避免高分屏下界面模糊。

    Qt6 默认已启用高 DPI 缩放，这里显式设置舍入策略与小数缩放，
    并兼容 PySide6 6.6 之前版本的枚举位置差异。
    """
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    for attr in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        try:
            value = getattr(Qt, attr, None) or getattr(Qt.ApplicationAttribute, attr, None)
            if value is not None:
                QApplication.setAttribute(value, True)
        except Exception:
            pass


# ==============================================================================
# 二、样式表与字体
# ==============================================================================

def _load_stylesheet() -> str:
    """读取科幻风格样式表；文件缺失时返回空串（不影响运行）。"""
    for name in ("styles.qss",):
        path = asset_path(name)
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fp:
                    return fp.read()
        except Exception as exc:
            LOG.warn(f"读取样式表失败：{exc}")
    LOG.warn("未找到 assets/styles.qss，使用内置默认外观")
    return ""


def _pick_font() -> str:
    """选择一款可用的中文字体，保证界面中文不出现方块。"""
    try:
        families = set(QFontDatabase.families())
        for name in ("Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei",
                     "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC",
                     "Source Han Sans SC", "WenQuanYi Micro Hei", "SimHei"):
            if name in families:
                return name
    except Exception:
        pass
    return "Microsoft YaHei UI"


def _ensure_icon() -> str:
    """确保应用图标存在（缺失时用 资产/make_assets.py 程序化生成）。"""
    icon_path = asset_path("icon.png")
    ico_path = asset_path("icon.ico")
    if os.path.isfile(ico_path):
        return ico_path
    if os.path.isfile(icon_path):
        return icon_path
    try:
        ensure_dir(asset_dir())
        script = asset_path("make_assets.py")
        if os.path.isfile(script):
            import runpy
            runpy.run_path(script, run_name="__main__")
            for candidate in (ico_path, icon_path):
                if os.path.isfile(candidate):
                    return candidate
    except Exception as exc:
        LOG.warn(f"生成应用图标失败（不影响运行）：{exc}")
    return ""


# ==============================================================================
# 三、主函数
# ==============================================================================

def main(argv: list | None = None) -> int:
    """程序入口。返回退出码。"""
    argv = list(sys.argv if argv is None else argv)
    _enable_high_dpi()

    # ---- 创建应用 ----
    app = QApplication(argv)
    app.setApplicationName(APP_SHORT)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("StellarNexus")

    # 关键：先切到 Fusion 样式并应用深色调色板，再加载 QSS。
    # 未被样式表覆盖的普通容器控件会退回系统调色板——Windows 默认浅灰底，
    # 会让浅色文字"消失"。Fusion + 深色调色板从根上避免这个问题，
    # 同时保证 Windows / macOS / Linux 三平台外观一致。
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    apply_dark_theme(app)

    # 全局异常钩子：所有未捕获异常都会写进日志，而不是让控制台报错
    LOG.install_excepthook()

    # ---- 字体与样式 ----
    family = _pick_font()
    font = QFont(family, 9)
    app.setFont(font)
    LOG.info(f"界面字体：{family}")

    qss = _load_stylesheet()
    if qss:
        app.setStyleSheet(qss)
        LOG.info("已加载科幻风格样式表 assets/styles.qss")

    # ---- 图标 ----
    icon_file = _ensure_icon()
    if icon_file:
        try:
            app.setWindowIcon(QIcon(icon_file))
        except Exception:
            pass

    # ---- 主窗口 ----
    try:
        from ui.main_window import MainWindow
        window = MainWindow()
        if icon_file:
            try:
                window.setWindowIcon(QIcon(icon_file))
            except Exception:
                pass
        window.show()
    except Exception as exc:  # pragma: no cover - 启动失败时给出可读提示
        detail = traceback.format_exc()
        LOG.error("主窗口初始化失败", exc)
        QMessageBox.critical(
            None, "启动失败",
            f"主窗口初始化失败：\n{type(exc).__name__}: {exc}\n\n"
            f"详细信息已写入日志：\n{LOG.recent_text(12)}")
        sys.stderr.write(detail)
        return 1

    # 启动后打印一次关键信息（控制台可见，但不会报错）
    LOG.info(f"{APP_NAME} v{APP_VERSION} 已启动（冻结模式：{is_frozen()}）")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
