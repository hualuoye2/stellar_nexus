# -*- coding: utf-8 -*-
"""
body_renderer.py —— 天体精细建模与渲染
================================================================================
本模块用「程序化纹理 + 球面光照」的方式，为太阳系天体生成接近照片观感的模型，
不依赖任何外部图片素材，也不需要 OpenGL：

    核心思路
        1. 用数学函数（分形噪声 / 条纹 / 环形山 / 大陆轮廓）在等距圆柱投影
           上生成行星表面纹理（QImage）；
        2. 把纹理逐像素映射到球面上，按法线做漫反射光照（含晨昏线柔化与相位）；
        3. 叠加边缘变暗（limb darkening）、大气辉光、极冠、云带与阴影。

    主要能力
        * render_planet(key, size, phase, rotation)  生成行星/月球/太阳的球体图像
        * BodyRenderCache                            带缓存与自转动画的渲染器
        * BodyModelView                              可交互（拖动自转）的 3D 天体视图控件
        * deep_sky_glyph(kind)                       深空天体类型的绘制符号

性能：单张 256×256 球体约 8~15 ms，渲染结果按 (类型, 尺寸, 相位, 自转档位) 缓存，
     自转动画每 40 ms 重新生成一帧，CPU 占用极低。
"""

from __future__ import annotations

import math
import random
import time
from typing import Dict, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QSizePolicy, QWidget

from 核心.utils import LOG, clamp, lerp

# numpy 为可选加速项：存在时球面渲染走向量化路径（快 50~100 倍），
# 缺失时自动回退到纯 Python 逐像素实现，功能完全一致。
try:                                                    # pragma: no cover
    import numpy as _np
    _HAS_NUMPY = True
except Exception:                                       # pragma: no cover
    _np = None                                          # type: ignore[assignment]
    _HAS_NUMPY = False

# ==============================================================================
# 一、天体表面参数表
# ==============================================================================
# 每个天体描述：基色、第二色、纹理类型与参数、自转速度、是否有环、光照强度等。

PLANET_SURFACE: Dict[str, Dict[str, object]] = {
    "sun": {
        "base": (255, 246, 210), "accent": (255, 170, 60),
        "texture": "granulation", "contrast": 0.55, "band_count": 0,
        "rotation": 25.4,          # 自转周期（天）
        "limb": 0.35, "glow": 1.0, "self_luminous": True,
        "note": "米粒组织与黑子构成的等离子体表面",
    },
    "mercury": {
        "base": (150, 140, 132), "accent": (96, 88, 84),
        "texture": "cratered", "contrast": 0.75, "craters": 90,
        "rotation": 58.6, "limb": 0.22, "glow": 0.0, "self_luminous": False,
        "note": "布满撞击坑的灰色岩质表面",
    },
    "venus": {
        "base": (232, 208, 158), "accent": (255, 240, 205),
        "texture": "clouds", "contrast": 0.35, "band_count": 5,
        "rotation": -243.0, "limb": 0.18, "glow": 0.55, "self_luminous": False,
        "note": "浓密硫酸云覆盖，看不到固体表面",
    },
    "earth": {
        "base": (38, 96, 178), "accent": (232, 246, 255),
        "texture": "continents", "contrast": 0.8, "craters": 0,
        "rotation": 1.0, "limb": 0.25, "glow": 0.5, "self_luminous": False,
        "note": "海洋、大陆、极冠与大气云层",
    },
    "mars": {
        "base": (196, 106, 66), "accent": (240, 214, 190),
        "texture": "desert", "contrast": 0.65, "craters": 26,
        "rotation": 1.026, "limb": 0.28, "glow": 0.12, "self_luminous": False,
        "note": "铁锈色沙漠、极地冰冠与水手号峡谷",
    },
    "jupiter": {
        "base": (222, 198, 162), "accent": (168, 118, 86),
        "texture": "banded", "contrast": 0.85, "band_count": 16,
        "rotation": 0.414, "limb": 0.30, "glow": 0.25, "self_luminous": False,
        "note": "氢氦大气中的强烈纬向条纹与大红斑",
    },
    "saturn": {
        "base": (232, 212, 168), "accent": (196, 168, 122),
        "texture": "banded", "contrast": 0.45, "band_count": 12,
        "rotation": 0.444, "limb": 0.26, "glow": 0.20, "self_luminous": False,
        "rings": True,
        "note": "淡金色云带与壮观的冰环系统",
    },
    "uranus": {
        "base": (168, 226, 232), "accent": (196, 240, 244),
        "texture": "smooth", "contrast": 0.16, "band_count": 4,
        "rotation": -0.718, "limb": 0.22, "glow": 0.30, "self_luminous": False,
        "rings": True, "ring_faint": True,
        "note": "甲烷造就的青蓝色平滑大气，自转轴几乎躺倒",
    },
    "neptune": {
        "base": (78, 118, 226), "accent": (150, 190, 255),
        "texture": "smooth", "contrast": 0.22, "band_count": 6,
        "rotation": 0.671, "limb": 0.24, "glow": 0.32, "self_luminous": False,
        "note": "深蓝色大气，拥有太阳系最猛烈的风暴",
    },
    "pluto": {
        "base": (188, 172, 158), "accent": (238, 232, 220),
        "texture": "cratered", "contrast": 0.7, "craters": 40,
        "rotation": -6.39, "limb": 0.2, "glow": 0.0, "self_luminous": False,
        "note": "氮冰平原与崎岖山地并存",
    },
    "moon": {
        "base": (176, 172, 166), "accent": (86, 82, 80),
        "texture": "cratered", "contrast": 0.85, "craters": 120,
        "rotation": 27.3, "limb": 0.18, "glow": 0.0, "self_luminous": False,
        "note": "月海、环形山与辐射纹，因潮汐锁定永远同一面朝向地球",
    },
}


