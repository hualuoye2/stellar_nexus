# -*- coding: utf-8 -*-
"""开发期工具：验证图片/GIF 导出与波特尔光害模拟。"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stellar_nexus")))

import datetime                                        # noqa: E402

from PySide6.QtCore import QEventLoop, QTimer           # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from 核心.utils import apply_dark_theme, LOG
    apply_dark_theme(app)

    from 核心 import astronomy as A
    from 核心 import export_media as EM
    from 核心 import light_pollution as LP
    from 核心 import star_data as SD
    from ui.real_sky_view import RealSkyView

    def pump(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    v = RealSkyView()
    v.resize(1280, 720)
    v.show()
    pump(400)
    obs = A.Observer(lat=39.9042, lon=116.4074,
                     when=datetime.datetime(2026, 9, 21, 22, 0, 0), utc_offset=8)
    v.set_observer(obs.lat, obs.lon, "北京", 8.0)
    v.set_time(obs.when)
    v.recalculate(force=True)
    pump(200)

    print("=" * 72)
    print("① PNG 导出")
    print("=" * 72)
    for label, size in (("1920×1080", (1920, 1080)), ("2560×1440", (2560, 1440))):
        out = os.path.join(HERE_TMP := os.path.dirname(os.path.abspath(__file__)),
                           f"export_test_{size[0]}.png")
        t0 = time.perf_counter()
        ok = EM.export_png(v, out, size,
                           overlay_text="北京　2026-09-21 22:00　视场 100°",
                           footer="STELLAR NEXUS 星图终端")
        dt = time.perf_counter() - t0
        mb = os.path.getsize(out) / 1024 / 1024 if os.path.isfile(out) else 0
        print(f"   {label}: {'成功' if ok else '失败'}　{dt:.2f} s　{mb:.2f} MB")

    print()
    print("=" * 72)
    print("② 整夜延时 GIF")
    print("=" * 72)
    frames = EM.night_frame_times(obs.when, 19.0, 5.0, 20.0)
    print(f"   19:00 → 05:00 每 20 分钟：{len(frames)} 帧")
    print(f"   首帧 {frames[0].strftime('%m-%d %H:%M')}　"
          f"末帧 {frames[-1].strftime('%m-%d %H:%M')}")
    print("   估算：" + EM.estimate_gif(len(frames), 640, 360, 120))
    gif_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export_test_night.gif")
    prog = []
    t0 = time.perf_counter()
    ok, note = EM.export_night_gif_blocking(
        v, gif_path, obs.when, 20.0, 4.0, 30.0, width=560, frame_delay_ms=120,
        overlay="北京整夜星空", progress=lambda d, t: prog.append((d, t)))
    dt = time.perf_counter() - t0
    print(f"   {'成功' if ok else '失败'}　耗时 {dt:.2f} s")
    print(f"   {note}")
    if prog:
        print(f"   进度回调 {len(prog)} 次，最后一次 {prog[-1]}")
    print(f"   视图时间已恢复：{v.observer.when.strftime('%Y-%m-%d %H:%M')}")

    # 验证 GIF 可被读取（帧数）
    from PySide6.QtGui import QMovie
    movie = QMovie(gif_path)
    movie.start()
    pump(300)
    print(f"   QMovie 读回：有效={movie.isValid()}　帧数={movie.frameCount()}　"
          f"尺寸={movie.currentPixmap().width()}x{movie.currentPixmap().height()}")
    movie.stop()

    print()
    print("=" * 72)
    print("③ 波特尔光害模拟（接入星图）")
    print("=" * 72)
    star_mags = [s.mag for s in SD.STAR_CATALOG]
    dso_mags = [m.mag for m in SD.DEEP_SKY_CATALOG]
    for bortle in (2, 5, 8, 9):
        rep = LP.evaluate(star_mags, dso_mags, bortle)
        print(f"   Bortle {bortle}: 肉眼极限 {rep.limiting_mag_naked:.1f}　"
              f"恒星 {rep.stars_visible} 颗　肉眼深空 {rep.dso_naked} 个　"
              f"双筒 {rep.dso_binocular} 个")
    print()
    print("   " + LP.compare_report(star_mags, dso_mags, 8, 2).replace("<br>", "\n   ")
          .replace("<b style='color:#ffb37a'>", "").replace("<b style='color:#8fffc8'>", "")
          .replace("<b>", "").replace("</b>", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
