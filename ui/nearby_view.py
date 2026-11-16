# -*- coding: utf-8 -*-
"""
nearby_view.py —— 太阳邻域 3D 视图（50 光年内的全部已知天体）
================================================================================
把「太阳 50 光年内的 860 个已知天体」用真实三维坐标渲染出来：

    * 太阳位于原点，用带光晕的黄色标记
    * 每颗近星按真实 X/Y/Z（光年）定位，透视投影 + 深度雾化
    * 距离标尺：5 / 10 / 20 / 30 / 40 / 50 光年的同心球壳（投影为椭圆）
    * 赤道面网格：3 个正交平面的网格，帮助建立三维空间感
    * 着色模式：按光谱型 / 按天体类型 / 按距离 / 按绝对亮度
    * 标注：近距亮星（比邻星、天狼、巴纳德星…）常显名称，其余随缩放在悬停时显示
    * 交互：左键拖拽旋转、右键平移、滚轮缩放、点击选中、双击聚焦

坐标系（容易搞错，这里写明）：x/y/z 是 **J2000 赤道直角坐标**，X 指向 (0h, 0°)，
Z 指向北天极，Y 与二者正交；单位光年，太阳在原点。
注意 HYG 星表的 readme 把它写作"银道直角坐标"，实测并非如此：天狼的 x/y/z 代入
atan2(y,x) 得 101.2872°，正是星表自己的 ra = 6.7525h（见 tools/dbg_hyg_frame.py）。
所以这里的 xy 平面是赤道面而不是银道面，界面标签已相应更正。
投影与架空星图同源（先绕 Z 轴偏航、再绕 X 轴俯仰，然后透视除法），
因此两套 3D 视图的操作手感完全一致。
"""

from __future__ import annotations

import math
import os
import time
from typing import Dict, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QWidget

from 核心 import nearby_stars as NS
from 核心.utils import LOG, clamp, format_distance_ly

# ==============================================================================
# 配色
# ==============================================================================

C_GRID = QColor(0, 150, 200, 70)
C_SHELL = QColor(0, 180, 220, 85)
C_SHELL_TEXT = QColor(170, 232, 252, 235)
C_SUN = QColor(255, 240, 170)
C_LABEL = QColor(220, 245, 255, 245)
C_HUD = QColor(145, 246, 255, 242)
#: 距离壳层（光年）
SHELLS: Tuple[float, ...] = (5.0, 10.0, 20.0, 30.0, 40.0, 50.0)