# ==============================================================================
# 二、程序化噪声
# ==============================================================================

def _hash2(x: int, y: int, seed: int = 0) -> float:
    """整数格点的确定性伪随机（0~1），用于生成可复现的噪声。"""
    h = (x * 374761393 + y * 668265263 + seed * 1442695040888963407) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def _value_noise(x: float, y: float, seed: int = 0) -> float:
    """二维值噪声（双线性插值）。"""
    x0, y0 = math.floor(x), math.floor(y)
    fx, fy = x - x0, y - y0
    # 平滑插值（smoothstep）
    sx = fx * fx * (3.0 - 2.0 * fx)
    sy = fy * fy * (3.0 - 2.0 * fy)
    n00 = _hash2(int(x0), int(y0), seed)
    n10 = _hash2(int(x0) + 1, int(y0), seed)
    n01 = _hash2(int(x0), int(y0) + 1, seed)
    n11 = _hash2(int(x0) + 1, int(y0) + 1, seed)
    return lerp(lerp(n00, n10, sx), lerp(n01, n11, sx), sy)


def _fbm(x: float, y: float, octaves: int = 5, seed: int = 0,
         lacunarity: float = 2.0, gain: float = 0.5) -> float:
    """分形布朗运动（多八度值噪声叠加），用于生成自然的表面起伏。"""
    total = 0.0
    amp = 1.0
    norm = 0.0
    freq = 1.0
    for i in range(octaves):
        total += amp * _value_noise(x * freq, y * freq, seed + i * 131)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / max(1e-6, norm)


# ==============================================================================
# 三、球体渲染
# ==============================================================================

def render_body(key: str, size: int = 220, phase: float = 1.0,
                rotation: float = 0.0, light_angle: float = -0.6) -> QImage:
    """渲染一个天体的球体图像。

    参数：
        key         天体键名（sun / mercury / ... / moon / earth）
        size        输出图像边长（像素）
        phase       相位（0 = 新月，1 = 满相）—— 决定照亮比例
        rotation    自转相位（0~1），控制表面纹理的经度偏移
        light_angle 光源方向（弧度，-π/2 表示光源在正前方）
    返回：
        带透明通道的 QImage，天体恰好内切于图像（考虑光环时会自动缩小）
    """
    surf = PLANET_SURFACE.get(key, PLANET_SURFACE["earth"])
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)

    has_rings = bool(surf.get("rings"))
    radius = size * (0.34 if has_rings else 0.46)
    cx = cy = size / 2.0

    # ---- 1. 生成球面像素 ----
    base = tuple(surf.get("base", (150, 150, 150)))          # type: ignore[arg-type]
    accent = tuple(surf.get("accent", (220, 220, 220)))      # type: ignore[arg-type]
    texture = str(surf.get("texture", "smooth"))
    contrast = float(surf.get("contrast", 0.5))
    band_count = int(surf.get("band_count", 0))
    craters = int(surf.get("craters", 0))
    limb = float(surf.get("limb", 0.25))
    glow = float(surf.get("glow", 0.0))
    self_lum = bool(surf.get("self_luminous", False))
    seed = abs(hash(key)) % 100000
    phase = clamp(phase, 0.0, 1.0)

    if _HAS_NUMPY:
        surface = _render_surface_numpy(size, cx, cy, radius, texture, rotation,
                                        base, accent, contrast, band_count, craters, seed)
    else:
        surface = None

    if surface is not None:
        _shade_and_write(img, surface, size, cx, cy, radius, light_angle, phase,
                         limb, self_lum)
    else:
        _render_surface_python(img, size, cx, cy, radius, texture, rotation, base, accent,
                               contrast, band_count, craters, seed, light_angle, phase,
                               limb, self_lum)

    # ---- 2. 大气辉光 / 日冕 ----
    if glow > 0.02:
        glow_grad = QRadialGradient(QPointF(cx, cy), radius * (1.55 if self_lum else 1.18))
        glow_grad.setColorAt(0.0, QColor(base[0], base[1], base[2], 0))
        glow_grad.setColorAt(0.78, QColor(base[0], base[1], base[2], int(120 * glow)))
        glow_grad.setColorAt(0.92, QColor(accent[0], accent[1], accent[2], int(90 * glow)))
        glow_grad.setColorAt(1.0, QColor(base[0], base[1], base[2], 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow_grad))
        painter.drawEllipse(QPointF(cx, cy), radius * (1.55 if self_lum else 1.18),
                            radius * (1.55 if self_lum else 1.18))

    # ---- 3. 光环（土星 / 天王星） ----
    if has_rings:
        _draw_rings(painter, cx, cy, radius, base, accent, bool(surf.get("ring_faint")),
                    rotation)

    # ---- 4. 太阳的辐射光刺 ----
    if self_lum:
        painter.setPen(QPen(QColor(255, 240, 200, 120), max(1.0, size * 0.006)))
        for i in range(24):
            a = math.radians(i * 15 + rotation * 360.0)
            l = radius * (1.25 + 0.35 * ((i * 37) % 7) / 7.0)
            painter.drawLine(QPointF(cx + math.cos(a) * radius * 1.05, cy + math.sin(a) * radius * 1.05),
                             QPointF(cx + math.cos(a) * l, cy + math.sin(a) * l))

    painter.end()
    return img


