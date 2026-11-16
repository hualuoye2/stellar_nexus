# -*- coding: utf-8 -*-
"""
sonify.py —— 把星图"弹"出来：星等 / 光谱型 / 位置 → 声音
========================================================================
对应设计想法第 ④ 条：

    恒星的光谱型决定音色（O 型蓝巨星是刺耳高频，M 型红矮星是低沉嗡鸣），
    光度决定音量，距离决定延迟，方位决定声场左右。
    于是旋转星图的时候，你听见的是那片天区的和声。银河带会变成一层低频持续音。

设计上的三个关键决定（都为了"好听"而不是"准确"）：

1. **不用各星真实频率，改用五声音阶**。
   12 颗亮星各自唱自己的频率会得到一堆小二度、增四度——那是噪音不是音乐。
   把光谱型映射到 A 小调五声音阶（A C D E G）后，**任意组合都协和**；
   光谱型仍然决定音区高低（O/B → 高音区、K/M → 低音区），
   所以"蓝巨星明亮、红矮星低沉"这条听觉事实并没有丢。

2. **音色用谐波振幅包络区分光谱型**，而不是加滤波器（省 CPU）：
   O/B 亮而噪（高次谐波多）、G 类接近正弦、K/M 暗而暖（只有前两次谐波）。

3. **不要"一星一振荡器"**。8883 颗星里可见的往往上千颗，
   真给每颗一个振荡器，CPU 会直接烧掉、听起来也是一团糊。
   这里用固定 48 个"声部"（voice），每帧把当前视野里**最亮/最重要**的目标
   分配给音高相同的声部，声部自己平滑漂移到新音高——
   于是进出视野的星是"渐变"的，而不是"啪"地开关。

银河带按设计做成一层低频持续音（55Hz 基频 + 它的八度），
音量随视线中心离银道面的距离变化——正对银河时最厚。

音频通路：`QAudioSink` 推模式 + `numpy` 合成（都是项目已有的依赖，
不引入任何新库；这符合"纯自研 + 单文件 EXE"的路线）。
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from 核心.utils import LOG

# ==============================================================================
# 音高与音色映射（纯函数，可单独测）
# ==============================================================================

#: A 自然小调音阶的半音偏移。
#: ★ 为什么不用五声音阶（原来就是它）：五声音阶只有 5 个音级，
#:   一旦要"换和弦"（把整组音高平移），平移后的音级立刻跑出原集合——
#:   实测 +3 半音后音级变成 [1,3,6,8,10]，出现小二度与三全音，听感发脏。
#:   换成 7 声自然小调后，**按音级平移**的结果始终是本音阶的子集
#:   （音阶每八度有 7 个音，平移 k 级后取模仍落在这 7 个音级里），
#:   于是"和声推进"与"任意组合都协和"这两件事可以同时成立。
#:   代价是集合里含一个三全音（2↔8），但它在慢速氛围乐里是色彩音，
#:   且声部是缓慢漂移的，不会形成刺耳的撞音。
SCALE = (0, 2, 3, 5, 7, 8, 10)

#: 光谱型首字母 → (音区八度, 音阶级数, 谐波振幅包络)
#: 音区：0=最低（约 220Hz 起），4=最高
SPECTRAL_VOICE: Dict[str, Tuple[int, int, Tuple[float, ...]]] = {
    "O": (4, 1, (1.00, 0.60, 0.45, 0.35, 0.28, 0.22, 0.18, 0.14)),
    "B": (3, 2, (1.00, 0.50, 0.34, 0.22, 0.15, 0.10, 0.07)),
    "A": (3, 5, (1.00, 0.40, 0.22, 0.12, 0.07)),
    "F": (2, 4, (1.00, 0.32, 0.14, 0.06)),
    "G": (2, 0, (1.00, 0.25, 0.08)),
    "K": (1, 2, (1.00, 0.42, 0.10)),
    "M": (0, 0, (1.00, 0.55, 0.16)),
    "L": (0, 4, (1.00, 0.50, 0.12)),
    "T": (0, 3, (1.00, 0.45, 0.10)),
    "W": (1, 6, (1.00, 0.30, 0.08)),
    "N": (4, 5, (1.00, 0.70, 0.55, 0.40, 0.30)),
    "D": (1, 1, (1.00, 0.20, 0.06)),
    "S": (2, 5, (1.00, 0.28, 0.09)),
    "C": (1, 3, (1.00, 0.35, 0.09)),
}
#: 认不出的光谱型一律按太阳（G2V）处理
DEFAULT_VOICE = SPECTRAL_VOICE["G"]

#: 主音频率（A3 = 220Hz），音区 0 就落在这里。
#:
#: ★ 为什么从 A2(110Hz) 提到 A3(220Hz)：这是个**听得见 vs 听不见**的问题，
#:   不是审美问题。实测（把音频设备真正读到的字节录下来做 FFT）：
#:   A2 时 20~250Hz 占了总能量的 **50%**，最强分量是 **48~135Hz**，
#:   1kHz 以上只有 3%。而 M 型星约占恒星总数的 76%，它的音区是 0
#:   —— 也就是说**绝大多数声部都在 110Hz 附近**。
#:   手机 / 笔记本的小喇叭对 200Hz 以下几乎没有输出，用户听到的就是
#:   "完全没声音"（而所有程序内部判据都是通过的：波形正常、电平正常）。
#:   提到 A3 后 M 型星落在 220Hz、K 型落在 523Hz，主能量进入可听区。
#:   音区跨度仍是 5 个八度（220Hz~2.8kHz），"O 型刺耳 / M 型低沉"的相对关系不变。
ROOT_HZ = 220.0

#: 银河带低频持续音的基频（A2 = 110Hz；原来是 55Hz，同样放不出来）
MILKY_DRONE_HZ = 110.0


def spectral_key(spectral: str) -> str:
    """从光谱型字符串里取首字母（"G2V" → "G"，"M5Ve" → "M"）。"""
    s = (spectral or "").strip()
    for ch in s:
        if ch.isalpha():
            return ch.upper()
    return "G"


def voice_of(spectral: str) -> Tuple[int, int, Tuple[float, ...]]:
    return SPECTRAL_VOICE.get(spectral_key(spectral), DEFAULT_VOICE)


def pitch_hz(octave: int, degree: int, root_hz: float = ROOT_HZ) -> float:
    """(音区, 音阶级数) → 频率（Hz）。

    级数按 `SCALE` 的音级走：级数超过 7 自动往高八度进位，
    负数也能正确处理（向下取整），所以任何整数都合法。
    """
    deg = int(degree)
    oct_shift, idx = divmod(deg, len(SCALE))
    semitone = SCALE[idx] + 12 * (int(octave) + oct_shift)
    return float(root_hz) * (2.0 ** (semitone / 12.0))


def semitones_to_steps(semitones: float) -> int:
    """把"半音偏移"折算成"音阶级数偏移"（用于和声推进）。

    自然小调每八度 7 级 ≈ 12 半音，所以 k 级 ≈ k·12/7 半音；
    反过来 t 个半音 ≈ round(t·7/12) 级。
    这样"按半音思考、按音级平移"，平移结果必然还在音阶内。
    """
    return int(round(float(semitones) * len(SCALE) / 12.0))


def magnitude_gain(mag: float, ref: float = 1.0, floor: float = 0.04) -> float:
    """星等 → 线性增益（每暗 5 等衰减到 1/100，再夹到 floor 之上）。

    用 2.512 的光度比，但压缩得比真实光度温和得多——
    否则一颗 -1.4 等的天狼会比 6 等的星响 1000 倍，动态范围完全没法听。
    """
    try:
        m = float(mag)
    except (TypeError, ValueError):
        return 0.0
    g = (2.512 ** (-(m - ref) / 5.0))
    return float(min(1.0, max(floor, g)))


def az_to_pan(az: float, center_az: float, span: float = 90.0) -> float:
    """方位角 → 声场位置（-1 全左 … +1 全右）。

    以视线中心方位为正中，span 度对应满偏；超出部分夹住。
    """
    try:
        d = ((float(az) - float(center_az) + 180.0) % 360.0) - 180.0
    except (TypeError, ValueError):
        return 0.0
    return float(max(-1.0, min(1.0, d / max(1e-6, float(span)))))


# ==============================================================================
# 声部与"乐谱"
# ==============================================================================

@dataclass
class SoundNote:
    """一个想要发声的目标（= 一颗星 / 一个天体）。"""

    pitch: float                       # 频率 Hz（= pitch_hz(0, degree)，供测试/参考）
    gain: float = 0.2                  # 0~1
    pan: float = 0.0                   # -1 左 … +1 右
    harmonics: Tuple[float, ...] = (1.0, 0.3)
    key: str = ""                      # 去重用（星表 key）
    #: ★ 音阶级数：和声推进的平移对象（必须是整数）。
    degree: float = -1.0
    #: 桶内微分音偏移（音分）：同音高的多个声部靠它铺开，避免同频叠加。
    #: 换算关系：pitch == pitch_hz(0, degree) * 2**(cents/1200)
    cents: float = 0.0


@dataclass
class Voice:
    """一个实际发声的声部（会平滑漂移到新目标）。

    ★ 声部跟踪的是**音阶级数**（`degree`），不是频率。
      这样"和声推进"就等于给整组声部一起 +k 级，
      平移后必然还落在自然小调的音级集合里（见 PROGRESSION 的注释）。
      频率在每块渲染时由 `pitch_hz(0, degree + hop)` 现算。
    """

    degree: float = -1.0               # 音阶级数（< 0 表示空闲）
    gain: float = 0.0
    pan: float = 0.0
    harmonics: Tuple[float, ...] = (1.0,)
    phase: float = 0.0
    #: 当前正在跟的目标级数（用于就近匹配）
    target: float = 0.0
    #: 上一次实际渲染用的频率（诊断/测试用）
    pitch: float = 0.0
    #: 永久微失谐比例（±0.3% ≈ ±5 音分）：让同音高的多个声部形成合唱宽度，
    #: 也让声音"活"一点。用固定值而不是逐块随机，否则会听到抖动的怪音。
    detune: float = 1.0
    #: 是否低音线声部（低音线由 bass 调度器占用，不走普通的就近匹配）
    is_bass: bool = False


#: 音乐模式：(内部键, 中文名, 说明)
MUSIC_MODES: Tuple[Tuple[str, str, str], ...] = (
    ("starry", "星海", "自然小调缓慢漂移 + 律动铃音 + 和声推进（默认）"),
    ("galaxy", "银河", "低频银道面持续音为主，偶尔点缀高音"),
    ("quiet", "静夜", "极少音符、长衰减，接近环境音"),
)

#: 和声推进：每 16 拍换一次"调性中心"，写法用**半音偏移**（0 / +3 / −4 / −2
#: 分别是 A / C / F / G 三和弦的根音距离），实际平移时由
#: `semitones_to_steps()` 折算成音阶级数——直接按半音乘会跑出音阶。
#: 选这几个是常规的 Am–C–F–G 进行，听感上"有走向"但不俗。
PROGRESSION: Tuple[int, ...] = (0, 3, -4, -2)
#: 每级和声持续多少拍。★ 不等长（6/5/5/4）是刻意的：
#: 等长会让人听出"机械循环"，不等长才有"乐句"的感觉。
PROG_BEATS: Tuple[int, ...] = (6, 5, 5, 4)
#: 低音线：先做成"随和声移动的低音持续音"——每次换和弦时
#: 音频包络重新起音一次，于是听感上有一个很轻的"落地"感。
#:
#: ★ 两个数字都是按"小喇叭能不能放出来"定的，不是审美：
#:   ① 音高不再降八度（见下面 `bp = pitch_hz(0, self._bass_degree)` 那行）。
#:      原来低音线落在主音的一半，实测它是低频能量的**最大单一来源**——
#:      单独占 20~250Hz 的约 30%（和声垫只有 25%）。
#:      而手机/笔记本小喇叭对 200Hz 以下几乎无输出，用户听到的就是"没声音"。
#:   ② 电平从 0.55 降到 0.34：它是**背景**音乐的低音，不该压过和声垫。
BASS_LEVEL = 0.34
BASS_ATTACK = 0.05
BASS_HARMONICS: Tuple[float, ...] = (1.00, 0.30, 0.12, 0.05)

#: 每小节拍数 / 默认速度（BPM）。律动的"呼吸"就来自这里。
BEATS_PER_BAR = 4
DEFAULT_TEMPO = 52.0
#: 铃声（bell）：每 {BELL_EVERY} 拍落一次，起音快、衰减长
BELL_EVERY = 2
BELL_DECAY = 3.2
BELL_ATTACK = 0.010

# ==============================================================================
# 乐器层：有"音头"的旋律声部（钢琴 / 吉他 / 铃）
# ==============================================================================
#: 可选的旋律乐器：(内部键, 中文名, 说明)
#: "pad" 是原来的纯氛围垫（没有音头）；其余三个是按和弦触发的旋律乐器。
INSTRUMENTS: Tuple[Tuple[str, str, str], ...] = (
    ("pad", "氛围垫", "只有和声、没有音头——最安静（原方案）"),
    ("epiano", "钢琴", "模态合成：非谐分音 + 高次先衰减（接近真钢琴）"),
    ("guitar", "钢弦吉他", "Karplus–Strong 拨弦，噪声激励 + 延迟线反馈"),
    ("bell", "钟琴", "非谐 FM 双算子，清亮、尾巴长"),
)

#: 乐器最多同时几个音（超了就抢最弱的声部）
MAX_INSTR_VOICES = 6
#: 每拍触发旋律的概率（按音景不同）；0 表示不触发
INSTRUMENT_DENSITY: Dict[str, float] = {
    "starry": 0.85, "galaxy": 0.45, "quiet": 0.30}
#: 乐器总电平。★ 必须明显高于和声垫的单声部电平：实测垫子
#:   18 个声部叠起来 RMS 有 0.065，而乐器一次只有 ~0.15、还只在拍点上响，
#:   给 0.42 时"换了乐器根本听不出来"（被整体 RMS 0.05 的伴奏盖住）。
INSTRUMENT_LEVEL = 1.25


class _Piano:
    """模态合成钢琴（v1.1.1 重做）。

    ★ 为什么不再用 2 算子 FM：实测 FM 版的**谱重心只有 348 Hz**、
      250 Hz 以上几乎没有能量（频段分布 0/59/28/12/1/0/0/0），
      听感是"闷闷的电子嗡"，不是钢琴。

    参考音色（用户给的《繁星、新生，与你》前 60 秒）实测：

        谱重心 1863 Hz   50% 能量滚降 856 Hz   85% 滚降 2945 Hz
        频段(40-120/120-250/250-500/500-1k/1k-2k/2k-4k/4k-8k/8k-16k)
            = 3% / 8% / 17% / 29% / 21% / 13% / 6% / 4%

      即：低音不轰、中频饱满、高频**缓降但有延伸**。

    所以这里换成钢琴真正的物理模型——**一组衰减的正弦分音（模态）**：

        y(t) = Σ_n a_n · sin(2π·f_n·t) · e^(-t/τ_n) · [1 - e^(-t/att)]

      · f_n = n·f0·√(1+B·n²)   —— 张力弦的**非谐性**（B 很小，约 1e-4）。
        这是钢琴与"纯正弦叠加"最大的区别：高次分音略偏高，
        听感上就是那种"金属味"，缺了它怎么调都像风琴。
      · a_n ∝ 1/n^1.1         —— 谐波能量缓降，对应上面那个"高频缓降"。
      · τ_n 随 n 变短          —— 高次分音先消失（真实钢琴就是这样），
        所以音头亮、随后越来越暖，而不是所有分音一起等比例衰减。
    """

    #: 分音个数。16 个：高音区（880Hz 的 16 次 = 14kHz）也能铺到高频，
    #: 对应参考曲"4k~8k 占 6%、8k~16k 占 4%"那点空气感。
    PARTIALS = 16
    #: 非谐性系数（钢琴低音区约 1e-4，高音区更小）
    INHARMONICITY = 1.2e-4
    #: 谐波幅度权重。★ 不是简单的 1/n：单音实测 250~1000Hz 明显偏弱
    #: （参考曲 250-500 占 17%、500-1k 占 29%）。
    #: 峰值放在第 2 次分音、两侧按幂律衰减。
    HARMONIC_PEAK = 1.4
    HARMONIC_POWER = 1.5
    #: ★ 按**频率**（不是级数）的低通滚降：真实钢琴的响板与琴槌本身有频响上限，
    #:   所以高音区的分音应该更弱。没有这一条时，任何一个音的高次分音都会
    #:   把 2k~4kHz 顶到 31~49%（参考曲只有 13%），听感就是"敲铁、尖、
    #:   像廉价电子琴"——这是"难听"的主因，比相位 bug 更直接。
    #:   实现：每个分音再乘 exp(-(f/KNEE)^SLOPE)。
    SHAPE_KNEE = 2000.0
    SHAPE_SLOPE = 0.90
    #: 基频分音的衰减时间（秒）与"高次分音衰减更快"的指数
    TAU_BASE = 2.2
    TAU_SLOPE = 1.40
    #: 起音时间
    ATTACK = 0.004
    #: 预计算的正弦查表
    _TABLE = None

    @classmethod
    def _table(cls) -> np.ndarray:
        if cls._TABLE is None:
            cls._TABLE = _sin_table()
        return cls._TABLE

    def __init__(self, sr: int) -> None:
        self.sr = sr
        self.t = 0.0
        self.freq = 220.0
        self.vel = 0.0
        self._amps = np.zeros(self.PARTIALS, dtype=np.float64)
        self._taus = np.ones(self.PARTIALS, dtype=np.float64)
        self._ratios = np.ones(self.PARTIALS, dtype=np.float64)
        self._phases = np.zeros(self.PARTIALS, dtype=np.float64)

    def trigger(self, freq: float, vel: float) -> None:
        self.freq = float(freq)
        self.vel = float(vel)
        self.t = 0.0
        self._phases[:] = 0.0
        f0 = self.freq
        for i in range(self.PARTIALS):
            n = i + 1
            # 非谐性：f_n = n·f0·√(1+B·n²)
            self._ratios[i] = n * math.sqrt(1.0 + self.INHARMONICITY * n * n)
            f_n = self._ratios[i] * f0
            # 峰值在第 HARMONIC_PEAK 次分音、两侧按幂律衰减
            amp = (n / self.HARMONIC_PEAK) ** (
                self.HARMONIC_POWER if n > self.HARMONIC_PEAK else -0.55)
            # ★ 再按**频率**做一次响板式滚降（见 SHAPE_KNEE 的说明）：
            #   没有这一条时，任何音的高次分音都会把 2k~4kHz 顶到 31~49%
            #   （参考曲只有 13%），听感是"敲铁、尖、像廉价电子琴"。
            amp *= math.exp(-((f_n / self.SHAPE_KNEE) ** self.SHAPE_SLOPE))
            self._amps[i] = amp
            # 高次分音衰减更快；同时越高越容易超出可听范围，随机收一点
            self._taus[i] = self.TAU_BASE / (n ** self.TAU_SLOPE)
        # 归一：让不同音高的响度接近（否则低音的 10 个分音会明显更响）
        s = float(self._amps.sum())
        if s > 0:
            self._amps /= s
        self._freqs = self._ratios * f0

    def render(self, n: int) -> np.ndarray:
        if self.vel <= 1e-4:
            return np.zeros(n, dtype=np.float64)
        sr = self.sr
        # ★ 必须先把"本块起点"存下来。原来写成
        #       t = self.t + arange(n)/sr
        #       self.t += n/sr
        #       idx = (cur + t*f) % 1.0
        #       self._phases[i] = (cur + self.t*f) % 1.0
        #   注意 `idx` 用的是 **t**（含本块偏移），而存回去的相位用的是
        #   **self.t**（已经加过本块了）——两者差一个块长，
        #   于是**每个音频块边界都会出现一次相位跳变**。
        #   听感就是持续的低频"嗡/喀"底噪，比音色调错更难听，也更难查
        #   （频谱上看不出明显峰，只是一个宽底的脏）。
        #   正确做法：块内用同一个基准 t0，存回去也用同一个。
        t0 = self.t
        t = t0 + np.arange(n, dtype=np.float64) / sr
        self.t = t0 + n / sr
        att = np.minimum(1.0, t / self.ATTACK)
        table = self._table()
        out = np.zeros(n, dtype=np.float64)
        # 块内相位增量（0~1 圈）：用**增量**推进，而不是每块算 t*f 再取模。
        # ★ 为什么必须这样：t*f 到 3 秒、16kHz 分音时已经是 48000，
        #   double 只有 ~15 位有效数字，`(t*f) % 1.0` 的误差会到 1e-4 圈量级
        #   —— 每个块边界的相位都会偏一点，听感是持续的"沙沙/嗡"底噪。
        #   增量式（每块只加 n*f/sr ≈ 0.5~740 圈，再取模）没有这个问题。
        dt = np.arange(n, dtype=np.float64) / sr
        for i in range(self.PARTIALS):
            f = self._freqs[i]
            if f <= 0.0 or f > 16000.0:
                continue
            env = np.exp(-t / self._taus[i])
            if env[-1] < 1e-5:
                continue
            cur = self._phases[i]
            idx = (cur + dt * f) % 1.0
            self._phases[i] = (cur + (n / sr) * f) % 1.0
            wave = table[(idx * SIN_TABLE_SIZE).astype(np.int32)]
            out += wave * (env * self._amps[i])
        out *= att * self.vel
        if float(np.abs(out).max()) < 1e-5 and self.t > 3.0:
            self.vel = 0.0
        return out


class _EVPiano:
    """2 算子 FM 电钢琴（保留作"电钢琴"的另一种风味，暂未启用）。

    实测它的谱重心只有 348 Hz、高频几乎没有能量，太闷；
    正式的音色换成了 `_Piano` 的模态合成。这个类留着以便对比与回退。
    """

    #: 起始调制指数与它的衰减时间常数（秒）
    I0 = 2.5
    I_TAU = 0.35
    #: 振幅包络。★ "快速衰减段"是必须的：只用一个长衰减时，余响电平相对音头
    #:   太高，音符之间会糊在一起（实测"后段/前段能量比"只有 3.0，
    #:   听起来不像键盘而像风琴）。这里做成
    #:   快衰减（0.38s）+ 少量慢尾巴（4.5s、权重 0.22）：
    #:   音头明显突出，同时保留钢琴那种余韵。
    ATTACK = 0.006
    DECAY_FAST = 0.38
    DECAY_SLOW = 4.0
    MIX_SLOW = 0.10

    def __init__(self, sr: int) -> None:
        self.sr = sr
        self.phase = 0.0
        self.mod = 0.0
        self.t = 0.0
        self.freq = 220.0
        self.vel = 0.0

    def trigger(self, freq: float, vel: float) -> None:
        self.freq = float(freq)
        self.vel = float(vel)
        self.phase = 0.0
        self.mod = 0.0
        self.t = 0.0

    def render(self, n: int) -> np.ndarray:
        if self.vel <= 1e-4:
            return np.zeros(n, dtype=np.float64)
        sr = self.sr
        t = self.t + np.arange(n, dtype=np.float64) / sr
        self.t += n / sr
        # 包络
        env = np.exp(-t / self.DECAY_FAST) + self.MIX_SLOW * np.exp(-t / self.DECAY_SLOW)
        att = np.minimum(1.0, t / self.ATTACK)
        env *= att
        # 调制指数
        idx = self.I0 * np.exp(-t / self.I_TAU)
        ph = 2.0 * math.pi * self.freq * t
        y = np.sin(ph + idx * np.sin(ph))
        out = y * env * self.vel
        # 播完就自停（省 CPU）
        if env[-1] < 1e-4:
            self.vel = 0.0
        return out


class _Plucked:
    """Karplus–Strong 拨弦（钢弦吉他味道）。

    做法：延迟线长度 = 一个周期，初始填入噪声，然后每个周期读一点、
    低通一点、按衰减系数写回去。**每样本一次一阶递推**（有状态），
    所以用与混响相同的方式分块向量化。

    ★ 滤波系数不能是常数：它决定"每经过一个周期衰减多少"，
      而周期长度随音高变化——常数会让低音衰减过快、高音几乎不衰减。
      正确做法是把系数折算成"想要的余响时间"（见 `_coeff`）。
    """

    #: 想要的余响时间（秒）——拨弦后大致衰减到听不见的时间
    RING = 2.6

    def __init__(self, sr: int) -> None:
        self.sr = sr
        self.buf = np.zeros(4096, dtype=np.float64)
        self.pos = 0
        self.period = 200
        self.vel = 0.0
        self.amp = 0.0
        self._primed = False

    def _coeff(self) -> float:
        # 一轮反馈里样本数 ≈ 2·P（往返），要让幅度每轮乘以 a^(2P) ≈ e^(-2P/(RING·sr/2))
        # 化简：a = exp(-2 / (RING · sr)) 与 P 无关（写成 P 相关式便于理解）
        decay_per_sample = 2.0 / max(1e-6, self.RING * self.sr)
        a = math.exp(-decay_per_sample)
        return float(min(0.9999, max(0.5, a)))

    def trigger(self, freq: float, vel: float) -> None:
        self.period = max(2, int(round(self.sr / max(20.0, freq))))
        if self.period > len(self.buf):
            self.buf = np.zeros(self.period * 2, dtype=np.float64)
        rng = np.random.default_rng(int(freq * 1000) & 0xFFFF)
        noise = rng.standard_normal(self.period)
        # 轻微低通，去掉最刺的高频 → 更接近钢弦而不是白噪声
        for i in range(1, self.period):
            noise[i] = 0.5 * (noise[i] + noise[i - 1])
        self.buf[:self.period] = noise
        self.buf[self.period:] = 0.0
        self.pos = 0
        self.vel = float(vel)
        self.amp = float(vel)
        self._primed = True

    def render(self, n: int) -> np.ndarray:
        if not self._primed or self.amp <= 1e-4:
            return np.zeros(n, dtype=np.float64)
        out = np.empty(n, dtype=np.float64)
        P = self.period
        a = self._coeff()
        buf = self.buf
        pos = self.pos
        peak = 0.0
        for i in range(n):
            prev = buf[pos - 1]              # 环形：pos-1 即 P 个样本前
            v = 0.5 * (buf[pos] + prev) * a
            buf[pos] = v
            out[i] = v
            if v > peak:
                peak = v
            pos += 1
            if pos >= P:
                pos = 0
        self.pos = pos
        self.amp = peak
        if self.amp <= 1e-4:
            self._primed = False
        # 激励能量归一（否则不同音高响度差异很大）
        return out * (7.0 / math.sqrt(P))


class _Bell:
    """非谐 FM 钟琴：载波 + 2.76 倍频调制（钟类乐器的典型非谐比例）。"""

    RATIO = 2.76
    I0 = 3.2
    I_TAU = 1.2
    #: 同样要有明显的音头：快衰减（0.6s，权重 1.0）+ 长尾（5.5s、权重 0.20）
    ATTACK = 0.003
    DECAY = 0.60
    TAIL = 6.0
    MIX_TAIL = 0.10

    def __init__(self, sr: int) -> None:
        self.sr = sr
        self.t = 0.0
        self.freq = 440.0
        self.vel = 0.0

    def trigger(self, freq: float, vel: float) -> None:
        self.freq = float(freq)
        self.vel = float(vel)
        self.t = 0.0

    def render(self, n: int) -> np.ndarray:
        if self.vel <= 1e-4:
            return np.zeros(n, dtype=np.float64)
        t = self.t + np.arange(n, dtype=np.float64) / self.sr
        self.t += n / self.sr
        env = np.minimum(1.0, t / self.ATTACK) * (
            np.exp(-t / self.DECAY) + self.MIX_TAIL * np.exp(-t / self.TAIL))
        idx = self.I0 * np.exp(-t / self.I_TAU)
        ph = 2.0 * math.pi * self.freq * t
        y = np.sin(ph + idx * np.sin(self.RATIO * ph))
        if env[-1] < 1e-4:
            self.vel = 0.0
        return y * env * self.vel


def make_instrument(kind: str, sr: int):
    if kind == "epiano":
        return _Piano(sr)
    if kind == "guitar":
        return _Plucked(sr)
    if kind == "bell":
        return _Bell(sr)
    return None


class AudioBrain:
    """线程安全的"当前该听什么"参数箱。

    UI 线程只往这里写（`set_notes` / `set_volume` / …），
    音频线程只从这里读。**音频线程绝不碰 QWidget / QPainter**——
    这是本项目在导出功能上已经栽过一次的坑（MP4 闪退）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._notes: List[SoundNote] = []
        self._master: float = 0.35
        self._drone: float = 0.0
        self._drone_hz: float = MILKY_DRONE_HZ
        self._mode: str = "starry"
        self._instrument: str = "pad"
        self._melody: float = 0.42
        self._tempo: float = DEFAULT_TEMPO
        self._progression: int = 0        # 当前和声推进的级数下标
        self._seq: int = 0

    # ---- 写 ----
    def set_notes(self, notes: Sequence[SoundNote]) -> None:
        with self._lock:
            self._notes = list(notes)
            self._seq += 1

    def set_master(self, v: float) -> None:
        with self._lock:
            self._master = float(max(0.0, min(1.0, v)))

    def set_drone(self, v: float) -> None:
        with self._lock:
            self._drone = float(max(0.0, min(1.0, v)))

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self._mode = str(mode)

    def set_tempo(self, bpm: float) -> None:
        with self._lock:
            self._tempo = float(max(20.0, min(160.0, bpm)))

    def set_progression(self, index: int) -> None:
        with self._lock:
            self._progression = int(index) % len(PROGRESSION)

    def set_instrument(self, kind: str) -> None:
        with self._lock:
            self._instrument = str(kind)

    # ---- 读 ----
    def snapshot(self) -> Tuple[List[SoundNote], float, float, str]:
        with self._lock:
            return (list(self._notes), self._master, self._drone, self._mode)

    def music_state(self) -> Tuple[float, int]:
        """律动相关参数（速度、当前和声级数）——单独取，避免每块多解一次包。"""
        with self._lock:
            return (self._tempo, self._progression)

    def instrument(self) -> str:
        with self._lock:
            return self._instrument

    def set_melody(self, v: float) -> None:
        with self._lock:
            self._melody = float(max(0.0, min(1.0, v)))

    def melody(self) -> float:
        with self._lock:
            return self._melody

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq


