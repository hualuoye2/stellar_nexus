# -*- coding: utf-8 -*-
"""观测清单导出（Markdown / CSV）。

为什么要这个功能：观测清单本身只活在内存里（关掉程序就没了），
而用户真正需要带走的是"今晚要看哪些目标、各自现在在哪、什么时候过中天"。
清单里每一项都已经由现实星图算好了坐标与升落信息，导出几乎是零成本，
但缺了它整条"搜索 → 加入清单 → 带到望远镜旁"的链路是断的。

输出内容（两种格式字段一致）：
    名称 / 类型 / 星等 / 赤经赤纬 / 高度角 / 方位角 / 过中天 / 升起 / 落下 / 星座 / 距离
坐标与时间是**导出当刻**的星图状态，不是导入时的，所以导出前请先把时间调到
你要观测的那一刻。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Sequence

from 核心.utils import format_datetime

__all__ = ["plan_markdown", "plan_csv", "default_filename"]

KIND_CN = {
    "star": "恒星", "planet": "行星", "sun": "太阳", "moon": "月球",
    "messier": "深空", "deep_sky": "深空", "constellation": "星座",
}

#: 表格列：(标题, 取值函数)
_COLUMNS = (
    ("名称", lambda it: str(it.get("name") or it.get("name_cn") or "")),
    ("类型", lambda it: KIND_CN.get(str(it.get("kind") or ""),
                                   str(it.get("type_cn") or it.get("kind") or ""))),
    ("星等", lambda it: _num(it.get("mag"), 2)),
    ("赤经", lambda it: _ra(it.get("ra"))),
    ("赤纬", lambda it: _dec(it.get("dec"))),
    ("高度角", lambda it: _deg(it.get("alt"))),
    ("方位角", lambda it: _deg(it.get("az"))),
    ("最高高度", lambda it: _deg(it.get("peak_alt"))),
    ("升落", lambda it: str(it.get("rise_set") or "")),
    ("星座", lambda it: str(it.get("constellation") or "")),
    ("距离", lambda it: _dist(it)),
)


def _num(v, nd=2) -> str:
    try:
        return ("%%.%df" % nd) % float(v)      # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""


def _deg(v) -> str:
    try:
        return "%+.1f°" % float(v)             # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""


def _ra(v) -> str:
    try:
        h = float(v) / 15.0                    # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return "%02dh%02dm" % (int(h), int(round((h - int(h)) * 60)) % 60)


def _dec(v) -> str:
    try:
        d = float(v)                           # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    sign = "+" if d >= 0 else "-"
    d = abs(d)
    return "%s%02d°%02d'" % (sign, int(d), int(round((d - int(d)) * 60)) % 60)


def _time(v) -> str:
    if isinstance(v, datetime):
        return v.strftime("%H:%M")
    s = str(v or "")
    return s[:5] if len(s) >= 5 else s


def _dist(it: Dict[str, object]) -> str:
    # 星图给的字段名是 distance_ly（恒星）/ distance_au（日月行星），
    # 也兼容手写的 distance + kind 组合。
    for key, unit in (("distance_ly", "ly"), ("distance_au", "AU"),
                      ("distance", None)):
        d = it.get(key)
        if d in (None, ""):
            continue
        if unit is None:
            unit = "AU" if str(it.get("kind") or "") in ("planet", "sun", "moon") else "ly"
        try:
            return "%.2f %s" % (float(d), unit)    # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return ""


def _header(when: datetime, observer) -> List[str]:
    loc = getattr(observer, "location_name", "") or "未知地点"
    lat = getattr(observer, "lat", 0.0)
    lon = getattr(observer, "lon", 0.0)
    return [
        "# STELLAR NEXUS 观测清单",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 观测地点 | {loc}（{lat:+.4f}°, {lon:+.4f}°） |",
        f"| 导出时刻 | {format_datetime(when)} |",
        "| 说明 | 坐标/高度角/升落均为**导出时刻**的值，导出前请把星图时间调到真正要观测的时刻 |",
        "",
    ]


def plan_markdown(items: Sequence[Dict[str, object]], when: datetime,
                  observer=None) -> str:
    """生成 Markdown 文本（含表头信息 + 主表）。"""
    out = _header(when, observer)
    if not items:
        out.append("（观测清单为空）")
        return "\n".join(out) + "\n"
    out.append("| " + " | ".join(c[0] for c in _COLUMNS) + " |")
    out.append("| " + " | ".join("---" for _ in _COLUMNS) + " |")
    for it in items:
        out.append("| " + " | ".join(c[1](it) for c in _COLUMNS) + " |")
    out.append("")
    out.append(f"共 {len(items)} 个目标。")
    return "\n".join(out) + "\n"


def plan_csv(items: Sequence[Dict[str, object]]) -> str:
    """生成 CSV 文本（带 BOM，Excel 打开中文不乱码）。"""
    rows = ["\ufeff" + ",".join(c[0] for c in _COLUMNS)]
    for it in items:
        cells = []
        for _title, fn in _COLUMNS:
            v = fn(it).replace('"', '""')
            cells.append('"%s"' % v if ("," in v or '"' in v) else v)
        rows.append(",".join(cells))
    return "\n".join(rows) + "\n"


def default_filename(when: datetime, ext: str = "md") -> str:
    return os.path.join(os.path.expanduser("~"),
                        "stellar_watchlist_%s.%s"
                        % (when.strftime("%Y%m%d_%H%M"), ext))
