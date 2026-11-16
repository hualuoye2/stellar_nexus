# -*- coding: utf-8 -*-
"""开发期工具：对比「地平线下天体」图层开 / 关两种效果。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "stellar_nexus")))

import datetime                                        # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer, Qt       # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402


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
    win.resize(1400, 900)
    win.show()

    def pump(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)
    rv = win.real_view
    rv.set_observer(39.9042, 116.4074, "北京", 8.0)
    rv.set_time(datetime.datetime(2026, 9, 21, 22, 0, 0))
    rv.set_layer("star_names", True)
    rv.set_layer("messier", True)
    rv.fov = 105.0
    rv.set_view_center(15.0, 180.0)          # 略微下压视角，让地平线附近内容丰富

    w, h = 760, 620
    sheet = QImage(w * 2, h, QImage.Format_ARGB32)
    sheet.fill(QColor(4, 8, 16))
    p = QPainter(sheet)
    for i, (on, label) in enumerate(((False, "地平线下天体：关闭（只画实际可见）"),
                                     (True, "地平线下天体：打开（压暗 38%，名称前加 ↓）"))):
        rv.set_layer("below_horizon", on)
        rv.recalculate(force=True)
        pump(160)
        rv.repaint()
        pump(40)
        LOG.entries.clear()
        rv.repaint()
        pump(30)
        bad = [e.message for e in LOG.entries if e.level == "ERROR"]
        if bad:
            print("ERR", label, bad[:1])
        img = rv.grab().toImage().scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        p.drawImage(i * w, 0, img)
        p.setPen(QColor(255, 220, 120))
        p.setFont(QFont("Microsoft YaHei UI", 12))
        p.drawText(i * w + 12, 24, label)
    p.end()
    out = os.path.join(HERE, "preview_below_horizon.png")
    sheet.save(out, "PNG")
    print("已保存", out)
    win.close()
    pump(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
