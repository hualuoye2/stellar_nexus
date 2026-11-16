# -*- coding: utf-8 -*-
"""星图声化（把星图"弹"出来）自检。

分四层：
  A 纯函数：光谱型→音高/音色、星等→增益、方位→声像，以及**协和性**
  B 合成器：真的合出音频；**变化**（和声推进/律动/低音线）也要能测
  C 从真实星表取音（StarVoiceSource）
  D 接进主窗口（子进程宿主 / 面板 / 开关 / 跟随视角）

★ 这个功能真正难的判据是两条，都不是"跑起来没报错"：

  1) **协和**：把光谱型映射到一个音阶上，好让任意组合都好听。
     断言：所有光谱型的音高只落在音阶的 7 个音级上；并且**和声推进
     （换和弦）平移后仍然是同一音级集合的子集**。
     这条曾经是错的：五声音阶 + 半音平移，+3 半音后音级变成 [1,3,6,8,10]，
     出现小二度与三全音，听感发脏。换成 7 声自然小调 + 按"音级"平移，
     才同时满足"能换和弦"与"任意组合协和"。

  2) **变化**：用户报"太单调"。断言和声推进在 60 秒内真的换过 >=3 个
     调性中心、铃音音高跟着换、低音线随和声平移、RMS 有律动。
     ★ 曾经整段都没有这类断言——因为推进逻辑写在子进程的定时循环里，
       离线单独调 `render_block()` 永远不会推进，"太单调"在自己的自检里
       完全看不出来。现在推进在合成器内部，所以测得到。
"""
import io
import math
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")

FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


from 核心 import sonify as SN   # noqa: E402

print("=" * 78)
print("A 纯函数层")
print("=" * 78)

check("光谱型取首字母（G2V→G，M5Ve→M）",
      SN.spectral_key("G2V") == "G" and SN.spectral_key("M5Ve") == "M"
      and SN.spectral_key("") == "G")
oct_o, _, harm_o = SN.voice_of("O5V")
oct_m, _, harm_m = SN.voice_of("M4V")
check("O 型比 M 型音区高", oct_o > oct_m, "O 音区 %d / M 音区 %d" % (oct_o, oct_m))
# ★ 判据从"数谐波个数"换成"**能量加权**的高频丰富度 sum(a_n × n²)"。
#   只数个数抓不到真正决定"刺耳与否"的东西——音高。实测：数个数时
#   O=8、M=3 看起来没问题，但把基频提到 A3（220Hz）之后，
#   N 型（5588Hz × 5 个谐波）的高频能量其实超过了 O 型。
_rich_o = sum(a * (i + 1) ** 2 for i, a in enumerate(harm_o))
_rich_m = sum(a * (i + 1) ** 2 for i, a in enumerate(harm_m))
check("O 型高频能量比 M 型丰富（音色更刺耳）", _rich_o > 3.0 * _rich_m,
      "O 丰富度 %.2f / M %.2f（= sum(a_n·n²)）" % (_rich_o, _rich_m))
check("认不出的光谱型回退到 G 类", SN.voice_of("???") == SN.SPECTRAL_VOICE["G"])

f_a = SN.pitch_hz(0, 0)
# ★ 主音从 A2(110Hz) 提到 A3(220Hz)：原来 110Hz 时实测 20~250Hz 占了
#   总能量 50%、最强分量在 48~135Hz，手机/笔记本小喇叭基本放不出来
#   （用户听到的就是"完全没声音"）。所以断言跟着改成"== ROOT_HZ"，
#   不再写死数值——写死数值正是它没能反映这次改动的原因。
check("音区 0 级数 0 = 主音（ROOT_HZ）", abs(f_a - SN.ROOT_HZ) < 1e-6,
      "%.3f Hz（ROOT_HZ=%.0f）" % (f_a, SN.ROOT_HZ))
check("级数进位到高八度（级数 = 音阶长度）",
      abs(SN.pitch_hz(0, len(SN.SCALE)) - SN.ROOT_HZ * 2) < 1e-6,
      "%.3f Hz" % SN.pitch_hz(0, len(SN.SCALE)))
