# -*- coding: utf-8 -*-
"""
nearby_data.py 的数据接入层（nearby catalog accessor）
================================================================================
本模块把自动生成的近星数据表（太阳 50 光年内的全部已知天体）包装成对象，
供「太阳邻域」3D 视图与信息面板直接使用：

    NearbyStar          单个近星对象（含距离、颜色、分类、赤道直角坐标）
    NEARBY_CATALOG      全部近星（按距离升序）
    nearby_stats()      统计信息（总数、分类计数、距离分布）
    nearest_stars()     取最近的 N 颗
    find_nearby()       按 HIP / 专名 / 中文名 查找
    search_nearby()     模糊搜索（供界面搜索框使用）
    stars_within()      按距离阈值筛选

数据来源：HYG 星表 v3.8（Hipparcos + Yale BSC + Gliese 汇编）
说明：这是**已知**天体的集合——褐矮星与极暗红矮星仍在持续发现中，
      因此表内容代表"目前人类已编目的 50 光年内天体"，而非理论上限。

坐标系的坑：HYG 的 readme 声称 x/y/z 是"银道直角坐标"，实际是**赤道直角
坐标**（X 指向 (0h, 0°)、Z 指向北天极，单位秒差距，入库时已换成光年）。
证据见 tools/dbg_hyg_frame.py：天狼的 x/y/z 代入 atan2(y,x) 得 101.2872°，
与星表自己的 ra = 6.7525h = 101.2872° 完全一致。因此 nearby_view 的
"银道面网格"其实画的是赤道面，界面上已改称「赤道面」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# PC_TO_LY 在本模块内不再直接引用，但它是**公开常量**（1 秒差距 = 3.2616 光年），
# core.inverse_sky 等模块通过 `NS.PC_TO_LY` 取用，所以必须继续从这里导出。
from .nearby_data import LIMIT_LY, NEARBY_META, NEARBY_STARS, PC_TO_LY
from .star_data import CONSTELLATIONS, STAR_BY_HIP, STAR_NAMES_CN
from .utils import bv_to_rgb, clamp

# ==============================================================================
# 天体分类与颜色
# ==============================================================================

#: 分类 → 显示颜色（与光谱型颜色略有区别，用于"按类型着色"模式）
KIND_COLORS: Dict[str, Tuple[int, int, int]] = {
    "主序星": (150, 200, 255),
    "次巨星": (255, 226, 160),
    "巨星": (255, 170, 120),
    "白矮星": (225, 240, 255),
    "褐矮星": (215, 120, 130),
    "未知类型": (170, 180, 195),
}

#: 光谱型 → 颜色（按光谱着色模式）
_SPECTRAL_RGB: Dict[str, Tuple[int, int, int]] = {
    "O": (155, 176, 255), "B": (170, 191, 255), "A": (202, 215, 255),
    "F": (248, 247, 255), "G": (255, 244, 232), "K": (255, 210, 161),
    "M": (255, 180, 130), "L": (255, 130, 100), "T": (200, 90, 120),
    "Y": (170, 80, 110), "D": (225, 235, 255), "W": (230, 240, 255),
}


@dataclass
class NearbyStar:
    """一颗位于太阳 50 光年内的已知天体。"""

    hip: int                 # HIP 编号（0 表示该星未被 Hipparcos 收录）
    proper: str              # 专名（英文/西方名，可能为空）
    gliese: str              # Gliese 编号（近星最常用的标识，如 "Gliese 1"）
    mag: float               # 视星等
    absmag: float            # 绝对星等
    bv: float                # B-V 色指数
    spectral: str            # 光谱型
    constellation: str       # 星座缩写
    distance_ly: float       # 距离（光年）
    x: float                 # 赤道直角坐标（光年，X 指向 (0h, 0°)，Z 指向北天极）
    y: float
    z: float
    kind: str = ""           # 分类（主序星 / 白矮星 / 褐矮星 …）
    name_cn: str = ""        # 中文名（若该星在内置亮星表中）

    # ---- 派生属性 ----

    @property
    def display_name(self) -> str:
        """优先中文名 → 专名 → Gliese 编号 → HIP 编号 → 坐标编号。"""
        if self.name_cn:
            return self.name_cn
        if self.proper:
            return self.proper
        if self.gliese:
            return self.gliese
        if self.hip:
            return f"HIP {self.hip}"
        return f"近星-{abs(hash((self.x, self.y, self.z))) % 10000:04d}"

    @property
    def catalog_id(self) -> str:
        """编目编号（信息面板单独一行显示）。"""
        parts = []
        if self.gliese:
            parts.append(self.gliese)
        if self.hip:
            parts.append(f"HIP {self.hip}")
        return "　".join(parts) or "—"

    @property
    def sub_name(self) -> str:
        """副标题：专名 / 光谱型 / 星座。"""
        parts: List[str] = []
        if self.name_cn and self.proper:
            parts.append(self.proper)
        if self.spectral:
            parts.append(self.spectral)
        if self.constellation:
            cn = CONSTELLATIONS.get(self.constellation, ("", ""))[0]
            parts.append(cn or self.constellation)
        return "　".join(parts)

    @property
    def constellation_cn(self) -> str:
        return CONSTELLATIONS.get(self.constellation, ("", ""))[0]

    @property
    def color(self) -> Tuple[int, int, int]:
        """按 B-V 计算颜色（与星图配色保持一致）。"""
        return bv_to_rgb(self.bv)

    @property
    def kind_color(self) -> Tuple[int, int, int]:
        return KIND_COLORS.get(self.kind, (200, 210, 225))

    @property
    def spectral_color(self) -> Tuple[int, int, int]:
        if not self.spectral:
            return (235, 240, 250)
        return _SPECTRAL_RGB.get(self.spectral.strip().upper()[0], (235, 240, 250))

    @property
    def radius(self) -> float:
        """渲染半径：由绝对星等换算，并对亮度跨度做压缩。

        50 光年内绝大多数是暗红矮星（M=10~17），若按线性比例它们几乎不可见；
        这里把 (M+1.5)/17.5 映射到 [0,1] 后做平方根压缩，使暗星保留 1.4px、
        亮星（天狼 M=1.45）可达 7px，既反映真实亮度差异又保证可读性。
        """
        t = clamp((self.absmag + 1.5) / 17.5, 0.0, 1.0)
        return 7.0 - 5.6 * math.sqrt(t)

    @property
    def ra(self) -> float:
        """赤经（度）。x/y/z 是赤道直角坐标，所以 atan2(y, x) 直接就是赤经。"""
        return math.degrees(math.atan2(self.y, self.x)) % 360.0

    @property
    def dec(self) -> float:
        """赤纬（度）。"""
        r = math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z) or 1.0
        return math.degrees(math.asin(clamp(self.z / r, -1.0, 1.0)))

    @property
    def galactic_l(self) -> float:
        """银经（度）。

        注意：x/y/z 本身是**赤道**直角坐标，所以必须先转到银道直角坐标，
        不能像早期版本那样直接把 atan2(y, x) 当银经——那样得到的是赤经，
        数值看着"像模像样"，但整张表的银经全错。
        """
        from .astronomy import equatorial_vector_to_galactic
        vx, vy, vz = equatorial_vector_to_galactic((self.x, self.y, self.z))
        return math.degrees(math.atan2(vy, vx)) % 360.0

    @property
    def galactic_b(self) -> float:
        """银纬（度）。"""
        from .astronomy import equatorial_vector_to_galactic
        vx, vy, vz = equatorial_vector_to_galactic((self.x, self.y, self.z))
        r = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
        return math.degrees(math.asin(clamp(vz / r, -1.0, 1.0)))

    def distance_to(self, other: "NearbyStar") -> float:
        """与另一颗近星的距离（光年）。"""
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2
                         + (self.z - other.z) ** 2)

    def to_dict(self) -> Dict[str, object]:
        """转成信息面板需要的字典。"""
        return {
            "panel": "nearby",
            "key": f"nearby:{self.hip or self.display_name}",
            "name": self.display_name,
            "name_en": self.proper,
            "kind": "nearby",
            "type_cn": self.kind,
            "spectral": self.spectral,
            "mag": self.mag,
            "absmag": self.absmag,
            "bv": self.bv,
            "distance_ly": self.distance_ly,
            "constellation": self.constellation_cn or self.constellation,
            "hip": self.hip or None,
            "galactic_l": round(self.galactic_l, 2),
            "galactic_b": round(self.galactic_b, 2),
            "position": (self.x, self.y, self.z),
        }


# ==============================================================================
# 目录构建
# ==============================================================================

def _classify(spect: str) -> str:
    """按光谱型分类（与生成脚本 tools/make_nearby.py 保持一致）。

    关键修正：白矮星写作 DA/DB/DC…（大写 D 开头），红矮星写作 dM4（小写 d），
    早期版本对光谱型统一 upper() 后再判断 D 开头，把大量红矮星误判为白矮星。
    """
    s = (spect or "").strip()
    if s.startswith("WD") or s.startswith("wd"):
        return "白矮星"
    if s[:1] == "D":
        return "白矮星"
    su = s.upper()
    if su[:1] in ("L", "T", "Y"):
        return "褐矮星"
    if not su:
        return "未知类型"
    if "III" in su or "II" in su or "IAB" in su or "IB" in su:
        return "巨星"
    if "IV" in su:
        return "次巨星"
    return "主序星"


def _build_catalog() -> List[NearbyStar]:
    """把数据表转换成对象列表（按距离升序）。"""
    out: List[NearbyStar] = []
    for row in NEARBY_STARS:
        (hip, proper, gliese, mag, absmag, bv, spect,
         con, dist, x, y, z) = row
        star = NearbyStar(
            hip=int(hip), proper=str(proper), gliese=str(gliese),
            mag=float(mag), absmag=float(absmag), bv=float(bv), spectral=str(spect),
            constellation=str(con), distance_ly=float(dist),
            x=float(x), y=float(y), z=float(z), kind=_classify(spect),
        )
        # 复用内置亮星表的传统中文名（如果有）
        if star.hip and star.hip in STAR_NAMES_CN:
            star.name_cn = STAR_NAMES_CN[star.hip]
        elif star.hip:
            ref = STAR_BY_HIP.get(star.hip)
            if ref is not None and ref.name:
                star.name_cn = ref.name
        out.append(star)
    out.sort(key=lambda s: s.distance_ly)
    return out


#: 全部近星（按距离升序）
NEARBY_CATALOG: List[NearbyStar] = _build_catalog()

#: HIP → 近星
NEARBY_BY_HIP: Dict[int, NearbyStar] = {s.hip: s for s in NEARBY_CATALOG if s.hip}

#: 著名近星的中文别名（传统星名 + 常用中文译名）
#: 说明：HYG 星表只有西文名，而中文用户习惯用「比邻星 / 巴纳德星」这类译名，
#: 因此这里补一张别名表，保证中英文与各种编号都能搜到。
NEARBY_ALIASES: Dict[str, str] = {
    "比邻星": "Proxima Centauri",
    "毗邻星": "Proxima Centauri",
    "半人马座比邻星": "Proxima Centauri",
    "南门二": "Toliman",
    "半人马座α": "Toliman",
    "巴纳德星": "Barnard's Star",
    "沃尔夫359": "Wolf 359",
    "拉兰代21185": "Lalande 21185",
    "天狼": "Sirius",
    "天狼星": "Sirius",
    "南河三": "Procyon",
    "罗斯154": "Ross 154",
    "罗斯248": "Ross 248",
    "罗斯128": "Ross 128",
    "鲁坦星": "Luyten's Star",
    "卡普坦星": "Kapteyn's Star",
    "范马南星": "Van Maanen's Star",
    "格鲁姆布里奇1830": "Groombridge 1830",
    "克鲁格60": "Kruger 60",
    "斯特鲁维2398": "Struve 2398",
    "印第安座ε": "Epsilon Indi",
    "天仓五": "Tau Ceti",
    "格雷泽1": "Gliese 1",
    "蒂加登星": "Teegarden's Star",
}


def _build_name_index() -> Dict[str, NearbyStar]:
    """构建可搜索的名称索引。

    需要把编目编号里的多个标识都拆开索引——数据库里的一行是
    「Gliese 551　HIP 70890」这种复合字符串，如果只按整串索引，
    搜「Gliese 551」就会失败（这也是早期版本的实际缺陷）。
    """
    index: Dict[str, NearbyStar] = {}
    for s in NEARBY_CATALOG:
        names: List[str] = [s.proper, s.name_cn, s.display_name]
        # 完整编目编号必须整体入索引，否则搜「Gliese 551」会失败
        # （早期版本只把拆出来的数字 "551" 入索引，导致全称搜不到）
        if s.gliese:
            names.append(s.gliese)
        # 再拆分出各个片段（"Gliese"、"551"、"HIP"、"70890"…）便于模糊搜索
        for piece in (s.gliese or "").replace("　", " ").split(" "):
            if piece.strip():
                names.append(piece.strip())
        if s.hip:
            names.extend([f"HIP {s.hip}", str(s.hip)])
        for n in names:
            if n and len(n.strip()) >= 2:
                index.setdefault(n.strip().lower(), s)
    # 中文别名
    for alias, proper in NEARBY_ALIASES.items():
        hit = index.get(proper.lower())
        if hit is not None:
            index.setdefault(alias.lower(), hit)
    return index


#: 名称（含中文别名、Gliese / HIP 编号）→ 近星
NEARBY_BY_NAME: Dict[str, NearbyStar] = _build_name_index()


def nearby_stats() -> Dict[str, object]:
    """统计信息（界面/文档直接展示）。"""
    from collections import Counter
    kinds = Counter(s.kind for s in NEARBY_CATALOG)
    bands: Counter = Counter()
    for s in NEARBY_CATALOG:
        if s.distance_ly <= 10:
            bands["≤10 光年"] += 1
        elif s.distance_ly <= 20:
            bands["10~20 光年"] += 1
        elif s.distance_ly <= 30:
            bands["20~30 光年"] += 1
        elif s.distance_ly <= 40:
            bands["30~40 光年"] += 1
        else:
            bands["40~50 光年"] += 1
    return {
        "count": len(NEARBY_CATALOG),
        "named": sum(1 for s in NEARBY_CATALOG if s.proper or s.name_cn),
        "with_hip": sum(1 for s in NEARBY_CATALOG if s.hip),
        "kinds": dict(kinds),
        "bands": dict(bands),
        "nearest": NEARBY_CATALOG[0] if NEARBY_CATALOG else None,
        "limit_ly": LIMIT_LY,
        "source": NEARBY_META.get("source", "HYG"),
        "generated": NEARBY_META,
    }


def nearest_stars(count: int = 20) -> List[NearbyStar]:
    """取最近的若干颗（已按距离排序）。"""
    return NEARBY_CATALOG[: max(1, int(count))]


def stars_within(limit_ly: float) -> List[NearbyStar]:
    """取指定距离内的近星。"""
    return [s for s in NEARBY_CATALOG if s.distance_ly <= limit_ly]


def find_nearby(text: str) -> Optional[NearbyStar]:
    """按 HIP / Gliese / 专名 / 中文名 / 中文别名查找近星。

    支持的写法示例：``HIP 87937``、``87937``、``Gliese 699``、``Gl 699``、
    ``GJ 699``、``Barnard's Star``、``巴纳德星``、``比邻星``。
    """
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    # HIP 编号
    if low.startswith("hip"):
        digits = low[3:].strip()
        if digits.isdigit():
            return NEARBY_BY_HIP.get(int(digits))
    # 纯数字 → 先当 HIP
    if low.isdigit():
        hit = NEARBY_BY_HIP.get(int(low))
        if hit is not None:
            return hit
    # Gliese 前缀归一化：Gl 699 / GJ 699 / Gliese 699 → gliese 699
    normalized = low
    for prefix in ("gliese ", "gl ", "gj ", "wo "):
        if low.startswith(prefix):
            normalized = "gliese " + low[len(prefix):].strip()
            break
    hit = NEARBY_BY_NAME.get(normalized)
    if hit is not None:
        return hit
    # 中文别名
    alias = NEARBY_ALIASES.get(t)
    if alias:
        hit = NEARBY_BY_NAME.get(alias.lower())
        if hit is not None:
            return hit
    # 整串精确 / 模糊匹配
    hit = NEARBY_BY_NAME.get(low)
    if hit is not None:
        return hit
    # 模糊匹配：只在"输入是索引键的前缀"时命中，避免 "NGC 7000" 撞上 "gliese 70"
    # （反向的 key in low 会让短键吃掉长输入，早期版本就有这个缺陷）
    if len(low) >= 3:
        for key, star in NEARBY_BY_NAME.items():
            if key.startswith(low) or low.startswith(key + " "):
                return star
    return None


def search_nearby(text: str, limit: int = 200,
                  kind: str = "all") -> List[NearbyStar]:
    """模糊搜索近星，返回按「匹配程度 → 距离」排序的列表。

    匹配优先顺序（分数越小越靠前）：
        0  专名 / 中文名完全相同
        1  专名 / 中文名以输入开头
        2  Gliese、HIP 等编号完全相同
        3  编号以输入开头
        4  名称中包含输入（兜底，可能召回一批）

    注意：这里的"包含"只用于**已经输入 ≥2 个字符**的情况，否则空串会把
    整个星表都召回；同时按距离排序保证近星优先出现在列表前面。
    """
    t = (text or "").strip()
    if not t:
        return []
    low = t.lower()
    scored: List[Tuple[int, float, NearbyStar]] = []
    for s in NEARBY_CATALOG:
        if kind and kind != "all" and s.kind != kind:
            continue
        names = [n for n in (s.name_cn, s.proper, s.gliese) if n]
        nums = [f"hip {s.hip}" if s.hip else "", s.gliese.lower() if s.gliese else "",
                str(s.hip) if s.hip else ""]
        best: Optional[int] = None
        for n in names:
            nl = n.lower()
            if nl == low:
                best = 0 if best is None else min(best, 0)
            elif nl.startswith(low):
                best = 1 if best is None else min(best, 1)
            elif len(low) >= 2 and low in nl:
                best = 4 if best is None else min(best, 4)
        for n in nums:
            if not n:
                continue
            if n == low:
                best = 2 if best is None else min(best, 2)
            elif n.startswith(low):
                best = 3 if best is None else min(best, 3)
        if best is not None:
            scored.append((best, s.distance_ly, s))
    scored.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in scored[: max(1, int(limit))]]


#: 类型筛选选项（供界面下拉框使用）
KIND_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("all", "全部类型"),
    ("主序星", "主序星"),
    ("次巨星", "次巨星"),
    ("巨星", "巨星"),
    ("白矮星", "白矮星"),
    ("褐矮星", "褐矮星"),
    ("未知类型", "类型未知"),
)

#: 着色模式
COLOR_MODES: Tuple[Tuple[str, str], ...] = (
    ("spectral", "按光谱型着色"),
    ("kind", "按天体类型着色"),
    ("distance", "按距离着色"),
    ("mag", "按绝对亮度着色"),
)


def distance_color(distance_ly: float, limit: float = LIMIT_LY) -> Tuple[int, int, int]:
    """按距离着色：近处青绿 → 远处紫。"""
    t = clamp(distance_ly / max(1.0, limit), 0.0, 1.0)
    return (int(80 + 90 * t), int(230 - 110 * t), int(220 - 90 * t))


def mag_color(absmag: float) -> Tuple[int, int, int]:
    """按绝对星等着色：越亮越白，越暗越红。"""
    t = clamp((absmag + 2.0) / 18.0, 0.0, 1.0)
    return (int(255 - 60 * t), int(250 - 140 * t), int(240 - 170 * t))


__all__ = [    "NearbyStar", "NEARBY_CATALOG", "NEARBY_BY_HIP", "NEARBY_BY_NAME",
    "KIND_COLORS", "KIND_OPTIONS", "COLOR_MODES",
    "nearby_stats", "nearest_stars", "stars_within", "find_nearby", "search_nearby",
    "distance_color", "mag_color", "LIMIT_LY",
]
