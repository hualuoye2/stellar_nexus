# -*- coding: utf-8 -*-
"""开发期工具：对照实验 —— 空 Qt 事件循环的 CPU 基线。

如果"什么都不做"的空 offscreen 应用也吃 100% CPU，说明测的是
Qt/沙箱环境的开销，而不是本程序的 bug。
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer           # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget     # noqa: E402


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
    print("=" * 70)
    print("对照 0：连窗口都没有，只跑事件循环")
    print("=" * 70)
    for i in range(2):
        cpu, dt = idle(2.0)
        print(f"   空闲 {dt:.1f} 秒 → CPU {cpu:6.1f}%")

    print()
    print("=" * 70)
    print("对照 1：一个空白 QWidget（显示）")
    print("=" * 70)
    w = QWidget()
    w.resize(1400, 860)
    w.show()
    for i in range(2):
        cpu, dt = idle(2.0)
        print(f"   空闲 {dt:.1f} 秒 → CPU {cpu:6.1f}%")
    w.close()

    print()
    print("=" * 70)
    print("对照 2：空白 QWidget + 一个 16ms 定时器（什么都不做）")
    print("=" * 70)
    w2 = QWidget()
    w2.resize(1400, 860)
    w2.show()
    tick = {"n": 0}
    t = QTimer()
    t.setInterval(16)
    t.timeout.connect(lambda: tick.__setitem__("n", tick["n"] + 1))
    t.start()
    tick["n"] = 0
    cpu, dt = idle(2.0)
    print(f"   空闲 {dt:.1f} 秒 → CPU {cpu:6.1f}%   定时器触发 {tick['n']} 次"
          f"（预期 ~{int(dt * 1000 / 16)}）")
    t.stop()
    w2.close()

    print()
    print("=" * 70)
    print("对照 3：一个自己重绘自己的 QWidget（模拟 paint 循环）")
    print("=" * 70)

    class Painter(QWidget):
        def __init__(self):
            super().__init__()
            self.n = 0
            self.timer = QTimer(self)
            self.timer.setInterval(16)
            self.timer.timeout.connect(self.tick)
            self.timer.start()

        def tick(self):
            self.update()

        def paintEvent(self, event):
            self.n += 1
            from PySide6.QtGui import QPainter, QColor
            from PySide6.QtCore import Qt
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(8, 18, 34))
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            for i in range(400):
                p.drawEllipse(100 + (i * 7) % 900, 100 + (i * 13) % 600, 3, 3)
            p.end()

    pw = Painter()
    pw.resize(1400, 860)
    pw.show()
    pw.n = 0
    cpu, dt = idle(2.0)
    print(f"   空闲 {dt:.1f} 秒 → CPU {cpu:6.1f}%   重绘 {pw.n} 次"
          f"（{pw.n / dt:.1f} 次/秒）")
    pw.timer.stop()
    pw.close()

    print()
    print("=" * 70)
    print("结论对照表")
    print("=" * 70)
    print("   若对照 0/1 就已是 100%+ → 是环境（offscreen 平台）的开销，非程序 bug")
    print("   若只有对照 3 很高 → 属于正常重绘成本，关键是别在空闲时重绘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
