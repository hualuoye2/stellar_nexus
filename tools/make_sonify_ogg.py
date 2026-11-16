# -*- coding: utf-8 -*-
"""把样例 WAV 转成体积小得多的 OGG（随发布包一起给人试听用）。

用本机自带的 ffmpeg.exe（纯转码，和程序内的播放链路无关）。
用法：python tools/make_sonify_ogg.py [wav 目录] [输出目录]
"""
import io
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

FF = r"D:\deepseek\.tools\ffmpeg.exe"
src_dir = sys.argv[1] if len(sys.argv) > 1 else r"D:\deepseek\.videotmp"
out_dir = sys.argv[2] if len(sys.argv) > 2 else src_dir
os.makedirs(out_dir, exist_ok=True)

if not os.path.isfile(FF):
    print("找不到 ffmpeg：%s" % FF)
    sys.exit(1)

# 依次尝试几种 vorbis 编码器；哪个能用用哪个
ENCODERS = (["-c:a", "libvorbis", "-q:a", "4"],
            ["-c:a", "vorbis", "-strict", "-2", "-q:a", "4"])

for key, cn in (("星海", "试听_星海"), ("银河", "试听_银河"),
                ("静夜", "试听_静夜"), ("钢琴", "试听_钢琴"),
                ("吉他", "试听_吉他"), ("钟琴", "试听_钟琴")):
    wav = os.path.join(src_dir, "sample_%s.wav" % key)
    ogg = os.path.join(out_dir, "%s.ogg" % cn)
    if not os.path.isfile(wav):
        print("跳过（没有 %s）" % wav)
        continue
    done = False
    for enc in ENCODERS:
        r = subprocess.run([FF, "-y", "-loglevel", "error", "-i", wav] + enc + [ogg],
                           capture_output=True)
        if r.returncode == 0 and os.path.isfile(ogg) and os.path.getsize(ogg) > 1024:
            print("%-16s %8.1f KB  ←  %8.1f KB  （%.0f%%）"
                  % (os.path.basename(ogg), os.path.getsize(ogg) / 1024.0,
                     os.path.getsize(wav) / 1024.0,
                     100.0 * os.path.getsize(ogg) / os.path.getsize(wav)))
            done = True
            break
    if not done:
        print("转码失败：%s" % r.stderr.decode("utf-8", "replace")[:200])