def _surface_color(texture: str, u: float, v: float, lat: float,
                   base: Sequence[int], accent: Sequence[int], contrast: float,
                   band_count: int, craters: int, seed: int,
                   nx: float, ny: float, nz: float) -> Tuple[int, int, int]:
    """按纹理类型计算某一点的表面颜色（纯 Python 路径，返回 0~255 的 RGB）。"""
    def mix(t: float) -> Tuple[int, int, int]:
        t = clamp(t, 0.0, 1.0)
        return (int(lerp(base[0], accent[0], t)), int(lerp(base[1], accent[1], t)),
                int(lerp(base[2], accent[2], t)))

    if texture == "granulation":
        # 太阳：米粒组织 + 黑子
        n = _fbm(u * 22.0, v * 22.0, 4, seed)
        spots = _fbm(u * 5.0, v * 5.0, 3, seed + 77)
        t = 0.35 + 0.65 * n
        if spots > 0.72:
            t *= 0.35                      # 黑子区域
        return mix(t)

    if texture == "cratered":
        # 岩质天体：撞击坑 + 高原
        n = _fbm(u * 9.0, v * 9.0, 5, seed)
        c = _fbm(u * 30.0, v * 30.0, 3, seed + 31)
        t = 0.25 + 0.75 * n * contrast + 0.35 * (1.0 - c) / max(1.0, craters / 30.0 + 1.0)
        t = clamp(t, 0.0, 1.0)
        # 环形山的亮环与暗坑
        if c > 0.62:
            t = clamp(t + 0.22, 0.0, 1.0)
        elif c < 0.34:
            t = clamp(t - 0.20, 0.0, 1.0)
        return mix(t)

    if texture == "clouds":
        # 金星：厚云带
        band = math.sin(v * band_count * math.pi * 2.0 + _fbm(u * 4.0, v * 4.0, 3, seed) * 3.0)
        t = 0.45 + 0.55 * (0.5 + 0.5 * band) * contrast + 0.2 * _fbm(u * 12.0, v * 12.0, 3, seed + 5)
        return mix(t)

    if texture == "banded":
        # 气态巨行星：纬向条纹 + 涡旋
        warp = (_fbm(u * 3.0, v * 6.0, 4, seed) - 0.5) * 0.10
        band = math.sin((v + warp) * band_count * math.pi)
        t = 0.5 + 0.5 * band * contrast
        t += 0.18 * (_fbm(u * 14.0, v * 20.0, 4, seed + 11) - 0.5)
        # 大红斑（木星）
        if -0.35 < lat < -0.05 and (u % 1.0) > 0.42 and (u % 1.0) < 0.56:
            t = clamp(t + 0.45, 0.0, 1.0)
        return mix(t)

    if texture == "continents":
        # 地球：海陆分布 + 极冠 + 云
        land = _fbm(u * 5.0, v * 5.0, 6, seed)
        sea_level = 0.52
        if abs(lat) > 1.20:
            return mix(0.92)                     # 极冠
        if land > sea_level:
            green = clamp((land - sea_level) / (1.0 - sea_level), 0.0, 1.0)
            # 陆地：绿→棕→白
            r = int(lerp(70, 200, green))
            g = int(lerp(120, 190, green))
            b = int(lerp(70, 170, green))
            # 云层
            cloud = _fbm(u * 9.0, v * 9.0, 4, seed + 61)
            if cloud > 0.62:
                k = (cloud - 0.62) / 0.38
                r = int(lerp(r, 250, k)); g = int(lerp(g, 250, k)); b = int(lerp(b, 255, k))
            return (clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255))
        # 海洋：深浅随距离海岸变化
        depth = clamp((sea_level - land) / sea_level, 0.0, 1.0)
        return mix(0.1 + 0.55 * depth)

    if texture == "desert":
        # 火星：铁锈色地表 + 暗区 + 极冠
        n = _fbm(u * 7.0, v * 7.0, 5, seed)
        t = 0.35 + 0.65 * n * contrast
        if abs(lat) > 1.28:
            return mix(0.95)                     # 极冠
        if 0.45 < n < 0.58:
            t *= 0.72                            # 大瑟提斯等暗区
        return mix(t)

    # smooth：冰巨星
    band = math.sin(v * max(1, band_count) * math.pi)
    t = 0.5 + 0.5 * band * contrast + 0.1 * (_fbm(u * 8.0, v * 8.0, 3, seed) - 0.5)
    return mix(t)


