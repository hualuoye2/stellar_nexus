# -*- coding: utf-8 -*-
"""开发期工具：追出 paintEvent 是谁触发的（完整调用栈）。"""
import io
import os
import sys
import time
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer           # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)
    from ui.main_window import MainWindow
    from ui.real_sky_view import RealSkyView

    stacks: list = []
    counts = {"paint": 0}
    orig_paint = RealSkyView.paintEvent

    def paint(self, event):
        counts["paint"] += 1
        if len(stacks) < 8:
            frames = traceback.extract_stack()[:-1]
            stacks.append(" <- ".join(
                f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}"
                for f in frames[-8:]))
        return orig_paint(self, event)

    RealSkyView.paintEvent = paint

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 100) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(900)
    rv = win.real_view
    print("=" * 76)
    print("① 空闲 2 秒：paintEvent 的完整调用栈")
    print("=" * 76)
    counts["paint"] = 0
    stacks.clear()
    t0 = time.process_time()
    w0 = time.perf_counter()
    end = w0 + 2.0
    while time.perf_counter() < end:
        pump(50)
    cpu = (time.process_time() - t0) / (time.perf_counter() - w0) * 100.0
    print(f"   CPU {cpu:.0f}%   paintEvent {counts['paint']} 次")
    print(f"   _needs_repaint={rv._needs_repaint}  isVisible={rv.isVisible()}")
    for i, st in enumerate(stacks[:4], 1):
        print(f"   [{i}] {st}")

    print()
    print("=" * 76)
    print("② 把 RealSkyView 从布局里摘出来（看是否与父容器/兄弟控件有关）")
    print("=" * 76)
    rv.setParent(None)
    rv.resize(1500, 900)
    rv.show()
    pump(300)
    counts["paint"] = 0
    t0 = time.process_time()
    w0 = time.perf_counter()
    end = w0 + 2.0
    while time.perf_counter() < end:
        pump(50)
    cpu = (time.process_time() - t0) / (time.perf_counter() - w0) * 100.0
    print(f"   独立窗口后 CPU {cpu:.0f}%   paintEvent {counts['paint']} 次")

    print()
    print("=" * 76)
    print("③ 停掉 _timer 后再测（独立窗口）")
    print("=" * 76)
    rv._timer.stop()
    pump(200)
    counts["paint"] = 0
    t0 = time.process_time()
    w0 = time.perf_counter()
    end = w0 + 2.0
    while time.perf_counter() < end:
        pump(50)
    cpu = (time.process_time() - t0) / (time.perf_counter() - w0) * 100.0
    print(f"   停定时器后 CPU {cpu:.0f}%   paintEvent {counts['paint']} 次")

    win.close()
    pump(150)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
