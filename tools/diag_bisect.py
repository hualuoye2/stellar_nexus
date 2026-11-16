# -*- coding: utf-8 -*-
"""开发期工具：终极二分 —— 主窗口 CPU 到底来自哪里。

对照链：
    A 空 QApplication
    B + 主窗口创建（show）
    C B + 停掉 4 个视图定时器
    D C + 停掉主窗口 2 个定时器
    E D + 隐藏所有视图
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


def sample(seconds: float = 2.0) -> float:
    c0 = time.process_time()
    w0 = time.perf_counter()
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()
    return (time.process_time() - c0) / max(1e-6, time.perf_counter() - w0) * 100.0


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)

    print("=" * 78)
    print("A 空 QApplication（只有事件循环）")
    print("=" * 78)
    print(f"   CPU {sample():6.1f}%")

    from ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    loop = QEventLoop()
    QTimer.singleShot(900, loop.quit)
    loop.exec()

    print()
    print("=" * 78)
    print("B 主窗口已显示（一切正常运转）")
    print("=" * 78)
    print(f"   CPU {sample():6.1f}%")

    print()
    print("=" * 78)
    print("C 停掉 4 个视图的 16ms 定时器")
    print("=" * 78)
    for name in ("real_view", "orrery_view", "nearby_view", "fiction_view"):
        v = getattr(win, name, None)
        t = getattr(v, "_timer", None)
        if t:
            t.stop()
    print(f"   CPU {sample():6.1f}%")

    print()
    print("=" * 78)
    print("D 再停掉主窗口的 _clock / _slow")
    print("=" * 78)
    for name in ("_clock", "_slow"):
        t = getattr(win, name, None)
        if t:
            t.stop()
    print(f"   CPU {sample():6.1f}%")

    print()
    print("=" * 78)
    print("E 再隐藏所有视图")
    print("=" * 78)
    for name in ("real_view", "orrery_view", "nearby_view", "fiction_view"):
        v = getattr(win, name, None)
        if v:
            v.setVisible(False)
    print(f"   CPU {sample():6.1f}%")

    print()
    print("=" * 78)
    print("F 把整个窗口隐藏")
    print("=" * 78)
    win.hide()
    print(f"   CPU {sample():6.1f}%")

    print()
    print("=" * 78)
    print("G 再重新显示窗口（但定时器都已停）")
    print("=" * 78)
    win.show()
    loop2 = QEventLoop()
    QTimer.singleShot(500, loop2.quit)
    loop2.exec()
    print(f"   CPU {sample():6.1f}%")

    print()
    print("=" * 78)
    print("H 逐个恢复视图定时器，看哪个一起就爆")
    print("=" * 78)
    for name in ("real_view", "orrery_view", "nearby_view", "fiction_view"):
        v = getattr(win, name, None)
        t = getattr(v, "_timer", None)
        if not t:
            continue
        v.setVisible(True)
        t.start()
        loop3 = QEventLoop()
        QTimer.singleShot(400, loop3.quit)
        loop3.exec()
        print(f"   恢复 {name:14s} 后 CPU {sample(1.5):6.1f}%")
        t.stop()

    win.close()
    loop4 = QEventLoop()
    QTimer.singleShot(200, loop4.quit)
    loop4.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
