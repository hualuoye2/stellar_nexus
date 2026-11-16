# -*- coding: utf-8 -*-
"""开发期工具：把观测计划里跨时刻的高度计算改为显式传 LST 的正确形式。

问题：Observer.altitude() 内部使用「创建时」算好的恒星时，对循环里变化的时刻
      会给出错误结果（暮光因此算不出来）。正确做法是每次用 jd 重新算 LST，
      再调 astronomy.equatorial_to_horizontal。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "stellar_nexus", "核心", "observation_planner.py")
s = open(PATH, encoding="utf-8").read()
applied = []


def rep(old: str, new: str, tag: str) -> None:
    global s
    if old in s:
        s = s.replace(old, new)
        applied.append(tag)
    else:
        print("MISS:", tag, "|", old[:56])


# 1) 暮光：改用显式 LST
rep('''        for i in range(steps + 1):
            t = base + timedelta(minutes=i * step_minutes)
            jd = A.julian_date(t) - observer.utc_offset / 24.0
            sun = A.sun_position(jd)
            alt = observer.altitude(sun["ra"], sun["dec"])
            samples.append((t, alt))''',
    '''        for i in range(steps + 1):
            t = base + timedelta(minutes=i * step_minutes)
            jd = A.julian_date(t) - observer.utc_offset / 24.0
            sun = A.sun_position(jd)
            # 必须用该时刻的恒星时（Observer.altitude 用的是创建时刻的 LST）
            lst = A.local_sidereal_time(jd, observer.lon)
            alt = A.equatorial_to_horizontal(sun["ra"], sun["dec"], lst, observer.lat)[0]
            samples.append((t, alt))''', "暮光 LST")

# 2) 月出月落：同上
rep('''            jd = A.julian_date(t) - observer.utc_offset / 24.0
            moon = A.moon_position(jd)
            alt = observer.altitude(moon["ra"], moon["dec"])''',
    '''            jd = A.julian_date(t) - observer.utc_offset / 24.0
            moon = A.moon_position(jd)
            lst = A.local_sidereal_time(jd, observer.lon)
            alt = A.equatorial_to_horizontal(moon["ra"], moon["dec"], lst, observer.lat)[0]''',
    "月出月落 LST")

# 3) 夜半月亮高度
rep('''        mid_obs = A.Observer(lat=obs.lat, lon=obs.lon, when=mid,
                             utc_offset=obs.utc_offset)
        moon_mid = A.moon_position(mid_obs.jd_ut)
        moon_alt_mid = mid_obs.altitude(moon_mid["ra"], moon_mid["dec"])''',
    '''        mid_jd = A.julian_date(mid) - obs.utc_offset / 24.0
        moon_mid = A.moon_position(mid_jd)
        moon_alt_mid = A.equatorial_to_horizontal(
            moon_mid["ra"], moon_mid["dec"],
            A.local_sidereal_time(mid_jd, obs.lon), obs.lat)[0]''', "夜半月亮高度")

# 4) 深空判定里的太阳与月亮高度
rep('''                sun = A.sun_position(jd)
                sun_alt = obs.altitude(sun["ra"], sun["dec"])
                if sun_alt > DARK_SUN_ALT:
                    ok = False
                # 月亮在天上且目标离月亮太近 → 不划算
                moon_now = A.moon_position(jd)
                moon_alt = obs.altitude(moon_now["ra"], moon_now["dec"])''',
    '''                sun = A.sun_position(jd)
                sun_alt = A.equatorial_to_horizontal(sun["ra"], sun["dec"], lst, obs.lat)[0]
                if sun_alt > DARK_SUN_ALT:
                    ok = False
                # 月亮在天上且目标离月亮太近 → 不划算
                moon_now = A.moon_position(jd)
                moon_alt = A.equatorial_to_horizontal(
                    moon_now["ra"], moon_now["dec"], lst, obs.lat)[0]''', "深空判定 LST")

# 5) 评分里的 self.limiting_mag 应取 plan 的值
rep('            if mag > self.limiting_mag and kind == "star":',
    '            if mag > plan.limiting_mag and kind == "star":', "评分引用 plan.limiting_mag")

# 6) 归一化：候选按当前极限星等裁剪时也用 plan 的值
rep('''        if include_stars:
            star_limit = max(plan.limiting_mag, 4.0)''',
    '''        if include_stars:
            star_limit = max(plan.limiting_mag, 4.0)''', "亮星阈值（占位）")

open(PATH, "w", encoding="utf-8", newline="\n").write(s)
print(f"修正 {len(applied)} 处：{applied}")
