# -*- coding: utf-8 -*-
"""开发期工具：验证航线规划的权重区分度与风险口径调整。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from 核心 import world_generator as WG          # noqa: E402

g = WG.generate_galaxy(seed=20250115, scale="large", distribution="spiral")
a, b = g.systems[0].sid, g.systems[-1].sid
print("同一对起终点、五种权重：")
for w in ("distance", "time", "fuel", "risk", "safe"):
    r = WG.plan_route(g, a, b, weight=w)
    legs = len(r["legs"])
    print(f"  {w:9s} 跳数={legs:2d}  距离={r['distance']:7.1f} ly  "
          f"时间={r['days']:8.0f} 天  燃料={r['fuel']:7.1f}  "
          f"风险={r['risk']:.3f}（严格累乘 {r['risk_cumulative']:.3f}）")

print()
print("多组起终点：距离权重 vs 安全权重的跳数/风险对比")
for i in range(4):
    a2 = g.systems[i * 3].sid
    b2 = g.systems[-(i * 3 + 2)].sid
    rd = WG.plan_route(g, a2, b2, weight="distance")
    rs = WG.plan_route(g, a2, b2, weight="safe")
    print(f"  #{i+1}  最短距离: {len(rd['legs'])} 跳 / 风险 {rd['risk']:.3f}   "
          f"综合安全: {len(rs['legs'])} 跳 / 风险 {rs['risk']:.3f}")
