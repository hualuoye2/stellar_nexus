# -*- coding: utf-8 -*-
"""开发期工具：验证跨视图搜索（现实星图 → 太阳邻域）。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    try:
        app.setStyle("Fusion")
        from 核心.utils import apply_dark_theme
        apply_dark_theme(app)
    except Exception:
        pass

    from ui.main_window import MainWindow
    from 核心.utils import LOG

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(350)
    for q in ("比邻星", "Gliese 551", "天狼", "Gliese 699", "HIP 87937"):
        r = win.real_view.find_object(q)
        pump(120)
        name = r.get("name") if isinstance(r, dict) else None
        sel = win.nearby_view._selected.display_name if win.nearby_view._selected else "-"
        print(f"  搜索 {q:14s} -> {str(name):18s} 模式={win.mode:8s} "
              f"信息页={win.info_panel._stack.currentIndex()} 近星选中={sel}")
    print("errors:", [e.message for e in LOG.entries if e.level == "ERROR"][:3])
    win.close()
    pump(80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
