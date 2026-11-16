# -*- coding: utf-8 -*-
"""开发期工具：排查天文暮光计算为何返回 None。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                        # noqa: E402

from 核心 import astronomy as A                         # noqa: E402

obs = A.Observer(lat=39.9042, lon=116.4074, location_name="北京",
                 utc_offset=8, when=datetime.datetime(2026, 9, 21, 20, 0, 0))
print("obs.jd_ut =", obs.jd_ut, " lst =", obs.lst)
base = obs.when.replace(hour=12, minute=0, second=0, microsecond=0)
jd = A.julian_date(base) - obs.utc_offset / 24.0
sun = A.sun_position(jd)
print("sun @12:00:", {k: round(v, 3) for k, v in sun.items() if isinstance(v, (int, float))})
print("observer.altitude        :", obs.altitude(sun["ra"], sun["dec"]))
print("equatorial_to_horizontal :",
      A.equatorial_to_horizontal(sun["ra"], sun["dec"], obs.lst, obs.lat))
print()
print("整点太阳高度：")
for h in range(0, 24, 2):
    t = base + datetime.timedelta(hours=h)
    jd2 = A.julian_date(t) - obs.utc_offset / 24.0
    s = A.sun_position(jd2)
    lst = A.local_sidereal_time(jd2, obs.lon)
    alt = A.equatorial_to_horizontal(s["ra"], s["dec"], lst, obs.lat)[0]
    flag = "  <-- 低于 -18°" if alt < -18 else ""
    print(f"  {t:%H:%M}  太阳高度 {alt:+7.2f}°{flag}")