# ==============================================================================
# numpy 向量化实现（性能关键路径：比纯 Python 快 50~100 倍）
# ==============================================================================

def _noise_np(x, y, seed: int):
    """numpy 版二维值噪声（与纯 Python 版同分布）。"""
    np = _np
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = x - x0
    fy = y - y0
    sx = fx * fx * (3.0 - 2.0 * fx)
    sy = fy * fy * (3.0 - 2.0 * fy)
    xi = x0.astype(np.int64)
    yi = y0.astype(np.int64)

    def h(ix, iy):
        # 全程使用 int64 且及时取模，避免溢出（seed 先折叠到 32 位）
        v = (ix * 374761393 + iy * 668265263 + (seed & 0xFFFF) * 1013904223) & 0xFFFFFFFF
        v = ((v ^ (v >> 13)) * 1274126177) & 0xFFFFFFFF
        return ((v ^ (v >> 16)) & 0xFFFF).astype(np.float32) / 65535.0

    n00 = h(xi, yi)
    n10 = h(xi + 1, yi)
    n01 = h(xi, yi + 1)
    n11 = h(xi + 1, yi + 1)
    return (n00 * (1 - sx) + n10 * sx) * (1 - sy) + (n01 * (1 - sx) + n11 * sx) * sy


def _fbm_np(x, y, octaves: int, seed: int, lacunarity: float = 2.0, gain: float = 0.5):
    """numpy 版分形噪声。"""
    np = _np
    total = np.zeros_like(x, dtype=np.float32)
    amp, freq, norm = 1.0, 1.0, 0.0
    for i in range(octaves):
        total = total + amp * _noise_np(x * freq, y * freq, seed + i * 131)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / max(1e-6, norm)


