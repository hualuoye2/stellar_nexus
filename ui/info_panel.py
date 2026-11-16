# -*- coding: utf-8 -*-
"""
info_panel.py —— 右侧信息面板
================================================================================
统一承载两类信息展示：

    1. 现实星图：天体信息卡片
        名称 / 类型 / 星等 / 赤经赤纬 / 方位角 / 高度角 / 距离 / 光谱型 / 星座
        行星另有轨道与物理参数；月球显示月相；Messier 显示类型与简介

    2. 架空星图：恒星系信息面板（7 个标签页）
        概览 / 行星 / 文明 / 资源 / 航线 / 事件 / 历史

对外信号：
    route_from_here(int)   把某恒星系设为航线起点
    route_to_here(int)     把某恒星系设为航线终点
    locate_system(int)     在星图中定位该恒星系
    dispatch_requested(int) 请求向该恒星系派遣飞船
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                               QSizePolicy, QStackedWidget, QTabWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from 核心 import astronomy as A
from 核心 import nearby_stars as NS
from 核心 import planet_data as P
from 核心 import star_data as SD
from 核心 import world_generator as WG
from 核心.utils import (LOG, azimuth_to_compass, clamp, fmt_alt_az, fmt_dec, fmt_float, fmt_ra,
                        format_distance_ly, format_gametime, human_duration, now_local,
                        rgb_str)

from .body_renderer import PLANET_SURFACE, BodyModelView, deep_sky_style
from .widgets import Badge, GlowButton, InfoRow, SeparatorLine, SectionTitle, StatBar

#: 类型 → 中文
KIND_CN: Dict[str, str] = {
    "star": "恒星", "planet": "行星", "sun": "恒星（太阳）", "moon": "卫星（月球）",
    "messier": "深空天体", "constellation": "星座",
}


# ==============================================================================
# 通用小工具
# ==============================================================================

def _section(text: str, accent: str = "cyan") -> SectionTitle:
    return SectionTitle(text, accent)


def _scrollable(inner: QWidget) -> QScrollArea:
    """把内容包进可滚动区域，保证小窗口下也能看全信息。

    这里额外做了两件"防看不见"的事：
        * 视口显式填充 #061019（某些平台默认视口会用浅色调色板）
        * 内层容器设置 WA_StyledBackground + 透明背景（避免继承系统浅色底）
    """
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(inner)
    try:
        from PySide6.QtGui import QPalette
        vp = area.viewport()
        if vp is not None:
            vp.setAutoFillBackground(True)
            pal = vp.palette()
            pal.setColor(QPalette.Window, QColor("#061019"))
            pal.setColor(QPalette.Base, QColor("#061019"))
            vp.setPalette(pal)
    except Exception:
        pass
    inner.setAttribute(Qt.WA_StyledBackground, True)
    inner.setStyleSheet("background:transparent;")
    area.setStyleSheet("QScrollArea{background:transparent;}"
                       "QScrollBar:vertical{background:rgba(6,16,26,150);width:8px;margin:0;}"
                       "QScrollBar::handle:vertical{background:rgba(0,180,220,150);"
                       "border-radius:4px;min-height:20px;}"
                       "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
    return area


def _text_block(html: str = "") -> QTextBrowser:
    """深色风格的内嵌文本浏览区（用于简介、事件流等）。"""
    browser = QTextBrowser()
    browser.setOpenExternalLinks(False)
    browser.setFrameShape(QFrame.NoFrame)
    browser.setHtml(html)
    browser.setStyleSheet(
        "QTextBrowser{background:rgba(5,14,22,150);border:1px solid rgba(0,150,190,90);"
        "border-radius:3px;color:#cfeaf5;padding:4px;}")
    return browser


def _badge_row(items: Sequence[Tuple[str, Sequence[int]]]) -> QWidget:
    """一行彩色徽章。"""
    w = QWidget()
    layout = QHBoxLayout(w)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(4)
    for text, color in items:
        layout.addWidget(Badge(text, color))
    layout.addStretch(1)
    return w


# ==============================================================================
# 现实星图 · 天体信息页
# ==============================================================================

class SkyObjectPanel(QWidget):
    """天体信息卡片（现实星图）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._inner = QWidget()
        layout = QVBoxLayout(self._inner)
        layout.setContentsMargins(8, 6, 8, 10)
        layout.setSpacing(6)

        # 标题区
        self._title = QLabel("未选择天体")
        self._title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        self._title.setStyleSheet("color:#7ff0ff;background:transparent;")
        self._subtitle = QLabel("点击星图中的天体查看详情")
        self._subtitle.setFont(QFont("Microsoft YaHei UI", 8))
        self._subtitle.setStyleSheet("color:#82a8bb;background:transparent;")
        self._badges = _badge_row([])
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        layout.addWidget(self._badges)
        layout.addWidget(SeparatorLine())

        # 基础信息行
        self._rows: Dict[str, InfoRow] = {}
        self._rows_host = QWidget()
        rows_layout = QVBoxLayout(self._rows_host)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(1)
        for key, label in (("type", "类型"), ("mag", "视星等"), ("ra", "赤经"), ("dec", "赤纬"),
                           ("alt", "高度角"), ("az", "方位角"), ("rise", "升落"),
                           ("peak", "最高高度"), ("distance", "距离"),
                           ("spectral", "光谱型"), ("bv", "B-V 色指数"), ("constellation", "所在星座"),
                           ("hip", "HIP 编号")):
            row = InfoRow(label)
            self._rows[key] = row
            rows_layout.addWidget(row)
        layout.addWidget(self._rows_host)

        # 扩展信息区（精细模型 + 行星参数 / 月相 / 深空简介）
        self._model_title = _section("精细模型", "violet")
        self._model_view = BodyModelView(self)
        self._model_view.setVisible(False)
        self._extra_title = _section("附加信息")
        self._extra_host = QWidget()
        self._extra_layout = QVBoxLayout(self._extra_host)
        self._extra_layout.setContentsMargins(0, 0, 0, 0)
        self._extra_layout.setSpacing(2)
        self._extra_desc = _text_block("")
        self._extra_desc.setVisible(False)
        layout.addWidget(self._model_title)
        layout.addWidget(self._model_view)
        layout.addWidget(self._extra_title)
        layout.addWidget(self._extra_host)
        layout.addWidget(self._extra_desc)
        layout.addStretch(1)

        self._model_title.setVisible(False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(self._inner))
        self.clear()      # 首次显示即给出上手引导，而不是十三个「--」

    def clear(self) -> None:
        """清空显示。

        首次进入时给出**可操作的上手引导**，而不是十三个「--」——
        让新用户知道这个面板能做什么、去哪里看什么。
        """
        self._title.setText("未选择天体")
        self._subtitle.setText("点击星图中的天体查看详情")
        self._set_badges([])
        for row in self._rows.values():
            row.set_value("--")
        self._clear_extra()
        self._model_view.setVisible(False)
        self._model_title.setVisible(False)
        self._extra_desc.setVisible(True)
        self._extra_desc.setHtml(
            "<div style='line-height:1.85;color:#bfe4f5'>"
            "<b style='color:#9ff6ff'>可以试试</b><br>"
            "· 点击一颗亮星 → 星等、赤经赤纬、升落时刻、未来最高高度<br>"
            "· 点击行星 → 顶部出现可拖动的<b>精细球体模型</b>（拖动自转 / 滚轮调相位）<br>"
            "· 打开图层「圈出 50 光年内的近星」→ 看哪些是我们的近邻<br>"
            "· 打开图层「地平线下天体」→ 查看尚未升起的目标与几点升起<br>"
            "· 搜索框支持：天狼 / Vega / HIP 87937 / Gliese 551 / M42 / 猎户座<br>"
            "· 架空星图中 Shift+点击两个恒星系 → 快速设置航线起点与终点"
            "</div>")

    def _set_badges(self, items: Sequence[Tuple[str, Sequence[int]]]) -> None:
        layout = self._badges.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for text, color in items:
            layout.addWidget(Badge(text, color))
        layout.addStretch(1)

    def _clear_extra(self) -> None:
        while self._extra_layout.count():
            item = self._extra_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def show_object(self, info: Dict[str, object]) -> None:
        """显示一个天体的信息字典（由 RealSkyView.describe_object 产生）。"""
        try:
            name = str(info.get("name") or info.get("name_en") or "未知天体")
            name_en = str(info.get("name_en") or "")
            kind = str(info.get("kind") or "star")
            self._title.setText(name)
            sub = KIND_CN.get(kind, "天体")
            if name_en and name_en != name:
                sub += f"　·　{name_en}"
            self._subtitle.setText(sub)

            # 徽章
            badges: List[Tuple[str, Sequence[int]]] = []
            if info.get("constellation"):
                badges.append((str(info["constellation"]), (0, 200, 230)))
            if info.get("spectral"):
                rgb = _spectral_rgb(str(info["spectral"]))
                badges.append((str(info["spectral"]), rgb))
            if kind == "planet":
                badges.append(("太阳系行星", (255, 190, 90)))
            elif kind == "messier":
                badges.append((str(info.get("mtype") or "深空"), (170, 255, 200)))
            self._set_badges(badges)

            # 基础行
            self._rows["type"].set_value(KIND_CN.get(kind, "--"))
            mag = info.get("mag")
            self._rows["mag"].set_value(fmt_float(mag, 2) if mag is not None else "--")
            ra = info.get("ra")
            dec = info.get("dec")
            self._rows["ra"].set_value(fmt_ra(float(ra)) if ra is not None else "--")
            self._rows["dec"].set_value(fmt_dec(float(dec)) if dec is not None else "--")
            alt = info.get("alt")
            az = info.get("az")
            if alt is not None and az is not None:
                self._rows["alt"].set_value(f"{float(alt):+.2f}°")
                self._rows["az"].set_value(f"{float(az):.2f}°  ({azimuth_to_compass(float(az))})")
            else:
                self._rows["alt"].set_value("--")
                self._rows["az"].set_value("--")
            self._rows["spectral"].set_value(str(info.get("spectral") or "--"))
            bv = info.get("bv")
            self._rows["bv"].set_value(fmt_float(bv, 3) if bv is not None else "--")
            self._rows["constellation"].set_value(str(info.get("constellation") or "--"))
            self._rows["hip"].set_value(str(info.get("hip") or "--"))

            # 升落信息：地平线以下的目标会显示「约几小时后升起」与未来最高高度角
            next_event = str(info.get("next_event") or "")
            above = info.get("above_horizon")
            if next_event:
                self._rows["rise"].set_value(next_event, "#8fffc8" if above else "#ffb37a")
                peak = info.get("peak_alt")
                self._rows["peak"].set_value(
                    f"{float(peak):+.1f}°" if peak is not None else "--")
            else:
                self._rows["rise"].set_value("--")
                self._rows["peak"].set_value("--")

            # 距离：恒星用光年，行星/日月用天文单位与公里
            if kind == "star":
                ly = info.get("distance_ly")
                self._rows["distance"].set_value(format_distance_ly(float(ly)) if ly else "未知")
            elif kind == "messier":
                self._rows["distance"].set_value("银河系内 / 河外天体")
            elif kind == "moon":
                km = info.get("distance_km")
                self._rows["distance"].set_value(f"{float(km):,.0f} 公里" if km else "--")
            elif info.get("distance_au") is not None:
                au = float(info["distance_au"])
                self._rows["distance"].set_value(f"{au:.4f} AU（{au * 149597870.7:,.0f} km）")
            else:
                self._rows["distance"].set_value("--")

            # 扩展区
            self._clear_extra()
            self._extra_desc.setVisible(False)
            self._model_view.setVisible(False)
            self._model_title.setVisible(False)
            if kind == "planet" and isinstance(info.get("planet"), dict):
                self._show_body_model(str(info["planet"].get("key") or ""),
                                      float(info["planet"].get("apparent_magnitude", 1.0) or 1.0))
                self._fill_planet(info["planet"])
            elif kind == "sun":
                self._show_body_model("sun", 1.0)
                self._add_extra("直径", "1,392,700 公里（约 109 个地球）")
                self._add_extra("表面温度", "约 5,500 ℃（核心 1,500 万 ℃）")
                self._add_extra("光谱型", "G2V")
                self._add_extra("与地球距离", "1 AU（光行 8 分 20 秒）")
            elif kind == "moon":
                phase = float(info.get("phase") or 0.5)
                self._show_body_model("moon", 1.0, phase)
                self._add_extra("月相", str(info.get("phase_name") or "--"))
                self._add_extra("照亮比例", f"{phase * 100:.1f}%")
                self._add_extra("平均距离", "384,400 公里（平均）")
                self._extra_desc.setVisible(True)
                self._extra_desc.setHtml(
                    "<span style='color:#bfe4f5'>月球是地球唯一的天然卫星，"
                    "潮汐锁定使其永远以同一面朝向地球。拖动上面的模型可查看表面环形山。</span>")
            elif kind == "star" and info.get("hip"):
                hip = int(info["hip"])  # type: ignore[arg-type]
                star = SD.get_star(hip)
                if star is not None:
                    self._add_extra("英文名", star.name_en or "--")
                    self._add_extra("颜色预览", "")
                    self._add_extra("坐标历元", "J2000.0（已做岁差修正）")
            elif kind == "messier":
                self._add_extra("天体类型", str(info.get("mtype") or "--"))
                self._add_extra("视大小", str(info.get("size") or "--"))
                desc = str(info.get("desc") or "")
                if desc:
                    self._extra_desc.setVisible(True)
                    self._extra_desc.setHtml(f"<span style='color:#a8d8ea'>{desc}</span>")
        except Exception:  # pragma: no cover - 防御性
            pass

    def _show_body_model(self, key: str, magnitude: float = 1.0,
                         phase_override: Optional[float] = None) -> None:
        """显示天体的精细模型视图。

        相位取真实月相（月亮）或满相（太阳与行星始终朝向我们的一面被照亮）。
        非太阳系天体（恒星、深空）不显示模型，因为无法用球面纹理表达。
        """
        if not key or key not in PLANET_SURFACE:
            return
        phase = 1.0 if phase_override is None else clamp(phase_override, 0.0, 1.0)
        if key == "sun":
            note = "米粒组织与黑子构成的等离子体表面"
        elif key == "moon":
            note = "月海、环形山与辐射纹（潮汐锁定，永远同一面朝向地球）"
        else:
            note = str(PLANET_SURFACE.get(key, {}).get("note", ""))
        self._model_title.setVisible(True)
        self._model_view.setVisible(True)
        self._model_view.set_body(key, phase, note)
        LOG.info(f"[精细模型] 已载入 {key}（相位 {phase:.2f}）")

    def _add_extra(self, label: str, value: str, color: Optional[str] = None) -> None:
        self._extra_layout.addWidget(InfoRow(label, value, color or "#dff4ff"))

    def _fill_planet(self, p: Dict[str, object]) -> None:
        """行星扩展信息：轨道/物理/亮度。"""
        self._add_extra("英文名", str(p.get("name_en") or "--"))
        self._add_extra("行星类别", str(p.get("type") or "--"))
        self._add_extra("赤道半径", f"{float(p.get('radius_km', 0)):,.0f} 公里")
        self._add_extra("质量", f"{float(p.get('mass_earth', 0)):.3f} 地球质量")
        self._add_extra("表面重力", f"{float(p.get('gravity', 0)):.2f} m/s²")
        self._add_extra("自转周期", f"{float(p.get('rotation_hours', 0)):,.2f} 小时")
        self._add_extra("已知卫星", f"{int(p.get('moons', 0))} 颗")
        self._add_extra("平均温度", f"{p.get('temperature_c', '--')} ℃")
        self._add_extra("大气成分", str(p.get("atmosphere") or "--"))
        self._add_extra("轨道半长轴", f"{float(p.get('heliocentric_distance_au', 0)):.4f} AU")
        self._add_extra("公转周期", f"{float(p.get('orbital_period_years', 0)):.3f} 地球年")
        self._add_extra("轨道偏心率", f"{float(p.get('eccentricity', 0)):.5f}")
        self._add_extra("轨道倾角", f"{float(p.get('inclination_deg', 0)):.3f}°")
        self._add_extra("近/远日点", f"{float(p.get('perihelion_au', 0)):.3f} / "
                                    f"{float(p.get('aphelion_au', 0)):.3f} AU")
        self._add_extra("平均轨道速度", f"{float(p.get('orbital_speed_kms', 0)):.2f} km/s")
        self._add_extra("与地球距离", f"{float(p.get('geocentric_distance_au', 0)):.4f} AU")
        self._add_extra("光行时间", f"{float(p.get('light_minutes', 0)):.2f} 分钟")
        note = str(p.get("note") or "")
        if note:
            self._extra_desc.setVisible(True)
            self._extra_desc.setHtml(f"<span style='color:#a8d8ea'>{note}</span>")


