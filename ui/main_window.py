# -*- coding: utf-8 -*-
"""
main_window.py —— 主窗口
================================================================================
把「现实星图 / 太阳系仪 / 太阳邻域 / 架空星图」四种模式、左右控制面板、
菜单工具栏、时间驱动、存档与各种工具对话框组装成一个完整的桌面应用。

结构：
    一、界面搭建      三栏布局（左控制台 / 中间视图栈 / 右信息面板）
    二、菜单与工具栏  文件 / 视图 / 工具 / 帮助
    三、信号连接
    四、定时器        时钟（1s）与慢速刷新
    五、模式切换      四模式 + 状态栏
    六、现实星图交互  时间、图层、搜索、观测清单、光害
    七、观测点        手动输入 / IP 自动定位
    八、架空星图      世界生成、航线、飞船、存档
    九、工具面板      天象日历 / 时间旅行 / 历史推演 / 导出
    十、太阳邻域与辅助
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QDateTime, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QIcon, QKeySequence,
                           QPalette)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDateTimeEdit, QDialog,
                               QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox,
                               QSplitter, QStackedWidget, QStatusBar, QToolBar, QVBoxLayout,
                               QWidget)

from 核心 import astronomy as A
from 核心 import cities as CT
from 核心 import light_pollution as LP
from 核心 import nearby_stars as NS
from 核心 import planet_data as P
from 核心 import sky_events as SE
from 核心 import star_data as SD
from 核心 import world_generator as WG
from 核心.deepseek_client import DeepSeekConfig
from 核心.utils import (APP_NAME, APP_VERSION, DEFAULT_LAT, DEFAULT_LOCATION_NAME,
                        DEFAULT_LON, LOG, asset_path, clamp, format_datetime,
                        format_gametime, human_duration, list_json_files, load_json,
                        now_local, rgb_str, save_json, saves_dir, timestamp_tag)
from .deepseek_panel import DeepSeekSettingsDialog, NovelWriterPanel
from .export_panel import ExportDialog
from .fiction_view import MAP_MODES, FictionView
from .history_panel import HistorySimDialog
from .info_panel import InfoPanel
from .inverse_sky_panel import InverseSkyDialog
from .nearby_view import NearbyView
from .object_browser import ObjectBrowserDialog
from .orrery_view import OrreryView
from .planner_panel import PlannerDialog
from .real_sky_view import FOV_MAX, RealSkyView
from .sky_calendar import (HORIZON_FILE, HorizonDialog, SkyCalendarDialog,
                           TimeTravelDialog, load_horizon_profile, save_horizon_profile)
from .sonify_panel import SonifyPanel, StarMusicController
from .tour_panel import TourPanel
from .widgets import (CollapsiblePanel, FpsLabel, GlowButton, HologramLoader,
                      IconToggleButton, ModeSwitch, NeonSlider, ScanlineOverlay,
                      SearchBox, SectionTitle, SeparatorLine)

#: 图层中文名（左侧复选框）
LAYER_LABELS: Dict[str, str] = {
    "constellation_lines": "星座连线",
    "constellation_names": "星座名称",
    "star_names": "恒星名称",
    "planets": "行星",
    "sun_moon": "太阳与月亮",
    "milky_way": "银河带",
    "horizon_grid": "地平网格",
    "azimuth_labels": "方位刻度",
    "messier": "深空天体",
    "deep_sky_labels": "深空天体名称",
    "background_stars": "背景暗星",
    "below_horizon": "地平线下天体（查看未升起目标）",
    "nearby_mark": "圈出 50 光年内的近星",
}


class WorldGenWorker(QThread):
    """后台生成架空宇宙（避免界面卡死）。"""

    progressed = Signal(int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, seed: Optional[int], scale: str, distribution: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.seed = seed
        self.scale = scale
        self.distribution = distribution

    def run(self) -> None:
        try:
            gen = WG.WorldGenerator(self.seed, self.scale, self.distribution)
            galaxy = gen.generate(
                progress=lambda p, m: self.progressed.emit(int(p), str(m)))
            self.finished_ok.emit(galaxy)
        except Exception as exc:
            LOG.error("宇宙生成失败", exc)
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    """STELLAR NEXUS 主窗口。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME}　v{APP_VERSION}")
        # ★ 初始窗口必须**放得进屏幕**。
        #   原来写死 resize(1560, 940) + setMinimumSize(1366, 768)，在 1536×960
        #   这类紧凑屏（或 1366×768 笔记本）上会超出工作区：标题栏被顶到屏幕外、
        #   底部状态栏看不见，用户还得手动拉一下。这里按可用屏幕收缩一次，
        #   但仍然不小于 1100×700（再小三栏布局就挤了）。
        try:
            scr = QApplication.primaryScreen()
            avail = scr.availableGeometry() if scr is not None else None
        except Exception:
            avail = None
        if avail is not None and avail.width() > 200 and avail.height() > 200:
            self.resize(max(1100, min(1560, avail.width() - 60)),
                        max(700, min(940, avail.height() - 80)))
        else:
            self.resize(1560, 940)
        self.setMinimumSize(1100, 700)

        self.mode = "real"
        self.galaxy: Optional[WG.Galaxy] = None
        self._gen_worker: Optional[WorldGenWorker] = None
        self._novel_window: Optional[NovelWriterPanel] = None
        self._layers_state: Dict[str, bool] = dict(RealSkyView.DEFAULT_LAYERS)
        self._fiction_layers: Dict[str, bool] = {
            "routes": True, "labels": True, "grid": True, "ships": True, "halos": True}
        self._setup_in_progress = False
        self.speed_multiplier = 60.0
        self.fiction_speed = 5.0

        # 地平障碍剖面（本地星空模拟器）：先建好，供左侧面板与视图共用
        self.horizon_profile = load_horizon_profile()

        self._build_ui()
        self._build_menus()
        self._connect_signals()
        self._start_timers()

        # 启动后异步定位 + 首次刷新
        QTimer.singleShot(120, self._initial_locate)
        LOG.info(f"{APP_NAME} 启动完成（Python {sys.version.split()[0]}）")
        self._set_status("就绪　·　拖动旋转 / 滚轮缩放 / 右键平移 / 点击天体查看详情 / "
                         "Shift+点击设置航线端点")

    # ==========================================================================
    # 一、界面搭建
    # ==========================================================================

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        self.toolbar = QToolBar("主工具栏")
        self.toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar)
        self._build_toolbar()

        # ---- 主体三栏 ----
        self.splitter = QSplitter(Qt.Horizontal)
        # 星空导览面板：必须早于左面板构建（现实星图页里会引用它）
        self.tour_panel = TourPanel()
        self.left_panel = self._build_left_panel()
        self.view_stack = QStackedWidget()

        self.real_view = RealSkyView()
        self.orrery_view = OrreryView()
        self.nearby_view = NearbyView()
        self.fiction_view = FictionView()
        self.real_view.set_horizon_profile(self.horizon_profile)
        self._scan_real = ScanlineOverlay(self.real_view)
        self._scan_orrery = ScanlineOverlay(self.orrery_view)
        self._scan_nearby = ScanlineOverlay(self.nearby_view)
        self._scan_fiction = ScanlineOverlay(self.fiction_view)

        self.view_stack.addWidget(self.real_view)      # 0
        self.view_stack.addWidget(self.orrery_view)    # 1
        self.view_stack.addWidget(self.nearby_view)    # 2
        self.view_stack.addWidget(self.fiction_view)   # 3

        self.info_panel = InfoPanel()
        self.info_container = self._wrap_collapsible(self.info_panel, "right")

        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.view_stack)
        self.splitter.addWidget(self.info_container)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([330, 880, 350])
        outer.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        # ---- 状态栏 ----
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet("color:#cfeaf7;background:transparent;")
        self.status.addWidget(self._status_label, 1)
        self.fps_label = FpsLabel(self.real_view)
        self.status.addPermanentWidget(self.fps_label)
        self.clock_label = QLabel("")
        self.clock_label.setStyleSheet("color:#8fd4ea;background:transparent;")
        self.status.addPermanentWidget(self.clock_label)
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color:#8fffc8;background:transparent;")
        self.status.addPermanentWidget(self.stats_label)

    def _build_toolbar(self) -> None:
        tb = self.toolbar
        self.mode_switch = ModeSwitch()
        tb.addWidget(self.mode_switch)
        tb.addSeparator()
        tb.addWidget(self._tb_label("视场"))
        self.zoom_slider = NeonSlider(Qt.Horizontal, "cyan")
        self.zoom_slider.setRange(12, int(FOV_MAX))
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(130)
        tb.addWidget(self.zoom_slider)
        tb.addSeparator()
        tb.addWidget(self._tb_label("时间"))
        self.time_slider = NeonSlider(Qt.Horizontal, "violet")
        self.time_slider.setRange(-720, 720)
        self.time_slider.setValue(0)
        self.time_slider.setFixedWidth(190)
        self.time_slider.setToolTip("相对当前时间的小时偏移（±720 小时）")
        tb.addWidget(self.time_slider)
        self.btn_play = IconToggleButton("▶ 播放", "green")
        self.btn_play.setCheckable(True)
        tb.addWidget(self.btn_play)
        self.speed_buttons: List[IconToggleButton] = []
        for label, mult in (("1x", 1.0), ("60x", 60.0), ("600x", 600.0), ("3600x", 3600.0)):
            btn = IconToggleButton(label, "cyan")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, m=mult: self._on_speed_clicked(m))
            self.speed_buttons.append(btn)
            tb.addWidget(btn)
        self.speed_buttons[1].setChecked(True)
        tb.addSeparator()
        self.btn_home_novel = GlowButton("历史小说", "violet", compact=True)
        self.btn_home_novel.clicked.connect(self.open_novel_panel)
        tb.addWidget(self.btn_home_novel)
        self.btn_browser = GlowButton("天体总览", "cyan", compact=True)
        self.btn_browser.clicked.connect(self.open_object_browser)
        tb.addWidget(self.btn_browser)
        self.btn_night = IconToggleButton("红光模式", "red")
        self.btn_night.setCheckable(True)
        tb.addWidget(self.btn_night)
        self.btn_fullscreen = GlowButton("全屏", "cyan", compact=True)
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        tb.addWidget(self.btn_fullscreen)
        self.btn_reset = GlowButton("重置视角", "violet", compact=True)
        self.btn_reset.clicked.connect(self._reset_view)
        tb.addWidget(self.btn_reset)

    def _tb_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color:#8fd4ea;background:transparent;padding:0 4px;")
        label.setFont(QFont("Microsoft YaHei UI", 9))
        return label

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("LeftPanel")
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(470)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)
        title = QLabel("控制台 / CONSOLE")
        title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        title.setStyleSheet("color:#7ff0ff;background:transparent;letter-spacing:2px;")
        outer.addWidget(title)
        outer.addWidget(SeparatorLine())

        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self._build_real_controls())     # 0
        self.left_stack.addWidget(self._build_orrery_controls())   # 1
        self.left_stack.addWidget(self._build_nearby_controls())   # 2
        self.left_stack.addWidget(self._build_fiction_controls())  # 3
        outer.addWidget(self.left_stack, 1)
        return panel

    # ------------------------------------------------------------------ 太阳系仪

    def _build_orrery_controls(self) -> QWidget:
        """左侧面板：太阳系仪（俯视黄道面看行星轨道）。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        info = CollapsiblePanel("太阳系仪", "cyan")
        il = QVBoxLayout(info.content)
        il.setContentsMargins(6, 0, 0, 0)
        il.setSpacing(3)
        hint = QLabel("俯视黄道面观察行星轨道与当前位置。配合时间倍率就是一台"
                      "「行星时钟」：把时间拉快，可以看到行星在轨道上追赶、"
                      "会合与冲日。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#e6f6fd;background:transparent;")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        il.addWidget(hint)
        layout.addWidget(info)

        view = CollapsiblePanel("视野", "green")
        vl = QVBoxLayout(view.content)
        vl.setContentsMargins(6, 0, 0, 0)
        vl.setSpacing(4)
        self.chk_orrery_outer = QCheckBox("包含外行星（到冥王星）")
        self.chk_orrery_outer.setChecked(True)
        self.chk_orrery_outer.stateChanged.connect(
            lambda s: self.orrery_view.set_outer_limit(bool(s)))
        vl.addWidget(self.chk_orrery_outer)
        self.chk_orrery_true = QCheckBox("真实比例（关＝压缩，便于同看内行星）")
        self.chk_orrery_true.stateChanged.connect(
            lambda s: self.orrery_view.set_scale_mode("true" if s else "compressed"))
        vl.addWidget(self.chk_orrery_true)
        self.orrery_zoom = NeonSlider(Qt.Horizontal, "cyan")
        self.orrery_zoom.setRange(20, 2000)
        self.orrery_zoom.setValue(100)
        self.orrery_zoom.valueChanged.connect(self._on_orrery_zoom)
        vl.addWidget(QLabel("缩放"))
        vl.addWidget(self.orrery_zoom)
        btn_reset = GlowButton("重置视角", "violet", compact=True)
        btn_reset.clicked.connect(lambda: self.orrery_view.reset_view())
        vl.addWidget(btn_reset)
        layout.addWidget(view)

        layer = CollapsiblePanel("图层", "orange")
        ll = QVBoxLayout(layer.content)
        ll.setContentsMargins(6, 0, 0, 0)
        ll.setSpacing(2)
        for key, text, default in (("orbits", "行星轨道", True),
                                   ("links", "行星-太阳连线", True),
                                   ("labels", "行星名称与日心距", True),
                                   ("grid", "轨道距离标尺", True)):
            box = QCheckBox(text)
            box.setChecked(default)
            box.stateChanged.connect(
                lambda s, k=key: self.orrery_view.set_layer(k, bool(s)))
            ll.addWidget(box)
        layout.addWidget(layer)

        time_panel = CollapsiblePanel("行星时钟", "violet")
        tl = QVBoxLayout(time_panel.content)
        tl.setContentsMargins(6, 0, 0, 0)
        tl.setSpacing(3)
        for label, mult in (("1 天 / 秒", 86400.0), ("10 天 / 秒", 864000.0),
                            ("100 天 / 秒", 8640000.0), ("1 年 / 秒", 31557600.0)):
            btn = GlowButton(label, "violet", compact=True)
            btn.clicked.connect(lambda _c=False, m=mult: self._set_orrery_speed(m))
            tl.addWidget(btn)
        thint = QLabel("点击即可让行星在轨道上跑起来（再点一次停止）")
        thint.setWordWrap(True)
        thint.setStyleSheet("color:#a9c9da;background:transparent;")
        thint.setFont(QFont("Microsoft YaHei UI", 9))
        tl.addWidget(thint)
        layout.addWidget(time_panel)

        layout.addStretch(1)
        return self._wrap_scroll(page)

    # ------------------------------------------------------------------ 太阳邻域

    def _build_nearby_controls(self) -> QWidget:
        """左侧面板：太阳邻域（50 光年内已知天体）的显示控制。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ===== 数据概览 =====
        info_panel = CollapsiblePanel("数据概览", "cyan")
        il = QVBoxLayout(info_panel.content)
        il.setContentsMargins(6, 0, 0, 0)
        il.setSpacing(3)
        stats = NS.nearby_stats()
        self._nearby_stats_label = QLabel("")
        self._nearby_stats_label.setWordWrap(True)
        self._nearby_stats_label.setStyleSheet("color:#e6f6fd;background:transparent;")
        self._nearby_stats_label.setFont(QFont("Microsoft YaHei UI", 9))
        self._nearby_stats_label.setText(
            f"<b>太阳 50 光年内的已知天体</b><br>"
            f"共 <b style='color:#9ff6ff'>{stats['count']}</b> 个"
            f"（其中 {stats['named']} 个有专名或 Gliese 编号）<br><br>"
            + "<br>".join(f"　{k}：{v}" for k, v in sorted(
                stats['kinds'].items(), key=lambda kv: -kv[1]))
            + "<br><br>" + "<br>".join(f"　{k}：{v}" for k, v in stats['bands'].items())
            + f"<br><br><span style='color:#a9c9da'>数据来源：{stats['source']}</span>")
        il.addWidget(self._nearby_stats_label)
        layout.addWidget(info_panel)

        # ===== 显示范围 =====
        range_panel = CollapsiblePanel("显示范围", "green")
        rl = QVBoxLayout(range_panel.content)
        rl.setContentsMargins(6, 0, 0, 0)
        rl.setSpacing(4)
        self.nearby_limit_label = QLabel(f"显示半径：{NS.LIMIT_LY:.0f} 光年（全部）")
        self.nearby_limit_label.setStyleSheet("color:#a9c9da;background:transparent;")
        self.nearby_limit_label.setFont(QFont("Microsoft YaHei UI", 9))
        self.nearby_limit = NeonSlider(Qt.Horizontal, "green")
        self.nearby_limit.setRange(5, int(NS.LIMIT_LY))
        self.nearby_limit.setValue(int(NS.LIMIT_LY))
        self.nearby_limit.valueChanged.connect(self._on_nearby_limit)
        rl.addWidget(self.nearby_limit_label)
        rl.addWidget(self.nearby_limit)
        self.nearby_kind = QComboBox()
        for key, label in NS.KIND_OPTIONS:
            self.nearby_kind.addItem(label, key)
        self.nearby_kind.currentIndexChanged.connect(
            lambda _i: self.nearby_view.set_kind_filter(
                str(self.nearby_kind.currentData() or "all")))
        rl.addWidget(QLabel("天体类型"))
        rl.addWidget(self.nearby_kind)
        layout.addWidget(range_panel)

        # ===== 着色模式 =====
        color_panel = CollapsiblePanel("着色模式", "violet")
        cl = QVBoxLayout(color_panel.content)
        cl.setContentsMargins(6, 0, 0, 0)
        cl.setSpacing(3)
        self._nearby_color_buttons: Dict[str, IconToggleButton] = {}
        for key, label in NS.COLOR_MODES:
            btn = IconToggleButton(label, "violet")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, k=key: self._on_nearby_color(k))
            self._nearby_color_buttons[key] = btn
            cl.addWidget(btn)
        self._nearby_color_buttons["spectral"].setChecked(True)
        layout.addWidget(color_panel)

        # ===== 图层 =====
        layer_panel = CollapsiblePanel("图层", "orange")
        ll = QVBoxLayout(layer_panel.content)
        ll.setContentsMargins(6, 0, 0, 0)
        ll.setSpacing(2)
        self._nearby_layer_boxes: Dict[str, QCheckBox] = {}
        for key, label, default in (("shells", "距离球壳（5~50 光年）", True),
                                    ("grid", "银道面网格", True),
                                    ("labels", "天体名称", True),
                                    ("all_labels", "显示全部名称（密集）", False),
                                    ("sun", "太阳标记", True)):
            box = QCheckBox(label)
            box.setChecked(default)
            box.stateChanged.connect(lambda state, k=key: self._on_nearby_layer(k, state))
            self._nearby_layer_boxes[key] = box
            ll.addWidget(box)
        layout.addWidget(layer_panel)

        # ===== 视图 =====
        view_panel = CollapsiblePanel("视图", "cyan")
        vl = QVBoxLayout(view_panel.content)
        vl.setContentsMargins(6, 0, 0, 0)
        vl.setSpacing(4)
        self.nearby_zoom = NeonSlider(Qt.Horizontal, "cyan")
        self.nearby_zoom.setRange(25, 1400)         # 0.25x ~ 14x
        self.nearby_zoom.setValue(100)
        self.nearby_zoom.setToolTip("缩放（0.25x ~ 14x）")
        self.nearby_zoom.valueChanged.connect(self._on_nearby_zoom)
        vl.addWidget(QLabel("缩放"))
        vl.addWidget(self.nearby_zoom)
        row = QHBoxLayout()
        self.btn_nearby_reset = GlowButton("重置视角", "violet", compact=True)
        self.btn_nearby_reset.clicked.connect(lambda: self.nearby_view.reset_view())
        self.btn_nearby_focus = GlowButton("聚焦选中", "cyan", compact=True)
        self.btn_nearby_focus.clicked.connect(self._focus_selected_nearby)
        row.addWidget(self.btn_nearby_reset)
        row.addWidget(self.btn_nearby_focus)
        vl.addLayout(row)
        # ★ 补一个「选中最近的星」。原来只能靠鼠标精确点中星点（命中半径 12 px），
        #   而画面正中往往刚好没有星（实测默认视角下中心无星）——于是"选一颗星
        #   再看它的天空"这条主线很容易卡在第一步，外星视角只能退回默认比邻星。
        #   现在给一个不依赖鼠标精度的入口：取离视野中心最近的星（48 px 内）。
        self.btn_nearby_nearest = GlowButton("选中最近的星", "green", compact=True)
        self.btn_nearby_nearest.setToolTip(
            "选中离视野中心最近的一颗近星（无需精确点中星点）\n"
            "选中后可用「工具 → 🔭 外星视角」从它看天空")
        self.btn_nearby_nearest.clicked.connect(self._select_nearest_nearby)
        vl.addWidget(self.btn_nearby_nearest)
        self.chk_nearby_night = QCheckBox("红光夜间模式")
        self.chk_nearby_night.stateChanged.connect(lambda s: self._set_night_mode(bool(s)))
        vl.addWidget(self.chk_nearby_night)
        layout.addWidget(view_panel)

        # ===== 搜索 =====
        search_panel = CollapsiblePanel("搜索近星", "green")
        sl = QVBoxLayout(search_panel.content)
        sl.setContentsMargins(6, 0, 0, 0)
        sl.setSpacing(4)
        self.nearby_search = SearchBox("比邻星 / Sirius / HIP 87937 / Gliese 699")
        self.nearby_search.returnPressed.connect(self._on_nearby_search)
        btn = GlowButton("定位并选中", "green", compact=True)
        btn.clicked.connect(self._on_nearby_search)
        sl.addWidget(self.nearby_search)
        sl.addWidget(btn)
        hint = QLabel("支持中文名、专名、HIP 编号与 Gliese 编号")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#a9c9da;background:transparent;")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        sl.addWidget(hint)
        layout.addWidget(search_panel)

        layout.addStretch(1)
        return self._wrap_scroll(page)

    # ------------------------------------------------------------------ 深色滚动容器

    def _wrap_scroll(self, page: QWidget) -> QScrollArea:
        """把面板内容包进深色滚动区域（视口显式填深色，避免系统浅色调色板透出）。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        try:
            vp = scroll.viewport()
            if vp is not None:
                vp.setAutoFillBackground(True)
                pal = vp.palette()
                pal.setColor(QPalette.Window, QColor("#061019"))
                pal.setColor(QPalette.Base, QColor("#061019"))
                vp.setPalette(pal)
        except Exception:
            pass
        page.setAttribute(Qt.WA_StyledBackground, True)
        page.setStyleSheet("background:transparent;")
        scroll.setStyleSheet("QScrollArea{background:transparent;}"
                             "QScrollBar:vertical{background:rgba(6,16,26,150);width:8px;}"
                             "QScrollBar::handle:vertical{background:rgba(0,180,220,150);"
                             "border-radius:4px;min-height:20px;}"
                             "QScrollBar::add-line:vertical,"
                             "QScrollBar::sub-line:vertical{height:0;}")
        return scroll

    # ------------------------------------------------------------------ 现实星图

    def _build_real_controls(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ===== 观测时间 =====
        time_panel = CollapsiblePanel("观测时间", "cyan")
        tl = QVBoxLayout(time_panel.content)
        tl.setContentsMargins(6, 0, 0, 0)
        tl.setSpacing(4)
        self.time_offset_label = QLabel("偏移 +0.0 小时")
        self.time_offset_label.setStyleSheet("color:#a9c9da;background:transparent;")
        self.time_offset_label.setFont(QFont("Microsoft YaHei UI", 9))
        tl.addWidget(self.time_offset_label)
        self.dt_edit = QDateTimeEdit()
        self.dt_edit.setCalendarPopup(True)
        self.dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_edit.setDateTime(QDateTime(now_local()))
        self.dt_edit.setMinimumHeight(28)
        tl.addWidget(QLabel("精确时间"))
        tl.addWidget(self.dt_edit)
        self.btn_now = GlowButton("回到现在", "green", compact=True)
        self.btn_now.clicked.connect(self._on_now_clicked)
        tl.addWidget(self.btn_now)
        layout.addWidget(time_panel)

        # ===== 观测位置 =====
        loc_panel = CollapsiblePanel("观测位置", "green", expanded=True)
        ll = QVBoxLayout(loc_panel.content)
        ll.setContentsMargins(6, 0, 0, 0)
        ll.setSpacing(5)

        # ---- 城市检索（离线 GeoNames，69000 个城市） ----
        ll.addWidget(SectionTitle("① 按城市检索", "green"))
        city_row = QHBoxLayout()
        self.city_search = SearchBox("北京 / Beijing / 纽约 / Reykjavik / 澳大利亚")
        self.city_search.returnPressed.connect(self._on_city_search)
        city_row.addWidget(self.city_search, 1)
        btn_city = GlowButton("搜索", "green", compact=True)
        btn_city.setFixedWidth(52)
        btn_city.clicked.connect(self._on_city_search)
        city_row.addWidget(btn_city)
        ll.addLayout(city_row)
        self.city_results = QListWidget()
        self.city_results.setMaximumHeight(132)
        self.city_results.setStyleSheet(
            "QListWidget{background:rgba(6,18,28,225);border:1px solid rgba(0,170,210,140);"
            "border-radius:3px;color:#e6f6fd;}"
            "QListWidget::item{padding:2px 4px;}"
            "QListWidget::item:selected{background:rgba(0,190,230,150);color:#02121a;}")
        self.city_results.itemClicked.connect(self._on_city_picked)
        self.city_results.itemDoubleClicked.connect(
            lambda item: self._apply_location())
        ll.addWidget(self.city_results)
        self.city_hint = QLabel(f"离线库共 {CT.city_count()} 个城市；"
                                "支持中文名、英文名、拼音与国家名。单击选中即填入下方坐标。")
        self.city_hint.setWordWrap(True)
        self.city_hint.setStyleSheet("color:#a9c9da;background:transparent;")
        self.city_hint.setFont(QFont("Microsoft YaHei UI", 8))
        ll.addWidget(self.city_hint)

        ll.addWidget(SeparatorLine("green"))

        # ---- 手动坐标：数值 + 半球 / 东西分离 ----
        ll.addWidget(SectionTitle("② 手动输入坐标", "green"))
        ll.addWidget(QLabel("地点名称"))
        self.loc_name = QLineEdit(DEFAULT_LOCATION_NAME)
        ll.addWidget(self.loc_name)

        # 纬度：数值 + 南北
        lat_row = QHBoxLayout()
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(0.0, 90.0)          # 只输数值，符号由下面的选择决定
        self.lat_spin.setDecimals(4)
        self.lat_spin.setValue(abs(DEFAULT_LAT))
        self.lat_spin.setSuffix(" °")
        lat_row.addWidget(self.lat_spin, 2)
        self.chk_north = QCheckBox("北纬 N")
        self.chk_north.setChecked(DEFAULT_LAT >= 0)
        self.chk_north.setToolTip("勾选＝北纬（北半球），取消＝南纬（南半球）")
        self.chk_north.stateChanged.connect(lambda _s: self._refresh_coord_preview())
        lat_row.addWidget(self.chk_north, 1)
        ll.addWidget(QLabel("纬度数值"))
        ll.addLayout(lat_row)

        # 经度：数值 + 东西
        lon_row = QHBoxLayout()
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(0.0, 180.0)
        self.lon_spin.setDecimals(4)
        self.lon_spin.setValue(abs(DEFAULT_LON))
        self.lon_spin.setSuffix(" °")
        lon_row.addWidget(self.lon_spin, 2)
        self.chk_east = QCheckBox("东经 E")
        self.chk_east.setChecked(DEFAULT_LON >= 0)
        self.chk_east.setToolTip("勾选＝东经，取消＝西经")
        self.chk_east.stateChanged.connect(lambda _s: self._refresh_coord_preview())
        lon_row.addWidget(self.chk_east, 1)
        ll.addWidget(QLabel("经度数值"))
        ll.addLayout(lon_row)

        self.lat_spin.valueChanged.connect(lambda _v: self._refresh_coord_preview())
        self.lon_spin.valueChanged.connect(lambda _v: self._refresh_coord_preview())

        # 实时预览：把「数值+半球」翻译成带符号坐标 + 半球说明
        self.coord_preview = QLabel("")
        self.coord_preview.setWordWrap(True)
        self.coord_preview.setStyleSheet(
            "color:#8fffc8;background:rgba(6,18,28,200);"
            "border:1px solid rgba(0,170,210,120);border-radius:3px;padding:4px;")
        self.coord_preview.setFont(QFont("Microsoft YaHei UI", 9))
        ll.addWidget(self.coord_preview)

        ll.addWidget(QLabel("时区（UTC 偏移）"))
        self.tz_spin = QDoubleSpinBox()
        self.tz_spin.setRange(-12.0, 14.0)
        self.tz_spin.setDecimals(1)
        self.tz_spin.setValue(8.0)
        self.tz_spin.setSuffix(" 小时")
        ll.addWidget(self.tz_spin)

        self.btn_apply_loc = GlowButton("应用观测点", "green")
        self.btn_apply_loc.clicked.connect(self._apply_location)
        self.btn_ip = GlowButton("IP 自动定位（会覆盖上面的坐标）", "cyan", compact=True)
        self.btn_ip.clicked.connect(lambda: self._locate_by_ip(silent=False))
        self.btn_default = GlowButton(f"恢复默认（{DEFAULT_LOCATION_NAME}）",
                                      "violet", compact=True)
        self.btn_default.clicked.connect(self._restore_default_location)
        ll.addWidget(self.btn_apply_loc)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_ip)
        btn_row.addWidget(self.btn_default)
        ll.addLayout(btn_row)
        layout.addWidget(loc_panel)

        # ===== 视场 =====
        view_panel = CollapsiblePanel("视场与视角", "cyan")
        vl = QVBoxLayout(view_panel.content)
        vl.setContentsMargins(6, 0, 0, 0)
        vl.setSpacing(4)
        self.zoom_slider_left = NeonSlider(Qt.Horizontal, "cyan")
        self.zoom_slider_left.setRange(12, int(FOV_MAX))
        self.zoom_slider_left.setValue(100)
        self.zoom_slider_left.valueChanged.connect(self._on_zoom_slider)
        zoom_head = QHBoxLayout()
        zoom_head.addWidget(QLabel("视场角 / 缩放"))
        zoom_head.addStretch(1)
        btn_fov100 = GlowButton("100°", "cyan", compact=True)
        btn_fov100.setFixedWidth(48)
        btn_fov100.setToolTip("回到默认视场 100°")
        btn_fov100.clicked.connect(self._reset_fov_100)
        zoom_head.addWidget(btn_fov100)
        vl.addLayout(zoom_head)
        vl.addWidget(self.zoom_slider_left)
        self.preset_combo = QComboBox()
        for name, fov in (("全天（150°）", 150.0), ("广角（100°）", 100.0),
                          ("标准（70°）", 70.0), ("望远镜（25°）", 25.0),
                          ("高倍（12°）", 12.0)):
            self.preset_combo.addItem(name, fov)
        self.preset_combo.setCurrentIndex(1)
        self.preset_combo.currentIndexChanged.connect(
            lambda _i: self.zoom_slider.setValue(int(self.preset_combo.currentData() or 100)))
        vl.addWidget(QLabel("常用视场"))
        vl.addWidget(self.preset_combo)
        self.btn_real_reset = GlowButton("重置视角", "violet", compact=True)
        self.btn_real_reset.clicked.connect(lambda: self.real_view.reset_view())
        vl.addWidget(self.btn_real_reset)
        layout.addWidget(view_panel)

        # ===== 暗空与光害（波特尔等级） =====
        sky_panel = CollapsiblePanel("暗空与光害（波特尔等级）", "violet")
        sl = QVBoxLayout(sky_panel.content)
        sl.setContentsMargins(6, 0, 0, 0)
        sl.setSpacing(4)
        self.bortle_combo = QComboBox()
        for level, label in LP.bortle_options():
            self.bortle_combo.addItem(label, level)
        self.bortle_combo.setCurrentIndex(4)          # 默认 Bortle 5 郊区
        self.bortle_combo.currentIndexChanged.connect(self._on_bortle_changed)
        sl.addWidget(QLabel("观测地点的暗空等级"))
        sl.addWidget(self.bortle_combo)
        self.lbl_bortle = QLabel("")
        self.lbl_bortle.setWordWrap(True)
        self.lbl_bortle.setStyleSheet("color:#8fffc8;background:transparent;")
        self.lbl_bortle.setFont(QFont("Microsoft YaHei UI", 9))
        sl.addWidget(self.lbl_bortle)
        self.btn_bortle_cmp = GlowButton("城市 vs 山上 对比", "green", compact=True)
        self.btn_bortle_cmp.clicked.connect(self._show_bortle_compare)
        sl.addWidget(self.btn_bortle_cmp)
        layout.addWidget(sky_panel)

        # ===== 搜索 =====
        search_panel = CollapsiblePanel("搜索天体", "green")
        sb = QVBoxLayout(search_panel.content)
        sb.setContentsMargins(6, 0, 0, 0)
        sb.setSpacing(4)
        self.search_box = SearchBox("天狼 / Vega / HIP 87937 / Gliese 551 / M42 / 猎户座")
        self.search_box.returnPressed.connect(self._on_search)
        btn_search = GlowButton("搜索并定位", "green", compact=True)
        btn_search.clicked.connect(self._on_search)
        sb.addWidget(self.search_box)
        sb.addWidget(btn_search)
        hint = QLabel("支持中文名、英文名、HIP / Gliese 编号、M / NGC 编号、星座名、行星名")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#a9c9da;background:transparent;")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        sb.addWidget(hint)
        layout.addWidget(search_panel)

        # ===== 星空导览 =====
        tour_block = CollapsiblePanel("星空导览（今晚看什么）", "violet")
        tbl = QVBoxLayout(tour_block.content)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.addWidget(self.tour_panel)
        self.btn_planner = GlowButton("📋 生成今晚观测计划表", "violet", compact=True)
        self.btn_planner.setToolTip("结合暗空等级与地平障碍，给出每个目标几点、"
                                    "什么高度、能拍多久（Ctrl+Shift+P）")
        self.btn_planner.clicked.connect(self.open_planner)
        tbl.addWidget(self.btn_planner)
        layout.addWidget(tour_block)

        # ===== 观测清单 =====
        watch_panel = CollapsiblePanel("观测清单", "green", expanded=False)
        wl = QVBoxLayout(watch_panel.content)
        wl.setContentsMargins(6, 0, 0, 0)
        wl.setSpacing(3)
        self._watchlist: List[Dict[str, object]] = []
        self.watch_list = QListWidget()
        self.watch_list.setMaximumHeight(150)
        self.watch_list.setStyleSheet(
            "QListWidget{background:rgba(6,18,28,220);border:1px solid rgba(0,170,210,140);"
            "border-radius:3px;color:#e6f6fd;}"
            "QListWidget::item{padding:2px 4px;}"
            "QListWidget::item:selected{background:rgba(0,190,230,150);color:#02121a;}")
        self.watch_list.itemDoubleClicked.connect(self._goto_watchlist_item)
        wl.addWidget(self.watch_list)
        wrow = QHBoxLayout()
        btn_wgoto = GlowButton("定位选中", "green", compact=True)
        btn_wgoto.clicked.connect(lambda: self._goto_watchlist_item(
            self.watch_list.currentRow()))
        btn_wclear = GlowButton("清空", "red", compact=True)
        btn_wclear.clicked.connect(self._clear_watchlist)
        wrow.addWidget(btn_wgoto)
        wrow.addWidget(btn_wclear)
        wl.addLayout(wrow)
        # 导出：清单本来只活在内存里，关掉程序就没了。清单每一项都已经由星图
        # 算好了坐标与升落，导出几乎是零成本——缺了它"搜索 → 加入清单 →
        # 带到望远镜旁"这条链路是断的。
        wrow2 = QHBoxLayout()
        btn_wexp = GlowButton("导出清单…", "cyan", compact=True)
        btn_wexp.setToolTip("把观测清单导出成 Markdown / CSV（含当前坐标与升落时刻）")
        btn_wexp.clicked.connect(self.export_watchlist)
        btn_wcopy = GlowButton("复制", "violet", compact=True)
        btn_wcopy.clicked.connect(lambda: self.export_watchlist(copy_only=True))
        wrow2.addWidget(btn_wexp)
        wrow2.addWidget(btn_wcopy)
        wl.addLayout(wrow2)
        whint = QLabel("在星图中点选天体即自动记录；也可以右键 → 「☆ 加入观测清单」；"
                       "双击条目可定位")
        whint.setWordWrap(True)
        whint.setStyleSheet("color:#a9c9da;background:transparent;")
        whint.setFont(QFont("Microsoft YaHei UI", 9))
        wl.addWidget(whint)
        layout.addWidget(watch_panel)

        # ===== 图层 =====
        layer_panel = CollapsiblePanel("图层开关", "orange")
        ll2 = QVBoxLayout(layer_panel.content)
        ll2.setContentsMargins(6, 0, 0, 0)
        ll2.setSpacing(2)
        self._layer_boxes: Dict[str, QCheckBox] = {}
        for key, default in RealSkyView.DEFAULT_LAYERS.items():
            box = QCheckBox(LAYER_LABELS.get(key, key))
            box.setChecked(bool(default))
            box.stateChanged.connect(lambda state, k=key: self._on_layer_toggle(k, state))
            self._layer_boxes[key] = box
            ll2.addWidget(box)
        layout.addWidget(layer_panel)

        # ===== 星图之声（把星图"弹"出来）=====
        # 光谱型 → 音色，星等 → 音量，方位 → 声场，视线中心 → 和声。
        # 合成完全在内存里用 numpy 做（见 核心/sonify.py），不需要任何音频文件。
        sound_panel = CollapsiblePanel("星图之声", "violet", expanded=False)
        sl = QVBoxLayout(sound_panel.content)
        sl.setContentsMargins(6, 0, 0, 0)
        sl.setSpacing(3)
        self._music = StarMusicController()
        self.sonify_panel = SonifyPanel(self._music)
        self.sonify_panel.enabled_changed.connect(self._on_sonify_toggled)
        sl.addWidget(self.sonify_panel)
        layout.addWidget(sound_panel)
        # 60Hz 更新乐谱太浪费（每帧要排序上千颗星），10Hz 足够——
        # 声部本身有 0.9 秒的平滑时间常数，听感上听不出 100ms 的更新间隔。
        self._sonify_timer = QTimer(self)
        self._sonify_timer.setInterval(100)
        self._sonify_timer.timeout.connect(self._pump_sonify)
        self._sonify_timer.start()

        layout.addStretch(1)
        return self._wrap_scroll(page)

    def _on_sonify_toggled(self, on: bool) -> None:
        self._set_status(
            "星图之声已开启：旋转星图时听到的是那片天区的和声"
            "（光谱型决定音色、星等决定音量、方位决定左右）"
            if on else "星图之声已关闭")

    def toggle_star_music(self) -> None:
        """「工具 → 星图之声」入口（与面板上的按钮同一条路径）。"""
        box = self.sonify_panel.btn_toggle
        box.setChecked(not box.isChecked())

    def _pump_sonify(self) -> None:
        """按 10Hz 把"当前可见天体 + 视线中心"喂给声化引擎。"""
        m = getattr(self, "_music", None)
        if m is None or not m.enabled:
            return
        if self.mode != "real":
            # 只在现实星图里跟随视角（其它模式的坐标语义不同）
            m.set_follow_view(False)
        else:
            rv = self.real_view
            try:
                m.set_objects(rv._all_objects())
            except Exception as exc:
                LOG.warn(f"星图之声取天体失败：{exc}")
        import time as _t
        m.tick(_t.perf_counter(), self.real_view.center_az, self.real_view.center_alt)

    # ------------------------------------------------------------------ 架空星图

    def _build_fiction_controls(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ===== 世界生成 =====
        gen_panel = CollapsiblePanel("架空宇宙生成", "violet")
        gl = QVBoxLayout(gen_panel.content)
        gl.setContentsMargins(6, 0, 0, 0)
        gl.setSpacing(4)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 999999999)
        self.seed_spin.setValue(20260921)
        self.scale_combo = QComboBox()
        for key, label in (("small", "小型（40 个星系）"),
                           ("medium", "中型（80 个星系）"),
                           ("large", "大型（140 个星系）"),
                           ("huge", "巨型（220 个星系）")):
            self.scale_combo.addItem(label, key)
        self.scale_combo.setCurrentIndex(1)
        self.dist_combo = QComboBox()
        for key, label in (("spiral", "旋涡分布"), ("cluster", "星团分布"),
                           ("filament", "纤维状分布"), ("scatter", "均匀散布")):
            self.dist_combo.addItem(label, key)
        gl.addWidget(QLabel("随机种子"))
        gl.addWidget(self.seed_spin)
        gl.addWidget(QLabel("规模"))
        gl.addWidget(self.scale_combo)
        gl.addWidget(QLabel("分布形态"))
        gl.addWidget(self.dist_combo)
        self.btn_generate = GlowButton("⚡ 生成宇宙", "violet")
        self.btn_generate.clicked.connect(self._generate_world)
        gl.addWidget(self.btn_generate)
        self.gen_progress = QLabel("")
        self.gen_progress.setWordWrap(True)
        self.gen_progress.setStyleSheet("color:#8fd4ea;background:transparent;")
        self.gen_progress.setFont(QFont("Microsoft YaHei UI", 9))
        gl.addWidget(self.gen_progress)
        layout.addWidget(gen_panel)

        # ===== 星图模式 =====
        map_panel = CollapsiblePanel("星图模式", "cyan")
        ml = QVBoxLayout(map_panel.content)
        ml.setContentsMargins(6, 0, 0, 0)
        ml.setSpacing(3)
        self._map_buttons: Dict[str, IconToggleButton] = {}
        for key, label in MAP_MODES:
            btn = IconToggleButton(label, "cyan")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, k=key: self._on_map_mode(k))
            self._map_buttons[key] = btn
            ml.addWidget(btn)
        if "explore" in self._map_buttons:
            self._map_buttons["explore"].setChecked(True)
        layout.addWidget(map_panel)

        # ===== 时间 =====
        tp = CollapsiblePanel("银河历时间", "green")
        tl = QVBoxLayout(tp.content)
        tl.setContentsMargins(6, 0, 0, 0)
        tl.setSpacing(4)
        self.galaxy_time_label = QLabel("银河历 0.00 年")
        self.galaxy_time_label.setStyleSheet("color:#e6f6fd;background:transparent;")
        self.galaxy_time_label.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        tl.addWidget(self.galaxy_time_label)
        self.fiction_time_slider = NeonSlider(Qt.Horizontal, "violet")
        self.fiction_time_slider.setRange(0, 500)
        self.fiction_time_slider.setValue(0)
        self.fiction_time_slider.valueChanged.connect(self._on_fiction_time)
        tl.addWidget(self.fiction_time_slider)
        self.btn_fiction_play = IconToggleButton("▶ 推进时间", "green")
        self.btn_fiction_play.setCheckable(True)
        self.btn_fiction_play.toggled.connect(self._on_fiction_play)
        tl.addWidget(self.btn_fiction_play)
        speed_row = QHBoxLayout()
        self._fiction_speed_buttons: List[IconToggleButton] = []
        for label, mult in (("1x", 1.0), ("5x", 5.0), ("20x", 20.0),
                            ("100x", 100.0), ("500x", 500.0), ("2000x", 2000.0)):
            btn = IconToggleButton(label, "cyan")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, m=mult: self._on_fiction_speed(m))
            self._fiction_speed_buttons.append(btn)
            speed_row.addWidget(btn)
        self._fiction_speed_buttons[1].setChecked(True)
        tl.addLayout(speed_row)
        note = QLabel("时间倍率参考：20x ≈ 每年 3 秒；100x ≈ 每年 0.6 秒；\n"
                      "2000x ≈ 每年 0.03 秒（几百年的历史几秒跑完）。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#a9c9da;background:transparent;")
        note.setFont(QFont("Microsoft YaHei UI", 8))
        tl.addWidget(note)
        layout.addWidget(tp)

        # ===== 航线 =====
        route_panel = CollapsiblePanel("航线规划", "cyan")
        rl = QVBoxLayout(route_panel.content)
        rl.setContentsMargins(6, 0, 0, 0)
        rl.setSpacing(4)
        self.route_start_combo = QComboBox()
        self.route_goal_combo = QComboBox()
        rl.addWidget(QLabel("起点"))
        rl.addWidget(self.route_start_combo)
        rl.addWidget(QLabel("终点"))
        rl.addWidget(self.route_goal_combo)
        self.route_weight_combo = QComboBox()
        for key, label in (("distance", "最短距离"), ("time", "最短时间"),
                           ("fuel", "最省燃料"), ("risk", "最低风险"),
                           ("safe", "综合安全")):
            self.route_weight_combo.addItem(label, key)
        rl.addWidget(QLabel("优化目标"))
        rl.addWidget(self.route_weight_combo)
        row = QHBoxLayout()
        btn_plan = GlowButton("规划航线", "violet", compact=True)
        btn_plan.clicked.connect(self._plan_route)
        btn_clear = GlowButton("清除", "red", compact=True)
        btn_clear.clicked.connect(self._clear_route)
        row.addWidget(btn_plan)
        row.addWidget(btn_clear)
        rl.addLayout(row)
        self.route_info = QLabel("在星图中 Shift+点击可快速设置起点 / 终点")
        self.route_info.setWordWrap(True)
        self.route_info.setStyleSheet("color:#8fffc8;background:transparent;")
        self.route_info.setFont(QFont("Microsoft YaHei UI", 9))
        rl.addWidget(self.route_info)
        layout.addWidget(route_panel)

        # ===== 飞船 =====
        ship_panel = CollapsiblePanel("飞船调度", "orange")
        sl = QVBoxLayout(ship_panel.content)
        sl.setContentsMargins(6, 0, 0, 0)
        sl.setSpacing(4)
        row2 = QHBoxLayout()
        btn_ship = GlowButton("派遣飞船", "orange", compact=True)
        btn_ship.clicked.connect(self._dispatch_ship)
        row2.addWidget(btn_ship)
        self.ship_count_label = QLabel("飞船 0 艘")
        self.ship_count_label.setStyleSheet("color:#e6f6fd;background:transparent;")
        row2.addWidget(self.ship_count_label)
        sl.addLayout(row2)
        self.ship_log = QLabel("（暂无飞船日志）")
        self.ship_log.setWordWrap(True)
        self.ship_log.setStyleSheet("color:#ffd08a;background:rgba(6,18,28,200);"
                                    "border:1px solid rgba(0,170,210,120);border-radius:3px;"
                                    "padding:4px;")
        self.ship_log.setFont(QFont("Microsoft YaHei UI", 8))
        self.ship_log.setMinimumHeight(90)
        sl.addWidget(self.ship_log)
        layout.addWidget(ship_panel)

        # ===== 图层 =====
        fp = CollapsiblePanel("图层", "violet")
        fl = QVBoxLayout(fp.content)
        fl.setContentsMargins(6, 0, 0, 0)
        fl.setSpacing(2)
        self._fiction_boxes: Dict[str, QCheckBox] = {}
        for key, label, default in (("routes", "航线网络", True),
                                    ("labels", "星系名称", True),
                                    ("grid", "参考网格", True),
                                    ("ships", "飞船位置", True),
                                    ("halos", "势力光晕", True)):
            box = QCheckBox(label)
            box.setChecked(default)
            box.stateChanged.connect(lambda s, k=key: self._on_fiction_layer(k, s))
            self._fiction_boxes[key] = box
            fl.addWidget(box)
        layout.addWidget(fp)

        # ===== 历史推演 =====
        hist_panel = CollapsiblePanel("历史推演", "green")
        hl = QVBoxLayout(hist_panel.content)
        hl.setContentsMargins(6, 0, 0, 0)
        hl.setSpacing(4)
        hist_hint = QLabel("自动推演 N 年，输出人口 / 势力版图 / 科技曲线与事件时间轴"
                           "（把「历史事件」从一段纯文字变成可量化的过程）。")
        hist_hint.setWordWrap(True)
        hist_hint.setStyleSheet("color:#e6f6fd;background:transparent;")
        hist_hint.setFont(QFont("Microsoft YaHei UI", 9))
        hl.addWidget(hist_hint)
        self.btn_history = GlowButton("📈 打开历史推演工作台", "green", compact=True)
        self.btn_history.clicked.connect(self.open_history_sim)
        hl.addWidget(self.btn_history)
        layout.addWidget(hist_panel)

        layout.addStretch(1)
        return self._wrap_scroll(page)

    # ------------------------------------------------------------------ 折叠侧栏

    def _wrap_collapsible(self, widget: QWidget, side: str = "right") -> QWidget:
        """把信息面板包成可折叠的侧栏。"""
        container = QWidget()
        container.setObjectName("InfoContainer")
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setMinimumWidth(240)
        container.setMaximumWidth(480)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        head = QWidget()
        head.setObjectName("PanelHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(4, 0, 4, 0)
        title = QLabel("信息面板 / INSPECTOR" if side == "right" else "控制台 / CONSOLE")
        title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        title.setStyleSheet("color:#7ff0ff;background:transparent;letter-spacing:2px;")
        hl.addWidget(title, 1)
        btn_fold = GlowButton("⌄", "cyan", compact=True)
        btn_fold.setFixedWidth(30)
        btn_fold.clicked.connect(lambda: self._toggle_side(side))
        hl.addWidget(btn_fold)
        layout.addWidget(head)
        layout.addWidget(widget, 1)
        setattr(self, f"_btn_fold_{side}", btn_fold)
        return container

    # ==========================================================================
    # 二、菜单
    # ==========================================================================

    def _build_menus(self) -> None:
        bar = self.menuBar()

        # ---- 文件 ----
        m_file = bar.addMenu("文件(&F)")
        self._add_action(m_file, "导出世界设定 JSON…", self._export_world, "Ctrl+E")
        self._add_action(m_file, "导入世界设定 JSON…", self._import_world, "Ctrl+I")
        m_file.addSeparator()
        self._save_menu = m_file.addMenu("本地存档")
        self._add_action(m_file, "刷新存档列表", self._refresh_save_menu)
        m_file.addSeparator()
        self._add_action(m_file, "🖼 导出星空图片 / 延时动画…", self.open_export_dialog,
                         "Ctrl+Shift+E")
        m_file.addSeparator()
        self._add_action(m_file, "退出", self.close, "Ctrl+Q")

        # ---- 视图 ----
        m_view = bar.addMenu("视图(&V)")
        self._add_action(m_view, "现实星图", lambda: self.switch_mode("real"), "Ctrl+1")
        self._add_action(m_view, "太阳系仪（行星时钟）",
                         lambda: self.switch_mode("orrery"), "Ctrl+4")
        self._add_action(m_view, "太阳邻域（50 光年内天体）",
                         lambda: self.switch_mode("nearby"), "Ctrl+2")
        self._add_action(m_view, "架空星图", lambda: self.switch_mode("fiction"), "Ctrl+3")
        m_view.addSeparator()
        self._add_action(m_view, "红光夜间模式", lambda: self.btn_night.toggle())
        self._add_action(m_view, "全屏切换", self._toggle_fullscreen, "F11")
        self._add_action(m_view, "重置视角", self._reset_view)
        m_view.addSeparator()
        self._add_action(m_view, "折叠/展开左侧控制台",
                         lambda: self._toggle_side("left"), "Ctrl+L")
        self._add_action(m_view, "折叠/展开右侧信息面板",
                         lambda: self._toggle_side("right"), "Ctrl+R")

        # ---- 工具 ----
        m_tool = bar.addMenu("工具(&T)")
        self._add_action(m_tool, "生成随机宇宙", self._generate_world, "Ctrl+G")
        self._add_action(m_tool, "规划航线", self._plan_route, "Ctrl+P")
        self._add_action(m_tool, "派遣飞船", self._dispatch_ship, "Ctrl+D")
        m_tool.addSeparator()
        self._add_action(m_tool, "📅 天象日历（未来 30 天）", self.open_sky_calendar, "Ctrl+K")
        self._add_action(m_tool, "⏱ 时间旅行…", self.open_time_travel, "Ctrl+T")
        self._add_action(m_tool, "🏔 地平障碍与可见性…", self.open_horizon_dialog)
        self._add_action(m_tool, "📋 观测计划表（今晚几点看什么）",
                         self.open_planner, "Ctrl+Shift+P")
        m_tool.addSeparator()
        self._add_action(m_tool, "☀ 太阳邻域（50 光年内的全部天体）",
                         lambda: self.switch_mode("nearby"), "Ctrl+2")
        self._add_action(m_tool, "🪐 太阳系仪（行星时钟）",
                         lambda: self.switch_mode("orrery"), "Ctrl+4")
        self._add_action(m_tool, "📈 历史推演工作台", self.open_history_sim, "Ctrl+H")
        self._add_action(m_tool, "🔭 外星视角（从别的恒星看天空）",
                         self.open_inverse_sky, "Ctrl+Shift+I")
        m_tool.addSeparator()
        self._add_action(m_tool, "🎵 星图之声（把星图弹出来）",
                         self.toggle_star_music, "Ctrl+M")
        self._add_action(m_tool, "📖 历史小说工作台（DeepSeek）",
                         self.open_novel_panel, "Ctrl+N")
        self._add_action(m_tool, "DeepSeek API 设置…", self.open_deepseek_settings)
        m_tool.addSeparator()
        self._add_action(m_tool, "全天天体总览", self.open_object_browser, "Ctrl+B")
        self._add_action(m_tool, "自动定位（IP）", lambda: self._locate_by_ip(silent=False))
        self._add_action(m_tool, "查看运行日志", self._show_log)
        self._add_action(m_tool, "数据信息", self._show_data_info)

        # ---- 帮助 ----
        m_help = bar.addMenu("帮助(&H)")
        self._add_action(m_help, "快捷键与操作说明", self._show_help, "F1")
        self._add_action(m_help, "关于 STELLAR NEXUS", self._show_about)

        self._refresh_save_menu()

    def _add_action(self, menu, text: str, slot, shortcut: str = "") -> QAction:
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(lambda _c=False: slot())
        menu.addAction(act)
        return act

    def _info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    # ==========================================================================
    # 三、信号连接
    # ==========================================================================

    def _connect_signals(self) -> None:
        self.mode_switch.mode_changed.connect(self.switch_mode)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        self.time_slider.valueChanged.connect(self._on_time_slider)
        self.btn_play.toggled.connect(self._on_play_toggled)
        self.btn_night.toggled.connect(self._set_night_mode)
        self.loc_name.editingFinished.connect(self._on_location_edited)

        # 现实星图
        self.real_view.object_selected.connect(self._on_sky_object)
        self.real_view.status_message.connect(self._set_status)
        self.real_view.time_changed.connect(self._on_time_changed)
        self.real_view.view_changed.connect(self._on_real_view_changed)
        self.real_view.nearby_requested.connect(self._open_nearby_star)
        self.real_view.watchlist_add.connect(self._add_to_watchlist)
        self.real_view.layers_changed.connect(self._on_layer_synced)
        self.tour_panel.target_chosen.connect(self._on_tour_target)
        self.tour_panel.status_message.connect(self._set_status)
        self.dt_edit.dateTimeChanged.connect(self._on_datetime_edit)

        # 太阳系仪
        self.orrery_view.planet_selected.connect(self._on_orrery_selected)
        self.orrery_view.status_message.connect(self._set_status)
        self.orrery_view.view_changed.connect(self._on_orrery_view_changed)

        # 架空星图
        self.fiction_view.system_selected.connect(self._on_system_selected)
        self.fiction_view.status_message.connect(self._set_status)
        self.fiction_view.ship_event.connect(self._on_ship_event)
        self.fiction_view.route_endpoint_changed.connect(self._on_route_endpoint)

        # 太阳邻域
        self.nearby_view.star_selected.connect(self._on_nearby_selected)
        self.nearby_view.status_message.connect(self._set_status)
        self.nearby_view.view_changed.connect(self._on_nearby_view_changed)

        # 信息面板
        self.info_panel.route_from_here.connect(self._set_route_start)
        self.info_panel.route_to_here.connect(self._set_route_goal)
        self.info_panel.locate_system.connect(self._locate_system)
        self.info_panel.dispatch_requested.connect(self._dispatch_to_system)
        self.info_panel.locate_nearby_on_sky.connect(self._locate_nearby_in_sky)

        # 初始光害显示
        self._on_bortle_changed()

    # ==========================================================================
    # 四、定时器
    # ==========================================================================

    def _start_timers(self) -> None:
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._on_clock)
        self._clock.start()
        self._slow = QTimer(self)
        self._slow.setInterval(1500)
        self._slow.timeout.connect(self._on_slow_tick)
        self._slow.start()

    def _on_clock(self) -> None:
        """每秒刷新状态栏时钟。"""
        now = now_local()
        self.clock_label.setText(f"{now.strftime('%H:%M:%S')}　"
                                 f"{self.real_view.observer.location_name}")
        if self.galaxy is not None:
            self.galaxy_time_label.setText(
                f"银河历 {self.galaxy.year:.2f} 年　{len(self.galaxy.ships)} 艘飞船")

    def _on_slow_tick(self) -> None:
        """慢速刷新：状态栏统计。"""
        try:
            if self.mode == "real":
                vis = self.real_view.visibility_summary()
                self.stats_label.setText(
                    f"恒星 {vis.get('stars', 0)}　行星 {vis.get('planets', 0)}　"
                    f"深空 {vis.get('deep_sky', 0)}　"
                    f"极限星等 {self.real_view.mag_limit():.1f}")
            elif self.mode == "fiction" and self.galaxy is not None:
                stats = self.galaxy.stats()
                self.stats_label.setText(
                    f"星系 {stats.get('systems', 0)}　航线 {stats.get('routes', 0)}　"
                    f"文明 {stats.get('civilizations', 0)}　势力 {stats.get('factions', 0)}")
            elif self.mode == "nearby":
                st = self.nearby_view.stats()
                self.stats_label.setText(f"近星 {st['shown']}/{st['total']}　"
                                         f"半径 {st['limit']:.0f} 光年")
            else:
                self.stats_label.setText("太阳系仪 · 俯视黄道面")
        except Exception:
            pass

    # ==========================================================================
    # 五、模式切换
    # ==========================================================================

    @Slot(str)
    def switch_mode(self, mode: str) -> None:
        """在现实星图 / 太阳系仪 / 太阳邻域 / 架空星图之间切换。"""
        mode = mode if mode in ("real", "orrery", "nearby", "fiction") else "real"
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "real":
            self.view_stack.setCurrentIndex(0)
            self.left_stack.setCurrentIndex(0)
            self.info_panel.show_real_mode()
            self.mode_switch.set_mode("real", animate=False)
            self._set_status("已切换到现实星图")
        elif mode == "orrery":
            self.view_stack.setCurrentIndex(1)
            self.left_stack.setCurrentIndex(1)
            self.info_panel.show_real_mode()
            self.mode_switch.set_mode("orrery", animate=False)
            self.orrery_view.set_observer(self.real_view.observer)
            self.orrery_view.refresh(force=True)
            self._set_status("已切换到太阳系仪：俯视黄道面查看行星轨道与当前位置")
        elif mode == "nearby":
            self.view_stack.setCurrentIndex(2)
            self.left_stack.setCurrentIndex(2)
            self.info_panel.show_nearby_mode()
            self.mode_switch.set_mode("nearby", animate=False)
            stats = NS.nearby_stats()
            self._set_status(
                f"已切换到太阳邻域：50 光年内共 {stats['count']} 个已知天体"
                f"（当前显示 {self.nearby_view.stats()['shown']} 个）")
        else:
            self.view_stack.setCurrentIndex(3)
            self.left_stack.setCurrentIndex(3)
            self.info_panel.show_fiction_mode(self.galaxy)
            self.mode_switch.set_mode("fiction", animate=False)
            if self.galaxy is None:
                self._set_status("架空星图尚未生成宇宙，点击左侧「⚡ 生成宇宙」开始")
            else:
                self._set_status(f"已切换到架空星图：{self.galaxy.name}")
        self._resize_overlays()

    def _reset_view(self) -> None:
        """重置当前视图的视角。"""
        if self.mode == "real":
            self.real_view.reset_view()
            self.zoom_slider.setValue(int(self.real_view.fov))
        elif self.mode == "orrery":
            self.orrery_view.reset_view()
            self.orrery_zoom.setValue(100)
        elif self.mode == "nearby":
            self.nearby_view.reset_view()
            self.nearby_zoom.setValue(100)
        else:
            self.fiction_view.reset_view()
        self._set_status("视角已重置")

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_side(self, side: str) -> None:
        """折叠 / 展开左右侧栏。"""
        target = self.left_panel if side == "left" else self.info_container
        target.setVisible(not target.isVisible())
        btn = getattr(self, f"_btn_fold_{side}", None)
        if btn is not None:
            btn.setText("⌃" if not target.isVisible() else "⌄")
        self._set_status(f"{'左侧控制台' if side == 'left' else '右侧信息面板'}"
                         f"{'已折叠' if not target.isVisible() else '已展开'}")

    def _set_night_mode(self, enabled: bool) -> None:
        """红光夜间模式：四个视图统一切换。"""
        for view in (self.real_view, self.orrery_view, self.nearby_view, self.fiction_view):
            if hasattr(view, "red_mode"):
                view.red_mode = bool(enabled)
                if hasattr(view, "mark_dirty"):
                    view.mark_dirty()
        for box in (getattr(self, "btn_night", None),
                    getattr(self, "chk_nearby_night", None)):
            if box is not None and box.isChecked() != bool(enabled):
                box.blockSignals(True)
                box.setChecked(bool(enabled))
                box.blockSignals(False)
        self._set_status(f"红光夜间模式已{'开启' if enabled else '关闭'}")

    def _resize_overlays(self) -> None:
        """把扫描线覆盖层对齐到各视图。

        性能说明（重要）：扫描线是「透明 + WA_NoSystemBackground」的覆盖层，
        它一旦重绘，Qt 就必须把它底下的**整个视图**一起重绘。实测每隔 200ms
        动画一次，会让当前视图持续重绘（现实星图 30~90ms/帧、架空星图 21ms/帧），
        空闲 CPU 就白吃掉 40%~50%——等于为了 1 像素的扫描线漂移反复重绘整片星空。
        因此这里**不做时间动画**，只保证对齐与置顶；扫描线保持静止，
        观感几乎没有差别，但空闲时真正不再消耗 CPU。
        """
        try:
            for overlay, target in ((self._scan_real, self.real_view),
                                    (self._scan_orrery, self.orrery_view),
                                    (self._scan_nearby, self.nearby_view),
                                    (self._scan_fiction, self.fiction_view)):
                overlay.setGeometry(target.rect())
                overlay.raise_()
                if hasattr(overlay, "set_animated"):
                    overlay.set_animated(False)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._resize_overlays()

    # ==========================================================================
    # 六、现实星图交互
    # ==========================================================================

    def _on_sky_object(self, info: Dict[str, object]) -> None:
        self.info_panel.show_sky_object(info)
        # ★ 点选即自动记入观测清单（静默，不写状态栏）。
        #   原来的链路是"右键天体 → 加入观测清单"，但右键菜单是隐式入口，
        #   实测走一遍程序时一次都不会被用到——清单永远是空的，导出更无从谈起。
        #   注意顺序：先记录再写状态栏，否则状态栏里的"已选中…"会被覆盖。
        self._auto_watch(info)
        self._set_status(f"已选中：{info.get('name')}（{info.get('type_cn')}）")

    def _auto_watch(self, info: Dict[str, object]) -> None:
        """点选天体后静默加入观测清单（不覆盖状态栏提示）。"""
        key = str(info.get("key") or "")
        if not key:
            return
        for item in self._watchlist:
            if str(item.get("key")) == key:
                return                      # 已在清单里，不重复、不提示
        self._watchlist.append(dict(info))
        self._refresh_watchlist()
        try:
            self.watch_list.setToolTip(
                f"已自动记录 {info.get('name')}（共 {len(self._watchlist)} 个）\n"
                f"可点「导出清单…」或「复制」带走")
        except Exception:
            pass

    def _on_real_view_changed(self, data: Dict[str, object]) -> None:
        fov = int(round(float(data.get("fov", 100.0))))
        for slider in (self.zoom_slider, self.zoom_slider_left):
            if abs(slider.value() - fov) > 1:
                slider.blockSignals(True)
                slider.setValue(fov)
                slider.blockSignals(False)

    def _on_time_changed(self, when: datetime) -> None:
        self.dt_edit.blockSignals(True)
        self.dt_edit.setDateTime(QDateTime(when))
        self.dt_edit.blockSignals(False)

    def _on_datetime_edit(self, qdt: QDateTime) -> None:
        self.real_view.set_time(qdt.toPython())
        self._set_status(f"时间已设为 {format_datetime(qdt.toPython())}")

    def _on_time_slider(self, value: int) -> None:
        """时间滑块：相对「当前系统时间」的小时偏移。"""
        base = datetime.now()
        when = base + timedelta(hours=float(value))
        self.real_view.set_time(when)
        self.time_offset_label.setText(f"偏移 {value / 1.0:+.1f} 小时")
        self.dt_edit.blockSignals(True)
        self.dt_edit.setDateTime(QDateTime(when))
        self.dt_edit.blockSignals(False)
        self._set_status(f"时间偏移 {value:+d} 小时 → {format_datetime(when)}")

    def _on_now_clicked(self) -> None:
        self.real_view.set_time(datetime.now())
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(0)
        self.time_slider.blockSignals(False)
        self._set_status("时间已同步到当前时刻")

    def _on_play_toggled(self, playing: bool) -> None:
        self.real_view.time_playing = bool(playing)
        self.btn_play.setText("⏸ 暂停" if playing else "▶ 播放")
        self._set_status("时间播放中" if playing else "时间已暂停")

    def _on_speed_clicked(self, multiplier: float) -> None:
        self.speed_multiplier = float(multiplier)
        for btn, mult in zip(self.speed_buttons, (1.0, 60.0, 600.0, 3600.0)):
            btn.setChecked(abs(mult - multiplier) < 1e-6)
        self.real_view.set_time_speed(60.0 * float(multiplier), self.real_view.time_playing)
        self._set_status(f"时间倍率：{multiplier:g}x")

    def _reset_fov_100(self) -> None:
        """把视场角恢复到默认的 100°。"""
        self.zoom_slider.setValue(100)
        self.real_view.fov = 100.0
        self.real_view.mark_dirty()
        self._set_status("视场角已恢复为 100°")

    def _on_zoom_slider(self, value: int) -> None:
        self.real_view.fov = clamp(float(value), 12.0, FOV_MAX)
        self.real_view.mark_dirty()
        for slider in (self.zoom_slider, self.zoom_slider_left):
            if abs(slider.value() - value) > 1:
                slider.blockSignals(True)
                slider.setValue(value)
                slider.blockSignals(False)

    def _on_layer_toggle(self, key: str, state: int) -> None:
        self._layers_state[key] = bool(state)
        self.real_view.set_layer(key, bool(state))

    def _on_search(self) -> None:
        text = self.search_box.text().strip()
        if not text:
            self.search_box.setFocus()
            return
        self.real_view.find_object(text)

    # ---- 观测清单 ----

    def _add_to_watchlist(self, info: Dict[str, object]) -> None:
        """把天体加入观测清单（右键菜单触发）。"""
        key = str(info.get("key") or "")
        for item in self._watchlist:
            if str(item.get("key")) == key:
                self._set_status(f"{info.get('name')} 已在观测清单中")
                return
        self._watchlist.append(dict(info))
        self._refresh_watchlist()
        self._set_status(f"已加入观测清单：{info.get('name')}")

    def _refresh_watchlist(self) -> None:
        """刷新清单显示：名称 + 高度（地下用 ↓ 标记）。"""
        self.watch_list.clear()
        for item in self._watchlist:
            alt = item.get("alt")
            prefix = ""
            try:
                a = float(alt) if alt is not None else 0.0
                prefix = "↓ " if a < 0 else ""
                alt_text = f"{a:+.1f}°"
            except (TypeError, ValueError):
                alt_text = "--"
            self.watch_list.addItem(f"{prefix}{item.get('name', '?')}　{alt_text}")

    def _goto_watchlist_item(self, row: int) -> None:
        """定位到清单里的某个天体。"""
        if row < 0 or row >= len(self._watchlist):
            return
        item = self._watchlist[row]
        name = str(item.get("name") or "")
        if name:
            self.real_view.find_object(name)

    def export_watchlist(self, copy_only: bool = False) -> None:
        """把观测清单导出成 Markdown / CSV（或复制到剪贴板）。

        导出前先用**当前时刻**重算一遍每项的高度角/方位角/升落——清单里存的
        可能是几小时前加入时的值，直接导出会给出误导性的坐标。
        """
        if not self._watchlist:
            self._set_status("观测清单是空的，先在星图里点选几个天体")
            return
        try:
            refreshed = []
            for item in self._watchlist:
                key = str(item.get("key") or "")
                info = None
                if key:
                    try:
                        info = self.real_view.describe_object(
                            next(o for o in self.real_view._all_objects()
                                 if o.key == key))          # type: ignore[attr-defined]
                    except Exception:
                        info = None
                refreshed.append(info or dict(item))
            from 核心 import watchlist_export as WX
            when = self.real_view.now()
            md = WX.plan_markdown(refreshed, when, self.real_view.observer)
            csv = WX.plan_csv(refreshed)
            if copy_only:
                from PySide6.QtGui import QGuiApplication
                QGuiApplication.clipboard().setText(md)
                self._set_status(f"已复制 {len(refreshed)} 个目标到剪贴板（Markdown）")
                return
            default = WX.default_filename(when, "md")
            path, chosen = QFileDialog.getSaveFileName(
                self, "导出观测清单", default,
                "Markdown (*.md);;CSV (*.csv);;文本 (*.txt)")
            if not path:
                return
            text = csv if (path.lower().endswith(".csv") or "csv" in str(chosen).lower()) \
                else md
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(text)
            self._set_status(f"已导出 {len(refreshed)} 个目标：{path}")
        except Exception as exc:
            LOG.error("导出观测清单失败", exc)
            self._warn("导出失败", f"导出观测清单失败：\n{exc}")

    def _clear_watchlist(self) -> None:
        self._watchlist.clear()
        self._refresh_watchlist()
        self._set_status("观测清单已清空")

    def _on_layer_synced(self, key: str, value: bool) -> None:
        """右键菜单切换图层后，同步左侧复选框状态与视图状态。

        两种情况都要兼顾：
            * 来自右键菜单（视图已改）→ 只需把复选框对齐，避免递归
            * 来自外部调用（只想改图层）→ 复选框变化信号会驱动视图更新
        """
        box = self._layer_boxes.get(key)
        if box is not None and box.isChecked() != bool(value):
            box.blockSignals(True)
            box.setChecked(bool(value))
            box.blockSignals(False)
            # 复选框被手工对齐时，视图未必已经改变，这里补齐
            self.real_view.set_layer(key, bool(value))
        label = box.text() if box else key
        self._set_status(f"图层「{label}」已{'开启' if value else '关闭'}")

    # ---- 光害 / 波特尔 ----

    def _on_bortle_changed(self) -> None:
        level = int(self.bortle_combo.currentData() or 5)
        self.real_view.set_bortle(level)
        info = LP.bortle_class(level)
        self.lbl_bortle.setText(
            f"天空亮度 {info.mu_sky:.2f} mag/arcsec²　肉眼极限星等 "
            f"{self.real_view.mag_limit():.1f}<br>银河：{info.milky_way}")
        self._set_status(f"光害等级已设为 Bortle {level}（{info.name}）")

    def _show_bortle_compare(self) -> None:
        """城市 vs 山上的可见性对比。"""
        try:
            star_mags = [s.mag for s in SD.STAR_CATALOG]
            dso_mags = [m.mag for m in SD.DEEP_SKY_CATALOG]
            low = int(self.bortle_combo.currentData() or 8)
            high = 2 if low > 2 else 5
            text = LP.compare_report(star_mags, dso_mags, low, high)
            box = QMessageBox(self)
            box.setWindowTitle("光害对比：城市 vs 暗空")
            box.setTextFormat(Qt.RichText)
            box.setText(text)
            box.exec()
        except Exception as exc:
            LOG.error("光害对比失败", exc)
            self._warn("无法生成对比", str(exc))

    # ==========================================================================
    # 七、观测点
    # ==========================================================================

    # ---- 城市检索（离线 GeoNames 数据） ----

    def _on_city_search(self) -> None:
        """按关键字检索城市，结果填入列表。"""
        text = self.city_search.text().strip()
        if not text:
            self.city_search.setFocus()
            return
        hits = CT.search_cities(text, limit=40)
        self.city_results.clear()
        if not hits:
            self.city_results.addItem(f"未找到「{text}」——可改用「手动输入坐标」")
            self.city_hint.setText("提示：库内为人口 5000 以上的城市；"
                                   "小地名请用下方的经纬度手动输入。")
            self._set_status(f"城市检索：未找到「{text}」")
            return
        for c in hits:
            item = QListWidgetItem(f"{c.display}　{c.coord_text()}　{c.timezone}")
            item.setData(Qt.UserRole, (c.lat, c.lon, c.timezone, c.short, c.country_cn))
            self.city_results.addItem(item)
        self.city_hint.setText(
            f"找到 {len(hits)} 个结果（按人口排序）；单击选中即填入下方坐标，"
            f"双击直接应用。")
        self._set_status(f"城市检索「{text}」：{len(hits)} 个结果，"
                         f"首选 {hits[0].short}（{hits[0].coord_text()}）")

    def _on_city_picked(self, item: QListWidgetItem) -> None:
        """选中城市 → 把坐标与时区填进输入框（不立即应用）。"""
        data = item.data(Qt.UserRole)
        if not data:
            return
        lat, lon, timezone, short, country = data
        self.loc_name.setText(f"{short}·{country}" if country else short)
        # 数值只填绝对值，正负号完全交给「南北 / 东西」勾选框，避免负号重复
        for spin, value in ((self.lat_spin, abs(lat)), (self.lon_spin, abs(lon))):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        for chk, flag in ((self.chk_north, lat >= 0), (self.chk_east, lon >= 0)):
            chk.blockSignals(True)
            chk.setChecked(flag)
            chk.blockSignals(False)
        offset = CT.City(short, "", lat, lon, "", timezone, 0).utc_offset_hours()
        if offset is not None:
            self.tz_spin.setValue(offset)
        self._refresh_coord_preview()
        self._set_status(f"已填入 {short}：{CT.format_coord(lat, lon)}　"
                         f"时区 {timezone}　点「应用观测点」生效")

    def _refresh_coord_preview(self) -> None:
        """把「数值 + 南北/东西」实时翻译成带符号坐标并给出半球说明。"""
        lat, lon = self._current_coord()
        self.coord_preview.setText(
            f"{CT.format_coord(lat, lon)}<br>"
            f"<span style='color:#a9c9da'>带符号：({lat:+.4f}, {lon:+.4f})　"
            f"{CT.describe_hemisphere(lat, lon)}</span>")

    def _current_coord(self) -> Tuple[float, float]:
        """由界面上的「数值 + 半球/东西」得到带符号坐标。"""
        return CT.parse_hemisphere_input(self.lat_spin.value(),
                                         self.chk_north.isChecked(),
                                         self.lon_spin.value(),
                                         self.chk_east.isChecked())

    def _restore_default_location(self) -> None:
        """把观测点恢复为内置默认（北京）。"""
        self._restore_default_location_quiet()
        self._refresh_coord_preview()
        self._apply_location()

    def _restore_default_location_quiet(self) -> None:
        """把观测点设为默认值（不触发提示，也不重复应用）。"""
        self.real_view.set_observer(DEFAULT_LAT, DEFAULT_LON,
                                    DEFAULT_LOCATION_NAME, 8.0)
        self.loc_name.setText(DEFAULT_LOCATION_NAME)
        for spin, value in ((self.lat_spin, abs(DEFAULT_LAT)),
                            (self.lon_spin, abs(DEFAULT_LON)),
                            (self.tz_spin, 8.0)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        for chk, flag in ((self.chk_north, DEFAULT_LAT >= 0),
                          (self.chk_east, DEFAULT_LON >= 0)):
            chk.blockSignals(True)
            chk.setChecked(flag)
            chk.blockSignals(False)

    def _on_location_edited(self) -> None:
        self._set_status("观测点已修改，点「应用观测点」生效")

    def _apply_location(self) -> None:
        """把界面上的坐标应用为观测点（含南北/东西的符号合成）。"""
        try:
            lat, lon = self._current_coord()
            name = self.loc_name.text().strip() or "自定义观测点"
            self.real_view.set_observer(lat, lon, name, self.tz_spin.value())
            self.tour_panel.set_observer(self.real_view.observer)
            self.orrery_view.set_observer(self.real_view.observer)
            self._refresh_coord_preview()
            self._set_status(
                f"观测点已更新：{name}　{CT.format_coord(lat, lon)}　"
                f"{CT.describe_hemisphere(lat, lon)}　UTC{self.tz_spin.value():+.1f}")
        except Exception as exc:
            LOG.error("应用观测点失败", exc)
            self._warn("无法应用观测点", str(exc))

    def _initial_locate(self) -> None:
        """启动时使用内置默认观测点（北京），**不做网络定位**。

        默认观测点固定为北京（北半球 / 东经），避免启动瞬间被 IP 定位悄悄换成
        别的城市；需要自动定位时可点「IP 自动定位」，或直接检索城市。
        """
        self.real_view.set_time(datetime.now())
        self._restore_default_location_quiet()
        self.tour_panel.set_observer(self.real_view.observer)
        self.orrery_view.set_observer(self.real_view.observer)
        self._refresh_coord_preview()
        self._set_status(
            f"默认观测点：{DEFAULT_LOCATION_NAME}　{CT.format_coord(DEFAULT_LAT, DEFAULT_LON)}"
            f"　{CT.describe_hemisphere(DEFAULT_LAT, DEFAULT_LON)}")

    def _locate_by_ip(self, silent: bool = True) -> None:
        """通过 IP 粗略定位（失败自动回退默认地点）。"""
        result = None
        try:
            from 核心.utils import locate_by_ip
            result = locate_by_ip()
        except Exception:
            result = None
        if result:
            lat, lon, name, tz = result
            # 坐标就近取库内城市名，显示更友好（如"北京"而不是裸坐标）
            near = CT.nearest_city(lat, lon, max_distance_deg=3.0)
            if near is not None:
                name = near.short
                tz = near.utc_offset_hours() or tz
            self.loc_name.setText(name)
            self.lat_spin.blockSignals(True)
            self.lon_spin.blockSignals(True)
            self.lat_spin.setValue(abs(lat))
            self.lon_spin.setValue(abs(lon))
            self.lat_spin.blockSignals(False)
            self.lon_spin.blockSignals(False)
            self.chk_north.blockSignals(True)
            self.chk_east.blockSignals(True)
            self.chk_north.setChecked(lat >= 0)
            self.chk_east.setChecked(lon >= 0)
            self.chk_north.blockSignals(False)
            self.chk_east.blockSignals(False)
            self.tz_spin.setValue(tz)
            self.real_view.set_observer(lat, lon, name, tz)
            source = "IP 定位"
        else:
            self._restore_default_location_quiet()
            source = "定位回退"
        self.tour_panel.set_observer(self.real_view.observer)
        self.orrery_view.set_observer(self.real_view.observer)
        if not silent:
            self._set_status(
                f"观测点已更新：{self.real_view.observer.location_name}（{source}）"
                f"{self.real_view.observer.lat:+.4f}°, {self.real_view.observer.lon:+.4f}°")

    # ==========================================================================
    # 八、架空星图
    # ==========================================================================

    def _generate_world(self) -> None:
        """后台生成架空宇宙。"""
        if self._gen_worker is not None and self._gen_worker.isRunning():
            self._set_status("宇宙正在生成中，请稍候…")
            return
        seed = int(self.seed_spin.value())
        scale = str(self.scale_combo.currentData() or "medium")
        dist = str(self.dist_combo.currentData() or "spiral")
        self.btn_generate.setEnabled(False)
        self.gen_progress.setText("正在生成宇宙…")
        worker = WorldGenWorker(seed, scale, dist, self)
        worker.progressed.connect(self._on_gen_progress)
        worker.finished_ok.connect(self._on_gen_finished)
        worker.failed.connect(self._on_gen_failed)
        self._gen_worker = worker
        worker.start()

    def _on_gen_progress(self, percent: int, message: str) -> None:
        self.gen_progress.setText(f"{percent}%　{message}")

    def _on_gen_finished(self, galaxy: object) -> None:
        self.btn_generate.setEnabled(True)
        self.galaxy = galaxy                      # type: ignore[assignment]
        g = self.galaxy
        assert g is not None
        self.gen_progress.setText(f"完成：{g.name}")
        self.fiction_view.set_galaxy(g)
        self.info_panel.set_galaxy(g)
        self._refresh_system_combos()
        stats = g.stats()
        self._set_status(
            f"已生成 {g.name}（种子 {g.seed}）：星系 {stats.get('systems')}　"
            f"行星 {stats.get('planets')}　航线 {stats.get('routes')}　"
            f"文明 {stats.get('civilizations')}　势力 {stats.get('factions')}")
        if self.mode != "fiction":
            self.switch_mode("fiction")

    def _on_gen_failed(self, message: str) -> None:
        self.btn_generate.setEnabled(True)
        self.gen_progress.setText("生成失败")
        self._warn("宇宙生成失败", message)

    def _on_system_selected(self, sid: int) -> None:
        if self.galaxy is None:
            return
        self.info_panel.show_system(self.galaxy, int(sid))
        self._set_status(f"已选中星系：{self._sys_name(sid)}")

    def _on_route_endpoint(self, sid: int, which: str) -> None:
        """右键菜单设置了航线端点：同步下拉框，起终点齐全时自动规划。

        注意：恒星系 id 可以为 0（第一个生成的星系），因此判断端点类型必须用
        `which == "start"` 而不是 `if which:`——后者在 sid 为 0 时会误判。
        """
        is_start = (which == "start")
        if is_start:
            self.fiction_view.route_start = int(sid)
        else:
            self.fiction_view.route_goal = int(sid)
        combo = self.route_start_combo if is_start else self.route_goal_combo
        idx = combo.findData(int(sid))
        if idx >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        label = "起点" if is_start else "终点"
        self._set_status(f"航线{label}：{self._sys_name(sid)}")
        if self.fiction_view.route_start is not None \
                and self.fiction_view.route_goal is not None:
            self._plan_route()

    def _on_ship_event(self, message: str) -> None:
        """飞船事件 → 状态栏 + 日志面板。"""
        self._set_status(f"飞船事件：{message}")
        current = self.ship_log.text()
        if current.startswith("（暂无"):
            current = ""
        lines = [ln for ln in current.splitlines() if ln.strip()]
        lines.append(f"· {message}")
        self.ship_log.setText("\n".join(lines[-8:]))
        if self.galaxy is not None:
            self.ship_count_label.setText(f"飞船 {len(self.galaxy.ships)} 艘")

    def _refresh_system_combos(self) -> None:
        """刷新航线起终点下拉框。"""
        for combo in (self.route_start_combo, self.route_goal_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("（未选择）", -1)
            if self.galaxy is not None:
                for sys_obj in self.galaxy.systems:
                    combo.addItem(sys_obj.name, sys_obj.sid)
            combo.blockSignals(False)
        # ★ 只在第一次接线。原先每次刷新都先 `disconnect()` 再 `connect()`，
        #   而 Qt 在"没有任何连接时 disconnect()"会往 stderr 打
        #   `RuntimeWarning: Failed to disconnect (None) from signal "currentIndexChanged(int)"`
        #   ——启动时刷一次、每次生成宇宙再刷一次，用户看不到但日志里一直有。
        #   用一次性标志替代 try/except，既没有告警也不会重复连接。
        if not getattr(self, "_route_combos_wired", False):
            for combo in (self.route_start_combo, self.route_goal_combo):
                combo.currentIndexChanged.connect(self._on_route_combo_changed)
            self._route_combos_wired = True

    def _on_route_combo_changed(self) -> None:
        start = self.route_start_combo.currentData()
        goal = self.route_goal_combo.currentData()
        self.fiction_view.set_route(None if start in (None, -1) else int(start),
                                    None if goal in (None, -1) else int(goal))

    def _set_route_start(self, sid: int) -> None:
        idx = self.route_start_combo.findData(int(sid))
        if idx >= 0:
            self.route_start_combo.setCurrentIndex(idx)

    def _set_route_goal(self, sid: int) -> None:
        idx = self.route_goal_combo.findData(int(sid))
        if idx >= 0:
            self.route_goal_combo.setCurrentIndex(idx)

    def _locate_system(self, sid: int) -> None:
        self.fiction_view.focus_system(int(sid))

    def _sys_name(self, sid: int) -> str:
        if self.galaxy is None:
            return "—"
        sys_obj = self.galaxy.system(int(sid))
        return sys_obj.name if sys_obj else "—"

    def _plan_route(self) -> None:
        if self.galaxy is None:
            self._set_status("请先生成宇宙")
            return
        start = self.route_start_combo.currentData()
        goal = self.route_goal_combo.currentData()
        if start in (None, -1) or goal in (None, -1):
            self._set_status("请先选择航线的起点与终点")
            return
        weight = str(self.route_weight_combo.currentData() or "distance")
        result = WG.plan_route(self.galaxy, int(start), int(goal), weight=weight)
        if not result.get("ok"):
            self.route_info.setText(f"规划失败：{result.get('message', '无可行航线')}")
            self._set_status("航线规划失败：两点之间没有连通路径")
            return
        self.fiction_view.planned_route = result
        self.fiction_view.update()
        self.route_info.setText(
            f"{result.get('message')}　距离 {result.get('distance')} 光年<br>"
            f"耗时 {result.get('days')} 天　燃料 {result.get('fuel')}　"
            f"风险 {result.get('risk')}")
        self._set_status(
            f"航线已规划：{result.get('message')}　距离 {result.get('distance')} 光年　"
            f"风险 {result.get('risk')}")

    def _clear_route(self) -> None:
        self.fiction_view.planned_route = None
        self.route_start_combo.setCurrentIndex(0)
        self.route_goal_combo.setCurrentIndex(0)
        self.route_info.setText("在星图中 Shift+点击可快速设置起点 / 终点")
        self.fiction_view.update()
        self._set_status("已清除航线")

    def _dispatch_ship(self) -> None:
        if self.galaxy is None:
            self._set_status("请先生成宇宙")
            return
        start = self.route_start_combo.currentData()
        goal = self.route_goal_combo.currentData()
        if start in (None, -1) or goal in (None, -1):
            self._set_status("请先规划航线，再派遣飞船")
            return
        # 用世界生成器提供的派遣函数（FictionView 只负责绘制飞船）
        result = WG.plan_route(self.galaxy, int(start), int(goal), weight="distance")
        if not result.get("ok"):
            self._set_status("派遣失败：两点之间没有可用航线")
            return
        path = result.get("path") or [int(start), int(goal)]
        ship = WG.dispatch_ship(self.galaxy, path)
        self.fiction_view.mark_dirty()
        self.ship_count_label.setText(f"飞船 {len(self.galaxy.ships)} 艘")
        self._set_status(f"已派遣 {ship.name}：{ship.mission}，航线 {len(path)} 跳")
        self._on_ship_event(f"{ship.name} 已出发（{ship.mission}，"
                            f"航速 {ship.speed_ly_per_year:g} 光年/年）")

    def _dispatch_to_system(self, sid: int) -> None:
        self._set_route_goal(sid)
        self._dispatch_ship()

    def _on_map_mode(self, mode: str) -> None:
        for key, btn in self._map_buttons.items():
            btn.setChecked(key == mode)
        self.fiction_view.set_mode(mode)
        label = dict(MAP_MODES).get(mode, mode)
        self._set_status(f"星图模式：{label}")

    def _on_fiction_layer(self, key: str, state: int) -> None:
        self._fiction_layers[key] = bool(state)
        self.fiction_view.set_layer(key, bool(state))

    def _on_fiction_play(self, playing: bool) -> None:
        self.fiction_view.set_time_scale(self.fiction_speed)
        self.fiction_view.set_playing(bool(playing))
        self.btn_fiction_play.setText("⏸ 暂停推进" if playing else "▶ 推进时间")

    def _on_fiction_speed(self, multiplier: float) -> None:
        self.fiction_speed = float(multiplier)
        for btn, mult in zip(self._fiction_speed_buttons,
                             (1.0, 5.0, 20.0, 100.0, 500.0, 2000.0)):
            btn.setChecked(abs(mult - multiplier) < 1e-6)
        self.fiction_view.set_time_scale(self.fiction_speed)
        self.fiction_view.set_playing(self.btn_fiction_play.isChecked())
        self._set_status(f"银河历推进倍率：{multiplier:g}x")

    def _on_fiction_time(self, value: int) -> None:
        """拖动银河历时间条。"""
        if self.galaxy is None:
            return
        self.galaxy.year = float(value)
        self.fiction_view.year = float(value)
        self.galaxy_time_label.setText(f"银河历 {self.galaxy.year:.2f} 年")
        self.fiction_view.update()

    def _export_world(self) -> None:
        if self.galaxy is None:
            self._set_status("请先生成宇宙")
            return
        default = os.path.join(os.path.expanduser("~"),
                               f"stellar_world_{self.galaxy.seed}.json")
        path, _ = QFileDialog.getSaveFileName(self, "导出世界设定", default,
                                              "JSON 文件 (*.json)")
        if not path:
            return
        if save_json(path, WG.galaxy_to_dict(self.galaxy)):
            self._set_status(f"已导出世界设定：{path}")
        else:
            self._warn("导出失败", "无法写入文件，请检查路径权限。")

    def _import_world(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入世界设定", "",
                                              "JSON 文件 (*.json)")
        if path:
            self._load_from_path(path)

    def _save_slot(self, slot: int) -> None:
        if self.galaxy is None:
            self._set_status("请先生成宇宙")
            return
        name = f"world_{slot}_{self.galaxy.seed}.json"
        if save_json(os.path.join(saves_dir(), name), WG.galaxy_to_dict(self.galaxy)):
            self._refresh_save_menu()
            self._set_status(f"已保存到存档槽 {slot}")

    def _refresh_save_menu(self) -> None:
        self._save_menu.clear()
        if self.galaxy is not None:
            for slot in range(1, 4):
                self._add_action(self._save_menu, f"保存到存档槽 {slot}",
                                 lambda s=slot: self._save_slot(s))
            self._save_menu.addSeparator()
        files = list_json_files(saves_dir())
        if not files:
            act = self._save_menu.addAction("（暂无存档）")
            act.setEnabled(False)
        for name in files[:10]:
            base = os.path.basename(name)
            act = QAction(base, self)
            path = os.path.join(saves_dir(), base)
            act.triggered.connect(lambda _c=False, p=path: self._load_from_path(p))
            self._save_menu.addAction(act)

    def _load_from_path(self, path: str) -> None:
        data = load_json(path)
        galaxy = WG.galaxy_from_dict(data) if data else None
        if galaxy is None:
            self._set_status(f"读取失败：{os.path.basename(path)}")
            return
        self.galaxy = galaxy
        self.fiction_view.set_galaxy(galaxy)
        self.info_panel.set_galaxy(galaxy)
        self._refresh_system_combos()
        if self.mode != "fiction":
            self.switch_mode("fiction")
        self._set_status(f"已读取存档：{galaxy.name}（种子 {galaxy.seed}）")

    # ==========================================================================
    # 九、工具面板：日历 / 时间旅行 / 历史推演 / 导出
    # ==========================================================================

    def open_sky_calendar(self) -> None:
        """打开天象日历（未来 30 天）。"""
        try:
            dlg = SkyCalendarDialog(self.real_view.observer, self)
            dlg.time_requested.connect(self._jump_to_time)
            dlg.exec()
        except Exception as exc:
            LOG.error("打开天象日历失败", exc)
            self._warn("无法打开天象日历", str(exc))

    def open_time_travel(self) -> None:
        """打开时间旅行对话框。"""
        try:
            dlg = TimeTravelDialog(self.real_view.observer, self)
            dlg.time_requested.connect(self._jump_to_time)
            dlg.exec()
        except Exception as exc:
            LOG.error("打开时间旅行失败", exc)
            self._warn("无法打开时间旅行", str(exc))

    def open_planner(self) -> None:
        """打开观测计划表（按暗空等级 + 地平障碍算出今晚该怎么拍）。"""
        try:
            dlg = PlannerDialog(self.real_view.observer,
                                bortle=self.real_view.bortle_level,
                                horizon=self.horizon_profile, parent=self)
            dlg.exec()
        except Exception as exc:
            LOG.error("打开观测计划表失败", exc)
            self._warn("无法打开观测计划表", str(exc))

    def open_horizon_dialog(self) -> None:
        """打开地平障碍与可见性分析。"""
        try:
            dlg = HorizonDialog(self.real_view.observer, self, self.horizon_profile)
            dlg.exec()
            self.horizon_profile = dlg.profile
            self.real_view.set_horizon_profile(self.horizon_profile)
            n = len(self.horizon_profile.azimuths)
            self._set_status(f"地平障碍已应用：{n} 个采样点"
                             if n else "地平障碍已清空（理想开阔地）")
        except Exception as exc:
            LOG.error("打开地平障碍对话框失败", exc)
            self._warn("无法打开地平障碍", str(exc))

    def open_history_sim(self) -> None:
        """打开历史推演工作台（需要先有宇宙）。"""
        if self.galaxy is None:
            self._set_status("请先生成宇宙，再打开历史推演")
            self._warn("还没有宇宙", "请先在「架空星图」里点「⚡ 生成宇宙」。")
            return
        try:
            dlg = HistorySimDialog(self.galaxy, self)
            dlg.applied.connect(self._on_history_applied)
            dlg.exec()
        except Exception as exc:
            LOG.error("打开历史推演失败", exc)
            self._warn("无法打开历史推演", str(exc))

    def _on_history_applied(self, result: object) -> None:
        """推演结果写回宇宙后刷新界面。"""
        if self.galaxy is None:
            return
        snapshots = getattr(result, "snapshots", [])
        if snapshots:
            self.galaxy.year = float(snapshots[-1].year)
            self.fiction_time_slider.blockSignals(True)
            self.fiction_time_slider.setValue(int(min(500, self.galaxy.year)))
            self.fiction_time_slider.blockSignals(False)
        self.fiction_view.set_galaxy(self.galaxy)
        self.fiction_view.update()
        self._refresh_system_combos()
        self._set_status(f"历史推演结果已应用：银河历 {self.galaxy.year:.1f} 年，"
                         f"历史事件 {len(self.galaxy.history)} 条")

    def open_export_dialog(self) -> None:
        """打开导出星空图片 / 延时动画对话框。"""
        try:
            dlg = ExportDialog(self.real_view, self.real_view.observer, self)
            dlg.exec()
        except Exception as exc:
            LOG.error("打开导出对话框失败", exc)
            self._warn("无法打开导出对话框", str(exc))

    def open_inverse_sky(self, initial: object = None) -> None:
        """打开「外星视角」：从太阳邻域里的某颗恒星看天空。

        观察点的来源优先级：
            1. 调用方显式传入的星（例如将来从信息面板的按钮直接打开）
            2. 太阳邻域视图里当前选中的星
            3. 默认比邻星（由 InverseSkyDialog 自己决定）

        注意：第 2 条依赖 NearbyView.selected_star()。早期这里写成
        getattr(self.nearby_view, "selected_star", None) 探测，而那个方法当时
        并不存在，于是"选中的星"永远传不进去、静默回退到默认值——功能看起来
        没接上却不报错。现在方法已经补上，并且下面会在状态栏明确告诉用户
        观察点是哪一个，避免再次出现"以为用上了选中星"的误解。
        """
        try:
            star = None
            if initial is not None:
                star = initial
            else:
                picker = getattr(self.nearby_view, "selected_star", None)
                if callable(picker):
                    star = picker()
            if star is not None:
                name = getattr(star, "display_name", str(star))
                self._set_status(f"外星视角：以「{name}」为观察点")
            else:
                self._set_status("外星视角：未选中近星，默认以比邻星为观察点")
            dlg = InverseSkyDialog(self, initial=star)      # type: ignore[arg-type]
            dlg.exec()
        except Exception as exc:
            LOG.error("打开外星视角失败", exc)
            self._warn("无法打开外星视角", str(exc))

    def _jump_to_time(self, when: object, key: object = None) -> None:
        """时间旅行：把星图时间跳到指定时刻，并可顺带定位目标。"""
        try:
            if not isinstance(when, datetime):
                return
            if self.mode != "real":
                self.switch_mode("real")
            self.real_view.set_time(when)
            # 同步左侧时间控件（滑块表示相对现在的偏移）
            offset = (when - datetime.now()).total_seconds() / 3600.0
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(int(clamp(offset, -720, 720)))
            self.time_slider.blockSignals(False)
            self.dt_edit.blockSignals(True)
            self.dt_edit.setDateTime(QDateTime(when))
            self.dt_edit.blockSignals(False)
            # 定位目标（放在设置状态之前，避免被 find_object 的状态文案覆盖）
            name = ""
            if isinstance(key, str) and key:
                if key.startswith("planet:"):
                    name = P.PLANET_INFO.get(key.split(":", 1)[1], {}).get("name_cn", "")
                elif key == "moon":
                    name = "月球"
                    self.real_view.set_layer("sun_moon", True)
                if name:
                    self.real_view.find_object(name)
            self._set_status(f"时间旅行 → {format_datetime(when)}"
                             + (f"　并定位到 {name}" if name else ""))
            # 导览清单需要按新时间重算
            self.tour_panel.set_observer(self.real_view.observer)
        except Exception as exc:
            LOG.error("时间旅行失败", exc)
            self._set_status(f"时间旅行失败：{exc}")

    def _on_tour_target(self, payload: Dict[str, object]) -> None:
        """导览选中目标 → 星图定位并显示详情。"""
        try:
            if self.mode != "real":
                self.switch_mode("real")
            key = str(payload.get("key") or "")
            name = str(payload.get("name") or "")
            target_name = name.split(" ")[0] if name else ""
            if key.startswith("planet:"):
                target_name = P.PLANET_INFO.get(key.split(":", 1)[1], {}).get("name_cn", name)
                self.real_view.set_layer("planets", True)
            elif key == "moon":
                target_name = "月球"
                self.real_view.set_layer("sun_moon", True)
            info = self.real_view.find_object(target_name) if target_name else None
            if info is None and payload.get("ra") is not None:
                self.real_view.look_at_radec(float(payload["ra"]), float(payload["dec"]),
                                             zoom_to=min(self.real_view.fov, 40.0))
            reason = str(payload.get("reason") or "")
            self._set_status(f"导览：{name}　{reason[:60]}")
        except Exception as exc:
            LOG.error("导览定位失败", exc)

    def refresh_tour(self) -> None:
        """按当前观测点与时间刷新导览清单。"""
        self.tour_panel.set_observer(self.real_view.observer)
        n = self.tour_panel.refresh(self.real_view.observer)
        if n:
            self._set_status(f"星空导览已生成 {n} 个目标")

    # ==========================================================================
    # 九·五、太阳系仪交互
    # ==========================================================================

    def _on_orrery_selected(self, info: Dict[str, object]) -> None:
        """太阳系仪选中行星 → 信息面板。"""
        self.info_panel.show_orrery_object(info)
        self._set_status(f"已选中 {info.get('name')}：日心距 "
                         f"{float(info.get('heliocentric_au') or 0.0):.3f} AU　"
                         f"黄经 {float(info.get('ecliptic_lon') or 0.0):.1f}°")

    def _on_orrery_view_changed(self, data: Dict[str, object]) -> None:
        zoom = float(data.get("zoom", 1.0))
        value = int(clamp(zoom * 100.0, 20, 2000))
        if abs(self.orrery_zoom.value() - value) > 5:
            self.orrery_zoom.blockSignals(True)
            self.orrery_zoom.setValue(value)
            self.orrery_zoom.blockSignals(False)

    def _on_orrery_zoom(self, value: int) -> None:
        self.orrery_view.zoom = clamp(value / 100.0, 0.2, 20.0)
        self.orrery_view.mark_dirty()

    def _set_orrery_speed(self, seconds_per_real_second: float) -> None:
        """让行星时钟跑起来 / 停下来。"""
        playing = (self.real_view.time_playing
                   and abs(self.real_view.time_speed - seconds_per_real_second) < 1.0)
        if playing:
            self.real_view.time_playing = False
            self.btn_play.setChecked(False)
            self._set_status("行星时钟已停止")
            return
        self.real_view.set_time_speed(seconds_per_real_second, True)
        self.btn_play.setChecked(True)
        self._set_status(
            f"行星时钟运行中：每真实秒推进 {human_duration(seconds_per_real_second)}")

    # ==========================================================================
    # 十、太阳邻域与辅助
    # ==========================================================================

    def _on_nearby_selected(self, info: Dict[str, object]) -> None:
        """太阳邻域选中一颗近星 → 右侧信息面板。"""
        self.info_panel.show_nearby_star(info)
        self._set_status(f"已选中 {info.get('name')}（{info.get('type_cn')}，"
                         f"{float(info.get('distance_ly') or 0):.2f} 光年）")

    def _on_nearby_view_changed(self, data: Dict[str, object]) -> None:
        if self.mode != "nearby":
            return
        zoom = float(data.get("zoom", 1.0))
        value = int(clamp(zoom * 100.0, 25, 1400))
        if abs(self.nearby_zoom.value() - value) > 5:
            self.nearby_zoom.blockSignals(True)
            self.nearby_zoom.setValue(value)
            self.nearby_zoom.blockSignals(False)

    def _on_nearby_limit(self, value: int) -> None:
        self.nearby_view.set_limit(float(value))
        shown = self.nearby_view.stats()["shown"]
        text = f"显示半径：{value} 光年（{shown} 个天体）"
        if value >= int(NS.LIMIT_LY):
            text = f"显示半径：{value} 光年（全部 {shown} 个）"
        self.nearby_limit_label.setText(text)
        self._set_status(text)

    def _on_nearby_color(self, mode: str) -> None:
        for key, btn in self._nearby_color_buttons.items():
            btn.setChecked(key == mode)
        self.nearby_view.set_color_mode(mode)
        self._set_status(f"着色模式：{dict(NS.COLOR_MODES).get(mode, mode)}")

    def _on_nearby_layer(self, key: str, state: int) -> None:
        self.nearby_view.set_layer(key, bool(state))

    def _on_nearby_zoom(self, value: int) -> None:
        self.nearby_view.zoom = clamp(value / 100.0, 0.25, 14.0)
        self.nearby_view.mark_dirty()

    def _focus_selected_nearby(self) -> None:
        star = self.nearby_view._selected
        if star is None:
            self._set_status("请先在太阳邻域视图中点击一颗天体")
            return
        self.nearby_view.focus_star(star)
        self._set_status(f"已聚焦到 {star.display_name}")

    def _select_nearest_nearby(self) -> None:
        """选中离视野中心最近的一颗近星（命中半径放宽到 48 px）。

        为什么需要这个按钮：鼠标点选要求离星点 12 px 以内，而实测默认视角下
        画面正中最远的一颗星也在 48 px 以外——用户想"选一颗星再看它的天空"
        时，第一步就可能点不中，于是 `工具 → 外星视角` 只会退回默认的比邻星。
        """
        try:
            star = self.nearby_view.star_nearest_center(radius=48.0)
        except Exception as exc:
            LOG.warn(f"选中最近近星失败：{exc}")
            star = None
        if star is None:
            self._set_status("视野中心附近 48 px 内没有近星，先把视图缩小一点再试")
            return
        self.nearby_view.select_star(star)
        self.nearby_view.focus_star(star)
        self._set_status(f"已选中离中心最近的近星：{star.display_name}")

    def _on_nearby_search(self) -> None:
        text = self.nearby_search.text().strip()
        if not text:
            self.nearby_search.setFocus()
            return
        self.nearby_view.select_by_key(text)

    def _open_nearby_star(self, name: str) -> None:
        """现实星图搜索到「不在亮星表里的近星」（如比邻星）时切到太阳邻域视图。

        若这颗星已经在太阳邻域里被选中，就只给出提示而不重复切换视图，
        避免用户看到"按了没反应"。
        """
        try:
            current = self.nearby_view._selected
            if (self.mode == "nearby" and current is not None
                    and current.display_name == name):
                self._set_status(
                    f"{name} 已在太阳邻域视图中选中（{current.distance_ly:.2f} 光年）")
                return
            self.switch_mode("nearby")
            self.nearby_view.reset_view()
            self.nearby_view.select_by_key(name)
        except Exception as exc:
            LOG.error("切换到太阳邻域失败", exc)

    def _locate_nearby_in_sky(self, name: str) -> None:
        """把一颗近星定位到现实星图（按 HIP 或专名搜索）。"""
        try:
            star = NS.find_nearby(name)
            if star is None:
                self._set_status(f"未找到近星：{name}")
                return
            self.switch_mode("real")
            if star.hip:
                ref = SD.get_star(star.hip)
                if ref is not None:
                    self.real_view.find_object(ref.display_name())
                    return
            self.real_view.find_object(star.proper or star.display_name)
        except Exception as exc:
            LOG.error("在星图中定位近星失败", exc)
            self._set_status(f"定位失败：{exc}")

    # ==========================================================================
    # 十一、历史小说 / DeepSeek / 天体总览
    # ==========================================================================

    def _place_tool_window(self, win: QWidget) -> None:
        """把非模态工具窗口摆到主窗口**正中央偏上**，并夹在可用屏幕内。

        ★ 为什么需要：`NovelWriterPanel` 之前是带着 `parent=self` 的顶层窗口，
          Qt 对这类窗口的默认摆放是屏幕左上角 (0,0)——而主窗口左边正好是
          控制台面板，于是它**整个压在左侧控制台上**（用户报的"重叠了"），
          只剩下面一小条露出"应用观测点"按钮。
          现在对齐主窗口中心（工具窗口都比主窗口小），再夹进
          `availableGeometry()`，保证标题栏与右下角都在屏内。
          离屏测试平台只报 800×800 的假屏幕，那时面板比屏幕还大，
          夹取会退化成贴左上角——这是环境的限制，不是摆放逻辑的问题，
          所以断言里对此做了显式豁免。
        """
        try:
            screen = self.screen() or QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen is not None else self.frameGeometry()
            g = self.frameGeometry()
            x = g.center().x() - win.width() // 2
            y = g.center().y() - win.height() // 2
            x = max(avail.left() + 8, min(x, avail.right() - win.width() - 8))
            y = max(avail.top() + 8, min(y, avail.bottom() - win.height() - 8))
            win.move(x, y)
        except Exception as exc:            # pragma: no cover - 摆放失败不该影响功能
            LOG.warn(f"工具窗口摆放失败：{exc}")

    def open_novel_panel(self) -> None:
        """打开历史小说工作台（懒加载，复用同一个窗口）。"""
        try:
            if self._novel_window is None:
                # ★ 不传 parent：这个面板要能独立于主窗口存在、还要有自己的
                #   任务栏条目（写小说时用户会把它拖到另一个屏幕）。
                #   传 parent 时 Qt 会把它当成"主窗口的附属顶层窗口"，
                #   在 Windows 上任务栏不显示、最小化主窗口时它也跟着消失。
                self._novel_window = NovelWriterPanel(self.galaxy, None)
                self._novel_window.setWindowTitle("历史小说工作台 · DeepSeek")
                self._novel_window.setAttribute(Qt.WA_DeleteOnClose, False)
                # 非模态：写小说时要能一边看架空星图、一边改参数
                self._novel_window.setWindowModality(Qt.NonModal)
            else:
                self._novel_window.set_galaxy(self.galaxy)
            self._place_tool_window(self._novel_window)
            self._novel_window.show()
            self._novel_window.raise_()
            self._novel_window.activateWindow()
            self._refresh_deepseek_state()
            if self.galaxy is None:
                self._set_status("已打开历史小说工作台：请先在「架空星图」中生成一个宇宙")
            else:
                self._set_status(f"已打开历史小说工作台（世界观：{self.galaxy.name}）")
        except Exception as exc:
            LOG.error("打开历史小说工作台失败", exc)
            self._warn("无法打开", f"打开历史小说工作台失败：\n{exc}")

    def open_deepseek_settings(self) -> None:
        """打开 DeepSeek API 设置对话框。"""
        try:
            # ★ 第一个参数必须是 DeepSeekConfig，不是主窗口自己。
            #   这里原先写成 `DeepSeekSettingsDialog(self)`，对话框拿到主窗口当配置用，
            #   点「保存」时 `_collect()` 去读 `self.api_key` 直接 AttributeError——
            #   也就是说 **DeepSeek 设置对话框在旧版里是打不开的**（用户报的
            #   "历史小说页面加载不出来"里有一部分就是它）。
            dlg = DeepSeekSettingsDialog(DeepSeekConfig().load(), self)
            dlg.exec()
            self._refresh_deepseek_state()
            self._set_status("DeepSeek 设置已更新")
        except Exception as exc:
            LOG.error("打开 DeepSeek 设置失败", exc)
            self._warn("无法打开", f"打开 DeepSeek 设置失败：\n{exc}")

    def _refresh_deepseek_state(self) -> None:
        """刷新与 DeepSeek 相关的界面状态提示。"""
        try:
            # ★ 必须先在**实例上**调 load()。`load()` 的签名是带 self 的实例方法
            #   （不是 @classmethod），直接 `DeepSeekConfig.load()` 会
            #   TypeError: missing 1 required positional argument: 'self'，
            #   被下面的 except 吞掉——症状是按钮提示永远显示"未配置"。
            cfg = DeepSeekConfig().load()
            configured = bool(getattr(cfg, "api_key", ""))
            self.btn_home_novel.setToolTip(
                "打开历史小说工作台　·　DeepSeek："
                + ("已配置" if configured else "未配置（可用离线示例）"))
        except Exception as exc:
            LOG.warn(f"刷新 DeepSeek 状态失败：{exc}")

    def open_object_browser(self) -> None:
        """打开全天天体总览。"""
        try:
            dlg = ObjectBrowserDialog(self.real_view.observer, self)
            dlg.object_chosen.connect(self._locate_key_in_sky)
            dlg.exec()
        except Exception as exc:
            LOG.error("打开天体总览失败", exc)
            self._warn("无法打开", f"打开天体总览失败：\n{exc}")

    def _locate_key_in_sky(self, key: str) -> None:
        """按 SkyObject.key 形式在现实星图中定位目标。"""
        try:
            if key.startswith("nearby:"):
                name = key.split(":", 1)[1]
                self.switch_mode("nearby")
                self.nearby_view.select_by_key(name)
                return
            if self.mode != "real":
                self.switch_mode("real")
            kind, _, value = key.partition(":")
            if kind == "star":
                ref = SD.get_star(int(value))
                if ref is not None:
                    self.real_view.find_object(ref.display_name())
                    return
            self.real_view.find_object(value)
        except Exception as exc:
            LOG.error("在星图中定位失败", exc)
            self._set_status(f"定位失败：{exc}")

    def _show_log(self) -> None:
        entries = LOG.recent(120) if hasattr(LOG, "recent") else []
        text = "\n".join(f"[{e.level}] {e.time}　{e.message}" for e in entries) \
            if entries else "（暂无日志）"
        dlg = QDialog(self)
        dlg.setWindowTitle("运行日志")
        dlg.resize(760, 460)
        layout = QVBoxLayout(dlg)
        view = QLabel(text)
        view.setWordWrap(True)
        view.setStyleSheet("color:#dff2fb;background:#061019;padding:8px;")
        view.setFont(QFont("Consolas", 9))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(view)
        layout.addWidget(scroll, 1)
        btn = GlowButton("关闭", "cyan", compact=True)
        btn.clicked.connect(dlg.accept)
        layout.addWidget(btn)
        dlg.exec()

    def _show_data_info(self) -> None:
        stats = SD.star_catalog_stats()
        near = NS.nearby_stats()
        text = (
            f"<b>内置数据</b><br>"
            f"具名亮星 {stats.get('named_stars')} 颗　参与渲染的恒星 "
            f"{stats.get('render_stars')} 颗<br>"
            f"星座 {stats.get('constellations')} 个　连线 "
            f"{stats.get('constellation_lines')} 条<br>"
            f"深空天体 {stats.get('deep_sky')} 个（含完整 Messier 表）<br>"
            f"太阳系 10 个天体（含冥王星）<br><br>"
            f"<b>太阳邻域</b><br>"
            f"50 光年内已知天体 {near['count']} 个（有专名或 Gliese 编号 "
            f"{near['named']} 个）<br>"
            f"数据来源：{near['source']}<br><br>"
            f"<b>当前状态</b><br>"
            f"观测点：{self.real_view.observer.location_name}　"
            f"{self.real_view.observer.lat:+.4f}°, {self.real_view.observer.lon:+.4f}°<br>"
            f"观测时间：{format_datetime(self.real_view.observer.when)}<br>"
            f"光害等级：Bortle {self.real_view.bortle_level}　"
            f"极限星等 {self.real_view.mag_limit():.1f}<br>"
            f"地平障碍采样点：{len(self.horizon_profile.azimuths)} 个"
        )
        box = QMessageBox(self)
        box.setWindowTitle("数据信息")
        box.setTextFormat(Qt.RichText)
        box.setText(text)
        box.exec()

    def _show_help(self) -> None:
        text = (
            "<b>操作说明</b><br>"
            "· 左键拖拽旋转视角；右键拖拽平移；滚轮缩放；双击把天体移到中心<br>"
            "· 点击天体 → 右侧显示详情（星等、赤经赤纬、升落时刻、最高高度）<br>"
            "· 右键天体 → 上下文菜单（居中 / 放大 / 加入观测清单 / 复制坐标 / 图层）<br>"
            "· Shift+点击架空星图的恒星系 → 快速设置航线起点与终点<br><br>"
            "<b>四种模式</b><br>"
            "· 现实星图：真实天空（星座、银河、行星、日月、深空天体）<br>"
            "· 太阳系仪：俯视黄道面的行星轨道，配合时间倍率就是行星时钟<br>"
            "· 太阳邻域：50 光年内 860 个已知天体的三维分布<br>"
            "· 架空星图：程序生成的银河、文明、势力、航线与飞船<br><br>"
            "<b>快捷键</b><br>"
            "Ctrl+1 / Ctrl+4 / Ctrl+2 / Ctrl+3　现实星图 / 太阳系仪 / 太阳邻域 / 架空星图<br>"
            "Ctrl+K 天象日历　Ctrl+T 时间旅行　Ctrl+H 历史推演　Ctrl+Shift+E 导出<br>"
            "Ctrl+Shift+P 观测计划表（今晚几点看什么）<br>"
            "Ctrl+G 生成宇宙　Ctrl+P 规划航线　Ctrl+D 派遣飞船<br>"
            "Ctrl+N 历史小说工作台　Ctrl+B 全天天体总览<br>"
            "Ctrl+E / Ctrl+I 导出 / 导入世界设定　F11 全屏　F1 本说明"
        )
        box = QMessageBox(self)
        box.setWindowTitle("快捷键与操作说明")
        box.setTextFormat(Qt.RichText)
        box.setText(text)
        box.exec()

    def _show_about(self) -> None:
        stats = SD.star_catalog_stats()
        near = NS.nearby_stats()
        text = (
            f"<b>{APP_NAME}</b>　v{APP_VERSION}<br><br>"
            "纯 Python + PySide6 的天文与架空世界终端：<br>"
            f"· 现实星图：{stats.get('render_stars')} 颗恒星 / "
            f"{stats.get('constellations')} 星座 / {stats.get('deep_sky')} 深空天体<br>"
            f"· 太阳邻域：50 光年内 {near['count']} 个已知天体<br>"
            "· 架空星图：程序生成宇宙 + 历史推演 + DeepSeek 历史小说<br><br>"
            "天文算法参考：Jean Meeus《Astronomical Algorithms》、VSOP87D、"
            "ELP2000-82B 截断级数<br>"
            "近星数据：HYG 星表 v3.8（Hipparcos / Yale / Gliese）"
        )
        box = QMessageBox(self)
        box.setWindowTitle("关于")
        box.setTextFormat(Qt.RichText)
        box.setText(text)
        box.exec()

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._last_status = text

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.real_view._timer.stop()
            self.orrery_view._timer.stop()
            self.nearby_view._timer.stop()
            self.fiction_view._timer.stop()
            self._clock.stop()
            self._slow.stop()
            self._sonify_timer.stop()
        except Exception:
            pass
        # 音频要显式停：QAudioSink 不关的话进程可能挂在音频线程上不退出
        try:
            m = getattr(self, "_music", None)
            if m is not None:
                m.set_enabled(False)
                m.engine.stop()
        except Exception as exc:
            LOG.warn(f"关闭星图之声失败：{exc}")
        if self._gen_worker is not None and self._gen_worker.isRunning():
            self._gen_worker.wait(1500)
        LOG.info("程序关闭")
        super().closeEvent(event)


def run() -> int:
    """启动应用（供 main.py 调用）。"""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = MainWindow()
    win.show()
    return app.exec()


__all__ = ["MainWindow", "WorldGenWorker", "LAYER_LABELS", "run"]
