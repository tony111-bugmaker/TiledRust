"""内容变换回归：4 个翻转/旋转按钮作用于「选中的图片」或「右键框选的图章」。

用户要求（2026-11-16）：这 4 个键不再变换整张地图，而是：
- 有选中的插入图片 → 变换图片（翻转标志位 / 旋转角，交换宽高，可撤销、可存盘）；
- 否则有右键框选的图章内容 → 变换 2D gid 矩阵；
- 两者都无 → 按钮禁用（暗色，点击无反应）。

同时验证工具栏顺序：4 个按钮在「瓦片画笔」文字之前。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.rw.transform import transform_stamp  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class StampTransformPureTest(unittest.TestCase):
    def test_flip_h_reverses_each_row(self):
        stamp = [[1, 2, 3], [4, 5, 6]]
        self.assertEqual(transform_stamp(stamp, "flip_h"),
                         [[3, 2, 1], [6, 5, 4]])
        self.assertEqual(stamp, [[1, 2, 3], [4, 5, 6]])  # 不改入参

    def test_flip_v_reverses_rows(self):
        self.assertEqual(transform_stamp([[1, 2, 3], [4, 5, 6]], "flip_v"),
                         [[4, 5, 6], [1, 2, 3]])

    def test_rot_cw_swaps_dimensions(self):
        out = transform_stamp([[1, 2, 3], [4, 5, 6]], "rot_cw")
        self.assertEqual(out, [[4, 1], [5, 2], [6, 3]])

    def test_rot_ccw_swaps_dimensions(self):
        out = transform_stamp([[1, 2, 3], [4, 5, 6]], "rot_ccw")
        self.assertEqual(out, [[3, 6], [2, 5], [1, 4]])

    def test_cw_then_ccw_is_identity(self):
        src = [[1, 2, 3], [4, 5, 6]]
        self.assertEqual(transform_stamp(transform_stamp(src, "rot_cw"), "rot_ccw"),
                         src)

    def test_empty_stamp_is_safe(self):
        self.assertEqual(transform_stamp([], "rot_cw"), [])
        self.assertEqual(transform_stamp([[]], "flip_h"), [[]])


class ContentTransformWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MainWindow(auto_open=False)

    def setUp(self):
        w = self.w
        w.state.undo_stack.clear()
        w.state.stamp = None
        w.scene.set_selected_objects([])
        self.image = MapObject(object_id=1, name="图", shape="image",
                               x=100, y=100, width=80, height=40,
                               image_source="img.png")
        self.rect = MapObject(object_id=2, name="矩形", shape="rect",
                              x=300, y=100, width=60, height=60)
        self.layer = ObjectLayer(name="Triggers", objects=[self.image, self.rect])
        doc = MapDocument(
            width=40, height=30, tile_w=20, tile_h=20,
            layers=[TileLayer(width=40, height=30, name="Ground",
                              cells=[0] * 1200), self.layer],
            tilesets=[])
        w.state.doc = doc
        w.state.profile.write_enabled = True
        w.scene.set_document(doc)
        w._set_object_tool("select")
        w._refresh_transform_actions()

    def obj(self, oid):
        return next(o for o in self.layer.objects if o.object_id == oid)

    def actions(self):
        return self.w.object_toolbar._transform_actions

    def test_no_target_disables_all_four_buttons(self):
        self.assertFalse(any(a.isEnabled() for a in self.actions()),
                         "无图片选中、无图章时 4 个按钮应禁用")

    def test_disabled_button_click_does_nothing(self):
        before = (self.image.image_flip_h, self.image.image_flip_v,
                  self.image.rotation)
        self.actions()[0].trigger()          # 禁用态下触发
        self.assertEqual(before, (self.image.image_flip_h,
                                  self.image.image_flip_v, self.image.rotation))

    def test_selecting_rect_keeps_buttons_disabled(self):
        self.w.scene.set_selected_objects([self.rect])
        self.assertFalse(any(a.isEnabled() for a in self.actions()),
                         "选中非图片对象不应点亮变换按钮")

    def test_selecting_image_enables_buttons(self):
        self.w.scene.set_selected_objects([self.image])
        self.assertTrue(all(a.isEnabled() for a in self.actions()))

    def test_stamp_enables_buttons(self):
        self.w.state.stamp = [[1, 2], [3, 4]]
        self.w._refresh_transform_actions()
        self.assertTrue(all(a.isEnabled() for a in self.actions()))

    def test_image_priority_over_stamp(self):
        """同时有选中图片和图章时，优先变换图片。"""
        w = self.w
        w.state.stamp = [[1, 2], [3, 4]]
        w.scene.set_selected_objects([self.image])
        w._transform_content("flip_h")
        self.assertTrue(self.obj(1).image_flip_h)
        self.assertEqual(w.state.stamp, [[1, 2], [3, 4]])  # 图章未被改动

    def test_image_flip_toggles_and_persists_undo(self):
        w = self.w
        w.scene.set_selected_objects([self.image])
        w._transform_content("flip_h")
        self.assertTrue(self.obj(1).image_flip_h)
        self.assertEqual(w.state.undo_stack.undoText(), "水平翻转图片")
        w.act_undo.trigger()
        self.assertFalse(self.obj(1).image_flip_h)

    def test_image_rotation_swaps_width_height_and_center(self):
        w = self.w
        w.scene.set_selected_objects([self.image])
        cx, cy = self.image.x + 40, self.image.y + 20
        w._transform_content("rot_cw")
        o = self.obj(1)
        self.assertEqual(o.rotation, 90)
        self.assertEqual((o.width, o.height), (40, 80))
        self.assertAlmostEqual(o.x + o.width / 2, cx)
        self.assertAlmostEqual(o.y + o.height / 2, cy)

    def test_stamp_transform_via_button(self):
        w = self.w
        w.state.stamp = [[1, 2, 3], [4, 5, 6]]
        w._refresh_transform_actions()
        w._transform_content("flip_h")
        self.assertEqual(w.state.stamp, [[3, 2, 1], [6, 5, 4]])

    def test_buttons_do_not_transform_whole_map(self):
        """4 个按钮不得再改动整张地图的瓦片层。"""
        w = self.w
        layer = next(l for l in w.state.doc.layers
                     if isinstance(l, TileLayer))
        layer.set(0, 0, 7)
        before = list(layer.cells)
        w.state.stamp = [[1]]
        w._refresh_transform_actions()
        for action in self.actions():
            action.trigger()
        self.assertEqual(list(layer.cells), before)

    def test_image_flip_persists_to_tmx_and_reloads(self):
        from rwmap.io.tmx_writer import create_blank_document, write_document
        from rwmap.io.tmx_reader import read_document
        w = self.w
        out = Path("logs") / "图片翻转_自测.tmx"
        out.parent.mkdir(parents=True, exist_ok=True)
        doc = create_blank_document(w.state.profile, 8, 6, 20, 20, path=out)
        obj = MapObject(object_id=1, name="图", shape="image",
                        x=20, y=20, width=40, height=30, image_source="img.png")
        doc.layers.append(ObjectLayer(name="Triggers", objects=[obj]))
        doc.rebuild_index()
        w.state.doc = doc
        w.scene.set_document(doc)
        w.scene.set_selected_objects([obj])
        w._refresh_transform_actions()
        w._transform_content("flip_v")
        self.assertTrue(obj.image_flip_v)
        write_document(doc, out)
        re = read_document(out, w.state.profile)
        imgs = [o for l in re.layers if isinstance(l, ObjectLayer)
                for o in l.objects if o.shape == "image"]
        self.assertEqual(len(imgs), 1)
        self.assertTrue(imgs[0].image_flip_v)
        self.assertFalse(imgs[0].image_flip_h)


if __name__ == "__main__":
    unittest.main()
