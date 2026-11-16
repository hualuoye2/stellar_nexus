# -*- coding: utf-8 -*-
"""
tools/check_charts_error.py —— 验证图表异常不再被静默吞掉
===============================================================================
背景：ui/charts.py 的 paintEvent 原先写成 `except Exception: pass`，
四个图表子类的任何绘制 bug 都会表现为"这张图是空的"，而"图是空的"
最容易被误判成"数据没算出来"。

本脚本断言三件事：
  1. 子类绘制抛错时，LOG 里必须出现 ERROR（而不是悄无声息）
  2. 画布上必须真的出现"图表绘制失败"的兜底红字（肉眼可辨）
  3. 四个真实子类（LineChart / AreaChart / BarChart / TimelineWidget）
     在空数据下绘制不得产生 ERROR

运行：python tools/check_charts_error.py
"""
from __future__ import annotations

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from PySide6.QtCore import QPoint                                    # noqa: E402
from PySide6.QtGui import QImage, QPainter                           # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'  OK  ' if ok else ' FAIL '}] {label}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def paint(widget) -> QImage:
    img = QImage(widget.width(), widget.height(), QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    widget.render(p, QPoint(0, 0))
    p.end()
    return img


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import LOG, apply_dark_theme
    apply_dark_theme(app)
    from ui.charts import AreaChart, BarChart, LineChart, TimelineWidget, _ChartBase

    print("=" * 78)
    print("① 子类绘制抛错时必须留下痕迹")
    print("=" * 78)

    class Boom(_ChartBase):
        def _paint_content(self, p):        # noqa: N802
            raise ValueError("故意抛错：验证异常不再被吞掉")

    LOG.entries.clear()
    w = Boom()
    w.resize(320, 180)
    img = paint(w)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("抛出异常时 LOG 里出现 ERROR", len(errs) >= 1,
          errs[0][:70] if errs else "没有任何 ERROR 日志")
    check("ERROR 文案指明是哪个图表类", any("Boom" in e for e in errs),
          errs[0][:70] if errs else "—")

    red = sum(1 for y in range(0, 180, 3) for x in range(0, 320, 3)
              if img.pixelColor(x, y).red() > 150 and img.pixelColor(x, y).green() < 160)
    check("画布上真的画出了兜底提示（红色像素采样点 > 0）", red > 3,
          f"{red} 个采样点")

    print()
    print("=" * 78)
    print("② 四个真实子类在空数据下必须安静地画完")
    print("=" * 78)
    LOG.entries.clear()
    for cls in (LineChart, AreaChart, BarChart, TimelineWidget):
        widget = cls()
        widget.resize(320, 180)
        paint(widget)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("空数据绘制不产生 ERROR", not errs, str(errs[:2]))

    print()
    print("=" * 78)
    print("③ 带合法数据时也必须正常")
    print("=" * 78)
    from ui.charts import Series
    LOG.entries.clear()
    chart = LineChart()
    chart.resize(320, 180)
    chart.set_series([Series(name="测试曲线",
                             points=[(float(i), float(i * i % 17)) for i in range(24)])],
                     title="测试曲线")
    img2 = paint(chart)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("带数据绘制不产生 ERROR", not errs, str(errs[:2]))

    # 画出来的折线必须真的落在画布上（否则"图是空的"依然可能被误判成无数据）
    ink = sum(1 for y in range(0, 180, 2) for x in range(0, 320, 2)
              if img2.pixelColor(x, y).lightness() > 40)
    check("折线确实被画出来了（亮像素采样点 > 20）", ink > 20, f"{ink} 个采样点")

    # 空数据对照：应当明显更暗
    LOG.entries.clear()
    empty = LineChart()
    empty.resize(320, 180)
    img3 = paint(empty)
    ink_empty = sum(1 for y in range(0, 180, 2) for x in range(0, 320, 2)
                    if img3.pixelColor(x, y).lightness() > 40)
    check("空数据画面的亮像素明显少于有数据画面", ink_empty < ink * 0.6,
          f"空 {ink_empty} vs 有数据 {ink}")

    print()
    print("=" * 78)
    print("④ 传错数据类型时必须报错而不是静默空图（回归保护）")
    print("=" * 78)
    LOG.entries.clear()
    bad = LineChart()
    bad.resize(320, 180)
    bad.set_series({"曲线": [(0.0, 1.0)]})       # 故意传 dict（类型不对）
    img4 = paint(bad)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    check("数据类型不对时留下 ERROR 日志", bool(errs), (errs[0][:70] if errs else "静默了"))
    red4 = sum(1 for y in range(0, 180, 3) for x in range(0, 320, 3)
               if img4.pixelColor(x, y).red() > 150 and img4.pixelColor(x, y).green() < 160)
    check("数据类型不对时画布上有可见提示", red4 > 3, f"{red4} 个红色采样点")

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
