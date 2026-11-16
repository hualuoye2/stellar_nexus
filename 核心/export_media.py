# -*- coding: utf-8 -*-
"""
export_media.py —— 导出星空图片与延时动画
================================================================================
把当前星图"拍下来"：

    export_png(widget, path, ...)      导出单张 PNG（可带 HUD 与右下角信息条）
    export_night_gif(view, path, ...)  导出整夜星空旋转的 GIF
                                       （按固定时间间隔取帧，从黄昏到黎明）

GIF 实现说明：
    * 不依赖 Pillow —— 直接用 Qt 的 QImageWriter("GIF") 逐帧写入，
      这样打包体积不变、依赖不增加
    * 帧率用"每帧延迟毫秒数"控制（GIF 规范里最小 20ms，浏览器通常按 100ms 起步）
    * 为了控制体积，帧尺寸默认缩到 640px 宽，并用调色板量化（Qt 自动处理）
    * 支持两种模式：
        rotation  固定时刻、按整夜时间推进（星星东升西落，最直观）
        hours     指定起止时间与步长（默认 19:00 → 05:00，每 10 分钟一帧）

导出进程走后台线程，界面用进度回调刷新，避免大分辨率长动画把界面卡住。
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QImageWriter, QPainter,
                           QPen, QPixmap)

from 核心 import astronomy as A
from 核心.utils import LOG, app_dir, ensure_dir, format_datetime, timestamp_tag

#: 默认导出尺寸档位（宽 × 高）
EXPORT_SIZES: Tuple[Tuple[str, Tuple[int, int]], ...] = (
    ("1280 × 720（720p）", (1280, 720)),
    ("1920 × 1080（1080p）", (1920, 1080)),
    ("2560 × 1440（2K）", (2560, 1440)),
    ("3840 × 2160（4K）", (3840, 2160)),
)


# ==============================================================================
# 单张 PNG
# ==============================================================================

def render_widget(widget, size: Optional[Tuple[int, int]] = None) -> QImage:
    """把任意 QWidget 渲染成 QImage（支持指定输出尺寸，等比缩放）。"""
    try:
        if size:
            w, h = int(size[0]), int(size[1])
            pix: QPixmap = widget.grab()
            if pix.width() != w or pix.height() != h:
                pix = pix.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            return pix.toImage()
        return widget.grab().toImage()
    except Exception as exc:
        LOG.error("渲染控件为图片失败", exc)
        img = QImage(640, 360, QImage.Format_ARGB32)
        img.fill(QColor(4, 8, 16))
        return img


def export_png(widget, path: str, size: Optional[Tuple[int, int]] = None,
               overlay_text: str = "", footer: str = "") -> bool:
    """导出单张 PNG。

    参数：
        widget       : 要导出的视图控件
        path         : 目标文件路径（.png）
        size         : 输出尺寸；None 表示按控件当前大小
        overlay_text : 左上角叠加的标题文字（一般放时间/地点）
        footer       : 右下角叠加的说明文字（一般放软件签名）
    """
    try:
        ensure_dir(os.path.dirname(os.path.abspath(path)))
        img = render_widget(widget, size)
        if overlay_text or footer:
            img = _draw_overlay(img, overlay_text, footer)
        writer = QImageWriter(path, b"png")
        writer.setQuality(95)
        if not writer.write(img):
            LOG.error(f"PNG 写入失败：{writer.errorString()}")
            return False
        LOG.info(f"已导出星空图片：{path}（{img.width()}×{img.height()}）")
        return True
    except Exception as exc:
        LOG.error("导出 PNG 失败", exc)
        return False


def _draw_overlay(img: QImage, title: str, footer: str) -> QImage:
    """在图片上叠加标题与签名（半透明底条，不遮挡主体）。"""
    try:
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        if title:
            p.setFont(QFont("Microsoft YaHei UI", max(11, img.width() // 78), QFont.Bold))
            fm = QFontMetrics(p.font())
            w = fm.horizontalAdvance(title) + 24
            h = fm.height() + 12
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(4, 14, 24, 165))
            p.drawRoundedRect(10, 10, w, h, 5, 5)
            p.setPen(QColor(0, 229, 255, 220))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(10, 10, w, h, 5, 5)
            p.setPen(QColor(230, 250, 255))
            p.drawText(22, 10 + h - 8, title)
        if footer:
            p.setFont(QFont("Microsoft YaHei UI", max(9, img.width() // 120)))
            fm = QFontMetrics(p.font())
            w = fm.horizontalAdvance(footer) + 20
            h = fm.height() + 8
            x = img.width() - w - 12
            y = img.height() - h - 12
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(4, 14, 24, 140))
            p.drawRoundedRect(x, y, w, h, 4, 4)
            p.setPen(QColor(150, 220, 240, 210))
            p.drawText(x + 10, y + h - 6, footer)
        p.end()
    except Exception as exc:
        LOG.warn(f"叠加文字失败：{exc}")
    return img


# ==============================================================================
# 整夜延时 GIF
# ==============================================================================

def night_frame_times(when: datetime, start_hour: float = 19.0, end_hour: float = 5.0,
                      step_minutes: float = 10.0) -> List[datetime]:
    """生成"整夜"的取帧时刻列表（默认 19:00 → 次日 05:00）。"""
    frames: List[datetime] = []
    begin = when.replace(hour=int(start_hour), minute=int((start_hour % 1) * 60),
                         second=0, microsecond=0)
    if end_hour <= start_hour:
        end = (begin + timedelta(days=1)).replace(
            hour=int(end_hour), minute=int((end_hour % 1) * 60), second=0, microsecond=0)
    else:
        end = begin.replace(hour=int(end_hour), minute=int((end_hour % 1) * 60),
                            second=0, microsecond=0)
    step = max(1.0, float(step_minutes))
    t = begin
    while t <= end:
        frames.append(t)
        t = t + timedelta(minutes=step)
    return frames


def export_night_gif_blocking(view, path: str, when: Optional[datetime] = None,
                     start_hour: float = 19.0, end_hour: float = 5.0,
                     step_minutes: float = 10.0, width: int = 640,
                     frame_delay_ms: int = 120, overlay: str = "",
                     progress: Optional[Callable[[int, int], None]] = None,
                     cancel: Optional[Callable[[], bool]] = None,
                     max_colors: int = 256,
                     fallback_png_dir: str = "") -> Tuple[bool, str]:
    """导出整夜星空旋转的 GIF（**阻塞实现，只能在主线程调用**）。

    ★ 重要：函数内部会调用 `view.set_time()` / `view.recalculate()` /
      `render_widget(view)`（= `widget.grab()`），这些都是 GUI 操作。
      从工作线程调用会导致**进程级崩溃**——用户报的"生成 MP4 程序会崩溃闪退"
      就是这么来的。界面请一律改用 `NightExportController`（渲染在主线程、
      编码在后台线程）；本函数只留给"没有事件循环的脚本/测试"使用。

    实现说明（重要）：
        PySide6 的 Qt 构建**通常不包含 GIF 写入插件**
        （QImageWriter.supportedImageFormats() 里没有 gif），
        因此这里用 Pillow 写 GIF；若环境里没有 Pillow，则自动降级为
        **导出 PNG 帧序列**（同样有分享价值，可用其它工具合成视频）。

    参数：
        view            : 现实星图视图（需要支持 set_time）
        path            : 目标 .gif 路径
        when            : 基准日期（默认当前观测时间）
        start_hour/end_hour/step_minutes : 取帧时间范围与步长
        width           : 输出宽度（高度按视图比例）
        frame_delay_ms  : 每帧延迟（毫秒）；越小越快
        max_colors      : GIF 调色板颜色数（越少体积越小）
        progress        : 进度回调 (done, total)
        cancel          : 返回 True 时中止导出
        fallback_png_dir: 降级导出 PNG 序列的目录（留空则用目标路径同级目录）

    返回：(是否成功, 说明文字)
    """
    saved_observer = getattr(view, "observer", None)
    saved_when = saved_observer.when if saved_observer is not None else None
    base = when or saved_when or datetime.now()
    frames = night_frame_times(base, start_hour, end_hour, step_minutes)
    if not frames:
        return (False, "没有生成任何帧，请检查时间范围")
    total = len(frames)
    try:
        ensure_dir(os.path.dirname(os.path.abspath(path)))
    except Exception:
        pass

    try:
        from PIL import Image                      # noqa: N813
        has_pillow = True
    except Exception:
        has_pillow = False

    images: List[object] = []
    out_w = out_h = 0
    try:
        for i, t in enumerate(frames):
            if cancel is not None and cancel():
                return (False, f"已取消（已渲染 {i} 帧）")
            if saved_observer is not None:
                view.set_time(t)
            if hasattr(view, "recalculate"):
                try:
                    view.recalculate(force=True)
                except Exception:
                    pass
            img = render_widget(view, None)
            if out_w == 0:
                ratio = img.height() / max(1, img.width())
                out_w = max(160, int(width))
                out_h = max(90, int(round(out_w * ratio)))
                out_w -= out_w % 2          # GIF 尺寸取偶数兼容性更好
                out_h -= out_h % 2
            img = img.scaled(out_w, out_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            if overlay:
                img = _draw_overlay(img, f"{overlay}　{t.strftime('%H:%M')}", "STELLAR NEXUS")
            images.append(_qimage_to_pil(img) if has_pillow else img)
            if progress is not None:
                progress(i + 1, total)

        if has_pillow:
            first = images[0].convert("P", palette=Image.ADAPTIVE, colors=max_colors)
            rest = [im.convert("P", palette=Image.ADAPTIVE, colors=max_colors)
                    for im in images[1:]]
            duration = max(20, int(frame_delay_ms))
            first.save(path, save_all=True, append_images=rest, duration=duration,
                       loop=0, optimize=True, disposal=2)
            size_mb = os.path.getsize(path) / 1024 / 1024 if os.path.isfile(path) else 0.0
            note = (f"已导出 {total} 帧 GIF，{out_w}×{out_h}，每帧 {duration} ms"
                    f"（约 {total * duration / 1000:.1f} 秒动画），"
                    f"{max_colors} 色，{size_mb:.2f} MB")
            LOG.info(f"整夜延时 GIF 导出完成：{path}　{note}")
            return (True, note)

        # ---- 降级：导出 PNG 帧序列 ----
        folder = fallback_png_dir or os.path.join(
            os.path.dirname(os.path.abspath(path)),
            os.path.splitext(os.path.basename(path))[0] + "_frames")
        ensure_dir(folder)
        for i, img in enumerate(images):
            frame_path = os.path.join(folder, f"frame_{i + 1:04d}.png")
            img.save(frame_path, "PNG")
        return (True, f"未找到 Pillow，已降级导出 {total} 张 PNG 帧到：{folder}")
    except Exception as exc:
        LOG.error("导出整夜延时失败", exc)
        return (False, f"导出失败：{exc}")
    finally:
        # 无论成功失败都恢复视图时间
        if saved_observer is not None and saved_when is not None:
            try:
                view.set_time(saved_when)
                if hasattr(view, "recalculate"):
                    view.recalculate(force=True)
            except Exception:
                pass


def _qimage_to_pil(img: QImage):
    """QImage → PIL.Image（走 ARGB32 原始字节，避免编码往返）。"""
    from PIL import Image                       # noqa: N813
    converted = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = converted.width(), converted.height()
    ptr = converted.constBits()
    data = bytes(ptr) if ptr is not None else b""
    return Image.frombytes("RGBA", (w, h), data).convert("RGB")


def estimate_gif(frames: int, width: int, height: int, delay_ms: int = 120) -> str:
    """粗略估算导出规模，给界面提示用。"""
    pixels = max(1, frames) * max(1, width) * max(1, height)
    # 经验值：GIF 单帧约 0.35 字节/像素（256 色调色板 + LZW）
    mb = pixels * 0.35 / 1024 / 1024
    seconds = frames * delay_ms / 1000.0
    return (f"预计 {frames} 帧 · {width}×{height} · 动画约 {seconds:.0f} 秒 · "
            f"文件约 {mb:.1f} MB")


# ==============================================================================
# MP4 / H.264 视频（需要外部的 ffmpeg）
# ==============================================================================

def _ffmpeg_candidates() -> List[str]:
    """列出所有可能的 ffmpeg 位置（按优先级）。

    为什么需要"向上逐层搜索"：打包成单文件 EXE 后程序位于 `stellar_nexus/dist/`，
    而便携版 ffmpeg 放在仓库根的 `.tools/`（`D:\\deepseek\\.tools\\ffmpeg.exe`），
    距 EXE 有 **三层**。早期候选表最远只查一层（`app_dir()/../.tools`），
    于是"从 EXE 运行时明明装了便携版却报未检测到"——用户实测遇到过这个。

    这里从 app_dir() 与 _MEIPASS 各自向上找 5 层，覆盖
    `dist/`、`stellar_nexus/`、仓库根等各种布局。
    另外 _MEIPASS（PyInstaller 解压目录）在单文件模式下是临时目录，
    向上找没有意义，所以只对它查同级的 `_internal/.tools` 与 `.tools`。
    """
    import shutil
    out: List[str] = []
    found = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if found:
        out.append(found)

    def add(path: str) -> None:
        if path:
            out.append(os.path.normpath(path))

    # 1) 程序自己所在目录（EXE 旁边放 ffmpeg.exe —— 最直白的用法）
    base = app_dir()
    add(os.path.join(base, "ffmpeg.exe"))
    add(os.path.join(base, "ffmpeg"))
    # 2) 从程序目录逐层向上找 .tools/ 与 bin/
    cur = base
    for _ in range(5):
        add(os.path.join(cur, ".tools", "ffmpeg.exe"))
        add(os.path.join(cur, ".tools", "ffmpeg"))
        add(os.path.join(cur, "tools", "ffmpeg.exe"))
        add(os.path.join(cur, "ffmpeg.exe"))
        cur = os.path.dirname(cur)
        if not cur or cur == os.path.dirname(cur):
            break
    # 3) PyInstaller 单文件模式：sys.executable 同级
    add(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "ffmpeg.exe"))
    # 4) 打包内部资源目录（若把 ffmpeg 一起打进了包）
    mei = getattr(sys, "_MEIPASS", "")
    add(os.path.join(mei, "ffmpeg.exe"))
    add(os.path.join(mei, ".tools", "ffmpeg.exe"))
    # 5) 源码运行时的推导（__file__ 上溯两级 = 项目根）
    add(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "..", ".tools", "ffmpeg.exe"))
    # 6) 常见系统安装位置
    add(r"C:\ffmpeg\bin\ffmpeg.exe")
    add(os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Microsoft", "WinGet", "Links", "ffmpeg.exe"))
    add("/usr/bin/ffmpeg")
    add("/usr/local/bin/ffmpeg")
    add("/opt/homebrew/bin/ffmpeg")
    return out


def find_ffmpeg() -> str:
    """查找可用的 ffmpeg：PATH → 程序目录 → 逐层向上 → 打包内 → 常见安装位置。"""
    for path in _ffmpeg_candidates():
        if path and os.path.isfile(path):
            return path
    return ""


def find_ffmpeg_nearby() -> List[str]:
    """列出"程序附近所有能找到的 ffmpeg"（供设置界面提示用户把文件放哪）。

    返回值是去重后的路径列表；界面用它给出"已找到 N 个，当前使用第 1 个"，
    比只报一个路径更容易排查。
    """
    seen: List[str] = []
    for path in _ffmpeg_candidates():
        if path and os.path.isfile(path) and path not in seen:
            seen.append(path)
    return seen


def ffmpeg_info() -> Tuple[bool, str]:
    """返回 (是否可用, 版本信息或安装提示)。"""
    exe = find_ffmpeg()
    if exe:
        try:
            import subprocess
            out = subprocess.run([exe, "-version"], capture_output=True, timeout=8,
                                 text=True, encoding="utf-8", errors="replace")
            first = (out.stdout or "").splitlines()[0] if out.stdout else ""
            return (True, f"{first}\n路径：{exe}")
        except Exception as exc:
            return (False, f"检测到 ffmpeg 但无法执行：{exc}\n路径：{exe}")

    # 未找到：给出**可操作**的提示——直接列出"把 ffmpeg.exe 放这几个位置就行"。
    # 早期只写了"winget install"，而打包成 EXE 后最方便的其实是把便携版
    # 放到 EXE 旁边；用户实测就卡在"明明装了却报未检测到"。
    here = app_dir()
    up1 = os.path.dirname(here)
    tips = [
        f"· 把 ffmpeg.exe 放到程序同目录（最省事）：{here}",
        f"· 或放到上一层的 .tools 目录：{os.path.join(up1, '.tools')}",
        "· Windows 也可以：winget install Gyan.FFmpeg（之后需重启本程序）",
        "· macOS：brew install ffmpeg",
    ]
    return (False, "未检测到 ffmpeg。MP4 导出需要它，任选一种方式：\n"
                   + "\n".join(tips)
                   + "\n在装好之前，可以先用「整夜延时 GIF」导出（无需任何外部依赖）。")


def estimate_video(frames: int, width: int, height: int, fps: float = 25.0) -> str:
    """估算 MP4 规模。H.264 经验码率约 0.06 bit/像素/帧（星图内容压缩率很高）。"""
    seconds = frames / max(1e-6, fps)
    mb = max(1, frames) * width * height * 0.06 / 8 / 1024 / 1024
    return (f"预计 {frames} 帧 · {width}×{height} · {fps:.0f} fps · "
            f"视频约 {seconds:.0f} 秒 · 文件约 {mb:.1f} MB")


def export_night_video_blocking(view, path: str, when: Optional[datetime] = None,
                       start_hour: float = 19.0, end_hour: float = 5.0,
                       step_minutes: float = 10.0, width: int = 1280,
                       fps: float = 25.0, overlay: str = "", crf: int = 20,
                       progress: Optional[Callable[[int, int], None]] = None,
                       cancel: Optional[Callable[[], bool]] = None
                       ) -> Tuple[bool, str]:
    """导出整夜星空旋转的 MP4（**阻塞实现，只能在主线程调用**）。

    ★ 与 export_night_gif_blocking 同一个道理：内部会 set_time / recalculate /
      grab()，都是 GUI 操作，放到工作线程里会**直接闪退**。
      界面请用 `NightExportController`。

    参数：
        crf : H.264 质量（0 最好 ~ 51 最差；星图建议 18~24）
    返回：(是否成功, 说明文字)
    """
    exe = find_ffmpeg()
    if not exe:
        return (False, ffmpeg_info()[1])

    import subprocess
    saved_observer = getattr(view, "observer", None)
    saved_when = saved_observer.when if saved_observer is not None else None
    base = when or saved_when or datetime.now()
    frames = night_frame_times(base, start_hour, end_hour, step_minutes)
    if not frames:
        return (False, "没有生成任何帧，请检查时间范围")
    total = len(frames)
    try:
        ensure_dir(os.path.dirname(os.path.abspath(path)))
    except Exception:
        pass

    proc = None
    out_w = out_h = 0
    try:
        for i, t in enumerate(frames):
            if cancel is not None and cancel():
                return (False, f"已取消（已渲染 {i} 帧）")
            if saved_observer is not None:
                view.set_time(t)
            if hasattr(view, "recalculate"):
                try:
                    view.recalculate(force=True)
                except Exception:
                    pass
            img = render_widget(view, None)
            if out_w == 0:
                ratio = img.height() / max(1, img.width())
                out_w = max(160, int(width))
                out_h = max(90, int(round(out_w * ratio)))
                out_w -= out_w % 2            # H.264 要求偶数尺寸
                out_h -= out_h % 2
                proc = subprocess.Popen(
                    [exe, "-y", "-hide_banner", "-loglevel", "error",
                     "-f", "rawvideo", "-pix_fmt", "rgb24",
                     "-s", f"{out_w}x{out_h}", "-r", f"{max(1.0, fps):g}", "-i", "-",
                     "-c:v", "libx264", "-preset", "medium", "-crf", str(int(crf)),
                     "-pix_fmt", "yuv420p", "-movflags", "+faststart", path],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE)
            img = img.scaled(out_w, out_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            if overlay:
                img = _draw_overlay(img, f"{overlay}　{t.strftime('%H:%M')}", "STELLAR NEXUS")
            rgb = img.convertToFormat(QImage.Format_RGB888)
            ptr = rgb.constBits()
            data = bytes(ptr) if ptr is not None else b""
            # QImage 每行可能有 4 字节对齐填充，需要按行精确取
            stride = rgb.bytesPerLine()
            row_bytes = out_w * 3
            if stride != row_bytes:
                data = b"".join(data[y * stride: y * stride + row_bytes]
                                for y in range(out_h))
            if proc is not None and proc.stdin is not None:
                proc.stdin.write(data)
            if progress is not None:
                progress(i + 1, total)

        if proc is not None:
            if proc.stdin is not None:
                proc.stdin.close()
            err = b""
            try:
                err = proc.stderr.read() if proc.stderr is not None else b""
            except Exception:
                pass
            code = proc.wait(timeout=180)
            if code != 0:
                msg = err.decode("utf-8", "replace").strip()[:300] if err else "未知错误"
                return (False, f"ffmpeg 退出码 {code}：{msg}")
        size_mb = os.path.getsize(path) / 1024 / 1024 if os.path.isfile(path) else 0.0
        note = (f"已导出 MP4：{out_w}×{out_h}　{total} 帧　{fps:.0f} fps　"
                f"{total / max(1.0, fps):.1f} 秒　{size_mb:.2f} MB")
        LOG.info(f"整夜延时 MP4 导出完成：{path}　{note}")
        return (True, note)
    except Exception as exc:
        LOG.error("导出 MP4 失败", exc)
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
        return (False, f"导出失败：{exc}")
    finally:
        if saved_observer is not None and saved_when is not None:
            try:
                view.set_time(saved_when)
                if hasattr(view, "recalculate"):
                    view.recalculate(force=True)
            except Exception:
                pass


# ==============================================================================
# 分线程导出：**渲染在主线程、编码在后台线程**
# ==============================================================================

@dataclass
class EncodeJob:
    """一次导出任务的参数（渲染与编码两边共用）。"""

    path: str
    frames: List[datetime]
    width: int
    overlay: str = ""
    video: bool = False
    fps: float = 25.0
    crf: int = 20
    delay_ms: int = 120
    max_colors: int = 256
    fallback_png_dir: str = ""


class _ExportAbort(Exception):
    """内部控制流：用户取消 / 渲染线程缺帧。"""


class NightExportWorker(QThread):
    """后台线程：只做**编码**（ffmpeg 管道 / Pillow 写 GIF / 落盘 PNG 序列）。

    ★ 为什么必须把渲染拆出去：
      之前整条导出都在这个线程里跑，包括 `view.set_time()`、
      `view.recalculate()` 和 `render_widget(view)`（内部是 `widget.grab()`）。
      而这三个都是 **GUI 操作，只能在主线程执行**；从工作线程碰 QWidget 是 Qt 里
      明确禁止的行为，实测后果就是"点导出 MP4 直接闪退"（进程级崩溃，
      Python 的 try/except 拦不住）。现在渲染由 `NightExportController`
      在主线程用 QTimer 逐帧做，本线程只从队列里取已经渲染好的 QImage。
    """

    progressed = Signal(int, int)

    def __init__(self, job: EncodeJob, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.job = job
        self._queue: "deque" = deque()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._cancel = False
        self._done = False
        self.result: Tuple[bool, str] = (False, "未执行")

    # ---- 主线程调用 ----
    def push_frame(self, img: QImage) -> None:
        with self._cv:
            self._queue.append(img)
            self._cv.notify()

    def finish(self) -> None:
        with self._cv:
            self._done = True
            self._cv.notify_all()

    def cancel(self) -> None:
        with self._cv:
            self._cancel = True
            self._done = True
            self._cv.notify_all()

    # ---- 后台线程 ----
    def _next_frame(self, timeout: float = 0.1) -> Optional[QImage]:
        """取下一帧；返回 None 表示"没有更多帧了，可以收尾"。"""
        deadline = time.monotonic() + timeout
        with self._cv:
            while not self._queue:
                if self._cancel:
                    raise _ExportAbort("已取消")
                if self._done:
                    return None
                remain = deadline - time.monotonic()
                if remain <= 0:
                    return None          # 暂时没帧，但还没结束——调用方自己判断
                self._cv.wait(remain)
            return self._queue.popleft()

    def run(self) -> None:  # noqa: D102
        try:
            self.result = self._run_gif() if not self.job.video else self._run_video()
        except _ExportAbort as exc:
            self.result = (False, str(exc))
        except Exception as exc:          # pragma: no cover - 防御性
            LOG.error("导出失败", exc)
            self.result = (False, f"导出失败：{exc}")

    # ---- 编码实现 ----
    def _scale(self, img: QImage, out_w: int, out_h: int) -> QImage:
        img = img.scaled(out_w, out_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if self.job.overlay:
            stamp = self.job.frames[self._idx].strftime("%H:%M") if self._idx < len(self.job.frames) else ""
            img = _draw_overlay(img, f"{self.job.overlay}　{stamp}", "STELLAR NEXUS")
        return img

    def _run_video(self) -> Tuple[bool, str]:
        import subprocess
        exe = find_ffmpeg()
        if not exe:
            return (False, ffmpeg_info()[1])
        job = self.job
        total = len(job.frames)
        try:
            ensure_dir(os.path.dirname(os.path.abspath(job.path)))
        except Exception:
            pass
        proc = None
        out_w = out_h = 0
        sent = 0
        self._idx = 0
        try:
            while True:
                img = self._next_frame()
                if img is None:
                    if self._done or self._cancel:
                        break
                    continue
                if self._cancel:
                    raise _ExportAbort(f"已取消（已渲染 {sent} 帧）")
                if out_w == 0:
                    ratio = img.height() / max(1, img.width())
                    out_w = max(160, int(job.width))
                    out_h = max(90, int(round(out_w * ratio)))
                    out_w -= out_w % 2
                    out_h -= out_h % 2
                    proc = subprocess.Popen(
                        [exe, "-y", "-hide_banner", "-loglevel", "error",
                         "-f", "rawvideo", "-pix_fmt", "rgb24",
                         "-s", f"{out_w}x{out_h}", "-r", f"{max(1.0, job.fps):g}", "-i", "-",
                         "-c:v", "libx264", "-preset", "medium", "-crf", str(int(job.crf)),
                         "-pix_fmt", "yuv420p", "-movflags", "+faststart", job.path],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE)
                img = self._scale(img, out_w, out_h)
                self._idx += 1
                rgb = img.convertToFormat(QImage.Format_RGB888)
                ptr = rgb.constBits()
                data = bytes(ptr) if ptr is not None else b""
                stride = rgb.bytesPerLine()
                row_bytes = out_w * 3
                if stride != row_bytes:
                    data = b"".join(data[y * stride: y * stride + row_bytes]
                                    for y in range(out_h))
                if proc is not None and proc.stdin is not None:
                    proc.stdin.write(data)
                sent += 1
                self.progressed.emit(sent, total)
            if proc is not None:
                if proc.stdin is not None:
                    proc.stdin.close()
                err = b""
                try:
                    err = proc.stderr.read() if proc.stderr is not None else b""
                except Exception:
                    pass
                code = proc.wait(timeout=180)
                if code != 0:
                    msg = err.decode("utf-8", "replace").strip()[:300] if err else "未知错误"
                    return (False, f"ffmpeg 退出码 {code}：{msg}")
            if sent == 0:
                return (False, "没有收到任何帧")
            size_mb = os.path.getsize(job.path) / 1024 / 1024 if os.path.isfile(job.path) else 0.0
            note = (f"已导出 MP4：{out_w}×{out_h}　{sent} 帧　{job.fps:.0f} fps　"
                    f"{sent / max(1.0, job.fps):.1f} 秒　{size_mb:.2f} MB")
            LOG.info(f"整夜延时 MP4 导出完成：{job.path}　{note}")
            return (True, note)
        finally:
            if self._cancel and proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _run_gif(self) -> Tuple[bool, str]:
        job = self.job
        total = len(job.frames)
        try:
            ensure_dir(os.path.dirname(os.path.abspath(job.path)))
        except Exception:
            pass
        try:
            from PIL import Image                      # noqa: N813
            has_pillow = True
        except Exception:
            has_pillow = False
        images: List[object] = []
        out_w = out_h = 0
        self._idx = 0
        while True:
            img = self._next_frame()
            if img is None:
                if self._done or self._cancel:
                    break
                continue
            if self._cancel:
                raise _ExportAbort(f"已取消（已渲染 {len(images)} 帧）")
            if out_w == 0:
                ratio = img.height() / max(1, img.width())
                out_w = max(160, int(job.width))
                out_h = max(90, int(round(out_w * ratio)))
                out_w -= out_w % 2
                out_h -= out_h % 2
            img = self._scale(img, out_w, out_h)
            self._idx += 1
            images.append(_qimage_to_pil(img) if has_pillow else img)
            self.progressed.emit(len(images), total)
        if not images:
            return (False, "没有收到任何帧")
        if has_pillow:
            first = images[0].convert("P", palette=Image.ADAPTIVE, colors=job.max_colors)
            rest = [im.convert("P", palette=Image.ADAPTIVE, colors=job.max_colors)
                    for im in images[1:]]
            duration = max(20, int(job.delay_ms))
            first.save(job.path, save_all=True, append_images=rest, duration=duration,
                       loop=0, optimize=True, disposal=2)
            size_mb = os.path.getsize(job.path) / 1024 / 1024 if os.path.isfile(job.path) else 0.0
            note = (f"已导出 {len(images)} 帧 GIF，{out_w}×{out_h}，每帧 {duration} ms"
                    f"（约 {len(images) * duration / 1000:.1f} 秒动画），"
                    f"{job.max_colors} 色，{size_mb:.2f} MB")
            LOG.info(f"整夜延时 GIF 导出完成：{job.path}　{note}")
            return (True, note)
        folder = job.fallback_png_dir or os.path.join(
            os.path.dirname(os.path.abspath(job.path)),
            os.path.splitext(os.path.basename(job.path))[0] + "_frames")
        ensure_dir(folder)
        for i, im in enumerate(images):
            im.save(os.path.join(folder, f"frame_{i + 1:04d}.png"), "PNG")
        return (True, f"未找到 Pillow，已降级导出 {len(images)} 张 PNG 帧到：{folder}")


class NightExportController(QObject):
    """**主线程**侧的导出控制器：逐帧渲染 → 交给编码线程。

    用法：
        ctl = NightExportController(view, observer)
        ctl.finished_ok.connect(on_done)
        ctl.start(EncodeJob(...))
    """

    progressed = Signal(int, int)
    finished_ok = Signal(bool, str)

    def __init__(self, view, observer=None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.view = view
        self.observer = observer if observer is not None else getattr(view, "observer", None)
        self._job: Optional[EncodeJob] = None
        self._worker: Optional[NightExportWorker] = None
        self._timer: Optional[QTimer] = None
        self._saved_when = None
        self._i = 0

    # ---- 对外 ----
    def start(self, job: EncodeJob) -> None:
        self._job = job
        saved = self.observer
        self._saved_when = saved.when if saved is not None else None
        self._i = 0
        self._worker = NightExportWorker(job, self)
        self._worker.progressed.connect(self.progressed.emit)
        self._worker.start()
        timer = QTimer(self)
        timer.setInterval(0)              # 每转一圈渲染一帧，同时保持界面可响应
        timer.timeout.connect(self._render_one)
        self._timer = timer
        timer.start()

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # ---- 主线程逐帧渲染 ----
    def _render_one(self) -> None:
        job, worker = self._job, self._worker
        if job is None or worker is None:
            self._stop()
            return
        if self._i >= len(job.frames):
            self._stop()
            worker.finish()
            self._await()
            return
        t = job.frames[self._i]
        try:
            if self.observer is not None:
                self.view.set_time(t)
            if hasattr(self.view, "recalculate"):
                self.view.recalculate(force=True)
            img = render_widget(self.view, None)
        except Exception as exc:
            LOG.error("导出渲染帧失败", exc)
            self._stop()
            worker.cancel()
            self.finished_ok.emit(False, f"渲染帧失败：{exc}")
            return
        worker.push_frame(img)
        self._i += 1
        self.progressed.emit(self._i, len(job.frames))

    def _await(self) -> None:
        worker = self._worker

        def done() -> None:
            self._restore()
            ok, note = worker.result if worker is not None else (False, "无结果")
            self.finished_ok.emit(ok, note)

        if worker is not None:
            worker.finished.connect(done)
        else:
            done()

    def _restore(self) -> None:
        if self.observer is not None and self._saved_when is not None:
            try:
                self.view.set_time(self._saved_when)
                if hasattr(self.view, "recalculate"):
                    self.view.recalculate(force=True)
            except Exception:
                pass


__all__ = ["EXPORT_SIZES", "render_widget", "export_png", "export_night_gif",
           "night_frame_times", "estimate_gif",
           "find_ffmpeg", "ffmpeg_info", "estimate_video", "export_night_video"]
