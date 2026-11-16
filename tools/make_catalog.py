# -*- coding: utf-8 -*-
"""开发期工具：校验并重建项目内置星表数据。

步骤：
  1. 从 核心/star_data.py 读取手写的亮星表 _RAW_STARS（中文名 + 坐标知识）
  2. 与 d3-celestial 的 Hipparcos 衍生星表（stars6.json，5044 颗）逐条比对
     - 若 HIP 命中且位置一致  → 采用权威星表的赤经/赤纬/星等/B-V
     - 若 HIP 命中但位置偏差大 → 报告冲突（人工确认）
     - 若 HIP 未命中           → 按坐标搜索最近的恒星并给出修正后的 HIP
  3. 把结果写入 核心/star_catalog_data.py（DETAIL_STARS + CONSTELLATION_LINES_HIP + EXTRA_STARS）

最终程序只依赖生成后的 Python 模块，不依赖网络。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.join(HERE, "..", "stellar_nexus")
CORE = os.path.join(PROJECT, "核心")
SRC = os.path.join(HERE, "..", "celestial")
sys.path.insert(0, os.path.abspath(PROJECT))

OUT = os.path.join(CORE, "star_catalog_data.py")


def load_reference() -> Tuple[Dict[int, Tuple[float, float, float, float]], List[Tuple[int, float, float, float, float]]]:
    """读取权威星表：返回 (HIP -> (ra,dec,mag,bv) 字典, 列表形式)。"""
    raw = json.load(open(os.path.join(SRC, "stars6.json"), encoding="utf-8"))
    by_hip: Dict[int, Tuple[float, float, float, float]] = {}
    listed: List[Tuple[int, float, float, float, float]] = []
    for feat in raw["features"]:
        ra = float(feat["geometry"]["coordinates"][0]) % 360.0
        dec = float(feat["geometry"]["coordinates"][1])
        mag = float(feat["properties"].get("mag") or 6.0)
        try:
            bv = float(feat["properties"].get("bv") or 0.6)
        except (TypeError, ValueError):
            bv = 0.6
        hip = int(feat["id"])
        by_hip[hip] = (ra, dec, mag, bv)
        listed.append((hip, ra, dec, mag, bv))
    return by_hip, listed


def sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """两个赤道坐标之间的角距离（度）。"""
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dl = math.radians(ra1 - ra2)
    cos_sep = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dl)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def nearest(listed, ra: float, dec: float, limit: float = 0.35):
    """按角距离找最近的参考恒星。"""
    best, best_sep = None, 1e9
    for hip, r, d, mag, bv in listed:
        s = sep_deg(ra, dec, r, d)
        if s < best_sep:
            best_sep, best = s, (hip, r, d, mag, bv)
    if best and best_sep <= limit:
        return best, best_sep
    return None, best_sep


def main() -> None:
    # 动态导入以拿到 _RAW_STARS（不触发星表构建）
    spec = importlib.util.spec_from_file_location("star_data_src", os.path.join(CORE, "star_data.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["star_data_src"] = mod
    # star_data 内部使用相对导入，这里临时提供包上下文
    pkg = importlib.import_module("核心")
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    by_hip, listed = load_reference()
    print(f"参考星表: {len(listed)} 颗")

    detail: List[Tuple[int, str, str, float, float, float, float, str, str]] = []
    fixes: List[str] = []
    misses: List[str] = []
    seen_hip: set = set()
    for hip, name, name_en, ra, dec, mag, bv, spec_t, abbr, dist in mod._RAW_STARS:
        hip = int(hip)
        ref = by_hip.get(hip)
        if ref and sep_deg(ra, dec, ref[0], ref[1]) < 0.35:
            ra_r, dec_r, mag_r, bv_r = ref
            if name and hip not in seen_hip:
                detail.append((hip, name, name_en, ra_r, dec_r, mag_r, bv_r, spec_t, abbr))
                seen_hip.add(hip)
            continue
        # HIP 不准：按坐标搜索最近的恒星
        found, sep = nearest(listed, ra, dec)
        if found:
            nhip, r, d, m, b = found
            fixes.append(f"{name or name_en}: HIP {hip} -> {nhip} (Δ{sep:.3f}°)")
            if name and nhip not in seen_hip:
                detail.append((nhip, name, name_en, r, d, m, b, spec_t, abbr))
                seen_hip.add(nhip)
        else:
            misses.append(f"{name or name_en}: HIP {hip} 未找到 (最近 Δ{sep:.2f}°)")
            if name and hip not in seen_hip:
                detail.append((hip, name, name_en, ra, dec, mag, bv, spec_t, abbr))
                seen_hip.add(hip)

    print(f"详细星表条目: {len(detail)}  修正: {len(fixes)}  未匹配: {len(misses)}")
    with open(os.path.join(HERE, "report_star_fixes.txt"), "w", encoding="utf-8") as fp:
        fp.write("== HIP 修正 ==\n" + "\n".join(fixes) + "\n\n== 未匹配 ==\n" + "\n".join(misses) + "\n")

    # ---- 星座连线 ----
    lines_raw = json.load(open(os.path.join(SRC, "lines.json"), encoding="utf-8"))
    lines: Dict[str, List[List[int]]] = {}
    used: Dict[int, Tuple[float, float, float, float]] = {}
    for feat in lines_raw["features"]:
        abbr = feat["id"]
        polys: List[List[int]] = []
        for coords in feat["geometry"]["coordinates"]:
            hips: List[int] = []
            for lon, lat in coords:
                found, sep = nearest(listed, float(lon) % 360.0, float(lat), 0.15)
                if not found:
                    continue
                hip, r, d, m, b = found
                used[hip] = (round(r, 4), round(d, 4), round(m, 2), round(b, 2))
                if not hips or hips[-1] != hip:
                    hips.append(hip)
            if len(hips) >= 2:
                polys.append(hips)
        if polys:
            lines[abbr] = polys
    print(f"星座连线: {len(lines)} 个星座, 涉及 {len(used)} 颗星")

    # ---- 写文件 ----
    with open(OUT, "w", encoding="utf-8", newline="\n") as fp:
        fp.write('# -*- coding: utf-8 -*-\n')
        fp.write('"""star_catalog_data.py —— 内置星表数据（自动生成，请勿手改）。\n\n')
        fp.write('由 tools/make_catalog.py 从公开的 Hipparcos 衍生亮星表整理生成：\n')
        fp.write('  * DETAIL_STARS            : 有中英文名的亮星（HIP, 中文名, 英文名, RA, Dec, 星等, B-V, 光谱型, 星座缩写）\n')
        fp.write('  * CONSTELLATION_LINES_HIP : 88 星座连线（HIP 编号折线）\n')
        fp.write('  * EXTRA_STARS             : 连线所需的补充恒星（HIP -> RA, Dec, 星等, B-V）\n')
        fp.write('"""\n\n')
        fp.write("from __future__ import annotations\n\n")
        fp.write("from typing import Dict, List, Tuple\n\n")
        fp.write("#: 有名字的亮星表\nDETAIL_STARS: List[Tuple[int, str, str, float, float, float, float, str, str]] = [\n")
        for row in sorted(detail, key=lambda x: x[4]):
            hip, cn, en, ra, dec, mag, bv, sp, ab = row
            fp.write(f'    ({hip}, "{cn}", "{en}", {ra:.5f}, {dec:.5f}, {mag:.2f}, {bv:.2f}, "{sp}", "{ab}"),\n')
        fp.write("]\n\n")
        fp.write("#: 88 星座连线：三字母缩写 -> [[HIP, HIP, ...], ...]\n")
        fp.write("CONSTELLATION_LINES_HIP: Dict[str, List[List[int]]] = {\n")
        for abbr in sorted(lines):
            fp.write(f'    "{abbr}": [\n')
            for poly in lines[abbr]:
                fp.write("        [" + ", ".join(str(h) for h in poly) + "],\n")
            fp.write("    ],\n")
        fp.write("}\n\n")
        fp.write("#: 补充恒星：HIP -> (赤经°, 赤纬°, 视星等, B-V)\n")
        fp.write("EXTRA_STARS: Dict[int, Tuple[float, float, float, float]] = {\n")
        for hip in sorted(used):
            ra, dec, mag, bv = used[hip]
            fp.write(f"    {hip}: ({ra}, {dec}, {mag}, {bv}),\n")
        fp.write("}\n")
    print("已写出:", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
