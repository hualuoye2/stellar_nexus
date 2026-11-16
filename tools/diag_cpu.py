# -*- coding: utf-8 -*-
"""开发期工具：定位哪个视图在空闲时空转（CPU 打满）。

做法：
    1) 启动主窗口，让它跑起来，测 3 秒基线 CPU
    2) 逐个停掉各视图的定时器 / 禁用重绘，测每次的 CPU
    3) 找出真正的元凶
用 time.process_time() 测"本进程 CPU 时间"，比看墙钟更准。
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
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)
    from ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 120) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def busy(seconds: float = 3.0) -> float:
        """返回这段时间内本进程消耗的 CPU 秒数 / 墙钟秒数（= 平均占用率）。"""
        t0 = time.process_time()
        w0 = time.perf_counter()
        end = w0 + seconds
        while time.perf_counter() < end:
            pump(50)
        return (time.process_time() - t0) / max(1e-6, time.perf_counter() - w0) * 100.0

    pump(600)
    print("=" * 74)
    print("① 基线（全窗口运行）")
    print("=" * 74)
    base = busy(3.0)
    print(f"   CPU 占用率：{base:.0f}%")

    views = (("现实星图", win.real_view), ("太阳系仪", win.orrery_view),
             ("太阳邻域", win.nearby_view), ("架空星图", win.fiction_view))
    print()
    print("=" * 74)
    print("② 逐个隐藏视图（隐藏后 isVisible() 为假，定时器本应自动跳过重绘）")
    print("=" * 74)
    results = {}
    for name, view in views:
        view.setVisible(False)
        pump(200)
        rate = busy(2.0)
        results[name] = rate
        print(f"   隐藏 {name:8s} 后 CPU：{rate:6.0f}%")
        view.setVisible(True)
        pump(200)

    print()
    print("=" * 74)
    print("③ 逐个停止定时器")
    print("=" * 74)
    for name, view in views:
        timer = getattr(view, "_timer", None)
        if timer is None:
            print(f"   {name}: 无 _timer")
            continue
        was = timer.isActive()
        timer.stop()
        pump(200)
        rate = busy(2.0)
        print(f"   停掉 {name:8s} 定时器后 CPU：{rate:6.0f}%（原状态 active={was}）")
        if was:
            timer.start()
        pump(200)

    print()
    print("=" * 74)
    print("④ 检查各视图的脏标记与悬停状态")
    print("=" * 74)
    for name, view in views:
        dirty = getattr(view, "_needs_repaint", None)
        hover = getattr(view, "_hover_key", "n/a")
        print(f"   {name:8s} _needs_repaint={dirty}  _hover_key={hover}  "
              f"timer_active={getattr(getattr(view, '_timer', None), 'isActive', lambda: '?')()}")
    print(f"   主窗口 _slow.active={win._slow.isActive()}  _clock.active={win._clock.isActive()}")

    print()
    print("=" * 74)
    print("⑤ 依次停掉主窗口的两个定时器")
    print("=" * 74)
    win._slow.stop()
    pump(200)
    print(f"   停掉 _slow（1.5s）后 CPU：{busy(2.0):.0f}%")
    win._clock.stop()
    pump(200)
    print(f"   再停掉 _clock（1s）后 CPU：{busy(2.0):.0f}%")

    win.close()
    pump(200)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
