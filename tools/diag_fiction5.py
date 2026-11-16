# -*- coding: utf-8 -*-
"""开发期工具：打印 _on_tick 每轮的关键变量，看清循环成因。"""
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
    from ui.fiction_view import FictionView
    from 核心 import world_generator as WG

    v = FictionView()
    v.resize(1400, 860)
    v.show()
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()

    g = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")
    v.set_galaxy(g)
    loop2 = QEventLoop()
    QTimer.singleShot(600, loop2.quit)
    loop2.exec()

    log: list = []
    orig = type(v)._on_tick

    def tick(self):
        before = self._needs_repaint
        playing = self.playing
        ships = bool(self.galaxy.ships) if self.galaxy else False
        show_ships = self.show_ships
        visible = self.isVisible()
        r = orig(self)
        if len(log) < 24:
            log.append((round(before if isinstance(before, bool) else bool(before)),
                        round(self._needs_repaint), playing, ships, show_ships, visible))
        return r

    type(v)._on_tick = tick
    loop3 = QEventLoop()
    QTimer.singleShot(600, loop3.quit)
    loop3.exec()

    print("=" * 88)
    print("每轮 _on_tick 的变量快照")
    print("   (进入时_needs_repaint, 退出时_needs_repaint, playing, ships, show_ships, visible)")
    print("=" * 88)
    for row in log:
        print("   " + str(row))

    print()
    print("   计数器说明：进入时=True 的比例 =", 
          sum(1 for r in log if r[0] == 1), "/", len(log))
    type(v)._on_tick = orig
    v._timer.stop()
    v.close()
    loop4 = QEventLoop()
    QTimer.singleShot(200, loop4.quit)
    loop4.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
