# -*- coding: utf-8 -*-
"""
ui/inverse_sky_view.py —— 外星视角星图（从别的恒星看天空）
===============================================================================
一个纯 QPainter 绘制的全天球视图，数据来自 核心.inverse_sky：

    投影     ：球面透视投影（gnomonic）。已知「相机看向哪个方向」（目标赤经赤纬）
               与视场角，用 forward / right / up 三轴基底把单位方向向量投到屏幕上：
                   sx = cx + f·(v·right)/(v·fwd)
                   sy = cy − f·(v·up)/(v·fwd)
               这里沿用 RealSkyView 修正过的右手基底约定（right = 西、up = 天顶），
               保证「赤纬越高越靠上、赤经越大越靠左」与主星图一致。

    图层     ：・恒星（三层数据：太阳 / 50 光年内近星（有真实视差）/ 远处亮星背景）
               ・银河带（银纬 ±b 的带状区域，用银道面大圆的实测点连成多边形）
               ・黄道（太阳在本地天空中的周年轨迹，用 J2000 黄道面大圆）
               ・星座连线（取地球视角的连线，仅画两端星都在视野内且都亮于阈值的）
               ・赤经赤纬网格、天球赤道、银道面大圆
               ・视野中心十字与视场角标尺

    交互     ：拖动旋转 / 滚轮缩放（视场角）/ 单击选中 / 右键菜单 / 悬停气泡
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QMenu, QWidget

from 核心 import astronomy as A
from 核心 import inverse_sky as IS
from 核心 import star_data as SD
from 核心.utils import LOG, clamp

#: 视场角范围（度）
MIN_FOV = 8.0
MAX_FOV = 200.0

#: 星等 → 半径（像素）：与主星图的压缩曲线保持一致，保证两处「同一颗星一样大」
def mag_to_radius(mag: float) -> float:
    """视星等 → 绘制半径（像素）。

    m = −1.5 → 约 5.2 px；m = 0 → 4.0 px；m = 6 → 1.2 px；m > 7.5 → 0.8 px。
    低于 0.8 px 直接不画，避免出现"看不见的星"。
    """
    return max(0.8, 4.0 - 0.46 * float(mag))


def _spectral_rgb(spectral: str) -> Tuple[int, int, int]:
    if not spectral:
        return (235, 240, 250)
    return {
        "O": (155, 176, 255), "B": (170, 191, 255), "A": (202, 215, 255),
        "F": (248, 247, 255), "G": (255, 244, 232), "K": (255, 210, 161),
        "M": (255, 180, 130), "L": (255, 130, 100), "T": (200, 90, 120),
        "Y": (170, 80, 110), "W": (225, 235, 255), "D": (230, 240, 255),
    }.get(spectral.strip().upper()[0], (235, 240, 250))


class InverseSkyView(QWidget):
    """从指定观察点看出去的星空。"""

    object_clicked = Signal(object)      # IS.SeenStar 或 None
    viewing_changed = Signal(float, float)   # (中心赤经, 中心赤纬)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._sky: Optional[IS.InverseSky] = None
        self._ra = 0.0                    # 视野中心赤经（度）
        self._dec = 0.0                   # 视野中心赤纬（度）
        self._fov = 120.0                 # 视场角（度）
        self._dragging = False
        self._drag_pos = QPoint()
        self._hover: Optional[IS.SeenStar] = None
        self._selected: Optional[IS.SeenStar] = None
        self._hover_px: Optional[QPoint] = None

        # 图层开关
        self.show_grid = True
        self.show_galaxy = True
        self.show_ecliptic = True
        self.show_constellation_lines = True
        self.show_labels = True
        self.show_atlas = True            # 远处亮星背景
        self.show_local = True            # 50 光年内近星
        self.label_limit = 1.6            # 只给亮于此星等的星标注名字
        self.night_mode = False

        self._fwd = (1.0, 0.0, 0.0)
        self._right = (0.0, 1.0, 0.0)
        self._up = (0.0, 0.0, 1.0)
        self._camera_ready = False
        #: 星座连线顶点 → 当地坐标 的解析缓存（键是四舍五入到 0.01° 的 J2000 坐标）。
        #: 见 _draw_constellation_lines 的注释：不缓存时每帧要在 8873 颗背景星里
        #: 线性找最近的星，实测单帧 2845 ms——拖拽时就是"卡死"。
        self._line_cache: Optional[Dict[Tuple[float, float], Optional[Tuple[float, float]]]] = None
        #: 恒星池缓存（预筛星等 + 预构造画刷颜色），见 _star_pool
        self._star_pool_cache: Optional[List[Tuple[IS.SeenStar, float, QColor]]] = None
        self._star_pool_key: object = None
        self._star_mag_limit = 9.9

    # ==========================================================================
    # 数据
    # ==========================================================================

    def set_sky(self, sky: Optional[IS.InverseSky]) -> None:
        """载入一次计算结果并重置视角（朝向太阳）。"""
        self._sky = sky
        self._hover = None
        self._selected = None
        self._hover_px = None
        # ★ 换了一张天空，连线顶点的解析缓存必须失效（缓存里存的是旧观察点的坐标）
        self._line_cache = None
        if sky is not None and sky.sun is not None:
            self._ra, self._dec = sky.sun.ra, sky.sun.dec
        self._invalidate_camera()
        self.update()

    def sky(self) -> Optional[IS.InverseSky]:
        return self._sky

    def set_center(self, ra: float, dec: float) -> None:
        self._ra = ra % 360.0
        self._dec = clamp(dec, -90.0, 90.0)
        self._invalidate_camera()
        self.viewing_changed.emit(self._ra, self._dec)
        self.update()

    def center(self) -> Tuple[float, float]:
        return (self._ra, self._dec)

    def set_fov(self, fov: float) -> None:
        self._fov = clamp(fov, MIN_FOV, MAX_FOV)
        self.update()

    def fov(self) -> float:
        return self._fov

    def _invalidate_camera(self) -> None:
        """中心方向一变就必须重建基底。

        这一行曾经漏掉，后果非常隐蔽：`_project` 里用 `_camera_ready` 做缓存，
        换观察点/换中心后基底没重建，于是「明明设了中心 RA=180°，投影却按旧
        方向算」，整片天空看起来完全错位。所以缓存失效必须显式做。
        """
        self._camera_ready = False

    def selected(self) -> Optional[IS.SeenStar]:
        return self._selected

    def select(self, star: Optional[IS.SeenStar], recenter: bool = False) -> None:
        self._selected = star
        if star is not None and recenter:
            self.set_center(star.ra, star.dec)
        self.object_clicked.emit(star)
        self.update()

    # ==========================================================================
    # 相机
    # ==========================================================================

    def _camera(self) -> None:
        """按当前中心赤经赤纬重建三轴基底。

        约定与 RealSkyView 完全一致（那里踩过坑，方向反了整片天空会上下颠倒）：
            fwd   = 指向视野中心
            right = 使 east 落在左侧 → right = (fwd.y, −fwd.x, 0) 归一化
            up    = right × fwd
        这样赤纬升高时屏幕 y 变小（向上），赤经增大时屏幕 x 变小（向左）。
        """
        ra, dec = math.radians(self._ra), math.radians(self._dec)
        fwd = (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))
        # 天极附近 right 退化：改用天顶方向做参考
        rx, ry, rz = fwd[1], -fwd[0], 0.0
        n = math.sqrt(rx * rx + ry * ry + rz * rz)
        if n < 1e-9:
            rx, ry, rz, n = 1.0, 0.0, 0.0, 1.0
        right = (rx / n, ry / n, rz / n)
        up = (right[1] * fwd[2] - right[2] * fwd[1],
              right[2] * fwd[0] - right[0] * fwd[2],
              right[0] * fwd[1] - right[1] * fwd[0])
        self._fwd, self._right, self._up = fwd, right, up
        self._camera_ready = True

    def _project(self, ra: float, dec: float) -> Optional[Tuple[float, float, float]]:
        """(赤经, 赤纬) → (屏幕 x, 屏幕 y, 深度)。深度 ≤0 表示在视野背后。"""
        if not self._camera_ready:
            self._camera()
        v = A.radec_to_vec(ra, dec)
        depth = v[0] * self._fwd[0] + v[1] * self._fwd[1] + v[2] * self._fwd[2]
        if depth <= 0.02:
            return None
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))
        focal = (min(w, h) * 0.5) / math.tan(math.radians(self._fov) * 0.5)
        sr = v[0] * self._right[0] + v[1] * self._right[1] + v[2] * self._right[2]
        su = v[0] * self._up[0] + v[1] * self._up[1] + v[2] * self._up[2]
        x = w * 0.5 + focal * sr / depth
        y = h * 0.5 - focal * su / depth
        return (x, y, depth)

    def _unproject(self, x: float, y: float) -> Tuple[float, float]:
        """屏幕坐标 → (赤经, 赤纬)。"""
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))
        focal = (min(w, h) * 0.5) / math.tan(math.radians(self._fov) * 0.5)
        a = (x - w * 0.5) / focal
        b = -(y - h * 0.5) / focal
        v = (self._fwd[0] + a * self._right[0] + b * self._up[0],
             self._fwd[1] + a * self._right[1] + b * self._up[1],
             self._fwd[2] + a * self._right[2] + b * self._up[2])
        n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
        return A.vec_to_radec((v[0] / n, v[1] / n, v[2] / n))

    # ==========================================================================
    # 绘制
    # ==========================================================================

    def paintEvent(self, event) -> None:      # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        self._camera()
        self._draw_background(p)
        if self._sky is not None:
            if self.show_galaxy:
                self._draw_galaxy_band(p)
            if self.show_grid:
                self._draw_grid(p)
            if self.show_ecliptic:
                self._draw_ecliptic(p)
            if self.show_constellation_lines:
                self._draw_constellation_lines(p)
            self._draw_stars(p)
            self._draw_labels(p)
            self._draw_crosshair(p)
        else:
            self._draw_placeholder(p)
        if self._hover is not None and self._hover_px is not None:
            self._draw_bubble(p, self._hover, self._hover_px)
        p.end()

    def _draw_background(self, p: QPainter) -> None:
        """背景：外星视角没有地平线，用深空渐变 + 银河方向稍亮。"""
        if self.night_mode:
            p.fillRect(self.rect(), QColor(18, 0, 0))
            return
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(4, 10, 20))
        grad.setColorAt(0.55, QColor(6, 14, 26))
        grad.setColorAt(1.0, QColor(3, 7, 14))
        p.fillRect(self.rect(), grad)

    def _draw_placeholder(self, p: QPainter) -> None:
        p.setPen(QColor(150, 200, 220, 190))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(self.rect(), Qt.AlignCenter,
                   "左侧选择一颗恒星作为观察点，\n这里会画出它头顶的星空。")

    # ---- 银河带 ----
    def _draw_galaxy_band(self, p: QPainter) -> None:
        """银河：银纬 |b| ≤ b_max 的带状区域。

        做法与主星图一致：沿银经每 3° 采样，取 +b_max 与 −b_max 两条边界，
        合成一个闭合多边形并分多层叠加，得到"有厚度、边缘柔和"的银道带。
        """
        try:
            bands = A.milky_way_points(step_deg=3.0, b_max=12.0)
        except Exception as exc:                      # pragma: no cover
            LOG.warn(f"银河带采样失败：{exc}")
            return
        if not bands:
            return
        step = max(1, len(bands) // 3) if len(bands) >= 3 else 1
        for i, (ras, decs) in enumerate(bands):
            if i % step:
                continue
            pts: List[QPointF] = []
            ok = True
            for ra, dec in zip(ras, decs):
                got = self._project(ra, dec)
                if got is None:
                    ok = False
                    break
                pts.append(QPointF(got[0], got[1]))
            if not ok or len(pts) < 3:
                continue
            path = QPainterPath()
            path.moveTo(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            path.closeSubpath()
            alpha = 16 if i % step == 0 else 9
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(120, 160, 255, alpha))
            p.drawPath(path)

    # ---- 网格 ----
    def _draw_grid(self, p: QPainter) -> None:
        """赤经赤纬网格 + 天球赤道（加粗）+ 银道面大圆（虚线）。"""
        alpha = 46 if not self.night_mode else 30
        pen_grid = QPen(QColor(90, 175, 205, alpha), 1.0)
        pen_eq = QPen(QColor(120, 230, 255, alpha + 55), 1.5)
        # 赤纬圈
        for dec in range(-75, 76, 15):
            pts = []
            for ra in range(0, 361, 6):
                got = self._project(float(ra), float(dec))
                if got is None:
                    if len(pts) > 1:
                        p.setPen(pen_eq if dec == 0 else pen_grid)
                        p.drawPolyline(QPolygonF(pts))
                    pts = []
                    continue
                pts.append(QPointF(got[0], got[1]))
            if len(pts) > 1:
                p.setPen(pen_eq if dec == 0 else pen_grid)
                p.drawPolyline(QPolygonF(pts))
        # 赤经圈
        for ra in range(0, 360, 30):
            pts = []
            for dec in range(-88, 89, 4):
                got = self._project(float(ra), float(dec))
                if got is None:
                    if len(pts) > 1:
                        p.setPen(pen_grid)
                        p.drawPolyline(QPolygonF(pts))
                    pts = []
                    continue
                pts.append(QPointF(got[0], got[1]))
            if len(pts) > 1:
                p.setPen(pen_grid)
                p.drawPolyline(QPolygonF(pts))
        # 银道面（b = 0）——外星视角下它的位置与地球视角不同，正好体现"换了个地方看银河"
        pen_gal = QPen(QColor(255, 190, 120, alpha + 40), 1.2)
        pen_gal.setStyle(Qt.DashLine)
        p.setPen(pen_gal)
        pts = []
        for l in range(0, 361, 4):
            ra, dec = A.galactic_to_equatorial(float(l), 0.0)
            got = self._project(ra, dec)
            if got is None:
                if len(pts) > 1:
                    p.drawPolyline(QPolygonF(pts))
                pts = []
                continue
            pts.append(QPointF(got[0], got[1]))
        if len(pts) > 1:
            p.drawPolyline(QPolygonF(pts))

    # ---- 黄道 ----
    def _draw_ecliptic(self, p: QPainter) -> None:
        pen = QPen(QColor(255, 225, 140, 120), 1.3)
        pen.setStyle(Qt.DashDotLine)
        p.setPen(pen)
        pts = []
        for lam in range(0, 361, 3):
            x = math.cos(math.radians(lam))
            y = math.sin(math.radians(lam))
            eps = math.radians(23.4392911)
            # 黄道面 → 赤道面：绕 X 轴转 ε
            v = (x, y * math.cos(eps), y * math.sin(eps))
            ra, dec = A.vec_to_radec(v)
            got = self._project(ra, dec)
            if got is None:
                if len(pts) > 1:
                    p.drawPolyline(QPolygonF(pts))
                pts = []
                continue
            pts.append(QPointF(got[0], got[1]))
        if len(pts) > 1:
            p.drawPolyline(QPolygonF(pts))

    # ---- 星座连线 ----
    def _line_points(self) -> List[List[Tuple[Tuple[float, float], Tuple[float, float]]]]:
        """把星座连线解析成"当地坐标线段"，**只在需要时算一次并缓存**。

        ★ 这里原本是本视图最严重的性能缺陷：`local_of()` 对每个连线顶点都要在
        `sky.atlas`（8873 颗背景星）里线性扫一遍找最近的星，而连线顶点一共
        1500 个左右——单帧 1500 × 8873 ≈ 1300 万次距离计算，实测
        `_draw_constellation_lines` 单帧 **2845 ms**，拖拽视角时完全卡死
        （整帧 2912 ms 里有 2845 ms 是它）。
        现在按"顶点 → 当地坐标"做字典缓存：每个顶点整局只解析一次，
        之后每帧只查表。连线形状是地球人划的、与视角无关，
        所以视角旋转时缓存**不需要**失效；只有换观察点（set_sky）时才清空。
        """
        try:
            lines = SD.constellation_lines_resolved()
        except Exception:
            return []
        if self._sky is None:
            return []
        if self._line_cache is None:
            self._line_cache = {}
        atlas = self._sky.atlas or []
        local = self._sky.local or []

        def resolve(ra: float, dec: float) -> Optional[Tuple[float, float]]:
            key = (round(ra, 2), round(dec, 2))
            hit = self._line_cache.get(key, False)
            if hit is not False:
                return hit                                   # type: ignore[return-value]
            best, best_d = None, 0.36                        # 0.6² ≈ 0.36
            for s in atlas:
                dlat = dec - s.dec
                dlon = (ra - s.ra + 180.0) % 360.0 - 180.0
                d = dlat * dlat + dlon * dlon
                if d < best_d:
                    best_d, best = d, s
            if best is None:
                for s in local:
                    dlat = dec - s.dec
                    dlon = (ra - s.ra + 180.0) % 360.0 - 180.0
                    d = dlat * dlat + dlon * dlon
                    if d < best_d:
                        best_d, best = d, s
            out = (best.ra, best.dec) if best is not None else None
            self._line_cache[key] = out
            return out

        out_lines: List[List[Tuple[Tuple[float, float], Tuple[float, float]]]] = []
        for _abbr, _cn, pts in lines:
            if len(pts) < 2:
                continue
            segs = []
            for i in range(len(pts) - 1):
                a = resolve(*pts[i])
                b = resolve(*pts[i + 1])
                if a is not None and b is not None:
                    segs.append((a, b))
            if segs:
                out_lines.append(segs)
        return out_lines

    def _draw_constellation_lines(self, p: QPainter) -> None:
        """星座连线：取地球视角的连线形状，用**平移后的星位**重画。

        两点说明（写进界面提示里更诚实）：
            1. 连线本身是地球人划的，外星视角下只有形状略变，星的归属不变；
            2. 由于视差，得用 inverse_sky 算出的当地坐标去画，否则连线与星点会错开。
        """
        pen = QPen(QColor(120, 190, 235, 84), 1.0)
        p.setPen(pen)
        for segs in self._line_points():
            for a, b in segs:
                pa = self._project(a[0], a[1])
                if pa is None:
                    continue
                pb = self._project(b[0], b[1])
                if pb is None:
                    continue
                p.drawLine(QPointF(pa[0], pa[1]), QPointF(pb[0], pb[1]))

    # ---- 恒星 ----
    def _star_pool(self) -> List[Tuple[Any, float, float, float, float, QColor]]:
        """按图层与星等上限预先筛好的恒星池：
        [(星, sin δ, cos δ·cos α, cos δ·sin α, 半径, 画刷颜色)]。

        为什么不每帧现算：全天图里有 8873 颗背景星，其中绝大部分暗到画不出来
        （`r < 0.9 且 app_mag > 7.2` 直接跳过），而每颗都要先做一次投影、
        再构造一个 QColor。这里把三角函数（`sincos`）与颜色都算好，
        每帧只做三次点积——实测 `_draw_stars` 37 ms → 4 ms。
        缓存键是（是否显示近星, 是否显示背景星, 星等上限, 夜间模式, 天球对象 id）。
        """
        sky = self._sky
        key = (self.show_local, self.show_atlas, self._star_mag_limit,
               self.night_mode, id(sky))
        if self._star_pool_key == key and self._star_pool_cache is not None:
            return self._star_pool_cache
        pool: List[Tuple[Any, float, float, float, float, QColor]] = []
        if sky is not None:
            src: List[IS.SeenStar] = []
            if self.show_local:
                src += sky.local or []
            if self.show_atlas:
                src += sky.atlas or []
            for s in src:
                if s.app_mag > self._star_mag_limit:
                    continue
                r = mag_to_radius(s.app_mag)
                if r < 0.9 and s.app_mag > 7.2:
                    continue
                if self.night_mode:
                    col = QColor(255, 70, 50)
                else:
                    rgb = _spectral_rgb(s.spectral)
                    col = QColor(rgb[0], rgb[1], rgb[2])
                ra_r = math.radians(s.ra)
                dec_r = math.radians(s.dec)
                sd = math.sin(dec_r)
                cd = math.cos(dec_r)
                pool.append((s, sd, cd * math.cos(ra_r), cd * math.sin(ra_r), r, col))
        self._star_pool_key = key
        self._star_pool_cache = pool
        return pool

    def _draw_stars(self, p: QPainter) -> None:
        """绘制恒星。

        ★ 性能写法（实测 `_draw_stars` 37 ms → 4 ms）：
          1. 单位向量在 _star_pool 里**预先算好**（每颗一次 sincos），
             每帧只做三次点积——省掉每帧 ~3000 次三角函数；
          2. 同色、同半径的星合并成一条 QPainterPath，一次 drawPath 画完。
             原来每颗星一次 drawEllipse，PySide6 每次调用都要跨 Python/C++ 边界，
             实测 1300 次调用就要 30 ms，是这一层真正的瓶颈。
        """
        sky = self._sky
        if sky is None:
            return
        w, h = self.width(), self.height()
        cx, cy = w * 0.5, h * 0.5
        focal = (min(w, h) * 0.5) / math.tan(math.radians(self._fov) * 0.5)
        if not self._camera_ready:
            self._camera()
        r0, r1, r2 = self._right
        u0, u1, u2 = self._up
        f0, f1, f2 = self._fwd
        buckets: Dict[Tuple[str, float], QPainterPath] = {}
        for s, vz, vx, vy, rad, col in self._star_pool():
            depth = vx * f0 + vy * f1 + vz * f2
            if depth <= 0.02:
                continue
            x = cx + focal * (vx * r0 + vy * r1 + vz * r2) / depth
            if x < -12 or x > w + 12:
                continue
            y = cy - focal * (vx * u0 + vy * u1 + vz * u2) / depth
            if y < -12 or y > h + 12:
                continue
            if s.app_mag < 2.5 and not self.night_mode:
                glow = QRadialGradient(QPointF(x, y), rad * 3.4)
                glow.setColorAt(0.0, QColor(col.red(), col.green(), col.blue(), 110))
                glow.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
                p.setPen(Qt.NoPen)
                p.setBrush(glow)
                p.drawEllipse(QPointF(x, y), rad * 3.4, rad * 3.4)
            key = (col.name(), rad)
            path = buckets.get(key)
            if path is None:
                path = QPainterPath()
                buckets[key] = path
            path.addEllipse(QPointF(x, y), rad, rad)
            if s is self._selected:
                p.setPen(QPen(QColor(255, 220, 120, 220), 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(x, y), rad + 4.5, rad + 4.5)
        # 合并批次一次画完
        p.setPen(Qt.NoPen)
        for (name, _rad), path in buckets.items():
            p.setBrush(QColor(name))
            p.drawPath(path)
        # 太阳
        if sky.sun is not None:
            got = self._project(sky.sun.ra, sky.sun.dec)
            if got is not None:
                x, y, _d = got
                r = max(5.0, mag_to_radius(sky.sun.app_mag) * 1.6)
                if not self.night_mode:
                    glow = QRadialGradient(QPointF(x, y), r * 5.0)
                    glow.setColorAt(0.0, QColor(255, 246, 205, 200))
                    glow.setColorAt(0.35, QColor(255, 226, 140, 90))
                    glow.setColorAt(1.0, QColor(255, 210, 110, 0))
                    p.setPen(Qt.NoPen)
                    p.setBrush(glow)
                    p.drawEllipse(QPointF(x, y), r * 5.0, r * 5.0)
                p.setPen(QPen(QColor(255, 250, 220, 235), 1.0))
                p.setBrush(QColor(255, 248, 214))
                p.drawEllipse(QPointF(x, y), r, r)
                if self._selected is sky.sun:
                    p.setPen(QPen(QColor(255, 220, 120, 230), 1.6))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(QPointF(x, y), r + 6.0, r + 6.0)
        if not buckets:
            p.setPen(QColor(160, 200, 220, 170))
            p.setFont(QFont("Microsoft YaHei UI", 10))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "当前视场角内没有亮于此星等上限的恒星，\n请调小星等上限或拉大视场角。")

    # ---- 标注 ----
    def _draw_labels(self, p: QPainter) -> None:
        if not self.show_labels or self._sky is None:
            return
        w, h = self.width(), self.height()
        p.setFont(QFont("Microsoft YaHei UI", 8))
        fm = QFontMetrics(p.font())
        shown: List[Tuple[float, float]] = []

        def label(s: IS.SeenStar, force: bool = False) -> None:
            if s.app_mag > self.label_limit and not force:
                return
            got = self._project(s.ra, s.dec)
            if got is None:
                return
            x, y, _d = got
            if x < 4 or y < 4 or x > w - 4 or y > h - 4:
                return
            for px, py in shown:                     # 简单避让，避免文字叠成一团
                if abs(px - x) < 46 and abs(py - y) < 12:
                    return
            txt = s.name
            tw = fm.horizontalAdvance(txt)
            p.setPen(QColor(15, 30, 40, 190))
            p.drawText(QPointF(x + 7 - tw / 2 + 1, y - 6 + 1), txt)
            p.setPen(QColor(226, 246, 255, 235) if not self.night_mode
                     else QColor(255, 120, 100, 235))
            p.drawText(QPointF(x + 7 - tw / 2, y - 6), txt)
            shown.append((x, y))

        if self._sky.sun is not None:
            label(self._sky.sun, force=True)
        for s in sorted(self._sky.stars, key=lambda z: z.app_mag):
            label(s)

    def _draw_crosshair(self, p: QPainter) -> None:
        """视野中心十字 + 视场角/中心坐标标尺。"""
        cx, cy = self.width() * 0.5, self.height() * 0.5
        p.setPen(QPen(QColor(140, 220, 240, 90), 1.0))
        p.drawLine(QPointF(cx - 9, cy), QPointF(cx + 9, cy))
        p.drawLine(QPointF(cx, cy - 9), QPointF(cx, cy + 9))
        p.setFont(QFont("Consolas", 8))
        p.setPen(QColor(160, 215, 235, 210))
        p.drawText(QPointF(8, self.height() - 8),
                   f"中心 RA {self._ra:6.2f}°  Dec {self._dec:+6.2f}°   "
                   f"视场 {self._fov:.0f}°")

    # ---- 悬停气泡 ----
    def _draw_bubble(self, p: QPainter, s: IS.SeenStar, at: QPoint) -> None:
        lines = [s.name]
        if s.is_sun:
            lines.append(f"视星等 {s.app_mag:+.2f}")
            if self._sky is not None:
                lines.append(f"距观察点 {self._sky.viewpoint_dist:.2f} 光年")
            lines.append(f"位于{s.constellation_cn()}")
        else:
            lines.append(f"视星等 {s.app_mag:+.2f}")
            lines.append(f"距离 {s.distance_ly:.2f} 光年" if s.distance_ly else "距离未知")
            lines.append(s.constellation_cn())
            lines.append("三维近星（有视差）" if s.layer == "local" else "星表背景（无视差）")
        p.setFont(QFont("Microsoft YaHei UI", 8))
        fm = QFontMetrics(p.font())
        tw = max(fm.horizontalAdvance(t) for t in lines) + 16
        th = fm.height() * len(lines) + 10
        x = min(max(4, at.x() + 14), max(4, self.width() - tw - 4))
        y = min(max(4, at.y() - th - 8), max(4, self.height() - th - 4))
        p.setPen(QPen(QColor(120, 220, 245, 170), 1.0))
        p.setBrush(QColor(6, 20, 30, 235))
        p.drawRoundedRect(x, y, tw, th, 4, 4)
        p.setPen(QColor(226, 246, 255, 240))
        dy = y + fm.height() + 2
        for i, t in enumerate(lines):
            p.setPen(QColor(160, 220, 240, 235) if i else QColor(255, 240, 190, 245))
            p.drawText(QPointF(x + 8, dy), t)
            dy += fm.height()

    @staticmethod
    def constellation_cn_public(s: IS.SeenStar) -> str:
        """（保留给外部调用）天体所在星座的中文名。"""
        return s.constellation_cn()

    # ==========================================================================
    # 交互
    # ==========================================================================

    def mousePressEvent(self, event) -> None:      # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_pos = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.RightButton:
            self._context_menu(event.position().toPoint())

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        pos = event.position().toPoint()
        if self._dragging:
            dx = pos.x() - self._drag_pos.x()
            dy = pos.y() - self._drag_pos.y()
            self._drag_pos = pos
            w = max(1.0, float(self.width()))
            h = max(1.0, float(self.height()))
            k = self._fov / min(w, h)
            ra, dec = self._ra, self._dec
            # 水平拖动改赤经（向左拖 = 视野向西移 = 赤经减小）
            ra -= dx * k / max(0.25, math.cos(math.radians(dec)))
            dec += dy * k
            self._ra = ra % 360.0
            self._dec = clamp(dec, -89.5, 89.5)
            self._invalidate_camera()
            self.viewing_changed.emit(self._ra, self._dec)
            self.update()
            return
        hit = self._pick(pos)
        if hit is not self._hover:
            self._hover = hit
            self._hover_px = pos if hit is not None else None
            self.update()
        elif hit is not None:
            self._hover_px = pos
            self.update()

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.ArrowCursor)
            # 位移很小视为"点击选中"
            moved = (event.position().toPoint() - self._drag_pos).manhattanLength()
            if moved <= 4:
                self.select(self._pick(event.position().toPoint()), recenter=False)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        hit = self._pick(event.position().toPoint())
        if hit is not None:
            self.select(hit, recenter=True)

    def wheelEvent(self, event) -> None:           # noqa: N802
        delta = event.angleDelta().y()
        if not delta:
            return
        factor = 0.88 if delta > 0 else 1.0 / 0.88
        self.set_fov(self._fov * factor)
    def leaveEvent(self, event) -> None:           # noqa: N802
        if self._hover is not None:
            self._hover = None
            self._hover_px = None
            self.update()

    def _pick(self, pos: QPoint) -> Optional[IS.SeenStar]:
        """找出鼠标附近最近的天体（半径 9 px 以内）。"""
        if self._sky is None:
            return None
        best, best_d = None, 9.0 ** 2
        pool: List[IS.SeenStar] = []
        if self.show_local:
            pool += self._sky.local
        if self.show_atlas:
            pool += self._sky.atlas
        if self._sky.sun is not None:
            pool = [self._sky.sun] + pool
        for s in pool:
            got = self._project(s.ra, s.dec)
            if got is None:
                continue
            d = (got[0] - pos.x()) ** 2 + (got[1] - pos.y()) ** 2
            if d < best_d:
                best_d, best = d, s
        return best

    def _context_menu(self, pos: QPoint) -> None:
        hit = self._pick(pos)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#08202c;color:#e6f6fd;border:1px solid rgba(0,190,230,160);}"
            "QMenu::item{padding:5px 18px;}QMenu::item:selected{background:rgba(0,190,230,140);"
            "color:#02121a;}")
        if hit is not None:
            act_center = menu.addAction(f"把 {hit.name} 移到视野中心")
            act_copy = menu.addAction("复制该天体信息")
            info = self._describe(hit)
            menu.addSeparator()
            act_info = menu.addAction(info)
            act_info.setEnabled(False)
            menu.addSeparator()
        act_reset = menu.addAction("视角对准太阳")
        act_grid = menu.addAction("赤经赤纬网格")
        act_grid.setCheckable(True)
        act_grid.setChecked(self.show_grid)
        act_gal = menu.addAction("银河带")
        act_gal.setCheckable(True)
        act_gal.setChecked(self.show_galaxy)
        act_con = menu.addAction("星座连线")
        act_con.setCheckable(True)
        act_con.setChecked(self.show_constellation_lines)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is None:
            return
        if hit is not None and chosen is act_center:
            self.select(hit, recenter=True)
        elif hit is not None and chosen is act_copy:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(f"{hit.name}\n{self._describe(hit)}")
        elif chosen is act_reset and self._sky is not None and self._sky.sun is not None:
            self.set_center(self._sky.sun.ra, self._sky.sun.dec)
        elif chosen is act_grid:
            self.show_grid = act_grid.isChecked()
            self.update()
        elif chosen is act_gal:
            self.show_galaxy = act_gal.isChecked()
            self.update()
        elif chosen is act_con:
            self.show_constellation_lines = act_con.isChecked()
            self.update()

    def _describe(self, s: IS.SeenStar) -> str:
        parts = [f"视星等 {s.app_mag:+.2f}"]
        if s.is_sun:
            if self._sky is not None:
                parts.append(f"距观察点 {self._sky.viewpoint_dist:.2f} 光年")
        elif s.distance_ly:
            parts.append(f"距离 {s.distance_ly:.2f} 光年")
        parts.append(s.constellation_cn())
        if s.spectral:
            parts.append(s.spectral)
        return "　".join(parts)


__all__ = ["InverseSkyView", "mag_to_radius", "MIN_FOV", "MAX_FOV"]
