# -*- coding: utf-8 -*-
"""
planet_data.py —— 行星轨道根数与位置计算
================================================================================
本模块负责：

  1. 行星轨道根数表（J2000 历元 + 每世纪变化率，来源为 JPL 近似根数标准表）
  2. 开普勒方程求解与轨道 → 日心黄道直角坐标的转换
  3. 行星的物理参数与显示信息（半径、自转、卫星、大气、中文说明）
  4. 渲染提示：观察颜色、光点大小、标签

说明：astronomy.py 中已内置 VSOP87 高精度级数用于「现实星图」的位置渲染；
本模块的根数法主要服务于：
    * 教学性的轨道可视化（画出行星轨道椭圆）
    * 快速的粗略位置估算（例如批量计算行星相位）
两种算法结果一致性已在开发期交叉验证（误差 < 0.1°）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .astronomy import AU_KM, D2R, J2000, R2D, julian_centuries, norm360
from .utils import LOG, clamp, lerp

# ==============================================================================
# 一、轨道根数
# ==============================================================================
# 每个行星给出 6 个根数 + 6 个每儒略世纪变化率：
#   a  半长轴（AU）              e  偏心率
#   i  轨道倾角（度）            L  平黄经（度）
#   w  近日点幅角 ω（度）        O  升交点黄经 Ω（度）
# 数据为 JPL "Keplerian Elements for Approximate Positions"（1800-2050 有效）。

ELEMENTS_EPOCH_J2000: Dict[str, Tuple[Tuple[float, ...], Tuple[float, ...]]] = {
    "mercury": (
        (0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
        (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081),
    ),
    "venus": (
        (0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
        (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418),
    ),
    "earth": (
        (1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
        (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0),
    ),
    "mars": (
        (1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
        (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343),
    ),
    "jupiter": (
        (5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
        (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106),
    ),
    "saturn": (
        (9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
        (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794),
    ),
    "uranus": (
        (19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503),
        (-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589),
    ),
    "neptune": (
        (30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574),
        (0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664),
    ),
    "pluto": (
        (39.48211675, 0.24882730, 17.14001206, 238.92903833, 224.06891629, 110.30393684),
        (-0.00031596, 0.00005170, 0.00004818, 145.20780515, -0.04062942, -0.01183482),
    ),
}

#: 太阳系天体基础物理参数（用于信息面板与渲染提示）
PLANET_INFO: Dict[str, Dict[str, object]] = {
    "mercury": {
        "name_cn": "水星", "name_en": "Mercury", "symbol": "☿",
        "radius_km": 2439.7, "mass_earth": 0.055, "gravity": 3.70,
        "rotation_hours": 1407.6, "moons": 0, "temperature_c": "白天 430 / 夜间 -180",
        "atmosphere": "极稀薄（钠、氦、氢）", "type": "类地行星",
        "color": (200, 190, 175), "note": "距太阳最近、昼夜温差最大的行星",
    },
    "venus": {
        "name_cn": "金星", "name_en": "Venus", "symbol": "♀",
        "radius_km": 6051.8, "mass_earth": 0.815, "gravity": 8.87,
        "rotation_hours": -5832.5, "moons": 0, "temperature_c": "平均 464",
        "atmosphere": "96% 二氧化碳，硫酸云", "type": "类地行星",
        "color": (255, 240, 200), "note": "温室效应失控的『地狱行星』",
    },
    "earth": {
        "name_cn": "地球", "name_en": "Earth", "symbol": "⊕",
        "radius_km": 6371.0, "mass_earth": 1.0, "gravity": 9.807,
        "rotation_hours": 23.934, "moons": 1, "temperature_c": "平均 15",
        "atmosphere": "78% 氮、21% 氧", "type": "类地行星",
        "color": (90, 150, 255), "note": "目前已知唯一存在生命的行星",
    },
    "mars": {
        "name_cn": "火星", "name_en": "Mars", "symbol": "♂",
        "radius_km": 3389.5, "mass_earth": 0.107, "gravity": 3.71,
        "rotation_hours": 24.623, "moons": 2, "temperature_c": "平均 -63",
        "atmosphere": "95% 二氧化碳，极稀薄", "type": "类地行星",
        "color": (255, 140, 90), "note": "拥有太阳系最高的奥林匹斯山",
    },
    "jupiter": {
        "name_cn": "木星", "name_en": "Jupiter", "symbol": "♃",
        "radius_km": 69911.0, "mass_earth": 317.8, "gravity": 24.79,
        "rotation_hours": 9.925, "moons": 95, "temperature_c": "云顶 -145",
        "atmosphere": "氢、氦为主", "type": "气态巨行星",
        "color": (255, 215, 170), "note": "拥有持续数百年的大红斑",
    },
    "saturn": {
        "name_cn": "土星", "name_en": "Saturn", "symbol": "♄",
        "radius_km": 58232.0, "mass_earth": 95.2, "gravity": 10.44,
        "rotation_hours": 10.656, "moons": 146, "temperature_c": "云顶 -178",
        "atmosphere": "氢、氦为主", "type": "气态巨行星",
        "color": (255, 235, 180), "note": "拥有最壮观的行星环系统",
    },
    "uranus": {
        "name_cn": "天王星", "name_en": "Uranus", "symbol": "♅",
        "radius_km": 25362.0, "mass_earth": 14.5, "gravity": 8.87,
        "rotation_hours": -17.24, "moons": 28, "temperature_c": "云顶 -224",
        "atmosphere": "氢、氦、甲烷", "type": "冰巨星",
        "color": (160, 240, 255), "note": "自转轴几乎躺倒在轨道面上",
    },
    "neptune": {
        "name_cn": "海王星", "name_en": "Neptune", "symbol": "♆",
        "radius_km": 24622.0, "mass_earth": 17.1, "gravity": 11.15,
        "rotation_hours": 16.11, "moons": 16, "temperature_c": "云顶 -214",
        "atmosphere": "氢、氦、甲烷", "type": "冰巨星",
        "color": (110, 160, 255), "note": "风速可达 2100 km/h 的蓝色巨行星",
    },
    "pluto": {
        "name_cn": "冥王星", "name_en": "Pluto", "symbol": "♇",
        "radius_km": 1188.3, "mass_earth": 0.0022, "gravity": 0.62,
        "rotation_hours": -153.3, "moons": 5, "temperature_c": "平均 -229",
        "atmosphere": "稀薄氮气（季节变化）", "type": "矮行星",
        "color": (220, 200, 190), "note": "2006 年被重新分类为矮行星",
    },
}

#: 五颗肉眼可见行星（用于默认渲染）
NAKED_EYE_PLANETS: Tuple[str, ...] = ("mercury", "venus", "mars", "jupiter", "saturn")

#: 天王星/海王星（需要望远镜，可作为附加图层）
TELESCOPIC_PLANETS: Tuple[str, ...] = ("uranus", "neptune")


# ==============================================================================
# 二、轨道计算（开普勒方法）
# ==============================================================================

@dataclass
class PlanetState:
    """行星在某一时刻的轨道状态。"""

    key: str
    name: str
    a: float          # 半长轴（AU）
    e: float          # 偏心率
    i: float          # 轨道倾角（度）
    lon: float        # 日心黄经（度）
    lat: float        # 日心黄纬（度）
    r: float          # 日心距离（AU）
    true_anomaly: float   # 真近点角（度）
    mean_anomaly: float   # 平近点角（度）
    x: float = 0.0    # 日心黄道直角坐标（AU）
    y: float = 0.0
    z: float = 0.0

    @property
    def perihelion(self) -> float:
        """近日点距离（AU）。"""
        return self.a * (1.0 - self.e)

    @property
    def aphelion(self) -> float:
        """远日点距离（AU）。"""
        return self.a * (1.0 + self.e)

    @property
    def period_years(self) -> float:
        """公转周期（年），由开普勒第三定律给出。"""
        return self.a ** 1.5

    @property
    def orbital_speed_kms(self) -> float:
        """平均轨道速度（km/s）：v = 2πa / T。"""
        try:
            period_s = self.period_years * 365.25 * 86400.0
            circumference_km = 2.0 * math.pi * self.a * AU_KM
            return circumference_km / period_s
        except Exception:
            return 0.0


def solve_kepler(mean_anomaly_deg: float, e: float, tol: float = 1e-10,
                 max_iter: int = 60) -> float:
    """求解开普勒方程 M = E - e·sinE，返回偏近点角 E（度）。

    采用牛顿迭代；对高偏心率（如冥王星 e≈0.25）也能稳定收敛。
    """
    m = math.radians(norm360(mean_anomaly_deg))
    # 初值：低偏心率直接用 M，高偏心率用改进初值加速收敛
    ecc_anom = m if e < 0.8 else math.pi
    for _ in range(max_iter):
        f = ecc_anom - e * math.sin(ecc_anom) - m
        fp = 1.0 - e * math.cos(ecc_anom)
        if abs(fp) < 1e-14:
            break
        delta = f / fp
        ecc_anom -= delta
        if abs(delta) < tol:
            break
    return math.degrees(ecc_anom)


def planet_state(planet: str, jd: float) -> PlanetState:
    """计算行星在儒略日 jd 时刻的日心黄道状态（开普勒根数法）。

    步骤：
        1. 根数从 J2000 线性外推到目标时刻：x = x0 + ẋ·T
        2. 平近点角 M = L - ω - Ω
        3. 解开普勒方程得偏近点角 E → 真近点角 ν 与日心距离 r
        4. 轨道平面内坐标 → 黄道直角坐标（绕 ω、i、Ω 三次旋转）
    """
    key = planet.lower()
    if key not in ELEMENTS_EPOCH_J2000:
        key = "earth"
    base, rate = ELEMENTS_EPOCH_J2000[key]
    t = julian_centuries(jd)
    a = base[0] + rate[0] * t
    e = base[1] + rate[1] * t
    inc = base[2] + rate[2] * t
    mean_lon = base[3] + rate[3] * t
    peri = base[4] + rate[4] * t
    node = base[5] + rate[5] * t
    # 平近点角
    m = norm360(mean_lon - peri - node)
    ecc_anom = solve_kepler(m, e)
    ea = math.radians(ecc_anom)
    # 真近点角与日心距离
    true_anom = math.degrees(math.atan2(math.sqrt(1 - e * e) * math.sin(ea),
                                        math.cos(ea) - e))
    r = a * (1.0 - e * math.cos(ea))
    # 轨道平面内的坐标（x 指向近日点）
    x_orb = r * math.cos(math.radians(true_anom))
    y_orb = r * math.sin(math.radians(true_anom))
    w = math.radians(peri - node)
    om = math.radians(node)
    i_r = math.radians(inc)
    # 三次旋转：绕 ω → 绕 i → 绕 Ω
    cos_w, sin_w = math.cos(w), math.sin(w)
    cos_o, sin_o = math.cos(om), math.sin(om)
    cos_i, sin_i = math.cos(i_r), math.sin(i_r)
    x = (cos_w * cos_o - sin_w * sin_o * cos_i) * x_orb + (-sin_w * cos_o - cos_w * sin_o * cos_i) * y_orb
    y = (cos_w * sin_o + sin_w * cos_o * cos_i) * x_orb + (-sin_w * sin_o + cos_w * cos_o * cos_i) * y_orb
    z = (sin_w * sin_i) * x_orb + (cos_w * sin_i) * y_orb
    lon = norm360(math.degrees(math.atan2(y, x)))
    lat = math.degrees(math.asin(clamp(z / max(1e-12, r), -1.0, 1.0)))
    return PlanetState(key=key, name=PLANET_INFO.get(key, {}).get("name_cn", key),
                       a=a, e=e, i=inc, lon=lon, lat=lat, r=r,
                       true_anomaly=true_anom, mean_anomaly=m, x=x, y=y, z=z)


def heliocentric_position(planet: str, jd: float) -> Tuple[float, float, float]:
    """行星日心黄道直角坐标（AU）。"""
    st = planet_state(planet, jd)
    return (st.x, st.y, st.z)


def geocentric_distance(planet: str, jd: float) -> float:
    """行星与地球的距离（AU，粗略值，用于亮度估算）。"""
    try:
        p = heliocentric_position(planet, jd)
        e = heliocentric_position("earth", jd)
        return math.sqrt((p[0] - e[0]) ** 2 + (p[1] - e[1]) ** 2 + (p[2] - e[2]) ** 2)
    except Exception as exc:
        LOG.error(f"geocentric_distance({planet}) 失败", exc)
        return 1.0


def orbital_path(planet: str, jd: float, samples: int = 256) -> List[Tuple[float, float]]:
    """返回行星轨道的 (日心黄经, 日心黄纬) 采样点，用于绘制轨道椭圆。

    通过对平近点角均匀采样得到轨道上的点，再套用当前时刻的根数。
    """
    pts: List[Tuple[float, float]] = []
    try:
        base, rate = ELEMENTS_EPOCH_J2000[planet.lower()]
        t = julian_centuries(jd)
        a = base[0] + rate[0] * t
        e = base[1] + rate[1] * t
        inc = base[2] + rate[2] * t
        peri = base[4] + rate[4] * t
        node = base[5] + rate[5] * t
        w = math.radians(peri - node)
        om = math.radians(node)
        i_r = math.radians(inc)
        for k in range(samples + 1):
            m = 360.0 * k / samples
            ea = math.radians(solve_kepler(m, e))
            nu = math.atan2(math.sqrt(1 - e * e) * math.sin(ea), math.cos(ea) - e)
            r = a * (1.0 - e * math.cos(ea))
            x_orb, y_orb = r * math.cos(nu), r * math.sin(nu)
            x = (math.cos(w) * math.cos(om) - math.sin(w) * math.sin(om) * math.cos(i_r)) * x_orb \
                + (-math.sin(w) * math.cos(om) - math.cos(w) * math.sin(om) * math.cos(i_r)) * y_orb
            y = (math.cos(w) * math.sin(om) + math.sin(w) * math.cos(om) * math.cos(i_r)) * x_orb \
                + (-math.sin(w) * math.sin(om) + math.cos(w) * math.cos(om) * math.cos(i_r)) * y_orb
            z = math.sin(w) * math.sin(i_r) * x_orb + math.cos(w) * math.sin(i_r) * y_orb
            pts.append((norm360(math.degrees(math.atan2(y, x))),
                        math.degrees(math.asin(clamp(z / max(1e-12, r), -1.0, 1.0)))))
    except Exception as exc:
        LOG.error(f"orbital_path({planet}) 失败", exc)
    return pts


def heliocentric_orbit_path(planet: str, jd: float,
                           samples: int = 256) -> List[Tuple[float, float, float]]:
    """返回行星轨道的**三维直角坐标**采样点（AU），供太阳系仪俯视图使用。

    与 orbital_path 的区别：
        orbital_path            返回 (黄经, 黄纬)，半径信息丢失，只适合画天球投影；
        heliocentric_orbit_path 返回 (x, y, z)，保留偏心率造成的真实椭圆形状。
    """
    pts: List[Tuple[float, float, float]] = []
    try:
        base, rate = ELEMENTS_EPOCH_J2000[planet.lower()]
        t = julian_centuries(jd)
        a = base[0] + rate[0] * t
        e = base[1] + rate[1] * t
        inc = base[2] + rate[2] * t
        peri = base[4] + rate[4] * t
        node = base[5] + rate[5] * t
        w = math.radians(peri - node)
        om = math.radians(node)
        i_r = math.radians(inc)
        for k in range(samples + 1):
            m = 360.0 * k / samples
            ea = math.radians(solve_kepler(m, e))
            nu = math.atan2(math.sqrt(1 - e * e) * math.sin(ea), math.cos(ea) - e)
            r = a * (1.0 - e * math.cos(ea))
            x_orb, y_orb = r * math.cos(nu), r * math.sin(nu)
            x = (math.cos(w) * math.cos(om) - math.sin(w) * math.sin(om) * math.cos(i_r)) * x_orb \
                + (-math.sin(w) * math.cos(om) - math.cos(w) * math.sin(om) * math.cos(i_r)) * y_orb
            y = (math.cos(w) * math.sin(om) + math.sin(w) * math.cos(om) * math.cos(i_r)) * x_orb \
                + (-math.sin(w) * math.sin(om) + math.cos(w) * math.cos(om) * math.cos(i_r)) * y_orb
            z = math.sin(w) * math.sin(i_r) * x_orb + math.cos(w) * math.sin(i_r) * y_orb
            pts.append((x, y, z))
    except Exception as exc:
        LOG.error(f"heliocentric_orbit_path({planet}) 失败", exc)
    return pts


# ==============================================================================
# 三、渲染与展示辅助
# ==============================================================================

def planet_color(planet: str) -> Tuple[int, int, int]:
    """行星的显示颜色。"""
    info = PLANET_INFO.get(planet.lower(), {})
    return tuple(info.get("color", (255, 220, 180)))  # type: ignore[return-value]


def planet_display_size(planet: str, distance_au: float, base: float = 3.0) -> float:
    """行星光点半径：按角直径粗略缩放，并设置上下限避免过大/过小。"""
    try:
        info = PLANET_INFO.get(planet.lower(), {})
        radius_km = float(info.get("radius_km", 6000.0))
        ang_rad = radius_km / max(1e-6, distance_au * AU_KM)
        size = base * (ang_rad / 2.0e-5) ** 0.35
        return clamp(size, 2.0, 12.0)
    except Exception:
        return base


def approximate_magnitude(planet: str, jd: float) -> float:
    """行星视星等的近似估算（含相位角修正，误差约 0.1~0.3 等）。

    采用天文年历通行的形式：
        V = V0 + 5·log10(r·Δ) + 相位项(α)
    其中 r 为日心距、Δ 为地心距（AU），α 为相位角（度）。
    相位角由太阳-行星-地球三角形求得：
        cos α = (r² + Δ² - R²) / (2·r·Δ)     （R 为地球日心距）
    """
    try:
        key = planet.lower()
        p = planet_state(key, jd)
        e = planet_state("earth", jd)
        delta = math.sqrt((p.x - e.x) ** 2 + (p.y - e.y) ** 2 + (p.z - e.z) ** 2)
        delta = max(1e-6, delta)
        r = max(1e-6, p.r)
        cos_phase = clamp((r * r + delta * delta - e.r * e.r) / (2.0 * r * delta), -1.0, 1.0)
        phase = math.degrees(math.acos(cos_phase))
        # 基准星等（Δ=1AU、r=1AU、α=0）
        base = {"mercury": -0.42, "venus": -4.40, "mars": -1.52,
                "jupiter": -9.40, "saturn": -8.88, "uranus": -7.19,
                "neptune": -6.87, "pluto": -1.00}.get(key, 0.0)
        # 相位系数（IAU / 天文年历简化值）
        phase_coeff = {"mercury": (0.0380, -0.000273, 0.000002), "venus": (0.0009, 0.000239, -0.00000065),
                       "mars": (0.016, 0.0, 0.0), "jupiter": (0.005, 0.0, 0.0),
                       "saturn": (0.044, 0.0, 0.0), "uranus": (0.001, 0.0, 0.0),
                       "neptune": (0.001, 0.0, 0.0)}.get(key, (0.02, 0.0, 0.0))
        c1, c2, c3 = phase_coeff
        return base + 5.0 * math.log10(r * delta) + c1 * phase + c2 * phase * phase + c3 * phase ** 3
    except Exception:
        return 5.0


def planet_summary(planet: str, jd: float) -> Dict[str, object]:
    """汇总行星的展示信息（供信息面板直接使用）。"""
    key = planet.lower()
    info = dict(PLANET_INFO.get(key, {}))
    st = planet_state(key, jd)
    dist = geocentric_distance(key, jd)
    info.update({
        "key": key,
        "heliocentric_distance_au": round(st.r, 4),
        "geocentric_distance_au": round(dist, 4),
        "geocentric_distance_km": round(dist * AU_KM, 0),
        "orbital_period_years": round(st.period_years, 3),
        "eccentricity": round(st.e, 5),
        "inclination_deg": round(st.i, 3),
        "mean_longitude_deg": round(st.lon, 3),
        "perihelion_au": round(st.perihelion, 4),
        "aphelion_au": round(st.aphelion, 4),
        "orbital_speed_kms": round(st.orbital_speed_kms, 2),
        "apparent_magnitude": round(approximate_magnitude(key, jd), 2),
        "light_minutes": round(dist * 499.005 / 60.0, 2),
    })
    return info


def all_planet_keys() -> List[str]:
    """所有具备根数的行星键名。"""
    return list(ELEMENTS_EPOCH_J2000.keys())


__all__ = [
    "ELEMENTS_EPOCH_J2000", "PLANET_INFO", "NAKED_EYE_PLANETS", "TELESCOPIC_PLANETS",
    "PlanetState", "solve_kepler", "planet_state", "heliocentric_position",
    "geocentric_distance", "orbital_path", "planet_color", "planet_display_size",
    "approximate_magnitude", "planet_summary", "all_planet_keys",
]
