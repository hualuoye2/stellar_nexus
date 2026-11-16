# -*- coding: utf-8 -*-
"""开发期工具：验证天象日历、时间旅行解析、地平障碍与可见性分析。"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from datetime import datetime, timedelta          # noqa: E402

from 核心 import astronomy as A                    # noqa: E402
from 核心 import sky_events as SE                  # noqa: E402
from 核心 import star_data as SD                   # noqa: E402


def main() -> int:
    obs = A.Observer(lat=39.9042, lon=116.4074,
                     when=datetime(2026, 8, 1, 22, 0, 0), utc_offset=8)
    print("=" * 74)
    print("① 月相事件（未来 30 天）")
    print("=" * 74)
    t0 = time.perf_counter()
    phases = SE.find_moon_phases(obs, 30)
    print(f"   找到 {len(phases)} 条，耗时 {(time.perf_counter() - t0) * 1000:.0f} ms")
    for e in phases:
        print(f"   {e.when_text()}  {e.title}　{e.detail}")

    print()
    print("=" * 74)
    print("② 行星事件（未来 30 天）")
    print("=" * 74)
    t0 = time.perf_counter()
    pl = SE.find_planet_events(obs, 30)
    print(f"   找到 {len(pl)} 条，耗时 {(time.perf_counter() - t0) * 1000:.0f} ms")
    for e in pl[:12]:
        print(f"   {e.when_text()}  {e.title}　{e.detail[:56]}")

    print()
    print("=" * 74)
    print("③ 流星雨（未来 60 天）")
    print("=" * 74)
    for e in SE.find_meteor_showers(obs, 60):
        print(f"   {e.when_text()}  {e.title}　{e.detail[:70]}")

    print()
    print("=" * 74)
    print("④ 时间旅行解析")
    print("=" * 74)
    cases = ["2026-08-12", "2026-08-12 21:30", "2026/8/12", "20260812", "8月12日",
             "今晚", "明天 22:00", "3 天后", "+7d", "-12h", "下次满月", "下次新月",
             "火星冲日", "木星冲日", "金星大距", "英仙座流星雨", "双子座流星雨",
             "冬至", "夏至", "21:30", "随便写点什么"]
    for text in cases:
        r = SE.parse_time_expression(text, obs)
        mark = "✓" if r else "✗"
        print(f"   {mark} {text:20s} → {r.strftime('%Y-%m-%d %H:%M') if r else '无法解析'}")

    print()
    print("=" * 74)
    print("⑤ 地平障碍与可见性")
    print("=" * 74)
    profile = SE.build_horizon_preset("城市楼群（四周 25°）")
    print(f"   预设「{profile.note}」：{len(profile.azimuths)} 个采样点")
    for az in (0, 90, 180, 270, 45):
        print(f"      方位 {az:3d}° 障碍高度 {profile.altitude_at(az):5.1f}°　"
              f"高度 20° 是否被挡={profile.is_blocked(az, 20.0)}")
    t0 = time.perf_counter()
    rep_open = SE.visibility_report(*_radec(32349), "天狼", obs, None, 60)
    dt = time.perf_counter() - t0
    print(f"   天狼（无遮挡）：{rep_open.describe()}　耗时 {dt:.1f} s")
    rep_blocked = SE.visibility_report(*_radec(32349), "天狼", obs, profile, 60)
    print(f"   天狼（城市楼群）：{rep_blocked.describe()}")
    print(f"   遮挡损失：{rep_open.clear_hours - rep_blocked.clear_hours:.0f} 小时")
    print("   月度分布（无遮挡）：" +
          "　".join(f"{m}:{h:.0f}h" for m, h in rep_open.monthly_hours))

    # 拱极天体对照
    rep_polaris = SE.visibility_report(*_radec(11767), "北极星", obs, profile, 120)
    print(f"   北极星：{rep_polaris.describe()}")
    return 0


def _radec(hip: int):
    st = SD.get_star(hip)
    return (st.ra, st.dec)


if __name__ == "__main__":
    raise SystemExit(main())
