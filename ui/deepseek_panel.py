# -*- coding: utf-8 -*-
"""
deepseek_panel.py —— 历史小说界面（DeepSeek 集成）
================================================================================
本模块提供「架空宇宙 → 历史小说」的专用界面：

    DeepSeekSettingsDialog  API Key / 模型 / 参数设置（含连通性测试）
    NovelWriterPanel        小说工作台
        · 左侧：风格、篇幅、聚焦星系、自定义要求、生成按钮、历史作品列表
        · 右侧：实时流式显示的小说正文（带章节排版）
        · 工具：保存到历史作品、导出 TXT/Markdown/HTML、复制全文、停止生成

线程模型：
    生成任务放在 QThread 中执行，通过信号把每个文本片段回传给主线程，
    因此在生成长篇（数千字）时界面依然可以滚动、复制与停止。
"""

from __future__ import annotations

import html
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QTextBrowser, QTextEdit,
                               QVBoxLayout, QWidget)

from 核心 import deepseek_client as DS
from 核心 import world_generator as WG
from 核心.utils import (LOG, app_dir, clamp, ensure_dir, format_gametime, list_json_files,
                        load_json, save_json, timestamp_tag)

from .widgets import (CollapsiblePanel, GlowButton, HologramLoader, IconToggleButton,
                      InfoRow, NeonSlider, SectionTitle, SeparatorLine)

#: 历史作品保存目录名
NOVEL_DIR_NAME = "novels"


def novels_dir() -> str:
    """小说存档目录（位于应用目录下，随程序携带）。"""
    return ensure_dir(os.path.join(app_dir(), NOVEL_DIR_NAME))


# ==============================================================================
# 一、API 设置对话框
# ==============================================================================

