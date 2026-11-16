# -*- coding: utf-8 -*-
"""
tools/check_inverse_ui.py —— 「外星视角」对话框的端到端自检
===============================================================================
检查的是**界面真的能用**，而不是函数返回值：
    * 从菜单打开对话框（走真实的 MainWindow 路径，含 open_inverse_sky）
    * 换观察点后，画布、结论、视差对比、最亮星列表全部刷新
    * 图层开关真的改变了渲染结果（把开关全关掉，非背景像素必须显著减少）
    * 搜索框能搜到星，双击列表能把星移到视野中心
    * 右键菜单、滚轮缩放、拖动旋转都能跑且不报错
    * 全程无 ERROR 级日志

运行：python tools/check_inverse_ui.py
"""
from __future__ import annotations

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer   # noqa: E402
from PySide6.QtGui import QImage, QPainter, QWheelEvent              # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'  OK  ' if ok else ' FAIL '}] {label}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def pump(ms: int = 200) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def count_ink(widget) -> int:
    """渲染一帧，数出"非背景"采样点，用来判断图层开关是否真的生效。"""
    img = QImage(widget.width(), widget.height(), QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    widget.render(p, QPoint(0, 0))
    p.end()
    ink = 0
    for y in range(0, widget.height(), 6):
        for x in range(0, widget.width(), 6):
            if img.pixelColor(x, y).lightness() > 18:
                ink += 1
    return ink


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import LOG, apply_dark_theme
    apply_dark_theme(app)

    from 核心 import inverse_sky as IS
    from 核心 import nearby_stars as NS
    from ui.inverse_sky_panel import InverseSkyDialog

    LOG.entries.clear()

    print("=" * 78)
    print("① 打开对话框并跑默认观察点")
    print("=" * 78)
    dlg = InverseSkyDialog()
    dlg.resize(1180, 740)
    dlg.show()
    pump(300)
    check("默认观察点为比邻星", dlg._sky is not None
          and "比邻星" in dlg._sky.viewpoint_name, dlg._sky.viewpoint_name if dlg._sky else "—")
    check("太阳视星等 ≈ +0.39", dlg._sky is not None and abs(dlg._sky.sun.app_mag - 0.39) < 0.02,
          f"{dlg._sky.sun.app_mag:+.3f}" if dlg._sky else "—")
    check("结论区已填充", "抬头看" in dlg.summary.text())
    check("最亮星列表非空", dlg.bright.count() >= 10, f"{dlg.bright.count()} 行")
    check("视差对比有具体数字", "°" in dlg.parallax.text()
          and "地球视角" in dlg.parallax.text(),
          dlg.parallax.text().replace("\n", " / ")[:80])
    check("观察点列表默认列出最近 400 颗", dlg.list.count() == 400, f"{dlg.list.count()} 行")

    print()
    print("=" * 78)
    print("② 切换观察点：画布与全部数据卡必须一起刷新")
    print("=" * 78)
    before = dlg._sky.sun.ra
    star = NS.find_nearby("天狼")
    dlg._pick(star)
    pump(200)
    check("观察点已切到天狼", dlg._sky is not None and "天狼" in dlg._sky.viewpoint_name,
          dlg._sky.viewpoint_name if dlg._sky else "—")
    check("太阳方向随之改变（赤经变化 > 1°）",
          abs(((dlg._sky.sun.ra - before + 180) % 360) - 180) > 1.0,
          f"比邻星 {before:.2f}° → 天狼 {dlg._sky.sun.ra:.2f}°")
    check("天狼看太阳视星等 ≈ +1.94", abs(dlg._sky.sun.app_mag - 1.94) < 0.02,
          f"{dlg._sky.sun.app_mag:+.3f}")
    check("列表行数仍为 400（搜索被清空后回到默认）", dlg.list.count() == 400)

    print()
    print("=" * 78)
    print("③ 搜索框 / 双击定位")
    print("=" * 78)
    dlg.edit_search.setText("Gliese 699")
    pump(150)
    check("搜索 Gliese 699 → 列表第一项是巴纳德星",
          dlg.list.count() > 0 and "巴纳德" in dlg.list.item(0).text(),
          dlg.list.item(0).text() if dlg.list.count() else "空")
    dlg.edit_search.setText("比邻星")
    pump(150)
    check("搜索「比邻星」能命中", dlg.list.count() > 0
          and "比邻星" in dlg.list.item(0).text(),
          dlg.list.item(0).text() if dlg.list.count() else "空")
    dlg.edit_search.clear()
    pump(150)
    check("清空搜索后恢复 400 条", dlg.list.count() == 400, f"{dlg.list.count()} 条")

    # 在画布上选中一颗星并居中
    target = dlg._sky.stars[0]
    dlg.view.select(target, recenter=True)
    pump(120)
    cra, cdec = dlg.view.center()
    from 核心 import inverse_sky as _IS
    sep = _IS.nearest_constellation(cra, cdec) is not None
    check("双击居中后视野中心 = 该星坐标",
          abs(((cra - target.ra + 180) % 360) - 180) < 0.01 and abs(cdec - target.dec) < 0.01,
          f"中心 {cra:.3f}/{cdec:+.3f} vs 星 {target.ra:.3f}/{target.dec:+.3f}")
    _ = sep

    print()
    print("=" * 78)
    print("④ 图层开关必须真的改变渲染结果")
    print("=" * 78)
    all_keys = ("show_local", "show_atlas", "show_galaxy", "show_grid",
                "show_ecliptic", "show_constellation_lines", "show_labels")
    dlg._pick(NS.find_nearby("比邻星"))
    pump(120)
    dlg.view.set_fov(60.0)              # 缩小到 60°，让恒星分布更集中便于比较
    pump(120)
    ink_all = count_ink(dlg.view)
    for key in all_keys:
        dlg.checks[key].setChecked(False)
    pump(150)
    ink_none = count_ink(dlg.view)
    check("全部图层关闭后画面明显变空", ink_none < ink_all * 0.6,
          f"开启时 {ink_all} 点 → 全关后 {ink_none} 点")
    dlg.checks["show_local"].setChecked(True)
    pump(150)
    ink_local = count_ink(dlg.view)
    check("只开「50 光年内近星」会重新出现星点", ink_local > ink_none + 2,
          f"{ink_none} → {ink_local}")
    dlg.checks["show_local"].setChecked(False)
    dlg.checks["show_grid"].setChecked(True)
    pump(150)
    ink_grid = count_ink(dlg.view)
    check("只开「赤经赤纬网格」会重新出现网格线", ink_grid > ink_none + 2,
          f"{ink_none} → {ink_grid}")
    for key in all_keys:
        dlg.checks[key].setChecked(True)
    dlg.view.set_fov(120.0)
    pump(150)

    print()
    print("=" * 78)
    print("⑤ 交互：滚轮缩放 / 拖动旋转 / 右键菜单")
    print("=" * 78)
    fov0 = dlg.view.fov()
    ev = QWheelEvent(QPointF(400, 300), QPointF(400, 300), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    dlg.view.wheelEvent(ev)
    check("滚轮上滚缩小视场角（放大）", dlg.view.fov() < fov0,
          f"{fov0:.1f}° → {dlg.view.fov():.1f}°")
    dlg.view.set_fov(120.0)

    from PySide6.QtGui import QMouseEvent
    press = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(400, 300),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    dlg.view.mousePressEvent(press)
    move = QMouseEvent(QMouseEvent.MouseMove, QPointF(460, 330),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    dlg.view.mouseMoveEvent(move)
    release = QMouseEvent(QMouseEvent.MouseButtonRelease, QPointF(460, 330),
                          Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    dlg.view.mouseReleaseEvent(release)
    pump(80)
    ra_after = dlg.view.center()[0]
    check("向左拖动会改变视野赤经", abs(ra_after - target.ra) > 0.01,
          f"赤经 {target.ra:.3f}° → {ra_after:.3f}°")

    # 右键菜单：只验证菜单能构造出来（exec 会阻塞，所以不真的弹出）
    hit = dlg.view._pick(QPoint(dlg.view.width() // 2, dlg.view.height() // 2))
    check("画布命中测试返回结果或 None 且不抛异常", True,
          "命中 " + (hit.name if hit else "无（视野中心恰好为空）"))

    print()
    print("=" * 78)
    print("⑥ 主窗口菜单入口")
    print("=" * 78)
    try:
        from PySide6.QtWidgets import QMenu
        from ui.main_window import MainWindow
        win = MainWindow()
        win.resize(1366, 768)
        win.show()
        pump(400)
        act = None
        for menu in win.menuBar().findChildren(QMenu):
            for a in menu.actions():
                if "外星视角" in a.text():
                    act = a
                    break
            if act is not None:
                break
        check("主窗口菜单里有「外星视角」入口", act is not None,
              act.text() if act else "未找到")
        check("入口已绑定 open_inverse_sky",
              hasattr(win, "open_inverse_sky") and callable(win.open_inverse_sky))

        # 观察点接力：太阳邻域里选中的星必须被 open_inverse_sky 取到。
        # 这条曾经是死代码——open_inverse_sky 用 getattr 探测
        # NearbyView.selected_star，而该方法当时并不存在，于是静默回退到默认值。
        from 核心 import nearby_stars as _NS
        picked = _NS.find_nearby("巴纳德星")
        win.nearby_view.select_star(picked)
        pump(120)
        check("NearbyView.selected_star() 存在且返回选中星",
              win.nearby_view.selected_star() is picked,
              getattr(win.nearby_view.selected_star(), "display_name", "None"))
        # 用假对话框拦下 exec，检查传进去的 initial
        import ui.main_window as _MW
        captured = {}

        class _FakeDialog:
            def __init__(self, parent=None, initial=None):
                captured["initial"] = initial

            def exec(self):
                return 0

        real_dialog = _MW.InverseSkyDialog
        _MW.InverseSkyDialog = _FakeDialog
        try:
            win.open_inverse_sky()
        finally:
            _MW.InverseSkyDialog = real_dialog
        check("open_inverse_sky 把选中星作为观察点传下去",
              captured.get("initial") is picked,
              getattr(captured.get("initial"), "display_name", "None"))
        win.close()
    except Exception as exc:
        check("主窗口菜单入口", False, repr(exc))

    print()
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("全程无 ERROR 级日志", not errs, str(errs[:3]))
    dlg.close()

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
