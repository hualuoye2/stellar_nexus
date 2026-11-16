# -*- coding: utf-8 -*-
"""
widgets.py —— 自定义科幻风格控件
================================================================================
本模块提供的控件全部为零依赖自绘控件，配合 assets/styles.qss 使用：

    GlowButton          霓虹发光按钮（可指定强调色）
    IconToggleButton    带状态指示的切换按钮
    NeonSlider          霓虹滑杆（可显示数值、可吸附刻度）
    CollapsiblePanel    可折叠分组面板（左侧控制面板的基本单元）
    SectionTitle        分组标题（带扫描线装饰）
    FpsLabel            帧率/性能指示器
    SciFiFrame          全息边框容器（四角括号 + 扫描线动画）
    HologramLoader      全息加载指示器（世界生成时显示）
    InfoRow             信息面板的「标签 : 值」行
    Badge               小徽章（用于势力、类型等彩色标签）
    SeparatorLine       渐变分隔线
    SearchBox           带图标的搜索输入框
    ScanlineOverlay     可叠加在任意控件上的扫描线（透明、不拦截鼠标）
"""

from __future__ import annotations

import math
import time
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import (QEasingCurve, QPoint, Property, QPropertyAnimation, QRect, QRectF,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontMetrics, QLinearGradient,
                           QPainter, QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QToolButton, QVBoxLayout, QWidget)

from 核心.utils import clamp, rgb_str

# ==============================================================================
# 全局配色（与 styles.qss 保持一致）
# ==============================================================================

CYAN = QColor(0, 229, 255)
CYAN_DIM = QColor(0, 140, 170)
VIOLET = QColor(160, 110, 255)
ORANGE = QColor(255, 150, 60)
GREEN = QColor(80, 255, 180)
RED = QColor(255, 90, 110)
TEXT = QColor(210, 235, 245)
TEXT_DIM = QColor(130, 165, 180)
BG_DEEP = QColor(3, 8, 16)

ACCENTS = {
    "cyan": CYAN,
    "violet": VIOLET,
    "orange": ORANGE,
    "green": GREEN,
    "red": RED,
}


# ==============================================================================
# 一、按钮
# ==============================================================================

class GlowButton(QPushButton):
    """霓虹发光按钮：悬停时发光增强，按下时内缩高亮。"""

    def __init__(self, text: str = "", accent: str = "cyan", parent: Optional[QWidget] = None,
                 compact: bool = False) -> None:
        super().__init__(text, parent)
        self._accent = ACCENTS.get(accent, CYAN)
        self._hover = False
        self._pressed = False
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(26 if compact else 32)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setAttribute(Qt.WA_Hover, True)

    def set_accent(self, accent: str) -> None:
        """切换强调色。"""
        self._accent = ACCENTS.get(accent, CYAN)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        accent = self._accent
        # 背景
        grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        base_alpha = 0.30 if self._hover else 0.18
        if self._pressed:
            base_alpha = 0.45
        grad.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), int(255 * base_alpha)))
        grad.setColorAt(1.0, QColor(6, 16, 26, 220))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(accent if (self._hover or self._pressed) else CYAN_DIM, 1.0))
        p.drawRoundedRect(r, 3.0, 3.0)
        # 发光描边
        if self._hover or self._pressed:
            glow = QColor(accent)
            glow.setAlpha(70)
            p.setPen(QPen(glow, 2.4))
            p.drawRoundedRect(r.adjusted(1.2, 1.2, -1.2, -1.2), 3.0, 3.0)
        # 文本
        p.setPen(QColor(235, 250, 255) if self.isEnabled() else QColor(110, 130, 140))
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignCenter, self.text())
        p.end()


class IconToggleButton(QToolButton):
    """可切换状态的工具按钮，选中时显示发光底。"""

    def __init__(self, text: str = "", accent: str = "cyan", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self._accent = ACCENTS.get(accent, CYAN)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(26)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setFont(QFont("Microsoft YaHei UI", 9))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        checked = self.isChecked()
        accent = self._accent
        if checked:
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 110))
            grad.setColorAt(1.0, QColor(6, 16, 26, 230))
            p.setBrush(QBrush(grad))
            p.setPen(QPen(accent, 1.2))
        else:
            p.setBrush(QBrush(QColor(10, 22, 34, 170)))
            p.setPen(QPen(QColor(40, 90, 110, 200), 1.0))
        p.drawRoundedRect(r, 3.0, 3.0)
        p.setPen(QColor(240, 252, 255) if checked else QColor(170, 200, 215))
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignCenter, self.text())
        p.end()


