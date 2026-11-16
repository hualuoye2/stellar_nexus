# -*- coding: utf-8 -*-
"""开发期工具：验证城市检索与半球/东西经纬度输入。"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from 核心 import cities as CT          # noqa: E402


def main() -> int:
    print("=" * 78)
    print("① 数据规模")
    print("=" * 78)
    print(f"   城市 {CT.city_count()} 个，覆盖国家 {len(CT.COUNTRIES)} 个")
    t0 = time.perf_counter()
    CT.search_cities("北京")
    print(f"   单次检索耗时 {(time.perf_counter() - t0) * 1000:.1f} ms")

    print()
    print("=" * 78)
    print("② 检索测试（中文 / 英文 / 拼音 / 国家）")
    print("=" * 78)
    for q in ("北京", "上海", "Beijing", "beijing", "new york", "纽约", "Tokyo",
              "santiago", "Santiago de Chile", "雷克雅未克", "Reykjavik",
              "ushuaia", "乌斯怀亚", "内罗毕", "开普敦", "新加坡", "拉萨",
              "longyearbyen", "澳大利亚", "冰岛"):
        hits = CT.search_cities(q, limit=3)
        if not hits:
            print(f"   ✗ {q:20s} 无结果")
            continue
        first = hits[0]
        extra = f"   （共 {len(hits)} 个候选）" if len(hits) > 1 else ""
        print(f"   ✓ {q:20s} → {first.short:10s} {first.coord_text():30s} "
              f"{first.timezone:22s} 人口 {first.population:>9,}{extra}")

    print()
    print("=" * 78)
    print("③ 经纬度的半球/东西格式化")
    print("=" * 78)
    samples = [
        (39.9042, 116.4074, "北京"),
        (-33.8688, 151.2093, "悉尼"),
        (40.7128, -74.0060, "纽约"),
        (-34.6037, -58.3816, "布宜诺斯艾利斯"),
        (64.1466, -21.9426, "雷克雅未克"),
        (-54.8019, -68.3030, "乌斯怀亚"),
        (78.2232, 15.6267, "朗伊尔城"),
        (0.0, 0.0, "赤道/本初子午线"),
    ]
    for lat, lon, name in samples:
        print(f"   {name:16s} {CT.format_coord(lat, lon)}"
              f"　→ {CT.describe_hemisphere(lat, lon)}")

    print()
    print("=" * 78)
    print("④ 半球输入合成（数值 + 南北 + 东西）")
    print("=" * 78)
    cases = [
        (39.9042, True, 116.4074, True, "北京（北纬/东经）"),
        (33.8688, False, 151.2093, True, "悉尼（南纬/东经）"),
        (40.7128, True, 74.0060, False, "纽约（北纬/西经）"),
        (34.6037, False, 58.3816, False, "布宜诺斯艾利斯（南纬/西经）"),
        (90.0, True, 180.0, True, "极值（北纬90/东经180）"),
        (90.0, False, 180.0, False, "极值（南纬90/西经180，应被钳制）"),
    ]
    for value_lat, north, value_lon, east, label in cases:
        lat, lon = CT.parse_hemisphere_input(value_lat, north, value_lon, east)
        print(f"   {label:34s} → ({lat:+8.4f}, {lon:+9.4f})")

    print()
    print("=" * 78)
    print("⑤ 反查最近城市（IP 定位后把坐标变成城市名）")
    print("=" * 78)
    for lat, lon, label in ((39.9042, 116.4074, "北京坐标"),
                            (-33.87, 151.21, "悉尼坐标"),
                            (40.71, -74.01, "纽约坐标"),
                            (64.15, -21.94, "雷克雅未克坐标")):
        c = CT.nearest_city(lat, lon)
        print(f"   {label:16s} → {c.short if c else '未找到':10s} "
              f"{c.coord_text() if c else '':30s} 时区 {c.timezone if c else ''}")

    print()
    print("=" * 78)
    print("⑥ 时区偏移推算")
    print("=" * 78)
    for q in ("北京", "Tokyo", "London", "New York", "santiago", "Reykjavik"):
        c = CT.find_city(q)
        if c:
            print(f"   {c.short:10s} {c.timezone:22s} → UTC{c.utc_offset_hours():+.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
