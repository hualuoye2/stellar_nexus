# -*- coding: utf-8 -*-
"""开发期工具：统计所有 QTimer 的触发次数，揪出空转源头。"""
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
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)

    # 在创建窗口之前，给 QTimer 打桩，记录每个定时器的触发次数
    counter: dict = {}
    orig_start = QTimer.start
    orig_setInterval = QTimer.setInterval
    meta: dict = {}

    def my_start(self, *a):
        meta.setdefault(id(self), {"interval": self.interval(), "n": 0,
                                   "single": self.isSingleShot()})
        return orig_start(self, *a)

    def my_setInterval(self, ms):
        meta.setdefault(id(self), {"interval": ms, "n": 0, "single": self.isSingleShot()})
        meta[id(self)]["interval"] = ms
        return orig_setInterval(self, ms)

    QTimer.start = my_start
    QTimer.setInterval = my_setInterval

    from ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 100) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(900)

    # 用 timeout 信号计数
    for obj_id, info in list(meta.items()):
        pass
    # 更可靠：遍历 QApplication 的所有 QTimer 子对象
    timers = [t for t in app.findChildren(QTimer)]
    counts = {id(t): 0 for t in timers}
    for t in timers:
        try:
            t.timeout.connect(lambda _id=id(t): counts.__setitem__(_id, counts.get(_id, 0) + 1))
        except Exception:
            pass

    print("=" * 78)
    print("① 空转 2 秒：各定时器触发次数")
    print("=" * 78)
    t0 = time.process_time()
    w0 = time.perf_counter()
    end = w0 + 2.0
    while time.perf_counter() < end:
        pump(50)
    cpu = (time.process_time() - t0) / (time.perf_counter() - w0) * 100.0
    print(f"   CPU {cpu:.0f}%   定时器对象数 {len(timers)}")
    rows = []
    for t in timers:
        n = counts.get(id(t), 0)
        if n > 0:
            rows.append((n, t.interval(), t.isActive(), t.objectName(),
                         type(t.parent()).__name__ if t.parent() else "无父"))
    rows.sort(reverse=True)
    print(f"   {'触发次数':>8}  {'间隔ms':>7}  {'活跃':>5}  父控件")
    for n, interval, active, name, parent in rows[:14]:
        print(f"   {n:>8}  {interval:>7}  {str(active):>5}  {parent}")

    print()
    print("=" * 78)
    print("② 把所有 interval<=16ms 的定时器停掉后再测")
    print("=" * 78)
    stopped = []
    for t in timers:
        if t.isActive() and 0 <= t.interval() <= 16:
            stopped.append((t.interval(), type(t.parent()).__name__ if t.parent() else "无父"))
            t.stop()
    pump(300)
    t0 = time.process_time()
    w0 = time.perf_counter()
    end = w0 + 2.0
    while time.perf_counter() < end:
        pump(50)
    cpu2 = (time.process_time() - t0) / (time.perf_counter() - w0) * 100.0
    print(f"   停掉的定时器：{stopped}")
    print(f"   停掉后 CPU {cpu2:.0f}%（原来 {cpu:.0f}%）")

    print()
    print("=" * 78)
    print("③ 逐个恢复，找出真正的元凶")
    print("=" * 78)
    for t in timers:
        if not t.isActive() and 0 <= t.interval() <= 16:
            t.start()
            pump(200)
            t0 = time.process_time()
            w0 = time.perf_counter()
            end = w0 + 1.5
            while time.perf_counter() < end:
                pump(50)
            c = (time.process_time() - t0) / (time.perf_counter() - w0) * 100.0
            if c > 40:
                print(f"   ▶ interval={t.interval()}ms 父={type(t.parent()).__name__ if t.parent() else '无父'}"
                      f" 恢复后 CPU {c:.0f}%  ← 元凶")
            t.stop()
    win.close()
    pump(150)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