# ==============================================================================
# 二、滑杆与输入
# ==============================================================================

class NeonSlider(QSlider):
    """霓虹滑杆：用于时间轴、缩放、时间倍率等。"""

    def __init__(self, orientation=Qt.Horizontal, accent: str = "cyan",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(orientation, parent)
        self._accent = ACCENTS.get(accent, CYAN)
        self.setMinimumHeight(18)
        self.setCursor(Qt.PointingHandCursor)

    def _ratio(self) -> float:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return 0.0
        return clamp((self.value() - self.minimum()) / span, 0.0, 1.0)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        cy = h / 2.0
        # 轨道
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 45, 60, 220))
        p.drawRoundedRect(QRectF(2, cy - 2.0, w - 4, 4.0), 2.0, 2.0)
        # 已填充部分
        ratio = self._ratio()
        fill_w = (w - 4) * ratio
        grad = QLinearGradient(2, 0, w - 2, 0)
        grad.setColorAt(0.0, QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 150))
        grad.setColorAt(1.0, self._accent)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(2, cy - 2.0, max(0.0, fill_w), 4.0), 2.0, 2.0)
        # 手柄
        cx = 2 + fill_w
        glow = QRadialGradient(cx, cy, 9.0)
        glow.setColorAt(0.0, QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 160))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - 9, cy - 9, 18, 18))
        p.setBrush(QColor(240, 255, 255))
        p.setPen(QPen(self._accent, 1.4))
        p.drawEllipse(QRectF(cx - 4.0, cy - 4.0, 8.0, 8.0))
        p.end()


