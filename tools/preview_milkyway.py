# -*- coding: utf-8 -*-
"""开发期工具：把现实星图在四个方向上的画面拼成一张预览图，用于检查银河带渲染。"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "stellar_nexus")))

import datetime                                     # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer, Qt   # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402


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

    pump(350)
    rv = win.real_view
    rv.set_observer(39.9042, 116.4074, "北京", 8.0)
    rv.set_time(datetime.datetime(2025, 8, 15, 22, 0, 0))
    rv.set_layer("messier", False)
    rv.set_layer("deep_sky_labels", False)
    rv.fov = 100.0

    views = (("天鹅座方向（银河最壮观段）", 45.0, 70.0),
             ("银心方向（人马座）", 22.0, 180.0),
             ("北极星方向（银河较淡）", 42.0, 0.0),
             ("南天低空", 30.0, 180.0))
    w, h = 900, 600
    sheet = QImage(w * 2, h * 2, QImage.Format_ARGB32)
    sheet.fill(QColor(4, 8, 16))
    p = QPainter(sheet)
    for i, (label, alt, az) in enumerate(views):
        rv.set_view_center(alt, az)
        rv.recalculate(force=True)
        pump(120)
        LOG.entries.clear()
        rv.repaint()
        pump(30)
        img = rv.grab().toImage().scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        p.drawImage((i % 2) * w, (i // 2) * h, img)
        p.setPen(QColor(255, 220, 120))
        p.setFont(QFont("Microsoft YaHei UI", 12))
        p.drawText((i % 2) * w + 12, (i // 2) * h + 24, label)
        bad = [e.message for e in LOG.entries if e.level == "ERROR"]
        if bad:
            print("ERR", label, bad[:1])
    p.end()
    out = os.path.join(HERE, "preview_milkyway.png")
    sheet.save(out, "PNG")
    print("已保存", out)

    t0 = time.perf_counter()
    for _ in range(10):
        rv.recalculate(force=True)
        rv.repaint()
    print(f"含银河带的平均帧耗时：{(time.perf_counter() - t0) / 10 * 1000:.1f} ms")
    win.close()
    pump(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
