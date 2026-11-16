# -*- coding: utf-8 -*-
"""
object_browser.py —— 全天天体总览（可浏览、搜索、跳转）
================================================================================
把内置的全部天体（太阳系 / 亮星 / 深空）集中在一个可搜索、可排序的表格里，
方便用户「先看清单再定位」，也直观体现本程序的天体数量与类型覆盖：

    * 太阳系页：太阳、月球与 8 大行星——带精细模型预览与关键参数
    * 亮星页：148 颗具名亮星（HIP / 中西名 / 星等 / 光谱型 / 距离 / 星座）
    * 深空页：129 个深空天体（Messier 全集 + 著名 NGC/IC），按类型着色

双击任意一行即可在主星图中定位并选中该天体。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from 核心 import astronomy as A
from 核心 import nearby_stars as NS
from 核心 import planet_data as P
from 核心 import star_data as SD
from 核心.utils import format_distance_ly, fmt_float, rgb_str

from .body_renderer import BodyModelView, deep_sky_style
from .widgets import GlowButton, SectionTitle, SeparatorLine


class ObjectBrowserDialog(QDialog):
    """全天天体总览对话框。"""

    object_chosen = Signal(str)          # 传回 SkyObject.key 形式的键

    def __init__(self, observer: Optional[A.Observer] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("全天天体总览")
        self.resize(1080, 720)
        self.observer = observer or A.Observer()
        self._build_ui()
        self._fill_solar_system()
        self._fill_stars()
        self._fill_deep_sky()
        self._fill_nearby()

    # ------------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("全天天体总览")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title.setStyleSheet("color:#9ff6ff;background:transparent;")
        head.addWidget(title)
        stats = SD.star_catalog_stats()
        info = QLabel(f"具名亮星 {stats['named_stars']}　参与渲染恒星 {stats['render_stars']}　"
                      f"星座 {stats['constellations']}（连线 {stats['constellation_lines']} 条）　"
                      f"深空天体 {stats['deep_sky']}　太阳系天体 10")
        info.setFont(QFont("Microsoft YaHei UI", 9))
        info.setStyleSheet("color:#a9d4e6;background:transparent;")
        head.addSpacing(12)
        head.addWidget(info, 1)
        root.addLayout(head)
        root.addWidget(SeparatorLine())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        # ---- 太阳系页 ----
        solar = QWidget()
        sl = QHBoxLayout(solar)
        sl.setContentsMargins(6, 6, 6, 6)
        sl.setSpacing(8)
        self.model_view = BodyModelView(solar, 260)
        self.model_view.setMinimumWidth(250)
        sl.addWidget(self.model_view, 0)
        self.solar_info = QTextBrowser()
        self.solar_info.setStyleSheet(
            "QTextBrowser{background:rgba(6,18,28,235);border:1px solid rgba(0,190,230,140);"
            "border-radius:4px;color:#e6f6fd;padding:10px;}")
        sl.addWidget(self.solar_info, 1)
        self.tabs.addTab(solar, "太阳系（10）")

        # ---- 亮星页 ----
        stars = QWidget()
        tl = QVBoxLayout(stars)
        tl.setContentsMargins(6, 6, 6, 6)
        tl.setSpacing(4)
        row = QHBoxLayout()
        self.star_filter = QLineEdit()
        self.star_filter.setPlaceholderText("按中文名 / 英文名 / HIP / 星座筛选…")
        self.star_filter.textChanged.connect(lambda _t: self._fill_stars())
        row.addWidget(self.star_filter, 1)
        tl.addLayout(row)
        self.star_table = self._make_table(["HIP", "中文名", "英文名", "星等", "光谱型",
                                            "距离", "星座", "赤经", "赤纬"])
        self.star_table.cellDoubleClicked.connect(self._on_star_activated)
        tl.addWidget(self.star_table, 1)
        self.tabs.addTab(stars, f"亮星（{SD.star_catalog_stats()['named_stars']}）")

        # ---- 深空页 ----
        dso = QWidget()
        dl = QVBoxLayout(dso)
        dl.setContentsMargins(6, 6, 6, 6)
        dl.setSpacing(4)
        drow = QHBoxLayout()
        self.dso_filter = QLineEdit()
        self.dso_filter.setPlaceholderText("按名称 / 类型 / 星座筛选，例如：星云 / M31 / 星系…")
        self.dso_filter.textChanged.connect(lambda _t: self._fill_deep_sky())
        self.dso_type = QComboBox()
        self.dso_type.addItem("全部类型", "")
        for t in sorted({m.mtype for m in SD.DEEP_SKY_CATALOG}):
            self.dso_type.addItem(t, t)
        self.dso_type.currentIndexChanged.connect(lambda _i: self._fill_deep_sky())
        drow.addWidget(self.dso_filter, 1)
        drow.addWidget(self.dso_type)
        dl.addLayout(drow)
        self.dso_table = self._make_table(["名称", "中文名", "类型", "星等", "视大小",
                                           "星座", "赤经", "赤纬", "简介"])
        self.dso_table.cellDoubleClicked.connect(self._on_dso_activated)
        dl.addWidget(self.dso_table, 1)
        self.tabs.addTab(dso, f"深空天体（{len(SD.DEEP_SKY_CATALOG)}）")

        # ---- 太阳邻域页（50 光年内全部已知天体） ----
        near = QWidget()
        nl = QVBoxLayout(near)
        nl.setContentsMargins(6, 6, 6, 6)
        nl.setSpacing(4)
        nrow = QHBoxLayout()
        self.near_filter = QLineEdit()
        self.near_filter.setPlaceholderText("按中文名 / 专名 / HIP / Gliese / 光谱型 筛选…")
        self.near_filter.textChanged.connect(lambda _t: self._fill_nearby())
        self.near_kind = QComboBox()
        for key, label in NS.KIND_OPTIONS:
            self.near_kind.addItem(label, key)
        self.near_kind.currentIndexChanged.connect(lambda _i: self._fill_nearby())
        nrow.addWidget(self.near_filter, 1)
        nrow.addWidget(self.near_kind)
        nl.addLayout(nrow)
        self.near_table = self._make_table(["专名/中文名", "编目编号", "类型", "光谱型",
                                            "距离(光年)", "视星等", "绝对星等", "星座",
                                            "银经", "银纬"])
        self.near_table.cellDoubleClicked.connect(self._on_near_activated)
        nl.addWidget(self.near_table, 1)
        self.tabs.addTab(near, f"太阳邻域 50 光年（{NS.nearby_stats()['count']}）")

        # ---- 底部按钮 ----
        bottom = QHBoxLayout()
        hint = QLabel("双击任意一行 → 在主星图中定位该天体")
        hint.setStyleSheet("color:#8fd4ea;background:transparent;")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        bottom.addWidget(hint, 1)
        self.btn_locate = GlowButton("定位选中目标", "violet", compact=True)
        self.btn_locate.clicked.connect(self._locate_current)
        btn_close = GlowButton("关闭", "cyan", compact=True)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(self.btn_locate)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    def _make_table(self, headers: List[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.setStyleSheet(
            "QTableWidget{background:rgba(6,18,28,235);alternate-background-color:rgba(10,26,40,235);"
            "color:#e6f6fd;gridline-color:rgba(0,150,190,90);border:1px solid rgba(0,190,230,140);}"
            "QHeaderView::section{background:rgba(10,30,44,245);color:#bfeaf8;"
            "border:1px solid rgba(0,150,190,120);padding:4px;font-weight:bold;}"
            "QTableWidget::item:selected{background:rgba(0,180,220,150);color:#ffffff;}")
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        return table

    # ------------------------------------------------------------------ 填充

    def _fill_solar_system(self) -> None:
        self._solar_rows: List[Tuple[str, str, Dict[str, object]]] = []
        order = ("sun", "moon", "mercury", "venus", "earth", "mars",
                 "jupiter", "saturn", "uranus", "neptune", "pluto")
        for key in order:
            if key == "sun":
                info = {"name_cn": "太阳", "name_en": "Sun", "type": "恒星",
                        "note": "太阳系唯一的恒星，占系统总质量的 99.86%"}
                self._solar_rows.append(("sun", "太阳", info))
            elif key == "moon":
                info = {"name_cn": "月球", "name_en": "Moon", "type": "卫星",
                        "note": "地球唯一的天然卫星，平均距离 384,400 公里"}
                self._solar_rows.append(("moon", "月球", info))
            elif key in P.PLANET_INFO:
                info = dict(P.PLANET_INFO[key])
                info["type"] = str(info.get("type", "行星"))
                self._solar_rows.append((f"planet:{key}", str(info.get("name_cn", key)), info))
        rows = ["<b style='color:#9ff6ff'>太阳系天体（点击左侧列表项查看精细模型）</b><br><br>"]
        for browse_key, name, info in self._solar_rows:
            body_key = browse_key.split(":")[-1]
            model = "有精细模型" if body_key in ("sun", "moon") or body_key in P.PLANET_INFO else "—"
            rows.append(f"· <b>{name}</b>（{info.get('name_en', '')}）类型：{info.get('type', '')}"
                        f"　精细模型：{model}<br><span style='color:#a9d4e6'>{info.get('note', '')}</span><br>")
        rows.append("<br><span style='color:#8fd4ea'>提示：在「现实星图」中放大到视场 &lt; 30° 时，"
                    "行星与日月会自动切换为精细球体模型。</span>")
        self.solar_info.setHtml("".join(rows))
        # 默认展示地球；双击列表交互简化为一键轮换
        self.model_view.set_body("earth", 1.0)
        self._solar_index = 4
        btn_row = QWidget()
        row_layout = QHBoxLayout(btn_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        prev_btn = GlowButton("◀ 上一个", "cyan", compact=True)
        next_btn = GlowButton("下一个 ▶", "cyan", compact=True)
        prev_btn.clicked.connect(lambda: self._cycle_solar(-1))
        next_btn.clicked.connect(lambda: self._cycle_solar(1))
        row_layout.addWidget(prev_btn)
        row_layout.addWidget(next_btn)
        row_layout.addStretch(1)
        # 插到太阳系页顶部
        solar_page = self.tabs.widget(0)
        layout = solar_page.layout()
        if layout is not None:
            layout.insertWidget(0, btn_row)
        self._solar_page_layout = layout

    def _cycle_solar(self, step: int) -> None:
        self._solar_index = (self._solar_index + step) % len(self._solar_rows)
        browse_key, name, info = self._solar_rows[self._solar_index]
        body_key = browse_key.split(":")[-1]
        self.model_view.set_body(body_key, 1.0, str(info.get("note", "")))
        detail = [f"<b style='color:#9ff6ff;font-size:13pt'>{name}</b> "
                  f"<span style='color:#a9d4e6'>{info.get('name_en', '')}</span><br><br>"]
        for k, v in info.items():
            if k in ("name_cn", "name_en"):
                continue
            label = {
                "type": "类型", "symbol": "符号", "radius_km": "赤道半径(km)",
                "mass_earth": "质量(地球=1)", "gravity": "表面重力(m/s²)",
                "rotation_hours": "自转周期(h)", "moons": "卫星数",
                "temperature_c": "平均温度(℃)", "atmosphere": "大气成分",
                "color": "参考颜色", "note": "简介",
            }.get(k, k)
            if k == "color":
                v = f"<span style='color:{rgb_str(v)}'>■</span> {rgb_str(v)}"
            detail.append(f"<b style='color:#8fd4ea'>{label}：</b>{v}<br>")
        detail.append("<br><span style='color:#8fd4ea'>在现实星图中该天体位置由 VSOP87 / "
                      "Meeus 算法实时计算。</span>")
        self.solar_info.setHtml("".join(detail))
        self._pending_key = browse_key

    def _fill_stars(self) -> None:
        text = self.star_filter.text().strip().lower()
        rows: List[Tuple] = []
        for s in SD.STAR_CATALOG:
            hay = f"{s.name} {s.name_en} {s.hip} {s.constellation}".lower()
            if text and text not in hay:
                continue
            rows.append(s)
        table = self.star_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, s in enumerate(rows):
            values = [
                str(s.hip), s.name or "—", s.name_en or "—", f"{s.mag:+.2f}",
                s.spectral or "—",
                format_distance_ly(s.distance) if s.distance else "—",
                s.constellation or "—",
                f"{s.ra / 15.0:.3f}h", f"{s.dec:+.3f}°",
            ]
            for j, v in enumerate(values):
                item = QTableWidgetItem(v)
                if j in (3, 7, 8):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if j == 3:
                    colour = max(0.0, min(1.0, (4.0 - s.mag) / 6.0))
                    item.setForeground(QColor(int(150 + 105 * colour),
                                              int(190 + 60 * colour), 255))
                table.setItem(i, j, item)
        table.setSortingEnabled(True)

    def _fill_deep_sky(self) -> None:
        text = self.dso_filter.text().strip().lower()
        type_filter = str(self.dso_type.currentData() or "")
        rows = []
        for m in SD.DEEP_SKY_CATALOG:
            if type_filter and m.mtype != type_filter:
                continue
            hay = f"{m.name} {m.name_cn} {m.name_en} {m.mtype} {m.constellation}".lower()
            if text and text not in hay:
                continue
            rows.append(m)
        table = self.dso_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, m in enumerate(rows):
            abbr, rgb = deep_sky_style(m.mtype)
            values = [
                m.name, m.name_cn, f"{m.mtype}（{abbr}）", f"{m.mag:+.1f}", m.size,
                m.constellation, f"{m.ra / 15.0:.3f}h", f"{m.dec:+.3f}°", m.desc,
            ]
            for j, v in enumerate(values):
                item = QTableWidgetItem(v)
                if j in (3, 6, 7):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if j == 2:
                    item.setForeground(QColor(rgb[0], rgb[1], rgb[2]))
                table.setItem(i, j, item)
        table.setSortingEnabled(True)

    # ------------------------------------------------------------------ 交互

    def _fill_nearby(self) -> None:
        """填充「太阳邻域」表格：50 光年内的全部已知天体。"""
        text = self.near_filter.text().strip().lower()
        kind = str(self.near_kind.currentData() or "all")
        rows = []
        for st in NS.NEARBY_CATALOG:
            if kind != "all" and st.kind != kind:
                continue
            hay = f"{st.display_name} {st.proper} {st.catalog_id} {st.spectral} {st.kind}".lower()
            if text and text not in hay:
                continue
            rows.append(st)
        table = self.near_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, st in enumerate(rows):
            rgb = st.spectral_color if not self.near_kind.currentData() else st.kind_color
            values = [
                st.display_name, st.catalog_id, st.kind, st.spectral or "—",
                f"{st.distance_ly:.2f}", f"{st.mag:+.2f}", f"{st.absmag:+.2f}",
                st.constellation_cn or st.constellation or "—",
                f"{st.galactic_l:.1f}°", f"{st.galactic_b:+.1f}°",
            ]
            for j, v in enumerate(values):
                item = QTableWidgetItem(v)
                if j in (4, 5, 6, 8, 9):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if j in (0, 3):
                    item.setForeground(QColor(rgb[0], rgb[1], rgb[2]))
                table.setItem(i, j, item)
        table.setSortingEnabled(True)

    def _on_near_activated(self, row: int, _col: int) -> None:
        item = self.near_table.item(row, 1)
        if item is None:
            return
        self.object_chosen.emit(f"nearby:{item.text().split()[0]}")
        self.accept()

    def _on_star_activated(self, row: int, _col: int) -> None:
        item = self.star_table.item(row, 0)
        if item is None:
            return
        self.object_chosen.emit(f"star:{item.text()}")
        self.accept()

    def _on_dso_activated(self, row: int, _col: int) -> None:
        item = self.dso_table.item(row, 0)
        if item is None:
            return
        self.object_chosen.emit(f"messier:{item.text()}")
        self.accept()

    def _locate_current(self) -> None:
        """定位当前标签页选中的目标。"""
        index = self.tabs.currentIndex()
        if index == 0 and getattr(self, "_pending_key", None):
            self.object_chosen.emit(str(self._pending_key))
        elif index == 1:
            row = self.star_table.currentRow()
            if row >= 0:
                self._on_star_activated(row, 0)
                return
        elif index == 2:
            row = self.dso_table.currentRow()
            if row >= 0:
                self._on_dso_activated(row, 0)
                return
        self.accept()


__all__ = ["ObjectBrowserDialog"]
