# -*- coding: utf-8 -*-
"""开发期工具：把 _draw_stars 拆开量化，找出真正的大头。

同时确认空闲时是谁在触发重绘（mark_dirty 为 0 却仍在 paint）。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                        # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer           # noqa: E402
from PySide6.QtGui import QPainter                      # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)
    from ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 100) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(700)
    rv = win.real_view
    rv.set_time(datetime.datetime(2026, 9, 21, 22, 0, 0))
    rv.recalculate(force=True)
    pump(300)

    print("=" * 74)
    print("① 统计 _draw_stars 内部：像素尺寸 / 光晕数量")
    print("=" * 74)
    # 侵入式统计：包裹内部用到的两个函数
    made = {"glow": 0, "pix": 0}
    orig_glow = rv._glow_pixmap

    def glow(rgb, size):
        made["glow"] += 1
        return orig_glow(rgb, size)

    rv._glow_pixmap = glow
    for fov in (30.0, 70.0, 100.0, 150.0):
        rv.fov = fov
        rv.recalculate(force=True)
        made["glow"] = 0
        p = QPainter(rv)
        try:
            t0 = time.perf_counter()
            rv._draw_stars(p)
            dt = (time.perf_counter() - t0) * 1000.0
        finally:
            p.end()
        print(f"   fov={fov:5.0f}°   _draw_stars {dt:7.1f} ms   "
              f"光晕贴图请求 {made['glow']} 次   屏幕目标 {len(rv._last_screen)}")

    print()
    print("=" * 74)
    print("② 逐项验证：把可疑操作单独计时（各 200 次）")
    print("=" * 74)
    from PySide6.QtGui import QFont, QFontMetrics, QColor, QPen
    from PySide6.QtCore import QPointF, Qt, QRectF
    p = QPainter(rv)
    try:
        font = QFont("Microsoft YaHei UI", 8)
        p.setFont(font)
        t0 = time.perf_counter()
        for _ in range(200):
            QFontMetrics(font).horizontalAdvance("天狼星")
        dt_fm = (time.perf_counter() - t0) / 200 * 1000.0

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 200))
        t0 = time.perf_counter()
        for i in range(200):
            p.drawEllipse(QPointF(400 + i % 50, 300), 3.0, 3.0)
        dt_ell = (time.perf_counter() - t0) / 200 * 1000.0

        pix = rv._glow_pixmap((255, 240, 200), 38)
        t0 = time.perf_counter()
        for i in range(200):
            p.drawPixmap(QPointF(400 + i % 50, 300), pix)
        dt_pix38 = (time.perf_counter() - t0) / 200 * 1000.0

        pix12 = rv._glow_pixmap((255, 240, 200), 12)
        t0 = time.perf_counter()
        for i in range(200):
            p.drawPixmap(QPointF(400 + i % 50, 300), pix12)
        dt_pix12 = (time.perf_counter() - t0) / 200 * 1000.0

        p.setPen(QColor(200, 200, 200))
        t0 = time.perf_counter()
        for i in range(200):
            p.drawText(QRectF(400 + i % 50, 300, 60, 14), Qt.AlignLeft, "天狼星")
        dt_txt = (time.perf_counter() - t0) / 200 * 1000.0
    finally:
        p.end()
    print(f"   setFont 后取 QFontMetrics(新建)   : {dt_fm:6.3f} ms/次")
    print(f"   drawEllipse 半径 3px              : {dt_ell:6.3f} ms/次")
    print(f"   drawPixmap 38px 光晕              : {dt_pix38:6.3f} ms/次")
    print(f"   drawPixmap 12px 光晕              : {dt_pix12:6.3f} ms/次")
    print(f"   drawText 一个标签（含字体度量）    : {dt_txt:6.3f} ms/次")
    print()
    print(f"   推算：400 个目标 × (drawEllipse) = {dt_ell * 400:.1f} ms")
    print(f"   推算：136 个名称 × (drawText)    = {dt_txt * 136:.1f} ms")

    print()
    print("=" * 74)
    print("③ 空闲时到底谁在请求重绘：给 QWidget.update 打桩并记录调用栈")
    print("=" * 74)
    import traceback
    calls: list = []
    orig_update = type(rv).update

    def traced_update(self, *a, **kw):
        if len(calls) < 6:
            stack = [f"{f.name}:{f.lineno}"
                     for f in traceback.extract_stack()[:-1][-5:]]
            calls.append(" <- ".join(reversed(stack)))
        return orig_update(self, *a, **kw)

    type(rv).update = traced_update
    rv._needs_repaint = False
    pump(1200)
    type(rv).update = orig_update
    print(f"   1.2 秒内 update 调用样本（{len(calls)} 条）：")
    for line in calls:
        print("     " + line)
    if not calls:
        print("     （没有显式 update 调用 → 重绘来自 Qt 自身的窗口刷新，"
              "可能是 WA_OpaquePaintEvent + 未设置 backing store 导致）")

    win.close()
    pump(150)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
