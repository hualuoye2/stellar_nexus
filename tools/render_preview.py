# -*- coding: utf-8 -*-
"""开发期工具：离屏渲染两套星图并保存 PNG，用于肉眼检查视觉效果。

用法：
    set QT_QPA_PLATFORM=offscreen
    python tools/render_preview.py
输出：
    tools/preview_real.png     现实星图（含星座线、银河、行星、搜索高亮）
    tools/preview_real_night.png 现实星图红光模式
    tools/preview_fiction.png  架空星图（含势力光晕、航线、规划航线、飞船）
"""
from __future__ import annotations

import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus"))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer                # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def pump(app, ms: int = 200) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def shot(widget, path: str) -> None:
    widget.repaint()
    QApplication.processEvents()
    pix = widget.grab()
    ok = pix.save(path, "PNG")
    print(f"  {'OK ' if ok else 'FAIL'} {path}  {pix.width()}x{pix.height()}")


def main() -> int:
    app = QApplication(sys.argv[:1])
    try:
        app.setStyle("Fusion")
        from 核心.utils import apply_dark_theme
        apply_dark_theme(app)
    except Exception:
        pass
    from ui.main_window import MainWindow
    from 核心 import world_generator as WG

    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    pump(app, 400)

    # ---------- 现实星图 ----------
    win.switch_mode("real")
    pump(app, 200)
    rv = win.real_view
    rv.set_observer(39.9042, 116.4074, "北京", 8.0)
    rv.set_time(rv.now().replace(hour=22, minute=0, second=0))
    rv.fov = 110.0
    rv.set_view_center(28.0, 180.0)
    rv.set_layer("messier", True)
    pump(app, 300)
    print("现实星图预览：")
    shot(rv, os.path.join(HERE, "preview_real.png"))
    rv.red_mode = True
    pump(app, 120)
    shot(rv, os.path.join(HERE, "preview_real_night.png"))
    rv.red_mode = False
    pump(app, 60)

    # 性能测量（连续重绘 40 帧）
    t0 = time.perf_counter()
    for _ in range(40):
        rv.repaint()
    dt = (time.perf_counter() - t0) / 40.0
    print(f"  现实星图平均帧耗时：{dt * 1000:.2f} ms（≈ {1.0 / max(dt, 1e-6):.0f} FPS）")

    # ---------- 架空星图 ----------
    win.switch_mode("fiction")
    pump(app, 150)
    g = WG.generate_galaxy(seed=20250115, scale="huge", distribution="spiral")
    win.galaxy = g
    win.fiction_view.set_galaxy(g)
    win.info_panel.set_galaxy(g)
    win._refresh_system_combos()
    pump(app, 300)
    fv = win.fiction_view

    # 规划一条航线并派船，让预览图包含这些元素
    a = g.systems[0].sid
    b = g.systems[len(g.systems) // 2].sid
    res = WG.plan_route(g, a, b, weight="safe")
    fv.set_route(a, b, res)
    WG.dispatch_ship(g, res["path"], name="探索者号")
    for _ in range(3):
        g.year += 2.0
        WG.advance_ships(g, 2.0)
    fv.year = g.year
    fv.pick_system(b)
    pump(app, 200)
    print("架空星图预览：")
    shot(fv, os.path.join(HERE, "preview_fiction.png"))

    t0 = time.perf_counter()
    for _ in range(30):
        fv.repaint()
    dt = (time.perf_counter() - t0) / 30.0
    print(f"  架空星图（220 星系 / {len(g.routes)} 航线）平均帧耗时："
          f"{dt * 1000:.2f} ms（≈ {1.0 / max(dt, 1e-6):.0f} FPS）")

    # 切换各模式各拍一张（只保留文明模式用于检查）
    win._on_map_mode("politics")
    pump(app, 150)
    shot(fv, os.path.join(HERE, "preview_fiction_politics.png"))

    win.close()
    pump(app, 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
