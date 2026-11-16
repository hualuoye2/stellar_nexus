# -*- coding: utf-8 -*-
"""开发期工具：确认修复后的画面朝向正确（天顶在上、地平线在下），并出预览图。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                       # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)
    from ui.real_sky_view import RealSkyView

    v = RealSkyView()
    v.resize(1000, 620)
    v.show()

    def pump(ms: int = 250) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(400)
    v.set_observer(39.9042, 116.4074, "北京", 8.0)
    v.set_time(datetime.datetime(2026, 9, 21, 23, 23, 4))
    v.recalculate(force=True)
    v.center_az, v.center_alt, v.fov = 180.0, 35.0, 100.0
    LOG.entries.clear()
    v.repaint()
    pump(150)
    print("绘制错误:", [e.message for e in LOG.entries if e.level == "ERROR"][:2])

    cy = v.height() / 2.0
    print()
    print("关键高度角在屏幕上的位置（视角：正南 / +35° / 视场 100°）")
    for name, alt in (("天顶 90°", 90.0), ("60°", 60.0), ("40°", 40.0),
                      ("20°", 20.0), ("南地平 0°", 0.0), ("-20°（地平下）", -20.0)):
        pt = v._project_point(alt, 180.0)
        if pt is None:
            print(f"   {name:12s} 不可见（视野外）")
            continue
        rel = pt.y() - cy
        pos = "屏幕上方" if rel < -3 else ("屏幕下方" if rel > 3 else "屏幕中心")
        print(f"   {name:12s} y={pt.y():7.1f}　{pos}（相对中心 {rel:+.0f} px）")

    print()
    print("判定：高度角越大越靠上；0° 地平线应在中心下方；-20° 更靠下")
    z = v._project_point(90.0, 180.0)
    h = v._project_point(0.0, 180.0)
    u = v._project_point(-20.0, 180.0)
    ok = (z is not None and h is not None and u is not None
          and z.y() < cy < h.y() < u.y())
    print(f"   ⇒ 朝向{'正确 ✓' if ok else '仍然有问题 ✗'}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview_orientation.png")
    v.grab().save(out, "PNG")
    print(f"   已保存 {out}")

    # 左右方向验证：朝南看时东应在左侧、西应在右侧
    e = v._project_point(20.0, 90.0)
    w = v._project_point(20.0, 270.0)
    if e is not None and w is not None:
        print()
        print("左右方向（朝南看：东应在左、西应在右）")
        print(f"   东（方位90°） x={e.x():7.1f}　{'左侧' if e.x() < v.width() / 2 else '右侧'}")
        print(f"   西（方位270°）x={w.x():7.1f}　{'左侧' if w.x() < v.width() / 2 else '右侧'}")
        print(f"   ⇒ {'正确 ✓' if e.x() < w.x() else '左右反了 ✗'}")

    v._timer.stop()
    v.close()
    pump(150)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
