# -*- coding: utf-8 -*-
"""开发期工具：验证观测计划表生成器。"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                       # noqa: E402

from 核心 import astronomy as A                        # noqa: E402
from 核心 import light_pollution as LP                 # noqa: E402
from 核心 import observation_planner as OP             # noqa: E402
from 核心 import sky_events as SE                      # noqa: E402


def main() -> int:
    when = datetime.datetime(2026, 9, 21, 20, 0, 0)
    obs = A.Observer(lat=39.9042, lon=116.4074, location_name="北京",
                     utc_offset=8, when=when)

    print("=" * 80)
    print("① 观测窗口与月相")
    print("=" * 80)
    ev, mo = OP.find_twilight(obs)
    rise, sett = OP.moon_rise_set(obs)
    print(f"   天文暮光结束：{ev.strftime('%Y-%m-%d %H:%M') if ev else '—'}")
    print(f"   天文晨光开始：{mo.strftime('%Y-%m-%d %H:%M') if mo else '—'}")
    if ev and mo:
        print(f"   天文暗夜时长：{(mo - ev).total_seconds() / 3600:.2f} 小时")
    print(f"   月出：{rise.strftime('%H:%M') if rise else '整夜在地平线上'}")
    print(f"   月落：{sett.strftime('%H:%M') if sett else '整夜在地平线下'}")

    print()
    print("=" * 80)
    print("② 计划生成（无地形遮挡 / 无望远镜）")
    print("=" * 80)
    t0 = time.perf_counter()
    plan = OP.quick_plan(obs, bortle=5)
    dt = time.perf_counter() - t0
    print(f"   耗时 {dt:.2f} s，评估 {len(plan.targets)} 个目标，"
          f"可用 {len(plan.visible_targets())} 个")
    print()
    for line in plan.summary_text().splitlines():
        print("   " + line)
    print("   " + plan.weather_note)
    print()
    print(plan.table_text(14))

    print()
    print("=" * 80)
    print("③ 加入「城市楼群（四周 25°）」地平障碍后的差异")
    print("=" * 80)
    horizon = SE.build_horizon_preset("城市楼群（四周 25°）")
    plan2 = OP.quick_plan(obs, bortle=5, horizon=horizon)
    print(f"   可用目标：无遮挡 {len(plan.visible_targets())} → "
          f"有遮挡 {len(plan2.visible_targets())}")
    a_keys = {t.key for t in plan.visible_targets()}
    b_keys = {t.key for t in plan2.visible_targets()}
    print(f"   因地形被排除：{len(a_keys - b_keys)} 个")
    lost = sorted(a_keys - b_keys)[:8]
    print(f"   例如：{lost}")
    print()
    print("   遮挡情况下前 8 个目标：")
    for i, t in enumerate(plan2.top(8), 1):
        print(f"     {i:2d}. {t.name[:16]:16s} {t.best_text():26s} "
              f"{t.window_text():13s} {t.duration_text()}")

    print()
    print("=" * 80)
    print("④ 光害等级对计划的影响")
    print("=" * 80)
    print(f"   {'等级':>6} {'极限星等':>8} {'可用目标':>8} {'深空目标':>8} {'最长可拍':>10}")
    for b in (1, 3, 5, 7, 9):
        p = OP.quick_plan(obs, bortle=b)
        vis = p.visible_targets()
        dso = [t for t in vis if t.kind == "messier"]
        longest = max((t.window_minutes for t in vis), default=0.0)
        print(f"   {b:>6} {p.limiting_mag:>8.1f} {len(vis):>8} {len(dso):>8} "
              f"{longest / 60:>9.1f}h")

    print()
    print("=" * 80)
    print("⑤ 望远镜口径（Bortle 5，含地形遮挡）")
    print("=" * 80)
    for ap in (0, 80, 150, 300):
        p = OP.quick_plan(obs, bortle=5, horizon=horizon, aperture_mm=ap)
        dso = [t for t in p.visible_targets() if t.kind == "messier"]
        lab = "肉眼" if ap == 0 else f"{ap}mm"
        print(f"   {lab:>7}：可用 {len(p.visible_targets()):3d} 个，"
              f"其中深空 {len(dso):3d} 个")

    print()
    print("=" * 80)
    print("⑥ 今晚安排（时间轴式）")
    print("=" * 80)
    for line in plan2.timeline_text().splitlines()[:16]:
        print("   " + line)

    print()
    print("=" * 80)
    print("⑦ Markdown 导出（前 5 行）")
    print("=" * 80)
    for line in plan2.table_text(5, markdown=True).splitlines():
        print("   " + line)

    # 校验：时间合理性
    print()
    print("=" * 80)
    print("⑧ 正确性抽查")
    print("=" * 80)
    for name in ("木星", "月球", "M31 仙女座星系", "M42 猎户座大星云"):
        t = next((x for x in plan2.targets if name.split()[0] in x.name), None)
        if t is None:
            print(f"   {name}: 未在评估列表中")
            continue
        print(f"   {t.name[:18]:18s} 可见={t.visible} 最佳={t.best_text()}"
              f"　窗口={t.window_text()}　时长={t.duration_text()}　评分={t.score:.2f}")
        if t.reasons:
            print(f"     理由：{'；'.join(t.reasons[:2])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
