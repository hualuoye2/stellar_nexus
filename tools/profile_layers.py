# -*- coding: utf-8 -*-
"""开发期工具：对现实星图做逐图层耗时剖析，找出全图层场景的瓶颈。"""
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
    try:
        app.setStyle("Fusion")
        from 核心.utils import apply_dark_theme
        apply_dark_theme(app)
    except Exception:
        pass

    from ui.real_sky_view import RealSkyView

    v = RealSkyView()
    v.resize(1500, 900)
    v.show()

    def pump(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(300)
    v.set_time(datetime.datetime(2026, 9, 21, 22, 0, 0))
    v.set_layer("below_horizon", True)
    v.set_layer("nearby_mark", True)
    v.set_layer("deep_sky_labels", True)
    v.set_layer("messier", True)
    v.recalculate(force=True)
    pump(150)

    layers = [
        ("背景", lambda p: v._draw_background(p)),
        ("地下阴影", lambda p: v._draw_underground(p)),
        ("银河带", lambda p: v._draw_milky_way(p)),
        ("地平网格", lambda p: v._draw_grid(p)),
        ("星座连线", lambda p: v._draw_constellation_lines(p)),
        ("恒星", lambda p: v._draw_stars(p)),
        ("近星高亮", lambda p: v._draw_nearby_marks(p)),
        ("行星", lambda p: v._draw_planets(p)),
        ("日月", lambda p: v._draw_sun_moon(p)),
        ("深空天体", lambda p: v._draw_messier(p)),
        ("星座名", lambda p: v._draw_constellation_names(p)),
        ("方位标签", lambda p: v._draw_azimuth_labels(p)),
        ("HUD", lambda p: v._draw_hud(p)),
    ]
    v._prepare_frame()
    print(f"窗口 {v.width()}x{v.height()}，地下天体 {v._below_count} 个，"
          f"恒星缓存 {len(v._stars)} 颗")
    total = 0.0
    for name, fn in layers:
        painter = QPainter(v)
        try:
            fn(painter)          # 预热
        except Exception as exc:
            print(f"   {name}: 异常 {exc}")
            painter.end()
            continue
        painter.end()
        t0 = time.perf_counter()
        for _ in range(3):
            painter = QPainter(v)
            try:
                fn(painter)
            finally:
                painter.end()
        dt = (time.perf_counter() - t0) / 3 * 1000
        total += dt
        print(f"   {name:10s} {dt:6.2f} ms")
    print(f"   {'合计':10s} {total:6.2f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
