"""第十九轮：钢笔落笔后的正反双向遍历，修「一段有过渡、一段没有」。

探针结论（probe_pen_transition_direction / probe_pen_transition_edge）：
  * compute() 与扫描方向无关，单纯跑两遍不会补任何东西；
  * 真正的缺口是 set 层标记被跳过造成的空洞：8 格草线中间挖 2 格洞，
    过渡瓦片从 22 掉到 18。

因此正反两遍遍历用在「补标记」上：起点→终点借左侧有效地形，终点→起点
借右侧有效地形，两遍都给候选时取距离更近的一侧。同时把自动映射的重算
范围从「值变化的格」扩展到整条曲线，避免线画在已有相同地形上时不重算。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeMapper:
    def __init__(self, gid=999):
        self.gid = gid
        self.calls = []

    def compute(self, doc, area=None, only_diff=True):
        self.calls.append((area, only_diff))
        if area is None:
            return {}
        c0, r0, _c1, _r1 = area
        return {"Ground": [(c0, r0, self.gid)]}


def _tilesets():
    from rwmap.document.tileset import Tileset
    return [
        Tileset(firstgid=126, name="Long Grass", tilecount=50),   # 126..175
        Tileset(firstgid=186, name="Water", tilecount=50),        # 186..235
        Tileset(firstgid=800, name="Long Grass - Water", tilecount=50),  # 过渡
        Tileset(firstgid=900, name="misc", tilecount=20),
    ]


def _build(tilesets=None):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    from rwmap.document.layer import TileLayer
    from rwmap.document.map_document import MapDocument

    w = MainWindow(auto_open=False)
    n = 12 * 12
    ground = TileLayer(width=12, height=12, name="Ground", cells=[0] * n)
    set_layer = TileLayer(width=12, height=12, name="set", cells=[0] * n)
    doc = MapDocument(width=12, height=12, tile_w=20, tile_h=20,
                      layers=[ground, set_layer], tilesets=tilesets or [])
    w.state.doc = doc
    w.state.current_layer = ground
    w.scene.doc = doc
    w.view._doc = doc
    w.view.resetTransform()
    w.state.profile.write_enabled = True
    w.state.brush_gid = 126
    w.state.stamp = None
    w.state.undo_stack.clear()
    w._set_object_tool("pen")
    return w, doc, set_layer, ground


def _events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


class PenSweepAlgorithmTest(unittest.TestCase):
    """纯算法层：不落笔，直接测双向补洞与 sweep 范围。"""

    def setUp(self):
        self.w, self.doc, self.set_layer, self.ground = _build(_tilesets())

    def test_two_pass_repairs_holes_with_nearest_valid_marker(self):
        w = self.w
        final = {(0, 0): 126, (1, 0): 813, (2, 0): 186,
                 (3, 0): 820, (4, 0): 130}
        curve = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
        repaired, filled, skipped = w._pen_repair_markers(final, curve)
        self.assertEqual(filled, 2)
        self.assertEqual(skipped, 0)
        self.assertEqual(repaired[(1, 0)], 126, "正向：借左侧 126")
        self.assertEqual(repaired[(3, 0)], 186, "正向：借左侧 186")
        self.assertEqual(repaired[(4, 0)], 126, "130 归一到首格 126")
        self.assertNotIn(813, repaired.values())
        self.assertNotIn(820, repaired.values())

    def test_backward_pass_fills_leading_holes(self):
        """起点侧就是空洞时，正向没有前驱，必须靠反向（终点→起点）补。"""
        w = self.w
        final = {(0, 0): 813, (1, 0): 820, (2, 0): 186, (3, 0): 126}
        curve = [(0, 0), (1, 0), (2, 0), (3, 0)]
        repaired, filled, skipped = w._pen_repair_markers(final, curve)
        self.assertEqual(filled, 2)
        self.assertEqual(skipped, 0)
        self.assertEqual(repaired[(0, 0)], 186)
        self.assertEqual(repaired[(1, 0)], 186)

    def test_all_invalid_markers_are_reported_not_silently_dropped(self):
        w = self.w
        final = {(i, 2): 813 for i in range(5)}
        curve = [(i, 2) for i in range(5)]
        repaired, filled, skipped = w._pen_repair_markers(final, curve)
        self.assertEqual((filled, skipped), (0, 5))
        self.assertEqual(repaired, {})

    def test_no_tileset_info_keeps_raw_markers(self):
        """无图集信息无法判断时保守保留原值，绝不丢整条落笔。"""
        w2, _doc, _s, _g = _build([])
        final = {(0, 2): 813, (1, 2): 126}
        repaired, filled, skipped = w2._pen_repair_markers(final, [(0, 2), (1, 2)])
        self.assertEqual((filled, skipped), (0, 0))
        self.assertEqual(repaired, final)

    def test_sweep_bounds_covers_whole_curve_not_only_changed_cells(self):
        w = self.w
        curve = [(i, 2) for i in range(1, 8)]
        self.assertEqual(w._pen_sweep_bounds(curve, (5, 2, 7, 2)), (1, 2, 7, 2))
        self.assertEqual(w._pen_sweep_bounds(curve, (0, 1, 8, 3)), (0, 1, 8, 3),
                         "图章展开的更大范围不能被缩小")
        self.assertEqual(w._pen_sweep_bounds([], (3, 3, 4, 4)), (3, 3, 4, 4))


class PenSweepIntegrationTest(unittest.TestCase):
    """端到端：信号级落笔，锁死 set 层不挖洞、area 覆盖整条线、一次撤销。"""

    def setUp(self):
        self.w, self.doc, self.set_layer, self.ground = _build(_tilesets())
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

    def _line(self):
        self.w.view.objectToolRightPressed.emit(30.0, 40.0)
        self.w.view.objectToolRightReleased.emit(30.0, 40.0)
        self.w.view.objectToolRightPressed.emit(140.0, 40.0)
        self.w.view.objectToolRightReleased.emit(140.0, 40.0)

    def _finish(self):
        self.w.view.objectToolPressed.emit(10.0, 200.0)
        self.w.view.objectToolReleased.emit(10.0, 200.0)
        _events()

    def _enable(self):
        self.w.act_automap_while.setChecked(True)
        _events()

    def test_stamp_with_transition_tile_leaves_no_hole_in_set_layer(self):
        self._enable()
        self.w.state.stamp = [[126, 813, 186]]     # 中间格是过渡瓦片
        self._line()
        self._finish()
        vals = [int(self.set_layer.get(c, 2)) & 0xFFFFFFFF for c in range(0, 9)]
        self.assertNotIn(813, vals, "过渡瓦片不该留在 set 层")
        self.assertNotIn(0, vals, "被跳过的格会在标记层挖洞")
        self.assertIn("双向补齐", self.w.statusBar().currentMessage())
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.w.state.undo_stack.count(), 1,
                         "钢笔线 + 自动映射仍是一条撤销")

    def test_area_covers_curve_even_when_only_tail_cells_changed(self):
        self._enable()
        # 前 4 格预先写成落笔后的值 → bounds 只有后 4 格
        for c in range(1, 5):
            self.set_layer.cells[self.set_layer.index(c, 2)] = 126
        self._line()
        self._finish()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.fake.calls[0][0], (1, 2, 7, 2),
                         "重算范围必须覆盖整条曲线，否则前段边界不生成过渡")


if __name__ == "__main__":
    unittest.main()
