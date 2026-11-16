# -*- coding: utf-8 -*-
"""
cities.py —— 城市检索（离线 GeoNames 数据）
================================================================================
提供「按城市名查到经纬度 + 时区」的能力，用于快速设置观测点。

    City                    一个城市的记录
    search_cities()         按关键字搜索（支持中文名、英文名、国家、拼音式前缀）
    nearest_city()          按坐标反查最近的城市（用于 IP 定位后显示城市名）
    format_lat()/format_lon()  把带符号的经纬度格式化成「北纬 39.91°」这种可读形式
    parse_hemisphere_input()   把「数值 + 半球/东西」两个输入合成带符号的经纬度

数据来源：GeoNames cities15000（CC-BY 4.0），见 city_data.py。
纬度正为北纬、负为南纬；经度正为东经、负为西经。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .city_data import CITIES, COUNTRIES, MIN_POPULATION
from .utils import LOG, clamp
#: 拼音/英文别名补充表：GeoNames 的英文名未必是常见拼音写法
_ALIAS: Dict[str, str] = {
    "beijing": "Beijing", "peking": "Beijing", "北京": "Beijing",
    "shanghai": "Shanghai", "上海": "Shanghai",
    "guangzhou": "Guangzhou", "广州": "Guangzhou", "canton": "Guangzhou",
    "shenzhen": "Shenzhen", "深圳": "Shenzhen",
    "chengdu": "Chengdu", "成都": "Chengdu",
    "chongqing": "Chongqing", "重庆": "Chongqing",
    "tianjin": "Tianjin", "天津": "Tianjin",
    "wuhan": "Wuhan", "武汉": "Wuhan",
    "xian": "Xi'an", "xi'an": "Xi'an", "西安": "Xi'an",
    "hangzhou": "Hangzhou", "杭州": "Hangzhou",
    "nanjing": "Nanjing", "南京": "Nanjing",
    "suzhou": "Suzhou", "苏州": "Suzhou",
    "kunming": "Kunming", "昆明": "Kunming",
    "lhasa": "Lhasa", "拉萨": "Lhasa",
    "urumqi": "Ürümqi", "乌鲁木齐": "Ürümqi",
    "harbin": "Harbin", "哈尔滨": "Harbin",
    "qingdao": "Qingdao", "青岛": "Qingdao",
    "dalian": "Dalian", "大连": "Dalian",
    "xiamen": "Xiamen", "厦门": "Xiamen",
    "sanya": "Sanya", "三亚": "Sanya",
    "hongkong": "Hong Kong", "香港": "Hong Kong",
    "macau": "Macau", "macao": "Macau", "澳门": "Macau",
    "taipei": "Taipei", "台北": "Taipei", "taibei": "Taipei",
    "kaohsiung": "Kaohsiung", "高雄": "Kaohsiung",
    "tokyo": "Tokyo", "东京": "Tokyo",
    "osaka": "Osaka", "大阪": "Osaka",
    "seoul": "Seoul", "首尔": "Seoul",
    "singapore": "Singapore", "新加坡": "Singapore",
    "bangkok": "Bangkok", "曼谷": "Bangkok",
    "london": "London", "伦敦": "London",
    "paris": "Paris", "巴黎": "Paris",
    "berlin": "Berlin", "柏林": "Berlin",
    "rome": "Rome", "罗马": "Rome",
    "moscow": "Moscow", "莫斯科": "Moscow",
    "newyork": "New York City", "new york": "New York City", "纽约": "New York City",
    "losangeles": "Los Angeles", "los angeles": "Los Angeles", "洛杉矶": "Los Angeles",
    "sydney": "Sydney", "悉尼": "Sydney",
    "melbourne": "Melbourne", "墨尔本": "Melbourne",
    "auckland": "Auckland", "奥克兰": "Auckland",
    "cairo": "Cairo", "开罗": "Cairo",
    "capetown": "Cape Town", "cape town": "Cape Town", "开普敦": "Cape Town",
    "nairobi": "Nairobi", "内罗毕": "Nairobi",
    "santiago": "Santiago", "圣地亚哥": "Santiago",
    "lima": "Lima", "利马": "Lima",
    "quito": "Quito", "基多": "Quito",
    "buenosaires": "Buenos Aires", "buenos aires": "Buenos Aires", "布宜诺斯艾利斯": "Buenos Aires",
    "ushuaia": "Ushuaia", "乌斯怀亚": "Ushuaia",
    "reykjavik": "Reykjavík", "雷克雅未克": "Reykjavík",
    "tromso": "Tromsø", "特罗姆瑟": "Tromsø",
    "longyearbyen": "Longyearbyen", "朗伊尔城": "Longyearbyen",
    "honolulu": "Honolulu", "檀香山": "Honolulu",
}


@dataclass
class City:
    """一个城市/城镇。"""

    name: str            # 英文名（GeoNames ascii 名）
    name_cn: str         # 中文名（可能为空）
    lat: float           # 纬度：正北纬、负南纬
    lon: float           # 经度：正东经、负西经
    country: str         # 国家码
    timezone: str        # 时区名（如 Asia/Shanghai）
    population: int

    @property
    def country_cn(self) -> str:
        return COUNTRIES.get(self.country, self.country)

    @property
    def display(self) -> str:
        """展示用名称：中文名（英文名）· 国家。"""
        if self.name_cn and self.name_cn != self.name:
            main = f"{self.name_cn}（{self.name}）"
        else:
            main = self.name
        country = self.country_cn
        return f"{main}　{country}" if country else main

    @property
    def short(self) -> str:
        return self.name_cn or self.name

    def lat_text(self) -> str:
        return format_lat(self.lat)

    def lon_text(self) -> str:
        return format_lon(self.lon)

    def coord_text(self) -> str:
        return f"{format_lat(self.lat)}　{format_lon(self.lon)}"

    def utc_offset_hours(self) -> Optional[float]:
        """由时区名推算 UTC 偏移（小时）。

        不依赖 tzdata 是否可用：优先用 zoneinfo；退回到按名字解析固定偏移；
        再退回按经度估算（每 15° 一小时）。
        """
        if self.timezone:
            try:
                import datetime as _dt
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(self.timezone)
                    now = _dt.datetime.now(tz)
                    off = now.utcoffset()
                    if off is not None:
                        return round(off.total_seconds() / 3600.0, 1)
                except Exception:
                    pass
            except Exception:
                pass
            # 已知的固定偏移时区名
            fixed = re.match(r"^(?:Etc/)?GMT([+-]\d{1,2})(?::?(\d{2}))?$", self.timezone)
            if fixed:
                hours = float(fixed.group(1))
                minutes = float(fixed.group(2) or 0)
                return hours + (minutes / 60.0 if hours >= 0 else -minutes / 60.0)
            if self.timezone in ("UTC", "Etc/UTC", "Zulu"):
                return 0.0
        # 最后退回按经度估算
        if self.lon == self.lon:
            return round(clamp(self.lon / 15.0, -12.0, 14.0))
        return None


# ==============================================================================
# 数据索引（**惰性构建**——这是冷启动优化过的关键路径，改动前请先看说明）
# ==============================================================================
# 历史问题：这里原先在模块导入时无条件执行 _build_index()，于是只要 import
# 核心.cities（哪怕只是想知道城市总数）就要付出：
#     69,338 个 City 对象构造   约 25 ms
#     两个 dict 索引             约 67 ms
#     常驻内存                   约 6.4 MB
# 而城市检索只在用户点开左侧「观测位置」面板并输入关键字时才需要。
#
# 现在改成三段式：
#     ① 导入期   什么都不做（只有 city_data 那个大模块本身被解析，无法避免）
#     ② 轻量查询 city_count()/all_cities() 直接用原始元组，不建索引
#     ③ 真正检索 search_cities() 第一次调用时才 _ensure_index()
# 这样"从不检索城市的用户"完全不付这笔钱；检索过的用户付一次（之后常驻）。
#
# 注意：索引用**索引整数**而不是 City 对象，构造对象推迟到返回结果时的那几个，
# 所以即使建了索引，也只多出 dict 的开销，不会一次性造 6.9 万个对象。

_ALL: List[City] = []                 # 惰性填充：仅保存最终要返回的 City 对象
_BY_NAME: Dict[str, List[int]] = {}
_BY_CN: Dict[str, List[int]] = {}
_index_ready = False


def _row_to_city(row: Sequence) -> City:
    return City(*row)


def _ensure_index() -> None:
    """构建检索索引（幂等）。只有 search_cities 需要它。"""
    global _index_ready
    if _index_ready:
        return
    for i, row in enumerate(CITIES):
        _BY_NAME.setdefault(str(row[0]).lower(), []).append(i)
        cn = row[1]
        if cn:
            _BY_CN.setdefault(str(cn).lower(), []).append(i)
    _index_ready = True


def _city_at(idx: int) -> City:
    """按下标取 City（按需构造，不缓存——每次搜索最多几十个）。"""
    return _row_to_city(CITIES[idx])


def _population_at(idx: int) -> int:
    try:
        return int(CITIES[idx][6] or 0)
    except (TypeError, ValueError, IndexError):
        return 0


def city_count() -> int:
    return len(CITIES)


def all_cities() -> List[City]:
    """全部城市（69k 个对象，调用方自负内存；界面不要用它遍历）。"""
    return [_row_to_city(r) for r in CITIES]


def index_ready() -> bool:
    """索引是否已构建（自检脚本用它验证惰性加载真的生效）。"""
    return _index_ready


def reset_index() -> None:
    """清空索引（仅供自检脚本模拟"从未检索过"的状态）。"""
    global _index_ready
    _BY_NAME.clear()
    _BY_CN.clear()
    _ALL.clear()
    _index_ready = False


# ==============================================================================
# 检索
# ==============================================================================

def search_cities(text: str, limit: int = 30,
                  country: str = "") -> List[City]:
    """按关键字搜索城市。

    匹配顺序（越靠前越优先）：
        1. 中文名完全相同
        2. 英文名完全相同 / 别名表命中
        3. 中文名前缀、英文名前缀
        4. 中文名包含、英文名包含
        5. 国家中文名匹配（列出该国人口最多的若干城市）
    结果按「匹配等级 → 人口」排序。
    """
    q = (text or "").strip()
    if not q:
        return []
    _ensure_index()
    low = q.lower()
    scored: Dict[int, Tuple[int, int]] = {}       # 索引 → (匹配等级, -人口)

    def add(idx: int, rank: int) -> None:
        prev = scored.get(idx)
        if prev is None or rank < prev[0]:
            scored[idx] = (rank, -_population_at(idx))

    # 1/2) 精确匹配
    for idx in _BY_CN.get(low, ()):
        add(idx, 0)
    alias = _ALIAS.get(low)
    if alias:
        for idx in _BY_NAME.get(alias.lower(), ()):
            add(idx, 1)
    for idx in _BY_NAME.get(low, ()):
        add(idx, 1)

    # 3/4) 前缀与包含（限制扫描量，数据已按人口降序）
    if len(scored) < limit:
        for i, row in enumerate(CITIES):
            name, name_cn = row[0], row[1]
            if name_cn and str(name_cn).lower().startswith(low):
                add(i, 2)
            elif str(name).lower().startswith(low):
                add(i, 3)
    if len(scored) < limit:
        for i, row in enumerate(CITIES):
            name, name_cn = row[0], row[1]
            if low in str(name).lower():
                add(i, 4)
            elif name_cn and low in str(name_cn).lower():
                add(i, 5)

    # 5) 国家名匹配（用于「只输入国家」时给几个主要城市）
    if not scored:
        for code, cn in COUNTRIES.items():
            if low == cn.lower() or low in cn.lower() or low == code.lower():
                for i, row in enumerate(CITIES):
                    if row[4] == code:
                        add(i, 6)
                break

    out = sorted(scored.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    cities = [_city_at(i) for i, _ in out]
    if country:
        filtered = [c for c in cities if c.country.upper() == country.upper()]
        if filtered:
            cities = filtered
    return cities[: max(1, limit)]


def find_city(text: str) -> Optional[City]:
    """取最匹配的一个城市。"""
    hits = search_cities(text, limit=1)
    return hits[0] if hits else None


def nearest_city(lat: float, lon: float, max_distance_deg: float = 5.0) -> Optional[City]:
    """按坐标找最近的城市（用于把 IP 定位的裸坐标变成城市名）。

    这里**故意不建索引**：直接在原始元组上算平方距离，最后只为命中的那一个
    城市构造 City 对象。于是"IP 定位后显示城市名"这条路径永远不会触发
    69k 对象的构造与两个 dict 索引（那是 search_cities 才需要的东西）。
    """
    best_idx: Optional[int] = None
    best_d = max_distance_deg * max_distance_deg
    cos_lat = math.cos(math.radians(clamp(lat, -89.0, 89.0)))
    for i, row in enumerate(CITIES):
        rlat, rlon = row[2], row[3]
        if rlat is None or rlon is None:
            continue
        dlat = rlat - lat
        dlon = (rlon - lon) * cos_lat
        d = dlat * dlat + dlon * dlon
        if d < best_d:
            best_d, best_idx = d, i
    return _city_at(best_idx) if best_idx is not None else None


# ==============================================================================
# 经纬度的可读格式与半球输入
# ==============================================================================

def format_lat(lat: float) -> str:
    """纬度 → 「北纬 39.9042°」/「南纬 33.8688°」。"""
    hemi = "北纬" if lat >= 0 else "南纬"
    return f"{hemi} {abs(lat):.4f}°"


def format_lon(lon: float) -> str:
    """经度 → 「东经 116.4074°」/「西经 70.6693°」。"""
    hemi = "东经" if lon >= 0 else "西经"
    return f"{hemi} {abs(lon):.4f}°"


def format_coord(lat: float, lon: float) -> str:
    return f"{format_lat(lat)}　{format_lon(lon)}"


def signed_from_hemisphere(magnitude: float, positive: bool) -> float:
    """把「数值 + 半球/东西」合成带符号的值。

    magnitude 取绝对值，positive=True 表示北纬/东经（正），False 表示南纬/西经（负）。
    """
    value = abs(float(magnitude))
    return value if positive else -value


def parse_hemisphere_input(lat_value: float, north: bool,
                           lon_value: float, east: bool) -> Tuple[float, float]:
    """由「纬度数值 + 南北」「经度数值 + 东西」得到带符号的 (lat, lon)。"""
    lat = clamp(signed_from_hemisphere(lat_value, north), -90.0, 90.0)
    lon = clamp(signed_from_hemisphere(lon_value, east), -180.0, 180.0)
    return (lat, lon)


def describe_hemisphere(lat: float, lon: float) -> str:
    """给一句直观的说明，帮助判断南北/东西是否正确。"""
    parts: List[str] = []
    if lat >= 0:
        parts.append("北半球")
    else:
        parts.append("南半球")
    if lon >= 0:
        parts.append("东经")
    else:
        parts.append("西经")
    # 极区/赤道附近额外提示
    if abs(lat) < 1.0:
        parts.append("（赤道附近，全年昼夜接近等长）")
    elif abs(lat) > 66.5:
        parts.append("（极圈内，有极昼极夜）")
    elif abs(lat) > 45.0:
        parts.append("（中高纬度，夏季有天文暮光整夜不暗）")
    return "　".join(parts)


__all__ = [
    "City", "city_count", "all_cities", "search_cities", "find_city", "nearest_city",
    "format_lat", "format_lon", "format_coord",
    "signed_from_hemisphere", "parse_hemisphere_input", "describe_hemisphere",
    "COUNTRIES", "MIN_POPULATION",
    # 惰性加载相关的自检接口（界面不用）
    "index_ready", "reset_index",
]
