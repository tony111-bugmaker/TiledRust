"""对象分组（编辑器私有属性 rwmap_group）可视化验收。

需求：对象有一个铁锈战争不识别、只有本软件识别的分组属性；触发器节点图可按该
属性里的「名字」把对象分组（同名=同组）；该属性不在右侧属性面板显示；可新建组、
可让别的对象加入指定组（把属性值改成该组名）。分组操作现已全部接入撤销栈
（画布右键 / 节点图都走 ObjectCommand / MultiObjectCommand）。

运行：
    python -m rwmap.probe.probe_object_group_visual
输出：
    logs/group_graph_colored.png    按组着色后的节点图（同组同色 + 组徽标）
    logs/group_inspector.png       右侧属性面板（已选中的节点不显示 rwmap_group）
同时用断言验证：分组属性已写入 / 不在 inspector 显示 / TMX 往返保真。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import EDITOR_GROUP_PROPERTY  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
FONT = r"C:\Windows\Fonts\msyh.ttc"


def _make_doc():
    tile = TileLayer(width=40, height=30, name="Ground",
                     cells=[0] * (40 * 30))
    objs = [
        MapObject(object_id=1, name="A", shape="rect", x=100, y=100,
                  width=40, height=20, type_="unitAdd"),
        MapObject(object_id=2, name="B", shape="rect", x=200, y=140,
                  width=40, height=20, type_="unitAdd"),
        MapObject(object_id=3, name="C", shape="point", x=320, y=90,
                  type_="point"),
        MapObject(object_id=4, name="D", shape="rect", x=420, y=160,
                  width=40, height=40, type_="move"),
    ]
    for o, kv in zip(objs, (
        {"id": "1", "team": "1"},
        {"id": "2", "team": "1"},
        {"id": "3"},
        {"id": "4", "team": "2"},
    )):
        for k, v in kv.items():
            o.properties.set(k, v)
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    doc = MapDocument(width=40, height=30, tile_w=20, tile_h=20,
                      layers=[tile, obj_layer], tilesets=[])
    return doc, obj_layer, objs


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
    d.text((14, 10), title, font=ImageFont.truetype(FONT, 18),
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
    w.trigger_graph_dock.set_document(doc, w.state.undo_stack, w._refresh_objects)
    w.trigger_graph_dock.setVisible(True)
    QApplication.processEvents()

    g = w.trigger_graph_dock
    g.refresh(fit=True)
    # 本验收脚本要同时选中不同组 / 未分组的对象：界面默认已去掉「全部」页，
    # 这里由脚本显式请求一次（下拉会临时出现「全部」档），保持旧验收流程。
    g.set_group_page("ALL")
    QApplication.processEvents()

    # 1) 建组：A、B → "Alpha"；C、D → "Bravo"
    g.set_group_page("ALL")          # 分组页互斥：先显式显示全部再选
    g.select_objects([objs[0], objs[1]])
    QApplication.processEvents()
    g._set_group_on_nodes(g._selected_nodes(), "Alpha")
    QApplication.processEvents()
    g.set_group_page("ALL")
    g.select_objects([objs[2], objs[3]])
    QApplication.processEvents()
    g._set_group_on_nodes(g._selected_nodes(), "Bravo")
    QApplication.processEvents()

    # 校验：rwmap_group 已写入对象
    assert objs[0].properties.get(EDITOR_GROUP_PROPERTY) == "Alpha"
    assert objs[2].properties.get(EDITOR_GROUP_PROPERTY) == "Bravo"
    # 校验：节点携带 group 信息，且分组下拉已同步
    assert "Alpha" in [g.group_combo.itemData(i)
                       for i in range(g.group_combo.count())]
    assert "Bravo" in [g.group_combo.itemData(i)
                       for i in range(g.group_combo.count())]

    # 2) 按组着色
    g._toggle_color_by_group(True)
    QApplication.processEvents()
    g.fit_view()
    QApplication.processEvents()
    graph_png = labeled(grab(g, "group_graph_colored.png"),
                        "按组着色：Alpha / Bravo 同组同色，节点带「组：」徽标",
                        LOGS / "group_graph_colored_labeled.png")

    # 3) 加入已有组：把 C 也加入 Alpha（演示「让别的对象加入指定组」）
    g.group_combo.setCurrentIndex(
        [g.group_combo.itemData(i) for i in range(g.group_combo.count())].index("Alpha"))
    g.set_group_page("ALL")
    g.select_objects([objs[2]])
    QApplication.processEvents()
    g._set_group_on_nodes(g._selected_nodes(), "Alpha")
    QApplication.processEvents()
    assert objs[2].properties.get(EDITOR_GROUP_PROPERTY) == "Alpha", \
        objs[2].properties.get(EDITOR_GROUP_PROPERTY)

    # 4) 右侧属性面板不应显示 rwmap_group
    g.set_group_page("ALL")
    g.select_objects([objs[0]])
    QApplication.processEvents()
    keys = [k for k, _e, _v in w.trigger_graph_dock.inspector._prop_rows]
    assert EDITOR_GROUP_PROPERTY not in keys, \
        f"inspector 不应显示 {EDITOR_GROUP_PROPERTY}，实际：{keys}"
    # 添加属性下拉里也不应出现该键
    picker_data = [w.trigger_graph_dock.inspector.prop_select.itemData(i)
                   for i in range(w.trigger_graph_dock.inspector.prop_select.count())]
    assert EDITOR_GROUP_PROPERTY not in picker_data, \
        f"添加属性下拉不应含 {EDITOR_GROUP_PROPERTY}"
    ins_png = labeled(grab(w.trigger_graph_dock.inspector, "group_inspector.png"),
                      "右侧属性面板：A 节点的属性里没有 rwmap_group",
                      LOGS / "group_inspector_labeled.png")

    # 5) TMX 保真：rwmap_group 作为标准 Tiled 自定义属性被写出，且可被读回；
    #    铁锈战争只认自己的触发器属性名，不会读取 rwmap_group（不影响游戏）。
    from lxml import etree
    from rwmap.document.properties import PropertyBag
    from rwmap.io.tmx_writer import _write_properties
    el = etree.Element("object")
    _write_properties(el, objs[0].properties)
    xml = etree.tostring(el).decode()
    assert 'name="rwmap_group"' in xml, "TMX 未写出 rwmap_group 属性"
    bag = PropertyBag.from_element(el.find("properties"))
    assert bag.get(EDITOR_GROUP_PROPERTY) == "Alpha", \
        bag.get(EDITOR_GROUP_PROPERTY)

    print("分组属性键:", EDITOR_GROUP_PROPERTY)
    print("A / B / C 组:", [o.properties.get(EDITOR_GROUP_PROPERTY)
                            for o in objs[:3]])
    print("D 组:", objs[3].properties.get(EDITOR_GROUP_PROPERTY))
    print("inspector 属性行 keys:", keys)
    print("TMX 往返保真: rwmap_group 写出且读回 == Alpha")
    print("截图:")
    for p in (graph_png, ins_png):
        print("  ", p)
    print("PASS：编辑器私有分组属性已写入 / 节点图按名分组着色 / "
          "不在属性面板显示 / TMX 保真（铁锈不读取）。")


if __name__ == "__main__":
    main()