check("音区每升一层翻一倍",
      abs(SN.pitch_hz(2, 0) - SN.pitch_hz(1, 0) * 2) < 1e-6)
check("负级数也能正确取模（不崩、且低于主音）",
      SN.pitch_hz(0, -1) < SN.ROOT_HZ, "%.1f Hz" % SN.pitch_hz(0, -1))

# --- 协和性 1：光谱型只落在音阶上 ---
letters = sorted(SN.SPECTRAL_VOICE)
pitch_classes = set()
for L in letters:
    oct_, base, _h = SN.SPECTRAL_VOICE[L]
    for deg in range(len(SN.SCALE)):
        pc = int(round(12 * math.log2(SN.pitch_hz(oct_, base + deg) / SN.ROOT_HZ))) % 12
        pitch_classes.add(pc)
check("★ 所有光谱型只落在音阶的 7 个音级上", pitch_classes.issubset(set(SN.SCALE)),
      "实际音级 %s，允许 %s" % (sorted(pitch_classes), list(SN.SCALE)))
check("★ 音级数 == 7（不是碰巧只用到更少）", len(pitch_classes) == 7,
      "%d 个" % len(pitch_classes))

# --- 协和性 2：和声推进平移后仍在音阶内（曾经错在这里） ---
all_in = True
rows = []
for t in SN.PROGRESSION:
    k = SN.semitones_to_steps(t)
    s = {int(round(12 * math.log2(SN.pitch_hz(0, d + k) / 110.0))) % 12
         for d in range(len(SN.SCALE))}
    ok = s.issubset(set(SN.SCALE))
    all_in = all_in and ok
    rows.append((t, k, sorted(s), ok))
check("★ 四种和声推进平移后都仍是同一音阶的子集（换和弦不发脏）", all_in,
      "；".join("%+d半音→%+d级 %s" % (t, k, "OK" if o else "越界") for t, k, _s, o in rows))
penta = (0, 3, 5, 7, 10)
bad = {(p + 3) % 12 for p in penta}
check("反例对照：五声音阶 +3 半音会跑出原集合（所以必须改成 7 声音阶+按级平移）",
      not bad.issubset(set(penta)), "平移后音级 %s" % sorted(bad))

# --- 星等 → 增益 ---
g_bright = SN.magnitude_gain(-1.44)
g_faint = SN.magnitude_gain(6.0)
check("亮星增益 > 暗星增益", g_bright > g_faint,
      "天狼 -1.44 等 → %.4f　6 等星 → %.4f" % (g_bright, g_faint))
check("动态范围被压缩（比值 < 100 倍，不是真实的 1000 倍）",
      g_bright / max(1e-9, g_faint) < 100,
      "%.1f 倍" % (g_bright / max(1e-9, g_faint)))
check("增益上限 1.0", SN.magnitude_gain(-30.0) == 1.0)
check("增益下限不为 0（暗星也有存在感）", SN.magnitude_gain(20.0) > 0.0,
      "%.3f" % SN.magnitude_gain(20.0))

# --- 方位 → 声像 ---
check("视线中心的天体在正前方（声像 0）", SN.az_to_pan(180, 180) == 0.0)
check("中心右侧 45° → 声像 +0.5（90° 满偏）", abs(SN.az_to_pan(225, 180) - 0.5) < 1e-9,
      "%.3f" % SN.az_to_pan(225, 180))
check("跨 0/360 不跳变（350° 相对 10° 应是 -20° 而不是 +340°）",
      abs(SN.az_to_pan(350, 10) + (20 / 90)) < 1e-9, "%.3f" % SN.az_to_pan(350, 10))

print()
print("=" * 78)
print("B 合成器层（真的合出音频 + 真的在变化）")
print("=" * 78)

os.environ.pop("QT_QPA_PLATFORM", None)     # 音频需要真实平台插件
from PySide6.QtWidgets import QApplication  # noqa: E402
import numpy as np                          # noqa: E402
app = QApplication.instance() or QApplication(sys.argv[:1])

