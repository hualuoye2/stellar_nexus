# -*- coding: utf-8 -*-
"""离线导出星图之声的样例 WAV，供人工试听。

用法：python tools/make_sonify_sample.py [输出目录]

导出三轨（每轨约 20 秒，44.1kHz 16bit 立体声）：
    sample_starry.wav  星海（跟随视角：当前时刻北京上空真实的星表）
    sample_galaxy.wav  银河（银道面低频持续音为主）
    sample_quiet.wav   静夜（极少音符、长衰减）

为什么要有这个脚本：合成器"没报错"不等于"好听"。
自动化断言只能保证音高落在五声音阶、电平不削顶、声部会淡出；
**"像不像轻音乐"只能靠耳朵**。所以把音频落成文件，让人自己听。
"""
import io
import math
import os
import sys
import wave

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])

from ui.main_window import MainWindow
from ui.sonify_panel import StarVoiceSource
from 核心 import sonify as SN

out_dir = sys.argv[1] if len(sys.argv) > 1 else r"D:\deepseek\.videotmp"
os.makedirs(out_dir, exist_ok=True)

w = MainWindow()
w.resize(1400, 860)
w.show()
for _ in range(30):
    app.processEvents()
w.real_view.recalculate(force=True)


src = StarVoiceSource()
SECONDS = 36
FPS = 10.0                       # 乐谱每秒更新 10 次（与界面一致）
# ★ 离线渲染用 SonifyEngine（同进程合成器），而不是 UI 里那个 SonifyHost：
#   Host 只往子进程的共享内存里写参数，它自己不合成，没有 render_block()。
#   和声推进现在在 render_block 内部按拍点走，所以离线渲染也能听到换和弦。
eng = SN.SonifyEngine()
BLOCK = SN.BLOCK_FRAMES

# 要导出的组合：(音景, 乐器, 文件名后缀)
TRACKS = (
    ("starry", "pad", "星海"),
    ("galaxy", "pad", "银河"),
    ("quiet", "pad", "静夜"),
    ("starry", "epiano", "钢琴"),
    ("starry", "guitar", "吉他"),
    ("starry", "bell", "钟琴"),
)

for mode, inst, tag in TRACKS:
    w._music.set_mode(mode)
    w._music.set_instrument(inst)
    w._music.set_follow_view(mode != "galaxy")   # 银河模式用独立持续音
    eng.brain.set_master(0.55)
    eng.brain.set_mode(mode)
    eng.brain.set_instrument(inst)
    eng._sync_instrument(inst)

    # 让"跟随视角"真的慢慢转起来——这才能听出"和声随天区变化"
    frames = []
    total_blocks = int(SECONDS * eng.sample_rate / BLOCK) + 1
    obj_all = w.real_view._all_objects()
    for b in range(total_blocks):
        if b % max(1, int(eng.sample_rate / BLOCK / FPS)) == 0:
            t = b / total_blocks
            w.real_view.center_az = (180.0 + 120.0 * math.sin(2 * math.pi * t)) % 360.0
            if mode == "galaxy":
                w._music._regenerate()
                eng.brain.set_notes(w._music._seed_notes)
                eng.brain.set_drone(w._music._drone_level(w.real_view.center_alt))
            else:
                notes = src.notes_from_objects(obj_all, w.real_view.center_az,
                                               w.real_view.center_alt)
                eng.brain.set_notes(notes)
                eng.brain.set_drone(w._music._drone_level(w.real_view.center_alt))
        frames.append(eng.render_block())
    pcm = b"".join(frames)
    path = os.path.join(out_dir, "sample_%s.wav" % tag)
    with wave.open(path, "wb") as fp:
        fp.setnchannels(2)
        fp.setsampwidth(2)
        fp.setframerate(eng.sample_rate)
        fp.writeframes(pcm)
    arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    print("%-24s %6.2f s  峰值 %.3f  RMS %.4f  %d 字节"
          % (os.path.basename(path), len(arr) / 2 / eng.sample_rate,
             float(np.abs(arr).max()), float(np.sqrt((arr ** 2).mean())), len(pcm)))
    w._music.enabled = False

w.close()
print()
print("试听提示：")
print("  · 星海 是主角——它跟随视角，声像会随天体方位左右移动")
print("  · 银河 低频最厚，适合当背景；静夜 音符很少")
print("  · 钢琴/吉他/钟琴 是同一片星空换乐器：能听出明确的音头与旋律走向")
print("  · 如果觉得吵，把面板音量降到 20%~30%")