# ==============================================================================
# 合成器
# ==============================================================================

#: 声部数量。48 个足够撑起"和声"的听感；再多 CPU 收益递减。
MAX_VOICES = 48
#: 声部跟随目标的平滑时间常数（秒）——进出视野是"漂移"而不是"开关"
GLIDE_TAU = 0.9
#: 每块渲染的帧数（44100Hz 下 2048 帧 ≈ 46ms）。
#: ★ 这个值原来是 512（11.6ms），实测在真实拖拽负载下合成一块要 19ms，
#:   **生产速度小于消费速度**，环形缓冲被抽干 → 16.6% 的采样点水位为 0，
#:   听感就是"转一下卡一下"。块加大到 2048 后每块有 46ms 预算，
#:   即使叠加渲染负载也有 2 倍以上余量。（同一轮还把每块的 numpy 调用
#:   从 ~400 次降到 ~110 次、混响改成向量化的分块递推，见下面注释。）
BLOCK_FRAMES = 2048
#: 混响是标量递推（有状态、没法整块向量化），单独用小块处理，
#: 这样它在 2048 帧的大块里也只循环 4 次 × 1024。
REVERB_CHUNK = 1024
#: 正弦查表长度。整块 `np.sin` 是每块最贵的一步，改成"一次算相位 → 查表"。
#: 8192 个点覆盖 2π，量化误差 ~0.0004 rad，听不出来。
SIN_TABLE_SIZE = 8192
TWO_PI = 2.0 * math.pi