class SearchBox(QLineEdit):
    """带霓虹边框与提示文字的搜索框。"""

    def __init__(self, placeholder: str = "搜索天体…", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setMinimumHeight(30)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setStyleSheet(
            "QLineEdit{background:rgba(6,18,28,200);border:1px solid rgba(0,180,220,140);"
            "border-radius:3px;padding:4px 8px;color:#d8f4ff;selection-background-color:#0a5f78;}"
            "QLineEdit:focus{border:1px solid rgba(0,229,255,220);background:rgba(8,24,36,230);}")


# ==============================================================================
# 三、面板与装饰
# ==============================================================================

class SectionTitle(QWidget):
    """分组标题：左侧竖条 + 文字 + 右侧渐隐线。"""

    def __init__(self, text: str, accent: str = "cyan", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text = text
        self._accent = ACCENTS.get(accent, CYAN)
        self.setFixedHeight(22)

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(self._accent)
        p.drawRect(QRectF(0, h * 0.22, 3.0, h * 0.56))
        p.setPen(QColor(232, 250, 255))
        f = QFont("Microsoft YaHei UI", 10, QFont.DemiBold)
        p.setFont(f)
        p.drawText(QRectF(9, 0, w - 9, h), Qt.AlignVCenter | Qt.AlignLeft, self._text)
        # 右侧渐隐线
        fm = QFontMetrics(f)
        x0 = 14 + fm.horizontalAdvance(self._text)
        if x0 < w - 12:
            grad = QLinearGradient(x0, 0, w, 0)
            grad.setColorAt(0.0, QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 130))
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(QPen(QBrush(grad), 1.0))
            p.drawLine(int(x0), int(h / 2), w, int(h / 2))
        p.end()


class SeparatorLine(QWidget):
    """渐变分隔线。"""

    def __init__(self, accent: str = "cyan", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._accent = ACCENTS.get(accent, CYAN)
        self.setFixedHeight(6)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(0.5, QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 120))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(QPen(QBrush(grad), 1.0))
        p.drawLine(0, self.height() // 2, self.width(), self.height() // 2)
        p.end()


class CollapsiblePanel(QWidget):
    """可折叠分组面板。

    结构：[标题栏（点击折叠/展开）] + [内容容器]
    左侧控制面板大量使用本控件组织参数。
    """

    toggled = Signal(bool)

    def __init__(self, title: str, accent: str = "cyan", expanded: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._accent = ACCENTS.get(accent, CYAN)
        self._expanded = expanded
        self._hover = False

        self._header = QWidget(self)
        self._header.setFixedHeight(26)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setAttribute(Qt.WA_Hover, True)
        self._header.installEventFilter(self)

        self._title = QLabel(title, self._header)
        self._title.setFont(QFont("Microsoft YaHei UI", 10, QFont.DemiBold))
        self._title.setStyleSheet("color:#e8faff;background:transparent;")
        self._title.setGeometry(22, 0, 200, 26)

        self._arrow = QLabel("▾", self._header)
        self._arrow.setFont(QFont("Microsoft YaHei UI", 9))
        self._arrow.setStyleSheet("color:#a8ecff;background:transparent;")
        self._arrow.setGeometry(6, 0, 16, 26)
        self._arrow.setAlignment(Qt.AlignCenter)

        self.content = QWidget(self)
        self.content.setVisible(expanded)
        # 内容容器显式使用透明背景（父面板已有深色全息背景），
        # 同时开启 WA_StyledBackground 让 QSS 的 background 生效，
        # 避免落到系统浅色调色板上造成"浅底浅字"。
        self.content.setAttribute(Qt.WA_StyledBackground, True)
        self.content.setStyleSheet("background:transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(4)
        layout.addWidget(self._header)
        layout.addWidget(self.content)

    # ---- 折叠控制 ----
    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.content.setVisible(self._expanded)
        self._arrow.setText("▾" if self._expanded else "▸")
        self.toggled.emit(self._expanded)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._header:
            et = event.type()
            if et == event.Type.Enter:
                self._hover = True
                self._header.update()
            elif et == event.Type.Leave:
                self._hover = False
                self._header.update()
            elif et == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.toggle()
                return True
            elif et == event.Type.Paint:
                self._paint_header()
                return True
        return super().eventFilter(obj, event)

    def _paint_header(self) -> None:
        """自绘标题栏（因为 header 是普通 QWidget，通过事件过滤器接管绘制）。"""
        p = QPainter(self._header)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self._header.rect())
        alpha = 90 if self._hover else 55
        grad = QLinearGradient(r.topLeft(), r.topRight())
        grad.setColorAt(0.0, QColor(self._accent.red(), self._accent.green(), self._accent.blue(), alpha))
        grad.setColorAt(1.0, QColor(8, 20, 30, 40))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 110), 1.0))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3.0, 3.0)
        # 左侧强调条
        p.setPen(Qt.NoPen)
        p.setBrush(self._accent)
        p.drawRect(QRectF(0, 5, 3.0, self._header.height() - 10))
        p.end()


class SciFiFrame(QWidget):
    """全息边框容器：四角括号 + 可选扫描线动画。

    把需要「科幻感」的控件放进本容器即可获得统一的视觉语言。
    """

    def __init__(self, parent: Optional[QWidget] = None, accent: str = "cyan",
                 scanlines: bool = True, corner: int = 12) -> None:
        super().__init__(parent)
        self._accent = ACCENTS.get(accent, CYAN)
        self._scan = scanlines
        self._corner = corner
        self._offset = 0.0
        self._timer: Optional[QTimer] = None
        if scanlines:
            self._timer = QTimer(self)
            self._timer.setInterval(40)
            self._timer.timeout.connect(self._advance)
            self._timer.start()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    def _advance(self) -> None:
        self._offset = (self._offset + 1.2) % max(1.0, float(self.height()))
        self.update()

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        r = QRectF(0.5, 0.5, w - 1, h - 1)
        # 背景
        p.setBrush(QColor(4, 12, 20, 190))
        p.setPen(QPen(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 70), 1.0))
        p.drawRoundedRect(r, 4.0, 4.0)
        # 扫描线
        if self._scan:
            p.setPen(QPen(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 26), 1.0))
            y = 0
            while y < h:
                p.drawLine(0, int(y), w, int(y))
                y += 4
            # 移动光带
            band = QLinearGradient(0, self._offset - 26, 0, self._offset + 26)
            band.setColorAt(0.0, QColor(0, 0, 0, 0))
            band.setColorAt(0.5, QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 26))
            band.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(band))
            p.drawRect(QRectF(0, self._offset - 26, w, 52))
        # 四角
        pen = QPen(self._accent, 1.6)
        p.setPen(pen)
        c = self._corner
        p.drawLine(0, 0, c, 0)
        p.drawLine(0, 0, 0, c)
        p.drawLine(w, 0, w - c, 0)
        p.drawLine(w, 0, w, c)
        p.drawLine(0, h, c, h)
        p.drawLine(0, h, 0, h - c)
        p.drawLine(w, h, w - c, h)
        p.drawLine(w, h, w, h - c)
        p.end()


