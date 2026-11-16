# -*- coding: utf-8 -*-
"""开发期工具：谁在每轮把 _needs_repaint 重新置为 True。

用属性拦截精确记录每一次赋值及其调用栈。
"""
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
    from ui.fiction_view import FictionView
    from 核心 import world_generator as WG

    v = FictionView()
    v.resize(1400, 860)
    v.show()
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()

    writes: list = []
    # 拦截实例属性写入
    real_setattr = FictionView.__setattr__

    def traced_setattr(self, name, value):
        if name == "_needs_repaint" and value is True and len(writes) < 30:
            frames = traceback.extract_stack()[:-1]
            writes.append(" <- ".join(
                f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in frames[-6:]))
        return real_setattr(self, name, value)

    FictionView.__setattr__ = traced_setattr

    g = WG.generate_galaxy(seed=1234, scale="small", distribution="spiral")
    v.set_galaxy(g)
    loop2 = QEventLoop()
    QTimer.singleShot(700, loop2.quit)
    loop2.exec()

    writes.clear()
    print("=" * 76)
    print("载入宇宙后 1 秒内，所有「_needs_repaint = True」的写入来源")
    print("=" * 76)
    c0 = time.process_time()
    w0 = time.perf_counter()
    loop3 = QEventLoop()
    QTimer.singleShot(1000, loop3.quit)
    loop3.exec()
    cpu = (time.process_time() - c0) / max(1e-6, time.perf_counter() - w0) * 100.0
    print(f"   CPU {cpu:.0f}%   捕获写入 {len(writes)} 次（上限 30）")
    seen = {}
    for s in writes:
        seen[s] = seen.get(s, 0) + 1
    for s, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"   ×{n}  {s}")

    FictionView.__setattr__ = real_setattr
    v._timer.stop()
    v.close()
    loop4 = QEventLoop()
    QTimer.singleShot(200, loop4.quit)
    loop4.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