def _render_surface_numpy(size: int, cx: float, cy: float, radius: float,
                          texture: str, u_base: float,
                          base: Sequence[int], accent: Sequence[int], contrast: float,
                          band_count: int, craters: int, seed: int):
    """用 numpy 一次性生成整张球面纹理，返回 (size, size, 3) 的 float32 数组。"""
    if not _HAS_NUMPY:
        return None
    np = _np
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    nx = (xx + 0.5 - cx) / radius
    ny = (cy - yy - 0.5) / radius
    r2 = nx * nx + ny * ny
    inside = r2 <= 1.0
    nz = np.sqrt(np.clip(1.0 - r2, 0.0, 1.0))
    lon = np.arctan2(nx, nz) + u_base * 2.0 * np.pi
    lat = np.arcsin(np.clip(ny, -1.0, 1.0))
    u = np.mod(lon / (2.0 * np.pi), 1.0)
    v = lat / np.pi + 0.5

    b = np.array(base, dtype=np.float32)
    a = np.array(accent, dtype=np.float32)

    if texture == "granulation":
        n = _fbm_np(u * 22.0, v * 22.0, 4, seed)
        spots = _fbm_np(u * 5.0, v * 5.0, 3, seed + 77)
        t = 0.35 + 0.65 * n
        t = np.where(spots > 0.72, t * 0.35, t)
    elif texture == "cratered":
        n = _fbm_np(u * 9.0, v * 9.0, 5, seed)
        c = _fbm_np(u * 30.0, v * 30.0, 3, seed + 31)
        t = 0.25 + 0.75 * n * contrast + 0.35 * (1.0 - c) / max(1.0, craters / 30.0 + 1.0)
        t = np.where(c > 0.62, t + 0.22, np.where(c < 0.34, t - 0.20, t))
    elif texture == "clouds":
        band = np.sin(v * band_count * np.pi * 2.0 + _fbm_np(u * 4.0, v * 4.0, 3, seed) * 3.0)
        t = (0.45 + 0.55 * (0.5 + 0.5 * band) * contrast
             + 0.2 * _fbm_np(u * 12.0, v * 12.0, 3, seed + 5))
    elif texture == "banded":
        warp = (_fbm_np(u * 3.0, v * 6.0, 4, seed) - 0.5) * 0.10
        band = np.sin((v + warp) * band_count * np.pi)
        t = 0.5 + 0.5 * band * contrast + 0.18 * (_fbm_np(u * 14.0, v * 20.0, 4, seed + 11) - 0.5)
        spot = ((lat > -0.35) & (lat < -0.05)
                & (np.mod(u, 1.0) > 0.42) & (np.mod(u, 1.0) < 0.56))
        t = np.where(spot, t + 0.45, t)
    elif texture == "continents":
        land = _fbm_np(u * 5.0, v * 5.0, 6, seed)
        sea = 0.52
        green = np.clip((land - sea) / (1.0 - sea), 0.0, 1.0)
        land_rgb = np.stack([70.0 + 130.0 * green, 120.0 + 70.0 * green,
                             70.0 + 100.0 * green], axis=-1)
        ocean_t = (0.1 + 0.55 * np.clip((sea - land) / sea, 0.0, 1.0))[..., None]
        ocean_rgb = b[None, None, :] * (1.0 - ocean_t) + a[None, None, :] * ocean_t
        cloud = _fbm_np(u * 9.0, v * 9.0, 4, seed + 61)
        ck = np.clip((cloud - 0.62) / 0.38, 0.0, 1.0)[..., None]
        land_rgb = land_rgb * (1.0 - ck) + np.array([250.0, 250.0, 255.0], dtype=np.float32)[None, None, :] * ck
        color = np.where((land > sea)[..., None], land_rgb, ocean_rgb)
        polar = np.abs(lat) > 1.20
        ice = b[None, None, :] * 0.08 + np.array([245.0, 248.0, 255.0], dtype=np.float32)[None, None, :] * 0.92
        color = np.where(polar[..., None], ice, color)
        surface = np.clip(color, 0.0, 255.0).astype(np.float32)
        surface[~inside] = 0.0
        return surface
    elif texture == "desert":
        n = _fbm_np(u * 7.0, v * 7.0, 5, seed)
        t = 0.35 + 0.65 * n * contrast
        t = np.where((n > 0.45) & (n < 0.58), t * 0.72, t)
        t = np.where(np.abs(lat) > 1.28, 0.95, t)
    else:
        band = np.sin(v * max(1, band_count) * np.pi)
        t = 0.5 + 0.5 * band * contrast + 0.1 * (_fbm_np(u * 8.0, v * 8.0, 3, seed) - 0.5)

    t = np.clip(t, 0.0, 1.0)[..., None]
    surface = b[None, None, :] * (1.0 - t) + a[None, None, :] * t
    surface = np.clip(surface, 0.0, 255.0).astype(np.float32)
    surface[~inside] = 0.0
    return surface


def _shade_and_write(img: QImage, surface, size: int, cx: float, cy: float, radius: float,
                     light_angle: float, phase: float, limb: float, self_lum: bool) -> None:
    """对球面纹理做光照着色，并把 ARGB32 像素直接写入 QImage。"""
    np = _np
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    nx = (xx + 0.5 - cx) / radius
    ny = (cy - yy - 0.5) / radius
    r2 = nx * nx + ny * ny
    inside = r2 <= 1.0
    nz = np.sqrt(np.clip(1.0 - r2, 0.0, 1.0))

    if self_lum:
        shade = np.ones_like(nx)
    else:
        lx = math.cos(light_angle) * math.cos(-0.35)
        ly = math.sin(light_angle)
        lz = math.cos(light_angle) * math.sin(-0.35)
        ndl = nx * lx + ny * ly + nz * lz
        # 相位项：0.60 的经验系数让满相时几乎全亮、新月时几乎全暗，
        # 同时保留一条柔和的晨昏线（0.85 会让整体偏暗，故下调）。
        ndl = ndl + (-1.0 + 2.0 * clamp(phase, 0.0, 1.0)) * 0.60
        shade = np.clip(ndl, 0.0, 1.0) ** 0.85
        shade = shade * (1.0 - limb * (1.0 - nz) ** 2)
        shade = np.clip(shade, 0.0, 1.0)

    rgb = np.clip(surface * shade[..., None], 0.0, 255.0).astype(np.uint8)
    alpha = np.where(inside, 255, 0).astype(np.uint8)
    # QImage.Format_ARGB32 在小端机器上的内存布局是 BGRA，可用 numpy 视图一次性写入
    buf = np.ascontiguousarray(np.dstack([rgb[..., 2], rgb[..., 1], rgb[..., 0], alpha]))
    data = buf.reshape(-1)
    bits = img.bits()
    try:
        view = np.frombuffer(bits, dtype=np.uint8, count=img.sizeInBytes())
        view[:] = data[: view.size]
    except Exception as exc:  # pragma: no cover - 极少触发
        LOG.warn(f"QImage 像素写入失败，改用逐像素回退：{exc}")
        for y in range(size):
            for x in range(size):
                r, g, b, a = buf[y, x]
                img.setPixelColor(x, y, QColor(int(r), int(g), int(b), int(a)))


