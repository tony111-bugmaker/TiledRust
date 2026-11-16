"""框选 / 复制 / 粘贴 / 移动 在主窗口中的端到端闭环（离屏）。

验证 ``MainWindow`` 上真实的信号路由（objectToolPressed/Moved/Released）、
选区状态、剪贴板、以及 ``DrawCommand`` 撤销，确保框选逻辑与
``rwmap/rw/selection.py`` 的纯算法在真实窗口里也一致。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402


def _build_window_and_doc():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    w = MainWindow(auto_open=False)
    # 10x10 单瓦片层，左上角预置可识别的 3x2 图案。
    grid = [[0] * 10 for _ in range(10)]
    pattern = [[1, 2, 3], [4, 5, 6]]
    for r in range(2):
        for c in range(3):
            grid[r][c] = pattern[r][c]
    cells = [grid[r][c] for r in range(10) for c in range(10)]
    layer = TileLayer(width=10, height=10, name="Ground",
                      cells=cells, readonly=False)
    doc = MapDocument(width=10, height=10, tile_w=20, tile_h=20,
                      layers=[layer], tilesets=[])
    w.state.doc = doc
    w.state.current_layer = layer
    w.scene.doc = doc
    w.state.profile.write_enabled = True
    # 清掉上一轮可能残留的选区 / 剪贴板状态。
    w._tile_selection = None
    w._tile_clipboard = None
    w._sel_rect = None
    w._sel_dragging = False
    w._sel_moving = False
    w._hover_cell = (-1, -1)
    return w, layer, doc


class TileSelectIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()

    def _select_top_left(self):
        w = self.w
        w._set_object_tool("tileselect")
        # 从 (0,0) 拖到 (50,30) → 半开区间映射到 (0,0,3,2) 格 = 60x40 像素。
        w.view.objectToolPressed.emit(0.0, 0.0)
        w.view.objectToolMoved.emit(50.0, 30.0)
        w.view.objectToolReleased.emit(50.0, 30.0)

    def test_box_select_computes_cells_and_rect(self):
        self._select_top_left()
        self.assertEqual(self.w._tile_selection, (0, 0, 3, 2))
        # 显示框吸附到格线，与实际选区 3x2 完全一致（不是鼠标拖出的 50x30）。
        self.assertEqual(self.w._sel_rect, (0, 0, 60, 40))
        self.assertIsNotNone(self.w.scene._selection_item)

    def test_copy_paste_at_hover(self):
        w = self.w
        self._select_top_left()
        w._copy_selection()
        self.assertEqual(len(w._tile_clipboard), 2)
        self.assertEqual([row[:3] for row in w._tile_clipboard],
                         [[1, 2, 3], [4, 5, 6]])
        # 鼠标悬停在 (5,5) 作为粘贴锚点。
        w._hover_cell = (5, 5)
        before = w.state.undo_stack.count()
        w._paste_selection()
        self.assertEqual(w.state.undo_stack.count(), before + 1)
        self.assertEqual(self.layer.get(5, 5), 1)
        self.assertEqual(self.layer.get(6, 5), 2)
        self.assertEqual(self.layer.get(7, 5), 3)
        self.assertEqual(self.layer.get(5, 6), 4)
        self.assertEqual(self.layer.get(7, 6), 6)
        # 选区也跟着落到新位置。
        self.assertEqual(w._tile_selection, (5, 5, 3, 2))
        # 撤销 / 重做。
        w.state.undo_stack.undo()
        self.assertEqual(self.layer.get(5, 5), 0)
        w.state.undo_stack.redo()
        self.assertEqual(self.layer.get(5, 5), 1)

    def test_move_selection_changes_cells_and_undo(self):
        w = self.w
        self._select_top_left()
        # 在选区内按下并向右下拖动 2 格 / 1 行（40px / 20px）。
        w.view.objectToolPressed.emit(10.0, 10.0)   # 命中选区内部
        w.view.objectToolMoved.emit(50.0, 30.0)
        w.view.objectToolReleased.emit(50.0, 30.0)
        # 原位置清空、目标位置写入。
        self.assertEqual(self.layer.get(0, 0), 0)
        self.assertEqual(self.layer.get(2, 1), 1)
        self.assertEqual(self.layer.get(3, 1), 2)
        self.assertEqual(self.layer.get(4, 1), 3)
        self.assertEqual(self.layer.get(2, 2), 4)
        self.assertEqual(self.layer.get(4, 2), 6)
        # 选区自身跟着平移。
        self.assertEqual(w._tile_selection, (2, 1, 3, 2))
        # 撤销，全部回到移动前的图案。
        w.state.undo_stack.undo()
        self.assertEqual(self.layer.get(0, 0), 1)
        self.assertEqual(self.layer.get(2, 1), 6)   # 重叠格原值
        self.assertEqual(self.layer.get(3, 1), 0)

    def test_many_move_events_do_not_accumulate_offset(self):
        """真实拖动会发几十个 mouseMove：位移不能被逐帧累加。

        回归用户录屏里的 bug：鼠标只挪 1 格（20px），选框却飞出屏幕。
        """
        w = self.w
        self._select_top_left()
        w.view.objectToolPressed.emit(10.0, 10.0)   # 命中选区内部
        for i in range(1, 31):
            w.view.objectToolMoved.emit(10.0 + 20.0 * i / 30, 10.0)
            # 每次预览都只能落在 0 或 1 格，绝不会越走越远。
            self.assertIn(w._sel_rect[0], (0.0, 20.0),
                          f"第 {i} 次 move 后选框累加了：{w._sel_rect}")
            self.assertEqual(w._sel_rect[2] - w._sel_rect[0], 60)
        w.view.objectToolReleased.emit(30.0, 10.0)
        self.assertEqual(w._tile_selection, (1, 0, 3, 2))
        self.assertEqual(w._sel_rect, (20, 0, 80, 40))
        # 数据同样只平移 1 格，且一条撤销即可还原。
        self.assertEqual(self.layer.get(0, 0), 0)
        self.assertEqual(self.layer.get(1, 0), 1)
        self.assertEqual(self.layer.get(3, 0), 3)
        self.assertEqual(self.layer.get(3, 1), 6)
        w.state.undo_stack.undo()
        self.assertEqual(self.layer.get(0, 0), 1)
        self.assertEqual(self.layer.get(3, 0), 0)

    def test_drag_back_and_forth_uses_total_offset_only(self):
        """中途来回抖动不影响最终结果，只认「按下点 → 松开点」。"""
        w = self.w
        self._select_top_left()
        w.view.objectToolPressed.emit(30.0, 10.0)
        for x in (90.0, 10.0, 130.0, 30.0, 70.0):
            w.view.objectToolMoved.emit(x, 10.0)
        w.view.objectToolReleased.emit(50.0, 10.0)   # 净位移 +20px = 1 格
        self.assertEqual(w._tile_selection, (1, 0, 3, 2))
        self.assertEqual(w._sel_rect, (20, 0, 80, 40))

    def test_click_inside_selection_does_not_shift_it(self):
        w = self.w
        self._select_top_left()
        w.view.objectToolPressed.emit(30.0, 25.0)
        w.view.objectToolMoved.emit(34.0, 27.0)     # 半格以内的抖动
        w.view.objectToolReleased.emit(34.0, 27.0)
        self.assertEqual(w._tile_selection, (0, 0, 3, 2))
        self.assertEqual(w._sel_rect, (0, 0, 60, 40))
        self.assertEqual(w.state.undo_stack.count(), 0)

    def test_drag_is_clamped_to_map_bounds(self):
        """拖出地图时选区停在边界内，内容不会被清空却写不回。"""
        w = self.w
        self._select_top_left()
        w.view.objectToolPressed.emit(10.0, 10.0)
        w.view.objectToolMoved.emit(1000.0, 1000.0)
        self.assertEqual(w._sel_rect, (140, 160, 200, 200))
        w.view.objectToolReleased.emit(1000.0, 1000.0)
        self.assertEqual(w._tile_selection, (7, 8, 3, 2))
        self.assertEqual(w._sel_rect, (140, 160, 200, 200))
        self.assertEqual(self.layer.get(7, 8), 1)
        self.assertEqual(self.layer.get(9, 9), 6)
        self.assertEqual(self.layer.get(0, 0), 0)

    def test_real_mouse_events_at_non_100_percent_zoom(self):
        """走完整 QMouseEvent → mapToScene 链路，缩放也不会放大位移。

        录屏里的编辑器是 126% 缩放：这里在 fit 之后再放大 1.26 倍，
        用真实视口坐标拖动 1 格（20 场景单位），验证选区只走 1 格。
        """
        w, doc = self.w, self.doc
        w._set_object_tool("tileselect")
        w.resize(1000, 700)
        w.show()
        QApplication.processEvents()
        w.view.set_document(doc)
        QApplication.processEvents()
        w.view.scale(1.26, 1.26)
        w.view.camera.scale = w.view.transform().m11()
        self.assertGreater(w.view.transform().m11(), 0)

        def px(sx, sy):
            return QPointF(w.view.mapFromScene(QPointF(float(sx), float(sy))))

        def press(sx, sy):
            w.view.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, px(sx, sy),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

        def move(sx, sy):
            w.view.mouseMoveEvent(QMouseEvent(
                QEvent.Type.MouseMove, px(sx, sy),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))

        def release(sx, sy):
            w.view.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, px(sx, sy),
                Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

        # 框选 (0,0)-(60,40) → 3x2 格。
        press(0.0, 0.0)
        move(60.0, 40.0)
        release(60.0, 40.0)
        self.assertEqual(w._tile_selection, (0, 0, 3, 2))

        # 选区内按下，20 个 mouseMove 只走 20 场景单位 = 1 格。
        press(30.0, 20.0)
        for i in range(1, 21):
            move(30.0 + i, 20.0)
        release(50.0, 20.0)
        self.assertEqual(w._tile_selection, (1, 0, 3, 2))
        self.assertEqual(w._sel_rect, (20, 0, 80, 40))
        self.assertEqual(self.layer.get(1, 0), 1)
        self.assertEqual(self.layer.get(3, 0), 3)
        self.assertEqual(self.layer.get(0, 0), 0)

    def test_undo_move_rewinds_selection_position(self):
        """撤销移动时选区（含画布虚线框）一起回到移动前的位置。"""
        w = self.w
        self._select_top_left()
        w.view.objectToolPressed.emit(10.0, 10.0)
        w.view.objectToolMoved.emit(50.0, 30.0)
        w.view.objectToolReleased.emit(50.0, 30.0)
        self.assertEqual(w._tile_selection, (2, 1, 3, 2))
        self.assertEqual(w._sel_rect, (40, 20, 100, 60))
        item = w.scene._selection_item
        self.assertEqual((item.rect().x(), item.rect().y()), (40.0, 20.0))

        w.state.undo_stack.undo()
        self.assertEqual(w._tile_selection, (0, 0, 3, 2))
        self.assertEqual(w._sel_rect, (0, 0, 60, 40))
        self.assertEqual((item.rect().x(), item.rect().y()), (0.0, 0.0))
        self.assertTrue(item.isVisible())
        self.assertEqual(self.layer.get(0, 0), 1)
        self.assertEqual(self.layer.get(2, 1), 6)

        w.state.undo_stack.redo()
        self.assertEqual(w._tile_selection, (2, 1, 3, 2))
        self.assertEqual(w._sel_rect, (40, 20, 100, 60))
        self.assertEqual((item.rect().x(), item.rect().y()), (40.0, 20.0))

        # 撤销到移动前之后，还能在原位置继续拖动。
        w.state.undo_stack.undo()
        w.view.objectToolPressed.emit(10.0, 10.0)
        w.view.objectToolReleased.emit(30.0, 10.0)
        self.assertEqual(w._tile_selection, (1, 0, 3, 2))
        self.assertEqual(w._sel_rect, (20, 0, 80, 40))

    def test_undo_paste_rewinds_selection_position(self):
        w = self.w
        self._select_top_left()
        w._copy_selection()
        w._hover_cell = (5, 5)
        before = w.state.undo_stack.count()
        w._paste_selection()
        self.assertEqual(w.state.undo_stack.count(), before + 1)
        self.assertEqual(w._tile_selection, (5, 5, 3, 2))
        self.assertEqual(w._sel_rect, (100, 100, 160, 140))
        w.state.undo_stack.undo()
        self.assertEqual(w._tile_selection, (0, 0, 3, 2))
        self.assertEqual(w._sel_rect, (0, 0, 60, 40))
        self.assertEqual(self.layer.get(5, 5), 0)
        w.state.undo_stack.redo()
        self.assertEqual(w._tile_selection, (5, 5, 3, 2))
        self.assertEqual(w._sel_rect, (100, 100, 160, 140))

    def test_multi_move_undo_redo_walks_selection_step_by_step(self):
        w = self.w
        self._select_top_left()
        for _ in range(3):
            col0, row0 = w._tile_selection[:2]
            x, y = col0 * 20 + 10.0, row0 * 20 + 10.0
            w.view.objectToolPressed.emit(x, y)
            w.view.objectToolReleased.emit(x + 20.0, y)
        self.assertEqual(w._tile_selection, (3, 0, 3, 2))

        for col in (2, 1, 0):
            w.state.undo_stack.undo()
            self.assertEqual(w._tile_selection, (col, 0, 3, 2))
            self.assertEqual(w._sel_rect, (col * 20, 0, (col + 3) * 20, 40))
        for col in (1, 2, 3):
            w.state.undo_stack.redo()
            self.assertEqual(w._tile_selection, (col, 0, 3, 2))
            self.assertEqual(w._sel_rect, (col * 20, 0, (col + 3) * 20, 40))

    def test_undo_plain_draw_does_not_touch_selection(self):
        """不带选区快照的普通绘制命令，撤销时不应改变选框位置。"""
        from rwmap.commands.draw_command import DrawCommand
        w = self.w
        self._select_top_left()
        w.view.objectToolPressed.emit(10.0, 10.0)
        w.view.objectToolReleased.emit(30.0, 10.0)
        self.assertEqual(w._tile_selection, (1, 0, 3, 2))
        idx = self.layer.index(8, 8)
        w.state.undo_stack.push(DrawCommand(
            w.state.doc, self.layer, [(idx, 0, 9)], w._refresh_layer,
            text="绘制"))
        w.state.undo_stack.undo()
        self.assertEqual(self.layer.get(8, 8), 0)
        self.assertEqual(w._tile_selection, (1, 0, 3, 2))
        self.assertEqual(w._sel_rect, (20, 0, 80, 40))

    def test_no_selection_copy_is_safe(self):
        self.w._tile_selection = None
        self.w._copy_selection()
        self.assertIsNone(self.w._tile_clipboard)

    def test_no_clipboard_paste_is_safe(self):
        self.w._tile_clipboard = None
        before = self.w.state.undo_stack.count()
        self.w._paste_selection()
        self.assertEqual(self.w.state.undo_stack.count(), before)


if __name__ == "__main__":
    unittest.main()
