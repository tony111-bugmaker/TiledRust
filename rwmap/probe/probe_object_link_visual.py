"""端口拖拽连线 可视化验收。

验证「从黄点（端口）拖到另一个节点」现在能建立一条 alsoActivate「激活」连线：
黄点拖拽之前无任何响应（端口只是装饰、没有 handler）；修复后抓端口拖到目标节点，
源触发器对象的 alsoActivate 写入目标 id，节点图里随即出现绿色边。

运行：python -m rwmap.probe.probe_object_link_visual
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
FONT = r"C:\Windows\Fonts\msyh.ttc"


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
        MapObject(object_id=3, name="机甲", shape="point", x=520, y=90,
                  type_="unitAdd", properties=_pb(id="3")),
    ]
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    doc = MapDocument(width=40, height=30, tile_w=20, tile_h=20,
                      layers=[tile, obj_layer], tilesets=[])
    return doc, obj_layer, objs


def _pb(**kv):
    from rwmap.document.properties import PropertyBag
    b = PropertyBag()
    for k, v in kv.items():
        b.set(k, v)
    return b


def grab(widget, name):
    path = LOGS / name
    widget.grab().save(str(path))
    return path


def labeled(path, title, out):
    im = Image.open(path).convert("RGB")
    band = 40
    canvas = Image.new("RGB", (im.width, im.height + band), (32, 36, 42))
    canvas.paste(im, (0, band))
    d = ImageDraw.Draw(canvas)
    d.text((14, 10), title, font=ImageFont.truetype(FONT, 17),
           fill=(150, 205, 250))
    canvas.save(out)
    return out


def main():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(auto_open=False)
    w.resize(1280, 820)
    w.show()
    QApplication.processEvents()

    doc, obj_layer, objs = _make_doc()
    w.state.doc = doc
    w.state.profile.write_enabled = True
    w.state.write_enabled = True
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
    itemC = g.node_items[[k for k, n in g.data.nodes.items() if n.tid == "3"][0]]

    before = objs[0].properties.get("alsoActivate")
    assert before is None, "初始不应有连线"

    # 模拟从 A 的 out 端口拖到 C 节点（落点在内）：建立 alsoActivate 连线
    def send(kind, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
        ev = QMouseEvent(kind, pos, pos, button, buttons, Qt.NoModifier)
        if kind == QEvent.MouseButtonPress:
            g.view.mousePressEvent(ev)
        elif kind == QEvent.MouseMove:
            g.view.mouseMoveEvent(ev)
        else:
            g.view.mouseReleaseEvent(ev)

    # 离屏视图尺寸为 0，mapToScene 无法正确往返；桩成恒等以验证事件链路
    with patch.object(g.view, "mapToScene", side_effect=lambda p: QPointF(p)):
        send(QEvent.MouseButtonPress, itemA.scene_port_out())
        assert g.view._link_src is not None, "按下端口应进入连线状态"
        send(QEvent.MouseMove, itemC.sceneBoundingRect().center())
        send(QEvent.MouseButtonRelease, itemC.sceneBoundingRect().center())

    assert objs[0].properties.get("alsoActivate") == "3", \
        f"A.alsoActivate 应为 3，实际 {objs[0].properties.get('alsoActivate')}"
    # 节点图出现绿色 alsoActivate 边
    pairs = {(e.src, e.dst, e.kind) for e in g.data.edges}
    assert (itemA.node.key, itemC.node.key, "alsoActivate") in pairs, \
        "节点图应出现 A->C 的 alsoActivate 边"
    print("PASS 端口拖拽：从 A(out) 拖到 C 建立 alsoActivate=3，节点图出现绿色激活连线")

    g.fit_view()
    QApplication.processEvents()
    png = labeled(grab(g, "object_link.png"),
                  "端口拖拽连线：A 的 out 端口 → C（绿色 alsoActivate）",
                  LOGS / "object_link_labeled.png")
    print("截图:", png)
    print("全部断言通过：黄点拖拽现在能建立连线。")


if __name__ == "__main__":
    main()
