# -*- coding: utf-8 -*-
"""「旋转星图时音乐不中断」的回归自检。

为什么单独一条：这条判据是用户直接报的缺陷，而它**不可能靠"跑起来没报错"
发现**——音频一直在响，只是缓冲被抽干时会有听不见的微小断点。
所以必须量三个东西：

  1) **生产速率**（子进程产出音频秒 ÷ 墙上秒）。< 1 就是欠账，迟早断音。
     ★ 这个量法很重要：不要去采样"另一个线程里的缓冲长度"——那需要额外的
       Python 线程抢 GIL，读数本身被 GIL 偏差污染（实测同一时刻能报出
       0 字节和 697ms 两种水位）。用共享内存里的读写指针差，父进程只读两个
       float64，无锁无争用，读数才是可信的。
  2) **缓冲最低水位**。归零 = 声卡拿不到数据 = 听得见的断音。
  3) **对比基线**：同进程线程方案实测 0.81x / 归零 40.6%，
     子进程方案必须明显更好（本脚本断言 >= 1.0x 且归零率 < 5%）。

同时验证"拖拽时界面确实在重绘"（重绘次数 > 0），否则等于没施加负载。
"""
import io
import math
import os
import statistics
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.pop("QT_QPA_PLATFORM", None)     # 音频需要真实平台插件

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
from ui.main_window import MainWindow      # noqa: E402
from 核心 import sonify as SN              # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


w = MainWindow()
w.resize(1400, 860)
w.show()
for _ in range(40):
    app.processEvents()
    time.sleep(0.01)

# 先量一帧重绘有多贵（这决定了拖拽时主线程会占用多少 GIL）
img = QImage(w.real_view.width(), w.real_view.height(),
             QImage.Format_ARGB32_Premultiplied)
frame_ms = []
for _ in range(5):
    img.fill(QColor(0, 0, 0))
    t0 = time.perf_counter()
    w.real_view.render(img, QPoint(0, 0))
    frame_ms.append((time.perf_counter() - t0) * 1000.0)
frame_med = statistics.median(frame_ms)
print("现实星图单帧耗时（中位）：%.1f ms（拖拽负载就来自这里）" % frame_med)

# 开启星图之声
w.sonify_panel.btn_toggle.click()
for _ in range(80):
    app.processEvents()
    time.sleep(0.01)
host = w._music.engine
check("音频走子进程合成（不是同进程线程）", host.degraded is False,
      "degraded=%s reason=%s" % (host.degraded, host.reason or "OK"))
if host.degraded:
    print("结果： 失败（退化成同进程线程，本自检的结论不成立）")
    sys.exit(1)

# 采样共享内存水位（只读两个 float64，无锁）
fills = []
hb = []
lag = []
rv = w.real_view
cx, cy = rv.width() // 2, rv.height() // 2
last_t = [time.perf_counter()]
repaints = [0]


def on_tick():
    now = time.perf_counter()
    lag.append((now - last_t[0]) * 1000.0)
    last_t[0] = now
    st = w._music.stats()
    fills.append(st.get("fill_ms", 0.0))
    hb.append(st.get("heartbeat", 0.0))


probe = QTimer()
probe.setInterval(10)
probe.timeout.connect(on_tick)
probe.start()

orig_render = rv.paintEvent


def counting_paint(event):
    repaints[0] += 1
    return orig_render(event)


rv.paintEvent = counting_paint

DURATION = 6.0


def mk(t, x, y, b):
    return QMouseEvent(t, QPointF(x, y), QPointF(x, y), b,
                       b if t != QMouseEvent.MouseMove else Qt.NoButton, Qt.NoModifier)


print()
print("拖拽旋转 %.0f 秒（真实鼠标事件 + 真实重绘），同时采共享内存水位……" % DURATION)
hb0 = w._music.stats().get("heartbeat", 0.0)
t_start = time.perf_counter()
app.sendEvent(rv, mk(QMouseEvent.MouseButtonPress, cx, cy, Qt.LeftButton))
i = 0
while time.perf_counter() - t_start < DURATION:
    i += 1
    x = cx + 260 * math.sin(i * 0.09)
    y = cy + 90 * math.cos(i * 0.05)
    app.sendEvent(rv, mk(QMouseEvent.MouseMove, x, y, Qt.LeftButton))
    app.processEvents()
    time.sleep(0.004)
app.sendEvent(rv, mk(QMouseEvent.MouseButtonRelease, cx, cy, Qt.LeftButton))
elapsed = time.perf_counter() - t_start
hb1 = w._music.stats().get("heartbeat", 0.0)
probe.stop()

block_ms = SN.BLOCK_FRAMES * 1000.0 / host.sample_rate
produced = (hb1 - hb0) * block_ms / 1000.0
rate = produced / max(1e-9, elapsed)

print()
print("=" * 74)
print("结果")
print("=" * 74)
print("  拖拽移动事件 %d 个　实际重绘 %d 帧　主线程 tick 中位 %.0f ms"
      % (i, repaints[0], statistics.median(lag) if lag else -1))
check("拖拽期间界面确实在重绘（否则等于没施加负载）", repaints[0] > 0,
      "%d 帧" % repaints[0])
check("★ 生产速率 >= 1.0x（子进程产出音频秒 / 墙上秒）", rate >= 1.0,
      "实测 %.2f x（%.2f 秒音频 / %.2f 秒墙上）；基线：同进程线程 0.81x" %
      (rate, produced, elapsed))
if fills:
    zeros = sum(1 for f in fills if f <= 1.0)
    ratio = 100.0 * zeros / len(fills)
    print("  共享环水位：最低 %.0f ms / 中位 %.0f ms（容量 %.0f ms）"
          % (min(fills), statistics.median(fills),
             SN.PCM_BYTES / 4.0 / host.sample_rate * 1000))
    check("★ 缓冲没有归零（归零率 < 5%）", ratio < 5.0,
          "归零 %d/%d = %.1f%%；基线：同进程线程 40.6%%" % (zeros, len(fills), ratio))
    check("缓冲最低水位 >= 100 ms（有存量顶过界面卡顿）", min(fills) >= 100.0,
          "最低 %.0f ms" % min(fills))
else:
    check("采到了缓冲水位样本", False, "0 个样本")

w.sonify_panel.btn_toggle.click()
app.processEvents()
time.sleep(0.3)
w.close()
print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