class NearbyView(QWidget):
    """太阳邻域 3D 渲染与交互视图。"""

    star_selected = Signal(dict)          # 选中近星（信息字典）
    status_message = Signal(str)
    view_changed = Signal(dict)

    COLOR_MODES: Tuple[Tuple[str, str], ...] = NS.COLOR_MODES

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        # ---- 相机（与架空星图同款操作手感） ----
        self.yaw = math.radians(-52.0)
        self.pitch = math.radians(26.0)
        self.distance = 78.0               # 相机到太阳的距离（光年）
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.cam_offset = (0.0, 0.0, 0.0)  # 观察中心（可平移到某颗星上）

        # ---- 显示选项 ----
        self.color_mode = "spectral"
        self.limit_ly = NS.LIMIT_LY
        self.kind_filter = "all"
        self.show_grid = True
        self.show_shells = True
        self.show_labels = True
        self.show_sun = True
        self.show_all_labels = False
        self.red_mode = False

        # ---- 数据 ----
        self._stars: List[NS.NearbyStar] = list(NS.NEARBY_CATALOG)
        self._visible: List[NS.NearbyStar] = list(self._stars)
        self._screen: Dict[str, QPointF] = {}
        self._depth: Dict[str, float] = {}
        self._glow_cache: Dict[str, QPixmap] = {}
        self._selected: Optional[NS.NearbyStar] = None
        self._hover: Optional[NS.NearbyStar] = None

        # ---- 交互状态 ----
        self._drag_mode = 0
        self._last_pos = QPoint(0, 0)
        self._frame_ms = 0.0
        self._needs_repaint = True

        self._apply_filter()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ==========================================================================
    # 一、数据与选项
    # ==========================================================================

    def _apply_filter(self) -> None:
        """按距离与类型筛选要绘制的近星。"""
        out: List[NS.NearbyStar] = []
        for s in self._stars:
            if s.distance_ly > self.limit_ly:
                continue
            if self.kind_filter != "all" and s.kind != self.kind_filter:
                continue
            out.append(s)
        self._visible = out
        self._needs_repaint = True

    def set_limit(self, limit_ly: float) -> None:
        """设置显示半径（光年）。"""
        self.limit_ly = clamp(float(limit_ly), 5.0, NS.LIMIT_LY)
        self._apply_filter()
        self.update()

    def set_kind_filter(self, kind: str) -> None:
        """按天体类型筛选。"""
        self.kind_filter = kind or "all"
        self._apply_filter()
        self.update()

    def set_color_mode(self, mode: str) -> None:
        """切换着色模式。"""
        if any(m == mode for m, _ in self.COLOR_MODES):
            self.color_mode = mode
            self.update()

    def set_layer(self, key: str, enabled: bool) -> None:
        """图层开关（grid / shells / labels / sun / all_labels）。"""
        mapping = {"grid": "show_grid", "shells": "show_shells", "labels": "show_labels",
                   "sun": "show_sun", "all_labels": "show_all_labels"}
        attr = mapping.get(key)
        if attr:
            setattr(self, attr, bool(enabled))
            self.update()

    def stats(self) -> Dict[str, object]:
        """当前显示统计。"""
        return {
            "shown": len(self._visible),
            "total": len(self._stars),
            "limit": self.limit_ly,
            "kind": self.kind_filter,
        }

    # ==========================================================================
    # 二、投影
    # ==========================================================================

    def _project(self, pos: Sequence[float]) -> Optional[Tuple[QPointF, float]]:
        """世界坐标（光年）→ (屏幕坐标, 深度)。

        与架空星图共用同一套相机模型：绕 Z 轴偏航 → 绕 X 轴俯仰 → 透视除法。
        深度用于雾化（越远越淡），营造三维纵深。
        """
        x = float(pos[0]) - self.cam_offset[0]
        y = float(pos[1]) - self.cam_offset[1]
        z = float(pos[2]) - self.cam_offset[2]
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        rx = x * cy - y * sy
        ry = x * sy + y * cy
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        ry2 = ry * cp - z * sp
        rz2 = ry * sp + z * cp
        denom = rz2 + self.distance
        if denom <= 1.0:
            return None
        scale = self._focal * self.zoom / denom
        sx = self.width() * 0.5 + self.pan_x + rx * scale
        sy_ = self.height() * 0.5 + self.pan_y - ry2 * scale
        return QPointF(sx, sy_), denom

    def _prepare_frame(self) -> None:
        h = max(1, self.height())
        self._focal = (h * 0.5) / math.tan(math.radians(45.0) * 0.5)

    def reset_view(self) -> None:
        """重置视角：正对赤道面斜俯视，能看到整个 50 光年气泡。"""
        self.yaw = math.radians(-52.0)
        self.pitch = math.radians(26.0)
        self.zoom = 1.0
        self.distance = 78.0
        self.pan_x = self.pan_y = 0.0
        self.cam_offset = (0.0, 0.0, 0.0)
        self._emit_view()
        self.update()

    def focus_star(self, star: NS.NearbyStar) -> None:
        """把观察中心移到某颗星上（平移动画省略，直接跳转）。"""
        self.cam_offset = (star.x * 0.6, star.y * 0.6, star.z * 0.6)
        self.pan_x = self.pan_y = 0.0
        self._emit_view()
        self.update()

    def _emit_view(self) -> None:
        self.view_changed.emit({
            "yaw": math.degrees(self.yaw), "pitch": math.degrees(self.pitch),
            "zoom": self.zoom, "distance": self.distance,
        })

    # ==========================================================================
    # 三、渲染
    # ==========================================================================

    def _on_tick(self) -> None:
        if self._needs_repaint and self.isVisible():
            self._needs_repaint = False
            self.update()

    def mark_dirty(self) -> None:
        self._needs_repaint = True
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        t0 = time.perf_counter()
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            self._prepare_frame()
            self._draw_background(p)
            # 先算屏幕坐标，再画网格与球壳（让它们处于星星"之下"，层次更清楚）
            self._compute_screen_positions()
            if self.show_grid:
                self._draw_grid(p)
            if self.show_shells:
                self._draw_shells(p)
            self._draw_stars(p)
            if self.show_sun:
                self._draw_sun(p)
            if self.show_labels:
                self._draw_labels(p)
            self._draw_hud(p)
        except Exception as exc:  # pragma: no cover - 防御性
            LOG.error("太阳邻域绘制异常", exc)
            import traceback
            for line in traceback.format_exc().strip().splitlines()[-6:]:
                LOG.error("TRACE", line)
            p.setPen(QColor(255, 120, 120))
            p.drawText(self.rect(), Qt.AlignCenter, f"渲染异常：{exc}")
        finally:
            p.end()
        self._frame_ms = (time.perf_counter() - t0) * 1000.0

    def _draw_background(self, p: QPainter) -> None:
        """深空背景 + 稳定的星尘。"""
        w, h = self.width(), self.height()
        grad = QRadialGradient(QPointF(w * 0.5, h * 0.5), max(w, h) * 0.75)
        if self.red_mode:
            grad.setColorAt(0.0, QColor(24, 6, 4))
            grad.setColorAt(1.0, QColor(4, 1, 2))
        else:
            grad.setColorAt(0.0, QColor(8, 18, 34))
            grad.setColorAt(0.6, QColor(4, 9, 20))
            grad.setColorAt(1.0, QColor(1, 3, 8))
        p.fillRect(self.rect(), QBrush(grad))
        # 星尘（固定伪随机，避免闪烁；关闭抗锯齿省时间）
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(Qt.NoPen)
        seed = 987654321
        for _ in range(150):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            x = (seed % 10000) / 10000.0 * w
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            y = (seed % 10000) / 10000.0 * h
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            p.setBrush(QColor(180, 220, 255, 30 + seed % 50))
            s = 0.5 + (seed % 100) / 100.0
            p.drawEllipse(QPointF(x, y), s, s)
        p.setRenderHint(QPainter.Antialiasing, True)

    def _draw_grid(self, p: QPainter) -> None:
        """赤道面网格：三个正交平面上每 10 光年一条网格线。

        坐标轴约定：x/y/z 是**赤道直角坐标**（X 指向 (0h, 0°)，Z 指向北天极），
        所以 xy 平面是「赤道面」而不是银道面。早期版本按 HYG readme 的说法
        把它当银道面标注，属于标注错误（数据本身没问题，只是被叫错了名字）。
        """
        span = float(int(self.limit_ly))
        step = 10.0
        p.setPen(QPen(C_GRID, 1.0))
        n = int(span / step)
        for i in range(-n, n + 1):
            v = i * step
            # 赤道面（xy 平面）
            a = self._project((v, -span, 0.0))
            b = self._project((v, span, 0.0))
            c = self._project((-span, v, 0.0))
            d = self._project((span, v, 0.0))
            for s, e in ((a, b), (c, d)):
                if s and e:
                    p.drawLine(s[0], e[0])
            # 侧平面（xz / yz）
            e1 = self._project((v, 0.0, -span))
            e2 = self._project((v, 0.0, span))
            e3 = self._project((0.0, v, -span))
            e4 = self._project((0.0, v, span))
            p.setPen(QPen(QColor(0, 130, 180, 40), 1.0))
            for s, e in ((e1, e2), (e3, e4)):
                if s and e:
                    p.drawLine(s[0], e[0])
            p.setPen(QPen(C_GRID, 1.0))

    def _draw_shells(self, p: QPainter) -> None:
        """距离球壳：把每个半径的球壳投影成椭圆环（近似即可，视觉上更直观）。"""
        for radius in SHELLS:
            if radius > self.limit_ly + 0.01:
                continue
            pts: List[QPointF] = []
            for deg in range(0, 361, 4):
                a = math.radians(deg)
                # 赤道面上的大圆（球壳的"赤道"）
                res = self._project((radius * math.cos(a), radius * math.sin(a), 0.0))
                if res is None:
                    if len(pts) > 1:
                        p.setPen(QPen(C_SHELL, 1.1))
                        p.drawPolyline(QPolygonF(pts))
                    pts = []
                    continue
                pts.append(res[0])
            if len(pts) > 1:
                p.setPen(QPen(C_SHELL, 1.1))
                p.drawPolyline(QPolygonF(pts))
            # 半径标签
            label_pt = self._project((radius, 0.0, 0.0))
            if label_pt is not None:
                p.setPen(C_SHELL_TEXT)
                p.setFont(QFont("Microsoft YaHei UI", 9))
                p.drawText(QPointF(label_pt[0].x() + 5, label_pt[0].y() - 4),
                           f"{radius:.0f} 光年")

    def _compute_screen_positions(self) -> None:
        """计算所有可见近星的屏幕坐标与深度（供绘制与命中测试共用）。"""
        self._screen = {}
        self._depth = {}
        w, h = self.width(), self.height()
        margin = 80.0
        for s in self._visible:
            res = self._project((s.x, s.y, s.z))
            if res is None:
                continue
            pt, depth = res
            self._depth[s.catalog_id] = depth
            if -margin <= pt.x() <= w + margin and -margin <= pt.y() <= h + margin:
                self._screen[s.catalog_id] = pt

    def _star_color(self, star: NS.NearbyStar) -> QColor:
        """按当前着色模式取颜色。"""
        if self.red_mode:
            return QColor(255, 95, 75)
        if self.color_mode == "kind":
            rgb = star.kind_color
        elif self.color_mode == "distance":
            rgb = NS.distance_color(star.distance_ly, self.limit_ly)
        elif self.color_mode == "mag":
            rgb = NS.mag_color(star.absmag)
        else:
            rgb = star.spectral_color
        return QColor(rgb[0], rgb[1], rgb[2])

    def _glow_pixmap(self, color: QColor, size: int) -> QPixmap:
        """缓存发光贴图（避免每帧重建渐变）。"""
        key = f"{color.red()},{color.green()},{color.blue()}|{size}"
        pix = self._glow_cache.get(key)
        if pix is not None:
            return pix
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        pp = QPainter(pix)
        pp.setRenderHint(QPainter.Antialiasing, True)
        half = size / 2.0
        grad = QRadialGradient(QPointF(half, half), half)
        grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 255))
        grad.setColorAt(0.22, QColor(color.red(), color.green(), color.blue(), 120))
        grad.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(), 34))
        grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        pp.setPen(Qt.NoPen)
        pp.setBrush(QBrush(grad))
        pp.drawEllipse(QPointF(half, half), half, half)
        pp.end()
        if len(self._glow_cache) < 400:
            self._glow_cache[key] = pix
        return pix

    def _nearest_glow_size(self, want: float) -> int:
        for s in (8, 12, 18, 26, 38, 54, 76):
            if s >= want:
                return s
        return 76

    #: 光晕贴图最大尺寸：超过则用目标矩形缩放（避免为 860 颗星各生成一张大贴图）
    MAX_GLOW = 38

    def _draw_glow(self, p: QPainter, pt: QPointF, color: QColor,
                   want: float, opacity: float) -> None:
        """绘制光晕：小尺寸直接贴图，大尺寸缩放贴图（性能友好）。"""
        want = clamp(want, 6.0, 150.0)
        size = self._nearest_glow_size(min(want, float(self.MAX_GLOW)))
        pix = self._glow_pixmap(color, size)
        p.setOpacity(opacity)
        if want <= self.MAX_GLOW:
            p.drawPixmap(QPointF(pt.x() - size / 2.0, pt.y() - size / 2.0), pix)
        else:
            p.drawPixmap(QRectF(pt.x() - want / 2.0, pt.y() - want / 2.0, want, want),
                         pix, QRectF(0, 0, size, size))
        p.setOpacity(1.0)

    def _draw_stars(self, p: QPainter) -> None:
        """绘制近星：按深度从远到近排序，远星压暗（雾化）。"""
        order = sorted(self._screen.keys(), key=lambda k: -self._depth.get(k, 0.0))
        by_id = {s.catalog_id: s for s in self._visible}
        for cid in order:
            star = by_id.get(cid)
            if star is None:
                continue
            pt = self._screen[cid]
            depth = self._depth.get(cid, 1.0)
            # 雾化：以相机距离为基准，越远越淡
            fog = clamp(1.0 - (depth - self.distance) / (self.distance * 1.6), 0.32, 1.0)
            color = self._star_color(star)
            r = star.radius * clamp(math.sqrt(self.zoom), 0.7, 2.2)
            # 光晕（只有较亮的星才画，避免 860 张光晕贴图拖慢帧率）
            if star.absmag < 9.0 and fog > 0.45:
                self._draw_glow(p, pt, color, r * 6.0, fog)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color.red(), color.green(), color.blue(), int(255 * fog)))
            p.drawEllipse(pt, r, r)
            # 选中 / 悬停高亮
            if self._selected is not None and self._selected.catalog_id == cid:
                p.setPen(QPen(QColor(255, 235, 150, 245), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r + 6.0, r + 6.0)
                p.drawEllipse(pt, r + 11.0, r + 11.0)
            elif self._hover is not None and self._hover.catalog_id == cid:
                p.setPen(QPen(QColor(140, 255, 235, 220), 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r + 5.0, r + 5.0)

    def _draw_sun(self, p: QPainter) -> None:
        """太阳：原点处的黄色光晕 + 十字标记。"""
        res = self._project((0.0, 0.0, 0.0))
        if res is None:
            return
        pt = res[0]
        color = QColor(255, 120, 90) if self.red_mode else C_SUN
        grad = QRadialGradient(pt, 26.0)
        grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 245))
        grad.setColorAt(0.3, QColor(color.red(), color.green(), color.blue(), 110))
        grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(pt, 26.0, 26.0)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 255))
        p.drawEllipse(pt, 5.0, 5.0)
        p.setPen(QPen(QColor(255, 250, 210, 235), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pt, 9.0, 9.0)
        p.setPen(QColor(255, 246, 200, 245))
        p.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        p.drawText(QPointF(pt.x() + 12, pt.y() - 8), "太阳")

    def _draw_labels(self, p: QPainter) -> None:
        """近星名称标注：重要近星常显，其余在缩放足够或悬停时显示。"""
        if not self._screen:
            return
        by_id = {s.catalog_id: s for s in self._visible}
        p.setFont(QFont("Microsoft YaHei UI", 9))
        fm = QFontMetrics(p.font())
        placed: List[QRectF] = []
        # 优先标注：有专名的、距离近的、被选中/悬停的
        candidates = sorted(
            self._screen.keys(),
            key=lambda k: (by_id[k].distance_ly if k in by_id else 999))
        for cid in candidates:
            star = by_id.get(cid)
            pt = self._screen.get(cid)
            if star is None or pt is None:
                continue
            important = bool(star.proper or star.name_cn) or star.distance_ly <= 12.0
            hovered = self._hover is not None and self._hover.catalog_id == cid
            selected = self._selected is not None and self._selected.catalog_id == cid
            if not (self.show_all_labels or important or hovered or selected):
                continue
            if not (self.show_all_labels or important) and self.zoom < 1.6:
                continue
            text = star.display_name
            if selected or hovered:
                text += f"　{star.distance_ly:.2f} ly"
            tw = fm.horizontalAdvance(text)
            rect = QRectF(pt.x() + 8, pt.y() - 7, tw + 4, 14)
            if rect.right() > self.width() - 2:
                rect.moveLeft(pt.x() - tw - 12)
            if any(rect.intersects(q) for q in placed):
                continue
            placed.append(rect)
            color = QColor(255, 240, 160) if (selected or hovered) else C_LABEL
            if self.red_mode:
                color = QColor(255, 120, 100)
            p.setPen(color)
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)

    def _draw_hud(self, p: QPainter) -> None:
        """HUD：视图状态、显示统计、比例尺、图例。"""
        stats = self.stats()
        p.setFont(QFont("Consolas", 9))
        color = C_HUD if not self.red_mode else QColor(255, 125, 105)
        p.setPen(color)
        mode_cn = dict(self.COLOR_MODES).get(self.color_mode, self.color_mode)
        kind_cn = dict(NS.KIND_OPTIONS).get(self.kind_filter, self.kind_filter)
        lines = [
            f"SOLAR NEIGHBORHOOD   ≤ {self.limit_ly:.0f} 光年",
            f"SHOWN {stats['shown']}/{stats['total']} 天体   类型 {kind_cn}",
            f"COLOR {mode_cn}",
            f"CAM   yaw {math.degrees(self.yaw) % 360:6.1f}°  pitch {math.degrees(self.pitch):5.1f}°  "
            f"dist {self.distance:5.1f} ly  zoom {self.zoom:4.2f}x",
            f"FRAME {self._frame_ms:.1f} ms",
        ]
        y = 16
        for line in lines:
            p.drawText(QPointF(10, y), line)
            y += 12

        # 比例尺：以赤道面 10 光年的投影长度为准
        a = self._project((0.0, 0.0, 0.0))
        b = self._project((10.0, 0.0, 0.0))
        if a and b:
            length = abs(b[0].x() - a[0].x())
            if 12 < length < 400:
                x0 = self.width() - length - 24
                y0 = self.height() - 22
                p.setPen(QPen(QColor(0, 210, 245, 190), 1.3))
                p.drawLine(QPointF(x0, y0), QPointF(x0 + length, y0))
                p.drawLine(QPointF(x0, y0 - 5), QPointF(x0, y0 + 5))
                p.drawLine(QPointF(x0 + length, y0 - 5), QPointF(x0 + length, y0 + 5))
                p.setPen(QColor(180, 240, 255, 235))
                p.drawText(QPointF(x0, y0 - 8), "10 光年")

        # 图例
        items: List[Tuple[str, QColor]] = []
        if self.show_sun:
            items.append(("太阳", C_SUN))
        if self.show_shells:
            items.append(("距离球壳（5~50 光年）", C_SHELL))
        if self.show_grid:
            items.append(("赤道面网格（10 光年）", C_GRID))
        items.append((f"近星 {stats['shown']} 颗（按{('光谱型' if self.color_mode == 'spectral' else '所选模式')}着色）",
                      QColor(150, 200, 255)))
        if self.red_mode:
            items = [(t, QColor(255, 110, 90)) for t, _ in items]
        p.setFont(QFont("Microsoft YaHei UI", 8))
        fm = QFontMetrics(p.font())
        width = max(fm.horizontalAdvance(t) for t, _ in items) + 34
        height = 14 * len(items) + 8
        x0, y0 = 10.0, self.height() - height - 24.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(4, 12, 20, 180))
        p.drawRoundedRect(QRectF(x0 - 4, y0 - 4, width, height), 3.0, 3.0)
        p.setPen(QPen(QColor(0, 160, 200, 130), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x0 - 4, y0 - 4, width, height), 3.0, 3.0)
        for i, (text, col) in enumerate(items):
            yy = y0 + 12 + i * 14
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(x0 + 6, yy - 3), 4.0, 4.0)
            p.setPen(QColor(206, 234, 248, 240))
            p.drawText(QPointF(x0 + 16, yy), text)

    # ==========================================================================
    # 四、交互
    # ==========================================================================

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._last_pos = event.position().toPoint()
        if event.button() == Qt.LeftButton:
            self._drag_mode = 1
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.RightButton:
            self._drag_mode = 2
            self.setCursor(Qt.SizeAllCursor)
        self._needs_repaint = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._drag_mode:
            dx = pos.x() - self._last_pos.x()
            dy = pos.y() - self._last_pos.y()
            self._last_pos = pos
            if self._drag_mode == 1:
                self.yaw += dx * 0.006
                self.pitch = clamp(self.pitch + dy * 0.006,
                                   math.radians(-88), math.radians(88))
            else:
                self.pan_x += dx
                self.pan_y += dy
            self._emit_view()
            self._needs_repaint = True
        else:
            hit = self._star_at(event.position())
            if hit is not self._hover:
                self._hover = hit
                if hit is not None:
                    self.setToolTip(
                        f"{hit.display_name}　{hit.catalog_id}\n"
                        f"{hit.spectral or '光谱型未知'}　{hit.kind}　"
                        f"{hit.distance_ly:.2f} 光年　视星等 {hit.mag:+.2f}")
                else:
                    self.setToolTip("")
                self._needs_repaint = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_mode == 1 and event.button() == Qt.LeftButton:
            moved = (event.position().toPoint() - self._last_pos).manhattanLength()
            if moved < 4:
                star = self._star_at(event.position())
                if star is not None:
                    self.select_star(star)
        self._drag_mode = 0
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        star = self._star_at(event.position())
        if star is not None:
            self.focus_star(star)
            self.select_star(star)
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            factor = 1.12 if delta > 0 else 1.0 / 1.12
            self.zoom = clamp(self.zoom * factor, 0.25, 14.0)
            self._emit_view()
            self._needs_repaint = True
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self._needs_repaint = True
        super().leaveEvent(event)

    def _star_at(self, pos: QPointF, radius: float = 12.0) -> Optional[NS.NearbyStar]:
        """屏幕坐标 → 最近的近星。"""
        best: Optional[str] = None
        best_d = radius * radius
        for cid, pt in self._screen.items():
            d = (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2
            if d < best_d:
                best_d = d
                best = cid
        if best is None:
            return None
        for s in self._visible:
            if s.catalog_id == best:
                return s
        return None

    def star_nearest_center(self, radius: float = 48.0) -> Optional[NS.NearbyStar]:
        """离**视野中心**最近的一颗近星（在 radius 像素内），没有则 None。

        与 `_star_at` 的区别：那个是"鼠标点在哪就取哪颗"（命中半径只有 12 px），
        这是"不管鼠标在哪，给我离屏幕中心最近的那颗"，供「选中最近的星」按钮使用。
        屏幕坐标来自上一帧的 `_screen`；若还没画过就先按当前相机算一次。
        """
        if not self._screen:
            try:
                self._compute_screen_positions()
            except Exception:
                return None
        return self._star_at(QPointF(self.width() * 0.5, self.height() * 0.5), radius)

    def select_star(self, star: NS.NearbyStar) -> None:
        """选中一颗近星并发出信号。"""
        self._selected = star
        self.star_selected.emit(star.to_dict())
        self.status_message.emit(
            f"已选中 {star.display_name}（{star.spectral or '未知光谱型'}，"
            f"{star.distance_ly:.2f} 光年，{star.kind}）")
        self.update()

    def selected_star(self) -> Optional[NS.NearbyStar]:
        """当前选中的近星（没有选中任何天体时返回 None）。

        供 main_window.open_inverse_sky 使用：在太阳邻域里选中某颗星后，
        从菜单打开「外星视角」时会自动以它为观察点。
        这个方法曾经漏掉——open_inverse_sky 里用 getattr(..., None) 探测它，
        探测不到就静默回退到默认的比邻星，功能"看起来没接上"但不报错。
        """
        return self._selected

    def select_by_key(self, text: str) -> bool:
        """按名称/HIP/Gliese 搜索并选中。"""
        star = NS.find_nearby(text)
        if star is None:
            self.status_message.emit(f"未在 50 光年内找到：{text}")
            return False
        if star.distance_ly > self.limit_ly:
            self.set_limit(NS.LIMIT_LY)
        self.select_star(star)
        return True

    def frame_ms(self) -> float:
        return self._frame_ms


__all__ = ["NearbyView", "SHELLS"]
