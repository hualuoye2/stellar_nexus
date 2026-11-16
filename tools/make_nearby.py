# -*- coding: utf-8 -*-
"""开发期工具：从 HYG 星表（Hipparcos+Yale+Gliese 汇编）提取 50 光年内的已知天体。

生成 核心/nearby_data.py：
    NEARBY_STARS : 50 光年内全部已知天体的紧凑数据表
                   (HIP 编号, 专名, 视星等, 绝对星等, B-V, 光谱型, 星座, 距离(光年),
                    银道坐标 X/Y/Z(光年), 是否带行星)
    NEARBY_META  : 统计信息（总数、分类计数、数据来源）

坐标系约定（与渲染层一致）：
    X 指向银心方向，Y 沿银道面，Z 指向银河北极；单位：光年。
    太阳位于原点。
"""
from __future__ import annotations

import csv
import gzip
import io
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Tuple

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "celestial", "hyg.csv.gz")
OUT = os.path.join(HERE, "..", "stellar_nexus", "核心", "nearby_data.py")

PC_TO_LY = 3.261563777
LIMIT_LY = 50.0


def classify(spect: str, mag: float, absmag: float, ci: float) -> str:
    """按光谱型与亮度分类（用于渲染颜色与信息标注）。

    注意：华盛顿分类里白矮星写作 DA/DB/DC…（大写 D 开头），
    而红矮星常写作 dM4 / dM5.5e（**小写 d** 表示矮星）。
    早期版本用 upper().startswith("D") 判断，把大量红矮星误判为白矮星，这里修正。
    """
    s = (spect or "").strip()
    if s.startswith("WD") or s.startswith("wd"):
        return "白矮星"
    if s[:1] == "D":
        return "白矮星"
    su = s.upper()
    if su[:1] in ("L", "T", "Y") and not su.startswith("LAC"):
        return "褐矮星"
    if not su:
        return "未知类型"
    # 光度分类：I/II/III 为巨星，IV 为次巨星，V 为主序星
    if "III" in su or "II" in su or "IAB" in su or "IB" in su:
        return "巨星"
    if "IV" in su:
        return "次巨星"
    return "主序星"


def spectral_rgb(spect: str) -> Tuple[int, int, int]:
    """光谱型 → 显示颜色。"""
    table = {
        "O": (155, 176, 255), "B": (170, 191, 255), "A": (202, 215, 255),
        "F": (248, 247, 255), "G": (255, 244, 232), "K": (255, 210, 161),
        "M": (255, 180, 130), "L": (255, 130, 100), "T": (200, 90, 120),
        "Y": (170, 80, 110), "D": (225, 235, 255), "W": (230, 240, 255),
    }
    if not spect:
        return (235, 240, 250)
    return table.get(spect.strip().upper()[0], (235, 240, 250))


