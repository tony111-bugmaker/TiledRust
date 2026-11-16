"""回归：Triggers 语义编辑 / 对象可视化编辑 / 对象层保真写回。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.io.path_resolver import ResourceResolver  # noqa: E402
from rwmap.io.tmx_reader import read_document  # noqa: E402
from rwmap.io.tmx_writer import write_document  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402


def object_layers(doc):
    return [l for l in doc.layers if isinstance(l, ObjectLayer)]


def legacy_editor(w):
    """旧「Triggers / 对象」树已从主窗口移除；撤销命令链用独立实例回归。"""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from rwmap.widgets.triggers_dock import TriggersDock

    dock = TriggersDock()
    dock.set_document(w.state.doc, w.state.undo_stack, w._refresh_objects)
    return dock


class ObjectWritebackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile("1.15")
        cls.sample = Path(cls.profile.sample_map)
        if not cls.sample.is_file():
            raise unittest.SkipTest("缺少示例地图")
        cls.resolver = ResourceResolver(cls.sample, cls.profile)

    def _read(self):
        return read_document(self.sample, self.profile, resolver=self.resolver)

    def _reload(self, path):
        # 临时 tmx 不在游戏目录，沿用原图的资源解析器找外部 .tsx。
        return read_document(path, self.profile, resolver=self.resolver)

    def test_edit_property_is_written_back(self):
        doc = self._read()
        layers = object_layers(doc)
        self.assertTrue(layers, "官方示例图没有对象层")
        obj = layers[0].objects[0]
        obj.properties.set("type", "workbuddy_test")
        obj.properties.set("new_key", "hello")
        layers[0].dirty = True
        doc.dirty = True
        with tempfile.TemporaryDirectory() as tmp:
            out = write_document(doc, Path(tmp) / "object_edit.tmx")
            again = self._reload(out)
            obj2 = object_layers(again)[0].objects[0]
            self.assertEqual(obj2.properties.get("type"), "workbuddy_test")
            self.assertEqual(obj2.properties.get("new_key"), "hello")

    def test_move_and_rename_are_written_back(self):
        doc = self._read()
        layer = object_layers(doc)[0]
        obj = layer.objects[0]
        obj.x, obj.y, obj.name = 100.0, 140.0, "renamed_object"
        layer.dirty = True
        doc.dirty = True
        with tempfile.TemporaryDirectory() as tmp:
            out = write_document(doc, Path(tmp) / "object_move.tmx")
            again = self._reload(out)
            obj2 = object_layers(again)[0].objects[0]
            self.assertEqual((obj2.x, obj2.y), (100.0, 140.0))
            self.assertEqual(obj2.name, "renamed_object")

    def test_add_delete_object_and_nextobjectid(self):
        from rwmap.document.map_object import MapObject
        doc = self._read()
        layer = object_layers(doc)[0]
        count = len(layer.objects)
        new_id = max(o.object_id for o in layer.objects) + 1
        layer.objects.append(MapObject(object_id=new_id, name="probe_object",
                                       x=60, y=80, width=20, height=20))
        layer.dirty = True
        doc.dirty = True
        with tempfile.TemporaryDirectory() as tmp:
            out = write_document(doc, Path(tmp) / "object_add.tmx")
            again = self._reload(out)
            layer2 = object_layers(again)[0]
            self.assertEqual(len(layer2.objects), count + 1)
            self.assertEqual(layer2.objects[-1].name, "probe_object")
            self.assertGreaterEqual(int(again.tree.getroot().get("nextobjectid", 0)),
                                    new_id + 1)
            # 删除后写回
            layer2.objects.pop()
            layer2.dirty = True
            again.dirty = True
            out2 = write_document(again, Path(tmp) / "object_del.tmx")
            final = self._reload(out2)
            self.assertEqual(len(object_layers(final)[0].objects), count)

    def test_unedited_document_is_byte_identical(self):
        """没改对象层时，写回不能动 <objectgroup> 的一个字节。"""
        doc = self._read()
        original = Path(self.sample).read_bytes()
        cells = doc.layer_by_name("Ground")
        cells.cells[cells.index(30, 30)] = 126
        cells.dirty = True
        doc.dirty = True
        with tempfile.TemporaryDirectory() as tmp:
            out = write_document(doc, Path(tmp) / "untouched.tmx")
            body = Path(out).read_bytes()
        # 除了被编辑那一层的 data，其余都应与原文相同；这里重点检查 objectgroup 未变。
        from lxml import etree
        a = etree.fromstring(original[original.index(b"<map"):])
        b = etree.fromstring(body[body.index(b"<map"):])
        ga = [etree.tostring(x) for x in a if x.tag == "objectgroup"]
        gb = [etree.tostring(x) for x in b if x.tag == "objectgroup"]
        self.assertEqual(ga, gb)


class ObjectEditUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.w = MainWindow(auto_open=False)
        cls.w.resize(1400, 900)
        cls.w.show()
        QApplication.processEvents()
        sample = Path(cls.w.state.profile.sample_map)
        if not sample.is_file():
            raise unittest.SkipTest("缺少示例地图")
        cls.sample = sample
        cls.w._open(sample)
        QApplication.processEvents()

    def setUp(self):
        self.w.state.undo_stack.clear()
        self.w.scene.set_selected_object(None)
        # 重新打开示例图：示例图仅 1 个对象层且只有 1 个对象，
        # 每用例都重置文档，避免上一个用例的删除 / 移动影响后续断言。
        if self.sample.is_file():
            self.w._open(self.sample)
            QApplication.processEvents()

    def test_triggers_dock_is_editable_and_undoable(self):
        from rwmap.commands.object_command import ObjectCommand
        w = self.w
        layer = object_layers(w.state.doc)[0]
        obj = layer.objects[0]
        key = next(iter(obj.properties.values), "wb_test")
        old = obj.properties.get(key, "")
        legacy_editor(w)._edit_value(layer, obj, key, "wb_edited")
        self.assertEqual(w.state.undo_stack.command(0).text().startswith("修改属性"), True)
        current = object_layers(w.state.doc)[0].objects[0]
        self.assertEqual(current.properties.get(key), "wb_edited")
        w.state.undo_stack.undo()
        restored = object_layers(w.state.doc)[0].objects[0]
        self.assertEqual(restored.properties.get(key), old)
        w.state.undo_stack.redo()
        self.assertIsInstance(w.state.undo_stack.command(0), ObjectCommand)

    def test_dock_add_and_delete_object_via_command(self):
        w = self.w
        layer = object_layers(w.state.doc)[0]
        count = len(layer.objects)
        # 绕过 QInputDialog，直接走 _commit 的同一条撤销链
        from rwmap.commands.object_command import ObjectCommand, snapshot
        from rwmap.document.map_object import MapObject
        before = snapshot(layer)
        new_id = max(o.object_id for o in layer.objects) + 1
        layer.objects.append(MapObject(object_id=new_id, name="ui_probe",
                                       x=40, y=40, width=20, height=20))
        after = snapshot(layer)
        w.state.undo_stack.push(ObjectCommand(
            w.state.doc, layer, before, after, "新建对象", w._refresh_objects))
        self.assertEqual(len(object_layers(w.state.doc)[0].objects), count + 1)
        w.state.undo_stack.undo()
        self.assertEqual(len(object_layers(w.state.doc)[0].objects), count)

    def test_ctrl_click_selects_object_on_canvas(self):
        w = self.w
        layer = object_layers(w.state.doc)[0]
        obj = layer.objects[0]
        x, y = obj.x + (obj.width or 20) / 2, obj.y + (obj.height or 20) / 2
        w.view.objectPressed.emit(x, y)
        self.assertIs(w.scene.selected_object, obj)
        w.view.objectReleased.emit(x, y)
        self.assertIsNone(w._sel_move_drag)

    def test_drag_moves_object(self):
        w = self.w
        layer = object_layers(w.state.doc)[0]
        obj = layer.objects[0]
        old = (obj.x, obj.y)
        w.scene.set_selected_objects([obj])
        x, y = obj.x + 10, obj.y + 10
        w.view.objectPressed.emit(x, y)
        self.assertIsNotNone(w._sel_move_drag)
        w.view.objectMoved.emit(x + 20, y + 20)
        w.view.objectReleased.emit(x + 20, y + 20)
        cur = object_layers(w.state.doc)[0].objects[0]
        self.assertEqual((cur.x, cur.y), (old[0] + 20, old[1] + 20))
        self.assertIsNone(w._sel_move_drag)
        # 拖动整组 = 一条撤销记录，撤销后回到原位
        self.assertEqual(w.state.undo_stack.count(), 1)
        w.state.undo_stack.undo()
        back = object_layers(w.state.doc)[0].objects[0]
        self.assertEqual((back.x, back.y), old)

    def test_delete_selected_object(self):
        w = self.w
        layer = object_layers(w.state.doc)[0]
        obj = layer.objects[0]
        count = len(layer.objects)
        w.scene.set_selected_objects([obj])
        w._delete_selected_object()
        self.assertEqual(len(object_layers(w.state.doc)[0].objects), count - 1)
        # 删除可撤销：撤销后对象（含原 <object> 元素）回来
        self.assertEqual(w.state.undo_stack.count(), 1)
        w.state.undo_stack.undo()
        self.assertEqual(len(object_layers(w.state.doc)[0].objects), count)

    def test_object_layer_paint_does_not_crash(self):
        from PySide6.QtWidgets import QApplication
        self.w.view.grab()
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
