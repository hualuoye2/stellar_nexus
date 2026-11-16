# -*- coding: utf-8 -*-
"""
sky_events.py —— 天象事件、时间旅行、地平障碍与可见性分析
================================================================================
本模块为「天象日历 / 时间旅行」「星空导览」「本地星空模拟器」三项功能提供算法。

内容：
  1. 月相          ：相位角、照亮比例、月相名称、未来 30 天月相事件（新月/上弦/满月/下弦）
  2. 行星事件      ：合月（与月亮近距离）、冲日、大距、留（顺逆行转换）的近似判定
  3. 流星雨        ：12 个主要流星雨的极大日期、ZHR、辐射点赤道坐标
  4. 天象日历      ：把上述事件汇总为未来 N 天的有序列表
  5. 时间旅行      ：自然语言 / 多种日期格式 → datetime（"下次满月"、"火星冲日"、"2026-08-12"…）
  6. 地平障碍      ：观测点四周障碍物角度剖面 → 有效高度阈值
  7. 可见性分析    ：某天体全年可观测小时数、最佳观测窗口、被遮挡时段

所有计算均为纯 Python，不依赖外部星历文件。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import astronomy as A
from . import planet_data as P
from . import star_data as SD
from .utils import LOG, azimuth_to_compass, clamp, format_datetime, norm360

# ==============================================================================
# 一、数据结构
# ==============================================================================

@dataclass
class SkyEvent:
    """一条天象事件。"""

    when: datetime
    category: str                 # 月相 / 行星 / 流星雨 / 太阳 / 深空 / 提示
    title: str                    # 简短标题
    detail: str = ""              # 详细说明
    ra: Optional[float] = None    # 相关天体的赤经（用于一键定位）
    dec: Optional[float] = None
    key: str = ""                 # SkyObject.key 形式，便于直接高亮
    importance: int = 2           # 1=一般 2=值得看 3=不容错过

    @property
    def icon(self) -> str:
        return {"月相": "🌙", "行星": "🪐", "流星雨": "☄", "太阳": "☀",
                "深空": "✨", "提示": "ℹ"}.get(self.category, "•")

    def when_text(self) -> str:
        """格式化时间：当天只显示时刻，其他显示日期。"""
        return self.when.strftime("%m-%d %H:%M")


@dataclass
class TourTarget:
    """星空导览里的一个观测目标。"""

    key: str
    name: str
    kind: str                     # planet / messier / star / moon
    ra: float
    dec: float
    alt: float
    az: float
    mag: float
    score: float = 0.0
    reason: str = ""
    detail: Dict[str, object] = field(default_factory=dict)

    def summary(self) -> str:
        return (f"{self.name}　高度 {self.alt:+.0f}°　方位 {azimuth_to_compass(self.az)}　"
                f"{self.mag:+.1f} 等")


@dataclass
class VisibilityReport:
    """某天体在指定观测点的全年可见性统计。"""

    name: str
    total_hours: float = 0.0          # 全年可观测小时数（考虑地平障碍）
    clear_hours: float = 0.0          # 未受障碍遮挡的小时数
    best_month: str = ""
    best_hours: float = 0.0
    max_altitude: float = 0.0         # 全年最高高度角
    circumpolar: bool = False
    never_rises: bool = False
    monthly_hours: List[Tuple[str, float]] = field(default_factory=list)

    def describe(self) -> str:
        if self.never_rises:
            return "该天体在此纬度永不升起"
        if self.circumpolar:
            return f"拱极天体：全年约 {self.clear_hours:.0f} 小时可观测（最高 {self.max_altitude:.0f}°）"
        return (f"全年约 {self.clear_hours:.0f} 小时可观测"
                f"（{self.best_month} 最多 {self.best_hours:.0f} 小时，"
                f"最高高度 {self.max_altitude:.0f}°）")


# ==============================================================================
# 二、月相
# ==============================================================================

MOON_PHASE_NAMES: Tuple[str, ...] = ("新月", "蛾眉月", "上弦月", "盈凸月",
                                     "满月", "亏凸月", "下弦月", "残月")


def moon_phase_angle(jd: float) -> float:
    """月亮相对于太阳的相位角（0° = 新月，180° = 满月）。"""
    sun = A.sun_position(jd)
    moon = A.moon_position(jd)
    return norm360(moon["lon"] - sun["lon"])


def moon_phase_name_ex(elong: float) -> str:
    """按黄经差给出 8 档月相名称（比 moon_phase_name 更细）。"""
    e = norm360(elong)
    idx = int(((e + 22.5) % 360.0) // 45.0)
    return MOON_PHASE_NAMES[idx]


def find_moon_phases(observer: A.Observer, days: int = 30) -> List[SkyEvent]:
    """搜索未来 days 天内的月相事件（新月 / 上弦 / 满月 / 下弦）。

    做法：以 1 小时为步长采样黄经差，检测其跨过 0° / 90° / 180° / 270° 的时刻，
    再做线性插值精确到分钟。月相的判别与观测地点无关，但事件时间用本地时间表示。
    """
    events: List[SkyEvent] = []
    try:
        step_h = 1.0
        samples = int(days * 24 / step_h)
        targets = ((0.0, "新月", "月亮与太阳同向，整夜不可见，适合深空摄影"),
                   (90.0, "上弦月", "傍晚出现在南方天空，午夜落下"),
                   (180.0, "满月", "整夜可见，最亮；但会淹没暗弱天体"),
                   (270.0, "下弦月", "午夜升起，清晨位于南方天空"))
        prev_t = observer.when
        prev_e = moon_phase_angle(A.julian_date(prev_t) - observer.utc_offset / 24.0)
        for i in range(1, samples + 1):
            t = observer.when + timedelta(hours=i * step_h)
            jd = A.julian_date(t) - observer.utc_offset / 24.0
            e = moon_phase_angle(jd)
            for target, cn, detail in targets:
                # 处理 360 → 0 的环绕
                d_prev = (prev_e - target) % 360.0
                d_now = (e - target) % 360.0
                if d_prev > 300.0 and d_now < 60.0:      # 跨过目标角
                    frac = d_prev / max(1e-6, (d_prev + (360.0 - d_now)))
                    when = prev_t + timedelta(hours=step_h * frac)
                    # 用更细的步长精修到分钟级（否则 1 小时步长会带来 ±30 分钟误差）
                    when = _refine_phase_time(when, target)
                    if when < observer.when:            # 只保留未来事件
                        continue
                    events.append(SkyEvent(
                        when=when, category="月相", title=cn, detail=detail,
                        importance=3 if cn in ("新月", "满月") else 2))
            prev_t, prev_e = t, e
    except Exception as exc:
        LOG.error("月相事件计算失败", exc)
    return events


def _refine_phase_time(approx: datetime, target: float, span_minutes: int = 70) -> datetime:
    """在 approx 附近用 1 分钟步长搜索，把月相时刻精修到分钟级。"""
    try:
        best_t, best_d = approx, 999.0
        for k in range(-span_minutes, span_minutes + 1):
            t = approx + timedelta(minutes=k)
            jd = A.julian_date(t)
            e = moon_phase_angle(jd)
            d = abs((e - target + 180.0) % 360.0 - 180.0)
            if d < best_d:
                best_d, best_t = d, t
        return best_t.replace(second=0, microsecond=0)
    except Exception:
        return approx


def moon_phase_series(days: int = 30, per_day: int = 8) -> List[Tuple[datetime, float]]:
    """未来 days 天的月相曲线（用于日历里的月相条）。

    返回 [(本地时间, 照亮比例 0~1), ...]
    """
    out: List[Tuple[datetime, float]] = []
    try:
        base = datetime.now().replace(minute=0, second=0, microsecond=0)
        total = days * per_day
        for i in range(total + 1):
            t = base + timedelta(days=i / per_day)
            jd = A.julian_date(t) - 8.0 / 24.0          # 以北京时区估算即可
            moon = A.moon_position(jd)
            out.append((t, float(moon["phase"])))
    except Exception as exc:
        LOG.error("月相曲线计算失败", exc)
    return out


# ==============================================================================
# 三、行星事件
# ==============================================================================

PLANET_CN = {"mercury": "水星", "venus": "金星", "mars": "火星",
             "jupiter": "木星", "saturn": "土星", "uranus": "天王星", "neptune": "海王星"}


def find_planet_events(observer: A.Observer, days: int = 30) -> List[SkyEvent]:
    """搜索未来 days 天内的行星事件。

    判定方式（全部用采样 + 极值检测，避免依赖外部星历）：
        * 冲日   ：地心距取到 30 天内的极小值且 < 2.2 AU（外行星）
        * 大距   ：太阳-行星角距取到极值（内行星，> 20°）
        * 合月   ：行星与月亮的角距 < 5°（近似"合月"）
        * 留     ：行星黄经方向反转（顺行↔逆行）
    """
    events: List[SkyEvent] = []
    try:
        step_h = 6.0
        steps = int(days * 24 / step_h)
        keys = ("mercury", "venus", "mars", "jupiter", "saturn")
        # 采样
        data: Dict[str, List[Tuple[datetime, float, float, float, float, float]]] = {k: [] for k in keys}
        for i in range(steps + 1):
            t = observer.when + timedelta(hours=i * step_h)
            jd = A.julian_date(t) - observer.utc_offset / 24.0
            sun = A.sun_position(jd)
            moon = A.moon_position(jd)
            for key in keys:
                p = A.planet_position(key, jd)
                elong = _angular_sep(p["ra"], p["dec"], moon["ra"], moon["dec"])
                sun_sep = _angular_sep(p["ra"], p["dec"], sun["ra"], sun["dec"])
                data[key].append((t, p["ra"], p["dec"], p["distance"], elong, sun_sep))

        for key in keys:
            rows = data[key]
            cn = PLANET_CN.get(key, key)
            is_inner = key in ("mercury", "venus")
            # ---- 冲日 / 最近距离（外行星） ----
            # 只在极值位于采样区间内部时才算事件（否则极值出现在区间端点，
            # 说明真正的冲日不在搜索窗口内，报出来会误导向用户）
            if not is_inner:
                best = min(rows, key=lambda r: r[3])
                if best is not rows[0] and best is not rows[-1] and best[3] < 2.2:
                    events.append(SkyEvent(
                        when=best[0], category="行星", title=f"{cn}接近冲日",
                        detail=f"地心距 {best[3]:.3f} AU，整夜可见且亮度最高",
                        ra=best[1], dec=best[2], key=f"planet:{key}", importance=3))
            # ---- 大距（内行星） ----
            else:
                best = max(rows, key=lambda r: r[5])
                if best is not rows[0] and best is not rows[-1] and best[5] > 20.0:
                    side = "东大距" if _is_east_of_sun(best[1], best[2], observer, best[0]) else "西大距"
                    events.append(SkyEvent(
                        when=best[0], category="行星", title=f"{cn}{side}",
                        detail=f"与太阳角距 {best[5]:.1f}°，是观测水星/金星的最佳时机",
                        ra=best[1], dec=best[2], key=f"planet:{key}", importance=3))
            # ---- 合月 ----
            prev = None
            for row in rows:
                t, ra, dec, _dist, elong, _ss = row
                if elong < 5.0:
                    if prev is None or (t - prev).total_seconds() > 36 * 3600:
                        events.append(SkyEvent(
                            when=t, category="行星", title=f"{cn}合月",
                            detail=f"与月亮角距 {elong:.1f}°，同框观测/摄影良机",
                            ra=ra, dec=dec, key=f"planet:{key}", importance=2))
                        prev = t
            # ---- 留（黄经方向反转） ----
            prev_dir = None
            for i in range(1, len(rows)):
                lon0 = A.equatorial_to_ecliptic(rows[i - 1][1], rows[i - 1][2], A.J2000)[0]
                lon1 = A.equatorial_to_ecliptic(rows[i][1], rows[i][2], A.J2000)[0]
                d = (lon1 - lon0 + 180.0) % 360.0 - 180.0
                direction = 1 if d >= 0 else -1
                if prev_dir is not None and direction != prev_dir:
                    state = "由顺行转逆行" if direction < 0 else "由逆行转顺行"
                    events.append(SkyEvent(
                        when=rows[i][0], category="行星", title=f"{cn}留（{state}）",
                        detail="行星在星空背景上的移动方向发生反转",
                        ra=rows[i][1], dec=rows[i][2], key=f"planet:{key}", importance=1))
                prev_dir = direction
    except Exception as exc:
        LOG.error("行星事件计算失败", exc)
    return events


def _angular_sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """两个赤道坐标的角距（度）。"""
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dl = math.radians(ra1 - ra2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dl)
    return math.degrees(math.acos(clamp(c, -1.0, 1.0)))


def _is_east_of_sun(ra: float, dec: float, observer: A.Observer, when: datetime) -> bool:
    """行星是否在太阳以东（傍晚可见 = 东大距）。"""
    try:
        jd = A.julian_date(when) - observer.utc_offset / 24.0
        sun = A.sun_position(jd)
        diff = (ra - sun["ra"] + 180.0) % 360.0 - 180.0
        return diff > 0
    except Exception:
        return True


# ==============================================================================
# 四、流星雨
# ==============================================================================
# 数据：名称、(极大月份, 极大日)、ZHR、辐射点赤经(度)、赤纬(度)、母天体
METEOR_SHOWERS: Tuple[Tuple[str, Tuple[int, int], int, float, float, str], ...] = (
    ("象限仪座流星雨", (1, 3), 110, 230.0, 49.0, "小行星 2003 EH1"),
    ("天琴座流星雨", (4, 22), 18, 271.0, 34.0, "彗星 C/1861 G1"),
    ("宝瓶座η流星雨", (5, 6), 50, 338.0, -1.0, "哈雷彗星"),
    ("南宝瓶座δ流星雨", (7, 30), 25, 340.0, -16.0, "彗星 96P/Machholz"),
    ("英仙座流星雨", (8, 12), 100, 48.0, 58.0, "彗星 109P/Swift-Tuttle"),
    ("猎户座流星雨", (10, 21), 20, 95.0, 16.0, "哈雷彗星"),
    ("南金牛座流星雨", (11, 5), 5, 52.0, 15.0, "彗星 2P/Encke"),
    ("北金牛座流星雨", (11, 12), 5, 58.0, 22.0, "彗星 2P/Encke"),
    ("狮子座流星雨", (11, 17), 15, 153.0, 22.0, "彗星 55P/Tempel-Tuttle"),
    ("双子座流星雨", (12, 14), 150, 112.0, 33.0, "小行星 3200 Phaethon"),
    ("小熊座流星雨", (12, 22), 10, 217.0, 76.0, "彗星 8P/Tuttle"),
    ("天琴座η流星雨", (5, 10), 3, 287.0, 44.0, "彗星 C/1983 H1"),
)


def find_meteor_showers(observer: A.Observer, days: int = 30) -> List[SkyEvent]:
    """未来 days 天内的流星雨极大事件。"""
    events: List[SkyEvent] = []
    try:
        year = observer.when.year
        for name, (mon, day), zhr, ra, dec, parent in METEOR_SHOWERS:
            for y in (year, year + 1, year - 1):
                try:
                    when = datetime(y, mon, day, 2, 0, 0)      # 极大通常在后半夜
                except ValueError:
                    continue
                delta = when - observer.when
                if timedelta(0) <= delta <= timedelta(days=days):
                    alt, az = observer.radec_to_altaz(ra, dec)
                    visible = alt > 10.0
                    # 月光影响
                    jd = A.julian_date(when) - observer.utc_offset / 24.0
                    moon = A.moon_position(jd)
                    moon_light = float(moon["phase"])
                    quality = "极佳" if moon_light < 0.25 else ("尚可" if moon_light < 0.6 else "受月光干扰")
                    events.append(SkyEvent(
                        when=when, category="流星雨", title=f"{name}极大",
                        detail=(f"ZHR ≈ {zhr}　母天体：{parent}　"
                                f"辐射点高度 {alt:+.0f}°（{azimuth_to_compass(az)}）　"
                                f"月相 {moon_light * 100:.0f}%（{quality}）"
                                + ("" if visible else "　注意：极大时辐射点偏低")),
                        ra=ra, dec=dec, importance=3 if zhr >= 50 else 2))
    except Exception as exc:
        LOG.error("流星雨事件计算失败", exc)
    return events


# ==============================================================================
# 五、天象日历汇总
# ==============================================================================

def build_calendar(observer: A.Observer, days: int = 30,
                   include_planets: bool = True,
                   include_meteors: bool = True,
                   include_moon: bool = True) -> List[SkyEvent]:
    """汇总未来 days 天的天象事件（按时间排序）。"""
    events: List[SkyEvent] = []
    if include_moon:
        events += find_moon_phases(observer, days)
    if include_planets:
        events += find_planet_events(observer, days)
    if include_meteors:
        events += find_meteor_showers(observer, days)
    events.sort(key=lambda e: e.when)
    return events


def calendar_text(events: Sequence[SkyEvent], limit: int = 40) -> str:
    """把日历渲染成纯文本（用于复制/导出）。"""
    lines = [f"天象日历（{len(events)} 条）", "=" * 46]
    for e in events[:limit]:
        lines.append(f"{e.when_text()}  {e.icon} {e.title}")
        if e.detail:
            lines.append(f"            {e.detail}")
    return "\n".join(lines)


# ==============================================================================
# 六、时间旅行（自然语言 / 多种日期格式解析）
# ==============================================================================

#: 常用中文时间表达 → 相对天数
_RELATIVE_WORDS: Dict[str, int] = {
    "今天": 0, "今晚": 0, "今夜": 0, "现在": 0,
    "明天": 1, "明晚": 1, "后晚": 2, "后天": 2, "大后天": 3,
}


def parse_time_expression(text: str, observer: Optional[A.Observer] = None,
                          base: Optional[datetime] = None) -> Optional[datetime]:
    """把用户输入解析为 datetime。

    支持的形式（尽量容错）：
        * ISO 与常见日期：``2026-08-12``、``2026/8/12``、``20260812``、``8-12``、``8月12日``
        * 带时间：``2026-08-12 21:30``、``2026-08-12T21:30:00``、``今晚 21:30``
        * 相对：``今晚``、``明天``、``3 天后``、``+7d``、``-12h``
        * 天象：``下次满月``、``下次新月``、``下次上弦``、``下次下弦``
        * 行星：``火星冲日``、``木星冲日``、``金星大距``、``水星大距``
        * 流星雨：``英仙座流星雨``、``双子座``
        * 节气：``夏至``、``冬至``、``春分``、``秋分``
    """
    if not text or not text.strip():
        return None
    t = text.strip()
    obs = observer or A.Observer()
    now = base or obs.when

    # ---- 1. 相对词 ----
    for word, offset in _RELATIVE_WORDS.items():
        if word in t:
            hh, mm = _extract_clock(t) or (22, 0)
            return (now + timedelta(days=offset)).replace(
                hour=hh, minute=mm, second=0, microsecond=0)

    # ---- 2. 相对偏移（3 天后 / +7d / -12h） ----
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(天|日|小时|时|h|d|周|星期)后?", t, re.I)
    if m:
        amount = float(m.group(1))
        unit = m.group(2).lower()
        if unit in ("天", "日", "d"):
            return now + timedelta(days=amount)
        if unit in ("小时", "时", "h"):
            return now + timedelta(hours=amount)
        if unit in ("周", "星期"):
            return now + timedelta(weeks=amount)
    m = re.fullmatch(r"\s*([+-]\d+)([hdw])\s*", t, re.I)
    if m:
        amount = float(m.group(1))
        unit = m.group(2).lower()
        delta = {"h": timedelta(hours=amount), "d": timedelta(days=amount),
                 "w": timedelta(weeks=amount)}[unit]
        return now + delta

    # ---- 3. 月相 ----
    for word, target in (("满月", 180.0), ("望", 180.0), ("新月", 0.0), ("朔", 0.0),
                         ("上弦", 90.0), ("下弦", 270.0)):
        if word in t:
            return _next_phase_time(obs, target)

    # ---- 4. 行星事件 ----
    for key, cn in PLANET_CN.items():
        if cn in t:
            if "冲" in t:
                return _next_opposition(obs, key)
            if "大距" in t:
                return _next_elongation(obs, key)

    # ---- 5. 流星雨 ----
    for name, (mon, day), _zhr, _ra, _dec, _p in METEOR_SHOWERS:
        short = name.replace("流星雨", "")
        if short and short in t:
            return _next_annual(now, mon, day, hour=2)

    # ---- 6. 节气 ----
    solar_terms = {"春分": (3, 20), "夏至": (6, 21), "秋分": (9, 23), "冬至": (12, 21)}
    for name, (mon, day) in solar_terms.items():
        if name in t:
            when = _next_annual(now, mon, day, hour=12)
            # 用太阳黄经精修（±1 天误差内搜到准确时刻）
            return _refine_solar_term(when, {"春分": 0.0, "夏至": 90.0,
                                             "秋分": 180.0, "冬至": 270.0}[name])

    # ---- 7. 日期格式 ----
    hhmm = _extract_clock(t)
    date = _extract_date(t, now)
    if date is not None:
        if hhmm:
            return date.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
        return date
    if hhmm:                      # 只有时间 → 今天/明天的该时刻
        when = now.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
        if when < now:
            when += timedelta(days=1)
        return when
    return None


def _extract_clock(text: str) -> Optional[Tuple[int, int]]:
    """从文本里提取时刻（支持 21:30 / 21点30 / 21时30分 / 21点）。"""
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh < 24 and 0 <= mm < 60:
            return (hh, mm)
    m = re.search(r"(\d{1,2})\s*[点時时]\s*(?:(\d{1,2})\s*分?)?", text)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        if 0 <= hh < 24 and 0 <= mm < 60:
            return (hh, mm)
    return None


def _extract_date(text: str, base: datetime) -> Optional[datetime]:
    """从文本里提取日期（多种格式）。

    顺序很重要：必须先匹配带 4 位年份的完整日期，再匹配 8 位紧凑格式，
    最后才匹配「月-日」。早期版本把顺序写反，导致 "20260812" 被当成
    "2026-08" 这样的月-日组合解析出错。
    """
    t = text.replace("年", "-").replace("月", "-").replace("日", "")
    t = t.replace("/", "-").replace(".", "-")
    # 1) 完整日期 YYYY-M-D
    m = re.search(r"(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})", t)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 22, 0)
        except ValueError:
            return None
    # 2) 紧凑 8 位 YYYYMMDD（必须在月-日之前判断）
    m = re.search(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", t)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 22, 0)
        except ValueError:
            return None
    # 3) 月-日（当年，若已过则明年）
    m = re.search(r"(?<!\d)(\d{1,2})\s*-\s*(\d{1,2})(?!\d)", t)
    if m:
        mon, day = int(m.group(1)), int(m.group(2))
        for y in (base.year, base.year + 1):
            try:
                when = datetime(y, mon, day, 22, 0)
            except ValueError:
                continue
            if when >= base - timedelta(days=1):
                return when
    return None


def _next_phase_time(obs: A.Observer, target: float, horizon_days: int = 60) -> datetime:
    """求下一次指定月相的时刻。"""
    events = find_moon_phases(obs, horizon_days)
    want = {0.0: "新月", 90.0: "上弦月", 180.0: "满月", 270.0: "下弦月"}[target]
    for e in events:
        if e.title == want:
            return e.when
    return obs.when


def _next_opposition(obs: A.Observer, key: str, horizon_days: int = 420) -> datetime:
    """求下一次冲日（地心距极小）的时刻。

    以 1 天为步长粗搜，再在极小点附近用 30 分钟步长精修，得到小时级精度。
    """
    step_h = 24.0
    steps = int(horizon_days * 24 / step_h)
    best_t, best_d = obs.when, 1e9
    for i in range(steps + 1):
        t = obs.when + timedelta(hours=i * step_h)
        jd = A.julian_date(t) - obs.utc_offset / 24.0
        d = P.geocentric_distance(key, jd)
        if d < best_d:
            best_d, best_t = d, t
    # 精修
    fine_t, fine_d = best_t, best_d
    for k in range(-24, 25):
        t = best_t + timedelta(hours=k * 0.5)
        jd = A.julian_date(t) - obs.utc_offset / 24.0
        d = P.geocentric_distance(key, jd)
        if d < fine_d:
            fine_d, fine_t = d, t
    return fine_t.replace(minute=0, second=0, microsecond=0)


def _next_elongation(obs: A.Observer, key: str, horizon_days: int = 200) -> datetime:
    """求下一次大距（与太阳角距极大）的时刻。"""
    step_h = 24.0
    steps = int(horizon_days * 24 / step_h)
    best_t, best_s = obs.when, -1.0
    for i in range(steps + 1):
        t = obs.when + timedelta(hours=i * step_h)
        jd = A.julian_date(t) - obs.utc_offset / 24.0
        p = A.planet_position(key, jd)
        sun = A.sun_position(jd)
        s = _angular_sep(p["ra"], p["dec"], sun["ra"], sun["dec"])
        if s > best_s:
            best_s, best_t = s, t
    fine_t, fine_s = best_t, best_s
    for k in range(-24, 25):
        t = best_t + timedelta(hours=k * 0.5)
        jd = A.julian_date(t) - obs.utc_offset / 24.0
        p = A.planet_position(key, jd)
        sun = A.sun_position(jd)
        s = _angular_sep(p["ra"], p["dec"], sun["ra"], sun["dec"])
        if s > fine_s:
            fine_s, fine_t = s, t
    return fine_t.replace(minute=0, second=0, microsecond=0)


def _next_annual(now: datetime, month: int, day: int, hour: int = 22) -> datetime:
    """求下一个"每年固定日期"的时刻。"""
    for y in (now.year, now.year + 1):
        try:
            when = datetime(y, month, day, hour, 0)
        except ValueError:
            continue
        if when >= now:
            return when
    return now


def _refine_solar_term(approx: datetime, target_lon: float) -> datetime:
    """在近似日期附近搜索太阳黄经等于目标值的精确时刻。"""
    try:
        best_t, best_d = approx, 999.0
        for i in range(-48, 49):                      # ±48 小时
            t = approx + timedelta(hours=i)
            jd = A.julian_date(t)
            lon = A.sun_position(jd)["lon"]
            d = abs((lon - target_lon + 180.0) % 360.0 - 180.0)
            if d < best_d:
                best_d, best_t = d, t
        return best_t
    except Exception:
        return approx


def time_travel_hint() -> str:
    """给界面用的输入示例说明。"""
    return ("支持：2026-08-12 / 2026-08-12 21:30 / 8月12日 / 今晚 / 明天 22:00 / "
            "3 天后 / +7d / 下次满月 / 火星冲日 / 金星大距 / 英仙座流星雨 / 冬至")


# ==============================================================================
# 七、地平障碍与可见性
# ==============================================================================

@dataclass
class HorizonProfile:
    """观测点的地平障碍剖面。

    azimuths 为方位角（度，0~360，按升序），altitudes 为对应方向上的
    障碍物高度角（度，正数表示被遮挡的高度）。
    """

    azimuths: List[float] = field(default_factory=list)
    altitudes: List[float] = field(default_factory=list)
    note: str = ""

    def add(self, azimuth: float, altitude: float) -> None:
        """加入一个采样点（按方位角排序去重）。"""
        az = norm360(azimuth)
        alt = clamp(float(altitude), 0.0, 89.0)
        for i, a in enumerate(self.azimuths):
            if abs(a - az) < 0.5:
                self.altitudes[i] = alt
                return
        self.azimuths.append(az)
        self.altitudes.append(alt)
        pairs = sorted(zip(self.azimuths, self.altitudes))
        self.azimuths = [p[0] for p in pairs]
        self.altitudes = [p[1] for p in pairs]

    def altitude_at(self, azimuth: float) -> float:
        """某方位角的障碍高度（线性插值，支持 0/360 环绕）。"""
        if not self.azimuths:
            return 0.0
        az = norm360(azimuth)
        n = len(self.azimuths)
        if n == 1:
            return self.altitudes[0]
        for i in range(n):
            a0, a1 = self.azimuths[i], self.azimuths[(i + 1) % n]
            if i == n - 1:
                a1 += 360.0
            if a0 <= az <= a1 or (i == n - 1 and az + 360.0 <= a1):
                span = max(1e-6, a1 - a0)
                k = ((az - a0) if az >= a0 else (az + 360.0 - a0)) / span
                return self.altitudes[i] + (self.altitudes[(i + 1) % n] - self.altitudes[i]) * k
        return 0.0

    def is_blocked(self, azimuth: float, altitude: float) -> bool:
        """给定方位与高度是否被障碍遮挡。"""
        return altitude < self.altitude_at(azimuth) - 0.01

    def to_dict(self) -> Dict[str, object]:
        return {"azimuths": self.azimuths, "altitudes": self.altitudes, "note": self.note}

    @staticmethod
    def from_dict(data: object) -> "HorizonProfile":
        p = HorizonProfile()
        if isinstance(data, dict):
            az = data.get("azimuths") or []
            al = data.get("altitudes") or []
            try:
                for a, h in zip(az, al):
                    p.add(float(a), float(h))
            except (TypeError, ValueError):
                pass
            p.note = str(data.get("note", ""))
        return p

    def is_empty(self) -> bool:
        return not self.azimuths


def visibility_report(ra: float, dec: float, name: str, observer: A.Observer,
                      profile: Optional[HorizonProfile] = None,
                      step_minutes: float = 30.0) -> VisibilityReport:
    """统计某天体在全年（按 30 分钟步长采样）的可见性。

    同时给出未受地平障碍遮挡的有效小时数与最佳月份。
    """
    report = VisibilityReport(name=name)
    try:
        report.circumpolar = A.is_circumpolar(dec, observer.lat)
        report.never_rises = A.never_rises(dec, observer.lat)
        step = max(5.0, float(step_minutes))
        per_day = int(24 * 60 / step)
        year = observer.when.year
        month_hours: Dict[int, float] = {m: 0.0 for m in range(1, 13)}
        hours_per_sample = step / 60.0
        d0 = datetime(year, 1, 1, 0, 0, 0)
        total_days = 366 if _is_leap(year) else 365
        jd_cache: Dict[int, float] = {}
        for day_index in range(total_days):
            base = d0 + timedelta(days=day_index)
            jd_day = A.julian_date(base)
            for k in range(per_day):
                minutes = k * step
                jd = jd_day + minutes / 1440.0
                lst = A.local_sidereal_time(jd, observer.lon)
                alt = A.equatorial_to_horizontal(ra, dec, lst, observer.lat)[0]
                if alt <= 0.0:
                    continue
                report.total_hours += hours_per_sample
                report.max_altitude = max(report.max_altitude, alt)
                if profile is not None and not profile.is_empty():
                    az = A.equatorial_to_horizontal(ra, dec, lst, observer.lat)[1]
                    if profile.is_blocked(az, alt):
                        continue
                report.clear_hours += hours_per_sample
                month_hours[(base + timedelta(minutes=minutes)).month] += hours_per_sample
        report.monthly_hours = [(f"{m} 月", round(month_hours[m], 1)) for m in range(1, 13)]
        if report.monthly_hours:
            best = max(report.monthly_hours, key=lambda x: x[1])
            report.best_month, report.best_hours = best[0], best[1]
        report.total_hours = round(report.total_hours, 1)
        report.clear_hours = round(report.clear_hours, 1)
        report.max_altitude = round(report.max_altitude, 1)
    except Exception as exc:
        LOG.error("可见性统计失败", exc)
    return report


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


#: 常见障碍预设（供界面一键套用）
HORIZON_PRESETS: Tuple[Tuple[str, Tuple[Tuple[float, float], ...]], ...] = (
    ("开阔地（无遮挡）", ((0.0, 0.0),)),
    ("城市楼群（四周 25°）", ((0.0, 25.0), (90.0, 25.0), (180.0, 25.0), (270.0, 25.0))),
    ("北侧有山（北 30°）", ((0.0, 30.0), (45.0, 18.0), (315.0, 18.0), (180.0, 2.0))),
    ("南侧有楼（南 20°）", ((180.0, 20.0), (135.0, 10.0), (225.0, 10.0), (0.0, 3.0))),
    ("东侧树线（东 15°）", ((90.0, 15.0), (60.0, 8.0), (120.0, 8.0), (270.0, 2.0))),
    ("阳台（单向 40°）", ((180.0, 40.0), (135.0, 32.0), (225.0, 32.0),
                          (90.0, 55.0), (270.0, 55.0), (0.0, 55.0))),
)


def build_horizon_preset(name: str) -> HorizonProfile:
    """按预设名称构建地平剖面。"""
    profile = HorizonProfile(note=name)
    for preset_name, points in HORIZON_PRESETS:
        if preset_name == name:
            for az, alt in points:
                profile.add(az, alt)
            return profile
    return profile


__all__ = [
    "SkyEvent", "TourTarget", "VisibilityReport", "HorizonProfile",
    "MOON_PHASE_NAMES", "METEOR_SHOWERS", "PLANET_CN", "HORIZON_PRESETS",
    "moon_phase_angle", "moon_phase_name_ex", "find_moon_phases", "moon_phase_series",
    "find_planet_events", "find_meteor_showers", "build_calendar", "calendar_text",
    "parse_time_expression", "time_travel_hint",
    "visibility_report", "build_horizon_preset",
]
