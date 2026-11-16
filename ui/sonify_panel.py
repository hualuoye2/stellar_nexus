# -*- coding: utf-8 -*-
"""
sonify_panel.py —— 「星图之声」面板 + 从星表取音的控制器
========================================================================
把 `核心/sonify.py` 的合成器接到真实星表与真实视线上：

  · 星海模式：从全天星表挑亮星，随机漫步地换音——这是"轻音乐"的主体
  · 银河模式：以银道面低频持续音为主，偶尔点缀高音
  · 静夜模式：极少音符、长衰减

  「跟随视角」开关（默认开）：打开后音高/声像由**当前视线**决定，
  旋转星图时听到的和声跟着变，银心方向最厚——这就是设计想法第 ④ 条里
  "旋转星图的时候，你听见的是那片天区的和声"。

取音规则（对应想法的四个映射）：
  光谱型 → 音区与音色（`spectral_voice`）
  星等    → 音量（`magnitude_gain`，动态被压缩过，不然天狼会盖住一切）
  方位角  → 声场左右（`az_to_pan`）
  高度角  → 声像左右之外的"远近"：地平线下不发声（不可见的东西不该有声音）
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QSlider,
                               QVBoxLayout, QWidget)

from 核心 import sonify as SN
from 核心.utils import LOG, clamp
from ui.widgets import GlowButton, NeonSlider

#: 声部上限（与合成器的 MAX_VOICES 一致）
MAX_NOTES = 48


class StarVoiceSource:
    """把星表对象转成 SoundNote（纯逻辑，不碰 Qt，方便单测）。"""

    #: 和声垫最多几个声部。★ 这个上限是"乐器能不能被听见"的关键：
    #:   27 颗星全上时叠加峰值到 0.297，而一次乐器音符只有 0.051——
    #:   伴奏比旋律响 6 倍，"换乐器"就听不出来了。收到 9 个声部后
    #:   垫子降到合理量级，旋律乐器才能站到前面。
    PAD_MAX_VOICES = 9

    #: 同一个音高最多允许几个声部。★ 这个上限是"能不能听"的关键：
    #:  天空中最多的就是 A/F 型同亮度恒星，如果直接"按星等取前 48 颗"，
    #:  实测 48 条乐谱**全部落在 329.6 Hz 一个音上**——听起来像持续警报音，
    #:  而不是和声。所以必须按音高分桶限量，把声部摊到不同音高上。
    PER_PITCH_CAP = 3

    #: 同一桶内的音高铺开量（音分）。桶里的第 i 个声部额外加 i×这个值，
    #: 于是"3 颗同型同亮度的星"会得到 3 个**不同但相邻**的音（微分音簇），
    #: 而不是 3 个完全同频的声部叠在一起（同频叠加只会更响，不会更好听）。
    #: 这也让"音高多样性"在自检里可测：3 颗星必须给出 3 个音高。
    BUCKET_SPREAD_CENTS = 55.0

    #: 音区压缩：光谱型原本覆盖 5 个八度，最高那几个音连续听会"扎耳朵"。
    #: 这里把整体压到 3 个八度内（220Hz~1980Hz），但保留
    #: "O/B 亮而高、K/M 暗而低"的对应关系。
    #: ★ 主音提到 A3(220Hz) 之后这张表必须跟着重算：原来的
    #:   {0:0,1:1,2:1,3:2,4:3} 会把 O 型顶到 **3951Hz**、N 型顶到 5588Hz
    #:   （刺耳）。新表把 4 压到 2，N 型再单独压一层。
    OCTAVE_COMPRESS: Dict[int, int] = {0: 0, 1: 1, 2: 1, 3: 2, 4: 2}
    #: N 型（行星/月球用的模拟光谱型）基频本来就高，单独再压一层
    OCTAVE_COMPRESS_N: int = 1

    def __init__(self) -> None:
        self._seen: Dict[str, int] = {}

    def notes_from_objects(self, objs: Sequence[object], center_az: float,
                           center_alt: float, limit: int = MAX_NOTES) -> List[SN.SoundNote]:
        """从一批天体（需要 .spectral/.mag/.alt/.az/.key）产生乐谱。

        · 只取地平线以上的（alt > 0）——看不见的东西不该有声音
        · 按星等从亮到暗排序
        · **按音高分桶，每桶最多 PER_PITCH_CAP 个**，否则会糊成一个音
        · 同一颗星连续出现的次数越多，音量略微衰减（避免"钉"在一个音上）
        """
        cands = []
        for o in objs:
            alt = float(getattr(o, "alt", 0.0) or 0.0)
            if alt <= 0.0:
                continue
            kind = str(getattr(o, "kind", "") or "")
            if kind not in ("star", "planet", "messier", "moon", "sun"):
                continue
            cands.append(o)
        cands.sort(key=lambda o: float(getattr(o, "mag", 99.0) or 99.0))

        out: List[SN.SoundNote] = []
        bucket: Dict[int, int] = {}
        for o in cands:
            if len(out) >= limit:
                break
            key = str(getattr(o, "key", "") or "")
            kind = str(getattr(o, "kind", "") or "")
            if kind in ("planet", "moon", "sun"):
                # 行星/日月给一个固定的、偏低沉的音，跟恒星区分开
                spec = {"planet": "K", "moon": "N", "sun": "G"}.get(kind, "G")
            else:
                spec = str(getattr(o, "spectral", "") or "")
            oct_, deg, harm = SN.voice_of(spec)
            oct_ = self.OCTAVE_COMPRESS.get(oct_, oct_)
            # ★ 声部跟踪的是"音阶级数"，不是频率——和声推进就是给整组 +k 级，
            #   所以这里必须把 (音区, 级数) 折算成**绝对级数**传下去。
            #   音区已压到 0~3（≈110~880Hz）；实测再加一级会顶到 1050Hz（发尖）。
            step = oct_ * len(SN.SCALE) + deg
            # 分桶键用**四舍五入后的实际音高**（不是 pitch class % 12）——
            # 用 %12 会让不同八度的同一个音级互相挤占配额，
            # 实测只能留下 10 条乐谱；按实际音高分桶能保留八度层次。
            bucket_key = int(round(12.0 * math.log2(SN.pitch_hz(0, step) / SN.ROOT_HZ)))
            n_pc = bucket.get(bucket_key, 0)
            if n_pc >= self.PER_PITCH_CAP:
                continue
            bucket[bucket_key] = n_pc + 1
            # 桶内铺开：第 2、3 个声部往上挪一点点（微分音簇），
            # 避免完全同频叠加（那只会更响，不会更好听）。
            # ★ 用**音分**表达偏移，而不是给音阶级数加小数：
            #   级数必须是整数（`pitch_hz` 会 int() 掉小数，加了也白加，
            #   实测三个声部的频率完全相同）。
            cents = n_pc * 55.0                      # 55 音分 ≈ 半个半音
            pitch = SN.pitch_hz(0, step) * (2.0 ** (cents / 1200.0))
            gain = SN.magnitude_gain(float(getattr(o, "mag", 6.0) or 6.0))
            # 高度角参与音量：贴近地平线的天体轻一点（大气消光）
            alt = float(getattr(o, "alt", 0.0) or 0.0)
            gain *= 0.45 + 0.55 * math.sin(math.radians(clamp(alt, 0.0, 90.0)))
            # 常驻降权：同一个天体连续出现就让它慢慢退到背景
            n = self._seen.get(key, 0)
            gain *= 1.0 / (1.0 + 0.22 * n)
            # 失谐只在**声部侧**做（`Voice.detune`，±0.3% 的固定值），
            # 音符这里保持"音高 = pitch_hz(0, degree)"的严格一致——
            # 两处都加失谐会让"音级↔音高"不再自洽，调试时很难看出问题。
            pan = SN.az_to_pan(float(getattr(o, "az", 0.0) or 0.0), center_az)
            out.append(SN.SoundNote(pitch=pitch, gain=gain,
                                    pan=pan, harmonics=harm, key=key,
                                    degree=step, cents=cents))
        # 只留最亮的若干颗星：全上会把旋律乐器彻底盖住（见 PAD_MAX_VOICES）
        if len(out) > self.PAD_MAX_VOICES:
            out.sort(key=lambda n: -n.gain)
            out = out[:self.PAD_MAX_VOICES]
        # ★ 按声部数做总能量归一：直接叠加 N 个 gain≈0.5 的声部，
        #   总增益和会到 10 以上，全被 tanh 限幅压扁——听感是"糊成一团"。
        #   这里把单声部幅度按 1/sqrt(N) 缩放（sqrt 而不是 N：
        #   保留"亮星更突出"的相对关系，只压总能量）。
        if out:
            k = 1.0 / math.sqrt(max(1, len(out)))
            for n in out:
                n.gain *= k
        return out

    def observe(self, notes: Sequence[SN.SoundNote]) -> None:
        """把"这一轮出现的天体"记进常驻计数（由调用方在每轮之后调用）。"""
        for n in notes:
            if n.key:
                self._seen[n.key] = self._seen.get(n.key, 0) + 1

    def decay(self) -> None:
        """缓慢忘记：久未出现的天体不应被永久降权。"""
        for k in list(self._seen):
            self._seen[k] = int(self._seen[k] * 0.97)
            if self._seen[k] <= 0:
                del self._seen[k]


class StarMusicController:
    """音频引擎 + 乐谱来源的统一入口（UI 只跟它打交道）。"""

    def __init__(self, catalog_provider=None) -> None:
        # ★ 用 SonifyHost（子进程合成）而不是 SonifyEngine（同进程线程）。
        #   实测同进程线程在主线程重绘的 GIL 挤压下生产速率只有 0.81×，
        #   听感就是"转一下卡一下"。子进程有自己的 GIL，不受影响。
        self.engine = SN.SonifyHost()
        self.enabled = False
        self.follow_view = True
        self.mode = "starry"
        #: 旋律乐器：pad（只有和声）/ epiano / guitar / bell
        self.instrument = "pad"
        #: 旋律突出度 0~1（见面板上的「旋律」滑杆）。0.42 = 偏伴奏：
        #: 垫子保持约 0.81 倍、乐器约 0.89 倍——实测这样总音量与纯垫子相当，
        #: 伴奏不会被乐器盖掉。
        self.melody = 0.42
        #: 默认音量。合成器的软限幅驱动只有 0.30（为了不失真），
        #: 所以这里要比"直连"时高一些才听得清；同时留足余量：
        #: 音景是背景音乐，不该盖过思考。
        self.volume = 0.55
        self._src = StarVoiceSource()
        self._rng = random.Random(20260922)
        self._catalog_provider = catalog_provider     # 返回 [(alt, az, railkey...)]
        self._objects: List[object] = []
        self._last_regen = 0.0
        self._regen_interval = 6.0
        self._seed_notes: List[SN.SoundNote] = []

    # ------------------------------------------------------------------ 开关
    def set_enabled(self, on: bool) -> bool:
        """打开/关闭。返回实际状态（启动失败时仍是 False）。"""
        if on:
            if not self.engine.available:
                ok = self.engine.start()
                if not ok:
                    self.enabled = False
                    return False
            self.engine.brain.set_master(self.volume)
            self.engine.brain.set_mode(self.mode)
            self.enabled = True
            self._regenerate(force=True)
            self.engine.push()
            return True
        self.enabled = False
        self.engine.brain.set_notes([])
        self.engine.brain.set_drone(0.0)
        self.engine.push()
        return False

    def set_volume(self, v: float) -> None:
        self.volume = float(clamp(v, 0.0, 1.0))
        self.engine.brain.set_master(self.volume)
        self.engine.push()

    def set_mode(self, mode: str) -> None:
        self.mode = mode if any(m == mode for m, _, _ in SN.MUSIC_MODES) else "starry"
        self.engine.brain.set_mode(self.mode)
        self._regenerate(force=True)
        self.engine.push()

    def set_instrument(self, kind: str) -> None:
        """切换旋律乐器（pad / epiano / guitar / bell）。"""
        self.instrument = (kind if any(k == kind for k, _, _ in SN.INSTRUMENTS)
                           else "pad")
        self.engine.brain.set_instrument(self.instrument)
        self.engine.push()

    def set_melody(self, v: float) -> None:
        """设置旋律突出度（0~1）。"""
        self.melody = float(clamp(v, 0.0, 1.0))
        self.engine.brain.set_melody(self.melody)
        self.engine.push()

    def set_follow_view(self, on: bool) -> None:
        self.follow_view = bool(on)
        self._regenerate(force=True)
        self.engine.push()

    def stats(self) -> Dict[str, float]:
        """诊断：共享环水位与是否退化成同进程线程。"""
        try:
            return self.engine.stats()
        except Exception:
            return {}

    # ------------------------------------------------------------------ 数据
    def set_objects(self, objs: Sequence[object]) -> None:
        """由视图每帧（或节流后）提供当前可见天体。"""
        self._objects = list(objs)

    def tick(self, now: float, center_az: float, center_alt: float) -> None:
        """UI 定时器调用：按模式产生乐谱并推给合成器。"""
        if not self.enabled:
            return
        if self.follow_view and self._objects:
            notes = self._src.notes_from_objects(self._objects, center_az, center_alt)
            self._src.observe(notes)
            self._src.decay()
            self.engine.brain.set_notes(notes)
            self.engine.brain.set_drone(self._drone_level(center_alt))
        else:
            # 不跟随视角：按模式自己产生乐谱，每隔几秒换一批音
            if now - self._last_regen >= self._regen_interval:
                self._regenerate()
            self.engine.brain.set_notes(self._seed_notes)
            self.engine.brain.set_drone(self._drone_level(center_alt))
        # ★ 参数写进共享内存（子进程模式）/ 转给同进程引擎（降级模式）。
        #   这一步只花几微秒，必须在每次改完参数后调用。
        self.engine.push()

    def _drone_level(self, center_alt: float) -> float:
        """银河带低频量：视线越接近银道面越厚。

        这里用高度角做一个粗略代理——真正的银道面纬度要算银道坐标，
        但那需要每帧算 8883 颗星；这里只要"抬头看天时更厚、看地平线时更薄"
        这个听感方向就够，成本为零。
        """
        if self.mode == "galaxy":
            return float(clamp(0.30 + 0.50 * math.sin(math.radians(max(0.0, center_alt))),
                               0.0, 0.85))
        if self.mode == "quiet":
            return 0.18
        return float(clamp(0.10 + 0.30 * math.sin(math.radians(max(0.0, center_alt))),
                           0.0, 0.45))

    def _regenerate(self, force: bool = False) -> None:
        """（不跟随视角时）重新挑一批音。"""
        import time as _t
        self._last_regen = _t.perf_counter()
        rng = self._rng
        if self.mode == "galaxy":
            count, span, gains = 6, 3, (0.10, 0.34)
            oct_range = (2, 4)          # 上限压到 ~1.3kHz，避免发尖
        elif self.mode == "quiet":
            count, span, gains = 3, 2, (0.06, 0.20)
            oct_range = (1, 3)
        else:                                  # starry
            count, span, gains = 14, 5, (0.10, 0.40)
            oct_range = (1, 4)          # 上限压到 ~1.3kHz
        notes: List[SN.SoundNote] = []
        base_deg = rng.randrange(len(SN.SCALE))
        for i in range(count):
            oct_ = rng.randint(*oct_range)
            deg = base_deg + rng.randrange(-span, span + 1)
            _, _, harm = SN.voice_of(rng.choice("OBAFGKM"))
            step = oct_ * len(SN.SCALE) + deg
            notes.append(SN.SoundNote(
                pitch=SN.pitch_hz(0, step),
                degree=step,
                gain=rng.uniform(*gains),
                pan=rng.uniform(-0.75, 0.75),
                harmonics=harm,
                key="seed%d" % i))
        self._seed_notes = notes
        self._regen_interval = {"galaxy": 8.0, "quiet": 11.0}.get(self.mode, 6.0)


class SonifyPanel(QWidget):
    """「星图之声」控制面板（放进左侧控制栏，与其它面板同款外观）。"""

    enabled_changed = Signal(bool)

    def __init__(self, controller: StarMusicController,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 2, 0, 2)
        lay.setSpacing(4)

        head = QLabel("把星图「弹」出来：旋转视角时，听到的是那片天区的和声")
        head.setWordWrap(True)
        head.setStyleSheet("color:#a9c9da;background:transparent;")
        head.setFont(QFont("Microsoft YaHei UI", 9))
        lay.addWidget(head)

        row = QHBoxLayout()
        # 用会闪烁的 GlowButton 而不是自定义开关：外观与左侧其它面板一致
        self.btn_toggle = GlowButton("♪ 开启星图之声", "violet", compact=True)
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.toggled.connect(self._on_toggle)
        row.addWidget(self.btn_toggle, 2)
        lay.addLayout(row)

        r2 = QHBoxLayout()
        lbl = QLabel("音景")
        lbl.setStyleSheet("color:#8fd4ea;background:transparent;")
        lbl.setFont(QFont("Microsoft YaHei UI", 9))
        r2.addWidget(lbl)
        self.combo_mode = QComboBox()
        for key, name, tip in SN.MUSIC_MODES:
            self.combo_mode.addItem(name, key)
            self.combo_mode.setItemData(self.combo_mode.count() - 1, tip,
                                        Qt.ToolTipRole)
        self.combo_mode.currentIndexChanged.connect(self._on_mode)
        r2.addWidget(self.combo_mode, 1)
        lay.addLayout(r2)

        r3 = QHBoxLayout()
        vlbl = QLabel("音量")
        vlbl.setStyleSheet("color:#8fd4ea;background:transparent;")
        vlbl.setFont(QFont("Microsoft YaHei UI", 9))
        r3.addWidget(vlbl)
        self.slider = NeonSlider(Qt.Horizontal, "cyan")
        self.slider.setRange(0, 100)
        self.slider.setValue(int(controller.volume * 100))
        self.slider.valueChanged.connect(self._on_volume)
        r3.addWidget(self.slider, 1)
        self.lbl_vol = QLabel("%d%%" % int(controller.volume * 100))
        self.lbl_vol.setStyleSheet("color:#a9c9da;background:transparent;")
        self.lbl_vol.setFont(QFont("Microsoft YaHei UI", 9))
        r3.addWidget(self.lbl_vol)
        lay.addLayout(r3)

        r4 = QHBoxLayout()
        ilbl = QLabel("乐器")
        ilbl.setStyleSheet("color:#8fd4ea;background:transparent;")
        ilbl.setFont(QFont("Microsoft YaHei UI", 9))
        r4.addWidget(ilbl)
        self.combo_inst = QComboBox()
        for key, name, tip in SN.INSTRUMENTS:
            self.combo_inst.addItem(name, key)
            self.combo_inst.setItemData(self.combo_inst.count() - 1, tip,
                                        Qt.ToolTipRole)
        idx = self.combo_inst.findData(controller.instrument)
        self.combo_inst.setCurrentIndex(max(0, idx))
        self.combo_inst.currentIndexChanged.connect(self._on_instrument)
        r4.addWidget(self.combo_inst, 1)
        lay.addLayout(r4)

        # 旋律突出度：伴奏与乐器的配比。
        # ★ 这个旋钮是为了修一个我自己配错的默认值：原来为了让乐器"被听见"，
        #   把伴奏压到整体只剩纯垫子的一半，听感变成"只有钢琴、没有背景音乐"。
        #   能量占比不等于听感占比（乐器是有音头的瞬态），所以留给人调。
        r5 = QHBoxLayout()
        mlbl = QLabel("旋律")
        mlbl.setStyleSheet("color:#8fd4ea;background:transparent;")
        mlbl.setFont(QFont("Microsoft YaHei UI", 9))
        r5.addWidget(mlbl)
        self.slider_mel = NeonSlider(Qt.Horizontal, "violet")
        self.slider_mel.setRange(0, 100)
        self.slider_mel.setValue(int(controller.melody * 100))
        self.slider_mel.setToolTip(
            "伴奏与乐器的配比\n"
            "  左：伴奏（和声垫 / 低音线 / 铃音）为主，乐器只做点缀\n"
            "  右：乐器靠前")
        self.slider_mel.valueChanged.connect(self._on_melody)
        r5.addWidget(self.slider_mel, 1)
        self.lbl_mel = QLabel("%d%%" % int(controller.melody * 100))
        self.lbl_mel.setStyleSheet("color:#a9c9da;background:transparent;")
        self.lbl_mel.setFont(QFont("Microsoft YaHei UI", 9))
        r5.addWidget(self.lbl_mel)
        lay.addLayout(r5)

        self.chk_follow = QCheckBox("跟随视角（音随星图转）")
        self.chk_follow.setChecked(controller.follow_view)
        self.chk_follow.stateChanged.connect(
            lambda s: controller.set_follow_view(bool(s)))
        lay.addWidget(self.chk_follow)

        self.lbl_state = QLabel("未开启")
        self.lbl_state.setWordWrap(True)
        self.lbl_state.setStyleSheet("color:#a9c9da;background:transparent;")
        self.lbl_state.setFont(QFont("Microsoft YaHei UI", 9))
        lay.addWidget(self.lbl_state)
        self._refresh_state()

    # ------------------------------------------------------------------ 槽
    def _on_toggle(self, on: bool) -> None:
        ok = self.controller.set_enabled(bool(on))
        if on and not ok:
            self.btn_toggle.blockSignals(True)
            self.btn_toggle.setChecked(False)
            self.btn_toggle.blockSignals(False)
            self.lbl_state.setText("开启失败：%s" % (self.controller.engine.reason
                                                or "系统没有可用的音频输出"))
            return
        self.enabled_changed.emit(ok)
        self._refresh_state()

    def _on_mode(self) -> None:
        self.controller.set_mode(str(self.combo_mode.currentData() or "starry"))
        self._refresh_state()

    def _on_instrument(self) -> None:
        self.controller.set_instrument(
            str(self.combo_inst.currentData() or "pad"))
        self._refresh_state()

    def _on_melody(self, v: int) -> None:
        self.controller.set_melody(v / 100.0)
        self.lbl_mel.setText("%d%%" % v)

    def _on_volume(self, v: int) -> None:
        self.controller.set_volume(v / 100.0)
        self.lbl_vol.setText("%d%%" % v)

    def _refresh_state(self) -> None:
        if not self.controller.enabled:
            self.lbl_state.setText("未开启　·　开启后星图会按光谱型/星等/方位发声"
                                   "（纯 numpy 合成，不需要音频文件）")
            return
        mode_cn = next((n for k, n, _ in SN.MUSIC_MODES if k == self.controller.mode),
                       self.controller.mode)
        inst_cn = next((n for k, n, _ in SN.INSTRUMENTS
                        if k == self.controller.instrument), self.controller.instrument)
        self.lbl_state.setText(
            "正在发声　·　音景「%s」　·　乐器「%s」　·　%s"
            % (mode_cn, inst_cn,
               "跟随视角" if self.controller.follow_view else "独立音景"))
