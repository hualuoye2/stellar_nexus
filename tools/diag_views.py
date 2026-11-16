# -*- coding: utf-8 -*-
"""开发期工具：逐个视图单独测空闲 CPU，找出真正空转的那个。"""
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
    return (time.process_time() - c0) / max(1e-6, dt) * 100.0


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme
    apply_dark_theme(app)
    from ui.real_sky_view import RealSkyView
    from ui.orrery_view import OrreryView
    from ui.nearby_view import NearbyView
    from ui.fiction_view import FictionView
    from 核心 import world_generator as WG

    galaxy = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")

    cases = []
    cases.append(("RealSkyView", lambda: RealSkyView()))
    cases.append(("OrreryView", lambda: OrreryView()))
    cases.append(("NearbyView", lambda: NearbyView()))

    def make_fiction():
        v = FictionView()
        v.set_galaxy(galaxy)
        return v

    cases.append(("FictionView", make_fiction))

    print("=" * 76)
    print("逐个视图：单独显示、不改动、空闲 3 秒")
    print("=" * 76)
    results = {}
    for name, factory in cases:
        v = factory()
        v.resize(1400, 860)
        v.show()
        paints = {"n": 0}
        ticks = {"n": 0}
        o_paint = type(v).paintEvent
        o_tick = getattr(type(v), "_on_tick", None)

        def paint(self, e, _p=paints, _o=o_paint):
            _p["n"] += 1
            return _o(self, e)

        def tick(self, _t=ticks, _o=o_tick):
            if _o:
                _t["n"] += 1
                return _o(self)
            return None

        type(v).paintEvent = paint
        if o_tick:
            type(v)._on_tick = tick
        loop = QEventLoop()
        QTimer.singleShot(500, loop.quit)
        loop.exec()
        paints["n"] = 0
        ticks["n"] = 0
        cpu = idle(3.0)
        timer = getattr(v, "_timer", None)
        results[name] = cpu
        flag = "   ← 空转！" if cpu > 20 else ""
        print(f"   {name:14s} CPU {cpu:6.1f}%   paint={paints['n']:4d} "
              f"tick={ticks['n']:4d}  定时器={timer.interval() if timer else '无'}ms"
              f"{flag}")
        type(v).paintEvent = o_paint
        if o_tick:
            type(v)._on_tick = o_tick
        if timer:
            timer.stop()
        v.close()
        loop2 = QEventLoop()
        QTimer.singleShot(200, loop2.quit)
        loop2.exec()

    print()
    print("=" * 76)
    print("结论")
    print("=" * 76)
    worst = max(results.items(), key=lambda kv: kv[1])
    print(f"   最耗 CPU 的是 {worst[0]}（{worst[1]:.1f}%），总和 "
          f"{sum(results.values()):.0f}%")
    for name, cpu in results.items():
        if cpu > 20:
            print(f"   ▶ 需要修：{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
