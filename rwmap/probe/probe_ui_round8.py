"""第八轮界面验收：对象工具栏 + 全形状对象（含文字 / 图片）离屏截图。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"   # offscreen 无字体，中文会变方块
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402
from rwmap.utils.paths import as_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)


def make_picture() -> Path:
    path = LOGS / "对象图片示例.png"
    im = Image.new("RGBA", (96, 72), (245, 245, 250, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 95, 71), outline=(60, 90, 160, 255), width=2)
    for i in range(6):
        d.rectangle((8 + i * 14, 8, 18 + i * 14, 40),
                    fill=(40 + i * 30, 110 + i * 15, 210 - i * 20, 255))
    d.ellipse((8, 46, 30, 66), fill=(250, 200, 60, 255))
    d.rectangle((36, 50, 88, 64), fill=(90, 170, 245, 255))
    im.save(path)
    return path


def main():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(auto_open=False)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1440, 900)
    w.show()
    QApplication.processEvents()

    sample = as_path(load_profile("1.15").sample_map)
    w._open(sample)
    QApplication.processEvents()

    layer = next(l for l in w.state.doc.layers if isinstance(l, ObjectLayer))
    pic = make_picture()
    objects = [
        MapObject(object_id=901, name="矩形", shape="rect",
                  x=80, y=80, width=60, height=50),
        MapObject(object_id=902, name="椭圆", shape="ellipse",
                  x=160, y=80, width=70, height=50),
        MapObject(object_id=903, name="点", shape="point", x=250, y=105),
        MapObject(object_id=904, name="多边形", shape="polygon",
                  x=280, y=80, points=[(0, 0), (50, 0), (65, 30), (20, 50)]),
        MapObject(object_id=905, name="折线", shape="polyline",
                  x=370, y=80, points=[(0, 45), (20, 0), (45, 40), (65, 5)]),
        MapObject(object_id=906, name="图块", shape="tile",
                  x=460, y=80, width=40, height=40, gid=126),
        MapObject(object_id=907, name="文字对象", shape="text",
                  x=80, y=160, width=180, height=50,
                  text="铁锈战争 · 地图说明",
                  text_style={"fontfamily": "sans-serif", "pixelsize": "18",
                              "wrap": "1", "color": "#203040", "bold": "1",
                              "halign": "center", "valign": "center"}),
        MapObject(object_id=908, name="图片对象", shape="image",
                  x=290, y=155, width=96, height=72,
                  image_source=str(pic)),
    ]
    objects[-1].properties.set("image", str(pic), "file")
    layer.objects = objects
    layer.dirty = True
    w.state.doc.dirty = True
    w.scene.set_document(w.state.doc)
    w.view.set_document(w.state.doc)
    w.scene.current_layer = w.state.current_layer
    w.trigger_graph_dock.refresh()
    w.scene.set_selected_object(objects[6])
    w.trigger_graph_dock.select_object(objects[6])
    w._set_object_tool("select")
    w.statusBar().showMessage(
        "对象工具：矩形 / 椭圆 / 点 / 多边形 / 折线 / 图块 / 文字 / 图片 已插入")

    w.view.fitInView(QRectF(40, 40, 520, 220), Qt.KeepAspectRatio)
    QApplication.processEvents()

    full = LOGS / "第八轮_对象工具_全窗口.png"
    w.grab().save(str(full))
    shot = Image.open(full).convert("RGB")

    shot.crop((0, 0, 780, 110)).resize((1560, 220), Image.LANCZOS).save(
        LOGS / "第八轮_对象工具栏特写.png")
    # 画布区域特写（左侧 dock 之后）
    shot.crop((240, 100, 1200, 760)).resize((1440, 990), Image.LANCZOS).save(
        LOGS / "第八轮_对象形状特写.png")

    print("full", full.resolve())
    print("toolbar", (LOGS / "第八轮_对象工具栏特写.png").resolve())
    print("shapes", (LOGS / "第八轮_对象形状特写.png").resolve())


if __name__ == "__main__":
    main()
