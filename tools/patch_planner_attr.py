# -*- coding: utf-8 -*-
"""开发期工具：把 _analyze_target 中的 self.moon_illum 改名为 plan.moon_illum。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "stellar_nexus", "核心", "observation_planner.py")
s = open(PATH, encoding="utf-8").read()

PAIRS = [
    ("dso_penalty = 1.0 - clamp(self.moon_illum * 0.6, 0.0, 0.6)",
     "dso_penalty = 1.0 - clamp(plan.moon_illum * 0.6, 0.0, 0.6)"),
    ("if moon_alt > 0 and self.moon_illum > 0.5:",
     "if moon_alt > 0 and plan.moon_illum > 0.5:"),
    ('if kind == "messier" and self.moon_illum > 0.4:',
     'if kind == "messier" and plan.moon_illum > 0.4:'),
]
n = 0
for old, new in PAIRS:
    if old in s:
        s = s.replace(old, new)
        n += 1
    else:
        print("MISS:", old[:60])

# 带 f-string 的那一行单独处理（避免转义问题）
old_f = "t.reasons.append(f\"月光影响（照亮 {self.moon_illum * 100:.0f}%），建议加窄带滤镜\")"
new_f = "t.reasons.append(f\"月光影响（照亮 {plan.moon_illum * 100:.0f}%），建议加窄带滤镜\")"
if old_f in s:
    s = s.replace(old_f, new_f)
    n += 1
else:
    print("MISS: f-string 那行")

open(PATH, "w", encoding="utf-8", newline="\n").write(s)
left = s.count("self.moon_illum")
print(f"替换 {n} 处；剩余 self.moon_illum = {left}（应为 2，属于 ObsPlan 自身）")
for i, line in enumerate(s.splitlines(), 1):
    if "self.moon_illum" in line:
        print(f"   {i}: {line.strip()[:70]}")
