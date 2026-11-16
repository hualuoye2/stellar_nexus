# -*- coding: utf-8 -*-
"""开发期工具：真·空闲 CPU 测量。

要点：不用 QTimer.singleShot 反复"点一下"事件循环（那本身会创建大量定时器、
污染测量），而是让 Qt 自己的事件循环连续跑 N 秒，期间不做任何操作。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer           # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)
    from ui.main_window import MainWindow
    from ui.real_sky_view import RealSkyView

    paints = {"n": 0}
    orig_paint = RealSkyView.paintEvent

    def paint(self, event):
        paints["n"] += 1
        return orig_paint(self, event)

    RealSkyView.paintEvent = paint

    win = MainWindow()
    win.resize(1400, 860)
    win.show()

    # 只跑一次单发定时器做初始化等待
    loop = QEventLoop()
    QTimer.singleShot(800, loop.quit)
    loop.exec()

    def idle(seconds: float) -> tuple:
        """让事件循环连续运行 seconds 秒，返回 (CPU占用率, paint次数)。"""
        paints["n"] = 0
        c0 = time.process_time()
        w0 = time.perf_counter()
        inner = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), inner.quit)
        inner.exec()
        dt = time.perf_counter() - w0
        cpu = (time.process_time() - c0) / max(1e-6, dt) * 100.0
        return cpu, paints["n"], dt

    print("=" * 76)
    print("① 现实星图模式（当前）")
    print("=" * 76)
    cpu, n, dt = idle(3.0)
    print(f"   空闲 {dt:.1f} 秒：CPU {cpu:.0f}%   paintEvent {n} 次"
          f"（{n / dt:.1f} 次/秒）")
    print(f"   _needs_repaint={win.real_view._needs_repaint}")

    print()
    print("=" * 76)
    print("② 逐个模式切换后再测（每个模式空闲 2 秒）")
    print("=" * 76)
    for mode in ("real", "orrery", "nearby", "fiction"):
        win.switch_mode(mode)
        loop2 = QEventLoop()
        QTimer.singleShot(400, loop2.quit)
        loop2.exec()
        cpu, n, dt = idle(2.0)
        flag = "  ← 异常" if cpu > 25 else ""
        print(f"   {mode:8s} CPU {cpu:6.0f}%   paintEvent {n:4d} 次{flag}")

    print()
    print("=" * 76)
    print("③ 现实星图全图层打开（最重场景）")
    print("=" * 76)
    win.switch_mode("real")
    rv = win.real_view
    for layer in ("below_horizon", "nearby_mark", "deep_sky_labels"):
        rv.set_layer(layer, True)
    rv.recalculate(force=True)
    loop3 = QEventLoop()
    QTimer.singleShot(400, loop3.quit)
    loop3.exec()
    cpu, n, dt = idle(2.0)
    print(f"   CPU {cpu:.0f}%   paintEvent {n} 次（{n / dt:.1f} 次/秒）"
          f"   _needs_repaint={rv._needs_repaint}")

    print()
    print("=" * 76)
    print("④ 单帧实际耗时（直接调 paintEvent，看真实绘制成本）")
    print("=" * 76)
    for label, setup in (("默认图层", lambda: None),
                         ("全图层", lambda: [rv.set_layer(x, True) for x in
                                          ("below_horizon", "nearby_mark", "deep_sky_labels")]),
                         ("fov=30°", lambda: setattr(rv, "fov", 30.0)),
                         ("fov=150°", lambda: setattr(rv, "fov", 150.0))):
        rv.fov = 100.0
        for layer in ("below_horizon", "nearby_mark", "deep_sky_labels"):
            rv.set_layer(layer, False)
        setup()
        rv.recalculate(force=True)
        inner = QEventLoop()
        QTimer.singleShot(150, inner.quit)
        inner.exec()
        t0 = time.perf_counter()
        for _ in range(3):
            rv.repaint()
        ms = (time.perf_counter() - t0) / 3 * 1000.0
        print(f"   {label:10s} {ms:7.1f} ms/帧  （屏幕目标 {len(rv._last_screen)}）")

    win.close()
    loop4 = QEventLoop()
    QTimer.singleShot(200, loop4.quit)
    loop4.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
