# -*- coding: utf-8 -*-
"""开发期诊断：检查关键控件的调色板、背景色与样式表实际生效情况。

重点排查「文字看不见」的根因：是否有控件因为没有显式背景色而落到
Qt 默认浅色调色板上，形成浅灰字 + 浅色背景的低对比。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtGui import QColor, QPalette             # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QLabel, QLineEdit,  # noqa: E402
                               QScrollArea, QWidget)


def dump(app: QApplication, widget: QWidget, name: str, depth: int = 0) -> None:
    pal = widget.palette()
    bg = pal.color(QPalette.Window)
    fg = pal.color(QPalette.WindowText)
    base = pal.color(QPalette.Base)
    txt = pal.color(QPalette.Text)
    print(f"{'  ' * depth}{name:<26} {type(widget).__name__:<18} "
          f"Window={bg.name()} WindowText={fg.name()} Base={base.name()} Text={txt.name()} "
          f"autoFill={widget.autoFillBackground()}")


def main() -> int:
    app = QApplication(sys.argv[:1])
    try:
        app.setStyle("Fusion")
        from 核心.utils import apply_dark_theme
        apply_dark_theme(app)
    except Exception:
        pass
    # 与 main.py 一致地加载样式表
    qss_path = os.path.join(ROOT, "资产", "styles.qss")
    if os.path.isfile(qss_path):
        app.setStyleSheet(open(qss_path, encoding="utf-8").read())
        print("已加载 styles.qss")
    app_pal = app.palette()
    print("== 应用默认调色板 ==")
    for role in (QPalette.Window, QPalette.WindowText, QPalette.Base,
                 QPalette.Text, QPalette.Button, QPalette.ButtonText):
        print(f"   {role.name:<14} {app_pal.color(role).name()}")

    from ui.main_window import MainWindow
    win = MainWindow()
    win.resize(1400, 860)
    win.show()

    def pump(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)

    print("\n== 左侧控制面板 ==")
    for w in win.left_panel.findChildren(QScrollArea):
        dump(app, w, "QScrollArea")
        vp = w.viewport()
        if vp is not None:
            dump(app, vp, "  viewport", 1)
    for w in win.left_panel.findChildren(QCheckBox)[:3]:
        dump(app, w, f"QCheckBox({w.text()})")
    for w in win.left_panel.findChildren(QLabel)[:4]:
        dump(app, w, f"QLabel({w.text()[:12]})")

    print("\n== 右侧信息面板 ==")
    for w in win.info_panel.findChildren(QScrollArea):
        dump(app, w, "QScrollArea")
        vp = w.viewport()
        if vp is not None:
            dump(app, vp, "  viewport", 1)
    for w in win.info_panel.sky_panel.findChildren(QLabel)[:6]:
        dump(app, w, f"QLabel({w.text()[:14]})")

    print("\n== 关键自定义控件 ==")
    print("   left_stack widget bg role:", win.left_stack.currentWidget().palette().color(QPalette.Window).name())

    win.close()
    pump(80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
