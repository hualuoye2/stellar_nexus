# -*- coding: utf-8 -*-
"""极窄视场（高倍放大）下，银河带内是否出现"太深的凹槽 / 接缝 / 纵向条纹"。

背景：用户截图里银河带中间有一块被红圈圈出的"缺失"区域，带缘很清楚。
根因是暗带在 l≈96 处被一条硬判据（强度 < 0.12 不画）**一刀切断**，
而那个交界正好落在银河最亮的一段里。修法是改成淡出（MILKY_RIFT_FADE_LO/HI）。
本脚本把放大倍率从 1x 扫到 4x，直接量：
  * 带内最大相邻跳变（0.1° 步长、5x5 中位数）
  * 中心凹槽深度（带心 ±3° 最小值 vs 两侧 ±4~10° 最大值）
  * 带内起伏（±10° 内极差）
并断言"沿银经扫过暗带淡出段（l=70~120）时，剖面不出现台阶"。
"""
import io
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
from PySide6.QtCore import QPoint
from ui.real_sky_view import RealSkyView
from 核心 import astronomy as A

app = QApplication(sys.argv)
W, H = 1348, 800
FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def make(fov, l0, b0):
    """把视角对准银道坐标 (l0, b0)。"""
    v = RealSkyView()
    v.resize(W, H)
    v.set_observer(39.9042, 116.4074, "北京")
    v.set_time(datetime(2026, 9, 22, 22, 38, 50), keep_offset=False)
    v.fov = fov
    ra, dec = A.galactic_to_equatorial(l0, b0)
    lst = A.local_sidereal_time(v.observer.jd_ut, 116.4074)
    alt, az = A.equatorial_to_horizontal(ra, dec, lst, v.observer.lat)
    v.set_view_center(alt, az, animate=False)
    v.set_layers({k: False for k in v.layers})
    v.set_layer("milky_way", True)
    v.show_legend = False
    v.show_fps = False
    # ★ 关掉会污染剖面的叠加物：背景暗星、星名、星座线/名（它们会给剖面塞进
    #   10 个亮度级的尖峰）。上一版没关，l=330 b=0 处的尖峰其实是**银心方向**
    #   文字标记，被误判成"银河带里有台阶"——又是一次"判据错了比代码错了更常见"。
    for key in ("background_stars", "star_names", "constellation_lines",
                "constellation_names", "nearby_mark", "messier", "planets",
                "sun_moon", "below_horizon"):
        v.set_layer(key, False)
    v.recalculate(force=True)
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0))
    v.render(img, QPoint(0, 0))
    v._prepare_frame()
    return v, img


def profile(v, img, l0, lo=-16.0, hi=16.0, step=0.1):
    """沿银纬取亮度（5x5 中位数），**排除屏幕中心 ±14 像素**。

    为什么排除：HUD 在画布正中画了一个小十字准星，它是"必然存在"的 UI 元素，
    不是银河带的一部分，但 5x5 中位数压不住它——实测它会在剖面上塞进一个
    9~14 个亮度级的尖峰（对准 b=0 时它正好在剖面中央，对准 b=+8 时又落在
    b=+8 那一档）。"判据把 UI 当成被测对象"这种坑已经踩过两次，写进注释。
    """
    lst, lat = v.observer.lst, v.observer.lat
    cx, cy = W * 0.5, H * 0.5
    out = []
    n = int(round((hi - lo) / step)) + 1
    for i in range(n):
        b = lo + i * step
        ra, dec = A.galactic_to_equatorial(l0, b)
        alt, az = A.equatorial_to_horizontal(ra, dec, lst, lat)
        pt = v._project_point(alt, az)
        if pt is None:
            continue
        x, y = int(round(pt.x())), int(round(pt.y()))
        if not (0 <= x < W and 0 <= y < H):
            continue
        if abs(x - cx) <= 14 and abs(y - cy) <= 14:
            continue                      # 屏幕中心十字准星
        vals = []
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                xx, yy = x + dx, y + dy
                if 0 <= xx < W and 0 <= yy < H:
                    vals.append(img.pixelColor(xx, yy).lightness())
        vals.sort()
        out.append((b, vals[len(vals) // 2]))
    return out


print("=" * 78)
print("A 不同放大倍率下的带内质量（对准 l=330，船底座亮段）")
print("=" * 78)
print("  说明：只统计 FOV <= 50° 的档位。FOV=100/66.7 时整条带子在画布上不足")
print("        20 像素高，而剖面必须排除屏幕中心 ±14 像素的十字准星，")
print("        能用的采样点太少（实测这种档位下的跳变高达 28，全部来自排除区两侧），")
print("        属于**量不了**而不是画错了——判据在测不了的地方必须弃权。")
print("  %-8s %-6s %-12s %-12s %-10s" % ("FOV", "峰值", "最大相邻跳变", "中心凹槽深度", "带内起伏"))
worst_jump = 0.0
worst_dip = 0
measured = 0
for zoom in (1.0, 1.5, 2.0, 2.5, 3.0, 3.59, 4.0):
    fov = max(12.0, 100.0 / zoom)
    if fov > 50.0:
        continue
    v, img = make(fov, 330.0, 0.0)
    p = profile(v, img, 330.0)
    if len(p) < 40:
        print("  %-8.1f 可见采样点只有 %d 个，跳过" % (fov, len(p)))
        continue
    measured += 1
    peak = max(x[1] for x in p)
    maxj = max(abs(p[i][1] - p[i - 1][1]) for i in range(1, len(p)))
    inner = [x[1] for x in p if abs(x[0]) <= 3.0]
    outer = [x[1] for x in p if 4.0 <= abs(x[0]) <= 10.0]
    dip = (max(outer) - min(inner)) if inner and outer else 0
    seg = [x[1] for x in p if abs(x[0]) <= 10.0]
    ripple = (max(seg) - min(seg)) if seg else 0
    print("  %-8.1f %-6d %-12.0f %-12d %-10d" % (fov, peak, maxj, dip, ripple))
    worst_jump = max(worst_jump, maxj)
    worst_dip = max(worst_dip, dip)

check("至少量到 4 个放大档位", measured >= 4, "实测 %d 档" % measured)
check("最大相邻跳变 <= 8 个亮度级（各倍率）", worst_jump <= 8.0, "实测最大 %.0f" % worst_jump)
check("中心凹槽 <= 34 个亮度级（各倍率）", worst_dip <= 34, "实测最大 %d" % worst_dip)

print()
print("=" * 78)
print("B 沿银经扫过暗带淡出段（l=70~120）：剖面不出现接缝")
print("=" * 78)
# 暗带原来在 l≈96 被硬判据一刀切断，交界处带宽突变即为"接缝"。
# 判据：相邻 5° 的剖面极差不应突变（半宽变化平滑）。
v, img = make(27.9, 95.0, 0.0)
last_w = None
worst_dw = 0.0
for l in range(70, 121, 5):
    p = profile(v, img, float(l))
    if len(p) < 40:
        continue
    peak = max(x[1] for x in p)
    half = [x[1] for x in p if x[1] >= peak * 0.5]
    w = len(half) * 0.1
    dw = 0.0 if last_w is None else abs(w - last_w)
    worst_dw = max(worst_dw, dw)
    print("   l=%3d  峰值 %3d  半高宽 %4.1f°  与上一档之差 %.1f°" % (l, peak, w, dw))
    last_w = w
check("相邻银经的半高宽变化 <= 4°（淡出平滑，无接缝）", worst_dw <= 4.0,
      "实测最大 %.1f°" % worst_dw)

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