_SIN_TABLE: Optional[np.ndarray] = None


def _sin_table() -> np.ndarray:
    """k·sin(2πk/N) 查表（含一个点的冗余，避免 `% N` 后越界）。"""
    global _SIN_TABLE
    if _SIN_TABLE is None:
        k = np.arange(SIN_TABLE_SIZE + 1, dtype=np.float64)
        _SIN_TABLE = np.sin(TWO_PI * k / SIN_TABLE_SIZE)
    return _SIN_TABLE


def _one_pole(x: np.ndarray, a: float, y0: float) -> np.ndarray:
    """向量化一阶低通递推：y[k] = x[k] + a·y[k-1]，y[-1] = y0。

    做法：把它看成**因果指数核的卷积**
        y[k] = Σ_{j≤k} a^(k-j)·x[j]  (+ a^(k+1)·y0)
    核长取到 a^L < 1e-9 即可截断（a=0.35 时 L=21），
    于是用 `np.convolve(x, kernel, 'full')[:n]` 一次算完。

    ★ 为什么不用"乘幂因子 + cumsum"那种更短的写法：
      它要算 `a^-j`，j 稍大就溢出成 inf，数值上不能用。
      卷积写法只有正幂，稳定。
    ★ 为什么不用 scipy.signal.lfilter：本项目刻意不引入 scipy
      （单文件 EXE 的体积与依赖纪律），而且这里 21 点的核开销可以忽略。
    """
    n = len(x)
    if n == 0:
        return x
    kern = _exp_kernel(a)
    y = np.convolve(x, kern, mode="full")[:n]
    if y0:
        k = np.arange(1, n + 1, dtype=np.float64)
        y += (a ** k) * y0
    return y


_EXP_KERNELS: Dict[float, np.ndarray] = {}


def _exp_kernel(a: float) -> np.ndarray:
    """指数衰减核 [a^0, a^1, …]，截到 a^L < 1e-9。"""
    key = round(float(a), 9)
    k = _EXP_KERNELS.get(key)
    if k is None:
        L = 1
        while a ** L > 1e-9 and L < 4096:
            L += 1
        k = a ** np.arange(L, dtype=np.float64)
        _EXP_KERNELS[key] = k
    return k


