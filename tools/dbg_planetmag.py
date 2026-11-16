# -*- coding: utf-8 -*-
"""开发期工具：确认行星位置接口返回的星等字段。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                        # noqa: E402

from 核心 import astronomy as A                         # noqa: E402
from 核心 import planet_data as P                       # noqa: E402

jd = A.julian_date(datetime.datetime(2026, 9, 21, 20, 0, 0)) - 8 / 24
for k in ("mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"):
    p = A.planet_position(k, jd)
    keys = sorted(p.keys())
    print(f"  {k:9s} " + "  ".join(f"{key}={p[key]:.3f}" if isinstance(p[key], (int, float))
                                    else f"{key}={p[key]!r}" for key in keys))
print()
print("approximate_magnitude 存在:", hasattr(P, "approximate_magnitude"))
if hasattr(P, "approximate_magnitude"):
    import inspect
    print("签名:", inspect.signature(P.approximate_magnitude))
    try:
        print("调用结果:", P.approximate_magnitude("jupiter", jd))
    except Exception as exc:
        print("调用失败:", exc)
