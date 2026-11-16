# -*- coding: utf-8 -*-
"""
ui/sky_calendar.py —— 天象日历 / 时间旅行 / 地平障碍编辑器
================================================================================
本模块提供三个对话框：

    SkyCalendarDialog   未来 30 天的天象日历（月相、行星、流星雨、节气）
                        · 点击任意事件 → 一键把星图时间跳到那一刻并定位目标
                        · 底部显示月相条与"今晚概览"
                        · 支持导出/复制日历文本

    TimeTravelDialog    输入任意时间表达 → 跳转
                        · 自然语言：下次满月 / 火星冲日 / 金星大距 / 英仙座流星雨 / 冬至
                        · 日期：2026-08-12 / 20260812 / 8月12日 / 今晚 21:30 / +7d

    HorizonDialog       地平障碍编辑器（本地星空模拟器）
                        · 方位-高度极坐标图，拖动鼠标绘制障碍轮廓
                        · 预设一键套用（城市楼群 / 北侧有山 / 阳台…）
                        · 输入目标天体 → 给出全年可见性报告（月度分布 + 最佳月份）
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QTextBrowser, QVBoxLayout,
                               QWidget)

from 核心 import astronomy as A
from 核心 import planet_data as P
from 核心 import sky_events as SE
from 核心 import star_data as SD
from 核心.utils import (LOG, app_dir, azimuth_to_compass, clamp, ensure_dir, load_json,
                        save_json, timestamp_tag)

from .widgets import (CollapsiblePanel, GlowButton, InfoRow, NeonSlider, SectionTitle,
                      SeparatorLine, StatBar)

#: 地平剖面配置文件（放在应用目录，随程序携带）
HORIZON_FILE = "horizon_profile.json"


# ==============================================================================
# 一、天象日历
# ==============================================================================

class SkyCalendarDialog(QDialog):
    """未来 30 天的天象日历。"""

    time_requested = Signal(object, object)      # (datetime, 目标 key 或 None)

    def __init__(self, observer: A.Observer, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("天象日历 · 未来 30 天")
        self.resize(940, 660)
        self.observer = observer
        self._events: List[SE.SkyEvent] = []
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("天象日历")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title.setStyleSheet("color:#9ff6ff;background:transparent;")
        head.addWidget(title)
        self.lbl_range = QLabel("")
        self.lbl_range.setFont(QFont("Microsoft YaHei UI", 9))
        self.lbl_range.setStyleSheet("color:#a9c9da;background:transparent;")
        head.addSpacing(10)
        head.addWidget(self.lbl_range, 1)
        self.spin_days = QSpinBox()
        self.spin_days.setRange(7, 120)
        self.spin_days.setValue(30)
        self.spin_days.setSuffix(" 天")
        self.spin_days.valueChanged.connect(lambda _v: self.reload())
        head.addWidget(self.spin_days)
        btn_reload = GlowButton("重新计算", "cyan", compact=True)
        btn_reload.clicked.connect(self.reload)
        head.addWidget(btn_reload)
        root.addLayout(head)
        root.addWidget(SeparatorLine())

        body = QSplitter(Qt.Horizontal)

        # 左：事件列表
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        ll.setSpacing(4)
        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget{background:rgba(6,18,28,230);border:1px solid rgba(0,170,210,150);"
            "border-radius:3px;color:#e6f6fd;font-size:10pt;}"
            "QListWidget::item{padding:4px 6px;border-bottom:1px solid rgba(0,120,160,60);}"
            "QListWidget::item:selected{background:rgba(0,190,230,150);color:#02121a;}")
        self.list.itemDoubleClicked.connect(self._on_event_activated)
        self.list.itemClicked.connect(self._on_event_activated)
        ll.addWidget(self.list, 1)

        row = QHBoxLayout()
        btn_go = GlowButton("⏱ 跳到该时刻并定位", "violet")
        btn_go.clicked.connect(lambda: self._on_event_activated(self.list.currentItem()))
        row.addWidget(btn_go)
        ll.addLayout(row)
        row2 = QHBoxLayout()
        btn_copy = GlowButton("复制日历文本", "cyan", compact=True)
        btn_copy.clicked.connect(self._copy_text)
        btn_export = GlowButton("导出…", "cyan", compact=True)
        btn_export.clicked.connect(self._export)
        row2.addWidget(btn_copy)
        row2.addWidget(btn_export)
        ll.addLayout(row2)
        body.addWidget(left)

        # 右：详情 + 月相
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        rl.addWidget(SectionTitle("事件详情", "cyan"))
        self.detail = QTextBrowser()
        self.detail.setStyleSheet(
            "QTextBrowser{background:rgba(6,18,28,235);border:1px solid rgba(0,190,230,140);"
            "border-radius:4px;color:#e6f6fd;padding:10px;}")
        self.detail.setMinimumHeight(180)
        rl.addWidget(self.detail, 1)

        rl.addWidget(SectionTitle("未来 30 天月相", "violet"))
        self.phase_widget = MoonPhaseStrip(self)
        rl.addWidget(self.phase_widget)

        rl.addWidget(SectionTitle("今晚概览", "green"))
        self.tonight = QTextBrowser()
        self.tonight.setStyleSheet(
            "QTextBrowser{background:rgba(6,18,28,235);border:1px solid rgba(0,190,230,140);"
            "border-radius:4px;color:#e6f6fd;padding:8px;}")
        self.tonight.setMinimumHeight(120)
        rl.addWidget(self.tonight, 1)
        body.addWidget(right)

        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        body.setSizes([560, 360])
        root.addWidget(body, 1)

        bottom = QHBoxLayout()
        hint = QLabel("单击事件即可把星图时间跳到那一刻并自动定位目标天体")
        hint.setStyleSheet("color:#8fd4ea;background:transparent;")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        bottom.addWidget(hint, 1)
        btn_close = GlowButton("关闭", "cyan", compact=True)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # ---- 数据 ----

    def reload(self) -> None:
        """重新计算日历。"""
        days = int(self.spin_days.value())
        self.lbl_range.setText(f"{self.observer.location_name}　"
                               f"{self.observer.when.strftime('%Y-%m-%d %H:%M')} 起")
        self._events = SE.build_calendar(self.observer, days)
        self.list.clear()
        for e in self._events:
            star = "★" * min(3, max(1, e.importance - 1))
            item = QListWidgetItem(f"{e.when_text()}  {e.icon} {e.title}　{star}")
            color = {"月相": (200, 225, 255), "行星": (255, 214, 150),
                     "流星雨": (180, 255, 210), "太阳": (255, 240, 190)}.get(
                         e.category, (230, 246, 253))
            item.setForeground(QColor(*color))
            item.setToolTip(e.detail)
            self.list.addItem(item)
        self.phase_widget.set_observer(self.observer)
        self.phase_widget.set_days(min(days, 40))
        self._fill_tonight()
        if self._events:
            self.list.setCurrentRow(0)
            self._show_detail(self._events[0])

    def _fill_tonight(self) -> None:
        """今晚概览：月相、行星可见性、推荐关注。"""
        try:
            obs = self.observer
            jd = obs.jd_ut
            moon = A.moon_position(jd)
            phase_name = SE.moon_phase_name_ex(moon["elongation"])
            lines = [f"<b style='color:#9ff6ff'>{obs.when.strftime('%Y-%m-%d %H:%M')}"
                     f"　{obs.location_name}</b><br>"]
            lines.append(f"月相：{phase_name}（照亮 {float(moon['phase']) * 100:.0f}%，"
                         f"高度 {obs.altitude(moon['ra'], moon['dec']):+.0f}°）<br>")
            # 行星
            parts = []
            for key in P.NAKED_EYE_PLANETS:
                p = A.planet_position(key, jd)
                alt = obs.altitude(p["ra"], p["dec"])
                if alt > 0:
                    parts.append(f"{P.PLANET_INFO[key]['name_cn']}（{alt:.0f}°）")
            lines.append("可见行星：" + ("、".join(parts) if parts else "无") + "<br>")
            # 最近事件
            upcoming = [e for e in self._events if e.when >= obs.when][:3]
            if upcoming:
                lines.append("<br><b style='color:#ffd08a'>接下来</b><br>")
                for e in upcoming:
                    lines.append(f"· {e.when_text()}　{e.icon} {e.title}<br>")
            self.tonight.setHtml("".join(lines))
        except Exception as exc:
            LOG.warn(f"今晚概览生成失败：{exc}")
            self.tonight.setPlainText("无法生成今晚概览")

    def _show_detail(self, event: SE.SkyEvent) -> None:
        if event is None:
            return
        html = [
            f"<div style='font-size:13pt;color:#9ff6ff'>{event.icon} {event.title}</div>",
            f"<p style='color:#a9c9da'>{event.when.strftime('%Y-%m-%d %H:%M:%S')}"
            f"　（{event.category}）</p>",
            f"<p style='line-height:1.9;color:#e6f6fd'>{event.detail}</p>",
        ]
        extra = _event_extra_tips(event, self.observer)
        if extra:
            html.append(f"<p style='color:#8fffc8;line-height:1.9'>{extra}</p>")
        self.detail.setHtml("".join(html))

    def _on_event_activated(self, item: Optional[QListWidgetItem]) -> None:
        if item is None:
            return
        row = self.list.row(item)
        if row < 0 or row >= len(self._events):
            return
        event = self._events[row]
        self._show_detail(event)
        self.time_requested.emit(event.when, event.key or None)

    def _calendar_text(self) -> str:
        header = (f"天象日历　{self.observer.location_name}　"
                  f"{self.observer.when.strftime('%Y-%m-%d')} 起 "
                  f"{self.int_days()} 天")
        return header + "\n" + SE.calendar_text(self._events, 200)

    def int_days(self) -> int:
        return int(self.spin_days.value())

    def _copy_text(self) -> None:
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self._calendar_text())
        QMessageBox.information(self, "已复制", "天象日历文本已复制到剪贴板。")

    def _export(self) -> None:
        default = os.path.join(os.path.expanduser("~"),
                               f"sky_calendar_{timestamp_tag()}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "导出天象日历", default,
                                              "文本文件 (*.txt);;Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(self._calendar_text())
            QMessageBox.information(self, "导出成功", f"已导出到：\n{path}")
        except Exception as exc:
            LOG.error("导出天象日历失败", exc)
            QMessageBox.warning(self, "导出失败", str(exc))


def _event_extra_tips(event: SE.SkyEvent, observer: A.Observer) -> str:
    """给事件补充"怎么观测"的建议。

    注意：只有带目标坐标的事件才谈得上"该时刻高度"；月相/流星雨这类
    事件并没有单一目标，硬套升落信息会误导（早期版本就是这样）。
    """
    tips: List[str] = []
    try:
        if event.ra is not None and event.dec is not None:
            alt, az = observer.radec_to_altaz(event.ra, event.dec)
            if alt > 0:
                tips.append(f"该时刻目标高度 {alt:+.0f}°，方位{azimuth_to_compass(az)}")
            else:
                info = A.rise_set_info(event.ra, event.dec, observer)
                tips.append(f"该时刻目标在地平线下，{info.describe()}")
        if event.category == "流星雨":
            tips.append("观测建议：避开城市灯光，辐射点升高后（后半夜）数量最多，"
                        "肉眼平躺仰视即可，无需望远镜")
        elif event.category == "月相":
            if event.title == "满月":
                tips.append("观测建议：满月适合看月海与辐射纹，但不适合拍深空天体")
            elif event.title == "新月":
                tips.append("观测建议：无月光的夜晚是拍星云、星团与银河的最佳窗口")
            else:
                tips.append("观测建议：上/下弦月看明暗交界处的环形山立体感最强")
        elif event.category == "行星":
            if "冲日" in event.title or "大距" in event.title:
                tips.append("观测建议：木星看四大伽利略卫星，土星看光环，"
                            "金星在相位接近半圆时最有趣")
            elif "合月" in event.title:
                tips.append("观测建议：用双筒或长焦镜头拍「行星伴月」，构图很出片")
    except Exception:
        pass
    return "<br>".join("· " + t for t in tips)


class MoonPhaseStrip(QWidget):
    """月相条：把未来若干天的照亮比例画成一条曲线 + 月相圆点。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)
        self._observer: Optional[A.Observer] = None
        self._series: List[Tuple[datetime, float]] = []
        self._days = 30

    def set_observer(self, observer: A.Observer) -> None:
        self._observer = observer

    def set_days(self, days: int) -> None:
        self._days = max(7, int(days))
        if self._observer is not None:
            base = self._observer.when
            series: List[Tuple[datetime, float]] = []
            for i in range(self._days + 1):
                t = base + timedelta(days=i)
                jd = A.julian_date(t) - self._observer.utc_offset / 24.0
                moon = A.moon_position(jd)
                series.append((t, float(moon["phase"])))
            self._series = series
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(6, 18, 28, 235))
        p.setPen(QPen(QColor(0, 170, 210, 140), 1.0))
        p.drawRect(0, 0, w - 1, h - 1)
        if not self._series:
            p.setPen(QColor(160, 200, 220))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(self.rect(), Qt.AlignCenter, "月相数据准备中…")
            p.end()
            return
        n = len(self._series)
        pad = 10.0
        plot_h = h - 34
        # 曲线
        path = QPainterPath()
        for i, (_t, phase) in enumerate(self._series):
            x = pad + (w - 2 * pad) * i / max(1, n - 1)
            y = plot_h - plot_h * phase + 4
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        grad = QLinearGradient(0, 0, 0, plot_h)
        grad.setColorAt(0.0, QColor(255, 240, 190, 210))
        grad.setColorAt(1.0, QColor(120, 150, 200, 180))
        p.setPen(QPen(QBrush(grad), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # 里程碑圆点（新月/满月）
        p.setFont(QFont("Microsoft YaHei UI", 8))
        for i in range(1, n):
            prev, cur = self._series[i - 1][1], self._series[i][1]
            for target, name, color in ((0.0, "新月", (150, 200, 255)),
                                        (1.0, "满月", (255, 240, 190))):
                if target == 1.0:
                    crossed = prev < 0.93 <= cur or prev > 0.93 >= cur
                else:
                    crossed = prev > 0.07 >= cur or prev < 0.07 <= cur
                if crossed:
                    x = pad + (w - 2 * pad) * i / max(1, n - 1)
                    y = plot_h - plot_h * cur + 4
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(*color, 230))
                    p.drawEllipse(QPointF(x, y), 3.6, 3.6)
                    p.setPen(QColor(*color, 240))
                    p.drawText(QPointF(x - 12, plot_h + 22),
                               self._series[i][0].strftime("%m-%d"))
        # 坐标说明
        p.setPen(QColor(170, 210, 235, 220))
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.drawText(QPointF(pad, h - 6), "照亮比例 0% → 100%")
        p.drawText(QPointF(w - 130, h - 6),
                   f"{self._series[0][0].strftime('%m-%d')} → "
                   f"{self._series[-1][0].strftime('%m-%d')}")
        p.end()


# ==============================================================================
# 二、时间旅行
# ==============================================================================

class TimeTravelDialog(QDialog):
    """输入任意时间表达并跳转。"""

    time_requested = Signal(object, object)      # (datetime, key 或 None)

    def __init__(self, observer: A.Observer, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("时间旅行")
        self.resize(620, 520)
        self.observer = observer
        self._parsed: Optional[datetime] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addWidget(SectionTitle("输入时间或天象"))
        hint = QLabel(SE.time_travel_hint())
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#a9c9da;background:transparent;")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        root.addWidget(hint)

        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("例如：下次满月　/　火星冲日　/　2026-08-12 21:30　/　今晚")
        self.edit.setMinimumHeight(32)
        self.edit.returnPressed.connect(self._try_parse)
        self.edit.textChanged.connect(self._try_parse)
        row.addWidget(self.edit, 1)
        root.addLayout(row)

        self.result = QLabel("等待输入…")
        self.result.setWordWrap(True)
        self.result.setStyleSheet("color:#9ff6ff;background:rgba(6,18,28,200);"
                                  "border:1px solid rgba(0,170,210,140);border-radius:3px;"
                                  "padding:8px;")
        self.result.setFont(QFont("Microsoft YaHei UI", 10))
        root.addWidget(self.result)

        root.addWidget(SectionTitle("快捷跳转", "violet"))
        grid = QWidget()
        from PySide6.QtWidgets import QGridLayout
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(4)
        shortcuts = [
            ("今晚 22:00", "今晚 22:00"), ("明晚 22:00", "明天 22:00"),
            ("下次满月", "下次满月"), ("下次新月", "下次新月"),
            ("火星冲日", "火星冲日"), ("木星冲日", "木星冲日"),
            ("英仙座流星雨", "英仙座流星雨"), ("双子座流星雨", "双子座流星雨"),
            ("冬至", "冬至"), ("夏至", "夏至"),
        ]
        for i, (label, expr) in enumerate(shortcuts):
            btn = GlowButton(label, "cyan", compact=True)
            btn.clicked.connect(lambda _c=False, e=expr: self._use(e))
            gl.addWidget(btn, i // 4, i % 4)
        root.addWidget(grid)

        root.addWidget(SectionTitle("历史时刻", "orange"))
        hist = QWidget()
        hl = QHBoxLayout(hist)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        for label, expr in (("阿波罗11号着陆", "1969-07-20 20:17"),
                            ("哈雷彗星回归", "1986-02-09 00:00"),
                            ("首次月背软着陆", "2019-01-03 10:26"),
                            ("旅行者1号发射", "1977-09-05 12:56")):
            btn = GlowButton(label, "orange", compact=True)
            btn.clicked.connect(lambda _c=False, e=expr: self._use(e))
            hl.addWidget(btn)
        hl.addStretch(1)
        root.addWidget(hist)

        root.addStretch(1)
        bottom = QHBoxLayout()
        self.lbl_state = QLabel("")
        self.lbl_state.setStyleSheet("color:#ffb37a;background:transparent;")
        self.lbl_state.setFont(QFont("Microsoft YaHei UI", 9))
        bottom.addWidget(self.lbl_state, 1)
        btn_go = GlowButton("⏱ 跳到该时刻", "violet")
        btn_go.clicked.connect(self._accept)
        bottom.addWidget(btn_go)
        btn_cancel = GlowButton("取消", "cyan", compact=True)
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)
        root.addLayout(bottom)
        self.edit.setFocus()

    def _use(self, expr: str) -> None:
        self.edit.setText(expr)
        self._try_parse()

    def _try_parse(self) -> None:
        text = self.edit.text().strip()
        if not text:
            self._parsed = None
            self.result.setText("等待输入…")
            self.lbl_state.setText("")
            return
        when = SE.parse_time_expression(text, self.observer)
        if when is None:
            self._parsed = None
            self.result.setText("无法解析这个时间表达")
            self.lbl_state.setText("试试「下次满月」「火星冲日」或「2026-08-12 21:30」")
            return
        self._parsed = when
        delta = when - self.observer.when
        days = delta.total_seconds() / 86400.0
        if abs(days) < 1:
            span = f"{delta.total_seconds() / 3600:+.1f} 小时"
        elif abs(days) < 400:
            span = f"{days:+.1f} 天"
        else:
            span = f"{days / 365.25:+.2f} 年"
        self.result.setText(
            f"<b style='color:#9ff6ff'>{when.strftime('%Y-%m-%d %H:%M:%S')}</b>"
            f"　<span style='color:#a9c9da'>（相对当前 {span}）</span>")
        # 该时刻的天象提示
        try:
            jd = A.julian_date(when) - self.observer.utc_offset / 24.0
            moon = A.moon_position(jd)
            sun = A.sun_position(jd)
            phase = SE.moon_phase_name_ex(moon["elongation"])
            obs = A.Observer(lat=self.observer.lat, lon=self.observer.lon, when=when,
                             utc_offset=self.observer.utc_offset)
            sun_alt = obs.altitude(sun["ra"], sun["dec"])
            day_night = "白昼" if sun_alt > 0 else "夜间"
            self.lbl_state.setText(f"该时刻为{day_night}，月相 {phase}"
                                   f"（太阳高度 {sun_alt:+.0f}°）")
            self.lbl_state.setStyleSheet("color:#8fffc8;background:transparent;")
        except Exception:
            self.lbl_state.setText("")

    def _accept(self) -> None:
        if self._parsed is None:
            self._try_parse()
        if self._parsed is None:
            QMessageBox.information(self, "无法解析", "请检查输入，或点击下面的快捷按钮。")
            return
        key = self._detect_target_key()
        self.time_requested.emit(self._parsed, key)
        self.accept()

    def _detect_target_key(self) -> Optional[str]:
        """根据输入判断要不要顺带定位某个天体。"""
        t = self.edit.text()
        for word, key in (("满月", "moon"), ("新月", "moon"), ("上弦", "moon"),
                          ("下弦", "moon"), ("月", "moon")):
            if word in t:
                return key
        for key, cn in SE.PLANET_CN.items():
            if cn in t:
                return f"planet:{key}"
        for name, (mon, day), _z, _ra, _dec, _p in SE.METEOR_SHOWERS:
            if name.replace("流星雨", "") in t:
                return None          # 流星雨辐射点不是单体目标，只跳时间
        return None


# ==============================================================================
# 三、地平障碍编辑器
# ==============================================================================

class HorizonChart(QWidget):
    """方位-高度极坐标图：拖动鼠标绘制障碍轮廓。"""

    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.profile = SE.HorizonProfile()
        self._dragging = False
        self._hover_az: Optional[float] = None

    def set_profile(self, profile: SE.HorizonProfile) -> None:
        self.profile = profile
        self.update()
        self.changed.emit()

    # ---- 坐标换算：屏幕 ↔ (方位, 高度) ----
    def _center_radius(self) -> Tuple[QPointF, float]:
        c = QPointF(self.width() / 2.0, self.height() / 2.0)
        r = min(self.width(), self.height()) / 2.0 - 18.0
        return c, max(20.0, r)

    def _screen_to_polar(self, pos: QPointF) -> Tuple[float, float]:
        c, r = self._center_radius()
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        dist = math.hypot(dx, dy)
        alt = clamp(dist / r * 90.0, 0.0, 89.0)
        # 屏幕 y 向下 → 方位角从正北（上）顺时针
        az = (math.degrees(math.atan2(dx, -dy))) % 360.0
        return (az, alt)

    def _polar_to_screen(self, az: float, alt: float) -> QPointF:
        c, r = self._center_radius()
        rr = r * clamp(alt, 0.0, 90.0) / 90.0
        a = math.radians(az)
        return QPointF(c.x() + rr * math.sin(a), c.y() - rr * math.cos(a))

    # ---- 交互 ----
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._apply(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        az, alt = self._screen_to_polar(event.position())
        self._hover_az = az
        if self._dragging:
            self._apply(event.position())
        else:
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = False
        self.changed.emit()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_az = None
        self.update()

    def _apply(self, pos: QPointF) -> None:
        az, alt = self._screen_to_polar(pos)
        self.profile.add(az, alt)
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        grad = QRadialGradient(QPointF(w / 2.0, h / 2.0), max(w, h) * 0.6)
        grad.setColorAt(0.0, QColor(10, 26, 42))
        grad.setColorAt(1.0, QColor(4, 10, 18))
        p.fillRect(self.rect(), QBrush(grad))
        c, r = self._center_radius()

        # 高度圈
        p.setFont(QFont("Microsoft YaHei UI", 8))
        for alt in (30, 60, 90):
            rr = r * alt / 90.0
            p.setPen(QPen(QColor(0, 150, 200, 70 if alt < 90 else 110), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, rr, rr)
            p.setPen(QColor(140, 190, 215, 190))
            p.drawText(QPointF(c.x() + 3, c.y() - rr + 11), f"{alt}°")
        # 方位线
        for az, name in ((0, "北"), (90, "东"), (180, "南"), (270, "西")):
            pt = self._polar_to_screen(az, 90.0)
            p.setPen(QPen(QColor(0, 140, 190, 80), 1.0))
            p.drawLine(c, pt)
            label_pt = self._polar_to_screen(az, 100.0)
            p.setPen(QColor(170, 220, 240, 230))
            p.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
            fm = QFontMetrics(p.font())
            p.drawText(QPointF(label_pt.x() - fm.horizontalAdvance(name) / 2.0,
                               label_pt.y() + 4), name)
            p.setFont(QFont("Microsoft YaHei UI", 8))

        # 障碍轮廓
        if not self.profile.is_empty():
            path = QPainterPath()
            path.moveTo(c)
            for az in range(0, 361, 2):
                alt = self.profile.altitude_at(float(az))
                pt = self._polar_to_screen(float(az), alt)
                path.lineTo(pt)
            path.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 130, 90, 70))
            p.drawPath(path)
            p.setPen(QPen(QColor(255, 150, 100, 220), 1.8))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            # 采样点
            p.setBrush(QColor(255, 200, 120, 240))
            for az, alt in zip(self.profile.azimuths, self.profile.altitudes):
                pt = self._polar_to_screen(az, alt)
                p.drawEllipse(pt, 3.0, 3.0)

        # 悬停提示
        if self._hover_az is not None:
            p.setPen(QColor(200, 240, 255, 230))
            p.setFont(QFont("Microsoft YaHei UI", 8))
            p.drawText(QPointF(8, 16), f"方位 {self._hover_az:.0f}°"
                                       f"（{azimuth_to_compass(self._hover_az)}）")
        p.setPen(QColor(150, 200, 225, 200))
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.drawText(QPointF(8, h - 8), "按住左键在图上拖动即可绘制障碍轮廓（红区为遮挡区）")
        p.end()


class HorizonDialog(QDialog):
    """地平障碍编辑器 + 全年可见性报告。"""

    def __init__(self, observer: A.Observer, parent: Optional[QWidget] = None,
                 profile: Optional[SE.HorizonProfile] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("本地星空模拟器 · 地平障碍与可见性")
        self.resize(1000, 680)
        self.observer = observer
        self.profile = profile or load_horizon_profile()
        self._build_ui()
        self._apply_to_chart()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("地平障碍与可见性分析")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title.setStyleSheet("color:#9ff6ff;background:transparent;")
        head.addWidget(title)
        head.addSpacing(10)
        self.lbl_info = QLabel(f"观测点：{self.observer.location_name}　"
                               f"{self.observer.lat:+.3f}°, {self.observer.lon:+.3f}°")
        self.lbl_info.setFont(QFont("Microsoft YaHei UI", 9))
        self.lbl_info.setStyleSheet("color:#a9c9da;background:transparent;")
        head.addWidget(self.lbl_info, 1)
        root.addLayout(head)
        root.addWidget(SeparatorLine())

        body = QSplitter(Qt.Horizontal)

        # 左：极坐标图 + 预设
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        ll.setSpacing(6)
        self.chart = HorizonChart()
        self.chart.changed.connect(self._on_chart_changed)
        ll.addWidget(self.chart, 1)
        ll.addWidget(SectionTitle("预设场景", "orange"))
        preset_row = QWidget()
        from PySide6.QtWidgets import QGridLayout
        pl = QGridLayout(preset_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        for i, (name, _pts) in enumerate(SE.HORIZON_PRESETS):
            btn = GlowButton(name, "orange", compact=True)
            btn.clicked.connect(lambda _c=False, n=name: self._use_preset(n))
            pl.addWidget(btn, i // 2, i % 2)
        ll.addWidget(preset_row)
        row = QHBoxLayout()
        btn_clear = GlowButton("清空障碍", "red", compact=True)
        btn_clear.clicked.connect(self._clear)
        btn_save = GlowButton("保存为我的观测点", "green", compact=True)
        btn_save.clicked.connect(self._save)
        row.addWidget(btn_clear)
        row.addWidget(btn_save)
        ll.addLayout(row)
        body.addWidget(left)

        # 右：可见性报告
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        rl.addWidget(SectionTitle("全年可见性", "cyan"))

        pick = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setEditable(True)
        for st in SD.STAR_CATALOG[:60]:
            self.combo.addItem(st.display_name(), st.hip)
        for key, cn in SE.PLANET_CN.items():
            self.combo.addItem(f"{cn}（行星）", f"planet:{key}")
        self.combo.addItem("月球", "moon")
        pick.addWidget(QLabel("目标"))
        pick.addWidget(self.combo, 1)
        btn_calc = GlowButton("分析", "violet", compact=True)
        btn_calc.clicked.connect(self._analyze)
        pick.addWidget(btn_calc)
        rl.addLayout(pick)

        metric_row = QHBoxLayout()
        self.spin_step = QSpinBox()
        self.spin_step.setRange(5, 120)
        self.spin_step.setValue(30)
        self.spin_step.setSuffix(" 分钟/采样")
        metric_row.addWidget(QLabel("精度"))
        metric_row.addWidget(self.spin_step)
        metric_row.addStretch(1)
        rl.addLayout(metric_row)

        self.report = QTextBrowser()
        self.report.setStyleSheet(
            "QTextBrowser{background:rgba(6,18,28,235);border:1px solid rgba(0,190,230,140);"
            "border-radius:4px;color:#e6f6fd;padding:10px;line-height:1.8;}")
        rl.addWidget(self.report, 1)

        rl.addWidget(SectionTitle("月度可观测小时", "green"))
        self.bars = QWidget()
        from PySide6.QtWidgets import QVBoxLayout as _V
        bl = _V(self.bars)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(1)
        self._bar_widgets: List[StatBar] = []
        for _ in range(12):
            bar = StatBar("", (120, 220, 160))
            self._bar_widgets.append(bar)
            bl.addWidget(bar)
        rl.addWidget(self.bars)
        body.addWidget(right)

        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 1)
        body.setSizes([480, 500])
        root.addWidget(body, 1)

        bottom = QHBoxLayout()
        self.lbl_state = QLabel("修改障碍后点「分析」查看该天体的全年可观测时长")
        self.lbl_state.setStyleSheet("color:#8fd4ea;background:transparent;")
        self.lbl_state.setFont(QFont("Microsoft YaHei UI", 9))
        bottom.addWidget(self.lbl_state, 1)
        btn_close = GlowButton("关闭", "cyan", compact=True)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # ---- 交互 ----

    def _apply_to_chart(self) -> None:
        self.chart.set_profile(self.profile)
        self._on_chart_changed()

    def _on_chart_changed(self) -> None:
        n = len(self.profile.azimuths)
        if n == 0:
            self.lbl_state.setText("当前无遮挡（理想开阔地）")
        else:
            avg = sum(self.profile.altitudes) / n
            worst = max(self.profile.altitudes)
            self.lbl_state.setText(
                f"{n} 个采样点　平均遮挡 {avg:.1f}°　最大 {worst:.1f}°")

    def _use_preset(self, name: str) -> None:
        self.profile = SE.build_horizon_preset(name)
        self.chart.set_profile(self.profile)

    def _clear(self) -> None:
        self.profile = SE.HorizonProfile()
        self.chart.set_profile(self.profile)

    def _save(self) -> None:
        if save_horizon_profile(self.profile):
            QMessageBox.information(
                self, "已保存",
                f"地平障碍已保存到：\n{os.path.join(app_dir(), HORIZON_FILE)}\n\n"
                "下次启动会自动载入。")
        else:
            QMessageBox.warning(self, "保存失败", "无法写入配置文件，请检查目录权限。")

    def _analyze(self) -> None:
        """计算并显示某目标的全年可见性。"""
        data = self.combo.currentData()
        text = self.combo.currentText().strip()
        ra = dec = None
        name = text
        if isinstance(data, int) and 0 < data < 900000:
            st = SD.get_star(int(data))
            if st is not None:
                ra, dec, name = st.ra, st.dec, st.display_name()
        elif isinstance(data, str) and data.startswith("planet:"):
            key = data.split(":", 1)[1]
            p = A.planet_position(key, self.observer.jd_ut)
            ra, dec, name = p["ra"], p["dec"], SE.PLANET_CN.get(key, key)
        elif data == "moon":
            m = A.moon_position(self.observer.jd_ut)
            ra, dec, name = m["ra"], m["dec"], "月球"
        else:
            hit = SD.search_targets()
            low = text.lower()
            for display, kind, key in hit:
                if display.lower() == low or low in display.lower():
                    if kind == "star":
                        st = SD.get_star(int(key.split(":")[1]))
                        if st:
                            ra, dec, name = st.ra, st.dec, st.display_name()
                    break
        if ra is None or dec is None:
            self.report.setHtml("<p style='color:#ffb37a'>无法识别这个目标，"
                                "请从下拉列表中选择，或输入亮星名（如「天狼」）。</p>")
            return
        self.lbl_state.setText(f"正在分析 {name} …（可能耗时 1~3 秒）")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        rep = SE.visibility_report(ra, dec, name, self.observer, self.profile,
                                   float(self.spin_step.value()))
        self._show_report(rep)
        self.lbl_state.setText(rep.describe())

    def _show_report(self, rep: SE.VisibilityReport) -> None:
        clear_ratio = (rep.clear_hours / rep.total_hours * 100.0) if rep.total_hours else 0.0
        html = [
            f"<div style='font-size:13pt;color:#9ff6ff'>{rep.name}</div>",
            f"<p style='line-height:1.9'>{rep.describe()}</p>",
            "<table style='color:#e6f6fd' cellspacing='6'>",
            f"<tr><td>地平线上总时长</td><td><b>{rep.total_hours:.0f}</b> 小时/年</td></tr>",
            f"<tr><td>扣掉遮挡后</td><td><b style='color:#8fffc8'>{rep.clear_hours:.0f}</b>"
            f" 小时/年（{clear_ratio:.0f}%）</td></tr>",
            f"<tr><td>全年最高高度</td><td>{rep.max_altitude:.1f}°</td></tr>",
            f"<tr><td>最佳月份</td><td>{rep.best_month}（{rep.best_hours:.0f} 小时）</td></tr>",
            f"<tr><td>是否拱极</td><td>{'是' if rep.circumpolar else '否'}</td></tr>",
            "</table>",
        ]
        if rep.never_rises:
            html.append("<p style='color:#ffb37a'>该天体在此纬度永不升起，无法观测。</p>")
        elif not self.profile.is_empty() and rep.total_hours > 0:
            lost = rep.total_hours - rep.clear_hours
            html.append(f"<p style='color:#ffd08a'>因地平障碍损失约 {lost:.0f} 小时"
                        f"（{lost / max(1.0, rep.total_hours) * 100:.0f}%）。"
                        f"若搬到开阔地可多得这些时间。</p>")
        self.report.setHtml("".join(html))
        for i, (month, hours) in enumerate(rep.monthly_hours[:12]):
            peak = max((h for _m, h in rep.monthly_hours), default=1.0) or 1.0
            self._bar_widgets[i].set_value(hours / peak)
        # StatBar 的标签需要单独设置
        for i, (month, hours) in enumerate(rep.monthly_hours[:12]):
            bar = self._bar_widgets[i]
            bar._label = f"{month}"       # 直接写入，重绘即可显示
            bar.setToolTip(f"{month}：{hours:.0f} 小时")
            bar.update()


# ==============================================================================
# 四、地平剖面持久化
# ==============================================================================

def save_horizon_profile(profile: SE.HorizonProfile, path: str = "") -> bool:
    """保存地平剖面到 JSON。"""
    try:
        target = path or os.path.join(app_dir(), HORIZON_FILE)
        data = profile.to_dict()
        if not data.get("azimuths"):
            data["note"] = data.get("note", "") or "（未设置障碍）"
        return save_json(target, data)
    except Exception as exc:
        LOG.error("保存地平剖面失败", exc)
        return False


def load_horizon_profile(path: str = "") -> SE.HorizonProfile:
    """载入地平剖面（不存在时返回空剖面）。"""
    try:
        target = path or os.path.join(app_dir(), HORIZON_FILE)
        if not os.path.isfile(target):
            return SE.HorizonProfile()
        return SE.HorizonProfile.from_dict(load_json(target, default={}))
    except Exception as exc:
        LOG.warn(f"载入地平剖面失败：{exc}")
        return SE.HorizonProfile()


__all__ = [
    "SkyCalendarDialog", "TimeTravelDialog", "HorizonDialog", "HorizonChart",
    "MoonPhaseStrip", "save_horizon_profile", "load_horizon_profile", "HORIZON_FILE",
]
