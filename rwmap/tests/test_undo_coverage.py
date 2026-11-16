"""撤销覆盖回归：除「移动视角」外的文件改动都必须能 Ctrl+Z 还原。

第三十轮：把以前明确标「暂不记撤销」的操作全部接进 QUndoStack——
图层可见性 / 透明度、对象拖动 / 组合 / 拆组 / 加入组 / 对齐 / 分布 / 删除、
节点图的建线 / 删线 / 改线型 / 分组、图集（.tsx）的每瓦片属性编辑。
另外验证多标签下撤销作用在「当前激活文档」，而不是第一个标签。
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtGui import QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.app_state import AppState  # noqa: E402
from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.commands.layer_command import LayerFlagCommand  # noqa: E402
from rwmap.commands.object_command import MultiObjectCommand  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import EDITOR_GROUP_PROPERTY, PropertyBag  # noqa: E402


def _make_doc(layer2: bool = False):
    tile = TileLayer(width=40, height=30, name="Ground", cells=[0] * (40 * 30))
    objs = [
        MapObject(object_id=1, name="A", shape="rect", x=100, y=100,
                  width=40, height=20),
        MapObject(object_id=2, name="B", shape="rect", x=200, y=100,
                  width=40, height=20),
        MapObject(object_id=3, name="C", shape="rect", x=300, y=100,
                  width=40, height=20),
    ]
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    layers = [tile, obj_layer]
    other = None
    if layer2:
        other = ObjectLayer(name="Notes", objects=[
            MapObject(object_id=9, name="D", shape="rect", x=400, y=100,
                      width=40, height=20)])
        layers.append(other)
    doc = MapDocument(width=40, height=30, tile_w=20, tile_h=20,
                      layers=layers, tilesets=[])
    return doc, obj_layer, objs, other


class _WindowCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = MainWindow(auto_open=False)
        self.w.resize(1200, 800)
        self.doc, self.obj_layer, self.objs, self.other = _make_doc(layer2=True)
        self.w.state.doc = self.doc
        self.w.state.profile.write_enabled = True
        self.w.scene.set_document(self.doc)
        self.w.layers_dock.set_document(self.doc)
        self.w._on_layer_selected(self.obj_layer)
        QApplication.processEvents()

    def tearDown(self):
        self.w.state.undo_stack.clear()
        self.w.deleteLater()
        QApplication.processEvents()


class LayerPropertyUndoTest(_WindowCase):
    def test_visibility_toggle_is_undoable(self):
        layer = self.doc.layers[0]
        self.assertTrue(layer.visible)
        self.w._on_visibility(layer, False)
        self.assertFalse(layer.visible)
        self.assertEqual(self.w.state.undo_stack.count(), 1)
        self.assertIsInstance(self.w.state.undo_stack.command(0), LayerFlagCommand)
        self.w.state.undo_stack.undo()
        self.assertTrue(layer.visible, "撤销后图层应重新可见")

    def test_opacity_drag_merges_into_single_step(self):
        layer = self.doc.layers[0]
        for value in (0.9, 0.8, 0.7, 0.6):
            self.w._on_opacity(layer, value)
        self.assertAlmostEqual(layer.opacity, 0.6, places=3)
        self.assertEqual(self.w.state.undo_stack.count(), 1,
                         "滑杆连续拖动应合并成一条撤销记录")
        self.w.state.undo_stack.undo()
        self.assertAlmostEqual(layer.opacity, 1.0, places=3)

    def test_toggle_other_layers_is_one_step(self):
        current = self.doc.layers[0]
        changes = [(layer, False) for layer in self.doc.layers[1:]]
        self.w._on_visibility_batch(changes)
        self.assertFalse(any(l.visible for l in self.doc.layers[1:]))
        self.assertEqual(self.w.state.undo_stack.count(), 1,
                         "「隐藏其它图层」应合成一条撤销")
        self.w.state.undo_stack.undo()
        self.assertTrue(all(l.visible for l in self.doc.layers), current and True)


class ObjectOperationUndoTest(_WindowCase):
    def test_group_is_undoable(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._group_selected_objects("G1")
        self.assertEqual(self.obj_layer.objects[0].properties.get(
            EDITOR_GROUP_PROPERTY), "G1")
        self.assertEqual(self.w.state.undo_stack.count(), 1)
        self.assertIsInstance(self.w.state.undo_stack.command(0),
                              MultiObjectCommand)
        self.w.state.undo_stack.undo()
        for obj in self.obj_layer.objects:
            self.assertNotIn(EDITOR_GROUP_PROPERTY, obj.properties)

    def test_ungroup_is_undoable(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._group_selected_objects("G1")
        self.w._ungroup_selected_objects()
        self.assertEqual(self.w.state.undo_stack.count(), 2)
        self.w.state.undo_stack.undo()       # 回到「有组」
        for obj in self.obj_layer.objects:
            self.assertEqual(obj.properties.get(EDITOR_GROUP_PROPERTY), "G1")

    def test_align_is_undoable(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._align_selected("left")
        for obj in self.obj_layer.objects:
            self.assertEqual(obj.x, 100.0)
        self.assertEqual(self.w.state.undo_stack.count(), 1)
        self.w.state.undo_stack.undo()
        xs = sorted(obj.x for obj in self.obj_layer.objects)
        self.assertEqual(xs, [100.0, 200.0, 300.0])

    def test_distribute_is_undoable(self):
        self.objs[0].x, self.objs[0].width = 100.0, 40.0
        self.objs[1].x, self.objs[1].width = 180.0, 40.0
        self.objs[2].x, self.objs[2].width = 300.0, 40.0
        self.w.scene.set_selected_objects(self.objs)
        self.w._distribute_selected("h")
        self.assertEqual(self.obj_layer.objects[1].x, 200.0)
        self.assertEqual(self.w.state.undo_stack.count(), 1)
        self.w.state.undo_stack.undo()
        self.assertEqual(self.obj_layer.objects[1].x, 180.0)

    def test_join_group_is_undoable(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._join_selected_to_group("已有组")
        self.assertEqual(self.obj_layer.objects[0].properties.get(
            EDITOR_GROUP_PROPERTY), "已有组")
        self.assertEqual(self.w.state.undo_stack.count(), 1)
        self.w.state.undo_stack.undo()
        self.assertNotIn(EDITOR_GROUP_PROPERTY,
                         self.obj_layer.objects[0].properties)

    def test_delete_across_layers_is_single_step(self):
        objs = [self.obj_layer.objects[0], self.other.objects[0]]
        self.w.scene.set_selected_objects(objs)
        self.w._delete_selected_object()
        self.assertEqual(len(self.obj_layer.objects), 2)
        self.assertEqual(len(self.other.objects), 0)
        self.assertEqual(self.w.state.undo_stack.count(), 1,
                         "跨两个对象层的删除也只算一条撤销")
        self.w.state.undo_stack.undo()
        self.assertEqual(len(self.obj_layer.objects), 3)
        self.assertEqual(len(self.other.objects), 1)

    def test_no_change_produces_no_undo_entry(self):
        # 已经左对齐再点一次左对齐：不应往撤销栈塞空记录
        self.w.scene.set_selected_objects(self.objs)
        self.w._align_selected("left")
        before = self.w.state.undo_stack.count()
        self.w._align_selected("left")
        self.assertEqual(self.w.state.undo_stack.count(), before)


def _bag(**kwargs) -> PropertyBag:
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


class TriggerGraphUndoTest(_WindowCase):
    def _link_doc(self):
        objs = [
            MapObject(object_id=1, name="抽签", type_="unitAdd", shape="rect",
                      x=0, y=0, width=100, height=100,
                      properties=_bag(id="1", alsoActivate="2")),
            MapObject(object_id=2, name="条件", type_="unitDetect", shape="rect",
                      x=200, y=0, width=100, height=100, properties=_bag(id="2")),
            MapObject(object_id=3, name="动作", type_="unitRemove", shape="rect",
                      x=400, y=0, width=100, height=100, properties=_bag(id="3")),
        ]
        return ObjectLayer(name="Triggers", objects=objs)

    def _setup_dock(self):
        layer = self._link_doc()
        self.doc.layers = [self.doc.layers[0], layer]
        self.w.scene.set_document(self.doc)
        dock = self.w.trigger_graph_dock
        dock.set_document(self.doc, self.w.state.undo_stack,
                          self.w._refresh_objects)
        dock.refresh(fit=False)      # 面板未显示时 set_document 不会自动建节点
        return dock, layer

    def test_create_link_is_undoable(self):
        dock, layer = self._setup_dock()
        nodes = {n.obj.object_id: n for n in dock.data.nodes.values()}
        dock.create_link(nodes[1], nodes[3])
        self.assertIn("3", self.w.state.undo_stack and
                      next(o for o in layer.objects
                           if o.object_id == 1).properties.get("alsoActivate"))
        self.assertEqual(self.w.state.undo_stack.count(), 1)
        self.w.state.undo_stack.undo()
        obj1 = next(o for o in layer.objects if o.object_id == 1)
        self.assertNotIn("3", (obj1.properties.get("alsoActivate") or "").split(", "))

    def test_delete_edge_is_undoable(self):
        dock, layer = self._setup_dock()
        e = next(e for e in dock.data.edges
                 if e.kind == "alsoActivate" and dock.data.nodes[e.dst].tid == "2")
        dock.delete_edge(e)
        self.assertNotIn("2", (next(o for o in layer.objects
                                    if o.object_id == 1)
                               .properties.get("alsoActivate") or "").split(", "))
        self.assertEqual(self.w.state.undo_stack.count(), 1)
        self.w.state.undo_stack.undo()
        obj1 = next(o for o in layer.objects if o.object_id == 1)
        self.assertIn("2", (obj1.properties.get("alsoActivate") or "").split(", "))

    def test_change_edge_type_is_undoable(self):
        dock, layer = self._setup_dock()
        e = next(e for e in dock.data.edges
                 if e.kind == "alsoActivate" and dock.data.nodes[e.dst].tid == "2")
        dock.change_edge_type(e, "deactivatedBy")
        self.assertEqual(self.w.state.undo_stack.count(), 1)
        self.w.state.undo_stack.undo()
        kinds = [x.kind for x in dock.data.edges]
        self.assertIn("alsoActivate", kinds)


class TilesetPropertyUndoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PIL import Image
        from rwmap.document.tileset import Tileset
        from rwmap.document.tileset_document import TilesetDocument
        from rwmap.widgets.tileset_editor import TilesetEditorWidget
        from rwmap.widgets.tileset_settings_dock import TilesetTilePropsDock

        self.tmp = Path(tempfile.mkdtemp(prefix="rw_tsundo_"))
        img = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
        p = self.tmp / "t.png"
        img.save(p)
        ts = Tileset(name="单位", tile_w=20, tile_h=20, tilecount=4, columns=2,
                     image_source=p.name, image_w=40, image_h=40)
        self.tdoc = TilesetDocument(path=self.tmp / "单位.tsx", tileset=ts,
                                    image_path=p)
        self.stack = QUndoStack()
        self.editor = TilesetEditorWidget()
        self.editor.set_tileset(self.tdoc.tileset, self.tdoc, self.stack)
        # 逐格属性编辑现由左侧「瓦片属性」停靠面板承载；图集点格事件
        # 经 tileClicked 转发到 dock.select_tile。
        self.dock = TilesetTilePropsDock()
        self.editor.tileClicked.connect(self.dock.select_tile)
        self.dock.set_context(self.tdoc.tileset, self.tdoc, self.stack)
        self.dock.select_tile(1)

    def tearDown(self):
        self.stack.clear()
        self.dock.deleteLater()
        self.editor.deleteLater()
        QApplication.processEvents()

    def test_tile_property_edit_is_undoable(self):
        self.dock.panel.select_terrain("water")
        bag = self.tdoc.tileset.tile_props(1)
        self.assertEqual(bag.get("water"), "")
        self.assertEqual(self.stack.count(), 1)
        self.stack.undo()
        self.assertIsNone(self.tdoc.tileset.tile_props(1).get("water"),
                          "撤销后该瓦片不应再有 water")

    def test_typing_merges_into_one_step(self):
        self.dock.panel.select_terrain("water")
        base = self.stack.count()
        for text in ("a", "ab", "abc"):
            self.dock.panel._on_value_text("water", text)
        self.assertEqual(self.tdoc.tileset.tile_props(1).get("water"), "abc")
        self.assertEqual(self.stack.count(), base + 1,
                         "连续输入应合并成一条撤销")
        self.stack.undo()
        self.assertEqual(self.tdoc.tileset.tile_props(1).get("water"), "")


class MultiTabUndoTest(unittest.TestCase):
    """撤销动作必须跟着当前标签的撤销栈，而不是第一个标签。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_undo_targets_active_document(self):
        w = MainWindow(auto_open=False)
        w.resize(1000, 700)
        doc1, layer1, _objs1, _ = _make_doc()
        doc2, layer2, _objs2, _ = _make_doc()
        ctx1 = w.state
        ctx1.doc = doc1
        ctx1.profile.write_enabled = True
        ctx2 = AppState()
        ctx2.doc = doc2
        ctx2.profile.write_enabled = True
        w._contexts.append(ctx2)
        w._displayed_ctx_id = None
        w._attach_context(ctx2)
        QApplication.processEvents()
        # 两个文档各记一条可见性改动
        ctx1.undo_stack.push(LayerFlagCommand(
            doc1, layer1, "visible", True, False, "隐藏 Ground", None))
        ctx2.undo_stack.push(LayerFlagCommand(
            doc2, layer2, "visible", True, False, "隐藏 Ground", None))
        self.assertEqual(len(w._contexts), 2)
        self.assertIs(w.state, ctx2)
        self.assertTrue(w.act_undo.isEnabled(), "切到第二张后撤销动作应可用")
        w._undo()
        self.assertTrue(layer2.visible, "当前标签(第二张)应被撤销")
        self.assertFalse(layer1.visible, "另一个标签不应被误改")
        self.assertFalse(w.act_undo.isEnabled(), "第二张已撤销干净")
        w.deleteLater()
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
