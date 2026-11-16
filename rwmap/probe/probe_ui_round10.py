"""第十轮界面验收：默认布局 / 图标内蓝色描边 / 8 个纯黄色缩放箭头。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"   # offscreen 无字体，中文会变方块
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402
from rwmap.utils.paths import as_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
FONT = r"C:\Windows\Fonts\msyh.ttc"


def caption(path: Path, lines, out: Path):
    im = Image.open(path).convert("RGB")
    font = ImageFont.truetype(FONT, 17)
    band = 20 + 26 * len(lines)
    canvas = Image.new("RGB", (im.width, im.height + band), (32, 36, 42))
    canvas.paste(im, (0, band))
    d = ImageDraw.Draw(canvas)
    for i, text in enumerate(lines):
        d.text((14, 8 + i * 26), text, font=font, fill=(240, 205, 110))
    canvas.save(out)


def main():
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei", 9))
    w = MainWindow(auto_open=False)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1440, 900)
    w.show()
    QApplication.processEvents()

    w._open(as_path(load_profile("1.15").sample_map))
    QApplication.processEvents()

    layer = next(l for l in w.state.doc.layers
                 if isinstance(l, ObjectLayer))
    # 一个大的黄色矩形，选中后四角/四边应各有一个纯黄色双向箭头。
    free = MapObject(object_id=901, name="矩形", shape="rect",
                     x=313, y=287, width=167, height=123)
    layer.objects = [free]
    layer.dirty = True
    w.state.doc.dirty = True
    w.scene.set_document(w.state.doc)
    w.view.set_document(w.state.doc)
    w.scene.current_layer = w.state.current_layer
    w.trigger_graph_dock.refresh()
    w.scene.set_selected_object(free)
    w.trigger_graph_dock.select_object(free)
    w._set_object_tool("select")
    w.statusBar().showMessage(
        "选中对象：只显示 8 个纯黄色箭头（与矩形框同色），无蓝色边框线、无白色方块")
    w.view.fitInView(QRectF(275, 250, 250, 200), Qt.KeepAspectRatio)
    QApplication.processEvents()

    full = LOGS / "第十轮_全窗口.png"
    w.grab().save(str(full))
    shot = Image.open(full).convert("RGB")

    bar = LOGS / "第十轮_工具栏特写.png"
    shot.crop((0, 0, 1000, 80)).resize((2000, 160), Image.LANCZOS).save(bar)
    caption(bar, [
        "主工具栏 + 插入对象图标合并为一行；按钮方块无蓝色描边",
        "蓝色描边画在图标内部图形上（鼠标箭头、T、矩形…），文字工具改成蓝色 T",
    ], LOGS / "第十轮_工具栏_标注.png")

    detail = LOGS / "第十轮_黄色箭头特写.png"
    shot.crop((320, 100, 1120, 700)).resize((1200, 900), Image.LANCZOS).save(detail)
    caption(detail, [
        "选中态：只有 8 个纯黄色双向箭头，尺寸为上一轮的 1.5 倍",
        "矩形框保持黄色不变蓝，没有蓝色轮廓线、白色虚线框或白色方块底",
    ], LOGS / "第十轮_黄色箭头_标注.png")

    print("full    ", full.resolve())
    print("toolbar ", (LOGS / "第十轮_工具栏_标注.png").resolve())
    print("arrows  ", (LOGS / "第十轮_黄色箭头_标注.png").resolve())


if __name__ == "__main__":
    main()
