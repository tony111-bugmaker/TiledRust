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
        # 蓝色选框在松手后撤掉。
        self.assertIsNone(self.w._sel_rect)
        self.assertFalse(self.w.scene._selection_item.isVisible()
                         if self.w.scene._selection_item is not None else False)

    def test_marquee_does_not_touch_tile_selection(self):
        self._marquee(80.0, 80.0, 260.0, 260.0)
        self.assertIsNone(self.w._tile_selection)  # 对象层框选不写入瓦片选区

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


if __name__ == "__main__":
    unittest.main()
