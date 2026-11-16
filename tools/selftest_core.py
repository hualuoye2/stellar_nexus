# -*- coding: utf-8 -*-
"""开发期自检脚本：验证天文算法、行星位置、架空宇宙生成与航线规划。"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus"))

from 核心 import astronomy as A          # noqa: E402
from 核心 import planet_data as P        # noqa: E402
from 核心 import star_data as SD         # noqa: E402
from 核心 import world_generator as WG   # noqa: E402


def main() -> None:
    print("=" * 70)
    print("1) 时间与恒星时")
    obs = A.Observer(lat=39.9042, lon=116.4074, when=datetime(2025, 1, 15, 22, 0, 0), utc_offset=8)
    print("   JD(UT) =", round(obs.jd_ut, 5), "  LST(h) =", round(obs.lst / 15.0, 4))
    print("   ΔT ≈", round(A.delta_t_seconds(2025.0), 1), "秒")

    print("2) 北极星高度角应约等于观测地纬度 39.9042")
    alt, az = obs.radec_to_altaz(37.9545, 89.2641)
    print("   alt =", round(alt, 3), " az =", round(az, 2))

    print("3) 太阳：北京 2025-06-21 正午高度角（理论约 73.5°）")
    noon = A.Observer(lat=39.9042, lon=116.4074, when=datetime(2025, 6, 21, 12, 0, 0), utc_offset=8)
    sun = A.sun_position(noon.jd_ut)
    salt, saz = noon.radec_to_altaz(sun["ra"], sun["dec"])
    print("   太阳黄经 =", round(sun["lon"], 3), " alt =", round(salt, 2), " az =", round(saz, 2))
    winter = A.Observer(lat=39.9042, lon=116.4074, when=datetime(2025, 12, 21, 12, 0, 0), utc_offset=8)
    wsun = A.sun_position(winter.jd_ut)
    walt, _ = winter.radec_to_altaz(wsun["ra"], wsun["dec"])
    print("   冬至正午高度角（理论约 26.7°）=", round(walt, 2))

    print("4) 银河带与银道坐标（银心应落在人马座，赤经约 266°）")
    ra, dec = A.galactic_to_equatorial(0.0, 0.0)
    print("   银心赤道坐标 ra =", round(ra, 2), " dec =", round(dec, 2))
    print("   银河带多边形数 =", len(A.milky_way_points()))

    print("5) 太阳系天体（VSOP87 为主算法；开普勒根数法用于轨道绘制）")
    for key in ("mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune"):
        v = A.planet_position(key, noon.jd_ut)
        # 注：JPL 近似根数在 1800-2050 内有效，但 25 年外推的瞬时位置误差可达数度，
        # 因此这里用 VSOP87 作为渲染与测距的权威结果。
        print(f"   {P.PLANET_INFO[key]['name_cn']:>4s} 地心距={v['distance']:.4f} AU  "
              f"黄经={v['lon']:.2f}°  视星等≈{P.approximate_magnitude(key, noon.jd_ut):.2f}")
    for key in ("jupiter", "saturn"):
        print(f"   {P.PLANET_INFO[key]['name_cn']} 轨道采样点数 =", len(P.orbital_path(key, noon.jd_ut)))
    moon = A.moon_position(noon.jd_ut)
    print("   月亮 ra =", round(moon["ra"], 2), " dec =", round(moon["dec"], 2),
          " 距离 =", round(moon["distance"]), "km  月相 =", A.moon_phase_name(moon["phase"]))

    print("6) 星表")
    print("  ", SD.star_catalog_stats())
    sky = A.compute_sky(noon, SD.ALL_SKY_STARS, P.NAKED_EYE_PLANETS, SD.MESSIER_CATALOG)
    visible = {k: sum(1 for o in v if o.alt > 0) for k, v in sky.items()}
    print("   当前可见数量:", visible)

    print("7) 架空宇宙生成（4 种规模）")
    for scale in ("small", "medium", "large", "huge"):
        g = WG.generate_galaxy(seed=20250115, scale=scale, distribution="spiral")
        st = g.stats()
        print(f"   {scale:6s} 星系={st['systems']:3d} 行星={st['planets']:4d} "
              f"航线={st['routes']:4d} 文明={st['civilizations']} 势力={st['factions']} 事件={st['events']}")

    print("8) 航线规划与飞船推进")
    g = WG.generate_galaxy(seed=7, scale="medium", distribution="spiral")
    a, b = g.systems[0].sid, g.systems[-1].sid
    for weight in ("distance", "time", "fuel", "risk", "safe"):
        r = WG.plan_route(g, a, b, weight=weight)
        print(f"   weight={weight:8s} 跳数={r.get('legs') and len(r['legs'])} "
              f"距离={r.get('distance')} 时间={r.get('days')}天 燃料={r.get('fuel')} 风险={r.get('risk')}")
    route = WG.plan_route(g, a, b, weight="distance")
    ship = WG.dispatch_ship(g, route["path"], name="测试舰")
    logs = []
    for i in range(12):
        g.year += 6.0
        logs += WG.advance_ships(g, 6.0)
    print("   飞船进度 =", round(ship.progress, 3), " 抵达 =", ship.arrived,
          " 燃料 =", round(ship.fuel, 1), " 舰体 =", round(ship.hull, 1))
    for line in logs[:6]:
        print("     ·", line)

    print("9) JSON 往返")
    data = WG.galaxy_to_dict(g)
    g2 = WG.galaxy_from_dict(data)
    print("   导出字段数 =", len(data), " 导入成功 =", g2 is not None,
          " 星系一致 =", g2 is not None and len(g2.systems) == len(g.systems))
    print("=" * 70)
    print("自检完成")


if __name__ == "__main__":
    main()
