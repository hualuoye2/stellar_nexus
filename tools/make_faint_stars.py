# -*- coding: utf-8 -*-
"""
开发期工具：从 HYG 星表生成「背景暗星」表
===============================================================================
目的：把主星图的渲染星数从 781 颗提升到几千颗。

现状与瓶颈：
    内置星表（核心/star_catalog_data.py）里的恒星分两部分——
        DETAIL_STARS（160 颗有中英文名的亮星）
        EXTRA_STARS （756 颗，但只包含"88 星座连线用得到的那几颗"）
    于是整片天空只有 781 颗星，放大到 30° 视场时稀疏得认不出星座。

    原先重建星表的 tools/make_catalog.py 依赖 celestial/stars6.json 与
    celestial/lines.json，而这两个文件已经不在仓库里，脚本无法重跑。
    好在 HYG 星表（celestial/hyg.csv.gz）还在，且它自带 Hipparcos 编号——
    所以这里走一条**加法**路线：

        * 星座连线数据（CONSTELLATION_LINES_HIP）保持原样，不动、不重算
          （它已被 22 个自检脚本覆盖，重算的风险远大于收益）
        * 只新增一层"背景暗星"：HYG 里视星等 ≤ 6.5 的全部恒星

    这样星座形状完全不变，只是星场变密——风险最低、收益最直接的改法。

输出：核心/faint_stars_data.py
    FAINT_STARS: Dict[HIP, (赤经°, 赤纬°, 视星等, B-V)]

为什么用 dict 而不是 list：主星图按 HIP 去重（连线端点星不能重复画一遍），
dict 的 key 天然就是去重键。实测 HYG 里 99% 的星都有 HIP 编号。

运行：python tools/make_faint_stars.py
"""
from __future__ import annotations

import csv
import gzip
import io
import os
import sys
from typing import Dict, List, Tuple

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.join(HERE, "..", "stellar_nexus")
CORE = os.path.join(PROJECT, "核心")
HYG = os.path.join(HERE, "..", "celestial", "hyg.csv.gz")
OUT = os.path.join(CORE, "faint_stars_data.py")

#: 视星等上限。6.5 等是"城市边缘肉眼极限"，再暗就只是噪声了
MAG_LIMIT = 6.5


def main() -> int:
    if not os.path.exists(HYG):
        print(f"缺少 HYG 星表：{HYG}")
        return 1

    stars: Dict[int, Tuple[float, float, float, float]] = {}
    skipped_sun = 0
    no_hip = 0
    total = 0
    with gzip.open(HYG, "rt", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                mag = float(row["mag"])
            except (TypeError, ValueError, KeyError):
                continue
            if mag > MAG_LIMIT:
                continue
            total += 1
            proper = (row.get("proper") or "").strip()
            if proper == "Sol":
                skipped_sun += 1          # 太阳不参与恒星渲染（由日月图层负责）
                continue
            hip_raw = (row.get("hip") or "").strip()
            if not hip_raw.isdigit():
                no_hip += 1
                continue
            hip = int(hip_raw)
            try:
                ra = float(row["ra"]) * 15.0        # HYG 的 ra 是**小时**
                dec = float(row["dec"])
            except (TypeError, ValueError, KeyError):
                continue
            if not (-90.0 <= dec <= 90.0):
                continue
            try:
                bv = float(row["ci"])
                if bv != bv:                        # NaN
                    bv = 0.6
            except (TypeError, ValueError):
                bv = 0.6
            bv = max(-0.4, min(2.5, bv))
            stars[hip] = (round(ra % 360.0, 5), round(dec, 5),
                          round(mag, 2), round(bv, 2))

    mags = sorted(v[2] for v in stars.values())
    print(f"HYG 中视星等 ≤ {MAG_LIMIT} 的条目 {total} 条")
    print(f"  跳过太阳              {skipped_sun} 条")
    print(f"  无 HIP 编号（丢弃）    {no_hip} 条")
    print(f"  最终写入              {len(stars)} 颗")
    print(f"  星等范围              {mags[0]:.2f} ~ {mags[-1]:.2f}")
    print(f"  其中亮于 6.0 等        {sum(1 for m in mags if m <= 6.0)} 颗")

    with open(OUT, "w", encoding="utf-8", newline="\n") as fp:
        fp.write('# -*- coding: utf-8 -*-\n')
        fp.write('"""faint_stars_data.py —— 背景暗星表（自动生成，请勿手改）。\n\n')
        fp.write('由 tools/make_faint_stars.py 从 HYG 星表 v3.8 生成：\n')
        fp.write(f'收录视星等 ≤ {MAG_LIMIT} 的全部恒星（含星座连线用到的那些），共 {len(stars)} 颗。\n\n')
        fp.write('字段：HIP -> (赤经(度), 赤纬(度), 视星等, B-V 色指数)\n')
        fp.write('来源：HYG v3.8（Hipparcos / Yale BSC / Gliese 汇编），CC-BY-SA 4.0\n\n')
        fp.write('用途：主星图的"背景星场"层。与 star_catalog_data.EXTRA_STARS 的区别——\n')
        fp.write('EXTRA_STARS 只含 88 星座连线用得到的星（几百颗），本表覆盖到肉眼极限，\n')
        fp.write('把渲染星数从 781 颗提到数千颗，放大视场时星场不再稀疏。\n')
        fp.write('"""\n\n')
        fp.write("from __future__ import annotations\n\n")
        fp.write("from typing import Dict, Tuple\n\n")
        fp.write(f"MAG_LIMIT = {MAG_LIMIT}\n\n")
        fp.write("#: HIP -> (赤经°, 赤纬°, 视星等, B-V)\n")
        fp.write("FAINT_STARS: Dict[int, Tuple[float, float, float, float]] = {\n")
        for hip in sorted(stars):
            ra, dec, mag, bv = stars[hip]
            fp.write(f"    {hip}: ({ra}, {dec}, {mag}, {bv}),\n")
        fp.write("}\n\n")
        fp.write("FAINT_META = {\n")
        fp.write(f'    "count": {len(stars)},\n')
        fp.write(f'    "mag_limit": {MAG_LIMIT},\n')
        fp.write('    "source": "HYG v3.8",\n')
        fp.write('}\n')

    size = os.path.getsize(OUT)
    print(f"\n已写出：{OUT}")
    print(f"  文件大小 {size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
