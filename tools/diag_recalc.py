# -*- coding: utf-8 -*-
"""开发期工具：精确统计 recalculate 的调用次数与真实重算次数。"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer           # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402


def idle(seconds: float):
    c0 = time.process_time()
    w0 = time.perf_counter()
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()
    dt = time.perf_counter() - w0
    return (time.process_time() - c0) / max(1e-6, dt) * 100.0, dt


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)
    from ui.real_sky_view import RealSkyView
    from 核心 import astronomy as A
    from 核心 import star_data as SD
    from 核心 import planet_data as P

    rv = RealSkyView()
    rv.resize(1400, 860)
    rv.show()

    stats = {"calls": 0, "real": 0, "fast": 0, "compute_ms": 0.0}
    orig = rv.recalculate
    orig_compute = A.compute_sky

    def compute(*a, **kw):
        t0 = time.perf_counter()
        try:
            return orig_compute(*a, **kw)
        finally:
            stats["compute_ms"] += (time.perf_counter() - t0) * 1000.0

    def recalc(force=False):
        stats["calls"] += 1
        before = stats["compute_ms"]
        r = orig(force)
        if stats["compute_ms"] > before:
            stats["real"] += 1
        else:
            stats["fast"] += 1
        return r

    A.compute_sky = compute
    rv.recalculate = recalc
    import 核心.astronomy
    核心.astronomy.compute_sky = compute
    # real_sky_view 里用的是 A.compute_sky，需要同步模块引用
    import ui.real_sky_view as rsv
    rsv.A.compute_sky = compute

    # 预热
    loop = QEventLoop()
    QTimer.singleShot(600, loop.quit)
    loop.exec()

    print("=" * 76)
    print("① 单独 RealSkyView 空闲 3 秒")
    print("=" * 76)
    stats.update({"calls": 0, "real": 0, "fast": 0, "compute_ms": 0.0})
    cpu, dt = idle(3.0)
    print(f"   CPU {cpu:.1f}%")
    print(f"   recalculate 调用 {stats['calls']} 次（{stats['calls'] / dt:.1f} 次/秒）")
    print(f"     其中真正重算 {stats['real']} 次，走节流跳过 {stats['fast']} 次")
    print(f"     compute_sky 总耗时 {stats['compute_ms']:.0f} ms "
          f"（平均 {stats['compute_ms'] / max(1, stats['real']):.1f} ms/次）")
    print(f"   timer 活跃={rv._timer.isActive()} 间隔={rv._timer.interval()}ms")
    print(f"   time_playing={rv.time_playing}  time_speed={rv.time_speed}")

    print()
    print("=" * 76)
    print("② 强制 time_playing=False 再测（排除时间推进的影响）")
    print("=" * 76)
    rv.time_playing = False
    stats.update({"calls": 0, "real": 0, "fast": 0, "compute_ms": 0.0})
    cpu, dt = idle(3.0)
    print(f"   CPU {cpu:.1f}%   recalculate {stats['calls']} 次/"
          f"（{stats['calls'] / dt:.1f} 次/秒）  真正重算 {stats['real']} 次  "
          f"compute_sky {stats['compute_ms']:.0f} ms")

    print()
    print("=" * 76)
    print("③ 只跑 _on_tick 本体，看它是否每次都真的重算")
    print("=" * 76)
    stats.update({"calls": 0, "real": 0, "fast": 0, "compute_ms": 0.0})
    ticks = {"n": 0}
    orig_tick = rv._on_tick

    def tick():
        ticks["n"] += 1
        return orig_tick()

    rv._on_tick = tick
    rv._timer.timeout.disconnect()
    rv._timer.timeout.connect(tick)
    cpu, dt = idle(3.0)
    print(f"   CPU {cpu:.1f}%   _on_tick {ticks['n']} 次（{ticks['n'] / dt:.1f} 次/秒）")
    print(f"   recalculate {stats['calls']} 次  真正重算 {stats['real']} 次  "
          f"compute_sky {stats['compute_ms']:.0f} ms")

    rv._timer.stop()
    rv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
