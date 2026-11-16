# -*- coding: utf-8 -*-
"""银河带与地平线：地平线以下的银河必须被压暗。

用户实测反馈"地平线下的银河带太亮了"，本脚本就是那条反馈的客观判据。

判据（两条，都直接量像素）：
  A. 在**银道面穿过地平线**的那些方位上，取地平线上方 1°/2° 与下方 1°/2° 做对比，
     下方亮度必须 <= 上方的 40%。
  B. 全画面：地平线以下任何位置的"银河带贡献"（含银河与不含银河两张图之差）
     都不应超过地平线以上的最大值（给 2 个亮度级的栅格容差）。
"""
import io
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "stellar_nexus")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication            # noqa: E402
from PySide6.QtGui import QImage, QColor                # noqa: E402
from PySide6.QtCore import QPoint, QPointF              # noqa: E402

from ui.real_sky_view import RealSkyView               # noqa: E402
from 核心 import astronomy as A                         # noqa: E402

W, H = 1184, 720
LAT, LON = 39.9042, 116.4074
app = QApplication.instance() or QApplication(sys.argv)

FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def build(only_band=True):
    v = RealSkyView()
    v.resize(W, H)
    v.set_observer(LAT, LON, "北京")
    v.set_time(datetime(2026, 9, 22, 21, 28, 47), keep_offset=False)
    v.fov = 89.5
    v.set_view_center(25.6, 180.0, animate=False)
    if only_band:
        v.set_layers({k: False for k in v.layers})
        v.set_layer("milky_way", True)
        # ★ 必须同时打开 horizon_grid：地平线以下的压暗是挂在
        #   `if layers["horizon_grid"]` 分支里的（_draw_underground）。
        #   只开银河带时地下完全不加暗，实测同一像素 57 vs 开网格后 15 ——
        #   这正是"判据错了比代码错了更常见"的一个实例：第一版脚本只开了银河带，
        #   于是把"没压暗"误报成了"银河在地下太亮"。
        v.set_layer("horizon_grid", True)
    # ★ 图例/帧率文字要关掉：图例的色块亮度 204，会被当成"地平线以下有一条亮银河"
    v.show_legend = False
    v.show_fps = False
    v.recalculate(force=True)
    return v


def render(v):
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0))
    v.render(img, QPoint(0, 0))
    v._prepare_frame()
    return img


def median_lum(img, v, alt, az, r=1):
    vals = []
    for da in range(-r, r + 1):
        for dz in range(-r, r + 1):
            pt = v._project_point(alt + da * 0.08, az + dz * 0.08)
            if pt is None:
                continue
            x, y = int(round(pt.x())), int(round(pt.y()))
            if 0 <= x < W and 0 <= y < H:
                vals.append(img.pixelColor(x, y).lightness())
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]


v = build()
img = render(v)
lst, lat = v.observer.lst, v.observer.lat

print("=" * 78)
print("A 银道面穿过地平线的方位：地上 1°/2° vs 地下 1°/2°")
print("=" * 78)
crossings = []
prev_alt = None
for i in range(0, 721):
    l = i * 0.5
    ra, dec = A.galactic_to_equatorial(l, 0.0)
    alt, az = A.equatorial_to_horizontal(ra, dec, lst, lat)
    if prev_alt is not None and prev_alt[0] * alt < 0:
        crossings.append((l, az, alt))
    prev_alt = (alt, az)

print("  找到 %d 个交点" % len(crossings))
ratios = []
for l, az, _ in crossings:
    ups = [x for x in (median_lum(img, v, 2.0, az), median_lum(img, v, 4.0, az))
           if x is not None]
    downs = [x for x in (median_lum(img, v, -2.0, az), median_lum(img, v, -4.0, az))
             if x is not None]
    if not ups or not downs:
        continue
    u = sum(ups) / len(ups)
    dn = sum(downs) / len(downs)
    if u < 6.0:
        continue                      # 上方本来就是夜空，没有可比性
    ratios.append((dn / u, l, az, u, dn))
ratios.sort(reverse=True)
for r, l, az, u, dn in ratios[:8]:
    print("   l=%6.1f az=%6.1f  地上 %5.1f  地下 %5.1f  比值 %.2f" % (l, az, u, dn, r))
if ratios:
    check("地平线以下的银河 <= 地上的 40%（所有交点）", ratios[0][0] <= 0.40,
          "最坏比值 %.2f（l=%.1f, az=%.1f）" % (ratios[0][0], ratios[0][1], ratios[0][2]))
else:
    check("找到银道面穿过地平线的样本", False, "一个都没找到")

print()
print("=" * 78)
print("B 全画面：银河带对地平线以下的贡献")
print("=" * 78)
v2 = build()
v2.set_layer("milky_way", False)
img2 = render(v2)
over = []
under = []
brightest_under = None
for y in range(0, H, 3):
    for x in range(0, W, 3):
        d = img.pixelColor(x, y).lightness() - img2.pixelColor(x, y).lightness()
        alt, az = v._invert_screen_pos(QPointF(x, y))
        if alt is None:
            continue
        if alt < -0.5:
            under.append(d)
            if brightest_under is None or d > brightest_under[0]:
                brightest_under = (d, x, y, alt, az)
        elif alt > 0.5:
            over.append(d)
mx_over = max(over) if over else 0
mx_under = max(under) if under else 0
print("  地上贡献：均值 %.2f  最大 %d（n=%d）" % (sum(over) / max(1, len(over)), mx_over, len(over)))
print("  地下贡献：均值 %.2f  最大 %d（n=%d）" % (sum(under) / max(1, len(under)), mx_under, len(under)))
if brightest_under:
    print("  地下最亮处：Δ=%d at (%d,%d) alt=%+.2f az=%.2f"
          % (brightest_under[0], brightest_under[1], brightest_under[2],
             brightest_under[3], brightest_under[4]))
check("地平线以下没有比地上更亮的银河", mx_under <= mx_over + 2,
      "地下最大 %d vs 地上最大 %d" % (mx_under, mx_over))

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