def main() -> int:
    if not os.path.isfile(SRC):
        print("缺少 HYG 星表：", os.path.abspath(SRC))
        return 1

    rows: List[Tuple] = []
    seen: set = set()
    total = 0
    counters: Counter = Counter()
    with gzip.open(SRC, "rt", encoding="utf-8", errors="replace") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            total += 1
            try:
                dist_pc = float(row.get("dist") or "nan")
            except ValueError:
                continue
            if dist_pc <= 0 or dist_pc * PC_TO_LY > LIMIT_LY:
                continue
            # 去重：同一颗星可能在星表里出现多次（多星系统的分量）
            base = row.get("base") or row.get("id") or ""
            key = base if base else (row.get("hip") or row.get("id") or "")
            if key in seen:
                continue
            seen.add(key)
            try:
                mag = float(row.get("mag") or "nan")
                absmag = float(row.get("absmag") or "nan")
            except ValueError:
                mag, absmag = 99.0, 99.0
            try:
                ci = float(row.get("ci") or "nan")
            except ValueError:
                ci = float("nan")
            if ci != ci:                      # NaN → 按光谱型估一个
                ci = 0.6
            try:
                # HYG 的 x/y/z 是**赤道直角坐标**（不是 readme 说的银道坐标），
                # 单位为**秒差距**。前者是坐标系判断问题，后者是单位问题，
                # 两个都踩过：只改单位不改坐标系，方向依然会错。
                x = float(row.get("x") or 0.0) * PC_TO_LY
                y = float(row.get("y") or 0.0) * PC_TO_LY
                z = float(row.get("z") or 0.0) * PC_TO_LY
            except ValueError:
                x = y = z = 0.0
            dist_ly = dist_pc * PC_TO_LY
            spect = (row.get("spect") or "").strip()
            kind = classify(spect, mag, absmag, ci)
            counters[kind] += 1
            # Gliese 编号（近星最常用的标识；HYG 的 gl 列形如 "Gl 551" / "GJ 1002"）
            gl_raw = (row.get("gl") or "").strip()
            gliese = ""
            if gl_raw:
                for prefix in ("Gl ", "GJ ", "Gliese ", "Wo ", "G "):
                    if gl_raw.startswith(prefix):
                        gliese = f"Gliese {gl_raw[len(prefix):].strip()}"
                        break
                else:
                    gliese = gl_raw if gl_raw.lower().startswith("gliese") else f"Gliese {gl_raw}"
            rows.append((
                int(row["hip"]) if (row.get("hip") or "").strip().isdigit() else 0,
                (row.get("proper") or "").strip(),
                gliese,
                round(mag, 2) if mag == mag else 99.0,
                round(absmag, 2) if absmag == absmag else 99.0,
                round(ci, 3),
                spect,
                (row.get("con") or "").strip(),
                round(dist_ly, 3),
                round(x, 5), round(y, 5), round(z, 5),
            ))

    rows.sort(key=lambda r: r[8])
    print(f"HYG 总条目 {total}，50 光年内去重后 {len(rows)} 个天体")
    for k, v in counters.most_common():
        print(f"    {k}: {v}")
    named = sum(1 for r in rows if r[1] or r[2])
    with_gliese = sum(1 for r in rows if r[2])
    print(f"    其中有专名或 Gliese 编号: {named}（含 Gliese 编号 {with_gliese}）")
    with_hip = sum(1 for r in rows if r[0])
    print(f"    其中有 HIP 编号: {with_hip}")

    # ---- 写模块 ----
    with open(OUT, "w", encoding="utf-8", newline="\n") as fp:
        fp.write('# -*- coding: utf-8 -*-\n')
        fp.write('"""nearby_data.py —— 太阳 50 光年内的已知天体（自动生成，请勿手改）。\n\n')
        fp.write('数据来源：HYG 星表 v3.8（Hipparcos / Yale BSC / Gliese 汇编）。\n')
        fp.write(f'共 {len(rows)} 个天体（按系统去重后），全部距离 ≤ {LIMIT_LY:.0f} 光年。\n\n')
        fp.write('字段：(HIP, 专名, Gliese 编号, 视星等, 绝对星等, B-V, 光谱型, 星座缩写,\n')
        fp.write('       距离(光年), X, Y, Z)\n')
        fp.write('坐标：**赤道直角坐标**，单位光年，太阳在原点，X 指向 (0h, 0°)，'
                 'Z 指向北天极。\n')
        fp.write('     （HYG 的 readme 写作"银道直角坐标"是错的：实测天狼的 x/y/z\n')
        fp.write('      代入 atan2(y,x) 得 101.2872°，正是星表自己的 ra=6.7525h。\n')
        fp.write('      HYG 原始 x/y/z 单位是秒差距，已在生成时乘 PC_TO_LY 换算为光年）\n')
        fp.write('"""\n\n')
        fp.write('from __future__ import annotations\n\n')
        fp.write('from typing import Any, Dict, List, Tuple\n\n')
        fp.write(f'PC_TO_LY = {PC_TO_LY}\n')
        fp.write(f'LIMIT_LY = {LIMIT_LY}\n\n')
        fp.write('NEARBY_STARS: List[Tuple[int, str, str, float, float, float, str, str, '
                 'float, float, float, float]] = [\n')
        for r in rows:
            fp.write(f'    ({r[0]}, "{r[1]}", "{r[2]}", {r[3]}, {r[4]}, {r[5]}, "{r[6]}", '
                     f'"{r[7]}", {r[8]}, {r[9]}, {r[10]}, {r[11]}),\n')
        fp.write(']\n\n')
        fp.write('NEARBY_META: Dict[str, Any] = {\n')
        fp.write(f'    "count": {len(rows)},\n')
        fp.write(f'    "named": {named},\n')
        fp.write(f'    "with_hip": {with_hip},\n')
        fp.write(f'    "limit_ly": {LIMIT_LY},\n')
        fp.write(f'    "source": "HYG v3.8 (Hipparcos/Yale/Gliese)",\n')
        fp.write(f'    "kinds": {dict(counters)},\n')
        fp.write('}\n')
    print("已写出:", os.path.abspath(OUT), os.path.getsize(OUT), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