eng = SN.SonifyEngine()
notes = []
for i, L in enumerate(letters):
    oct_, deg, harm = SN.SPECTRAL_VOICE[L]
    step = oct_ * len(SN.SCALE) + deg
    notes.append(SN.SoundNote(pitch=SN.pitch_hz(0, step), degree=step,
                              gain=SN.magnitude_gain(-1.0 + i * 0.5),
                              pan=-0.8 + 0.16 * i, harmonics=harm, key=L))
eng.brain.set_notes(notes)
eng.brain.set_drone(0.7)
eng.brain.set_master(0.35)
blocks = [np.frombuffer(eng.render_block(), dtype="<i2") for _ in range(30)]
peak = max(int(np.abs(b).max()) for b in blocks)
tail = blocks[-1].astype(np.float32) / 32768.0
rms = float(np.sqrt(np.mean(tail ** 2)))
clipped = int(np.sum(np.abs(blocks[-1]) >= 32767))
check("合成输出不是静音", peak > 1000, "峰值 %d / 32767" % peak)
check("RMS 在合理区间（0.01~0.5，既不虚也不炸）", 0.01 < rms < 0.5, "RMS %.4f" % rms)
check("没有削顶（软限幅生效）", clipped == 0, "%d 个样本贴顶" % clipped)
check("输出是 16bit 立体声交错", len(blocks[-1]) == SN.BLOCK_FRAMES * 2)

t0 = time.perf_counter()
for _ in range(50):
    eng.render_block()
per = (time.perf_counter() - t0) / 50 * 1000.0
budget = SN.BLOCK_FRAMES * 1000.0 / eng.sample_rate
check("★ 单块合成耗时 < 块长的 40%（拖拽时可留足余量）", per < budget * 0.4,
      "实测 %.2f ms/块（块长 %.1f ms）→ 余量 %.1f x" % (per, budget, budget / per))

print()
print("  —— 变化（用户报「太单调」，这里必须测到）——")
eng2 = SN.SonifyEngine()
eng2.brain.set_notes(notes)
eng2.brain.set_drone(0.3)
eng2.brain.set_master(0.55)
seen = []
bell_at = {}
rms_env = []
for i in range(int(60 * eng2.sample_rate / SN.BLOCK_FRAMES)):
    pcm = eng2.render_block()
    if not seen or seen[-1] != eng2._prog_idx:
        seen.append(eng2._prog_idx)
        bell_at[eng2._prog_idx] = eng2._bell_hz
    if i % 4 == 0:
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        rms_env.append(float(np.sqrt((a ** 2).mean())))
check("★ 60 秒内和声推进真的换过调性中心（>= 3 次）", len(seen) >= 3,
      "换过 %d 次：%s" % (len(seen), seen[:6]))
check("★ 铃音音高跟着和声推进改变（不是卡在一个音上）",
      len(set(round(v, 1) for v in bell_at.values())) >= 3,
      "各级铃音 %s Hz" % {k: round(v) for k, v in list(bell_at.items())[:5]})
bass_degrees = sorted({SN.semitones_to_steps(SN.PROGRESSION[p]) + (0 if p % 2 == 0 else 4)
                       for p in range(len(SN.PROGRESSION))})
check("低音线随和声平移（不同推进级下低音级数不同，>=3 种）",
      len(bass_degrees) >= 3, "低音级 %s" % bass_degrees)
env = np.array(rms_env)
ratio = env.max() / max(1e-6, env.min())
check("有律动（RMS 包络起伏比 > 1.3，不是一条平线）", ratio > 1.3,
      "%.2f（%.4f ~ %.4f）" % (ratio, env.min(), env.max()))
x = env - env.mean()
X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
fr = np.fft.rfftfreq(len(x), 4 * SN.BLOCK_FRAMES / eng2.sample_rate)
if len(X) > 3:
    top = float(fr[int(np.argmax(X[1:])) + 1])
    beat = SN.DEFAULT_TEMPO / 60.0
    check("律动主频≈拍频（说明呼吸/铃声是按节拍时钟走的，不是随机抖动）",
          abs(top - beat) < beat * 1.0, "实测 %.3f Hz，拍频 %.3f Hz" % (top, beat))