# ==============================================================================
# 子进程共享内存布局（父进程合成参数 → 子进程 PCM）
# ==============================================================================
#: ★ magic 必须放在**标量区里一个不会被任何参数用到的槽**。
#:   原来写成 `struct.pack_into("<Q", buf, 8, MAGIC)`——字节 8~15 正好是
#:   标量下标 1，也就是 `SH_MASTER`！于是父进程每设一次音量
#:   （`sh[SH_MASTER] = 0.55`）就把魔数砸成 float(0.55) 的位模式
#:   （实测子进程读到 `0x3fe199999999999a`），子进程从此**再也读不到音符**
#:   （notes=0），合成出全零 PCM —— 这就是"这次完全没声了"的根因。
#:   现在改成存放在下标 SH_MAGIC_WORD（= 6），与任何参数都不重叠。
SH_MAGIC_WORD = 6
SHM_MAGIC = 0x534E5831          # "SNX1"
NOTE_BYTES = 16                 # pitch(f4) gain(f4) pan(f4) 谐波签名(u2) 填充
NOTES_BYTES = MAX_VOICES * NOTE_BYTES
#: 标量参数槽（float64 数组里的下标）
SH_LOCK, SH_MASTER, SH_DRONE, SH_MODE, SH_CMD, SH_HEARTBEAT = 0, 1, 2, 3, 4, 5
SH_PCM_R, SH_PCM_W = 8, 9
#: 子进程启动状态字：0=启动中 1=就绪 2=失败。由**子进程写、父进程读**。
#: ★ 为什么不用管道报状态：`subprocess.Popen` 默认 `close_fds=True`，
#:   子进程**根本继承不到**额外的管道 fd（实测子进程拿到的是 -1，
#:   于是它退回 stderr——而 `--windowed` 打包的 EXE 的 stderr 是空的，
#:   父进程永远等不到 "OK"，把好好的子进程判成启动失败）。
#:   共享内存没有这个问题：句柄按名字打开，与 fd 继承无关。
#:   状态字用整数值存进 float64 槽，精确无歧义。
SH_STATUS_WORD = 10
#: 父进程心跳计数：父进程每 push() 一次就 +1。子进程 8 秒收不到新心跳
#: 就自行退出（父进程被强杀时，管道可能来不及感知 EOF）。
SH_HEARTBEAT_PARENT = 11
#: 旋律乐器（INSTRUMENTS 的下标）
SH_INSTRUMENT = 12
#: 速度 BPM
SH_TEMPO = 13
#: 旋律密度（0~1）
SH_INST_DENSITY = 14
#: ★ 旋律突出度（0~1）：0 = 只有伴奏，1 = 乐器大幅靠前，默认偏伴奏。
#:   为什么需要它：我原来用"乐器占混音能量 36~55%"当判据，结果垫子被压到
#:   整体只有原来一半响，听感变成"只有钢琴、没有背景音乐"。
#:   能量占比不等于听感占比——乐器是**有音头的瞬态**，感知上比同样能量的
#:   稳态垫子响得多。所以留一个可调旋钮，默认偏伴奏。
SH_MELODY = 15
STATUS_STARTING, STATUS_READY, STATUS_FAILED = 0.0, 1.0, 2.0
SCALARS = 16
NOTES_OFFSET = SCALARS * 8                    # 128
PCM_OFFSET = 4096                             # 4KB 处开始放 PCM
#: PCM 环形缓冲字节数（约 3 秒 @44.1kHz 立体声 16bit）
PCM_BYTES = 44100 * 4 * 3
SHM_WORDS = (PCM_OFFSET + PCM_BYTES) // 8

#: 谐波签名表：音符里只传一个下标，避免每 100ms 往共享内存里搬元组。
#: ★ 每个元素必须是**纯谐波元组** Tuple[float, ...]。
#:   这里连错两次，两次都表现为"完全没声"，记下来：
#:     ① 写成 `sorted(set(SPECTRAL_VOICE.values()) | {DEFAULT_VOICE}, key=len)`——
#:        语义上还过得去，但很脆；
#:     ② 改成 `tuple(SPECTRAL_VOICE.values())` 后**漏了取第三个元素**：
#:        SPECTRAL_VOICE 的值是 `(音区, 级数, 谐波元组)` 三元组，
#:        于是 `for amp in v.harmonics` 拿到的是整数/元组，
#:        在 `if amp <= 1e-4` 处抛 TypeError。
#:     异常都被子进程的 except 吞掉 → 写出全零 PCM → 用户听到静音。
#:   现在明确解构出第三个元素。
_HARM_SIGS: Tuple[Tuple[float, ...], ...] = tuple(
    dict.fromkeys([v[2] for v in SPECTRAL_VOICE.values()] + [DEFAULT_VOICE[2]]))
_MODE_NAMES: Tuple[str, ...] = tuple(m[0] for m in MUSIC_MODES)
_INST_NAMES: Tuple[str, ...] = tuple(i[0] for i in INSTRUMENTS)

__all__ = [
    "SoundNote", "Voice", "AudioBrain", "SonifyEngine", "SonifyHost",
    "MUSIC_MODES", "SCALE", "SPECTRAL_VOICE", "ROOT_HZ",
    "spectral_key", "voice_of", "pitch_hz", "magnitude_gain", "az_to_pan",
]