def _spectral_rgb(spectral: str) -> Tuple[int, int, int]:
    from 核心.utils import spectral_to_rgb
    return spectral_to_rgb(spectral)


# ==============================================================================
# 架空星图 · 恒星系信息页
# ==============================================================================

class FictionSystemPanel(QWidget):
    """架空星图的恒星系信息面板（7 个标签页）。"""

    route_from_here = Signal(int)
    route_to_here = Signal(int)
    locate_system = Signal(int)
    dispatch_requested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.galaxy: Optional[WG.Galaxy] = None
        self.sid: Optional[int] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        # 标题 + 操作按钮
        self._title = QLabel("未选择恒星系")
        self._title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        self._title.setStyleSheet("color:#c9a8ff;background:transparent;")
        self._subtitle = QLabel("在星图中点击恒星系查看详情（Shift+点击设置航线端点）")
        self._subtitle.setFont(QFont("Microsoft YaHei UI", 8))
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet("color:#8fa8c0;background:transparent;")
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self._btn_from = GlowButton("设为起点", "green", compact=True)
        self._btn_to = GlowButton("设为终点", "orange", compact=True)
        self._btn_locate = GlowButton("定位", "cyan", compact=True)
        self._btn_ship = GlowButton("派遣飞船", "violet", compact=True)
        self._btn_from.clicked.connect(lambda: self._emit_route(self.route_from_here))
        self._btn_to.clicked.connect(lambda: self._emit_route(self.route_to_here))
        self._btn_locate.clicked.connect(lambda: self._emit_route(self.locate_system))
        self._btn_ship.clicked.connect(lambda: self._emit_route(self.dispatch_requested))
        for b in (self._btn_from, self._btn_to, self._btn_locate, self._btn_ship):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        # 标签页
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setAttribute(Qt.WA_StyledBackground, True)
        self.tabs.setStyleSheet("""
            QTabWidget::pane{border:1px solid rgba(0,150,190,90);border-radius:3px;
                             background:rgba(5,14,22,120);}
            QTabBar::tab{background:rgba(8,20,30,200);color:#8fb8cc;padding:4px 7px;
                         margin-right:1px;border:1px solid rgba(0,150,190,70);
                         border-bottom:none;font-size:11px;}
            QTabBar::tab:selected{background:rgba(0,120,160,120);color:#e8fbff;
                                  border:1px solid rgba(0,220,255,180);}
            QTabBar::tab:hover{color:#d0f4ff;}
        """)
        self._pages: Dict[str, QWidget] = {}
        self._bodies: Dict[str, QTextBrowser] = {}
        for key, label in (("overview", "概览"), ("planets", "行星"), ("civilization", "文明"),
                           ("resource", "资源"), ("routes", "航线"), ("events", "事件"),
                           ("history", "历史")):
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(4, 4, 4, 4)
            pl.setSpacing(4)
            if key in ("routes", "events", "history"):
                browser = _text_block("")
                browser.setMinimumHeight(180)
                pl.addWidget(browser)
                self._bodies[key] = browser
            else:
                host = QWidget()
                host_layout = QVBoxLayout(host)
                host_layout.setContentsMargins(0, 0, 0, 0)
                host_layout.setSpacing(2)
                host.setObjectName(f"host_{key}")
                pl.addWidget(_scrollable(host))
            self._pages[key] = page
            self.tabs.addTab(page, label)
        layout.addWidget(self.tabs, 1)

    # ---- 外部接口 ----

    def set_galaxy(self, galaxy: Optional[WG.Galaxy]) -> None:
        self.galaxy = galaxy
        if galaxy is None:
            self.clear()

    def clear(self) -> None:
        self.sid = None
        self._title.setText("未选择恒星系")
        self._subtitle.setText("在星图中点击恒星系查看详情（Shift+点击设置航线端点）")
        for key in ("routes", "events", "history"):
            self._bodies[key].setHtml("")

    def _emit_route(self, signal) -> None:
        if self.sid is not None:
            signal.emit(int(self.sid))

    def show_system(self, sid: int) -> None:
        """展示指定恒星系的全部信息。"""
        g = self.galaxy
        if g is None:
            return
        s = g.system(sid)
        if s is None:
            return
        self.sid = int(sid)
        self._title.setText(s.name)
        self._subtitle.setText(
            f"光谱型 {s.spectral}　质量 {s.mass_solar:.2f} M☉　"
            f"势力：{s.faction or '无主之地'}")
        self._fill_overview(g, s)
        self._fill_planets(s)
        self._fill_civilization(g, s)
        self._fill_resource(g, s)
        self._fill_routes(g, s)
        self._fill_events(g, s)
        self._fill_history(g)

    # ---- 各标签页填充 ----

    def _host(self, key: str) -> QWidget:
        page = self._pages[key]
        area = page.findChild(QScrollArea)
        return area.widget() if area else page

    def _reset_host(self, key: str) -> QVBoxLayout:
        host = self._host(key)
        layout = host.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        return layout  # type: ignore[return-value]

    def _fill_overview(self, g: WG.Galaxy, s: WG.StarSystem) -> None:
        layout = self._reset_host("overview")
        pos = s.position
        r = (pos[0] ** 2 + pos[1] ** 2 + pos[2] ** 2) ** 0.5
        rows = [
            ("坐标 X", f"{pos[0]:,.1f} 光年"), ("坐标 Y", f"{pos[1]:,.1f} 光年"),
            ("坐标 Z", f"{pos[2]:,.1f} 光年"), ("距核心", format_distance_ly(r)),
            ("光谱型", s.spectral), ("恒星质量", f"{s.mass_solar:.3f} 太阳质量"),
            ("表面温度", f"{s.temperature_k:,.0f} K"),
            ("光度", f"{s.luminosity_solar:,.4f} 太阳光度"),
            ("恒星半径", f"{s.radius_solar:.3f} 太阳半径"),
            ("行星数量", f"{s.planet_count} 颗"),
            ("人口", f"{s.population_millions:,.2f} 百万"),
            ("资源丰富度", f"{s.resource_richness * 100:.1f}%"),
            ("危险度", f"{s.danger * 100:.1f}%"),
            ("所属势力", s.faction or "无主之地"),
            ("本土文明", s.civ_name or "无"),
            ("空间站", "有" if s.has_station else "无"),
            ("异常现象", s.anomaly or "无"),
        ]
        for label, value in rows:
            layout.addWidget(InfoRow(label, str(value)))
        layout.addWidget(SeparatorLine())
        layout.addWidget(_section("评估"))
        bar = StatBar("资源", (80, 230, 160))
        bar.set_value(s.resource_richness)
        layout.addWidget(bar)
        bar2 = StatBar("危险", (255, 110, 90))
        bar2.set_value(s.danger)
        layout.addWidget(bar2)
        bar3 = StatBar("宜居", (120, 200, 255))
        best = max((p.habitability for p in s.planets), default=0.0)
        bar3.set_value(best)
        layout.addWidget(bar3)
        if s.anomaly:
            desc = _text_block(f"<span style='color:#d7b0ff'>异常现象：{s.anomaly}。"
                               f"建议派遣科考舰前往调查。</span>")
            desc.setFixedHeight(64)
            layout.addWidget(desc)
        layout.addStretch(1)

    def _fill_planets(self, s: WG.StarSystem) -> None:
        layout = self._reset_host("planets")
        if not s.planets:
            layout.addWidget(QLabel("该恒星系没有行星。"))
            layout.addStretch(1)
            return
        for idx, pl in enumerate(s.planets):
            card = QFrame()
            card.setStyleSheet("QFrame{background:rgba(8,20,32,150);"
                               "border:1px solid rgba(0,140,180,80);border-radius:3px;}")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(6, 4, 6, 4)
            cl.setSpacing(2)
            header = QHBoxLayout()
            name = QLabel(pl.name)
            name.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
            name.setStyleSheet(f"color:{rgb_str(pl.color)};background:transparent;")
            header.addWidget(name)
            header.addStretch(1)
            badge_color = pl.color if pl.ptype != "relic" else (200, 140, 255)
            header.addWidget(Badge(pl.ptype_cn, badge_color))
            cl.addLayout(header)
            cl.addWidget(InfoRow("轨道", f"{pl.orbit_au:.3f} AU（{pl.period_days:,.1f} 天）"))
            cl.addWidget(InfoRow("半径", f"{pl.radius_earth:.3f} 地球半径"))
            cl.addWidget(InfoRow("重力", f"{pl.gravity_g:.2f} g"))
            cl.addWidget(InfoRow("温度", f"{pl.temperature_c:+.1f} ℃"))
            cl.addWidget(InfoRow("大气", f"{pl.atmosphere}（{pl.pressure_atm:.2f} atm）"))
            cl.addWidget(InfoRow("生命", pl.life_signs,
                                 "#8fffc8" if pl.life_signs != "无" else "#dff4ff"))
            if pl.civ_level > 0:
                cl.addWidget(InfoRow("文明等级", f"Lv.{pl.civ_level}"))
            cl.addWidget(InfoRow("资源", "、".join(pl.resources) or "无"))
            if pl.notes:
                note = QLabel(pl.notes)
                note.setWordWrap(True)
                note.setStyleSheet("color:#9fc4d8;background:transparent;")
                note.setFont(QFont("Microsoft YaHei UI", 8))
                cl.addWidget(note)
            layout.addWidget(card)
        layout.addStretch(1)

    def _fill_civilization(self, g: WG.Galaxy, s: WG.StarSystem) -> None:
        layout = self._reset_host("civilization")
        civs = [c for c in g.civilizations if c.home_system == s.name or s.name in c.colonies]
        if not civs:
            layout.addWidget(QLabel("该恒星系没有已知文明活动。"))
            layout.addStretch(1)
            return
        for civ in civs:
            card = QFrame()
            card.setStyleSheet("QFrame{background:rgba(18,10,32,150);"
                               "border:1px solid rgba(160,110,255,90);border-radius:3px;}")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(6, 4, 6, 4)
            cl.setSpacing(2)
            title = QLabel(f"{civ.name}　·　{civ.species}")
            title.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
            title.setStyleSheet("color:#d9c2ff;background:transparent;")
            cl.addWidget(title)
            cl.addWidget(InfoRow("科技等级", f"Lv.{civ.tech_level}　{civ.tech_name}"))
            cl.addWidget(InfoRow("政体", civ.government))
            cl.addWidget(InfoRow("意识形态", civ.ideology))
            cl.addWidget(InfoRow("母星", civ.home_system))
            cl.addWidget(InfoRow("殖民地", f"{len(civ.colonies)} 个"))
            if civ.colonies:
                cl.addWidget(InfoRow("殖民列表", "、".join(civ.colonies[:6]) +
                                    ("…" if len(civ.colonies) > 6 else "")))
            cl.addWidget(InfoRow("人口", f"{civ.population_millions:,.2f} 百万"))
            cl.addWidget(InfoRow("立国年份", f"银河历 {civ.founded_year:,.1f} 年"))
            cl.addWidget(InfoRow("种族特征", "、".join(civ.traits) or "未知"))
            layout.addWidget(card)
        layout.addStretch(1)

    def _fill_resource(self, g: WG.Galaxy, s: WG.StarSystem) -> None:
        layout = self._reset_host("resource")
        layout.addWidget(_section("本地资源"))
        for res in s.resources:
            layout.addWidget(Badge(res, (255, 210, 120)))
        layout.addWidget(SeparatorLine())
        layout.addWidget(_section("资源分布（本星区内最富集的 8 个星系）"))
        rich = sorted(g.systems, key=lambda x: -x.resource_richness)[:8]
        for i, other in enumerate(rich, 1):
            layout.addWidget(InfoRow(f"#{i} {other.name}",
                                     f"{other.resource_richness * 100:.0f}%　"
                                     f"距此 {format_distance_ly(g.distance(s.sid, other.sid))}"))
        layout.addWidget(SeparatorLine())
        layout.addWidget(_section("行星资源汇总"))
        collected: Dict[str, int] = {}
        for pl in s.planets:
            for r in pl.resources:
                collected[r] = collected.get(r, 0) + 1
        for name, count in sorted(collected.items(), key=lambda kv: -kv[1]):
            layout.addWidget(InfoRow(name, f"{count} 处"))
        layout.addStretch(1)

    def _fill_routes(self, g: WG.Galaxy, s: WG.StarSystem) -> None:
        rows: List[str] = []
        neighbors = g.neighbors(s.sid)
        rows.append(f"<b style='color:#8ff0ff'>直达航线 {len(neighbors)} 条</b><br>")
        for nid in sorted(neighbors, key=lambda x: g.distance(s.sid, x)):
            other = g.system(nid)
            route = g.route_between(s.sid, nid)
            if other is None or route is None:
                continue
            rows.append(
                f"<div style='margin-bottom:3px'>▶ <span style='color:#cfeaf5'>{other.name}</span> "
                f"<span style='color:#7fa8bb'>| {format_distance_ly(route.distance_ly)} | "
                f"{human_duration(route.travel_days * 86400.0)} | 燃料 {route.fuel_cost:.1f} | "
                f"风险 {route.risk * 100:.0f}% | {route.kind}</span></div>")
        # 本星系出发的飞船
        ships = [sh for sh in g.ships if s.sid in sh.route]
        if ships:
            rows.append("<br><b style='color:#c9a8ff'>相关飞船</b><br>")
            for sh in ships:
                state = "已抵达" if sh.arrived else ("已损毁" if sh.destroyed else
                                                     f"航行中 {sh.progress * 100:.0f}%")
                rows.append(f"<div>· {sh.name}（{sh.mission}） 状态：{state}　"
                            f"燃料 {sh.fuel:.0f}　舰体 {sh.hull:.0f}</div>")
        if not ships and not neighbors:
            rows.append("<span style='color:#8fa8c0'>该星系目前与外界没有航线连接。</span>")
        self._bodies["routes"].setHtml("".join(rows))

    def _fill_events(self, g: WG.Galaxy, s: WG.StarSystem) -> None:
        events = [e for e in g.history if e.system == s.name]
        if not events:
            self._bodies["events"].setHtml(
                "<span style='color:#8fa8c0'>该星系暂无重大历史事件记录。</span>")
            return
        rows = [f"<b style='color:#8ff0ff'>本星系事件（{len(events)} 条）</b><br>"]
        for e in sorted(events, key=lambda x: x.year):
            rows.append(f"<div style='margin-bottom:3px'>"
                        f"<span style='color:#ffd08a'>[银河历 {e.year:,.1f}]</span> "
                        f"<span style='color:#9fe8ff'>{e.category}</span> "
                        f"<span style='color:#cfeaf5'>{e.description}</span></div>")
        self._bodies["events"].setHtml("".join(rows))

    def _fill_history(self, g: WG.Galaxy) -> None:
        rows = [f"<b style='color:#8ff0ff'>{g.name} · 编年史（最近 60 条）</b><br>"]
        for e in g.history[-60:][::-1]:
            rows.append(f"<div style='margin-bottom:2px'>"
                        f"<span style='color:#ffd08a'>[{e.year:,.1f}]</span> "
                        f"<span style='color:#9fe8ff'>{e.category}</span> "
                        f"<span style='color:#cfeaf5'>{e.description}</span> "
                        f"<span style='color:#7fa8bb'>（{e.system}）</span></div>")
        if len(g.history) == 0:
            rows.append("<span style='color:#8fa8c0'>暂无历史事件。</span>")
        self._bodies["history"].setHtml("".join(rows))


