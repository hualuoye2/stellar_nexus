# -*- coding: utf-8 -*-
"""
开发期工具：验证「从别的恒星看天空」的计算
===============================================================================
坐标系的坑（本脚本的存在理由之一）：HYG 星表的 x/y/z 是**赤道**直角坐标，
不是 readme 里写的银道坐标；而且它的单位是秒差距。两处都曾经写错：

    * 单位错        → 视点距太阳算成 1.30 光年（应为 4.227），全部距离偏小 3.26 倍
    * 坐标系认错    → 从比邻星看天狼的方向差了 100 多度，星座判定完全乱套

下面每一节的最后都有一条"应有值"的对照，方便一眼看出退化。
"""
import io
import math
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from 核心 import inverse_sky as IS          # noqa: E402
from 核心 import nearby_stars as NS         # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print(f"[{'  OK  ' if ok else ' FAIL '}] {label}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


print("=" * 82)
print("① 从各近星看太阳：视星等与所在星座（按视星等排序，取最近的 14 颗）")
print("=" * 82)
print(f"   {'观察点':<18}{'距太阳(光年)':>12}  {'太阳视星等':>10}   {'太阳所在星座':<12}")
for name, d, mag, con in IS.sun_constellation_table()[:14]:
    print(f"   {name:<18}{d:>12.2f}  {mag:>+10.2f}   {con:<12}")

print()
print("=" * 82)
print("② 已知天文事实对照")
print("=" * 82)
print("   要点：从比邻星（4.23 光年）看太阳，视星等约 +0.4——")
print("        这个亮度在天上排得进最亮十几颗，但绝不显眼；")
print("        太阳所在的星座会随观察点方位而变：从比邻星看去它在天龙座，")
print("        而在地球上比邻星位于半人马座，两者正好互为反方向（相差约 114°）。")
rows = IS.sun_constellation_table()
for name, d, mag, con in rows[:6]:
    print(f"   {name:<18} d={d:5.2f} ly   m_sun={mag:+5.2f}   星座={con}")

print()
print("=" * 82)
print("③ 从比邻星看：当地夜空最亮的 12 颗星")
print("=" * 82)
prox = NS.find_nearby("Proxima Centauri")
sky = IS.compute_inverse_sky(prox)
print(f"   {sky.note}")
print(f"   观察点距太阳：{sky.viewpoint_dist:.3f} 光年")
print(f"   看到太阳：{sky.sun.name}  m={sky.sun.app_mag:+.2f}  赤经 {sky.sun.ra:.1f}° "
      f"赤纬 {sky.sun.dec:+.1f}°  星座 {sky.sun.constellation_cn()}")
print()
print(f"   {'天体':<20}{'距离(光年)':>12}{'视星等':>9}   星座")
for s in sky.brightest(12):
    print(f"   {s.name:<20}{s.distance_ly:>12.2f}{s.app_mag:>+9.2f}   {s.constellation_cn()}")

print()
print("=" * 82)
print("④ 从不同位置看，太阳的视星等（距离平方反比）")
print("=" * 82)
for d in (1.0, 4.23, 8.6, 10.0, 20.0, 50.0, 100.0, 1000.0):
    m = IS.apparent_magnitude(IS.SUN_ABSMAG, d)
    naked = "肉眼可见" if m < 6.0 else ("双筒可见" if m < 10.0 else "需望远镜")
    print(f"   距离 {d:>7.1f} 光年 → 太阳视星等 {m:>+7.2f}　{naked}")

print()
print("=" * 82)
print("⑤ 抽查：几颗著名恒星作为观察点的结果")
print("=" * 82)
for q in ("Proxima Centauri", "天狼", "巴纳德星", "南门二", "Wolf 359", "Gliese 1"):
    star = NS.find_nearby(q)
    if star is None:
        print(f"   {q}: 未找到")
        continue
    sk = IS.compute_inverse_sky(star)
    print(f"   {star.display_name:<18} 距太阳 {sk.viewpoint_dist:5.2f} ly　"
          f"看到太阳 m={sk.sun.app_mag:+5.2f} 在{sk.sun.constellation_cn():<8}　"
          f"当地亮星 {len(sk.stars)} 颗")

print()
print("=" * 82)
print("⑥ 逐项断言（失败会列出）")
print("=" * 82)


def _sep(ra1, dec1, ra2, dec2):
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dl = math.radians(ra1 - ra2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dl)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


# 单位：坐标长度必须等于记录的距离（都是光年）
lign = math.sqrt(prox.x ** 2 + prox.y ** 2 + prox.z ** 2)
check("比邻星坐标长度 == distance_ly（单位已统一为光年）",
      abs(lign - prox.distance_ly) < 0.02, f"|xyz|={lign:.3f} ly, distance={prox.distance_ly:.3f} ly")
check("比邻星距太阳 ≈ 4.227 光年", abs(prox.distance_ly - 4.227) < 0.01,
      f"{prox.distance_ly:.3f} ly")

# 坐标系：HYG 的 x/y/z 是赤道直角坐标 → atan2/asin 直接给出赤经赤纬
sirius = NS.find_nearby("天狼")
ra_s, dec_s = IS.direction_from_offsets(sirius.x, sirius.y, sirius.z)
check("HYG 坐标按赤道直角系解读，天狼回到 101.29°/−16.72°",
      abs(ra_s - 101.287) < 0.01 and abs(dec_s + 16.716) < 0.01,
      f"算出 RA {ra_s:.4f}° Dec {dec_s:+.4f}°")

# 视差：同一颗星在两个地方方向必须明显不同
ra_x, dec_x = IS.direction_from_offsets(sirius.x - prox.x, sirius.y - prox.y,
                                        sirius.z - prox.z)
sep = _sep(ra_s, dec_s, ra_x, dec_x)
check("从比邻星看天狼，方向与地球视角相差 20° 以上（真实视差）", sep > 20.0,
      f"相差 {sep:.1f}°")

# 太阳方向 = 视点反方向
d_sun = math.sqrt(prox.x ** 2 + prox.y ** 2 + prox.z ** 2)
check("太阳视星等 = M − 5 + 5·lg(d/3.2616)，比邻星处约 +0.39",
      abs(sky.sun.app_mag - 0.39) < 0.02, f"{sky.sun.app_mag:+.3f}")
check("从比邻星看太阳位于天龙座", sky.sun.constellation_cn() == "天龙座",
      sky.sun.constellation_cn())

# 去重：天狼不应出现两次
check("天狼在合并结果里只出现一次", sum(1 for s in sky.stars if s.name == "天狼") == 1,
      f"出现 {sum(1 for s in sky.stars if s.name == '天狼')} 次")

print()
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for f in FAILS:
        print(f"   - {f}")
    raise SystemExit(1)
print("✅ 全部通过")

