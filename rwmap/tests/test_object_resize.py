"""第三十三轮回归：选中对象的 8 个黄色缩放箭头 + 拖拽缩放。

背景：第十几轮改成「PPT 风格整体包围框」时把缩放箭头和拖拽缩放一起删了，
但功能表仍写着「8 手柄自由缩放」。用户用「选择对象」（鼠标箭头图标）左键
点矩形后，看不到放大缩小的箭头。本轮把它恢复：

- 箭头画在整体选中框的四角 / 四边（``ObjectSelectionBoxItem``，屏幕固定像素）；
- 拖拽按方向改 x/y/width/height，多边形按比例缩放 points，点对象只挪中心；
- 多选时整组按整体框等比缩放；
- 一次拖动 = 一条撤销；Esc 取消恢复且不留记录；Ctrl 把被拖的边吸附到瓦片网格；
- 视图缩小时命中区不得侵入对象内部（否则点内部会变成缩放、拖不动）。
"""
from __future__ import annotations

import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
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


def w_pos(w, sx, sy):
    """场景坐标 → 视图局部坐标（QMouseEvent 用局部坐标）。"""
    return w.view.mapFromScene(QRectF(sx, sy, 0, 0).topLeft())


class ObjectResizeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)

    def setUp(self):
        w = self.w
        w.state.undo_stack.clear()
        # 彻底清掉上一测试残留的工具 / pending / 拖动标志，避免跨测试污染。
        w._object_pending = None
        w._obj_tool_drag = False
        w._object_drag = False
        w._sel_resize_drag = None
        w._sel_move_drag = None
        w.scene.set_object_preview(None)
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

    def test_point_selection_shows_no_resize_arrows(self):
        """点对象没有尺寸、不能缩放：选中时不画 8 个黄色箭头。"""
        w = self.w
        w.scene.set_selected_objects([self.c])       # 纯点选区
        item = w.scene._obj_sel_box
        self.assertIsInstance(item, ObjectSelectionBoxItem)
        self.assertFalse(item._show_handles)
        # 选中框本身仍在（12x12），只是没有箭头
        self.assertAlmostEqual(item.rect().width(), 12, places=3)
        img = self._render(QRectF(self.c.x - 40, self.c.y - 40, 80, 80), 80, 80)
        # 点对象自身是黄色十字，但四角箭头位置不应有箭头；用整体框四角判断
        box = item.rect()
        side = 13.5 / (abs(w.view.transform().m11()) or 1.0)
        half = side * 0.78
        self.assertLess(
            self._yellow_near(img, box.right() + half - (self.c.x - 40),
                              box.bottom() + half - (self.c.y - 40)), 5,
            "点对象选中时不应画缩放箭头")

    def test_point_selection_has_no_handle_and_cannot_resize(self):
        w = self.w
        w.scene.set_selected_objects([self.c])
        box = self.box()
        # 箭头位置（右下角框外）不再命中任何 handle
        self.assertIsNone(w._selection_handle_at(box.right() + 8, box.bottom() + 8))
        # 即便直接调用缩放入口也应安全跳过（纯点选区无 handle）
        self.assertIsNone(w._selection_handle_at(self.c.x + 6, self.c.y + 6))

    def test_mixed_selection_keeps_arrows(self):
        """混选里只要有非点对象，整组仍可缩放、箭头照常显示。"""
        w = self.w
        w.scene.set_selected_objects([self.a, self.c])
        self.assertTrue(w.scene._obj_sel_box._show_handles)

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

    def test_ctrl_snaps_dragged_edge_to_tile_grid(self):
        """Ctrl + 拖箭头：被拖的角/边吸附到最近的瓦片线，不是位移取整。"""
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.bottom() + 8
        self._drag((sx, sy), (sx + 33, sy + 27), Qt.ControlModifier)
        o = self.obj(1)
        # 右 200+33=233 → 240；下 180+27=207 → 200；左上不动
        self.assertEqual((o.x, o.y), (100, 100))
        self.assertEqual((o.x + o.width, o.y + o.height), (240, 200))
        self.assertEqual((o.width, o.height), (140, 100))

    def test_ctrl_resize_snaps_unaligned_edges_to_nearest_tile_line(self):
        """用户场景：矩形四边都不在瓦片边上，Ctrl 拖箭头后要吸附。

        矩形 103,107 75x65 → 右边 178、下边 172，四条边都不是 20 的倍数。
        轻微拖动 se 箭头（自由位移仅 +4,+3，走不满一格），Ctrl 应把被拖的
        右/下边分别吸到最近的瓦片线 180/180；未被拖的左/上边保持原值。
        """
        w = self.w
        self.a.x, self.a.y, self.a.width, self.a.height = 103, 107, 75, 65
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        self.assertEqual((box.left(), box.top(), box.right(), box.bottom()),
                         (103, 107, 178, 172))
        sx, sy = box.right() + 8, box.bottom() + 8
        self._drag((sx, sy), (sx + 4, sy + 3), Qt.ControlModifier)
        o = self.obj(1)
        self.assertEqual((o.x, o.y), (103, 107))       # 未拖的两边不动
        self.assertEqual(o.x + o.width, 180)           # 178+4=182 → 180
        self.assertEqual(o.y + o.height, 180)          # 172+3=175 → 180
        self.assertEqual((o.width, o.height), (77, 73))

    def test_ctrl_resize_side_handle_snaps_only_that_side(self):
        """Ctrl 拖单边箭头：只吸附被拖的那条边，其余三边保持非对齐坐标。"""
        w = self.w
        self.a.x, self.a.y, self.a.width, self.a.height = 103, 107, 75, 65
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.center().y()
        self._drag((sx, sy), (sx + 8, sy + 20), Qt.ControlModifier)
        o = self.obj(1)
        self.assertEqual((o.x, o.y, o.height), (103, 107, 65))
        self.assertEqual(o.x + o.width, 180)           # 178+8=186 → 180

    def test_shift_resize_keeps_aspect_ratio(self):
        """Shift + 拖箭头：等比例缩放，宽高比与原对象一致。"""
        w = self.w
        w.scene.set_selected_objects([self.obj(1)])
        box = self.box()
        sx, sy = box.right() + 8, box.bottom() + 8
        self._drag((sx, sy), (sx + 40, sy + 5), Qt.ShiftModifier)
        o = self.obj(1)
        # 原矩形 100x80，se 手柄水平位移更大 → 以宽度为准，f=1.4
        self.assertAlmostEqual(o.width, 140, places=4)
        self.assertAlmostEqual(o.height, 112, places=4)
        self.assertAlmostEqual(o.width / o.height, 100 / 80, places=5)
        # se 手柄：左上角固定不动
        self.assertEqual((o.x, o.y), (100, 100))

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

    # ---------- 插入对象时的修饰键 ----------
    def _create_rect(self, x0, y0, x1, y1, modifiers=None):
        w = self.w
        w._set_object_tool("rect")
        ctx = (mock.patch.object(QApplication, "keyboardModifiers",
                                return_value=modifiers)
               if modifiers is not None else None)
        if ctx:
            ctx.start()
        try:
            w._on_object_tool_press(float(x0), float(y0))
            w._on_object_tool_move(float(x1), float(y1))
            w._on_object_tool_release(float(x1), float(y1))
        finally:
            if ctx:
                ctx.stop()
            w._set_object_tool("select")
        return w._target_object_layer().objects[-1]

    def test_shift_creates_square_rect(self):
        """Shift + 拖创建矩形：强制正方形（原 Shift 吸附语义已迁移到 Ctrl）。"""
        obj = self._create_rect(100, 100, 180, 140, Qt.ShiftModifier)
        self.assertEqual(obj.shape, "rect")
        self.assertEqual((obj.x, obj.y), (100.0, 100.0))
        self.assertEqual(obj.width, obj.height)
        self.assertEqual(obj.width, 80)          # 取较大边长

    def test_ctrl_creates_grid_snapped_rect(self):
        """Ctrl + 拖创建矩形：四角吸附到瓦片网格角点（原 Shift 行为）。"""
        obj = self._create_rect(103, 107, 175, 146, Qt.ControlModifier)
        self.assertEqual(obj.shape, "rect")
        for v in (obj.x, obj.y, obj.width, obj.height):
            self.assertEqual(v % 20, 0, f"{v} 未吸附到瓦片网格")

    def test_plain_drag_is_free_and_not_square(self):
        """无修饰键：自由摆放、不约束正方形。"""
        obj = self._create_rect(103, 107, 175, 140)
        self.assertEqual((obj.x, obj.y), (103.0, 107.0))
        self.assertNotEqual(obj.width, obj.height)

    # ---------- Ctrl 修饰键在 map_view 的分发 ----------
    def _ctrl_click_counts(self, tool):
        """发一个 Ctrl+左键，返回 (objectPressed 次数, objectToolPressed 次数)。"""
        w = self.w
        w._set_object_tool(tool)
        w.view.set_document(w.state.doc)
        counts = {"old": 0, "new": 0}

        def on_old(*_a):
            counts["old"] += 1

        def on_new(*_a):
            counts["new"] += 1

        w.view.objectPressed.connect(on_old)
        w.view.objectToolPressed.connect(on_new)
        try:
            ev = QMouseEvent(QEvent.Type.MouseButtonPress, w_pos(w, 120, 130),
                             Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier)
            w.view.mousePressEvent(ev)
        finally:
            w.view.objectPressed.disconnect(on_old)
            w.view.objectToolPressed.disconnect(on_new)
            w.view._obj_tool_drag = False
            w.view._object_drag = False
            w._cancel_object_tool()          # 清掉 press 可能留下的 pending
            w._set_object_tool("select")     # 恢复工具，避免污染后续测试
        return counts["old"], counts["new"]

    def test_ctrl_left_click_in_object_tool_is_not_swallowed(self):
        """rect 工具下 Ctrl+左键必须走 objectToolPressed，不能被旧的对象
        Ctrl+拖动分支吞掉，否则 Ctrl+创建矩形吸附网格会失效。"""
        old, new = self._ctrl_click_counts("rect")
        self.assertEqual(old, 0, "Ctrl 被旧对象拖动分支拦截了")
        self.assertEqual(new, 1, "Ctrl+左键未透传给对象创建")

    def test_ctrl_left_click_in_brush_still_selects_object(self):
        """画笔模式下 Ctrl+左键保留旧的「临时点选对象」快捷入口。"""
        old, new = self._ctrl_click_counts("brush")
        self.assertEqual(old, 1)
        self.assertEqual(new, 0)

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
