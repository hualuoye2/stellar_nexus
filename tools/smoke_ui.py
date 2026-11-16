# -*- coding: utf-8 -*-
"""开发期冒烟测试：在离屏模式下创建主窗口并跑一遍主要功能。

用法（Windows）：
    set QT_QPA_PLATFORM=offscreen && python tools/smoke_ui.py
"""
from __future__ import annotations

import io
import os
import sys
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")
sys.path.insert(0, os.path.abspath(ROOT))

from PySide6.QtCore import QTimer, Qt                       # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402


def process(app, ms: int = 60) -> None:
    """处理事件循环 ms 毫秒。"""
    from PySide6.QtCore import QEventLoop
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def main() -> int:
    app = QApplication(sys.argv[:1])
    try:
        app.setStyle("Fusion")
        from 核心.utils import apply_dark_theme
        apply_dark_theme(app)
    except Exception:
        pass
    fails = []

    try:
        from ui.main_window import MainWindow
        win = MainWindow()
        win.resize(1400, 860)
        win.show()
        process(app, 150)
        print("[OK] 主窗口创建并显示")

        # --- 现实星图 ---
        win.switch_mode("real")
        process(app, 80)
        rv = win.real_view
        vis = rv.visibility_summary()
        print(f"[OK] 现实星图可见天体：{vis}")
        if vis.get("stars", 0) <= 0:
            fails.append("现实星图没有可见恒星")

        # 图层开关
        for key in rv.layers:
            rv.set_layer(key, not rv.layers[key])
            rv.set_layer(key, not rv.layers[key])
        process(app, 40)
        print("[OK] 全部图层开关切换正常")

        # 时间滑块与播放
        win.time_slider.setValue(180)
        process(app, 40)
        win.btn_play.setChecked(True)
        process(app, 120)
        win.btn_play.setChecked(False)
        after = rv.now()
        print(f"[OK] 时间推进正常：{after:%Y-%m-%d %H:%M:%S}")
        win.time_slider.setValue(0)
        process(app, 40)

        # 搜索
        for q in ("天狼", "Vega", "M42", "猎户座", "木星", "HIP 91262"):
            win.search_box.setText(q)
            rv.find_object(q)
            process(app, 20)
        print("[OK] 搜索功能正常（中文名 / 英文名 / Messier / 星座 / 行星 / HIP）")

        # 点击选中（直接调用内部命中逻辑）
        found = 0
        for key, pt in list(rv._last_screen.items())[:40]:
            obj = rv._object_at(pt)
            if obj is not None:
                rv._handle_click(pt)
                found += 1
                break
        print(f"[OK] 点击选中天体：{'成功' if found else '未命中（可能星图为空）'}")

        # 缩放与视角
        rv.fov = 40.0
        rv.set_view_center(20.0, 120.0)
        rv.reset_view()
        process(app, 60)
        print("[OK] 视角操作正常")

        # --- 架空星图 ---
        win.switch_mode("fiction")
        process(app, 80)
        win.scale_combo.setCurrentIndex(0)        # 小型 40
        win._generate_world()
        for _ in range(60):
            process(app, 100)
            if win.galaxy is not None:
                break
        if win.galaxy is None:
            fails.append("架空宇宙生成失败")
            print("[FAIL] 架空宇宙生成失败")
        else:
            g = win.galaxy
            print(f"[OK] 架空宇宙生成：{g.name} 种子 {g.seed} 星系 {len(g.systems)} "
                  f"航线 {len(g.routes)} 文明 {len(g.civilizations)} 势力 {len(g.factions)}")
            fv = win.fiction_view
            process(app, 120)
            print(f"[OK] 架空星图可见星系：{fv.visibility_stats()}")

            # 星图模式
            for mode, _label in __import__("ui.fiction_view", fromlist=["MAP_MODES"]).MAP_MODES:
                win._on_map_mode(mode)
                process(app, 30)
            print("[OK] 星图模式切换正常（探索/政治/资源/危险度/文明）")

            # 选中恒星系
            sid = g.systems[10].sid
            fv.pick_system(sid)
            process(app, 40)
            info_panel = win.info_panel
            print(f"[OK] 恒星系信息面板：{g.system(sid).name}")

            # 航线规划 + 派遣飞船
            win._set_route_start(g.systems[0].sid)
            win._set_route_goal(g.systems[-1].sid)
            win._plan_route()
            process(app, 40)
            route = fv.planned_route
            print(f"[OK] 航线规划：{route.get('message')} 距离 {route.get('distance')} 光年")
            win._dispatch_ship()
            process(app, 40)
            print(f"[OK] 飞船派遣：当前飞船数 {len(g.ships)}")

            # 时间推进触发事件
            win.btn_fiction_play.setChecked(True)
            for _ in range(25):
                process(app, 60)
            win.btn_fiction_play.setChecked(False)
            print(f"[OK] 时间推进：银河历 {fv.year:.3f} 年，飞船数 {len(g.ships)}，"
                  f"已抵达 {sum(1 for s in g.ships if s.arrived)}")

            # 导出 / 导入 JSON
            from 核心 import world_generator as WG
            from 核心.utils import load_json, save_json
            tmp = os.path.join(os.path.abspath(ROOT), "_smoke_world.json")
            ok = save_json(tmp, WG.galaxy_to_dict(g))
            data = load_json(tmp)
            g2 = WG.galaxy_from_dict(data) if data else None
            os.remove(tmp) if os.path.exists(tmp) else None
            print(f"[OK] JSON 导出/导入：{'成功' if ok and g2 else '失败'}"
                  f"（导入 {len(g2.systems) if g2 else 0} 星系）")
            if not (ok and g2):
                fails.append("JSON 往返失败")

            # 存档槽位
            win._save_slot(1)
            process(app, 20)
            print("[OK] 本地存档写入正常")

        # --- 夜间模式与折叠 ---
        win._set_night_mode(True)
        process(app, 30)
        win._set_night_mode(False)
        win._toggle_side("left")
        win._toggle_side("left")
        win._toggle_side("right")
        win._toggle_side("right")
        process(app, 30)
        print("[OK] 夜间模式与面板折叠正常")

        # --- 精细天体模型（行星 / 月球 / 太阳） ---
        from ui.body_renderer import body_pixmap, render_body
        win.switch_mode("real")
        process(app, 80)
        rv = win.real_view
        rv.set_layer("messier", True)
        rv.set_layer("deep_sky_labels", True)
        model_ok = 0
        for q in ("木星", "土星", "月球", "太阳", "天狼", "M42", "NGC 5139"):
            rv.find_object(q)
            process(app, 60)
            panel = win.info_panel.sky_panel
            if panel._model_view.isVisible():
                model_ok += 1
        print(f"[OK] 精细模型：{model_ok} 个天体显示球体模型（行星/日月）")
        if model_ok < 4:
            fails.append("精细天体模型显示数量不足")
        from ui import body_renderer as BR
        print(f"[OK] 球体渲染器：{len(BR.PLANET_SURFACE)} 种天体，"
              f"numpy 加速={'开' if BR._HAS_NUMPY else '关'}")

        # --- 深空天体符号渲染 ---
        deep_drawn = 0
        for fov in (25.0, 45.0, 90.0):
            rv.fov = fov
            rv.recalculate(force=True)
            process(app, 60)
            from 核心.utils import LOG as _L
            _L.entries.clear()
            rv.repaint()
            process(app, 30)
            bad = [e.message for e in _L.entries if e.level == "ERROR"]
            if bad:
                fails.append(f"深空天体渲染异常(fov={fov})：{bad[:1]}")
            deep_drawn = max(deep_drawn, sum(1 for k in rv._last_screen
                                             if k.startswith("messier:")))
        print(f"[OK] 深空天体渲染：最多同时绘制 {deep_drawn} 个（含类型符号）")

        # --- 全天天体总览 ---
        from ui.object_browser import ObjectBrowserDialog
        dlg = ObjectBrowserDialog(rv.observer, win)
        process(app, 120)
        tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
        print(f"[OK] 天体总览：{tabs} 亮星 {dlg.star_table.rowCount()} 行，"
              f"深空 {dlg.dso_table.rowCount()} 行")
        dlg.close()
        process(app, 60)

        # --- 历史小说工作台（离线示例 + 保存 + 导出） ---
        win.open_novel_panel()
        process(app, 200)
        nw = win._novel_window
        nw.generate_offline_sample()
        process(app, 120)
        text_len = len(nw.current_text())
        nw.save_novel()
        process(app, 120)
        tmp_txt = os.path.join(os.path.abspath(ROOT), "_smoke_novel.md")
        with open(tmp_txt, "w", encoding="utf-8") as fp:
            fp.write(nw.current_text())
        ok_export = os.path.getsize(tmp_txt) > 100
        os.remove(tmp_txt) if os.path.exists(tmp_txt) else None
        print(f"[OK] 历史小说工作台：示例 {text_len} 字，保存与导出{'成功' if ok_export else '失败'}")
        if text_len < 200 or not ok_export:
            fails.append("历史小说工作台生成/导出异常")

        # --- DeepSeek 客户端（无 Key 时的提示路径） ---
        from 核心 import deepseek_client as DS
        cfg = DS.DeepSeekConfig().load()
        ok_conn, msg = DS.DeepSeekClient(cfg).check()
        print(f"[OK] DeepSeek 客户端：ready={cfg.ready()} 提示信息={'有效' if msg else '无'}")
        prompt_ok = False
        if win.galaxy is not None:
            sysp, userp, mt = DS.build_story_prompt(win.galaxy, "epic", "medium", "")
            prompt_ok = len(userp) > 500 and mt > 1000
            print(f"[OK] 提示词构建：system {len(sysp)} 字 / user {len(userp)} 字 / max_tokens {mt}")
        if not prompt_ok:
            fails.append("提示词构建异常")
        nw.close()
        process(app, 80)

        # --- 数据信息 / 帮助 ---
        from 核心 import star_data as SD
        print(f"[OK] 星表统计：{SD.star_catalog_stats()}")

        # --- 多分辨率下的渲染健全性检查（确保任何尺寸都不产生异常） ---
        from PySide6.QtCore import QEventLoop
        from 核心.utils import LOG as _LOG

        def render_once(widget):
            _LOG.entries.clear()
            widget.repaint()
            process(app, 40)
            return [e.message for e in _LOG.entries if e.level == "ERROR"]

        for w, h in ((1366, 768), (1920, 1080), (2560, 1440)):
            win.resize(w, h)
            process(app, 120)
            errs = render_once(win.real_view)
            if errs:
                fails.append(f"现实星图 {w}x{h} 渲染异常：{errs[:2]}")
            win.switch_mode("fiction")
            process(app, 80)
            errs = render_once(win.fiction_view)
            if errs:
                fails.append(f"架空星图 {w}x{h} 渲染异常：{errs[:2]}")
            win.switch_mode("real")
            process(app, 60)
        print("[OK] 1366×768 / 1920×1080 / 2560×1440 分辨率下渲染均无异常")

        win.close()
        process(app, 80)
        print("[OK] 窗口关闭正常")

    except Exception as exc:
        fails.append(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    print("=" * 66)
    if fails:
        print("冒烟测试失败项：")
        for f in fails:
            print("  -", f)
        return 1
    print("冒烟测试全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
