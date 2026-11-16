"""出第七轮界面截图：工具栏无吸管按钮 + Triggers 可编辑面板 + 画布对象选中高亮。"""
from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "windows"   # 有字体，中文不会变方块

from pathlib import Path  # noqa: E402

from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402

OUT = Path("logs/第七轮_界面.png")
OUT_DETAIL = Path("logs/第七轮_界面_局部.png")
OUT_CLOSEUP = Path("logs/第七轮_对象编辑特写.png")

app = QApplication.instance() or QApplication([])
w = MainWindow(auto_open=False)
w.setAttribute(Qt.WA_DontShowOnScreen, True)
w.resize(1400, 900)
w.show()
QApplication.processEvents()
sample = Path(load_profile("1.15").sample_map)
w._open(sample)
QApplication.processEvents()

# 选中一个对象，让画布出现蓝色高亮 + 面板同步选中
layer = next(l for l in w.state.doc.layers if isinstance(l, ObjectLayer))
obj = layer.objects[0]
w.view.objectPressed.emit(obj.x + 5, obj.y + 5)
QApplication.processEvents()
w.view.objectReleased.emit(obj.x + 5, obj.y + 5)
QApplication.processEvents()

OUT.parent.mkdir(parents=True, exist_ok=True)
w.grab().save(str(OUT))
whole = Image.open(OUT).convert("RGB")
print("全窗口", whole.size)

# 局部：顶部工具栏 + 左侧 Triggers 面板 + 画布上方对象框
top = whole.crop((0, 0, whole.width, 60))
left = whole.crop((0, 50, 270, 560))
canvas = whole.crop((260, 50, 820, 420))
detail = Image.new("RGB", (max(top.width, left.width + canvas.width),
                           top.height + max(left.height, canvas.height) + 8),
                   (24, 26, 30))
detail.paste(top, (0, 0))
detail.paste(left, (0, top.height + 8))
detail.paste(canvas, (left.width, top.height + 8))
detail.save(OUT_DETAIL)
print("局部", detail.size, "->", OUT.resolve(), OUT_DETAIL.resolve())

# 特写：把视图放大到对象附近，让选中框 / 名称标签 / 属性面板同框可见
from PySide6.QtCore import QRectF  # noqa: E402
pad = 60
w.view.fitInView(QRectF(obj.x - pad, obj.y - pad,
                        (obj.width or 20) + pad * 2,
                        (obj.height or 20) + pad * 2),
                 Qt.KeepAspectRatio)
QApplication.processEvents()
w.grab().save(str(OUT_CLOSEUP))
print("特写", OUT_CLOSEUP.resolve())
