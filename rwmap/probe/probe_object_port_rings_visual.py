"""端口判定区虚线圈 可视化验收（放大渲染，确认虚线环确实画出）。

运行：python -m rwmap.probe.probe_object_port_rings_visual
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
FONT = r"C:\Windows\Fonts\msyh.ttc"


def _pb(**kv):
    from rwmap.document.properties import PropertyBag
    b = PropertyBag()
    for k, v in kv.items():
        b.set(k, v)
    return b


def _make_doc():
    tile = TileLayer(width=40, height=30, name="Ground",
                     cells=[0] * (40 * 30))
    objs = [
        MapObject(object_id=1, name="哨兵", shape="rect", x=100, y=100,
                  width=40, height=20, type_="unitDetect",
                  properties=_pb(id="1")),
        MapObject(object_id=2, name="猛犸", shape="rect", x=300, y=160,
                  width=40, height=20, type_="unitRemove",
                  properties=_pb(id="2")),
    ]
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    doc = MapDocument(width=40, height=30, tile_w=20, tile_h=20,
                      layers=[tile, obj_layer], tilesets=[])
    return doc, obj_layer, objs


def main():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(auto_open=False)
    w.resize(1280, 820)
    w.show()
    QApplication.processEvents()

    doc, obj_layer, objs = _make_doc()
    w.state.doc = doc
    w.scene.set_document(doc)
    w.layers_dock.doc = doc
    w._on_layer_selected(obj_layer)
    g = w.trigger_graph_dock
    g.set_document(doc, w.state.undo_stack, w._refresh_objects)
    g.setVisible(True)
    QApplication.processEvents()
    g.set_group_page("ALL")
    QApplication.processEvents()

    itemA = g.node_items[[k for k, n in g.data.nodes.items() if n.tid == "1"][0]]

    # 先 fit（已知在离屏下能正常绘制），再放大，让端口虚线环足够清晰
    g.fit_view()
    QApplication.processEvents()
    g.view.scale(2.6, 2.6)
    QApplication.processEvents()

    path = LOGS / "object_port_rings.png"
    g.grab().save(str(path))

    im = Image.open(path).convert("RGB")
    band = 40
    canvas = Image.new("RGB", (im.width, im.height + band), (32, 36, 42))
    canvas.paste(im, (0, band))
    d = ImageDraw.Draw(canvas)
    d.text((14, 10), "端口判定区：内亮圈=真实命中半径，外淡圈=定位提示",
           font=ImageFont.truetype(FONT, 17), fill=(150, 205, 250))
    out = LOGS / "object_port_rings_labeled.png"
    canvas.save(out)
    print("截图:", out)
    print("虚线判定区已渲染（放大视图）。")


if __name__ == "__main__":
    main()
