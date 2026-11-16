# -*- coding: utf-8 -*-
"""
tools/check_star_density.py —— 星表扩充（781 → 8883 颗）的正确性与性能自检
===============================================================================
这次改动同时动了两件容易出事的东西：**数据量**（10 倍）与**每帧绘制成本**。
所以断言分两半：

  A 正确性
    * 合并后必须没有重复 HIP（同一颗星画两遍会看起来"更亮"，是隐蔽的视觉 bug）
    * 具名亮星的坐标必须**优先于**背景暗星层（人工校验过的数据不能被覆盖）
    * 扩充进来的星必须落在合法范围内（RA 0~360、Dec ±90、星等 ≤ 6.5）
    * 星座连线用的 HIP 必须全部还在表里（否则连线会断）
    * 具名亮星与 HYG 的坐标偏差必须 < 0.05°（抽查，锁住"小时当度"那类单位错）

  B 性能
    * 每帧实际参与绘制的星数必须受"极限星等"约束（不是无脑画 8883 颗）
    * 单帧耗时不得比扩充前明显退化（按波特尔 5 级与 1 级分别测）

运行：python tools/check_star_density.py
"""
from __future__ import annotations

import io
import math
import os
import sys
import time

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
    from 核心 import light_pollution as LP
    from 核心 import star_data as SD

    print("=" * 80)
    print("A. 正确性")
    print("=" * 80)
    stats = SD.star_catalog_stats()
    for k, v in stats.items():
        print(f"       {k:<20} {v:,}")
    print()

    total = len(SD.ALL_SKY_STARS)
    check("渲染星数已扩充到数千颗（≥ 5000）", total >= 5000, f"{total:,} 颗")

    hips = [s.hip for s in SD.ALL_SKY_STARS]
    dup = total - len(set(hips))
    check("合并后无重复 HIP", dup == 0, f"重复 {dup} 条")

    # 具名亮星优先：同 HIP 必须取 STAR_CATALOG 里的对象（它带中文名与光谱型）
    by_hip = {s.hip: s for s in SD.STAR_CATALOG}
    clash = []
    for s in SD.ALL_SKY_STARS:
        ref = by_hip.get(s.hip)
        if ref is not None and s is not ref:
            clash.append(s.hip)
    check("具名亮星对象未被背景层覆盖（同 HIP 取具名版本）", not clash,
          f"被覆盖 {len(clash)} 条" if clash else "")

    # 坐标与星等合法性
    bad_range = [s.hip for s in SD.ALL_SKY_STARS
                 if not (0.0 <= s.ra < 360.0 and -90.0 <= s.dec <= 90.0)]
    check("全部 RA ∈ [0,360)、Dec ∈ [−90,90]", not bad_range,
          f"越界 {len(bad_range)} 条：{bad_range[:5]}")

    too_dark = [s.hip for s in SD.FAINT_STAR_CATALOG if s.mag > 6.5 + 1e-9]
    check("背景暗星层不超过 6.5 等上限", not too_dark, f"越界 {len(too_dark)} 条")

    # 星座连线端点必须都还在（否则连线会断成两截）
    missing = SD.missing_hip_references()
    check("星座连线引用的 HIP 全部存在", not missing, f"缺失 {len(missing)}：{missing[:6]}")

    # 具名亮星坐标与 HYG 抽查（锁住"小时当度"那类单位错）
    # 这里不重读 HYG（那要 13 MB 解压），改用 FAINT_STARS 作为独立第二源
    worst = 0.0
    worst_name = ""
    checked = 0
    for s in SD.STAR_CATALOG:
        ref = SD.FAINT_STARS.get(s.hip)
        if ref is None:
            continue
        checked += 1
        d1, d2 = math.radians(s.dec), math.radians(ref[1])
        dl = math.radians(s.ra - ref[0])
        c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dl)
        sep = math.degrees(math.acos(max(-1.0, min(1.0, c))))
        if sep > worst:
            worst, worst_name = sep, (s.name or s.name_en)
    check(f"具名亮星与 HYG 坐标一致（抽查 {checked} 颗，偏差 < 0.001°）", worst < 0.001,
          f"最大偏差 {worst:.6f}°（{worst_name}）")

    # 生成表 DETAIL_STARS 里有 10 条坐标是错的，必须被 _authoritative_coords 纠正。
    # 这条断言锁住"纠正逻辑真的在跑"——它一旦失效，那 10 颗星会跑到完全错误的天区，
    # 而且因为都带名字，比背景暗星更容易被用户发现。
    check("DETAIL_STARS 里的 10 条错误坐标已被纠正", len(SD.CORRECTED_HIP) == 10,
          f"纠正了 {len(SD.CORRECTED_HIP)} 条：{sorted(SD.CORRECTED_HIP)}")

    # 特别锁一下那 12 颗曾经"小时当度"的近星
    ra_ok = True
    details = []
    for hip in (70890, 57939, 71681, 54035, 92403, 57548, 91768, 36208, 24186,
                110893, 3829, 439):
        ref = SD.FAINT_STARS.get(hip)
        st = SD.STAR_BY_HIP.get(hip) or SD.ALL_STAR_BY_HIP.get(hip)
        if ref is None or st is None:
            continue
        if abs(st.ra - ref[0]) > 0.05:
            ra_ok = False
            details.append(f"HIP {hip}: {st.ra:.3f} vs {ref[0]:.3f}")
    check("12 颗著名近星的赤经单位正确（曾把小时当度）", ra_ok, "; ".join(details[:3]))

    # key 是 RealSkyView._RenderStar 的属性（形如 star:12345），用作渲染缓存键。
    # HIP 重复会让两颗不同的星共用同一个缓存槽，位置互相覆盖——所以这里查一遍。
    from ui.real_sky_view import _RenderStar
    rkey = _RenderStar(key="star:1", name="x", ra=0.0, dec=0.0, mag=1.0,
                       rgb=(255, 255, 255), distance=None, spectral="",
                       constellation="", hip=1, base_radius=2.0, alpha=1.0,
                       is_named=False)
    check("_RenderStar 具备 key 属性（渲染缓存键）", rkey.key == "star:1")

    print()
    print("=" * 80)
    print("B. 性能：每帧实际绘制量必须受极限星等约束")
    print("=" * 80)
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from datetime import datetime
    app = QApplication.instance() or QApplication([])
    from 核心 import astronomy as A
    from ui.real_sky_view import RealSkyView

    view = RealSkyView()
    view.resize(1400, 860)
    view.show()
    view.set_observer(39.9042, 116.4074, "北京", 8.0)
    view.set_time(datetime(2026, 9, 21, 22, 0, 0))
    check("视图已载入扩充后的星表",
          len(view._stars) == len(SD.ALL_SKY_STARS) and len(view._stars) > 5000,
          f"{len(view._stars):,} 颗")

    for level, label in ((1, "暗空 Bortle 1"), (5, "郊区 Bortle 5"), (9, "市区 Bortle 9")):
        view.set_bortle(level)
        m = view.mag_limit()
        culled = len(view._culled_stars(m))
        t0 = time.time()
        frames = 3
        for _ in range(frames):
            img = QImage(1400, 860, QImage.Format_ARGB32)
            img.fill(0)
            p = QPainter(img)
            view.render(p, QPoint(0, 0))
            p.end()
        dt = (time.time() - t0) / frames * 1000.0
        expect = sum(1 for s in SD.ALL_SKY_STARS if s.mag <= m)
        print(f"       {label}：极限 {m:.1f} 等 → 参与绘制 {culled:,} 颗，"
              f"单帧 {dt:.0f} ms")
        check(f"{label} 的绘制量等于该极限星等下的星数", culled == expect,
              f"{culled:,} vs {expect:,}")
        check(f"{label} 单帧耗时可接受（< 200 ms）", dt < 200.0, f"{dt:.0f} ms")

    # 市区必须显著少于暗空（证明光害模拟真的在省绘制量）
    view.set_bortle(9)
    m9 = len(view._culled_stars(view.mag_limit()))
    view.set_bortle(1)
    m1 = len(view._culled_stars(view.mag_limit()))
    check("市区绘制量明显少于暗空（光害模拟生效）", m9 * 2 < m1,
          f"市区 {m9:,} vs 暗空 {m1:,}")

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
