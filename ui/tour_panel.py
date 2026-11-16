# -*- coding: utf-8 -*-
"""
ui/tour_panel.py —— 星空导览面板（左侧）
================================================================================
把「今晚值得看什么」变成一个可点击的清单：

    * 自动从当前天空中挑选最多 12 个目标（行星 / 月球 / 深空天体 / 亮星）
    * 每个目标给出评分理由（高度合适、当晚最亮、本季最佳、远离月光…）
    * 逐条点击 → 主星图自动转向并高亮，同时右侧显示详情
    * 提供「下一个 / 上一个」按钮，像讲解员一样带着看

评分模型（权重写在注释里，方便调参）：
    高度        ：45°~70° 最佳，过低有大气消光、过高接近天顶不便观测
    明亮程度    ：按类型给不同的星等阈值（行星 -2 等最佳、深空 6 等最佳）
    类型权重    ：行星/月球 > 深空天体 > 亮星（行星与深空更有"看点"）
    特殊加分    ：行星接近冲日、月相处于上弦/盈凸、深空天体属 M 编号经典目标
    月光惩罚    ：深空天体在月光下会被淹没，按与月亮的角距与月相衰减
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout,
                               QWidget)

from 核心 import astronomy as A
from 核心 import planet_data as P
from 核心 import sky_events as SE
from 核心 import star_data as SD
from 核心.utils import LOG, azimuth_to_compass, clamp, format_distance_ly

from .widgets import GlowButton, InfoRow, SeparatorLine, SectionTitle


class TourPanel(QWidget):
    """星空导览：生成并逐步讲解今晚的目标清单。"""

    target_chosen = Signal(dict)        # 请求主窗口定位到该目标
    status_message = Signal(str)

    MAX_TARGETS = 12

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._targets: List[SE.TourTarget] = []
        self._index = -1
        self._observer: Optional[A.Observer] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(4)

        head = QHBoxLayout()
        self.btn_refresh = GlowButton("生成导览", "violet", compact=True)
        self.btn_refresh.clicked.connect(self.refresh)
        head.addWidget(self.btn_refresh)
        self.btn_prev = GlowButton("◀ 上一个", "cyan", compact=True)
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next = GlowButton("下一个 ▶", "cyan", compact=True)
        self.btn_next.clicked.connect(lambda: self._step(1))
        head.addWidget(self.btn_prev)
        head.addWidget(self.btn_next)
        layout.addLayout(head)

        self.summary = QLabel("点击「生成导览」，程序会按当前时间与地点挑出今晚值得看的目标。")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#a9c9da;background:transparent;")
        self.summary.setFont(QFont("Microsoft YaHei UI", 9))
        layout.addWidget(self.summary)

        self.list = QListWidget()
        self.list.setMinimumHeight(200)
        self.list.setStyleSheet(
            "QListWidget{background:rgba(6,18,28,225);border:1px solid rgba(0,170,210,150);"
            "border-radius:3px;color:#e6f6fd;}"
            "QListWidget::item{padding:3px 4px;border-bottom:1px solid rgba(0,120,160,60);}"
            "QListWidget::item:selected{background:rgba(160,110,255,150);color:#ffffff;}")
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.itemDoubleClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list, 1)

        layout.addWidget(SeparatorLine("violet"))
        self.detail = QWidget()
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(1)
        self._rows: Dict[str, InfoRow] = {}
        for key, label in (("name", "目标"), ("type", "类型"), ("pos", "位置"),
                           ("mag", "亮度"), ("why", "看点"), ("extra", "备注")):
            row = InfoRow(label)
            self._rows[key] = row
            detail_layout.addWidget(row)
        layout.addWidget(self.detail)

    # ==========================================================================
    # 生成导览
    # ==========================================================================

    def set_observer(self, observer: A.Observer) -> None:
        self._observer = observer

    def refresh(self, observer: Optional[A.Observer] = None) -> int:
        """扫描当前天空并生成目标清单。"""
        obs = observer or self._observer
        if obs is None:
            self.status_message.emit("缺少观测点信息，无法生成导览")
            return 0
        self._observer = obs
        try:
            sky = A.compute_sky(
                obs, SD.ALL_SKY_STARS, planets=tuple(P.NAKED_EYE_PLANETS),
                messier=SD.DEEP_SKY_CATALOG, include_sun_moon=True)
            moon = next((o for o in sky["sun_moon"] if o.kind == "moon"), None)
            targets: List[SE.TourTarget] = []
            targets += self._score_planets(sky["planets"], obs)
            targets += self._score_moon(moon, obs)
            targets += self._score_deep_sky(sky["deep_sky"], moon, obs)
            targets += self._score_stars(sky["stars"], moon, obs)
            targets.sort(key=lambda t: -t.score)
            self._targets = targets[: self.MAX_TARGETS]
            self._index = -1
            self._fill_list()
            hour = obs.when.strftime("%H:%M")
            self.summary.setText(
                f"{obs.location_name}　{obs.when.strftime('%Y-%m-%d')} {hour}　"
                f"共挑出 {len(self._targets)} 个目标（按可观测性排序）")
            self.status_message.emit(
                f"导览已生成：{len(self._targets)} 个目标，"
                f"最高分 {self._targets[0].name if self._targets else '—'}")
            return len(self._targets)
        except Exception as exc:
            LOG.error("生成星空导览失败", exc)
            self.status_message.emit(f"生成导览失败：{exc}")
            return 0

    # ---- 各类目标的评分 ----

    def _altitude_score(self, alt: float) -> float:
        """高度得分：45°~70° 最高，地平附近与天顶都扣分。"""
        if alt < 8:
            return 0.0
        if alt < 25:
            return clamp((alt - 8) / 17.0, 0.0, 1.0) * 0.55
        if alt <= 70:
            return 1.0
        return clamp(1.0 - (alt - 70) / 25.0, 0.45, 1.0)

    def _moon_penalty(self, ra: float, dec: float, moon: Optional[A.SkyObject]) -> float:
        """月光对深空天体的影响：0（无影响）~ 1（完全淹没）。"""
        if moon is None or moon.alt < 0:
            return 0.0
        sep = _sep(ra, dec, moon.ra, moon.dec)
        phase = float(moon.extra.get("phase", 0.5) or 0.5)
        # 角距 30° 以内且月相越满，惩罚越重
        near = clamp(1.0 - sep / 60.0, 0.0, 1.0)
        return clamp(near * phase * 0.9, 0.0, 1.0)

    def _score_planets(self, planets: Sequence[A.SkyObject],
                       obs: A.Observer) -> List[SE.TourTarget]:
        out: List[SE.TourTarget] = []
        jd = obs.jd_ut
        for obj in planets:
            if obj.alt < 8.0:
                continue
            key = obj.key.split(":")[-1]
            cn = P.PLANET_INFO.get(key, {}).get("name_cn", obj.name)
            mag_score = clamp((2.0 - obj.mag) / 6.0, 0.1, 1.0)
            score = self._altitude_score(obj.alt) * 1.6 + mag_score * 0.9 + 0.5
            reasons = [f"高度 {obj.alt:.0f}°"]
            info = P.planet_summary(key, jd)
            dist = float(info.get("geocentric_distance_au", 0.0) or 0.0)
            if dist and dist < 2.0 and key not in ("mercury", "venus"):
                score += 0.6
                reasons.append(f"接近冲日（{dist:.2f} AU）")
            if key in ("jupiter", "saturn"):
                reasons.append("表面细节最适合望远镜")
            if obj.mag < -1.0:
                reasons.append(f"极亮（{obj.mag:+.1f} 等）")
            detail = {
                "key": obj.key, "ra": obj.ra, "dec": obj.dec,
                "type": "行星", "mag": obj.mag,
                "extra": f"{info.get('type', '')}　距地 {dist:.3f} AU",
            }
            out.append(SE.TourTarget(key=obj.key, name=cn, kind="planet",
                                     ra=obj.ra, dec=obj.dec, alt=obj.alt, az=obj.az,
                                     mag=obj.mag, score=score,
                                     reason="；".join(reasons), detail=detail))
        return out

    def _score_moon(self, moon: Optional[A.SkyObject],
                    obs: A.Observer) -> List[SE.TourTarget]:
        if moon is None or moon.alt < 8.0:
            return []
        phase = float(moon.extra.get("phase", 0.5) or 0.5)
        name = str(moon.extra.get("phase_name", ""))
        # 上弦/盈凸最适合观测（傍晚到前半夜，且地形阴影明显）
        best = 0.25 <= phase <= 0.75
        score = self._altitude_score(moon.alt) * 1.4 + (0.8 if best else 0.3) + 0.4
        reason = f"月相 {name}（照亮 {phase * 100:.0f}%），{('明暗交界处环形山最清晰' if best else '地平附近适合与地景同框')}"
        detail = {"key": "moon", "ra": moon.ra, "dec": moon.dec, "type": "月球",
                  "mag": moon.mag, "extra": f"距离 {moon.extra.get('distance_km', 0):,.0f} 公里"}
        return [SE.TourTarget(key="moon", name="月球", kind="moon", ra=moon.ra, dec=moon.dec,
                              alt=moon.alt, az=moon.az, mag=moon.mag, score=score,
                              reason=reason, detail=detail)]

    def _score_deep_sky(self, objects: Sequence[A.SkyObject],
                        moon: Optional[A.SkyObject], obs: A.Observer) -> List[SE.TourTarget]:
        out: List[SE.TourTarget] = []
        for obj in objects:
            if obj.alt < 15.0:
                continue
            penalty = self._moon_penalty(obj.ra, obj.dec, moon)
            if penalty > 0.72:
                continue
            mtype = str(obj.extra.get("mtype", ""))
            # 经典目标加分：Messier 编号 + 视面积较大的星团/星云
            classic = obj.name.startswith("M") and not obj.name.startswith("NGC")
            kind_weight = 1.0 if ("星团" in mtype or "星云" in mtype) else 0.8
            mag_score = clamp((10.0 - obj.mag) / 6.0, 0.15, 1.0)
            score = (self._altitude_score(obj.alt) * 1.2 + mag_score * 1.0
                     + (0.7 if classic else 0.2) + kind_weight * 0.3) * (1.0 - penalty)
            reasons = [f"高度 {obj.alt:.0f}°", mtype]
            if classic:
                reasons.append("Messier 经典目标")
            if penalty > 0.25:
                reasons.append(f"受月光影响（{penalty * 100:.0f}%）")
            detail = {"key": obj.key, "ra": obj.ra, "dec": obj.dec,
                      "type": f"深空天体 · {mtype}", "mag": obj.mag,
                      "extra": str(obj.extra.get("desc", ""))}
            out.append(SE.TourTarget(key=obj.key, name=f"{obj.name} {obj.name_cn}",
                                     kind="messier", ra=obj.ra, dec=obj.dec,
                                     alt=obj.alt, az=obj.az, mag=obj.mag, score=score,
                                     reason="；".join(reasons), detail=detail))
        return out

    def _score_stars(self, stars: Sequence[A.SkyObject],
                     moon: Optional[A.SkyObject], obs: A.Observer) -> List[SE.TourTarget]:
        out: List[SE.TourTarget] = []
        for obj in stars:
            if obj.alt < 25.0 or obj.mag > 1.6:
                continue
            # 只推荐有中文名的亮星，避免清单里出现无名暗星
            display = obj.name or ""
            if not display or display.startswith("HIP"):
                continue
            penalty = self._moon_penalty(obj.ra, obj.dec, moon) * 0.4   # 亮星受月光影响小
            mag_score = clamp((2.2 - obj.mag) / 2.5, 0.2, 1.0)
            score = (self._altitude_score(obj.alt) * 1.1 + mag_score * 1.1 + 0.25) * (1.0 - penalty)
            reasons = [f"高度 {obj.alt:.0f}°", f"{obj.mag:+.2f} 等亮星"]
            if obj.spectral:
                reasons.append(f"光谱型 {obj.spectral}")
            if obj.bv > 1.3:
                reasons.append("明显的橙红色，适合目视辨认")
            elif obj.bv < 0.1:
                reasons.append("蓝白色，双筒可见其伴星")
            detail = {"key": obj.key, "ra": obj.ra, "dec": obj.dec, "type": "亮星",
                      "mag": obj.mag,
                      "extra": (f"{obj.spectral}　"
                                + (format_distance_ly(obj.distance) if obj.distance else "距离未知"))}
            out.append(SE.TourTarget(key=obj.key, name=display, kind="star",
                                     ra=obj.ra, dec=obj.dec, alt=obj.alt, az=obj.az,
                                     mag=obj.mag, score=score,
                                     reason="；".join(reasons), detail=detail))
        return out

    # ==========================================================================
    # 清单与交互
    # ==========================================================================

    def _fill_list(self) -> None:
        self.list.clear()
        for i, t in enumerate(self._targets, 1):
            item = QListWidgetItem(f"{i:2d}. {t.name}　{t.alt:+.0f}°　{t.mag:+.1f} 等")
            item.setForeground(QColor(230, 246, 253))
            item.setToolTip(t.reason)
            self.list.addItem(item)
        if self._targets:
            self.list.setCurrentRow(0)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """点击清单项 → 请求定位并显示详情。"""
        row = self.list.row(item)
        self._show_target(row, emit=True)

    def _step(self, delta: int) -> None:
        """上一个 / 下一个。"""
        if not self._targets:
            self.status_message.emit("请先生成导览清单")
            return
        row = self._index if self._index >= 0 else -1
        row = (row + delta) % len(self._targets)
        self.list.setCurrentRow(row)
        self._show_target(row, emit=True)

    def _show_target(self, row: int, emit: bool = False) -> None:
        if row < 0 or row >= len(self._targets):
            return
        self._index = row
        t = self._targets[row]
        self._rows["name"].set_value(f"{row + 1}/{len(self._targets)}　{t.name}")
        self._rows["type"].set_value(str(t.detail.get("type", "")))
        self._rows["pos"].set_value(f"高度 {t.alt:+.1f}°　方位 "
                                    f"{azimuth_to_compass(t.az)}（{t.az:.0f}°）")
        self._rows["mag"].set_value(f"{t.mag:+.2f} 等　评分 {t.score:.2f}")
        self._rows["why"].set_value(t.reason)
        self._rows["extra"].set_value(str(t.detail.get("extra", "")))
        if emit:
            payload = dict(t.detail)
            payload.update({"name": t.name, "kind": payload.get("type", ""),
                            "alt": t.alt, "az": t.az, "score": t.score,
                            "reason": t.reason, "from_tour": True})
            self.target_chosen.emit(payload)

    def targets(self) -> List[SE.TourTarget]:
        return list(self._targets)

    def current(self) -> Optional[SE.TourTarget]:
        if 0 <= self._index < len(self._targets):
            return self._targets[self._index]
        return None


def _sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """两点的角距（度）。"""
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dl = math.radians(ra1 - ra2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dl)
    return math.degrees(math.acos(clamp(c, -1.0, 1.0)))


__all__ = ["TourPanel"]
