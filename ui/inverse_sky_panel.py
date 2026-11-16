# -*- coding: utf-8 -*-
"""
ui/inverse_sky_panel.py —— 「外星视角」对话框：从别的恒星看天空
===============================================================================
把「太阳邻域（50 光年内全部天体）」这份真实三维数据**反着用**：

    站在 50 光年内的任意一颗恒星上，抬头看到的星空是什么样？

界面分三栏：
    左    观察点选择（按距离排序的近星列表 + 搜索框 + 快捷按钮）与图层开关
    中    InverseSkyView 全天球星图（可拖动旋转、滚轮缩放、点击选中）
    右    数据卡：太阳在哪里/多亮、视差位移对比、当地最亮恒星表

科学口径（界面上也会写明，避免误解）：
    * 太阳         ：位置由观察点坐标直接反算（太阳在坐标原点），
                     视星等 m = 4.83 − 5 + 5·lg(d_pc)，与真实距离一致
    * 50 光年内近星 ：用 HYG 三维坐标算**真实视差**——从比邻星看天狼，
                     方向与地球上看到的天狼差 100 多度，星座也完全不同
    * 更远的亮星    ：只有二维星表位置，按无穷远背景处理（位移通常 < 0.1°），
                     界面上会标注「星表背景（无视差）」
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QSplitter,
                               QVBoxLayout, QWidget)

from 核心 import inverse_sky as IS
from 核心 import nearby_stars as NS
from 核心.utils import LOG, clamp, format_distance_ly

from .inverse_sky_view import InverseSkyView
from .widgets import GlowButton, InfoRow, SectionTitle, SeparatorLine

#: 列表里默认列出的近星数量（按距太阳由近到远）
LIST_LIMIT = 400


class InverseSkyDialog(QDialog):
    """外星视角：从太阳邻域里的任意一颗恒星看天空。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 initial: Optional[NS.NearbyStar] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("外星视角 · 从别的恒星看天空")
        self.resize(1180, 740)
        self._sky: Optional[IS.InverseSky] = None
        self._build_ui()
        self._fill_list()
        self._pick(initial or NS.find_nearby("比邻星")
                   or (NS.NEARBY_CATALOG[0] if NS.NEARBY_CATALOG else None))

    # ==========================================================================
    # 界面
    # ==========================================================================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("外星视角　从别的恒星看天空")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title.setStyleSheet("color:#9ff6ff;background:transparent;")
        head.addWidget(title)
        self.lbl_view = QLabel("")
        self.lbl_view.setFont(QFont("Microsoft YaHei UI", 9))
        self.lbl_view.setStyleSheet("color:#a9c9da;background:transparent;")
        head.addSpacing(12)
        head.addWidget(self.lbl_view, 1)
        root.addLayout(head)
        root.addWidget(SeparatorLine())

        split = QSplitter(Qt.Horizontal)

        # ---------------- 左：观察点 ----------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        ll.setSpacing(4)

        ll.addWidget(SectionTitle("观察点", "cyan"))
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("搜索：比邻星 / Gliese 699 / HIP 87937 …")
        self.edit_search.setStyleSheet(
            "QLineEdit{background:rgba(4,16,24,230);border:1px solid rgba(0,190,230,150);"
            "border-radius:3px;color:#e6f6fd;padding:3px 6px;}")
        self.edit_search.returnPressed.connect(self._on_search)
        self.edit_search.textChanged.connect(self._on_search)
        ll.addWidget(self.edit_search)

        quick = QHBoxLayout()
        quick.setSpacing(3)
        for label, query in (("比邻星", "比邻星"), ("南门二", "南门二"),
                             ("天狼", "天狼"), ("巴纳德星", "巴纳德星"),
                             ("格雷泽 1", "Gliese 1")):
            btn = GlowButton(label, "violet", compact=True)
            btn.clicked.connect(lambda _c=False, q=query: self._quick(q))
            quick.addWidget(btn)
        ll.addLayout(quick)

        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget{background:rgba(6,18,28,230);border:1px solid rgba(0,170,210,150);"
            "border-radius:3px;color:#e6f6fd;font-size:9pt;}"
            "QListWidget::item{padding:2px 4px;border-bottom:1px solid rgba(0,120,160,50);}"
            "QListWidget::item:selected{background:rgba(0,190,230,150);color:#02121a;}")
        self.list.currentItemChanged.connect(lambda *_a: self._on_list_changed())
        self.list.itemDoubleClicked.connect(lambda *_a: self._on_list_changed())
        ll.addWidget(self.list, 1)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color:#8fb6c9;background:transparent;font-size:8pt;")
        ll.addWidget(self.lbl_count)

        ll.addWidget(SeparatorLine("violet"))
        ll.addWidget(SectionTitle("图层", "violet"))
        self.checks: Dict[str, QCheckBox] = {}
        for key, text, default in (
                ("show_local", "50 光年内近星（有真实视差）", True),
                ("show_atlas", "远处亮星背景（无视差）", True),
                ("show_galaxy", "银河带", True),
                ("show_ecliptic", "黄道", True),
                ("show_grid", "赤经赤纬网格", True),
                ("show_constellation_lines", "星座连线（地球人的划法）", True),
                ("show_labels", "星名标注", True),
                ("night_mode", "红光夜间模式", False)):
            cb = QCheckBox(text)
            cb.setChecked(default)
            cb.setStyleSheet("QCheckBox{color:#cfe9f5;background:transparent;font-size:9pt;}")
            cb.toggled.connect(lambda v, k=key: self._on_layer(k, v))
            self.checks[key] = cb
            ll.addWidget(cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("星等上限"))
        self.combo_mag = QComboBox()
        for m in (4.0, 5.0, 6.0, 6.5, 7.5, 9.0, 11.0):
            self.combo_mag.addItem(f"{m:g} 等", m)
        self.combo_mag.setCurrentIndex(4)
        self.combo_mag.currentIndexChanged.connect(lambda _i: self._recompute())
        row.addWidget(self.combo_mag, 1)
        ll.addLayout(row)

        split.addWidget(left)

        # ---------------- 中：星图 ----------------
        self.view = InverseSkyView()
        self.view.object_clicked.connect(self._on_object_clicked)
        split.addWidget(self.view)

        # ---------------- 右：数据卡 ----------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        rl.setSpacing(3)

        rl.addWidget(SectionTitle("结论", "cyan"))
        self.summary = QLabel("—")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#dff3fb;background:rgba(4,16,24,180);"
                                   "border:1px solid rgba(0,150,190,110);border-radius:3px;"
                                   "padding:5px 6px;font-size:9pt;")
        rl.addWidget(self.summary)

        rl.addWidget(SeparatorLine())
        rl.addWidget(SectionTitle("太阳", "cyan"))
        self.rows: Dict[str, InfoRow] = {}
        for key, label in (("dist", "距观察点"), ("mag", "视星等"), ("where", "所在星座"),
                           ("radec", "赤经赤纬"), ("earth", "那里的地球")):
            r = InfoRow(label)
            self.rows[key] = r
            rl.addWidget(r)

        rl.addWidget(SeparatorLine("violet"))
        rl.addWidget(SectionTitle("视差有多大", "violet"))
        self.parallax = QLabel("—")
        self.parallax.setWordWrap(True)
        self.parallax.setStyleSheet("color:#c9e6f2;background:transparent;font-size:8pt;")
        rl.addWidget(self.parallax)

        rl.addWidget(SeparatorLine("violet"))
        rl.addWidget(SectionTitle("当地最亮的星", "violet"))
        self.bright = QListWidget()
        self.bright.setStyleSheet(
            "QListWidget{background:rgba(6,18,28,225);border:1px solid rgba(140,90,220,140);"
            "border-radius:3px;color:#e6f6fd;font-size:9pt;}"
            "QListWidget::item{padding:2px 4px;}"
            "QListWidget::item:selected{background:rgba(160,110,255,150);}")
        self.bright.setMinimumHeight(150)
        self.bright.itemClicked.connect(self._on_bright_clicked)
        self.bright.itemDoubleClicked.connect(self._on_bright_clicked)
        rl.addWidget(self.bright, 1)

        rl.addWidget(SeparatorLine())
        hint = QLabel("操作：左键拖动旋转　滚轮缩放　单击选中　双击居中　右键菜单")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#7fa8bb;background:transparent;font-size:8pt;")
        rl.addWidget(hint)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([300, 620, 300])
        root.addWidget(split, 1)

        foot = QHBoxLayout()
        btn_close = GlowButton("关闭", "cyan", compact=True)
        btn_close.clicked.connect(self.accept)
        foot.addStretch(1)
        foot.addWidget(btn_close)
        root.addLayout(foot)

    # ==========================================================================
    # 观察点列表
    # ==========================================================================

    def _fill_list(self, query: str = "") -> None:
        """按搜索词填充观察点列表；空搜索时列出最近的 LIST_LIMIT 颗。"""
        self.list.blockSignals(True)
        self.list.clear()
        if query.strip():
            hits = NS.search_nearby(query.strip(), limit=200)
            rows = hits
        else:
            rows = sorted(NS.NEARBY_CATALOG, key=lambda s: s.distance_ly)[:LIST_LIMIT]
        for s in rows:
            item = QListWidgetItem(
                f"{s.display_name}　{format_distance_ly(s.distance_ly)}　"
                f"{s.kind or s.spectral or ''}")
            item.setForeground(QColor(230, 246, 253))
            item.setData(Qt.UserRole, s)
            item.setToolTip(f"{s.display_name}\n{s.catalog_id}\n"
                            f"距太阳 {s.distance_ly:.3f} 光年\n"
                            f"视星等（地球）{s.mag:+.2f}　绝对星等 {s.absmag:+.2f}\n"
                            f"{s.spectral}")
            self.list.addItem(item)
        total = len(NS.NEARBY_CATALOG)
        self.lbl_count.setText(
            f"共 {total} 个近星天体；当前显示 {self.list.count()} 条"
            + ("（搜索结果）" if query.strip() else f"（最近的 {LIST_LIMIT} 颗）"))
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _on_search(self) -> None:
        self._fill_list(self.edit_search.text())

    def _quick(self, query: str) -> None:
        self.edit_search.setText(query)

    def _on_list_changed(self) -> None:
        star = self._current_star()
        if star is not None:
            self._pick(star)

    def _current_star(self) -> Optional[NS.NearbyStar]:
        item = self.list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return data if isinstance(data, NS.NearbyStar) else None

    # ==========================================================================
    # 计算与展示
    # ==========================================================================

    def _pick(self, star: Optional[NS.NearbyStar]) -> None:
        if star is None:
            return
        self._star = star
        self._recompute()

    def _recompute(self) -> None:
        star = getattr(self, "_star", None)
        if star is None:
            return
        mag_limit = float(self.combo_mag.currentData() or 7.5)
        try:
            sky = IS.compute_inverse_sky(star, mag_limit=mag_limit)
        except Exception as exc:
            LOG.error("计算外星视角失败", exc)
            QMessageBox.warning(self, "计算失败", str(exc))
            return
        self._sky = sky
        self.view.set_sky(sky)
        self._update_panels()

    def _on_layer(self, key: str, value: bool) -> None:
        setattr(self.view, key, bool(value))
        self.view.update()

    def _update_panels(self) -> None:
        sky = self._sky
        if sky is None or sky.sun is None:
            return
        star = getattr(self, "_star", None)
        name = star.display_name if star is not None else sky.viewpoint_name
        self.lbl_view.setText(
            f"观察点：{name}　距太阳 {sky.viewpoint_dist:.3f} 光年　"
            f"视场 {self.view.fov():.0f}°　当地亮于 {sky.limiting_mag:g} 等的恒星 "
            f"{len(sky.stars)} 颗")

        self.summary.setText(
            f"从 <b>{name}</b> 抬头看：太阳是一颗 <b>{sky.sun.app_mag:+.2f} 等</b>的星，"
            f"位于<b>{sky.sun.constellation_cn()}</b>。<br>"
            f"视野里有 {len(sky.local)} 颗 50 光年内的近星（位置含真实视差），"
            f"另有 {len(sky.atlas)} 颗更远的亮星按背景处理。")

        self.rows["dist"].set_value(f"{sky.viewpoint_dist:.3f} 光年"
                                    + ("（同一天体系统内）" if sky.viewpoint_dist < 0.5 else ""))
        self.rows["mag"].set_value(f"{sky.sun.app_mag:+.2f} 等"
                                   + ("　（比地球上看天狼还亮）" if sky.sun.app_mag < -0.5
                                      else "　（肉眼清晰可见）" if sky.sun.app_mag < 6.0
                                      else "　（需要双筒/望远镜）"))
        self.rows["where"].set_value(sky.sun.constellation_cn())
        self.rows["radec"].set_value(f"RA {sky.sun.ra:.2f}°　Dec {sky.sun.dec:+.2f}°")
        if sky.earth_app_mag is not None:
            self.rows["earth"].set_value(
                f"视星等 {sky.earth_app_mag:+.2f}（与太阳同方向，最多偏 1 角秒量级）")

        self.parallax.setText(self._parallax_text())

        self.bright.clear()
        for s in sky.brightest(16):
            item = QListWidgetItem(f"{s.name}　{s.app_mag:+.2f} 等　"
                                   f"{s.constellation_cn()}　"
                                   f"{('%.2f ly' % s.distance_ly) if s.distance_ly else '背景'}")
            item.setForeground(QColor(230, 246, 253))
            item.setData(Qt.UserRole, s)
            self.bright.addItem(item)

    def _parallax_text(self) -> str:
        """算几条有代表性的视差位移，让"换了个地方看"变成数字。"""
        sky = self._sky
        if sky is None:
            return "—"
        lines: List[str] = []
        # 挑几颗著名的：天狼、南门二、大角、织女一、巴纳德星
        wanted = ("天狼", "南门二", "大角", "织女一", "巴纳德星", "比邻星")
        seen = set()
        for w in wanted:
            target = None
            for s in sky.stars:
                if s.name == w and s.layer == "local":
                    target = s
                    break
            if target is None or w in seen:
                continue
            seen.add(w)
            ra0, dec0 = self._earth_position(target)
            if ra0 is None:
                continue
            sep = _sep(target.ra, target.dec, ra0, dec0)
            if not lines:
                lines.append("与地球视角相比：")
            lines.append(f"　{w}：{sep:.1f}°（{target.constellation_cn()}）")
        if not lines:
            lines.append("该观察点附近没有可用于对比的近星。")
        return "\n".join(lines)

    @staticmethod
    def _earth_position(s: IS.SeenStar) -> Tuple[Optional[float], Optional[float]]:
        """把当地坐标换算回「地球视角」坐标，用于算视差位移。

        观察点在 (vx, vy, vz)，星的坐标是 (x, y, z)，都在**赤道直角系**里
        （HYG 的 x/y/z 实为赤道直角坐标，见 inverse_sky.direction_from_offsets）。
        地球视角下太阳在原点，所以直接看 (x, y, z) 的方向即可。
        """
        src = s.source
        if src is None or s.layer != "local":
            return (None, None)
        return IS.direction_from_offsets(src.x, src.y, src.z)

    # ==========================================================================
    # 交互
    # ==========================================================================

    def _on_object_clicked(self, obj: object) -> None:
        """星图上点了某颗星 → 高亮右侧列表里的同名项。"""
        if not isinstance(obj, IS.SeenStar):
            return
        for i in range(self.bright.count()):
            item = self.bright.item(i)
            got = item.data(Qt.UserRole)
            if got is obj:
                self.bright.setCurrentRow(i)
                return

    def _on_bright_clicked(self, item: QListWidgetItem) -> None:
        s = item.data(Qt.UserRole)
        if isinstance(s, IS.SeenStar):
            self.view.select(s, recenter=True)


def _sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """两点角距（度）。"""
    d1, d2 = math.radians(dec1), math.radians(dec2)
    dl = math.radians(ra1 - ra2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dl)
    return math.degrees(math.acos(clamp(c, -1.0, 1.0)))


__all__ = ["InverseSkyDialog"]
