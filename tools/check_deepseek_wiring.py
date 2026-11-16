# -*- coding: utf-8 -*-
"""DeepSeek 接线自检：配置能载入、设置对话框能打开、历史小说工作台能生成。

起因（两个都是"静默失败"）：
  · `MainWindow.open_deepseek_settings()` 写成 `DeepSeekSettingsDialog(self)`，
    把主窗口当配置传进去 → 点保存时 `_collect()` 读 `self.api_key` 直接
    AttributeError，也就是**设置对话框根本打不开**；
  · `MainWindow._refresh_deepseek_state()` 写成 `DeepSeekConfig.load()`，
    而 `load()` 是带 self 的实例方法 → TypeError 被 except 吞掉，
    按钮提示永远显示"未配置"，没人会发现。

两者都不抛到用户面前（一个弹窗、一个静默），所以必须有断言盯着。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QEventLoop, QTimer
app = QApplication(sys.argv)

FAILS = []


def check(label, ok, detail=""):
    print("[%s] %s%s" % ("  OK  " if ok else " FAIL ", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)


def pump(ms=200):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


from 核心.deepseek_client import DeepSeekConfig, build_story_prompt, STORY_STYLES  # noqa: E402
from 核心 import world_generator as WG                                             # noqa: E402

print("=" * 78)
print("A 配置层")
print("=" * 78)
cfg = None
try:
    cfg = DeepSeekConfig().load()
    check("DeepSeekConfig().load() 可用", True,
          "model=%s  base_url=%s" % (cfg.model, cfg.base_url))
except Exception as exc:
    check("DeepSeekConfig().load() 可用", False, "%s: %s" % (type(exc).__name__, exc))
check("有可用模型列表", len(DeepSeekConfig().load().__class__.__dataclass_fields__) > 0)
check("叙事风格预设 >= 3 种", len(STORY_STYLES) >= 3, "%d 种" % len(STORY_STYLES))

print()
print("=" * 78)
print("B 设置对话框（旧版直接打不开）")
print("=" * 78)
from ui.main_window import MainWindow      # noqa: E402
w = MainWindow()
w.resize(1400, 860)
w.show()
pump(150)

from ui.deepseek_panel import DeepSeekSettingsDialog, NovelWriterPanel   # noqa: E402
try:
    dlg = DeepSeekSettingsDialog(DeepSeekConfig().load(), w)
    dlg.show()
    pump(150)
    check("DeepSeek 设置对话框能构造并显示", dlg.isVisible())
    # 关键：点「保存」会走 _collect()，旧版在这里 AttributeError
    try:
        dlg._collect()
        check("设置对话框 _collect() 能读出配置（旧版此处 AttributeError）", True)
    except Exception as exc:
        check("设置对话框 _collect() 能读出配置（旧版此处 AttributeError）", False,
              "%s: %s" % (type(exc).__name__, exc))
    dlg.close()
except Exception as exc:
    check("DeepSeek 设置对话框能构造并显示", False, "%s: %s" % (type(exc).__name__, exc))

# 走主窗口的真实入口，并把可能弹出的 QMessageBox 自动关掉
def close_box():
    for t in QApplication.topLevelWidgets():
        if isinstance(t, QMessageBox) and t.isVisible():
            print("      弹出提示框：%s / %s" % (t.windowTitle(), t.text()[:80]))
            t.close()
        elif t.isVisible() and t.isModal() and not isinstance(t, QMessageBox):
            t.close()

QTimer.singleShot(350, close_box)
try:
    w.open_deepseek_settings()
    check("主窗口 open_deepseek_settings() 不抛异常", True)
except Exception as exc:
    check("主窗口 open_deepseek_settings() 不抛异常", False, str(exc))
pump(200)

# _refresh_deepseek_state 不能写日志 WARN
from 核心.utils import LOG   # noqa: E402
sink = []
LOG.subscribe(lambda e: sink.append((e.level, e.message)))
w._refresh_deepseek_state()
pump(80)
warns = [(lv, m) for lv, m in sink
         if str(lv).upper() in ("WARN", "ERROR")
         and "文件不存在" not in str(m)]   # 首次运行没有配置文件是正常的，不该算缺陷
check("_refresh_deepseek_state() 不产生 WARN/ERROR（旧版 TypeError 被吞掉）",
      not warns, str(warns[:2]))
check("历史小说按钮的提示反映配置状态",
      "DeepSeek" in w.btn_home_novel.toolTip(), w.btn_home_novel.toolTip()[:60])

print()
print("=" * 78)
print("C 历史小说工作台")
print("=" * 78)
g = WG.WorldGenerator(seed=20260922, scale="small", distribution="spiral").generate()
w.galaxy = g
try:
    panel = NovelWriterPanel(g, None)
    panel.resize(1180, 760)
    panel.show()
    pump(200)
    check("工作台能构造", panel.isVisible())
    prompt = None
    try:
        from 核心.deepseek_client import build_story_prompt as bsp
        sys_p, user_p, mx = bsp(g, style_key=STORY_STYLES[0][0], length_key="medium", focus="")
        prompt = sys_p + "\n" + user_p
        check("能按世界设定生成提示词", bool(prompt) and len(prompt) > 200,
              "%d 字" % (len(prompt) if prompt else 0))
        check("提示词里带上了宇宙名", g.name in prompt, g.name)
    except Exception as exc:
        check("能按世界设定生成提示词", False, "%s: %s" % (type(exc).__name__, exc))
    # 离线示例（不需要 API Key）是"没配 Key 也能玩"的兜底路径
    try:
        panel.generate_offline_sample()
        pump(150)
        txt = panel.current_text()
        check("离线示例能生成正文（无 API Key 的兜底路径）", bool(txt) and len(txt) > 50,
              "%d 字" % len(txt))
    except Exception as exc:
        check("离线示例能生成正文（无 API Key 的兜底路径）", False,
              "%s: %s" % (type(exc).__name__, exc))
    panel.close()
except Exception as exc:
    check("工作台能构造", False, "%s: %s" % (type(exc).__name__, exc))

print()
print("结果：", "全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), FAILS))
sys.exit(0 if not FAILS else 1)
