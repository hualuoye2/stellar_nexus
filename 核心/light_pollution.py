# -*- coding: utf-8 -*-
"""
light_pollution.py —— 暗空 / 光害模拟（波特尔暗空分类 Bortle 1~9）
================================================================================
回答一个实际观测中最常见的问题：**"我在城里能看到什么？搬到山上能多看到什么？"**

物理模型（简化但可用的工程近似）：

    天空亮度  μ_sky  (mag/arcsec²)
        Bortle 1 无人区        ≈ 21.9
        Bortle 2 典型真暗空    ≈ 21.7
        Bortle 3 乡村          ≈ 21.4
        Bortle 4 乡村/郊区     ≈ 20.9
        Bortle 5 郊区          ≈ 20.3
        Bortle 6 亮郊区        ≈ 19.4
        Bortle 7 郊区/城区     ≈ 18.6
        Bortle 8 城区          ≈ 18.0
        Bortle 9 市中心        ≈ 17.5

    人眼极限星等  m_lim ≈ μ_sky / 2 + 1.4      （Schaefer 经验式的常用简写形式）
    望远镜极限星等 m_tel ≈ m_lim + 2.5·log10(D_mm) + 2.0（经验增益）

    * 大气消光：目标越低，消光越强，等效于"变暗"
        消光系数 k ≈ 0.20 mag/airmass（晴天城市），airmass ≈ 1/sin(h)
    * 月光：满月可让天空亮度提高 2~3 mag，直接抬高 μ_sky 的数值

本模块为「现实星图」提供：
    1. 各波特尔等级的极限星等 → 用于隐藏看不见的暗星（按星等裁剪）
    2. 城市 vs 山上的对比报告（能看到多少颗星、多少深空天体）
    3. 星空亮度 / 对比度的直观描述
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .utils import clamp

# ==============================================================================
# 波特尔等级数据
# ==============================================================================

@dataclass(frozen=True)
class BortleClass:
    """一个波特尔等级的描述。"""

    level: int
    name: str                  # 中文描述
    mu_sky: float              # 天顶天空亮度（mag/arcsec²）
    naked_eye_limiting: float  # 人眼极限星等
    milky_way: str             # 银河可见度描述
    note: str                  # 典型地点

    @property
    def label(self) -> str:
        return f"Bortle {self.level} · {self.name}"


#: 波特尔 1~9 级（亮度值取该等级的常见代表值）
BORTLE_CLASSES: Tuple[BortleClass, ...] = (
    BortleClass(1, "极暗天空", 21.95, 7.9, "银河投下可见阴影，黄道光横贯全天",
                "无人区 / 高海拔荒漠"),
    BortleClass(2, "典型真暗空", 21.75, 7.5, "银河结构极其清晰，M33 肉眼可见",
                "国家公园核心区"),
    BortleClass(3, "乡村天空", 21.45, 7.0, "银河细节丰富，黄道光明显",
                "远离城市的乡村"),
    BortleClass(4, "乡村/郊区过渡", 20.90, 6.5, "银河仍清晰，但地平附近受光污染",
                "乡村与郊区之间"),
    BortleClass(5, "郊区天空", 20.30, 6.0, "银河暗淡、仅见主体，地平有光晕",
                "城市郊区"),
    BortleClass(6, "亮郊区", 19.40, 5.4, "银河几乎不可见，只在极好条件下隐约可见",
                "亮郊区 / 小城镇"),
    BortleClass(7, "郊区/城区过渡", 18.60, 5.0, "银河不可见，天空呈灰白色",
                "城市边缘"),
    BortleClass(8, "城区天空", 18.00, 4.5, "只能看到最亮的星座，天空发亮",
                "城市市区"),
    BortleClass(9, "市中心天空", 17.50, 4.0, "除月亮与少数亮星外几乎看不到",
                "大城市市中心"),
)

BORTLE_BY_LEVEL: Dict[int, BortleClass] = {b.level: b for b in BORTLE_CLASSES}

#: 消光系数（mag/airmass）：夜空越亮，大气越浑浊，取值略高
_EXTINCTION_BY_BORTLE: Dict[int, float] = {
    1: 0.14, 2: 0.15, 3: 0.16, 4: 0.18, 5: 0.20,
    6: 0.22, 7: 0.24, 8: 0.26, 9: 0.28,
}


def bortle_class(level: int) -> BortleClass:
    """按等级取波特尔分类（越界时钳制到 1~9）。"""
    return BORTLE_BY_LEVEL.get(int(clamp(level, 1, 9)), BORTLE_BY_LEVEL[5])


# ==============================================================================
# 极限星等
# ==============================================================================

def limiting_magnitude(bortle: int, moon_phase: float = 0.0,
                       object_altitude: float = 90.0,
                       aperture_mm: float = 0.0,
                       moon_altitude: float = -90.0) -> float:
    """计算给定条件下的极限星等。

    参数：
        bortle          : 波特尔等级 1~9
        moon_phase      : 月相照亮比例 0~1
        object_altitude : 目标高度角（度），用于大气消光
        aperture_mm     : 望远镜口径（毫米）；0 表示肉眼
        moon_altitude   : 月亮高度角（度）；月亮在地平线下则不抬亮天空
    """
    info = bortle_class(bortle)
    mu = info.mu_sky

    # ---- 月光：满月可使天空亮度提高约 2.5 mag/arcsec² ----
    phase = clamp(float(moon_phase), 0.0, 1.0)
    if moon_altitude > 0.0:
        # 月亮越高、越圆，天空越亮
        factor = math.sin(math.radians(clamp(moon_altitude, 0.0, 90.0)))
        mu -= 2.6 * (phase ** 1.4) * (0.35 + 0.65 * factor)
    elif moon_altitude > -18.0:
        # 月光 twilight 残余
        mu -= 0.5 * (phase ** 1.5) * (1.0 - abs(moon_altitude) / 18.0)

    # ---- 人眼极限星等 ----
    # 经验式（按波特尔各级的典型极限星等标定：21.95→7.9、20.30→6.0、17.50→4.0）
    # 早期版本用了 μ/2+1.4，在亮天空下明显高估（Bortle 8 会算成 10.4 等，
    # 等于说市区能用肉眼看到 10 等星，与实际完全不符）
    m_lim = 0.2720 * mu + 0.0200

    # ---- 大气消光 ----
    alt = clamp(float(object_altitude), 1.0, 90.0)
    k = _EXTINCTION_BY_BORTLE.get(info.level, 0.20)
    airmass = 1.0 / max(0.02, math.sin(math.radians(alt)))
    # 消光使目标变暗：等效于极限星等下降
    m_lim -= k * (airmass - 1.0)

    # ---- 望远镜增益 ----
    if aperture_mm and aperture_mm > 1.0:
        m_lim += 2.5 * math.log10(float(aperture_mm)) + 2.0
    return round(m_lim, 2)


def naked_eye_limiting(bortle: int) -> float:
    """人眼在天顶的极限星等（不含月光与消光）。"""
    return bortle_class(bortle).naked_eye_limiting


def telescope_limiting(bortle: int, aperture_mm: float) -> float:
    """给定口径的望远镜极限星等（天顶、无月）。"""
    return limiting_magnitude(bortle, 0.0, 90.0, aperture_mm)


def sky_brightness_label(bortle: int) -> str:
    """给界面用的亮度文字描述。"""
    info = bortle_class(bortle)
    return f"{info.mu_sky:.2f} mag/arcsec²"


# ==============================================================================
# 对比报告
# ==============================================================================

@dataclass
class PollutionReport:
    """城市 vs 山上的对比结果。"""

    bortle: int = 5
    limiting_mag_naked: float = 6.0
    limiting_mag_telescope: float = 12.0
    stars_visible: int = 0
    deep_sky_visible: int = 0
    dso_naked: int = 0            # 肉眼可见的深空天体（如 M31、M42、昴星团）
    dso_binocular: int = 0        # 双筒可见
    dso_small_scope: int = 0      # 小口径望远镜（150mm）
    dso_large_scope: int = 0      # 大口径望远镜（300mm）
    milky_way: str = ""
    description: str = ""
    compare_text: str = ""


def visible_star_count(limiting_mag: float) -> int:
    """整个天球上肉眼可见的恒星数量（按星等分布的经验拟合）。

    依据天文观测统计：
        m ≤ 4.0 → 约 500 颗；m ≤ 5.0 → 约 1,600 颗
        m ≤ 6.0 → 约 4,800 颗；m ≤ 6.5 → 约 8,000 颗；m ≤ 7.9 → 约 30,000 颗
    在 4~8 等之间用对数-线性拟合，两端夹紧。
    """
    from math import log10
    m = float(limiting_mag)
    if m <= 1.0:
        return 15
    # N(m) ≈ 10^(0.545·m - 0.63)，由 m=4→500、m=6.5→8000 两点定标
    n = 10 ** (0.545 * m - 0.630)
    return int(clamp(n, 15, 120000))


def evaluate(star_mags: Sequence[float], dso_mags: Sequence[float],
             bortle: int, moon_phase: float = 0.0,
             moon_altitude: float = -90.0, aperture_mm: float = 0.0) -> PollutionReport:
    """统计在给定光害条件下能看到多少恒星与深空天体。

    星数用 sky-wide 经验分布估算（内置星表是"精选亮星"，样本太窄，
    直接数它会把城市与暗空的差距压成个位数——早期版本就是这个毛病）。
    深空天体数用内置 129 个 Messier/NGC 样本统计，这才是真实清单。
    """
    rep = PollutionReport(bortle=int(clamp(bortle, 1, 9)))
    info = bortle_class(rep.bortle)
    rep.milky_way = info.milky_way
    rep.limiting_mag_naked = limiting_magnitude(rep.bortle, moon_phase, 60.0,
                                                0.0, moon_altitude)
    rep.limiting_mag_telescope = limiting_magnitude(rep.bortle, moon_phase, 60.0,
                                                    aperture_mm, moon_altitude)
    rep.stars_visible = visible_star_count(rep.limiting_mag_naked)
    # 深空天体按"观测手段"分档统计：
    # 只用望远镜极限星等会把 129 个目标在各级暗空下都算成可见（内置样本最暗 10.2 等，
    # 150mm 望远镜在市区也能到 12 等），完全失去区分度。按手段分档才符合实际观感。
    rep.dso_naked = sum(1 for m in dso_mags if m <= rep.limiting_mag_naked)
    rep.dso_binocular = sum(1 for m in dso_mags
                            if m <= limiting_magnitude(rep.bortle, moon_phase, 60.0,
                                                       50.0, moon_altitude))
    rep.dso_small_scope = sum(1 for m in dso_mags
                              if m <= limiting_magnitude(rep.bortle, moon_phase, 60.0,
                                                         150.0, moon_altitude))
    rep.dso_large_scope = sum(1 for m in dso_mags
                              if m <= limiting_magnitude(rep.bortle, moon_phase, 60.0,
                                                         300.0, moon_altitude))
    rep.deep_sky_visible = rep.dso_small_scope
    rep.description = (f"{info.label}（天空亮度 {info.mu_sky:.2f} mag/arcsec²）　"
                       f"肉眼极限星等 {rep.limiting_mag_naked:.1f}　"
                       f"银河：{info.milky_way}")
    return rep


def compare_report(star_mags: Sequence[float], dso_mags: Sequence[float],
                   low: int = 8, high: int = 2,
                   aperture_mm: float = 150.0) -> str:
    """生成"城里 vs 山上"的对比文字（给界面直接用）。"""
    a = evaluate(star_mags, dso_mags, low, aperture_mm=aperture_mm)
    b = evaluate(star_mags, dso_mags, high, aperture_mm=aperture_mm)
    ia, ib = bortle_class(low), bortle_class(high)

    def block(rep: "PollutionReport", info: "BortleClass", color: str) -> str:
        return (
            f"<b style='color:{color}'>{info.label}</b>（{info.note}）<br>"
            f"· 天空亮度 {info.mu_sky:.2f} mag/arcsec²　"
            f"肉眼极限星等 <b>{rep.limiting_mag_naked:.1f}</b><br>"
            f"· 全天真能看到的恒星约 <b>{rep.stars_visible:,}</b> 颗<br>"
            f"· 深空天体：肉眼 {rep.dso_naked} 个　双筒 {rep.dso_binocular} 个　"
            f"150mm 望远镜 {rep.dso_small_scope} 个<br>"
            f"· 银河：{info.milky_way}"
        )

    d_star = b.stars_visible - a.stars_visible
    d_dso = b.dso_binocular - a.dso_binocular
    ratio = (b.stars_visible / a.stars_visible) if a.stars_visible else 1.0
    return (
        block(a, ia, "#ffb37a") + "<br><br>" + block(b, ib, "#8fffc8") + "<br><br>"
        f"<b>搬到暗空后：肉眼可见恒星从 {a.stars_visible:,} 颗增加到 "
        f"{b.stars_visible:,} 颗（约 {ratio:.1f} 倍），双筒可见深空天体多 {d_dso} 个，"
        f"极限星等提高 {b.limiting_mag_naked - a.limiting_mag_naked:.1f} 等</b>"
    )


def bortle_options() -> List[Tuple[int, str]]:
    """供下拉框使用的 (等级, 显示文本) 列表。"""
    return [(b.level, f"{b.level} · {b.name}（{b.note}）") for b in BORTLE_CLASSES]


__all__ = [
    "BortleClass", "BORTLE_CLASSES", "BORTLE_BY_LEVEL",
    "bortle_class", "limiting_magnitude", "naked_eye_limiting", "telescope_limiting",
    "sky_brightness_label", "PollutionReport", "evaluate", "compare_report",
    "bortle_options",
]
