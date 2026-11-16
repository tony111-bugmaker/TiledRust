"""对象选中包围框 + 整组拖动 + Shift 按格 + 组合/对齐/分布（离屏）。

第十一轮：PPT 风格的对象选择——单个整体包围框，框内拖动整组移动；
右键组合 / 对齐 / 分布。第三十三轮起，整体框四角 / 四边恢复 8 个黄色
缩放箭头（可拖拽等比缩放整组），命中判定不允许侵入框内以免挡住拖动。
第十四轮：这些操作全部接入撤销栈（每条操作一条 Ctrl+Z）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsRectItem  # noqa: E402
from shiboken6 import delete as _sip_delete  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import EDITOR_GROUP_PROPERTY  # noqa: E402

SHIFT = "rwmap.app.main_window.QApplication.keyboardModifiers"


def _make_doc():
    tile = TileLayer(width=40, height=30, name="Ground",
                     cells=[0] * (40 * 30))
    objs = [
        MapObject(object_id=1, name="A", shape="rect", x=100, y=100,
                  width=40, height=20),
        MapObject(object_id=2, name="B", shape="rect", x=200, y=100,
                  width=40, height=20),
        MapObject(object_id=3, name="C", shape="rect", x=300, y=100,
                  width=40, height=20),
    ]
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    doc = MapDocument(width=40, height=30, tile_w=20, tile_h=20,
                      layers=[tile, obj_layer], tilesets=[])
    return doc, obj_layer, objs


class ObjectSelectionGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = MainWindow(auto_open=False)
        self.w.resize(1200, 800)
        self.doc, self.obj_layer, self.objs = _make_doc()
        self.w.state.doc = self.doc
        self.w.state.profile.write_enabled = True
        self.w.state.write_enabled = True
        self.w.scene.set_document(self.doc)
        self.w.layers_dock.doc = self.doc
        self.w._on_layer_selected(self.obj_layer)
        self.w._set_object_tool("select")
        QApplication.processEvents()

    # ---- 包围框 ----
    def test_selection_rect_safe_after_item_deleted(self):
        """文档重载后选框 item 被 clear() 销毁，set_selection_rect(None)
        不应再抛 'Internal C++ object already deleted'。"""
        item = QGraphicsRectItem()
        self.w.scene.addItem(item)
        _sip_delete(item)                      # 强制销毁底层 C++ 对象
        self.w.scene._selection_item = item    # 模拟悬空引用（与崩溃现场一致）
        self.w.scene.set_selection_rect(None)  # 必须不抛异常
        self.assertIsNone(self.w.scene._selection_item)

    def test_selection_box_contains_all_objects(self):
        self.w.scene.set_selected_objects(self.objs)
        box = self.w._obj_selection_box()
        self.assertEqual(box, QRectF(100, 100, 240, 20))
        self.assertTrue(self.w.scene._obj_sel_box.isVisible())

    def test_single_selection_box_matches_object(self):
        self.w.scene.set_selected_objects([self.objs[0]])
        self.assertEqual(self.w._obj_selection_box(), QRectF(100, 100, 40, 20))

    def test_clear_selection_hides_box(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w.scene.set_selected_objects([])
        self.assertIsNone(self.w._obj_selection_box())
        self.assertFalse(self.w.scene._obj_sel_box.isVisible())

    # ---- 框内拖动整组移动 ----
    def test_press_inside_box_begins_group_move(self):
        self.w.scene.set_selected_objects(self.objs)
        # 框内一点（非任意单对象内部也行，只要落在整体包围框内）
        self.w.view.objectPressed.emit(150.0, 110.0)
        self.assertIsNotNone(self.w._sel_move_drag)
        self.w.view.objectReleased.emit(150.0, 110.0)
        self.assertIsNone(self.w._sel_move_drag)

    def test_group_move_shifts_every_object(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._begin_selection_move(150.0, 110.0)
        self.w._do_selection_move(170.0, 130.0)   # dx=20, dy=20
        base = {1: (120, 120), 2: (220, 120), 3: (320, 120)}
        for o in self.objs:
            self.assertEqual((o.x, o.y), base[o.object_id])
        self.assertEqual(self.w._obj_selection_box(), QRectF(120, 120, 240, 20))

    def test_shift_move_snaps_to_grid(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._begin_selection_move(0.0, 0.0)
        with patch(SHIFT, return_value=Qt.ShiftModifier):
            self.w._do_selection_move(60.0, 0.0)    # 60 → 整 3 格（20px）
        # 每个对象 x 偏移 +60，y 不变
        self.assertEqual(self.objs[0].x, 160.0)
        self.assertEqual(self.objs[1].x, 260.0)
        self.assertEqual(self.objs[2].x, 360.0)
        for o in self.objs:
            self.assertEqual(o.y, 100.0)

    # ---- 组合 ----
    def test_group_assigns_property_and_selects_all(self):
        # 给 A、B 同一个组名
        self.objs[0].properties.set(EDITOR_GROUP_PROPERTY, "7", "string")
        self.objs[1].properties.set(EDITOR_GROUP_PROPERTY, "7", "string")
        self.w.scene.set_selected_objects([self.objs[0]])
        self.w._select_object_with_group(self.objs[0])
        self.assertEqual(len(self.w.scene.selected_objects), 2)
        self.assertIn(self.objs[1], self.w.scene.selected_objects)

    def test_group_command_groups_selection(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._group_selected_objects("G1")
        gid = self.objs[0].properties.get(EDITOR_GROUP_PROPERTY)
        self.assertIsNotNone(gid)
        for o in self.objs:
            self.assertEqual(o.properties.get(EDITOR_GROUP_PROPERTY), gid)

    def test_ungroup_clears_property(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._group_selected_objects("G1")
        self.w._ungroup_selected_objects()
        # 撤销命令用快照替换 layer.objects，对象实例会换新，按 id 重新取。
        for o in self.obj_layer.objects:
            self.assertNotIn(EDITOR_GROUP_PROPERTY, o.properties)

    # ---- 对齐 ----
    def test_align_left(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._align_selected("left")
        for o in self.objs:
            self.assertEqual(o.x, 100.0)

    def test_align_right(self):
        self.w.scene.set_selected_objects(self.objs)
        self.w._align_selected("right")
        for o in self.objs:
            self.assertEqual(o.x + o.width, 340.0)

    def test_align_top(self):
        # 让三个对象纵向不同，再顶对齐
        for i, o in enumerate(self.objs):
            o.y = 100.0 + i * 30
        self.w.scene.set_selected_objects(self.objs)
        self.w._align_selected("top")
        for o in self.objs:
            self.assertEqual(o.y, 100.0)

    def test_align_vertical_center(self):
        for i, o in enumerate(self.objs):
            o.y = 100.0 + i * 30
            o.height = 20.0
        self.w.scene.set_selected_objects(self.objs)
        self.w._align_selected("vcenter")
        # 选中整体纵向跨度：顶 100、底 160+20=180，中线 = 140
        for o in self.objs:
            self.assertEqual(o.y + o.height / 2.0, 140.0)

    # ---- 分布 ----
    def test_distribute_horizontal(self):
        # 故意排得不均匀：B 靠 A
        self.objs[0].x, self.objs[0].width = 100.0, 40.0
        self.objs[1].x, self.objs[1].width = 180.0, 40.0   # 中间挤
        self.objs[2].x, self.objs[2].width = 300.0, 40.0
        self.w.scene.set_selected_objects(self.objs)
        self.w._distribute_selected("h")
        # 左=100，右=340，宽总和 120，gap=(240-120)/2=60
        self.assertEqual(self.objs[0].x, 100.0)
        self.assertEqual(self.objs[1].x, 200.0)
        self.assertEqual(self.objs[2].x, 300.0)

    # ---- 删除多选 ----
    def test_delete_multiple(self):
        self.w.scene.set_selected_objects([self.objs[0], self.objs[1]])
        before = len(self.obj_layer.objects)
        self.w._delete_selected_object()
        self.assertEqual(len(self.obj_layer.objects), before - 2)
        self.assertEqual(len(self.w.scene.selected_objects), 0)


class ObjectGroupVisualTest(unittest.TestCase):
    """组合在点阵图（地图画布）上可见 + 右键菜单「拆除组合」条件出现。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = MainWindow(auto_open=False)
        self.w.resize(1200, 800)
        self.doc, self.obj_layer, self.objs = _make_doc()
        self.w.state.doc = self.doc
        self.w.state.profile.write_enabled = True
        self.w.state.write_enabled = True
        self.w.scene.set_document(self.doc)
        self.w.layers_dock.doc = self.doc
        self.w._on_layer_selected(self.obj_layer)
        self.w._set_object_tool("select")
        QApplication.processEvents()

    # ---- 点阵图可见 ----
    def _render_scene(self):
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtCore import QRectF, Qt
        tw, th = self.doc.tile_w, self.doc.tile_h
        W, H = self.doc.width * tw, self.doc.height * th
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        self.w.scene.render(p, QRectF(0, 0, W, H), QRectF(0, 0, W, H))
        p.end()
        return img

    @staticmethod
    def _color_near(c, target, tol=40):
        return (abs(c.red() - target.red()) < tol
                and abs(c.green() - target.green()) < tol
                and abs(c.blue() - target.blue()) < tol)

    def test_group_hull_renders_on_map(self):
        from rwmap.view.object_layer_item import group_color
        # 未分组时不应出现组色描边
        img0 = self._render_scene()
        col = group_color("G1")
        before = any(self._color_near(img0.pixelColor(x, y), col)
                     for y in range(0, img0.height(), 3)
                     for x in range(0, img0.width(), 3))
        self.assertFalse(before, "未分组就出现了组色像素")
        # 分组后再渲染
        self.w.scene.set_selected_objects(self.objs)
        self.w._group_selected_objects("G1")
        img1 = self._render_scene()
        found = any(self._color_near(img1.pixelColor(x, y), col)
                    for y in range(0, img1.height(), 2)
                    for x in range(0, img1.width(), 2))
        self.assertTrue(found, "分组后地图（点阵图）上未出现组色描边")

    def test_ungroup_clears_map_visual(self):
        from rwmap.view.object_layer_item import group_color
        self.w.scene.set_selected_objects(self.objs)
        self.w._group_selected_objects("G1")
        self.w._ungroup_selected_objects()
        img = self._render_scene()
        col = group_color("G1")
        found = any(self._color_near(img.pixelColor(x, y), col)
                    for y in range(0, img.height(), 2)
                    for x in range(0, img.width(), 2))
        self.assertFalse(found, "拆除组合后地图（点阵图）上仍有组色像素")

    # ---- 右键菜单 ----
    def test_menu_shows_dismantle_only_when_grouped(self):
        self.w.scene.set_selected_objects(self.objs)
        texts = [a.text() for a in self.w._build_object_context_menu().actions()]
        self.assertIn("组合", texts)
        self.assertNotIn("拆除组合", texts)
        # 分组后再看菜单
        self.w._group_selected_objects("G1")
        # 重新取选中（分组不会清空选区，但仍显式设置一遍）
        self.w.scene.set_selected_objects(self.objs)
        texts2 = [a.text() for a in self.w._build_object_context_menu().actions()]
        self.assertIn("组合", texts2)
        self.assertIn("拆除组合", texts2)

    def test_group_via_input_dialog(self):
        from unittest import mock
        from PySide6.QtWidgets import QInputDialog
        self.w.scene.set_selected_objects(self.objs)
        with mock.patch.object(QInputDialog, "getText",
                               return_value=("我的组", True)):
            self.w._group_selected_objects()      # 无 name → 弹输入框
        gid = self.objs[0].properties.get(EDITOR_GROUP_PROPERTY)
        self.assertEqual(gid, "我的组")
        for o in self.objs:
            self.assertEqual(o.properties.get(EDITOR_GROUP_PROPERTY), "我的组")


if __name__ == "__main__":
    unittest.main()
