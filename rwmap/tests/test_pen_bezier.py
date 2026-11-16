"""钢笔工具的纯算法单测（无需 QApplication / Qt 平台插件）。"""
import math
import unittest

from rwmap.tools.pen_tool import Anchor, PenPath
from rwmap.utils.bezier import (
    bezier_derivative, bezier_point, flatten_segment, flatten_segments,
    nearest_on_curve, rasterize_polyline_to_cells, split_segment,
    tangent_handle,
)


SEG = ((0.0, 0.0), (0.0, 100.0), (100.0, 100.0), (100.0, 0.0))


class BezierPureTest(unittest.TestCase):

    def test_endpoints_and_midpoint(self):
        self.assertEqual(bezier_point(SEG, 0.0), (0.0, 0.0))
        self.assertEqual(bezier_point(SEG, 1.0), (100.0, 0.0))
        # 对称 S 形曲线的中点应为 (50, 75)
        x, y = bezier_point(SEG, 0.5)
        self.assertAlmostEqual(x, 50.0, places=6)
        self.assertAlmostEqual(y, 75.0, places=6)
        dx, dy = bezier_derivative(SEG, 0.5)
        self.assertGreater(dx, 0)          # 中点切线向右
        self.assertAlmostEqual(dy, 0.0, places=6)

    def test_straight_segment_needs_no_subdivision(self):
        straight = ((0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0))
        samples = flatten_segment(straight, tol=0.5)
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0], ((0.0, 0.0), 0.0))
        self.assertEqual(samples[-1], ((30.0, 0.0), 1.0))

    def test_adaptive_flatten_error_below_tol(self):
        tol = 0.5
        samples = flatten_segment(SEG, tol)
        self.assertGreater(len(samples), 2)
        for (a, ta), (b, tb) in zip(samples, samples[1:]):
            mid = bezier_point(SEG, (ta + tb) * 0.5)
            chord = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
            self.assertLess(math.dist(mid, chord), tol * 1.5)

    def test_split_segment_preserves_shape(self):
        t = 0.3
        left, right = split_segment(SEG, t)
        f = bezier_point(SEG, t)
        self.assertAlmostEqual(math.dist(left[3], f), 0.0, places=6)
        self.assertAlmostEqual(math.dist(right[0], f), 0.0, places=6)
        # 左段的 u 对应原段 t*u；右段对应 t + (1-t)*u
        for u in (0.0, 0.25, 0.5, 0.75, 1.0):
            self.assertAlmostEqual(
                math.dist(bezier_point(left, u), bezier_point(SEG, t * u)),
                0.0, places=6)
            self.assertAlmostEqual(
                math.dist(bezier_point(right, u),
                          bezier_point(SEG, t + (1 - t) * u)),
                0.0, places=6)

    def test_tangent_handle_is_symmetric(self):
        left, right = split_segment(SEG, 0.4)
        h = tangent_handle(left, right)
        self.assertGreater(math.hypot(*h), 0.0)
        new = Anchor(left[3][0], left[3][1])
        new.set_symmetric(*h)
        self.assertEqual(new.hin, (-h[0], -h[1]))
        self.assertEqual(new.hout, h)
        # 新点落在原曲线上
        self.assertAlmostEqual(math.dist(new.pos(), bezier_point(SEG, 0.4)),
                               0.0, places=6)

    def test_nearest_on_curve(self):
        segments = [SEG]
        for t in (0.1, 0.35, 0.8):
            p = bezier_point(SEG, t)
            dist, si, got_t = nearest_on_curve(p, segments, tol=0.2)
            self.assertEqual(si, 0)
            self.assertLess(dist, 0.2)
            self.assertLess(abs(got_t - t), 0.02)
        dist, _si, _t = nearest_on_curve((500.0, 500.0), segments, tol=0.2)
        self.assertGreater(dist, 100.0)
        self.assertIsNone(nearest_on_curve((0.0, 0.0), [], tol=0.2))

    def test_rasterize_no_gaps_and_no_duplicates(self):
        # 从 (5,5) 到 (175, 95)，20px 一格：斜线必须 4-连通、不跳格
        cells = rasterize_polyline_to_cells([(5.0, 5.0), (175.0, 95.0)], 20, 20)
        self.assertEqual(len(cells), len(set(cells)), "同一格被重复加入")
        self.assertGreater(len(cells), 8)
        for a, b in zip(cells, cells[1:]):
            self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1,
                             f"出现跳格：{a} → {b}")
        self.assertEqual(cells[0], (0, 0))
        self.assertEqual(cells[-1], (8, 4))

    def test_rasterize_single_point_and_empty(self):
        self.assertEqual(rasterize_polyline_to_cells([], 20, 20), [])
        self.assertEqual(rasterize_polyline_to_cells([(45.0, 65.0)], 20, 20),
                         [(2, 3)])

    def test_flatten_segments_joins_without_duplicate_endpoints(self):
        a = Anchor.corner(0.0, 0.0)
        b = Anchor.corner(100.0, 0.0)
        c = Anchor.corner(100.0, 100.0)
        path = PenPath([a, b, c])
        pts = path.flatten(0.5)
        self.assertEqual(pts[0], (0.0, 0.0))
        self.assertEqual(pts[-1], (100.0, 100.0))
        self.assertEqual(len(path.segments()), 2)
        self.assertIn((100.0, 0.0), pts)

    def test_pen_path_bounds_and_copy(self):
        a = Anchor.corner(10.0, 20.0)
        a.set_symmetric(30.0, -10.0)
        path = PenPath([a])
        x0, y0, x1, y1 = path.bounds()
        self.assertEqual((x0, y0, x1, y1), (-20.0, 10.0, 40.0, 30.0))
        clone = path.copy()
        clone.anchors[0].set_symmetric(5.0, 5.0)
        self.assertEqual(a.hout, (30.0, -10.0))   # 深拷贝，互不影响


if __name__ == "__main__":
    unittest.main()