class ScanlineOverlay(QWidget):
    """扫描线覆盖层：透明、不接收鼠标事件，可叠加在视图之上。

    性能注意（重要）：
        本控件是「透明 + WA_NoSystemBackground」的覆盖层，一旦它重绘，Qt 必须
        把它下面的整个视图一起重绘。因此**绝不能无条件地持续动画**：
        原实现用 50ms 定时器不停 update()，而窗口里同时存在 4 个覆盖层
        （现实星图 / 太阳系仪 / 太阳邻域 / 架空星图），即使视图被 StackedWidget
        隐藏、覆盖层仍在动画，导致空闲时 CPU 长期 130% 以上、界面严重卡顿。
        现在改为：只在父视图可见时动画（showEvent 启动 / hideEvent 停止），
        间隔放宽到 0.2 秒，线距 6px。
    """

    def __init__(self, parent: Optional[QWidget] = None, alpha: int = 12,
                 animate: bool = False) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._alpha = alpha
        self._offset = 0.0
        # 默认**不**动画：静态扫描线的观感几乎一样，但完全不需要周期性重绘。
        # 需要动效时可显式传 animate=True（代价是持续重绘整个视口）。
        self._animate = bool(animate)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        # 双保险：不可见时立刻停表，隐藏页面绝不消耗 CPU
        if not self.isVisible():
            self._timer.stop()
            return
        self._offset = (self._offset + 0.8) % 6.0
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._animate and not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        if self._timer.isActive():
            self._timer.stop()

    def set_animated(self, enabled: bool) -> None:
        """开关动画。

        主窗口只在**当前显示的视图**上开启动画：被 StackedWidget 藏起来的页面
        即使可见性上报为 True，也不该继续驱动全视口重绘（那是纯粹的浪费）。
        """
        self._animate = bool(enabled)
        if not self._animate:
            if self._timer.isActive():
                self._timer.stop()
        elif self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def is_animated(self) -> bool:
        return self._animate

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setPen(QPen(QColor(140, 220, 255, self._alpha), 1.0))
        y = self._offset
        h = self.height()
        w = self.width()
        while y < h:
            p.drawLine(0, int(y), w, int(y))
            y += 6.0
        p.end()


