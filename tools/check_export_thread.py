# -*- coding: utf-8 -*-
"""端到端验证：新导出路径（主线程渲染 + 后台编码）真的能在**有 QThread 的
真实事件循环**里跑出 MP4/GIF，而且不崩。

这条自检的存在理由：用户报过"生成 MP4 程序会崩溃闪退"——根因是渲染
（set_time / recalculate / widget.grab）被放在工作线程里。这类崩溃是
**进程级**的，单元测试里 try/except 拦不住，所以必须真的跑一遍编码。
"""
import io
import os
import sys
import time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
app = QApplication(sys.argv)

from ui.real_sky_view import RealSkyView
from 核心 import export_media as EM

FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def build_view():
    v = RealSkyView()
    v.resize(640, 360)
    v.set_observer(39.9042, 116.4074, "北京")
    v.set_time(datetime(2026, 9, 22, 22, 0, 0), keep_offset=False)
    v.show_legend = False
    v.show_fps = False
    v.recalculate(force=True)
    return v


def run_export(job, view, timeout_s=180.0):
    """在主线程跑控制器，用真实事件循环等它结束。"""
    ctl = EM.NightExportController(view, view.observer)
    box = {"done": False, "ok": False, "note": ""}

    def on_done(ok, note):
        box.update(done=True, ok=ok, note=note)
        app.quit()

    ctl.finished_ok.connect(on_done)
    ctl.start(job)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(app.quit)
    guard.start(int(timeout_s * 1000))
    app.exec()
    return box


print("=" * 78)
print("A MP4（需要 ffmpeg）")
print("=" * 78)
out_mp4 = r"D:\deepseek\.videotmp\check_export.mp4"
if os.path.isfile(out_mp4):
    os.remove(out_mp4)
exe = EM.find_ffmpeg()
print("  ffmpeg:", exe or "（没找到）")
if exe:
    view = build_view()
    frames = EM.night_frame_times(datetime(2026, 9, 22, 22, 0, 0), 20.0, 2.0, 120.0)
    print("  帧数:", len(frames))
    job = EM.EncodeJob(path=out_mp4, frames=frames, width=640, overlay="北京",
                       video=True, fps=12.0, crf=26)
    t0 = time.perf_counter()
    box = run_export(job, view)
    dt = (time.perf_counter() - t0) * 1000
    size = os.path.getsize(out_mp4) / 1024 if os.path.isfile(out_mp4) else 0
    print("  结果: ok=%s note=%s" % (box["ok"], box["note"]))
    print("  用时 %.0f ms，文件 %.0f KB" % (dt, size))
    check("MP4 导出成功（不再闪退）", box["done"] and box["ok"], box["note"])
    check("MP4 文件非空", size > 5, "%.0f KB" % size)
    check("视图时间被还原", view.observer.when == datetime(2026, 9, 22, 22, 0, 0),
          str(view.observer.when))
else:
    print("  跳过 MP4（本机没有 ffmpeg）")

print()
print("=" * 78)
print("B GIF（不需要 ffmpeg）")
print("=" * 78)
out_gif = r"D:\deepseek\.videotmp\check_export.gif"
if os.path.isfile(out_gif):
    os.remove(out_gif)
view = build_view()
frames = EM.night_frame_times(datetime(2026, 9, 22, 22, 0, 0), 20.0, 2.0, 180.0)
job = EM.EncodeJob(path=out_gif, frames=frames, width=320, overlay="北京",
                   video=False, delay_ms=150, max_colors=128)
box = run_export(job, view)
size = os.path.getsize(out_gif) / 1024 if os.path.isfile(out_gif) else 0
print("  结果: ok=%s note=%s" % (box["ok"], box["note"]))
check("GIF 导出成功", box["done"] and box["ok"], box["note"])
check("GIF 文件非空", size > 5, "%.0f KB" % size)

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
