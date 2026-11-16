# -*- coding: utf-8 -*-
"""开发期工具：给导出对话框加「MP4 视频」标签页。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "stellar_nexus", "ui", "export_panel.py")
s = open(PATH, encoding="utf-8").read()
applied = []


def rep(old: str, new: str, tag: str) -> None:
    global s
    if old in s:
        s = s.replace(old, new, 1)
        applied.append(tag)
    else:
        print("MISS:", tag, "|", old.splitlines()[0][:60])


# 1) 标签页注册
rep('        self.tabs.addTab(self._build_gif_tab(), "整夜延时 GIF")',
    '        self.tabs.addTab(self._build_gif_tab(), "整夜延时 GIF")\n'
    '        self.tabs.addTab(self._build_video_tab(), "MP4 视频（需 ffmpeg）")',
    "注册标签页")

# 2) 视频页 + 导出逻辑
rep("    def _refresh_estimate(self) -> None:",
    '''    def _build_video_tab(self) -> QWidget:
        """MP4 导出页：需要系统里有 ffmpeg。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        ok, info = EM.ffmpeg_info()
        self.lbl_ffmpeg = QLabel(("✅ " if ok else "⚠ ") + info.replace("\\n", "\\n"))
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
            "说明：MP4 用 H.264 编码，体积远小于 GIF、清晰度更高，适合发视频号 / B 站。\\n"
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
        self._worker = GifWorker(
            self.view, path, when, float(self.v_spin_start.value()),
            float(self.v_spin_end.value()), float(self.v_combo_step.currentData() or 5.0),
            int(self.v_combo_width.currentData() or 1280),
            1000.0 / max(1.0, float(self.v_combo_fps.currentData() or 25.0)),
            overlay, 256, self,
            video=True, fps=float(self.v_combo_fps.currentData() or 25.0),
            crf=int(self.v_spin_crf.value()))
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.cancelled.connect(self._on_cancelled)
        self.btn_video.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setFormat("渲染帧并编码中… %p%")
        self._worker.start()

    def _refresh_estimate(self) -> None:''', "视频页")

# 3) GifWorker 支持视频模式
rep('''    def __init__(self, view, path: str, when: datetime, start_hour: float, end_hour: float,
                 step: float, width: int, delay: int, overlay: str, colors: int,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.view = view
        self.path = path
        self.when = when
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.step = step
        self.width = width
        self.delay = delay
        self.overlay = overlay
        self.colors = colors
        self._cancel = False''',
    '''    def __init__(self, view, path: str, when: datetime, start_hour: float, end_hour: float,
                 step: float, width: int, delay: int, overlay: str, colors: int,
                 parent: Optional[QWidget] = None, video: bool = False,
                 fps: float = 25.0, crf: int = 20) -> None:
        super().__init__(parent)
        self.view = view
        self.path = path
        self.when = when
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.step = step
        self.width = width
        self.delay = delay
        self.overlay = overlay
        self.colors = colors
        self.video = bool(video)
        self.fps = float(fps)
        self.crf = int(crf)
        self._cancel = False''', "GifWorker 构造")

rep('''    def run(self) -> None:
        ok, note = EM.export_night_gif(
            self.view, self.path, self.when, self.start_hour, self.end_hour,
            self.step, self.width, self.delay, self.overlay,
            progress=lambda d, t: self.progressed.emit(int(d), int(t)),
            cancel=lambda: self._cancel, max_colors=self.colors)
        if self._cancel:
            self.cancelled.emit()
        self.finished_ok.emit(ok, note)''',
    '''    def run(self) -> None:
        if self.video:
            ok, note = EM.export_night_video(
                self.view, self.path, self.when, self.start_hour, self.end_hour,
                self.step, self.width, self.fps, self.overlay, self.crf,
                progress=lambda d, t: self.progressed.emit(int(d), int(t)),
                cancel=lambda: self._cancel)
        else:
            ok, note = EM.export_night_gif(
                self.view, self.path, self.when, self.start_hour, self.end_hour,
                self.step, self.width, self.delay, self.overlay,
                progress=lambda d, t: self.progressed.emit(int(d), int(t)),
                cancel=lambda: self._cancel, max_colors=self.colors)
        if self._cancel:
            self.cancelled.emit()
        self.finished_ok.emit(ok, note)''', "GifWorker run")

# 4) 两个按钮都恢复
rep('''    @Slot(bool, str)
    def _on_finished(self, ok: bool, note: str) -> None:
        self.btn_gif.setEnabled(True)''',
    '''    @Slot(bool, str)
    def _on_finished(self, ok: bool, note: str) -> None:
        self.btn_gif.setEnabled(True)
        if hasattr(self, "btn_video"):
            self.btn_video.setEnabled(True)''', "finished 恢复按钮")
rep('''    @Slot()
    def _on_cancelled(self) -> None:
        self.btn_gif.setEnabled(True)''',
    '''    @Slot()
    def _on_cancelled(self) -> None:
        self.btn_gif.setEnabled(True)
        if hasattr(self, "btn_video"):
            self.btn_video.setEnabled(True)''', "cancelled 恢复按钮")

open(PATH, "w", encoding="utf-8", newline="\n").write(s)
print(f"接入 {len(applied)} 处：{applied}")
