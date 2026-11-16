"""第三十三轮回归：选中对象的 8 个黄色缩放箭头 + 拖拽缩放。

背景：第十几轮改成「PPT 风格整体包围框」时把缩放箭头和拖拽缩放一起删了，
但功能表仍写着「8 手柄自由缩放」。用户用「选择对象」（鼠标箭头图标）左键
点矩形后，看不到放大缩小的箭头。本轮把它恢复：

- 箭头画在整体选中框的四角 / 四边（``ObjectSelectionBoxItem``，屏幕固定像素）；
- 拖拽按方向改 x/y/width/height，多边形按比例缩放 points，点对象只挪中心；
- 多选时整组按整体框等比缩放；
- 一次拖动 = 一条撤销；Esc 取消恢复且不留记录；Shift 吸附网格；
- 视图缩小时命中区不得侵入对象内部（否则点内部会变成缩放、拖不动）。
"""
from __future__ import annotations

import unittest
from unittest import mock

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from rwmap.app.main_window import MainWindow
from rwmap.document.layer import ObjectLayer, TileLayer
from rwmap.document.map_document import MapDocument
from rwmap.document.map_object import MapObject
from rwmap.view.map_scene import ObjectSelectionBoxItem
from rwmap.view.object_layer_item import (
    HANDLE_NAMES, _ARROW_DIRS, handle_hit_arrow, handle_positions,
)


def _is_yellow(c):
    return c.red() > 200 and 140 < c.green() < 230 and c.blue() < 120


class ObjectResizeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)

    def setUp(self):
        w = self.w
        w.state.undo_stack.clear()
        w.scene.set_selected_objects([])
        self.a = MapObject(object_id=1, name="矩形", shape="rect",
                           x=100, y=100, width=100, height=80)
        self.b = MapObject(object_id=2, shape="polygon", x=250, y=100,
                           points=[(0, 0), (60, 0), (60, 40)])
        self.c = MapObject(object_id=3, shape="point", x=400, y=150)
        self.layer = ObjectLayer(name="Triggers",
                                 objects=[self.a, self.b, self.c])
        doc = MapDocument(
            width=40, height=30, tile_w=20, tile_h=20,
            layers=[TileLayer(width=40, height=30, name="Ground",
                              cells=[0] * 1200), self.layer],
            tilesets=[])
        w.state.doc = doc
        w.state.profile.write_enabled = True
        w.scene.set_document(doc)
        w._set_object_tool("select")
        w.view.resetTransform()          # 固定 1:1，箭头命中坐标才好算

    def obj(self, oid):
        return next(o for o in self.layer.objects if o.object_id == oid)

    def box(self):
        return self.w.scene._selection_combined_bounds()

    def _render(self, rect, w, h):
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        self.w.scene.render(p, QRectF(0, 0, w, h), rect)
        p.end()
        return img

    def _yellow_near(self, img, cx, cy, half=5):
        return sum(1 for dy in range(-half, half + 1)
                   for dx in range(-half, half + 1)
                   if _is_yellow(img.pixelColor(int(cx + dx), int(cy + dy))))

    # ---------- 绘制 ----------
    def test_single_left_click_shows_eight_yellow_arrows(self):
        """用户报告的场景：选择对象工具左键点一下矩形，8 个箭头要出现。"""
        w = self.w
        cx, cy = self.a.x + 50, self.a.y + 40
        w._on_object_tool_press(cx, cy)
        w._on_object_tool_release(cx, cy)
        self.assertEqual(len(w.scene.selected_objects), 1)
        item = w.scene._obj_sel_box
        self.assertIsInstance(item, ObjectSelectionBoxItem)
        self.assertTrue(item.isVisible())
        self.assertEqual(item.rect(), QRectF(100, 100, 100, 80))

        img = self._render(QRectF(50, 50, 400, 300), 400, 300)
        scale = abs(w.view.transform().m11()) or 1.0
        side = 13.5 / scale
        half = side * 0.78
        for name, point in handle_positions(item.rect(), scale).items():
            ux, uy = _ARROW_DIRS[name]
            cx, cy = point.x() + ux * half - 50, point.y() + uy * half - 50
            self.assertGreaterEqual(
                self._yellow_near(img, cx, cy), 5,
                f"{name} 位置没有黄色双向箭头")

    def test_arrow_bounding_rect_covers_outside_arrows(self):
        """箭头画在框外，boundingRect 必须外扩，否则 MinimalViewport 下被裁掉。"""
        self.w.scene.set_selected_objects([self.a])
        item = self.w.scene._obj_sel_box
        br = item.boundingRect()
        r = item.rect()
        self.assertLess(br.left(), r.left())
        self.assertLess(br.top(), r.top())
        self.assertGreater(br.right(), r.right())
        self.assertGreater(br.bottom(), r.bottom())

    def test_zoom_updates_arrow_scale(self):
        self.w.scene.set_selected_objects([self.a])
        item = self.w.scene._obj_sel_box
        before = item.boundingRect()
        self.w._on_zoom(0.5)
        self.assertNotEqual(item.boundingRect(), before)
        self.assertAlmostEqual(item._scale, 0.5)

    # ---------- 命中 ----------
    def test_handle_hit_all_eight_directions(self):
        r = QRectF(100, 100, 100, 80)
        side, half = 13.5, 13.5 * 0.78
        for name, point in handle_positions(r).items():
            ux, uy = _ARROW_DIRS[name]
            self.assertEqual(
                handle_hit_arrow(r, point.x() + ux * half,
                                 point.y() + uy * half), name)
        self.assertEqual(set(HANDLE_NAMES), set(handle_positions(r)))

    def test_hit_never_enters_object_interior_at_small_zoom(self):
        """视图缩到 23% 时箭头在场景里被放大，命中区不能吃掉对象内部。"""
        r = QRectF(304, 165, 203, 122)
        scale = 0.23
        self.assertIsNone(handle_hit_arrow(r, 314, 175, scale))
        self.assertIsNone(handle_hit_arrow(r, 400, 220, scale))
        # 框外的箭头仍然命中
        self.assertEqual(handle_hit_arrow(r, r.right() + 30, r.bottom() + 30,
                                          scale), "se")

    def test_small_zoom_click_inside_still_moves(self):
        w = self.w
        w.view.scale(0.23, 0.23)
        w.scene.set_selected_objects([self.obj(1)])
        x, y = self.a.x + 10, self.a.y + 10
        self.assertIsNone(w._selection_handle_at(x, y))
        w.view.objectPressed.emit(x, y)
        self.assertIsNotNone(w._sel_move_drag)
        self.assertIsNone(w._sel_resize_drag)
        w.view.objectMoved.emit(x + 20, y + 20)
        w.view.objectReleased.emit(x + 20, y + 20)

    def test_hover_sets_resize_cursor(self):
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        w._on_object_hover(box.right() + 8, box.bottom() + 8)
        self.assertEqual(w.view.cursor().shape(), Qt.SizeFDiagCursor)
        w._on_object_hover(box.center().x(), box.center().y())
        self.assertNotEqual(w.view.cursor().shape(), Qt.SizeFDiagCursor)

    # ---------- 拖拽缩放 ----------
    def _drag(self, handle_pt, end_pt, modifiers=None):
        w = self.w
        ctx = (mock.patch.object(QApplication, "keyboardModifiers",
                                return_value=modifiers)
               if modifiers is not None else None)
        if ctx:
            ctx.start()
        try:
            w._on_object_tool_press(*handle_pt)
            w._on_object_tool_move(*end_pt)
            w._on_object_tool_release(*end_pt)
        finally:
            if ctx:
                ctx.stop()

    def test_drag_se_changes_size_and_is_one_undo_step(self):
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.bottom() + 8
        self._drag((sx, sy), (sx + 30, sy + 20))
        o = self.obj(1)
        self.assertEqual((o.x, o.y), (100, 100))
        self.assertEqual((o.width, o.height), (130, 100))
        self.assertEqual(w.scene._obj_sel_box.rect(), QRectF(100, 100, 130, 100))
        self.assertEqual(w.state.undo_stack.count(), 1)
        self.assertEqual(w.state.undo_stack.undoText(), "缩放对象")
        w.act_undo.trigger()
        o = self.obj(1)
        self.assertEqual((o.x, o.y, o.width, o.height), (100, 100, 100, 80))
        w.act_redo.trigger()
        o = self.obj(1)
        self.assertEqual((o.width, o.height), (130, 100))

    def test_drag_nw_moves_topleft_and_keeps_bottomright(self):
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.left() - 8, box.top() - 8
        self._drag((sx, sy), (sx + 20, sy + 10))
        o = self.obj(1)
        self.assertEqual((o.x, o.y), (120, 110))
        self.assertEqual((o.width, o.height), (80, 70))
        self.assertEqual((o.x + o.width, o.y + o.height), (200, 180))

    def test_drag_e_only_changes_width(self):
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.center().y()
        self._drag((sx, sy), (sx + 25, sy + 40))
        o = self.obj(1)
        self.assertEqual((o.x, o.y, o.height), (100, 100, 80))
        self.assertEqual(o.width, 125)

    def test_no_movement_produces_no_undo_record(self):
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.bottom() + 8
        self._drag((sx, sy), (sx, sy))
        self.assertEqual(w.state.undo_stack.count(), 0)

    def test_resize_never_flips_below_minimum(self):
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.bottom() + 8
        self._drag((sx, sy), (sx - 500, sy - 500))
        o = self.obj(1)
        self.assertGreaterEqual(o.width, 8)
        self.assertGreaterEqual(o.height, 8)
        self.assertEqual((o.x, o.y), (100, 100))

    def test_shift_snaps_delta_to_tile_grid(self):
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.bottom() + 8
        self._drag((sx, sy), (sx + 33, sy + 27), Qt.ShiftModifier)
        o = self.obj(1)
        self.assertEqual(o.width % 20, 0)
        self.assertEqual(o.height % 20, 0)

    def test_group_resize_scales_polygon_and_moves_point(self):
        w = self.w
        w.scene.set_selected_objects(list(self.layer.objects))
        box = self.box()
        sx, sy = box.right() + 8, box.bottom() + 8
        self._drag((sx, sy), (sx + 60, sy + 40))
        a, b, c = self.obj(1), self.obj(2), self.obj(3)
        fx = (box.width() + 60) / box.width()
        fy = (box.height() + 40) / box.height()
        self.assertAlmostEqual(a.width, 100 * fx, places=4)
        self.assertAlmostEqual(a.height, 80 * fy, places=4)
        self.assertAlmostEqual(b.points[1][0], 60 * fx, places=4)
        self.assertAlmostEqual(b.points[2][1], 40 * fy, places=4)
        self.assertGreater(c.x, 400)          # 点对象按比例挪位
        self.assertEqual(w.state.undo_stack.count(), 1)

    def test_esc_cancels_resize_without_undo(self):
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.bottom() + 8
        w._on_object_tool_press(sx, sy)
        w._on_object_tool_move(sx + 40, sy + 40)
        self.assertGreater(self.obj(1).width, 100)
        w._cancel_object_tool()
        o = self.obj(1)
        self.assertEqual((o.x, o.y, o.width, o.height), (100, 100, 100, 80))
        self.assertEqual(w.state.undo_stack.count(), 0)
        self.assertIsNone(w._sel_resize_drag)


if __name__ == "__main__":
    unittest.main()
