# -*- coding: utf-8 -*-
"""
ui/orrery_view.py —— 太阳系仪（行星时钟）
================================================================================
俯视黄道面观察太阳系：中心是太阳，行星按真实轨道根数运行在椭圆轨道上，
配合时间倍率就是一台"行星时钟"。

绘制内容：
    · 行星轨道（椭圆，含偏心率与近日点方向，由 planet_data.orbital_path 提供）
    · 行星当前位置（按星等给大小、按类型给颜色）
    · 黄道面网格与轨道距离标尺（对数或线性两种）
    · 行星与太阳的连线、轨道速度方向箭头（可选）
    · 内行星 / 外行星两档视野，以及"真实比例"与"压缩比例"切换
      （真实比例下海王星轨道是水星的 78 倍，内行星会挤成一点，因此默认压缩）

交互：
    · 左键拖拽旋转视角（俯仰/自转）
    · 滚轮缩放
    · 点击行星选中并显示信息
    · 底部时间条直接拖动时间（由主窗口的时间控件驱动）
"""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QWidget

from 核心 import astronomy as A
from 核心 import planet_data as P
from 核心.utils import LOG, azimuth_to_compass, clamp, format_datetime


class OrreryView(QWidget):
    """太阳系仪：俯视黄道面的行星轨道视图。"""

    planet_selected = Signal(dict)          # 选中行星的信息字典
    status_message = Signal(str)
    view_changed = Signal(dict)

    #: 行星显示颜色（与星空视图区分：这里是"轨道图"配色，更鲜亮）
    COLORS: Dict[str, Tuple[int, int, int]] = {
        "mercury": (176, 160, 150), "venus": (255, 214, 150), "earth": (110, 190, 255),
        "mars": (255, 130, 100), "jupiter": (255, 190, 130), "saturn": (240, 220, 160),
        "uranus": (150, 235, 240), "neptune": (120, 160, 255), "pluto": (200, 180, 220),
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        # ---- 视图状态 ----
        self.observer: Optional[A.Observer] = None
        self.tilt = math.radians(58.0)        # 俯视倾角（90° = 正上方俯视）
        self.rotate = math.radians(-18.0)     # 绕黄道法线的自转
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.scale_mode = "compressed"        # compressed / true
        self.show_orbits = True
        self.show_labels = True
        self.show_links = True                # 行星-太阳连线
        self.show_grid = True
        self.outer_limit = True               # True = 含外行星，False = 只看内行星
        self.red_mode = False

        # ---- 运行时 ----
        self._pos_au: Dict[str, Tuple[float, float, float]] = {}
        self._screen: Dict[str, QPointF] = {}
        self._selected: Optional[str] = None
        self._hover: Optional[str] = None
        self._drag_mode = 0
        self._last_pos = QPointF()
        self._frame_ms = 0.0
        self._needs_repaint = True
        self._last_key = ""

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ==========================================================================
    # 一、数据与选项
    # ==========================================================================

    def set_observer(self, observer: A.Observer) -> None:
        """设置观测点（决定"从地球看到的方向"，也用于计算行星相位等）。"""
        self.observer = observer
        self.refresh(force=True)

    def set_time(self, when) -> None:
        """设置时间（由主窗口的时间控件驱动）。"""
        if self.observer is None:
            self.observer = A.Observer(when=when)
        else:
            self.observer.when = when
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        """按当前时间重算所有行星的日心黄道坐标。"""
        obs = self.observer
        if obs is None:
            return
        key = obs.when.strftime("%Y%m%d%H%M")
        if not force and key == self._last_key:
            return
        self._last_key = key
        try:
            jd = obs.jd_ut
            self._pos_au = {}
            for pk in P.all_planet_keys():
                self._pos_au[pk] = P.heliocentric_position(pk, jd)
        except Exception as exc:
            LOG.error("太阳系仪位置计算失败", exc)
        self.mark_dirty()

    def set_layer(self, key: str, enabled: bool) -> None:
        mapping = {"orbits": "show_orbits", "labels": "show_labels",
                   "links": "show_links", "grid": "show_grid"}
        attr = mapping.get(key)
        if attr:
            setattr(self, attr, bool(enabled))
            self.mark_dirty()

    def set_scale_mode(self, mode: str) -> None:
        self.scale_mode = mode if mode in ("compressed", "true") else "compressed"
        self.mark_dirty()

    def set_outer_limit(self, outer: bool) -> None:
        self.outer_limit = bool(outer)
        self.mark_dirty()

    def reset_view(self) -> None:
        self.tilt = math.radians(58.0)
        self.rotate = math.radians(-18.0)
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self._emit_view()
        self.mark_dirty()

    def _emit_view(self) -> None:
        self.view_changed.emit({"tilt": math.degrees(self.tilt),
                                "rotate": math.degrees(self.rotate),
                                "zoom": self.zoom})

    def frame_ms(self) -> float:
        return self._frame_ms

    def mark_dirty(self) -> None:
        self._needs_repaint = True
        self.update()

    def _on_tick(self) -> None:
        if not self.isVisible():
            return
        self.refresh()
        if self._needs_repaint:
            self._needs_repaint = False
            self.update()

    # ==========================================================================
    # 二、投影
    # ==========================================================================

    def _radius_scale(self) -> float:
        """轨道半径的显示尺度（像素 / AU）。

        compressed：对半径取 0.55 次方，让内行星与外行星都能看见
        true      ：等比例（海王星 30 AU 会远在内行星之外）
        """
        return 1.0

    def _plot_radius(self, au: float) -> float:
        """把轨道半径（AU）映射为绘图半径（标准化单位）。"""
        if self.scale_mode == "true":
            return au
        return au ** 0.55

    def _project(self, x: float, y: float, z: float) -> QPointF:
        """黄道直角坐标（AU）→ 屏幕坐标。

        先按"显示比例"压缩半径，再绕 z 轴自转、绕 x 轴倾斜，最后透视投影。
        """
        r = math.hypot(x, y)
        if r > 1e-9:
            k = self._plot_radius(r) / r
            x, y = x * k, y * k
        z = z * k if r > 1e-9 else z
        cs, sn = math.cos(self.rotate), math.sin(self.rotate)
        rx = x * cs - y * sn
        ry = x * sn + y * cs
        ct, st = math.cos(self.tilt), math.sin(self.tilt)
        sy = ry * ct - z * st
        depth = ry * st + z * ct
        focal = 1.9
        denom = 1.0 + depth / 26.0
        if denom < 0.05:
            denom = 0.05
        scale = self._base_scale() * self.zoom * focal / denom
        return QPointF(self.width() * 0.5 + self.pan_x + rx * scale,
                       self.height() * 0.5 + self.pan_y - sy * scale)

    def _base_scale(self) -> float:
        """基准比例：让最大轨道占据窗口的 ~42%。"""
        limit = self._view_limit_au()
        return min(self.width(), self.height()) * 0.42 / max(0.35, self._plot_radius(limit))

    def _view_limit_au(self) -> float:
        return 32.0 if self.outer_limit else 1.8

    # ==========================================================================
    # 三、渲染
    # ==========================================================================

    def paintEvent(self, event) -> None:  # noqa: N802
        t0 = time.perf_counter()
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            self._draw_background(p)
            if self.show_grid:
                self._draw_grid(p)
            self._compute_screen_positions()
            if self.show_orbits:
                self._draw_orbits(p)
            self._draw_sun(p)
            if self.show_links:
                self._draw_links(p)
            self._draw_planets(p)
            self._draw_hud(p)
        except Exception as exc:      # pragma: no cover
            LOG.error("太阳系仪绘制异常", exc)
            import traceback
            for line in traceback.format_exc().strip().splitlines()[-5:]:
                LOG.error("TRACE", line)
            p.setPen(QColor(255, 120, 120))
            p.drawText(self.rect(), Qt.AlignCenter, f"渲染异常：{exc}")
        finally:
            p.end()
        self._frame_ms = (time.perf_counter() - t0) * 1000.0

    def _draw_background(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        grad = QRadialGradient(QPointF(w * 0.5, h * 0.5), max(w, h) * 0.7)
        if self.red_mode:
            grad.setColorAt(0.0, QColor(26, 8, 6))
            grad.setColorAt(1.0, QColor(4, 1, 2))
        else:
            grad.setColorAt(0.0, QColor(10, 20, 38))
            grad.setColorAt(0.65, QColor(4, 10, 20))
            grad.setColorAt(1.0, QColor(1, 3, 8))
        p.fillRect(self.rect(), QBrush(grad))

    def _draw_grid(self, p: QPainter) -> None:
        """黄道面网格：以 AU 为单位的同心圆（取 1/2/5 系列刻度）。"""
        steps = ((0.5, 1, 2, 3, 4, 5) if not self.outer_limit
                 else (1, 5, 10, 20, 30))
        p.setFont(QFont("Consolas", 8))
        for au in steps:
            if au > self._view_limit_au() * 1.05:
                continue
            pts: List[QPointF] = []
            for deg in range(0, 361, 6):
                a = math.radians(deg)
                pts.append(self._project(au * math.cos(a), au * math.sin(a), 0.0))
            p.setPen(QPen(QColor(0, 130, 180, 60), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(QPolygonF(pts))
            label_pt = self._project(au, 0.0, 0.0)
            if 0 < label_pt.x() < self.width() - 40:
                p.setPen(QColor(140, 190, 215, 190))
                text = f"{au:g} AU"
                p.drawText(QPointF(label_pt.x() + 4, label_pt.y() + 12), text)

    def _compute_screen_positions(self) -> None:
        self._screen = {}
        for pk, pos in self._pos_au.items():
            self._screen[pk] = self._project(*pos)

    def _draw_orbits(self, p: QPainter) -> None:
        """画行星轨道：用 planet_data 的轨道根数生成椭圆。"""
        obs = self.observer
        if obs is None:
            return
        jd = obs.jd_ut
        for pk in P.all_planet_keys():
            color = QColor(*self.COLORS.get(pk, (200, 210, 230)))
            a_au = P.planet_state(pk, jd).a
            if a_au > self._view_limit_au() * 1.15:
                continue
            path = QPainterPath()
            first = True
            for pos in P.heliocentric_orbit_path(pk, jd, 128):
                pt = self._project(*pos)
                if first:
                    path.moveTo(pt)
                    first = False
                else:
                    path.lineTo(pt)
            selected = (pk == self._selected)
            pen = QPen(QColor(color.red(), color.green(), color.blue(),
                              235 if selected else 80),
                       1.8 if selected else 1.0)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

    def _draw_sun(self, p: QPainter) -> None:
        c = self._project(0.0, 0.0, 0.0)
        color = QColor(255, 130, 100) if self.red_mode else QColor(255, 240, 170)
        grad = QRadialGradient(c, 30.0)
        grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 250))
        grad.setColorAt(0.28, QColor(color.red(), color.green(), color.blue(), 130))
        grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(c, 30.0, 30.0)
        p.setBrush(color)
        p.drawEllipse(c, 6.0, 6.0)
        p.setPen(QColor(255, 246, 205, 240))
        p.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        p.drawText(QPointF(c.x() + 10, c.y() - 8), "太阳")

    def _planet_radius(self, pk: str) -> float:
        """行星显示半径：按半径的对数缩放（真实比例下木星是水星的 29 倍）。"""
        try:
            radius_km = P.PLANET_INFO.get(pk, {}).get("radius_km", 6371.0)
            return clamp(2.2 + 1.9 * math.log10(max(1.0, radius_km / 2000.0)), 2.0, 9.0)
        except Exception:
            return 3.0

    def _draw_planets(self, p: QPainter) -> None:
        obs = self.observer
        for pk in P.all_planet_keys():
            pt = self._screen.get(pk)
            if pt is None:
                continue
            color = QColor(*self.COLORS.get(pk, (200, 210, 230)))
            r = self._planet_radius(pk)
            # 光晕
            grad = QRadialGradient(pt, r * 3.2)
            grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 200))
            grad.setColorAt(0.5, QColor(color.red(), color.green(), color.blue(), 60))
            grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(grad))
            p.drawEllipse(pt, r * 3.2, r * 3.2)
            p.setBrush(color)
            p.drawEllipse(pt, r, r)
            # 偏心率的直观提示：近日点方向画一个小箭头（只给选中的行星）
            selected = (pk == self._selected)
            if selected:
                p.setPen(QPen(QColor(255, 240, 160, 235), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r + 6, r + 6)
            elif pk == self._hover:
                p.setPen(QPen(QColor(140, 255, 235, 215), 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r + 5, r + 5)

    def _draw_links(self, p: QPainter) -> None:
        """行星与太阳的连线（虚线，体现"半径向量"）。"""
        sun = self._project(0.0, 0.0, 0.0)
        p.setPen(QPen(QColor(0, 170, 210, 60), 1.0, Qt.DashLine))
        for pk, pt in self._screen.items():
            if pk == self._selected:
                p.setPen(QPen(QColor(255, 230, 150, 170), 1.2, Qt.DashLine))
                p.drawLine(sun, pt)
                p.setPen(QPen(QColor(0, 170, 210, 60), 1.0, Qt.DashLine))

    def _draw_hud(self, p: QPainter) -> None:
        obs = self.observer
        p.setFont(QFont("Consolas", 9))
        color = QColor(145, 246, 255, 242) if not self.red_mode else QColor(255, 125, 105)
        p.setPen(color)
        mode = "压缩比例（r^0.55）" if self.scale_mode == "compressed" else "真实比例"
        lines = [
            "SOLAR SYSTEM ORRERY",
            f"TIME  {format_datetime(obs.when) if obs else '--'}",
            f"VIEW  {'含外行星' if self.outer_limit else '仅内行星'}   {mode}",
            f"CAM   tilt {math.degrees(self.tilt):5.1f}°  rot {math.degrees(self.rotate):6.1f}°  "
            f"zoom {self.zoom:4.2f}x",
            f"FRAME {self._frame_ms:.1f} ms",
        ]
        y = 16
        for line in lines:
            p.drawText(QPointF(10, y), line)
            y += 12

        # 行星名称与日心距离
        if self.show_labels:
            p.setFont(QFont("Microsoft YaHei UI", 8))
            fm = QFontMetrics(p.font())
            for pk in P.all_planet_keys():
                pt = self._screen.get(pk)
                if pt is None:
                    continue
                pos = self._pos_au.get(pk)
                r_au = math.sqrt(sum(v * v for v in pos)) if pos else 0.0
                name = P.PLANET_INFO.get(pk, {}).get("name_cn", pk)
                text = f"{name} {r_au:.1f} AU"
                selected = (pk == self._selected)
                hover = (pk == self._hover)
                if selected or hover:
                    p.setPen(QColor(255, 240, 160, 245))
                else:
                    p.setPen(QColor(210, 240, 252, 230))
                x = pt.x() + self._planet_radius(pk) + 6
                if x + fm.horizontalAdvance(text) > self.width() - 4:
                    x = pt.x() - fm.horizontalAdvance(text) - 8
                p.drawText(QPointF(x, pt.y() + 4), text)

        # 图例
        p.setFont(QFont("Microsoft YaHei UI", 8))
        items = [("太阳", (255, 240, 170))]
        for pk in P.all_planet_keys()[:9]:
            items.append((P.PLANET_INFO.get(pk, {}).get("name_cn", pk),
                          self.COLORS.get(pk, (200, 210, 230))))
        width = 150.0
        height = 14 * len(items) + 8
        x0, y0 = 10.0, self.height() - height - 12.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(4, 12, 20, 180))
        p.drawRoundedRect(QRectF(x0 - 4, y0 - 4, width, height), 3, 3)
        p.setPen(QPen(QColor(0, 160, 200, 130), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x0 - 4, y0 - 4, width, height), 3, 3)
        for i, (text, rgb) in enumerate(items):
            yy = y0 + 12 + i * 14
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*rgb))
            p.drawEllipse(QPointF(x0 + 6, yy - 3), 3.6, 3.6)
            p.setPen(QColor(206, 234, 248, 240))
            p.drawText(QPointF(x0 + 16, yy), text)

        # 操作提示
        p.setPen(QColor(150, 200, 225, 200))
        p.drawText(QPointF(self.width() - 250, self.height() - 10),
                   "拖拽旋转 · 滚轮缩放 · 点击行星查看详情")

    # ==========================================================================
    # 四、交互
    # ==========================================================================

    def _planet_at(self, pos: QPointF, radius: float = 14.0) -> Optional[str]:
        best: Optional[str] = None
        best_d = radius * radius
        for pk, pt in self._screen.items():
            d = (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2
            if d < best_d:
                best_d, best = d, pk
        return best

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._last_pos = event.position()
        if event.button() == Qt.LeftButton:
            self._drag_mode = 1
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.RightButton:
            self._drag_mode = 2
            self.setCursor(Qt.SizeAllCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if self._drag_mode == 1:
            dx = pos.x() - self._last_pos.x()
            dy = pos.y() - self._last_pos.y()
            self._last_pos = pos
            self.rotate += dx * 0.006
            self.tilt = clamp(self.tilt + dy * 0.005, math.radians(4), math.radians(89))
            self._emit_view()
            self.mark_dirty()
        elif self._drag_mode == 2:
            self.pan_x += pos.x() - self._last_pos.x()
            self.pan_y += pos.y() - self._last_pos.y()
            self._last_pos = pos
            self.mark_dirty()
        else:
            hit = self._planet_at(pos)
            if hit != self._hover:
                self._hover = hit
                if hit:
                    self.setToolTip(self._tooltip(hit))
                else:
                    self.setToolTip("")
                self.mark_dirty()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_mode == 1 and event.button() == Qt.LeftButton:
            moved = (event.position() - self._last_pos)
            if abs(moved.x()) + abs(moved.y()) < 4:
                pk = self._planet_at(event.position())
                if pk:
                    self.select_planet(pk)
        self._drag_mode = 0
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self.zoom = clamp(self.zoom * (1.12 if delta > 0 else 1 / 1.12), 0.2, 20.0)
            self._emit_view()
            self.mark_dirty()
        event.accept()

    def _tooltip(self, pk: str) -> str:
        info = P.PLANET_INFO.get(pk, {})
        pos = self._pos_au.get(pk)
        r = math.sqrt(sum(v * v for v in pos)) if pos else 0.0
        return (f"{info.get('name_cn', pk)}（{info.get('name_en', '')}）\n"
                f"日心距 {r:.3f} AU　半径 {info.get('radius_km', 0):,.0f} km")

    def select_planet(self, pk: str) -> None:
        """选中一行星并发出信息。"""
        self._selected = pk
        info = dict(P.PLANET_INFO.get(pk, {}))
        pos = self._pos_au.get(pk) or (0.0, 0.0, 0.0)
        state = P.planet_state(pk, self.observer.jd_ut) if self.observer else None
        r_au = math.sqrt(sum(v * v for v in pos))
        lon = math.degrees(math.atan2(pos[1], pos[0])) % 360.0
        lat = math.degrees(math.asin(clamp(pos[2] / max(1e-9, r_au), -1, 1)))
        payload = {
            "panel": "orrery", "kind": "planet", "key": f"planet:{pk}",
            "name": info.get("name_cn", pk), "name_en": info.get("name_en", ""),
            "type_cn": info.get("type", "行星"),
            "heliocentric_au": r_au,
            "ecliptic_lon": lon, "ecliptic_lat": lat,
            "position": pos,
            "radius_km": info.get("radius_km", 0),
            "mass_earth": info.get("mass_earth", 0),
            "gravity": info.get("gravity", 0.0),
            "rotation_hours": info.get("rotation_hours", 0),
            "moons": info.get("moons", 0),
            "temperature_c": info.get("temperature_c", "—"),
            "atmosphere": info.get("atmosphere", "—"),
            "note": info.get("note", ""),
            "period_years": state.period_years,
            "orbital_speed_kms": state.orbital_speed_kms,
            "eccentricity": state.e,
            "inclination": state.i,
        }
        self.planet_selected.emit(payload)
        self.status_message.emit(
            f"已选中 {info.get('name_cn', pk)}：日心距 {r_au:.3f} AU，"
            f"黄经 {lon:.1f}°，黄纬 {lat:+.2f}°")
        self.mark_dirty()


__all__ = ["OrreryView"]
