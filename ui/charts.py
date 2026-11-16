# -*- coding: utf-8 -*-
"""
ui/charts.py —— 数据可视化控件（折线图 / 堆叠面积图 / 时间轴 / 柱状图）
================================================================================
纯 QPainter 手绘，风格与程序其余部分一致（深色科幻 HUD 风），不引入任何绘图库。

    LineChart        多序列折线图，支持填充、网格、坐标轴、图例、悬停数值
    AreaChart        堆叠面积图（用于"势力版图占比"这类构成变化）
    BarChart         分组/单序列柱状图（用于月度分布、事件分类统计）
    TimelineWidget   横向时间轴：把历史事件按年份摆开，可点击定位

设计要点：
    * 所有控件都用 `set_data()` 一次性灌数据，paintEvent 只负责画
    * X 轴支持"数值轴（年份）"与"分类轴（月份/名称）"两种模式
    * 数值标签自动避开边界，图例放在不遮挡曲线的位置
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import QWidget

from 核心.utils import LOG, clamp

PALETTE: Tuple[Tuple[int, int, int], ...] = (
    (0, 229, 255),      # 青
    (255, 138, 101),    # 橙
    (140, 255, 190),    # 绿
    (200, 140, 255),    # 紫
    (255, 214, 120),    # 黄
    (255, 120, 170),    # 粉
    (120, 190, 255),    # 蓝
    (180, 255, 120),    # 黄绿
)


def series_color(index: int, override: Optional[Sequence[int]] = None) -> QColor:
    """按序号取调色板颜色（可被势力自身颜色覆盖）。"""
    rgb = tuple(override) if override else PALETTE[index % len(PALETTE)]
    return QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))


@dataclass
class Series:
    """一条曲线。"""

    name: str
    points: List[Tuple[float, float]] = field(default_factory=list)
    color: Optional[Tuple[int, int, int]] = None
    fill: bool = False
    dashed: bool = False
    width: float = 1.8


class _ChartBase(QWidget):
    """图表基类：负责坐标轴、网格、边距与悬停。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.setMouseTracking(True)
        self.title = ""
        self.x_label = ""
        self.y_label = ""
        self.y_min: Optional[float] = None
        self.y_max: Optional[float] = None
        self.y_suffix = ""
        self.x_is_numeric = True
        self.legend = True
        self._hover_x: Optional[float] = None
        self._plot = QRectF()

    # ---- 公共绘图工具 ----

    def _margins(self) -> Tuple[float, float, float, float]:
        return (58.0, 14.0, 12.0, 30.0)      # 左、上、右、下

    def _setup_plot(self) -> QRectF:
        left, top, right, bottom = self._margins()
        self._plot = QRectF(left, top,
                            max(10.0, self.width() - left - right),
                            max(10.0, self.height() - top - bottom))
        return self._plot

    def _draw_frame(self, p: QPainter, y_ticks: int = 4) -> None:
        """画背景、网格、坐标轴。"""
        plot = self._plot
        grad = QLinearGradient(plot.topLeft(), plot.bottomLeft())
        grad.setColorAt(0.0, QColor(10, 26, 42, 200))
        grad.setColorAt(1.0, QColor(5, 14, 24, 220))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(plot.adjusted(-1, -1, 1, 1), 4, 4)
        # 水平网格与 Y 轴刻度
        p.setFont(QFont("Consolas", 8))
        lo, hi = self._y_range()
        span = max(1e-9, hi - lo)
        for i in range(y_ticks + 1):
            k = i / y_ticks
            y = plot.bottom() - plot.height() * k
            value = lo + span * k
            p.setPen(QPen(QColor(0, 150, 200, 46), 1.0))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(150, 200, 225, 210))
            p.drawText(QRectF(0, y - 8, plot.left() - 6, 16),
                       Qt.AlignRight | Qt.AlignVCenter, self._fmt_y(value))
        p.setPen(QPen(QColor(0, 170, 210, 150), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(plot.adjusted(-0.5, -0.5, 0.5, 0.5), 4, 4)

    def _y_range(self) -> Tuple[float, float]:
        lo, hi = self.y_min, self.y_max
        if lo is None or hi is None:
            values = [v for _x, v in self._all_points()]
            if not values:
                return (0.0, 1.0)
            lo = min(values) if lo is None else lo
            hi = max(values) if hi is None else hi
            if abs(hi - lo) < 1e-9:
                hi = lo + 1.0
            pad = (hi - lo) * 0.10
            lo, hi = lo - pad, hi + pad
            lo = min(lo, 0.0) if lo > -0.001 else lo
        return (float(lo), float(hi))

    def _fmt_y(self, value: float) -> str:
        if abs(value) >= 10000:
            return f"{value / 1000:.0f}k"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        if abs(value) >= 100:
            return f"{value:.0f}"
        if abs(value) >= 10:
            return f"{value:.1f}"
        return f"{value:.2f}"

    def _all_points(self) -> List[Tuple[float, float]]:
        return []

    def _series(self) -> List[Series]:
        return []

    # ---- 交互：悬停显示数值 ----

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._plot.contains(event.position()):
            self._hover_x = event.position().x()
        else:
            self._hover_x = None
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_x = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        """绘制图表。

        这里**必须**记录异常，不能静默吞掉。四个子类（LineChart / AreaChart /
        BarChart / TimelineWidget）被历史推演曲线、月相条、可见性月度分布、
        时间线共用，任何一处画错都会表现为"这张图是空的"——而"图是空的"最
        容易被误判成"数据没算出来"，于是排查方向整个跑偏。第一版写成
        `except Exception: pass`，等于把四个绘图 bug 一起藏起来了。
        """
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.fillRect(self.rect(), QColor(4, 10, 18))
            if self.title:
                p.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
                p.setPen(QColor(160, 240, 255))
                p.drawText(QPointF(8, 12), self.title)
            self._paint_content(p)
        except Exception as exc:
            LOG.error(f"{type(self).__name__} 绘制失败：{exc}")
            # 除了日志，还要在画布上留下可见痕迹：
            # 用户看到"图表区域里有红字"就知道是绘制出错，而不是数据为空。
            try:
                p.setFont(QFont("Microsoft YaHei UI", 9))
                p.setPen(QColor(255, 120, 120))
                p.drawText(QRectF(6, 6, max(10.0, self.width() - 12),
                                  max(10.0, self.height() - 12)),
                           Qt.AlignCenter,
                           f"图表绘制失败\n{type(exc).__name__}: {exc}")
            except Exception:
                pass          # 连兜底提示都画不出来时不要再抛，否则 paintEvent 会死循环
        finally:
            p.end()

    def _paint_content(self, p: QPainter) -> None:
        raise NotImplementedError


# ==============================================================================
# 折线图
# ==============================================================================

class LineChart(_ChartBase):
    """多序列折线图（数值 X 轴）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._series_list: List[Series] = []
        self.point_markers = False

    def set_series(self, series: Sequence[Series], title: str = "",
                   y_suffix: str = "", y_range: Optional[Tuple[float, float]] = None) -> None:
        self._series_list = list(series)
        self.title = title
        self.y_suffix = y_suffix
        if y_range:
            self.y_min, self.y_max = y_range
        else:
            self.y_min = self.y_max = None
        self.update()

    def _series(self) -> List[Series]:
        return self._series_list

    def _all_points(self) -> List[Tuple[float, float]]:
        return [pt for s in self._series_list for pt in s.points]

    def _x_range(self) -> Tuple[float, float]:
        xs = [x for s in self._series_list for x, _v in s.points]
        if not xs:
            return (0.0, 1.0)
        lo, hi = min(xs), max(xs)
        if abs(hi - lo) < 1e-9:
            hi = lo + 1.0
        return (lo, hi)

    def _paint_content(self, p: QPainter) -> None:
        plot = self._setup_plot()
        if not self._series_list:
            p.setPen(QColor(150, 200, 225))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            return
        self._draw_frame(p)
        x_lo, x_hi = self._x_range()
        y_lo, y_hi = self._y_range()
        x_span = max(1e-9, x_hi - x_lo)
        y_span = max(1e-9, y_hi - y_lo)

        def to_screen(x: float, y: float) -> QPointF:
            sx = plot.left() + plot.width() * (x - x_lo) / x_span
            sy = plot.bottom() - plot.height() * (y - y_lo) / y_span
            return QPointF(sx, sy)

        # X 轴刻度
        p.setFont(QFont("Consolas", 8))
        p.setPen(QColor(150, 200, 225, 210))
        for i in range(6):
            k = i / 5.0
            x = x_lo + x_span * k
            sx = plot.left() + plot.width() * k
            p.drawLine(QPointF(sx, plot.bottom()), QPointF(sx, plot.bottom() + 4))
            label = f"{x:,.0f}" if x_span > 3 else f"{x:.1f}"
            fm = QFontMetrics(p.font())
            p.drawText(QPointF(sx - fm.horizontalAdvance(label) / 2,
                               plot.bottom() + 16), label)
        if self.x_label:
            p.setPen(QColor(170, 215, 235, 220))
            p.setFont(QFont("Microsoft YaHei UI", 8))
            p.drawText(QRectF(plot.left(), plot.bottom() + 16, plot.width(), 14),
                       Qt.AlignRight, self.x_label)

        # 曲线
        for idx, s in enumerate(self._series_list):
            if len(s.points) < 2:
                continue
            color = series_color(idx, s.color)
            path = QPainterPath()
            first = True
            pts: List[QPointF] = []
            for x, y in s.points:
                pt = to_screen(x, y)
                pts.append(pt)
                if first:
                    path.moveTo(pt)
                    first = False
                else:
                    path.lineTo(pt)
            if s.fill:
                area = QPainterPath(path)
                area.lineTo(pts[-1].x(), plot.bottom())
                area.lineTo(pts[0].x(), plot.bottom())
                area.closeSubpath()
                g = QLinearGradient(pts[0].x(), plot.top(), pts[0].x(), plot.bottom())
                g.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 90))
                g.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 8))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(g))
                p.drawPath(area)
            pen = QPen(color, s.width)
            if s.dashed:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            if self.point_markers and len(pts) <= 60:
                p.setBrush(color)
                p.setPen(Qt.NoPen)
                for pt in pts:
                    p.drawEllipse(pt, 1.8, 1.8)

        # 悬停竖线与数值
        if self._hover_x is not None:
            k = clamp((self._hover_x - plot.left()) / max(1.0, plot.width()), 0.0, 1.0)
            xv = x_lo + x_span * k
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.0, Qt.DashLine))
            p.drawLine(QPointF(self._hover_x, plot.top()),
                       QPointF(self._hover_x, plot.bottom()))
            lines = [f"{self.x_label or 'X'} = {xv:,.1f}"]
            for idx, s in enumerate(self._series_list):
                if not s.points:
                    continue
                nearest = min(s.points, key=lambda pt: abs(pt[0] - xv))
                if abs(nearest[0] - xv) > x_span * 0.06:
                    continue
                color = series_color(idx, s.color)
                lines.append(f"{s.name}：{self._fmt_y(nearest[1])}{self.y_suffix}")
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(to_screen(*nearest), 3.2, 3.2)
            # 数值框
            p.setFont(QFont("Microsoft YaHei UI", 8))
            fm = QFontMetrics(p.font())
            w = max(fm.horizontalAdvance(t) for t in lines) + 14
            h = 14 * len(lines) + 6
            bx = min(self._hover_x + 10, self.width() - w - 4)
            rect = QRectF(bx, plot.top() + 4, w, h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(6, 18, 30, 236))
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(QPen(QColor(0, 190, 230, 150), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 3, 3)
            for i, text in enumerate(lines):
                p.setPen(QColor(230, 250, 255) if i == 0 else QColor(190, 230, 245))
                p.drawText(QPointF(rect.left() + 7, rect.top() + 12 + i * 14), text)

        # 图例
        if self.legend and len(self._series_list) > 1:
            self._draw_legend(p, plot)

    def _draw_legend(self, p: QPainter, plot: QRectF) -> None:
        p.setFont(QFont("Microsoft YaHei UI", 8))
        fm = QFontMetrics(p.font())
        x = plot.left() + 8
        y = plot.top() + 12
        for idx, s in enumerate(self._series_list):
            if len(s.points) < 2:
                continue
            color = series_color(idx, s.color)
            text = s.name
            w = fm.horizontalAdvance(text)
            if x + w + 22 > plot.right():
                break
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, y - 5, 10, 3), 1.5, 1.5)
            p.setPen(QColor(215, 240, 250))
            p.drawText(QPointF(x + 14, y), text)
            x += w + 24


# ==============================================================================
# 堆叠面积图
# ==============================================================================

class AreaChart(_ChartBase):
    """堆叠面积图：显示构成比例随时间的演变（如各势力版图占比）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._series_list: List[Series] = []
        self.normalize = True          # True = 显示占比，False = 显示绝对量

    def set_series(self, series: Sequence[Series], title: str = "",
                   normalize: bool = True, y_suffix: str = "") -> None:
        self._series_list = list(series)
        self.title = title
        self.normalize = normalize
        self.y_suffix = y_suffix
        self.y_min = 0.0
        self.y_max = 100.0 if normalize else None
        self.update()

    def _series(self) -> List[Series]:
        return self._series_list

    def _all_points(self) -> List[Tuple[float, float]]:
        return [pt for s in self._series_list for pt in s.points]

    def _paint_content(self, p: QPainter) -> None:
        plot = self._setup_plot()
        if not self._series_list:
            p.setPen(QColor(150, 200, 225))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            return
        self._draw_frame(p)
        n = min(len(s.points) for s in self._series_list if s.points) if self._series_list else 0
        if n < 2:
            return
        xs = [s.points[i][0] for s in self._series_list for i in range(min(n, len(s.points)))]
        x_lo, x_hi = min(xs), max(xs)
        x_span = max(1e-9, x_hi - x_lo)
        # 逐点累加
        totals: List[float] = []
        for i in range(n):
            totals.append(sum(s.points[i][1] for s in self._series_list) or 1.0)
        y_hi = 100.0 if self.normalize else max(totals)
        y_hi = max(1e-9, y_hi)

        def to_screen(i: int, value: float) -> QPointF:
            x = self._series_list[0].points[i][0]
            sx = plot.left() + plot.width() * (x - x_lo) / x_span
            sy = plot.bottom() - plot.height() * clamp(value / y_hi, 0, 1)
            return QPointF(sx, sy)

        base = [0.0] * n
        for idx, s in enumerate(self._series_list):
            if len(s.points) < n:
                continue
            color = series_color(idx, s.color)
            top = [base[i] + (s.points[i][1] / (totals[i] if self.normalize else 1.0)) * (100.0 if self.normalize else 1.0) for i in range(n)]
            up = QPainterPath()
            down = QPainterPath()
            for i in range(n):
                pt = to_screen(i, top[i])
                if i == 0:
                    up.moveTo(pt)
                else:
                    up.lineTo(pt)
            for i in range(n - 1, -1, -1):
                pt = to_screen(i, base[i])
                if i == n - 1:
                    down.moveTo(pt)
                else:
                    down.lineTo(pt)
            band = QPainterPath(up)
            band.connectPath(down)
            band.closeSubpath()
            g = QLinearGradient(plot.topLeft(), plot.bottomRight())
            g.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 170))
            g.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 90))
            p.setPen(QPen(color.lighter(130), 1.0))
            p.setBrush(QBrush(g))
            p.drawPath(band)
            base = top
        # 图例
        p.setFont(QFont("Microsoft YaHei UI", 8))
        fm = QFontMetrics(p.font())
        x = plot.left() + 8
        y = plot.top() + 12
        for idx, s in enumerate(self._series_list):
            color = series_color(idx, s.color)
            if x + fm.horizontalAdvance(s.name) + 22 > plot.right():
                break
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, y - 5, 10, 3), 1.5, 1.5)
            p.setPen(QColor(215, 240, 250))
            p.drawText(QPointF(x + 14, y), s.name)
            x += fm.horizontalAdvance(s.name) + 24
        if self.normalize:
            p.setPen(QColor(170, 215, 235, 210))
            p.drawText(QRectF(plot.right() - 60, plot.top() + 4, 56, 12),
                       Qt.AlignRight, "占比 %")
        # X 轴
        p.setFont(QFont("Consolas", 8))
        p.setPen(QColor(150, 200, 225, 210))
        for i in range(6):
            k = i / 5.0
            sx = plot.left() + plot.width() * k
            xv = x_lo + x_span * k
            p.drawLine(QPointF(sx, plot.bottom()), QPointF(sx, plot.bottom() + 4))
            label = f"{xv:,.0f}"
            fm2 = QFontMetrics(p.font())
            p.drawText(QPointF(sx - fm2.horizontalAdvance(label) / 2,
                               plot.bottom() + 16), label)


