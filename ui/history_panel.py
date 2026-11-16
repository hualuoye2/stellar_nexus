# -*- coding: utf-8 -*-
"""
ui/history_panel.py —— 历史推演工作台
================================================================================
把"架空宇宙的历史事件只是一段文字"升级为可推演、可量化的过程：

    · 选择推演年数（50~1000）与三个政策参数（扩张 / 冲突 / 增长）
    · 后台线程执行推演，进度条实时反馈
    · 输出四张图：
        人口与科技折线图（双序列）
        势力版图堆叠面积图（各势力星系占比随时间的演变）
        事件分类柱状图
        历史时间轴（事件按年份铺开，可点击定位）
    · 事件清单表格（按年份 / 分类筛选）
    · 一键"应用推演结果"写回宇宙（人口 / 年份 / 势力 / 历史事件）
    · 导出推演报告（纯文本）
"""

from __future__ import annotations

import math
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar,
                               QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from 核心 import history_sim as HS
from 核心 import world_generator as WG
from 核心.utils import LOG, timestamp_tag

from .charts import AreaChart, BarChart, LineChart, Series, TimelineWidget
from .widgets import GlowButton, SectionTitle, SeparatorLine, StatBar


# ==============================================================================
# 后台推演线程
# ==============================================================================

class SimWorker(QThread):
    """在后台线程执行推演，避免界面卡死。"""

    progressed = Signal(int, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, galaxy: WG.Galaxy, years: int, seed: int,
                 expansion: float, conflict: float, growth: float,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.galaxy = galaxy
        self.years = years
        self.seed = seed
        self.expansion = expansion
        self.conflict = conflict
        self.growth = growth

    def run(self) -> None:  # noqa: D401
        try:
            sim = HS.HistorySimulator(self.galaxy, self.years, self.seed,
                                      self.expansion, self.conflict, self.growth)
            result = sim.run(progress=lambda d, t: self.progressed.emit(int(d), int(t)))
            self.finished_ok.emit(result)
        except Exception as exc:      # pragma: no cover
            LOG.error("历史推演失败", exc)
            self.failed.emit(str(exc))


# ==============================================================================
# 主对话框
# ==============================================================================

class HistorySimDialog(QDialog):
    """历史推演工作台。"""

    applied = Signal(object)          # 推演结果已应用到宇宙

    def __init__(self, galaxy: WG.Galaxy, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"历史推演 · {galaxy.name}")
        self.resize(1160, 760)
        self.galaxy = galaxy
        self.result: Optional[HS.SimResult] = None
        self._worker: Optional[SimWorker] = None
        self._build_ui()
        self._refresh_hint()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("历史推演")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title.setStyleSheet("color:#9ff6ff;background:transparent;")
        head.addWidget(title)
        self.lbl_galaxy = QLabel("")
        self.lbl_galaxy.setFont(QFont("Microsoft YaHei UI", 9))
        self.lbl_galaxy.setStyleSheet("color:#a9c9da;background:transparent;")
        head.addSpacing(10)
        head.addWidget(self.lbl_galaxy, 1)
        root.addLayout(head)
        root.addWidget(SeparatorLine())

        # ---- 参数行 ----
        params = QWidget()
        pl = QGridLayout(params)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)

        self.combo_years = QComboBox()
        for y in (50, 100, 200, 300, 500, 800, 1000):
            self.combo_years.addItem(f"{y} 年", y)
        self.combo_years.setCurrentIndex(3)          # 默认 300 年
        self.combo_years.currentIndexChanged.connect(lambda _i: self._refresh_hint())
        pl.addWidget(QLabel("推演年数"), 0, 0)
        pl.addWidget(self.combo_years, 0, 1)

        self.combo_expansion = QComboBox()
        for label, v in (("保守（扩张慢）", 0.5), ("标准", 1.0), ("激进（快速铺开）", 2.0)):
            self.combo_expansion.addItem(label, v)
        self.combo_expansion.setCurrentIndex(1)
        pl.addWidget(QLabel("扩张倾向"), 0, 2)
        pl.addWidget(self.combo_expansion, 0, 3)

        self.combo_conflict = QComboBox()
        for label, v in (("和平（少量战争）", 0.3), ("标准", 1.0), ("战乱（频繁冲突）", 2.5)):
            self.combo_conflict.addItem(label, v)
        self.combo_conflict.setCurrentIndex(1)
        pl.addWidget(QLabel("冲突倾向"), 0, 4)
        pl.addWidget(self.combo_conflict, 0, 5)

        self.combo_growth = QComboBox()
        for label, v in (("缓慢", 0.6), ("标准", 1.0), ("爆发", 1.8)):
            self.combo_growth.addItem(label, v)
        self.combo_growth.setCurrentIndex(1)
        pl.addWidget(QLabel("人口增长"), 0, 6)
        pl.addWidget(self.combo_growth, 0, 7)

        self.btn_run = GlowButton("▶ 开始推演", "violet")
        self.btn_run.clicked.connect(self.run_sim)
        pl.addWidget(self.btn_run, 0, 8)
        self.btn_apply = GlowButton("应用推演结果", "green", compact=True)
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        pl.addWidget(self.btn_apply, 0, 9)
        root.addWidget(params)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("就绪")
        self.progress.setMaximumHeight(16)
        root.addWidget(self.progress)

        self.lbl_summary = QLabel("点击「开始推演」，程序会按参数逐年演化人口、势力版图、科技与事件。")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet("color:#a9c9da;background:transparent;")
        self.lbl_summary.setFont(QFont("Microsoft YaHei UI", 9))
        root.addWidget(self.lbl_summary)

        # ---- 图表标签页 ----
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid rgba(0,170,210,140);border-radius:4px;"
            "background:rgba(4,10,18,200);}"
            "QTabBar::tab{background:rgba(8,22,34,230);color:#9fc8da;padding:5px 12px;"
            "border:1px solid rgba(0,150,190,120);border-bottom:none;"
            "border-top-left-radius:4px;border-top-right-radius:4px;}"
            "QTabBar::tab:selected{background:rgba(0,120,170,200);color:#ffffff;}"
            "QTabBar::tab:hover{background:rgba(0,150,200,170);}")

        # 1) 人口与科技
        page1 = QWidget()
        l1 = QVBoxLayout(page1)
        l1.setContentsMargins(6, 6, 6, 6)
        self.chart_pop = LineChart()
        self.chart_pop.setMinimumHeight(200)
        l1.addWidget(self.chart_pop, 3)
        row = QHBoxLayout()
        self.chk_log = QCheckBox("人口用对数刻度（看清早期变化）")
        self.chk_log.setChecked(False)
        row.addWidget(self.chk_log)
        self.chk_tech_only = QCheckBox("只显示科技曲线")
        row.addWidget(self.chk_tech_only)
        row.addStretch(1)
        l1.addLayout(row)
        self.chart_tech = LineChart()
        self.chart_tech.setMinimumHeight(130)
        l1.addWidget(self.chart_tech, 2)
        self.tabs.addTab(page1, "人口与科技")

        # 2) 势力版图
        page2 = QWidget()
        l2 = QVBoxLayout(page2)
        l2.setContentsMargins(6, 6, 6, 6)
        self.chart_area = AreaChart()
        l2.addWidget(self.chart_area, 3)
        self.chart_systems = LineChart()
        self.chart_systems.setMinimumHeight(140)
        l2.addWidget(self.chart_systems, 2)
        self.tabs.addTab(page2, "势力版图")

        # 3) 事件统计
        page3 = QWidget()
        l3 = QVBoxLayout(page3)
        l3.setContentsMargins(6, 6, 6, 6)
        self.chart_events = BarChart()
        self.chart_events.setMinimumHeight(200)
        l3.addWidget(self.chart_events, 1)
        self.lbl_stats = QTextBrowser()
        self.lbl_stats.setStyleSheet(
            "QTextBrowser{background:rgba(6,18,28,230);border:1px solid rgba(0,190,230,130);"
            "border-radius:4px;color:#e6f6fd;padding:8px;}")
        self.lbl_stats.setMaximumHeight(150)
        l3.addWidget(self.lbl_stats)
        self.tabs.addTab(page3, "事件统计")

        # 4) 时间轴
        page4 = QWidget()
        l4 = QVBoxLayout(page4)
        l4.setContentsMargins(6, 6, 6, 6)
        self.timeline = TimelineWidget()
        self.timeline.event_clicked.connect(self._on_timeline_event)
        l4.addWidget(self.timeline, 1)
        self.event_detail = QTextBrowser()
        self.event_detail.setStyleSheet(
            "QTextBrowser{background:rgba(6,18,28,230);border:1px solid rgba(0,190,230,130);"
            "border-radius:4px;color:#e6f6fd;padding:8px;}")
        self.event_detail.setMaximumHeight(110)
        l4.addWidget(self.event_detail)
        self.tabs.addTab(page4, "历史时间轴")

        # 5) 事件清单
        page5 = QWidget()
        l5 = QVBoxLayout(page5)
        l5.setContentsMargins(6, 6, 6, 6)
        filt = QHBoxLayout()
        self.combo_cat = QComboBox()
        self.combo_cat.addItem("全部分类", "")
        for cat in ("殖民", "科技", "战争", "经济", "灾变", "外交", "文明"):
            self.combo_cat.addItem(cat, cat)
        self.combo_cat.currentIndexChanged.connect(lambda _i: self._fill_events())
        filt.addWidget(QLabel("分类"))
        filt.addWidget(self.combo_cat)
        self.chk_key_only = QCheckBox("只看转折点（★★★）")
        self.chk_key_only.stateChanged.connect(lambda _s: self._fill_events())
        filt.addWidget(self.chk_key_only)
        filt.addStretch(1)
        l5.addLayout(filt)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["年份", "分类", "事件", "势力", "星系"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setStyleSheet(
            "QTableWidget{background:rgba(6,18,28,225);color:#e6f6fd;"
            "gridline-color:rgba(0,120,160,90);border:1px solid rgba(0,170,210,140);}"
            "QHeaderView::section{background:rgba(0,90,130,200);color:#eafaff;"
            "border:none;padding:4px;}")
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        l5.addWidget(self.table, 1)
        self.tabs.addTab(page5, "事件清单")

        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.btn_export = GlowButton("导出报告…", "cyan", compact=True)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export)
        bottom.addWidget(self.btn_export)
        bottom.addStretch(1)
        btn_close = GlowButton("关闭", "cyan", compact=True)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    def _refresh_hint(self) -> None:
        g = self.galaxy
        stats = g.stats()
        years = int(self.combo_years.currentData() or 300)
        self.lbl_galaxy.setText(
            f"宇宙：{g.name}　种子 {g.seed}　星系 {stats.get('systems', 0)}　"
            f"势力 {stats.get('factions', 0)}　文明 {stats.get('civilizations', 0)}　"
            f"当前人口 {stats.get('population_millions', 0):,.0f} 百万")
        self.lbl_summary.setText(
            f"将在当前状态基础上推演 {years} 年；推演不修改原宇宙，"
            f"需要写回时点「应用推演结果」。同一种子 + 同一参数 → 结果完全可复现。")

    # ---------------------------------------------------------------- 推演

    def run_sim(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        years = int(self.combo_years.currentData() or 300)
        worker = SimWorker(
            self.galaxy, years, int(self.galaxy.seed),
            float(self.combo_expansion.currentData() or 1.0),
            float(self.combo_conflict.currentData() or 1.0),
            float(self.combo_growth.currentData() or 1.0), self)
        worker.progressed.connect(self._on_progress)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self.btn_run.setEnabled(False)
        self.progress.setFormat("推演中… %p%")
        worker.start()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.btn_run.setEnabled(True)
        self.progress.setFormat("失败")
        QMessageBox.warning(self, "推演失败", message)

    @Slot(object)
    def _on_finished(self, result: object) -> None:
        self.btn_run.setEnabled(True)
        self.progress.setFormat(f"完成（{len(getattr(result, 'events', []))} 条事件）")
        self.result = result                                    # type: ignore[assignment]
        self.btn_apply.setEnabled(True)
        self.btn_export.setEnabled(True)
        self._render(result)
        self.lbl_summary.setText(getattr(result, "summary_text", lambda: "")())

    # ---------------------------------------------------------------- 渲染

    def _render(self, res: HS.SimResult) -> None:
        self._render_population(res)
        self._render_factions(res)
        self._render_events(res)
        self.timeline.set_events(
            [e for e in res.events if e.importance >= 2],
            (res.snapshots[0].year if res.snapshots else 0.0,
             res.snapshots[-1].year if res.snapshots else 1.0))
        self._fill_events()

    def _render_population(self, res: HS.SimResult) -> None:
        pop = res.series("population")
        tech = res.series("tech")
        log_mode = self.chk_log.isChecked()
        pop_pts = [(y, math.log10(max(1.0, v))) for y, v in pop] if log_mode else pop
        self.chart_pop.set_series(
            [Series("总人口（百万）", pop_pts, fill=True)],
            title=("总人口（对数刻度，log10 百万）" if log_mode else "总人口（百万）"),
            y_suffix=" 百万")
        self.chart_tech.set_series(
            [Series("平均科技指数", tech, color=(255, 214, 120), fill=True)],
            title="平均科技指数（0 → 1）", y_range=(0.0, 1.05))

    def _render_factions(self, res: HS.SimResult) -> None:
        series = []
        for name in res.faction_names:
            if name in res.collapsed:
                continue
            series.append(Series(name, res.series("systems", name),
                                 color=res.faction_colors.get(name)))
        self.chart_area.set_series(series, title="势力版图占比（按控制星系数据）",
                                   normalize=True)
        self.chart_systems.set_series(
            [Series(s.name, s.points, color=s.color) for s in series],
            title="各势力控制星系数量", y_suffix=" 个")

    def _render_events(self, res: HS.SimResult) -> None:
        from collections import Counter
        cnt = Counter(e.category for e in res.events)
        cats = [c for c in ("殖民", "科技", "战争", "经济", "灾变", "外交", "文明")
                if cnt.get(c)]
        if not cats:
            cats = list(cnt)
        self.chart_events.set_data(
            cats, [Series("事件数", [(i, cnt[c]) for i, c in enumerate(cats)])],
            title="事件分类统计")
        # 统计文本
        import statistics
        pops = [s.population for s in res.snapshots]
        growth = 0.0
        if len(pops) >= 2 and pops[0] > 0:
            growth = (pops[-1] / pops[0] - 1.0) * 100.0
        wars = cnt.get("战争", 0)
        cols = cnt.get("殖民", 0)
        techs = cnt.get("科技", 0)
        html = [
            f"<b style='color:#9ff6ff'>推演 {res.years} 年 · 种子 {res.seed}</b><br>",
            f"起始人口 {res.start_population:,.0f} 百万 → "
            f"结束 {res.end_population:,.0f} 百万"
            f"（{growth:+.0f}%，峰值 {res.peak_population:,.0f}）<br>",
            f"开始时已开发星系 {res.snapshots[0].systems_inhabited if res.snapshots else 0} → "
            f"结束时 {res.snapshots[-1].systems_inhabited if res.snapshots else 0} 个<br>",
            f"平均科技 {res.snapshots[0].tech_avg if res.snapshots else 0:.3f} → "
            f"{res.snapshots[-1].tech_avg if res.snapshots else 0:.3f}<br>",
            f"事件构成：殖民 {cols}　科技 {techs}　战争 {wars}　"
            f"经济 {cnt.get('经济', 0)}　灾变 {cnt.get('灾变', 0)}　"
            f"外交 {cnt.get('外交', 0)}　文明 {cnt.get('文明', 0)}<br>",
        ]
        if res.collapsed:
            html.append(f"<span style='color:#ffb37a'>已消亡势力："
                        f"{'、'.join(res.collapsed)}</span><br>")
        top = sorted(res.snapshots[-1].faction_population.items(),
                     key=lambda kv: -kv[1])[:4] if res.snapshots else []
        if top:
            html.append("末期人口排名：" +
                        "　".join(f"{n} {p:,.0f}" for n, p in top))
        self.lbl_stats.setHtml("".join(html))

    def _fill_events(self) -> None:
        if self.result is None:
            return
        cat = str(self.combo_cat.currentData() or "")
        key_only = self.chk_key_only.isChecked()
        rows = [e for e in self.result.events
                if (not cat or e.category == cat) and (not key_only or e.importance >= 3)]
        self.table.setRowCount(len(rows))
        for i, e in enumerate(rows):
            values = [f"{e.year:.1f}", f"{e.icon} {e.category}", e.title,
                      e.faction or "—", e.system or "—"]
            for j, v in enumerate(values):
                item = QTableWidgetItem(v)
                if j == 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                color = {"战争": (255, 150, 140), "科技": (140, 200, 255),
                         "殖民": (150, 255, 200), "灾变": (255, 160, 110),
                         "外交": (210, 170, 255), "经济": (255, 220, 140),
                         "文明": (255, 190, 230)}.get(e.category, (230, 246, 253))
                item.setForeground(QColor(*color))
                self.table.setItem(i, j, item)
        self._rows_cache = rows

    def _on_row_selected(self) -> None:
        row = self.table.currentRow()
        rows = getattr(self, "_rows_cache", [])
        if 0 <= row < len(rows):
            self._show_event(rows[row])

    def _on_timeline_event(self, event: object) -> None:
        self._show_event(event)
        # 同步高亮表格
        rows = getattr(self, "_rows_cache", [])
        for i, e in enumerate(rows):
            if e is event:
                self.table.selectRow(i)
                self.tabs.setCurrentIndex(5)
                break

    def _show_event(self, event: object) -> None:
        year = getattr(event, "year", 0.0)
        title = getattr(event, "title", "")
        detail = getattr(event, "detail", "")
        cat = getattr(event, "category", "")
        faction = getattr(event, "faction", "")
        system = getattr(event, "system", "")
        self.event_detail.setHtml(
            f"<b style='color:#9ff6ff'>{getattr(event, 'icon', '•')} {title}</b>"
            f"　<span style='color:#a9c9da'>银河历 {year:.1f} 年 · {cat}</span><br>"
            f"<span style='color:#e6f6fd'>{detail}</span><br>"
            f"<span style='color:#8fd4ea'>势力：{faction or '—'}　"
            f"星系：{system or '—'}</span>")

    # ---------------------------------------------------------------- 应用 / 导出

    def _apply(self) -> None:
        if self.result is None:
            return
        try:
            sim = HS.HistorySimulator(self.galaxy, self.result.years, self.result.seed)
            sim.apply_to_galaxy(self.result)
            self.applied.emit(self.result)
            QMessageBox.information(
                self, "已应用",
                f"推演结果已写回宇宙：\n"
                f"· 银河历推进到 {self.galaxy.year:.1f} 年\n"
                f"· 星系归属与人口已更新\n"
                f"· 新增 {len(self.result.events)} 条历史事件\n"
                f"· 消亡势力 {len(self.result.collapsed)} 个")
        except Exception as exc:
            LOG.error("应用推演结果失败", exc)
            QMessageBox.warning(self, "应用失败", str(exc))

    def _export(self) -> None:
        if self.result is None:
            return
        default = os.path.join(os.path.expanduser("~"),
                               f"history_{timestamp_tag()}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "导出推演报告", default,
                                              "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(self.result.events_text())
                fp.write("\n\n")
                fp.write("逐年数据（年份, 总人口, 平均科技, 已开发星系）\n")
                for snap in self.result.snapshots:
                    fp.write(f"{snap.year:.0f}, {snap.population:.2f}, "
                             f"{snap.tech_avg:.3f}, {snap.systems_inhabited}\n")
            QMessageBox.information(self, "导出成功", f"已导出到：\n{path}")
        except Exception as exc:
            LOG.error("导出推演报告失败", exc)
            QMessageBox.warning(self, "导出失败", str(exc))


__all__ = ["HistorySimDialog", "SimWorker"]