class FpsLabel(QLabel):
    """帧率与性能指示器：显示 FPS、渲染天体数量与时间推进倍率。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("FPS --", parent)
        self.setMinimumWidth(190)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(QFont("Consolas", 9))
        self.setStyleSheet("color:#9ff6ff;background:rgba(8,22,34,228);"
                           "border:1px solid rgba(0,180,220,110);border-radius:3px;padding:2px 6px;")
        self._frames = 0
        self._last = time.perf_counter()
        self._fps = 0.0

    def tick(self, extra: str = "") -> None:
        """每帧调用一次；内部按 0.5 秒窗口统计 FPS。"""
        self._frames += 1
        now = time.perf_counter()
        dt = now - self._last
        if dt >= 0.5:
            self._fps = self._frames / dt
            self._frames = 0
            self._last = now
            text = f"FPS {self._fps:5.1f}"
            if extra:
                text += f"  |  {extra}"
            self.setText(text)

    @property
    def fps(self) -> float:
        return self._fps


class HologramLoader(QWidget):
    """全息加载指示器：世界生成等耗时操作时显示旋转环与文字。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._angle = 0.0
        self._text = "处理中"
        self._progress = 0
        self.setMinimumHeight(72)
        timer = QTimer(self)
        timer.setInterval(33)
        timer.timeout.connect(self._tick)
        timer.start()

    def _tick(self) -> None:
        self._angle = (self._angle + 4.0) % 360.0
        self.update()

    def set_progress(self, percent: int, text: str = "") -> None:
        self._progress = int(clamp(percent, 0, 100))
        if text:
            self._text = text
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy, r = w / 2.0, h / 2.0 - 8, min(w, h) / 2.6
        # 旋转弧
        for i, (color, span, radius, speed) in enumerate((
                (CYAN, 120.0, r, 1.0), (VIOLET, 90.0, r * 0.74, -1.4), (ORANGE, 60.0, r * 0.5, 2.0))):
            pen = QPen(color, 2.0)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            start = int((self._angle * speed) * 16)
            p.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2), start, int(span * 16))
        # 中心文字
        p.setPen(QColor(210, 240, 255))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QRectF(0, cy + r + 4, w, 20), Qt.AlignCenter, f"{self._text}  {self._progress}%")
        # 进度条
        bar_w = w * 0.6
        x0 = cx - bar_w / 2
        p.setBrush(QColor(20, 45, 60, 200))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(x0, cy + r + 26, bar_w, 4), 2, 2)
        p.setBrush(CYAN)
        p.drawRoundedRect(QRectF(x0, cy + r + 26, bar_w * self._progress / 100.0, 4), 2, 2)
        p.end()


# ==============================================================================
# 四、信息展示小组件
# ==============================================================================

class InfoRow(QWidget):
    """一行「标签 : 值」，值可换行、可着色。"""

    def __init__(self, label: str, value: str = "--", value_color: str = "#eafaff",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(6)
        self._label = QLabel(label)
        self._label.setFixedWidth(76)
        self._label.setFont(QFont("Microsoft YaHei UI", 9))
        self._label.setStyleSheet("color:#8fd4ea;background:transparent;")
        self._value = QLabel(value)
        self._value.setFont(QFont("Microsoft YaHei UI", 9, QFont.DemiBold))
        self._value.setWordWrap(True)
        self._value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._value.setStyleSheet(f"color:{value_color};background:transparent;")
        self._value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self._label, 0)
        layout.addWidget(self._value, 1)

    def set_value(self, value: str, color: Optional[str] = None) -> None:
        self._value.setText(str(value))
        if color:
            self._value.setStyleSheet(f"color:{color};background:transparent;")


class Badge(QLabel):
    """彩色小徽章，用于类型 / 势力 / 危险度等标记。"""

    def __init__(self, text: str = "", color: Sequence[int] = (0, 229, 255),
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setFont(QFont("Microsoft YaHei UI", 8))
        self.setAlignment(Qt.AlignCenter)
        self.set_color(color)
        self.setFixedHeight(18)

    def set_color(self, color: Sequence[int]) -> None:
        self.setStyleSheet(
            f"color:rgb({color[0]},{color[1]},{color[2]});"
            f"background:{rgb_str(color, 0.14)};"
            f"border:1px solid {rgb_str(color, 0.55)};border-radius:2px;padding:0 4px;")


class StatBar(QWidget):
    """数值条：以条状显示 0~1 的比例（科技、经济、军事、危险度等）。"""

    def __init__(self, label: str = "", color: Sequence[int] = (0, 229, 255),
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._label = label
        self._value = 0.0
        self._color = tuple(color)
        self.setFixedHeight(16)

    def set_value(self, value: float, color: Optional[Sequence[int]] = None) -> None:
        self._value = clamp(float(value), 0.0, 1.0)
        if color:
            self._color = tuple(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.setPen(QColor(150, 190, 210))
        p.setFont(QFont("Microsoft YaHei UI", 8))
        label_w = 62
        p.drawText(QRectF(0, 0, label_w, h), Qt.AlignVCenter | Qt.AlignLeft, self._label)
        bar_x = label_w + 2
        bar_w = max(10.0, w - label_w - 42)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 40, 54, 210))
        p.drawRoundedRect(QRectF(bar_x, h / 2 - 3, bar_w, 6), 3, 3)
        grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
        c = self._color
        grad.setColorAt(0.0, QColor(c[0], c[1], c[2], 130))
        grad.setColorAt(1.0, QColor(c[0], c[1], c[2], 255))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(bar_x, h / 2 - 3, bar_w * self._value, 6), 3, 3)
        p.setPen(QColor(238, 252, 255))
        p.drawText(QRectF(w - 42, 0, 42, h), Qt.AlignVCenter | Qt.AlignRight, f"{self._value * 100:.0f}%")
        p.end()


