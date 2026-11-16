# -*- coding: utf-8 -*-
"""
world_generator.py —— 架空宇宙生成器
================================================================================
职责：
    1. 按随机种子生成一个完整、自洽的架空宇宙（Galaxy）
         · 恒星系（3D 坐标 / 光谱型 / 物理参数 / 资源 / 危险度 / 势力归属）
         · 行星（类型 / 轨道 / 大气 / 温度 / 重力 / 资源 / 生命迹象 / 文明等级）
         · 文明（种族 / 科技等级 / 政体 / 意识形态 / 母星 / 殖民地）
         · 势力（颜色 / 领土 / 外交关系矩阵）
         · 航线网络（发光曲线用到的折线 + 跳数/距离/燃料/风险）
         · 历史事件时间线
    2. 提供航线规划（Dijkstra 最短路）、飞船推进、事件触发等运行时逻辑
    3. JSON 序列化 / 反序列化（导出、导入、多槽位存档）

设计要点：
    * 全部使用 dataclass + 纯 Python，随机数统一走 random.Random(seed)，
      因此「同一种子 → 同一宇宙」，可复现、可分享。
    * 坐标单位：1.0 = 1 光年；星系尺度可选 40 / 80 / 140 / 220 个恒星系。
    * 生成算法复杂度 O(n²) 且 n ≤ 220，单次生成在数十毫秒级；
      界面层仍通过 QThread 调用 generate_galaxy()，避免任何卡顿风险。
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .utils import LOG, clamp, format_distance_ly, vec3_dist

# ==============================================================================
# 一、常量与词库
# ==============================================================================

#: 星区规模 → 恒星系数量
SCALE_PRESETS: Dict[str, int] = {
    "small": 40,
    "medium": 80,
    "large": 140,
    "huge": 220,
}

SCALE_LABELS_CN: Dict[str, str] = {
    "small": "小型星区",
    "medium": "中型星区",
    "large": "大型星区",
    "huge": "超大型星区",
}

#: 星图分布模式
DISTRIBUTION_MODES: Dict[str, str] = {
    "random": "均匀散布",
    "spiral": "旋臂星系",
    "cluster": "星团聚集",
    "filament": "纤维状结构",
}

#: 光谱型 → (出现权重, 质量范围(太阳质量), 温度范围(K), 光度范围(太阳光度), 颜色)
SPECTRAL_TABLE: Tuple[Tuple[str, float, Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[int, int, int]], ...] = (
    ("O", 0.7, (16.0, 60.0), (30000, 50000), (30000.0, 1000000.0), (155, 176, 255)),
    ("B", 3.0, (2.1, 16.0), (10000, 30000), (25.0, 30000.0), (170, 191, 255)),
    ("A", 7.0, (1.4, 2.1), (7500, 10000), (5.0, 25.0), (202, 215, 255)),
    ("F", 12.0, (1.04, 1.4), (6000, 7500), (1.5, 5.0), (248, 247, 255)),
    ("G", 20.0, (0.8, 1.04), (5200, 6000), (0.6, 1.5), (255, 244, 232)),
    ("K", 30.0, (0.45, 0.8), (3700, 5200), (0.08, 0.6), (255, 210, 161)),
    ("M", 22.0, (0.08, 0.45), (2400, 3700), (0.001, 0.08), (255, 180, 130)),
    ("L", 1.5, (0.05, 0.08), (1300, 2400), (0.0001, 0.001), (255, 130, 100)),
    ("T", 0.8, (0.02, 0.05), (700, 1300), (0.00001, 0.0001), (200, 90, 120)),
    ("W", 0.5, (5.0, 20.0), (20000, 60000), (10000.0, 500000.0), (230, 240, 255)),   # 沃尔夫-拉叶星
    ("N", 0.6, (1.1, 2.0), (3000, 9000), (100.0, 5000.0), (120, 160, 255)),          # 中子星
    ("D", 0.4, (0.5, 1.2), (5000, 30000), (0.0001, 0.01), (220, 230, 255)),          # 白矮星
)

#: 行星类型 → (中文名, 出现权重, 颜色)
PLANET_TYPES: Tuple[Tuple[str, str, float, Tuple[int, int, int]], ...] = (
    ("rocky", "岩质行星", 24.0, (190, 160, 130)),
    ("gas", "气态巨行星", 16.0, (230, 190, 140)),
    ("ice", "冰封行星", 14.0, (170, 220, 255)),
    ("lava", "熔岩行星", 9.0, (255, 110, 70)),
    ("ocean", "海洋行星", 12.0, (70, 170, 255)),
    ("desert", "沙漠行星", 11.0, (230, 200, 120)),
    ("artificial", "人造世界", 5.0, (170, 255, 230)),
    ("relic", "遗迹星球", 3.0, (200, 140, 255)),
    ("toxic", "毒气行星", 6.0, (180, 255, 140)),
)

#: 资源种类
RESOURCE_KINDS: Tuple[str, ...] = (
    "金属矿藏", "稀有气体", "氦-3", "水晶矿脉", "有机化合物", "反物质前体",
    "古代合金", "淡水储量", "生物质", "放射性元素", "量子晶体", "暗物质凝聚体",
)

#: 政体
GOVERNMENT_TYPES: Tuple[str, ...] = (
    "技术官僚议会", "世袭帝制", "军事独裁", "直接民主", "企业联合体",
    "神权统治", "无政府邦联", "AI 托管", "长老评议会", "氏族联盟",
)

#: 意识形态
IDEOLOGIES: Tuple[str, ...] = (
    "扩张主义", "孤立主义", "和平共处", "科技至上", "商业至上",
    "生态守护", "军国主义", "宗教狂热", "自由意志", "集体主义",
)

#: 外交状态
DIPLOMACY_STATES: Tuple[str, ...] = ("同盟", "中立", "敌对", "战争")

#: 科技等级描述（0-12 级，参考卡尔达肖夫与科幻常用分级）
TECH_LEVELS: Tuple[str, ...] = (
    "前工业文明", "工业文明", "信息文明", "早期星际", "恒星系航行",
    "反物质驱动", "曲率航行", "戴森结构", "意识上传", "星系工程",
    "时空操控", "维度跃迁", "不可名状",
)

#: 历史事件类型 → (描述模板, 权重)
EVENT_TEMPLATES: Tuple[Tuple[str, Tuple[str, ...], float], ...] = (
    ("探索", ("在 {sys} 发现未知跃迁通道", "探测船队在 {sys} 失联 72 小时后返航",
              "首次绘制 {sys} 的完整星图", "在 {sys} 外缘发现废弃探测器"), 18.0),
    ("贸易", ("{fac} 与 {fac2} 签订 {sys} 通商协定", "{sys} 成为区域贸易枢纽",
              "稀有资源在 {sys} 拍卖创下天价", "商队遇袭导致 {sys} 航线运费暴涨"), 16.0),
    ("战斗", ("{fac} 与 {fac2} 在 {sys} 爆发舰队冲突", "{sys} 防御网被突破",
              "游击队在 {sys} 击沉三艘主力舰", "{fac} 宣布 {sys} 为战区"), 15.0),
    ("外交", ("{fac} 与 {fac2} 签署互不侵犯条约", "{sys} 召开多方和平会议",
              "{fac} 召回驻 {fac2} 大使", "中立星系 {sys} 加入 {fac} 阵营"), 13.0),
    ("异常", ("{sys} 出现周期性引力波脉冲", "{sys} 的恒星亮度异常波动",
              "在 {sys} 观测到无法解释的量子纠缠现象", "{sys} 附近空间曲率出现褶皱"), 14.0),
    ("遗迹觉醒", ("{sys} 的远古构造体首次启动", "{sys} 遗迹释放出全频段信号",
                  "{sys} 地底机械开始自行复制", "{sys} 的守护者被唤醒"), 8.0),
    ("灾难", ("{sys} 遭遇伽马射线暴，殖民地受创", "{sys} 恒星耀斑摧毁轨道设施",
              "瘟疫在 {sys} 空间站扩散", "{sys} 生态穹顶发生连锁失效"), 10.0),
    ("科技", ("{fac} 在 {sys} 完成曲率引擎原型测试", "{sys} 实验室突破意识上传瓶颈",
              "{fac} 公布可编程物质技术", "{sys} 建成首个恒星能量环"), 6.0),
)

# ==============================================================================
# 二、数据结构
# ==============================================================================

@dataclass
class Planet:
    """一颗行星。"""

    pid: str
    name: str
    ptype: str                    # 类型键（rocky / gas / ...）
    ptype_cn: str
    orbit_au: float               # 轨道半长轴（天文单位）
    period_days: float            # 公转周期（地球日）
    radius_earth: float           # 半径（地球半径）
    gravity_g: float              # 表面重力（g）
    temperature_c: float          # 平均表面温度（摄氏度）
    atmosphere: str               # 大气成分描述
    pressure_atm: float           # 大气压（标准大气压）
    resources: List[str] = field(default_factory=list)
    life_signs: str = "无"        # 生命迹象：无 / 微生物 / 复杂生态 / 智慧生命
    civ_level: int = 0            # 文明等级（0 = 无）
    moons: int = 0
    color: Tuple[int, int, int] = (200, 200, 200)
    notes: str = ""

    @property
    def habitability(self) -> float:
        """宜居度评分（0~1）：温度、重力、大气、液态水综合评估。"""
        score = 1.0
        # 温度：15℃ 附近最佳
        score *= math.exp(-((self.temperature_c - 15.0) / 55.0) ** 2)
        # 重力：1g 附近最佳
        score *= math.exp(-((self.gravity_g - 1.0) / 1.4) ** 2)
        # 大气压：0.5~2 atm 最佳
        if self.pressure_atm < 0.05:
            score *= 0.15
        elif self.pressure_atm > 6.0:
            score *= 0.35
        # 生命迹象加成
        score *= {"无": 0.55, "微生物": 0.8, "复杂生态": 1.0, "智慧生命": 1.15}.get(self.life_signs, 0.6)
        return clamp(score, 0.0, 1.0)


@dataclass
class StarSystem:
    """一个恒星系。"""

    sid: int
    name: str
    position: Tuple[float, float, float]     # 三维坐标，单位光年
    spectral: str                            # 光谱型，如 "G2V"
    star_class: str                          # 光谱大类（O/B/A/...）
    mass_solar: float
    temperature_k: float
    luminosity_solar: float
    radius_solar: float
    color: Tuple[int, int, int]
    planet_count: int
    planets: List[Planet] = field(default_factory=list)
    resources: List[str] = field(default_factory=list)
    resource_richness: float = 0.5           # 0~1
    danger: float = 0.0                      # 0~1 危险度
    faction: str = ""                        # 所属势力名称
    civ_name: str = ""                       # 本土文明（可空）
    population_millions: float = 0.0
    has_station: bool = False                # 是否有空间站
    anomaly: str = ""                        # 异常现象描述
    discovered: bool = True
    notes: str = ""

    @property
    def brightness_score(self) -> float:
        """渲染尺寸用的亮度评分（对数压缩，避免巨星过大）。"""
        return clamp(math.log10(max(1e-5, self.luminosity_solar)) + 5.0, 0.6, 10.0)


@dataclass
class Civilization:
    """一个文明。"""

    cid: str
    name: str
    species: str
    tech_level: int
    tech_name: str
    government: str
    ideology: str
    home_system: str
    colonies: List[str] = field(default_factory=list)
    population_millions: float = 0.0
    founded_year: float = 0.0
    faction: str = ""
    traits: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Faction:
    """一个政治势力。"""

    fid: str
    name: str
    color: Tuple[int, int, int]
    ideology: str
    capital: str
    member_systems: List[int] = field(default_factory=list)
    military: float = 0.5
    economy: float = 0.5
    tech: float = 0.5
    relations: Dict[str, str] = field(default_factory=dict)   # 势力名 -> 外交状态
    description: str = ""


@dataclass
class Route:
    """一条航线（无向边，星图渲染时按曲线绘制）。"""

    a: int
    b: int
    distance_ly: float
    travel_days: float
    fuel_cost: float
    risk: float
    kind: str = "trade"          # trade / military / exploration / secret
    control: str = ""            # 控制方势力

    @property
    def key(self) -> Tuple[int, int]:
        return (min(self.a, self.b), max(self.a, self.b))


@dataclass
class HistoryEvent:
    """历史事件。"""

    year: float
    category: str
    description: str
    system: str = ""
    faction: str = ""


@dataclass
class Ship:
    """一艘被派遣的飞船。"""

    ship_id: str
    name: str
    route: List[int] = field(default_factory=list)      # 途经恒星系 id
    progress: float = 0.0                              # 已完成比例 0~1
    speed_ly_per_year: float = 12.0
    cargo: str = ""
    mission: str = "探索"
    fuel: float = 100.0
    hull: float = 100.0
    log: List[str] = field(default_factory=list)
    arrived: bool = False
    destroyed: bool = False
    owner: str = ""

    @property
    def total_distance(self) -> float:
        return 0.0  # 由 Galaxy 计算（需要坐标）


@dataclass
class Galaxy:
    """完整的架空宇宙。"""

    seed: int = 0
    name: str = "未命名星系"
    scale: str = "medium"
    distribution: str = "spiral"
    year: float = 0.0                     # 银河历（年）
    time_scale: float = 1.0               # 时间倍率（1/10/100/1000）
    systems: List[StarSystem] = field(default_factory=list)
    routes: List[Route] = field(default_factory=list)
    civilizations: List[Civilization] = field(default_factory=list)
    factions: List[Faction] = field(default_factory=list)
    history: List[HistoryEvent] = field(default_factory=list)
    ships: List[Ship] = field(default_factory=list)
    notes: str = ""
    created_at: float = field(default_factory=time.time)

    # ---- 索引（运行时构建，不参与序列化） ----
    def index(self) -> None:
        """建立常用索引，加速查询。"""
        self._by_id: Dict[int, StarSystem] = {s.sid: s for s in self.systems}
        self._route_map: Dict[Tuple[int, int], Route] = {r.key: r for r in self.routes}
        self._adj: Dict[int, List[int]] = {s.sid: [] for s in self.systems}
        for r in self.routes:
            self._adj.setdefault(r.a, []).append(r.b)
            self._adj.setdefault(r.b, []).append(r.a)
        self._faction_by_name: Dict[str, Faction] = {f.name: f for f in self.factions}

    def system(self, sid: int) -> Optional[StarSystem]:
        """按 id 取恒星系。"""
        if not hasattr(self, "_by_id"):
            self.index()
        return self._by_id.get(int(sid))

    def system_by_name(self, name: str) -> Optional[StarSystem]:
        for s in self.systems:
            if s.name == name:
                return s
        return None

    def neighbors(self, sid: int) -> List[int]:
        if not hasattr(self, "_adj"):
            self.index()
        return self._adj.get(int(sid), [])

    def route_between(self, a: int, b: int) -> Optional[Route]:
        if not hasattr(self, "_route_map"):
            self.index()
        return self._route_map.get((min(a, b), max(a, b)))

    def distance(self, a: int, b: int) -> float:
        """两个恒星系之间的直线距离（光年）。"""
        sa, sb = self.system(a), self.system(b)
        if not sa or not sb:
            return 0.0
        return vec3_dist(sa.position, sb.position)

    def stats(self) -> Dict[str, Any]:
        """整体统计信息（工具栏/信息面板用）。"""
        planets = sum(len(s.planets) for s in self.systems)
        pop = sum(s.population_millions for s in self.systems)
        rich = sum(s.resource_richness for s in self.systems) / max(1, len(self.systems))
        danger = sum(s.danger for s in self.systems) / max(1, len(self.systems))
        return {
            "systems": len(self.systems),
            "planets": planets,
            "routes": len(self.routes),
            "civilizations": len(self.civilizations),
            "factions": len(self.factions),
            "events": len(self.history),
            "ships": len(self.ships),
            "population_millions": round(pop, 1),
            "avg_resource": round(rich, 3),
            "avg_danger": round(danger, 3),
            "year": round(self.year, 2),
        }


# ==============================================================================
# 三、名称生成
# ==============================================================================

_NAME_PREFIX = ("新", "旧", "远", "幽", "赤", "蓝", "银", "黑", "白", "金", "紫", "苍",
                "曦", "朔", "墟", "渊", "霜", "烬", "岚", "雷", "曜", "煌", "寂", "尘")
_NAME_MID = ("河", "海", "域", "环", "崖", "门", "谷", "原", "庭", "巢", "塔", "渊",
             "桥", "脊", "港", "垒", "庭", "岚")
_NAME_SUFFIX = ("座", "星系", "星区", "之门", "枢纽", "边陲", "星团", "回廊", "断崖", "深井")
_LATIN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_SPECIES = ("人类", "泰坦族", "晶族", "硅基族", "拟态体", "深海族", "翼民", "群识体",
            "机械集合", "共生者", "虚空行者", "苔原氏族", "星尘族", "共生真菌", "赛博格")

_CIV_PREFIX = ("阿", "赛", "诺", "克", "维", "泽", "塔", "洛", "伊", "厄", "苏", "弥")
_CIV_SUFFIX = ("联邦", "帝国", "议会", "共同体", "霸权", "圣庭", "同盟", "公国", "共和国", "教团")

_FACTION_PREFIX = ("钢铁", "黎明", "深渊", "苍蓝", "赤红", "银色", "虚空", "新星",
                   "自由", "永恒", "寂静", "黄金", "翡翠", "黑曜", "星火")
_FACTION_SUFFIX = ("联邦", "帝国", "联合体", "理事会", "同盟", "军团", "商会", "教会",
                   "共和国", "督政府")

_SHIPS = ("探索者号", "先驱号", "游隼号", "长风号", "星槎号", "奥德赛号", "回声号",
          "银翼号", "破晓号", "寒鸦号", "织星者号", "寂静航者号")

_ANOMALIES = ("", "", "", "空间褶皱", "周期性引力波", "量子回声", "引力透镜异常",
              "时间流速偏差", "未知信号源", "维度裂缝", "暗物质凝聚晕")

_MISSIONS = ("科学探索", "资源勘探", "外交使节", "贸易运输", "军事侦察", "救援行动", "遗迹调查")

_PLANET_NAME_WORDS = ("新", "赤", "霜", "砂", "澜", "曜", "玄", "翠", "铁", "雾", "曦", "砾")
_PLANET_NAME_TAIL = ("星", "球", "世界", "之地", "殖民地", "前哨", "主星", "卫星")


class NameBook:
    """名称生成器：保证同一次生成内名称唯一。"""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self._used: Set[str] = set()

    def _unique(self, name: str) -> str:
        base = name
        n = 2
        while name in self._used:
            name = f"{base}-{n}"
            n += 1
        self._used.add(name)
        return name

    def system_name(self) -> str:
        """恒星系名称：中文意象 + 编号后缀。"""
        style = self.rng.random()
        if style < 0.42:
            name = (self.rng.choice(_NAME_PREFIX) + self.rng.choice(_NAME_MID)
                    + self.rng.choice(_NAME_SUFFIX))
        elif style < 0.78:
            name = (f"{self.rng.choice(_LATIN_LETTERS)}{self.rng.choice(_LATIN_LETTERS)}-"
                    f"{self.rng.randint(1, 999):03d}")
        else:
            name = (self.rng.choice(_NAME_PREFIX) + self.rng.choice(_NAME_MID)
                    + f"-{self.rng.randint(1, 99)}")
        return self._unique(name)

    def planet_name(self, index: int) -> str:
        """行星名称：恒星系名 + 罗马式序号在外部拼接，这里给核心词。"""
        return self._unique(self.rng.choice(_PLANET_NAME_WORDS)
                            + self.rng.choice(_PLANET_NAME_TAIL))

    def civ_name(self) -> str:
        return self._unique(self.rng.choice(_CIV_PREFIX) + self.rng.choice(_CIV_PREFIX) + self.rng.choice(_CIV_SUFFIX))

    def faction_name(self) -> str:
        return self._unique(self.rng.choice(_FACTION_PREFIX) + self.rng.choice(_FACTION_SUFFIX))

    def species(self) -> str:
        return self.rng.choice(_SPECIES)

    def ship_name(self) -> str:
        return self._unique(self.rng.choice(_SHIPS))

    def galaxy_name(self) -> str:
        return self._unique(self.rng.choice(_NAME_PREFIX) + self.rng.choice(_NAME_MID) + "星系群")


# ==============================================================================
# 四、生成器
# ==============================================================================

class WorldGenerator:
    """架空宇宙生成器。

    用法：
        gen = WorldGenerator(seed=12345, scale="medium")
        galaxy = gen.generate(progress=callback)   # callback(percent:int, message:str)
    """

    def __init__(self, seed: Optional[int] = None, scale: str = "medium",
                 distribution: str = "spiral", density: float = 1.0) -> None:
        self.seed = int(seed if seed is not None else random.randrange(1, 2 ** 31 - 1))
        self.scale = scale if scale in SCALE_PRESETS else "medium"
        self.distribution = distribution if distribution in DISTRIBUTION_MODES else "spiral"
        self.density = clamp(float(density), 0.3, 2.0)
        self.rng = random.Random(self.seed)
        self.names = NameBook(self.rng)
        self.count = SCALE_PRESETS[self.scale]

    # ---------------- 主流程 ----------------

    def generate(self, progress=None) -> Galaxy:
        """生成完整宇宙。progress(percent, message) 可选，用于后台线程上报进度。"""
        def report(pct: int, msg: str) -> None:
            if progress:
                try:
                    progress(int(pct), str(msg))
                except Exception:
                    pass

        report(2, "初始化随机种子…")
        galaxy = Galaxy(seed=self.seed, scale=self.scale, distribution=self.distribution,
                        name=self.names.galaxy_name())
        report(8, "铺设星区拓扑…")
        galaxy.systems = self._make_systems()
        report(30, "生成行星系统…")
        for s in galaxy.systems:
            self._populate_planets(s)
        report(52, "孕育文明与势力…")
        galaxy.factions = self._make_factions(galaxy)
        self._assign_factions(galaxy)
        galaxy.civilizations = self._make_civilizations(galaxy)
        report(72, "铺设航线网络…")
        galaxy.routes = self._make_routes(galaxy)
        report(86, "推演历史事件…")
        galaxy.history = self._make_history(galaxy)
        report(94, "整理星图索引…")
        galaxy.index()
        galaxy.notes = (f"由 STELLAR NEXUS 生成 | 种子 {self.seed} | "
                        f"{SCALE_LABELS_CN[self.scale]} | {DISTRIBUTION_MODES[self.distribution]}")
        report(100, "生成完成")
        LOG.info(f"架空宇宙已生成：种子 {self.seed}，{len(galaxy.systems)} 个恒星系，"
                 f"{len(galaxy.factions)} 个势力，{len(galaxy.routes)} 条航线")
        return galaxy

    # ---------------- 星区拓扑 ----------------

    def _make_systems(self) -> List[StarSystem]:
        """按分布模式生成恒星系坐标。"""
        positions = self._distribute_positions(self.count)
        systems: List[StarSystem] = []
        for i, pos in enumerate(positions):
            systems.append(self._make_system(i, pos))
        return systems

    def _distribute_positions(self, count: int) -> List[Tuple[float, float, float]]:
        """生成 count 个三维坐标点，尺度随星区规模自动扩大。"""
        rng = self.rng
        # 基础半径：让平均间距保持在 25~45 光年
        radius = 45.0 * math.sqrt(count / 40.0) * 1.55
        pts: List[Tuple[float, float, float]] = []
        mode = self.distribution

        if mode == "spiral":
            arms = rng.randint(2, 4)
            for i in range(count):
                arm = i % arms
                t = (i / max(1, count - 1)) ** 0.85
                r = radius * (0.12 + 0.88 * t)
                base_angle = arm * (2.0 * math.pi / arms) + t * 2.6 + rng.gauss(0, 0.10)
                spread = 0.22 * (1.0 - 0.55 * t)
                angle = base_angle + rng.gauss(0, spread)
                rr = clamp(r * (1.0 + rng.gauss(0, 0.10)), 3.0, radius * 1.4)
                z = rng.gauss(0, radius * 0.055)
                pts.append((rr * math.cos(angle), rr * math.sin(angle), z))
        elif mode == "cluster":
            clusters = max(3, count // 14)
            centers = [(rng.uniform(-radius, radius), rng.uniform(-radius, radius),
                        rng.gauss(0, radius * 0.12)) for _ in range(clusters)]
            for i in range(count):
                cx, cy, cz = centers[i % clusters]
                pts.append((cx + rng.gauss(0, radius * 0.16),
                            cy + rng.gauss(0, radius * 0.16),
                            cz + rng.gauss(0, radius * 0.08)))
        elif mode == "filament":
            for i in range(count):
                t = i / max(1, count - 1)
                x = (t - 0.5) * radius * 2.0
                y = math.sin(t * math.pi * 2.4) * radius * 0.42 + rng.gauss(0, radius * 0.10)
                z = math.cos(t * math.pi * 1.7) * radius * 0.28 + rng.gauss(0, radius * 0.08)
                pts.append((x, y, z))
        else:  # random：球内均匀采样
            for _ in range(count):
                while True:
                    x, y, z = (rng.uniform(-1, 1) for _ in range(3))
                    if x * x + y * y + z * z <= 1.0:
                        break
                pts.append((x * radius, y * radius, z * radius))

        # 保证不会有完全重合的点
        cleaned: List[Tuple[float, float, float]] = []
        for p in pts:
            if any(vec3_dist(p, q) < 1.5 for q in cleaned[-12:]):
                p = (p[0] + rng.gauss(0, 4.0), p[1] + rng.gauss(0, 4.0), p[2] + rng.gauss(0, 3.0))
            cleaned.append(p)
        return cleaned

    def _make_system(self, sid: int, pos: Tuple[float, float, float]) -> StarSystem:
        """生成单个恒星系的物理参数。"""
        rng = self.rng
        spec_key, mass, temp, lum, color = self._weighted_spectral()
        # 光谱型细分（如 G2V / K5III）
        sub = rng.randint(0, 9)
        lum_class = rng.choices(("V", "IV", "III", "II", "I"), weights=(62, 10, 18, 6, 4))[0]
        spectral = f"{spec_key}{sub}{lum_class}"
        # 恒星半径：由光度与温度反推（R ∝ sqrt(L)/T²）
        radius_solar = math.sqrt(max(1e-6, lum)) / max(0.02, (temp / 5772.0) ** 2)
        radius_solar = clamp(radius_solar, 0.02, 900.0)
        planet_count = self._planet_count(spec_key)
        sys_obj = StarSystem(
            sid=sid,
            name=self.names.system_name(),
            position=(round(pos[0], 3), round(pos[1], 3), round(pos[2], 3)),
            spectral=spectral, star_class=spec_key,
            mass_solar=round(mass, 3), temperature_k=round(temp, 0),
            luminosity_solar=round(lum, 6), radius_solar=round(radius_solar, 4),
            color=color, planet_count=planet_count,
            resources=sorted(rng.sample(RESOURCE_KINDS, rng.randint(1, 4))),
            resource_richness=round(clamp(rng.gauss(0.5, 0.2), 0.02, 1.0), 3),
            danger=round(clamp(rng.gauss(0.22, 0.16), 0.0, 1.0), 3),
            has_station=rng.random() < 0.34,
            anomaly=rng.choice(_ANOMALIES),
        )
        return sys_obj

    def _weighted_spectral(self) -> Tuple[str, float, float, float, Tuple[int, int, int]]:
        """按权重抽光谱型，再在区间内取值并做「质量-光度」物理一致化。"""
        rng = self.rng
        keys = [row[0] for row in SPECTRAL_TABLE]
        weights = [row[1] for row in SPECTRAL_TABLE]
        key = rng.choices(keys, weights=weights)[0]
        row = next(r for r in SPECTRAL_TABLE if r[0] == key)
        _k, _w, (m_lo, m_hi), (t_lo, t_hi), (l_lo, l_hi), color = row
        mass = rng.uniform(m_lo, m_hi)
        temp = rng.uniform(t_lo, t_hi)
        # 主序质光关系 L ∝ M^3.5；对巨星/白矮星用区间内随机
        lum_main = mass ** 3.5
        if key in ("O", "B", "A", "F", "G", "K", "M"):
            lum = clamp(lum_main * rng.uniform(0.5, 2.2), l_lo, l_hi)
        else:
            lum = rng.uniform(l_lo, l_hi)
        return key, mass, temp, lum, color

    def _planet_count(self, star_class: str) -> int:
        """行星数量：与光谱型相关（模拟金属丰度差异）。"""
        rng = self.rng
        base = {"O": 1.2, "B": 1.8, "A": 2.6, "F": 3.4, "G": 4.2,
                "K": 3.6, "M": 2.8, "L": 1.2, "T": 0.7, "W": 1.0,
                "N": 1.4, "D": 1.6}.get(star_class, 3.0)
        n = rng.randint(0, max(0, int(round(base + rng.gauss(0, 1.3)))))
        return int(clamp(n, 0, 12))

    # ---------------- 行星 ----------------

    def _populate_planets(self, system: StarSystem) -> None:
        """为恒星系生成行星序列（遵循轨道间距递增的规律）。"""
        rng = self.rng
        orbit = rng.uniform(0.22, 0.75)
        for i in range(system.planet_count):
            ptype, type_cn, _w, color = self._weighted_planet_type(system, i)
            gravity, radius, temp, pressure, atmo = self._planet_physics(system, ptype, orbit)
            life = self._life_signs(ptype, temp, pressure, system)
            civ_level = 0
            if life == "智慧生命":
                civ_level = rng.randint(1, min(9, max(2, int(math.log10(max(1e4, system.luminosity_solar * 1e4))))))
            planet = Planet(
                pid=f"S{system.sid:03d}-P{i + 1}",
                name=f"{system.name} {_roman(i + 1)}",
                ptype=ptype, ptype_cn=type_cn,
                orbit_au=round(orbit, 3),
                period_days=round(365.25 * (orbit ** 1.5) / max(0.05, math.sqrt(max(0.02, system.mass_solar))), 2),
                radius_earth=round(radius, 3), gravity_g=round(gravity, 2),
                temperature_c=round(temp, 1), atmosphere=atmo,
                pressure_atm=round(pressure, 3),
                resources=sorted(rng.sample(RESOURCE_KINDS, rng.randint(1, 4))),
                life_signs=life, civ_level=civ_level,
                moons=rng.randint(0, 4 if ptype != "gas" else 22),
                color=color,
                notes=self._planet_note(ptype, temp, life),
            )
            system.planets.append(planet)
            system.resources = sorted(set(system.resources) | set(planet.resources))
            # 下一颗行星的轨道：提丢斯-波得式增长
            orbit *= rng.uniform(1.45, 2.15)
        # 统计人口（有智慧生命的星系才有常住人口）
        pop = 0.0
        for p in system.planets:
            if p.civ_level > 0:
                pop += (10 ** (p.civ_level - 1)) * rng.uniform(0.6, 3.0) * p.habitability
        system.population_millions = round(pop, 3)

    def _weighted_planet_type(self, system: StarSystem, index: int) -> Tuple[str, str, float, Tuple[int, int, int]]:
        """按恒星温度与轨道位置决定行星类型（外侧更冷）。"""
        rng = self.rng
        weights: List[float] = []
        for key, _cn, w, _color in PLANET_TYPES:
            weight = w
            if key == "gas" and index >= 1:
                weight *= 2.4
            if key == "lava" and index == 0:
                weight *= 2.2
            if key == "ice" and index >= 2:
                weight *= 2.0
            if key == "ocean" and system.star_class in ("G", "K", "F"):
                weight *= 1.6
            if key == "artificial" and system.resource_richness > 0.6:
                weight *= 1.5
            if key == "relic" and system.anomaly:
                weight *= 2.5
            weights.append(max(0.05, weight))
        picked = rng.choices(PLANET_TYPES, weights=weights)[0]
        return picked[0], picked[1], picked[2], picked[3]

    def _planet_physics(self, system: StarSystem, ptype: str, orbit_au: float
                        ) -> Tuple[float, float, float, float, str]:
        """计算行星重力、半径、温度、大气压与大气成分。"""
        rng = self.rng
        # 半径（地球半径）
        radius = {"rocky": rng.uniform(0.4, 1.8), "gas": rng.uniform(4.0, 14.0),
                  "ice": rng.uniform(0.6, 3.2), "lava": rng.uniform(0.5, 2.0),
                  "ocean": rng.uniform(0.8, 2.2), "desert": rng.uniform(0.6, 1.8),
                  "artificial": rng.uniform(0.3, 2.6), "relic": rng.uniform(0.4, 3.0),
                  "toxic": rng.uniform(0.7, 2.4)}.get(ptype, 1.0)
        # 密度 → 重力：g ∝ ρ·R
        density = {"gas": 0.25, "ice": 0.9, "rocky": 1.0, "lava": 1.15,
                   "ocean": 0.95, "desert": 0.9, "artificial": 0.75,
                   "relic": 1.3, "toxic": 1.05}.get(ptype, 1.0)
        gravity = clamp(radius * density * rng.uniform(0.85, 1.15), 0.05, 30.0)
        # 平衡温度：T = 278.5 · L^0.25 / sqrt(a)，再叠加温室效应
        base_temp = 278.5 * (max(1e-5, system.luminosity_solar) ** 0.25) / math.sqrt(max(0.02, orbit_au))
        greenhouse = {"rocky": rng.uniform(0.6, 1.35), "gas": rng.uniform(1.2, 2.4),
                      "ice": rng.uniform(0.35, 0.85), "lava": rng.uniform(1.6, 3.2),
                      "ocean": rng.uniform(0.9, 1.4), "desert": rng.uniform(0.8, 1.5),
                      "artificial": rng.uniform(0.9, 1.15), "relic": rng.uniform(0.5, 1.2),
                      "toxic": rng.uniform(1.4, 2.6)}.get(ptype, 1.0)
        temp_k = base_temp * greenhouse
        temp_c = temp_k - 273.15
        # 大气压
        pressure = {"gas": rng.uniform(20.0, 900.0), "ice": rng.uniform(0.0, 1.6),
                    "rocky": rng.uniform(0.0, 4.5), "lava": rng.uniform(0.2, 12.0),
                    "ocean": rng.uniform(0.6, 3.5), "desert": rng.uniform(0.05, 1.4),
                    "artificial": rng.uniform(0.8, 1.3), "relic": rng.uniform(0.1, 2.0),
                    "toxic": rng.uniform(2.0, 60.0)}.get(ptype, 1.0)
        atmo = self._atmosphere(ptype, temp_c, pressure)
        return gravity, radius, temp_c, pressure, atmo

    def _atmosphere(self, ptype: str, temp_c: float, pressure: float) -> str:
        """生成大气成分描述。"""
        rng = self.rng
        if pressure < 0.01:
            return "几乎真空"
        pools = {
            "rocky": ("氮氧混合", "稀薄二氧化碳", "氮气为主"),
            "gas": ("氢氦为主", "氢氦 + 氨云", "氢氦 + 甲烷带"),
            "ice": ("稀薄甲烷", "氮气 + 甲烷雾", "氦氖混合"),
            "lava": ("二氧化硫浓雾", "硅酸盐蒸气", "一氧化碳大气"),
            "ocean": ("氮氧 + 水蒸气", "富氧潮湿大气", "氮气 + 水循环"),
            "desert": ("干燥二氧化碳", "氮气 + 尘暴", "稀薄氩气"),
            "artificial": ("人工调配标准大气", "闭环循环氮氧", "过滤式穹顶大气"),
            "relic": ("未知惰性气体", "含微量异常粒子的氮气", "成分无法识别"),
            "toxic": ("硫化氢 + 氯气", "高浓度氨气", "有机毒雾"),
        }
        base = rng.choice(pools.get(ptype, ("未知混合气体",)))
        if temp_c > 300:
            base += "（高温）"
        elif temp_c < -120:
            base += "（低温冷凝）"
        return base

    def _life_signs(self, ptype: str, temp_c: float, pressure: float, system: WG.StarSystem) -> str:
        """判断生命迹象：宜居带 + 液态水 + 大气压。

        概率设计目标（保证生成出来的宇宙「有戏可看」）：
            * 每颗类地行星约 30%~60% 概率存在生命（取决于宜居度）
            * 其中约 1/5 演化为智慧生命，用于支撑文明与政治系统
            这样 40 星系的星区通常能出现 3~10 个有生命的行星、1~4 个文明。
        """
        rng = self.rng
        if ptype in ("gas", "lava", "toxic", "ice"):
            return rng.choices(("无", "微生物"), weights=(88, 12))[0]
        habitability = math.exp(-((temp_c - 15.0) / 45.0) ** 2) * (1.0 if 0.2 < pressure < 5 else 0.25)
        habitability *= 1.45 if ptype in ("ocean", "rocky", "desert") else 0.6
        habitability = clamp(habitability, 0.0, 1.0)
        r = rng.random()
        if r < habitability * 0.34:
            return "智慧生命"
        if r < habitability * 0.80:
            return "复杂生态"
        if r < habitability * 1.15 + 0.12:
            return "微生物"
        return "无"

    def _planet_note(self, ptype: str, temp_c: float, life: str) -> str:
        """为行星生成一句有画面感的备注。"""
        rng = self.rng
        if ptype == "relic":
            return rng.choice(("地表散布着不明合金结构", "轨道上漂浮着巨型环形残骸",
                               "地核仍在缓慢脉冲，来源未知"))
        if ptype == "artificial":
            return rng.choice(("整颗行星被改造成计算矩阵", "表面由自修复装甲覆盖",
                               "重力场由人工发生器维持"))
        if life == "智慧生命":
            return "探测到大规模能量网络与轨道设施"
        if life == "复杂生态":
            return "赤道带存在大面积植被信号"
        if life == "微生物":
            return "极地冰层下检测到代谢活动"
        if temp_c > 400:
            return "地表熔融，无法登陆"
        if temp_c < -150:
            return "表面被永久冰层覆盖"
        return "荒芜但资源可采"

    # ---------------- 势力 ----------------

    def _make_factions(self, galaxy: Galaxy) -> List[Faction]:
        """生成 2~5 个势力（数量随星区规模增长）。"""
        rng = self.rng
        n = int(clamp(round(len(galaxy.systems) / 38.0) + rng.randint(1, 2), 2, 6))
        palette = ((90, 220, 255), (255, 120, 190), (255, 190, 90), (150, 255, 150),
                   (190, 150, 255), (255, 110, 110), (140, 220, 200))
        factions: List[Faction] = []
        for i in range(n):
            capital = rng.choice(galaxy.systems)
            f = Faction(
                fid=f"F{i + 1}",
                name=self.names.faction_name(),
                color=palette[i % len(palette)],
                ideology=rng.choice(IDEOLOGIES),
                capital=capital.name,
                military=round(clamp(rng.gauss(0.5, 0.2), 0.05, 1.0), 3),
                economy=round(clamp(rng.gauss(0.5, 0.2), 0.05, 1.0), 3),
                tech=round(clamp(rng.gauss(0.5, 0.2), 0.05, 1.0), 3),
                description=(f"以「{rng.choice(IDEOLOGIES)}」为纲领的"
                             f"{rng.choice(('星际政体', '商业联盟', '军事集团', '宗教国度', '技术共同体'))}，"
                             f"首都位于 {capital.name}。"),
            )
            factions.append(f)
        # 外交关系矩阵（对称）
        relations: Dict[Tuple[str, str], str] = {}
        for i, a in enumerate(factions):
            for b in factions[i + 1:]:
                state = rng.choices(DIPLOMACY_STATES, weights=(22, 40, 25, 13))[0]
                relations[(a.name, b.name)] = state
                relations[(b.name, a.name)] = state
        for f in factions:
            f.relations = {other.name: relations.get((f.name, other.name), "中立")
                           for other in factions if other is not f}
        return factions

    def _assign_factions(self, galaxy: Galaxy) -> None:
        """用「最近首都 + 噪声」的方式划分领土，形成自然的势力边界。"""
        rng = self.rng
        if not galaxy.factions:
            return
        for s in galaxy.systems:
            best, best_d = None, 1e18
            for f in galaxy.factions:
                cap = galaxy.system_by_name(f.capital) or galaxy.systems[0]
                d = vec3_dist(s.position, cap.position)
                # 加噪声让边界不规则
                d *= rng.uniform(0.72, 1.35)
                if d < best_d:
                    best_d, best = d, f
            # 部分星系保持中立（无主之地）
            if rng.random() < 0.14:
                s.faction = ""
            else:
                s.faction = best.name if best else ""
        # 统计领土
        for f in galaxy.factions:
            f.member_systems = [s.sid for s in galaxy.systems if s.faction == f.name]

    # ---------------- 文明 ----------------

    def _make_civilizations(self, galaxy: Galaxy) -> List[Civilization]:
        """在拥有生命的星系上建立文明，并分配殖民地。

        候选优先级：
            1. 已有智慧生命的星系（最合适）
            2. 存在复杂生态或大量人口的星系（视为正在崛起的文明）
        这样即便随机种子不巧，星区也至少会有 1 个文明，避免「无主宇宙」的空洞感。
        """
        rng = self.rng
        civs: List[Civilization] = []

        def has_life(s: StarSystem) -> bool:
            return any(p.life_signs != "无" for p in s.planets)

        primary = [s for s in galaxy.systems if s.population_millions > 0]
        secondary = [s for s in galaxy.systems
                     if s not in primary and (has_life(s) or s.has_station or s.resource_richness > 0.55)]
        candidates = primary + secondary
        if not candidates:
            candidates = list(galaxy.systems)
        # 文明数量：星系数量与人口共同决定（至少 1~2 个）
        target = int(clamp(round(len(galaxy.systems) / 24.0) + rng.randint(1, 2), 1, 10))
        target = min(target, len(candidates))
        rng.shuffle(candidates)
        for i, home in enumerate(candidates[:target]):
            # 若该星系尚无智慧生命（来自 secondary），补上一颗有生命迹象的行星
            if home.population_millions <= 0 and home.planets:
                best = max(home.planets, key=lambda p: p.habitability)
                if best.life_signs == "无":
                    best.life_signs = "复杂生态"
                best.civ_level = max(best.civ_level, 2)
                home.population_millions = round(max(1.0, 10.0 * best.habitability), 2)
            tech = int(clamp(rng.gauss(5.5, 2.2), 1, len(TECH_LEVELS) - 1))
            civ = Civilization(
                cid=f"C{i + 1}",
                name=self.names.civ_name(),
                species=self.names.species(),
                tech_level=tech,
                tech_name=TECH_LEVELS[tech],
                government=rng.choice(GOVERNMENT_TYPES),
                ideology=rng.choice(IDEOLOGIES),
                home_system=home.name,
                population_millions=round(max(1.0, home.population_millions * rng.uniform(1.5, 8.0)), 2),
                founded_year=round(rng.uniform(0.0, max(1.0, galaxy.year + 400.0)), 1),
                faction=home.faction,
                traits=sorted(rng.sample(
                    ("长寿种族", "集体意识", "水栖", "硅基代谢", "共生菌群", "能量体",
                     "好战", "乐商", "厌恶接触", "迁徙习性", "强辐射耐受", "梦境共享"),
                    rng.randint(1, 3))),
                notes=f"以 {TECH_LEVELS[tech]} 立足于 {home.name}。",
            )
            # 分配殖民地：同一势力内较近的星系
            home_pos = home.position
            near = sorted((s for s in galaxy.systems if s is not home and s.faction == home.faction),
                          key=lambda s: vec3_dist(home_pos, s.position))
            for s in near[:rng.randint(0, min(5, len(near)))]:
                if rng.random() < 0.55:
                    civ.colonies.append(s.name)
                    s.civ_name = s.civ_name or civ.name
            home.civ_name = civ.name
            civs.append(civ)
        return civs

    # ---------------- 航线 ----------------

    def _make_routes(self, galaxy: Galaxy) -> List[Route]:
        """构建航线网络。

        策略：
            1. 每个恒星系连接最近的 2~4 个邻居（保证图连通）
            2. 额外按距离与资源权重补充随机长航线（10%~25%）
            3. 用并查集保证整个星图连通，不存在孤岛
        """
        rng = self.rng
        systems = galaxy.systems
        n = len(systems)
        if n < 2:
            return []
        existing: Set[Tuple[int, int]] = set()
        routes: List[Route] = []

        def add_route(a: StarSystem, b: StarSystem, kind: str = "trade") -> None:
            key = (min(a.sid, b.sid), max(a.sid, b.sid))
            if key in existing:
                return
            existing.add(key)
            dist = vec3_dist(a.position, b.position)
            # 时间/燃料/风险：距离越远越慢越贵，危险度取两端较高值
            risk = clamp((a.danger + b.danger) / 2.0 * rng.uniform(0.7, 1.3), 0.0, 1.0)
            speed = 12.0  # 标准巡航速度（光年/银河历年）
            routes.append(Route(
                a=a.sid, b=b.sid,
                distance_ly=round(dist, 2),
                travel_days=round(dist / speed * 365.25, 1),
                fuel_cost=round(dist * rng.uniform(0.8, 1.4) * 0.5, 2),
                risk=round(risk, 3),
                kind=kind,
                control=(a.faction if a.faction == b.faction else ""),
            ))

        # 1) 邻域连接
        positions = [s.position for s in systems]
        for i, s in enumerate(systems):
            order = sorted(range(n), key=lambda j: vec3_dist(positions[i], positions[j]) if j != i else 1e9)
            k = rng.randint(2, 4)
            for j in order[:k]:
                add_route(s, systems[j])
        # 2) 随机长航线
        extra = int(n * rng.uniform(0.10, 0.25))
        for _ in range(extra):
            a, b = rng.sample(systems, 2)
            if vec3_dist(a.position, b.position) < 120.0:
                add_route(a, b, kind=rng.choice(("trade", "military", "exploration", "secret")))
        # 3) 连通性修补（并查集）
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        id_to_index = {s.sid: i for i, s in enumerate(systems)}
        for r in routes:
            union(id_to_index[r.a], id_to_index[r.b])
        for i in range(1, n):
            if find(i) != find(0):
                add_route(systems[i], systems[0], kind="exploration")
                union(i, 0)
        return routes

    # ---------------- 历史 ----------------

    def _make_history(self, galaxy: Galaxy) -> List[HistoryEvent]:
        """生成 20~60 条按时间排序的历史事件。"""
        rng = self.rng
        count = int(clamp(len(galaxy.systems) * rng.uniform(0.22, 0.45), 12, 90))
        events: List[HistoryEvent] = []
        fnames = [f.name for f in galaxy.factions] or ["独立殖民者"]
        span = 400.0
        for _ in range(count):
            category, templates, _w = rng.choices(EVENT_TEMPLATES,
                                                  weights=[t[2] for t in EVENT_TEMPLATES])[0]
            sys_obj = rng.choice(galaxy.systems)
            f1 = rng.choice(fnames)
            f2 = rng.choice([x for x in fnames if x != f1] or fnames)
            text = rng.choice(templates).format(sys=sys_obj.name, fac=f1, fac2=f2)
            events.append(HistoryEvent(
                year=round(rng.uniform(-span * 0.35, span), 1),
                category=category, description=text,
                system=sys_obj.name, faction=f1,
            ))
        events.sort(key=lambda e: e.year)
        return events


def _roman(n: int) -> str:
    """整数 → 罗马数字（行星序号用，最多到 XII）。"""
    table = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    out = ""
    for value, sym in table:
        while n >= value:
            out += sym
            n -= value
    return out or "I"


# ==============================================================================
# 五、航线规划与飞船运行
# ==============================================================================

def plan_route(galaxy: Galaxy, start: int, goal: int,
               weight: str = "distance") -> Dict[str, Any]:
    """用 Dijkstra 计算最优航线。

    weight 选择：
        distance  最短距离（默认）
        time      最短时间
        fuel      最省燃料
        risk      最低风险
        safe      综合安全（时间 + 风险加权）

    返回：{"path": [sid...], "distance": 光年, "days": 天, "fuel": 单位,
           "risk": 0~1, "legs": [Route...], "ok": bool, "message": str}
    """
    import heapq

    if galaxy.system(start) is None or galaxy.system(goal) is None:
        return {"ok": False, "message": "起点或终点不存在", "path": [], "legs": []}
    start, goal = int(start), int(goal)
    if start == goal:
        return {"ok": True, "message": "起点与终点相同", "path": [start], "legs": [],
                "distance": 0.0, "days": 0.0, "fuel": 0.0, "risk": 0.0}

    def cost(r: Route) -> float:
        if weight == "time":
            return r.travel_days
        if weight == "fuel":
            return r.fuel_cost
        if weight == "risk":
            # 风险按"每跳失败概率"的平方加权：0.3 的风险得 0.09*100=9 分，
            # 0.8 的风险得 64 分，低风险路线真正占优。
            # （早期版本用 risk*100 做尺度，风险项完全压过距离，规划结果不合理）
            return r.risk * r.risk * 100.0 + r.distance_ly * 0.02
        if weight == "safe":
            # 综合安全：以时间为基础，风险用 1.6 次方惩罚
            return r.travel_days * (0.6 + 2.4 * (r.risk ** 1.6))
        return r.distance_ly

    # 邻接表（带边数据）
    adj: Dict[int, List[Route]] = {}
    for r in galaxy.routes:
        adj.setdefault(r.a, []).append(r)
        adj.setdefault(r.b, []).append(r)

    dist: Dict[int, float] = {start: 0.0}
    prev: Dict[int, Tuple[int, Route]] = {}
    heap: List[Tuple[float, int]] = [(0.0, start)]
    visited: Set[int] = set()
    while heap:
        d, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == goal:
            break
        for r in adj.get(node, ()):
            nxt = r.b if r.a == node else r.a
            nd = d + cost(r)
            if nd < dist.get(nxt, 1e18):
                dist[nxt] = nd
                prev[nxt] = (node, r)
                heapq.heappush(heap, (nd, nxt))

    if goal not in dist:
        return {"ok": False, "message": "两星系之间没有可用航线", "path": [], "legs": []}

    # 回溯路径
    path: List[int] = [goal]
    legs: List[Route] = []
    node = goal
    while node != start:
        p, r = prev[node]
        legs.append(r)
        path.append(p)
        node = p
    path.reverse()
    legs.reverse()
    total_dist = sum(r.distance_ly for r in legs)
    total_days = sum(r.travel_days for r in legs)
    total_fuel = sum(r.fuel_cost for r in legs)
    # 综合风险：严格值 = 1 - Π(1 - ri)；但线性累乘会让 6 跳以上的航线风险逼近 100%，
    # 使长途航线在玩法上"不可接单"。因此对外报出的值改用**单跳几何平均风险**
    # 再做温和放大，严格累乘值单独放在 risk_cumulative 里供参考。
    survival = 1.0
    for r in legs:
        survival *= (1.0 - clamp(r.risk, 0.0, 0.95))
    raw_risk = clamp(1.0 - survival, 0.0, 1.0)
    if legs:
        per_leg = 1.0 - survival ** (1.0 / max(1, len(legs)))
        risk_out = round(clamp(per_leg * (1.0 + 0.06 * (len(legs) - 1)), 0.0, 0.98), 3)
    else:
        risk_out = 0.0
    return {
        "ok": True,
        "message": f"共 {len(legs)} 跳，{format_distance_ly(total_dist)}",
        "path": path, "legs": legs,
        "distance": round(total_dist, 2),
        "days": round(total_days, 1),
        "fuel": round(total_fuel, 2),
        "risk": risk_out,
        "risk_cumulative": round(raw_risk, 3),
    }


def dispatch_ship(galaxy: Galaxy, path: Sequence[int], name: str = "",
                  speed_ly_per_year: float = 12.0, mission: str = "科学探索") -> Ship:
    """按航线派遣飞船。"""
    rng = random.Random(int(galaxy.seed) + len(galaxy.ships) * 977 + int(galaxy.year))
    ship = Ship(
        ship_id=f"SHIP-{len(galaxy.ships) + 1:03d}",
        name=name or f"探险者-{len(galaxy.ships) + 1}",
        route=[int(x) for x in path],
        speed_ly_per_year=max(0.5, float(speed_ly_per_year)),
        mission=mission,
        cargo=rng.choice(("科研仪器", "医疗物资", "稀有矿石", "外交文书", "殖民者", "空载")),
        owner=rng.choice([f.name for f in galaxy.factions] or ["独立"]),
    )
    ship.log.append(f"银河历 {galaxy.year:.1f} 年：从 {_sysname(galaxy, ship.route[0])} 启航，"
                    f"目的地 {_sysname(galaxy, ship.route[-1])}")
    galaxy.ships.append(ship)
    return ship


def _sysname(galaxy: Galaxy, sid: int) -> str:
    s = galaxy.system(sid)
    return s.name if s else f"#{sid}"


def ship_total_distance(galaxy: Galaxy, ship: Ship) -> float:
    """飞船整条航线的总距离。"""
    total = 0.0
    for a, b in zip(ship.route, ship.route[1:]):
        total += galaxy.distance(a, b)
    return total


def advance_ships(galaxy: Galaxy, years: float) -> List[str]:
    """推进飞船位置，返回本次发生的事件描述列表。

    时间推进逻辑：
        · 飞船沿折线航线按 speed_ly_per_year 前进
        · 每跨过一段航线就记录日志，并按危险度与随机数触发事件
    """
    rng = random.Random(int(galaxy.seed * 31 + int(galaxy.year * 1000) + len(galaxy.ships)))
    logs: List[str] = []
    if years <= 0:
        return logs
    for ship in galaxy.ships:
        if ship.arrived or ship.destroyed or len(ship.route) < 2:
            continue
        ship.fuel = max(0.0, ship.fuel - years * 0.6)
        travel = ship.speed_ly_per_year * years
        while travel > 0 and not ship.arrived and not ship.destroyed:
            # 当前所在段
            seg_index = int(ship.progress * (len(ship.route) - 1))
            seg_index = int(clamp(seg_index, 0, len(ship.route) - 2))
            a, b = ship.route[seg_index], ship.route[seg_index + 1]
            seg_len = max(0.001, galaxy.distance(a, b))
            # 段内已走距离
            seg_progress = ship.progress * (len(ship.route) - 1) - seg_index
            remain = seg_len * (1.0 - seg_progress)
            if travel < remain:
                ship.progress += travel / seg_len / (len(ship.route) - 1)
                travel = 0.0
                break
            travel -= remain
            # 到达分段节点
            ship.progress = (seg_index + 1) / (len(ship.route) - 1)
            arr = galaxy.system(b)
            if arr is not None:
                ship.log.append(f"银河历 {galaxy.year:.2f} 年：抵达 {arr.name}")
                logs.append(f"{ship.name} 抵达 {arr.name}")
                # 事件判定：危险度 + 异常 + 随机
                risk = clamp(arr.danger * 0.7 + (0.25 if arr.anomaly else 0.0), 0.0, 0.95)
                if rng.random() < risk * 0.55 + 0.06:
                    ev = _random_ship_event(rng, galaxy, arr, ship)
                    ship.log.append(f"银河历 {galaxy.year:.2f} 年：{ev}")
                    logs.append(f"{ship.name}：{ev}")
                    if "被击毁" in ev:
                        ship.destroyed = True
                        break
            if ship.progress >= 0.999:
                ship.arrived = True
                dst = _sysname(galaxy, ship.route[-1])
                ship.log.append(f"银河历 {galaxy.year:.2f} 年：任务完成，停泊于 {dst}")
                logs.append(f"{ship.name} 完成航程，抵达 {dst}")
                break
    return logs


def _random_ship_event(rng: random.Random, galaxy: Galaxy, system: StarSystem, ship: Ship) -> str:
    """为飞船随机抽取一个事件。"""
    kinds = ("探索", "贸易", "战斗", "外交", "异常", "遗迹觉醒")
    kind = rng.choices(kinds, weights=(24, 20, 16, 14, 18, 8))[0]
    fac = system.faction or "无主之地"
    if kind == "探索":
        return rng.choice([
            f"在 {system.name} 发现未登记的类地行星",
            f"于 {system.name} 外围记录到稀有星云结构",
            f"扫描到 {system.name} 第三行星存在液态水迹象",
        ])
    if kind == "贸易":
        gain = rng.randint(80, 900)
        ship.fuel = min(100.0, ship.fuel + 12.0)
        return f"在 {system.name} 完成交易，获利 {gain} 信用点"
    if kind == "战斗":
        dmg = rng.randint(8, 45)
        ship.hull = max(0.0, ship.hull - dmg)
        if ship.hull <= 0:
            return f"在 {system.name} 遭遇伏击，舰体损毁，飞船被击毁"
        return f"在 {system.name} 遭遇劫掠者，舰体受损 {dmg}%"
    if kind == "外交":
        return f"在 {system.name} 与 {fac} 代表会面，获得通行许可"
    if kind == "异常":
        return rng.choice([
            f"飞船在 {system.name} 经历 3.2 秒的时间膨胀",
            f"{system.name} 的引力场导致导航系统短暂失效",
            f"接收到来自 {system.name} 深处的重复脉冲信号",
        ])
    return rng.choice([
        f"{system.name} 地表的遗迹构造体突然启动，扫描波掠过舰体",
        f"在 {system.name} 唤醒沉睡的守护者，被迫紧急脱离",
    ])


# ==============================================================================
# 六、序列化
# ==============================================================================

def galaxy_to_dict(galaxy: Galaxy) -> Dict[str, Any]:
    """Galaxy → 可 JSON 序列化的字典。"""
    return {
        "format": "stellar-nexus-world",
        "version": 1,
        "seed": galaxy.seed,
        "name": galaxy.name,
        "scale": galaxy.scale,
        "distribution": galaxy.distribution,
        "year": galaxy.year,
        "time_scale": galaxy.time_scale,
        "created_at": galaxy.created_at,
        "notes": galaxy.notes,
        "systems": [
            {
                "sid": s.sid, "name": s.name, "position": list(s.position),
                "spectral": s.spectral, "star_class": s.star_class,
                "mass_solar": s.mass_solar, "temperature_k": s.temperature_k,
                "luminosity_solar": s.luminosity_solar, "radius_solar": s.radius_solar,
                "color": list(s.color), "planet_count": s.planet_count,
                "resources": list(s.resources), "resource_richness": s.resource_richness,
                "danger": s.danger, "faction": s.faction, "civ_name": s.civ_name,
                "population_millions": s.population_millions,
                "has_station": s.has_station, "anomaly": s.anomaly,
                "discovered": s.discovered, "notes": s.notes,
                "planets": [
                    {
                        "pid": p.pid, "name": p.name, "ptype": p.ptype, "ptype_cn": p.ptype_cn,
                        "orbit_au": p.orbit_au, "period_days": p.period_days,
                        "radius_earth": p.radius_earth, "gravity_g": p.gravity_g,
                        "temperature_c": p.temperature_c, "atmosphere": p.atmosphere,
                        "pressure_atm": p.pressure_atm, "resources": list(p.resources),
                        "life_signs": p.life_signs, "civ_level": p.civ_level,
                        "moons": p.moons, "color": list(p.color), "notes": p.notes,
                    } for p in s.planets
                ],
            } for s in galaxy.systems
        ],
        "routes": [
            {"a": r.a, "b": r.b, "distance_ly": r.distance_ly, "travel_days": r.travel_days,
             "fuel_cost": r.fuel_cost, "risk": r.risk, "kind": r.kind, "control": r.control}
            for r in galaxy.routes
        ],
        "civilizations": [asdict(c) for c in galaxy.civilizations],
        "factions": [asdict(f) for f in galaxy.factions],
        "history": [asdict(h) for h in galaxy.history],
        "ships": [
            {"ship_id": s.ship_id, "name": s.name, "route": list(s.route),
             "progress": s.progress, "speed_ly_per_year": s.speed_ly_per_year,
             "cargo": s.cargo, "mission": s.mission, "fuel": s.fuel, "hull": s.hull,
             "log": list(s.log), "arrived": s.arrived, "destroyed": s.destroyed,
             "owner": s.owner}
            for s in galaxy.ships
        ],
    }


def galaxy_from_dict(data: Dict[str, Any]) -> Optional[Galaxy]:
    """字典 → Galaxy（容忍缺字段，尽量恢复）。"""
    try:
        if not isinstance(data, dict) or "systems" not in data:
            return None
        g = Galaxy(
            seed=int(data.get("seed", 0)), name=str(data.get("name", "导入星系")),
            scale=str(data.get("scale", "medium")),
            distribution=str(data.get("distribution", "spiral")),
            year=float(data.get("year", 0.0)), time_scale=float(data.get("time_scale", 1.0)),
            notes=str(data.get("notes", "")), created_at=float(data.get("created_at", time.time())),
        )
        for s in data.get("systems", []):
            pos = s.get("position", [0, 0, 0])
            sys_obj = StarSystem(
                sid=int(s.get("sid", 0)), name=str(s.get("name", "?")),
                position=(float(pos[0]), float(pos[1]), float(pos[2])),
                spectral=str(s.get("spectral", "G2V")), star_class=str(s.get("star_class", "G")),
                mass_solar=float(s.get("mass_solar", 1.0)),
                temperature_k=float(s.get("temperature_k", 5772.0)),
                luminosity_solar=float(s.get("luminosity_solar", 1.0)),
                radius_solar=float(s.get("radius_solar", 1.0)),
                color=tuple(s.get("color", (255, 255, 255)))[:3],  # type: ignore[arg-type]
                planet_count=int(s.get("planet_count", 0)),
                resources=list(s.get("resources", [])),
                resource_richness=float(s.get("resource_richness", 0.5)),
                danger=float(s.get("danger", 0.0)), faction=str(s.get("faction", "")),
                civ_name=str(s.get("civ_name", "")),
                population_millions=float(s.get("population_millions", 0.0)),
                has_station=bool(s.get("has_station", False)),
                anomaly=str(s.get("anomaly", "")), discovered=bool(s.get("discovered", True)),
                notes=str(s.get("notes", "")),
            )
            for p in s.get("planets", []):
                sys_obj.planets.append(Planet(
                    pid=str(p.get("pid", "")), name=str(p.get("name", "")),
                    ptype=str(p.get("ptype", "rocky")), ptype_cn=str(p.get("ptype_cn", "岩质行星")),
                    orbit_au=float(p.get("orbit_au", 1.0)), period_days=float(p.get("period_days", 365.0)),
                    radius_earth=float(p.get("radius_earth", 1.0)), gravity_g=float(p.get("gravity_g", 1.0)),
                    temperature_c=float(p.get("temperature_c", 0.0)),
                    atmosphere=str(p.get("atmosphere", "未知")),
                    pressure_atm=float(p.get("pressure_atm", 1.0)),
                    resources=list(p.get("resources", [])), life_signs=str(p.get("life_signs", "无")),
                    civ_level=int(p.get("civ_level", 0)), moons=int(p.get("moons", 0)),
                    color=tuple(p.get("color", (200, 200, 200)))[:3],  # type: ignore[arg-type]
                    notes=str(p.get("notes", "")),
                ))
            g.systems.append(sys_obj)
        for r in data.get("routes", []):
            g.routes.append(Route(
                a=int(r.get("a", 0)), b=int(r.get("b", 0)),
                distance_ly=float(r.get("distance_ly", 0.0)),
                travel_days=float(r.get("travel_days", 0.0)),
                fuel_cost=float(r.get("fuel_cost", 0.0)), risk=float(r.get("risk", 0.0)),
                kind=str(r.get("kind", "trade")), control=str(r.get("control", "")),
            ))
        for c in data.get("civilizations", []):
            g.civilizations.append(Civilization(
                cid=str(c.get("cid", "")), name=str(c.get("name", "")),
                species=str(c.get("species", "")), tech_level=int(c.get("tech_level", 1)),
                tech_name=str(c.get("tech_name", "")), government=str(c.get("government", "")),
                ideology=str(c.get("ideology", "")), home_system=str(c.get("home_system", "")),
                colonies=list(c.get("colonies", [])),
                population_millions=float(c.get("population_millions", 0.0)),
                founded_year=float(c.get("founded_year", 0.0)), faction=str(c.get("faction", "")),
                traits=list(c.get("traits", [])), notes=str(c.get("notes", "")),
            ))
        for f in data.get("factions", []):
            g.factions.append(Faction(
                fid=str(f.get("fid", "")), name=str(f.get("name", "")),
                color=tuple(f.get("color", (255, 255, 255)))[:3],  # type: ignore[arg-type]
                ideology=str(f.get("ideology", "")), capital=str(f.get("capital", "")),
                member_systems=[int(x) for x in f.get("member_systems", [])],
                military=float(f.get("military", 0.5)), economy=float(f.get("economy", 0.5)),
                tech=float(f.get("tech", 0.5)),
                relations=dict(f.get("relations", {})), description=str(f.get("description", "")),
            ))
        for h in data.get("history", []):
            g.history.append(HistoryEvent(
                year=float(h.get("year", 0.0)), category=str(h.get("category", "")),
                description=str(h.get("description", "")), system=str(h.get("system", "")),
                faction=str(h.get("faction", "")),
            ))
        for s in data.get("ships", []):
            g.ships.append(Ship(
                ship_id=str(s.get("ship_id", "")), name=str(s.get("name", "")),
                route=[int(x) for x in s.get("route", [])], progress=float(s.get("progress", 0.0)),
                speed_ly_per_year=float(s.get("speed_ly_per_year", 12.0)),
                cargo=str(s.get("cargo", "")), mission=str(s.get("mission", "")),
                fuel=float(s.get("fuel", 100.0)), hull=float(s.get("hull", 100.0)),
                log=list(s.get("log", [])), arrived=bool(s.get("arrived", False)),
                destroyed=bool(s.get("destroyed", False)), owner=str(s.get("owner", "")),
            ))
        g.index()
        return g
    except Exception as exc:
        LOG.error("导入世界设定失败", exc)
        return None


def generate_galaxy(seed: Optional[int] = None, scale: str = "medium",
                    distribution: str = "spiral", progress=None) -> Galaxy:
    """便捷函数：一步生成一个宇宙。"""
    return WorldGenerator(seed=seed, scale=scale, distribution=distribution).generate(progress)


def random_seed() -> int:
    """生成一个随机种子（1 ~ 2^31-1）。"""
    return random.randrange(1, 2 ** 31 - 1)


__all__ = [
    "SCALE_PRESETS", "SCALE_LABELS_CN", "DISTRIBUTION_MODES", "SPECTRAL_TABLE",
    "PLANET_TYPES", "RESOURCE_KINDS", "GOVERNMENT_TYPES", "IDEOLOGIES",
    "DIPLOMACY_STATES", "TECH_LEVELS", "EVENT_TEMPLATES",
    "Planet", "StarSystem", "Civilization", "Faction", "Route", "HistoryEvent",
    "Ship", "Galaxy", "NameBook", "WorldGenerator",
    "plan_route", "dispatch_ship", "advance_ships", "ship_total_distance",
    "galaxy_to_dict", "galaxy_from_dict", "generate_galaxy", "random_seed",
]