# ==============================================================================
# 架空星图 · 宇宙总览页
# ==============================================================================

class GalaxyOverviewPanel(QWidget):
    """未选中恒星系时显示宇宙总览。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        self._title = QLabel("宇宙总览")
        self._title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        self._title.setStyleSheet("color:#c9a8ff;background:transparent;")
        self._body = _text_block("")
        layout.addWidget(self._title)
        layout.addWidget(self._body, 1)

    def set_galaxy(self, galaxy: Optional[WG.Galaxy]) -> None:
        if galaxy is None:
            self._body.setHtml("<span style='color:#8fa8c0'>尚未生成架空宇宙。</span>")
            return
        st = galaxy.stats()
        rows = [
            "<b style='color:#8ff0ff'>基本信息</b>",
            f"名称：{galaxy.name}　种子：{galaxy.seed}",
            f"规模：{WG.SCALE_LABELS_CN.get(galaxy.scale, galaxy.scale)}"
            f"（{st['systems']} 个恒星系）　分布：{WG.DISTRIBUTION_MODES.get(galaxy.distribution, '')}",
            f"当前时间：{format_gametime(galaxy.year)}　倍率：{galaxy.time_scale}x",
            "<br><b style='color:#8ff0ff'>统计</b>",
            f"行星总数：{st['planets']}　航线：{st['routes']}",
            f"文明：{st['civilizations']}　势力：{st['factions']}",
            f"历史事件：{st['events']}　在航飞船：{st['ships']}",
            f"总人口：{st['population_millions']:,.1f} 百万",
            f"平均资源：{st['avg_resource'] * 100:.1f}%　平均危险度：{st['avg_danger'] * 100:.1f}%",
            "<br><b style='color:#8ff0ff'>势力一览</b>",
        ]
        for f in galaxy.factions:
            rows.append(f"<span style='color:{rgb_str(f.color)}'>■</span> {f.name}"
                        f"　({f.ideology})　领土 {len(f.member_systems)} 星系　首都 {f.capital}")
        if galaxy.civilizations:
            rows.append("<br><b style='color:#8ff0ff'>文明一览</b>")
            for c in galaxy.civilizations:
                rows.append(f"· {c.name}（{c.species}）Lv.{c.tech_level} {c.tech_name}"
                            f"　母星 {c.home_system}")
        rows.append("<br><span style='color:#8fa8c0'>提示：按住 Shift 点击两个恒星系可设置航线起点与终点，"
                    "然后点击左侧「规划航线」。</span>")
        self._body.setHtml("<br>".join(rows))


# ==============================================================================
# 太阳邻域 · 近星信息页
# ==============================================================================

class NearbyObjectPanel(QWidget):
    """近星信息卡片（太阳邻域视图）。"""

    locate_on_sky = Signal(str)          # 请求在现实星图中定位该近星

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.star: Optional[NS.NearbyStar] = None

        self._inner = QWidget()
        layout = QVBoxLayout(self._inner)
        layout.setContentsMargins(8, 6, 8, 10)
        layout.setSpacing(6)

        self._title = QLabel("未选择近星")
        self._title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        self._title.setStyleSheet("color:#9ff6ff;background:transparent;")
        self._subtitle = QLabel("在「太阳邻域」视图中点击任意天体查看详情")
        self._subtitle.setFont(QFont("Microsoft YaHei UI", 9))
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet("color:#a9c9da;background:transparent;")
        self._badges = _badge_row([])
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        layout.addWidget(self._badges)

        self._model_title = _section("精细模型", "violet")
        self._model_view = BodyModelView(self)
        self._model_view.setVisible(False)
        layout.addWidget(self._model_title)
        layout.addWidget(self._model_view)

        layout.addWidget(SeparatorLine())
        self._rows: Dict[str, InfoRow] = {}
        host = QWidget()
        rows_layout = QVBoxLayout(host)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(1)
        for key, label in (("catalog", "编目编号"), ("kind", "天体类型"), ("spectral", "光谱型"),
                           ("distance", "距离"), ("mag", "视星等"), ("absmag", "绝对星等"),
                           ("bv", "B-V 色指数"), ("constellation", "所在星座"),
                           ("galactic", "银道坐标"), ("xyz", "赤道直角坐标"),
                           ("neighbors", "最近邻居"), ("note", "备注")):
            row = InfoRow(label)
            self._rows[key] = row
            rows_layout.addWidget(row)
        layout.addWidget(host)

        self._extra_desc = _text_block("")
        layout.addWidget(self._extra_desc)

        btn_row = QHBoxLayout()
        self.btn_locate = GlowButton("在星图中定位", "cyan", compact=True)
        self.btn_locate.clicked.connect(self._emit_locate)
        btn_row.addWidget(self.btn_locate)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(self._inner))

    def clear(self) -> None:
        self.star = None
        self._title.setText("未选择近星")
        self._subtitle.setText("在「太阳邻域」视图中点击任意天体查看详情")
        self._model_view.setVisible(False)
        self._model_title.setVisible(False)
        for row in self._rows.values():
            row.set_value("--")

    def _emit_locate(self) -> None:
        if self.star is not None:
            self.locate_on_sky.emit(self.star.display_name)

    def show_star(self, info: Dict[str, object]) -> None:
        """显示一颗近星的完整信息。"""
        try:
            key = str(info.get("key") or "")
            hit = NS.find_nearby(key.split(":", 1)[-1]) if key.startswith("nearby:") else None
            if hit is None:
                # 退回到按名称匹配
                hit = NS.find_nearby(str(info.get("name") or ""))
            if hit is None:
                return
            self.star = hit

            self._title.setText(hit.display_name)
            self._subtitle.setText(hit.sub_name or "距离太阳 50 光年内的已知天体")
            badges: List[Tuple[str, Sequence[int]]] = [(hit.kind, hit.kind_color)]
            if hit.spectral:
                badges.append((hit.spectral, hit.spectral_color))
            if hit.constellation_cn:
                badges.append((hit.constellation_cn, (0, 200, 230)))
            badges.append((f"{hit.distance_ly:.2f} 光年", (255, 210, 120)))
            self._set_badges(badges)

            self._rows["catalog"].set_value(hit.catalog_id)
            self._rows["kind"].set_value(hit.kind)
            self._rows["spectral"].set_value(hit.spectral or "未知")
            self._rows["distance"].set_value(
                f"{hit.distance_ly:.3f} 光年（{hit.distance_ly * 0.306601:.3f} 秒差距）")
            self._rows["mag"].set_value(f"{hit.mag:+.2f}")
            self._rows["absmag"].set_value(f"{hit.absmag:+.2f}")
            self._rows["bv"].set_value(f"{hit.bv:.3f}")
            self._rows["constellation"].set_value(hit.constellation_cn or "—")
            self._rows["galactic"].set_value(
                f"l = {hit.galactic_l:.2f}°　b = {hit.galactic_b:+.2f}°")
            self._rows["xyz"].set_value(
                f"({hit.x:+.2f}, {hit.y:+.2f}, {hit.z:+.2f}) 光年"
                f"　（X→(0h,0°)，Z→北天极）")
            # 最近邻居
            near = sorted((s for s in NS.NEARBY_CATALOG if s is not hit),
                          key=lambda s: hit.distance_to(s))[:3]
            self._rows["neighbors"].set_value(
                "、".join(f"{s.display_name}（{hit.distance_to(s):.2f} ly）" for s in near) or "—")
            self._rows["note"].set_value(_nearby_note(hit))

            # 若该近星在内置标注星表里有对应的行星模型，则展示（仅太阳系外的恒星无法建模，
            # 因此这里只在它是太阳系天体时才有意义——近星目录不含太阳系天体，故隐藏）
            self._model_view.setVisible(False)
            self._model_title.setVisible(False)

            light = hit.distance_ly  # 光年即光行年
            desc = (f"<span style='color:#bfe4f5'>这颗天体距离太阳 {hit.distance_ly:.2f} 光年，"
                    f"我们现在看到的是它 {light:.2f} 年前的样子。")
            if hit.absmag < 8:
                desc += " 它的绝对亮度较高，若置于天狼星的距离将极为醒目。"
            elif hit.absmag > 14:
                desc += " 它非常暗弱，属于红矮星/褐矮星的典型亮度。"
            desc += "</span>"
            self._extra_desc.setHtml(desc)
        except Exception as exc:      # pragma: no cover - 防御性
            LOG.warn(f"显示近星信息失败：{exc}")

    def _set_badges(self, items: Sequence[Tuple[str, Sequence[int]]]) -> None:
        layout = self._badges.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for text, color in items:
            layout.addWidget(Badge(text, color))
        layout.addStretch(1)


def _nearby_note(star: "NS.NearbyStar") -> str:
    """给近星生成一句有信息量的备注。"""
    notes: List[str] = []
    if star.distance_ly <= 5:
        notes.append("属于最近的恒星系统之一")
    if star.kind == "白矮星":
        notes.append("恒星演化末期的致密残骸")
    elif star.kind == "褐矮星":
        notes.append("质量不足以点燃稳定氢核聚变")
    elif star.kind == "巨星":
        notes.append("已离开主序、体积膨胀的晚年恒星")
    if star.absmag < 3:
        notes.append("本区域内的亮星")
    if not notes:
        notes.append("主序星")
    return "；".join(notes)


# ==============================================================================
# 组合面板
# ==============================================================================

class InfoPanel(QWidget):
    """右侧信息面板：根据模式自动切换内容。"""

    route_from_here = Signal(int)
    route_to_here = Signal(int)
    locate_system = Signal(int)
    dispatch_requested = Signal(int)
    locate_nearby_on_sky = Signal(str)          # 在现实星图中定位某颗近星

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setObjectName("InfoPanel")

        self._stack = QStackedWidget()
        self.sky_panel = SkyObjectPanel()
        self.system_panel = FictionSystemPanel()
        self.overview_panel = GalaxyOverviewPanel()
        self.nearby_panel = NearbyObjectPanel()

        self._fiction_stack = QStackedWidget()
        self._fiction_stack.addWidget(self.overview_panel)
        self._fiction_stack.addWidget(self.system_panel)

        self._stack.addWidget(self.sky_panel)          # index 0
        self._stack.addWidget(self._fiction_stack)     # index 1
        self._stack.addWidget(self.nearby_panel)       # index 2

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        # 转发子面板信号
        self.system_panel.route_from_here.connect(self.route_from_here)
        self.system_panel.route_to_here.connect(self.route_to_here)
        self.system_panel.locate_system.connect(self.locate_system)
        self.system_panel.dispatch_requested.connect(self.dispatch_requested)
        self.nearby_panel.locate_on_sky.connect(self.locate_nearby_on_sky)

    # ---- 模式切换 ----

    def show_real_mode(self) -> None:
        self._stack.setCurrentIndex(0)

    def show_nearby_mode(self) -> None:
        """切到太阳邻域信息页。"""
        self._stack.setCurrentIndex(2)

    def show_orrery_object(self, info: Dict[str, object]) -> None:
        """显示太阳系仪选中行星的详情（日心视角的补充数据）。"""
        try:
            self._stack.setCurrentIndex(0)
            payload = dict(info)
            payload.setdefault("panel", "sky")
            payload.setdefault("type_cn", "行星")
            # 复用天体贴片，但把"距离"改成日心距语义，避免与地心距混淆
            payload["distance_au"] = info.get("heliocentric_au")
            payload["orrery"] = True
            # 精细模型与行星参数页需要 planet_summary 字典
            key = str(info.get("key", "")).split(":")[-1]
            if key:
                try:
                    from 核心 import planet_data as _P
                    jd = A.julian_date(now_local())
                    summary = _P.planet_summary(key, jd)
                    if summary:
                        summary["apparent_magnitude"] = info.get("mag", 1.0)
                        payload["planet"] = summary
                except Exception as exc:
                    LOG.warn(f"太阳系仪行星参数构建失败：{exc}")
            self.sky_panel.show_object(payload)
            # 追加日心轨道信息
            rows = []
            period = info.get("period_years")
            speed = info.get("orbital_speed_kms")
            ecc = info.get("eccentricity")
            inc = info.get("inclination")
            if period:
                rows.append(f"公转周期 {float(period):.3f} 年")
            if speed:
                rows.append(f"平均轨道速度 {float(speed):.2f} km/s")
            if ecc is not None:
                rows.append(f"轨道偏心率 {float(ecc):.4f}")
            if inc is not None:
                rows.append(f"轨道倾角 {float(inc):.2f}°")
            lon = info.get("ecliptic_lon")
            lat = info.get("ecliptic_lat")
            if lon is not None and lat is not None:
                rows.append(f"当前日心黄经 {float(lon):.2f}°，黄纬 {float(lat):+.2f}°")
            if rows:
                block = _text_block(
                    "<b style='color:#9ff6ff'>日心轨道</b><br>"
                    + "<br>".join("· " + r for r in rows))
                self.sky_panel._extra_layout.addWidget(block)
        except Exception as exc:
            LOG.warn(f"显示太阳系仪行星详情失败：{exc}")

    def show_nearby_star(self, info: Dict[str, object]) -> None:
        """显示一颗近星的详情。"""
        self._stack.setCurrentIndex(2)
        self.nearby_panel.show_star(info)

    def show_fiction_mode(self, galaxy: Optional[WG.Galaxy] = None) -> None:
        self._stack.setCurrentIndex(1)
        if galaxy is not None:
            self.system_panel.set_galaxy(galaxy)
            self.overview_panel.set_galaxy(galaxy)
        self._fiction_stack.setCurrentWidget(self.overview_panel)

    # ---- 内容更新 ----

    def show_sky_object(self, info: Dict[str, object]) -> None:
        self._stack.setCurrentIndex(0)
        self.sky_panel.show_object(info)

    def show_system(self, galaxy: WG.Galaxy, sid: int) -> None:
        self.system_panel.set_galaxy(galaxy)
        self.system_panel.show_system(sid)
        self._fiction_stack.setCurrentWidget(self.system_panel)

    def set_galaxy(self, galaxy: Optional[WG.Galaxy]) -> None:
        self.system_panel.set_galaxy(galaxy)
        self.overview_panel.set_galaxy(galaxy)
        if galaxy is None:
            self._fiction_stack.setCurrentWidget(self.overview_panel)

    def clear_selection(self) -> None:
        self.sky_panel.clear()
        self.system_panel.clear()
        self._fiction_stack.setCurrentWidget(self.overview_panel)


__all__ = ["InfoPanel", "SkyObjectPanel", "FictionSystemPanel", "GalaxyOverviewPanel",
           "NearbyObjectPanel"]
