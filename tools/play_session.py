# -*- coding: utf-8 -*-
"""开发期工具：像真实用户一样把整程序走一遍，记录体验问题。

流程：
  1) 启动 → 现实星图：定位、搜索、点击、缩放、时间推移、图层开关、升落信息
  2) 切到太阳邻域：旋转、选中近星、筛选、着色、跳回星图
  3) 切到架空星图：生成宇宙、选中星系、规划航线、派船、推进时间、切换模式
  4) 打开历史小说工作台与天体总览
  5) 打印每一步的耗时、状态栏文本、异常与需要改进的地方
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "stellar_nexus")))

import datetime                                          # noqa: E402

from PySide6.QtCore import QEventLoop, QPoint, QPointF, QTimer, Qt   # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent                   # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

ISSUES = []


def note(text: str) -> None:
    ISSUES.append(text)
    print("      ⚠ " + text)


def main() -> int:
    app = QApplication(sys.argv[:1])
    try:
        app.setStyle("Fusion")
        from 核心.utils import apply_dark_theme
        apply_dark_theme(app)
    except Exception:
        pass

    from ui.main_window import MainWindow
    from 核心 import world_generator as WG
    from 核心.utils import LOG

    win = MainWindow()
    win.resize(1500, 900)
    win.show()

    def pump(ms: int = 120) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def drag(widget, x0, y0, x1, y1, button=Qt.LeftButton, steps=6):
        pos = QPointF(x0, y0)
        press = QMouseEvent(QMouseEvent.MouseButtonPress, pos, button, button, Qt.NoModifier)
        widget.mousePressEvent(press)
        for i in range(1, steps + 1):
            k = i / steps
            pos = QPointF(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k)
            widget.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, pos, Qt.NoButton,
                                              button, Qt.NoModifier))
        rel = QMouseEvent(QMouseEvent.MouseButtonRelease, QPointF(x1, y1), button,
                          Qt.NoButton, Qt.NoModifier)
        widget.mouseReleaseEvent(rel)

    def wheel(widget, x, y, delta):
        widget.wheelEvent(QWheelEvent(QPointF(x, y), QPointF(x, y), QPoint(0, 0),
                                      QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
                                      Qt.ScrollUpdate, False))

    def click(widget, x, y, button=Qt.LeftButton):
        pos = QPointF(x, y)
        widget.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, pos, button,
                                           button, Qt.NoModifier))
        widget.mouseReleaseEvent(QMouseEvent(QMouseEvent.MouseButtonRelease, pos, button,
                                             Qt.NoButton, Qt.NoModifier))

    print("=" * 78)
    print("① 启动与初始状态")
    print("=" * 78)
    t0 = time.perf_counter()
    pump(600)
    print(f"   窗口 {win.width()}x{win.height()}，模式 = {win.mode}，"
          f"启动耗时 {(time.perf_counter() - t0) * 1000:.0f} ms")
    print(f"   状态栏：{win._status_label.text()}")
    print(f"   工具栏按钮：{[b.text() for b in win.toolbar.findChildren(type(win._act_novel))]}")
    if not win.real_view.visibility_summary().get("stars"):
        note("启动后现实星图没有可见恒星")
    print(f"   左面板可见区块数：{len(win.left_panel.findChildren(__import__('ui.widgets', fromlist=['CollapsiblePanel']).CollapsiblePanel))}")

    print()
    print("=" * 78)
    print("② 现实星图：交互")
    print("=" * 78)
    rv = win.real_view
    rv.set_time(datetime.datetime(2026, 9, 21, 22, 0, 0))
    rv.recalculate(force=True)
    pump(150)
    vis = rv.visibility_summary()
    print(f"   可见：恒星 {vis.get('stars')}　行星 {vis.get('planets')}　"
          f"深空 {vis.get('deep_sky')}　日月 {vis.get('sun_moon')}")

    # 拖拽旋转
    az0, alt0 = rv.center_az, rv.center_alt
    drag(rv, 400, 300, 620, 380)
    pump(80)
    print(f"   拖拽旋转：方位 {az0:.1f}° → {rv.center_az:.1f}°，"
          f"高度 {alt0:.1f}° → {rv.center_alt:.1f}°")
    if abs(rv.center_az - az0) < 1:
        note("左键拖拽没有改变视角")

    # 滚轮缩放
    fov0 = rv.fov
    wheel(rv, 400, 300, 480)
    pump(60)
    print(f"   滚轮缩放：视场 {fov0:.1f}° → {rv.fov:.1f}°")
    if abs(rv.fov - fov0) < 0.5:
        note("滚轮缩放无效")

    # 点击选中
    rv.repaint()
    pump(60)
    hit = None
    for key, pt in list(rv._last_screen.items())[:80]:
        click(rv, pt.x(), pt.y())
        pump(30)
        if win.info_panel.sky_panel._title.text() != "未选择天体":
            hit = win.info_panel.sky_panel._title.text()
            break
    print(f"   点击选中：{hit}")
    if hit is None:
        note("无法通过点击选中天体（命中测试可能失效）")

    # 升落信息
    info = win.info_panel.sky_panel
    print(f"   信息卡片：升落 = {info._rows['rise']._value.text()}")
    print(f"             最高高度 = {info._rows['peak']._value.text()}")
    if info._rows["rise"]._value.text() in ("--", ""):
        note("信息卡片缺少升落信息")

    # 搜索体验
    print("   搜索测试：")
    for q in ("天狼", "Vega", "M42", "NGC 7000", "猎户座", "木星", "巴纳德星"):
        t = time.perf_counter()
        res = rv.find_object(q)
        dt = (time.perf_counter() - t) * 1000
        name = (res or {}).get("name") if isinstance(res, dict) else None
        flag = "✓" if res else "✗"
        print(f"      {flag} {q:12s} → {name or '未找到'}（{dt:.0f} ms）")
        if res is None:
            note(f"搜索「{q}」无结果")
    if not rv.find_object("Gliese 551"):
        note("搜索不到 Gliese 编号（近星常用编号）")

    # 时间滑块极限
    win.time_slider.setValue(720)
    pump(80)
    print(f"   时间滑块 +720h → {rv.now().strftime('%Y-%m-%d %H:%M')}（状态：{win._status_label.text()[:46]}）")
    win.time_slider.setValue(0)
    pump(60)

    print()
    print("=" * 78)
    print("③ 太阳邻域")
    print("=" * 78)
    win.switch_mode("nearby")
    pump(250)
    nv = win.nearby_view
    st = nv.stats()
    print(f"   显示 {st['shown']}/{st['total']} 个天体（半径 {st['limit']} 光年）")
    t = time.perf_counter()
    nv.repaint()
    pump(40)
    print(f"   单帧耗时 {nv.frame_ms():.1f} ms（含 {len(nv._screen)} 个目标）")
    if nv.frame_ms() > 60:
        note(f"太阳邻域帧耗时偏高（{nv.frame_ms():.0f} ms），密集场景可能掉帧")

    drag(nv, 500, 400, 700, 330)
    pump(60)
    print(f"   拖拽旋转：yaw {__import__('math').degrees(nv.yaw) % 360:.1f}°  "
          f"pitch {__import__('math').degrees(nv.pitch):.1f}°")
    wheel(nv, 500, 400, 480)
    pump(60)
    print(f"   滚轮缩放：zoom {nv.zoom:.2f}x")

    for name in ("Proxima Centauri", "Barnard's Star", "Gliese 1", "天狼"):
        ok = nv.select_by_key(name)
        print(f"   搜索 {name:20s} → {'✓ ' + (nv._selected.display_name if nv._selected else '') if ok else '✗ 未找到'}")
        if not ok:
            note(f"太阳邻域搜索不到「{name}」")
    panel = win.info_panel.nearby_panel
    print(f"   信息卡片：{panel._title.text()} | {panel._rows['catalog']._value.text()}")
    print(f"             邻居 = {panel._rows['neighbors']._value.text()[:60]}")

    nv.set_layer("all_labels", True)
    pump(80)
    nv.repaint()
    pump(40)
    print(f"   打开「显示全部名称」后帧耗时 {nv.frame_ms():.1f} ms")
    if nv.frame_ms() > 90:
        note(f"「显示全部名称」导致帧耗时升到 {nv.frame_ms():.0f} ms")
    nv.set_layer("all_labels", False)

    print()
    print("=" * 78)
    print("④ 架空星图：生成与运营")
    print("=" * 78)
    win.switch_mode("fiction")
    pump(200)
    win.scale_combo.setCurrentIndex(1)          # 中型 80
    t = time.perf_counter()
    win._generate_world()
    for _ in range(80):
        pump(120)
        if win.galaxy is not None:
            break
    dt = time.perf_counter() - t
    g = win.galaxy
    if g is None:
        note("架空宇宙生成失败")
        return 1
    print(f"   生成耗时 {dt:.1f} s（后台线程），{g.name}：{g.stats()}")
    fv = win.fiction_view
    print(f"   可见星系 {fv.visibility_stats()}，单帧 {fv.frame_ms():.1f} ms")

    sid = g.systems[len(g.systems) // 2].sid
    fv.pick_system(sid)
    pump(120)
    print(f"   选中星系：{g.system(sid).name}，信息面板标签页数 = {win.info_panel.system_panel.tabs.count()}")

    win._set_route_start(g.systems[0].sid)
    win._set_route_goal(g.systems[-1].sid)
    win._plan_route()
    pump(150)
    route = fv.planned_route
    print(f"   航线：{route.get('message')}　距离 {route.get('distance')} ly　"
          f"耗时 {route.get('days')} 天　燃料 {route.get('fuel')}　风险 {route.get('risk')}")
    if not route.get("ok"):
        note("航线规划失败")

    win._dispatch_ship()
    pump(120)
    print(f"   飞船：{len(g.ships)} 艘，日志 = {win.ship_log.text().splitlines()[:1]}")

    win.btn_fiction_play.setChecked(True)
    for _ in range(30):
        pump(100)
    win.btn_fiction_play.setChecked(False)
    print(f"   时间推进到 银河历 {fv.year:.2f} 年；飞船状态：")
    for sh in g.ships[:3]:
        print(f"      {sh.name}：进度 {sh.progress * 100:.0f}%　"
              f"{'已抵达' if sh.arrived else ('已损毁' if sh.destroyed else '航行中')}　"
              f"燃料 {sh.fuel:.0f}　舰体 {sh.hull:.0f}")
    if fv.year < 0.01:
        note("时间推进似乎没有生效")
    if not any(sh.log for sh in g.ships):
        note("飞船没有产生任何事件日志")

    print(f"   飞船日志面板文本长度：{len(win.ship_log.text())} 字")

    print()
    print("=" * 78)
    print("⑤ 历史小说工作台")
    print("=" * 78)
    win.open_novel_panel()
    pump(300)
    nw = win._novel_window
    print(f"   窗口可见 = {nw.isVisible()}，世界观 = {nw.galaxy.name if nw.galaxy else None}")
    nw.generate_offline_sample()
    pump(200)
    print(f"   离线示例 {len(nw.current_text())} 字")
    print(f"   历史作品下拉项数 = {nw.record_combo.count()}")
    if nw.record_combo.count() == 0:
        note("历史小说面板没有作品列表")
    nw.close()
    pump(100)

    print()
    print("=" * 78)
    print("⑥ 稳定性：连续操作与空闲资源")
    print("=" * 78)
    LOG.entries.clear()
    for i in range(40):
        win.switch_mode(["real", "nearby", "fiction"][i % 3])
        pump(40)
    errs = [e.message for e in LOG.entries if e.level == "ERROR"]
    print(f"   40 次模式切换后错误数：{len(errs)}")
    for e in errs[:4]:
        print("      " + e[:100])
    if errs:
        note(f"模式切换产生 {len(errs)} 条错误日志")

    # 图层全开（最重场景）
    win.switch_mode("real")
    rv.set_layer("below_horizon", True)
    rv.set_layer("nearby_mark", True)
    rv.set_layer("deep_sky_labels", True)
    rv.recalculate(force=True)
    pump(120)
    LOG.entries.clear()
    t = time.perf_counter()
    for _ in range(5):
        rv.repaint()
    frame = (time.perf_counter() - t) / 5 * 1000
    print(f"   现实星图最重场景（全图层 + 地下天体）帧耗时 {frame:.1f} ms")
    if frame > 60:
        note(f"现实星图全图层帧耗时 {frame:.0f} ms，低于 20 FPS")

    win.close()
    pump(120)

    print()
    print("=" * 78)
    print(f"共记录 {len(ISSUES)} 条体验问题")
    print("=" * 78)
    for i, text in enumerate(ISSUES, 1):
        print(f"  {i}. {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
