# -*- coding: utf-8 -*-
"""开发期工具：验证光害模型与历史推演引擎。"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from 核心 import light_pollution as LP           # noqa: E402
from 核心 import history_sim as HS               # noqa: E402
from 核心 import world_generator as WG           # noqa: E402
from 核心 import star_data as SD                 # noqa: E402


def main() -> int:
    print("=" * 74)
    print("① 波特尔光害模型")
    print("=" * 74)
    print("   等级      名称           天空亮度      肉眼极限   望远镜(150mm)")
    for b in LP.BORTLE_CLASSES:
        print(f"   {b.level}  {b.name:12s}  {b.mu_sky:6.2f}      "
              f"{b.naked_eye_limiting:5.1f}     "
              f"{LP.telescope_limiting(b.level, 150):5.1f}")
    star_mags = [s.mag for s in SD.STAR_CATALOG]
    dso_mags = [m.mag for m in SD.DEEP_SKY_CATALOG]
    print()
    print("   城市（Bortle 8）vs 山上（Bortle 2）对比：")
    text = LP.compare_report(star_mags, dso_mags, 8, 2, aperture_mm=150)
    for line in text.replace("<br>", "\n").splitlines():
        clean = line.replace("<b style='color:#ffb37a'>", "").replace(
            "<b style='color:#8fffc8'>", "").replace("<b>", "").replace("</b>", "")
        if clean.strip():
            print("     " + clean)
    print()
    print("   月光影响（Bortle 4，木星高度 45°）：")
    for phase, malt in ((0.0, -90), (0.5, 30), (1.0, 60)):
        m = LP.limiting_magnitude(4, phase, 45.0, 0.0, malt)
        print(f"     月相 {phase * 100:3.0f}%  月亮高度 {malt:+3d}° → 极限星等 {m:.2f}")
    print("   大气消光（Bortle 5）：")
    for alt in (90, 60, 30, 15, 8):
        print(f"     高度 {alt:2d}° → 极限星等 {LP.limiting_magnitude(5, 0, alt):.2f}")

    print()
    print("=" * 74)
    print("② 历史推演")
    print("=" * 74)
    g = WG.generate_galaxy(seed=20260101, scale="medium", distribution="spiral")
    print(f"   宇宙：{g.name}　{g.stats()}")
    t0 = time.perf_counter()
    result = HS.simulate(g, years=300, seed=20260101, expansion_rate=1.0,
                         conflict_rate=1.0, growth_rate=1.0)
    dt = time.perf_counter() - t0
    print(f"   推演 300 年耗时 {dt:.2f} s，快照 {len(result.snapshots)} 个，"
          f"事件 {len(result.events)} 条")
    print()
    for line in result.summary_text().splitlines():
        print("   " + line)
    print()
    print("   事件分类统计：")
    from collections import Counter
    cnt = Counter(e.category for e in result.events)
    for k, v in cnt.most_common():
        print(f"     {k}: {v}")
    print()
    print("   关键事件（转折点 + 部分重要事件）：")
    key = [e for e in result.events if e.importance == 3][:10]
    for e in key:
        print(f"     {e.year:6.1f} 年 {e.icon} {e.title}")
        print(f"              {e.detail}")
    print()
    print("   曲线数据（每 50 年取一点）：")
    pop = result.series("population")
    tech = result.series("tech")
    sysobj = result.series("systems")
    print("     年份      总人口(百万)   平均科技   已开发星系")
    for i in range(0, len(pop), 50):
        print(f"     {pop[i][0]:6.0f}   {pop[i][1]:11,.0f}   "
              f"{tech[i][1]:8.3f}   {sysobj[i][1]:8.0f}")
    if pop:
        print(f"     {pop[-1][0]:6.0f}   {pop[-1][1]:11,.0f}   "
              f"{tech[-1][1]:8.3f}   {sysobj[-1][1]:8.0f}")
    print()
    print("   势力曲线（各自人口，首末对比）：")
    for name in result.faction_names:
        s = result.series("population", name)
        if s:
            print(f"     {name:14s} {s[0][1]:9,.1f} → {s[-1][1]:9,.1f} 百万"
                  f"　星系 {result.series('systems', name)[-1][1]:.0f}")

    # 复现性
    r2 = HS.simulate(g, years=300, seed=20260101)
    same = (len(r2.events) == len(result.events)
            and abs(r2.end_population - result.end_population) < 1e-6)
    print()
    print(f"   可复现性（同种子同参数）：{'一致' if same else '不一致'}")

    # 长期推演
    t0 = time.perf_counter()
    r3 = HS.simulate(g, years=800, seed=20260101, conflict_rate=2.0)
    print(f"   800 年高强度冲突推演：{time.perf_counter() - t0:.2f} s，"
          f"事件 {len(r3.events)} 条，消亡势力 {len(r3.collapsed)} 个")
    print(f"     结局：{r3.end_population:,.0f} 百万（峰值 {r3.peak_population:,.0f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
