"""钢笔 / 贝塞尔矢量曲线：交互、插入锚点、沿线盖章、撤销、清理（离屏）。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent


def _build_window_and_doc():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    from rwmap.document.layer import TileLayer
    from rwmap.document.map_document import MapDocument

    w = MainWindow(auto_open=False)
    layer = TileLayer(width=10, height=10, name="Ground",
                      cells=[0] * 100, readonly=False)
    doc = MapDocument(width=10, height=10, tile_w=20, tile_h=20,
                      layers=[layer], tilesets=[])
    w.state.doc = doc
    w.state.current_layer = layer
    w.scene.doc = doc
    w.view._doc = doc
    w.view.resetTransform()          # 单位变换 → 命中半径 / 阈值可预测
    w.state.profile.write_enabled = True
    w.state.brush_gid = 7
    w.state.stamp = None
    w._hover_cell = (-1, -1)
    return w, layer, doc


class PenToolIntegrationTest(unittest.TestCase):

    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()
        self.w._set_object_tool("pen")
        self.w.state.brush_gid = 7
        self.w.state.stamp = None

    # ---- 信号级模拟 ----
    def _right_click(self, x, y):
        self.w.view.objectToolRightPressed.emit(float(x), float(y))
        self.w.view.objectToolRightReleased.emit(float(x), float(y))

    def _right_drag(self, x0, y0, x1, y1):
        self.w.view.objectToolRightPressed.emit(float(x0), float(y0))
        self.w.view.objectToolRightMoved.emit(float(x1), float(y1))
        self.w.view.objectToolRightReleased.emit(float(x1), float(y1))

    def _left(self, x0, y0, x1=None, y1=None):
        self.w.view.objectToolPressed.emit(float(x0), float(y0))
        if x1 is not None:
            self.w.view.objectToolMoved.emit(float(x1), float(y1))
            self.w.view.objectToolReleased.emit(float(x1), float(y1))
        else:
            self.w.view.objectToolReleased.emit(float(x0), float(y0))

    def _straight_line(self):
        self._right_click(30, 40)
        self._right_click(140, 40)

    # ---- 工具注册 ----
    def test_tool_registered_right_of_brush(self):
        from rwmap.widgets.object_toolbar import TOOLS
        self.assertEqual(TOOLS[0][0], "brush")
        self.assertEqual(TOOLS[1][0], "pen")
        self.assertEqual(TOOLS[1][2], "A")
        self.assertEqual(self.w.view.object_mode, "pen")
        self.assertEqual(self.w._object_tool, "pen")
        self.assertNotEqual(self.w.state.tool_id, "pen")   # 不走瓦片 desired_gid 路径

    # ---- 右键放点 ----
    def test_right_click_adds_corner_anchor(self):
        self._right_click(30, 40)
        path = self.w._pen_path
        self.assertEqual(len(path.anchors), 1)
        self.assertEqual(path.anchors[0].pos(), (30.0, 40.0))
        self.assertEqual(path.anchors[0].hin, (0.0, 0.0))
        self.assertEqual(path.anchors[0].hout, (0.0, 0.0))

    def test_right_drag_adds_symmetric_handles(self):
        self._right_click(30, 40)
        self._right_drag(140, 40, 200, 70)
        a = self.w._pen_path.anchors[1]
        self.assertEqual(a.pos(), (140.0, 40.0))
        self.assertEqual(a.hout, (60.0, 30.0))
        self.assertEqual(a.hin, (-60.0, -30.0))

    def test_right_drag_below_threshold_is_corner(self):
        self._right_click(30, 40)
        self._right_drag(140, 40, 142, 41)
        a = self.w._pen_path.anchors[1]
        self.assertEqual(a.hin, (0.0, 0.0))
        self.assertEqual(a.hout, (0.0, 0.0))

    # ---- 左键调节 ----
    def test_left_drag_anchor_moves_without_changing_handles(self):
        self._right_click(30, 40)
        self._right_drag(140, 40, 180, 60)
        first = self.w._pen_path.anchors[0]
        first.set_symmetric(10.0, -15.0)
        self.w._pen_refresh()
        self._left(30, 40, 55, 75)
        self.assertEqual(first.pos(), (55.0, 75.0))
        self.assertEqual(first.hout, (10.0, -15.0))
        self.assertEqual(first.hin, (-10.0, 15.0))

    def test_left_drag_handle_keeps_symmetry(self):
        self._right_click(30, 40)
        self._right_drag(140, 40, 180, 60)
        a = self.w._pen_path.anchors[1]
        hx, hy = a.out_pt()
        self._left(hx, hy, 150, 100)
        self.assertEqual(a.hout, (10.0, 60.0))
        self.assertEqual(a.hin, (-10.0, -60.0))

    # ---- 曲线插点 ----
    def test_left_click_on_curve_inserts_symmetric_anchor(self):
        self._straight_line()
        self._left(85, 40)
        path = self.w._pen_path
        self.assertEqual(len(path.anchors), 3)
        new = path.anchors[1]
        self.assertAlmostEqual(new.x, 85.0, delta=1.0)
        self.assertAlmostEqual(new.y, 40.0, delta=1.0)
        self.assertEqual(new.hin, (-new.hout[0], -new.hout[1]))
        self.assertEqual(self.w.state.undo_stack.count(), 0)   # 插点不落笔
        self.assertIsNotNone(self.w._pen_path)                 # 插点不算完成

    # ---- 完成 / 撤销 ----
    def test_left_click_blank_finishes_and_single_undo(self):
        self._straight_line()
        before = self.w.state.undo_stack.count()
        self._left(10, 160)                       # 线之外的空白区
        self.assertEqual(self.layer.get(3, 2), 7)   # y=40 → row 2
        self.assertEqual(self.layer.get(1, 2), 7)
        self.assertEqual(self.layer.get(7, 2), 7)
        self.assertEqual(self.layer.get(3, 8), 0)   # 别的行不受影响
        self.assertEqual(self.w.state.undo_stack.count(), before + 1)
        self.assertIsNone(self.w._pen_path, "完成后应清空路径")
        self.assertEqual(self.w._object_tool, "pen", "完成后应保留钢笔工具")
        self.w.state.undo_stack.undo()
        self.assertEqual(self.layer.get(3, 2), 0)
        self.w.state.undo_stack.redo()
        self.assertEqual(self.layer.get(3, 2), 7)

    def test_less_than_two_anchors_cannot_finish(self):
        self._right_click(30, 40)
        before = self.w.state.undo_stack.count()
        self._left(10, 160)
        self.assertEqual(self.w.state.undo_stack.count(), before)
        self.assertIsNotNone(self.w._pen_path)

    def test_no_object_layer_still_works(self):
        from rwmap.document.layer import ObjectLayer
        self.assertFalse(any(isinstance(l, ObjectLayer) for l in self.doc.layers))
        self._straight_line()
        self._left(10, 160)
        self.assertEqual(self.layer.get(3, 2), 7)

    def test_stamp_center_align_and_zero_skip(self):
        self._straight_line()
        self.w.state.stamp = [[1, 0, 2], [3, 4, 5]]
        final = self.w._pen_stamp_cells()
        # 图章里的 0 不应写入（也不清空别的采样点写入的值）
        self.assertNotIn(0, final.values())
        # 第一个采样点是 (1,2)；w=3,h=2 → 中心对齐原点是 (0,1)
        self.assertIn((0, 1), final)
        self.assertEqual(final[(0, 1)], 1)
        self.assertEqual(final[(0, 2)], 3)
        self.assertEqual(final[(1, 2)], 4)
        self.assertEqual(final[(2, 2)], 5)
        # 落笔仍然只有一条撤销命令
        before = self.w.state.undo_stack.count()
        self._left(10, 160)
        self.assertEqual(self.w.state.undo_stack.count(), before + 1)

    # ---- 取消 / 切换 / 键位 ----
    def test_esc_cancels_without_writing(self):
        self._straight_line()
        before = self.w.state.undo_stack.count()
        self.w.view.objectToolCancelled.emit()
        self.assertIsNone(self.w._pen_path)
        self.assertIsNone(self.w.scene._pen_preview._path
                          if self.w.scene._pen_preview else None)
        self.assertEqual(self.w.state.undo_stack.count(), before)
        self.assertEqual(self.layer.get(3, 2), 0)

    def test_switch_tool_clears_pen_state(self):
        self._straight_line()
        self.w._set_object_tool("rect")
        self.assertIsNone(self.w._pen_path)
        self.assertEqual(self.w.view.object_mode, "rect")

    def test_delete_removes_last_anchor(self):
        self._straight_line()
        self.w.act_delete_object.trigger()
        self.assertEqual(len(self.w._pen_path.anchors), 1)
        self.w.act_delete_object.trigger()
        self.assertIsNone(self.w._pen_path)

    def test_ctrl_return_finishes(self):
        self._straight_line()
        self.w.act_finish_poly.trigger()
        self.assertEqual(self.layer.get(3, 2), 7)
        self.assertIsNone(self.w._pen_path)


class PenViewRoutingTest(unittest.TestCase):
    """真实鼠标事件层：右键 release 不被 _obj_tool_drag 吞掉（最高危回归）。"""

    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()
        self.w.view.set_document(self.doc)
        self.w.view.resetTransform()
        self.w._set_object_tool("pen")
        self.received = []

    def _pixel(self, x, y):
        return self.w.view.mapFromScene(QPointF(x, y))

    def _press(self, x, y):
        self.w.view.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, self._pixel(x, y),
            Qt.RightButton, Qt.RightButton, Qt.NoModifier))

    def _move(self, x, y):
        self.w.view.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, self._pixel(x, y),
            Qt.NoButton, Qt.RightButton, Qt.NoModifier))

    def _release(self, x, y):
        self.w.view.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, self._pixel(x, y),
            Qt.RightButton, Qt.NoButton, Qt.NoModifier))

    def test_right_drag_routes_move_and_sets_handle(self):
        self.w.view.objectToolRightMoved.connect(
            lambda x, y: self.received.append(("move", x, y)))
        self.w.view.objectToolRightReleased.connect(
            lambda x, y: self.received.append(("release", x, y)))
        self._press(45, 45)
        self._move(100, 80)
        self._release(100, 80)
        self.assertIn("move", [e[0] for e in self.received])
        self.assertIn("release", [e[0] for e in self.received])
        path = self.w._pen_path
        self.assertEqual(len(path.anchors), 1)
        # 右键按下点 = 锚点，松开 - 按下 = 对称控制柄
        self.assertAlmostEqual(path.anchors[0].hout[0], 55.0, delta=1.0)
        self.assertAlmostEqual(path.anchors[0].hout[1], 35.0, delta=1.0)
        self.assertEqual(path.anchors[0].hin,
                         (-path.anchors[0].hout[0], -path.anchors[0].hout[1]))

    def test_right_press_release_reaches_pen(self):
        self.w.view.objectToolRightPressed.connect(
            lambda x, y: self.received.append(("press", x, y)))
        self.w.view.objectToolRightReleased.connect(
            lambda x, y: self.received.append(("release", x, y)))
        self._press(45, 45)
        self._release(45, 45)
        self.assertEqual([e[0] for e in self.received], ["press", "release"])
        self.assertIsNotNone(self.w._pen_path)
        self.assertEqual(len(self.w._pen_path.anchors), 1)

    def test_brush_mode_right_click_does_not_emit_pen_signals(self):
        self.w._set_object_tool("brush")
        self.w.view.objectToolRightPressed.connect(
            lambda x, y: self.received.append("pen"))
        self._press(45, 45)
        self._release(45, 45)
        self.assertEqual(self.received, [])      # 仍走原来的 _rcap 分支


if __name__ == "__main__":
    unittest.main()