# ==============================================================================
# 柱状图
# ==============================================================================

class BarChart(_ChartBase):
    """柱状图：支持单序列与分组多序列。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._categories: List[str] = []
        self._series_list: List[Series] = []
        self.value_labels = True
        self.x_is_numeric = False

    def set_data(self, categories: Sequence[str], series: Sequence[Series],
                 title: str = "", y_suffix: str = "") -> None:
        self._categories = list(categories)
        self._series_list = list(series)
        self.title = title
        self.y_suffix = y_suffix
        self.y_min = 0.0
        self.y_max = None
        self.update()

    def _series(self) -> List[Series]:
        return self._series_list

    def _all_points(self) -> List[Tuple[float, float]]:
        return [pt for s in self._series_list for pt in s.points]

    def _paint_content(self, p: QPainter) -> None:
        plot = self._setup_plot()
        if not self._categories or not self._series_list:
            p.setPen(QColor(150, 200, 225))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            return
        self._draw_frame(p)
        y_lo, y_hi = self._y_range()
        y_span = max(1e-9, y_hi - y_lo)
        n = len(self._categories)
        m = max(1, len(self._series_list))
        group_w = plot.width() / n
        bar_w = max(2.0, group_w * 0.72 / m)
        p.setFont(QFont("Microsoft YaHei UI", 7))
        fm = QFontMetrics(p.font())
        for i, cat in enumerate(self._categories):
            gx = plot.left() + group_w * i
            for j, s in enumerate(self._series_list):
                value = s.points[i][1] if i < len(s.points) else 0.0
                h = plot.height() * clamp((value - y_lo) / y_span, 0, 1)
                color = series_color(j, s.color)
                rect = QRectF(gx + group_w * 0.14 + j * bar_w,
                              plot.bottom() - h, max(1.0, bar_w - 1.2), h)
                g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                g.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 235))
                g.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 110))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(g))
                p.drawRoundedRect(rect, 2, 2)
                if self.value_labels and n * m <= 16 and value > 0:
                    p.setPen(QColor(220, 245, 255))
                    text = self._fmt_y(value)
                    p.drawText(QPointF(rect.center().x() - fm.horizontalAdvance(text) / 2,
                                       rect.top() - 2), text)
            # 分类标签
            p.setPen(QColor(170, 215, 235, 220))
            p.setFont(QFont("Microsoft YaHei UI", 7))
            label = cat
            if fm.horizontalAdvance(label) > group_w:
                label = label[: max(1, int(len(label) * group_w / max(1.0, fm.horizontalAdvance(cat))))]
            p.drawText(QRectF(gx, plot.bottom() + 3, group_w, 14), Qt.AlignCenter, label)


# ==============================================================================
# 时间轴
# ==============================================================================

class TimelineWidget(_ChartBase):
    """横向时间轴：把历史事件按年份摆放，可点击定位。"""

    event_clicked = Signal(object)          # 传出事件对象

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(210)
        self._events: List[object] = []
        self._year_range: Tuple[float, float] = (0.0, 1.0)
        self._hit: List[Tuple[QRectF, object]] = []
        self.legend = False

    def set_events(self, events: Sequence[object],
                   year_range: Optional[Tuple[float, float]] = None,
                   title: str = "历史时间轴") -> None:
        self._events = list(events)
        self.title = title
        if year_range:
            self._year_range = year_range
        elif events:
            years = [float(getattr(e, "year", 0.0)) for e in events]
            self._year_range = (min(years), max(years) if max(years) > min(years) else min(years) + 1)
        self.update()

    def _paint_content(self, p: QPainter) -> None:
        left, top = 46.0, 22.0
        right, bottom = 14.0, 18.0
        plot = QRectF(left, top, max(20.0, self.width() - left - right),
                      max(20.0, self.height() - top - bottom))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 18, 30, 210))
        p.drawRoundedRect(plot.adjusted(-4, -4, 4, 4), 4, 4)
        if not self._events:
            p.setPen(QColor(150, 200, 225))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无事件（点击「开始推演」生成）")
            return
        y0, y1 = self._year_range
        span = max(1e-6, y1 - y0)
        # 中轴
        axis_y = plot.center().y()
        p.setPen(QPen(QColor(0, 170, 210, 130), 1.2))
        p.drawLine(QPointF(plot.left(), axis_y), QPointF(plot.right(), axis_y))
        # 年份刻度
        p.setFont(QFont("Consolas", 8))
        p.setPen(QColor(150, 200, 225, 210))
        for i in range(7):
            k = i / 6.0
            x = plot.left() + plot.width() * k
            p.drawLine(QPointF(x, axis_y - 4), QPointF(x, axis_y + 4))
            label = f"{y0 + span * k:,.0f}"
            fm = QFontMetrics(p.font())
            p.drawText(QPointF(x - fm.horizontalAdvance(label) / 2, plot.bottom() + 14),
                       label)
        p.setPen(QColor(170, 215, 235, 200))
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.drawText(QPointF(plot.left(), plot.top() - 6), "年份 →")

        # 事件节点：上下交替摆放，避免重叠
        self._hit = []
        lanes_up = [0.0] * 4
        lanes_down = [0.0] * 4
        p.setFont(QFont("Microsoft YaHei UI", 8))
        fm = QFontMetrics(p.font())
        for ev in self._events:
            year = float(getattr(ev, "year", 0.0))
            importance = int(getattr(ev, "importance", 1))
            x = plot.left() + plot.width() * clamp((year - y0) / span, 0, 1)
            color_map = {"殖民": (140, 255, 190), "科技": (120, 190, 255),
                         "战争": (255, 120, 110), "经济": (255, 214, 120),
                         "灾变": (255, 150, 90), "外交": (200, 160, 255),
                         "文明": (255, 180, 220)}
            rgb = color_map.get(str(getattr(ev, "category", "")), (200, 220, 240))
            color = QColor(*rgb)
            # 选择通道
            lanes = lanes_up if (len(self._hit) % 2 == 0) else lanes_down
            sign = -1 if lanes is lanes_up else 1
            lane = 0
            for i, used in enumerate(lanes):
                if x - used > 92:
                    lane = i
                    break
            else:
                lane = 3
            lanes[lane] = x
            ty = axis_y + sign * (16 + lane * 20)
            p.setPen(QPen(color, 1.0))
            p.drawLine(QPointF(x, axis_y), QPointF(x, ty))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            r = 3.6 if importance >= 3 else (2.8 if importance == 2 else 2.0)
            p.drawEllipse(QPointF(x, axis_y), r, r)
            # 文本
            text = f"{getattr(ev, 'icon', '·')} {getattr(ev, 'title', '')}"
            max_w = plot.width() * 0.32
            while fm.horizontalAdvance(text) > max_w and len(text) > 6:
                text = text[:-2]
            tw = fm.horizontalAdvance(text)
            tx = clamp(x - tw / 2, plot.left(), plot.right() - tw)
            rect = QRectF(tx, ty - (12 if sign < 0 else -2), tw + 4, 13)
            p.setPen(color.lighter(120) if importance >= 3 else QColor(215, 240, 250))
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            hit = QRectF(min(x, tx) - 4, min(axis_y, ty) - 4,
                         max(12.0, tw + 8), abs(ty - axis_y) + 18)
            self._hit.append((hit, ev))
            # 年份小字
            if importance >= 3:
                p.setPen(QColor(170, 215, 235, 200))
                p.setFont(QFont("Consolas", 7))
                p.drawText(QPointF(x + 3, axis_y + 10), f"{year:.0f}")
                p.setFont(QFont("Microsoft YaHei UI", 8))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        for rect, ev in self._hit:
            if rect.contains(event.position()):
                self.event_clicked.emit(ev)
                return
        super().mouseReleaseEvent(event)


__all__ = ["Series", "LineChart", "AreaChart", "BarChart", "TimelineWidget",
           "series_color", "PALETTE"]
