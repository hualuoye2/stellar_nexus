# -*- coding: utf-8 -*-
"""开发期工具：检查现实星图在新版渲染（精细天体模型 + 深空天体符号）下的表现。"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    app = QApplication(sys.argv[:1])
    try:
        app.setStyle("Fusion")
        from 核心.utils import apply_dark_theme
        apply_dark_theme(app)
    except Exception:
        pass
    from ui.main_window import MainWindow
    from 核心.utils import LOG

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(300)
    rv = win.real_view
    rv.set_layer("messier", True)
    rv.set_layer("deep_sky_labels", True)
    rv.set_observer(39.9042, 116.4074, "北京", 8.0)
    rv.fov = 40.0
    rv.recalculate(force=True)
    pump(120)

    errs = []
    for fov in (25.0, 40.0, 70.0, 110.0):
        rv.fov = fov
        rv.recalculate(force=True)
        LOG.entries.clear()
        rv.repaint()
        pump(40)
        bad = [e.message for e in LOG.entries if e.level == "ERROR"]
        errs += bad
        print(f"fov={fov:5.1f}  深空天体 {sum(1 for k in rv._last_screen if k.startswith('messier:')):3d}  "
              f"行星 {[k.split(':')[-1] for k in rv._last_screen if k.startswith('planet:')]}  "
              f"日月 {[k for k in rv._last_screen if k in ('sun', 'moon')]}  错误 {len(bad)}")

    # 保存一张放大后的截图，便于肉眼确认精细模型
    rv.fov = 22.0
    rv.set_view_center(35.0, 250.0)
    rv.recalculate(force=True)
    pump(150)
    rv.repaint()
    QApplication.processEvents()
    path = os.path.join(HERE, "preview_real_detail.png")
    rv.grab().save(path, "PNG")
    print("已保存", path)

    win.close()
    pump(60)
    print("渲染检查完成，错误数：", len(errs))
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
