# -*- coding: utf-8 -*-
"""
inverse_sky.py —— 从别的恒星看天空（反用太阳邻域数据）
================================================================================
把「太阳邻域」和「现实星图」的数据反过来用：

    站在 50 光年内的任意一颗恒星上，头顶的星空长什么样？

计算全部基于已有的真实数据，没有任何虚构：
    * 每颗近星的三维位置（光年，**赤道直角坐标**）来自 HYG 星表 → 两星间真实距离
    * 从观察点看到的**太阳**，其视星等由绝对星等 M_V=+4.83 与光年距离算出
        m = M − 5 + 5·log10(d_ly)
    * 其他近星的视星等同理用各自的绝对星等换算
    * 方向就是该偏移向量的赤经赤纬，可直接与地球星图逐颗对照

坐标系的坑（务必保留这段说明）：HYG 的 readme 把 x/y/z 写作"银道直角坐标"，
但实测（tools/dbg_hyg_frame.py）表明它其实是**赤道直角坐标**——X 指向
(0h, 0°)、Z 指向北天极，单位秒差距。把天狼的 x/y/z 代入 atan2(y,x) 得到
101.2872°，与星表自己的 ra = 6.7525h = 101.2872° 完全一致。早期版本误当银道
坐标又转了一次赤道，结果所有近星的星座判定都错得离谱。

典型结论（程序会算出来，下面是已知的天文事实）：
    比邻星看太阳 → m ≈ +0.4，位于**仙后座**，是当地夜空最亮的星之一
    从 4.2 光年外看太阳，它并不显眼——只是众多亮星中的一颗
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import astronomy as A
from . import nearby_stars as NS
from . import star_data as SD
from .utils import clamp

#: 太阳的绝对目视星等（V 波段）
SUN_ABSMAG = 4.83


@dataclass
class SeenStar:
    """在观察点看到的一个天体。"""

    name: str                # 显示名（近星的显示名，或"太阳"）
    source: NS.NearbyStar | None   # 对应的近星记录（太阳为 None）
    distance_ly: float       # 与观察点的距离（光年；0 表示距离未知）
    app_mag: float           # 从观察点看到的视星等
    ra: float                # 赤经（度）
    dec: float               # 赤纬（度）
    spectral: str = ""
    is_sun: bool = False
    layer: str = "atlas"     # local=50 光年内（有真实视差）/ atlas=远处亮星背景
    hip: int = 0             # HIP 编号（仅用于去重，界面不显示）

    @property
    def constellation(self) -> str:
        """它落在哪个星座里（按赤经赤纬就近匹配星座中心）。"""
        return nearest_constellation(self.ra, self.dec)

    def constellation_cn(self) -> str:
        abbr = self.constellation
        return SD.CONSTELLATIONS.get(abbr, ("", ""))[0] or abbr

    @property
    def color(self) -> Tuple[int, int, int]:
        """按光谱型给颜色（与主星图配色一致）。"""
        if self.is_sun:
            return (255, 244, 200)
        if not self.spectral:
            return (235, 240, 250)
        return NS._SPECTRAL_RGB.get(self.spectral.strip().upper()[0], (235, 240, 250))  # noqa: SLF001


@dataclass
class InverseSky:
    """一次"外星视角"的完整结果。"""

    viewpoint_name: str
    viewpoint_dist: float            # 观察点到太阳的距离（光年）
    sun: Optional[SeenStar] = None   # 从观察点看到的太阳
    stars: List[SeenStar] = None     # type: ignore[assignment]
    local: List[SeenStar] = None     # type: ignore[assignment]
    atlas: List[SeenStar] = None     # type: ignore[assignment]
    earth_direction: Optional[Tuple[float, float]] = None   # 地球所在方向（赤经赤纬）
    earth_app_mag: Optional[float] = None                   # 从观察点看地球的视星等
    truncated: bool = False          # 是否因为数量上限被截断
    limiting_mag: float = 7.5        # 生成时使用的星等上限
    note: str = ""

    def __post_init__(self) -> None:
        for field_name in ("stars", "local", "atlas"):
            if getattr(self, field_name) is None:
                setattr(self, field_name, [])

    def brightest(self, count: int = 12) -> List[SeenStar]:
        return sorted(self.stars, key=lambda s: s.app_mag)[: max(1, count)]

    def describe(self) -> str:
        """一句话结论（界面与导出共用）。"""
        if self.sun is None:
            return self.note
        return (f"太阳视星等 {self.sun.app_mag:+.2f}，位于{self.sun.constellation_cn()}；"
                f"当地亮于 {self.limiting_mag:.1f} 等的恒星 {len(self.stars)} 颗"
                f"（其中 50 光年内近星 {len(self.local)} 颗，有真实视差）")


# ==============================================================================
# 基础换算
# ==============================================================================

def apparent_magnitude(abs_mag: float, distance_ly: float) -> float:
    """由绝对星等与距离（光年）求视星等：m = M − 5 + 5·log10(d_pc)。"""
    d_pc = max(1e-6, float(distance_ly) / NS.PC_TO_LY)
    return float(abs_mag) - 5.0 + 5.0 * math.log10(d_pc)


def direction_from_offsets(dx: float, dy: float, dz: float) -> Tuple[float, float]:
    """**赤道直角坐标**下的偏移向量 → (赤经, 赤纬)（度）。

    这里的关键事实（曾判断错，导致近星方向全错）：HYG 星表的 x/y/z 三列
    并不是 readme 里写的"银道直角坐标"，而是**赤道直角坐标**——单位秒差距、
    X 指向 (0h, 0°)、Z 指向北天极。实测校验（tools/dbg_hyg_frame.py）：
        天狼  atan2(y,x) = 101.2872°，asin(z/r) = −16.7161°
        而星表自己的 ra = 6.7525h = 101.2872°、dec = −16.7161° ——完全吻合。
    因此换算方向既不要先转银经银纬，也不要再转一次赤道，直接用 atan2/asin 即可。

    单位由调用方负责：nearby_data 里存的是**光年**（生成时已由秒差距换算），
    本函数只关心方向，与单位无关。
    """
    r = math.sqrt(dx * dx + dy * dy + dz * dz)
    if r < 1e-9:
        return (0.0, 0.0)
    ra = math.degrees(math.atan2(dy, dx)) % 360.0
    dec = math.degrees(math.asin(clamp(dz / r, -1.0, 1.0)))
    return (ra, dec)


def sun_position_from(viewpoint_x: float, viewpoint_y: float,
                      viewpoint_z: float) -> Tuple[float, float, float, float]:
    """从观察点看太阳。

    太阳在赤道直角坐标系的原点，所以「太阳相对观察点」的向量就是
    −(vx, vy, vz)，长度即观察点到太阳的真实距离（由坐标本身求出，
    不再绕银经银纬一圈，避免任何数值损失）。

    返回 (赤经, 赤纬, 视星等, 距离光年)。
    """
    dx, dy, dz = -float(viewpoint_x), -float(viewpoint_y), -float(viewpoint_z)
    d = math.sqrt(dx * dx + dy * dy + dz * dz)
    if d < 1e-9:
        return (0.0, 0.0, apparent_magnitude(SUN_ABSMAG, 0.01), 0.01)
    ra, dec = direction_from_offsets(dx, dy, dz)
    mag = apparent_magnitude(SUN_ABSMAG, d)
    return (ra, dec, mag, d)


def nearest_constellation(ra: float, dec: float) -> str:
    """按赤经赤纬就近匹配星座（用 88 星座中心点）。"""
    best, best_d = "", 1e9
    for abbr, (cra, cdec) in SD.CONSTELLATION_CENTERS.items():
        dlat = dec - cdec
        dlon = (ra - cra + 180.0) % 360.0 - 180.0
        d = dlat * dlat + (dlon * math.cos(math.radians((dec + cdec) / 2.0))) ** 2
        if d < best_d:
            best_d, best = d, abbr
    return best


def equatorial_to_galactic_vector(ra: float, dec: float) -> Tuple[float, float, float]:
    """赤道坐标 → **银道**直角坐标的单位向量。

    两个坐标系的关系容易记混，这里写清楚（A._GAL_MATRIX 是**转置存放**的
    银道→赤道旋转矩阵，其第 i 行 = (x_gal[i], y_gal[i], z_gal[i])）：
        A.galactic_to_equatorial 计算的是   v_eq  = Mᵀ · v_gal   （逐行点乘）
        所以逆变换是                        v_gal = M  · v_eq    （逐列点乘）

    本函数**不参与**外星视角的方向计算（那里的坐标本来就是赤道直角系，
    见 direction_from_offsets 的说明），只用于银河带之类的银道系辅助换算。
    """
    v = A.radec_to_vec(ra, dec)
    return A.equatorial_vector_to_galactic(v)


# ==============================================================================
# 主计算
# ==============================================================================

def compute_inverse_sky(viewpoint: NS.NearbyStar,
                        limit_ly: float = 50.0,
                        mag_limit: float = 7.5,
                        max_stars: int = 3000) -> InverseSky:
    """从一颗近星出发，算出它的夜空。

    参数：
        viewpoint  观察点（太阳邻域里的某颗近星）
        limit_ly   只考虑观察点周围多大范围内的**近星**（光年）
        mag_limit  只保留视星等亮于此的天体
        max_stars  返回天体的数量上限（按视星等从亮到暗截断）

    内容分三层，物理精度依次递减，但都写在返回值里以便界面区分：
        1. 太阳         —— 由坐标直接反算方向，视星等用 M=+4.83 与真实距离
        2. 50 光年内的近星 —— 位置来自 HYG 三维坐标，**有真实视差**（从比邻星看
                              天狼，方向会与地球上看到的天狼差好几度）
        3. 远处亮星（星表背景）—— 只把地球上的赤经赤纬搬过去当作无穷远背景。
                              这些星距离几十到几千光年，50 光年内的位移通常小于
                              百分之一度，肉眼无感；但界面会明确标注这一点。
    """
    vx, vy, vz = viewpoint.x, viewpoint.y, viewpoint.z

    # ---- 太阳 ----
    ra, dec, mag, v_dist = sun_position_from(vx, vy, vz)
    sky = InverseSky(viewpoint_name=viewpoint.display_name, viewpoint_dist=v_dist,
                     limiting_mag=mag_limit)
    sky.sun = SeenStar(name="太阳", source=None, distance_ly=v_dist, app_mag=mag,
                       ra=ra, dec=dec, spectral="G2V", is_sun=True)

    # ---- 第 2 层：50 光年内的近星（真实三维 + 视差） ----
    local: List[SeenStar] = []
    for other in NS.NEARBY_CATALOG:
        if other is viewpoint:
            continue
        dx = other.x - vx
        dy = other.y - vy
        dz = other.z - vz
        d = math.sqrt(dx * dx + dy * dy + dz * dz)
        if d <= 0.02 or d > limit_ly:
            continue
        m = apparent_magnitude(other.absmag, d)
        if m > mag_limit:
            continue
        ora, odec = direction_from_offsets(dx, dy, dz)
        local.append(SeenStar(name=other.display_name, source=other,
                              distance_ly=d, app_mag=m, ra=ora, dec=odec,
                              spectral=other.spectral, layer="local",
                              hip=int(other.hip or 0)))
    sky.local = local

    # ---- 第 3 层：星表亮星当无穷远背景 ----
    atlas: List[SeenStar] = []
    for st in SD.ALL_SKY_STARS:
        if st.mag > mag_limit:
            continue
        s = SeenStar(name=st.display_name(), source=None,
                     distance_ly=float(st.distance or 0.0), app_mag=st.mag,
                     ra=st.ra, dec=st.dec, spectral=st.spectral, layer="atlas")
        s.hip = int(st.hip or 0)         # 供去重使用（本身不显示）
        atlas.append(s)
    sky.atlas = atlas

    # ---- 合并、去重（同一颗星可能既有二维星表条目又有三维近星条目） ----
    # 例：天狼在「太阳邻域 50 光年」里是一条三维记录（HIP 32349），
    # 在「亮星表」里又是一条二维记录；必须只留三维那条，否则天上会有两个天狼。
    by_hip: Dict[int, int] = {}          # HIP → merged 中的下标
    merged: List[SeenStar] = []
    for s in local + atlas:
        hip = int(s.hip or 0)
        if hip and hip in by_hip:
            idx = by_hip[hip]
            if s.layer == "local":       # 三维视差版本优先
                merged[idx] = s
            continue
        if hip:
            by_hip[hip] = len(merged)
        merged.append(s)
    merged.sort(key=lambda s: s.app_mag)
    sky.stars = merged[:max_stars]
    sky.truncated = len(merged) > max_stars

    # ---- 地球方向（太阳系里最容易想到的"另一个世界"） ----
    # 地球相对太阳的位移极小（1 AU），方向与太阳几乎一致；
    # 这里仍算出精确方向，便于将来接入"从别的恒星看地球凌日"。
    sky.earth_direction = (ra, dec)
    sky.earth_app_mag = apparent_magnitude(SUN_ABSMAG, max(0.01, v_dist))

    sky.note = (f"从 {viewpoint.display_name}（距太阳 {v_dist:.2f} 光年）看去："
                f"太阳视星等 {mag:+.2f}，位于{sky.sun.constellation_cn()}；"
                f"视野内另有 {len(sky.stars)} 颗亮于 {mag_limit:g} 等的恒星")
    return sky


def sun_constellation_table(max_dist: float = 16.0) -> List[Tuple[str, float, float, str]]:
    """列出"从各近星看太阳"的星座与亮度，用于文字说明。

    返回 [(观察点名, 距太阳(光年), 太阳视星等, 太阳所在星座)]，按亮度排序。
    """
    rows: List[Tuple[str, float, float, str]] = []
    for star in NS.NEARBY_CATALOG:
        d = math.sqrt(star.x ** 2 + star.y ** 2 + star.z ** 2)
        if d <= 0.02 or d > max_dist:
            continue
        ra, dec, mag, d = sun_position_from(star.x, star.y, star.z)
        abbr = nearest_constellation(ra, dec)
        cn = SD.CONSTELLATIONS.get(abbr, ("", ""))[0] or abbr
        rows.append((star.display_name, d, mag, cn))
    rows.sort(key=lambda r: r[2])
    return rows


__all__ = [
    "SeenStar", "InverseSky", "SUN_ABSMAG",
    "apparent_magnitude", "direction_from_offsets", "equatorial_to_galactic_vector",
    "sun_position_from", "nearest_constellation", "compute_inverse_sky",
    "sun_constellation_table",
]