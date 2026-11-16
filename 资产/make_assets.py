# -*- coding: utf-8 -*-
"""
make_assets.py —— 程序化生成应用资产（图标与发光贴图）
================================================================================
运行方式（无需任何额外依赖，只用 PySide6 的离屏绘制）：

    python 资产/make_assets.py

生成内容：
    资产/icon.png            512×512 应用图标（PNG）
    资产/icon.ico            多尺寸 Windows 图标（16/24/32/48/64/128/256）
    资产/sprites/star_glow.png    恒星发光贴图（径向渐变）
    资产/sprites/nebula.png       星云贴图（多层噪声光斑）
    资产/sprites/ship.png         飞船标记

说明：程序运行时不依赖这些贴图（所有发光效果均由 QPainter 实时绘制），
     它们主要用于文档、预览图与后续扩展；icon.ico 会被 PyInstaller 使用
     （--icon 参数）并作为窗口图标。
"""

from __future__ import annotations

import math
import os
import random
import sys

# 允许以脚本方式直接运行（把项目根目录加入 sys.path）
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF, QRectF, Qt           # noqa: E402
from PySide6.QtGui import (QColor, QConicalGradient, QFont, QIcon, QImage, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QApplication                # noqa: E402

SPRITES_DIR = os.path.join(HERE, "sprites")


# ==============================================================================
# 图标绘制
# ==============================================================================

def draw_icon(size: int = 512) -> QImage:
    """绘制应用图标：深空圆盘 + 星图网格 + 发光星体 + 轨道环。

    视觉语言与主界面一致：青色主色、紫色点缀、橙色强调。
    """
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = size / 2.0
    r = size * 0.46

    # --- 背景圆盘 ---
    grad = QRadialGradient(QPointF(c, c * 0.86), r * 1.25)
    grad.setColorAt(0.0, QColor(10, 30, 48, 255))
    grad.setColorAt(0.55, QColor(5, 14, 26, 255))
    grad.setColorAt(1.0, QColor(2, 6, 14, 255))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawEllipse(QPointF(c, c), r, r)

    # --- 外圈霓虹描边 ---
    for width, alpha in ((size * 0.018, 210), (size * 0.045, 60)):
        pen = QPen(QColor(0, 229, 255, alpha), width)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(c, c), r, r)

    # --- 星图网格（经纬线） ---
    p.setPen(QPen(QColor(0, 190, 230, 90), max(1.0, size * 0.0025)))
    for i in range(1, 4):
        rr = r * i / 4.0
        p.drawEllipse(QPointF(c, c), rr, rr)
    for deg in range(0, 180, 30):
        a = math.radians(deg)
        p.drawLine(QPointF(c - r * math.cos(a), c - r * math.sin(a)),
                   QPointF(c + r * math.cos(a), c + r * math.sin(a)))

    # --- 星座连线（模拟一条折线） ---
    p.setPen(QPen(QColor(120, 220, 255, 170), max(1.2, size * 0.004)))
    pts = [(0.30, 0.36), (0.44, 0.30), (0.58, 0.42), (0.70, 0.34), (0.78, 0.50)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        p.drawLine(QPointF(x1 * size, y1 * size), QPointF(x2 * size, y2 * size))

    # --- 恒星点（带发光） ---
    stars = [(0.30, 0.36, 0.030, (255, 210, 160)), (0.44, 0.30, 0.022, (200, 220, 255)),
             (0.58, 0.42, 0.026, (255, 240, 220)), (0.70, 0.34, 0.018, (170, 200, 255)),
             (0.78, 0.50, 0.020, (255, 190, 150)), (0.26, 0.62, 0.016, (255, 255, 255)),
             (0.66, 0.70, 0.014, (255, 255, 255)), (0.42, 0.74, 0.012, (255, 255, 255))]
    for x, y, sr, rgb in stars:
        cx, cy = x * size, y * size
        rr = sr * size
        glow = QRadialGradient(QPointF(cx, cy), rr * 4.5)
        glow.setColorAt(0.0, QColor(rgb[0], rgb[1], rgb[2], 230))
        glow.setColorAt(0.35, QColor(rgb[0], rgb[1], rgb[2], 90))
        glow.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), rr * 4.5, rr * 4.5)
        p.setBrush(QColor(rgb[0], rgb[1], rgb[2], 255))
        p.drawEllipse(QPointF(cx, cy), rr, rr)

    # --- 中央星体 + 轨道环 ---
    core_r = size * 0.10
    core = QRadialGradient(QPointF(c, c), core_r * 2.6)
    core.setColorAt(0.0, QColor(255, 255, 255, 255))
    core.setColorAt(0.25, QColor(160, 240, 255, 220))
    core.setColorAt(0.6, QColor(0, 190, 240, 110))
    core.setColorAt(1.0, QColor(0, 120, 200, 0))
    p.setBrush(core)
    p.drawEllipse(QPointF(c, c), core_r * 2.6, core_r * 2.6)
    p.setBrush(QColor(240, 253, 255, 255))
    p.drawEllipse(QPointF(c, c), core_r * 0.62, core_r * 0.62)

    p.setPen(QPen(QColor(160, 110, 255, 200), max(1.4, size * 0.006)))
    p.setBrush(Qt.NoBrush)
    p.save()
    p.translate(c, c)
    p.rotate(-28)
    p.drawEllipse(QRectF(-r * 0.86, -r * 0.40, r * 1.72, r * 0.80))
    p.restore()
    p.setPen(QPen(QColor(255, 150, 60, 190), max(1.2, size * 0.004)))
    p.save()
    p.translate(c, c)
    p.rotate(38)
    p.drawEllipse(QRectF(-r * 0.72, -r * 0.30, r * 1.44, r * 0.60))
    p.restore()

    # --- 四角装饰 ---
    pen = QPen(QColor(0, 229, 255, 230), max(2.0, size * 0.010))
    p.setPen(pen)
    m = size * 0.06
    L = size * 0.10
    for (sx, sy) in ((m, m), (size - m, m), (m, size - m), (size - m, size - m)):
        dx = L if sx < size / 2 else -L
        dy = L if sy < size / 2 else -L
        p.drawLine(QPointF(sx, sy), QPointF(sx + dx, sy))
        p.drawLine(QPointF(sx, sy), QPointF(sx, sy + dy))
    p.end()
    return img


