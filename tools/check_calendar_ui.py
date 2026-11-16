# -*- coding: utf-8 -*-
"""开发期工具：验证天象日历 / 时间旅行 / 地平障碍三个对话框。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from datetime import datetime                        # noqa: E402

from PySide6.QtCore import QEventLoop, QPointF, QTimer   # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)

    from 核心 import astronomy as A
    from 核心 import sky_events as SE
    from ui.sky_calendar import (SkyCalendarDialog, TimeTravelDialog, HorizonDialog,
                                 save_horizon_profile, load_horizon_profile)

    def pump(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    obs = A.Observer(lat=39.9042, lon=116.4074,
                     when=datetime.now().replace(microsecond=0), utc_offset=8)

    print("=" * 72)
    print("① 天象日历对话框")
    print("=" * 72)
    LOG.entries.clear()
    dlg = SkyCalendarDialog(obs)
    dlg.resize(960, 680)
    dlg.show()
    pump(500)
    print(f"   事件数：{dlg.list.count()}")
    for i in range(min(6, dlg.list.count())):
        print("   ·", dlg.list.item(i).text())
    got = {}
    dlg.time_requested.connect(lambda w, k: got.update({"when": w, "key": k}))
    dlg.list.setCurrentRow(0)
    dlg._on_event_activated(dlg.list.item(0))
    pump(120)
    print(f"   点击首条 → 时间={got.get('when')} key={got.get('key')}")
    print(f"   详情 HTML 长度：{len(dlg.detail.toHtml())}")
    print(f"   今晚概览长度：{len(dlg.tonight.toPlainText())}")
    print(f"   月相条数据点：{len(dlg.phase_widget._series)}")
    print(f"   日历文本前 3 行：{dlg._calendar_text().splitlines()[:3]}")
    dlg.repaint()
    pump(100)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    print(f"   错误：{errs[:2]}")
    dlg.close()
    pump(80)

    print()
    print("=" * 72)
    print("② 时间旅行对话框")
    print("=" * 72)
    LOG.entries.clear()
    tt = TimeTravelDialog(obs)
    tt.show()
    pump(200)
    for expr in ("下次满月", "火星冲日", "2026-12-14 02:00", "英仙座流星雨", "今晚 21:00", "乱写"):
        tt.edit.setText(expr)
        pump(60)
        print(f"   {expr:16s} → {tt.result.text()[:52]}")
    tt.edit.setText("火星冲日")
    pump(80)
    captured = {}
    tt.time_requested.connect(lambda w, k: captured.update({"when": w, "key": k}))
    tt._accept()
    pump(80)
    print(f"   接受后：时间={captured.get('when')} 目标={captured.get('key')}")
    tt.close()
    pump(60)

    print()
    print("=" * 72)
    print("③ 地平障碍编辑器")
    print("=" * 72)
    LOG.entries.clear()
    hd = HorizonDialog(obs)
    hd.resize(1020, 700)
    hd.show()
    pump(300)
    for preset in ("城市楼群（四周 25°）", "阳台（单向 40°）"):
        hd._use_preset(preset)
        pump(120)
        print(f"   预设「{preset}」→ {len(hd.profile.azimuths)} 点，"
              f"状态：{hd.lbl_state.text()}")
    # 模拟在图上拖动绘制
    chart = hd.chart
    print(f"   极坐标图尺寸：{chart.width()}x{chart.height()}")
    c, r = chart._center_radius()
    for az in (0, 45, 90, 180, 270):
        pt = chart._polar_to_screen(az, 18.0)
        chart._apply(pt)
    pump(100)
    print(f"   拖动采样后：{len(hd.profile.azimuths)} 点")
    back = chart._screen_to_polar(chart._polar_to_screen(90.0, 18.0))
    print(f"   坐标换算自检：方位 90°/高度 18° → 反算 "
          f"({back[0]:.1f}°, {back[1]:.1f}°)")
    # 可见性分析
    idx = hd.combo.findData(32349)      # 天狼 HIP 32349
    hd.combo.setCurrentIndex(idx if idx >= 0 else 0)
    hd._analyze()
    pump(200)
    print(f"   分析结果：{hd.lbl_state.text()}")
    print(f"   报告 HTML 长度：{len(hd.report.toHtml())}")
    print(f"   月度条：{[f'{w._value:.2f}' for w in hd._bar_widgets[:6]]}")
    # 保存 / 载入
    ok = save_horizon_profile(hd.profile)
    loaded = load_horizon_profile()
    print(f"   保存={ok}　载回 {len(loaded.azimuths)} 点（方位 "
          f"{[round(a) for a in loaded.azimuths]}）")
    hd.repaint()
    pump(100)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    print(f"   错误：{errs[:3]}")
    hd.close()
    pump(80)

    print()
    print("全部对话框创建与交互完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