print()
print("=" * 78)
print("B2 乐器层（钢琴 / 吉他 / 钟琴 —— 用户要的「来点钢琴」）")
print("=" * 78)
#: "有没有音头"怎么量化：触发一个音后比较**前 0.3 秒**与**1.5~2.5 秒**的
#: RMS 能量比。有音头的乐器这个比值很大（几十倍），纯氛围垫几乎持平。
#: 这是"听起来像不像钢琴"的可测代理。
# ★ 判据要量**包络包线**，不能拿"两个不同时长窗口的平均能量"相比：
#   那样对长尾巴的乐器不公平（0.3 秒的均值会被尾部拉高，1 秒的均值里
#   有效信号又太少）。改成：音头处（前 60ms）的 RMS vs 2 秒后的 RMS。
ATTACK_WIN = (0.0, 0.06)
TAIL_POINT = (1.95, 2.05)
inst_rows = {}
for key, cn, _tip in SN.INSTRUMENTS:
    inst = SN.make_instrument(key, 44100)
    if inst is None:
        inst_rows[key] = (cn, float("nan"), 0.0, 0.0)
        continue
    inst.trigger(440.0, 0.5)
    buf, rest = [], int(3.0 * 44100)
    while rest > 0:
        k = min(1024, rest)
        buf.append(inst.render(k))
        rest -= k
    y = np.concatenate(buf)
    seg = lambda a, b: y[int(a * 44100):int(b * 44100)]
    atk = float(np.sqrt(np.mean(seg(*ATTACK_WIN) ** 2)))
    tl = float(np.sqrt(np.mean(seg(*TAIL_POINT) ** 2)))
    inst_rows[key] = (cn, atk / max(1e-9, tl), atk, tl)

for key, cn, _t in SN.INSTRUMENTS:
    if key == "pad":
        continue
    _cn, ratio, atk, tl = inst_rows[key]
    check("★「%s」有音头（音头 RMS / 2 秒后的 RMS >= 3 倍）" % cn,
          ratio >= 3.0, "比值 %.1f（音头 %.4f → 2 秒后 %.4f）" % (ratio, atk, tl))
check("不含音头的氛围垫没被误判成有音头（对照）",
      inst_rows["pad"][1] != inst_rows["pad"][1]      # NaN = 没有乐器对象
      or inst_rows["pad"][1] < 8.0)

# 四种乐器的音色必须真的不同（否则"加乐器"只是换了个名字）
sigs = {}
for key, _cn, _t in SN.INSTRUMENTS:
    inst = SN.make_instrument(key, 44100)
    if inst is None:
        continue
    inst.trigger(440.0, 0.5)
    buf, rest = [], int(0.5 * 44100)
    while rest > 0:
        k = min(1024, rest)
        buf.append(inst.render(k))
        rest -= k
    y = np.concatenate(buf)
    sigs[key] = y / max(1e-9, float(np.abs(y).max()))
keys = sorted(sigs)
diffs = []
for a in range(len(keys)):
    for b in range(a + 1, len(keys)):
        diffs.append((keys[a], keys[b],
                      float(np.mean(np.abs(sigs[keys[a]] - sigs[keys[b]])))))
worst = min(diffs, key=lambda t: t[2])
check("四种乐器两两音色都不同（最接近的一对差值 > 0.05）", worst[2] > 0.05,
      "最接近：%s vs %s 差值 %.3f" % (worst[0], worst[1], worst[2]))

# ★ 「加了钢琴却听不出来」的根因回归：低音线与铃声是**和声声部**，
#   没有乐谱时必须停。它们原来只依赖拍点与和声级数、与乐谱无关，
#   于是清空乐谱后仍在稳定发声（实测 RMS 恒为 0.063、包络一条平线），
#   把旋律乐器整段埋在底下。
eng_silent = SN.SonifyEngine()
eng_silent.brain.set_notes([])
eng_silent.brain.set_drone(0.0)
eng_silent.brain.set_master(0.55)
eng_silent.brain.set_instrument("pad")
sil = [np.frombuffer(eng_silent.render_block(), dtype="<i2").astype(np.float32) / 32768
       for _ in range(120)]
