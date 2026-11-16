# -*- coding: utf-8 -*-
"""开发期工具：独立验证计划表里 M31 / 木星 / 月球 的高度与方位是否算对。

用天文常识交叉校验：
    · 上中天最高高度 = 90° − |纬度 − 赤纬|
    · M31 赤纬约 +41.3°，北京纬度 39.90° → 最高约 90 − 1.4 = 88.6°，方位正北
    · 月球在 9 月下旬北京上空高度应在 30° 上下
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                        # noqa: E402
import math                                            # noqa: E402

from 核心 import astronomy as A                         # noqa: E402
from 核心 import observation_planner as OP              # noqa: E402
from 核心 import star_data as SD                        # noqa: E402

LAT = 39.9042
LON = 116.4074

print("① 目标赤纬与理论最高高度（90° − |纬度 − 赤纬|）")
targets = {
    "M31": next(m for m in SD.DEEP_SKY_CATALOG if m.name == "M31"),
    "M42": next(m for m in SD.DEEP_SKY_CATALOG if m.name == "M42"),
    "天狼": SD.get_star(32349),
    "织女一": SD.get_star(91262),
}
for name, obj in targets.items():
    dec = float(obj.dec)
    peak_theory = 90.0 - abs(LAT - dec)
    print(f"   {name:8s} 赤纬 {dec:+7.3f}°　理论最高 {peak_theory:6.2f}°")

print()
print("② 用计划表同一套算法逐小时算高度，找实际最高点")
when = datetime.datetime(2026, 9, 21, 20, 0, 0)
obs = A.Observer(lat=LAT, lon=LON, location_name="北京", utc_offset=8, when=when)
plan = OP.quick_plan(obs, bortle=5)
for key in ("messier:M31", "messier:M42", "planet:jupiter", "moon"):
    t = next((x for x in plan.targets if x.key == key), None)
    if t is None:
        print(f"   {key}: 不在计划中")
        continue
    # 独立重算：用赤道坐标 + 逐分钟 LST
    ra = 0.0
    dec = 0.0
    if key.startswith("messier:"):
        m = next(m for m in SD.DEEP_SKY_CATALOG if m.name == key.split(":")[1])
        ra, dec = float(m.ra), float(m.dec)
    elif key.startswith("planet:"):
        p = A.planet_position(key.split(":")[1], obs.jd_ut)
        ra, dec = float(p["ra"]), float(p["dec"])
    else:
        mo = A.moon_position(obs.jd_ut)
        ra, dec = float(mo["ra"]), float(mo["dec"])
    peak = (-99.0, None, 0.0)
    for i in range(0, 24 * 60, 5):
        tt = when + datetime.timedelta(minutes=i)
        jd = A.julian_date(tt) - 8 / 24.0
        lst = A.local_sidereal_time(jd, LON)
        alt, az = A.equatorial_to_horizontal(ra, dec, lst, LAT)
        if alt > peak[0]:
            peak = (alt, tt, az)
    theory = 90.0 - abs(LAT - dec)
    print(f"   {t.name[:12]:12s} 计划表最佳 {t.best_alt:+6.2f}° @ "
          f"{t.best_time.strftime('%H:%M') if t.best_time else '--:--'}"
          f"　独立重算 {peak[0]:+6.2f}° @ {peak[1].strftime('%H:%M')}"
          f"（方位 {az if False else peak[2]:.0f}°）　理论 {theory:.2f}°"
          f"　{'一致' if abs(t.best_alt - peak[0]) < 0.6 else '**不一致**'}")

print()
print("③ 木星在最佳时刻的方位是否合理（应在南方附近）")
t = next((x for x in plan.targets if x.key == "planet:jupiter"), None)
if t is not None:
    from 核心.utils import azimuth_to_compass
    print(f"   木星最佳 {t.best_time.strftime('%H:%M')} 高度 {t.best_alt:+.1f}° "
          f"方位 {t.best_az:.1f}°（{azimuth_to_compass(t.best_az)}）")
    print("   说明：北京看赤纬 +11.8° 的天体，上中天高度 = 90-(39.9-11.8) = 61.9°，方位正南")
