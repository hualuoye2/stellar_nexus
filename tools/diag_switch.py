# -*- coding: utf-8 -*-
"""开发期工具：切换模式后，实际状态与 CPU 的对应关系。"""
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
    from ui.main_window import MainWindow
    from 核心 import world_generator as WG

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def settle(ms=500):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(800)

    # 统计每个视图的 paint 次数
    counters = {}
    originals = {}
    for name in ("real_view", "orrery_view", "nearby_view", "fiction_view"):
        v = getattr(win, name)
        counters[name] = {"paint": 0}
        o = type(v).paintEvent

        def make(v, name, o):
            def paint(self, e):
                counters[name]["paint"] += 1
                return o(self, e)
            return paint

        originals[name] = o
        type(v).paintEvent = make(v, name, o)

    def report(label: str, sec: float = 2.0) -> None:
        for c in counters.values():
            c["paint"] = 0
        cpu = sample(sec)
        detail = "  ".join(f"{k.replace('_view', '')}={v['paint']}" for k, v in counters.items())
        print(f"   {label:16s} CPU {cpu:6.1f}%   paint: {detail}")

    g = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")
    win.galaxy = g
    win.fiction_view.set_galaxy(g)
    win.info_panel.set_galaxy(g)
    win._refresh_system_combos()
    settle(600)

    print("=" * 84)
    print("切换模式后的 CPU 与各视图重绘次数")
    print("=" * 84)
    for mode in ("real", "fiction", "real", "nearby", "real", "orrery", "real"):
        win.switch_mode(mode)
        settle(600)
        # 只统计当前状态
        report(mode)

    print()
    print("=" * 84)
    print("太阳邻域模式下的详细状态")
    print("=" * 84)
    win.switch_mode("nearby")
    settle(600)
    nv = win.nearby_view
    print(f"   nearby.isVisible={nv.isVisible()}  _needs_repaint={nv._needs_repaint}  "
          f"timer={nv._timer.isActive()}")
    print(f"   覆盖层 animated={win._scan_nearby.is_animated()}  "
          f"timer_active={win._scan_nearby._timer.isActive()}  "
          f"visible={win._scan_nearby.isVisible()}")
    report("nearby 复测")

    for name, o in originals.items():
        type(getattr(win, name)).paintEvent = o

    win.close()
    settle(200)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
