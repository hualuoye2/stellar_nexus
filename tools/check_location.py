# -*- coding: utf-8 -*-
"""开发期工具：验证观测位置的三项改造。

  1. 南北纬 / 东西经区分（数值 + 勾选框 → 带符号坐标）
  2. 城市检索（离线 69000 城市，中文名/英文名/国家）
  3. 默认观测点固定为北京（不被 IP 定位覆盖）
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QEventLoop, QTimer, Qt      # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

CHECKS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok)))
    print(f"   [{'OK' if ok else '!!'}] {label}" + (f"　{detail}" if detail else ""))


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)
    from 核心 import cities as CT
    from ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1560, 940)
    win.show()

    def pump(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(900)          # 等 _initial_locate 跑完

    print("=" * 76)
    print("③ 默认观测点应为北京（北纬/东经），不被 IP 定位覆盖")
    print("=" * 76)
    obs = win.real_view.observer
    check("默认纬度 = 北京", abs(obs.lat - 39.9042) < 0.01, f"{obs.lat:+.4f}")
    check("默认经度 = 北京", abs(obs.lon - 116.4074) < 0.01, f"{obs.lon:+.4f}")
    check("默认时区 = +8", abs(obs.utc_offset - 8.0) < 0.01, f"{obs.utc_offset:+.1f}")
    print(f"      界面显示：{win.coord_preview.text().replace(chr(10), ' | ')[:120]}")
    check("北纬勾选", win.chk_north.isChecked())
    check("东经勾选", win.chk_east.isChecked())

    print()
    print("=" * 76)
    print("① 南北纬 / 东西经 区分")
    print("=" * 76)
    cases = [
        (39.9042, True, 116.4074, True, "北京 → 北纬东经", (39.9042, 116.4074)),
        (33.8688, False, 151.2093, True, "悉尼 → 南纬东经", (-33.8688, 151.2093)),
        (40.7128, True, 74.006, False, "纽约 → 北纬西经", (40.7128, -74.006)),
        (34.6037, False, 58.3816, False, "布宜诺斯艾利斯 → 南纬西经", (-34.6037, -58.3816)),
    ]
    for lat_v, north, lon_v, east, label, expect in cases:
        win.lat_spin.setValue(lat_v)
        win.lon_spin.setValue(lon_v)
        win.chk_north.setChecked(north)
        win.chk_east.setChecked(east)
        pump(60)
        got = win._current_coord()
        ok = abs(got[0] - expect[0]) < 1e-6 and abs(got[1] - expect[1]) < 1e-6
        check(label, ok, f"得到 ({got[0]:+.4f}, {got[1]:+.4f})")
        win._apply_location()
        pump(60)
        if label.startswith("悉尼"):
            check("  应用后视图坐标同步", abs(win.real_view.observer.lat + 33.8688) < 0.01,
                  f"{win.real_view.observer.lat:+.4f} / {win.real_view.observer.lon:+.4f}")
            check("  状态栏含半球说明", "南半球" in win._status_label.text(),
                  win._status_label.text()[:60])

    print()
    print("=" * 76)
    print("② 城市检索")
    print("=" * 76)
    for q in ("北京", "Beijing", "纽约", "Reykjavik", "santiago", "拉萨",
              "Ushuaia", "开普敦", "乌鲁木齐", "longyearbyen"):
        win.city_search.setText(q)
        win._on_city_search()
        pump(120)
        n = win.city_results.count()
        first = win.city_results.item(0).text() if n else "—"
        ok = n > 0 and "未找到" not in first
        check(f"检索「{q}」", ok, f"{n} 个结果，首选：{first[:56]}")
        if ok and q in ("北京", "纽约", "Reykjavik", "santiago"):
            win._on_city_picked(win.city_results.item(0))
            pump(60)
            lat, lon = win._current_coord()
            r = CT.find_city(q)
            if r:
                match = abs(lat - r.lat) < 0.05 and abs(lon - r.lon) < 0.05
                check(f"  选中后坐标一致", match,
                      f"界面 ({lat:+.4f},{lon:+.4f}) vs 库 ({r.lat:+.4f},{r.lon:+.4f})，"
                      f"北纬={win.chk_north.isChecked()} 东经={win.chk_east.isChecked()}"
                      f" 时区={win.tz_spin.value():+.1f}")

    print()
    print("=" * 76)
    print("④ 恢复默认按钮")
    print("=" * 76)
    win.city_search.setText("sydney")
    win._on_city_search()
    pump(100)
    if win.city_results.count():
        win._on_city_picked(win.city_results.item(0))
        win._apply_location()
        pump(100)
    check("先切到悉尼", win.real_view.observer.lat < 0,
          f"{win.real_view.observer.lat:+.3f}")
    win._restore_default_location()
    pump(150)
    check("恢复默认为北京", abs(win.real_view.observer.lat - 39.9042) < 0.01,
          f"{win.real_view.observer.lat:+.4f}, {win.real_view.observer.lon:+.4f}")
    check("恢复后北纬东经勾选", win.chk_north.isChecked() and win.chk_east.isChecked())

    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("无错误日志", not errs, str(errs[:2]))

    print()
    passed = sum(1 for _l, ok in CHECKS if ok)
    print(f"通过 {passed}/{len(CHECKS)} 项检查")
    for label, ok in CHECKS:
        if not ok:
            print("  失败：" + label)
    win.close()
    pump(150)
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
