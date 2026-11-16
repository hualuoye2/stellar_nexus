# -*- coding: utf-8 -*-
"""
history_sim.py —— 架空宇宙的历史推演
================================================================================
把"历史事件只是一段文字"变成**可推演、可量化、可画曲线**的过程：

    输入：一个 Galaxy（世界生成器产出）+ 推演年数 + 若干政策参数
    输出：SimResult
        · 逐年快照 TimelineSnapshot（总人口 / 各势力星系与人口 / 平均科技 / 航线 / 冲突数）
        · 事件流 SimEvent（殖民 / 科技突破 / 战争 / 瘟疫 / 贸易协定 / 首次接触 / 文明衰亡）
        · 每个势力的完整曲线，可直接画折线图

模型说明（工程近似，追求"合理且有叙事感"，不是社会科学仿真）：

    人口增长   ：逻辑斯蒂增长，年增长率
                 r = base × 宜居度 × 资源 × (1 - 危险度) × 科技加成
                 上限 = 星系承载量（由行星数量与资源决定）
    殖民扩张   ：势力在已有星系的基础上，向相邻（航线连通）星系扩散，
                 成功率取决于距离、目标危险度与自身科技
    科技       ：势力科技值随人口与经济投入增长，跨越阈值时触发"科技突破"事件
    冲突       ：相邻势力在边界星系摩擦，按军力比与随机性判定胜负，可能易主
    事件       ：按权重抽取，保证每 N 年至少有一条"值得记录"的历史

所有随机数都由种子决定，因此同一宇宙 + 同一参数 → 完全可复现。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import world_generator as WG
from .utils import LOG, clamp

# ==============================================================================
# 数据结构
# ==============================================================================

@dataclass
class SimEvent:
    """推演过程中的一条历史事件。"""

    year: float
    category: str                 # 殖民 / 科技 / 战争 / 经济 / 灾变 / 外交 / 文明
    title: str
    detail: str = ""
    faction: str = ""
    system: str = ""
    importance: int = 2           # 1 一般 2 重要 3 转折点

    @property
    def icon(self) -> str:
        return {"殖民": "🚀", "科技": "🔬", "战争": "⚔", "经济": "💰",
                "灾变": "☄", "外交": "🤝", "文明": "🏛"}.get(self.category, "•")


@dataclass
class TimelineSnapshot:
    """某一年的全局状态快照（曲线图的数据点）。"""

    year: float
    population: float = 0.0                            # 总人口（百万）
    faction_systems: Dict[str, int] = field(default_factory=dict)
    faction_population: Dict[str, float] = field(default_factory=dict)
    faction_tech: Dict[str, float] = field(default_factory=dict)
    faction_military: Dict[str, float] = field(default_factory=dict)
    systems_inhabited: int = 0
    routes: int = 0
    conflicts: int = 0
    tech_avg: float = 0.0


@dataclass
class SimResult:
    """一次完整推演的结果。"""

    years: int = 0
    seed: int = 0
    snapshots: List[TimelineSnapshot] = field(default_factory=list)
    events: List[SimEvent] = field(default_factory=list)
    faction_names: List[str] = field(default_factory=list)
    faction_colors: Dict[str, Tuple[int, int, int]] = field(default_factory=dict)
    start_population: float = 0.0
    end_population: float = 0.0
    peak_population: float = 0.0
    collapsed: List[str] = field(default_factory=list)
    notes: str = ""

    # ---- 取曲线用的便捷方法 ----

    def series(self, key: str, faction: str = "") -> List[Tuple[float, float]]:
        """取某条曲线：key ∈ population / tech / military / systems / conflicts。

        faction 非空时取该势力的曲线，否则取全局。
        """
        out: List[Tuple[float, float]] = []
        for snap in self.snapshots:
            if faction:
                if key == "population":
                    v = snap.faction_population.get(faction, 0.0)
                elif key == "tech":
                    v = snap.faction_tech.get(faction, 0.0)
                elif key == "military":
                    v = snap.faction_military.get(faction, 0.0)
                elif key == "systems":
                    v = float(snap.faction_systems.get(faction, 0))
                else:
                    v = 0.0
            else:
                v = {
                    "population": snap.population,
                    "tech": snap.tech_avg,
                    "military": sum(snap.faction_military.values()),
                    "systems": float(snap.systems_inhabited),
                    "conflicts": float(snap.conflicts),
                }.get(key, 0.0)
            out.append((snap.year, float(v)))
        return out

    def summary_text(self) -> str:
        """一段可读的推演总结。"""
        lines = [
            f"推演 {self.years} 年（种子 {self.seed}）",
            f"总人口：{self.start_population:,.1f} → {self.end_population:,.1f} 百万"
            f"（峰值 {self.peak_population:,.1f}）",
        ]
        if self.snapshots:
            first, last = self.snapshots[0], self.snapshots[-1]
            lines.append(f"已开发星系：{first.systems_inhabited} → {last.systems_inhabited}"
                         f"（全图 {len(self.faction_names)} 个势力）")
            lines.append(f"平均科技：{first.tech_avg:.2f} → {last.tech_avg:.2f}")
        if self.collapsed:
            lines.append("已消亡势力：" + "、".join(self.collapsed))
        top = sorted(self.snapshots[-1].faction_population.items(),
                     key=lambda kv: -kv[1])[:3] if self.snapshots else []
        if top:
            lines.append("人口前三：" + "、".join(f"{n}（{p:,.0f} 百万）" for n, p in top))
        lines.append(f"记录事件 {len(self.events)} 条")
        return "\n".join(lines)

    def events_text(self) -> str:
        """事件流纯文本（导出用）。"""
        lines = [self.summary_text(), "=" * 60, ""]
        for e in self.events:
            lines.append(f"{e.year:7.1f} 年  {e.icon} [{e.category}] {e.title}")
            if e.detail:
                lines.append(f"            {e.detail}")
        return "\n".join(lines)


# ==============================================================================
# 推演器
# ==============================================================================

#: 科技等级 → 名称（用于事件描述）
TECH_TIERS: Tuple[str, ...] = (
    "前工业", "蒸汽时代", "电气时代", "信息时代", "早期星际", "恒星际航行",
    "星系际航行", "戴森结构", "后物质文明",
)


class HistorySimulator:
    """历史推演引擎。"""

    def __init__(self, galaxy: WG.Galaxy, years: int = 300, seed: Optional[int] = None,
                 expansion_rate: float = 1.0, conflict_rate: float = 1.0,
                 growth_rate: float = 1.0) -> None:
        self.galaxy = galaxy
        self.years = max(1, int(years))
        self.seed = int(seed if seed is not None else galaxy.seed)
        self.rng = random.Random(self.seed * 7919 + self.years)
        # 政策参数（界面可调）
        self.expansion_rate = clamp(float(expansion_rate), 0.0, 3.0)
        self.conflict_rate = clamp(float(conflict_rate), 0.0, 3.0)
        self.growth_rate = clamp(float(growth_rate), 0.1, 3.0)

    # ---------------------------------------------------------------- 主流程

    def run(self, progress=None) -> SimResult:
        """执行推演。progress(done, total) 可用于更新进度条。

        为**可复现**（同种子 + 同参数 → 同结果），推演前保存宇宙的可变状态，
        结束后还原。否则第二次推演会从上一次的结局继续，结果必然不同
        （早期版本存在这个缺陷）。
        """
        g = self.galaxy
        if not hasattr(g, "_by_id"):
            g.index()
        saved = self._save_state()
        try:
            return self._run_inner(progress)
        finally:
            self._restore_state(saved)

    # ---- 状态保存 / 还原 ----

    def _save_state(self) -> Dict[str, object]:
        """保存会被推演修改的字段。"""
        g = self.galaxy
        return {
            "year": g.year,
            "population": {s.sid: s.population_millions for s in g.systems},
            "faction": {s.sid: s.faction for s in g.systems},
            "fac_tech": {f.name: f.tech for f in g.factions},
            "fac_mil": {f.name: f.military for f in g.factions},
            "fac_eco": {f.name: f.economy for f in g.factions},
            "fac_members": {f.name: list(f.member_systems) for f in g.factions},
            "factions": list(g.factions),
            "history_len": len(g.history),
        }

    def _restore_state(self, saved: Dict[str, object]) -> None:
        """还原宇宙状态。"""
        g = self.galaxy
        g.year = float(saved["year"])
        for sid, pop in dict(saved["population"]).items():
            sys_obj = g.system(sid)
            if sys_obj is not None:
                sys_obj.population_millions = float(pop)
        for sid, fac in dict(saved["faction"]).items():
            sys_obj = g.system(sid)
            if sys_obj is not None:
                sys_obj.faction = str(fac)
        g.factions = list(saved["factions"])
        tech_map = dict(saved["fac_tech"])
        mil_map = dict(saved["fac_mil"])
        eco_map = dict(saved["fac_eco"])
        mem_map = dict(saved["fac_members"])
        for f in g.factions:
            f.tech = float(tech_map.get(f.name, f.tech))
            f.military = float(mil_map.get(f.name, f.military))
            f.economy = float(eco_map.get(f.name, f.economy))
            f.member_systems = list(mem_map.get(f.name, f.member_systems))
        del g.history[int(saved["history_len"]):]
        g.index()

    def _run_inner(self, progress=None) -> SimResult:
        g = self.galaxy
        res = SimResult(years=self.years, seed=self.seed)
        res.faction_names = [f.name for f in g.factions]
        res.faction_colors = {f.name: f.color for f in g.factions}

        # ---- 初始化势力状态 ----
        state: Dict[str, Dict[str, float]] = {}
        for f in g.factions:
            members = [g.system(sid) for sid in f.member_systems]
            pop = sum((s.population_millions if s else 0.0) for s in members)
            state[f.name] = {
                "pop": pop, "tech": float(f.tech), "mil": float(f.military),
                "eco": float(f.economy), "systems": float(len(f.member_systems)),
                "capital": f.capital,
            }
        start_pop = sum(s.population_millions for s in g.systems)
        res.start_population = start_pop

        # ---- 起始快照 ----
        res.snapshots.append(self._snapshot(g.year, state))
        events: List[SimEvent] = []
        alive: Dict[str, bool] = {name: True for name in state}

        for step in range(1, self.years + 1):
            year = g.year + step
            # 1) 人口演化
            self._grow_population(year, state, events)
            # 2) 殖民扩张
            if self.rng.random() < 0.35 * self.expansion_rate:
                self._expand(year, state, events)
            # 3) 科技推进
            self._advance_tech(year, state, events)
            # 4) 冲突
            if self.rng.random() < 0.06 * self.conflict_rate:
                self._conflict(year, state, events, alive)
            # 5) 随机事件
            self._random_event(year, state, events)
            # 6) 势力存亡判定
            for name in list(state):
                if alive.get(name) and state[name]["pop"] < 1.0:
                    alive[name] = False
                    res.collapsed.append(name)
                    events.append(SimEvent(
                        year=year, category="文明", title=f"{name} 文明消亡",
                        detail="人口跌破维持文明延续的下限，势力从星图上消失",
                        faction=name, importance=3))
                    state.pop(name, None)
            snap = self._snapshot(year, state)
            snap.conflicts = sum(1 for e in events if e.category == "战争"
                                 and abs(e.year - year) < 0.5)
            res.snapshots.append(snap)
            if progress is not None and (step % 5 == 0 or step == self.years):
                progress(step, self.years)

        res.events = sorted(events, key=lambda e: e.year)
        if res.snapshots:
            res.end_population = res.snapshots[-1].population
            res.peak_population = max(s.population for s in res.snapshots)
        return res

    # ---------------------------------------------------------------- 各阶段

    def _snapshot(self, year: float, state: Dict[str, Dict[str, float]]) -> TimelineSnapshot:
        g = self.galaxy
        snap = TimelineSnapshot(year=float(year))
        for name, st in state.items():
            snap.faction_systems[name] = int(st["systems"])
            snap.faction_population[name] = round(st["pop"], 2)
            snap.faction_tech[name] = round(st["tech"], 3)
            snap.faction_military[name] = round(st["mil"], 3)
        snap.population = round(sum(st["pop"] for st in state.values())
                                + sum(s.population_millions for s in g.systems
                                      if not s.faction), 2)
        snap.systems_inhabited = sum(1 for s in g.systems if s.population_millions > 0)
        snap.routes = len(g.routes)
        if state:
            snap.tech_avg = round(sum(st["tech"] for st in state.values()) / len(state), 3)
        return snap

    def _grow_population(self, year: float, state: Dict[str, Dict[str, float]],
                         events: List[SimEvent]) -> None:
        """逐年人口演化：逻辑斯蒂增长 + 灾变。"""
        g = self.galaxy
        for sys_obj in g.systems:
            if sys_obj.population_millions <= 0:
                continue
            # 承载量：由行星数量与资源决定（百万）
            capacity = 40.0 + sys_obj.planet_count * 55.0 + sys_obj.resource_richness * 900.0
            habitability = 0.55
            if sys_obj.planets:
                habitability = max(0.15, sum(p.habitability for p in sys_obj.planets)
                                   / len(sys_obj.planets))
            owner = sys_obj.faction
            tech = state.get(owner, {}).get("tech", 0.5) if owner else 0.4
            r = (0.006 * self.growth_rate * (0.6 + habitability) *
                 (0.5 + sys_obj.resource_richness) * (1.0 - 0.6 * sys_obj.danger) *
                 (0.7 + 0.9 * tech))
            pop = sys_obj.population_millions
            grow = r * pop * (1.0 - pop / max(1.0, capacity))
            # 灾变：低概率人口骤减
            if self.rng.random() < 0.004 * (0.5 + sys_obj.danger):
                loss = pop * self.rng.uniform(0.05, 0.30)
                pop -= loss
                if loss > 30 and len(events) < 4000:
                    events.append(SimEvent(
                        year=year, category="灾变",
                        title=f"{sys_obj.name} 遭遇灾变",
                        detail=f"人口减少 {loss:,.0f} 百万"
                               + ("（疫情）" if self.rng.random() < 0.5 else "（天体撞击）"),
                        faction=owner, system=sys_obj.name,
                        importance=2 if loss > 80 else 1))
            else:
                pop += grow
            sys_obj.population_millions = max(0.0, pop)
        # 同步到势力
        for name, st in state.items():
            st["pop"] = sum(s.population_millions for s in g.systems if s.faction == name)

    def _expand(self, year: float, state: Dict[str, Dict[str, float]],
                events: List[SimEvent]) -> None:
        """殖民扩张：势力向相邻星系扩散。"""
        g = self.galaxy
        for fidx, fac in enumerate(g.factions):
            name = fac.name
            st = state.get(name)
            if st is None:
                continue
            owned = [s.sid for s in g.systems if s.faction == name]
            if not owned:
                continue
            frontier: List[int] = []
            for sid in owned:
                for nb in g.neighbors(sid):
                    target = g.system(nb)
                    if target is not None and not target.faction:
                        frontier.append(nb)
            if not frontier:
                continue
            target = g.system(self.rng.choice(frontier))
            if target is None:
                continue
            dist = min(g.distance(sid, target.sid) for sid in owned) or 5.0
            # 成功率：科技越高、距离越近、危险度越低越容易
            chance = clamp(0.85 * self.expansion_rate * (0.5 + st["tech"])
                           * math.exp(-dist / 45.0) * (1.0 - 0.7 * target.danger), 0.02, 0.97)
            if self.rng.random() > chance:
                continue
            target.faction = name
            target.population_millions = max(target.population_millions,
                                             self.rng.uniform(2.0, 18.0) * (0.5 + st["tech"]))
            # 必须同步写回势力的成员列表，否则星图与信息面板看不到新增星系
            # （早期版本只改了 local state，导致"势力星系数 38"与"星系 15"对不上）
            for fac in g.factions:
                if fac.name == name and target.sid not in fac.member_systems:
                    fac.member_systems.append(target.sid)
                    break
            st["systems"] += 1
            st["pop"] += target.population_millions
            if len(events) < 4000:
                events.append(SimEvent(
                    year=year, category="殖民", title=f"{name} 殖民 {target.name}",
                    detail=f"越过 {dist:.1f} 光年建立新定居点（"
                           f"资源 {target.resource_richness * 100:.0f}%，"
                           f"危险 {target.danger * 100:.0f}%）",
                    faction=name, system=target.name,
                    importance=2 if target.danger > 0.5 else 1))

    def _advance_tech(self, year: float, state: Dict[str, Dict[str, float]],
                      events: List[SimEvent]) -> None:
        """科技推进：投入 = 人口 × 经济，跨越 Tier 触发突破事件。"""
        for name, st in state.items():
            invest = (math.log10(1.0 + st["pop"]) * 0.00045 + 0.00012) * (0.6 + st["eco"])
            before = st["tech"]
            st["tech"] = clamp(before + invest, 0.0, 1.0)
            # 每 0.1 视为一个小台阶
            if int(before * 10) != int(st["tech"] * 10):
                tier = int(clamp(st["tech"] * (len(TECH_TIERS) - 1), 0,
                                 len(TECH_TIERS) - 1))
                if st["tech"] >= 0.999:
                    continue
                if len(events) >= 4000:
                    continue
                events.append(SimEvent(
                    year=year, category="科技",
                    title=f"{name} 进入 {TECH_TIERS[tier]} 阶段",
                    detail=f"科技指数 {st['tech'] * 100:.0f}%（人口 "
                           f"{st['pop']:,.0f} 百万支撑）",
                    faction=name, importance=2 if tier >= 5 else 1))
            # 经济随人口与科技增长
            st["eco"] = clamp(st["eco"] + 0.004 * (0.5 + st["tech"]), 0.0, 1.0)

    def _conflict(self, year: float, state: Dict[str, Dict[str, float]],
                  events: List[SimEvent], alive: Dict[str, bool]) -> None:
        """势力冲突：争夺边界星系。"""
        g = self.galaxy
        names = [n for n in state if alive.get(n, True)]
        if len(names) < 2:
            return
        a_name, b_name = self.rng.sample(names, 2)
        a_sys = [s for s in g.systems if s.faction == a_name]
        b_sys = [s for s in g.systems if s.faction == b_name]
        if not a_sys or not b_sys:
            return
        # 找距离最近的星系对
        best: Optional[Tuple[float, WG.StarSystem, WG.StarSystem]] = None
        for sa in a_sys:
            for sb in b_sys:
                d = g.distance(sa.sid, sb.sid)
                if best is None or d < best[0]:
                    best = (d, sa, sb)
        if best is None:
            return
        dist, sa, sb = best
        if dist > 60.0:                      # 太远打不起来
            return
        st_a, st_b = state[a_name], state[b_name]
        power_a = st_a["mil"] * (1.0 + st_a["tech"]) * math.log10(2.0 + st_a["pop"])
        power_b = st_b["mil"] * (1.0 + st_b["tech"]) * math.log10(2.0 + st_b["pop"])
        if power_a + power_b <= 0:
            return
        # 攻击方按实力优势决定胜率
        p_a = power_a / (power_a + power_b)
        attacker_wins = self.rng.random() < p_a
        winner, loser = (a_name, b_name) if attacker_wins else (b_name, a_name)
        w_state, l_state = state[winner], state[loser]
        # 争夺的星系：败方距离战场最近的星系
        l_sys = [s for s in g.systems if s.faction == loser]
        if not l_sys:
            return
        contested = min(l_sys, key=lambda s: g.distance(s.sid, sa.sid if winner == a_name else sb.sid))
        contested.faction = winner
        for fac in g.factions:
            if fac.name == loser and contested.sid in fac.member_systems:
                fac.member_systems.remove(contested.sid)
            elif fac.name == winner and contested.sid not in fac.member_systems:
                fac.member_systems.append(contested.sid)
        w_state["systems"] += 1
        l_state["systems"] = max(0.0, l_state["systems"] - 1)
        w_state["mil"] = clamp(w_state["mil"] + 0.01, 0.0, 1.0)
        l_state["mil"] = clamp(l_state["mil"] - 0.03, 0.0, 1.0)
        # 战争损失人口
        loss = min(l_state["pop"] * 0.04, 400.0)
        l_state["pop"] = max(0.0, l_state["pop"] - loss)
        killed = min(w_state["pop"] * 0.02, 200.0)
        w_state["pop"] = max(0.0, w_state["pop"] - killed)
        if len(events) < 4000:
            events.append(SimEvent(
                year=year, category="战争",
                title=f"{winner} 夺取 {contested.name}",
                detail=f"与 {loser} 争夺，双方合计损失约 {loss + killed:,.0f} 百万人口；"
                       f"战场距双方核心 {dist:.1f} 光年",
                faction=winner, system=contested.name, importance=3))

    def _random_event(self, year: float, state: Dict[str, Dict[str, float]],
                      events: List[SimEvent]) -> None:
        """按权重抽取通用事件（外交 / 经济 / 异常）。"""
        if not state or len(events) >= 4000:
            return
        if self.rng.random() > 0.10:
            return
        names = list(state)
        name = self.rng.choice(names)
        st = state[name]
        kind = self.rng.choices(("外交", "经济", "文明"), weights=(4, 4, 2))[0]
        g = self.galaxy
        if kind == "外交" and len(names) >= 2:
            other = self.rng.choice([n for n in names if n != name])
            pact = self.rng.choice(("签署贸易协定", "结成军事同盟", "划定中立区", "爆发边境摩擦"))
            st["eco"] = clamp(st["eco"] + (0.02 if "贸易" in pact else 0.0), 0.0, 1.0)
            events.append(SimEvent(
                year=year, category="外交", title=f"{name} 与 {other} {pact}",
                detail="双方关系随边界接触频次上升而变化",
                faction=name, importance=2 if "同盟" in pact or "摩擦" in pact else 1))
        elif kind == "经济":
            boom = self.rng.random() < 0.6
            factor = self.rng.uniform(0.06, 0.18) * (1 if boom else -1)
            st["pop"] = max(0.0, st["pop"] * (1.0 + factor))
            st["eco"] = clamp(st["eco"] + (0.03 if boom else -0.05), 0.0, 1.0)
            events.append(SimEvent(
                year=year, category="经济",
                title=f"{name} {'贸易繁荣' if boom else '经济衰退'}",
                detail=f"人口{'增长' if boom else '减少'} {abs(factor) * 100:.1f}%",
                faction=name, importance=1))
        else:
            sys_obj = self.rng.choice([s for s in g.systems if s.faction == name]
                                      or g.systems)
            events.append(SimEvent(
                year=year, category="文明",
                title=f"{sys_obj.name} 出现{self.rng.choice(('远古遗迹', '未知信号', '技术奇点', '宗教改革'))}",
                detail=f"归属 {name}，当地人口 {sys_obj.population_millions:,.0f} 百万",
                faction=name, system=sys_obj.name, importance=2))

    def apply_to_galaxy(self, result: SimResult) -> None:
        """把推演结果写回宇宙（人口 / 势力 / 年份 / 历史事件）。"""
        g = self.galaxy
        g.year = result.snapshots[-1].year if result.snapshots else g.year
        for name in result.collapsed:
            for fac in list(g.factions):
                if fac.name == name:
                    g.factions.remove(fac)
        for e in result.events:
            g.history.append(WG.HistoryEvent(
                year=e.year, category=e.category, description=f"{e.title}　{e.detail}",
                system=e.system, faction=e.faction))


# ==============================================================================
# 便捷入口
# ==============================================================================

def simulate(galaxy: WG.Galaxy, years: int = 300, seed: Optional[int] = None,
             expansion_rate: float = 1.0, conflict_rate: float = 1.0,
             growth_rate: float = 1.0, progress=None) -> SimResult:
    """对宇宙做一次推演（不修改原宇宙，除非调用 apply_to_galaxy）。"""
    sim = HistorySimulator(galaxy, years, seed, expansion_rate, conflict_rate, growth_rate)
    return sim.run(progress)


__all__ = ["SimEvent", "TimelineSnapshot", "SimResult", "HistorySimulator",
           "simulate", "TECH_TIERS"]
