"""第十七轮：钢笔（矢量线）接入「绘制时自动地图」开关（离屏）。

用可注入的假 AutoMapper 锁死主窗口语义，避免依赖官方 rules.txt / 示例地图：
开启时钢笔落笔 → 按整条线的改动范围做一次局部重算 → 与钢笔线合成一条撤销；
关闭时 / 无新格时不触发；Ctrl+Return 等所有完成入口都走同一条路径。
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeMapper:
    """只实现 _apply_automap 需要的 compute()，记录调用参数。"""

    def __init__(self, gid=999):
        self.gid = gid
        self.calls = []
        self.boom = False

    def compute(self, doc, area=None, only_diff=True):
        self.calls.append((area, only_diff))
        if self.boom:
            raise RuntimeError("规则计算模拟异常")
        if area is None:
            return {}
        c0, r0, _c1, _r1 = area
        return {"Ground": [(c0, r0, self.gid)]}


def _build():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    from rwmap.document.layer import TileLayer
    from rwmap.document.map_document import MapDocument

    w = MainWindow(auto_open=False)
    n = 12 * 12
    ground = TileLayer(width=12, height=12, name="Ground",
                       cells=[0] * n, readonly=False)
    set_layer = TileLayer(width=12, height=12, name="set",
                          cells=[0] * n, readonly=False)
    doc = MapDocument(width=12, height=12, tile_w=20, tile_h=20,
                      layers=[ground, set_layer], tilesets=[])
    w.state.doc = doc
    w.state.current_layer = ground
    w.scene.doc = doc
    w.view._doc = doc
    w.view.resetTransform()             # 单位变换 → 采样格可预测
    w.state.profile.write_enabled = True
    w.state.brush_gid = 126
    w.state.stamp = None
    w.state.undo_stack.clear()
    w._set_object_tool("pen")
    return w, doc, set_layer, ground


def _events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


class PenAutoMappingTest(unittest.TestCase):

    def setUp(self):
        self.w, self.doc, self.set_layer, self.ground = _build()
        self.fake = FakeMapper()
        self.w._automapper = self.fake
        self.w._automap_while_drawing = False

    def tearDown(self):
        self.w._pen_cancel()
        self.w.act_automap_while.blockSignals(True)
        self.w.act_automap_while.setChecked(False)
        self.w.act_automap_while.blockSignals(False)
        self.w._automap_while_drawing = False
        self.w._automapper = None
        self.w.state.undo_stack.clear()

    # ---- 用信号级模拟画一条水平直线，左键点空白 = 完成 ----
    def _right_click(self, x, y):
        self.w.view.objectToolRightPressed.emit(float(x), float(y))
        self.w.view.objectToolRightReleased.emit(float(x), float(y))

    def _straight_line(self):
        self._right_click(30, 40)
        self._right_click(140, 40)

    def _finish(self):
        self.w.view.objectToolPressed.emit(10.0, 200.0)
        self.w.view.objectToolReleased.emit(10.0, 200.0)
        _events()

    def _expected_bounds(self):
        """按「真正会被改写且在界内的格」推导本次自动映射应收到的 area。"""
        final = self.w._pen_stamp_cells()
        pts = [(c, r) for (c, r), new in final.items()
               if self.set_layer.in_bounds(c, r)
               and self.set_layer.get(c, r) != new]
        self.assertTrue(pts)
        return (min(c for c, _ in pts), min(r for _, r in pts),
                max(c for c, _ in pts), max(r for _, r in pts))

    def _enable(self):
        self.w.act_automap_while.setChecked(True)
        _events()
        self.assertTrue(self.w._automap_while_drawing)
        self.assertIs(self.w.state.current_layer, self.set_layer)

    # ---- 开关语义 ----
    def test_toggle_switches_to_set_layer_for_pen(self):
        self._enable()
        self.assertIs(self.w.state.current_layer, self.set_layer)
        self.assertEqual(self.w._object_tool, "pen")

    def test_automap_off_does_not_call_mapper(self):
        # 关掉开关时不会自动切到 set 层，这里手动对齐以便和「开」的用例对比
        self.w.state.current_layer = self.set_layer
        self._straight_line()
        before = self.w.state.undo_stack.count()
        self._finish()
        self.assertEqual(self.w.state.undo_stack.count(), before + 1)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.ground.get(1, 2), 0)
        self.assertEqual(self.set_layer.get(3, 2), 126)

    def test_pen_finish_triggers_local_automap_and_one_undo(self):
        self._enable()
        self._straight_line()
        expected = self._expected_bounds()
        before = self.w.state.undo_stack.count()
        ground_before = self.ground.get(expected[0], expected[1])
        set_before = self.set_layer.get(3, 2)
        self._finish()
        # 1) 钢笔线确实落在 set 层；2) 自动映射按线范围局部重算并写入 Ground
        self.assertNotEqual(self.set_layer.get(3, 2), set_before)
        self.assertEqual(self.ground.get(expected[0], expected[1]), self.fake.gid)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.fake.calls[0], (expected, True))
        # 3) 钢笔线 + 自动映射合成一条撤销命令
        self.assertEqual(self.w.state.undo_stack.count(), before + 1)
        self.w.state.undo_stack.undo()
        _events()
        self.assertEqual(self.set_layer.get(3, 2), set_before)
        self.assertEqual(self.ground.get(expected[0], expected[1]), ground_before)
        self.w.state.undo_stack.redo()
        _events()
        self.assertNotEqual(self.set_layer.get(3, 2), set_before)
        self.assertEqual(self.ground.get(expected[0], expected[1]), self.fake.gid)

    def test_no_new_cells_skips_automap(self):
        self._enable()
        self._straight_line()
        # 预先把线要经过的格都写成与笔刷相同的值 → 本次落笔无任何改动
        for (c, r), new in self.w._pen_stamp_cells().items():
            if self.set_layer.in_bounds(c, r):
                self.set_layer.cells[self.set_layer.index(c, r)] = new
        before = self.w.state.undo_stack.count()
        self._finish()
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.w.state.undo_stack.count(), before)
        self.assertIsNone(self.w._pen_path)

    def test_stamp_line_area_covers_whole_stamp(self):
        self._enable()
        self.w.state.stamp = [[1, 0, 2], [3, 4, 5]]
        self._straight_line()
        expected = self._expected_bounds()
        before = self.w.state.undo_stack.count()
        self._finish()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.fake.calls[0][0], expected,
                         "自动映射范围必须覆盖整块图章，而不是只覆盖曲线采样格")
        self.assertEqual(self.ground.get(expected[0], expected[1]), self.fake.gid)
        self.assertEqual(self.w.state.undo_stack.count(), before + 1)
        self.w.state.undo_stack.undo()
        _events()
        self.assertEqual(self.ground.get(expected[0], expected[1]), 0)
        self.assertEqual(self.set_layer.get(3, 2), 0)

    def test_ctrl_return_finish_also_automaps(self):
        self._enable()
        self._straight_line()
        self.w.act_finish_poly.trigger()
        _events()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertIsNone(self.w._pen_path)

    def test_mapper_exception_does_not_leave_macro_open(self):
        self._enable()
        self.fake.boom = True
        self._straight_line()
        before = self.w.state.undo_stack.count()
        with self.assertRaises(RuntimeError):
            self.w._pen_finish()
        self.assertEqual(self.w.state.undo_stack.count(), before + 1,
                         "异常也必须 endMacro，否则后续撤销栈会一直处于宏中")
        self.w.state.undo_stack.undo()
        _events()
        self.assertEqual(self.set_layer.get(3, 2), 0)


if __name__ == "__main__":
    unittest.main()
