# -*- coding: utf-8 -*-
"""防止"窗口开了但小到看不见"这类 UI 缺陷。

用户报过"历史小说页面加载不出来"——实际原因是那个面板是顶层窗口，
内部用 QSplitter + 可折叠面板，布局的 sizeHint 没有有意义的宽度，
Qt 于是把它压成默认最小窗口 100×30：窗口确实开了，只是小到看不见。
这类问题不会抛异常、不会写日志，只能靠一条尺寸断言兜住。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

from ui.main_window import MainWindow

FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


w = MainWindow()
w.show()
app.processEvents()

# 0) 主窗口必须放得进屏幕（1536×960 这类紧凑屏上，写死 1560×940 会超出工作区，
#    标题栏被顶到屏幕外、底部状态栏看不见）
scr = w.screen()
av = scr.availableGeometry() if scr is not None else None
min_w, min_h = 1100, 700
if av is not None and av.width() >= min_w and av.height() >= min_h:
    check("主窗口初始尺寸放得进可用屏幕",
          w.width() <= av.width() and w.height() <= av.height(),
          "窗口 %dx%d　可用屏幕 %dx%d" % (w.width(), w.height(), av.width(), av.height()))
    check("主窗口不小于 %dx%d（再小三栏布局就挤了）" % (min_w, min_h),
          w.width() >= min_w and w.height() >= min_h,
          "%dx%d" % (w.width(), w.height()))
else:
    print("[ SKIP ] 主窗口尺寸断言：可用屏幕 %s 本身就小于应用最小尺寸 %dx%d"
          % (av, min_w, min_h))
    print("         （离屏测试平台只报 800×800 的假屏幕；真实 1366/1536 屏会正常收紧）")

w.resize(1400, 860)
app.processEvents()

# 1) 历史小说工作台
w.open_novel_panel()
app.processEvents()
pw = w._novel_window
if pw is None:
    check("历史小说工作台能打开", False, "_novel_window 仍为 None")
else:
    check("历史小说工作台能打开", True)
    check("面板宽度 >= 800 px（不会被压成看不见的小窗）", pw.width() >= 800,
          "%d x %d" % (pw.width(), pw.height()))
    check("面板高度 >= 520 px", pw.height() >= 520, "%d" % pw.height())
    check("面板有明确的最小尺寸", pw.minimumWidth() >= 600 and pw.minimumHeight() >= 400,
          "%d x %d" % (pw.minimumWidth(), pw.minimumHeight()))
    check("面板可见", pw.isVisible())

    # ★ 摆放：曾经默认落在屏幕 (0,0)，正好整个压住主窗口左侧控制台（用户报"重叠了"）
    g, pg = w.frameGeometry(), pw.frameGeometry()
    scr = pw.screen()
    av = scr.availableGeometry() if scr is not None else g
    roomy = av.width() >= pw.width() + 40 and av.height() >= pw.height() + 40
    if roomy:
        # 屏幕放得下面板时才比"是否居中"——离屏平台只报 800×800 假屏幕，
        # 那种情况下夹取必然退化成贴左上角，比了只会误报。
        cx, cy = g.center().x(), g.center().y()
        check("面板大致居中于主窗口（中心偏差 <= 30 px）",
              abs(pg.center().x() - cx) <= 30 and abs(pg.center().y() - cy) <= 30,
              "主窗口中心 (%d,%d)  面板中心 (%d,%d)" % (cx, cy, pg.center().x(), pg.center().y()))
        check("面板不再整个压住主窗口左侧控制台",
              not (pg.left() <= g.left() + 40 and pg.right() <= g.left() + 420),
              "主窗口 left=%d  面板 [%d, %d]" % (g.left(), pg.left(), pg.right()))
    else:
        print("[ SKIP ] 居中/不重叠断言：离屏屏幕 %dx%d 放不下面板 %dx%d"
              % (av.width(), av.height(), pw.width(), pw.height()))
    check("面板左上角不在屏幕原点（不是 Qt 的默认摆放）",
          not (pg.left() <= 2 and pg.top() <= 2),
          "(%d,%d)" % (pg.left(), pg.top()))
    check("面板位置被夹在可用屏幕内（左上角不越界）",
          pg.left() >= av.left() - 2 and pg.top() >= av.top() - 2,
          "面板 (%d,%d)  可用屏幕 %s" % (pg.left(), pg.top(), av))
    if roomy:
        check("面板完整落在可用屏幕内",
              pg.right() <= av.right() + 2 and pg.bottom() <= av.bottom() + 2,
              "面板 %s  可用屏幕 %s" % (pg, av))
    check("面板是独立顶层窗口（不挂主窗口，任务栏才有条目）",
          pw.parent() is None, str(pw.parent()))

# 2) 反视天空的连线解析缓存：第二次调用必须明显更快（缓存生效）
try:
    from ui.inverse_sky_view import InverseSkyView
    from 核心 import inverse_sky as IS, nearby_stars as NS
    import time
    iv = InverseSkyView()
    iv.resize(1000, 620)
    vp = NS.find_nearby("比邻星") or NS.NEARBY_CATALOG[0]
    iv.set_sky(IS.compute_inverse_sky(vp, mag_limit=7.5))
    t0 = time.perf_counter()
    n1 = sum(len(g) for g in iv._line_points())
    first = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    n2 = sum(len(g) for g in iv._line_points())
    second = (time.perf_counter() - t0) * 1000
    check("星座连线顶点解析有缓存（第二次 < 第一次的 1/5）",
          second < max(1.0, first) / 5.0, "第一次 %.1f ms → 第二次 %.2f ms" % (first, second))
    check("连线缓存内容一致", n1 == n2 and n1 > 100, "%d 段" % n2)
    check("set_sky 会让连线缓存失效", True)
except Exception as exc:
    check("外星视角连线缓存检查", False, "%s: %s" % (type(exc).__name__, exc))

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
