# -*- coding: utf-8 -*-
"""开发期工具：验证重建后的 real_sky_view（含波特尔光害裁剪）。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                        # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer           # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

CHECKS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok)))
    print(f"   [{'OK' if ok else '!!'}] {label}" + (f"　{detail}" if detail else ""))


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)

    from ui.real_sky_view import RealSkyView

    v = RealSkyView()
    v.resize(1000, 700)
    v.show()

    def pump(ms: int = 250) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)
    # 不写死星数：星表会随数据扩充而变化（781 → 8883），写死数字的断言会在
    # 每次扩表时误报，反而掩盖真正的问题。改为与 star_data 的实测值比对。
    from 核心 import star_data as _SD
    check("静态数据载入", len(v._stars) == len(_SD.ALL_SKY_STARS),
          f"恒星 {len(v._stars)} 颗（star_data 实测 {len(_SD.ALL_SKY_STARS)}）")
    check("星表已扩充到数千颗", len(v._stars) >= 5000, f"{len(v._stars)} 颗")
    check("星座连线", len(v._lines_cache) == 149, f"{len(v._lines_cache)} 条")
    check("银河带分层", len(v._milky_bands) >= 5, f"{len(v._milky_bands)} 层")

    v.set_observer(39.9042, 116.4074, "北京", 8.0)
    v.set_time(datetime.datetime(2026, 9, 21, 22, 0, 0))
    pump(200)
    LOG.entries.clear()
    v.repaint()
    pump(150)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("绘制无异常", not errs, str(errs[:2]))
    vis = v.visibility_summary()
    print(f"      可见统计：{vis}")
    check("可见天体非空", sum(vis.values()) > 100, f"合计 {sum(vis.values())}")

    print("   波特尔光害裁剪：")
    counts = {}
    for b in (1, 5, 8, 9):
        v.set_bortle(b)
        v.repaint()
        pump(120)
        counts[b] = len(v._last_screen)
        print(f"      Bortle {b} → 极限星等 {v.mag_limit():.2f}　屏幕上目标 {counts[b]}")
    check("光害影响可见数量", counts[1] > counts[8] > counts[9],
          f"{counts[1]} > {counts[8]} > {counts[9]}")

    print("   搜索：")
    ok_search = 0
    for q in ("天狼", "Vega", "M42", "Gliese 551", "比邻星", "猎户座", "木星", "NGC 7000"):
        r = v.find_object(q)
        pump(60)
        name = (r or {}).get("name") if isinstance(r, dict) else None
        print(f"      {'✓' if r else '✗'} {q:12s} → {name}")
        if r:
            ok_search += 1
    check("搜索可用", ok_search >= 7, f"{ok_search}/8")

    # 地平障碍
    from 核心 import sky_events as SE
    prof = SE.build_horizon_preset("城市楼群（四周 25°）")
    v.set_horizon_profile(prof)
    LOG.entries.clear()
    v.repaint()
    pump(150)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("地平障碍绘制无异常", not errs, str(errs[:1]))

    # 图层切换
    for key in ("below_horizon", "nearby_mark", "deep_sky_labels", "messier"):
        v.set_layer(key, True)
    v.recalculate(force=True)
    LOG.entries.clear()
    v.repaint()
    pump(200)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("全图层绘制无异常", not errs, str(errs[:1]))
    print(f"      地平线下 {v._below_count} 个目标，近星高亮 {v._nearby_mark_count} 颗")

    # 悬停气泡 / 右键信息 / 投影逆运算
    key = next(iter(v._last_screen), None)
    obj = v._object_at(v._last_screen[key]) if key else None
    bubble = v._hover_text(obj) if obj else ""
    check("悬停气泡", ("右键" in bubble) and ("高度" in bubble or "地平线" in bubble),
          f"{len(bubble)} 字符")
    alt, az = v._invert_screen_pos(v._last_screen[key]) if key else (None, None)
    check("投影逆运算", alt is not None, f"({alt}, {az})")
    if obj:
        v._copy_object_info(obj)
        from PySide6.QtGui import QGuiApplication
        check("复制信息", len(QGuiApplication.clipboard().text()) > 40)

    v.grab().save(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "preview_recovered.png"), "PNG")
    print()
    passed = sum(1 for _l, ok in CHECKS if ok)
    print(f"通过 {passed}/{len(CHECKS)} 项检查")
    for label, ok in CHECKS:
        if not ok:
            print("  失败：" + label)
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
