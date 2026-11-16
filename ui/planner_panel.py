# -*- coding: utf-8 -*-
"""
ui/planner_panel.py —— 观测计划表工作台
================================================================================
把「按波特尔等级与地平障碍生成的实际观测计划」做成可用的界面：

    · 顶部参数：日期、暗空等级、望远镜口径、是否含深空/亮星、是否套用地平障碍
    · 左侧表格：按评分排序的目标清单（最佳时刻 / 最佳高度+方位 / 可拍窗口 / 时长）
    · 右上：选中目标当晚的**高度曲线**（含最低可用高度线与地平障碍线）
    · 右下：详情（评分理由、观测建议）+ 今晚安排时间轴
    · 底部：导出文本 / Markdown 计划表、复制到剪贴板

计划由 核心.observation_planner 生成，本模块只负责展示与交互。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QProgressBar, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from 核心 import light_pollution as LP
from 核心 import observation_planner as OP
from 核心 import sky_events as SE
from 核心.utils import LOG, format_datetime, timestamp_tag

from .charts import LineChart, Series
from .widgets import GlowButton, SectionTitle, SeparatorLine, StatBar


class PlanWorker(QThread):
    """后台生成计划（目标多时避免界面卡顿）。"""

    progressed = Signal(int, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, observer, bortle: int, horizon, aperture: float,
                 include_dso: bool, include_stars: bool,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.observer = observer
        self.bortle = bortle
        self.horizon = horizon
        self.aperture = aperture
        self.include_dso = include_dso
        self.include_stars = include_stars

    def run(self) -> None:
        try:
            planner = OP.ObservationPlanner(self.observer, self.bortle,
                                            self.horizon, self.aperture)
            plan = planner.build(include_deep_sky=self.include_dso,
                                 include_stars=self.include_stars,
                                 progress=lambda d, t: self.progressed.emit(int(d), int(t)))
            self.finished_ok.emit(plan)
        except Exception as exc:
            LOG.error("生成观测计划失败", exc)
            self.failed.emit(str(exc))


class PlannerDialog(QDialog):
    """观测计划表工作台。"""

    def __init__(self, observer, bortle: int = 5, horizon=None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("观测计划表 · 今晚几点看什么")
        self.resize(1200, 780)
        self.observer = observer
        self.bortle = int(bortle)
        self.horizon = horizon
        self.plan: Optional[OP.ObsPlan] = None
        self._worker: Optional[PlanWorker] = None
        self._rows: List[OP.PlanTarget] = []
        self._build_ui()
        # 打开即自动生成一次
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, self.generate)

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("观测计划表")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title.setStyleSheet("color:#9ff6ff;background:transparent;")
        head.addWidget(title)
        head.addSpacing(10)
        self.lbl_head = QLabel("")
        self.lbl_head.setFont(QFont("Microsoft YaHei UI", 9))
        self.lbl_head.setStyleSheet("color:#a9c9da;background:transparent;")
        head.addWidget(self.lbl_head, 1)
        root.addLayout(head)
        root.addWidget(SeparatorLine())

        # ---- 参数 ----
        params = QWidget()
        pl = QGridLayout(params)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)

        self.spin_hour = QDoubleSpinBox()
        self.spin_hour.setRange(0.0, 23.5)
        self.spin_hour.setSingleStep(1.0)
        self.spin_hour.setDecimals(1)
        self.spin_hour.setValue(20.0)
        self.spin_hour.setSuffix(" 时")
        pl.addWidget(QLabel("观测夜起点"), 0, 0)
        pl.addWidget(self.spin_hour, 0, 1)

        self.combo_bortle = QComboBox()
        for level, text in LP.bortle_options():
            self.combo_bortle.addItem(text, level)
        idx = self.combo_bortle.findData(self.bortle)
        self.combo_bortle.setCurrentIndex(max(0, idx))
        self.combo_bortle.currentIndexChanged.connect(self._on_bortle_changed)
        pl.addWidget(QLabel("暗空等级"), 0, 2)
        pl.addWidget(self.combo_bortle, 0, 3)

        self.spin_aperture = QDoubleSpinBox()
        self.spin_aperture.setRange(0.0, 600.0)
        self.spin_aperture.setDecimals(0)
        self.spin_aperture.setSingleStep(10.0)
        self.spin_aperture.setValue(0.0)
        self.spin_aperture.setSuffix(" mm")
        self.spin_aperture.setSpecialValueText("肉眼/双筒")
        pl.addWidget(QLabel("望远镜口径"), 0, 4)
        pl.addWidget(self.spin_aperture, 0, 5)

        self.chk_horizon = QCheckBox("套用地平障碍")
        self.chk_horizon.setChecked(self.horizon is not None
                                    and not self.horizon.is_empty())
        pl.addWidget(self.chk_horizon, 0, 6)
        self.chk_dso = QCheckBox("包含深空天体")
        self.chk_dso.setChecked(True)
        pl.addWidget(self.chk_dso, 0, 7)
        self.chk_stars = QCheckBox("包含亮星")
        self.chk_stars.setChecked(True)
        pl.addWidget(self.chk_stars, 0, 8)

        self.btn_run = GlowButton("重新生成计划", "violet", compact=True)
        self.btn_run.clicked.connect(self.generate)
        pl.addWidget(self.btn_run, 0, 9)
        root.addWidget(params)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMaximumHeight(14)
        self.progress.setFormat("就绪")
        root.addWidget(self.progress)

        self.lbl_summary = QLabel("正在准备…")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet("color:#8fffc8;background:transparent;")
        self.lbl_summary.setFont(QFont("Microsoft YaHei UI", 9))
        root.addWidget(self.lbl_summary)

        # ---- 主体 ----
        body = QSplitter(Qt.Horizontal)

        # 左：目标表
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        ll.setSpacing(4)
        ll.addWidget(SectionTitle("目标清单（按评分排序）", "cyan"))
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["#", "目标", "类型", "星等", "最佳时刻", "最佳高度", "可拍窗口", "时长"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setStyleSheet(
            "QTableWidget{background:rgba(6,18,28,228);color:#e6f6fd;"
            "gridline-color:rgba(0,120,160,90);border:1px solid rgba(0,170,210,140);}"
            "QHeaderView::section{background:rgba(0,90,130,210);color:#eafaff;"
            "border:none;padding:4px;}")
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        ll.addWidget(self.table, 1)
        body.addWidget(left)

        # 右：曲线 + 详情
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        rl.addWidget(SectionTitle("高度曲线（整夜）", "violet"))
        self.chart = LineChart()
        self.chart.setMinimumHeight(210)
        rl.addWidget(self.chart, 3)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid rgba(0,170,210,140);border-radius:4px;"
            "background:rgba(4,10,18,200);}"
            "QTabBar::tab{background:rgba(8,22,34,230);color:#9fc8da;padding:4px 10px;"
            "border:1px solid rgba(0,150,190,120);border-bottom:none;}"
            "QTabBar::tab:selected{background:rgba(0,120,170,200);color:#ffffff;}")
        self.detail = QTextBrowser()
        self.detail.setStyleSheet(
            "QTextBrowser{background:rgba(6,18,28,230);border:1px solid rgba(0,190,230,130);"
            "border-radius:4px;color:#e6f6fd;padding:8px;}")
        self.tabs.addTab(self.detail, "目标详情")
        self.agenda = QTextBrowser()
        self.agenda.setStyleSheet(self.detail.styleSheet())
        self.tabs.addTab(self.agenda, "今晚安排")
        self.stats = QTextBrowser()
        self.stats.setStyleSheet(self.detail.styleSheet())
        self.tabs.addTab(self.stats, "整夜概览")
        rl.addWidget(self.tabs, 2)
        body.addWidget(right)

        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 4)
        body.setSizes([560, 620])
        root.addWidget(body, 1)

        bottom = QHBoxLayout()
        self.lbl_state = QLabel("")
        self.lbl_state.setStyleSheet("color:#8fd4ea;background:transparent;")
        self.lbl_state.setFont(QFont("Microsoft YaHei UI", 9))
        # ★ 必须允许换行。这一行会被塞进 `p.summary_text()`——计划生成之后
        #   实测 132 个字（"观测计划　北京　2026-09-23　暗空等级 Bortle 5…"），
        #   而它在一行 QHBoxLayout 里、wordWrap 默认 False，sizeHint 就是那串
        #   文字的像素宽 = 1584 px，于是**整个对话框最小宽度被撑到 1944 px**。
        #   在 1366 宽、125% 缩放的笔记本上（可用约 1312 px）窗口会超出屏幕，
        #   底部"复制计划表 / 导出 Markdown"两个按钮直接被推到屏幕外点不到。
        #   这是"不抛异常、不写日志"的缺陷，由 tools/check_dialog_widths.py 兜住。
        self.lbl_state.setWordWrap(True)
        self.lbl_state.setMinimumWidth(0)
        bottom.addWidget(self.lbl_state, 1)
        self.btn_copy = GlowButton("复制计划表", "cyan", compact=True)
        self.btn_copy.clicked.connect(lambda: self._export(copy_only=True))
        bottom.addWidget(self.btn_copy)
        self.btn_export = GlowButton("导出 Markdown…", "green", compact=True)
        self.btn_export.clicked.connect(lambda: self._export(copy_only=False))
        bottom.addWidget(self.btn_export)
        btn_close = GlowButton("关闭", "cyan", compact=True)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    def _on_bortle_changed(self) -> None:
        self.bortle = int(self.combo_bortle.currentData() or 5)

    # ---------------------------------------------------------------- 生成

    def generate(self) -> None:
        """按当前参数生成计划。"""
        if self._worker is not None and self._worker.isRunning():
            return
        base = self.observer.when.replace(hour=int(self.spin_hour.value()),
                                          minute=int((self.spin_hour.value() % 1) * 60),
                                          second=0, microsecond=0)
        from 核心 import astronomy as A
        obs = A.Observer(lat=self.observer.lat, lon=self.observer.lon,
                         location_name=self.observer.location_name,
                         utc_offset=self.observer.utc_offset, when=base)
        horizon = self.horizon if self.chk_horizon.isChecked() else None
        self.btn_run.setEnabled(False)
        self.progress.setFormat("生成中… %p%")
        worker = PlanWorker(obs, self.bortle, horizon, float(self.spin_aperture.value()),
                            self.chk_dso.isChecked(), self.chk_stars.isChecked(), self)
        worker.progressed.connect(self._on_progress)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.btn_run.setEnabled(True)
        self.progress.setFormat("失败")
        QMessageBox.warning(self, "生成失败", message)

    @Slot(object)
    def _on_finished(self, plan: object) -> None:
        self.btn_run.setEnabled(True)
        self.plan = plan                       # type: ignore[assignment]
        p = self.plan
        assert p is not None
        self.progress.setFormat(
            f"完成：{len(p.visible_targets())} 个可用目标（共评估 {len(p.targets)} 个）")
        self.lbl_head.setText(
            f"{p.observer.location_name}　{p.observer.when.strftime('%Y-%m-%d')}　"
            f"Bortle {p.bortle}　极限星等 {p.limiting_mag:.1f}"
            + (f"　口径 {p.aperture_mm:.0f}mm" if p.aperture_mm else ""))
        self.lbl_summary.setText(
            f"天文暗夜 {p.twilight_evening.strftime('%H:%M') if p.twilight_evening else '--:--'}"
            f"–{p.twilight_morning.strftime('%H:%M') if p.twilight_morning else '--:--'}"
            f"（{p.night_minutes / 60:.1f} 小时）　"
            f"月相 {p.moon_illum * 100:.0f}%"
            + (f"　月落 {p.moon_set.strftime('%H:%M')}" if p.moon_set else "")
            + "　·　" + p.weather_note)
        self._fill_table(p)
        self._fill_agenda(p)
        self._fill_stats(p)
        if self._rows:
            self.table.selectRow(0)
        self.lbl_state.setText(p.summary_text().replace("\n", "　"))

    # ---------------------------------------------------------------- 填充

    def _fill_table(self, p: OP.ObsPlan) -> None:
        self._rows = p.visible_targets()
        self.table.setRowCount(len(self._rows))
        kind_cn = {"planet": (255, 200, 150), "moon": (230, 235, 245),
                   "messier": (200, 180, 255), "star": (180, 220, 255)}
        for i, t in enumerate(self._rows):
            best = t.best_time.strftime("%H:%M") if t.best_time else "—"
            values = [str(i + 1), t.name, (t.detail or t.kind)[:12], f"{t.mag:+.1f}",
                      best, f"{t.best_alt:+.0f}°", t.window_text(), t.duration_text()]
            for j, v in enumerate(values):
                item = QTableWidgetItem(v)
                if j in (0, 3, 4, 5, 7):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if j in (0, 1, 2):
                    rgb = kind_cn.get(t.kind, (230, 246, 253))
                    item.setForeground(QColor(*rgb))
                if j == 5:
                    # 高度越高越绿
                    k = min(1.0, max(0.0, (t.best_alt - 20.0) / 60.0))
                    item.setForeground(QColor(int(255 - 130 * k), int(180 + 70 * k), 150))
                self.table.setItem(i, j, item)
        legend = "　".join(
            f"{name} {sum(1 for t in self._rows if t.kind == key)} 个"
            for key, name in (("planet", "行星"), ("moon", "月球"),
                              ("messier", "深空"), ("star", "亮星")))
        self.lbl_state.setText(f"可用目标构成：{legend}")

    def _on_row_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return
        t = self._rows[row]
        self._draw_curve(t)
        self._show_detail(t)

    def _draw_curve(self, t: OP.PlanTarget) -> None:
        """画该目标整夜的高度曲线 + 最低可用高度线 + 地平障碍线。"""
        if not t.alt_curve:
            return
        pts = [(dt.hour + dt.minute / 60.0, alt) for dt, alt in t.alt_curve]
        # 折线跨越 24 点会跳变，这里统一成"距起点小时数"
        if pts:
            t0 = pts[0][0]
            pts = [((x - t0) % 24.0, y) for x, y in pts]
        series = [Series(t.name, pts, color=(0, 229, 255), fill=True)]
        # 最低可用高度线
        min_alt = OP.MIN_ALTITUDE.get(t.kind, 20.0)
        if pts:
            series.append(Series(f"最低可用 {min_alt:.0f}°",
                                 [(pts[0][0], min_alt), (pts[-1][0], min_alt)],
                                 color=(255, 160, 120), dashed=True, width=1.2))
        # 地平障碍（只画一个平均值作为参考线）
        if self.horizon is not None and not self.horizon.is_empty():
            avg = sum(self.horizon.altitudes) / max(1, len(self.horizon.azimuths))
            series.append(Series(f"障碍平均 {avg:.0f}°",
                                 [(pts[0][0], avg), (pts[-1][0], avg)],
                                 color=(255, 110, 95), dashed=True, width=1.2))
        self.chart.set_series(series, title=f"{t.name} 整夜高度曲线（横轴：距起点小时）",
                              y_suffix="°", y_range=(-5.0, 92.0))

    def _show_detail(self, t: OP.PlanTarget) -> None:
        html = [
            f"<b style='color:#9ff6ff'>{t.name}</b>　"
            f"<span style='color:#a9c9da'>{t.detail or t.kind}　{t.mag:+.2f} 等</span><br><br>",
            f"<b>最佳时刻</b>：{t.best_text()}<br>",
            f"<b>可拍窗口</b>：{t.window_text()}　（{t.duration_text()}）<br>",
            f"<b>评分</b>：{t.score:.2f} / 1.00<br>",
        ]
        if t.reasons:
            html.append("<br><b style='color:#8fffc8'>为什么推荐</b><br>")
            html.append("<br>".join("· " + r for r in t.reasons))
        if t.notes:
            html.append(f"<br><span style='color:#ffb37a'>注意：{t.notes}</span>")
        # 观测/拍摄建议
        tips: List[str] = []
        if t.kind == "messier":
            tips.append("深空目标建议叠加多张短曝光（每张 30~120 秒），再堆栈降噪")
            tips.append("视场小的话先用低倍寻星，确认位置后再换高倍")
        elif t.kind == "planet":
            tips.append("行星拍摄用「视频抽帧」（lucky imaging）比长曝光更有效")
            tips.append("大气抖动明显时等高度再高一些（接近中天）再拍")
        elif t.kind == "moon":
            tips.append("沿明暗交界线（晨昏圈）拍摄，环形山立体感最强")
        else:
            tips.append("亮星适合拍星轨或以地景同框构图")
        if t.window_minutes < 60:
            tips.append("窗口较短，建议提前完成对焦与构图")
        html.append("<br><br><b style='color:#ffd08a'>观测建议</b><br>")
        html.append("<br>".join("· " + x for x in tips))
        self.detail.setHtml("".join(html))

    def _fill_agenda(self, p: OP.ObsPlan) -> None:
        rows = sorted(p.visible_targets(), key=lambda t: t.start_time or p.observer.when)
        if not rows:
            self.agenda.setPlainText("今晚没有满足条件的目标。可以尝试：降低暗空等级要求、"
                                     "关闭地平障碍、或换一个日期。")
            return
        html = ["<b style='color:#9ff6ff'>今晚安排</b>（按可开始观测时间排序）<br><br>"]
        for t in rows[:22]:
            start = t.start_time.strftime("%H:%M") if t.start_time else "--:--"
            html.append(
                f"<b style='color:#ffd08a'>{start}</b> 起　"
                f"<b>{t.name}</b>（{t.detail or t.kind}，{t.mag:+.1f} 等）<br>"
                f"　　最佳 {t.best_text()}　可拍 <b>{t.duration_text()}</b><br>")
        self.agenda.setHtml("".join(html))

    def _fill_stats(self, p: OP.ObsPlan) -> None:
        from collections import Counter
        kinds = Counter(t.kind for t in p.visible_targets())
        lines = [
            f"<b style='color:#9ff6ff'>{p.observer.location_name}　"
            f"{p.observer.when.strftime('%Y-%m-%d')}</b><br><br>",
            f"<b>观测窗口</b><br>",
            f"· 天文暮光结束 {p.twilight_evening.strftime('%H:%M') if p.twilight_evening else '—'}"
            f"　天文晨光开始 {p.twilight_morning.strftime('%H:%M') if p.twilight_morning else '—'}<br>",
            f"· 真正黑暗时长 <b>{p.night_minutes / 60:.1f} 小时</b><br>",
            f"· 月相 照亮 {p.moon_illum * 100:.0f}%（{SE.moon_phase_name_ex(p.moon_phase)}）"
            + (f"　月落 {p.moon_set.strftime('%H:%M')}" if p.moon_set else "")
            + "<br><br>",
            f"<b>观测条件</b><br>",
            f"· 暗空等级 Bortle {p.bortle}（{LP.bortle_class(p.bortle).name}）<br>",
            f"· 肉眼极限星等 <b>{p.limiting_mag:.1f}</b>"
            + (f"　望远镜口径 {p.aperture_mm:.0f}mm" if p.aperture_mm else "") + "<br>",
            f"· 银河可见度：{LP.bortle_class(p.bortle).milky_way}<br><br>",
            f"<b>目标构成</b>（共 {len(p.visible_targets())} 个可用）<br>",
            "· " + "　".join(f"{k} {v} 个" for k, v in
                             (("行星", kinds.get("planet", 0)), ("月球", kinds.get("moon", 0)),
                              ("深空天体", kinds.get("messier", 0)),
                              ("亮星", kinds.get("star", 0)))) + "<br>",
        ]
        best = p.visible_targets()[:3]
        if best:
            lines.append("<br><b>最推荐</b><br>")
            for i, t in enumerate(best, 1):
                lines.append(f"{i}. {t.name}　最佳 {t.best_text()}　"
                             f"可拍 {t.duration_text()}（评分 {t.score:.2f}）<br>")
        lines.append(f"<br><span style='color:#8fd4ea'>{p.weather_note}</span>")
        self.stats.setHtml("".join(lines))

    # ---------------------------------------------------------------- 导出

    def _export(self, copy_only: bool = False) -> None:
        if self.plan is None:
            return
        md = self.plan.table_text(60, markdown=True)
        text = self.plan.summary_text() + "\n\n" + md + "\n\n" + self.plan.timeline_text()
        if copy_only:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(text)
            QMessageBox.information(self, "已复制", "观测计划已复制到剪贴板（Markdown 表格）。")
            return
        default = os.path.join(os.path.expanduser("~"),
                               f"obs_plan_{timestamp_tag()}.md")
        path, _ = QFileDialog.getSaveFileName(self, "导出观测计划", default,
                                              "Markdown (*.md);;文本 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(text)
            QMessageBox.information(self, "导出成功", f"已导出到：\n{path}")
        except Exception as exc:
            LOG.error("导出观测计划失败", exc)
            QMessageBox.warning(self, "导出失败", str(exc))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(800)
        super().closeEvent(event)


__all__ = ["PlannerDialog", "PlanWorker"]
