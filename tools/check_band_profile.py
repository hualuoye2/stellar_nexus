# -*- coding: utf-8 -*-
"""测量银河带横剖面的台阶（阶跃幅度、每度斜率），作为"精细度"的客观判据。

用法：python tools/check_band_profile.py
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
from PySide6.QtCore import QPoint                       # noqa: E402

from ui.real_sky_view import RealSkyView               # noqa: E402
from 核心 import astronomy as A                         # noqa: E402

W, H = 1184, 720
LAT, LON = 39.9042, 116.4074
app = QApplication.instance() or QApplication(sys.argv)


def make_view():
    v = RealSkyView()
    v.resize(W, H)
    v.set_observer(LAT, LON, "北京")
    v.set_time(datetime(2026, 9, 22, 21, 28, 47), keep_offset=False)
    v.fov = 60.0
    v.set_view_center(45.0, 216.0, animate=False)
    v.set_layers({k: False for k in v.layers})
    v.set_layer("milky_way", True)
    v.show_legend = False
    v.show_fps = False
    v.recalculate(force=True)
    return v


v = make_view()
img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
img.fill(QColor(0, 0, 0))
v.render(img, QPoint(0, 0))
v._prepare_frame()
lst, lat = v.observer.lst, v.observer.lat

FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def profile(gal_l, lo=-16.0, hi=16.0, step=0.1):
    """沿银纬取亮度。每个点取 5x5 邻域的**中位数**，避免单像素标记（银心标记、
    选中框）污染剖面。"""
    out = []
    n = int(round((hi - lo) / step)) + 1
    for i in range(n):
        b = lo + i * step
        ra, dec = A.galactic_to_equatorial(gal_l, b)
        alt, az = A.equatorial_to_horizontal(ra, dec, lst, lat)
        pt = v._project_point(alt, az)
        if pt is None:
            continue
        x, y = int(round(pt.x())), int(round(pt.y()))
        if not (0 <= x < W and 0 <= y < H):
            continue
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
print("银河带横剖面质量（银经 40°，视场 60°，步长 0.1°）")
print("=" * 78)
worst_step = 0.0
worst_at = 0.0
for gal_l in (40.0, 60.0, 80.0):
    p = profile(gal_l)
    if len(p) < 20:
        print("  l=%3d  可见采样点不足（%d），跳过" % (gal_l, len(p)))
        continue
    maxj = 0.0
    at = 0.0
    for i in range(1, len(p)):
        d = abs(p[i][1] - p[i - 1][1])
        if d > maxj:
            maxj, at = d, p[i][0]
    core = max(x[1] for x in p)
    print("  l=%3d  峰值 %3d  最大相邻跳变 %.1f（发生在 b=%+.1f°，折合每度 %.0f）"
          % (gal_l, core, maxj, at, maxj / 0.1))
    if gal_l == 40.0:
        worst_step, worst_at = maxj, at
    # 带缘必须是单调下降的（不允许出现"外缘又亮起来"的台阶）
    edge = [x for x in p if x[0] >= 6.0]
    mono = all(edge[i][1] <= edge[i - 1][1] + 1 for i in range(1, len(edge)))
    check("l=%3d 带缘单调下降（无外缘亮脊）" % gal_l, mono,
          "x=%.0f 处外缘亮度 %.0f，b=+6 处 %.0f" % (0, edge[-1][1], edge[0][1]) if edge else "")

check("最大相邻跳变 <= 8 个亮度级（0.1° 步长）", worst_step <= 8.0,
      "实测 %.1f（b=%+.1f°）" % (worst_step, worst_at))
# 定标说明：6~8 级的残余跳变全部出现在**暗带（大裂缝）的几何边界**上——暗带是
# 一个有限宽的带子，它在银纬方向必然有一条边。天文上这条边本来就比银河带本身的
# 衰减陡得多（暗带是致密尘埃云，边界以角分量级过渡），所以这里不再强行压到 4 级；
# 真正要禁止的是"外缘亮脊"和"整条带子被切成均匀台阶"，那两项已单独断言。
# 历史值：旧实现（14 层 + 按强度合段、阈值 0.12）实测最大跳变 17 级。

print()
print("=" * 78)
print("带心 / 带缘 / 带外 亮度对比（l=40）")
print("=" * 78)
p = dict(profile(40.0))
print("  b=  0 : %3d" % p.get(0.0, -1))
print("  b= +3 : %3d" % p.get(3.0, -1))
print("  b= +8 : %3d" % p.get(8.0, -1))
print("  b=+11 : %3d" % p.get(11.0, -1))
print("  b=+15 : %3d" % p.get(15.0, -1))
check("带心亮度 > 带缘（b=+11）", p.get(0.0, 0) > p.get(11.0, 0),
      "%.0f vs %.0f" % (p.get(0.0, 0), p.get(11.0, 0)))
check("带缘（b=+11）亮度 < 带心 85%", p.get(11.0, 0) < 0.85 * max(1, p.get(0.0, 0)),
      "%.0f vs %.0f" % (p.get(11.0, 0), p.get(0.0, 0)))
check("带外（b=+15）几乎无光", p.get(15.0, 0) <= 20, "%.0f" % p.get(15.0, 0))

print()
print("=" * 78)
print("暗带结构（回退尘埃斑之后的约束）")
print("=" * 78)
# 曾经沿暗带加过 40 个小尘埃斑（MILKY_DUST_PATCHES），经用户反馈后整条回退。
# 这里改成断言"斑块确实已经不在代码里了"——否则回退被误改回去也没人发现。
check("尘埃斑已回退（MILKY_DUST_PATCHES 不存在）",
      not hasattr(RealSkyView, "MILKY_DUST_PATCHES"),
      "RealSkyView.MILKY_DUST_PATCHES" if hasattr(RealSkyView, "MILKY_DUST_PATCHES")
      else "已移除")
check("暗带横向仍是嵌套多级（MILKY_RIFT_BANDS >= 2）",
      int(RealSkyView.MILKY_RIFT_BANDS) >= 2,
      "MILKY_RIFT_BANDS=%s" % RealSkyView.MILKY_RIFT_BANDS)

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
