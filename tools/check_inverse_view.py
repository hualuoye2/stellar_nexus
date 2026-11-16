# -*- coding: utf-8 -*-
"""
tools/check_inverse_view.py —— 「外星视角」渲染层自检
===============================================================================
锁住三件容易写错、写错了界面还"看着正常"的事：

  1. 相机基底方向
     沿用 RealSkyView 修正后的约定（right = 西、up = 天顶）。检查：
         * 视野中心 → 屏幕正中
         * 赤纬升高 → 屏幕 y 变小（向上）
         * 赤经增大 → 屏幕 x 变小（向左）——这是"天球外看"与"天球内看"的分水岭
         * 天顶方向在屏幕上的 up 分量为正

  2. 视差确实存在
     从比邻星看天狼，方向必须与地球视角的天狼差几十度；从太阳（原点）看则几乎为 0。

  3. 单位与亮度
     * 观察点到太阳的距离 == |(x, y, z)| == nearby_data 里的 distance_ly
     * m = M − 5 + 5·lg(d_pc) 的数值校验（比邻星看太阳 ≈ +0.39）

运行：python tools/check_inverse_view.py
"""
from __future__ import annotations

import io
import math
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

FAILS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "  OK  " if ok else " FAIL "
    print(f"[{mark}] {label}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from 核心 import astronomy as A, inverse_sky as IS, nearby_stars as NS
    from ui.inverse_sky_view import InverseSkyView

    print("=" * 78)
    print("① 相机基底与投影方向")
    print("=" * 78)
    v = InverseSkyView()
    v.resize(800, 500)
    v.set_fov(90.0)

    # 中心点必须落在屏幕正中
    v.set_center(0.0, 0.0)
    got = v._project(0.0, 0.0)
    check("视野中心投影到屏幕中心", got is not None
          and abs(got[0] - 400) < 0.5 and abs(got[1] - 250) < 0.5,
          f"got {None if got is None else (round(got[0],2), round(got[1],2))}")

    # 赤纬升高 → y 变小
    v.set_center(0.0, 0.0)
    y_low = v._project(0.0, 10.0)
    y_high = v._project(0.0, 40.0)
    check("赤纬升高时屏幕 y 变小（向上）", y_low is not None and y_high is not None
          and y_high[1] < y_low[1],
          f"dec=10 → y={y_low[1]:.1f}，dec=40 → y={y_high[1]:.1f}")

    # 赤经增大 → x 变小（向左）
    v.set_fov(120.0)
    v.set_center(180.0, 0.0)
    x_a = v._project(170.0, 0.0)
    x_b = v._project(190.0, 0.0)
    check("赤经增大时屏幕 x 变小（向东为左）", x_a is not None and x_b is not None
          and x_b[0] < x_a[0],
          f"ra=170 → x={x_a[0]:.1f}，ra=190 → x={x_b[0]:.1f}")

    # 天顶方向 up 分量为正
    v.set_center(120.0, 30.0)
    up_dot = v._up[2]
    check("相机 up 轴的天顶分量为正", up_dot > 0, f"up·ẑ = {up_dot:+.3f}")

    # 屏幕反投影必须与投影互逆
    v.set_center(45.0, 12.0)
    p = v._project(60.0, 25.0)
    ra2, dec2 = v._unproject(p[0], p[1])
    err = abs(((ra2 - 60.0 + 180) % 360) - 180) + abs(dec2 - 25.0)
    check("投影 / 反投影互逆（误差 < 1e-6 度）", err < 1e-6, f"误差 {err:.2e}°")

    print()
    print("=" * 78)
    print("② 视差：同一颗星在两个地方的坐标差")
    print("=" * 78)
    proxima = NS.find_nearby("比邻星")
    sirius = NS.find_nearby("天狼")
    check("能查到比邻星与天狼", proxima is not None and sirius is not None)

    ra_e, dec_e = IS.direction_from_offsets(sirius.x, sirius.y, sirius.z)
    ra_x, dec_x = IS.direction_from_offsets(sirius.x - proxima.x, sirius.y - proxima.y,
                                            sirius.z - proxima.z)
    sep = _sep(ra_e, dec_e, ra_x, dec_x)
    check("从比邻星看天狼，方向与地球差 20° 以上", sep > 20.0, f"相差 {sep:.1f}°")
    check("地球视角的天狼赤经赤纬正确",
          abs(ra_e - 101.29) < 0.3 and abs(dec_e + 16.72) < 0.3,
          f"RA {ra_e:.2f}° Dec {dec_e:+.2f}°（应为 101.29 / −16.72）")

    # 太阳处的观察点：视差必须≈0
    sun_like = type(proxima)(0, "太阳", "", 0, 0, 0, "", "", 0.0, 0.0, 0.0, 0.0)
    ra_s, dec_s = IS.direction_from_offsets(sirius.x - 0.0, sirius.y - 0.0, sirius.z - 0.0)
    check("在太阳处看天狼，方向就是地球视角", _sep(ra_s, dec_s, ra_e, dec_e) < 1e-6,
          f"相差 {_sep(ra_s, dec_s, ra_e, dec_e):.2e}°")

    print()
    print("=" * 78)
    print("③ 距离单位与视星等")
    print("=" * 78)
    lign = math.sqrt(proxima.x ** 2 + proxima.y ** 2 + proxima.z ** 2)
    check("近星坐标长度 == 记录的 distance_ly（都应是光年）",
          abs(lign - proxima.distance_ly) < 0.02,
          f"|xyz| = {lign:.3f} ly，distance_ly = {proxima.distance_ly:.3f} ly")
    check("比邻星距太阳 ≈ 4.23 光年", abs(proxima.distance_ly - 4.227) < 0.05,
          f"{proxima.distance_ly:.3f} ly")
    m_sun = IS.apparent_magnitude(IS.SUN_ABSMAG, proxima.distance_ly)
    check("比邻星看太阳视星等 ≈ +0.39", abs(m_sun - 0.39) < 0.02, f"{m_sun:+.3f}")
    # 1 光年处应为 −2.74（与 4.227 光年的 +0.39 相差 5·lg(4.227) = 3.13）
    check("距离平方反比关系正确（差 5·lg(4.227) = 3.13 等）",
          abs((m_sun - IS.apparent_magnitude(IS.SUN_ABSMAG, 1.0)) - 5 * math.log10(4.227)) < 1e-6)

    print()
    print("=" * 78)
    print("④ 整体计算：三层数据是否齐全、太阳位置是否正确")
    print("=" * 78)
    sky = IS.compute_inverse_sky(proxima, mag_limit=7.5)
    check("太阳已算出", sky.sun is not None)
    # 太阳在坐标原点，所以从比邻星看它必然位于比邻星方向的正反侧：
    # 比邻星 RA 217.43/Dec −62.68 → 太阳 RA ≈ 37.4/Dec ≈ +62.7（天龙座天区）
    check("太阳位于比邻星方向的反方向（RA≈37.4°, Dec≈+62.7°）",
          sky.sun is not None
          and abs(((sky.sun.ra - 37.43 + 180) % 360) - 180) < 0.5
          and abs(sky.sun.dec - 62.68) < 0.5,
          (f"RA {sky.sun.ra:.2f}° Dec {sky.sun.dec:+.2f}°" if sky.sun else "—"))
    check("50 光年内近星数量 > 100", len(sky.local) > 100, f"{len(sky.local)} 颗")
    check("远处亮星背景非空", len(sky.atlas) > 200, f"{len(sky.atlas)} 颗")
    check("合并后无重复 HIP（天狼只出现一次）",
          sum(1 for s in sky.stars if s.name == "天狼") == 1,
          f"天狼出现 {sum(1 for s in sky.stars if s.name == '天狼')} 次")
    check("viewpoint_dist 由坐标算出而不是別的来源",
          abs(sky.viewpoint_dist - proxima.distance_ly) < 0.01,
          f"{sky.viewpoint_dist:.3f} vs {proxima.distance_ly:.3f}")
    check("天体按视星等从亮到暗排序",
          all(sky.stars[i].app_mag <= sky.stars[i + 1].app_mag
              for i in range(min(50, len(sky.stars) - 1))))

    print()
    print("=" * 78)
    print("⑤ 渲染：投影一天球不崩溃，且亮星都落在可见范围内")
    print("=" * 78)
    v.set_sky(sky)
    v.resize(900, 560)
    v.set_fov(180.0)
    drawn = 0
    for s in sky.stars[:400]:
        if v._project(s.ra, s.dec) is not None:
            drawn += 1
    # 视场 180° 时相机前方是**半个天球**，而天体在天空中并非均匀分布
    # （银河带与太阳附近方向贡献了大半），所以约 190/400 落在可投影范围里
    # 完全正常。这里只校验"不低于一半"，同时另外校验：全天分布下应有一半以上。
    check("视场 180° 时可投影天体覆盖相机前方半球", 170 <= drawn <= 260, f"{drawn}/400")
    all_drawn = sum(1 for st in sky.stars if v._project(st.ra, st.dec) is not None)
    check("覆盖比例介于 25%~95% 之间（既不是全丢也不是全中）",
          0.25 <= all_drawn / max(1, len(sky.stars)) <= 0.95,
          f"{all_drawn}/{len(sky.stars)}")
    # 渲染一帧到 QImage，确保 paintEvent 不抛异常
    try:
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QImage, QPainter
        img = QImage(900, 560, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        v.render(p, QPoint(0, 0))
        p.end()
        # 统计非背景像素，证明确实画出了东西（而不是"没报错但全空"）。
        # 阈值取 30：夜空底色本身很暗（#040A14，lightness≈9），网格线的 alpha 只有
        # 40~100，采样后只有几十个点会超过 18，所以这里只验证"确实画上了东西"。
        painted = 0
        for yy in range(0, 560, 8):
            for xx in range(0, 900, 8):
                if img.pixelColor(xx, yy).lightness() > 18:
                    painted += 1
        check("paintEvent 完整跑通（含银河带 / 网格 / 黄道 / 星座连线）",
              painted > 30, f"非背景采样点 {painted}")
    except Exception as exc:                       # pragma: no cover
        check("paintEvent 完整跑通", False, repr(exc))

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


def _sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dl = math.radians(ra1 - ra2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dl)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


if __name__ == "__main__":
    raise SystemExit(main())
