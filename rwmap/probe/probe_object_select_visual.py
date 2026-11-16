"""对象选中：PPT 风格整体包围框 + 整组拖动 + Shift 按格 + 右键组合/对齐/分布 可视化验收。

复现用户需求：对象层框选后形成一个包围框（含全部选中对象），框的四角 / 四边
显示 8 个黄色缩放箭头（拖它们可等比缩放整组）；框内拖动整组移动；按住 Shift
按格移动；右键菜单含「组合 / 取消组合 / 对齐(6 项) / 分布(2 项) / 删除」。
这些操作都接入撤销栈（一条操作一条 Ctrl+Z）。

运行：
    python -m rwmap.probe.probe_object_select_visual
输出：
    logs/obj_sel_box.png        框选后：蓝色虚线包围框 + 8 个黄色缩放箭头
    logs/obj_drag_after.png     框内拖动后：整组一起移动（包围框跟手）
    logs/obj_align_left.png     左对齐后
    logs/obj_distribute_h.png   水平均匀分布后
同时用断言验证：包围框包含全部对象 / 整组位移一致 / Shift 吸附 / 右键菜单项齐全。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unittest.mock import patch  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QPointF, Qt, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402

SHIFT = "rwmap.app.main_window.QApplication.keyboardModifiers"

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
FONT = r"C:\Windows\Fonts\msyh.ttc"


def _make_doc():
    tile = TileLayer(width=40, height=30, name="Ground",
                     cells=[0] * (40 * 30))
    objs = [
        MapObject(object_id=1, name="A", shape="rect", x=100, y=100,
                  width=40, height=20),
        MapObject(object_id=2, name="B", shape="rect", x=200, y=140,
                  width=40, height=20),
        MapObject(object_id=3, name="C", shape="rect", x=320, y=90,
                  width=60, height=30),
        MapObject(object_id=4, name="D", shape="rect", x=420, y=160,
                  width=40, height=40),
    ]
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    doc = MapDocument(width=40, height=30, tile_w=20, tile_h=20,
                      layers=[tile, obj_layer], tilesets=[])
    return doc, obj_layer, objs


def grab(w, name):
    path = LOGS / name
    w.grab().save(str(path))
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
    w.resize(1200, 800)
    w.show()
    QApplication.processEvents()

    doc, obj_layer, objs = _make_doc()
    w.state.doc = doc
    w.state.profile.write_enabled = True
    w.state.write_enabled = True
    w.scene.set_document(doc)
    w.layers_dock.doc = doc
    w._on_layer_selected(obj_layer)
    w._set_object_tool("tileselect")
    QApplication.processEvents()

    view = w.view
    cur = view.transform().m11()
    view.scale(1.5 / cur, 1.5 / cur)
    view.camera.scale = view.transform().m11()
    view.centerOn(QPointF(280.0, 150.0))
    QApplication.processEvents()

    def press(x, y):
        view.objectToolPressed.emit(float(x), float(y))

    def move(x, y):
        view.objectToolMoved.emit(float(x), float(y))

    def release(x, y):
        view.objectToolReleased.emit(float(x), float(y))

    # 1) 框选：从 (80,80) 拖到 (500,220)，应选中全部 4 个对象。
    press(80.0, 80.0)
    move(500.0, 220.0)
    release(500.0, 220.0)
    QApplication.processEvents()
    sel = list(w.scene.selected_objects)
    box = w._obj_selection_box()
    assert len(sel) == 4, f"框选应选中 4 个对象，实际 {len(sel)}"
    # 包围框应恰好包含全部对象（100,90)-(460,200)
    assert box == QRectF(100, 90, 360, 110), box
    assert w.scene._obj_sel_box.isVisible(), "选中包围框应可见"
    sel_png = labeled(grab(w, "obj_sel_box.png"),
                      f"框选后：蓝色虚线包围框含 {len(sel)} 个对象 + 8 个黄色缩放箭头",
                      LOGS / "obj_sel_box_labeled.png")

    # 2) 框内拖动整组：在包围框内 (150,110) 按下，移到 (190,130)，应整组 +40/+20。
    before = {o.object_id: (o.x, o.y) for o in objs}
    press(150.0, 110.0)
    assert w._sel_move_drag is not None, "框内按下应进入整组拖动"
    move(190.0, 130.0)
    QApplication.processEvents()
    mid_box = w._obj_selection_box()
    assert mid_box == QRectF(140, 110, 360, 110), mid_box
    release(190.0, 130.0)
    QApplication.processEvents()
    after = {o.object_id: (o.x, o.y) for o in objs}
    for oid in before:
        assert after[oid] == (before[oid][0] + 40, before[oid][1] + 20), \
            (oid, before[oid], after[oid])
    drag_png = labeled(grab(w, "obj_drag_after.png"),
                       "框内拖动后：整组一起 +40/+20，包围框跟手",
                       LOGS / "obj_drag_after_labeled.png")

    # 3) Shift 按格：mock 键盘 Shift，移动 70 场景单位应吸附到整 4 格(80px)。
    w.scene.set_selected_objects(objs)
    w._begin_selection_move(0.0, 0.0)
    with patch(SHIFT, return_value=Qt.ShiftModifier):
        w._do_selection_move(70.0, 0.0)
    shifted = {o.object_id: o.x for o in objs}
    for oid in before:
        # 之前已经 +40，现在再 +80（70 吸附到 80）
        assert shifted[oid] == before[oid][0] + 40 + 80, (oid, shifted[oid])
    # 还原位置，便于后续对齐/分布截图
    for o, bx, by in [(objs[0], 100, 100), (objs[1], 200, 140),
                      (objs[2], 320, 90), (objs[3], 420, 160)]:
        o.x, o.y = bx, by
    w._refresh_moved_objects(objs)
    w.scene.set_object_selection_box(w._obj_selection_box())

    # 4) 对齐：左对齐后所有对象 x 相同（= 最左 100）。
    w.scene.set_selected_objects(objs)
    w._align_selected("left")
    QApplication.processEvents()
    xs = {o.x for o in objs}
    assert xs == {100.0}, xs
    align_png = labeled(grab(w, "obj_align_left.png"),
                        "左对齐后：4 个对象左侧对齐到 x=100",
                        LOGS / "obj_align_left_labeled.png")

    # 5) 分布：水平均匀分布（基于当前 x）。
    w._distribute_selected("h")
    QApplication.processEvents()
    dist_png = labeled(grab(w, "obj_distribute_h.png"),
                       "水平均匀分布后：4 个对象等间距",
                       LOGS / "obj_distribute_h_labeled.png")

    # 6) 右键菜单：选中 ≥2 个对象，右键菜单应含组合/对齐/分布/删除。
    w.scene.set_selected_objects(objs)
    menu = w._build_object_context_menu()
    assert menu is not None
    top = [a.text() for a in menu.actions()]
    # 组合 / 取消组合 / 对齐 / 分布 / 删除
    assert "组合" in top, top
    assert "取消组合" in top, top
    assert "对齐" in top, top
    assert "分布" in top, top
    assert "删除选中对象" in top, top
    # 对齐子菜单 6 项
    align_sub = next(a.menu() for a in menu.actions() if a.text() == "对齐")
    align_items = [a.text() for a in align_sub.actions() if a.text()]
    assert align_items == ["左对齐", "水平居中", "右对齐",
                           "顶对齐", "垂直居中", "底对齐"], align_items
    # 分布子菜单 2 项
    dist_sub = next(a.menu() for a in menu.actions() if a.text() == "分布")
    dist_items = [a.text() for a in dist_sub.actions() if a.text()]
    assert dist_items == ["水平均匀分布", "垂直均匀分布"], dist_items

    # 拼一张对比图
    a = Image.open(sel_png).convert("RGB")
    b = Image.open(drag_png).convert("RGB")
    c = Image.open(align_png).convert("RGB")
    d = Image.open(dist_png).convert("RGB")
    w4 = max(im.width for im in (a, b, c, d))
    h4 = max(im.height for im in (a, b, c, d))
    grid = Image.new("RGB", (w4 * 2 + 12, h4 * 2 + 12), (32, 36, 42))
    grid.paste(a, (0, 0))
    grid.paste(b, (w4 + 12, 0))
    grid.paste(c, (0, h4 + 12))
    grid.paste(d, (w4 + 12, h4 + 12))
    grid.save(LOGS / "obj_select_overview.png")

    print("选中对象数:", len(sel), "包围框:", box)
    print("整组位移后坐标:", after)
    print("Shift 吸附后 x:", shifted)
    print("左对齐后 x 集合:", xs)
    print("右键菜单顶层项:", top)
    print("对齐子菜单:", align_items)
    print("分布子菜单:", dist_items)
    print("截图:")
    for p in (sel_png, drag_png, align_png, dist_png,
              LOGS / "obj_select_overview.png"):
        print("  ", p)
    print("PASS：整体包围框 / 整组拖动 / Shift 吸附 / 组合·对齐·分布 菜单 均正确。")


if __name__ == "__main__":
    main()