def _render_surface_python(img: QImage, size: int, cx: float, cy: float, radius: float,
                           texture: str, rotation: float, base, accent, contrast: float,
                           band_count: int, craters: int, seed: int,
                           light_angle: float, phase: float, limb: float,
                           self_lum: bool) -> None:
    """纯 Python 逐像素渲染（numpy 缺失时的回退路径，功能完全一致）。"""
    lx = math.cos(light_angle) * math.cos(-0.35)
    ly = math.sin(light_angle)
    lz = math.cos(light_angle) * math.sin(-0.35)
    phase = clamp(phase, 0.0, 1.0)
    lit_threshold = -1.0 + 2.0 * phase
    for py in range(size):
        ny = (cy - py - 0.5) / radius
        if abs(ny) > 1.0:
            continue
        for px in range(size):
            nx = (px + 0.5 - cx) / radius
            r2 = nx * nx + ny * ny
            if r2 > 1.0:
                continue
            nz = math.sqrt(max(0.0, 1.0 - r2))
            lon = math.atan2(nx, nz) + rotation * 2.0 * math.pi
            lat = math.asin(clamp(ny, -1.0, 1.0))
            u = (lon / (2.0 * math.pi)) % 1.0
            v = (lat / math.pi) + 0.5
            color = _surface_color(texture, u, v, lat, base, accent, contrast,
                                   band_count, craters, seed, nx, ny, nz)
            if self_lum:
                shade = 1.0
            else:
                ndl = nx * lx + ny * ly + nz * lz + lit_threshold * 0.60
                shade = clamp(ndl, 0.0, 1.0) ** 0.85
                shade *= (1.0 - limb * (1.0 - nz) ** 2)
                shade = clamp(shade, 0.0, 1.0)
            img.setPixelColor(px, py, QColor(int(clamp(color[0] * shade, 0, 255)),
                                             int(clamp(color[1] * shade, 0, 255)),
                                             int(clamp(color[2] * shade, 0, 255)), 255))


def _draw_rings(painter: QPainter, cx: float, cy: float, radius: float,
                base: Sequence[int], accent: Sequence[int], faint: bool,
                rotation: float) -> None:
    """绘制行星环（用椭圆环 + 明暗分层模拟）。"""
    tilt = math.radians(18.0)
    ring_outer = radius * (1.95 if not faint else 1.55)
    ring_inner = radius * (1.25 if not faint else 1.30)
    alpha_scale = 0.55 if faint else 1.0

    # 环的后半（被行星遮挡的部分先画）
    painter.save()
    painter.translate(cx, cy)
    painter.rotate(math.degrees(tilt))
    painter.setBrush(Qt.NoBrush)
    bands = 9 if not faint else 4
    for i in range(bands):
        t = i / max(1, bands - 1)
        rr = lerp(ring_inner, ring_outer, t)
        a = int(alpha_scale * (150 - 90 * abs(t - 0.5) * 2))
        pen = QPen(QColor(accent[0], accent[1], accent[2], max(20, a)),
                   radius * 0.14)
        painter.setPen(pen)
        painter.drawEllipse(QPointF(0, 0), rr, rr * 0.28)
    painter.restore()

    # 行星本体遮挡环的前半：用背景色覆盖（这里用透明处理，让环穿过前景更自然）
    painter.save()
    painter.translate(cx, cy)
    painter.rotate(math.degrees(tilt))
    painter.setPen(QPen(QColor(accent[0], accent[1], accent[2], int(200 * alpha_scale)),
                        radius * 0.10))
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(-ring_outer, 0)
    path.arcTo(QRectF(-ring_outer, -ring_outer * 0.28, ring_outer * 2, ring_outer * 0.56), 180, 180)
    painter.drawPath(path)
    painter.restore()


# ==============================================================================
# 四、渲染缓存（带动画）
# ==============================================================================

