# -*- coding: utf-8 -*-
"""开发期工具：剩余视图（太阳邻域 / 太阳系仪 / 现实星图）在空闲时的重绘频率。

要求：不推进时间、无交互时应当 0 次重绘。
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


def sample(sec: float = 2.0):
    c0 = time.process_time()
    w0 = time.perf_counter()
    loop = QEventLoop()
    QTimer.singleShot(int(sec * 1000), loop.quit)
    loop.exec()
    return (time.process_time() - c0) / max(1e-6, time.perf_counter() - w0) * 100.0


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)
    from ui.real_sky_view import RealSkyView
    from ui.orrery_view import OrreryView
    from ui.nearby_view import NearbyView

    print("=" * 80)
    print("单独显示各视图（含它们的控制层），空闲 3 秒")
    print("=" * 80)
    for name, factory in (("RealSkyView", RealSkyView),
                          ("OrreryView", OrreryView),
                          ("NearbyView", NearbyView)):
        v = factory()
        v.resize(1400, 860)
        v.show()
        paints = {"n": 0}
        o_paint = type(v).paintEvent

        def paint(self, e, _p=paints, _o=o_paint):
            _p["n"] += 1
            return _o(self, e)

        type(v).paintEvent = paint
        loop = QEventLoop()
        QTimer.singleShot(500, loop.quit)
        loop.exec()
        paints["n"] = 0
        cpu = sample(3.0)
        t = getattr(v, "_timer", None)
        flag = "  ← 空闲仍在重绘" if paints["n"] > 5 else ""
        print(f"   {name:12s} CPU {cpu:6.1f}%   paint {paints['n']:4d} 次"
              f"（{paints['n'] / 3:.1f} 次/秒）  定时器={t.interval() if t else '无'}ms{flag}")
        type(v).paintEvent = o_paint
        if t:
            t.stop()
        v.close()
        loop2 = QEventLoop()
        QTimer.singleShot(250, loop2.quit)
        loop2.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
