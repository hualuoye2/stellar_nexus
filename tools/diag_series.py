# -*- coding: utf-8 -*-
"""开发期工具：连续多窗口采样 CPU，区分「一次性开销」与「持续空转」。

关键：每 2 秒一个窗口，连测 5 个窗口。如果只有第 1 个窗口高，
就是启动/切换时的一次性渲染；如果每个窗口都高，才是持续空转。
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


def window(seconds: float = 2.0):
    c0 = time.process_time()
    w0 = time.perf_counter()
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()
    dt = time.perf_counter() - w0
    return (time.process_time() - c0) / max(1e-6, dt) * 100.0


def series(count: int = 5, seconds: float = 2.0) -> list:
    return [round(window(seconds), 1) for _ in range(count)]


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

    def settle(ms: int = 400) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(900)
    print("=" * 80)
    print("每 2 秒一个窗口，连测 5 个窗口（数值是 CPU 占用率 %）")
    print("=" * 80)

    rows = []
    # 现实星图（无宇宙）
    win.switch_mode("real")
    settle()
    rows.append(("现实星图（无宇宙）", series()))

    # 现实星图 + 已生成宇宙（FictionView 持有数据但不可见）
    g = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")
    win.galaxy = g
    win.fiction_view.set_galaxy(g)
    win.info_panel.set_galaxy(g)
    win._refresh_system_combos()
    settle(600)
    rows.append(("现实星图（有宇宙数据）", series()))

    # 架空星图（可见）
    win.switch_mode("fiction")
    settle(600)
    rows.append(("架空星图（可见）", series()))

    # 切回现实星图
    win.switch_mode("real")
    settle(600)
    rows.append(("切回现实星图", series()))

    # 架空星图 + 推进时间
    win.switch_mode("fiction")
    settle()
    win.btn_fiction_play.setChecked(True)
    settle(400)
    rows.append(("架空星图+时间推进", series()))
    win.btn_fiction_play.setChecked(False)

    # 太阳邻域
    win.switch_mode("nearby")
    settle(600)
    rows.append(("太阳邻域", series()))

    # 太阳系仪 + 行星时钟
    win.switch_mode("orrery")
    settle(600)
    rows.append(("太阳系仪", series()))

    print(f"   {'场景':<22} {'窗口1':>7} {'窗口2':>7} {'窗口3':>7} {'窗口4':>7} {'窗口5':>7}   判定")
    for name, vals in rows:
        tail = vals[2:]
        sustained = sum(tail) / len(tail)
        verdict = "持续空转 ← 需修" if sustained > 20 else ("仅启动瞬态" if vals[0] > 20 else "正常")
        print(f"   {name:<22} " + " ".join(f"{v:>7.1f}" for v in vals) + f"   {verdict}")

    win.close()
    settle(200)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
