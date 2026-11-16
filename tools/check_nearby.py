# -*- coding: utf-8 -*-
"""开发期工具：验证「太阳邻域」3D 视图的完整流程。"""
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
    from 核心 import nearby_stars as NS
    from 核心.utils import LOG

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(350)
    print("初始模式:", win.mode
          )
    win.switch_mode("nearby")
    pump(200)
    nv = win.nearby_view
    print("太阳邻域统计:", nv.stats())
    LOG.entries.clear()
    nv.repaint()
    pump(40)
    print("绘制错误:", [e.message for e in LOG.entries if e.level == "ERROR"][:3])
    print("屏幕可见星数:", len(nv._screen))

    ok = nv.select_by_key("Proxima Centauri")
    pump(120)
    panel = win.info_panel.nearby_panel
    print("选中比邻星:", ok, "|", panel._title.text())
    print("  距离行:", panel._rows["distance"]._value.text())
    print("  邻居行:", panel._rows["neighbors"]._value.text()[:80])
    print("  编目行:", panel._rows["catalog"]._value.text())

    for lim in (10, 20, 50):
        win._on_nearby_limit(lim)
        pump(60)
        print(f"  半径 {lim} ly -> 显示 {nv.stats()['shown']} 个天体")

    for kind in ("白矮星", "主序星", "巨星", "all"):
        nv.set_kind_filter(kind)
        pump(40)
        print(f"  类型 {kind} -> {nv.stats()['shown']} 个")

    for mode, label in nv.COLOR_MODES:
        win._on_nearby_color(mode)
        pump(40)
    print("着色模式切换完成:", [m for m, _ in nv.COLOR_MODES])

    # 渲染耗时
    import time
    t0 = time.perf_counter()
    for _ in range(10):
        nv.repaint()
    print(f"太阳邻域帧耗时：{(time.perf_counter() - t0) / 10 * 1000:.1f} ms")

    # 从近星跳到现实星图
    win._locate_nearby_in_sky("天狼")
    pump(180)
    print("模式:", win.mode, "| 星图信息面板:", win.info_panel.sky_panel._title.text())

    # 截图
    win.switch_mode("nearby")
    nv.reset_view()
    pump(220)
    nv.repaint()
    QApplication.processEvents()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview_nearby.png")
    win.grab().save(out, "PNG")
    print("已保存", out)

    win.close()
    pump(80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
