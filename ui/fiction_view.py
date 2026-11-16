# -*- coding: utf-8 -*-
"""
fiction_view.py —— 架空星图视图（3D / 2.5D 银河渲染）
================================================================================
职责：渲染 world_generator 生成的架空宇宙，并提供完整的交互：

    * 3D 透视投影（可绕 Z 轴偏航、绕 X 轴俯仰、滚轮缩放、右键平移）
    * 恒星系节点：光谱色 + 亮度定尺寸 + 势力光晕
    * 航线网络：发光曲线，支持沿航线流动的能量动画
    * 航线规划高亮：起点/终点/路径逐段描边，并显示距离、时间、燃料、风险
    * 飞船：沿航线折线移动，带尾迹与状态环
    * 星图模式：探索 / 政治 / 资源 / 危险度 / 文明，各自的着色与标注逻辑
    * 命中测试：点击选中恒星系并发出信号（同时兼容 Shift+点击设置航线端点）

坐标与投影约定：
    * 世界坐标：1 单位 = 1 光年，z 为银道面法向
    * 相机：先绕 Z 轴旋转 yaw，再绕 X 轴旋转 pitch，然后沿 X 轴平移（透视投影）
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

from 核心 import world_generator as WG
from 核心.utils import (LOG, clamp, format_distance_ly, format_gametime, human_duration,
                        rgb_str, vec3_dist)

# ==============================================================================
# 星图模式定义
# ==============================================================================

MAP_MODES: Tuple[Tuple[str, str], ...] = (
    ("explore", "探索"),
    ("politics", "政治"),
    ("resource", "资源"),
    ("danger", "危险度"),
    ("civilization", "文明"),
)

C_GRID = QColor(0, 150, 200, 90)
C_ROUTE = QColor(115, 218, 255, 150)
C_ROUTE_ACTIVE = QColor(255, 200, 90, 230)
C_SHIP = QColor(255, 240, 180)
C_BG_TOP = QColor(6, 10, 24)

#: 各光谱型的显示颜色（与 real_sky_view 保持一致的观感）
SPECTRAL_RGB: Dict[str, Tuple[int, int, int]] = {
    "O": (155, 176, 255), "B": (170, 191, 255), "A": (202, 215, 255),
    "F": (248, 247, 255), "G": (255, 244, 232), "K": (255, 210, 161),
    "M": (255, 180, 130), "L": (255, 130, 100), "T": (200, 90, 120),
    "W": (230, 240, 255), "N": (120, 160, 255), "D": (220, 230, 255),
}


class FictionView(QWidget):
    """架空星图渲染与交互视图。"""

    system_selected = Signal(int)              # 选中恒星系（sid）
    status_message = Signal(str)
    view_changed = Signal(dict)
    ship_event = Signal(str)
    route_endpoint_changed = Signal(int, str)  # 右键菜单设置了航线端点 (sid, "start"/"goal")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        self.galaxy: Optional[WG.Galaxy] = None
        self.mode = "explore"
        self.red_mode = False

        # ---- 相机 ----
        self.yaw = math.radians(-58.0)
        self.pitch = math.radians(38.0)
        self.zoom = 3.0                 # 像素 / 光年
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.show_routes = True
        self.show_labels = True
        self.show_grid = True
        self.show_ships = True
        self.show_halos = True
        self._auto_fit_done = False

        # ---- 交互状态 ----
        self._drag_mode = 0
        self._last_pos = QPoint(0, 0)
        self._hover_sid: Optional[int] = None
        self.selected_sid: Optional[int] = None
        self.route_start: Optional[int] = None
        self.route_goal: Optional[int] = None
        self.planned_route: Optional[Dict[str, object]] = None
        self._screen_pos: Dict[int, QPointF] = {}
        self._depth: Dict[int, float] = {}

        # ---- 时间与动画 ----
        self.year = 0.0
        self.time_scale = 1.0
        self.playing = False
        self._anim_phase = 0.0
        self._last_tick = time.perf_counter()
        self._frame_ms = 0.0

        # ---- 统计数据（状态栏） ----
        self._visible_stats: Dict[str, int] = {}

        # ---- 渲染缓存（CPU 绘制下把渐变预烘焙成贴图，帧率提升非常明显） ----
        self._glow_cache: Dict[str, QPixmap] = {}
        self._path_cache: Dict[Tuple[str, float, float, float, float], QPainterPath] = {}
        self._halo_cache: Dict[str, QPixmap] = {}
        self._needs_repaint = True          # 脏标记：只有内容变化时才重绘

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ==========================================================================
    # 一、数据设置
    # ==========================================================================

    def set_galaxy(self, galaxy: Optional[WG.Galaxy]) -> None:
        """载入一个架空宇宙并自动适配视野。"""
        self.galaxy = galaxy
        self.selected_sid = None
        self._hover_sid = None
        self.route_start = None
        self.route_goal = None
        self.planned_route = None
        self._auto_fit_done = False
        if galaxy is not None:
            self.year = galaxy.year
            self.time_scale = galaxy.time_scale
            self.auto_fit()
            LOG.info(f"架空星图已载入：{galaxy.name}（{len(galaxy.systems)} 个恒星系）")
        self.mark_dirty()

    def auto_fit(self) -> None:
        """根据星系尺度自动设置缩放与平移，保证整体可见。

        zoom 的含义是「屏幕中心处的像素/光年」，因此：
            zoom = min(视口宽, 视口高) × 0.78 / 星系最大跨度
        """
        g = self.galaxy
        if not g or not g.systems:
            return
        xs = [s.position[0] for s in g.systems]
        ys = [s.position[1] for s in g.systems]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 15.0)
        w = max(200, self.width())
        h = max(150, self.height())
        self.zoom = clamp(min(w, h) * 0.78 / span, 0.15, 120.0)
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.yaw = math.radians(-58.0)
        self.pitch = math.radians(38.0)
        self._auto_fit_done = True
        self._emit_view()
        self.mark_dirty()

    def reset_view(self) -> None:
        """重置视角（等比适配 + 默认倾角）。"""
        self.auto_fit()

    def set_mode(self, mode: str) -> None:
        """切换星图模式：探索 / 政治 / 资源 / 危险度 / 文明。"""
        if any(m == mode for m, _ in MAP_MODES):
            self.mode = mode
            self.mark_dirty()

    def set_playing(self, playing: bool) -> None:
        self.playing = bool(playing)

    def set_time_scale(self, scale: float) -> None:
        self.time_scale = float(scale)

    def set_layer(self, key: str, enabled: bool) -> None:
        """图层开关（航线 / 标签 / 网格 / 飞船 / 势力光晕）。"""
        mapping = {
            "routes": "show_routes", "labels": "show_labels", "grid": "show_grid",
            "ships": "show_ships", "halos": "show_halos",
        }
        attr = mapping.get(key)
        if attr:
            setattr(self, attr, bool(enabled))
            self.mark_dirty()

    # ==========================================================================
    # 二、时间推进
    # ==========================================================================

    def advance_time(self, years: float) -> List[str]:
        """推进银河历时间，并驱动飞船移动（返回事件日志）。"""
        if self.galaxy is None or years <= 0:
            return []
        self.year += years
        self.galaxy.year = self.year
        self.galaxy.time_scale = self.time_scale
        try:
            logs = WG.advance_ships(self.galaxy, years)
        except Exception as exc:
            LOG.error("飞船推进失败", exc)
            logs = []
        for line in logs:
            self.ship_event.emit(line)
        return logs

    def _on_tick(self) -> None:
        """60 FPS 定时器：仅在内容变化（时间推进 / 交互 / 动画）时重绘。

        静态星图没有理由每秒重绘 60 次——省下来的 CPU 时间可以让
        相机操作与飞船动画更顺滑（这也是「不卡顿」的关键）。

        注意：这里必须直接调 update()，**不能**调 mark_dirty()。
        mark_dirty() 会把 _needs_repaint 重新置为 True，于是下一轮 tick 又会
        重绘，形成"清标记→置标记→再清标记"的自我永动循环：实测会让架空星图
        在完全没有交互时空转 54 次/秒、CPU 长期 110% 以上（曾经的真实故障）。
        """
        now = time.perf_counter()
        dt = clamp(now - self._last_tick, 0.0, 0.25)
        self._last_tick = now
        changed = False
        if self.playing and self.galaxy is not None:
            # 时间推进时，逐帧重绘让星图动起来
            years = dt * (self.time_scale * 0.5)
            self.advance_time(years)
            changed = True
        elif self.galaxy is not None and self.galaxy.ships and self.show_ships:
            # 有飞船在航线上时，保持流动光效的动画
            changed = True
        if not changed:
            self._anim_phase = 0.0
            return
        self._anim_phase = (self._anim_phase + dt * 0.6) % 1.0
        self._needs_repaint = False
        self.update()

    def mark_dirty(self) -> None:
        """外部（或交互）请求重绘。"""
        self._needs_repaint = True
        self.update()

    # ==========================================================================
    # 三、投影
    # ==========================================================================

    def _project(self, pos: Sequence[float]) -> Optional[Tuple[QPointF, float]]:
        """世界坐标 → (屏幕坐标, 深度)。

        投影流程（与真实相机一致）：
            1. 绕 Z 轴偏航 yaw，把星系盘转到合适朝向
            2. 绕 X 轴俯仰 pitch，获得立体感
            3. 透视投影：屏幕比例 = zoom · cam / (深度 + cam)
               —— 近处的星系间距更大、远处的更密，形成自然的纵深
        cam 随缩放自适应，保证任何缩放级别下透视强度都合适。
        """
        x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        rx = x * cy - y * sy
        ry = x * sy + y * cy
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        ry2 = ry * cp - z * sp
        rz2 = ry * sp + z * cp
        # 相机距离：保证在 zoom 较大（贴近观察）时透视明显，缩小时接近正交投影
        cam = 900.0 / max(0.05, self.zoom) + 60.0
        denom = rz2 + cam
        if denom <= 1.0:
            return None
        k = self.zoom * cam / denom
        sx = self.width() * 0.5 + self.pan_x + rx * k
        sy_ = self.height() * 0.5 + self.pan_y - ry2 * k
        return QPointF(sx, sy_), denom

    def _emit_view(self) -> None:
        """视角变了：发信号给主窗口，**并且必须自己请求重绘**。

        ★ 这里曾经有一个致命遗漏，症状就是用户报的"架空星图生成后拖不动 3D 视角"：
          本方法原来只 `emit`，没有 `update()` / `mark_dirty()`；而重绘依赖
          `_on_tick` 把 `_needs_repaint` 清掉后 `update()`。`_on_tick` 只在这两种
          情况下认为"内容变了"：① 时间在推进（`playing`）② 有飞船在航线上。
          问题是**新生成的宇宙飞船数是 0**（实测 four 档规模都是 0，飞船要用户手动
          "派遣"才有），于是 `_on_tick` 每帧直接 return、从不重绘——
          拖拽确实改了 yaw/pitch，但画面一次都没重画，看起来就是"拖不动"。
          一旦用户派了一艘船，动画让每帧都重绘，同一个拖拽又"突然好了"——
          这个线索正好指向上面的判据。
        """
        self.view_changed.emit({
            "yaw": math.degrees(self.yaw), "pitch": math.degrees(self.pitch),
            "zoom": self.zoom, "mode": self.mode,
        })
        self.mark_dirty()

    # ==========================================================================
    # 四、绘制
    # ==========================================================================

    #: 发光贴图尺寸档位
    GLOW_SIZES: Tuple[int, ...] = (10, 16, 24, 34, 48, 68, 96, 136)

    def _glow_pixmap(self, rgb: Sequence[int], size: int) -> QPixmap:
        """获取（并缓存）指定颜色/尺寸的圆形发光贴图。"""
        key = f"{int(rgb[0])},{int(rgb[1])},{int(rgb[2])}|{size}"
        pix = self._glow_cache.get(key)
        if pix is not None:
            return pix
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing, True)
        half = size / 2.0
        color = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
        grad = QRadialGradient(QPointF(half, half), half)
        grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 255))
        grad.setColorAt(0.22, QColor(color.red(), color.green(), color.blue(), 130))
        grad.setColorAt(0.52, QColor(color.red(), color.green(), color.blue(), 40))
        grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(half, half), half, half)
        p.end()
        if len(self._glow_cache) < 512:
            self._glow_cache[key] = pix
        return pix

    def _halo_pixmap(self, rgb: Sequence[int], size: int) -> QPixmap:
        """势力范围光晕贴图（更柔和、更大范围）。"""
        key = f"{int(rgb[0])},{int(rgb[1])},{int(rgb[2])}|{size}"
        pix = self._halo_cache.get(key)
        if pix is not None:
            return pix
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing, True)
        half = size / 2.0
        color = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
        grad = QRadialGradient(QPointF(half, half), half)
        grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 78))
        grad.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(), 30))
        grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(half, half), half, half)
        p.end()
        if len(self._halo_cache) < 64:
            self._halo_cache[key] = pix
        return pix

    def _nearest_size(self, want: float, sizes: Sequence[int] = GLOW_SIZES) -> int:
        """选择不小于 want 的最小档位。"""
        for s in sizes:
            if s >= want:
                return s
        return sizes[-1]

    def _cached_path(self, a: QPointF, b: QPointF, bend: float) -> QPainterPath:
        """缓存二次贝塞尔航线的 QPainterPath（屏幕坐标变化时自动重建）。"""
        key = (round(a.x(), 1), round(a.y(), 1), round(b.x(), 1), round(b.y(), 1))
        path = self._path_cache.get(key)
        if path is not None:
            return path
        mx, my = (a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0
        dx, dy = b.x() - a.x(), b.y() - a.y()
        nx, ny = -dy, dx
        length = math.hypot(nx, ny) or 1.0
        cx = mx + nx / length * bend * length * 0.35
        cy = my + ny / length * bend * length * 0.35
        path = QPainterPath(a)
        path.quadTo(QPointF(cx, cy), b)
        if len(self._path_cache) < 4096:
            self._path_cache[key] = path
        return path

    def paintEvent(self, event) -> None:  # noqa: N802
        t0 = time.perf_counter()
        p = QPainter(self)
        profile = bool(os.environ.get("STELLAR_NEXUS_PROFILE"))
        marks: List[Tuple[str, float]] = []

        def mark(name: str) -> None:
            if profile:
                marks.append((name, (time.perf_counter() - t0) * 1000.0))

        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            self._draw_background(p)
            mark("background")
            if self.galaxy is None:
                self._draw_empty_hint(p)
                return
            if self.show_grid:
                self._draw_grid(p)
            mark("grid")
            self._compute_screen_positions()
            mark("project")
            if self.show_halos and self.mode in ("politics", "explore"):
                self._draw_faction_halos(p)
            mark("halos")
            if self.show_routes:
                self._draw_routes(p)
            mark("routes")
            if self.planned_route:
                self._draw_planned_route(p)
            mark("plan")
            self._draw_systems(p)
            mark("systems")
            if self.show_ships:
                self._draw_ships(p)
            mark("ships")
            if self.show_labels:
                self._draw_labels(p)
            mark("labels")
            self._draw_hud(p)
            mark("hud")
        except Exception as exc:  # pragma: no cover - 防御性
            LOG.error("架空星图绘制异常", exc)
            import traceback
            for line in traceback.format_exc().strip().splitlines()[-6:]:
                LOG.error("TRACE", line)
            p.setPen(QColor(255, 120, 120))
            p.drawText(self.rect(), Qt.AlignCenter, f"渲染异常：{exc}")
        finally:
            p.end()
        self._frame_ms = (time.perf_counter() - t0) * 1000.0
        if profile and marks:
            prev = 0.0
            parts = []
            for name, at in marks:
                parts.append(f"{name} {at - prev:.1f}")
                prev = at
            LOG.info("架空星图各层耗时(ms)：" + " | ".join(parts) + f" | total {self._frame_ms:.1f}")

    def _draw_background(self, p: QPainter) -> None:
        """深空背景：纵向渐变 + 星尘。"""
        w, h = self.width(), self.height()
        grad = QLinearGradient(0, 0, 0, h)
        if self.red_mode:
            grad.setColorAt(0.0, QColor(24, 6, 4))
            grad.setColorAt(1.0, QColor(4, 1, 2))
        else:
            grad.setColorAt(0.0, QColor(6, 10, 24))
            grad.setColorAt(0.6, QColor(3, 6, 16))
            grad.setColorAt(1.0, QColor(1, 2, 6))
        p.fillRect(self.rect(), QBrush(grad))
        # 远景星尘（用固定种子的伪随机，保证稳定不闪烁；关闭抗锯齿以省时间）
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(Qt.NoPen)
        seed = 12345
        for i in range(160):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            x = (seed % 10000) / 10000.0 * w
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            y = (seed % 10000) / 10000.0 * h
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            s = 0.5 + (seed % 100) / 100.0 * 1.1
            a = 40 + (seed % 60)
            p.setBrush(QColor(180, 220, 255, a))
            p.drawEllipse(QPointF(x, y), s, s)
        p.setRenderHint(QPainter.Antialiasing, True)

    def _draw_empty_hint(self, p: QPainter) -> None:
        """没有载入宇宙时的提示。"""
        p.setPen(QColor(175, 246, 255, 235))
        p.setFont(QFont("Microsoft YaHei UI", 12, QFont.DemiBold))
        p.drawText(self.rect().adjusted(0, -20, 0, -20), Qt.AlignCenter, "尚未生成架空宇宙")
        p.setPen(QColor(182, 226, 246, 238))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(self.rect().adjusted(0, 26, 0, 26), Qt.AlignCenter,
                   "在左侧面板输入随机种子并点击「生成宇宙」，或使用「工具 → 生成随机宇宙」")

    def _draw_grid(self, p: QPainter) -> None:
        """绘制三维参考网格（银道面上的同心圈 + 放射线）。"""
        g = self.galaxy
        if not g or not g.systems:
            return
        radius = max(40.0, max(math.hypot(s.position[0], s.position[1]) for s in g.systems) * 1.15)
        center = (0.0, 0.0, 0.0)
        p.setPen(QPen(C_GRID, 1.0))
        # 同心圈
        for k in range(1, 5):
            r = radius * k / 4.0
            pts: List[QPointF] = []
            for deg in range(0, 361, 6):
                a = math.radians(deg)
                res = self._project((center[0] + r * math.cos(a), center[1] + r * math.sin(a), 0.0))
                if res is None:
                    if len(pts) > 1:
                        p.drawPolyline(QPolygonF(pts))
                    pts = []
                    continue
                pts.append(res[0])
            if len(pts) > 1:
                p.drawPolyline(QPolygonF(pts))
        # 放射线
        for deg in range(0, 360, 30):
            a = math.radians(deg)
            a1 = self._project((radius * math.cos(a), radius * math.sin(a), 0.0))
            a2 = self._project((-radius * math.cos(a), -radius * math.sin(a), 0.0))
            if a1 and a2:
                p.setPen(QPen(QColor(0, 120, 170, 40), 1.0))
                p.drawLine(a1[0], a2[0])
        # 比例尺标注
        p.setPen(QColor(178, 236, 252, 225))
        p.setFont(QFont("Consolas", 9))
        p.drawText(QPointF(10, self.height() - 12), f"1 格 = {radius / 4.0:,.0f} 光年")

    def _compute_screen_positions(self) -> None:
        """每帧计算所有恒星系的屏幕坐标与深度（供绘制与命中测试共用）。"""
        self._screen_pos = {}
        self._depth = {}
        g = self.galaxy
        if not g:
            return
        w, h = self.width(), self.height()
        margin = 60.0
        for s in g.systems:
            res = self._project(s.position)
            if res is None:
                continue
            pt, depth = res
            self._depth[s.sid] = depth
            if -margin <= pt.x() <= w + margin and -margin <= pt.y() <= h + margin:
                self._screen_pos[s.sid] = pt
        self._visible_stats = {"visible": len(self._screen_pos), "total": len(g.systems)}

    def _faction_color(self, name: str) -> QColor:
        g = self.galaxy
        if g:
            for f in g.factions:
                if f.name == name:
                    return QColor(int(f.color[0]), int(f.color[1]), int(f.color[2]))
        return QColor(120, 160, 190)

    def _system_center_color(self, sys_obj: WG.StarSystem) -> QColor:
        """按当前星图模式决定节点颜色。"""
        g = self.galaxy
        if self.mode == "politics":
            return self._faction_color(sys_obj.faction)
        if self.mode == "resource":
            v = clamp(sys_obj.resource_richness, 0.0, 1.0)
            return QColor(int(60 + 195 * v), int(230 - 60 * v), int(200 - 150 * v))
        if self.mode == "danger":
            v = clamp(sys_obj.danger, 0.0, 1.0)
            return QColor(int(90 + 165 * v), int(220 - 190 * v), int(120 - 80 * v))
        if self.mode == "civilization":
            if sys_obj.civ_name:
                return QColor(255, 220, 130)
            if sys_obj.population_millions > 0:
                return QColor(150, 255, 210)
            return QColor(120, 160, 190)
        base = SPECTRAL_RGB.get(sys_obj.star_class, (255, 240, 220))
        return QColor(base[0], base[1], base[2])

    def _draw_faction_halos(self, p: QPainter) -> None:
        """势力范围光晕：成员星系的屏幕位置外接矩形中心放置一张柔和大光晕。

        相比「每个星系一张径向渐变」，这种方式只需每势力一张缓存贴图，
        在 220 星系的超大型宇宙中把这一层的耗时从 ~40ms 降到 <2ms。
        """
        g = self.galaxy
        if not g:
            return
        p.setPen(Qt.NoPen)
        for f in g.factions:
            color = (int(f.color[0]), int(f.color[1]), int(f.color[2]))
            pts = [self._screen_pos[sid] for sid in f.member_systems if sid in self._screen_pos]
            if len(pts) < 2:
                continue
            xs = [q.x() for q in pts]
            ys = [q.y() for q in pts]
            cx = (min(xs) + max(xs)) / 2.0
            cy = (min(ys) + max(ys)) / 2.0
            radius = max(max(xs) - min(xs), max(ys) - min(ys)) / 2.0 + 40.0
            size = self._nearest_size(radius * 2.0, self.GLOW_SIZES + (260, 360, 480, 640, 900))
            pix = self._halo_pixmap(color, size)
            p.setOpacity(0.55)
            p.drawPixmap(QPointF(cx - size / 2.0, cy - size / 2.0), pix)
            p.setOpacity(1.0)

    def _draw_routes(self, p: QPainter) -> None:
        """航线：普通航线为淡青色曲线，控制方势力航线带上势力色。

        性能取舍（220 星系 / 450+ 航线时很关键）：
            * 航线数量多时关闭抗锯齿（细直线几乎看不出差别，但速度快一倍以上）
            * 只在航线较少时绘制沿线流动的能量光点，避免大量小圆绘制
            * 路径对象按屏幕坐标缓存，避免每帧重新构造贝塞尔曲线
        """
        g = self.galaxy
        if not g:
            return
        active_keys: Set[Tuple[int, int]] = set()
        if self.planned_route:
            path_ids = [int(x) for x in self.planned_route.get("path", [])]
            for a, b in zip(path_ids[:-1], path_ids[1:]):
                active_keys.add((min(a, b), max(a, b)))
        many = len(g.routes) > 260
        p.setRenderHint(QPainter.Antialiasing, not many)
        show_flow = (not many) and self.mode == "explore"
        for r in g.routes:
            pa = self._screen_pos.get(r.a)
            pb = self._screen_pos.get(r.b)
            if pa is None or pb is None:
                continue
            key = r.key
            if key in active_keys:
                continue
            if r.control:
                c = self._faction_color(r.control)
                color = QColor(c.red(), c.green(), c.blue(), 90)
            else:
                color = C_ROUTE
            if self.red_mode:
                color = QColor(220, 70, 50, 90)
            width = 0.8 + clamp(r.distance_ly / 60.0, 0.0, 1.2)
            pen = QPen(color, width)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            p.drawPath(self._cached_path(pa, pb, 0.12))
            if show_flow:
                # 能量流动：仅航线较少时绘制，避免大量小圆的绘制开销
                t = (self._anim_phase + (r.a * 0.13 + r.b * 0.07) % 1.0) % 1.0
                mx = pa.x() + (pb.x() - pa.x()) * t
                my = pa.y() + (pb.y() - pa.y()) * t
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color.red(), color.green(), color.blue(), 200))
                p.drawEllipse(QPointF(mx, my), 2.0, 2.0)
                p.setPen(pen)
        p.setRenderHint(QPainter.Antialiasing, True)

    def _draw_curve(self, p: QPainter, a: QPointF, b: QPointF, bend: float = 0.12) -> None:
        """把直线航线画成轻微弯曲的曲线（使用缓存路径，渲染更省时）。"""
        p.drawPath(self._cached_path(a, b, bend))

    def _draw_planned_route(self, p: QPainter) -> None:
        """高亮规划航线：起终点标记 + 每段描边 + 信息标签。"""
        route = self.planned_route
        if not route or not route.get("ok"):
            return
        path = [int(x) for x in route.get("path", [])]
        pts = [self._screen_pos[sid] for sid in path if sid in self._screen_pos]
        if len(pts) >= 2:
            pen = QPen(C_ROUTE_ACTIVE, 2.2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            for a, b in zip(pts, pts[1:]):
                self._draw_curve(p, a, b, bend=0.10)
            # 流动光点
            total = sum(1 for _ in pts)
            for i in range(len(pts) - 1):
                t = (self._anim_phase * 2.0 + i * 0.25) % 1.0
                a, b = pts[i], pts[i + 1]
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 230, 140, 220))
                p.drawEllipse(QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t), 2.6, 2.6)
        # 起点/终点
        for sid, label, color in ((self.route_start, "起点", QColor(120, 255, 180)),
                                  (self.route_goal, "终点", QColor(255, 150, 150))):
            if sid is None or sid not in self._screen_pos:
                continue
            pt = self._screen_pos[sid]
            p.setPen(QPen(color, 1.6, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pt, 16.0, 16.0)
            p.setPen(color)
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(QPointF(pt.x() + 18, pt.y() - 12), label)
        # 信息标签
        if pts:
            info = (f"跳数 {len(path) - 1}   距离 {format_distance_ly(float(route.get('distance', 0.0)))}   "
                    f"耗时 {human_duration(float(route.get('days', 0.0)) * 86400.0)}   "
                    f"燃料 {route.get('fuel')}   风险 {float(route.get('risk', 0.0)) * 100:.0f}%")
            anchor = pts[0]
            p.setPen(QColor(255, 235, 175, 245))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(QPointF(anchor.x() + 18, anchor.y() + 22), info)

    def _draw_systems(self, p: QPainter) -> None:
        """绘制恒星系节点：光晕 + 核心 + 类型标记。

        光晕使用缓存的发光贴图；星系数量很多时只给较亮的节点画光晕，
        在保持观感的同时把这一层的耗时控制在可接受范围内。
        """
        g = self.galaxy
        if not g:
            return
        many = len(self._screen_pos) > 120
        # 按深度从远到近绘制，保证近处覆盖远处
        order = sorted(self._screen_pos.keys(), key=lambda sid: -self._depth.get(sid, 0.0))
        for sid in order:
            sys_obj = g.system(sid)
            if sys_obj is None:
                continue
            pt = self._screen_pos[sid]
            color = self._system_center_color(sys_obj)
            if self.red_mode:
                color = QColor(255, 80, 60)
            # 尺寸随亮度与缩放变化
            base = 2.2 + clamp(sys_obj.brightness_score * 0.35, 0.4, 3.2)
            scale = clamp(math.sqrt(self.zoom / 3.0), 0.55, 3.2)
            r = base * scale
            depth = self._depth.get(sid, 1.0)
            fog = clamp(1.0 - (depth - 400.0) / 2600.0, 0.28, 1.0)
            # 光晕：使用缓存的发光贴图（避免每帧重建 QRadialGradient）
            important = bool(sys_obj.civ_name) or sys_obj.has_station or bool(sys_obj.anomaly)
            want = r * 9.0
            if fog > 0.35 and want >= 14.0 and (not many or important
                                                or sid == self.selected_sid
                                                or sid == self._hover_sid):
                gsize = self._nearest_size(want)
                pix = self._glow_pixmap((color.red(), color.green(), color.blue()), gsize)
                p.setOpacity(fog)
                p.drawPixmap(QPointF(pt.x() - gsize / 2.0, pt.y() - gsize / 2.0), pix)
                p.setOpacity(1.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color.red(), color.green(), color.blue(), int(255 * fog)))
            p.drawEllipse(pt, r, r)
            # 空间站 / 异常 / 文明标记
            if sys_obj.has_station:
                p.setPen(QPen(QColor(140, 255, 230, int(200 * fog)), 1.0))
                p.setBrush(Qt.NoBrush)
                p.drawRect(QRectF(pt.x() - r - 3, pt.y() - r - 3, (r + 3) * 2, (r + 3) * 2))
            if sys_obj.anomaly:
                p.setPen(QPen(QColor(232, 165, 255, int(240 * fog)), 1.3))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r + 4.5, r + 4.5)
            # 选中 / 悬停高亮
            if sid == self.selected_sid:
                p.setPen(QPen(QColor(255, 230, 150, 240), 1.8))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r + 8.0, r + 8.0)
                p.drawLine(QPointF(pt.x() - r - 14, pt.y()), QPointF(pt.x() - r - 9, pt.y()))
                p.drawLine(QPointF(pt.x() + r + 9, pt.y()), QPointF(pt.x() + r + 14, pt.y()))
            elif sid == self._hover_sid:
                p.setPen(QPen(QColor(120, 255, 220, 200), 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r + 5.5, r + 5.5)

    def _draw_ships(self, p: QPainter) -> None:
        """绘制飞船：沿航线插值定位，带尾迹与状态色（红=受损、灰=已毁）。"""
        g = self.galaxy
        if not g or not g.ships:
            return
        p.setFont(QFont("Microsoft YaHei UI", 9))
        for ship in g.ships:
            if len(ship.route) < 2:
                continue
            # 计算当前所在位置（沿折线插值）
            seg_count = len(ship.route) - 1
            frac = clamp(ship.progress, 0.0, 1.0) * seg_count
            idx = int(clamp(math.floor(frac), 0, seg_count - 1))
            t = frac - idx
            a = g.system(ship.route[idx])
            b = g.system(ship.route[idx + 1])
            if a is None or b is None:
                continue
            wx = a.position[0] + (b.position[0] - a.position[0]) * t
            wy = a.position[1] + (b.position[1] - a.position[1]) * t
            wz = a.position[2] + (b.position[2] - a.position[2]) * t
            res = self._project((wx, wy, wz))
            if res is None:
                continue
            pt, _depth = res
            if ship.destroyed:
                color = QColor(130, 130, 130)
            elif ship.hull < 60:
                color = QColor(255, 110, 90)
            elif ship.arrived:
                color = QColor(140, 255, 180)
            else:
                color = C_SHIP
            # 尾迹
            tail = self._project((wx - (b.position[0] - a.position[0]) * 0.06,
                                  wy - (b.position[1] - a.position[1]) * 0.06,
                                  wz - (b.position[2] - a.position[2]) * 0.06))
            if tail is not None:
                p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 120), 1.4))
                p.drawLine(tail[0], pt)
            # 舰体标记（朝向航线方向的小三角）
            angle = math.atan2(pt.y() - (tail[0].y() if tail else pt.y()),
                               pt.x() - (tail[0].x() if tail else pt.x() - 1.0))
            size = 6.0
            tri = QPolygonF([
                QPointF(pt.x() + math.cos(angle) * size, pt.y() + math.sin(angle) * size),
                QPointF(pt.x() + math.cos(angle + 2.5) * size * 0.7, pt.y() + math.sin(angle + 2.5) * size * 0.7),
                QPointF(pt.x() + math.cos(angle - 2.5) * size * 0.7, pt.y() + math.sin(angle - 2.5) * size * 0.7),
            ])
            p.setPen(QPen(QColor(20, 30, 40, 220), 1.0))
            p.setBrush(color)
            p.drawPolygon(tri)
            p.setPen(QColor(color.red(), color.green(), color.blue(), 220))
            p.drawText(QPointF(pt.x() + 8, pt.y() - 6), ship.name)

    def _draw_labels(self, p: QPainter) -> None:
        """恒星系名称：按缩放级别控制显示密度，避免文字重叠。"""
        g = self.galaxy
        if not g:
            return
        # 缩得越小只显示重要星系（有文明/首都/异常）
        show_all = self.zoom > 4.5 or len(self._screen_pos) < 60
        p.setFont(QFont("Microsoft YaHei UI", 9))
        fm = QFontMetrics(p.font())
        placed: List[QRectF] = []
        for sid, pt in self._screen_pos.items():
            sys_obj = g.system(sid)
            if sys_obj is None:
                continue
            important = bool(sys_obj.civ_name) or sys_obj.has_station or bool(sys_obj.anomaly)
            if not show_all and not important:
                continue
            text = sys_obj.name
            if self.mode == "politics" and sys_obj.faction:
                text = f"{sys_obj.name} · {sys_obj.faction}"
            tw = fm.horizontalAdvance(text)
            rect = QRectF(pt.x() + 9, pt.y() - 6, tw + 4, 12)
            if rect.right() > self.width() - 2:
                rect.moveLeft(pt.x() - tw - 12)
            if any(rect.intersects(q) for q in placed):
                continue
            placed.append(rect)
            color = self._system_center_color(sys_obj)
            if self.red_mode:
                color = QColor(255, 100, 80)
            p.setPen(QColor(color.red(), color.green(), color.blue(),
                            225 if important else 160))
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)

    def _draw_hud(self, p: QPainter) -> None:
        """HUD：模式说明、时间、缩放、图例。"""
        g = self.galaxy
        w, h = self.width(), self.height()
        if g is None:
            return
        p.setFont(QFont("Consolas", 9))
        color = QColor(255, 125, 105) if self.red_mode else QColor(145, 246, 255, 242)
        p.setPen(color)
        mode_cn = dict(MAP_MODES).get(self.mode, self.mode)
        lines = [
            f"GALAXY  {g.name}   SEED {g.seed}",
            f"MODE    {mode_cn}   ZOOM {self.zoom:5.2f} px/ly",
            f"YAW     {math.degrees(self.yaw) % 360:6.1f}°   PITCH {math.degrees(self.pitch):5.1f}°",
            f"TIME    {format_gametime(self.year)}   {'▶ ' + str(self.time_scale) + 'x' if self.playing else '⏸ 暂停'}",
            f"VISIBLE {self._visible_stats.get('visible', 0)}/{self._visible_stats.get('total', 0)} 星系   "
            f"帧耗时 {self._frame_ms:.1f} ms",
        ]
        y = 16
        for line in lines:
            p.drawText(QPointF(10, y), line)
            y += 12
        # 图例
        legend = {
            "explore": "节点色 = 光谱型；青线 = 航线",
            "politics": "节点色 = 所属势力；光晕 = 领土",
            "resource": "节点色 = 资源丰富度（绿→黄）",
            "danger": "节点色 = 危险度（绿→红）",
            "civilization": "节点色 = 文明状态（金=有文明）",
        }.get(self.mode, "")
        if legend:
            p.setPen(QColor(192, 240, 255, 238))
            p.drawText(QPointF(10, h - 12), legend)
        if self.red_mode:
            p.setPen(QColor(255, 90, 70, 220))
            p.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
            p.drawText(QPointF(w - 92, 18), "夜间红光模式")

    # ==========================================================================
    # 五、交互
    # ==========================================================================

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._last_pos = event.position().toPoint()
        if event.button() == Qt.LeftButton:
            self._drag_mode = 1
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.RightButton:
            self._drag_mode = 2
            self.setCursor(Qt.SizeAllCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._drag_mode:
            dx = pos.x() - self._last_pos.x()
            dy = pos.y() - self._last_pos.y()
            self._last_pos = pos
            if self._drag_mode == 1:
                # 左键：先偏航，同时按纵向拖拽调整俯仰
                self.yaw += dx * 0.006
                self.pitch = clamp(self.pitch + dy * 0.006, math.radians(-88), math.radians(88))
            else:
                self.pan_x += dx
                self.pan_y += dy
            self._emit_view()
        else:
            sid = self._system_at(event.position())
            if sid != self._hover_sid:
                self._hover_sid = sid
                if sid is not None and self.galaxy:
                    s = self.galaxy.system(sid)
                    if s:
                        self.setToolTip(f"{s.name}  {s.spectral}  {s.planet_count} 行星"
                                        f"{'  ' + s.faction if s.faction else ''}")
                else:
                    self.setToolTip("")
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_mode == 1 and event.button() == Qt.LeftButton:
            moved = (event.position().toPoint() - self._last_pos).manhattanLength()
            if moved < 4:
                sid = self._system_at(event.position())
                if sid is not None:
                    self.pick_system(sid, shift=bool(event.modifiers() & Qt.ShiftModifier))
        elif self._drag_mode == 2 and event.button() == Qt.RightButton:
            moved = (event.position().toPoint() - self._last_pos).manhattanLength()
            if moved < 5:
                self._show_context_menu(event.position())
        self._drag_mode = 0
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def _show_context_menu(self, pos: QPointF) -> None:
        """右键菜单：恒星系相关操作（设为航线端点、查看邻居、复制坐标）。"""
        from PySide6.QtWidgets import QMenu
        g = self.galaxy
        if g is None:
            return
        sid = self._system_at(pos)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:rgba(8,22,34,250);border:1px solid rgba(0,190,230,180);"
            "border-radius:4px;padding:4px;color:#e6f6fd;}"
            "QMenu::item{padding:5px 22px 5px 16px;border-radius:3px;}"
            "QMenu::item:selected{background:rgba(200,120,255,130);color:#ffffff;}"
            "QMenu::separator{height:1px;background:rgba(0,190,230,120);margin:4px 6px;}")
        if sid is not None:
            s = g.system(sid)
            if s is None:
                return
            title = menu.addAction(f"{s.name}　{s.spectral}　{s.planet_count} 行星")
            title.setEnabled(False)
            menu.addSeparator()
            menu.addAction("▶ 设为航线起点", lambda: self._menu_set_endpoint(sid, "start"))
            menu.addAction("⏹ 设为航线终点", lambda: self._menu_set_endpoint(sid, "goal"))
            menu.addSeparator()
            menu.addAction("◎ 居中显示", lambda: self.focus_system(sid))
            menu.addSeparator()
            near = sorted((x for x in g.systems if x.sid != sid),
                          key=lambda x: g.distance(sid, x.sid))[:5]
            if near:
                sub = menu.addMenu("📍 连接到最近星系")
                for other in near:
                    d = g.distance(sid, other.sid)
                    sub.addAction(f"{other.name}（{d:.1f} 光年）",
                                  lambda _c=False, o=other.sid: self._menu_connect(sid, o))
            menu.addSeparator()
            menu.addAction("📋 复制星系信息", lambda: self._copy_system_info(sid))
        else:
            title = menu.addAction("星图空白处")
            title.setEnabled(False)
            menu.addSeparator()
            menu.addAction("🔄 适配视图", self.auto_fit)
            menu.addSeparator()
            menu.addAction("图层：航线网络",
                           lambda: self.set_layer("routes", not self.show_routes))
            menu.addAction("图层：星系名称",
                           lambda: self.set_layer("labels", not self.show_labels))
            menu.addAction("图层：参考网格",
                           lambda: self.set_layer("grid", not self.show_grid))
            menu.addAction("图层：势力光晕",
                           lambda: self.set_layer("halos", not self.show_halos))
        menu.exec(self.mapToGlobal(pos.toPoint()))

    def _menu_set_endpoint(self, sid: int, which: str) -> None:
        """右键设置航线端点，并通知主窗口同步下拉框。"""
        if which == "start":
            self.route_start = int(sid)
        else:
            self.route_goal = int(sid)
        self.route_endpoint_changed.emit(int(sid), which)
        self.update()

    def _menu_connect(self, a: int, b: int) -> None:
        """把两个星系设为航线起终点。"""
        self._menu_set_endpoint(a, "start")
        self._menu_set_endpoint(b, "goal")

    def _copy_system_info(self, sid: int) -> None:
        """复制恒星系关键信息到剪贴板。"""
        try:
            from PySide6.QtGui import QGuiApplication
            g = self.galaxy
            if g is None:
                return
            s = g.system(sid)
            if s is None:
                return
            stats = g.stats()
            lines = [
                f"{s.name}　光谱型 {s.spectral}",
                f"坐标：({s.position[0]:.1f}, {s.position[1]:.1f}, {s.position[2]:.1f}) 光年",
                f"距核心：{math.sqrt(sum(v * v for v in s.position)):.1f} 光年",
                f"恒星质量 {s.mass_solar:.3f} M☉　温度 {s.temperature_k:.0f} K　"
                f"光度 {s.luminosity_solar:.4f} L☉",
                f"行星 {s.planet_count} 颗　人口 {s.population_millions:.2f} 百万",
                f"资源丰富度 {s.resource_richness * 100:.1f}%　危险度 {s.danger * 100:.1f}%",
                f"所属势力：{s.faction or '无主之地'}　本土文明：{s.civ_name or '无'}",
                f"异常：{s.anomaly or '无'}",
                f"文明数 {stats.get('civilizations', 0)}　银河历 {g.year:.2f} 年　种子 {g.seed}",
            ]
            QGuiApplication.clipboard().setText("\n".join(lines))
            self.status_message.emit(f"已复制 {s.name} 的信息")
        except Exception as exc:
            LOG.warn(f"复制星系信息失败：{exc}")

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            factor = 1.14 if delta > 0 else 1.0 / 1.14
            self.zoom = clamp(self.zoom * factor, 0.15, 120.0)
            self._emit_view()
            self.mark_dirty()
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_sid = None
        self.mark_dirty()
        super().leaveEvent(event)

    def _system_at(self, pos: QPointF, radius: float = 13.0) -> Optional[int]:
        """屏幕坐标 → 最近恒星系 id。"""
        best: Optional[int] = None
        best_d = radius * radius
        for sid, pt in self._screen_pos.items():
            d = (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2
            if d < best_d:
                best_d = d
                best = sid
        return best

    def pick_system(self, sid: int, shift: bool = False) -> None:
        """选中恒星系；按住 Shift 时用于设置航线起点/终点。"""
        g = self.galaxy
        if g is None or g.system(sid) is None:
            return
        self.selected_sid = int(sid)
        if shift:
            if self.route_start is None or (self.route_start is not None and self.route_goal is not None):
                self.route_start = int(sid)
                self.route_goal = None
                self.status_message.emit(f"航线起点已设为 {g.system(sid).name}")
            else:
                self.route_goal = int(sid)
                self.status_message.emit(f"航线终点已设为 {g.system(sid).name}")
        self.system_selected.emit(int(sid))
        self.mark_dirty()

    def set_route(self, start: Optional[int], goal: Optional[int],
                  result: Optional[Dict[str, object]] = None) -> None:
        """外部设置航线（供航线规划对话框/面板调用）。"""
        self.route_start = None if start is None else int(start)
        self.route_goal = None if goal is None else int(goal)
        self.planned_route = result
        self.mark_dirty()

    def clear_route(self) -> None:
        self.route_start = None
        self.route_goal = None
        self.planned_route = None
        self.mark_dirty()

    def focus_system(self, sid: int) -> None:
        """把视角平移到指定恒星系（并选中）。"""
        g = self.galaxy
        if g is None:
            return
        s = g.system(sid)
        if s is None:
            return
        # 通过投影反推平移量，使该点落在屏幕中心
        self.pan_x = 0.0
        self.pan_y = 0.0
        res = self._project(s.position)
        if res is not None:
            pt, _ = res
            self.pan_x += self.width() * 0.5 - pt.x()
            self.pan_y += self.height() * 0.5 - pt.y()
        self.selected_sid = int(sid)
        self.mark_dirty()

    # ==========================================================================
    # 六、查询
    # ==========================================================================

    def visibility_stats(self) -> Dict[str, int]:
        return dict(self._visible_stats)

    def frame_ms(self) -> float:
        return self._frame_ms


__all__ = ["FictionView", "MAP_MODES"]