class SonifyEngine:
    """把 AudioBrain 里的目标合成为音频，推到 QAudioSink。

    生命周期：`start()` → （反复 `set_notes`）→ `stop()`。
    任何一步失败都**不抛给调用方**：音频是锦上添花的功能，
    它坏了不能让星图跟着崩。失败信息进 LOG 并把 `available` 置 False。
    """

    def __init__(self, sample_rate: int = 44100) -> None:
        self.sample_rate = int(sample_rate)
        self.brain = AudioBrain()
        self.available = False
        self.reason = ""
        self._sink = None
        self._source = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._drone_phase = 0.0
        self._lfo_phase = 0.0
        self._rev_a = np.zeros(8192, dtype=np.float64)
        self._rev_b = np.zeros(12288, dtype=np.float64)
        self._rev_ia = 0
        self._rev_ib = 0
        self._rev_lp_a = 0.0
        self._rev_lp_b = 0.0
        self._step_vec: Optional[np.ndarray] = None
        self._fps_ms = 0.0
        # 律动状态：拍相位、铃声包络与其相位/音高
        self._beat_phase = 0.0
        self._bell_amp = 0.0
        self._bell_phase = 0.0
        self._bell_hz = 440.0
        self._bell_step = 0
        self._prog_beats = 0
        self._prog_idx = 0
        self._bass_degree = 0.0
        self._bass_gain = 0.0
        self._bass_phase = 0.0
        # 旋律乐器：当前乐器 + 6 个声部的池（按"最弱优先"抢占）
        self._inst_kind = "pad"
        self._inst = None
        self._inst_voices: List = []
        self._inst_beat = 0.0
        self._inst_idx = 0
        self._inst_rng = None
        self._inst_backing = False
        self._voices: List[Voice] = []
        for i in range(MAX_VOICES):
            v = Voice(degree=-1.0)
            # 每个声部一个固定的微失谐；低音线单独一个（不参与合唱抖动）
            k = (i % 7) - 3
            v.detune = 1.0 + k * 0.0009
            if i == MAX_VOICES - 1:
                v.is_bass = True
                v.detune = 1.0
            self._voices.append(v)
        # 子进程模式用：共享内存句柄、句柄号、管道读端、音符暂存
        self._child_shm = None
        self._child_handle = 0
        self._child_notes: List[Optional[SoundNote]] = [None] * MAX_VOICES
        self._shm_words = SHM_WORDS
        self._pipe_fd = -1
        self._status_r = -1
        self._status_w = -1
        self._child_sh = None

    # ------------------------------------------------------------------ 启动
    def start(self) -> bool:
        """启动音频线程。返回是否成功（失败时 available=False 且有 reason）。"""
        if self._running:
            return self.available
        # ★ GIL 切换间隔从默认 5ms 降到 1ms。
        #   音频线程的每块计算只要 1ms（向量化之后），但默认间隔下
        #   它可能要等主线程画完一整帧（实测 70~105ms）才轮到一次：
        #   声卡在这中间就把缓冲抽干了。缩短间隔让音频线程能更频繁地
        #   拿到很短的时间片，代价只是切换开销略增（对交互体验无感）。
        try:
            if sys.getswitchinterval() > 0.001:
                sys.setswitchinterval(0.001)
        except Exception:
            pass
        try:
            from PySide6.QtCore import QCoreApplication, QIODevice
            from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
        except Exception as exc:
            self.reason = f"QtMultimedia 不可用：{exc}"
            LOG.warn(f"星图声化未启用：{self.reason}")
            return False
        if QCoreApplication.instance() is None:
            self.reason = "没有 QCoreApplication 实例"
            return False
        dev = QMediaDevices.defaultAudioOutput()
        if dev is None or dev.isNull():
            self.reason = "系统没有可用的音频输出设备"
            LOG.warn(f"星图声化未启用：{self.reason}")
            return False

        fmt = QAudioFormat()
        fmt.setSampleRate(self.sample_rate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self._bytes_per_frame = 4
        if not dev.isFormatSupported(fmt):
            fmt = dev.preferredFormat()
            self._bytes_per_frame = max(1, fmt.bytesPerFrame())
            LOG.info(f"星图声化改用设备首选格式：{fmt.sampleRate()}Hz "
                     f"{fmt.channelCount()}ch {fmt.sampleFormat()}")
        self.sample_rate = int(fmt.sampleRate())

        source = _PushSource(self, fmt)
        source.open(QIODevice.ReadOnly)
        # 不要手工 setBufferSize()：实测在某些后端上会把 sink 压成
        # "只消费了几十毫秒就再也不取数据"的状态（processedUSecs 停在 58ms）。
        # 让 Qt 用设备默认缓冲更稳；我们自己的环形缓冲已经吃掉了抖动。
        sink = QAudioSink(dev, fmt)
        self._sink = sink
        self._source = source
        self._running = True
        self._thread = threading.Thread(target=self._render_loop, name="sonify",
                                        daemon=True)
        self._thread.start()
        sink.start(source)
        self.available = True
        self.reason = ""
        LOG.info(f"星图声化已启动：{self.sample_rate}Hz 立体声，设备 {dev.description()}")
        return True

    def stop(self) -> None:
        self._running = False
        try:
            if self._sink is not None:
                self._sink.stop()
        except Exception:
            pass
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        try:
            if self._source is not None:
                self._source.close()
        except Exception:
            pass
        self._sink = None
        self._source = None
        self.available = False

    # ------------------------------------------------------------------ 渲染
    def _render_loop(self) -> None:
        """音频线程主循环：按块节拍合成，把 PCM 塞进环形缓冲。

        音频线程只碰 `AudioBrain`（有锁）和 `_PushSource.feed`（有锁），
        **绝不碰 QWidget / QPainter**——真机上 Qt 对"非 GUI 线程操作控件"
        是进程级崩溃，Python 的 try/except 拦不住（导出 MP4 那次已经栽过）。

        节拍 = 块长的一半：留一倍余量，某一块算慢了下一块也不会迟到太多。
        """
        block_s = BLOCK_FRAMES / max(1.0, float(self.sample_rate))
        interval = max(0.005, block_s * 0.5)
        next_t = time.perf_counter()
        while self._running:
            try:
                pcm = self.render_block()
                if self._source is not None:
                    self._source.feed(pcm)
            except Exception as exc:            # pragma: no cover - 防御性
                LOG.error("星图声化渲染失败", exc)
                self._running = False
                break
            next_t += interval
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                # 落后了就重新对齐，别越欠越多
                next_t = time.perf_counter()

    def render_block(self) -> np.ndarray:
        """合成一块音频（也供离线自检直接调用）。返回 Int16 立体声字节。

        性能上做了三件事（都是实测出来的，不是猜的）：
          1. 正弦查表：整块 `np.sin(f*t + phase)` 是每块最贵的一步，
             改成"算一次相位索引 → 查表"；
          2. 每块只算一次相位步进向量并复用；
          3. 混响从"每块一次标量循环"改成向量化的分块梳状递推。

        "变化"来自三层叠加（对应"太单调"的反馈）：
          · 和声推进：每 16 拍把整组音高做一次半音平移（PROGRESSION）
          · 律动铃声：每 2 拍落一个起音快、衰减长的铃音
          · 呼吸：整体音量随拍点做 ±6% 的起伏
        """
        notes, master, drone, mode = self.brain.snapshot()
        tempo, prog = self.brain.music_state()
        instrument = self.brain.instrument()
        self._update_voices(notes)
        n = BLOCK_FRAMES
        sr = self.sample_rate
        step = self._step_vec
        if step is None or len(step) != n:
            step = np.arange(n, dtype=np.float64) / float(sr)
            self._step_vec = step
        left = np.zeros(n, dtype=np.float64)
        right = np.zeros(n, dtype=np.float64)
        table = _sin_table()
        hop = (semitones_to_steps(PROGRESSION[prog % len(PROGRESSION)])
               if PROGRESSION and self._prog_idx < len(PROGRESSION) else 0)

        for v in self._voices:
            if v.is_bass:
                continue                    # 低音线单独渲染（见下）
            # 声部记的是**音阶级数**（不是频率），和声推进就是整组 +hop 级。
            # 这样平移后必然还在自然小调里；用频率乘半音比会跑出音阶。
            pit = pitch_hz(0, v.degree + hop) * v.detune if v.degree >= 0 else 0.0
            if v.gain <= 1e-4 or pit <= 0.0:
                if pit > 0.0:
                    v.phase = (v.phase + 2.0 * math.pi * pit * n / sr) % TWO_PI
                continue
            cur = v.phase / TWO_PI
            idx = (cur + step * pit) % 1.0
            v.phase = (cur + n / sr * pit) % 1.0 * TWO_PI
            base = (idx * SIN_TABLE_SIZE).astype(np.int32)
            wave = np.zeros(n, dtype=np.float64)
            for h, amp in enumerate(v.harmonics[:8], start=1):
                if amp <= 1e-4:
                    continue
                # ★ 必须按奈奎斯特过滤。这里的谐波是"把基频的查表索引乘 h"
                #   （`base * h`），只有在 f0·h < sr/2 时才等价于"第 h 次谐波"。
                #   一旦越过奈奎斯特，它不会消失，而是**折回成低频**——
                #   实测 21720Hz 这种折回分量加上 44~133Hz 的杂散，
                #   听感是"嗡"的脏底噪，还会白白吃掉动态余量。
                #   （pad 的基频最高到 1.4kHz，8 次谐波就是 11kHz，
                #     叠加和声推进的 +级数 之后很容易越界。）
                if pit * h >= sr * 0.5:
                    continue
                if h == 1:
                    np.add(wave, amp * table[base], out=wave)
                else:
                    np.add(wave, amp * table[(base * h) % SIN_TABLE_SIZE], out=wave)
            ang = (v.pan + 1.0) * 0.25 * math.pi       # 恒功率声像
            g = v.gain
            np.add(left, wave * (g * math.cos(ang)), out=left)
            np.add(right, wave * (g * math.sin(ang)), out=right)

        # ---- 和声推进 + 低音线 ----
        #   ★ 推进放在这里（合成器内部）而不是子进程的定时循环里：
        #     放在循环里的话，"单独调用 render_block 做离线自检"永远不会推进，
        #     实测就踩到了这个坑——离线跑 40 秒，和声一动不动，
        #     于是"太单调"这个问题在自己的自检里完全看不出来。
        #   ★ 另外：低音线与铃声都是"和声声部"，**没有乐谱时就该停**。
        #     它们原来只依赖拍点与和声级数，与乐谱无关，
        #     于是清空乐谱后仍在稳定发声（实测 RMS 恒为 0.063、包络一条平线），
        #     把旋律乐器整段埋在底下——这正是"加了钢琴却听不出来"的原因。
        has_harmony = bool(self.brain.snapshot()[0])
        # ★ 伴奏与乐器的相对配比由 melody 决定（0 = 只有伴奏，1 = 乐器大幅靠前）。
        #   默认 0.42 偏伴奏。**为什么需要这个旋钮**：我原来用"乐器占混音能量
        #   36~55%"当判据，把垫子压到 0.55、乐器给到 1.25，结果整体音量只剩
        #   纯垫子的一半，听感变成"只有钢琴、没有背景音乐"。
        #   能量占比不等于听感占比——乐器是**有音头的瞬态**，感知上比同样
        #   能量的稳态垫子响得多。所以配比必须可调，且默认偏伴奏。
        mel = float(self.brain.melody())
        if instrument == "pad":
            backing = 1.0
            inst_scale = 1.0
        else:
            backing = 0.94 - 0.32 * mel          # mel=0.42 -> 0.81
            inst_scale = 0.45 + 1.05 * mel       # mel=0.42 -> 0.89
        rate = max(20.0, float(tempo)) / 60.0        # 拍/秒
        self._beat_phase += rate * n / sr
        self._prog_beats += rate * n / sr
        bar = PROG_BEATS[self._prog_idx % len(PROG_BEATS)]
        if self._prog_beats >= bar:
            self._prog_beats -= bar
            self._prog_idx = (self._prog_idx + 1) % len(PROGRESSION)
            self.brain.set_progression(self._prog_idx)
            # 换和弦时低音线重新起音：听感上有一个很轻的"落地"
            self._bass_gain = 0.0
        if has_harmony and self._bass_gain < BASS_LEVEL:
            self._bass_gain = min(
                BASS_LEVEL, self._bass_gain + BASS_LEVEL * (n / sr) / BASS_ATTACK)
        elif not has_harmony:
            # 没有乐谱 → 低音线淡出（它是和声声部，不是常驻嗡鸣）
            self._bass_gain *= 1.0 - min(0.99, (n / sr) / 0.35)
        # 低音线：根音与五度交替（每级和声换一次），整体跟着和声推进平移
        bass_hop = (semitones_to_steps(PROGRESSION[self._prog_idx
                                                  % len(PROGRESSION)])
                    if PROGRESSION else 0)
        self._bass_degree = bass_hop + (0 if self._prog_idx % 2 == 0 else 4)
        if self._bass_gain > 1e-4:
            # ★ 不再降八度：原来这里是 `* 0.5`（落在主音的一半，即 110Hz），
            #   而 110Hz 在小喇叭上基本放不出来。实测低音线是低频能量的
            #   最大单一来源（单独占 20~250Hz 的约 30%）。现在它落在主音上，
            #   与和声垫同八度但只占节奏重音，听感是"低音"而不是"次声"。
            bp = pitch_hz(0, self._bass_degree)
            cur = self._bass_phase / TWO_PI
            idx = (cur + step * bp) % 1.0
            self._bass_phase = (cur + n / sr * bp) % 1.0 * TWO_PI
            base_i = (idx * SIN_TABLE_SIZE).astype(np.int32)
            b = table[base_i].copy()
            np.multiply(b, BASS_HARMONICS[0] if BASS_HARMONICS else 1.0, out=b)
            for h in range(2, len(BASS_HARMONICS) + 1):
                amp = BASS_HARMONICS[h - 1]
                if amp > 1e-4:
                    np.add(b, amp * table[((idx * h) % 1.0
                                           * SIN_TABLE_SIZE).astype(np.int32)],
                           out=b)
            np.add(left, b * self._bass_gain * backing, out=left)
            np.add(right, b * self._bass_gain * 0.85 * backing, out=right)
        # 呼吸：±6% 的音量起伏，相位跟着拍点
        breathe = 1.0 + 0.06 * math.sin(TWO_PI * self._beat_phase)
        np.multiply(left, breathe, out=left)
        np.multiply(right, breathe, out=right)

        # 铃声：跨过整拍（每 BELL_EVERY 拍）就触发一次；同一块里可能触发多个
        # ★ 同样受 has_harmony 约束：乐谱清空后铃声也要停，
        #   否则它会一直以稳定节奏敲下去，盖住旋律乐器。
        while has_harmony and self._beat_phase >= BELL_EVERY:
            self._beat_phase -= BELL_EVERY
            self._bell_amp = 0.16
            self._bell_phase = 0.0
            # 铃音音高：取当前和声里的一个音（保证协和），
            # **并且跟声部一样乘上和声推进的半音比例**——
            # 少了这个 hop_ratio 就会"推进了但铃声不动"，听感上像卡带。
            self._bell_hz = pitch_hz(2, (prog + self._bell_step) % len(SCALE) + hop)
            self._bell_step += 1
        if self._bell_amp > 1e-4:
            # 起音段（10ms）线性拉起，之后指数衰减
            env = np.empty(n, dtype=np.float64)
            k = int(BELL_ATTACK * sr)
            if k > n:
                k = n
            env[:k] = np.linspace(0.0, 1.0, k, endpoint=False)
            if k < n:
                t_tail = (np.arange(n - k, dtype=np.float64)) / sr
                env[k:] = np.exp(-t_tail / BELL_DECAY)
            env *= self._bell_amp
            cur = self._bell_phase / TWO_PI
            idx = (cur + step * self._bell_hz) % 1.0
            self._bell_phase = (cur + n / sr * self._bell_hz) % 1.0 * TWO_PI
            b = table[(idx * SIN_TABLE_SIZE).astype(np.int32)]
            _ = mode
            np.add(left, b * env * backing, out=left)
            np.add(right, b * env * backing, out=right)
            # 衰减量按块长算，避免随块大小改变听感
            self._bell_amp *= math.exp(-(n / sr) / BELL_DECAY)

        # 银河带低频持续音：55Hz + 八度 + 一个很慢的音量起伏
        if drone > 1e-4:
            lfo = 0.72 + 0.28 * math.sin(self._lfo_phase)
            dlfo = TWO_PI * 0.07 * n / sr
            self._lfo_phase = (self._lfo_phase + dlfo) % TWO_PI
            cur = self._drone_phase / TWO_PI
            i1 = (cur + step * MILKY_DRONE_HZ) % 1.0
            i2 = (cur + step * MILKY_DRONE_HZ * 2.0) % 1.0
            self._drone_phase = (cur + n / sr * MILKY_DRONE_HZ) % 1.0 * TWO_PI
            d = table[(i1 * SIN_TABLE_SIZE).astype(np.int32)]
            d = d + 0.45 * table[(i2 * SIN_TABLE_SIZE).astype(np.int32)]
            d *= (drone * 0.16 * lfo)
            np.add(left, d, out=left)
            np.add(right, d, out=right)

        # ---- 旋律乐器（钢琴 / 吉他 / 钟琴）----
        #   触发在拍点上，音高从当前和声里取（根音/三度/五度/八度 + 推进平移），
        #   所以旋律永远跟着和弦走。`pad` 就是不插乐器，只有和声。
        self._sync_instrument(instrument)
        if instrument != "pad":
            density = INSTRUMENT_DENSITY.get(mode, 0.6)
            self._inst_beat += rate * n / sr
            while self._inst_beat >= 1.0:
                self._inst_beat -= 1.0
                self._trigger_instrument(bass_hop, density)
            il, ir = self._render_instrument(n)
            np.add(left, il * inst_scale, out=left)
            np.add(right, ir * inst_scale, out=right)
            # ★ 伴奏必须给乐器让位。实测只把和声垫乘 0.62 是不够的：
            #   低音线（BASS_LEVEL 0.55）+ 铃声 + 垫子叠起来的 RMS 有 0.079，
            #   而单个乐器的 RMS 只有 0.05 左右——乐器**整段被埋在底下**，
            #   听感上就是"换了乐器却听不出区别"。所以三路伴奏一起压：
            #   垫 → 0.30；低音线与铃声在各自渲染处再乘 0.45 / 0.5。
            np.multiply(left, backing, out=left)
            np.multiply(right, backing, out=right)
            self._inst_backing = True
        else:
            self._inst_backing = False

        # 简易混响：两条低通反馈延迟线（给"空灵"感，成本几乎为零）
        left = self._reverb(left, self._rev_a, "a")
        right = self._reverb(right, self._rev_b, "b")

        # ★ 自动响度补偿：插入旋律乐器会**替换掉一部分伴奏**（垫子被压、
        #   乐器加进来），实测总响度会掉到纯垫子的 74%~53%（melody 越大掉越多）。
        #   换乐器就变轻/变闷，听感上像"坏了"。这里按"伴奏还剩多少"做一次
        #   解析补偿：melody=0 时补偿系数恰好 1.0（与纯垫子完全一致），
        #   乐器越靠前补得越多。上限 1.6 避免把峰值推过头。
        comp = 1.0
        if instrument != "pad" and backing > 0.05:
            comp = min(1.6, 1.0 / (backing ** 0.80))
        left = np.tanh(left * 0.30) * master * comp
        right = np.tanh(right * 0.30) * master * comp
        stereo = np.empty(n * 2, dtype=np.float32)
        stereo[0::2] = left
        stereo[1::2] = right
        pcm = np.clip(stereo * 32767.0, -32768.0, 32767.0).astype("<i2")
        return pcm.tobytes()

    # ------------------------------------------------------------------ 乐器
    def _sync_instrument(self, kind: str) -> None:
        """乐器换了就重建声部池（重建成本只在这时付一次）。"""
        if kind == self._inst_kind and self._inst is not None:
            return
        self._inst_kind = kind
        self._inst = make_instrument(kind, self.sample_rate)
        if self._inst is None:
            self._inst_voices = []
            return
        self._inst_voices = [make_instrument(kind, self.sample_rate)
                             for _ in range(MAX_INSTR_VOICES)]
        import random as _r
        self._inst_rng = _r.Random(20260922)
        # ★ 这里必须是 0 或负数，不能是"很大的数"。
        #   触发循环写的是 `while self._inst_beat >= 1.0: self._inst_beat -= 1.0`，
        #   塞一个 1e9 进去会空转十亿次、把音频线程彻底卡死
        #   （实测：换一次乐器就再也没声音了）。
        self._inst_beat = 0.0
        self._inst_idx = 0

    def _trigger_instrument(self, hop: int, density: float) -> None:
        """在拍点上触发一个旋律音（按密度概率）。

        音高从**当前和声**里取：根音 / 三度 / 五度 / 八度，
        再叠上和声推进的级数平移——所以旋律永远跟着和弦走，不会跑调。
        """
        if not self._inst_voices or self._inst_rng is None:
            return
        if self._inst_rng.random() > density:
            return
        offsets = (0, 2, 4, 7, 9, 4, 2, 0)         # 音阶内的常见走向
        step = hop + offsets[self._inst_idx % len(offsets)]
        self._inst_idx += 1
        oct_shift = 1 if self._inst_rng.random() < 0.45 else 0
        freq = pitch_hz(oct_shift, step)
        if freq < 90.0 or freq > 2000.0:
            return
        vel = 0.5 + 0.5 * self._inst_rng.random()
        # 抢占：优先用空闲声部，没有就抢最弱/最旧的那个
        slot = None
        for v in self._inst_voices:
            if getattr(v, "vel", 1.0) <= 1e-3:
                slot = v
                break
        if slot is None:
            slot = min(self._inst_voices, key=lambda v: getattr(v, "vel", 1.0))
        slot.trigger(freq, vel * INSTRUMENT_LEVEL)

    def _render_instrument(self, n: int) -> Tuple[np.ndarray, np.ndarray]:
        """把乐器声部渲染成 (左, 右)。乐器本身是单声道，按音高给一点声像。"""
        if not self._inst_voices:
            return (np.zeros(n, dtype=np.float64), np.zeros(n, dtype=np.float64))
        left = np.zeros(n, dtype=np.float64)
        right = np.zeros(n, dtype=np.float64)
        for i, v in enumerate(self._inst_voices):
            y = v.render(n)
            if not y.any():
                continue
            # 声像：按声部序号在 -0.5~+0.5 之间铺开，避免全部挤在正中
            pan = ((i % 3) - 1) * 0.35
            ang = (pan + 1.0) * 0.25 * math.pi
            np.add(left, y * math.cos(ang), out=left)
            np.add(right, y * math.sin(ang), out=right)
        return left, right

    def _reverb(self, x: np.ndarray, buf: np.ndarray, which: str) -> np.ndarray:
        """一条低通反馈延迟线（梳状滤波），**全程向量化**。

        数学：y[n] = x[n] + d[n]，d[n] = fb · lp(d[n-D] + damp·d[n-D-1])，
        其中 D = len(buf)。因为 d[n] 只依赖 d[n-D]，所以可以**按块递推**：

            d[s:e] = fb * (lp(d[s-D:e-D]) + damp * lp(d[s-D-1:e-D-1]))

        只要每段的长度 ≤ D，段内就没有依赖，`np.add.at` 一次算完。

        ★ 这一条是"旋转星图时音乐卡顿"的真正修复点。
          原来的实现是纯 Python 标量循环（2048 次/块/声道），
          **它在整个循环期间持有 GIL 且不让出**。空载实测 2.7ms/块，
          但叠上主线程 70ms 的重绘负载后涨到 **49.8ms/块**（预算 46.4ms），
          生产速度再次小于消费速度、缓冲被抽干。
          改成向量化后，计算发生在 numpy 的 C 循环里（会主动让出 GIL），
          对主线程负载的敏感度大幅下降。
        """
        out = x.copy()
        n = len(x)
        D = len(buf)
        fb = 0.32              # 反馈量：0.42 时尾巴会把"关掉"拖到 3 秒以上
        damp = 0.35            # 低通阻尼
        # 段长：取"核长"与延迟线长度的较小值，保证段内近似成立且开销小。
        # 实测该段长下 `_one_pole` 的截断误差可忽略（核尾 < 1e-9）。
        seg = int(min(n, D, 256))
        lp = self._rev_lp_a if which == "a" else self._rev_lp_b
        idx = self._rev_ia if which == "a" else self._rev_ib
        for s in range(0, n, seg):
            e = min(n, s + seg)
            # 取延迟线里的对应片段（环形索引，可能跨界 → 分两段拼接）
            i0 = idx
            i1 = i0 + (e - s)
            if i1 <= D:
                d = buf[i0:i1].copy()
            else:
                d = np.concatenate((buf[i0:], buf[:i1 - D]))
            # 一阶低通（向量化的指数加权累计和）+ 反馈
            #   y[k] = x[k] + damp·y[k-1]  →  批量公式见 _one_pole
            fil = _one_pole(d, damp, lp)
            lp = float(fil[-1]) if len(fil) else lp
            new = fil * fb
            buf[i0:i0 + len(new)] = new
            out[s:e] += d * 0.55
            idx = (idx + len(new)) % D
        if which == "a":
            self._rev_lp_a = float(lp)
            self._rev_ia = idx
        else:
            self._rev_lp_b = float(lp)
            self._rev_ib = idx
        return out

    def _update_voices(self, notes: Sequence[SoundNote]) -> None:
        """把目标音符分配给声部（按音级就近匹配），并做平滑漂移。

        ★ 淡出判据不能用 `v.target <= 0.0` 当"没有目标"的代理。
          它一旦被赋值就恒为正、下一块还要拿它做就近匹配，
          所以乐谱撤走时它永远不会变回 0——结果是**声部永不淡出**，
          实测表现为"乐谱清空后 3 秒仍以满音量持续发声"。
          正确做法是每块用一个 `claimed` 标志记录"这一块有没有被认领"。
        """
        targets = sorted(notes, key=lambda s: -s.gain)[:MAX_VOICES]
        claimed = [False] * len(self._voices)
        used = [False] * len(targets)
        # 1) 已有声部优先接手音级最接近的目标
        for vi, v in enumerate(self._voices):
            if v.target <= 0.0:
                continue
            best, best_d = -1, 1e9
            for i, s in enumerate(targets):
                if used[i]:
                    continue
                d = abs(s.degree - v.target) if s.degree >= 0 else 1e9
                if d < best_d:
                    best, best_d = i, d
            if best >= 0 and best_d < 4.0:      # 4 级以内算"同一个和声区"
                used[best] = True
                claimed[vi] = True
                self._aim(v, targets[best])
        # 2) 剩下的目标交给空闲声部
        for i, s in enumerate(targets):
            if used[i]:
                continue
            for vi, v in enumerate(self._voices):
                if claimed[vi] or v.target > 0.0:
                    continue
                claimed[vi] = True
                self._aim(v, s)
                break
        # 3) 这一块没被认领的声部淡出（用与起音同一个时间常数，听感才是"渐隐"）
        fall = min(0.99, (BLOCK_FRAMES / max(1, self.sample_rate)) / GLIDE_TAU * 8.0)
        for vi, v in enumerate(self._voices):
            if claimed[vi]:
                continue
            if v.gain > 1e-4:
                v.gain *= (1.0 - fall)
            else:
                v.gain = 0.0
                v.target = 0.0          # 彻底静音后才允许回收给新目标

    # ------------------------------------------------------------------ 子进程
    def run_audio_process(self) -> int:
        """**子进程入口**：把 PCM 写进共享环形缓冲，由父进程播放。

        为什么必须单开一个进程（实测数据）：
            同一个进程里，主线程画一帧星图要 70~114ms，这段时间 GIL 基本被它
            占着。音频线程虽然每块只要约 1ms，却常常要等 40ms 才轮到一次——
            实测**生产速率只有 0.81×**（6.10 秒墙上时间只产出 4.97 秒音频），
            缓冲被反复抽干，听感就是"转一下卡一下"。
            把合成放进子进程后，它有**自己的 GIL 和自己的解释器**，
            父进程再卡也影响不到出声。父进程只负责写 48 条音符参数（每 100ms），
            以及把 `QAudioSink` 当消费者。

        与父进程的全部通信都在一块共享内存里：
            [0]        锁 / ready 标志
            [1..6]     标量参数（音量、银河层、模式、心跳、代次、保留）
            [16..783]  音符（48 × 16 字节：pitch/gain/pan/谐波签名）
            [4096..]   PCM 环形缓冲
        另外有一条匿名管道用于**停止**信号（父进程一关就断开，
        子进程读到 EOF 自行退出，不会留下孤儿进程）。
        """
        import struct

        def _say(text: str) -> None:
            """向父进程报状态。

            ★ 主通道是**共享内存里的状态字**，不是管道也不是 print：
              · `print` 不可靠：`--windowed` 打包的 EXE 里
                `sys.stdout`/`sys.stderr` 是 None，输出全被丢掉；
              · 管道也不可靠：`subprocess.Popen` 默认 `close_fds=True`，
                子进程继承不到额外的管道 fd（实测拿到 -1）。
              所以：状态字负责"成功/失败"这个二值信号（父进程轮询），
              出错时的文字仍然尽力往管道/stderr 写一份，方便排查。
            """
            try:
                if self._child_sh is not None:
                    self._child_sh[SH_STATUS_WORD] = (
                        STATUS_FAILED if text.startswith("ERR") else STATUS_READY)
            except Exception:
                pass
            for target in (self._status_w, 2):
                if target is None or target < 0:
                    continue
                try:
                    os.write(target, (text + "\n").encode("utf-8"))
                    break
                except Exception:
                    continue
            # 诊断：`_SNX_DEBUG_LOG` 指向一个文件时，把子进程的状态与调试信息
            # 追加进去。排查"子进程为什么没出声"这种问题时，没有它就只能猜
            # （曾经因为把子进程 stderr 扔进 DEVNULL 而多花了一整轮）。
            dbg = os.environ.get("_SNX_DEBUG_LOG")
            if dbg:
                try:
                    with open(dbg, "a", encoding="utf-8") as fp:
                        fp.write("[child] %s\n" % text)
                except Exception:
                    pass

        try:
            from PySide6.QtCore import (QCoreApplication, QIODevice, QTimer,
                                        QSocketNotifier, QObject)
            from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
        except Exception as exc:
            _say("ERR child no-qtmultimedia %s" % exc)
            return 3

        shm = self._child_shm
        handle = self._child_handle
        if shm is None or handle is None:
            _say("ERR child no-shm")
            return 3
        sh = np.ndarray((self._shm_words,), dtype=np.float64, buffer=shm.buf)
        self._child_sh = sh
        pcm_area = shm.buf[PCM_OFFSET:]
        pcm_n = len(pcm_area)

        app = QCoreApplication([])
        dev = QMediaDevices.defaultAudioOutput()
        if dev is None or dev.isNull():
            _say("ERR child no-device")
            return 4
        fmt = QAudioFormat()
        fmt.setSampleRate(self.sample_rate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not dev.isFormatSupported(fmt):
            fmt = dev.preferredFormat()

        # ---- 消费者：从共享内存读 PCM ----
        class _ShmSource(QIODevice):
            def isSequential(self):            # noqa: N802
                return True

            def bytesAvailable(self):          # noqa: N802
                rem = (int(sh[SH_PCM_W]) - int(sh[SH_PCM_R])) % pcm_n
                return max(0, rem) + super().bytesAvailable()

            def readData(self, maxlen):        # noqa: N802
                r = int(sh[SH_PCM_R])
                w = int(sh[SH_PCM_W])
                rem = (w - r) % pcm_n
                if rem <= 0:
                    return b""
                n = min(maxlen, rem)
                end = r + n
                if end <= pcm_n:
                    out = bytes(pcm_area[r:end])
                else:
                    out = bytes(pcm_area[r:]) + bytes(pcm_area[:end - pcm_n])
                sh[SH_PCM_R] = float(end % pcm_n)
                return out

            def writeData(self, data):         # noqa: N802
                return 0

        src = _ShmSource()
        src.open(QIODevice.ReadOnly)
        sink = QAudioSink(dev, fmt)
        sink.start(src)

        state = {"running": True}
        blocks = [0]
        _errs = [0]

        def render_tick():
            """合成一块并写进共享环。"""
            if not state["running"]:
                return
            try:
                if sh[SH_CMD] > 0.5:           # 父进程要求退出
                    state["running"] = False
                    _quit()
                    return
                if state["parent_gone"]():     # 父进程没了（心跳停）
                    state["running"] = False
                    _quit()
                    return
                if float(sh[SH_MAGIC_WORD]) == float(SHM_MAGIC):
                    raw = shm.buf[NOTES_OFFSET:NOTES_OFFSET + NOTES_BYTES]
                    for i in range(MAX_VOICES):
                        o = i * NOTE_BYTES
                        # ★ 必须是 "<fff"：pitch / gain / pan **三个** float32。
                        #   这里曾经被一次批量替换弄成 "<ff"（只解包两个），
                        #   紧接着又去取 p[2] → IndexError: tuple index out of range。
                        #   而那个异常被下面的 except 吞掉 → 子进程静默写出全零 PCM
                        #   → 用户听到的是"完全没声"。教训：`render_tick` 里的
                        #   异常绝不能只 print（windowed EXE 的 stdout 是空的）。
                        pit, g, pan = struct.unpack_from("<fff", raw, o)
                        sig = struct.unpack_from("<H", raw, o + 12)[0]
                        if pit > 0.0 and g > 1e-5:
                            self._child_notes[i] = SoundNote(
                                pitch=pit, gain=g, pan=pan,
                                harmonics=_HARM_SIGS[sig % len(_HARM_SIGS)],
                                key="")
                        else:
                            self._child_notes[i] = None
                # 从共享内存取参数（父进程每秒写 10 次）
                self.brain.set_notes([n for n in self._child_notes if n is not None])
                self.brain.set_master(float(sh[SH_MASTER]))
                self.brain.set_drone(float(sh[SH_DRONE]))
                self.brain.set_mode(_MODE_NAMES[int(sh[SH_MODE]) % len(_MODE_NAMES)])
                # 旋律乐器由父进程通过共享内存下发（改了才重建声部池）
                try:
                    self.brain.set_instrument(
                        _INST_NAMES[int(sh[SH_INSTRUMENT]) % len(_INST_NAMES)])
                except Exception:
                    pass
                try:
                    self.brain.set_melody(float(sh[SH_MELODY]))
                except Exception:
                    pass
                # 注意：和声推进**不在这里**做，它由 render_block 内部按拍点推进
                # （放在这里的话，离线自检单独调 render_block 时永远不会换和弦，
                #   我实测就踩到了这个坑——"太单调"在自己的自检里看不出来）。
                pcm = self.render_block()
                # 写进共享环（空间不足就丢这一块——总比阻塞好）
                w = int(sh[SH_PCM_W])
                r = int(sh[SH_PCM_R])
                free = (r - w - 1) % pcm_n
                if len(pcm) <= free:
                    end = w + len(pcm)
                    if end <= pcm_n:
                        pcm_area[w:end] = pcm
                    else:
                        k = pcm_n - w
                        pcm_area[w:] = pcm[:k]
                        pcm_area[:end - pcm_n] = pcm[k:]
                    sh[SH_PCM_W] = float(end % pcm_n)
                # 心跳 = 已经合成过多少块（诊断用；必须是**递增计数**，
                # 曾经错写成"写一个常量 1"，于是生产速率算出来永远是 0）
                blocks[0] += 1
                sh[SH_HEARTBEAT] = float(blocks[0])
            except Exception as exc:           # pragma: no cover - 防御性
                # ★ 渲染异常绝不能"只记录然后继续"：子进程会一直写出全零 PCM，
                #   而父进程看到状态字=就绪、水位也在涨，**完全发现不了静音**。
                #   实测就是这样丢了一次声音（struct 格式写错 → IndexError）。
                #   所以：记完整 traceback，连续失败 5 次就主动退出，
                #   让父进程的降级逻辑接手（同进程合成），至少还有声音。
                import traceback as _tb
                _errs[0] += 1
                _say("ERR child render %s | %s"
                     % (exc, _tb.format_exc().replace(chr(10), " << ")[-300:]))
                if _errs[0] >= 5:
                    state["running"] = False
                    _quit()
                    return

        timer = QTimer()
        try:
            from PySide6.QtCore import Qt as _Qt
            timer.setTimerType(_Qt.PreciseTimer)
        except Exception:
            pass
        timer.setInterval(max(5, int(BLOCK_FRAMES * 1000 / self.sample_rate * 0.5)))
        timer.timeout.connect(render_tick)
        timer.start()

        # 父进程一走就退出（防孤儿进程）。
        # ★ 用**共享内存心跳**判定，不用管道 / QSocketNotifier：
        #   · QSocketNotifier 在 Windows 上只对 socket 有效，对匿名管道不触发；
        #   · 继承来的管道 fd 在冻结环境里读到的是 EBADF（实测 fd=3 Bad file
        #     descriptor），也就是说 fd 继承这条路同样不可靠。
        #   共享内存按名字打开，与 fd 继承无关，父子进程都能读写，稳。
        hb_start = float(sh[SH_HEARTBEAT_PARENT])
        last_hb = [hb_start]
        hb_time = [time.time()]

        def _parent_gone() -> bool:
            hb = float(sh[SH_HEARTBEAT_PARENT])
            if hb != last_hb[0]:
                last_hb[0] = hb
                hb_time[0] = time.time()
                return False
            if hb <= 0.0:
                # 父进程还一次都没 push（刚起来），再给它时间
                return (time.time() - hb_time[0]) > 20.0
            return (time.time() - hb_time[0]) > 8.0

        state["parent_gone"] = _parent_gone

        def _quit() -> None:
            """把子进程收干净再退出事件循环。"""
            state["running"] = False
            try:
                sink.stop()
            except Exception:
                pass
            app.quit()

        state["quit"] = _quit
        _say("OK child up")
        app.exec()
        try:
            sink.stop()
        except Exception:
            pass
        return 0

    def _aim(self, v: Voice, s: SoundNote) -> None:
        """让声部朝目标漂移一小步（每块约 46ms，所以用固定比例而不是精确积分）。"""
        k = min(1.0, (BLOCK_FRAMES / max(1, self.sample_rate)) / GLIDE_TAU * 8.0)
        tgt = s.degree if s.degree >= 0 else 0.0
        if v.target <= 0.0:
            v.degree = tgt                       # 新声部直接起音，不做滑音
            v.gain = 0.0
        else:
            v.degree += (tgt - v.degree) * k
        v.gain += (s.gain - v.gain) * k
        v.pan += (s.pan - v.pan) * k
        v.harmonics = s.harmonics
        v.target = tgt


class _PushSource:
    """QAudioSink 推模式的数据源：QIODevice 包一层环形缓冲。

    渲染在独立 Python 线程里做，这里只负责"有数据就给、没有就返回空"——
    绝不能在这个方法里做重活（它跑在 Qt 的音频回调路径上）。
    """

    def __new__(cls, engine: SonifyEngine, fmt):
        from PySide6.QtCore import QIODevice

        class _Impl(QIODevice):
            def __init__(self) -> None:
                super().__init__()
                self._engine = engine
                self._buf = bytearray()
                self._lock = threading.Lock()
                self._fmt = fmt

            # Qt 侧接口
            def isSequential(self) -> bool:      # noqa: N802
                return True

            def bytesAvailable(self) -> int:      # noqa: N802
                with self._lock:
                    return len(self._buf) + super().bytesAvailable()

            def readData(self, maxlen: int) -> bytes:   # noqa: N802
                with self._lock:
                    n = min(maxlen, len(self._buf))
                    out = bytes(self._buf[:n])
                    del self._buf[:n]
                return out

            def writeData(self, data) -> int:      # noqa: N802
                return 0

            # 渲染线程侧接口
            def feed(self, pcm: bytes) -> None:
                with self._lock:
                    self._buf.extend(pcm)
                    # 环形缓冲上限 1.5 秒：主线程被一帧 70ms 的重绘卡住时，
                    # 声卡靠这段存量顶过去（原来 0.5 秒太薄，实测拖拽时
                    # 16.6% 的采样点水位为 0 = 听得见的断音）。
                    limit = int(self._engine.sample_rate * 4 * 1.5)
                    if len(self._buf) > limit:
                        del self._buf[:len(self._buf) - limit]

        return _Impl()


# ==============================================================================
# 子进程宿主（默认路径）
# ==============================================================================

class _RemoteBrain:
    """`SonifyEngine.brain` 的替身：参数存在父进程侧，由 `SonifyHost.push()`
    写进共享内存。UI 只用 `set_notes / set_master / set_drone / set_mode /
    snapshot`，这里逐一对应。"""

    def __init__(self) -> None:
        self._notes: List[SoundNote] = []
        self._master = 0.55
        self._drone = 0.0
        self._mode = "starry"
        self._instrument = "pad"
        self._melody = 0.42
        self._seq = 0

    def set_notes(self, notes: Sequence[SoundNote]) -> None:
        self._notes = list(notes)
        self._seq += 1

    def set_master(self, v: float) -> None:
        self._master = float(max(0.0, min(1.0, v)))

    def set_drone(self, v: float) -> None:
        self._drone = float(max(0.0, min(1.0, v)))

    def set_mode(self, mode: str) -> None:
        self._mode = str(mode)

    def set_instrument(self, kind: str) -> None:
        self._instrument = str(kind)

    def instrument(self) -> str:
        return self._instrument

    def set_melody(self, v: float) -> None:
        self._melody = float(max(0.0, min(1.0, v)))

    def melody(self) -> float:
        return self._melody

    def snapshot(self) -> Tuple[List[SoundNote], float, float, str]:
        return (list(self._notes), self._master, self._drone, self._mode)

    @property
    def seq(self) -> int:
        return self._seq


class SonifyHost:
    """在**子进程**里合成音频的宿主（对外接口与 `SonifyEngine` 一致）。

    为什么是子进程而不是线程（实测）：
        同进程里主线程画一帧星图要 70~114ms，GIL 被它占着；音频线程虽然每块
        只要约 1ms，却常要等 40ms 才轮到——**生产速率只有 0.81×**
        （6.10 秒墙上时间只产出 4.97 秒音频），缓冲被反复抽干 →
        "转一下卡一下"。子进程有自己的 GIL 与解释器，父进程再卡也不影响出声。

    父进程只做两件事：每 100ms 把 48 条音符写进共享内存（几微秒），
    以及从共享环里取 PCM 喂给 QAudioSink。
    """

    def __init__(self, sample_rate: int = 44100) -> None:
        self.sample_rate = int(sample_rate)
        self.available = False
        self.reason = ""
        self.degraded = False          # True = 子进程起不来，退回同进程线程
        self.fallback: Optional[SonifyEngine] = None
        self._shm = None
        self._sh = None
        self._proc = None
        self._pipe_r = -1
        self._pipe_w = -1
        self._source = None
        self._sink = None
        self.brain = _RemoteBrain()
        self._last_push = 0.0
        self._hb_seq = 0

    # ------------------------------------------------------------------ 启动
    def start(self) -> bool:
        if self.available:
            return True
        if self._start_child():
            return True
        LOG.warn(f"星图之声子进程启动失败（{self.reason}），退回同进程线程合成")
        self.degraded = True
        self.fallback = SonifyEngine(self.sample_rate)
        ok = self.fallback.start()
        self.available = ok
        if not ok:
            self.reason = self.fallback.reason
        return ok

    @staticmethod
    def _child_bootstrap(pkg: str) -> str:
        """子进程的引导脚本（源码运行时用 `python -c`）。

        特意用 `-c` 而不是 multiprocessing 的 spawn/fork：
        Windows 上是 spawn，会把父进程的 __main__ 再执行一遍（本程序是 GUI，
        那样会多开一个窗口）；`-c` 只跑这一小段。

        ⚠️ 打包成单文件 EXE 之后**不能走这条路**：`sys.executable` 是那个 EXE，
        而 `核心` 包在 EXE 内部、不在文件系统上，`-c` 脚本 import 不到它。
        冰冻模式下改成"用同一个 EXE + 环境变量标志"再起一次进程，
        由 `main.py` 顶部的音频子进程入口接管（见 `_start_child`）。
        """
        return (
            "import os,sys\n"
            "sys.path.insert(0, %r)\n"
            "from multiprocessing import shared_memory\n"
            "shm=shared_memory.SharedMemory(name=os.environ['_SNX_SHM'])\n"
            "import 核心.sonify as S\n"
            "e=S.SonifyEngine()\n"
            "e._child_shm=shm\n"
            "e._child_handle=1\n"
            "e._pipe_fd=int(os.environ['_SNX_PIPE_FD'])\n"
            "e._status_w=int(os.environ.get('_SNX_STATUS_FD','-1'))\n"
            "sys.exit(e.run_audio_process())\n"
        ) % pkg

    @staticmethod
    def _frozen() -> bool:
        return bool(getattr(sys, "frozen", False))

    def _start_child(self) -> bool:
        import struct
        import subprocess
        from multiprocessing import shared_memory

        try:
            self._shm = shared_memory.SharedMemory(create=True, size=SHM_WORDS * 8)
        except Exception as exc:
            self.reason = f"无法创建共享内存：{exc}"
            return False
        self._sh = np.ndarray((SHM_WORDS,), dtype=np.float64, buffer=self._shm.buf)
        self._sh[:] = 0.0
        self._sh[SH_MAGIC_WORD] = float(SHM_MAGIC)
        self._sh[SH_MASTER] = 0.55
        self._sh[SH_STATUS_WORD] = STATUS_STARTING
        self._child_sh = self._sh

        try:
            self._pipe_r, self._pipe_w = os.pipe()
        except Exception as exc:
            self._release()
            self.reason = f"无法创建管道：{exc}"
            return False
        # 第二条管道专门给子进程报状态（"OK child up" / "ERR ..."）。
        # 双向共用一条不行：那样父进程读的正是自己写的端，
        # 而且子进程一读就会把父进程写进去的退出信号吃掉。
        try:
            self._status_r, self._status_w = os.pipe()
        except Exception as exc:
            self._release()
            self.reason = f"无法创建状态管道：{exc}"
            return False

        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env["_SNX_SHM"] = self._shm.name
        env["_SNX_PIPE_FD"] = str(self._pipe_r)
        env["_SNX_STATUS_FD"] = str(self._status_w)
        env["QT_LOGGING_RULES"] = "qt.multimedia.*=false"
        if self._frozen():
            # 打包后：用同一个 EXE 再起一个进程，由 main.py 顶部的
            # 音频子进程入口接管（不再走 `python -c`，因为包在 EXE 内部）
            env["_SNX_AUDIO_CHILD"] = "1"
            cmd = [sys.executable]
        else:
            env.pop("_SNX_AUDIO_CHILD", None)
            cmd = [sys.executable, "-c", self._child_bootstrap(pkg)]
        try:
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=env, creationflags=creation)
        except Exception as exc:
            self._release()
            self.reason = f"无法启动音频子进程：{exc}"
            return False
        # 父进程不再需要状态管道的写端；关掉它，子进程退出时读端才会 EOF
        try:
            os.close(self._status_w)
        except Exception:
            pass
        self._status_w = -1

        # 等子进程报 "OK child up"（最多 6 秒；首次启动要解压单文件 EXE）
        t0 = time.time()
        ok = False
        try:
            os.set_blocking(self._status_r, False)
        except Exception:
            pass
        while time.time() - t0 < 8.0:
            try:
                st = float(self._sh[SH_STATUS_WORD])
            except Exception:
                st = STATUS_STARTING
            if st == STATUS_READY:
                ok = True
                break
            if st == STATUS_FAILED:
                if not self.reason:
                    self.reason = "音频子进程报错（详见日志）"
                break
            # 顺带把子进程尽力写进管道的文字捞出来，便于报错时给出原因
            try:
                chunk = os.read(self._status_r, 4096)
                if chunk:
                    text = chunk.decode("utf-8", "replace").strip()
                    if text.startswith("ERR"):
                        self.reason = text.splitlines()[0]
                    elif text.startswith("OK"):
                        ok = True
                        break
            except Exception:
                pass
            if self._proc.poll() is not None:
                self.reason = f"音频子进程提前退出（退出码 {self._proc.returncode}）"
                break
            time.sleep(0.05)
        if not ok:
            if not self.reason:
                self.reason = "音频子进程未在 8 秒内就绪"
            self._kill_child()
            self._release()
            return False

        try:
            from PySide6.QtCore import QIODevice
            self._source = _ShmFeed(self._shm)
            self._source.open(QIODevice.ReadOnly)
        except Exception as exc:
            self.reason = f"父进程读取共享音频失败：{exc}"
            self._kill_child()
            self._release()
            return False
        self.available = True
        self.reason = ""
        return True

    # ------------------------------------------------------------------ 推送
    def push(self) -> None:
        """把当前参数写进共享内存（父进程调用，默认每 100ms 一次）。"""
        if not self.available:
            return
        if self.degraded:
            eng = self.fallback
            if eng is not None:
                notes, master, drone, mode = self.brain.snapshot()
                eng.brain.set_notes(notes)
                eng.brain.set_master(master)
                eng.brain.set_drone(drone)
                eng.brain.set_mode(mode)
            return
        import struct
        sh = self._sh
        buf = self._shm.buf
        notes, master, drone, mode = self.brain.snapshot()
        sh[SH_MASTER] = master
        sh[SH_DRONE] = drone
        try:
            sh[SH_MODE] = float(_MODE_NAMES.index(mode))
        except ValueError:
            sh[SH_MODE] = 0.0
        try:
            sh[SH_INSTRUMENT] = float(
                [k for k, _, _ in INSTRUMENTS].index(self.brain.instrument()))
        except (ValueError, TypeError):
            sh[SH_INSTRUMENT] = 0.0
        sh[SH_MELODY] = float(self.brain.melody())
        raw = bytearray(NOTES_BYTES)
        for i, n in enumerate(notes[:MAX_VOICES]):
            o = i * NOTE_BYTES
            try:
                sig = _HARM_SIGS.index(tuple(n.harmonics))
            except ValueError:
                sig = 0
            struct.pack_into("<fffH", raw, o, float(n.pitch), float(n.gain),
                             float(n.pan), sig)
        buf[NOTES_OFFSET:NOTES_OFFSET + NOTES_BYTES] = raw
        self._last_push = time.time()
        try:
            self._hb_seq += 1
            self._sh[SH_HEARTBEAT_PARENT] = float(self._hb_seq)
        except Exception:
            pass

    # ------------------------------------------------------------------ 停止
    def stop(self) -> None:
        self.available = False
        self.brain.set_notes([])
        if self.degraded:
            if self.fallback is not None:
                self.fallback.brain.set_notes([])
                self.fallback.brain.set_drone(0.0)
                self.fallback.stop()
            return
        # 先让子进程安静下来（不然会以最后一块的音量"切断"）
        try:
            if self._sh is not None:
                self._sh[SH_MASTER] = 0.0
            time.sleep(0.05)
        except Exception:
            pass
        self._kill_child()
        try:
            if self._source is not None:
                self._source.close()
        except Exception:
            pass
        self._source = None
        self._release()

    def _kill_child(self) -> None:
        try:
            if self._sh is not None:
                self._sh[SH_CMD] = 1.0
        except Exception:
            pass
        try:
            if self._pipe_w >= 0:
                os.close(self._pipe_w)
                self._pipe_w = -1
        except Exception:
            pass
        p = self._proc
        if p is not None:
            try:
                p.wait(timeout=2.0)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            try:
                if p.stdout is not None:
                    p.stdout.close()
            except Exception:
                pass
        self._proc = None
        try:
            if self._pipe_r >= 0:
                os.close(self._pipe_r)
                self._pipe_r = -1
        except Exception:
            pass
        for attr in ("_status_r", "_status_w"):
            try:
                fd = getattr(self, attr, -1)
                if fd >= 0:
                    os.close(fd)
            except Exception:
                pass
            setattr(self, attr, -1)

    def _release(self) -> None:
        try:
            if self._shm is not None:
                self._shm.close()
                self._shm.unlink()
        except Exception:
            pass
        self._shm = None
        self._sh = None

    # ------------------------------------------------------------------ 状态
    def stats(self) -> Dict[str, float]:
        """诊断用：共享环水位（毫秒）与子进程心跳。"""
        out = {"degraded": 1.0 if self.degraded else 0.0, "fill_ms": 0.0}
        if self._sh is None:
            return out
        try:
            r = int(self._sh[SH_PCM_R])
            w = int(self._sh[SH_PCM_W])
            out["fill_ms"] = (w - r) % PCM_BYTES / 4.0 / self.sample_rate * 1000.0
            out["heartbeat"] = float(self._sh[SH_HEARTBEAT])
        except Exception:
            pass
        return out


class _ShmFeed:
    """父进程侧的消费者：把共享环里的 PCM 交给 `QAudioSink`。

    注意：**这里不做合成**，只搬字节。合成在子进程里。
    """

    def __new__(cls, shm):
        from PySide6.QtCore import QIODevice

        class _Impl(QIODevice):
            def __init__(self) -> None:
                super().__init__()
                self._shm = shm
                self.starved = 0

            def isSequential(self) -> bool:      # noqa: N802
                return True

            def bytesAvailable(self) -> int:      # noqa: N802
                return self._avail() + super().bytesAvailable()

            def _avail(self) -> int:
                import struct
                try:
                    r, w = struct.unpack_from(
                        "<dd", self._shm.buf, SH_PCM_R * 8)
                    return (int(w) - int(r)) % PCM_BYTES
                except Exception:
                    return 0

            def readData(self, maxlen: int) -> bytes:   # noqa: N802
                import struct
                buf = self._shm.buf
                r, w = struct.unpack_from("<dd", buf, SH_PCM_R * 8)
                r = int(r)
                w = int(w)
                rem = (w - r) % PCM_BYTES
                if rem <= 0:
                    self.starved += 1
                    return b""
                n = min(maxlen, rem)
                base = PCM_OFFSET
                end = r + n
                if end <= PCM_BYTES:
                    out = bytes(buf[base + r:base + end])
                else:
                    out = (bytes(buf[base + r:base + PCM_BYTES])
                           + bytes(buf[base:base + end - PCM_BYTES]))
                struct.pack_into("<d", buf, SH_PCM_R * 8, float(end % PCM_BYTES))
                return out

            def writeData(self, data) -> int:      # noqa: N802
                return 0

        return _Impl()

