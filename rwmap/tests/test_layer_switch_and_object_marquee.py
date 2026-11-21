"""切图层清框选 + 对象层用 M 工具框选对象（离屏端到端）。

覆盖两个新行为：
1. 在瓦片层框选后切换图层，瓦片选框 / 对象选区都要被清掉。
2. 当前激活层是对象层时，M 工具的矩形框选应当选中框内所有对象
   （而不是瓦片），并联动节点图高亮。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402


def _build_doc():
    cells = [0] * 100
    tile = TileLayer(width=10, height=10, name="Ground",
                     cells=cells, readonly=False)
    objs = [
        MapObject(object_id=1, name="A", shape="rect", x=100, y=100, width=40, height=40),
        MapObject(object_id=2, name="B", shape="rect", x=200, y=200, width=40, height=40),
        MapObject(object_id=3, name="C", shape="rect", x=500, y=500, width=40, height=40),
        MapObject(object_id=4, name="D", shape="point", x=150, y=150),
    ]
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    doc = MapDocument(width=10, height=10, tile_w=20, tile_h=20,
                      layers=[tile, obj_layer], tilesets=[])
    return doc, tile, obj_layer, objs


def _build_window():
    app = QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    w = MainWindow(auto_open=False)
    doc, tile, obj_layer, objs = _build_doc()
    w.state.doc = doc
    w.state.current_layer = tile
    w.scene.doc = doc
    w.scene.set_document(doc)
    w.state.profile.write_enabled = True
    w._tile_selection = None
    w._tile_clipboard = None
    w._sel_rect = None
    w._sel_dragging = False
    w._sel_moving = False
    w._obj_marquee = False
    w._active_layer = tile
    return w, tile, obj_layer, objs


class LayerSwitchClearsSelectionTest(unittest.TestCase):
    def setUp(self):
        self.w, self.tile, self.obj_layer, self.objs = _build_window()

    def _box_select_tiles(self):
        w = self.w
        w._set_object_tool("tileselect")
        w.view.objectToolPressed.emit(0.0, 0.0)
        w.view.objectToolMoved.emit(50.0, 30.0)
        w.view.objectToolReleased.emit(50.0, 30.0)

    def test_switch_layer_clears_tile_selection(self):
        self._box_select_tiles()
        self.assertEqual(self.w._tile_selection, (0, 0, 3, 2))
        self.assertIsNotNone(self.w._sel_rect)
        # 切到对象层：瓦片选框应被清掉。
        self.w._on_layer_selected(self.obj_layer)
        self.assertIsNone(self.w._tile_selection)
        self.assertIsNone(self.w._sel_rect)
        self.assertIsNone(self.w.scene._selection_item
                          if self.w.scene._selection_item is None
                          else None)
        self.assertFalse(self.w.scene._selection_item.isVisible()
                         if self.w.scene._selection_item is not None else True)

    def test_switch_layer_clears_object_selection(self):
        # 先在对象层框选出对象选区，再切回瓦片层，对象选区应被清掉。
        w = self.w
        w._active_layer = self.obj_layer
        w._set_object_tool("tileselect")
        w.view.objectToolPressed.emit(80.0, 80.0)
        w.view.objectToolMoved.emit(260.0, 260.0)
        w.view.objectToolReleased.emit(260.0, 260.0)
        self.assertGreater(len(w.scene.selected_objects), 0)
        w._on_layer_selected(self.tile)
        self.assertEqual(list(w.scene.selected_objects), [])

    def test_switch_layer_tracks_active_layer(self):
        self.w._on_layer_selected(self.obj_layer)
        self.assertIs(self.w._active_layer, self.obj_layer)
        self.assertIsNone(self.w.state.current_layer)  # 对象层不是瓦片层


class ObjectMarqueeTest(unittest.TestCase):
    def setUp(self):
        self.w, self.tile, self.obj_layer, self.objs = _build_window()

    def _marquee(self, x0, y0, x1, y1):
        w = self.w
        w._active_layer = self.obj_layer
        w._set_object_tool("tileselect")
        w.view.objectToolPressed.emit(x0, y0)
        w.view.objectToolMoved.emit(x1, y1)
        w.view.objectToolReleased.emit(x1, y1)

    def test_marquee_selects_objects_in_box(self):
        # 框 (80,80)-(260,260) 只盖住 A(100,100) 与 B(200,200)，不盖 C(500,500)。
        self._marquee(80.0, 80.0, 260.0, 260.0)
        got = {id(o) for o in self.w.scene.selected_objects}
        self.assertIn(id(self.objs[0]), got)
        self.assertIn(id(self.objs[1]), got)
        self.assertNotIn(id(self.objs[2]), got)
        # 框到多个对象：选框刚好包住所有对象（A∪B∪D 的外接矩形）。
        self.assertIsNotNone(self.w._sel_rect)
        self.assertEqual(tuple(round(v, 3) for v in self.w._sel_rect),
                         (100.0, 100.0, 240.0, 240.0))

    def test_marquee_does_not_select_tiles(self):
        # 用户要求：对象层拖框只选对象，绝不选中瓦片。
        self._marquee(80.0, 80.0, 260.0, 260.0)
        self.assertIsNone(self.w._tile_selection)
        # 框到多个对象时选框保留（贴合对象外接矩形）。
        self.assertIsNotNone(self.w._sel_rect)

    def test_marquee_single_object_hides_rect(self):
        # 只框到一个对象：蓝色虚线选框消失。
        self._marquee(90.0, 90.0, 120.0, 120.0)  # 只碰 A 的左上角，避开 point D(150,150)
        self.assertEqual(len(self.w.scene.selected_objects), 1)
        self.assertIsNone(self.w._sel_rect)
        self.assertFalse(self.w.scene._selection_item.isVisible()
                         if self.w.scene._selection_item is not None else False)

    def test_marquee_empty_hides_rect(self):
        # 一个都没框到：蓝色虚线选框同样消失。
        self._marquee(800.0, 800.0, 900.0, 900.0)
        self.assertEqual(list(self.w.scene.selected_objects), [])
        self.assertIsNone(self.w._sel_rect)
        self.assertFalse(self.w.scene._selection_item.isVisible()
                         if self.w.scene._selection_item is not None else False)

    def test_marquee_click_selects_single_object(self):
        # 极小拖拽视为点击：选中指针下的对象。
        w = self.w
        w._active_layer = self.obj_layer
        w._set_object_tool("tileselect")
        w.view.objectToolPressed.emit(120.0, 120.0)
        w.view.objectToolMoved.emit(120.0, 120.0)
        w.view.objectToolReleased.emit(120.0, 120.0)
        selected = list(w.scene.selected_objects)
        self.assertEqual(len(selected), 1)
        self.assertIs(selected[0], self.objs[0])  # A 在 (100,100,40,40) 内


def _build_multi_layer_doc():
    """两个对象层 + 一个瓦片层，用于验证跨层选择。"""
    doc, tile, obj_layer, objs = _build_doc()
    units = ObjectLayer(name="Units", objects=[
        MapObject(object_id=10, name="U1", shape="rect",
                  x=120, y=120, width=30, height=30),
        MapObject(object_id=11, name="U2", shape="rect",
                  x=400, y=400, width=30, height=30),
    ])
    hidden = ObjectLayer(name="Hidden", visible=False, objects=[
        MapObject(object_id=20, name="H1", shape="rect",
                  x=130, y=130, width=30, height=30),
    ])
    doc.layers.extend([units, hidden])
    return doc, tile, obj_layer, objs, units, hidden


def _build_multi_window():
    app = QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    w = MainWindow(auto_open=False)
    doc, tile, obj_layer, objs, units, hidden = _build_multi_layer_doc()
    w.state.doc = doc
    w.state.current_layer = tile
    w.scene.doc = doc
    w.scene.set_document(doc)
    w.state.profile.write_enabled = True
    w._tile_selection = None
    w._tile_clipboard = None
    w._sel_rect = None
    w._sel_dragging = False
    w._sel_moving = False
    w._obj_marquee = False
    w._active_layer = tile
    w._set_object_tool("select")
    return w, tile, obj_layer, objs, units, hidden


class CrossLayerSelectionTest(unittest.TestCase):
    """选择 / 框选不受当前激活层限制（2026-11-21 用户反馈）。"""

    def setUp(self):
        (self.w, self.tile, self.triggers, self.objs,
         self.units, self.hidden) = _build_multi_window()

    def test_tile_layer_marquee_ignores_objects(self):
        # 在 Ground（瓦片层）拖框：对象对鼠标透明，框内对象一个都不选。
        w = self.w
        w._on_layer_selected(self.tile)
        w.view.objectToolPressed.emit(80.0, 80.0)
        w.view.objectToolMoved.emit(180.0, 180.0)
        w.view.objectToolReleased.emit(180.0, 180.0)
        self.assertEqual(list(w.scene.selected_objects), [])

    def test_tile_layer_marquee_keeps_tile_selection(self):
        # Ground 层拖框只框瓦片：瓦片选区保留（Ctrl+C 复制瓦片不丢）。
        w = self.w
        w._on_layer_selected(self.tile)
        w.view.objectToolPressed.emit(80.0, 80.0)
        w.view.objectToolMoved.emit(180.0, 180.0)
        w.view.objectToolReleased.emit(180.0, 180.0)
        self.assertIsNotNone(w._tile_selection)

    def test_object_layer_marquee_selects_all_object_layers(self):
        # 在 Triggers（对象层）拖框：Units 层对象也要被选中。
        w = self.w
        w._on_layer_selected(self.triggers)
        w.view.objectToolPressed.emit(80.0, 80.0)
        w.view.objectToolMoved.emit(180.0, 180.0)
        w.view.objectToolReleased.emit(180.0, 180.0)
        names = {o.name for o in w.scene.selected_objects}
        self.assertIn("A", names)
        self.assertIn("U1", names)
        self.assertNotIn("H1", names)

    def test_click_on_tile_layer_ignores_object_from_other_layer(self):
        # 用户要求：Ground（瓦片层）对象透明，点 Units 层对象也选不中。
        w = self.w
        w._on_layer_selected(self.tile)
        w.view.objectToolPressed.emit(130.0, 130.0)
        w.view.objectToolReleased.emit(130.0, 130.0)
        self.assertEqual(list(w.scene.selected_objects), [])

    def test_click_on_tile_layer_ignores_triggers_object(self):
        # 用户场景：Ground 层单击 Triggers 对象（map_info 那类）不选不拖。
        w = self.w
        w._on_layer_selected(self.tile)
        w.view.objectToolPressed.emit(105.0, 105.0)   # Triggers 的 A(100,100,40,40) 独有区
        w.view.objectToolReleased.emit(105.0, 105.0)
        self.assertEqual(list(w.scene.selected_objects), [])
        # 拖动也不该移动对象。
        a = self.objs[0]
        before = (a.x, a.y)
        w.view.objectToolPressed.emit(105.0, 105.0)
        w.view.objectToolMoved.emit(135.0, 130.0)
        w.view.objectToolReleased.emit(135.0, 130.0)
        self.assertEqual((a.x, a.y), before)

    def test_object_layer_marquee_does_not_select_tiles(self):
        # 用户要求：对象层拖框只选对象，不选 Ground 瓦片。
        w = self.w
        w._on_layer_selected(self.triggers)
        w.view.objectToolPressed.emit(10.0, 10.0)
        w.view.objectToolMoved.emit(160.0, 160.0)
        w.view.objectToolReleased.emit(160.0, 160.0)
        self.assertIsNone(w._tile_selection)
        # 框到多个对象 → 选框贴合对象外接矩形（不吸附格线）。
        self.assertIsNotNone(w._sel_rect)


if __name__ == "__main__":
    unittest.main()