# ==============================================================================
# 贴图绘制
# ==============================================================================

def draw_star_glow(size: int = 128, rgb=(255, 240, 210)) -> QImage:
    """恒星发光贴图：中心亮核 + 柔和光晕 + 十字光刺。"""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = size / 2.0
    grad = QRadialGradient(QPointF(c, c), c)
    grad.setColorAt(0.0, QColor(255, 255, 255, 255))
    grad.setColorAt(0.16, QColor(rgb[0], rgb[1], rgb[2], 235))
    grad.setColorAt(0.38, QColor(rgb[0], rgb[1], rgb[2], 110))
    grad.setColorAt(0.70, QColor(rgb[0], rgb[1], rgb[2], 30))
    grad.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawEllipse(QPointF(c, c), c, c)
    pen = QPen(QColor(rgb[0], rgb[1], rgb[2], 120), max(1.0, size * 0.012))
    p.setPen(pen)
    p.drawLine(QPointF(0, c), QPointF(size, c))
    p.drawLine(QPointF(c, 0), QPointF(c, size))
    p.end()
    return img


def draw_nebula(size: int = 256, seed: int = 7) -> QImage:
    """星云贴图：多层随机光斑叠加。"""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    rng = random.Random(seed)
    palette = ((120, 80, 255), (0, 190, 255), (255, 90, 180), (90, 255, 200))
    p.setPen(Qt.NoPen)
    for _ in range(26):
        x, y = rng.uniform(0.15, 0.85) * size, rng.uniform(0.15, 0.85) * size
        r = rng.uniform(0.12, 0.36) * size
        rgb = palette[rng.randrange(len(palette))]
        grad = QRadialGradient(QPointF(x, y), r)
        grad.setColorAt(0.0, QColor(rgb[0], rgb[1], rgb[2], rng.randint(50, 110)))
        grad.setColorAt(0.6, QColor(rgb[0], rgb[1], rgb[2], rng.randint(14, 34)))
        grad.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
        p.setBrush(grad)
        p.drawEllipse(QPointF(x, y), r, r)
    p.end()
    return img


def draw_ship(size: int = 64) -> QImage:
    """飞船标记贴图（朝上的三角舰体 + 引擎光）。"""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = size / 2.0
    # 引擎光
    grad = QRadialGradient(QPointF(c, size * 0.80), size * 0.30)
    grad.setColorAt(0.0, QColor(120, 220, 255, 200))
    grad.setColorAt(1.0, QColor(120, 220, 255, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawEllipse(QPointF(c, size * 0.80), size * 0.30, size * 0.30)
    # 舰体
    path = QPainterPath()
    path.moveTo(c, size * 0.08)
    path.lineTo(size * 0.80, size * 0.82)
    path.lineTo(c, size * 0.66)
    path.lineTo(size * 0.20, size * 0.82)
    path.closeSubpath()
    p.setBrush(QColor(235, 250, 255, 240))
    p.setPen(QPen(QColor(0, 210, 250, 230), max(1.0, size * 0.02)))
    p.drawPath(path)
    p.end()
    return img


# ==============================================================================
# 入口
# ==============================================================================

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    os.makedirs(SPRITES_DIR, exist_ok=True)

    # ---- 图标 ----
    icon_img = draw_icon(512)
    png_path = os.path.join(HERE, "icon.png")
    icon_img.save(png_path, "PNG")
    print(f"已生成 {png_path}")

    ico_path = os.path.join(HERE, "icon.ico")
    try:
        from PySide6.QtGui import QImageWriter
        writer = QImageWriter(ico_path, b"ico")
        writer.setQuality(100)
        # 256×256 是 Windows 图标常用的最大尺寸，兼顾清晰度与文件体积
        if writer.write(icon_img.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)):
            print(f"已生成 {ico_path}（256×256）")
        else:
            print(f"警告：ICO 写入失败（{writer.errorString()}），仅使用 icon.png 亦可打包")
    except Exception as exc:
        print(f"写入 ICO 失败：{exc}（可仅使用 icon.png）")

    # ---- 贴图 ----
    draw_star_glow(128, (255, 240, 210)).save(os.path.join(SPRITES_DIR, "star_glow.png"), "PNG")
    draw_star_glow(128, (170, 200, 255)).save(os.path.join(SPRITES_DIR, "star_glow_blue.png"), "PNG")
    draw_nebula(256, 7).save(os.path.join(SPRITES_DIR, "nebula.png"), "PNG")
    draw_ship(64).save(os.path.join(SPRITES_DIR, "ship.png"), "PNG")
    print(f"已生成贴图目录 {SPRITES_DIR}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
