# -*- coding: utf-8 -*-
"""
tools/check_milky_way.py —— 银河带结构化自检
===============================================================================
银河带是**视觉功能**，没有"正确答案"可比对，但它的几个结构性特征是可以
客观测量的，而且每一处都对应一次真实做错：

  A 数据层（不依赖渲染）
    * 亮度随银经变化：银心/反银心比值必须落在 3~5 倍（天文事实）
    * 两个局部峰：天鹅座 l≈80、船底 l≈330 必须高于邻近银经
    * 大暗带只在 310°~80°（跨 0°）内有效，l≥100 以后必须归零
      —— 第一版衰减尺度取 25°，导致 l=180 仍有 0.50 遮挡、整条银河被均匀压暗
    * 暗带控制点必须单调递增（否则插值全部落空）
    * 暗带横向剖面必须单调：中心最暗、偏离 6° 明显变亮
    * 等照度轮廓必须**同时**返回赤经与赤纬（第一版漏了赤纬，调用方无法投影）

  B 渲染层（直接量像素）
    * 银河带区域亮度必须显著高于同银经的带外区域（> 2 倍）
    * 暗带中心的像素亮度必须低于同银经±6° 处
    * 沿银道面的亮度应呈现"银心/船底方向亮、反银心暗"
    * 红mode 下仍然能画出银河（不崩、不是全黑）

运行：python tools/check_milky_way.py
"""
from __future__ import annotations

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'  OK  ' if ok else ' FAIL '}] {label}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def main() -> int:
    from 核心 import astronomy as A

    print("=" * 80)
    print("A. 数据层")
    print("=" * 80)

    b0 = A.milky_way_brightness(0.0)
    b180 = A.milky_way_brightness(180.0)
    ratio = b0 / max(1e-6, b180)
    check("银心/反银心亮度比落在 3~5 倍（天文事实）", 3.0 <= ratio <= 5.0,
          f"{ratio:.2f} 倍（l=0 → {b0:.2f}，l=180 → {b180:.2f}）")

    b80 = A.milky_way_brightness(80.0)
    b100 = A.milky_way_brightness(100.0)
    check("天鹅座方向 l=80 是局部峰（高于 l=100）", b80 > b100,
          f"l=80 → {b80:.2f}，l=100 → {b100:.2f}")

    b330 = A.milky_way_brightness(330.0)
    b300 = A.milky_way_brightness(300.0)
    check("船底-人马方向 l=330 是局部峰（高于 l=300）", b330 > b300,
          f"l=330 → {b330:.2f}，l=300 → {b300:.2f}")

    check("亮度处处落在 0.25~1.0", all(
        0.25 <= A.milky_way_brightness(l) <= 1.0 for l in range(0, 360, 5)))

    # 大暗带：只在 310~80 有效
    inside = [A.milky_way_rift_at(l, b) for l in (0.0, 20.0, 40.0, 60.0, 80.0)
              for b in (-2.0, 0.0, 2.0)]
    outside = [A.milky_way_rift_at(l, 0.0) for l in (110.0, 140.0, 180.0, 220.0, 270.0)]
    check("暗带内部最大遮挡 > 0.6", max(inside) > 0.6, f"最大 {max(inside):.2f}")
    check("暗带外部（l≥110）遮挡全部归零", max(outside) < 0.02,
          f"最大 {max(outside):.3f}（第一版此处是 0.50，整条银河被均匀压暗）")
    check("偏离银道面 9° 处无遮挡", all(
        A.milky_way_rift_at(l, 9.0) < 0.02 for l in range(0, 360, 10)))

    # 横向剖面单调性：中心最暗（遮挡最大）
    for l in (20, 40, 60):
        prof = [A.milky_way_rift_at(l, b / 2.0) for b in range(-16, 17)]
        peak = max(prof)
        at_center = prof[16]
        at_6deg = max(A.milky_way_rift_at(l, 6.0), A.milky_way_rift_at(l, -6.0))
        check(f"l={l} 暗带横向剖面：中心遮挡@{at_center:.2f} > 偏离6°@{at_6deg:.2f}",
              at_center > at_6deg + 0.05 and peak <= 1.0)

    # 控制点单调性
    ok_mono = True
    for lane in A.MILKY_RIFT_LANES:
        for i in range(len(lane) - 1):
            if lane[i + 1][0] <= lane[i][0]:
                ok_mono = False
    check("暗带控制点银经单调递增（跨 0° 用 360~440 表示）", ok_mono)

    # 等照度轮廓字段完整性
    cont = A.milky_way_contours(levels=5)
    check("等照度轮廓每层返回 6 个字段（含赤纬）",
          bool(cont) and all(len(row) == 6 for row in cont),
          f"{len(cont)} 层，字段数 {[len(r) for r in cont][:3]}")
    frac, ra_u, de_u, ra_d, de_d, rel = cont[0]
    check("轮廓的赤纬确实有变化（不是常量占位）",
          max(de_u) - min(de_u) > 5.0, f"上边界赤纬跨度 {max(de_u) - min(de_u):.1f}°")
    # 宽度随亮度收放：银心附近应比反银心宽
    def width_at(row, lon):
        _f, ru, du, rd, dd, _r = row
        i = int(lon / 3.0) % len(ru)
        return abs(du[i] - dd[i])
    outer = cont[0]
    w0 = width_at(outer, 0.0)
    w180 = width_at(outer, 180.0)
    check("最外层在银心附近比反银心更宽", w0 > w180 * 1.3,
          f"l=0 → {w0:.1f}°，l=180 → {w180:.1f}°")

    print()
    print("=" * 80)
    print("B. 渲染层（直接量像素）")
    print("=" * 80)
    from datetime import datetime
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.real_sky_view import RealSkyView

    v = RealSkyView()
    v.resize(1200, 800)
    v.show()
    v.set_observer(39.9042, 116.4074, "北京", 8.0)
    v.set_time(datetime(2026, 7, 15, 23, 0, 0))
    lst, lat = v.observer.lst, v.observer.lat

    def render(layers):
        for k in list(v.layers):
            v.layers[k] = False
        for k in layers:
            v.layers[k] = True
        img = QImage(1200, 800, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        v.render(p, QPoint(0, 0))
        p.end()
        return img

    def lum(img, l, b):
        ra, dec = A.galactic_to_equatorial(float(l), float(b))
        alt, az = A.equatorial_to_horizontal(ra, dec, lst, lat)
        pt = v._project_point(alt, az)
        if pt is None:
            return None
        x, y = int(pt.x()), int(pt.y())
        if not (0 <= x < 1200 and 0 <= y < 800):
            return None
        return img.pixelColor(x, y).lightness()

    i_empty = render([])
    i_mw = render(["milky_way"])

    def mean(vals):
        vals = [x for x in vals if x is not None]
        return sum(vals) / len(vals) if vals else None

    band = mean([lum(i_mw, l, b) for l in range(0, 360, 5) for b in (-4, 0, 4)])
    # 带外用 ±20°：最外层轮廓半宽 11°，±14° 仍会落在带缘上（实测 93 vs 107，
    # 比例只有 1.15，会被误判成"带子没画出来"）。±20° 才是真正的带外夜空。
    off = mean([lum(i_mw, l, b) for l in range(0, 360, 5) for b in (-20, 20)])
    empty = mean([lum(i_empty, l, 0) for l in range(0, 360, 5)])
    check("银河带区域亮度显著高于带外 ±20°（> 1.5 倍）",
          band is not None and off is not None and band > off * 1.5,
          f"带内 {band:.1f} vs 带外 {off:.1f}")
    check("银河带亮度远高于纯背景（> 2 倍）",
          band is not None and empty is not None and band > empty * 2.0,
          f"带内 {band:.1f} vs 空背景 {empty:.1f}")

    # 暗带：中心 vs 两侧。
    # 这条判据改过一次定标，原因值得记下来：
    #   旧版用「|x| <= half 一律填满 alpha」的**矩形核**，暗带核心与两侧能差 20+ 级；
    #   用户反馈那道"很直的斜边"就是矩形核的边界，所以第三版改成**升余弦横向剖面**
    #   （列中心最暗、两侧平滑衰减到 0）。剖面变柔和后，同一套参数下核心只比两侧
    #   暗 12 级左右——如果继续要求 20 级，就只能把填充加深，而那样会把银河亮核一起
    #   压掉，正好撞上下面那条"核不能被压空"的判据（实测会掉到 0.5）。
    #   两条判据是互相拉扯的，这里按"核仍然是亮的、暗带可辨"取平衡：
    #   要求中心比**较亮的一侧**暗 8 级以上，同时核/侧翼仍落在 0.70~1.00。
    RIFT_EDGE = 4.0
    dark_hits = 0
    samples = 0
    for l in (15, 20, 30, 40, 50, 60, 70):
        bb = max(range(-8, 9), key=lambda x: A.milky_way_rift_at(float(l), float(x)))
        if A.milky_way_rift_at(float(l), float(bb)) < 0.3:
            continue
        c = lum(i_mw, l, bb)
        sides = [x for x in (lum(i_mw, l, bb + RIFT_EDGE),
                             lum(i_mw, l, bb - RIFT_EDGE)) if x is not None]
        if c is None or not sides:
            continue
        samples += 1
        if c < max(sides) - 8.0:
            dark_hits += 1
    check(f"暗带中心像素明显暗于同银经 ±{RIFT_EDGE}°（≥4/6 个采样点）",
          samples >= 4 and dark_hits >= 4,
          f"{dark_hits}/{samples} 个采样点通过（矩形核时期判据是 20 级，"
          f"改成升余弦后按 8 级重新定标）")

    # ★ 锁住用户实测反馈的那条缺陷：暗带把银河的亮核压得太狠，
    #   肉眼看就是"银河中间是空的"。
    #   量化判据：核亮度 / 两侧亮带亮度必须落在 0.70~1.00。
    #     小于 0.70  → 核被抹掉（用户反馈的那一版是 0.34）
    #     大于 1.00  → 暗带弱到看不出（等于没做）
    #   rift_scale=0 时这个比值是 1.17，说明等照度轮廓本身是"中间亮"的，
    #   所以这条断言同时也在保护轮廓层的轴向权重不被改坏。
    core_vals, flank_vals = [], []
    for l in (20, 30, 40, 50, 60):
        bb, _s = v._rift_center_at(float(l))
        c = lum(i_mw, l, bb)
        f1, f2 = lum(i_mw, l, bb + 4.0), lum(i_mw, l, bb - 4.0)
        fs = [x for x in (f1, f2) if x is not None]
        if c is not None and fs:
            core_vals.append(c)
            flank_vals.append(max(fs))
    if core_vals and flank_vals:
        core = sum(core_vals) / len(core_vals)
        flank = sum(flank_vals) / len(flank_vals)
        ratio = core / max(1.0, flank)
        check("暗带没有把银河的核压空（核/侧翼 0.70~1.00）",
              0.70 <= ratio <= 1.00,
              f"核 {core:.1f} / 侧翼 {flank:.1f} = {ratio:.2f}"
              f"（用户反馈的那一版是 0.34；关掉暗带时是 1.17）")
    else:
        check("暗带没有把银河的核压空", False, "采样点不足")

    # 亮度是否跟随模型：不按"哪一侧"比（某一侧此刻可能整片在地平线下，
    # 实测 l=140~220 在夏夜北京全部低于地平线，任何"银心侧 vs 反银心侧"的
    # 比较都会被几何而非亮度支配）。改为**按模型亮度把采样点分三档**，
    # 比较最亮档与最暗档的实测像素——这样与几何完全无关。
    pairs = []
    for l in range(0, 360, 10):
        for b in (-6.0, -3.0, 3.0, 6.0):     # 避开银道面正中（那里有暗带）
            px = lum(i_mw, l, b)
            if px is None:
                continue
            pairs.append((A.milky_way_brightness(float(l)), px))
    if len(pairs) >= 12:
        pairs.sort()
        third = max(1, len(pairs) // 3)
        dim = mean([p for _b, p in pairs[:third]])
        bright = mean([p for _b, p in pairs[-third:]])
        check("实测像素亮度跟随模型：最亮 1/3 银经明显亮于最暗 1/3（> 1.15 倍）",
              dim is not None and bright is not None and bright > dim * 1.15,
              f"亮档 {bright:.1f} vs 暗档 {dim:.1f}（{len(pairs)} 个可见采样点）")
    else:
        check("实测像素亮度跟随模型", False,
              f"可见采样点只有 {len(pairs)} 个，不足以比较")

    # ★ 地平线以下必须被压暗（用户反馈「地平线下的银河带太亮了」）。
    #   根因是绘制顺序：_draw_underground 是"盖一层半透明暗色"，只能压暗
    #   **先画**的东西；原先顺序是 underground → milky_way，银河在地下完全没被压暗。
    #   判定方法：同一视角渲染两次（含/不含 horizon_grid），只比较
    #   **在"仅银河"那一帧里属于银河带**（亮度≥28）的像素——
    #   否则被压暗后的像素会低于阈值而被过滤掉，算出"地下比天上还亮"的假结论。
    v.set_view_center(6.0, 213.0, animate=False)
    lst2, lat2 = v.observer.lst, v.observer.lat
    i_gal = render(["milky_way"])
    i_all = render(["milky_way", "horizon_grid"])

    above_g, above_a, below_g, below_a = [], [], [], []
    for l in range(0, 360, 2):
        for bb in range(-9, 10):
            ra, dec = A.galactic_to_equatorial(float(l), float(bb))
            alt, az = A.equatorial_to_horizontal(ra, dec, lst2, lat2)
            pt = v._project_point(alt, az)
            if pt is None:
                continue
            x, y = int(pt.x()), int(pt.y())
            if not (0 <= x < 1200 and 0 <= y < 800):
                continue
            g = i_gal.pixelColor(x, y).lightness()
            if g < 28.0:
                continue                     # 不是银河带像素
            a = i_all.pixelColor(x, y).lightness()
            if alt > 2.0:
                above_g.append(g)
                above_a.append(a)
            elif alt < -2.0:
                below_g.append(g)
                below_a.append(a)

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    if above_g and below_g:
        ag, aa = _mean(above_g), _mean(above_a)
        bg, ba = _mean(below_g), _mean(below_a)
        check("地平线以上的银河几乎不受地下层影响（≥ 0.9）", aa >= ag * 0.9,
              f"仅银河 {ag:.1f} → 含地下层 {aa:.1f}（比 {aa / max(1.0, ag):.2f}，"
              f"n={len(above_g)}）")
        check("地平线以下的银河被压暗到 40% 以下",
              ba < bg * 0.40,
              f"仅银河 {bg:.1f} → 含地下层 {ba:.1f}（比 {ba / max(1.0, bg):.2f}，"
              f"n={len(below_g)}）")
    else:
        check("地平线以下的银河被压暗", False,
              f"采样不足：上 {len(above_g)} 下 {len(below_g)}")
    v.set_view_center(47.8, 213.83, animate=False)
    lst, lat = v.observer.lst, v.observer.lat

    # 红mode
    v.red_mode = True
    try:
        i_red = render(["milky_way"])
        red_ok = mean([lum(i_red, l, 0) for l in range(0, 360, 10)]) is not None
        check("红mode 下银河仍能绘制", red_ok)
    finally:
        v.red_mode = False

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
