"""矩形框选 / 复制 / 粘贴 / 移动的纯算法回归（零 GUI 依赖）。"""
import unittest

from rwmap.document.layer import TileLayer
from rwmap.rw.selection import (
    cells_to_rect, compute_move_changes, in_rect, moved_rect, norm_rect,
    read_selection, rect_to_cells,
)


def make_layer(grid):
    h = len(grid)
    w = len(grid[0])
    cells = [grid[r][c] for r in range(h) for c in range(w)]
    return TileLayer(width=w, height=h, cells=cells)


class SelectionPureTest(unittest.TestCase):
    def test_norm_rect(self):
        self.assertEqual(norm_rect((5, 9), (1, 2)), (1, 2, 5, 9))

    def test_rect_to_cells(self):
        self.assertEqual(rect_to_cells((0, 0, 65, 33), 32, 32), (0, 0, 3, 2))
        self.assertEqual(rect_to_cells((0, 0, 31, 31), 32, 32), (0, 0, 1, 1))

    def test_rect_to_cells_boundary_eps(self):
        # 缩放后格线回传成 40.13：默认半开区间会多选一行，
        # 传入 1 屏幕像素的容差后才仍是 3x2。
        self.assertEqual(rect_to_cells((0, 0, 59.97, 40.13), 20, 20),
                         (0, 0, 3, 3))
        self.assertEqual(rect_to_cells((0, 0, 59.97, 40.13), 20, 20,
                                       boundary_eps=0.5), (0, 0, 3, 2))

    def test_cells_to_rect_roundtrip(self):
        sel = (2, 3, 4, 5)
        self.assertEqual(rect_to_cells(cells_to_rect(sel, 16, 16), 16, 16), sel)

    def test_in_rect(self):
        r = (0, 0, 100, 100)
        self.assertTrue(in_rect(r, 50, 50))
        self.assertFalse(in_rect(r, 150, 50))
        self.assertFalse(in_rect(None, 0, 0))

    def test_moved_rect(self):
        self.assertEqual(moved_rect((0, 0, 10, 10), 5, -3), (5, -3, 15, 7))
        self.assertIsNone(moved_rect(None, 1, 1))

    def test_read_selection(self):
        layer = make_layer([[1, 2, 3], [4, 5, 6]])
        self.assertEqual(read_selection(layer, (0, 0, 2, 2)), [[1, 2], [4, 5]])

    def test_read_selection_out_of_bounds_is_empty(self):
        """地图外拖框得到的越界选区读取时不能 IndexError，越界格当空格。"""
        layer = make_layer([[1, 2, 3], [4, 5, 6]])
        # 右/下越界曾直接抛 IndexError（用户崩溃堆栈的根因）。
        self.assertEqual(read_selection(layer, (2, 1, 2, 2)), [[6, 0], [0, 0]])
        # 负坐标起点曾静默绕回表尾读错瓦片，现在一律当空格。
        self.assertEqual(read_selection(layer, (-1, -1, 2, 2)), [[0, 0], [0, 1]])

    def test_move_non_overlapping(self):
        layer = make_layer([[1, 1, 0, 0], [1, 1, 0, 0],
                            [0, 0, 0, 0], [0, 0, 0, 0]])
        changes = compute_move_changes(layer, (0, 0, 2, 2), 2, 0)
        self.assertEqual(len(changes), 8)
        new_cells = {idx: new for idx, _old, new in changes}
        self.assertEqual(new_cells[layer.index(2, 0)], 1)
        self.assertEqual(new_cells[layer.index(3, 0)], 1)
        self.assertEqual(new_cells[layer.index(2, 1)], 1)
        self.assertEqual(new_cells[layer.index(3, 1)], 1)

    def test_move_overlapping(self):
        layer = make_layer([[7, 7, 7], [7, 7, 7], [7, 7, 7]])
        changes = compute_move_changes(layer, (0, 0,2, 2), 1, 0)
        # 原 (0,0)(0,1) 未被覆盖 → 清 0；新 (2,0)(2,1) 原已是 7，old==new 不计入。
        self.assertEqual(len(changes), 2)
        new_cells = {idx: new for idx, _old, new in changes}
        self.assertEqual(new_cells[layer.index(0, 0)], 0)
        self.assertEqual(new_cells[layer.index(0, 1)], 0)

    def test_move_noop(self):
        layer = make_layer([[1, 2], [3, 4]])
        self.assertEqual(compute_move_changes(layer, (0, 0, 2, 2), 0, 0), [])

    def test_move_out_of_bounds(self):
        layer = make_layer([[1, 2], [3, 4]])
        changes = compute_move_changes(layer, (0, 0, 2, 2), 5, 5)
        self.assertEqual(len(changes), 4)
        for _idx, _old, new in changes:
            self.assertEqual(new, 0)


if __name__ == "__main__":
    unittest.main()