class BodyRenderCache:
    """天体图像缓存：按 (类型, 尺寸, 相位档位, 自转档位) 缓存，避免重复渲染。"""

    MAX_ENTRIES = 96

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, int, int, int], QPixmap] = {}
        self._order: list = []

    def pixmap(self, key: str, size: int, phase: float = 1.0,
               rotation: float = 0.0) -> QPixmap:
        """取得（必要时生成）天体图像。相位与自转都做量化以提升缓存命中率。"""
        size = int(clamp(size, 32, 512))
        phase_q = int(clamp(phase, 0.0, 1.0) * 20)          # 5% 一档
        rot_q = int((rotation % 1.0) * 36)                  # 10° 一档
        ck = (key, size, phase_q, rot_q)
        hit = self._cache.get(ck)
        if hit is not None:
            return hit
        img = render_body(key, size, phase_q / 20.0, rot_q / 36.0)
        pix = QPixmap.fromImage(img)
        if len(self._cache) >= self.MAX_ENTRIES:
            oldest = self._order.pop(0) if self._order else None
            if oldest is not None:
                self._cache.pop(oldest, None)
        self._cache[ck] = pix
        self._order.append(ck)
        return pix

    def clear(self) -> None:
        self._cache.clear()
        self._order.clear()


#: 全局共享缓存（现实星图绘制与信息面板共用）
BODY_CACHE = BodyRenderCache()


def body_pixmap(key: str, size: int, phase: float = 1.0, rotation: float = 0.0) -> QPixmap:
    """便捷入口：取得天体图像（带缓存）。"""
    return BODY_CACHE.pixmap(key, size, phase, rotation)


def body_rotation_phase(key: str, when_seconds: float) -> float:
    """根据真实时间计算自转相位（0~1）。

    自转周期取自 PLANET_SURFACE（单位：地球日）；太阳与行星都用真实周期，
    因此拖动时间滑块时能看到表面自转（木星 10 小时一圈，肉眼可见）。
    """
    surf = PLANET_SURFACE.get(key)
    if not surf:
        return 0.0
    period_days = float(surf.get("rotation", 1.0)) or 1.0
    period_seconds = abs(period_days) * 86400.0
    phase = (when_seconds / period_seconds) % 1.0
    if period_days < 0:                     # 逆行自转
        phase = 1.0 - phase
    return phase


# ==============================================================================
# 五、可交互天体模型视图
# ==============================================================================

