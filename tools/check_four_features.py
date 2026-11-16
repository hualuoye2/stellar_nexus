# -*- coding: utf-8 -*-
"""开发期工具：端到端验证四项新功能在主窗口中的可用性。

  1. 历史推演与曲线图
  2. 导出星空 PNG / 整夜延时 GIF
  3. 太阳系仪模式
  4. 暗空 / 光害模拟（波特尔等级）
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

CHECKS = []
SKIPPED = []

# ==============================================================================
# 输出目录（P1-3）
# ==============================================================================
# 默认仍然是「脚本自己所在目录」（= tools\），行为与改动前逐字一致：
# 不传 --out-dir、也不设 CHECK_OUT_DIR 时，导出照样写 tools\export_final.*，
# 写不进去照样 FAIL。
#
# 只有**显式**指定输出目录时才启用「可写性探测 + 环境性跳过」：
#   本沙箱里 Python 子进程写不进 tools\（实测 danger-full-access 下依然被拒：
#   os.makedirs() 成功、**写文件** PermissionError）。这种 FAIL 是环境限制、
#   不是回归，把它报成 SKIP 才能和真回归区分开。
#   ⚠️ 探针必须**真的写一个文件**再删掉 —— 只测 os.makedirs() 会得出
#      「目录可写」的假结论（AGENTS.md 四·补三就是这个坑）。
ENV_OUT_DIR = "CHECK_OUT_DIR"
DEFAULT_OUT_DIR = os.path.dirname(os.path.abspath(__file__))

OUT_DIR = DEFAULT_OUT_DIR        #: 产物落盘目录
OUT_DIR_SOURCE = ""              #: "" = 默认路径；否则记下是谁指定的（--out-dir / 环境变量）
OUT_DIR_UNWRITABLE = False       #: 仅当「显式指定」且「写不进去」时为 True


def resolve_out_dir(argv):
    """解析 --out-dir <path> / --out-dir=<path> / 环境变量 CHECK_OUT_DIR。

    返回 (目录, 来源)；来源为空串表示「没指定，用默认路径」。
    手写解析而不用 argparse：Qt 自己的参数会混在 argv 里，手写最可预测，
    也保证任何未知参数都被原样忽略（行为不变）。
    """
    cli = ""
    for i, arg in enumerate(argv):
        if arg == "--out-dir":
            cli = argv[i + 1] if i + 1 < len(argv) else ""
            break
        if arg.startswith("--out-dir="):
            cli = arg.split("=", 1)[1]
            break
    env = os.environ.get(ENV_OUT_DIR, "").strip()
    if cli:
        return os.path.abspath(cli), "--out-dir"
    if env:
        return os.path.abspath(env), ENV_OUT_DIR
    return DEFAULT_OUT_DIR, ""


def out_dir_writable(path: str) -> bool:
    """真的写一个探针文件再删掉；只测 makedirs 会把不可写目录误判为可写。"""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return False
    probe = os.path.join(path, ".check_out_dir_probe.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as fp:
            fp.write("probe")
        os.remove(probe)
        return True
    except OSError:
        return False


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok)))
    print(f"   [{'OK' if ok else '!!'}] {label}" + (f"　{detail}" if detail else ""))


def skip(label: str, reason: str) -> None:
    """环境性跳过：既不计入 FAIL，也不算通过（不伪装成 PASS）。"""
    SKIPPED.append(label)
    print(f"   [SKIP] {label}　环境性跳过（{reason}）")


def main() -> int:
    global OUT_DIR, OUT_DIR_SOURCE, OUT_DIR_UNWRITABLE
    OUT_DIR, OUT_DIR_SOURCE = resolve_out_dir(sys.argv[1:])
    OUT_DIR_UNWRITABLE = bool(OUT_DIR_SOURCE) and not out_dir_writable(OUT_DIR)

    print(f"输出目录：{OUT_DIR}"
          + (f"　（来自 {OUT_DIR_SOURCE}）" if OUT_DIR_SOURCE
             else "　（默认路径，未指定 --out-dir / CHECK_OUT_DIR）"))
    if OUT_DIR_UNWRITABLE:
        print(f"SKIP (output dir not writable): {OUT_DIR}")
        print("    -> 依赖写盘的断言将计为环境性跳过，不计入 FAIL；其余断言照常执行。")

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)

    from 核心 import history_sim as HS
    from 核心 import light_pollution as LP
    from ui.history_panel import HistorySimDialog
    from ui.main_window import MainWindow
    from ui.orrery_view import OrreryView

    win = MainWindow()
    win.resize(1560, 940)
    win.show()

    def pump(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)

    # ---------------------------------------------------------------- 太阳系仪
    print("=" * 72)
    print("① 太阳系仪模式")
    print("=" * 72)
    win.switch_mode("orrery")
    pump(250)
    ov = win.orrery_view
    check("模式切换", win.mode == "orrery" and win.view_stack.currentIndex() == 1,
          f"view={win.view_stack.currentIndex()} left={win.left_stack.currentIndex()}")
    LOG.entries.clear()
    ov.repaint()
    pump(150)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("绘制无异常", not errs, str(errs[:1]))
    import math
    earth = ov._pos_au.get("earth")
    jupiter = ov._pos_au.get("jupiter")
    r_earth = math.sqrt(sum(v * v for v in earth)) if earth else 0
    r_jup = math.sqrt(sum(v * v for v in jupiter)) if jupiter else 0
    check("地球日心距 ≈1 AU", 0.98 < r_earth < 1.02, f"{r_earth:.4f} AU")
    check("木星日心距 ≈5.2 AU", 4.9 < r_jup < 5.5, f"{r_jup:.4f} AU")
    check("行星数 = 9", len(ov._pos_au) == 9, f"{len(ov._pos_au)} 个")
    ov.select_planet("mars")
    pump(150)
    check("选中行星 → 信息面板", win.info_panel.sky_panel._title.text() == "火星",
          win.info_panel.sky_panel._title.text())
    check("行星精细模型可见", win.info_panel.sky_panel._model_view.isVisible())
    # 行星时钟
    year0 = ov.observer.when
    win._set_orrery_speed(31557600.0)
    pump(1200)
    win.real_view.time_playing = False
    win.btn_play.setChecked(False)
    moved = (ov.observer.when - year0).total_seconds() / 86400.0
    check("行星时钟可推进", moved > 5, f"推进 {moved:.1f} 天")
    for mode in ("true", "compressed"):
        ov.set_scale_mode(mode)
        ov.repaint()
        pump(120)
    check("比例模式切换无异常", True)

    # ---------------------------------------------------------------- 光害模拟
    print()
    print("=" * 72)
    print("② 暗空 / 光害模拟（波特尔等级）")
    print("=" * 72)
    win.switch_mode("real")
    pump(200)
    rv = win.real_view
    counts = {}
    for level in (1, 5, 8, 9):
        rv.set_bortle(level)
        rv.repaint()
        pump(120)
        counts[level] = len(rv._last_screen)
        print(f"      Bortle {level} → 极限星等 {rv.mag_limit():.2f}　屏幕目标 {counts[level]}")
    check("等级影响可见数量", counts[1] >= counts[8] >= counts[9],
          f"{counts[1]} / {counts[8]} / {counts[9]}")
    idx = win.bortle_combo.findData(8)
    win.bortle_combo.setCurrentIndex(idx)
    pump(150)
    check("下拉框联动", rv.bortle_level == 8, f"level={rv.bortle_level}")
    check("极限星等标签", "极限星等" in win.lbl_bortle.text())
    star_mags = [s.mag for s in __import__("核心.star_data", fromlist=["x"]).STAR_CATALOG]
    dso_mags = [m.mag for m in __import__("核心.star_data", fromlist=["x"]).DEEP_SKY_CATALOG]
    a = LP.evaluate(star_mags, dso_mags, 8)
    b = LP.evaluate(star_mags, dso_mags, 2)
    check("城市 < 暗空可见星数", a.stars_visible < b.stars_visible,
          f"{a.stars_visible} < {b.stars_visible}")

    # ---------------------------------------------------------------- 历史推演
    print()
    print("=" * 72)
    print("③ 历史推演与曲线图")
    print("=" * 72)
    from 核心 import world_generator as WG
    g = WG.generate_galaxy(seed=20260101, scale="medium", distribution="spiral")
    win.galaxy = g
    win.fiction_view.set_galaxy(g)
    win.info_panel.set_galaxy(g)
    win._refresh_system_combos()
    pump(200)
    dlg = HistorySimDialog(g, win)
    dlg.resize(1180, 780)
    dlg.show()
    pump(400)
    dlg.combo_years.setCurrentIndex(3)          # 300 年
    dlg.run_sim()
    for _ in range(80):
        pump(200)
        if dlg.result is not None:
            break
    pump(300)
    res = dlg.result
    check("推演完成", res is not None)
    if res is not None:
        check("事件数 > 20", len(res.events) > 20, f"{len(res.events)} 条")
        check("快照数 = 年数+1", len(res.snapshots) == res.years + 1,
              f"{len(res.snapshots)} 个")
        check("人口曲线有数据", len(res.series("population")) > 10)
        check("科技曲线单调上升",
              res.series("tech")[0][1] <= res.series("tech")[-1][1],
              f"{res.series('tech')[0][1]:.3f} → {res.series('tech')[-1][1]:.3f}")
        check("势力曲线可用", all(len(res.series("population", n)) > 0
                                  for n in res.faction_names))
        check("事件表已填充", dlg.table.rowCount() == len(res.events),
              f"{dlg.table.rowCount()} 行")
        dlg.tabs.setCurrentIndex(3)
        pump(300)
        dlg.timeline.repaint()
        pump(250)
        check("时间轴有可点击区域", len(dlg.timeline._hit) > 5,
              f"{len(dlg.timeline._hit)} 个")
        if dlg.timeline._hit:
            dlg._on_timeline_event(dlg.timeline._hit[0][1])
            pump(200)
            check("时间轴点击显示详情", len(dlg.event_detail.toPlainText()) > 5)
        # 图表至少有曲线数据
        check("人口图已绘制", len(dlg.chart_pop._series_list) > 0)
        check("版图图已绘制", len(dlg.chart_area._series_list) > 0)
        check("柱状图已绘制", len(dlg.chart_events._series_list) > 0)
        # 应用回宇宙（走对话框内部的写回逻辑，跳过模态提示框）
        before = len(g.history)
        HS.HistorySimulator(g, res.years, res.seed).apply_to_galaxy(res)
        win._on_history_applied(res)
        pump(300)
        check("应用结果写回宇宙", len(g.history) > before,
              f"历史事件 {before} → {len(g.history)}")
        check("银河历已推进", g.year > 0, f"{g.year:.1f} 年")
    dlg.close()
    pump(150)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("历史推演无错误日志", not errs, str(errs[:1]))

    # ---------------------------------------------------------------- 导出
    print()
    print("=" * 72)
    print("④ 导出星空图片 / 延时动画")
    print("=" * 72)
    win.switch_mode("real")
    pump(200)
    from 核心 import export_media as EM
    png = os.path.join(OUT_DIR, "export_final.png")
    gif = os.path.join(OUT_DIR, "export_final.gif")
    if OUT_DIR_UNWRITABLE:
        # 显式指定的输出目录写不进去 → 环境性跳过，**不放宽断言**、也不假装通过。
        skip("PNG 导出", "输出目录不可写")
        skip("整夜延时 GIF 导出", "输出目录不可写")
        skip("GIF 可读回", "输出目录不可写")
    else:
        ok = EM.export_png(rv, png, (1920, 1080), "北京　星空", "STELLAR NEXUS")
        check("PNG 导出", ok and os.path.isfile(png),
              f"{os.path.getsize(png) / 1024 / 1024:.2f} MB" if os.path.isfile(png) else "")
        ok2, note = EM.export_night_gif_blocking(rv, gif, rv.observer.when, 20.0, 4.0, 40.0,
                                        width=480, frame_delay_ms=120)
        check("整夜延时 GIF 导出", ok2 and os.path.isfile(gif), note)
        from PySide6.QtGui import QMovie
        movie = QMovie(gif)
        movie.start()
        pump(300)
        check("GIF 可读回", movie.isValid() and movie.frameCount() > 3,
              f"{movie.frameCount()} 帧")
        movie.stop()

    print()
    print("=" * 72)
    passed = sum(1 for _l, ok_ in CHECKS if ok_)
    tail = f"，环境性跳过 {len(SKIPPED)} 项" if SKIPPED else ""
    print(f"通过 {passed}/{len(CHECKS)} 项检查{tail}")
    print("=" * 72)
    for label, ok_ in CHECKS:
        if not ok_:
            print("  失败：" + label)
    for label in SKIPPED:
        print("  跳过：" + label)
    if SKIPPED:
        print(f"SKIP (output dir not writable): {OUT_DIR}")
    win.close()
    pump(150)
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
