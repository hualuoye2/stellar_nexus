# -*- coding: utf-8 -*-
"""自己把整程序玩一遍（2026-09 版），记录体验问题、耗时与异常。

流程：
  ① 启动 → 现实星图：时间、缩放、拖拽、搜索、图层、Bortle、点击天体
  ② 太阳系仪：缩放、切换内外行星
  ③ 太阳邻域：旋转、选中近星、着色
  ④ 外星视角：换观察点、拖拽、星等上限
  ⑤ 架空星图：生成 → 选星 → 航线规划 → 派船 → 推进时间 → 切换模式 → 存档
  ⑥ 历史小说工作台 / 观测计划表 / 天体总览 / 导出
  ⑦ 汇总：每一步耗时、报错、待改进点
用法：python tools/play_tour.py [--shots]
"""
import io
import os
import sys
import time
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "stellar_nexus")))

import datetime                                                    # noqa: E402
from PySide6.QtCore import QEventLoop, QPoint, QPointF, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QImage, QColor, QMouseEvent, QWheelEvent   # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

SHOTS = "--shots" in sys.argv
ISSUES = []
ERRORS = []
TIMES = []


def note(text):
    ISSUES.append(text)
    print("      ! " + text)


def log_error(where, exc):
    ERRORS.append((where, f"{type(exc).__name__}: {exc}"))
    print("      !! %s 抛出 %s: %s" % (where, type(exc).__name__, exc))


