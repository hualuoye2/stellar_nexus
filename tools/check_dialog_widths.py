# -*- coding: utf-8 -*-
"""扫描所有工具窗口的"最小宽度"，找出在 1366×768 笔记本上会跑出屏幕的那些。

起因：观测计划表实测最小宽度 **1944 px**——顶部一行状态文字（地点/日期/暗空等级/
暗夜/月相/可用目标）是单行 QLabel 且 `wordWrap=False`，sizeHint 就是那行文字的像素宽，
于是整个对话框被撑到 1944 px。用户的屏幕只有约 1312 px（1366 宽、125% 缩放），
窗口两侧按钮直接被推到屏幕外——属于"不抛异常、不写日志"的缺陷，只能靠宽度断言抓。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication, QDialog, QWidget
from PySide6.QtCore import QEventLoop, QTimer
app = QApplication(sys.argv)

from ui.main_window import MainWindow
from 核心 import world_generator as WG

#: 1366 宽、125% 缩放的笔记本 → 可用约 1093 px；这里给到 1280 作为"必须放得下"的底线
MAX_W = 1280
FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def pump(ms=250):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


w = MainWindow()
w.resize(1400, 860)
w.show()
pump(150)
# 需要一个宇宙，历史推演才打得开
w.galaxy = WG.WorldGenerator(seed=20260922, scale="small", distribution="spiral").generate()
print("可用屏幕（离屏平台是假的 800×800，只用于参考）:",
      w.screen().availableGeometry() if w.screen() else None)
print("判定底线：最小宽度必须 <= %d px" % MAX_W)
print()

builders = []
try:
    from ui.planner_panel import PlannerDialog
    builders.append(("观测计划表", lambda: PlannerDialog(
        w.real_view.observer, bortle=w.real_view.bortle_level,
        horizon=w.horizon_profile, parent=w)))
except Exception as exc:
    print("跳过观测计划表：%s" % exc)
try:
    from ui.object_browser import ObjectBrowserDialog
    builders.append(("全天天体总览", lambda: ObjectBrowserDialog(w.real_view.observer, w)))
except Exception as exc:
    print("跳全天体总览：%s" % exc)
try:
    from ui.sky_calendar import SkyCalendarDialog, TimeTravelDialog, HorizonDialog
    builders.append(("天象日历", lambda: SkyCalendarDialog(w.real_view.observer, w)))
    builders.append(("时间旅行", lambda: TimeTravelDialog(w.real_view.observer, w)))
    builders.append(("地平障碍", lambda: HorizonDialog(w.real_view.observer, w,
                                                    w.horizon_profile)))
except Exception as exc:
    print("跳过日历族：%s" % exc)
try:
    from ui.history_panel import HistorySimDialog
    builders.append(("历史推演", lambda: HistorySimDialog(w.galaxy, w)))
except Exception as exc:
    print("跳过历史推演：%s" % exc)
try:
    from ui.export_panel import ExportDialog
    builders.append(("导出对话框", lambda: ExportDialog(w.real_view, w.real_view.observer, w)))
except Exception as exc:
    print("跳过导出：%s" % exc)
try:
    from ui.inverse_sky_panel import InverseSkyDialog
    builders.append(("外星视角", lambda: InverseSkyDialog(w)))
except Exception as exc:
    print("跳过外星视角：%s" % exc)
try:
    from ui.deepseek_panel import DeepSeekSettingsDialog
    from 核心.deepseek_client import DeepSeekConfig
    builders.append(("DeepSeek 设置", lambda: DeepSeekSettingsDialog(DeepSeekConfig().load(), w)))
except Exception as exc:
    print("跳过 DeepSeek 设置：%s" % exc)

print("%-16s %8s %8s  %s" % ("窗口", "最小宽", "最小高", "判定"))
for name, fn in builders:
    try:
        dlg = fn()
        dlg.show()
        # ★ 关键：必须等"计划真正生成完"再量。观测计划表的最小宽度是**内容相关**的——
        #   计划生成之前 lbl_state 是空串、最小宽 1094；生成之后它被塞进 132 个字的
        #   摘要，最小宽才跳到 1944。只量构造后的瞬间会完全看不出这个缺陷。
        for attr in ("_recompute", "refresh", "run"):
            fn2 = getattr(dlg, attr, None)
            if callable(fn2):
                try:
                    fn2()
                except Exception:
                    pass
                break
        pump(1200)
        mw, mh = dlg.minimumWidth(), dlg.minimumHeight()
        ok = mw <= MAX_W
        print("%-16s %8d %8d  %s" % (name, mw, mh, "OK" if ok else "太宽！"))
        check("%s 最小宽度 <= %d px" % (name, MAX_W), ok,
              "实测 %d px（屏幕放不下时两侧按钮会被推到屏幕外）" % mw)
        dlg.close()
        dlg.deleteLater()
        pump(60)
    except Exception as exc:
        check("%s 能构造" % name, False, "%s: %s" % (type(exc).__name__, exc))

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
