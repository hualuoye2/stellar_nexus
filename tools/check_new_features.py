# -*- coding: utf-8 -*-
"""开发期工具：验证本轮三项改造（时间尺度 / 视图内交互 / 近星中文名）。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QPointF, QTimer      # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402


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
    from 核心 import star_data as SD
    from 核心 import world_generator as WG
    from 核心.utils import LOG

    win = MainWindow()
    win.resize(1560, 940)
    win.show()

    def pump(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)
    ok = []

    # ---------- 第 6 条：近星中文名 ----------
    named = 0
    missing = []
    for st in NS.NEARBY_CATALOG:
        if not st.hip:
            continue
        ref = SD.STAR_BY_HIP.get(st.hip)
        if ref is not None and ref.name:
            named += 1
        elif st.proper:
            missing.append(st.proper)
    print(f"【第6条·中文名】50 光年内有 HIP 的天体里，主星表能给出中文名的：{named} 个")
    print(f"           仍无中文名的著名近星（前 10）：{missing[:10]}")
    for hip in (70890, 87937, 71681, 24186):
        ref = SD.STAR_BY_HIP.get(hip)
        n = NS.NEARBY_BY_HIP.get(hip)
        print(f"           HIP {hip}: {ref.name if ref else '—'} / "
              f"{n.distance_ly if n else 0:.2f} ly")
    ok.append(named >= 15)

    # ---------- 第 4 条：悬停气泡 + 右键菜单 + 观测清单 ----------
    rv = win.real_view
    rv.repaint()
    pump(80)
    key = next(iter(rv._last_screen))
    obj = rv._object_at(rv._last_screen[key])
    bubble = rv._hover_text(obj) if obj else ""
    print(f"\n【第4条·悬停气泡】{obj.name if obj else '—'}：{len(bubble)} 字符，"
          f"含距离={'距离' in bubble}，含右键提示={'右键' in bubble}")
    ok.append("右键" in bubble and "距离" in bubble)

    win._add_to_watchlist(rv.describe_object(obj))
    pump(60)
    print(f"【第4条·观测清单】{win.watch_list.count()} 条：{win.watch_list.item(0).text()}")
    win._add_to_watchlist(rv.describe_object(obj))
    pump(40)
    print(f"           重复添加仍为 {win.watch_list.count()} 条（已去重）")
    ok.append(win.watch_list.count() == 1)

    win._on_layer_synced("star_names", False)
    pump(40)
    print(f"【第4条·右键图层同步】star_names → 复选框={win._layer_boxes['star_names'].isChecked()} "
          f"视图={rv.layers['star_names']}")
    ok.append(win._layer_boxes["star_names"].isChecked() is False
              and rv.layers["star_names"] is False)

    # ---------- 第 3 条：时间尺度 ----------
    g = WG.generate_galaxy(seed=7, scale="small", distribution="spiral")
    win.galaxy = g
    win.fiction_view.set_galaxy(g)
    win.info_panel.set_galaxy(g)
    win._refresh_system_combos()
    win.switch_mode("fiction")
    pump(150)
    speeds = [b.text() for b in win._fiction_speed_buttons]
    print(f"\n【第3条·时间尺度】档位：{speeds}")
    fv = win.fiction_view
    for btn in win._fiction_speed_buttons:
        if btn.text() == "100x":
            btn.click()
            break
    win.btn_fiction_play.setChecked(True)
    year0 = fv.year
    pump(1500)
    win.btn_fiction_play.setChecked(False)
    years = fv.year - year0
    print(f"           播放 1.5 秒（100x）推进了 {years:.2f} 银河历年 "
          f"→ 约 {years / 1.5 * 60:.1f} 年/分钟")
    ok.append(years > 0.02)
    print(f"           飞船：{len(g.ships)} 艘")

    # 右键设置航线端点 → 自动规划
    a, b = g.systems[0].sid, g.systems[-1].sid
    win._on_route_endpoint(a, "start")
    pump(60)
    win._on_route_endpoint(b, "goal")
    pump(250)
    planned = bool(fv.planned_route and fv.planned_route.get("ok"))
    print(f"【第3条·右键航线端点】自动规划={'成功' if planned else '失败'}，"
          f"起点={win.route_start_combo.currentText()} 终点={win.route_goal_combo.currentText()}")
    ok.append(planned)

    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    print(f"\n错误日志：{len(errs)} 条 {errs[:2]}")
    ok.append(not errs)

    print()
    print("=" * 60)
    print(f"通过 {sum(1 for x in ok if x)}/{len(ok)} 项检查")
    print("=" * 60)
    win.close()
    pump(100)
    return 0 if all(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
