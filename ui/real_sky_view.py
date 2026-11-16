# -*- coding: utf-8 -*-
"""
real_sky_view.py —— 现实星图视图
================================================================================
职责：把「观测者 + 时间 + 天体数据」渲染成一张可交互的 2.5D 星图。

核心设计：
    * 投影方式：球面天球 → 相机坐标系 → 透视（心射 gnomonic）投影，
      等价于「站在地面仰望天空」，视角中心可自由旋转，无极点畸变。
    * 坐标链路：赤道坐标(RA/Dec) → 地平坐标(Alt/Az) → 本地 ENU 单位向量 → 屏幕坐标
    * 渲染顺序：银河带 → 地平网格 → 星座连线 → 暗星 → 亮星 → 行星/日月 → 深空 → 标签 → HUD
    * 交互：左键拖拽旋转、右键拖拽平移、滚轮缩放、点击选中、双击居中
    * 性能：QTimer 驱动 60 FPS，向量预计算 + 简单剔除，781 颗恒星可稳定 60 帧

对外信号：
    object_selected(object)   点击/搜索选中某天体（字典，见 describe_object）
    status_message(str)       需要在状态栏显示的提示
    time_changed(datetime)    时间被用户改动
    view_changed(dict)        视角变化（方位/高度/视场），用于状态栏与信息面板
    nearby_requested(str)     请求切到「太阳邻域」并选中某颗近星
    watchlist_add(dict)       请求把天体加入观测清单
    layers_changed(str, bool) 右键菜单里切换了图层（主窗口同步复选框）
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QWidget

from 核心 import astronomy as A
from 核心 import planet_data as P
from 核心 import star_data as SD
from 核心.utils import (LOG, azimuth_to_compass, clamp, fmt_alt_az, fmt_dec, fmt_float,
                        fmt_ra, mag_to_alpha, mag_to_radius, norm360, rgb_str)
from .body_renderer import (body_pixmap, body_rotation_phase, deep_sky_style,
                            draw_deep_sky_symbol)

# ==============================================================================
# 配色
# ==============================================================================

C_HORIZON = QColor(0, 255, 215, 165)
C_GRID = QColor(0, 150, 200, 95)
C_GRID_MAJOR = QColor(0, 210, 245, 145)
C_CONST_LINE = QColor(96, 208, 248, 155)
C_CONST_LABEL = QColor(152, 230, 240, 228)
C_STAR_LABEL = QColor(246, 200, 120, 180)
C_PLANET = QColor(255, 180, 120, 240)
C_SUN = QColor(255, 240, 170, 250)
C_MOON = QColor(240, 240, 228, 245)
C_DSO = QColor(200, 180, 255, 210)
C_RED_MODE = QColor(255, 60, 40)

#: 视场角范围（度）
FOV_MIN = 12.0
FOV_MAX = 150.0

#: 触发「近星编目查询」的中文名集合（其余输入按普通天体搜索）
_CN_NEARBY_NAMES = frozenset((
    "比邻星", "毗邻星", "半人马座比邻星", "南门二", "半人马座α", "巴纳德星",
    "沃尔夫359", "拉兰代21185", "罗斯154", "罗斯248", "罗斯128", "鲁坦星",
    "卡普坦星", "范马南星", "格鲁姆布里奇1830", "克鲁格60", "斯特鲁维2398",
    "格雷泽1", "格利泽", "蒂加登星",
))


@dataclass
class _RenderStar:
    """预计算好的恒星渲染数据。"""

    key: str
    name: str
    ra: float
    dec: float
    mag: float
    rgb: Tuple[int, int, int]
    distance: Optional[float]
    spectral: str
    constellation: str
    hip: int
    base_radius: float
    alpha: float
    is_named: bool


class RealSkyView(QWidget):
    """现实星图渲染与交互视图。"""

    object_selected = Signal(dict)
    status_message = Signal(str)
    time_changed = Signal(datetime)
    view_changed = Signal(dict)
    nearby_requested = Signal(str)      # 请求切到「太阳邻域」并选中某颗近星
    watchlist_add = Signal(dict)        # 请求把天体加入观测清单
    layers_changed = Signal(str, bool)  # 右键菜单里切换了图层（主窗口同步复选框）

    DEFAULT_LAYERS: Dict[str, bool] = {
        "constellation_lines": True,
        "constellation_names": True,
        "star_names": True,
        "planets": True,
        "sun_moon": True,
        "milky_way": True,
        "horizon_grid": True,
        "azimuth_labels": True,
        "messier": True,
        "deep_sky_labels": False,
        "background_stars": True,
        # 地平线以下的天体：默认关闭以保持"实际可见天空"的清爽观感，
        # 打开后可以查看"现在看不到、几点升起"的目标（会以暗色 + ↓ 标记绘制）
        "below_horizon": False,
        # 50 光年内的近星：用暖黄色光环圈出这些"我们的近邻"
        "nearby_mark": False,
    }

    #: 发光贴图可选尺寸
    GLOW_SIZES: Tuple[int, ...] = (8, 12, 18, 26, 38, 54, 76)

    #: 银河带结构参数（改前只有一层均匀半透明带，这些是"结构化"用到的量）
    #: CONTOUR_LEVELS 从 9 提到 14：等照度轮廓的层数直接决定带内的平滑度，
    #: 9 层时能看到明显的层间台阶（用户反馈"精细度不够、太丑"）。
    #: 14 层时带心 80、层间跳变仍有 5~7 个亮度级，录屏里肉眼看得到"脏条纹"，
    #: 所以再提到 20 层（实测相邻 0.1° 采样最大跳变 17 → 7 个亮度级），
    #: 并且外层做径向渐隐（见 MILKY_OUTER_TAPER）。定标见 README 3.5。
    MILKY_CONTOUR_LEVELS = 20
    MILKY_B_MAX = 11.0                # 最外层的半宽（度）
    MILKY_STEP_DEG = 2.0              # 沿银经的采样步长（1° 太慢，3° 有可见折角）
    #: 外层渐隐：最外一层的不透明度乘 1-TAPER，最内层乘 1.0。
    #: 为什么需要：把多层半透明**同色**叠加后，带缘会留下一道"亮边"——实测
    #: 最外两层各贡献 6 个亮度级，带缘出现 15→23 的硬台阶，看起来像一条折线
    #: （用户录屏里那道"很直的斜边"）。按层号线性渐隐后，带缘是渐隐而不是切断。
    MILKY_OUTER_TAPER = 0.72
    MILKY_RGB: Tuple[int, int, int] = (176, 196, 244)   # 银河带基色（偏冷白）
    #: 暗带（Great Rift）渲染三参数——都做成类常量，方便调参与自检
    #:   SCALE  ：把 astronomy 给出的遮挡强度整体缩放
    #:   FILL_MAX：挖空填充的最大不透明度
    #:   HALF_K ：挖空半宽 = 1.0 + HALF_K × 遮挡强度
    #:
    #: 定标依据（全部由 tools/check_milky_way.py 断言，不靠肉眼）：
    #:   * rift_scale=0（即完全关掉暗带）时"核/侧翼 = 1.17"——证明等照度轮廓
    #:     本身已经是"中间亮、边缘淡"，这一层没问题；
    #:   * 加上暗带后，目标是让核落到侧翼的 0.75~0.95，即"核仍然亮、暗带可辨"。
    #:   * 用户实测反馈过的那一版是 (1.0, 130, 2.2)：核/侧翼 = 0.34，
    #:     核被压掉三分之二，看着就是"银河中间是空的"——这是那条反馈的量化症状。
    MILKY_RIFT_SCALE = 0.45
    MILKY_FILL_MAX = 130
    MILKY_RIFT_HALF_K = 2.0
    #: 暗带填充的不透明度系数：a = FILL_MAX × 系数 × **原始**遮挡强度。
    #: ★ 这里踩过一个大坑，值得写清楚：
    #:   `strengths[]` 里存的已经是 `原始强度 × MILKY_RIFT_SCALE(0.45)`，
    #:   第一版系数直接乘在这个**已经缩放过**的数组上，等于把 0.45 算了两遍，
    #:   峰值 alpha 只有预期的 45%——用户看到的"缺口不深"其实主要是这个。
    #:   现在明确写清：横向几何仍用 SCALE 缩放后的宽度，填充 alpha 用原始强度。
    #:   定标（升余弦剖面 + 4 层嵌套之后的实测，横剖面 b=±3°/±5° 与暗带中心的差）：
    #:     0.72 → 挖深 12.2、核/侧翼 0.81     0.90 → 挖深 15.6、核/侧翼 0.76
    #:     1.05 → 挖深 18.0、核/侧翼 0.73     1.20 → 挖深 20.4、核/侧翼 0.69（越界）
    #:   取 1.05：挖深 18 级（原来 11.6 级，+55%），核/侧翼 0.73 高于判据下限 0.70。
    MILKY_RIFT_FILL_K = 1.05
    #: 暗带横向**嵌套层数**：把"升余弦剖面"从一条硬边拆成 N 级台阶。
    #: 单层实心填充时，横剖面在暗带边缘实测有 11~12 个亮度级的单步跳变；
    #: 4 层后每步约 4 级（判据 ≤ 8），而暗带中心的峰值不透明度不变。
    MILKY_RIFT_BANDS = 4
    #: 暗带**淡入淡出的强度区间**（按原始遮挡强度）。低于 LO 完全不画，
    #: 高于 HI 给满，中间线性过渡。
    #: 起因：原先用"强度 < 0.12 不画"的硬判据，暗带在 l≈96 一刀切断，
    #: 而那个交界正好在银河最亮的一段里——用户看到的"银河带中间缺失"
    #: 其实是一条接缝。大裂缝在天鹅座一带本来就是逐渐消失的。
    MILKY_RIFT_FADE_LO = 0.02
    MILKY_RIFT_FADE_HI = 0.20
    #: 暗带填充色相对画布背景的暗化系数（0 = 用纯画布背景色，1 = 更暗）
    #: 背景色在屏幕中部约 (4,9,20)，银河带最暗层也约 (5,10,20)——
    #: 两者几乎一样亮，所以用纯背景色填暗带时"挖空"几乎看不出来（实测核 62 对侧翼 83，
    #: 且画面整体偏淡）。这里把填充色再压暗一点，让暗带真的成为一条暗缝。
    MILKY_RIFT_DARKEN = 0.45

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        self.observer = A.Observer(when=datetime.now())
        self.layers: Dict[str, bool] = dict(self.DEFAULT_LAYERS)
        self.red_mode = False
        self.show_fps = True
        self.show_legend = True
        self.time_speed = 60.0
        self.time_playing = False
        self._base_speed_seconds = 60.0
        self.center_az = 180.0
        self.center_alt = 35.0
        self.fov = 100.0

        self._drag_mode = 0
        self._last_pos = QPoint(0, 0)
        self._stars: List[_RenderStar] = []
        self._sky_cache: Dict[str, List[A.SkyObject]] = {}
        self._lines_cache: List[List[Tuple[float, float]]] = []
        self._milky_bands: List[Tuple[List[float], List[float]]] = []
        self._milky_center: List[Tuple[float, float]] = []
        self._last_calc = 0.0
        self._calc_interval = 0.25
        self._hover_key: Optional[str] = None
        self._highlight_key: Optional[str] = None
        self._last_screen: Dict[str, QPointF] = {}
        self._frame_count = 0
        self._frame_ms = 0.0
        self._last_status = ""
        self._glow_cache: Dict[str, QPixmap] = {}
        self._glow_color_cache: Dict[str, QColor] = {}
        self._needs_repaint = True
        self._last_minute = -1
        self._nearby_pairs: List[Tuple[str, object]] = []
        self._nearby_by_hip: Dict[int, object] = {}
        self._nearby_mark_count = 0
        self._below_count = 0
        self._below_keys: set = set()
        self._horizon_profile: object = None
        # 光害模拟：波特尔暗空等级（1 极暗 → 9 市中心），决定肉眼极限星等
        self.bortle_level = 5
        self._mag_limit = 6.0
        self._basis: Tuple[Tuple[float, float, float], ...] = ((1.0, 0.0, 0.0),
                                                              (0.0, 1.0, 0.0),
                                                              (0.0, 0.0, 1.0))
        self._focal = 300.0
        self._cx = 200.0
        self._cy = 150.0

        self._build_static_data()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()
        self.recalculate(force=True)

    # ==========================================================================
    # 一、静态数据与状态
    # ==========================================================================

    def _build_static_data(self) -> None:
        """构建不随时间变化的渲染数据：恒星颜色、星等半径、星座连线、银河带。"""
        self._stars = []
        for star in SD.ALL_SKY_STARS:
            self._stars.append(_RenderStar(
                key=f"star:{star.hip}",
                name=star.display_name(),
                ra=star.ra, dec=star.dec, mag=star.mag, rgb=_star_rgb(star),
                distance=star.distance, spectral=star.spectral,
                constellation=star.constellation, hip=star.hip,
                base_radius=mag_to_radius(star.mag),
                alpha=mag_to_alpha(star.mag),
                is_named=bool(star.name and not star.name.startswith("HIP")),
            ))
        self._lines_cache = SD.constellation_lines_resolved()
        # 银河带：取 5 条银纬圈作为"带"的骨架（中心线 + 上下各两条）
        bands = A.milky_way_points(4.0, 11.0)
        self._milky_bands = bands
        self._milky_center = list(zip(bands[0][0], bands[0][1])) if bands else []
        LOG.info(f"现实星图：载入 {len(self._stars)} 颗恒星、"
                 f"{len(self._lines_cache)} 条星座连线、银河带 "
                 f"{len(self._milky_bands)} 层")

    def set_observer(self, lat: float, lon: float, name: str = "",
                     utc_offset: float = 8.0) -> None:
        """更新观测点。"""
        self.observer = A.Observer(lat=clamp(float(lat), -89.9, 89.9),
                                   lon=norm360(float(lon) + 180.0) - 180.0,
                                   location_name=name, utc_offset=float(utc_offset),
                                   when=self.observer.when)
        self.recalculate(force=True)

    def set_time(self, when: datetime, keep_offset: bool = True) -> None:
        """设置观测时间（本地时间）。"""
        self.observer.when = when
        self._last_minute = -1
        self.recalculate(force=True)

    def now(self) -> datetime:
        return self.observer.when

    def set_layer(self, key: str, enabled: bool) -> None:
        """切换图层。"""
        if key in self.layers:
            self.layers[key] = bool(enabled)
            self.mark_dirty()

    def set_layers(self, values: Dict[str, bool]) -> None:
        for k, v in values.items():
            if k in self.layers:
                self.layers[k] = bool(v)
        self.mark_dirty()

    # ---- 光害模拟（波特尔等级） ----

    def set_bortle(self, level: int) -> None:
        """设置波特尔暗空等级（1 极暗 → 9 市中心）。

        等级越高天空越亮 → 肉眼极限星等越低 → 暗星被裁掉，
        用来模拟"我在城里能看到什么 / 搬到山上能多看到什么"。
        """
        self.bortle_level = int(clamp(level, 1, 9))
        self._update_mag_limit()
        self.mark_dirty()

    def _update_mag_limit(self) -> None:
        """按光害等级 + 当前月光计算肉眼极限星等。"""
        try:
            from 核心 import light_pollution as LP
            moon_phase, moon_alt = 0.0, -90.0
            obs = self.observer
            if obs is not None:
                moon = A.moon_position(obs.jd_ut)
                moon_phase = float(moon["phase"])
                moon_alt = obs.altitude(moon["ra"], moon["dec"])
            self._mag_limit = LP.limiting_magnitude(self.bortle_level, moon_phase,
                                                    90.0, 0.0, moon_alt)
        except Exception as exc:
            LOG.warn(f"极限星等计算失败：{exc}")
            self._mag_limit = 6.0

    def mag_limit(self) -> float:
        """当前可见的极限星等（供界面显示）。"""
        return self._mag_limit

    # ---- 地平障碍（本地星空模拟器） ----

    def set_horizon_profile(self, profile: object) -> None:
        """设置地平障碍剖面。

        设置后星图会把被地形/建筑遮挡的天区画成暗红色，并在信息卡片里标注
        「被地平障碍遮挡」，让用户一眼看出"我这里到底能看到什么"。
        """
        self._horizon_profile = profile
        self.mark_dirty()

    def _horizon_blocked(self, alt: float, az: float) -> bool:
        """给定地平坐标是否被地平障碍遮挡。"""
        prof = self._horizon_profile
        if prof is None:
            return False
        try:
            if prof.is_empty():                       # type: ignore[attr-defined]
                return False
            return bool(prof.is_blocked(az, alt))     # type: ignore[attr-defined]
        except Exception:
            return False

    def _draw_horizon_obstruction(self, p: QPainter) -> None:
        """把被地平障碍遮挡的天区画成暗红色（轮廓 + 半透明填充）。"""
        prof = self._horizon_profile
        if prof is None:
            return
        try:
            if prof.is_empty():                       # type: ignore[attr-defined]
                return
        except Exception:
            return
        path = QPainterPath()
        low_points: List[QPointF] = []
        high_points: List[QPointF] = []
        for az in range(0, 361, 3):
            top = float(prof.altitude_at(float(az)))  # type: ignore[attr-defined]
            low = self._project_point(0.0, float(az))
            high = self._project_point(max(0.0, top), float(az))
            if low is None or high is None:
                continue
            low_points.append(low)
            high_points.append(high)
        if len(low_points) < 2:
            return
        path.addPolygon(QPolygonF(low_points + list(reversed(high_points))))
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(140, 35, 22, 92))
        p.drawPath(path)
        # 轮廓线只画障碍上沿，读起来更像"山脊线"
        ridge = QPainterPath()
        ridge.moveTo(high_points[0])
        for pt in high_points[1:]:
            ridge.lineTo(pt)
        p.setPen(QPen(QColor(255, 130, 96, 200), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(ridge)
        if high_points:
            mid = high_points[len(high_points) // 2]
            p.setPen(QColor(255, 170, 140, 235))
            p.setFont(QFont("Microsoft YaHei UI", 8))
            p.drawText(QPointF(mid.x() + 6, mid.y() - 4), "地平障碍遮挡区")

    def reset_view(self) -> None:
        """重置视角：面向南方、视场 100°。"""
        self.center_az = 180.0
        self.center_alt = 35.0
        self.fov = 100.0
        self._emit_view()
        self.mark_dirty()

    def set_time_speed(self, speed_seconds_per_real_second: float,
                       playing: Optional[bool] = None) -> None:
        """设置时间推进速度（每个真实秒推进多少模拟秒）。"""
        self.time_speed = max(0.0, float(speed_seconds_per_real_second))
        self._base_speed_seconds = self.time_speed
        if playing is not None:
            self.time_playing = bool(playing)

    def recalculate(self, force: bool = False) -> None:
        """重新计算当前天空所有天体的地平坐标（按 _calc_interval 节流）。"""
        now = time.perf_counter()
        if not force and (now - self._last_calc) < self._calc_interval:
            return
        self._last_calc = now
        include_below = bool(self.layers.get("below_horizon", False))
        planet_keys = tuple(P.NAKED_EYE_PLANETS) if self.layers.get("planets", True) \
            else tuple()
        try:
            self._sky_cache = A.compute_sky(
                self.observer, SD.ALL_SKY_STARS, planets=planet_keys,
                messier=SD.DEEP_SKY_CATALOG,
                include_sun_moon=self.layers.get("sun_moon", True),
                include_below_horizon=include_below)
            # 统计地平线以下的目标数（供压暗系数与 HUD 提示使用）
            self._below_count = sum(
                1 for group in self._sky_cache.values() for o in group if o.alt < 0.0)
            # 预先把「地平线以下」的目标键集合算好，绘制时只需一次集合查找，
            # 避免每帧重新构造字典/元组（这是全图层场景的主要开销之一）
            self._below_keys = {
                o.key for group in self._sky_cache.values() for o in group if o.alt < 0.0
            }
            self._update_mag_limit()
        except Exception as exc:
            LOG.error("天空计算失败", exc)

    def _all_objects(self) -> List[A.SkyObject]:
        """当前所有天体（按地平坐标合并）。"""
        out: List[A.SkyObject] = []
        for group in self._sky_cache.values():
            out.extend(group)
        return out

    # ==========================================================================
    # 二、投影
    # ==========================================================================

    def _camera(self) -> Tuple[Tuple[float, float, float], ...]:
        """返回相机的 (right, up, forward) 三个正交基向量（本地 ENU 坐标）。

        本地 ENU：x=东、y=北、z=天顶。

        推导（必须成对正确，否则整张天球会上下/左右颠倒）：
            天顶方向 z_up = (0, 0, 1)
            forward      = 视线方向
            叉积 fwd × z_up = (fwd_y, -fwd_x, 0)
            屏幕"向右"   = -(fwd × z_up)       ← 人朝南看时，右手边是西
            屏幕"向上"   = right × forward

        曾经的真实缺陷：right 写成了 +(fwd × z_up)，朝南看时得到"东"（左右反了），
        连带 up 变成朝下后方，于是**天顶被画在屏幕底部、地平线被画在顶部**，
        整幅星图上下颠倒。
        """
        fwd = A.altaz_to_vector(self.center_alt, self.center_az)
        # 屏幕"向右" = normalize(fwd × ẑ)，ẑ=(0,0,1) 为天顶方向：
        #     fwd × ẑ = (fwd_y·1 − fwd_z·0, fwd_z·0 − fwd_x·1, 0) = (fwd_y, −fwd_x, 0)
        # 校验（朝南看，fwd = (0, −0.819, +0.574)）：
        #     fwd × ẑ = (−0.819, 0, 0) → 归一化 (−1, 0, 0) = **西**
        # 而"面朝南方站立时右手边正是西"，与观测经验一致 ✓
        # 再由 up = right × fwd 得 (0, +0.574, +0.819)：指向东北上方，即屏幕上方 ✓
        #
        # 曾经的真实缺陷：这里写成 r = −(fwd × ẑ)，朝南时得到"东"（左右反了），
        # 连带 up 变成 (0, −0.574, −0.819) 指向下后方，于是**地平线被画到屏幕上方、
        # 天顶被画到下方**，整幅星图上下颠倒。
        rx = fwd[1]
        ry = -fwd[0]
        rlen = math.sqrt(rx * rx + ry * ry)
        if rlen < 1e-9:
            right = (1.0, 0.0, 0.0)
        else:
            right = (rx / rlen, ry / rlen, 0.0)
        # up = right × forward → 保证与 right 成右手系，且指向"屏幕上方"
        ux = right[1] * fwd[2] - right[2] * fwd[1]
        uy = right[2] * fwd[0] - right[0] * fwd[2]
        uz = right[0] * fwd[1] - right[1] * fwd[0]
        return (right, (ux, uy, uz), fwd)

    def _project_point(self, alt: float, az: float) -> Optional[QPointF]:
        """把地平坐标投影到屏幕；不可见（在背后或超出视野）返回 None。"""
        v = A.altaz_to_vector(alt, az)
        return self._project_vec(v)

    def _project_vec(self, v: Sequence[float]) -> Optional[QPointF]:
        right, up, fwd = self._basis
        x = v[0] * right[0] + v[1] * right[1] + v[2] * right[2]
        y = v[0] * up[0] + v[1] * up[1] + v[2] * up[2]
        z = v[0] * fwd[0] + v[1] * fwd[1] + v[2] * fwd[2]
        if z <= 0.02:
            return None
        return QPointF(self._cx + self._focal * x / z,
                       self._cy - self._focal * y / z)

    def _prepare_frame(self) -> None:
        """每帧预计算相机基与焦距。"""
        self._basis = self._camera()
        self._focal = (max(1, self.height()) * 0.5) / math.tan(math.radians(self.fov) * 0.5)
        self._cx = self.width() * 0.5
        self._cy = self.height() * 0.5
        self._last_screen = {}

    def set_view_center(self, alt: float, az: float, animate: bool = True) -> None:
        """把视角中心移动到指定地平坐标（搜索定位用）。"""
        self.center_alt = clamp(float(alt), -88.0, 88.0)
        self.center_az = norm360(float(az))
        self._emit_view()
        self.mark_dirty()

    def look_at_radec(self, ra: float, dec: float, zoom_to: Optional[float] = None) -> None:
        """把视角对准指定赤道坐标（若在地平线下会给出提示）。"""
        alt, az = self.observer.radec_to_altaz(ra, dec)
        self.set_view_center(alt, az)
        if zoom_to is not None:
            self.fov = clamp(float(zoom_to), FOV_MIN, FOV_MAX)
            self._emit_view()
        if alt < 0:
            self.status_message.emit(
                f"目标当前在地平线下（高度 {alt:+.1f}°，方位 "
                f"{azimuth_to_compass(az)}），可打开「地平线下天体」图层查看")
        self.mark_dirty()

    def _emit_view(self) -> None:
        self.view_changed.emit({
            "center_alt": self.center_alt, "center_az": self.center_az,
            "fov": self.fov, "zoom": FOV_MAX / max(1e-6, self.fov),
        })

    def _on_tick(self) -> None:
        """60 FPS 定时器：推进时间并在需要时重绘。"""
        try:
            if self.time_playing and self.time_speed > 0:
                self.observer.when = self.observer.when + timedelta(
                    seconds=self.time_speed * 0.016)
                self._frame_count += 1
                if self._frame_count % 15 == 0:
                    self.time_changed.emit(self.observer.when)
                minute = self.observer.when.hour * 60 + self.observer.when.minute
                if minute != self._last_minute:
                    self._last_minute = minute
                    self.recalculate(force=True)
                    self._needs_repaint = True
            if self._needs_repaint and self.isVisible():
                self._needs_repaint = False
                self.update()
        except Exception as exc:      # pragma: no cover
            LOG.error("星图定时器异常", exc)

    def mark_dirty(self) -> None:
        """请求重绘（视角 / 图层 / 夜间模式等变化后调用）。"""
        self._needs_repaint = True
        self.update()

    # ---- 发光贴图缓存 ----

    def _glow_pixmap(self, rgb: Tuple[int, int, int], size: int) -> QPixmap:
        """获取（并缓存）指定颜色与尺寸的圆形发光贴图。"""
        key = f"{rgb[0]},{rgb[1]},{rgb[2]}|{size}"
        pix = self._glow_cache.get(key)
        if pix is not None:
            return pix
        color = self._glow_color_cache.get(key)
        if color is None:
            color = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
            self._glow_color_cache[key] = color
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        pp = QPainter(pix)
        pp.setRenderHint(QPainter.Antialiasing, True)
        half = size / 2.0
        grad = QRadialGradient(QPointF(half, half), half)
        grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 255))
        grad.setColorAt(0.28, QColor(color.red(), color.green(), color.blue(), 105))
        grad.setColorAt(0.62, QColor(color.red(), color.green(), color.blue(), 26))
        grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        pp.setPen(Qt.NoPen)
        pp.setBrush(QBrush(grad))
        pp.drawEllipse(QPointF(half, half), half, half)
        pp.end()
        if len(self._glow_cache) < 512:
            self._glow_cache[key] = pix
        return pix

    def _nearest_glow_size(self, want: float) -> int:
        """选择不小于 want 的最小贴图档位。"""
        for size in self.GLOW_SIZES:
            if size >= want:
                return size
        return self.GLOW_SIZES[-1]

    # ==========================================================================
    # 三、绘制
    # ==========================================================================

    def paintEvent(self, event) -> None:  # noqa: N802
        t0 = time.perf_counter()
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            self._prepare_frame()
            self._draw_background(p)
            # ★ 银河带必须在 _draw_underground **之前**画。
            #   地平线以下的压暗是用一层半透明渐变覆盖实现的（"叠加"而不是
            #   "变暗已有像素"），所以它只能压暗**先画的东西**。
            #   原先顺序是 underground → milky_way，等于地平线下的银河完全
            #   没有被压暗——用户实测反馈"地平线下的银河带太亮了"就是这个顺序。
            if self.layers.get("milky_way", True):
                self._draw_milky_way(p)
            if self.layers.get("horizon_grid", True):
                self._draw_underground(p)
                self._draw_horizon_obstruction(p)
            if self.layers.get("horizon_grid", True):
                self._draw_grid(p)
            if self.layers.get("constellation_lines", True):
                self._draw_constellation_lines(p)
            self._draw_stars(p)
            if self.layers.get("nearby_mark", False):
                self._draw_nearby_marks(p)
            if self.layers.get("planets", True):
                self._draw_planets(p)
            if self.layers.get("sun_moon", True):
                self._draw_sun_moon(p)
            if self.layers.get("messier", True):
                self._draw_messier(p)
            if self.layers.get("constellation_names", True):
                self._draw_constellation_names(p)
            if self.layers.get("azimuth_labels", True):
                self._draw_azimuth_labels(p)
            self._draw_hud(p)
        except Exception as exc:      # pragma: no cover - 防御性
            LOG.error("星图绘制异常", exc)
            import traceback
            for line in traceback.format_exc().strip().splitlines()[-6:]:
                LOG.error("TRACE", line)
            p.setPen(QColor(255, 120, 120))
            p.drawText(self.rect(), Qt.AlignCenter, f"渲染异常：{exc}")
        finally:
            p.end()
        self._frame_ms = (time.perf_counter() - t0) * 1000.0

    def _draw_background(self, p: QPainter) -> None:
        """深空背景：径向渐变 + 细微星尘噪点。"""
        w, h = self.width(), self.height()
        grad = QRadialGradient(QPointF(w * 0.5, h * 0.45), max(w, h) * 0.75)
        if self.red_mode:
            grad.setColorAt(0.0, QColor(28, 6, 4))
            grad.setColorAt(1.0, QColor(4, 1, 2))
        else:
            grad.setColorAt(0.0, QColor(8, 18, 34))
            grad.setColorAt(0.55, QColor(4, 9, 20))
            grad.setColorAt(1.0, QColor(1, 3, 8))
        p.fillRect(self.rect(), QBrush(grad))

    def _draw_underground(self, p: QPainter) -> None:
        """地平线以下区域：压暗，视觉上区分"地下"。

        实现方式（改过一次，两个坑都记在这里）：

        坑 1：这层是「盖一层半透明暗色」而不是「把已有像素变暗」，所以它只能
              压暗**先画**的东西。原先 paintEvent 的顺序是
              `_draw_underground` → `_draw_milky_way`，于是地平线下的银河
              完全没有被压暗（用户反馈"地平线下的银河带太亮了"）。
              现在银河带排在它前面。

        坑 2：原先用"把地平线各采样点连成一条折线，再补三条边围成一个多边形"
              来圈定地下区域。当视场角较大、地平线铺满整个屏幕时，这个包围盒
              会退化或自交，填充不到该填的地方。现在改成**按方位逐列填充**：
              对每一列取该方位的地平线高度，从那里一直填到画布底部。
              这样不依赖任何包围盒，任何视场角下都成立。

        不透明度取 175→215（约 3~6 倍压暗）：实测地平线上的银河带 39.6 亮度，
        地下压到 ~15，与"地下不该比天上亮"的预期一致。

        坑 3（红模式）：原先固定用 (2,6,12)/(1,3,7) 这类**深蓝**去盖，红模式下
        盖在暗红夜空上就成了一条**暗红色**带，和上方的暗蓝天空完全对不上
        （离屏冒烟测试截图里那条红带就是它）。现在改成：基色与当前模式的夜空一致
        （正常模式深蓝 #040A14、红模式 #180604），只用**不透明度**随高度加深。
        为什么不是"取上方像素颜色再乘系数"：夜空本身只有 15~20 亮度，乘系数会把
        银河一起压没——实测那版地下亮度只有 2，等于地下全黑。用固定基色 + 递增
        alpha 就能"把银河压下去、把天空留下"，实测地平线上方 57 → 地下 14~21。
        """
        w = max(1, self.width())
        h = max(1, self.height())
        # 取半个画布高度作为渐变长度：越往地下越暗，且在画布内基本走完
        horizon_ys: List[Optional[float]] = []
        n = 0
        while n <= w:
            az = self.center_az + (n - w * 0.5) / max(1.0, w) * self.fov
            pt = self._project_point(0.0, norm360(az))
            horizon_ys.append(pt.y() if pt is not None else None)
            n += 4
        if all(y is None for y in horizon_ys):
            return
        base = QColor(24, 6, 4) if self.red_mode else QColor(4, 10, 20)
        # 定标（实测，1400×860 / 1184×720 离屏）：地平线上方银河 57，
        # 地下 4° 处必须压到 0.40 以下（判据），12° 处约 0.20（看得出"越深越暗"）。
        grad = QLinearGradient(0.0, 0.0, 0.0, float(h))
        for stop, a in ((0.0, 110), (0.08, 140), (0.30, 180), (0.60, 215), (1.0, 238)):
            grad.setColorAt(stop, QColor(base.red(), base.green(), base.blue(), a))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        for i, y in enumerate(horizon_ys):
            if y is None:
                continue
            x0 = i * 4
            top = clamp(y, -2.0, float(h) + 2.0)
            if top >= h:
                continue
            p.drawRect(x0, int(top), 5, h - int(top) + 2)
        # "地平线下"提示
        p.setFont(QFont("Microsoft YaHei UI", 9))
        fm = QFontMetrics(p.font())
        for label, az in (("北·地平线下", 0.0), ("南·地平线下", 180.0)):
            pt = self._project_point(-14.0, az)
            if pt is None:
                continue
            p.setPen(C_RED_MODE if self.red_mode else QColor(90, 165, 195, 170))
            p.drawText(QPointF(pt.x() - fm.horizontalAdvance(label) / 2, pt.y()), label)

    def _draw_milky_way(self, p: QPainter) -> None:
        """绘制银河带（Milky Way）：分层光带 + 沿银经的亮度调制 + 大暗带。

        三层做法，逐层加真实感（参数化的观测事实，见 astronomy 里的
        MILKY_LONGITUDE_BRIGHTNESS / MILKY_RIFT_LANES 注释）：

          ① 9 条等照度轮廓叠出"中间亮、边缘淡"的光带，且**宽度随亮度收放**
             ——银心附近又宽又亮，反银心又窄又暗（亮度比约 3.3 倍）；
          ② 每段多边形的透明度按 milky_way_brightness(l) 调制，
             不再是全带一个固定 alpha（改前的效果是一条均匀的带子）；
          ③ 沿银经每 3° 检查 milky_way_rift_at(l, b)，把大暗带覆盖的那几段
             单独画成"天空底色"以挖空银河——这是夏季银河被劈成两半的来源。

        实现上仍是一条 QPainterPath 一次成型，不做逐段画线，避免圆点断线。
        """
        try:
            obs = self.observer
            lst, lat = obs.lst, obs.lat
            base_alpha = 105 if not self.red_mode else 72
            levels = max(1, self.MILKY_CONTOUR_LEVELS)
            contour = self._contours_cached()
            # ★ 必须先按「连续可投影段」把轮廓点切段，再沿银经把 alpha 相近的列合并成路径。
            #   原因（改过三次，三个坑都记在这里）：
            #   ① 投影失败的点若被简单跳过，路径会跨过镜头背后的缺口把两端连起来，
            #      画出一个横贯整个屏幕的巨大多边形——实测每行都有 77 个亮像素。
            #      银道面是大圆，绕一圈必有一段在背后，这个缺口一定会出现。
            #   ② 每一层的 alpha 用一个包络套到整层上，会让"包络"变成**阶梯**：
            #      叠起来在带缘留下 5~7 个亮度级的台阶，看起来是脏条纹。
            #   ③ 改成"每列一个四边形"虽然平滑，但 20 层要 146 ms/帧；而沿银经
            #      alpha 本来就缓变，按"差值 ≤ 0.5 个亮度级"合并后路径数降到每层几条。
            #
            #   alpha 用「层间递进」而不是线性 ramp：
            #   多层半透明叠加的合成是乘性的：最终透明度 T = Π(1−a_i)。
            #   要让 T 随层级线性下降，每层应提供**相同的净增不透明度**
            #   a = 1 − (1−A)^(1/levels)，而不是各自一个 a_i。
            step_alpha = 1.0 - (1.0 - clamp(base_alpha / 255.0, 0.0, 0.98)) ** (1.0 / levels)

            for level, (frac, ra_u, de_u, ra_d, de_d, rel) in enumerate(contour):
                ups: List[Optional[QPointF]] = []
                downs: List[Optional[QPointF]] = []
                for i in range(len(ra_u)):
                    au = A.equatorial_to_horizontal(ra_u[i], de_u[i], lst, lat)
                    ad = A.equatorial_to_horizontal(ra_d[i], de_d[i], lst, lat)
                    ups.append(self._project_point(au[0], au[1]))
                    downs.append(self._project_point(ad[0], ad[1]))
                # 外层渐隐：level 0 最外（乘 1-TAPER），level 最大最内（乘 1.0）
                radial = 1.0 - self.MILKY_OUTER_TAPER * (1.0 - frac)
                color = C_RED_MODE if self.red_mode else QColor(*self.MILKY_RGB)
                p.setPen(Qt.NoPen)
                for i0, i1 in self._contiguous_runs(ups, downs):
                    # ★ 沿银经把 alpha 相近的相邻列合成一条路径。
                    #   为什么：逐列画四边形（每列一个 drawPath）实测 20 层要
                    #   146 ms/帧，太慢；而沿银经 alpha 本来就是缓变的，
                    #   合并阈值取 0.5 个亮度级以内，视觉上完全等价。
                    #   合并后每层只剩 2~20 条路径，且顶点仍然逐列贴合轮廓曲率。
                    k = i0
                    while k < i1:
                        r = rel[k] if k < len(rel) else 0.6
                        mod = 0.55 + 0.45 * clamp(r, 0.0, 1.0)
                        a0 = int(clamp(step_alpha * 255.0 * mod * radial, 3, 255))
                        k2 = k + 1
                        while k2 < i1:
                            r2 = rel[k2] if k2 < len(rel) else r
                            mod2 = 0.55 + 0.45 * clamp(r2, 0.0, 1.0)
                            a1 = int(clamp(step_alpha * 255.0 * mod2 * radial, 3, 255))
                            if abs(a1 - a0) > 0.5 or ups[k2] is None or downs[k2] is None:
                                break
                            k2 += 1
                        path = QPainterPath()
                        path.moveTo(ups[k])
                        for t in range(k + 1, k2 + 1):
                            path.lineTo(ups[t])
                        for t in range(k2, k - 1, -1):
                            path.lineTo(downs[t])
                        path.closeSubpath()
                        p.setBrush(QColor(color.red(), color.green(), color.blue(), a0))
                        p.drawPath(path)
                        k = k2
            # ③ 大暗带：把银河挖空
            self._draw_milky_rift(p, lst, lat)
            # 银心方向标记
            ra, dec = A.galactic_to_equatorial(0.0, 0.0)
            alt, az = A.equatorial_to_horizontal(ra, dec, lst, lat)
            gc = self._project_point(alt, az)
            if gc is not None and alt > -5.0:
                p.setPen(QColor(230, 235, 255, 200))
                p.setFont(QFont("Microsoft YaHei UI", 9))
                p.drawText(QPointF(gc.x() + 6, gc.y() - 6), "银心方向 →")
        except Exception as exc:      # pragma: no cover
            LOG.warn(f"银河带绘制失败：{exc}")

    @staticmethod
    def _contiguous_runs(ups: List[Optional[QPointF]],
                         downs: List[Optional[QPointF]]) -> List[Tuple[int, int]]:
        """找出「上下边界都能投影」的连续下标区间。

        为什么需要它：银道面是个大圆，绕一圈必有一段落在相机背后
        （_project_point 返回 None）。若把这些点简单跳过再连成一条路径，
        路径会跨过缺口、把缺口两端直接连起来，画出横贯全屏的多边形。
        实测症状：整幅画面每行都有 ~77 个亮像素，看起来像"银河铺满全天"。
        """
        runs: List[Tuple[int, int]] = []
        start = None
        for i in range(len(ups)):
            ok = ups[i] is not None and downs[i] is not None
            if ok and start is None:
                start = i
            elif not ok and start is not None:
                if i - start >= 3:          # 少于 3 个点的段画不出形状
                    runs.append((start, i - 1))
                start = None
        if start is not None and len(ups) - start >= 3:
            runs.append((start, len(ups) - 1))
        return runs

    def _rift_center_at(self, l_deg: float) -> Tuple[float, float]:
        """暗带在该银经下的 (中心银纬, 遮挡强度)，按 1° 网格预计算后插值。

        暗带中心只随银经变化、与时间视角无关，所以第一次用到时算一次即可。
        实测：每帧现算需要 181 × 41 次 milky_way_rift_at（约 70 ms）；
        预计算后每帧只做一次字典/列表插值。
        """
        table = getattr(self, "_rift_table", None)
        if table is None:
            table = []
            for i in range(361):
                table.append(A.milky_way_rift_peak(float(i), coarse=1.5))
            self._rift_table = table
            # 预存相邻两项，省掉每帧的取模与取整
            self._rift_pairs = [(table[i][0], table[i][1],
                                 table[(i + 1) % 360][0], table[(i + 1) % 360][1])
                                for i in range(360)]
        x = l_deg % 360.0
        i0 = int(x)
        b0, s0, b1, s1 = self._rift_pairs[i0]
        t = x - i0
        return (b0 + (b1 - b0) * t, s0 + (s1 - s0) * t)

    def _contours_cached(self):
        """等照度轮廓的采样点缓存。

        轮廓只由 (层数, 半宽, 步长) 决定，与时间、视角、波特尔等级都无关，
        所以算一次就够了。实测生成一次约 8 ms——占银河图层总耗时的约 10%。
        """
        key = (self.MILKY_CONTOUR_LEVELS, self.MILKY_B_MAX, self.MILKY_STEP_DEG)
        if getattr(self, "_contours_key", None) != key:
            self._contours_key = key
            self._contours_cache = A.milky_way_contours(
                levels=key[0], b_max=key[1], step_deg=key[2])
        return self._contours_cache

    def _canvas_bg_at(self, y: int) -> QColor:
        """取画布在该屏幕高度处的背景色（银河挖空要用它，不能用固定色）。

        这里踩过一个坑：最初用固定色 #040A12（亮度 32）填充暗带，而画布的
        背景渐变在竖直方向是 15~21，银河带最亮处 92。结果暗带虽然涂上了，
        却比周围夜空还亮——肉眼上"银河被劈开"的效果完全没有，实测暗带中心
        亮度 60、两侧 47~67，看不出差异。

        正确做法：用与画布完全一致的渐变取色，这样"填掉"就是真的回到夜空。
        """
        h = max(1, self.height())
        t = clamp(y / float(h), 0.0, 1.0)
        if self.red_mode:
            return QColor(18, 0, 0)
        # 与 RealSkyView.paintEvent 里的背景渐变保持一致
        # （顶部 #040A14 → 中部 #060E1A → 底部 #03070E）
        if t < 0.55:
            k = t / 0.55
            r = int(4 + (6 - 4) * k)
            g = int(10 + (14 - 10) * k)
            b = int(20 + (26 - 20) * k)
        else:
            k = (t - 0.55) / 0.45
            r = int(6 + (3 - 6) * k)
            g = int(14 + (7 - 14) * k)
            b = int(26 + (14 - 26) * k)
        return QColor(r, g, b)

    def _draw_milky_rift(self, p: QPainter, lst: float, lat: float) -> None:
        """把大暗带覆盖的银河区段用天空底色覆盖，做出"银河被劈开"的效果。

        关键实现细节（第一版做错过）：必须把整条暗带画成**一个连续多边形**，
        沿上边界走一遍再沿下边界走回来。第一版是"每 2° 画一个独立小四边形"，
        结果相邻四边形之间留缝、而且各自的 alpha 叠加不足，
        实测暗带中心与旁边只差 6 个亮度级——等于没画。

        用背景色而不是纯黑：银河带是半透明的，纯黑会留下比夜空更暗的痕迹。
        """
        step = 2.0
        ups: List[Optional[QPointF]] = []
        downs: List[Optional[QPointF]] = []
        strengths: List[float] = []
        l = 0.0
        while l <= 360.0:
            # ★ 用预计算的暗带中心线（首次绘制时算一次，之后每帧复用）。
            #   原先每帧对每个银经做 41 次 milky_way_rift_at 粗扫，
            #   实测银河图层单帧 70 ms，其中大部分耗在这里。
            best_b, best_s = self._rift_center_at(l)
            strengths.append(best_s * self.MILKY_RIFT_SCALE)
            half = 1.0 + self.MILKY_RIFT_HALF_K * best_s
            ups.append(self._rift_point(l, best_b + half, lst, lat))
            downs.append(self._rift_point(l, best_b - half, lst, lat))
            l += step
        if len(ups) < 4:
            return

        # ★ 改法（第三版）：把暗带从"矩形核 + 硬边"改成**升余弦横向剖面**。
        #
        #   上一版：|x| <= half 的地方一律用同一个 alpha 填满，外面突然为 0——
        #   暗带因此是一条边很直的深色条带，在录屏里就是那道"很直的斜边"。
        #   现在 alpha 沿横向按 cos 剖面取值：列中心最暗、两侧平滑衰减到 0。
        #
        #   ★ 第四版：单层实心填充的"升余弦"只体现在**沿银经**的 alpha 上，
        #   横跨方向仍是一条硬边——挖深调到 17 级后，横剖面在暗带边缘实测出现
        #   11~12 个亮度级的单步跳变（"黑色缺口"的观感一半来自这条硬边）。
        #   现在改成**嵌套 4 层**：第 j 层只覆盖到 |b-cb| <= half×(1-j/4)，
        #   每层贡献 cos 剖面在那一档的增量，于是边缘被拆成 4 小级（≈4 级/步）。
        BASE = self._canvas_bg_at(int(self.height() * 0.5))
        k = clamp(1.0 - self.MILKY_RIFT_DARKEN, 0.0, 1.0)
        fill = QColor(int(BASE.red() * k), int(BASE.green() * k), int(BASE.blue() * k))
        p.setPen(Qt.NoPen)
        # 沿银经合并"强度几乎相同"的列，减少绘制次数（纯性能优化）。
        n = len(ups)
        # 每列的中心线也要留着：嵌套层要按"到中心线的距离"收边
        centers: List[Optional[QPointF]] = []
        for t in range(n):
            cb, _cs = self._rift_center_at(float(t) * step)
            centers.append(self._rift_point(float(t) * step, cb, lst, lat))
        bands = max(1, int(self.MILKY_RIFT_BANDS))
        groups = []
        i = 0
        while i < n - 1:
            j = i + 1
            while j < n - 1 and abs(strengths[j] - strengths[i]) < 0.04:
                j += 1
            groups.append((i, j))
            i = j
        groups.sort(key=lambda g: -sum(strengths[g[0]:g[1] + 1]) / max(1, g[1] - g[0] + 1))
        for i, j in groups:
            ok = all(ups[t] is not None and downs[t] is not None for t in range(i, j + 1))
            raw_s = sum(strengths[i:j + 1]) / max(1, len(strengths[i:j + 1])) / self.MILKY_RIFT_SCALE
            # ★ 淡入淡出：遮挡强度低于 FADE_LO 就完全不画，到 FADE_HI 才给满。
            #   为什么需要：原先的判据是"raw_s < 0.12 就不画"，于是暗带在
            #   l≈96（0.12 → 0.11）**一刀切断**，而那个交界正好落在银河最亮的一段里，
            #   用户看到的就是"银河带中间有一块缺失"——缺失区的边界其实是一条直线接缝。
            #   天文上大裂缝（Cygnus Rift）确实在天鹅座一带逐渐消失，
            #   所以这里也应该是渐隐，而不是截断。
            fade = 1.0 if raw_s >= self.MILKY_RIFT_FADE_HI else (
                0.0 if raw_s <= self.MILKY_RIFT_FADE_LO else
                (raw_s - self.MILKY_RIFT_FADE_LO) /
                max(1e-6, self.MILKY_RIFT_FADE_HI - self.MILKY_RIFT_FADE_LO))
            if ok and fade > 0.0 and centers[i] is not None and centers[j] is not None:
                # ★ 用**原始**强度算 alpha：strengths[] 已经乘过 MILKY_RIFT_SCALE，
                #   再乘一次等于把 0.45 算了两遍（第一版就是这么错的）。
                peak = clamp(self.MILKY_FILL_MAX * self.MILKY_RIFT_FILL_K * raw_s * fade,
                             0.0, float(self.MILKY_FILL_MAX))
                # 先把这组的上/下边界点、中心点都取出来（每层要反复用），
                # 避免在"层 × 顶点"两层循环里重复做 _lerp（实测差 4 倍耗时）。
                idx = [t for t in range(i, j + 1) if centers[t] is not None]
                if len(idx) < 2:
                    continue
                u_pts = [ups[t] for t in idx]
                d_pts = [downs[t] for t in idx]
                c_pts = [centers[t] for t in idx]
                # 每层的上下边界点只算一次，4 层共用
                edges = []
                for jb in range(bands):
                    f0 = jb / float(bands)
                    f1 = (jb + 1) / float(bands)
                    # 这一层覆盖 [f0, f1]，贡献 = cos 剖面在 f0 处的值 − 在 f1 处的值
                    w = math.cos(math.pi * 0.5 * f0) - math.cos(math.pi * 0.5 * f1)
                    edges.append((f0, int(clamp(peak * w, 0, self.MILKY_FILL_MAX))))
                for f0, a in edges:
                    if a <= 0:
                        continue
                    poly = QPainterPath()
                    poly.moveTo(self._lerp(u_pts[0], c_pts[0], f0))
                    for k in range(1, len(idx)):
                        poly.lineTo(self._lerp(u_pts[k], c_pts[k], f0))
                    for k in range(len(idx) - 1, -1, -1):
                        poly.lineTo(self._lerp(d_pts[k], c_pts[k], f0))
                    poly.closeSubpath()
                    p.setBrush(QColor(fill.red(), fill.green(), fill.blue(), a))
                    p.drawPath(poly)

    def _rift_point(self, l: float, b: float, lst: float, lat: float) -> Optional[QPointF]:
        ra, dec = A.galactic_to_equatorial(l, b)
        alt, az = A.equatorial_to_horizontal(ra, dec, lst, lat)
        return self._project_point(alt, az)

    @staticmethod
    def _lerp(p_out: Optional[QPointF], p_mid: Optional[QPointF],
              f: float) -> QPointF:
        """在"暗带边界点"与"暗带中心点"之间按比例 f 插值（f=0 边界、f=1 中心）。

        暗带的嵌套层要按"到中心线的距离"逐层收边。直接用屏幕坐标插值是近似——
        但暗带半宽只有 1~3°，在这个尺度上心射投影的偏差远小于一个像素
        （与尘埃斑用椭圆近似是同一个理由）。
        """
        if p_out is None:
            return QPointF(p_mid) if p_mid is not None else QPointF(0.0, 0.0)
        if p_mid is None:
            return QPointF(p_out)
        return QPointF(p_out.x() + (p_mid.x() - p_out.x()) * f,
                       p_out.y() + (p_mid.y() - p_out.y()) * f)

    def _draw_grid(self, p: QPainter) -> None:
        """地平网格：等高圈 + 等方位线 + 地平线。"""
        for alt in range(0, 91, 15):
            pts: List[QPointF] = []
            for az in range(0, 361, 4):
                pt = self._project_point(float(alt), float(az))
                if pt is not None:
                    pts.append(pt)
            if len(pts) < 2:
                continue
            major = (alt % 30 == 0)
            p.setPen(QPen(C_GRID_MAJOR if major else C_GRID, 1.6 if major else 0.9))
            p.drawPolyline(QPolygonF(pts))
        for az in range(0, 360, 30):
            pts2: List[QPointF] = []
            for alt in range(0, 91, 3):
                pt = self._project_point(float(alt), float(az))
                if pt is not None:
                    pts2.append(pt)
            if len(pts2) < 2:
                continue
            p.setPen(QPen(C_GRID_MAJOR if az % 90 == 0 else C_GRID, 0.8))
            p.drawPolyline(QPolygonF(pts2))
        horiz: List[QPointF] = []
        for az in range(0, 361, 2):
            pt = self._project_point(0.0, float(az))
            if pt is not None:
                horiz.append(pt)
        if len(horiz) >= 2:
            p.setPen(QPen(C_HORIZON, 1.8))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF(horiz))

    def _draw_constellation_lines(self, p: QPainter) -> None:
        """星座连线。

        `constellation_lines_resolved()` 的每项是 (缩写, 中文名, [(ra, dec), ...])。
        """
        obs = self.observer
        lst, lat = obs.lst, obs.lat
        pen = QPen(C_CONST_LINE, 1.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for item in self._lines_cache:
            if len(item) < 3:
                continue
            coords = item[2]
            pts: List[QPointF] = []
            for ra, dec in coords:
                alt, az = A.equatorial_to_horizontal(ra, dec, lst, lat)
                pt = self._project_point(alt, az)
                if pt is None:
                    if len(pts) > 1:
                        p.drawPolyline(QPolygonF(pts))
                    pts = []
                    continue
                pts.append(pt)
            if len(pts) > 1:
                p.drawPolyline(QPolygonF(pts))

    def _below_visible(self) -> bool:
        """当前是否绘制地平线以下的天体。"""
        return bool(self.layers.get("below_horizon", False))

    def _fade_for_key(self, key: str, alpha: float) -> float:
        """按「该天体是否在地平线以下」取压暗后的透明度（O(1) 集合查找）。"""
        if key not in self._below_keys:
            return alpha
        below = self._below_count
        k = 0.30 if below <= 120 else (0.42 if below <= 400 else 0.55)
        return alpha * k

    def _fade(self, alpha: float, underground: bool) -> float:
        """地平线以下的天体统一压暗，与真实可见天空区分开。

        压暗系数随"地下天体数量"自适应：地下目标少时压到 30%（明显区分），
        地下目标很多（整片天球都打开了）时放宽到 55%，否则暗得几乎看不到。
        """
        if not underground:
            return alpha
        below = self._below_count
        k = 0.30 if below <= 120 else (0.42 if below <= 400 else 0.55)
        return alpha * k

    def _layer_filter(self, objects: Sequence[A.SkyObject],
                      alt_min: float = 0.0) -> List[A.SkyObject]:
        """按「地平线以下」图层开关过滤天体。"""
        if self._below_visible():
            return list(objects)
        return [o for o in objects if o.alt >= alt_min]

    def _culled_stars(self, mag_limit: float) -> List[object]:
        """按星等上限筛出本帧要画的恒星（带缓存）。

        星表 8870 颗（含背景暗星）之后，每帧都做一遍 `star.mag > mag_limit`
        判断虽然只有一次比较，但遍历 8870 次仍会叠加到绘制时间上；而星等上限
        只在"波特尔等级 / 背景星图层 / 月相"变化时才改，所以把结果缓存下来。
        缓存键用 (星表长度, 星等上限)，星表被替换时也能自动失效。
        """
        key = (len(self._stars), round(float(mag_limit), 3))
        if getattr(self, "_star_floor_key", None) != key:
            self._star_floor_key = key
            self._star_floor_cache = [s for s in self._stars if s.mag <= mag_limit]
        return self._star_floor_cache

    def _draw_stars(self, p: QPainter) -> None:
        """绘制恒星：按星等决定半径与透明度，亮星附加十字光刺。

        光害模拟：超过当前极限星等的暗星直接跳过（城里看不到 6 等以下）。

        性能：星表扩充到 8870 颗之后，这里每帧要遍历全部恒星，所以先按星等
        做一次**提前过滤并缓存下标**（见 _star_floor_cache）：星等下限只在
        波特尔等级 / 图层 / 月亮状态变化时才会变，因此缓存命中率极高，
        每帧实际参与投影的只有当前极限星等以内的一两千颗。
        """
        show_below = self._below_visible()
        mag_limit = self._mag_limit if self.layers.get("background_stars", True) else 11.0
        screen: Dict[str, QPointF] = {}
        labels: List[Tuple[QPointF, str, float]] = []
        for star in self._culled_stars(mag_limit):
            cached = self._sky_cache.get(star.key)
            if cached:
                obj = cached[0]
                if obj.alt < -92.0 or (obj.alt < -1.5 and not show_below):
                    continue
                alt, az = obj.alt, obj.az
            else:
                alt, az = A.equatorial_to_horizontal(
                    star.ra, star.dec, self.observer.lst, self.observer.lat)
                if alt < -1.5 and not show_below:
                    continue
            pt = self._project_point(alt, az)
            if pt is None:
                continue
            screen[star.key] = pt
            radius = star.base_radius
            if star.is_named:
                radius = max(radius, 3.6)
            underground = alt < 0.0
            alpha = self._fade(star.alpha, underground)
            if self.red_mode:
                rgb = (255, int(star.rgb[1] * 0.35), int(star.rgb[2] * 0.25))
            else:
                rgb = star.rgb
            # 光晕
            if star.mag < 3.4 and self.fov < 70.0:
                gsize = self._nearest_glow_size(radius * 7.0)
                pix = self._glow_pixmap(rgb, gsize)
                p.setOpacity(clamp(alpha, 0.0, 1.0))
                p.drawPixmap(QPointF(pt.x() - gsize / 2.0, pt.y() - gsize / 2.0), pix)
                p.setOpacity(1.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(rgb[0], rgb[1], rgb[2], int(255 * clamp(alpha, 0.0, 1.0))))
            p.drawEllipse(pt, radius, radius)
            # 亮星光刺
            if star.mag < 1.6 and not self.red_mode:
                p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2], int(150 * alpha)), 1.0))
                p.drawLine(QPointF(pt.x() - radius * 2.6, pt.y()),
                           QPointF(pt.x() + radius * 2.6, pt.y()))
                p.drawLine(QPointF(pt.x(), pt.y() - radius * 2.6),
                           QPointF(pt.x(), pt.y() + radius * 2.6))
            if star.is_named and self.layers.get("star_names", True):
                labels.append((pt, ("↓ " if underground else "") + star.name,
                               radius))
        # 名称标签（带避让）
        if labels:
            p.setFont(QFont("Microsoft YaHei UI", 8))
            fm = QFontMetrics(p.font())
            placed: List[QRectF] = []
            for pt, name, radius in labels:
                text = name
                w = fm.horizontalAdvance(text)
                rect = QRectF(pt.x() + radius + 3.0, pt.y() - 7.0, w + 4.0, 14.0)
                if rect.right() > self.width() - 2:
                    rect.moveLeft(pt.x() - w - radius - 4.0)
                if any(rect.intersects(q) for q in placed):
                    continue
                placed.append(rect)
                color = C_RED_MODE if self.red_mode else C_STAR_LABEL
                p.setPen(color)
                p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        # 注意：这里**不能**调用 self.update()。
        # 在 paintEvent 里请求重绘会形成"绘制→请求重绘→再绘制"的无限循环，
        # 把 CPU 吃到 100% 以上、界面响应严重变慢（曾经的真实故障）。
        self._last_screen.update(screen)

    def _draw_nearby_marks(self, p: QPainter) -> None:
        """圈出 50 光年内的近星（太阳邻域的"我们附近"标记）。

        这些星本来就包含在主星表里（天狼 8.6 光年、织女一 25 光年…），
        这一图层的作用是把它们从 770 颗背景恒星里**视觉上挑出来**：
        用一个暖黄色圆环 + 距离角标标注，便于快速识别"哪些是我们的近邻"。

        性能：近星集合在首次调用时缓存（原先每帧都遍历 148 颗星做字典查询）。
        """
        if not self._nearby_pairs:
            try:
                from 核心 import nearby_stars as NS
                self._nearby_pairs = [
                    (f"star:{st.hip}", NS.NEARBY_BY_HIP[st.hip])
                    for st in SD.STAR_CATALOG if st.hip in NS.NEARBY_BY_HIP
                ]
                self._nearby_by_hip = dict(NS.NEARBY_BY_HIP)
            except Exception as exc:
                LOG.warn(f"构建近星索引失败：{exc}")
                self._nearby_pairs = []
                return
        screen = self._last_screen
        count = 0
        mark = QColor(255, 120, 95) if self.red_mode else QColor(255, 214, 130)
        label_color = QColor(255, 226, 170, 235)
        show_label = self.fov < 70.0
        for obj_key, near in self._nearby_pairs:
            pt = screen.get(obj_key)
            if pt is None:
                continue
            radius = 5.0 + clamp((20.0 - near.distance_ly) / 20.0, 0.0, 1.0) * 3.0
            p.setPen(QPen(QColor(mark.red(), mark.green(), mark.blue(), 210), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pt, radius, radius)
            if near.distance_ly <= 15.0 and show_label:
                p.setPen(label_color)
                p.setFont(QFont("Microsoft YaHei UI", 8))
                p.drawText(QPointF(pt.x() + radius + 2, pt.y() + radius + 8),
                           f"{near.distance_ly:.1f} ly")
            count += 1
        self._nearby_mark_count = count

    def _body_pixel_size(self, key: str, angular_minutes: float,
                         extra_boost: float = 1.0) -> int:
        """把天体的角直径换算成屏幕上应绘制的像素尺寸。"""
        try:
            px_per_rad = self._focal
            size = px_per_rad * math.tan(math.radians(angular_minutes / 60.0)) * 2.0
            size = size * max(0.5, extra_boost)
            return int(clamp(size, 4.0 if key != "sun" else 26.0,
                             160.0 if key != "sun" else 160.0))
        except Exception:
            return 8

    def _draw_planets(self, p: QPainter) -> None:
        """绘制行星：按类型着色、按亮度定大小，并标注名称。"""
        show_below = self._below_visible()
        labels: List[Tuple[QPointF, str, float]] = []
        for obj in self._sky_cache.get("planets", ()):
            if obj.alt < -1.0 and not show_below:
                continue
            pt = self._project_point(obj.alt, obj.az)
            if pt is None:
                continue
            key = obj.key.split(":")[-1]
            info = P.PLANET_INFO.get(key, {})
            base_rgb = P.planet_color(key) if hasattr(P, "planet_color") else (255, 180, 120)
            if self.red_mode:
                rgb = (255, int(base_rgb[1] * 0.35), int(base_rgb[2] * 0.25))
            else:
                rgb = base_rgb
            underground = obj.alt < 0.0
            alpha = self._fade(1.0, underground)
            size = clamp(4.0 + (2.4 - min(2.4, obj.mag)) * 1.35, 4.4, 16.0)
            # 精细模型：行星始终使用程序化球体（大视场时按放大系数保证可辨认），
            # 这样点击任意行星，信息面板都能给出可拖动的 3D 模型
            angular = float(info.get("angular_diameter_arcsec", 0.0) or 0.0) / 60.0
            boost = 12.0 if self.fov > 60.0 else (6.0 if self.fov > 26.0 else 3.0)
            try:
                px = self._body_pixel_size(key, max(angular, 0.4), boost)
                pix = body_pixmap(key, max(14, px),
                                  body_rotation_phase(key, self.observer.jd_ut * 86400.0))
                if not pix.isNull():
                    p.setOpacity(clamp(alpha, 0.0, 1.0))
                    p.drawPixmap(QPointF(pt.x() - pix.width() / 2.0,
                                         pt.y() - pix.height() / 2.0), pix)
                    p.setOpacity(1.0)
                    labels.append((pt, ("↓ " if underground else "") + obj.name,
                                   pix.width() / 2.0))
                    self._last_screen[obj.key] = pt
                    continue
            except Exception as exc:
                LOG.warn(f"行星模型绘制失败：{exc}")
            grad = QRadialGradient(pt, size * 2.6)
            grad.setColorAt(0.0, QColor(rgb[0], rgb[1], rgb[2], int(235 * alpha)))
            grad.setColorAt(0.45, QColor(rgb[0], rgb[1], rgb[2], int(90 * alpha)))
            grad.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(grad))
            p.drawEllipse(pt, size * 2.6, size * 2.6)
            p.setBrush(QColor(rgb[0], rgb[1], rgb[2], int(255 * alpha)))
            p.drawEllipse(pt, size, size)
            p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2], int(180 * alpha)), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pt, size + 3.0, size + 3.0)
            labels.append((pt, ("↓ " if underground else "") + obj.name, size + 3.0))
            self._last_screen[obj.key] = pt
        if labels:
            self._draw_labels(p, labels, 6.0)

    def _draw_sun_moon(self, p: QPainter) -> None:
        """绘制太阳与月亮。"""
        show_below = self._below_visible()
        labels: List[Tuple[QPointF, str, float]] = []
        for obj in self._sky_cache.get("sun_moon", ()):
            if obj.alt < -1.0 and not show_below:
                continue
            pt = self._project_point(obj.alt, obj.az)
            if pt is None:
                continue
            underground = obj.alt < 0.0
            alpha = self._fade(1.0, underground)
            if obj.kind == "sun":
                size = 9.0 if not underground else 6.0
                rgb = (255, 235, 150)
            else:
                phase = float(obj.extra.get("phase", 0.5) or 0.5)
                size = 6.4 + phase * 3.6
                rgb = (236, 240, 228)
            if self.red_mode:
                rgb = (255, int(rgb[1] * 0.35), int(rgb[2] * 0.25))
            # 精细模型：日月始终使用程序化球体（月相/太阳纹理），便于近距离观察
            used_model = False
            try:
                angular = float(obj.extra.get("angular_diameter_arcsec", 0.0) or 0.0) / 60.0
                boost = 3.0 if self.fov > 40.0 else 1.6
                px = self._body_pixel_size(obj.kind, max(angular, 8.0), boost)
                pix = body_pixmap(obj.kind, max(18, px),
                                  body_rotation_phase(obj.kind,
                                                      self.observer.jd_ut * 86400.0))
                if not pix.isNull():
                    p.setOpacity(clamp(alpha, 0.0, 1.0))
                    p.drawPixmap(QPointF(pt.x() - pix.width() / 2.0,
                                         pt.y() - pix.height() / 2.0), pix)
                    p.setOpacity(1.0)
                    labels.append((pt, ("↓ " if underground else "") + obj.name,
                                   pix.width() / 2.0))
                    self._last_screen[obj.key] = pt
                    used_model = True
            except Exception as exc:
                LOG.warn(f"日月模型绘制失败：{exc}")
            if not used_model:
                grad = QRadialGradient(pt, size * 3.4)
                grad.setColorAt(0.0, QColor(rgb[0], rgb[1], rgb[2], int(250 * alpha)))
                grad.setColorAt(0.3, QColor(rgb[0], rgb[1], rgb[2], int(120 * alpha)))
                grad.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(grad))
                p.drawEllipse(pt, size * 3.4, size * 3.4)
                p.setBrush(QColor(rgb[0], rgb[1], rgb[2], int(255 * alpha)))
                p.drawEllipse(pt, size, size)
                labels.append((pt, ("↓ " if underground else "") + obj.name, size + 4.0))
                self._last_screen[obj.key] = pt
        if labels:
            self._draw_labels(p, labels, 8.0)

    def _draw_messier(self, p: QPainter) -> None:
        """绘制深空天体：按类型使用不同标记符号（星系 / 球状星团 / 疏散星团 / 星云）。"""
        show_below = self._below_visible()
        labels: List[Tuple[QPointF, str, float]] = []
        for obj in self._sky_cache.get("deep_sky", ()):
            if obj.alt < -1.0 and not show_below:
                continue
            if obj.mag > 11.0 and self.fov > 45.0:
                continue
            pt = self._project_point(obj.alt, obj.az)
            if pt is None:
                continue
            mtype = str(obj.extra.get("mtype", ""))
            radius = clamp(6.0 - (obj.mag - 6.0) * 0.55, 3.2, 10.0)
            underground = obj.alt < 0.0
            alpha = self._fade(0.9, underground)
            if self.red_mode:
                color = C_RED_MODE
            else:
                _abbr, rgb = deep_sky_style(mtype)
                color = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]),
                               int(255 * clamp(alpha, 0.0, 1.0)))
            draw_deep_sky_symbol(p, pt, radius, mtype, color)
            self._last_screen[obj.key] = pt
            if self.layers.get("deep_sky_labels", False):
                labels.append((pt, ("↓ " if underground else "")
                               + f"{obj.name} {obj.name_cn}".strip(), radius))
        if labels:
            self._draw_labels(p, labels, 6.0)

    def _draw_constellation_names(self, p: QPainter) -> None:
        """在星座中心位置绘制中文名称。"""
        obs = self.observer
        lst, lat = obs.lst, obs.lat
        p.setFont(QFont("Microsoft YaHei UI", 9))
        for abbr, (cn, _en) in SD.CONSTELLATIONS.items():
            center = SD.CONSTELLATION_CENTERS.get(abbr)
            if center is None:
                continue
            alt, az = A.equatorial_to_horizontal(center[0], center[1], lst, lat)
            if alt < 0.0 and not self._below_visible():
                continue
            pt = self._project_point(alt, az)
            if pt is None:
                continue
            color = C_RED_MODE if self.red_mode else C_CONST_LABEL
            p.setPen(QColor(color.red(), color.green(), color.blue(), 200))
            fm = QFontMetrics(p.font())
            p.drawText(QPointF(pt.x() - fm.horizontalAdvance(cn) / 2.0, pt.y()), cn)

    def _draw_azimuth_labels(self, p: QPainter) -> None:
        """地平线方位刻度：北 / 东 / 南 / 西 + 每 30° 的度数。"""
        p.setFont(QFont("Microsoft YaHei UI", 9, QFont.DemiBold))
        for az in range(0, 360, 10):
            pt = self._project_point(-1.2, float(az))
            if pt is None:
                continue
            major = (az % 90 == 0)
            color = QColor(175, 232, 250, 225) if major else QColor(130, 205, 235, 150)
            p.setPen(color)
            if az % 30 == 0:
                p.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold if major else QFont.Normal))
                label = azimuth_to_compass(float(az)) if major else f"{az}°"
                fm = QFontMetrics(p.font())
                p.drawLine(QPointF(pt.x(), pt.y()), QPointF(pt.x(), pt.y() - 6.0))
                p.drawText(QPointF(pt.x() - fm.horizontalAdvance(label) / 2.0,
                                   pt.y() - 8.0), label)
            else:
                p.setPen(QPen(color, 1.0))
                p.drawLine(QPointF(pt.x(), pt.y()), QPointF(pt.x(), pt.y() - 3.0))

    def _draw_labels(self, p: QPainter, labels: Sequence[Tuple[QPointF, str, float]],
                     offset: float = 4.0) -> None:
        """统一绘制天体名称标签（带轻微描边，避免与星空混淆）。"""
        p.setFont(QFont("Microsoft YaHei UI", 9))
        fm = QFontMetrics(p.font())
        for pt, text, radius in labels:
            if not text:
                continue
            w = fm.horizontalAdvance(text)
            rect = QRectF(pt.x() + radius + offset, pt.y() - 7.0, w + 4.0, 14.0)
            if rect.right() > self.width() - 2:
                rect.moveLeft(pt.x() - w - radius - offset - 2.0)
            path = QPainterPath()
            path.addText(rect.left(), rect.bottom() - 3.0, p.font(), text)
            if self.red_mode:
                p.setPen(QPen(QColor(30, 0, 0, 170), 2.0))
            else:
                p.setPen(QPen(QColor(2, 8, 16, 170), 2.4))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(C_RED_MODE if self.red_mode else QColor(240, 250, 255, 235))
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)

    def _draw_hud(self, p: QPainter) -> None:
        """屏幕 HUD：视角信息、准星、选中高亮、比例尺。"""
        # 准星
        cx, cy = self.width() * 0.5, self.height() * 0.5
        p.setPen(QPen(QColor(0, 200, 240, 90), 1.0))
        p.drawLine(QPointF(cx - 12, cy), QPointF(cx + 12, cy))
        p.drawLine(QPointF(cx, cy - 12), QPointF(cx, cy + 12))
        # 文本信息
        p.setFont(QFont("Consolas", 9))
        color = QColor(145, 246, 255, 242) if not self.red_mode else QColor(255, 125, 105)
        p.setPen(color)
        lines = [
            f"CENTER  ALT {self.center_alt:+6.2f}°   AZ {self.center_az:6.2f}°"
            f"  ({azimuth_to_compass(self.center_az)})",
            f"FOV     {self.fov:6.2f}°   ZOOM {FOV_MAX / max(1e-6, self.fov):5.2f}x",
            f"LST     {self.observer.lst / 15.0:5.2f}h   UTC{self.observer.utc_offset:+.1f}",
            f"TIME    {self.observer.when.strftime('%Y-%m-%d %H:%M:%S')}",
            f"LIMIT   {self._mag_limit:4.1f} 等（波特尔 {self.bortle_level} 级）",
        ]
        # 图层状态提示：让用户随时知道当前叠加了什么
        active: List[str] = []
        if self.layers.get("below_horizon"):
            active.append(f"地平线下天体 {self._below_count} 个"
                          f"（压暗 {self._fade(1.0, True) * 100:.0f}%）")
        if self.layers.get("nearby_mark"):
            active.append(f"50 光年内近星 {self._nearby_mark_count} 颗")
        if self.layers.get("messier"):
            deep = sum(1 for o in self._sky_cache.get("deep_sky", ()) if o.alt > 0)
            active.append(f"深空天体可见 {deep} 个")
        for text in active:
            lines.append(f"LAYER   {text}")
        y = 16
        for line in lines:
            p.drawText(QPointF(10, y), line)
            y += 12
        if self.show_fps:
            p.setPen(QColor(120, 215, 245, 200))
            p.drawText(QPointF(10, y + 2), f"FRAME   {getattr(self, '_frame_ms', 0.0):5.1f} ms")
        # 选中高亮
        if self._highlight_key:
            pt = self._last_screen.get(self._highlight_key)
            if pt is not None:
                p.setPen(QPen(QColor(255, 235, 150, 235), 1.6, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, 14.0, 14.0)
        elif self._hover_key:
            pt = self._last_screen.get(self._hover_key)
            if pt is not None:
                p.setPen(QPen(QColor(140, 255, 235, 200), 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, 10.0, 10.0)
        if self.show_legend:
            self._draw_legend(p)

    def _draw_legend(self, p: QPainter) -> None:
        """左下角图例：说明画面中各元素的含义。"""
        items: List[Tuple[str, Tuple[int, int, int]]] = []
        if self.layers.get("milky_way", True):
            items.append(("银河带", (150, 170, 230)))
        if self.layers.get("constellation_lines", True):
            items.append(("星座连线", (96, 208, 248)))
        if self.layers.get("messier", True):
            items.append(("深空天体", (200, 180, 255)))
        if self.layers.get("planets", True):
            items.append(("行星", (255, 180, 120)))
        if self.layers.get("below_horizon", False):
            items.append((f"地平线下天体（压暗 {self._fade(1.0, True) * 100:.0f}%）",
                          (255, 130, 96)))
        if self.layers.get("nearby_mark", False):
            items.append(("50 光年内的近星", (255, 214, 130)))
        if not items:
            return
        p.setFont(QFont("Microsoft YaHei UI", 8))
        fm = QFontMetrics(p.font())
        width = max(fm.horizontalAdvance(t) for t, _c in items) + 34.0
        height = 14.0 * len(items) + 8.0
        x0, y0 = 10.0, self.height() - height - 24.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(4, 12, 20, 175))
        p.drawRoundedRect(QRectF(x0 - 4, y0 - 4, width, height), 3.0, 3.0)
        p.setPen(QPen(QColor(0, 150, 190, 120), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x0 - 4, y0 - 4, width, height), 3.0, 3.0)
        for i, (text, rgb) in enumerate(items):
            yy = y0 + 12 + i * 14
            color = C_RED_MODE if self.red_mode else QColor(*rgb)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(x0 + 6, yy - 3), 4.0, 4.0)
            p.setPen(QColor(210, 236, 250, 235))
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
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._drag_mode:
            dx = pos.x() - self._last_pos.x()
            dy = pos.y() - self._last_pos.y()
            self._last_pos = pos
            if self._drag_mode == 1:
                # 拖拽旋转：视场越小，转动越精细
                self.fov = self.fov
                k = 1.0 / max(1.0, self.height()) * 180.0 * (self.fov / 100.0)
                self.center_az = norm360(self.center_az - dx * k * 0.9)
                self.center_alt = clamp(self.center_alt + dy * k * 0.9, -88.0, 88.0)
                self._emit_view()
                self.mark_dirty()
            else:
                self.mark_dirty()
        else:
            self._update_hover(event.position())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_mode == 1 and event.button() == Qt.LeftButton:
            # 拖拽距离很小视为点击选中
            moved = (event.position().toPoint() - self._last_pos).manhattanLength()
            if moved < 4:
                self._handle_click(event.position())
        elif self._drag_mode == 2 and event.button() == Qt.RightButton:
            # 右键未拖动 → 弹出上下文菜单
            moved = (event.position().toPoint() - self._last_pos).manhattanLength()
            if moved < 5:
                self._show_context_menu(event.position())
        self._drag_mode = 0
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击把该天体移到视野中心。"""
        obj = self._object_at(event.position())
        if obj is not None:
            self.set_view_center(obj.alt, obj.az)
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self.fov = clamp(self.fov * (0.88 if delta > 0 else 1.1363636363636365),
                             FOV_MIN, FOV_MAX)
            self._emit_view()
            self.mark_dirty()
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_key = None
        self.mark_dirty()
        super().leaveEvent(event)

    def _update_hover(self, pos: QPointF) -> None:
        """更新鼠标悬停提示（悬停气泡：名称 + 类型 + 星等 + 高度方位 + 距离）。"""
        obj = self._object_at(pos)
        key = obj.key if obj else None
        if key != self._hover_key:
            self._hover_key = key
            self._needs_repaint = True
            if obj is not None:
                self.setToolTip(self._hover_text(obj))
            else:
                self.setToolTip("")
            self.mark_dirty()

    def _hover_text(self, obj: A.SkyObject) -> str:
        """构造悬停气泡文本（比默认 tooltip 信息更全，且区分地上/地下）。"""
        kind_cn = {"star": "恒星", "planet": "行星", "sun": "太阳", "moon": "月球",
                   "messier": "深空天体"}.get(obj.kind, "天体")
        lines = [f"<b>{obj.name or obj.name_en}</b>　"
                 f"<span style='color:#8fd4ea'>{kind_cn}</span>"]
        detail: List[str] = []
        if obj.spectral:
            detail.append(f"光谱型 {obj.spectral}")
        if obj.mag is not None:
            detail.append(f"星等 {obj.mag:+.2f}")
        if obj.constellation:
            detail.append(obj.constellation)
        if detail:
            lines.append("　".join(detail))
        if obj.alt < 0:
            lines.append(f"<span style='color:#ffb37a'>地平线下 {obj.alt:+.1f}°"
                         f"（方位 {azimuth_to_compass(obj.az)}）</span>")
        else:
            lines.append(f"高度 {obj.alt:+.1f}°　方位 {azimuth_to_compass(obj.az)}"
                         f"（{obj.az:.0f}°）")
        if obj.distance is not None:
            if obj.kind == "star":
                lines.append(f"距离 {obj.distance:.2f} 光年")
            elif obj.kind in ("planet", "sun"):
                lines.append(f"距离 {obj.distance:.4f} AU")
            elif obj.kind == "moon":
                lines.append(f"距离 {obj.distance * A.AU_KM:,.0f} 公里")
        if obj.kind == "messier":
            lines.append(f"{obj.extra.get('mtype', '')}　{obj.extra.get('size', '')}")
        lines.append("<span style='color:#7f9fb0'>右键查看更多操作</span>")
        return "<br>".join(lines)

    def _show_context_menu(self, pos: QPointF) -> None:
        """右键菜单：把常用操作放到光标位置，减少来回找面板的次数。"""
        from PySide6.QtWidgets import QMenu
        obj = self._object_at(pos)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:rgba(8,22,34,250);border:1px solid rgba(0,190,230,180);"
            "border-radius:4px;padding:4px;color:#e6f6fd;}"
            "QMenu::item{padding:5px 22px 5px 16px;border-radius:3px;}"
            "QMenu::item:selected{background:rgba(0,200,240,120);color:#ffffff;}"
            "QMenu::separator{height:1px;background:rgba(0,190,230,120);margin:4px 6px;}")
        if obj is not None:
            title = menu.addAction(f"{obj.name or obj.name_en}"
                                   + (f"（{obj.mag:+.2f} 等）" if obj.mag is not None else ""))
            title.setEnabled(False)
            menu.addSeparator()
            if obj.alt < 0:
                info = A.rise_set_info(obj.ra, obj.dec, self.observer)
                act_rise = menu.addAction(f"⏱ 升落：{info.describe()}")
                act_rise.setEnabled(False)
                menu.addSeparator()
            menu.addAction("◎ 移到视野中心", lambda: self.set_view_center(obj.alt, obj.az))
            menu.addAction("🔍 放大定位（视场 20°）",
                           lambda: self.look_at_radec(obj.ra, obj.dec, zoom_to=20.0))
            menu.addAction("☆ 加入观测清单",
                           lambda: self.watchlist_add.emit(self.describe_object(obj)))
            menu.addAction("📋 复制坐标与升落信息", lambda: self._copy_object_info(obj))
            menu.addSeparator()
            menu.addAction("隐藏恒星名称", lambda: self._quick_layer("star_names", False))
            menu.addAction("圈出 50 光年内的近星",
                           lambda: self._quick_layer("nearby_mark", True))
            menu.addAction("显示地平线下天体",
                           lambda: self._quick_layer("below_horizon", True))
        else:
            title = menu.addAction("星空（未选中天体）")
            title.setEnabled(False)
            menu.addSeparator()
            alt, az = self._invert_screen_pos(pos)
            if alt is not None:
                menu.addAction(f"◎ 以此处为中心（高度 {alt:.0f}°）",
                               lambda: self.set_view_center(alt, az))
            menu.addAction("🔄 重置视角", self.reset_view)
            menu.addSeparator()
            menu.addAction("图层：星座连线",
                           lambda: self._quick_layer(
                               "constellation_lines",
                               not self.layers.get("constellation_lines", True)))
            menu.addAction("图层：银河带",
                           lambda: self._quick_layer(
                               "milky_way", not self.layers.get("milky_way", True)))
            menu.addAction("图层：深空天体",
                           lambda: self._quick_layer(
                               "messier", not self.layers.get("messier", True)))
            menu.addAction("图层：地平网格",
                           lambda: self._quick_layer(
                               "horizon_grid", not self.layers.get("horizon_grid", True)))
        menu.exec(self.mapToGlobal(pos.toPoint()))

    def _quick_layer(self, key: str, value: bool) -> None:
        """右键菜单里的图层快捷开关（同时同步左侧复选框由主窗口负责）。"""
        self.set_layer(key, value)
        self.layers_changed.emit(key, bool(value))

    def _invert_screen_pos(self, pos: QPointF) -> Tuple[float, float]:
        """屏幕坐标 → 地平坐标（用于"以此处为中心"）。

        心射投影的逆运算：屏幕点 → 相机坐标 → 单位向量 → 地平经纬。
        """
        try:
            x = (pos.x() - self._cx) / self._focal
            y = (self._cy - pos.y()) / self._focal
            right, up, fwd = self._basis
            vx = fwd[0] + x * right[0] + y * up[0]
            vy = fwd[1] + x * right[1] + y * up[1]
            vz = fwd[2] + x * right[2] + y * up[2]
            n = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
            vx, vy, vz = vx / n, vy / n, vz / n
            alt = math.degrees(math.asin(clamp(vz, -1.0, 1.0)))
            az = norm360(math.degrees(math.atan2(vx, vy)))
            return (alt, az)
        except Exception:
            return (None, None)      # type: ignore[return-value]

    def _copy_object_info(self, obj: A.SkyObject) -> None:
        """把天体的坐标与升落信息复制到剪贴板。"""
        try:
            from PySide6.QtGui import QGuiApplication
            info = self.describe_object(obj)
            lines = [
                f"{obj.name or obj.name_en}（{obj.name_en}）",
                f"类型：{info.get('type_cn', obj.kind)}",
                f"赤经：{fmt_ra(obj.ra)}　赤纬：{fmt_dec(obj.dec)}（J2000 历元，已做岁差修正）",
                f"高度角：{obj.alt:+.2f}°　方位角：{obj.az:.2f}°（{azimuth_to_compass(obj.az)}）",
                f"视星等：{obj.mag:+.2f}",
            ]
            if info.get("next_event"):
                lines.append(f"升落：{info['next_event']}")
            if info.get("peak_alt") is not None:
                lines.append(f"未来 24 小时最高高度：{float(info['peak_alt']):+.1f}°")
            lines.append(f"观测时刻（本地）：{self.observer.when.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"观测点：{self.observer.location_name} "
                         f"{self.observer.lat:+.4f}°, {self.observer.lon:+.4f}°")
            QGuiApplication.clipboard().setText("\n".join(lines))
            self.status_message.emit(f"已复制 {obj.name or obj.name_en} 的坐标与升落信息")
        except Exception as exc:
            LOG.warn(f"复制天体信息失败：{exc}")

    def _object_at(self, pos: QPointF, radius: float = 14.0) -> Optional[A.SkyObject]:
        """屏幕坐标 → 最近的天体（用于点击与悬停）。"""
        best_key: Optional[str] = None
        best_d = radius * radius
        for key, pt in self._last_screen.items():
            d = (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2
            if d < best_d:
                best_d, best_key = d, key
        if best_key is None:
            return None
        for obj in self._all_objects():
            if obj.key == best_key:
                return obj
        return None

    def _handle_click(self, pos: QPointF) -> None:
        """点击选中天体并发出信号。"""
        obj = self._object_at(pos)
        if obj is None:
            self._highlight_key = None
            self.mark_dirty()
            return
        self._highlight_key = obj.key
        self.mark_dirty()
        self.object_selected.emit(self.describe_object(obj))

    def _focus_star_by_object(self, star: "SD.Star") -> Optional[Dict[str, object]]:
        """把视角对准一颗星表里的恒星并返回它的信息字典。"""
        try:
            key = f"star:{star.hip}"
            self._highlight_key = key
            self.look_at_radec(star.ra, star.dec, zoom_to=min(self.fov, 45.0))
            for obj in self._all_objects():
                if obj.key == key:
                    info = self.describe_object(obj)
                    self.object_selected.emit(info)
                    return info
            alt = self.observer.altitude(star.ra, star.dec)
            az = self.observer.radec_to_altaz(star.ra, star.dec)[1]
            info: Dict[str, object] = {
                "panel": "sky", "kind": "star", "name": star.display_name(),
                "name_en": star.name_en, "key": key, "type_cn": "恒星",
                "ra": star.ra, "dec": star.dec, "alt": alt, "az": az,
                "mag": star.mag, "bv": star.bv, "spectral": star.spectral,
                "constellation": star.constellation,
                "distance_ly": star.distance, "hip": star.hip,
            }
            self.object_selected.emit(info)
            return info
        except Exception as exc:
            LOG.error(f"定位恒星失败：{exc}")
            return None

    def _find_planet_object(self, key: str) -> Optional[A.SkyObject]:
        """取行星的 SkyObject；未开启「地平线下天体」时缓存里可能没有，
        这里按需临时计算一个，保证搜索/点击任何时刻都能拿到行星信息
        （否则行星落在地平线下时搜索会"找不到"，这是原版的缺陷）。
        """
        full_key = f"planet:{key}"
        for obj in self._sky_cache.get("planets", ()):
            if obj.key == full_key:
                return obj
        try:
            p = A.planet_position(key, self.observer.jd_ut)
            alt, az = self.observer.radec_to_altaz(p["ra"], p["dec"])
            info = P.PLANET_INFO.get(key, {})
            return A.SkyObject(
                key=full_key, name=info.get("name_cn", key),
                name_en=info.get("name_en", key), kind="planet",
                ra=p["ra"], dec=p["dec"], alt=alt, az=az,
                mag=float(p.get("mag", 0.0)), bv=0.6,
                distance=float(p.get("distance", 0.0)),
                extra={"angular_diameter_arcsec": info.get("angular_diameter_arcsec", 0.0),
                       "underground_only": True})
        except Exception as exc:
            LOG.warn(f"临时计算行星位置失败（{key}）：{exc}")
            return None

    def _find_sun_moon(self, kind: str) -> Optional[A.SkyObject]:
        """取太阳或月球的 SkyObject（同理，按需兜底计算）。"""
        for obj in self._sky_cache.get("sun_moon", ()):
            if obj.kind == kind:
                return obj
        try:
            if kind == "moon":
                m = A.moon_position(self.observer.jd_ut)
                name, name_en = "月球", "Moon"
                extra = {"phase": float(m.get("phase", 0.5)),
                         "phase_name": m.get("phase_name", ""),
                         "distance_km": float(m.get("distance", 384400.0)),
                         "angular_diameter_arcsec": 1860.0}
                mag = -12.7
            else:
                s = A.sun_position(self.observer.jd_ut)
                m = s
                name, name_en = "太阳", "Sun"
                extra = {"angular_diameter_arcsec": 1920.0}
                mag = -26.7
            alt, az = self.observer.radec_to_altaz(m["ra"], m["dec"])
            return A.SkyObject(key=kind, name=name, name_en=name_en, kind=kind,
                               ra=m["ra"], dec=m["dec"], alt=alt, az=az, mag=mag,
                               bv=0.6, extra=extra)
        except Exception as exc:
            LOG.warn(f"临时计算{kind}位置失败：{exc}")
            return None

    def describe_object(self, obj: A.SkyObject) -> Dict[str, object]:
        """把 SkyObject 转换成信息面板需要的字典。"""
        info: Dict[str, object] = {
            "panel": "sky", "kind": obj.kind, "key": obj.key,
            "name": obj.name or obj.name_en, "name_en": obj.name_en,
            "type_cn": {"star": "恒星", "planet": "行星", "sun": "太阳",
                        "moon": "月球", "messier": "深空天体"}.get(obj.kind, obj.kind),
            "ra": obj.ra, "dec": obj.dec, "alt": obj.alt, "az": obj.az,
            "mag": obj.mag, "bv": obj.bv, "spectral": obj.spectral,
            "constellation": obj.constellation,
        }
        try:
            rsi = A.rise_set_info(obj.ra, obj.dec, self.observer)
            info["rise_set"] = rsi.describe()
            info["next_event"] = rsi.next_event
            info["peak_alt"] = rsi.peak_alt
            info["above_horizon"] = bool(obj.alt > 0.0)
            info["blocked"] = self._horizon_blocked(obj.alt, obj.az)
            info["circumpolar"] = rsi.circumpolar
            info["never_rises"] = rsi.never_rises
        except Exception as exc:
            LOG.warn(f"升落信息计算失败：{exc}")
        if obj.kind == "star":
            info["distance_ly"] = obj.distance
        elif obj.kind in ("planet", "sun", "moon"):
            info["distance_au"] = obj.distance
            info["extra"] = dict(obj.extra)
            if obj.kind == "planet":
                # 信息面板的精细模型与行星参数页需要 planet_summary 字典
                key = obj.key.split(":")[-1]
                summary = P.planet_summary(key, self.observer.jd_ut)
                if summary:
                    summary["apparent_magnitude"] = obj.mag
                    info["planet"] = summary
            elif obj.kind == "moon":
                info["phase"] = float(obj.extra.get("phase", 0.5) or 0.5)
                info["phase_name"] = str(obj.extra.get("phase_name", ""))
            elif obj.kind == "sun":
                info["spectral"] = info.get("spectral") or "G2V"
        elif obj.kind == "messier":
            info["mtype"] = obj.extra.get("mtype", "")
            info["size"] = obj.extra.get("size", "")
            info["desc"] = obj.extra.get("desc", "")
            if obj.name.startswith("M") and not obj.name.startswith("NGC"):
                info["messier"] = obj.name
        return info

    def find_object(self, text: str) -> Optional[Dict[str, object]]:
        """按名称搜索天体；返回信息字典（同时更新高亮并转向目标）。

        支持的输入形式：
            中文名（天狼）/ 英文名（Vega）/ HIP 编号 / Messier 编号（M42）/
            NGC 与 IC 编号 / 星座名（猎户座）/ 行星名 / **Gliese 近星编号**
        """
        text = (text or "").strip()
        if not text:
            return None
        low = text.lower()
        # 先试近星编目编号 / 中文别名（Gliese、HIP、比邻星、巴纳德星…）
        # 注意：只有输入确实"像编目编号"或带空格时才查近星，否则
        # "M42"、"NGC 7000" 里的数字会被模糊匹配成 Gliese 编号（早期版本的实际缺陷）
        look_nearby = (low.startswith(("gliese", "gl ", "gj ", "hip", "nearby"))
                       or " " in text or bool(_CN_NEARBY_NAMES & set(text)))
        near = None
        if look_nearby:
            try:
                from 核心 import nearby_stars as NS
                near = NS.find_nearby(text)
            except Exception:
                near = None
        if near is not None:
            if near.hip:
                ref = SD.get_star(near.hip)
                if ref is not None:
                    info = self._focus_star_by_object(ref)
                    if info is not None:
                        self.status_message.emit(
                            f"已定位 {near.display_name}（{near.catalog_id}，"
                            f"{near.distance_ly:.2f} 光年）")
                        return info
            # 该近星不在亮星表里（例如比邻星）：交给主窗口切到太阳邻域视图
            self.nearby_requested.emit(near.display_name)
            self.status_message.emit(
                f"{near.display_name} 不在亮星表中，已切换到太阳邻域视图显示它"
                f"（{near.distance_ly:.2f} 光年）")
            return near.to_dict()
        # 行星
        for key, info in P.PLANET_INFO.items():
            names = (info.get("name_cn", ""), info.get("name_en", ""), key)
            if any(n and (low == str(n).lower() or str(n).lower().startswith(low))
                   for n in names):
                obj = self._find_planet_object(key)
                if obj is None:
                    return None
                out = self.describe_object(obj)
                self._highlight_key = obj.key
                self.look_at_radec(obj.ra, obj.dec, zoom_to=min(self.fov, 45.0))
                self.object_selected.emit(out)
                self.status_message.emit(f"已定位 {obj.name}")
                return out
        # 太阳 / 月球：先做中文与英文精确匹配
        # （早期版本用 `low in (...)` 判断，搜"月球"时会因为 "moon" 在候选里
        #   而返回循环先遇到的太阳对象，属于真实的逻辑缺陷）
        sun_moon_names = {"sun": "sun", "太阳": "sun", "moon": "moon",
                          "月球": "moon", "月亮": "moon", "太阳": "sun"}
        kind_hit = sun_moon_names.get(low)
        if kind_hit is None:
            for obj in self._sky_cache.get("sun_moon", ()):
                if low in (obj.name.lower(), obj.name_en.lower()):
                    kind_hit = obj.kind
                    break
        if kind_hit is not None:
            obj = self._find_sun_moon(kind_hit)
            if obj is not None:
                out = self.describe_object(obj)
                self._highlight_key = obj.key
                self.look_at_radec(obj.ra, obj.dec, zoom_to=min(self.fov, 45.0))
                self.object_selected.emit(out)
                self.status_message.emit(f"已定位 {obj.name}")
                return out
        # 亮星 / 深空天体 / 星座：用统一搜索索引
        # 分两轮匹配：先精确匹配，再前缀/包含匹配，避免 "NGC 7000" 命中 "Gliese 70"
        targets = SD.search_targets()
        for exact in (True, False):
            for display, kind, key in targets:
                low_display = display.lower()
                if exact:
                    hit = (low_display == low)
                else:
                    hit = (low in low_display)
                if not hit:
                    continue
                if kind == "star":
                    star = SD.get_star(int(key.split(":")[-1]))
                    if star is not None:
                        return self._focus_star_by_object(star)
                elif kind == "messier":
                    name = key.split(":")[-1]
                    for obj in self._sky_cache.get("deep_sky", ()):
                        if obj.name == name:
                            out = self.describe_object(obj)
                            self._highlight_key = obj.key
                            self.look_at_radec(obj.ra, obj.dec, zoom_to=min(self.fov, 45.0))
                            self.object_selected.emit(out)
                            self.status_message.emit(f"已定位 {obj.name} {obj.name_cn}")
                            return out
                    deep = next((m for m in SD.DEEP_SKY_CATALOG if m.name == name), None)
                    if deep is not None:
                        alt, az = A.equatorial_to_horizontal(
                            deep.ra, deep.dec, self.observer.lst, self.observer.lat)
                        self.set_view_center(alt, az)
                        self.status_message.emit(f"已定位 {name}（{deep.name_cn}）")
                    return None
                elif kind == "constellation":
                    center = SD.CONSTELLATION_CENTERS.get(key)
                    if center is not None:
                        alt, az = A.equatorial_to_horizontal(
                            center[0], center[1], self.observer.lst, self.observer.lat)
                        self.set_view_center(alt, az)
                        cn = SD.CONSTELLATIONS.get(key, (key, ""))[0]
                        self.status_message.emit(f"已定位星座 {cn}")
                        return {"panel": "sky", "kind": "constellation",
                                "key": f"const:{key}", "name": cn, "name_en": key,
                                "type_cn": "星座", "ra": center[0], "dec": center[1],
                                "alt": alt, "az": az, "mag": 0.0, "bv": 0.0,
                                "spectral": "", "constellation": key}
        self.status_message.emit(f"未找到「{text}」")
        return None

    def highlight_key(self, key: str) -> None:
        """外部设置高亮目标。"""
        self._highlight_key = key or None
        self.mark_dirty()

    def set_time_speed_level(self, level: float, playing: Optional[bool] = None) -> None:
        """按倍率等级设置时间速度（1/10/100/1000 倍）。"""
        self.set_time_speed(max(0.01, self._base_speed_seconds) * float(level), playing)

    def visibility_summary(self) -> Dict[str, int]:
        """统计当前可见天体数量（状态栏用）。"""
        return {group: sum(1 for o in objs if o.alt > 0)
                for group, objs in self._sky_cache.items()}


# ==============================================================================
# 恒星颜色
# ==============================================================================

def _star_rgb(star: "SD.Star") -> Tuple[int, int, int]:
    """取恒星显示颜色（B-V → RGB）。"""
    from 核心.utils import bv_to_rgb
    return bv_to_rgb(star.bv)


__all__ = ["RealSkyView", "FOV_MIN", "FOV_MAX"]
