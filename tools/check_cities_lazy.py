# -*- coding: utf-8 -*-
"""
tools/check_cities_lazy.py —— 验证城市库的惰性加载真的生效
===============================================================================
背景：核心/cities.py 原先在模块导入时无条件构建索引
（69,338 个 City 对象 + 两个 dict），实测约 92 ms、常驻 6.4 MB，
而城市检索只在用户点开「观测位置」面板并输入关键字时才用得上。

改成惰性后必须同时守住两件事，本脚本就是守它们的：
  ① 省下了钱     —— import 之后 index_ready() 必须是 False
  ② 没弄坏功能   —— 检索结果必须与"早就建好索引"时完全一致

第 ② 条是重点：惰性加载最容易的翻车方式是"为了省时间改变了行为"
（例如忘了在 _ensure_index 之后才用索引、或漏了某些行的中文名）。
所以这里对一批真实查询做**内容级**断言，而不是只看"返回非空"。

运行：python tools/check_cities_lazy.py
"""
from __future__ import annotations

import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'  OK  ' if ok else ' FAIL '}] {label}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


#: 一批真实查询 → 期望首条结果（拿 check_cities.py 里的既有结论做基准）
CASES = {
    "北京": ("北京市", 39.9075, 116.3972),
    "Beijing": ("北京市", 39.9075, 116.3972),
    "santiago": ("圣地亚哥", -33.4569, -70.6483),
    "拉萨": ("拉萨市", 29.6500, 91.1000),
    "Ushuaia": ("乌斯怀亚", -54.8108, -68.3159),
    "开普敦": ("好望角镇", -33.9258, 18.4232),
    "longyearbyen": ("朗伊尔城", 78.2233, 15.6469),
    "乌鲁木齐": ("乌鲁木齐市", 43.8010, 87.6005),
}


def main() -> int:
    print("=" * 78)
    print("① 导入期不得构建索引")
    print("=" * 78)
    t0 = time.time()
    from 核心 import cities as CT
    dt_import = (time.time() - t0) * 1000.0
    check("import 之后 index_ready() 为 False", CT.index_ready() is False,
          f"index_ready={CT.index_ready()}")
    check("city_count() 不依赖索引且立即返回 69338", CT.city_count() == 69338,
          f"{CT.city_count():,}")
    print(f"       import 耗时 {dt_import:.0f} ms（索引尚未构建）")

    print()
    print("=" * 78)
    print("② nearest_city 走轻量路径，不得触发索引")
    print("=" * 78)
    t0 = time.time()
    near = CT.nearest_city(39.9042, 116.4074)
    dt_near = (time.time() - t0) * 1000.0
    check("nearest_city 返回合理结果", near is not None and "北京" in (near.name_cn or near.name),
          (near.name_cn or near.name) if near else "None")
    check("调用 nearest_city 之后 index_ready() 仍为 False", CT.index_ready() is False,
          "IP 定位路径不应付出 69k 对象的代价")
    print(f"       耗时 {dt_near:.0f} ms")

    print()
    print("=" * 78)
    print("③ 首次检索才建索引，且结果与预期完全一致")
    print("=" * 78)
    t0 = time.time()
    CT.search_cities("北京")
    dt_first = (time.time() - t0) * 1000.0
    check("首次检索后 index_ready() 变为 True", CT.index_ready() is True)
    print(f"       首次检索（含建索引）{dt_first:.0f} ms")

    bad = []
    for query, (want_cn, want_lat, want_lon) in CASES.items():
        hits = CT.search_cities(query, limit=3)
        if not hits:
            bad.append(f"{query}: 无结果")
            continue
        top = hits[0]
        name = top.name_cn or top.name
        ok_name = want_cn in name or name in want_cn
        ok_coord = abs(top.lat - want_lat) < 0.01 and abs(top.lon - want_lon) < 0.01
        check(f"检索「{query}」→ {name}（{top.lat:.4f}, {top.lon:.4f}）",
              ok_name and ok_coord,
              "" if (ok_name and ok_coord) else f"期望 {want_cn} ({want_lat}, {want_lon})")
        if not (ok_name and ok_coord):
            bad.append(query)

    print()
    print("=" * 78)
    print("④ 二次检索不重建索引（幂等）")
    print("=" * 78)
    t0 = time.time()
    CT.search_cities("北京")
    dt_again = (time.time() - t0) * 1000.0
    check("二次检索明显快于首次（索引被复用）", dt_again < dt_first,
          f"首次 {dt_first:.0f} ms → 二次 {dt_again:.0f} ms")

    print()
    print("=" * 78)
    print("⑤ reset_index 后行为必须与初始一致（自检接口本身也要对）")
    print("=" * 78)
    CT.reset_index()
    check("reset 之后 index_ready() 为 False", CT.index_ready() is False)
    hits = CT.search_cities("santiago", limit=1)
    check("reset 后重新检索仍能命中", bool(hits) and "圣地亚哥" in (hits[0].name_cn or hits[0].name),
          (hits[0].name_cn or hits[0].name) if hits else "无结果")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