sil_rms = float(np.sqrt(np.mean(np.concatenate(sil) ** 2)))
# ★ 共享内存布局自检：magic 不能被任何参数覆盖。
#   这一条抓的是"完全没声"的根因之一：magic 原来写在字节 8~15，
#   而那正好是标量下标 1 = SH_MASTER。父进程每设一次音量就把魔数砸掉，
#   子进程从此读不到音符（notes=0），合成出全零 PCM。
#   判据：所有标量槽的取值必须两两不同（下标不重叠），
#   且 magic 槽不是任何参数槽。
_slots = {}
for _n in ("SH_LOCK", "SH_MASTER", "SH_DRONE", "SH_MODE", "SH_CMD",
           "SH_HEARTBEAT", "SH_MAGIC_WORD", "SH_PCM_R", "SH_PCM_W",
           "SH_STATUS_WORD", "SH_HEARTBEAT_PARENT", "SH_INSTRUMENT",
           "SH_TEMPO", "SH_INST_DENSITY", "SH_MELODY"):
    _v = getattr(SN, _n, None)
    if isinstance(_v, int):
        _slots[_n] = _v
check("★ 共享内存标量槽互不重叠（magic 不再被参数覆盖）",
      len(set(_slots.values())) == len(_slots),
      "%d 个槽，去重后 %d 个 → %s" % (len(_slots), len(set(_slots.values())),
                                     "无重叠" if len(set(_slots.values())) == len(_slots) else "有重叠！"))
check("★ magic 有独立槽位（不是 SH_MASTER 的字节）",
      SN.SH_MAGIC_WORD not in (SN.SH_MASTER, SN.SH_DRONE, SN.SH_MODE, SN.SH_CMD),
      "SH_MAGIC_WORD=%d，SH_MASTER=%d" % (SN.SH_MAGIC_WORD, SN.SH_MASTER))

# ★ 谐波签名表必须是"纯 float 元组"的序列。
#   这里连错两次，两次都表现为"完全没声"（异常被吞、子进程写全零 PCM）：
#     ① 元素的类型不对（取到 SPECTRAL_VOICE 的整个三元组而不是第三项）；
#     ② 排序写法引入杂质。
check("★ 谐波签名表每个元素都是纯 float 元组",
      len(SN._HARM_SIGS) > 0 and all(
          isinstance(_h, tuple) and len(_h) > 0
          and all(isinstance(_v, (int, float)) for _v in _h)
          for _h in SN._HARM_SIGS),
      "%d 个签名，首项 %s" % (len(SN._HARM_SIGS), SN._HARM_SIGS[0] if SN._HARM_SIGS else "空"))

check("★ 乐谱为空且无 drone 时完全安静（低音线/铃声不再常驻发声）",
      sil_rms < 1e-4, "RMS %.6f（修前实测 0.063）" % sil_rms)

# ★ 相位连续性：分块渲染必须与"整块一次渲染"逐样本一致。
#   这一条抓的是一个真实且极其难听的 bug：相位推进写成
#       idx = (cur + t*f) % 1.0            # t 含本块
#       self._phases[i] = (cur + self.t*f) % 1.0   # self.t 已加过本块
#   两者差一个块长 → **每个音频块边界一次相位跳变**，
#   听感是持续的"嗡/沙"底噪（频谱上看不出明显峰，极难定位）。
#   后来又发现即使对齐了基准，`(t*f)%1.0` 在 t=3s、16kHz 时已经是 48000，
#   超出 double 的取模精度，同样会偏 → 最终改成**增量式相位**。
#   ★ 判据不能拿"整块一次渲染"当基准：那样 t*f 会累积到 4e5、精度更差，
#     反而把正确的实现判成错的（我第一版就是这么写的）。
#     正确判据是"块边界处的样本跳变不比块内正常跳变大"。
_ph = SN.make_instrument("epiano", 44100)
_ph.trigger(20.0, 0.9)          # 低频：斜率小，跳变容易暴露
_ser, _rest = [], 8 * 44100
while _rest > 0:
    _k = min(SN.BLOCK_FRAMES, _rest)
    _ser.append(_ph.render(_k))
    _rest -= _k
