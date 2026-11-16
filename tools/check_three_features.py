# -*- coding: utf-8 -*-
"""开发期工具：端到端验证三项新功能（天象日历 / 星空导览 / 本地星空模拟器）。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                          # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer             # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

CHECKS = []
SKIPPED = []

# ==============================================================================
# 输出目录（P1-3）
# ==============================================================================
# 本脚本只写一个产物：地平剖面 JSON。默认**不给路径**，于是走
# save/load_horizon_profile 的原有默认 = app_dir()/horizon_profile.json
# （源码运行即 stellar_nexus\horizon_profile.json）—— 行为与改动前逐字一致：
# 不传 --out-dir、也不设 CHECK_OUT_DIR 时，写不进去照样 FAIL。
#
# 只有**显式**指定输出目录时才启用「可写性探测 + 环境性跳过」：
#   本沙箱里 Python 子进程写不进 stellar_nexus\（实测 danger-full-access 下依然
#   被拒：os.makedirs() 成功、**写文件** PermissionError）。这种 FAIL 是环境限制、
#   不是回归，报成 SKIP 才能和真回归区分开。
#   ⚠️ 探针必须**真的写一个文件**再删掉 —— 只测 os.makedirs() 会得出
#      「目录可写」的假结论（AGENTS.md 四·补三就是这个坑）。
ENV_OUT_DIR = "CHECK_OUT_DIR"
DEFAULT_OUT_DIR = ""             #: 空串 = 用 save/load_horizon_profile 自己的默认路径

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

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)

    from 核心 import astronomy as A
    from 核心 import sky_events as SE
    from ui.main_window import MainWindow
    from ui.sky_calendar import (HORIZON_FILE, HorizonDialog, SkyCalendarDialog,
                                 TimeTravelDialog, load_horizon_profile,
                                 save_horizon_profile)

    #: 地平剖面落盘路径。空串 = 走 save/load_horizon_profile 的原有默认
    #: （app_dir()/horizon_profile.json），即默认行为完全不变。
    #: ⚠️ 必须放在上面那句 import 之后：HORIZON_FILE 是在 main() 里 import 进来的，
    #: 在其之前读它会 UnboundLocalError（而且只在显式给了 --out-dir 时才触发）。
    horizon_path = os.path.join(OUT_DIR, HORIZON_FILE) if OUT_DIR else ""

    print(f"地平剖面目录：{horizon_path or '（程序默认 app_dir()/horizon_profile.json）'}"
          + (f"　（来自 {OUT_DIR_SOURCE}）" if OUT_DIR_SOURCE else ""))
    if OUT_DIR_UNWRITABLE:
        print(f"SKIP (output dir not writable): {OUT_DIR}")
        print("    -> 依赖写盘的断言将计为环境性跳过，不计入 FAIL；其余断言照常执行。")

    win = MainWindow()
    win.resize(1560, 940)
    win.show()

    def pump(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)

    print("=" * 72)
    print("① 功能一：天象日历 + 时间旅行")
    print("=" * 72)
    dlg = SkyCalendarDialog(win.real_view.observer, win)
    dlg.resize(980, 700)
    dlg.show()
    pump(500)
    check("日历生成事件", dlg.list.count() >= 4, f"{dlg.list.count()} 条")
    titles = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    has_moon = any("月" in t for t in titles)
    check("包含月相事件", has_moon)
    # 点击事件 → 主窗口跳时间
    got = {}
    dlg.time_requested.connect(lambda w, k: got.update({"w": w, "k": k}))
    dlg.list.setCurrentRow(0)
    dlg._on_event_activated(dlg.list.item(0))
    pump(150)
    check("事件可触发时间跳转", got.get("w") is not None, str(got.get("w")))
    check("月相条已绘制", len(dlg.phase_widget._series) > 10,
          f"{len(dlg.phase_widget._series)} 个点")
    check("今晚概览非空", len(dlg.tonight.toPlainText()) > 20)
    dlg.close()
    pump(100)

    tt = TimeTravelDialog(win.real_view.observer, win)
    tt.show()
    pump(200)
    ok_parse = 0
    for expr in ("下次满月", "火星冲日", "2026-12-14 02:00", "英仙座流星雨", "今晚 21:00", "+7d"):
        if SE.parse_time_expression(expr, win.real_view.observer) is not None:
            ok_parse += 1
    check("自然语言时间解析", ok_parse == 6, f"{ok_parse}/6")
    when = SE.parse_time_expression("下次满月", win.real_view.observer)
    win._jump_to_time(when, "moon")
    pump(250)
    check("跳转后星图时间已改",
          abs((win.real_view.observer.when - when).total_seconds()) < 120,
          win.real_view.observer.when.strftime("%Y-%m-%d %H:%M"))
    check("月球已定位", win.info_panel.sky_panel._title.text() == "月球",
          win.info_panel.sky_panel._title.text())
    tt.close()
    pump(100)

    print()
    print("=" * 72)
    print("② 功能二：星空导览")
    print("=" * 72)
    # 切回一个适合观测的时间
    win._jump_to_time(datetime.datetime.now().replace(hour=22, minute=0, second=0), None)
    pump(200)
    n = win.tour_panel.refresh(win.real_view.observer)
    pump(250)
    check("生成导览清单", n >= 5, f"{n} 个目标")
    names = [win.tour_panel.list.item(i).text() for i in range(win.tour_panel.list.count())]
    kinds = set()
    for t in win.tour_panel.targets():
        kinds.add(t.kind)
    check("目标类型多样", len(kinds) >= 2, str(sorted(kinds)))
    check("目标都在地平线上",
          all(t.alt > 0 for t in win.tour_panel.targets()),
          f"最低高度 {min((t.alt for t in win.tour_panel.targets()), default=0):.0f}°")
    check("按评分排序",
          all(win.tour_panel.targets()[i].score >= win.tour_panel.targets()[i + 1].score
              for i in range(len(win.tour_panel.targets()) - 1)))
    win.tour_panel.list.setCurrentRow(0)
    t0 = win.tour_panel.targets()[0]
    win.tour_panel._on_item_clicked(win.tour_panel.list.item(0))
    pump(250)
    check("点击导览可定位",
          win.info_panel.sky_panel._title.text() not in ("", "未选择天体"),
          win.info_panel.sky_panel._title.text())
    # 下一个
    first = win.tour_panel.current().name
    win.tour_panel._step(1)
    pump(200)
    check("「下一个」可切换", win.tour_panel.current().name != first,
          f"{first} → {win.tour_panel.current().name}")
    print("      导览清单：")
    for line in names[:8]:
        print("        " + line)

    print()
    print("=" * 72)
    print("③ 功能三：本地星空模拟器（地平障碍）")
    print("=" * 72)
    hd = HorizonDialog(win.real_view.observer, win)
    hd.resize(1020, 700)
    hd.show()
    pump(400)
    hd._use_preset("城市楼群（四周 25°）")
    pump(150)
    check("预设可套用", len(hd.profile.azimuths) == 4, f"{len(hd.profile.azimuths)} 点")
    chart = hd.chart
    hd._clear()                       # 先清空预设，否则自带采样点会参与合并
    pump(100)
    for az in (0, 45, 90, 135, 200, 300):
        chart._apply(chart._polar_to_screen(float(az), 20.0))
    pump(120)
    check("鼠标绘制障碍", len(hd.profile.azimuths) == 6,
          f"{len(hd.profile.azimuths)} 点 {[round(a) for a in hd.profile.azimuths]}")
    back = chart._screen_to_polar(chart._polar_to_screen(150.0, 35.0))
    check("极坐标换算自检", abs(back[0] - 150) < 1.5 and abs(back[1] - 35) < 1.5,
          f"({back[0]:.1f}°, {back[1]:.1f}°)")
    idx = hd.combo.findData(32349)
    hd.combo.setCurrentIndex(max(0, idx))
    hd._analyze()
    pump(300)
    check("可见性分析有结果", "小时" in hd.report.toPlainText())
    hours_text = hd.lbl_state.text()
    print("      天狼（有遮挡）：" + hours_text)
    check("月度条已填充", any(b._value > 0 for b in hd._bar_widgets))
    if OUT_DIR_UNWRITABLE:
        # 显式指定的输出目录写不进去 → 这两项是环境限制，报 SKIP 而不是 FAIL。
        # 下游断言改用**内存里那份 6 点剖面**（等价输入），保证它们照常真跑，
        # 而不是拿一个空剖面糊弄出「通过」。
        skip("保存地平剖面", "输出目录不可写")
        skip("重新载入一致", "输出目录不可写")
        prof = hd.profile
    else:
        check("保存地平剖面", save_horizon_profile(hd.profile, horizon_path))
        prof = load_horizon_profile(horizon_path)
        check("重新载入一致", len(prof.azimuths) == len(hd.profile.azimuths),
              f"载回 {len(prof.azimuths)} 点")
    hd.close()
    pump(120)

    # 应用回主窗口 → 星图应画出遮挡区且不报错
    win.horizon_profile = prof
    win.real_view.set_horizon_profile(prof)
    LOG.entries.clear()
    win.real_view.recalculate(force=True)
    win.real_view.repaint()
    pump(200)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("星图绘制遮挡区无异常", not errs, str(errs[:1]))
    # 无遮挡对照
    empty = SE.HorizonProfile()
    win.real_view.set_horizon_profile(empty)
    rep_open = SE.visibility_report(*_radec(32349), "天狼", win.real_view.observer, empty, 30)
    rep_blocked = SE.visibility_report(*_radec(32349), "天狼", win.real_view.observer, prof, 30)
    check("遮挡确实减少可观测时间", rep_blocked.clear_hours < rep_open.clear_hours,
          f"{rep_open.clear_hours:.0f}h → {rep_blocked.clear_hours:.0f}h")

    print()
    print("=" * 72)
    passed = sum(1 for _l, ok in CHECKS if ok)
    tail = f"，环境性跳过 {len(SKIPPED)} 项" if SKIPPED else ""
    print(f"通过 {passed}/{len(CHECKS)} 项检查{tail}")
    print("=" * 72)
    for label, ok in CHECKS:
        if not ok:
            print("  失败：" + label)
    for label in SKIPPED:
        print("  跳过：" + label)
    if SKIPPED:
        print(f"SKIP (output dir not writable): {OUT_DIR}")
    win.close()
    pump(120)
    return 0 if passed == len(CHECKS) else 1


def _radec(hip: int):
    from 核心 import star_data as SD
    st = SD.get_star(hip)
    return (st.ra, st.dec)


if __name__ == "__main__":
    raise SystemExit(main())