def main():
    app = QApplication(sys.argv[:1])
    from ui.main_window import MainWindow
    from 核心.utils import LOG

    sink = []
    try:
        LOG.subscribe(lambda e: sink.append((e.level, e.message)))
    except Exception:
        pass

    t0 = time.perf_counter()
    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    TIMES.append(("启动", (time.perf_counter() - t0) * 1000))
    print("启动 %.0f ms" % TIMES[-1][1])

    def pump(ms=80):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def step(name, fn):
        t = time.perf_counter()
        try:
            fn()
            dt = (time.perf_counter() - t) * 1000
            TIMES.append((name, dt))
            print("  [%s] %.0f ms" % (name, dt))
        except Exception as exc:
            log_error(name, exc)

    def shot(tag):
        if not SHOTS:
            return
        img = QImage(win.width(), win.height(), QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0))
        win.render(img, QPoint(0, 0))
        p = os.path.join(r"D:\deepseek\.videotmp", "tour_%s.png" % tag)
        img.save(p)
        print("      截图 %s" % p)

    def drag(w, x0, y0, x1, y1, button=Qt.LeftButton, steps=8):
        def mk(t, x, y, b):
            return QMouseEvent(t, QPointF(x, y), QPointF(x, y), b,
                               b if t != QMouseEvent.MouseMove else Qt.NoButton,
                               Qt.NoModifier)
        app.sendEvent(w, mk(QMouseEvent.MouseButtonPress, x0, y0, button))
        for i in range(1, steps + 1):
            pump(16)
            app.sendEvent(w, mk(QMouseEvent.MouseMove,
                                x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, button))
        app.sendEvent(w, mk(QMouseEvent.MouseButtonRelease, x1, y1, button))
        pump(60)

    def wheel(w, x, y, delta):
        ev = QWheelEvent(QPointF(x, y), QPointF(x, y), QPoint(0, 0), QPoint(0, delta),
                         Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        app.sendEvent(w, ev)
        pump(60)

    print()
    print("=" * 78)
    print("① 现实星图")
    print("=" * 78)
    rv = win.real_view
    print("  状态栏：%s" % win._last_status[:90])
    print("  中心 ALT %.1f AZ %.1f  FOV %.1f  极限星等 %.1f"
          % (rv.center_alt, rv.center_az, rv.fov, rv.mag_limit()))
    step("拖拽旋转", lambda: drag(rv, 700, 400, 900, 460))
    step("滚轮缩放", lambda: wheel(rv, 700, 400, 240))
    step("搜索「仙女座星系」", lambda: win.real_view.find_object("仙女座星系"))
    step("搜索「M42」", lambda: win.real_view.find_object("M42"))
    step("搜索「HIP 70890」", lambda: win.real_view.find_object("HIP 70890"))
    step("灰度：Bortle 1", lambda: (win.bortle_combo.setCurrentIndex(0), win._on_bortle_changed(), rv.set_bortle(1)))
    step("灰度：Bortle 9", lambda: rv.set_bortle(9))
    step("回 Bortle 5", lambda: rv.set_bortle(5))
    for key in ("milky_way", "constellation_lines", "messier", "planets", "sun_moon",
                "background_stars", "star_names", "horizon_grid", "below_horizon"):
        try:
            rv.set_layer(key, not rv.layers.get(key, False))
            pump(30)
            rv.set_layer(key, not rv.layers.get(key, False))
        except Exception as exc:
            log_error("图层 " + key, exc)
    print("  图层开关 9 项完成")
    step("时间 +3 小时", lambda: rv.set_time(rv.now() + datetime.timedelta(hours=3)))
    step("复位视角", lambda: rv.reset_view())
    print("  可见天体 %s" % rv.visibility_summary())
    shot("real")

    print()
    print("=" * 78)
    print("② 太阳系仪")
    print("=" * 78)
    step("切到太阳系仪", lambda: win.switch_mode("orrery"))
    ov = win.orrery_view
    step("滚轮放大", lambda: wheel(ov, 750, 450, 240))
    step("拖拽倾斜", lambda: drag(ov, 750, 450, 820, 400))
    shot("orrery")

    print()
    print("=" * 78)
    print("③ 太阳邻域")
    print("=" * 78)
    step("切到太阳邻域", lambda: win.switch_mode("nearby"))
    nv = win.nearby_view
    step("拖拽旋转", lambda: drag(nv, 750, 450, 880, 470))
    step("滚轮缩放", lambda: wheel(nv, 750, 450, -240))
    try:
        picked = nv._star_at(QPointF(nv.width() * 0.5, nv.height() * 0.5))
        if picked:
            step("选中最近的星", lambda: nv.select(picked, recenter=True))
            print("      选中的是：%s" % picked.display_name)
        else:
            note("太阳邻域：中心 12 px 内没有星（这正是新增「选中最近的星」按钮的理由）")
        step("选中最近的星（按钮）", lambda: win._select_nearest_nearby())
        sel = nv.selected_star()
        print("      按钮选中的是：%s" % (sel.display_name if sel else "（无）"))
        if sel is None:
            note("「选中最近的星」按钮在 48 px 内也没找到星")
    except Exception as exc:
        log_error("太阳邻域选中", exc)
    shot("nearby")

    print()
    print("=" * 78)
    print("④ 外星视角")
    print("=" * 78)
    try:
        from ui.inverse_sky_panel import InverseSkyDialog
        sel = None
        try:
            sel = nv.selected_star()
        except Exception:
            pass
        dlg = InverseSkyDialog(win, initial=sel)
        dlg.show()
        pump(120)
        iv = dlg.view if hasattr(dlg, "view") else None
        print("  对话框 %dx%d，view=%s" % (dlg.width(), dlg.height(), iv))
        if iv is not None:
            step("拖拽旋转", lambda: drag(iv, 600, 350, 760, 380))
            step("滚轮缩放", lambda: wheel(iv, 600, 350, 240))
        shot("inverse")
        dlg.close()
    except Exception as exc:
        log_error("外星视角", exc)

    print()
    print("=" * 78)
    print("⑤ 架空星图")
    print("=" * 78)
    step("切到架空星图", lambda: win.switch_mode("fiction"))
    fv = win.fiction_view
    step("生成宇宙（后台线程）", lambda: win._generate_world())
    t = time.perf_counter()
    while win._gen_worker is not None and win._gen_worker.isRunning() and time.time() - t < 120:
        pump(50)
    pump(200)
    TIMES.append(("宇宙生成", (time.perf_counter() - t) * 1000))
    print("  生成耗时 %.1f s　%s" % (TIMES[-1][1] / 1000.0, win.gen_progress.text()))
    if win.galaxy is None:
        note("宇宙生成失败，后续架空星图步骤跳过")
    else:
        g = win.galaxy
        print("  %s：星系 %d 航线 %d 文明 %d 势力 %d 飞船 %d"
              % (g.name, len(g.systems), len(g.routes), len(g.civilizations),
                 len(g.factions), len(g.ships)))
        step("拖拽旋转 3D", lambda: drag(fv, 700, 420, 900, 470))
        step("滚轮缩放", lambda: wheel(fv, 700, 420, 240))
        for mode in ("explore", "politics", "resource", "danger", "civilization"):
            step("星图模式 " + mode, lambda m=mode: fv.set_mode(m))
        # 选两个星系并规划航线
        sids = [s.sid for s in g.systems[:2]]
        step("选中星系", lambda: fv.pick_system(sids[0]))
        try:
            from 核心 import world_generator as WG
            res = WG.plan_route(g, sids[0], sids[1], weight="distance")
            print("      航线规划：%s" % (res.get("note") if isinstance(res, dict) else res))
            if isinstance(res, dict) and res.get("path"):
                step("派船", lambda: (win.route_start_combo.setCurrentIndex(win.route_start_combo.findData(sids[0])), win.route_goal_combo.setCurrentIndex(win.route_goal_combo.findData(sids[1])), win._dispatch_ship()))
                print("      飞船数 → %d" % len(g.ships))
        except Exception as exc:
            log_error("航线规划", exc)
        step("推进时间（10 年）", lambda: fv.advance_time(10.0))
        step("存档", lambda: win._save_slot(1))
        shot("fiction")

    print()
    print("=" * 78)
    print("⑥ 工具窗口")
    print("=" * 78)
    step("历史小说工作台", lambda: win.open_novel_panel())
    if win._novel_window is not None:
        print("      面板 %dx%d @ (%d,%d)"
              % (win._novel_window.width(), win._novel_window.height(),
                 win._novel_window.x(), win._novel_window.y()))
        shot("novel")
    # ★ 这些 open_* 都用 dlg.exec()（模态嵌套事件循环）——直接调用会把脚本永久挂住。
    #   绕法：先在事件循环里挂一个 250 ms 的单次定时器，它负责遍历所有顶层对话框逐个 close()，
    #   这样 exec() 能正常返回，我们又能量到对话框的尺寸与内容。
    def close_dialogs():
        for w in QApplication.topLevelWidgets():
            if w.isVisible() and w.isModal():
                try:
                    print("      模态对话框：%s  %dx%d" % (type(w).__name__, w.width(), w.height()))
                except Exception:
                    pass
                try:
                    w.close()
                except Exception:
                    pass

    for name, fn in (("观测计划表", "open_planner"), ("天体总览", "open_object_browser"),
                     ("天象日历", "open_sky_calendar"), ("地平障碍", "open_horizon_dialog"),
                     ("时间旅行", "open_time_travel"), ("历史推演", "open_history_sim"),
                     ("DeepSeek 设置", "open_deepseek_settings"),
                     ("导出对话框", "open_export_dialog")):
        f = getattr(win, fn, None)
        if not callable(f):
            note("主窗口没有 %s 方法（菜单名可能变了）" % fn)
            continue
        QTimer.singleShot(400, close_dialogs)
        step(name, f)

    print()
    print("=" * 78)
    print("⑦ 星图之声（把星图弹出来）")
    print("=" * 78)
    step("切回现实星图", lambda: win.switch_mode("real"))
    try:
        from 核心 import sonify as _SN
        sp = win.sonify_panel
        print("  初始为关闭：%s" % (not win._music.enabled))
        step("开启星图之声", lambda: sp.btn_toggle.click())
        pump(300)
        print("  enabled=%s　原因=%s"
              % (win._music.enabled, win._music.engine.reason or "就绪"))
        notes, master, drone, mode = win._music.engine.brain.snapshot()
        print("  乐谱 %d 条　音景 %s　音量 %.2f　银河层 %.2f"
              % (len(notes), mode, master, drone))
        if notes:
            print("  音高 %.0f~%.0f Hz　声像 %.2f~%.2f"
                  % (min(n.pitch for n in notes), max(n.pitch for n in notes),
                     min(n.pan for n in notes), max(n.pan for n in notes)))
        for i, (_k, name, _t) in enumerate(_SN.MUSIC_MODES):
            step("音景「%s」" % name, lambda i=i: sp.combo_mode.setCurrentIndex(i))
        step("音量降到 20%", lambda: sp.slider.setValue(20))
        step("关闭跟随视角", lambda: sp.chk_follow.setChecked(False))
        step("恢复跟随视角", lambda: sp.chk_follow.setChecked(True))
        step("关闭星图之声", lambda: sp.btn_toggle.click())
        pump(200)
        print("  关闭后 enabled=%s" % win._music.enabled)
    except Exception as exc:
        log_error("星图之声", exc)

    print()
    print("=" * 78)
    print("⑧ 汇总")
    print("=" * 78)
    print("耗时前 8：")
    for name, ms in sorted(TIMES, key=lambda x: -x[1])[:8]:
        print("    %-22s %8.0f ms" % (name, ms))
    errs = [e for e in ERRORS]
    print()
    if errs:
        print("★ 抛出异常 %d 处：" % len(errs))
        for where, msg in errs:
            print("    %-22s %s" % (where, msg))
    else:
        print("★ 无异常")
    bad = [(lv, m) for lv, m in sink if str(lv).upper() in ("ERROR", "WARN")]
    if bad:
        print("★ 日志里的 ERROR/WARN %d 条：" % len(bad))
        for lv, m in bad[:14]:
            print("    [%s] %s" % (lv, str(m)[:110]))
    else:
        print("★ 日志无 ERROR/WARN")
    if ISSUES:
        print("★ 体验待改进 %d 条：" % len(ISSUES))
        for x in ISSUES:
            print("    - " + x)
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