_yph = np.concatenate(_ser)
_dph = np.abs(np.diff(_yph))
_bound = np.array([_dph[i * SN.BLOCK_FRAMES - 1]
                   for i in range(1, len(_yph) // SN.BLOCK_FRAMES)])
# ★ 不能拿"块内中位跳变"当基准：音符衰减到静音后中位数趋近 0，
#   比值会爆掉（实测 1183），那是判据的问题不是代码的问题。
#   正确做法：比"边界处最大跳变"与"整个信号里除边界外的最大跳变"。
_edge_idx = set((i * SN.BLOCK_FRAMES - 1)
                for i in range(1, len(_yph) // SN.BLOCK_FRAMES))
_inner = np.array([_dph[i] for i in range(len(_dph)) if i not in _edge_idx])
_imax = float(_inner.max()) if _inner.size else 0.0
_ratio = float(_bound.max()) / max(1e-12, _imax)
check("★ 块边界没有相位跳变（边界跳变 <= 块内最大跳变 × 1.5）",
      float(_bound.max()) <= 1.5 * _imax + 1e-9,
      "边界最大 %.5f　块内最大 %.5f　比值 %.2f" % (float(_bound.max()), _imax, _ratio))

# ★ 音色：钢琴不能"敲铁"，也不能"蒙棉被"。
#   判据用频率分布（用户给的参考曲实测：重心 1863Hz，
#   120-250/250-500/500-1k/1k-2k/2k-4k/4k-8k = 8/17/29/21/13/6 %）。
#   只断言两条最容易做错的：重心落在"不刺耳也不闷"的区间、
#   且 2k~4kHz 不占主导（>35% 就是"敲铁"）。
_pc, _pb = [], []
for _f0 in (165.0, 220.0, 440.0, 523.25):
    _ins = SN.make_instrument("epiano", 44100)
    _ins.trigger(_f0, 0.6)
    _buf, _r = [], 2 * 44100
    while _r > 0:
        _k = min(2048, _r)
        _buf.append(_ins.render(_k))
        _r -= _k
    _y = np.concatenate(_buf)
    _N = 8192
    _fr = np.fft.rfftfreq(_N, 1 / 44100)
    _X = np.zeros(_N // 2 + 1)
    _c = 0
    for _i in range(0, len(_y) - _N, 4096):
        _X += np.abs(np.fft.rfft(_y[_i:_i + _N] * np.hanning(_N)))
        _c += 1
    _X /= max(1, _c)
    _pc.append(float((_X * _fr).sum() / _X.sum()))
    _tot = _X[(_fr > 40) & (_fr < 16000)].sum()
    _pb.append(100 * _X[(_fr >= 2000) & (_fr < 4000)].sum() / max(1e-9, _tot))
_cent = float(np.mean(_pc))
_check_hi = 100 * _X[(_fr >= 2000) & (_fr < 4000)].sum() / max(1e-9, _tot)
check("★ 钢琴谱重心落在 900~2600 Hz（不闷也不刺）", 900.0 <= _cent <= 2600.0,
      "实测 %.0f Hz；参考曲（用户提供）1863 Hz" % _cent)
check("★ 钢琴的 2k~4kHz 不占主导（<35%；参考曲 13%）",
      float(np.mean(_pb)) < 35.0, "实测 %.0f%%" % float(np.mean(_pb)))

# 有乐器时确实能听到旋律（而不是只有和声垫）
for key in ("epiano", "guitar", "bell"):
    e = SN.SonifyEngine()
    e.brain.set_notes([])
    e.brain.set_drone(0.0)
    e.brain.set_master(0.55)
    e.brain.set_instrument(key)
    e._sync_instrument(key)
    fr = [np.frombuffer(e.render_block(), dtype="<i2").astype(np.float32) / 32768
          for _ in range(int(30 * 44100 / SN.BLOCK_FRAMES))]
    x = np.concatenate(fr)
    w2 = int(0.02 * 44100)
    kk = len(x) // w2
    env = np.sqrt((x[:kk * w2].reshape(kk, w2) ** 2).mean(axis=1))
    onsets = 0
    last = -99
    for i in range(2, kk):
        if env[i] > 0.01 and env[i] > env[i - 1] * 2.2 and env[i] > env[i - 2] * 2.0:
            if i - last > 12:          # 0.24 秒内不重复计数
                onsets += 1
                last = i
    cn = next(n for k, n, _ in SN.INSTRUMENTS if k == key)
    check("★「%s」在 30 秒里触发出了可辨识的音头（>= 4 个）" % cn, onsets >= 4,
          "%d 个音头（期望约 %.0f 拍）" % (onsets, 30 * SN.DEFAULT_TEMPO / 60))

print()
print("=" * 78)
print("C 从真实星表取音（StarVoiceSource）")
print("=" * 78)


class _FakeObj:
    """星表对象的最小替身：只要 sonify_panel 用到的属性。"""

    def __init__(self, key, spectral, mag, alt, az):
        self.key = key
        self.spectral = spectral
        self.mag = mag
        self.alt = alt
        self.az = az
        self.kind = "star"


from ui.sonify_panel import StarVoiceSource, MAX_NOTES   # noqa: E402

src = StarVoiceSource()
fake = [_FakeObj("s%d" % i, "A0V", 2.0 + 0.01 * i, 45.0, (i * 6.0) % 360)
        for i in range(60)]
notes2 = src.notes_from_objects(fake, 180.0, 45.0)
uniq = {round(n.pitch) for n in notes2}
check("★ 同型同亮度的恒星被摊成多个音（原始 bug：48 条全同音）",
      len(uniq) >= 2 and len(notes2) <= StarVoiceSource.PER_PITCH_CAP + 1,
      "%d 条乐谱落在 %d 个不同音高（每桶上限 %d）"
      % (len(notes2), len(uniq), StarVoiceSource.PER_PITCH_CAP))

mixed = []
letters_all = "OBAFGKMLT"
for i in range(240):
    mixed.append(_FakeObj("m%d" % i, letters_all[i % len(letters_all)] + "5V",
                          1.0 + 0.02 * i, 30.0, (i * 3.0) % 360))
notes3 = StarVoiceSource().notes_from_objects(mixed, 180.0, 45.0)
import collections  # noqa: E402
cnt = collections.Counter(round(n.pitch) for n in notes3)
check("混合光谱型能产生足够丰富的音高（>= 8 个）", len(cnt) >= 8,
      "%d 条乐谱 / %d 个音高" % (len(notes3), len(cnt)))
check("同一音高的声部数不超过 PER_PITCH_CAP（超了就会糊成一个音）",
      max(cnt.values()) <= StarVoiceSource.PER_PITCH_CAP,
      "最多 %d 个" % max(cnt.values()))
# ★ 判据用**相对主音的音程**，不写死 Hz。
#   写死 1100 Hz 的后果：主音从 A2 提到 A3（为了让手机/笔记本喇叭放得出来）
#   之后它立刻假失败，而真正要知道的是"跨度有没有超过 3 个八度"。
_span = math.log2(max(n.pitch for n in notes3) / min(n.pitch for n in notes3))
check("音区跨度不超过 3 个八度（不扎耳朵）", _span <= 3.05,
      "跨度 %.2f 个八度（最高 %.0f Hz，最低 %.0f Hz；含 ±55 音分的桶内微分音铺开）"
      % (_span, max(n.pitch for n in notes3), min(n.pitch for n in notes3)))
check("★ 最低音不低于 200Hz（手机/笔记本小喇叭能放出来）",
      min(n.pitch for n in notes3) >= 200.0,
      "最低 %.0f Hz（原来是 110Hz：实测 20~250Hz 占能量 50%%，小喇叭等于没声）"
      % min(n.pitch for n in notes3))
check("每个音符都带了音阶级数（和声推进靠它，缺了就永远是同一个和弦）",
      all(n.degree >= 0 for n in notes3))
check("音符音级与音高自洽（pitch == pitch_hz(0, degree) × 音分偏移）",
      all(abs(SN.pitch_hz(0, n.degree) * 2.0 ** (n.cents / 1200.0) - n.pitch) < 1e-6
          for n in notes3))
check("所有音高都落在音阶上（桶内微分音允许 ±1 半音）",
      all(min(abs(12 * math.log2(n.pitch / SN.ROOT_HZ) - (pc + 12 * k))
              for k in range(-1, 7) for pc in SN.SCALE) <= 1.0 for n in notes3))
check("总增益做了归一（<= 4.0，不会被限幅压扁）",
      sum(n.gain for n in notes3) <= 4.0, "%.2f" % sum(n.gain for n in notes3))
check("地平线以下的天体不发声音",
      not src.notes_from_objects([_FakeObj("below", "G2V", 0.5, -5.0, 180.0)],
                                 180.0, 45.0))
check("方位决定声像：中心右侧的天体声像为正",
      src.notes_from_objects([_FakeObj("r", "G2V", 1.0, 40.0, 225.0)], 180.0, 45.0)[0].pan > 0)

print()
print("=" * 78)
print("D 接进主窗口（子进程宿主 / 面板 / 开关 / 跟随视角）")
print("=" * 78)
from ui.main_window import MainWindow   # noqa: E402

w = MainWindow()
w.resize(1400, 860)
w.show()
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)
check("左侧有「星图之声」面板", w.sonify_panel is not None)
check("初始为关闭（不打扰用户）", not w._music.enabled)
w.sonify_panel.btn_toggle.click()
app.processEvents()
check("点按钮能开启", w._music.enabled, w._music.engine.reason or "已启动")
check("★ 走的是子进程合成（不是同进程线程）",
      w._music.engine.degraded is False,
      "degraded=%s" % w._music.engine.degraded)
if w._music.enabled:
    for _ in range(30):
        app.processEvents()
        time.sleep(0.02)
    notes4, master, drone, mode = w._music.engine.brain.snapshot()
    check("开启后乐谱非空（真的在跟随星空发声）", len(notes4) > 0,
          "%d 条　音景 %s　音量 %.2f" % (len(notes4), mode, master))
    st = w._music.stats()
    check("★ 共享环被填上了（子进程真的在往里写 PCM）",
          st.get("fill_ms", 0) > 50, "水位 %.0f ms" % st.get("fill_ms", 0))
    hb0 = st.get("heartbeat", 0)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    hb1 = w._music.stats().get("heartbeat", 0)
    check("★ 子进程心跳在推进（持续合成，不是卡住）", hb1 > hb0,
          "%d → %d 块" % (hb0, hb1))
    w.sonify_panel.combo_mode.setCurrentIndex(1)
    app.processEvents()
    check("切换音景生效", w._music.mode == SN.MUSIC_MODES[1][0], w._music.mode)
    w.sonify_panel.chk_follow.setChecked(False)
    w._pump_sonify()
    app.processEvents()
    seed_notes = w._music.engine.brain.snapshot()[0]
    check("关掉跟随视角后改用独立音景（乐谱不再来自星表）",
          all(n.key.startswith("seed") for n in seed_notes) and len(seed_notes) > 0,
          "%d 条，key 样例 %s" % (len(seed_notes), [n.key for n in seed_notes[:3]]))
    w.sonify_panel.chk_follow.setChecked(True)
w.sonify_panel.btn_toggle.click()
app.processEvents()
check("再点按钮能关闭", not w._music.enabled)
w.toggle_star_music()
app.processEvents()
check("菜单入口 toggle_star_music() 与按钮同一条路径", w._music.enabled)
proc = w._music.engine._proc
w.close()
app.processEvents()
time.sleep(0.4)
check("关窗后音频子进程已结束（不留孤儿进程）",
      proc is None or proc.poll() is not None,
      "返回值 %s" % (proc.poll() if proc is not None else "已回收"))

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
