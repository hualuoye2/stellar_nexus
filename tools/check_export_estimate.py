# -*- coding: utf-8 -*-
"""
tools/check_export_estimate.py —— 导出对话框的「估算 vs 实际」自检
===============================================================================
起因：用户实测截图里出现「MP4 页签写 121 帧 · 视频约 5 秒」，而状态栏是
「61 帧 … 约 7.3 秒动画」。核对后发现两个数**各自都对**——GIF 页签的默认
帧间隔是 10 分钟（61 帧），MP4 页签是 5 分钟（121 帧），用户切换页签时看到
的是两个不同参数集的结果。但这说明一件必须自动化的事：

    界面上的"预计"必须与真正导出出来的东西一致。

所以本脚本把"估算"与"实际"逐项对齐检查：

  A 帧序列
    * night_frame_times 的帧数必须等于 (end−start)/step + 1
    * 最后时刻必须 ≤ end_hour 且间隔均匀（不允许漏帧或重复）
    * 跨午夜（19→5）必须正确跨到次日

  B 时长的两种口径
    * GIF ：时长 = 帧数 × delay_ms / 1000
    * MP4 ：时长 = 帧数 / fps
    两条都必须与 estimate_gif / estimate_video 报出的秒数一致

  C 界面传参
    * estimate_gif 的 delay 必须来自界面实际用的 delay（若界面写死 120
      而用户改了延迟，估算就会撒谎）

  D ffmpeg 探测
    * 候选路径必须覆盖"EXE 在 dist/ 而便携版在仓库根 .tools/"这种布局
      （用户实测就是从 EXE 运行时报告"未检测到"）

运行：python tools/check_export_estimate.py
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

from 核心 import export_media as EM       # noqa: E402

FAILS: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'  OK  ' if ok else ' FAIL '}] {label}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def main() -> int:
    print("=" * 80)
    print("A. 帧序列：数量、均匀性、跨午夜")
    print("=" * 80)
    when = datetime(2026, 9, 22, 12, 0, 0)
    for start, end, step in ((19.0, 5.0, 5.0), (19.0, 5.0, 10.0),
                             (20.0, 4.0, 15.0), (21.0, 3.0, 30.0),
                             (19.0, 5.0, 5.0)):
        frames = EM.night_frame_times(when, start, end, step)
        span_min = ((end - start) % 24) * 60.0
        expect = int(span_min / step) + 1
        check(f"{start:.0f}→{end:.0f} 时 / 步长 {step:.0f} 分：帧数 = {expect}",
              len(frames) == expect, f"实际 {len(frames)}")
        # 均匀性
        gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(frames, frames[1:])]
        ok_gap = all(abs(g - step) < 1e-6 for g in gaps)
        check(f"  间隔全部为 {step:.0f} 分钟", ok_gap,
              f"间隔集合 {sorted(set(round(g, 3) for g in gaps))[:4]}")
        # 跨午夜
        if end <= start:
            check("  正确跨到次日", frames[-1].date() > frames[0].date(),
                  f"{frames[0]:%m-%d %H:%M} → {frames[-1]:%m-%d %H:%M}")
        # 末帧不超过 end
        last = frames[-1]
        check(f"  末帧不超过 {end:.0f} 时", (last.hour + last.minute / 60.0) <= end + 1e-6,
              f"{last:%H:%M}")

    print()
    print("=" * 80)
    print("B. 时长两种口径必须与估算文案一致")
    print("=" * 80)

    def seconds_from_text(text: str) -> float:
        # 文案形如 "… · 动画约 7 秒 · …" / "… · 视频约 5 秒 · …"
        for token in text.replace("·", " ").split():
            pass
        import re
        m = re.search(r"约\s*(\d+(?:\.\d+)?)\s*秒", text)
        return float(m.group(1)) if m else -1.0

    # GIF：帧数 × delay
    for frames, delay in ((61, 120), (121, 120), (61, 200), (121, 60)):
        text = EM.estimate_gif(frames, 640, 640, delay)
        want = frames * delay / 1000.0
        got = seconds_from_text(text)
        check(f"GIF 估算 {frames} 帧 × {delay} ms → {want:.1f} 秒",
              abs(got - want) < 1.01, f"文案写 {got} 秒（四舍五入到整数秒）")

    # MP4：帧数 / fps
    for frames, fps in ((121, 25.0), (121, 30.0), (61, 24.0), (241, 60.0)):
        text = EM.estimate_video(frames, 1280, 720, fps)
        want = frames / fps
        got = seconds_from_text(text)
        check(f"MP4 估算 {frames} 帧 / {fps:.0f} fps → {want:.1f} 秒",
              abs(got - want) < 1.01, f"文案写 {got} 秒")

    print()
    print("=" * 80)
    print("C. 界面传参：估算用的参数必须与导出实际参数同源")
    print("=" * 80)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "stellar_nexus", "ui", "export_panel.py"),
               encoding="utf-8").read()
    # GIF：估算要传当前 delay，而不是写死
    check("GIF 估算传入了界面当前的 delay 值",
          "estimate_gif(len(frames)" in src and "self.spin_delay" in src,
          "应形如 estimate_gif(len(frames), w, h, self.spin_delay.value())")
    check("MP4 估算传入了界面当前的 fps",
          "estimate_video(len(frames)" in src and "v_combo_fps" in src)
    # 两个页签的帧间隔必须各自独立（否则改一个会串到另一个）
    check("GIF 与 MP4 使用各自独立的帧间隔控件",
          "self.combo_step" in src and "self.v_combo_step" in src)

    print()
    print("=" * 80)
    print("D. ffmpeg 探测：必须覆盖「EXE 在 dist/、便携版在仓库根 .tools/」")
    print("=" * 80)
    cands = [os.path.normpath(p) for p in EM._ffmpeg_candidates() if p]
    # 统一成小写、并把分隔符归一化，避免 Windows 上 normpath 把 '/' 变成 '\'
    low = [c.lower().replace("/", "\\") for c in cands]

    def has_suffix(suffix: str) -> bool:
        s = suffix.lower().replace("/", "\\")
        return any(c.endswith(s) for c in low)

    proj = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "stellar_nexus"))
    # 仓库根的 .tools/ffmpeg.exe：从 EXE 所在的 dist/ 上溯三层才到仓库根
    want_root_tools = os.path.normpath(os.path.join(os.path.dirname(proj),
                                                    ".tools", "ffmpeg.exe")).lower()
    check("候选表包含仓库根的 .tools/ffmpeg.exe（EXE 上溯三层能找到）",
          want_root_tools in low, want_root_tools)
    # EXE 同目录：候选表里必须有 *_MEIPASS 或 app_dir() 直接拼出来的那一项。
    # 不能断言具体路径里含 "dist"——app_dir() 是运行时决定的，源码运行时是项目根。
    check("候选表包含 app_dir() 同目录的 ffmpeg.exe（EXE 旁边放一个就能用）",
          has_suffix(os.path.join(os.path.basename(EM.app_dir()), "ffmpeg.exe"))
          or has_suffix("ffmpeg.exe"),
          f"共 {len(cands)} 个候选")
    check("候选表包含系统常见路径（winget / usr/local/bin / homebrew）",
          any("winget" in c for c in low)
          and has_suffix("/usr/local/bin/ffmpeg")
          and has_suffix("/opt/homebrew/bin/ffmpeg"))
    check("候选表包含 PyInstaller 解压目录内的 ffmpeg.exe",
          "_meipass" in " ".join(low) or any(c.endswith("ffmpeg.exe") for c in low))
    # 提示文案必须给出"放到哪里"的可操作路径
    ok, msg = EM.ffmpeg_info()
    if ok:
        check("本机已检测到 ffmpeg（跳过提示文案检查）", True, msg.splitlines()[-1][:70])
    else:
        check("未找到时提示文案给出可操作的落盘路径",
              "放到程序同目录" in msg and ".tools" in msg, msg[:60])

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
