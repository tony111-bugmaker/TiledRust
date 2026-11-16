"""填充工具（Flood Fill）算法与区域笔划回归。"""
import unittest
from types import SimpleNamespace

from rwmap.document.layer import TileLayer
from rwmap.tools.fill_tool import FillTool


def make_layer(grid):
    h = len(grid)
    w = len(grid[0])
    cells = [grid[r][c] for r in range(h) for c in range(w)]
    return TileLayer(width=w, height=h, cells=cells)


class FillToolTest(unittest.TestCase):
    def setUp(self):
        self.tool = FillTool()

    def test_fill_whole_region(self):
        layer = make_layer([[1, 1], [1, 1]])
        out = self.tool.fill_region(SimpleNamespace(brush_gid=5), layer, 0, 0)
        self.assertEqual(len(out), 4)
        self.assertTrue(all(new == 5 for _, _, new in out))

    def test_fill_connected_only(self):
        # 1 1 2 / 1 2 2 ：左上角 1 的连通区只有 3 格。
        layer = make_layer([[1, 1, 2], [1, 2, 2]])
        out = self.tool.fill_region(SimpleNamespace(brush_gid=9), layer, 0, 0)
        coords = {(c, r) for c, r, _ in out}
        self.assertEqual(coords, {(0, 0), (1, 0), (0, 1)})
        self.assertTrue(all(new == 9 for _, _, new in out))

    def test_same_color_is_noop(self):
        layer = make_layer([[3, 3], [3, 3]])
        self.assertIsNone(
            self.tool.fill_region(SimpleNamespace(brush_gid=3), layer, 0, 0))

    def test_out_of_bounds(self):
        layer = make_layer([[1]])
        self.assertIsNone(
            self.tool.fill_region(SimpleNamespace(brush_gid=5), layer, 5, 5))

    def test_blocked_by_other(self):
        # 中心 2 阻断，外围 8 个 1 仍四连通成环。
        layer = make_layer([[1, 1, 1], [1, 2, 1], [1, 1, 1]])
        out = self.tool.fill_region(SimpleNamespace(brush_gid=7), layer, 0, 0)
        coords = {(c, r) for c, r, _ in out}
        self.assertEqual(len(coords), 8)
        self.assertNotIn((1, 1), coords)

    def test_desired_gid_none(self):
        # 填充不走单格写入，desired_gid 必须返回 None。
        layer = make_layer([[1]])
        self.assertIsNone(
            self.tool.desired_gid(SimpleNamespace(brush_gid=5), layer, 0, 0))


if __name__ == "__main__":
    unittest.main()
