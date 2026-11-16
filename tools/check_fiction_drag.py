# -*- coding: utf-8 -*-
"""架空星图：生成之后 3D 视角能不能拖动（用户报"生成后拖不动"）。

★ 这条自检的存在理由（值得记住的排查过程）：
  用户说"架空星图生成后拖不动 3D 视角"，但用鼠标事件模拟拖拽时画面明明
  变了 2 万个采样点——因为在**测试脚本**里 `render()` 是强制绘制的，
  看不出"有没有人请求重绘"。
  真正的原因藏在静止判据里：`_emit_view()` 只发信号、不请求重绘；
  而重绘依赖 `_on_tick` 认为"内容变了"——它只认两种情况：① 时间在推进
  ② **有飞船在航线上**。而新生成的宇宙飞船数是 **0**（四档规模实测都是 0，
  飞船要用户手动"派遣"才有），于是 tick 每帧直接 return、从不重绘。
  拖拽改了 yaw/pitch 但画面一次没重画 = "拖不动"。
  一旦派一艘船，动画让每帧都重绘，同一个拖拽又"突然好了"。

所以本脚本断言的不是"像素变没变"，而是**"拖拽有没有把脏标记立起来"**。
"""
import io
import os
import sys
import math

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor, QMouseEvent, QWheelEvent
from PySide6.QtCore import QPoint, QPointF, Qt, QEvent

app = QApplication(sys.argv)
from ui.fiction_view import FictionView
from 核心 import world_generator as WG

W, H = 1200, 760
FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def send(w, kind, x, y, button=Qt.LeftButton):
    ev = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button,
                     button if kind != QEvent.MouseMove else Qt.NoButton, Qt.NoModifier)
    app.sendEvent(w, ev)


def diff(a, b, step=4):
    n = 0
    for y in range(0, a.height(), step):
        for x in range(0, a.width(), step):
            ca, cb = a.pixelColor(x, y), b.pixelColor(x, y)
            if abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + \
               abs(ca.blue() - cb.blue()) > 12:
                n += 1
    return n


v = FictionView()
v.resize(W, H)
v.show()
app.processEvents()
g = WG.WorldGenerator(seed=20260922, scale="medium", distribution="spiral").generate()
v.set_galaxy(g)
app.processEvents()

check("新生成的宇宙飞船数为 0（这正是 bug 的触发条件）", len(g.ships) == 0,
      "%d 艘" % len(g.ships))

# 模拟"静止状态"：先把脏标记清掉（等价于 _on_tick 刚跑过一轮、且没有飞船）
v._needs_repaint = False
app.processEvents()


def shot():
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0))
    v.render(img, QPoint(0, 0))
    return img


cx, cy = W // 2, H // 2
before = shot()
y0, p0 = v.yaw, v.pitch
send(v, QEvent.MouseButtonPress, cx, cy)
for i in range(1, 11):
    app.processEvents()
    send(v, QEvent.MouseMove, cx + i * 15, cy + i * 2)
send(v, QEvent.MouseButtonRelease, cx + 150, cy + 20)
app.processEvents()

check("左键拖拽改变了 yaw", abs(v.yaw - y0) > 0.3,
      "Δyaw=%.1f°" % math.degrees(v.yaw - y0))
check("左键拖拽改变了 pitch", abs(v.pitch - p0) > 0.01,
      "Δpitch=%.1f°" % math.degrees(v.pitch - p0))
check("★ 拖拽把脏标记立起来了（没有这一条画面就不会重绘）", v._needs_repaint)
after = shot()
check("拖拽后画面确实变了", diff(before, after) > 1000,
      "%d 个采样点" % diff(before, after))

# 右键拖拽（平移）也应立脏标记
v._needs_repaint = False
p1 = (v.pan_x, v.pan_y)
send(v, QEvent.MouseButtonPress, cx, cy, Qt.RightButton)
for i in range(1, 7):
    app.processEvents()
    send(v, QEvent.MouseMove, cx + i * 12, cy + i * 8, Qt.RightButton)
# ★ 释放点必须离最后一条 move 足够远（> 5 px），否则会被判成"右键点击"，
#   进而弹出 QMenu.exec() —— 那是嵌套事件循环，脚本会永远停在那里。
#   （第一版就是这么挂住的：release 只用 1 px 位移，触发了上下文菜单。）
send(v, QEvent.MouseButtonRelease, cx + 200, cy + 140, Qt.RightButton)
app.processEvents()
check("右键拖拽改变了平移量", (v.pan_x, v.pan_y) != p1,
      "(%.0f,%.0f)" % (v.pan_x, v.pan_y))
check("右键拖拽也立起了脏标记", v._needs_repaint)

# 滚轮缩放
v._needs_repaint = False
z0 = v.zoom
we = QWheelEvent(QPointF(cx, cy), QPointF(cx, cy), QPoint(0, 0), QPoint(0, 120),
                 Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
app.sendEvent(v, we)
app.processEvents()
check("滚轮改变了 zoom", abs(v.zoom - z0) > 1e-6, "%.3f → %.3f" % (z0, v.zoom))
check("滚轮也立起了脏标记", v._needs_repaint)

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
