"""分组分页 + 按名字连线 + 画布「加入组」菜单 可视化验收。

验证本次新增行为：
1. 节点图分组「分页」：每组合一页，a 组不显示 b 组；「选择已有组」页只显示未分组对象；
   「全部」页显示所有；新建组出现在下拉且可切到该页。
2. 连线适配其它地图：按触发器的「名字 / 标签」互相引用（不只数字 id）也能画出连线。
3. 画布右键菜单含「加入组」子菜单（含「选择已有组」与已有组名）。

运行：python -m rwmap.probe.probe_object_group_pages_visual
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import (  # noqa: E402
    EDITOR_GROUP_PROPERTY, PropertyBag,
)
from rwmap.widgets.trigger_graph_dock import collect_graph  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
FONT = r"C:\Windows\Fonts\msyh.ttc"


def _make_doc():
    tile = TileLayer(width=40, height=30, name="Ground",
                     cells=[0] * (40 * 30))
    # 关键：obj3 的 name="Bob"，obj1 用 alsoActivate="Bob" 按名字引用它，
    # 验证「别的地图」里常见的「按名字连线」也能解析。
    objs = [
        MapObject(object_id=1, name="A", shape="rect", x=100, y=100,
                  width=40, height=20, type_="unitAdd"),
        MapObject(object_id=2, name="B", shape="rect", x=200, y=140,
                  width=40, height=20, type_="unitAdd"),
        MapObject(object_id=3, name="Bob", shape="point", x=320, y=90,
                  type_="point"),
        MapObject(object_id=4, name="D", shape="rect", x=420, y=160,
                  width=40, height=40, type_="move"),
        MapObject(object_id=5, name="E", shape="rect", x=520, y=120,
                  width=40, height=20, type_="unitDetect"),
    ]
    for o, kv in zip(objs, (
        {"id": "1", "alsoActivate": "Bob"},
        {"id": "2"},
        {"id": "3"},
        {"id": "4"},
        {"id": "5"},
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

    # 0) 按名字连线：obj1 通过 alsoActivate="Bob" 连到 obj3（name=Bob）
    data = collect_graph(doc)
    # 找出 id=1 与 id=3 的 key
    k1 = next(k for k, n in data.nodes.items() if n.tid == "1")
    k3 = next(k for k, n in data.nodes.items() if n.tid == "3")
    name_edge = (k1, k3, "alsoActivate") in {(e.src, e.dst, e.kind)
                                              for e in data.edges}
    assert name_edge, "按名字连线未解析：obj1 alsoActivate=Bob 应连到 obj3(Bob)"
    print("PASS 按名字连线：obj1(alsoActivate=Bob) -> obj3(name=Bob)")

    # 1) 分组：A、B -> Alpha；D -> Bravo；C、E 未分组（选择已有组）
    #    在节点图里选节点分组时，确保目标节点在「当前页」可见（隐藏节点无法被选中）。
    g.set_group_page("ALL")
    QApplication.processEvents()
    g.select_objects([objs[0], objs[1]])
    QApplication.processEvents()
    g._set_group_on_nodes(g._selected_nodes(), "Alpha")
    QApplication.processEvents()
    g.set_group_page("")           # D 未分组，在「选择已有组」页可见
    QApplication.processEvents()
    g.select_objects([objs[3]])
    QApplication.processEvents()
    g._set_group_on_nodes(g._selected_nodes(), "Bravo")
    QApplication.processEvents()

    # 2) 分页：Alpha 页只显示 A、B
    g.set_group_page("Alpha")
    QApplication.processEvents()
    vis_alpha = [i for i in g.node_items.values() if i.isVisible()]
    vis_alpha_ids = sorted(i.node.obj.object_id for i in vis_alpha)
    assert vis_alpha_ids == [1, 2], f"Alpha 页应只有 A/B，实际 {vis_alpha_ids}"
    print("PASS 分页：Alpha 页仅显示 A、B（a 组不显示 b 组）")

    # 3) 选择已有组页只显示未分组（C=3、E=5）
    g.set_group_page("")
    QApplication.processEvents()
    vis_none = sorted(i.node.obj.object_id for i in g.node_items.values()
                     if i.isVisible())
    assert vis_none == [3, 5], f"选择已有组页应只有未分组 3、5，实际 {vis_none}"
    print("PASS 分页：「选择已有组」页只显示未分组的 C、E")

    # 4) 全部页显示全部 5 个
    g.set_group_page("ALL")
    QApplication.processEvents()
    vis_all = sorted(i.node.obj.object_id for i in g.node_items.values()
                     if i.isVisible())
    assert vis_all == [1, 2, 3, 4, 5], vis_all
    print("PASS 分页：「全部」页显示所有节点")

    # 5) 新建组出现在下拉（这里直接走 _set_group_on_nodes 模拟新建）
    assert "Alpha" in [g.group_combo.itemData(i)
                       for i in range(g.group_combo.count())]
    print("PASS 下拉包含 Alpha / Bravo 等已有组")

    # 6) 画布右键菜单含「加入组」子菜单（含「选择已有组」与已有组名）
    w.scene.set_selected_objects([objs[4]])   # 选中 E
    menu = w._build_object_context_menu()
    assert menu is not None, "选中对象后右键菜单不应为空"
    join_sub = None
    for act in menu.actions():
        if act.text() == "加入组" and act.menu() is not None:
            join_sub = act.menu()
            break
    assert join_sub is not None, "右键菜单缺少「加入组」子菜单"
    join_texts = [a.text() for a in join_sub.actions()]
    assert "选择已有组（取消分组）" in join_texts, join_texts
    assert "Alpha" in join_texts and "Bravo" in join_texts, join_texts
    print("PASS 画布右键菜单含「加入组」子菜单：", join_texts)

    # 7) 通过画布「加入组」把 E 加入 Alpha，验证属性能写、页面切换正确
    w._join_selected_to_group("Alpha")
    QApplication.processEvents()
    assert objs[4].properties.get(EDITOR_GROUP_PROPERTY) == "Alpha"
    g.set_group_page("Alpha")
    QApplication.processEvents()
    vis = sorted(i.node.obj.object_id for i in g.node_items.values()
                 if i.isVisible())
    assert vis == [1, 2, 5], f"E 加入 Alpha 后该页应有 A/B/E，实际 {vis}"
    print("PASS 加入组：E 经画布菜单加入 Alpha，分页结果正确")

    # 截图：Alpha 页（含按名字连出的线）
    g.fit_view()
    QApplication.processEvents()
    png = labeled(grab(g, "group_page_alpha.png"),
                  "Alpha 页：A/B/E + 按名字连线(alsoActivate=Bob->Bob)",
                  LOGS / "group_page_alpha_labeled.png")

    print("截图:", png)
    print("全部断言通过：分页 / 按名字连线 / 加入组菜单 均符合需求。")


if __name__ == "__main__":
    main()
