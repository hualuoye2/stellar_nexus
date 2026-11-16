# -*- coding: utf-8 -*-
"""开发期工具：追出 FictionView 里反复置脏的调用者。"""
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


def sample(seconds: float = 2.0):
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
    loop = QEventLoop()
    QTimer.singleShot(600, loop.quit)
    loop.exec()

    stats = {"dirty": 0, "tick": 0, "paint": 0, "playing": 0, "ships_anim": 0}
    stacks: list = []

    o_def = type(v).mark_dirty
    o_tick = type(v)._on_tick
    o_paint = type(v).paintEvent
    o_advance = type(v).advance_time

    def dirty(self):
        stats["dirty"] += 1
        if len(stacks) < 4:
            frames = traceback.extract_stack()[:-1]
            stacks.append("mark_dirty <- " + " <- ".join(
                f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in frames[-6:]))
        return o_def(self)

    def tick(self):
        stats["tick"] += 1
        return o_tick(self)

    def paint(self, e):
        stats["paint"] += 1
        return o_paint(self, e)

    type(v).mark_dirty = dirty
    type(v)._on_tick = tick
    type(v).paintEvent = paint

    g = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")
    v.set_galaxy(g)
    loop2 = QEventLoop()
    QTimer.singleShot(700, loop2.quit)
    loop2.exec()

    print("=" * 76)
    print("① 载入宇宙后空闲 3 秒（无飞船、未推进时间）")
    print("=" * 76)
    for k in ("dirty", "tick", "paint"):
        stats[k] = 0
    stacks.clear()
    cpu = sample(3.0)
    print(f"   CPU {cpu:.0f}%")
    print(f"   _on_tick   {stats['tick']:4d} 次（{stats['tick'] / 3:.1f} 次/秒）")
    print(f"   mark_dirty {stats['dirty']:4d} 次（{stats['dirty'] / 3:.1f} 次/秒）")
    print(f"   paintEvent {stats['paint']:4d} 次（{stats['paint'] / 3:.1f} 次/秒）")
    print(f"   _needs_repaint={v._needs_repaint}  playing={v.playing}  "
          f"ships={len(v.galaxy.ships)}  show_ships={v.show_ships}")
    print()
    print("   mark_dirty 的调用来源：")
    for s in stacks:
        print("     " + s)

    print()
    print("=" * 76)
    print("② 把 ship 动画分支的参数打出来，确认它是否总为真")
    print("=" * 76)
    print(f"   v.galaxy is not None      = {v.galaxy is not None}")
    print(f"   v.galaxy.ships (真值)      = {bool(v.galaxy.ships)}  "
          f"（len={len(v.galaxy.ships)}）")
    print(f"   v.show_ships              = {v.show_ships}")
    print(f"   ⇒ elif 分支成立            = "
          f"{v.galaxy is not None and bool(v.galaxy.ships) and v.show_ships}")
    print()
    print("   实际走的是哪条分支？看 tick 内部统计：")
    ticks_playing = {"n": 0}
    o_tick2 = type(v)._on_tick

    type(v).mark_dirty = o_def
    type(v).paintEvent = o_paint
    type(v)._on_tick = o_tick
    v._timer.stop()
    v.close()
    loop3 = QEventLoop()
    QTimer.singleShot(200, loop3.quit)
    loop3.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
