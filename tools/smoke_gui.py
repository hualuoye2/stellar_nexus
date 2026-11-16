import io, os, sys, time
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\deepseek\stellar_nexus")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("STELLAR_NEXUS_HEADLESS", "1")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
from PySide6.QtCore import QPoint
app = QApplication(sys.argv)
from ui.main_window import MainWindow
w = MainWindow()
w.resize(1400, 860)
w.show()
app.processEvents()
img = QImage(1400, 860, QImage.Format_ARGB32_Premultiplied)
img.fill(QColor(0, 0, 0))
t0 = time.perf_counter()
w.render(img, QPoint(0, 0))
ms = (time.perf_counter() - t0) * 1000
img.save(r"D:\deepseek\.videotmp\smoke_main.png")
print("MainWindow 渲染 OK  %.1f ms" % ms)
v = w.real_view
print("LST=%.2f  fov=%.1f  银河层数=%d taper=%.2f" % (
    v.observer.lst, v.fov, v.MILKY_CONTOUR_LEVELS, v.MILKY_OUTER_TAPER))
print("可见天体:", v.visibility_summary())
print("图例/帧率:", v.show_legend, v.show_fps)
