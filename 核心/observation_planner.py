# -*- coding: utf-8 -*-
"""
observation_planner.py —— 观测计划表生成器
================================================================================
回答一个非常具体的问题：**今晚（或指定某夜）几点、在什么高度、能拍多久？**

输入：
    · 观测点（经纬度 + 时区）
    · 波特尔暗空等级（决定极限星等，进而决定"看不看得见"）
    · 地平障碍剖面（决定"会不会被楼/山挡住"）
    · 望远镜口径（可选，决定深空天体能拍到多暗）

输出：ObsPlan
    · 观测窗口（天文暮光结束 → 天文晨光开始）
    · 每个目标：升起/落下时刻、最佳时刻、最佳高度、方位、连续可拍时长、评分
    · 按评分排序的清单 + 可导出为文本/Markdown 计划表

"能拍多久"的判定：把观测窗口按 5 分钟步长采样，对每个目标连续统计
"高度 ≥ 最低可用高度 **且** 未被地平障碍遮挡"的时间段，取最长的一段。
深空天体额外要求"太阳高度 < -15°"（真的黑下来）与"月球不构成致命干扰"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import astronomy as A
from . import light_pollution as LP
from . import planet_data as P
from . import sky_events as SE
from . import star_data as SD
from .utils import LOG, azimuth_to_compass, clamp, format_datetime, norm360

# ==============================================================================
# 数据结构
# ==============================================================================

@dataclass
class PlanTarget:
    """计划表里的一个观测目标。"""

    key: str
    name: str
    kind: str                       # planet / moon / messier / star
    mag: float
    detail: str = ""                # 类型描述（如"旋涡星系"）
    # 时间
    best_time: Optional[datetime] = None
    best_alt: float = -90.0
    best_az: float = 0.0
    rise_time: Optional[datetime] = None
    set_time: Optional[datetime] = None
    start_time: Optional[datetime] = None      # 可拍窗口起点
    end_time: Optional[datetime] = None        # 可拍窗口终点
    window_minutes: float = 0.0                # 连续可拍时长（分钟）
    # 评分与理由
    score: float = 0.0
    visible: bool = False
    reasons: List[str] = field(default_factory=list)
    notes: str = ""
    alt_curve: List[Tuple[datetime, float]] = field(default_factory=list)

    def window_text(self) -> str:
        """可拍窗口的文字描述。"""
        if not self.visible or self.start_time is None or self.end_time is None:
            return "—"
        return (f"{self.start_time.strftime('%H:%M')}–"
                f"{self.end_time.strftime('%H:%M')}")

    def duration_text(self) -> str:
        m = self.window_minutes
        if m <= 0:
            return "—"
        if m >= 90:
            return f"{m / 60:.1f} 小时"
        return f"{m:.0f} 分钟"

    def best_text(self) -> str:
        if self.best_time is None:
            return "—"
        return (f"{self.best_time.strftime('%H:%M')}　{self.best_alt:+.0f}°　"
                f"{azimuth_to_compass(self.best_az)}")


@dataclass
class ObsPlan:
    """一整夜的观测计划。"""

    observer: A.Observer
    bortle: int = 5
    aperture_mm: float = 0.0
    twilight_evening: Optional[datetime] = None      # 天文暮光结束
    twilight_morning: Optional[datetime] = None      # 天文晨光开始
    moon_phase: float = 0.0
    moon_illum: float = 0.0
    moon_rise: Optional[datetime] = None
    moon_set: Optional[datetime] = None
    limiting_mag: float = 6.0
    targets: List[PlanTarget] = field(default_factory=list)
    weather_note: str = ""

    @property
    def night_minutes(self) -> float:
        """真正黑暗的时长（分钟）。"""
        if not (self.twilight_evening and self.twilight_morning):
            return 0.0
        return max(0.0, (self.twilight_morning - self.twilight_evening).total_seconds() / 60.0)

    def visible_targets(self) -> List[PlanTarget]:
        return [t for t in self.targets if t.visible and t.window_minutes > 0]

    def top(self, count: int = 12) -> List[PlanTarget]:
        return self.visible_targets()[: max(1, count)]

    def summary_text(self) -> str:
        lines = [
            f"观测计划　{self.observer.location_name}　"
            f"{self.observer.when.strftime('%Y-%m-%d')}",
            f"暗空等级 Bortle {self.bortle}（{LP.bortle_class(self.bortle).name}）　"
            f"肉眼极限 {self.limiting_mag:.1f} 等"
            + (f"　望远镜 {self.aperture_mm:.0f}mm" if self.aperture_mm else ""),
        ]
        if self.twilight_evening and self.twilight_morning:
            lines.append(f"天文暗夜：{self.twilight_evening.strftime('%H:%M')} – "
                         f"{self.twilight_morning.strftime('%H:%M')}"
                         f"（{self.night_minutes / 60:.1f} 小时）")
        lines.append(f"月相：照亮 {self.moon_illum * 100:.0f}%"
                     + (f"　月落 {self.moon_set.strftime('%H:%M')}"
                        if self.moon_set else "")
                     + (f"　月出 {self.moon_rise.strftime('%H:%M')}"
                        if self.moon_rise else ""))
        lines.append(f"可用目标 {len(self.visible_targets())} 个 / 共评估 {len(self.targets)} 个")
        return "\n".join(lines)

    def table_text(self, limit: int = 40, markdown: bool = False) -> str:
        """生成可复制的计划表（文本或 Markdown）。"""
        rows = self.visible_targets()[:limit]
        if markdown:
            out = ["| # | 目标 | 类型 | 星等 | 最佳时刻 | 最佳高度 | 可拍窗口 | 时长 | 评分 |",
                   "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
            for i, t in enumerate(rows, 1):
                out.append(f"| {i} | {t.name} | {t.detail or t.kind} | {t.mag:+.1f} | "
                           f"{t.best_time.strftime('%H:%M') if t.best_time else '—'} | "
                           f"{t.best_alt:+.0f}° | {t.window_text()} | "
                           f"{t.duration_text()} | {t.score:.2f} |")
            return "\n".join(out)
        width = 78
        out = [self.summary_text(), "=" * width,
               f"{'#':>2}  {'目标':14s} {'类型':10s} {'星等':>6s}  "
               f"{'最佳':>5s} {'高度':>6s} {'可拍窗口':13s} {'时长':>8s} {'评分':>5s}",
               "-" * width]
        for i, t in enumerate(rows, 1):
            best = t.best_time.strftime("%H:%M") if t.best_time else "—"
            out.append(f"{i:>2}  {t.name[:14]:14s} {(t.detail or t.kind)[:10]:10s} "
                       f"{t.mag:+6.1f}  {best:>5s} {t.best_alt:+5.0f}° "
                       f"{t.window_text():13s} {t.duration_text():>8s} {t.score:5.2f}")
        return "\n".join(out)

    def timeline_text(self) -> str:
        """按时间排序的"今晚怎么安排"，便于照着做。"""
        rows = sorted(self.visible_targets(), key=lambda t: t.start_time or self.observer.when)
        if not rows:
            return "今晚没有满足条件的目标（可能月光过强或全被地形遮挡）。"
        lines = ["今晚安排（按可开始观测的时间排序）", "-" * 60]
        for t in rows[:24]:
            lines.append(
                f"{t.start_time.strftime('%H:%M') if t.start_time else '--:--'} 起　"
                f"{t.name}（{(t.detail or t.kind)}，{t.mag:+.1f} 等）　"
                f"最佳 {t.best_text()}　可拍 {t.duration_text()}")
            if t.reasons:
                lines.append(f"          {'；'.join(t.reasons[:3])}")
        return "\n".join(lines)


# ==============================================================================
# 计算：天文暮光与月出月落
# ==============================================================================

def find_twilight(observer: A.Observer, sun_alt: float = -18.0,
                  step_minutes: float = 5.0) -> Tuple[Optional[datetime], Optional[datetime]]:
    """求当夜的「天文暮光结束」与「天文晨光开始」时刻。

    做法：从当天 12:00 起采样 24 小时，找太阳高度穿越 -18° 的两个时刻
    （下降沿 = 暮光结束，上升沿 = 晨光开始）。
    """
    try:
        base = observer.when.replace(hour=12, minute=0, second=0, microsecond=0)
        samples: List[Tuple[datetime, float]] = []
        steps = int(24 * 60 / step_minutes)
        for i in range(steps + 1):
            t = base + timedelta(minutes=i * step_minutes)
            jd = A.julian_date(t) - observer.utc_offset / 24.0
            sun = A.sun_position(jd)
            # 必须用该时刻的恒星时（Observer.altitude 用的是创建时刻的 LST）
            lst = A.local_sidereal_time(jd, observer.lon)
            alt = A.equatorial_to_horizontal(sun["ra"], sun["dec"], lst, observer.lat)[0]
            samples.append((t, alt))
        evening = morning = None
        for i in range(1, len(samples)):
            t0, a0 = samples[i - 1]
            t1, a1 = samples[i]
            if a0 > sun_alt >= a1 and evening is None:          # 下降沿
                evening = t0 + timedelta(minutes=step_minutes * (a0 - sun_alt) /
                                         max(1e-6, a0 - a1))
            elif a0 <= sun_alt < a1 and evening is not None and morning is None:
                morning = t0 + timedelta(minutes=step_minutes * (sun_alt - a0) /
                                         max(1e-6, a1 - a0))
        return (evening, morning)
    except Exception as exc:
        LOG.error("天文暮光计算失败", exc)
        return (None, None)


def moon_rise_set(observer: A.Observer, step_minutes: float = 5.0
                  ) -> Tuple[Optional[datetime], Optional[datetime]]:
    """求当晚的月出与月落时刻（可能为空：整夜在地平线上/下）。"""
    try:
        base = observer.when.replace(hour=12, minute=0, second=0, microsecond=0)
        steps = int(24 * 60 / step_minutes)
        prev = None
        rise = sett = None
        for i in range(steps + 1):
            t = base + timedelta(minutes=i * step_minutes)
            jd = A.julian_date(t) - observer.utc_offset / 24.0
            moon = A.moon_position(jd)
            lst = A.local_sidereal_time(jd, observer.lon)
            alt = A.equatorial_to_horizontal(moon["ra"], moon["dec"], lst, observer.lat)[0]
            if prev is not None:
                tp, ap = prev
                if ap <= 0.0 < alt and rise is None:
                    rise = tp + timedelta(minutes=step_minutes * (-ap) / max(1e-6, alt - ap))
                elif ap > 0.0 >= alt and sett is None:
                    sett = tp + timedelta(minutes=step_minutes * ap / max(1e-6, ap - alt))
            prev = (t, alt)
        return (rise, sett)
    except Exception as exc:
        LOG.warn(f"月出月落计算失败：{exc}")
        return (None, None)


# ==============================================================================
# 计划生成
# ==============================================================================

#: 各类目标的最低可用高度（度）：低于这个高度大气消光与抖动使观测价值骤降
MIN_ALTITUDE: Dict[str, float] = {
    "planet": 12.0,
    "moon": 8.0,
    "messier": 25.0,      # 深空天体需要足够高才能避开光害与大气
    "star": 20.0,
}

#: 深空天体的"需要多暗"阈值：太阳高度低于此值才算真的黑
DARK_SUN_ALT = -15.0


class ObservationPlanner:
    """观测计划生成器。"""

    def __init__(self, observer: A.Observer, bortle: int = 5,
                 horizon: Optional[SE.HorizonProfile] = None,
                 aperture_mm: float = 0.0, step_minutes: float = 5.0,
                 max_targets: int = 260) -> None:
        self.observer = observer
        self.bortle = int(clamp(bortle, 1, 9))
        self.horizon = horizon
        self.aperture_mm = float(aperture_mm or 0.0)
        self.step = max(2.0, float(step_minutes))
        self.max_targets = int(max_targets)

    # ---------------------------------------------------------------- 主入口

    def build(self, when: Optional[datetime] = None,
              include_deep_sky: bool = True,
              include_stars: bool = True,
              progress=None) -> ObsPlan:
        """生成一整夜的观测计划。"""
        obs = self.observer
        if when is not None:
            obs = A.Observer(lat=obs.lat, lon=obs.lon, location_name=obs.location_name,
                             utc_offset=obs.utc_offset,
                             when=when.replace(hour=20, minute=0, second=0, microsecond=0))
        plan = ObsPlan(observer=obs, bortle=self.bortle, aperture_mm=self.aperture_mm)

        # ---- 观测窗口 ----
        plan.twilight_evening, plan.twilight_morning = find_twilight(obs)
        plan.moon_rise, plan.moon_set = moon_rise_set(obs)
        moon = A.moon_position(obs.jd_ut)
        plan.moon_phase = float(moon["elongation"])
        plan.moon_illum = float(moon["phase"])

        # ---- 极限星等（用夜半时刻的月亮高度估计） ----
        mid = plan.twilight_evening or obs.when
        if plan.twilight_evening and plan.twilight_morning:
            mid = plan.twilight_evening + (plan.twilight_morning - plan.twilight_evening) / 2
        mid_jd = A.julian_date(mid) - obs.utc_offset / 24.0
        moon_mid = A.moon_position(mid_jd)
        moon_alt_mid = A.equatorial_to_horizontal(
            moon_mid["ra"], moon_mid["dec"],
            A.local_sidereal_time(mid_jd, obs.lon), obs.lat)[0]
        plan.limiting_mag = LP.limiting_magnitude(
            self.bortle, plan.moon_illum, 60.0, 0.0, moon_alt_mid)

        # ---- 候选目标 ----
        candidates = self._collect_candidates(plan, include_deep_sky, include_stars)
        total = len(candidates)
        for i, (key, name, kind, mag, detail, ra, dec) in enumerate(candidates):
            target = self._analyze_target(obs, plan, key, name, kind, mag, detail, ra, dec)
            if target is not None:
                plan.targets.append(target)
            if progress is not None and (i % 20 == 0 or i == total - 1):
                progress(i + 1, total)

        plan.targets.sort(key=lambda t: (-t.score, -t.window_minutes))
        plan.weather_note = self._advice(plan)
        return plan

    def _collect_candidates(self, plan: ObsPlan, include_deep_sky: bool, include_stars: bool
                            ) -> List[Tuple[str, str, str, float, str, float, float]]:
        """收集候选目标：行星 / 月球 / 深空天体 / 亮星。"""
        out: List[Tuple[str, str, str, float, str, float, float]] = []
        jd = self.observer.jd_ut
        # 行星（注意排除地球：我们站在地球上，不能把地球当观测目标）
        for key in P.all_planet_keys():
            if key == "earth":
                continue
            try:
                info = P.PLANET_INFO.get(key, {})
                st = P.planet_state(key, jd)
                # 必须用地心坐标 + 实际视星等；用日心黄经会把位置算错，
                # 且 planet_position 返回的字典里没有 mag 字段（早期版本全成了 +0.0）
                p = A.planet_position(key, jd)
                try:
                    mag = float(P.approximate_magnitude(key, jd))
                except Exception:
                    mag = 0.0
                out.append((f"planet:{key}", info.get("name_cn", key) or key.title(),
                            "planet", mag, info.get("type", "行星"),
                            float(p["ra"]), float(p["dec"])))
            except Exception as exc:
                LOG.warn(f"行星候选取值失败（{key}）：{exc}")
        # 月球
        moon = A.moon_position(jd)
        out.append(("moon", "月球", "moon", -12.7,
                    SE.moon_phase_name_ex(moon["elongation"]), moon["ra"], moon["dec"]))
        # 深空天体：按"当前光害 + 月光 + 口径"能看到的极限星等筛选
        if include_deep_sky:
            scope_limit = LP.limiting_magnitude(self.bortle, plan.moon_illum, 60.0,
                                                self.aperture_mm, 60.0)
            for m in SD.DEEP_SKY_CATALOG:
                if m.mag > scope_limit:
                    continue
                out.append((f"messier:{m.name}", f"{m.name} {m.name_cn}", "messier",
                            float(m.mag), str(getattr(m, "mtype", "")),
                            float(m.ra), float(m.dec)))
        # 亮星：肉眼/双筒可及的范围
        if include_stars:
            star_limit = max(plan.limiting_mag, 4.0)
            named = [s for s in SD.STAR_CATALOG if s.name and not s.name.startswith("HIP")]
            named.sort(key=lambda s: s.mag)
            for s in named[:60]:
                if s.mag > star_limit + 1.0:
                    continue
                out.append((f"star:{s.hip}", s.display_name(), "star", float(s.mag),
                            s.spectral or "恒星", float(s.ra), float(s.dec)))
        return out[: self.max_targets]

    # ---------------------------------------------------------------- 单目标分析

    def _analyze_target(self, obs: A.Observer, plan: ObsPlan, key: str, name: str,
                        kind: str, mag: float, detail: str, ra: float, dec: float
                        ) -> Optional[PlanTarget]:
        """计算单个目标在观测窗口内的高度曲线、可拍窗口与评分。"""
        t = PlanTarget(key=key, name=name, kind=kind, mag=mag, detail=detail)
        start = plan.twilight_evening or obs.when
        end = plan.twilight_morning or (start + timedelta(hours=8))
        # 观测窗口（不含暮光）
        steps = max(2, int((end - start).total_seconds() / 60.0 / self.step))
        min_alt = MIN_ALTITUDE.get(kind, 20.0)
        # 深空天体需要更严的月光条件
        dso_penalty = 1.0
        if kind == "messier":
            dso_penalty = 1.0 - clamp(plan.moon_illum * 0.6, 0.0, 0.6)

        best = (-90.0, None, 0.0)
        run_start: Optional[datetime] = None
        runs: List[Tuple[datetime, datetime, float]] = []
        for i in range(steps + 1):
            tnow = start + timedelta(minutes=i * self.step)
            jd = A.julian_date(tnow) - obs.utc_offset / 24.0
            lst = A.local_sidereal_time(jd, obs.lon)
            alt, az = A.equatorial_to_horizontal(ra, dec, lst, obs.lat)
            t.alt_curve.append((tnow, alt))
            if alt > best[0]:
                best = (alt, tnow, az)
            # 可用条件：高度够、没被地形挡、天够黑、月亮别太亮
            ok = alt >= min_alt
            if ok and self.horizon is not None and not self.horizon.is_empty():
                ok = not self.horizon.is_blocked(az, alt)
            if ok and kind == "messier":
                sun = A.sun_position(jd)
                sun_alt = A.equatorial_to_horizontal(sun["ra"], sun["dec"], lst, obs.lat)[0]
                if sun_alt > DARK_SUN_ALT:
                    ok = False
                # 月亮在天上且目标离月亮太近 → 不划算
                moon_now = A.moon_position(jd)
                moon_alt = A.equatorial_to_horizontal(
                    moon_now["ra"], moon_now["dec"], lst, obs.lat)[0]
                if moon_alt > 0 and plan.moon_illum > 0.5:
                    sep = _sep(ra, dec, moon_now["ra"], moon_now["dec"])
                    if sep < 35.0:
                        ok = False
            if ok:
                if run_start is None:
                    run_start = tnow
            else:
                if run_start is not None:
                    runs.append((run_start, tnow, (tnow - run_start).total_seconds() / 60.0))
                    run_start = None
        if run_start is not None:
            runs.append((run_start, end, (end - run_start).total_seconds() / 60.0))

        t.best_alt, t.best_time, t.best_az = best[0], best[1], best[2]
        if runs:
            best_run = max(runs, key=lambda r: r[2])
            t.start_time, t.end_time, t.window_minutes = best_run[0], best_run[1], best_run[2]
            t.visible = t.window_minutes >= 20.0        # 少于 20 分钟不值得开工
        # 可见但窗口太短的，也给出信息但标为不可用
        if not t.visible and best[0] > min_alt:
            t.notes = "可观测窗口过短（< 20 分钟）"

        # ---- 评分 ----
        if t.visible:
            # 用 96° 而不是 45° 做高度归一化——否则常见目标都顶到满分，
            # 排序会退化成"人人 0.86"（早期版本的实际问题）
            alt_score = clamp((t.best_alt - min_alt) / 96.0, 0.0, 1.0)
            # 时长用对数压缩并留出整夜量程，保证"整夜可拍"仍有区分度
            dur_score = clamp(
                math.log10(1.0 + t.window_minutes / 30.0) / math.log10(21.0), 0.0, 1.0)
            mag_score = clamp((12.0 - mag) / 10.0, 0.0, 1.0)
            # 类型权重：深空天体与行星更有看点；月球在满月时反而是干扰源
            kind_w = {"planet": 0.85, "moon": 0.5, "messier": 1.0, "star": 0.40}.get(kind, 0.5)
            if kind == "moon":
                # 月亮最好看的时候是有明暗交界的上/下弦
                kind_w = 0.9 if 0.15 <= plan.moon_illum <= 0.75 else 0.35
            # 加权合成：高度、时长、亮度任一很差都会明显拉低总分
            base = 0.45 * alt_score + 0.30 * dur_score + 0.25 * mag_score
            t.score = clamp(base * (0.55 + 0.45 * kind_w) * dso_penalty, 0.0, 1.0)
            # 理由
            if t.best_alt >= 60:
                t.reasons.append("接近天顶，大气影响最小")
            elif t.best_alt >= 40:
                t.reasons.append("高度合适")
            else:
                t.reasons.append("高度偏低，建议抓最佳时段")
            if t.window_minutes >= 180:
                t.reasons.append("整夜可拍，适合长曝光或多目标连拍")
            elif t.window_minutes >= 60:
                t.reasons.append("有一小时以上连续窗口")
            if kind == "messier" and plan.moon_illum > 0.4:
                t.reasons.append(f"月光影响（照亮 {plan.moon_illum * 100:.0f}%），建议加窄带滤镜")
            if mag > plan.limiting_mag and kind == "star":
                t.reasons.append("暗于肉眼极限，需双筒/望远镜")
        return t

    def _advice(self, plan: ObsPlan) -> str:
        """给一句整体建议。"""
        tips: List[str] = []
        info = LP.bortle_class(self.bortle)
        tips.append(f"{info.label}：{info.milky_way}")
        if plan.moon_illum > 0.7:
            tips.append("月光很强，优先观测行星/双星/月球本身，深空天体改天再拍")
        elif plan.moon_illum < 0.2:
            tips.append("月光很弱，是拍星云与星系的好时机")
        if plan.moon_set and plan.twilight_evening and plan.moon_set > plan.twilight_evening:
            tips.append(f"月落 {plan.moon_set.strftime('%H:%M')} 之后才是真正的深空时段")
        if self.horizon is not None and not self.horizon.is_empty():
            avg = sum(self.horizon.altitudes) / max(1, len(self.horizon.azimuths))
            tips.append(f"已计入地平障碍（平均遮挡 {avg:.0f}°），"
                        f"计划表里的目标都已避开被挡方位")
        return "；".join(tips)


def _sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """两点角距（度）。"""
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dl = math.radians(ra1 - ra2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dl)
    return math.degrees(math.acos(clamp(c, -1.0, 1.0)))


def quick_plan(observer: A.Observer, bortle: int = 5,
               horizon: Optional[SE.HorizonProfile] = None,
               aperture_mm: float = 0.0) -> ObsPlan:
    """便捷入口：直接生成一份计划。"""
    return ObservationPlanner(observer, bortle, horizon, aperture_mm).build()


__all__ = [
    "PlanTarget", "ObsPlan", "ObservationPlanner", "MIN_ALTITUDE",
    "find_twilight", "moon_rise_set", "quick_plan",
]
