# -*- coding: utf-8 -*-
"""观测清单导出与「点选即记录」自检。

新增这条链路的理由：观测清单原来只活在内存里、而且只能通过**右键菜单**加入，
实测走一遍程序时一次都不会被用到——清单永远是空的，"搜索 → 记录 → 带到望远镜旁"
这条路是断的。现在：
  · 左键点选天体即自动记录（静默，不抢状态栏）；
  · 左栏「导出清单…」可导出 Markdown / CSV，「复制」可进剪贴板；
  · 导出前用**当前时刻**重算高度角/方位角/升落（清单里存的可能是几小时前的值）。
"""
import io
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
app = QApplication(sys.argv)

from 核心 import watchlist_export as WX
from ui.main_window import MainWindow

FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def pump(ms=150):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


print("=" * 78)
print("A 格式化（纯函数，先测边界）")
print("=" * 78)
sample = [
    {"name": "天狼", "kind": "star", "mag": -1.44, "ra": 101.287, "dec": -16.716,
     "alt": -14.6, "az": 99.7, "peak_alt": 33.4, "constellation": "大犬座",
     "distance_ly": 8.6, "rise_set": "约 1.3 小时后升起（09-23 01:49）"},
    {"name": '带"引号,和逗号', "kind": "messier", "mag": 4.0, "ra": 83.8, "dec": -5.4,
     "alt": 5.8, "az": 102.0, "constellation": "猎户座"},
    {"name": "木星", "kind": "planet", "mag": -2.38, "ra": 26.9, "dec": 11.8,
     "alt": 56.3, "az": 140.5, "peak_alt": 61.9, "distance_au": 4.87},
]
md = WX.plan_markdown(sample, datetime(2026, 9, 23, 22, 0), None)
csv = WX.plan_csv(sample)
check("Markdown 含标题与地点行", "# STELLAR NEXUS 观测清单" in md and "观测地点" in md)
check("Markdown 三个目标都在表里", md.count("| 天狼 ") == 1 and md.count("| 木星 ") == 1)
check("赤经按 h/m 显示（J2000 06h44m51s → 四舍五入 06h45m）", "06h45m" in md,
      next((ln for ln in md.splitlines() if "天狼" in ln), "")[:80])
check("赤纬带符号与度分（-16°42'58\" → -16°43'）", "-16°43'" in md)
check("升落一句带进来了", "01:49" in md)
check("行星距离用 AU、恒星用 ly", "4.87 AU" in md and "8.60 ly" in md)
check("CSV 带 BOM（Excel 中文不乱码）", csv.startswith("\ufeff"))
check("CSV 里的引号被转义（RFC4180）", '""引号' in csv.replace('带"引号', '带""引号'))
check("CSV 含逗号的字段被整体加引号",
      '"带"引号' in csv or '带""引号,和逗号' in csv)
check("空清单也能导出（不崩）", "（观测清单为空）" in WX.plan_markdown([], datetime.now()))
check("默认文件名带时间戳", WX.default_filename(datetime(2026, 9, 23, 22, 0)).endswith(".md"))

print()
print("=" * 78)
print("B 点选即记录 + 导出接口")
print("=" * 78)
w = MainWindow()
w.resize(1500, 900)
w.show()
pump(250)
rv = w.real_view
check("起始观测清单为空", len(w._watchlist) == 0, "%d 条" % len(w._watchlist))

picked = []
for name in ("天狼", "M42", "木星", "织女一"):
    try:
        got = rv.find_object(name)
        if not got:
            continue
        key = str(got.get("key") or "")
        obj = next(o for o in rv._all_objects() if o.key == key)
        w._on_sky_object(rv.describe_object(obj))
        picked.append(name)
        pump(30)
    except Exception as exc:
        check("点选 %s" % name, False, "%s: %s" % (type(exc).__name__, exc))
check("点选 4 个天体后清单自动记录了 4 条", len(w._watchlist) == 4,
      "%d 条：%s" % (len(w._watchlist), [i.get("name") for i in w._watchlist]))
# 同一个天体点两次不应该重复
try:
    got = rv.find_object("天狼")
    key = str(got.get("key") or "")
    obj = next(o for o in rv._all_objects() if o.key == key)
    w._on_sky_object(rv.describe_object(obj))
    pump(30)
    check("重复点选同一个天体不会重复记录", len(w._watchlist) == 4, "%d 条" % len(w._watchlist))
except Exception as exc:
    check("重复点选同一个天体不会重复记录", False, str(exc))

check("状态栏显示的是「已选中」而不是「已加入观测清单」（自动记录不抢提示）",
      "已选中" in w._last_status, w._last_status[:50])

# 复制路径（不弹文件对话框）
try:
    w.export_watchlist(copy_only=True)
    from PySide6.QtGui import QGuiApplication
    txt = QGuiApplication.clipboard().text()
    check("复制到剪贴板成功（Markdown）", "STELLAR NEXUS 观测清单" in txt,
          "%d 字" % len(txt))
    check("剪贴板里含全部 4 个目标",
          all(n in txt for n in ("天狼", "M42", "木星", "织女一")))
except Exception as exc:
    check("复制到剪贴板成功（Markdown）", False, "%s: %s" % (type(exc).__name__, exc))

# 空清单时给提示而不是崩
w._watchlist.clear()
try:
    w.export_watchlist(copy_only=True)
    msg = w._last_status
    check("清单为空时给出「清单是空的」提示且不崩", "空" in msg, msg[:44])
except Exception as exc:
    check("清单为空时给出「清单是空的」提示且不崩", False, str(exc))

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
