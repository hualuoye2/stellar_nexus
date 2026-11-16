# -*- coding: utf-8 -*-
"""开发期工具：给 RealSkyView 打点，查清重绘频率与单帧耗时。

不修改源码：运行时包裹 paintEvent 与 mark_dirty，统计 3 秒内的调用次数。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)
    from ui.main_window import MainWindow
    from ui.real_sky_view import RealSkyView

    stats = {"paint": 0, "paint_ms": 0.0, "dirty": 0, "update": 0, "tick": 0}

    orig_paint = RealSkyView.paintEvent
    orig_dirty = RealSkyView.mark_dirty
    orig_tick = RealSkyView._on_tick
    orig_update = RealSkyView.update

    def paint(self, event):
        t0 = time.perf_counter()
        try:
            return orig_paint(self, event)
        finally:
            stats["paint"] += 1
            stats["paint_ms"] += (time.perf_counter() - t0) * 1000.0

    def dirty(self):
        stats["dirty"] += 1
        return orig_dirty(self)

    def tick(self):
        stats["tick"] += 1
        return orig_tick(self)

    def update(self, *a, **kw):
        stats["update"] += 1
        return orig_update(self, *a, **kw)

    RealSkyView.paintEvent = paint
    RealSkyView.mark_dirty = dirty
    RealSkyView._on_tick = tick
    RealSkyView.update = update

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 100) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(800)
    print("=" * 74)
    print("① 空转 3 秒的调用统计（应接近 0：脏标记机制应让空闲时零重绘）")
    print("=" * 74)
    for key in stats:
        stats[key] = 0
    t0 = time.process_time()
    w0 = time.perf_counter()
    end = w0 + 3.0
    while time.perf_counter() < end:
        pump(50)
    cpu = (time.process_time() - t0) / (time.perf_counter() - w0) * 100.0
    print(f"   CPU 占用率        : {cpu:.0f}%")
    print(f"   paintEvent 次数    : {stats['paint']}  "
          f"（≈{stats['paint'] / 3:.0f} 次/秒）")
    print(f"   累计绘制耗时       : {stats['paint_ms']:.0f} ms  "
          f"（单帧 {stats['paint_ms'] / max(1, stats['paint']):.1f} ms）")
    print(f"   _on_tick 次数      : {stats['tick']}（≈{stats['tick'] / 3:.0f} 次/秒，定时器 16ms ⇒ 应 ~62）")
    print(f"   mark_dirty 次数    : {stats['dirty']}")
    print(f"   update() 次数      : {stats['update']}")

    print()
    print("=" * 74)
    print("② 再把 plot 拆开看：单帧里哪一层最贵")
    print("=" * 74)
    rv = win.real_view
    layers = [("背景", rv._draw_background), ("地下", rv._draw_underground),
              ("银河带", rv._draw_milky_way), ("网格", rv._draw_grid),
              ("星座连线", rv._draw_constellation_lines), ("恒星", rv._draw_stars),
              ("行星", rv._draw_planets), ("日月", rv._draw_sun_moon),
              ("深空", rv._draw_messier), ("星座名", rv._draw_constellation_names),
              ("方位", rv._draw_azimuth_labels), ("HUD", rv._draw_hud)]
    from PySide6.QtGui import QPainter
    rv._prepare_frame()
    for name, fn in layers:
        p = QPainter(rv)
        try:
            fn(p)
        except Exception as exc:
            print(f"   {name}: 异常 {exc}")
            p.end()
            continue
        p.end()
        t0 = time.perf_counter()
        for _ in range(3):
            p = QPainter(rv)
            try:
                fn(p)
            finally:
                p.end()
        dt = (time.perf_counter() - t0) / 3 * 1000.0
        print(f"   {name:10s} {dt:7.2f} ms")

    print()
    print("=" * 74)
    print("③ 谁在请求重绘：清空标记后再看")
    print("=" * 74)
    rv._needs_repaint = False
    for key in stats:
        stats[key] = 0
    pump(1000)
    print(f"   1 秒内 paintEvent={stats['paint']}  update={stats['update']}  "
          f"mark_dirty={stats['dirty']}  _needs_repaint={rv._needs_repaint}")
    win.close()
    pump(150)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
