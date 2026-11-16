# -*- coding: utf-8 -*-
"""开发期工具：单独截取左控制面板与右信息面板，用于核验文字对比度。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "stellar_nexus")))

import datetime                                       # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    try:
        app.setStyle("Fusion")
        from 核心.utils import apply_dark_theme
        apply_dark_theme(app)
    except Exception:
        pass

    from ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1500, 940)
    win.show()

    def pump(ms: int = 220) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)
    rv = win.real_view
    rv.set_observer(39.9042, 116.4074, "北京", 8.0)
    rv.set_time(datetime.datetime(2026, 9, 21, 22, 0, 0))
    rv.set_layer("messier", True)

    # 现实星图模式：左面板为「时间 / 位置 / 搜索 / 图层」
    win.switch_mode("real")
    pump(300)
    left = win.left_panel.grab()
    left.save(os.path.join(HERE, "preview_left_panel.png"), "PNG")
    print("左控制面板:", left.width(), "x", left.height())

    # 选中一个天体，让右侧信息面板填满内容
    rv.find_object("木星")
    pump(250)
    right = win.info_container.grab()
    right.save(os.path.join(HERE, "preview_right_panel.png"), "PNG")
    print("右信息面板:", right.width(), "x", right.height())

    # 通用工具提示：把窗口整体截一张，检查整体明暗
    whole = win.grab()
    whole.save(os.path.join(HERE, "preview_window.png"), "PNG")
    print("整窗:", whole.width(), "x", whole.height())

    win.close()
    pump(80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
