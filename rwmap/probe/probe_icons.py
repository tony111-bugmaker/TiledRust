"""把对象工具栏 13 个图标渲染到一张横排 PNG，用于人工核对图标外观。"""
import os
import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from rwmap.widgets.object_toolbar import TOOLS, make_object_icon

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
app = QApplication(sys.argv)

ids = [t[0] for t in TOOLS]
names = [t[1] for t in TOOLS]
cell = 34
W = len(ids) * cell
H = cell
pix = QPixmap(W, H)
pix.fill(QColor(40, 46, 54))
p = QPainter(pix)
p.setRenderHint(QPainter.Antialiasing, True)
for i, tid in enumerate(ids):
    icon = make_object_icon(tid)
    p.drawPixmap(i * cell + 3, 3, icon.pixmap(QSize(28, 28)))
p.end()

out = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs", "图标核对.png")
out = os.path.abspath(out)
pix.save(out)
print("saved", out, "size", pix.width(), pix.height())
