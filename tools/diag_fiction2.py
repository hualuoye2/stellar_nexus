# -*- coding: utf-8 -*-
"""开发期工具：架空星图（FictionView）持续高 CPU 的根因。

对照：① 有宇宙 ② 清空宇宙 ③ 无飞船 ④ 关闭各图层
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
    from ui.fiction_view import FictionView
    from 核心 import world_generator as WG

    v = FictionView()
    v.resize(1400, 860)
    v.show()

    def settle(ms: int = 400) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(700)
    print("=" * 76)
    print("① 空視图（无宇宙）")
    print("=" * 76)
    print(f"   CPU {sample():6.1f}%   _needs_repaint={v._needs_repaint}")

    g = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")
    v.set_galaxy(g)
    settle(700)
    print()
    print("=" * 76)
    print("② 载入宇宙（40 星系 / 87 航线 / 0 飞船）")
    print("=" * 76)
    print(f"   CPU {sample():6.1f}%   _needs_repaint={v._needs_repaint}  "
          f"playing={v.playing}  ships={len(g.ships)}")

    # 统计 paint 次数与单帧耗时
    paints = {"n": 0, "ms": 0.0}
    orig = type(v).paintEvent

    def paint(self, e):
        t0 = time.perf_counter()
        try:
            return orig(self, e)
        finally:
            paints["n"] += 1
            paints["ms"] += (time.perf_counter() - t0) * 1000.0

    type(v).paintEvent = paint
    paints["n"] = 0
    paints["ms"] = 0.0
    cpu = sample(3.0)
    print(f"   实测：CPU {cpu:.0f}%   paint {paints['n']} 次（{paints['n'] / 3:.1f} 次/秒）"
          f"   单帧 {paints['ms'] / max(1, paints['n']):.1f} ms")
    type(v).paintEvent = orig

    print()
    print("=" * 76)
    print("③ 逐个关闭图层，看谁最贵")
    print("=" * 76)
    for layer in ("routes", "labels", "grid", "ships", "halos"):
        v.set_layer(layer, False)
        settle(400)
        print(f"   关闭 {layer:8s} 后 CPU {sample(1.5):6.1f}%")
    print()
    print("   全部图层关闭后 CPU "
          f"{sample(1.5):6.1f}%（若仍高，问题在重绘循环本身而非绘制内容）")

    print()
    print("=" * 76)
    print("④ 直接手工重绘一次，测真实单帧成本（各图层全开）")
    print("=" * 76)
    for layer in ("routes", "labels", "grid", "ships", "halos"):
        v.set_layer(layer, True)
    settle(400)
    t0 = time.perf_counter()
    for _ in range(3):
        v.repaint()
    print(f"   全图层单帧 {((time.perf_counter() - t0) / 3 * 1000):.1f} ms")

    v._timer.stop()
    v.close()
    settle(200)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
