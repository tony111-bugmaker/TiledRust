"""对象层右键复制：吸附鼠标粘贴、Shift 连粘、整组命名 / Ctrl 并入、整合体保留。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QPushButton,
)

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.widgets.copy_group_dialog import (  # noqa: E402
    CANCEL, KEEP_ORIGINAL, RENAME, CopyGroupDialog,
)
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import (  # noqa: E402
    EDITOR_GROUP_PROPERTY, MERGE_GROUP_PROPERTY, MERGE_PRIMARY_PROPERTY,
)


def _make_doc(objs):
    tile = TileLayer(width=40, height=30, name="Ground",
                     cells=[0] * (40 * 30))
    layer = ObjectLayer(name="Triggers", objects=list(objs))
    doc = MapDocument(width=40, height=30, tile_w=20, tile_h=20,
                      layers=[tile, layer], tilesets=[])
    return doc, layer


def _obj(oid, name, **props):
    return MapObject(object_id=oid, name=name, shape="rect",
                     x=100 + (oid - 1) * 60, y=100, width=40, height=20,
                     properties=__import__(
                         "rwmap.document.properties", fromlist=["PropertyBag"]
                     ).PropertyBag(values={k: str(v) for k, v in props.items()}))


class ObjectPasteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = MainWindow(auto_open=False)
        obj = _obj(1, "A", foo="bar")
        obj.xml_element = object()          # 复制品必须清掉，不能复用原 DOM
        self.doc, self.layer = _make_doc([obj])
        self.w.state.doc = self.doc
        self.w.state.profile.write_enabled = True
        self.w.state.write_enabled = True
        self.w.scene.set_document(self.doc)
        self.w._on_layer_selected(self.layer)
        self.w._set_object_tool("select")
        self.w.scene.set_selected_objects([obj])
        self.w._paste_right_xy = (100.0, 100.0)
        QApplication.processEvents()

    def tearDown(self):
        self.w._cancel_object_paste(silent=True)

    def _copy(self):
        self.w._copy_objects_for_paste()
        self.assertTrue(self.w.view.paste_mode)
        self.assertEqual(len(self.w._paste_clones), 1)

    def test_context_menu_has_copy_action(self):
        menu = self.w._build_object_context_menu()
        self.assertIsNotNone(menu)
        self.assertIn("复制（吸附鼠标）", [a.text() for a in menu.actions()])

    # ---- 单对象 ----
    def test_copy_clone_keeps_properties_but_not_dom_or_id(self):
        self._copy()
        src = self.doc.layers[1].objects[0]
        clone = self.w._paste_clones[0]
        self.assertIsNone(clone.xml_element)
        self.assertEqual(clone.properties.get("foo"), "bar")
        self.assertEqual(clone.name, "A")
        self.assertEqual((clone.x, clone.y), (0.0, 0.0))  # 已减掉锚点

    def test_preview_follows_cursor(self):
        self._copy()
        self.w._on_paste_move(400.0, 300.0)
        preview = self.w.scene._paste_preview
        self.assertIsNotNone(preview)
        self.assertEqual(preview._rects, [(400.0, 300.0, 40.0, 20.0)])

    def test_place_creates_new_object_at_cursor(self):
        self._copy()
        self.w._on_paste_place(300.0, 250.0, Qt.NoModifier)
        objs = self.doc.layers[1].objects
        self.assertEqual(len(objs), 2)
        new = objs[1]
        self.assertEqual((new.x, new.y), (300.0, 250.0))
        self.assertNotEqual(new.object_id, objs[0].object_id)
        self.assertIsNone(new.xml_element)
        self.assertEqual(new.properties.get("foo"), "bar")
        self.assertIs(new, self.w.scene.selected_object)
        self.assertFalse(self.w.view.paste_mode)
        self.assertEqual(self.w.state.undo_stack.count(), 1)

    # ---- 整组 ----
    def _group_setup(self):
        a, b = _obj(1, "A", **{EDITOR_GROUP_PROPERTY: "G1"}), \
               _obj(2, "B", **{EDITOR_GROUP_PROPERTY: "G1"})
        doc, layer = _make_doc([a, b])
        self.w.state.doc = doc
        self.doc, self.layer = doc, layer
        self.w.scene.set_document(doc)
        self.w._on_layer_selected(layer)
        self.w.scene.set_selected_objects([a, b])
        self.w._paste_right_xy = (100.0, 100.0)
        return doc, layer

    @patch("rwmap.app.main_window.ask_copy_group_name",
           return_value=(RENAME, "新组X"))
    def test_group_prompts_on_placement(self, ask):
        doc, layer = self._group_setup()
        self.w._copy_objects_for_paste()
        ask.assert_not_called()          # 复制时不弹，左键按下才弹
        self.assertTrue(self.w._paste_meta["is_group"])
        self.w._on_paste_place(200.0, 200.0, Qt.NoModifier)
        ask.assert_called_once_with(self.w, "G1")
        self.assertEqual(len(layer.objects), 4)
        for obj in layer.objects[2:]:
            self.assertEqual(obj.properties.get(EDITOR_GROUP_PROPERTY), "新组X")

    @patch("rwmap.app.main_window.ask_copy_group_name",
           return_value=(KEEP_ORIGINAL, ""))
    def test_keep_original_button(self, ask):
        doc, layer = self._group_setup()
        self.w._copy_objects_for_paste()
        self.w._on_paste_place(200.0, 200.0, Qt.NoModifier)
        for obj in layer.objects[2:]:
            self.assertEqual(obj.properties.get(EDITOR_GROUP_PROPERTY), "G1")

    @patch("rwmap.app.main_window.ask_copy_group_name",
           return_value=(RENAME, "不该出现"))
    def test_ctrl_at_placement_skips_dialog(self, ask):
        doc, layer = self._group_setup()
        self.w._copy_objects_for_paste()
        ask.assert_not_called()
        self.w._on_paste_place(200.0, 200.0, Qt.ControlModifier)
        ask.assert_not_called()
        for obj in layer.objects[2:]:
            self.assertEqual(obj.properties.get(EDITOR_GROUP_PROPERTY), "G1")

    @patch("rwmap.app.main_window.ask_copy_group_name",
           return_value=(CANCEL, ""))
    def test_dialog_cancel_aborts_placement_but_keeps_paste(self, ask):
        doc, layer = self._group_setup()
        self.w._copy_objects_for_paste()
        self.w._on_paste_place(200.0, 200.0, Qt.NoModifier)
        ask.assert_called_once()
        self.assertEqual(len(layer.objects), 2)
        self.assertTrue(self.w.view.paste_mode)
        self.assertEqual(len(self.w._paste_clones), 2)

    @patch("rwmap.app.main_window.ask_copy_group_name",
           side_effect=[(RENAME, "G1副本"), (RENAME, "G2副本")])
    def test_multiple_groups_prompt_in_order(self, ask):
        a = _obj(1, "A", **{EDITOR_GROUP_PROPERTY: "G1"})
        b = _obj(2, "B", **{EDITOR_GROUP_PROPERTY: "G2"})
        c = _obj(3, "C", **{EDITOR_GROUP_PROPERTY: "G1"})
        doc, layer = _make_doc([a, b, c])
        self.w.state.doc = doc
        self.doc, self.layer = doc, layer
        self.w.scene.set_document(doc)
        self.w._on_layer_selected(layer)
        self.w.scene.set_selected_objects([a, b, c])
        self.w._paste_right_xy = (100.0, 100.0)
        self.w._copy_objects_for_paste()
        ask.assert_not_called()
        self.w._on_paste_place(300.0, 300.0, Qt.NoModifier)
        self.assertEqual([c.args[1] for c in ask.call_args_list], ["G1", "G2"])
        new = layer.objects[3:]
        self.assertEqual([o.properties.get(EDITOR_GROUP_PROPERTY) for o in new],
                         ["G1副本", "G2副本", "G1副本"])

    def test_paste_hides_static_selection_box(self):
        box = self.w.scene._obj_sel_box
        self.assertIsNotNone(box)
        self.assertTrue(box.isVisible())
        self._copy()
        self.assertFalse(box.isVisible())
        self.w._on_paste_move(400.0, 300.0)
        self.assertFalse(box.isVisible())
        self.w._cancel_object_paste(silent=True)
        self.assertTrue(box.isVisible())

    # ---- 连续粘贴 ----
    def test_shift_keeps_pasting(self):
        self._copy()
        self.w._on_paste_place(200.0, 200.0, Qt.ShiftModifier)
        self.assertTrue(self.w.view.paste_mode)
        self.w._on_paste_place(400.0, 400.0, Qt.NoModifier)
        self.assertEqual(len(self.doc.layers[1].objects), 3)
        self.assertFalse(self.w.view.paste_mode)

    # ---- 整合体 ----
    def test_merge_relation_kept_with_new_gid(self):
        a = _obj(1, "A", **{MERGE_GROUP_PROPERTY: "oldGid",
                            MERGE_PRIMARY_PROPERTY: "1"})
        b = _obj(2, "B", **{MERGE_GROUP_PROPERTY: "oldGid"})
        doc, layer = _make_doc([a, b])
        self.w.state.doc = doc
        self.doc, self.layer = doc, layer
        self.w.scene.set_document(doc)
        self.w._on_layer_selected(layer)
        self.w.scene.set_selected_objects([a, b])
        self.w._paste_right_xy = (100.0, 100.0)
        self.w._copy_objects_for_paste()
        self.w._on_paste_place(300.0, 300.0, Qt.NoModifier)
        new_a, new_b = layer.objects[2], layer.objects[3]
        new_gid = new_a.properties.get(MERGE_GROUP_PROPERTY)
        self.assertEqual(new_gid, new_b.properties.get(MERGE_GROUP_PROPERTY))
        self.assertNotEqual(new_gid, "oldGid")
        self.assertEqual(new_a.properties.get(MERGE_PRIMARY_PROPERTY), "1")
        self.assertFalse(new_b.properties.get(MERGE_PRIMARY_PROPERTY))


class CopyGroupDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_wording_and_buttons(self):
        dlg = CopyGroupDialog("G1")
        label = dlg.findChild(QLabel)
        self.assertEqual(label.text(), "从「G1」复制的内容名字改为：")
        self.assertEqual(dlg.name_edit.text(), "G1_副本")
        self.assertEqual([b.text() for b in dlg.findChildren(QPushButton)],
                         ["归到原来的组中", "OK", "Cancel"])


if __name__ == "__main__":
    unittest.main()
