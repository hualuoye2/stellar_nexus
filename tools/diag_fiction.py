# -*- coding: utf-8 -*-
"""开发期工具：FictionView 隐藏时为何仍在重绘 —— 打桩追栈。"""
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

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms=150):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(600)
    # 生成一个小宇宙，让 FictionView 有内容
    from 核心 import world_generator as WG
    g = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")
    win.galaxy = g
    win.fiction_view.set_galaxy(g)
    win.info_panel.set_galaxy(g)
    pump(400)

    fv = win.fiction_view
    print("=" * 76)
    print("① 状态检查")
    print("=" * 76)
    print(f"   当前模式            : {win.mode}（视图栈 index={win.view_stack.currentIndex()}）")
    print(f"   fiction_view.isVisible()      : {fv.isVisible()}")
    print(f"   fiction_view.isVisibleTo(win) : {fv.isVisibleTo(win)}")
    print(f"   fiction_view._needs_repaint   : {fv._needs_repaint}")
    print(f"   fiction_view.playing          : {fv.playing}")
    print(f"   galaxy.ships 数量             : {len(g.ships)}")
    print(f"   fiction_view.show_ships       : {fv.show_ships}")

    # 追栈
    stacks: list = []
    orig_paint = type(fv).paintEvent
    orig_update = type(fv).update

    def paint(self, e):
        if len(stacks) < 5:
            frames = traceback.extract_stack()[:-1]
            stacks.append(" <- ".join(f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}"
                                      for f in frames[-7:]))
        return orig_paint(self, e)

    def update(self, *a, **kw):
        if len(stacks) < 5:
            frames = traceback.extract_stack()[:-1]
            stacks.append("[update] " + " <- ".join(
                f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in frames[-7:]))
        return orig_update(self, *a, **kw)

    type(fv).paintEvent = paint
    type(fv).update = update
    pump(600)
    # 让主窗口两个定时器也跑
    for _ in range(12):
        pump(200)

    print()
    print("=" * 76)
    print("② 隐藏状态下（现实星图模式）的 paint/update 调用栈")
    print("=" * 76)
    for s in stacks[:6]:
        print("   " + s)

    print()
    print("=" * 76)
    print("③ 实测：切到「架空星图」再切回来，比较 CPU")
    print("=" * 76)

    def idle(sec: float):
        c0 = time.process_time()
        w0 = time.perf_counter()
        loop = QEventLoop()
        QTimer.singleShot(int(sec * 1000), loop.quit)
        loop.exec()
        return (time.process_time() - c0) / max(1e-6, time.perf_counter() - w0) * 100.0

    win.switch_mode("real")
    pump(300)
    print(f"   切回现实星图后 CPU : {idle(2.0):6.1f}%   "
          f"fiction.isVisible={fv.isVisible()}  _needs_repaint={fv._needs_repaint}")
    win.switch_mode("fiction")
    pump(300)
    print(f"   切到架空星图后 CPU : {idle(2.0):6.1f}%")

    type(fv).paintEvent = orig_paint
    type(fv).update = orig_update
    win.close()
    pump(200)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
