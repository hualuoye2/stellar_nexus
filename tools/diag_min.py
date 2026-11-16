# -*- coding: utf-8 -*-
"""开发期工具：最小化测量，逐项定位空转来源。

逐层剥离：空白窗口 → +RealSkyView → +定时器 → +主窗口，
每一步都测真·空闲 CPU，找出从哪一步开始爆。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer           # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget     # noqa: E402


def idle(seconds: float = 2.0):
    c0 = time.process_time()
    w0 = time.perf_counter()
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()
    dt = time.perf_counter() - w0
    return (time.process_time() - c0) / max(1e-6, dt) * 100.0


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)
    from ui.real_sky_view import RealSkyView

    print("=" * 76)
    print("① 空白窗口基线")
    print("=" * 76)
    w0 = QWidget()
    w0.resize(1400, 860)
    w0.show()
    print(f"   CPU {idle():6.1f}%")

    print()
    print("=" * 76)
    print("② 只放一个 RealSkyView（含它自己的 16ms 定时器）")
    print("=" * 76)
    rv = RealSkyView()
    rv.resize(1400, 860)
    rv.show()
    paints = {"n": 0}
    ticks = {"n": 0}
    dirties = {"n": 0}
    o_paint, o_tick, o_dirty = rv.paintEvent, rv._on_tick, rv.mark_dirty

    def paint(e):
        paints["n"] += 1
        return o_paint(e)

    def tick():
        ticks["n"] += 1
        return o_tick()

    def dirty():
        dirties["n"] += 1
        return o_dirty()

    rv.paintEvent = paint
    rv._on_tick = tick
    rv.mark_dirty = dirty

    for name, label in ((idle, "第 1 次"),):
        paints["n"] = ticks["n"] = dirties["n"] = 0
        c = name()
        print(f"   {label} CPU {c:6.1f}%   paint={paints['n']}  tick={ticks['n']}  "
              f"mark_dirty={dirties['n']}  _needs_repaint={rv._needs_repaint}")
    paints["n"] = ticks["n"] = dirties["n"] = 0
    c = idle()
    print(f"   第 2 次 CPU {c:6.1f}%   paint={paints['n']}  tick={ticks['n']}  "
          f"mark_dirty={dirties['n']}  _needs_repaint={rv._needs_repaint}")

    print()
    print("=" * 76)
    print("③ 停掉 RealSkyView 的定时器")
    print("=" * 76)
    rv._timer.stop()
    paints["n"] = ticks["n"] = dirties["n"] = 0
    c = idle()
    print(f"   CPU {c:6.1f}%   paint={paints['n']}  tick={ticks['n']}  "
          f"mark_dirty={dirties['n']}  _needs_repaint={rv._needs_repaint}")

    print()
    print("=" * 76)
    print("④ 恢复定时器，但把 _on_tick 换成空实现（隔离定时器本身 vs 它做的事）")
    print("=" * 76)
    rv._timer.start()
    rv._on_tick = lambda: None
    paints["n"] = dirties["n"] = 0
    c = idle()
    print(f"   CPU {c:6.1f}%   paint={paints['n']}  mark_dirty={dirties['n']}")

    print()
    print("=" * 76)
    print("⑤ 只保留 _on_tick 里的 recalculate 那一段")
    print("=" * 76)

    def tick_recalc_only():
        rv.recalculate()

    rv._on_tick = tick_recalc_only
    paints["n"] = dirties["n"] = 0
    c = idle()
    print(f"   CPU {c:6.1f}%   paint={paints['n']}  mark_dirty={dirties['n']}  "
          f"_needs_repaint={rv._needs_repaint}")

    print()
    print("=" * 76)
    print("⑥ 直接连测 recalculate：它一次要多久、会不会置脏")
    print("=" * 76)
    dirties["n"] = 0
    t0 = time.perf_counter()
    for _ in range(20):
        rv._last_calc = 0.0        # 强制真正重算
        rv.recalculate()
    ms = (time.perf_counter() - t0) / 20 * 1000.0
    print(f"   强制重算 20 次：{ms:.1f} ms/次   mark_dirty 被调用 {dirties['n']} 次")
    dirties["n"] = 0
    t0 = time.perf_counter()
    for _ in range(200):
        rv.recalculate()           # 走节流分支
    ms2 = (time.perf_counter() - t0) / 200 * 1000.0
    print(f"   节流命中 200 次：{ms2:.3f} ms/次   mark_dirty 被调用 {dirties['n']} 次")

    rv._timer.stop()
    rv.close()
    w0.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