class ModeSwitch(QWidget):
    """顶部模式切换开关（现实星图 / 太阳邻域 / 架空星图），带滑动指示块。"""

    mode_changed = Signal(str)

    #: 模式顺序（与指示块位置一一对应）
    MODES: Tuple[str, ...] = ("real", "orrery", "nearby", "fiction")
    LABELS: Tuple[str, ...] = ("现实星图", "太阳系仪", "太阳邻域", "架空星图")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(432, 32)
        self._mode = "real"
        self._pos = 0.0                 # 0.0 ~ 1.0，对应三个位置
        self._anim = QPropertyAnimation(self, b"anim_pos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setCursor(Qt.PointingHandCursor)

    # 供 QPropertyAnimation 使用的 Qt 属性（必须用 Property 注册，否则动画无效）
    def _get_anim_pos(self) -> float:
        return self._pos

    def _set_anim_pos(self, value: float) -> None:
        self._pos = float(value)
        self.update()

    anim_pos = Property(float, _get_anim_pos, _set_anim_pos)

    def set_mode(self, mode: str, animate: bool = True) -> None:
        self._mode = mode if mode in self.MODES else "real"
        try:
            index = self.MODES.index(self._mode)
        except ValueError:
            index = 0
        target = index / float(len(self.MODES) - 1)
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._pos = target
            self.update()

    def mode(self) -> str:
        return self._mode

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            seg = self.width() / float(len(self.MODES))
            index = int(clamp(event.position().x() / seg, 0, len(self.MODES) - 1))
            new_mode = self.MODES[index]
            if new_mode != self._mode:
                self.set_mode(new_mode)
                self.mode_changed.emit(new_mode)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(0, 170, 210, 150), 1.0))
        p.setBrush(QColor(6, 16, 26, 210))
        p.drawRoundedRect(r, 4.0, 4.0)
        segments = float(len(self.MODES))
        seg = self.width() / segments
        # 滑块
        x = self._pos * (self.width() - seg)
        grad = QLinearGradient(x, 0, x + seg, 0)
        grad.setColorAt(0.0, QColor(0, 229, 255, 90))
        grad.setColorAt(1.0, QColor(160, 110, 255, 110))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(0, 229, 255, 200), 1.0))
        p.drawRoundedRect(QRectF(x + 2, 2, seg - 4, self.height() - 4), 3.0, 3.0)
        # 文本（当前模式用深色字，其余用浅色）
        p.setFont(QFont("Microsoft YaHei UI", 9, QFont.DemiBold))
        for i, label in enumerate(self.LABELS):
            active = self.MODES[i] == self._mode
            p.setPen(QColor(20, 30, 40) if active else QColor(200, 225, 240))
            p.drawText(QRectF(i * seg, 0, seg, self.height()), Qt.AlignCenter, label)
        p.end()


class ElidedLabel(QLabel):
    """自动省略过长的文本标签。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full)
        self._update_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._update_elide()
        super().resizeEvent(event)

    def _update_elide(self) -> None:
        w = max(10, self.width())
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.ElideRight, w))


__all__ = [
    "CYAN", "CYAN_DIM", "VIOLET", "ORANGE", "GREEN", "RED", "TEXT", "TEXT_DIM", "BG_DEEP", "ACCENTS",
    "GlowButton", "IconToggleButton", "NeonSlider", "SearchBox", "SectionTitle", "SeparatorLine",
    "CollapsiblePanel", "SciFiFrame", "ScanlineOverlay", "FpsLabel", "HologramLoader",
    "InfoRow", "Badge", "StatBar", "ModeSwitch", "ElidedLabel",
]