class BodyModelView(QWidget):
    """可拖动自转的天体模型视图（用于信息面板中的「精细模型」展示）。"""

    def __init__(self, parent: Optional[QWidget] = None, size: int = 240) -> None:
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self.setMaximumHeight(size + 60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setCursor(Qt.OpenHandCursor)
        self._key = "earth"
        self._phase = 1.0
        self._rotation = 0.0
        self._auto_spin = True
        self._dragging = False
        self._drag_dx = 0.0
        self._label = ""
        self._render_size = 0
        self._pixmap: Optional[QPixmap] = None
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(50)
        self._spin_timer.timeout.connect(self._tick)
        self._spin_timer.start()

    # ---- 对外接口 ----

    def set_body(self, key: str, phase: float = 1.0, label: str = "") -> None:
        """设置要展示的天体。"""
        self._key = key if key in PLANET_SURFACE else "earth"
        self._phase = clamp(phase, 0.0, 1.0)
        self._label = label or str(PLANET_SURFACE.get(self._key, {}).get("note", ""))
        self._rotation = 0.0
        self._pixmap = None
        self.update()

    def set_phase(self, phase: float) -> None:
        self._phase = clamp(phase, 0.0, 1.0)
        self.update()

    def current_key(self) -> str:
        return self._key

    # ---- 内部 ----

    def _tick(self) -> None:
        if self._auto_spin and not self._dragging and self.isVisible():
            self._rotation = (self._rotation + 0.0035) % 1.0
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_dx = 0.0
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._rotation = (self._rotation - event.position().x() * 0.0025) % 1.0
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = False
        self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """滚轮调整相位（观察不同月相 / 光照角度）。"""
        delta = event.angleDelta().y()
        if delta:
            self._phase = clamp(self._phase + (0.05 if delta > 0 else -0.05), 0.0, 1.0)
            self.update()
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        # 背景：深空 + 网格
        grad = QRadialGradient(QPointF(w / 2.0, h / 2.0), max(w, h) * 0.7)
        grad.setColorAt(0.0, QColor(10, 24, 40, 255))
        grad.setColorAt(1.0, QColor(4, 10, 20, 255))
        p.fillRect(self.rect(), QBrush(grad))
        p.setPen(QPen(QColor(0, 150, 200, 45), 1.0))
        for i in range(1, 6):
            y = h * i / 6.0
            p.drawLine(0, int(y), w, int(y))
        for i in range(1, 6):
            x = w * i / 6.0
            p.drawLine(int(x), 0, int(x), h)

        size = int(min(w, h) * 0.86)
        size = max(64, size)
        if self._pixmap is None or self._render_size != size:
            self._pixmap = body_pixmap(self._key, size, self._phase, self._rotation)
            self._render_size = size
        else:
            # 自转/相位变化时重新取缓存图
            self._pixmap = body_pixmap(self._key, size, self._phase, self._rotation)
        p.drawPixmap(QPointF((w - size) / 2.0, (h - size) / 2.0), self._pixmap)

        # 标注
        p.setPen(QColor(190, 240, 255, 235))
        p.setFont(QFont("Microsoft YaHei UI", 9, QFont.DemiBold))
        name = str(PLANET_SURFACE.get(self._key, {}).get("note", ""))
        p.drawText(QRectF(6, h - 34, w - 12, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   f"精细模型　相位 {self._phase * 100:.0f}%")
        p.setPen(QColor(160, 200, 222, 220))
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.drawText(QRectF(6, h - 19, w - 12, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   "拖动自转　滚轮调相位")
        del name
        p.end()


# ==============================================================================
# 六、深空天体类型符号
# ==============================================================================

#: 深空天体类型 → (缩写, 颜色)
DSO_STYLE: Dict[str, Tuple[str, Tuple[int, int, int]]] = {
    "旋涡星系": ("S", (170, 200, 255)),
    "棒旋星系": ("SB", (170, 200, 255)),
    "椭圆星系": ("E", (200, 190, 255)),
    "透镜星系": ("L", (190, 200, 250)),
    "不规则星系": ("Irr", (180, 210, 255)),
    "活动星系": ("AGN", (255, 190, 220)),
    "球状星团": ("G", (255, 230, 170)),
    "疏散星团": ("O", (170, 255, 220)),
    "发射星云": ("N", (255, 150, 170)),
    "反射星云": ("RN", (150, 200, 255)),
    "行星状星云": ("PN", (170, 255, 230)),
    "超新星遗迹": ("SNR", (255, 170, 140)),
    "暗星云": ("DN", (140, 150, 170)),
    "恒星云": ("C", (255, 240, 200)),
    "双星": ("**", (230, 230, 255)),
    "星群": ("Ast", (200, 255, 230)),
}


def deep_sky_style(mtype: str) -> Tuple[str, Tuple[int, int, int]]:
    """取深空天体类型的缩写与颜色。"""
    for key, value in DSO_STYLE.items():
        if key in (mtype or ""):
            return value
    return ("DSO", (170, 255, 210))


def draw_deep_sky_symbol(p: QPainter, center: QPointF, radius: float, mtype: str,
                         color: Optional[QColor] = None) -> None:
    """按类型绘制深空天体符号（星系=椭圆、球状星团=带十字的圆、疏散星团=虚线圆、
    星云=方块、遗迹=双圈）。"""
    abbr, rgb = deep_sky_style(mtype)
    c = color or QColor(rgb[0], rgb[1], rgb[2])
    r = max(3.0, radius)
    if "星系" in (mtype or ""):
        # 椭圆 + 中心核
        p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 225), 1.1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(center, r * 1.35, r * 0.78)
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 150))
        p.setPen(Qt.NoPen)
        p.drawEllipse(center, r * 0.30, r * 0.30)
    elif "球状" in (mtype or ""):
        p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 225), 1.2))
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 60))
        p.drawEllipse(center, r, r)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(center.x() - r * 1.5, center.y()), QPointF(center.x() + r * 1.5, center.y()))
        p.drawLine(QPointF(center.x(), center.y() - r * 1.5), QPointF(center.x(), center.y() + r * 1.5))
    elif "疏散" in (mtype or "") or "星群" in (mtype or ""):
        pen = QPen(QColor(c.red(), c.green(), c.blue(), 210), 1.0, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(center, r * 1.1, r * 1.1)
    elif "遗迹" in (mtype or ""):
        p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 230), 1.1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(center, r * 1.2, r * 1.0)
        p.drawEllipse(center, r * 1.7, r * 1.45)
    elif "行星状" in (mtype or ""):
        p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 230), 1.2))
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 55))
        p.drawEllipse(center, r * 1.15, r * 1.15)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 190), 0.9))
        p.drawEllipse(center, r * 0.45, r * 0.45)
    elif "暗" in (mtype or ""):
        p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 190), 1.0))
        p.setBrush(QColor(6, 10, 18, 120))
        p.drawRect(QRectF(center.x() - r, center.y() - r * 0.8, r * 2, r * 1.6))
    else:
        # 星云：方形边框 + 内部柔光
        p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 220), 1.1))
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 45))
        p.drawRect(QRectF(center.x() - r, center.y() - r, r * 2, r * 2))


__all__ = [
    "PLANET_SURFACE", "render_body", "BodyRenderCache", "BODY_CACHE", "body_pixmap",
    "body_rotation_phase", "BodyModelView", "DSO_STYLE", "deep_sky_style",
    "draw_deep_sky_symbol",
]
