# -*- coding: utf-8 -*-
"""
ui/export_panel.py —— 星空导出对话框（PNG 图片 / 整夜延时 GIF）
================================================================================
    · 单张 PNG：选择分辨率（720p / 1080p / 2K / 4K），可叠加时间地点与签名
    · 整夜延时 GIF：指定起止时刻与步长，导出星空旋转动画，附体积与时长估算
    · 导出在后台线程执行，进度条实时刷新，可取消
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from 核心 import export_media as EM
from 核心.utils import LOG, format_datetime, timestamp_tag

from .widgets import GlowButton, SectionTitle, SeparatorLine


class GifWorker(QObject):
    """导出任务的**主线程侧外壳**：负责把渲染控制器与编码线程接起来。

    ★ 这里曾经是本项目最严重的一个 bug：整个导出（含**渲染**）都在一个
      QThread 里跑，而渲染要调 `view.set_time()` / `view.recalculate()` /
      `render_widget(view)`（= `widget.grab()`）——全是 GUI 操作。
      从非 GUI 线程碰 QWidget 是 Qt 明令禁止的，实测后果是**进程级闪退**，
      Python 的 try/except 完全拦不住（用户报的"生成 MP4 程序会崩溃闪退"）。
      现在职责拆成两半：
        · 渲染：主线程，由 `EM.NightExportController` 用 0ms QTimer 逐帧做；
        · 编码：后台 QThread（`EM.NightExportWorker`），只吃 QImage、不碰 QWidget。
      本类本身**不创建任何线程**，它只是对话框侧的一个信号转发器，
      并且保证 `_worker` 在编码线程真正结束之前不被析构。
    """

    progressed = Signal(int, int)
    finished_ok = Signal(bool, str)
    cancelled = Signal()

    def __init__(self, job, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._job = job
        self._ctl: Optional[EM.NightExportController] = None
        self._result: Tuple[bool, str] = (False, "未执行")

    def start(self, view, observer) -> None:
        """在主线程里创建控制器并启动（必须在 GUI 线程调用）。"""
        self._ctl = EM.NightExportController(view, observer, self)
        self._ctl.progressed.connect(self.progressed.emit)
        self._ctl.finished_ok.connect(self._on_done)
        self._ctl.start(self._job)

    def _on_done(self, ok: bool, note: str) -> None:
        self._result = (ok, note)
        self.finished_ok.emit(ok, note)

    def isRunning(self) -> bool:
        """还在导出吗？（对话框用它决定是否重复启动）"""
        ctl = self._ctl
        if ctl is None:
            return False
        worker = getattr(ctl, "_worker", None)
        return bool(worker is not None and worker.isRunning())

    def cancel(self) -> None:
        if self._ctl is not None:
            self._ctl.cancel()

    def result(self) -> Tuple[bool, str]:
        return self._result


class ExportDialog(QDialog):
    """导出星空图片与延时动画。"""

    def __init__(self, view, observer, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出星空图片 / 延时动画")
        self.resize(720, 560)
        self.view = view
        self.observer = observer
        self._worker: Optional[GifWorker] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        head = QLabel("导出星空")
        head.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        head.setStyleSheet("color:#9ff6ff;background:transparent;")
        root.addWidget(head)
        root.addWidget(SeparatorLine())

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid rgba(0,170,210,140);border-radius:4px;"
            "background:rgba(4,10,18,200);}"
            "QTabBar::tab{background:rgba(8,22,34,230);color:#9fc8da;padding:5px 12px;"
            "border:1px solid rgba(0,150,190,120);border-bottom:none;"
            "border-top-left-radius:4px;border-top-right-radius:4px;}"
            "QTabBar::tab:selected{background:rgba(0,120,170,200);color:#ffffff;}")
        self.tabs.addTab(self._build_png_tab(), "单张 PNG")
        self.tabs.addTab(self._build_gif_tab(), "整夜延时 GIF")
        self.tabs.addTab(self._build_video_tab(), "MP4 视频（需 ffmpeg）")
        root.addWidget(self.tabs, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMaximumHeight(16)
        self.progress.setFormat("就绪")
        root.addWidget(self.progress)
        self.lbl_state = QLabel("")
        self.lbl_state.setWordWrap(True)
        self.lbl_state.setStyleSheet("color:#a9c9da;background:transparent;")
        self.lbl_state.setFont(QFont("Microsoft YaHei UI", 9))
        root.addWidget(self.lbl_state)

        bottom = QHBoxLayout()
        self.btn_cancel = GlowButton("取消导出", "red", compact=True)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        bottom.addWidget(self.btn_cancel)
        bottom.addStretch(1)
        btn_close = GlowButton("关闭", "cyan", compact=True)
        btn_close.clicked.connect(self._close)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # ---------------------------------------------------------------- PNG 页

    def _build_png_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.addWidget(QLabel("分辨率"))
        self.combo_size = QComboBox()
        for label, _size in EM.EXPORT_SIZES:
            self.combo_size.addItem(label, _size)
        self.combo_size.addItem("按当前窗口大小（不缩放）", None)
        self.combo_size.setCurrentIndex(1)
        layout.addWidget(self.combo_size)

        self.chk_overlay = QCheckBox("叠加时间与地点文字")
        self.chk_overlay.setChecked(True)
        layout.addWidget(self.chk_overlay)
        self.chk_sign = QCheckBox("叠加程序签名（右下角）")
        self.chk_sign.setChecked(True)
        layout.addWidget(self.chk_sign)

        layout.addWidget(QLabel("叠加文字（留空则自动生成时间/地点/视场）"))
        self.edit_overlay = QLineEdit()
        self.edit_overlay.setPlaceholderText("例如：北京　2026-09-21 22:00　猎户座方向")
        layout.addWidget(self.edit_overlay)
        layout.addStretch(1)

        btn = GlowButton("导出 PNG 图片…", "violet")
        btn.clicked.connect(self._export_png)
        layout.addWidget(btn)
        return page

    def _export_png(self) -> None:
        size = self.combo_size.currentData()
        default = os.path.join(os.path.expanduser("~"),
                               f"star_chart_{timestamp_tag()}.png")
        path, _ = QFileDialog.getSaveFileName(self, "导出星空图片", default,
                                              "PNG 图片 (*.png)")
        if not path:
            return
        text = self.edit_overlay.text().strip()
        if not text and self.chk_overlay.isChecked():
            obs = self.observer
            try:
                fov = getattr(self.view, "fov", 0.0)
                text = (f"{obs.location_name}　{format_datetime(obs.when)}　"
                        f"视场 {fov:.0f}°　高度 {getattr(self.view, 'center_alt', 0):+.0f}°")
            except Exception:
                text = format_datetime(obs.when) if obs else ""
        footer = "STELLAR NEXUS 星图终端" if self.chk_sign.isChecked() else ""
        self.progress.setFormat("导出中…")
        self.lbl_state.setText("正在渲染图片，请稍候…")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        ok = EM.export_png(self.view, path, size, text, footer)
        if ok:
            from PySide6.QtGui import QImage
            img = QImage(path)
            mb = os.path.getsize(path) / 1024 / 1024
            self.progress.setFormat("完成")
            self.lbl_state.setText(f"已导出：{path}\n尺寸 {img.width()}×{img.height()}，"
                                   f"{mb:.2f} MB")
            QMessageBox.information(self, "导出成功",
                                    f"图片已保存到：\n{path}\n\n"
                                    f"{img.width()}×{img.height()}，{mb:.2f} MB")
        else:
            self.progress.setFormat("失败")
            self.lbl_state.setText("导出失败，请查看运行日志")
            QMessageBox.warning(self, "导出失败", "无法写入图片，请检查路径与权限。")

    # ---------------------------------------------------------------- GIF 页

    def _build_gif_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("起始时刻"))
        self.spin_start = QSpinBox()
        self.spin_start.setRange(0, 23)
        self.spin_start.setValue(19)
        self.spin_start.setSuffix(" 时")
        row1.addWidget(self.spin_start)
        row1.addWidget(QLabel("结束时刻"))
        self.spin_end = QSpinBox()
        self.spin_end.setRange(0, 23)
        self.spin_end.setValue(5)
        self.spin_end.setSuffix(" 时")
        row1.addWidget(self.spin_end)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("帧间隔"))
        self.combo_step = QComboBox()
        for label, v in (("5 分钟", 5.0), ("10 分钟", 10.0), ("20 分钟", 20.0),
                         ("30 分钟", 30.0), ("60 分钟", 60.0)):
            self.combo_step.addItem(label, v)
        self.combo_step.setCurrentIndex(1)
        self.combo_step.currentIndexChanged.connect(lambda _i: self._refresh_estimate())
        row2.addWidget(self.combo_step)
        row2.addWidget(QLabel("输出宽度"))
        self.combo_width = QComboBox()
        for w in (480, 640, 800, 1024):
            self.combo_width.addItem(f"{w} px", w)
        self.combo_width.setCurrentIndex(1)
        self.combo_width.currentIndexChanged.connect(lambda _i: self._refresh_estimate())
        row2.addWidget(self.combo_width)
        row2.addStretch(1)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("每帧延迟"))
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(20, 1000)
        self.spin_delay.setValue(120)
        self.spin_delay.setSuffix(" ms")
        self.spin_delay.valueChanged.connect(lambda _v: self._refresh_estimate())
        row3.addWidget(self.spin_delay)
        row3.addWidget(QLabel("调色板"))
        self.combo_colors = QComboBox()
        for c in (64, 128, 256):
            self.combo_colors.addItem(f"{c} 色", c)
        self.combo_colors.setCurrentIndex(2)
        row3.addWidget(self.combo_colors)
        row3.addStretch(1)
        layout.addLayout(row3)

        self.lbl_estimate = QLabel("")
        self.lbl_estimate.setWordWrap(True)
        self.lbl_estimate.setStyleSheet("color:#8fffc8;background:transparent;")
        self.lbl_estimate.setFont(QFont("Microsoft YaHei UI", 9))
        layout.addWidget(self.lbl_estimate)

        note = QLabel(
            "说明：每帧推进一次时间，导出后播放即为「星星东升西落」的整夜延时。\n"
            "调色板越小、宽度越小，文件越小；帧间隔越大、动画越短。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#a9c9da;background:transparent;")
        note.setFont(QFont("Microsoft YaHei UI", 9))
        layout.addWidget(note)
        layout.addStretch(1)

        self.btn_gif = GlowButton("导出整夜延时 GIF…", "violet")
        self.btn_gif.clicked.connect(self._export_gif)
        layout.addWidget(self.btn_gif)
        self._refresh_estimate()
        return page

    def _build_video_tab(self) -> QWidget:
        """MP4 导出页：需要系统里有 ffmpeg。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        ok, info = EM.ffmpeg_info()
        self.lbl_ffmpeg = QLabel(("✅ " if ok else "⚠ ") + info.replace("\n", "\n"))
        self.lbl_ffmpeg.setWordWrap(True)
        self.lbl_ffmpeg.setStyleSheet(
            "color:%s;background:rgba(6,18,28,200);border:1px solid rgba(0,170,210,120);"
            "border-radius:3px;padding:6px;" % ("#8fffc8" if ok else "#ffb37a"))
        self.lbl_ffmpeg.setFont(QFont("Microsoft YaHei UI", 9))
        layout.addWidget(self.lbl_ffmpeg)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("起始时刻"))
        self.v_spin_start = QSpinBox()
        self.v_spin_start.setRange(0, 23)
        self.v_spin_start.setValue(19)
        self.v_spin_start.setSuffix(" 时")
        row1.addWidget(self.v_spin_start)
        row1.addWidget(QLabel("结束时刻"))
        self.v_spin_end = QSpinBox()
        self.v_spin_end.setRange(0, 23)
        self.v_spin_end.setValue(5)
        self.v_spin_end.setSuffix(" 时")
        row1.addWidget(self.v_spin_end)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("帧间隔"))
        self.v_combo_step = QComboBox()
        for label, v in (("2 分钟", 2.0), ("5 分钟", 5.0), ("10 分钟", 10.0),
                         ("20 分钟", 20.0), ("30 分钟", 30.0)):
            self.v_combo_step.addItem(label, v)
        self.v_combo_step.setCurrentIndex(1)
        row2.addWidget(self.v_combo_step)
        row2.addWidget(QLabel("输出宽度"))
        self.v_combo_width = QComboBox()
        for w in (854, 1280, 1920, 2560):
            self.v_combo_width.addItem(f"{w} px", w)
        self.v_combo_width.setCurrentIndex(1)
        row2.addWidget(self.v_combo_width)
        row2.addWidget(QLabel("帧率"))
        self.v_combo_fps = QComboBox()
        for f in (12, 15, 24, 25, 30, 60):
            self.v_combo_fps.addItem(f"{f} fps", float(f))
        self.v_combo_fps.setCurrentIndex(3)
        row2.addWidget(self.v_combo_fps)
        row2.addStretch(1)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("画质 CRF"))
        self.v_spin_crf = QSpinBox()
        self.v_spin_crf.setRange(0, 40)
        self.v_spin_crf.setValue(20)
        self.v_spin_crf.setToolTip("数值越小画质越好、文件越大；星图建议 18~24")
        row3.addWidget(self.v_spin_crf)
        for label, val in (("高画质", 16), ("均衡", 20), ("小体积", 26)):
            btn = GlowButton(label, "cyan", compact=True)
            btn.clicked.connect(lambda _c=False, v=val: self.v_spin_crf.setValue(v))
            row3.addWidget(btn)
        row3.addStretch(1)
        layout.addLayout(row3)

        self.lbl_video_estimate = QLabel("")
        self.lbl_video_estimate.setWordWrap(True)
        self.lbl_video_estimate.setStyleSheet("color:#8fffc8;background:transparent;")
        self.lbl_video_estimate.setFont(QFont("Microsoft YaHei UI", 9))
        layout.addWidget(self.lbl_video_estimate)
        for widget in (self.v_spin_start, self.v_spin_end, self.v_combo_step,
                       self.v_combo_width, self.v_combo_fps):
            try:
                widget.valueChanged.connect(lambda _v: self._refresh_video_estimate())
                widget.currentIndexChanged.connect(lambda _i: self._refresh_video_estimate())
            except AttributeError:
                pass

        note = QLabel(
            "说明：MP4 用 H.264 编码，体积远小于 GIF、清晰度更高，适合发视频号 / B 站。\n"
            "帧间隔 2~5 分钟 + 25 fps 会得到非常流畅的星轨旋转效果（帧数多、耗时也长）。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#a9c9da;background:transparent;")
        note.setFont(QFont("Microsoft YaHei UI", 9))
        layout.addWidget(note)
        layout.addStretch(1)

        self.btn_video = GlowButton("导出 MP4 视频…", "violet")
        self.btn_video.setEnabled(ok)
        self.btn_video.clicked.connect(self._export_video)
        layout.addWidget(self.btn_video)
        self._refresh_video_estimate()
        return page

    def _refresh_video_estimate(self) -> None:
        try:
            when = self.observer.when if self.observer else datetime.now()
            frames = EM.night_frame_times(when, float(self.v_spin_start.value()),
                                          float(self.v_spin_end.value()),
                                          float(self.v_combo_step.currentData() or 5.0))
            width = int(self.v_combo_width.currentData() or 1280)
            fps = float(self.v_combo_fps.currentData() or 25.0)
            self.lbl_video_estimate.setText(
                f"共 {len(frames)} 帧　" + EM.estimate_video(len(frames), width,
                                                             int(width * 9 / 16), fps))
        except Exception as exc:
            self.lbl_video_estimate.setText(f"估算失败：{exc}")

    def _export_video(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if not EM.find_ffmpeg():
            QMessageBox.information(self, "需要 ffmpeg", EM.ffmpeg_info()[1])
            return
        default = os.path.join(os.path.expanduser("~"),
                               f"night_sky_{timestamp_tag()}.mp4")
        path, _ = QFileDialog.getSaveFileName(self, "导出 MP4 视频", default,
                                              "MP4 视频 (*.mp4)")
        if not path:
            return
        obs = self.observer
        when = obs.when if obs else datetime.now()
        overlay = obs.location_name if obs else ""
        fps = float(self.v_combo_fps.currentData() or 25.0)
        job = EM.EncodeJob(
            path=path,
            frames=EM.night_frame_times(
                when, float(self.v_spin_start.value()), float(self.v_spin_end.value()),
                float(self.v_combo_step.currentData() or 5.0)),
            width=int(self.v_combo_width.currentData() or 1280),
            overlay=overlay, video=True, fps=fps,
            crf=int(self.v_spin_crf.value()))
        if not job.frames:
            QMessageBox.warning(self, "没有帧", "请检查起止时刻与步长。")
            return
        self._worker = GifWorker(job, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.cancelled.connect(self._on_cancelled)
        self.btn_video.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, len(job.frames))
        self.progress.setFormat("主线程渲染帧 → 后台编码… %p%")
        self._worker.start(self.view, self.observer)   # ★ 渲染仍在主线程

    def _refresh_estimate(self) -> None:
        try:
            when = self.observer.when if self.observer else datetime.now()
            frames = EM.night_frame_times(when, float(self.spin_start.value()),
                                          float(self.spin_end.value()),
                                          float(self.combo_step.currentData() or 10.0))
            width = int(self.combo_width.currentData() or 640)
            height = int(round(width * 9 / 16))
            self.lbl_estimate.setText(
                f"共 {len(frames)} 帧　" +
                EM.estimate_gif(len(frames), width, height,
                                int(self.spin_delay.value())))
        except Exception as exc:
            self.lbl_estimate.setText(f"估算失败：{exc}")

    def _export_gif(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        default = os.path.join(os.path.expanduser("~"),
                               f"night_sky_{timestamp_tag()}.gif")
        path, _ = QFileDialog.getSaveFileName(self, "导出整夜延时", default,
                                              "GIF 动画 (*.gif)")
        if not path:
            return
        obs = self.observer
        when = obs.when if obs else datetime.now()
        overlay = obs.location_name if obs else ""
        job = EM.EncodeJob(
            path=path,
            frames=EM.night_frame_times(
                when, float(self.spin_start.value()), float(self.spin_end.value()),
                float(self.combo_step.currentData() or 10.0)),
            width=int(self.combo_width.currentData() or 640),
            overlay=overlay, video=False,
            delay_ms=int(self.spin_delay.value()),
            max_colors=int(self.combo_colors.currentData() or 256))
        if not job.frames:
            QMessageBox.warning(self, "没有帧", "请检查起止时刻与步长。")
            return
        self._worker = GifWorker(job, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.cancelled.connect(self._on_cancelled)
        self.btn_gif.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, len(job.frames))
        self.progress.setFormat("主线程渲染帧 → 后台编码… %p%")
        self._worker.start(self.view, self.observer)   # ★ 渲染仍在主线程

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.lbl_state.setText(f"已渲染 {done}/{total} 帧…")

    @Slot(bool, str)
    def _on_finished(self, ok: bool, note: str) -> None:
        self.btn_gif.setEnabled(True)
        if hasattr(self, "btn_video"):
            self.btn_video.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setFormat("完成" if ok else "失败")
        self.lbl_state.setText(note)
        if ok:
            QMessageBox.information(self, "导出成功", note)
        else:
            QMessageBox.warning(self, "导出失败", note)

    @Slot()
    def _on_cancelled(self) -> None:
        self.btn_gif.setEnabled(True)
        if hasattr(self, "btn_video"):
            self.btn_video.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setFormat("已取消")
        self.lbl_state.setText("导出已取消")

    def _cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.lbl_state.setText("正在取消…")

    def _close(self) -> None:
        self._cancel()
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._cancel()
        super().closeEvent(event)


__all__ = ["ExportDialog", "GifWorker"]
