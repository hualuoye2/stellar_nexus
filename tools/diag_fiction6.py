# -*- coding: utf-8 -*-
"""开发期工具：抓 _needs_repaint 的每一次写入（True 与 False 都抓）。"""
import io
import os
import sys
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
    from ui.fiction_view import FictionView
    from 核心 import world_generator as WG

    writes: list = []
    real_setattr = FictionView.__setattr__

    def traced(self, name, value):
        if name == "_needs_repaint" and len(writes) < 40:
            frames = traceback.extract_stack()[:-1]
            writes.append((bool(value),
                           " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}"
                                       for f in frames[-6:])))
        return real_setattr(self, name, value)

    FictionView.__setattr__ = traced

    v = FictionView()
    v.resize(1400, 860)
    v.show()
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()

    g = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")
    writes.clear()
    v.set_galaxy(g)
    loop2 = QEventLoop()
    QTimer.singleShot(400, loop2.quit)
    loop2.exec()

    print("=" * 90)
    print("_needs_repaint 的写入序列（载入宇宙后的前若干个 tick）")
    print("=" * 90)
    for i, (val, stack) in enumerate(writes[:26], 1):
        print(f"   [{i:2d}] = {val!s:5s}  {stack}")

    FictionView.__setattr__ = real_setattr
    v._timer.stop()
    v.close()
    loop3 = QEventLoop()
    QTimer.singleShot(200, loop3.quit)
    loop3.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