class DeepSeekSettingsDialog(QDialog):
    """DeepSeek API 设置对话框。"""

    def __init__(self, config: DS.DeepSeekConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DeepSeek 设置")
        self.setMinimumWidth(560)
        self.config = config
        self._result_config: Optional[DS.DeepSeekConfig] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(SectionTitle("DeepSeek API 配置"))

        form = QFormLayout()
        form.setSpacing(6)

        self.key_edit = QLineEdit(config.api_key)
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-xxxxxxxx…（在 platform.deepseek.com 申请）")
        self.show_key = QCheckBox("显示")
        self.show_key.toggled.connect(
            lambda on: self.key_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_edit, 1)
        key_layout.addWidget(self.show_key)
        form.addRow("API Key", key_row)

        self.url_edit = QLineEdit(config.base_url)
        self.url_edit.setPlaceholderText("https://api.deepseek.com")
        form.addRow("接口地址", self.url_edit)

        self.model_combo = QComboBox()
        for key, label in DS.AVAILABLE_MODELS:
            self.model_combo.addItem(f"{key}　—　{label}", key)
        idx = self.model_combo.findData(config.model)
        self.model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("模型", self.model_combo)

        self.temp_slider = NeonSlider(Qt.Horizontal, "orange")
        self.temp_slider.setRange(0, 20)          # 0.0 ~ 2.0
        self.temp_slider.setValue(int(clamp(config.temperature, 0.0, 2.0) * 10))
        self.temp_label = QLabel(f"{config.temperature:.1f}")
        temp_row = QWidget()
        temp_layout = QHBoxLayout(temp_row)
        temp_layout.setContentsMargins(0, 0, 0, 0)
        temp_layout.addWidget(self.temp_slider, 1)
        temp_layout.addWidget(self.temp_label)
        self.temp_slider.valueChanged.connect(lambda v: self.temp_label.setText(f"{v / 10:.1f}"))
        form.addRow("创造性温度", temp_row)

        self.stream_check = QCheckBox("流式输出（边生成边显示，推荐）")
        self.stream_check.setChecked(config.stream)
        form.addRow("", self.stream_check)

        self.system_edit = QPlainTextEdit(config.system_prompt)
        self.system_edit.setMaximumHeight(96)
        self.system_edit.setPlaceholderText("系统提示词：定义模型的写作身份与风格约束")
        form.addRow("系统提示词", self.system_edit)

        layout.addLayout(form)
        layout.addWidget(SeparatorLine())

        hint = QLabel(
            "· API Key 仅保存在本机 <b>" + os.path.relpath(config.config_path(), app_dir()) +
            "</b>，不会随世界设定导出，也不会写入日志。<br>"
            "· 也可用环境变量 <b>DEEPSEEK_API_KEY</b> 提供 Key（优先级高于此处设置）。<br>"
            "· 未配置 Key 时仍可使用「本地示例」，先看看排版与内容组织方式。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#a9d4e6;background:transparent;")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        layout.addWidget(hint)

        # 按钮行
        row = QHBoxLayout()
        self.btn_test = GlowButton("测试连接", "green", compact=True)
        self.btn_test.clicked.connect(self._test)
        row.addWidget(self.btn_test)
        row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText("保存")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        row.addWidget(box)
        layout.addLayout(row)

    # ---- 交互 ----

    def _collect(self) -> DS.DeepSeekConfig:
        cfg = DS.DeepSeekConfig(
            api_key=self.key_edit.text().strip(),
            base_url=self.url_edit.text().strip() or DS.DEFAULT_BASE_URL,
            model=str(self.model_combo.currentData() or DS.DEFAULT_MODEL),
            temperature=self.temp_slider.value() / 10.0,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout,
            stream=self.stream_check.isChecked(),
            system_prompt=self.system_edit.toPlainText().strip() or DS.DeepSeekConfig().system_prompt,
        )
        return cfg

    def _test(self) -> None:
        cfg = self._collect()
        if not cfg.api_key:
            QMessageBox.warning(self, "无法测试", "请先填写 API Key。")
            return
        self.btn_test.setEnabled(False)
        self.btn_test.setText("测试中…")
        QGuiApplication.processEvents()
        try:
            ok, msg = DS.DeepSeekClient(cfg).check()
        except Exception as exc:                       # pragma: no cover
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        self.btn_test.setEnabled(True)
        self.btn_test.setText("测试连接")
        if ok:
            QMessageBox.information(self, "连接成功", msg)
        else:
            QMessageBox.warning(self, "连接失败", msg)

    def _save(self) -> None:
        cfg = self._collect()
        if cfg.save():
            self._result_config = cfg
            self.accept()
        else:
            QMessageBox.warning(self, "保存失败", "无法写入配置文件，请检查目录权限。")

    def result_config(self) -> Optional[DS.DeepSeekConfig]:
        return self._result_config


# ==============================================================================
# 二、生成线程
# ==============================================================================

class StoryWorker(QThread):
    """在后台线程生成小说，逐段回传文本。"""

    chunk = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, galaxy: WG.Galaxy, config: DS.DeepSeekConfig, style_key: str,
                 length_key: str, focus: str, custom_prompt: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.galaxy = galaxy
        self.config = config
        self.style_key = style_key
        self.length_key = length_key
        self.focus = focus
        self.custom_prompt = custom_prompt
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102
        try:
            system_prompt, user_prompt, max_tokens = DS.build_story_prompt(
                self.galaxy, self.style_key, self.length_key, self.focus, self.custom_prompt)
            client = DS.DeepSeekClient(self.config)
            messages = [{"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}]
            parts: List[str] = []
            if self.config.stream:
                for piece in client.chat_stream(messages, max_tokens=max_tokens):
                    if self._stop:
                        break
                    parts.append(piece)
                    self.chunk.emit(piece)
            else:
                text = client.chat(messages, stream=False, max_tokens=max_tokens)
                for i in range(0, len(text), 120):
                    if self._stop:
                        break
                    piece = text[i:i + 120]
                    parts.append(piece)
                    self.chunk.emit(piece)
            self.finished_ok.emit("".join(parts))
        except Exception as exc:
            LOG.error("生成历史小说失败", exc)
            self.failed.emit(str(exc))


# ==============================================================================
# 三、小说工作台
# ==============================================================================

@dataclass
class NovelRecord:
    """一篇历史小说的元数据。"""

    title: str
    galaxy: str
    seed: int
    style: str
    length: str
    focus: str
    created: float
    words: int
    path: str = ""


class NovelWriterPanel(QWidget):
    """历史小说工作台：生成 / 预览 / 保存 / 导出。"""

    status_message = Signal(str)

    def __init__(self, galaxy: Optional[WG.Galaxy] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.galaxy = galaxy
        self.config = DS.DeepSeekConfig().load()
        self._worker: Optional[StoryWorker] = None
        self._text = ""
        self._records: List[NovelRecord] = []
        self._gen_start = 0.0

        # ★ 必须给一个明确尺寸。这个面板是**顶层窗口**（由主窗口 show() 出来），
        #   而它内部用了 QSplitter + 可折叠面板：布局的 sizeHint 并没有一个
        #   有意义的宽度，Qt 于是把它压成默认的最小窗口 100×30——用户看到的就是
        #   "历史小说页面加载不出来"（其实窗口开了，只是小到看不见）。
        #   实测改前：面板 100×30、位置 (0,0)；改后 1180×760 正常显示。
        self.resize(1180, 760)
        self.setMinimumSize(880, 560)

        self._build_ui()
        self._refresh_records()
        self._update_galaxy_info()

    # ------------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # 顶部标题栏
        head = QHBoxLayout()
        title = QLabel("历史小说工作台")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title.setStyleSheet("color:#c9a8ff;background:transparent;letter-spacing:1px;")
        self._head_info = QLabel("尚未生成宇宙")
        self._head_info.setFont(QFont("Microsoft YaHei UI", 9))
        self._head_info.setStyleSheet("color:#a9d4e6;background:transparent;")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(self._head_info, 1)
        self.btn_settings = GlowButton("DeepSeek 设置", "cyan", compact=True)
        self.btn_settings.clicked.connect(self.open_settings)
        head.addWidget(self.btn_settings)
        root.addLayout(head)
        root.addWidget(SeparatorLine())

        splitter = QSplitter(Qt.Horizontal)

        # ---------------- 左：参数 ----------------
        left = QWidget()
        left.setMinimumWidth(268)
        left.setMaximumWidth(360)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        ll.setSpacing(6)

        # 世界观
        g_panel = CollapsiblePanel("世界观来源", "violet")
        gl = QVBoxLayout(g_panel.content)
        gl.setContentsMargins(6, 0, 0, 0)
        gl.setSpacing(3)
        self.lbl_galaxy = QLabel("（未载入宇宙）")
        self.lbl_galaxy.setWordWrap(True)
        self.lbl_galaxy.setStyleSheet("color:#e6f6fd;background:transparent;")
        self.lbl_galaxy.setFont(QFont("Microsoft YaHei UI", 9))
        gl.addWidget(self.lbl_galaxy)
        self.focus_combo = QComboBox()
        self.focus_combo.addItem("（全星区）", "")
        gl.addWidget(QLabel("舞台中心"))
        gl.addWidget(self.focus_combo)
        self.btn_refresh = GlowButton("刷新世界观", "cyan", compact=True)
        self.btn_refresh.clicked.connect(self._update_galaxy_info)
        gl.addWidget(self.btn_refresh)
        ll.addWidget(g_panel)

        # 写作参数
        w_panel = CollapsiblePanel("写作参数", "cyan")
        wl = QVBoxLayout(w_panel.content)
        wl.setContentsMargins(6, 0, 0, 0)
        wl.setSpacing(4)
        self.style_combo = QComboBox()
        for key, label, _desc in DS.STORY_STYLES:
            self.style_combo.addItem(label, key)
        self.style_combo.setCurrentIndex(0)
        self.style_combo.currentIndexChanged.connect(self._update_style_hint)
        wl.addWidget(QLabel("叙事风格"))
        wl.addWidget(self.style_combo)
        self.style_hint = QLabel("")
        self.style_hint.setWordWrap(True)
        self.style_hint.setStyleSheet("color:#8fd4ea;background:transparent;")
        self.style_hint.setFont(QFont("Microsoft YaHei UI", 8))
        wl.addWidget(self.style_hint)

        self.length_combo = QComboBox()
        for key, _words, label in DS.STORY_LENGTHS:
            self.length_combo.addItem(label, key)
        self.length_combo.setCurrentIndex(1)
        wl.addWidget(QLabel("篇幅"))
        wl.addWidget(self.length_combo)

        self.requirement_edit = QPlainTextEdit()
        self.requirement_edit.setPlaceholderText(
            "额外要求（可留空），例如：\n以一位考古学家的日记体写作；\n重点描写遗迹觉醒后的三日；\n不要出现胜利者视角。")
        self.requirement_edit.setMaximumHeight(84)
        wl.addWidget(QLabel("额外要求"))
        wl.addWidget(self.requirement_edit)
        ll.addWidget(w_panel)

        # 生成
        a_panel = CollapsiblePanel("生成", "orange")
        al = QVBoxLayout(a_panel.content)
        al.setContentsMargins(6, 0, 0, 0)
        al.setSpacing(4)
        self.btn_generate = GlowButton("✒ 生成历史小说", "violet")
        self.btn_generate.setMinimumHeight(34)
        self.btn_generate.clicked.connect(self.generate)
        al.addWidget(self.btn_generate)
        row = QHBoxLayout()
        self.btn_stop = GlowButton("■ 停止", "red", compact=True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_sample = GlowButton("本地示例", "cyan", compact=True)
        self.btn_sample.clicked.connect(self.generate_offline_sample)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_sample)
        al.addLayout(row)
        self.loader = HologramLoader()
        self.loader.setVisible(False)
        al.addWidget(self.loader)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color:#a9d4e6;background:transparent;")
        self.lbl_status.setFont(QFont("Microsoft YaHei UI", 9))
        al.addWidget(self.lbl_status)
        ll.addWidget(a_panel)

        # 历史作品
        h_panel = CollapsiblePanel("历史作品", "green")
        hl = QVBoxLayout(h_panel.content)
        hl.setContentsMargins(6, 0, 0, 0)
        hl.setSpacing(3)
        self.record_combo = QComboBox()
        self.record_combo.currentIndexChanged.connect(self._on_record_pick)
        hl.addWidget(self.record_combo)
        rrow = QHBoxLayout()
        self.btn_load = GlowButton("载入", "cyan", compact=True)
        self.btn_load.clicked.connect(lambda: self._on_record_pick(self.record_combo.currentIndex()))
        self.btn_delete = GlowButton("删除", "red", compact=True)
        self.btn_delete.clicked.connect(self._delete_record)
        rrow.addWidget(self.btn_load)
        rrow.addWidget(self.btn_delete)
        hl.addLayout(rrow)
        ll.addWidget(h_panel)

        ll.addStretch(1)
        splitter.addWidget(left)

        # ---------------- 右：正文 ----------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        tools = QHBoxLayout()
        self.btn_save = GlowButton("保存到历史作品", "green", compact=True)
        self.btn_save.clicked.connect(self.save_novel)
        self.btn_export = GlowButton("导出…", "cyan", compact=True)
        self.btn_export.clicked.connect(self.export_novel)
        self.btn_copy = GlowButton("复制全文", "violet", compact=True)
        self.btn_copy.clicked.connect(self.copy_text)
        tools.addWidget(self.btn_save)
        tools.addWidget(self.btn_export)
        tools.addWidget(self.btn_copy)
        tools.addStretch(1)
        self.lbl_stats = QLabel("0 字")
        self.lbl_stats.setFont(QFont("Consolas", 9))
        self.lbl_stats.setStyleSheet("color:#8fd4ea;background:transparent;")
        tools.addWidget(self.lbl_stats)
        rl.addLayout(tools)

        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(False)
        self.viewer.setStyleSheet(
            "QTextBrowser{background:rgba(6,18,28,240);border:1px solid rgba(0,190,230,150);"
            "border-radius:4px;color:#e6f6fd;padding:14px;}"
            "QScrollBar:vertical{background:rgba(6,16,26,150);width:9px;}"
            "QScrollBar::handle:vertical{background:rgba(0,190,230,180);border-radius:4px;}")
        self.viewer.setFont(QFont("Microsoft YaHei UI", 10))
        rl.addWidget(self.viewer, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 760])
        root.addWidget(splitter, 1)

        self._update_style_hint()
        self._render_text()

    # ------------------------------------------------------------------ 数据

    def set_galaxy(self, galaxy: Optional[WG.Galaxy]) -> None:
        """设置用于生成小说的架空宇宙。"""
        self.galaxy = galaxy
        self._update_galaxy_info()

    def _update_galaxy_info(self) -> None:
        self.focus_combo.blockSignals(True)
        self.focus_combo.clear()
        self.focus_combo.addItem("（全星区）", "")
        if self.galaxy is None:
            self.lbl_galaxy.setText("（未载入宇宙）\n请先在「架空星图」中生成一个宇宙。")
            self.focus_combo.blockSignals(False)
            return
        stats = self.galaxy.stats()
        self.lbl_galaxy.setText(
            f"<b>{html.escape(self.galaxy.name)}</b>　种子 {self.galaxy.seed}<br>"
            f"{stats.get('systems')} 星系 / {stats.get('planets')} 行星 / "
            f"{stats.get('routes')} 航线<br>"
            f"{stats.get('civilizations')} 文明 / {stats.get('factions')} 势力 / "
            f"{stats.get('events')} 历史事件<br>"
            f"时间：{format_gametime(self.galaxy.year)}")
        for s in self.galaxy.systems:
            label = s.name
            if s.civ_name:
                label += f"（{s.civ_name}）"
            elif s.population_millions > 0:
                label += "（有居民）"
            self.focus_combo.addItem(label, s.name)
        self.focus_combo.blockSignals(False)
        self._head_info.setText(
            f"{self.galaxy.name}　·　{stats.get('systems')} 星系　·　"
            f"{stats.get('civilizations')} 文明　·　{stats.get('events')} 事件")

    def _update_style_hint(self) -> None:
        key = str(self.style_combo.currentData() or "chronicle")
        for k, name, desc in DS.STORY_STYLES:
            if k == key:
                self.style_hint.setText(f"{name}：{desc}")
                return

    # ------------------------------------------------------------------ 生成

    def open_settings(self) -> None:
        """打开 DeepSeek 设置对话框。"""
        dialog = DeepSeekSettingsDialog(self.config, self)
        if dialog.exec() == QDialog.Accepted:
            cfg = dialog.result_config()
            if cfg is not None:
                self.config = cfg
                self._set_status(f"DeepSeek 配置已更新（模型 {cfg.model}，Key {cfg.masked_key()}）")

    def generate(self) -> None:
        """开始生成（后台线程 + 流式显示）。"""
        if self.galaxy is None:
            self._set_status("请先在「架空星图」中生成一个宇宙，再来写它的历史。")
            QMessageBox.information(self, "还没有宇宙", "历史小说需要一个架空宇宙作为素材。\n"
                                                        "请切换到「架空星图」并点击「⚡ 生成宇宙」。")
            return
        if self._worker is not None and self._worker.isRunning():
            self._set_status("正在生成中，请稍候或点击停止。")
            return
        if not self.config.ready():
            answer = QMessageBox.question(
                self, "尚未配置 DeepSeek API Key",
                "还没有配置 API Key，无法调用大模型。\n\n"
                "是否现在去配置？\n（选择「No」将改用本地示例文本）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                self.open_settings()
                if not self.config.ready():
                    return
            else:
                self.generate_offline_sample()
                return

        style_key = str(self.style_combo.currentData() or "chronicle")
        length_key = str(self.length_combo.currentData() or "medium")
        focus = str(self.focus_combo.currentData() or "")
        custom = self.requirement_edit.toPlainText().strip()

        self._text = ""
        self._render_text()
        self._gen_start = time.time()
        self.btn_generate.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.loader.setVisible(True)
        self.loader.set_progress(5, "正在构建提示词…")
        self._set_status("正在调用 DeepSeek 生成历史小说…")

        self._worker = StoryWorker(self.galaxy, self.config, style_key, length_key,
                                   focus, custom, self)
        self._worker.chunk.connect(self._on_chunk)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(str)
    def _on_chunk(self, piece: str) -> None:
        """接收流式片段并追加到正文（保持滚动到底部）。"""
        self._text += piece
        self.viewer.moveCursor(QTextCursor.End)
        self.viewer.insertPlainText(piece)
        self.viewer.moveCursor(QTextCursor.End)
        words = len(self._text)
        elapsed = max(0.1, time.time() - self._gen_start)
        self.lbl_stats.setText(f"{words} 字")
        self.loader.set_progress(int(clamp(words / 60.0, 5, 96)),
                                 f"生成中… {words} 字（{words / elapsed:.0f} 字/秒）")

    @Slot(str)
    def _on_finished(self, text: str) -> None:
        self.btn_generate.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.loader.setVisible(False)
        self._text = text or self._text
        self._render_text()
        self._set_status(f"生成完成：共 {len(self._text)} 字。可点击「保存到历史作品」或「导出」。")

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.btn_generate.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.loader.setVisible(False)
        self._set_status(f"生成失败：{message}")
        QMessageBox.warning(self, "生成失败", f"{message}\n\n"
                            "可尝试：检查网络 / 更换模型 / 稍后重试，"
                            "或先使用「本地示例」查看排版效果。")

    def stop(self) -> None:
        """停止生成。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._set_status("已请求停止，正在等待当前响应结束…")
            self.btn_stop.setEnabled(False)

    def generate_offline_sample(self) -> None:
        """生成离线示例文本（无需 API Key）。"""
        if self.galaxy is None:
            self._set_status("请先生成一个架空宇宙。")
            return
        style_key = str(self.style_combo.currentData() or "chronicle")
        self._text = DS.offline_sample_story(self.galaxy, style_key)
        self._render_text()
        self._set_status("已生成离线示例文本（配置 API Key 后可改写为真正的大模型小说）。")

    # ------------------------------------------------------------------ 显示

    def _render_text(self) -> None:
        """把纯文本渲染成带排版的富文本（章节标题、史官注、行距）。"""
        self.lbl_stats.setText(f"{len(self._text)} 字")
        if not self._text.strip():
            self.viewer.setHtml(
                "<div style='color:#8fd4ea;font-size:11pt;line-height:1.9'>"
                "<p>这里将显示由 DeepSeek 生成的<b>架空宇宙历史小说</b>。</p>"
                "<p style='color:#a9d4e6'>使用步骤：</p>"
                "<ol style='color:#a9d4e6;line-height:1.8'>"
                "<li>在「架空星图」中生成一个宇宙（种子可复现）；</li>"
                "<li>在「工具 → DeepSeek 设置」中填入 API Key（或使用本地示例）；</li>"
                "<li>选择叙事风格、篇幅与舞台中心星系，点击「✒ 生成历史小说」；</li>"
                "<li>生成结果可保存到历史作品、导出 TXT / Markdown / HTML。</li>"
                "</ol></div>")
            return
        lines = self._text.splitlines()
        html_lines: List[str] = []
        for raw in lines:
            line = raw.strip()
            if not line:
                html_lines.append("<p style='margin:0 0 10px 0'></p>")
                continue
            escaped = html.escape(line)
            if line.startswith("【") or line.startswith("史官注") or line.startswith("——"):
                html_lines.append(
                    f"<p style='color:#ffd08a;font-weight:bold;margin:12px 0 8px 0'>{escaped}</p>")
            elif (line.startswith("第") and ("章" in line[:6] or "节" in line[:6])):
                html_lines.append(
                    f"<p style='color:#9ff6ff;font-size:13pt;font-weight:bold;"
                    f"margin:20px 0 10px 0;letter-spacing:2px'>{escaped}</p>")
            elif line.startswith("-") or line.startswith("·") or line.startswith("　　·"):
                html_lines.append(
                    f"<p style='margin:2px 0 6px 18px;color:#d6eefb'>{escaped}</p>")
            else:
                html_lines.append(
                    f"<p style='margin:0 0 10px 0;line-height:1.95;text-indent:2em'>{escaped}</p>")
        self.viewer.setHtml(
            "<div style='font-size:11pt'>" + "\n".join(html_lines) + "</div>")
        self.viewer.moveCursor(QTextCursor.End)

    def current_text(self) -> str:
        return self._text

    def _set_status(self, text: str) -> None:
        self.lbl_status.setText(text)
        self.status_message.emit(text)

    # ------------------------------------------------------------------ 存档

    def _record_title(self) -> str:
        galaxy_name = self.galaxy.name if self.galaxy else "未知宇宙"
        style = str(self.style_combo.currentText())
        return f"{galaxy_name}·{style}·{timestamp_tag()}"

    def save_novel(self) -> None:
        """保存当前小说到历史作品目录。"""
        if not self._text.strip():
            self._set_status("正文为空，无法保存。")
            return
        title = self._record_title()
        focus = str(self.focus_combo.currentData() or "")
        record = {
            "title": title,
            "galaxy": self.galaxy.name if self.galaxy else "",
            "seed": int(self.galaxy.seed) if self.galaxy else 0,
            "style": str(self.style_combo.currentText()),
            "length": str(self.length_combo.currentText()),
            "focus": focus,
            "created": time.time(),
            "words": len(self._text),
            "text": self._text,
            "settings": {
                "model": self.config.model,
                "temperature": self.config.temperature,
            },
        }
        path = os.path.join(novels_dir(), f"novel_{timestamp_tag()}_{int(time.time() % 1000):03d}.json")
        if save_json(path, record):
            self._refresh_records()
            self._set_status(f"已保存到历史作品：{title}")
        else:
            self._set_status("保存失败，请检查磁盘权限。")

    def _refresh_records(self) -> None:
        """刷新历史作品下拉列表。"""
        self._records = []
        try:
            for name in sorted(list_json_files(novels_dir()), reverse=True):
                data = load_json(os.path.join(novels_dir(), name), default=None)
                if not isinstance(data, dict):
                    continue
                self._records.append(NovelRecord(
                    title=str(data.get("title", name)),
                    galaxy=str(data.get("galaxy", "")),
                    seed=int(data.get("seed", 0) or 0),
                    style=str(data.get("style", "")),
                    length=str(data.get("length", "")),
                    focus=str(data.get("focus", "")),
                    created=float(data.get("created", 0.0) or 0.0),
                    words=int(data.get("words", 0) or 0),
                    path=os.path.join(novels_dir(), name),
                ))
        except Exception as exc:
            LOG.error("读取历史作品失败", exc)
        self.record_combo.blockSignals(True)
        self.record_combo.clear()
        if not self._records:
            self.record_combo.addItem("（暂无作品）", "")
        else:
            for r in self._records:
                stamp = time.strftime("%m-%d %H:%M", time.localtime(r.created))
                self.record_combo.addItem(f"{stamp}　{r.title[:26]}　{r.words}字", r.path)
        self.record_combo.blockSignals(False)

    def _on_record_pick(self, index: int) -> None:
        """载入选中的历史作品。"""
        path = str(self.record_combo.currentData() or "")
        if not path:
            return
        data = load_json(path, default=None)
        if not isinstance(data, dict):
            self._set_status("读取失败：文件损坏或格式不正确。")
            return
        self._text = str(data.get("text", ""))
        self._render_text()
        self._set_status(f"已载入历史作品：{data.get('title', '')}（{len(self._text)} 字）")

    def _delete_record(self) -> None:
        path = str(self.record_combo.currentData() or "")
        if not path:
            self._set_status("没有可删除的作品。")
            return
        if QMessageBox.question(self, "删除作品", "确定要删除选中的历史作品吗？此操作不可撤销。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            os.remove(path)
            self._set_status("已删除该作品。")
        except Exception as exc:
            LOG.error("删除作品失败", exc)
            self._set_status(f"删除失败：{exc}")
        self._refresh_records()

    # ------------------------------------------------------------------ 导出

    def export_novel(self) -> None:
        """导出为 TXT / Markdown / HTML。"""
        if not self._text.strip():
            self._set_status("正文为空，无法导出。")
            return
        default = os.path.join(os.path.expanduser("~"),
                               f"stellar_novel_{timestamp_tag()}.md")
        path, selected = QFileDialog.getSaveFileName(
            self, "导出历史小说", default,
            "Markdown (*.md);;纯文本 (*.txt);;网页 (*.html)")
        if not path:
            return
        try:
            title = self._record_title()
            if path.lower().endswith(".html") or "html" in (selected or "").lower():
                body = "".join(
                    f"<p>{html.escape(line)}</p>" if line.strip() else "<p></p>"
                    for line in self._text.splitlines())
                content = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                           f"<title>{html.escape(title)}</title>"
                           "<style>body{background:#061019;color:#e6f6fd;"
                           "font-family:'Microsoft YaHei UI',sans-serif;line-height:1.9;"
                           "max-width:820px;margin:40px auto;padding:0 20px;}"
                           "h1{color:#9ff6ff;font-size:22px;}"
                           "p{text-indent:2em;margin:0 0 10px 0;}</style></head><body>"
                           f"<h1>{html.escape(title)}</h1>{body}</body></html>")
            elif path.lower().endswith(".md") or "markdown" in (selected or "").lower():
                content = f"# {title}\n\n{self._text}\n"
            else:
                content = f"{title}\n{'=' * 40}\n\n{self._text}\n"
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(content)
            self._set_status(f"已导出：{path}")
            QMessageBox.information(self, "导出成功", f"小说已导出到：\n{path}")
        except Exception as exc:
            LOG.error("导出小说失败", exc)
            self._set_status(f"导出失败：{exc}")

    def copy_text(self) -> None:
        """复制全文到剪贴板。"""
        if not self._text.strip():
            self._set_status("正文为空。")
            return
        QGuiApplication.clipboard().setText(self._text)
        self._set_status(f"已复制 {len(self._text)} 字到剪贴板。")

    # ------------------------------------------------------------------ 清理

    def shutdown(self) -> None:
        """关闭时停止后台线程。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)


__all__ = ["NovelWriterPanel", "DeepSeekSettingsDialog", "StoryWorker", "NovelRecord",
           "novels_dir"]
