"""GPU 渲染器的纯函数回归（不依赖 GL 上下文，offscreen 可跑）。"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch

from rwmap.view import graph_gpu_renderer as ggr


class ProbeTest(unittest.TestCase):
    def test_offscreen_returns_false(self):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            ok, reason = ggr.probe_gl_environment()
        self.assertFalse(ok)
        self.assertIn("offscreen", reason)

    def test_env_zero_returns_false(self):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "windows",
                                     "RWMAP_GRAPH_GPU": "0"}):
            ok, reason = ggr.probe_gl_environment()
        self.assertFalse(ok)
        self.assertIn("RWMAP_GRAPH_GPU", reason)

    def test_frozen_returns_false(self):
        env = dict(os.environ)
        env.pop("QT_QPA_PLATFORM", None)
        env.pop("RWMAP_GRAPH_GPU", None)
        with patch.dict(os.environ, env, clear=True), \
             patch.object(ggr.sys, "frozen", True, create=True):
            ok, reason = ggr.probe_gl_environment()
        self.assertFalse(ok)
        self.assertIn("frozen", reason)

    def test_software_renderer_tokens(self):
        for name in ("llvmpipe", "SwiftShader Device", "Microsoft Basic Render Driver",
                     "Mesa offscreen", "ANGLE (Google, Vulkan)"):
            self.assertTrue(ggr._renderer_is_software(name), name)
        self.assertFalse(ggr._renderer_is_software("Intel(R) UHD Graphics 630"))
        self.assertFalse(ggr._renderer_is_software("NVIDIA GeForce RTX 3060"))


class PackingTest(unittest.TestCase):
    def test_layout_length_and_values(self):
        e = ggr.EdgeInstance(
            eid=1,
            ctrl=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0)),
            color=(0.1, 0.2, 0.3, 0.4),
            tip=(9.0, 10.0), direction=(0.0, 1.0),
            has_arrow=True, overview=True, scale=2.5,
        )
        data = ggr.pack_edge_instances([e])
        self.assertEqual(len(data), ggr.FLOATS_PER_INSTANCE)
        self.assertEqual(data[ggr.IDX_CTRL0:ggr.IDX_CTRL0 + 2], array_pair(1.0, 2.0))
        self.assertAlmostEqual(data[ggr.IDX_COLOR + 3], 0.4, places=6)
        self.assertAlmostEqual(data[ggr.IDX_TIP], 9.0)
        self.assertEqual(int(data[ggr.IDX_FLAGS]),
                         ggr.FLAG_HAS_ARROW | ggr.FLAG_OVERVIEW)
        self.assertAlmostEqual(data[ggr.IDX_SCALE], 2.5)

    def test_no_arrow_flag(self):
        e = ggr.EdgeInstance(1, ((0, 0), (0, 0), (0, 0), (0, 0)), (1, 1, 1, 1),
                             has_arrow=False)
        data = ggr.pack_edge_instances([e])
        self.assertEqual(int(data[ggr.IDX_FLAGS]) & ggr.FLAG_HAS_ARROW, 0)

    def test_multiple_instances_stride(self):
        es = [ggr.EdgeInstance(i, ((0, 0), (0, 0), (0, 0), (0, 0)), (1, 1, 1, 1))
              for i in range(3)]
        self.assertEqual(len(ggr.pack_edge_instances(es)),
                         3 * ggr.FLOATS_PER_INSTANCE)


def array_pair(a, b):
    from array import array
    return array("f", (a, b))


class MathTest(unittest.TestCase):
    def test_bezier_matches_dock_cubic_at(self):
        from PySide6.QtCore import QPointF
        from rwmap.widgets.trigger_graph_dock import _cubic_at
        c = ((0.0, 0.0), (10.0, 20.0), (30.0, -5.0), (40.0, 0.0))
        qc = tuple(QPointF(x, y) for x, y in c)
        for t in (0.0, 0.13, 0.5, 0.77, 1.0):
            mine = ggr.bezier_at(c, t)
            theirs = _cubic_at(qc, t)
            self.assertAlmostEqual(mine[0], theirs.x(), places=5)
            self.assertAlmostEqual(mine[1], theirs.y(), places=5)

    def test_sdf_alpha_monotonic(self):
        vals = [ggr.sdf_segment_alpha(d, 0.9, 0.5)
                for d in (0.0, 0.5, 0.9, 1.2, 1.5, 2.0)]
        self.assertEqual(vals[0], 1.0)
        self.assertEqual(vals[-1], 0.0)
        for a, b in zip(vals, vals[1:]):
            self.assertGreaterEqual(a, b)

    def test_arrow_scale_formula(self):
        self.assertAlmostEqual(ggr.arrow_scale_for(0.5, False), 1.0)
        self.assertAlmostEqual(ggr.arrow_scale_for(0.05, True), 1.0 / 0.05)
        self.assertAlmostEqual(ggr.arrow_scale_for(0.01, True), 1.0 / 0.05)

    def test_scene_to_px_matrix(self):
        a, d, tx, ty = ggr.make_scene_to_px(0.5, 0.5, -100.0, 50.0, 2.0)
        self.assertAlmostEqual(a, 1.0)
        self.assertAlmostEqual(d, 1.0)
        self.assertAlmostEqual(tx, -200.0)
        self.assertAlmostEqual(ty, 100.0)
        # scene (10, 20) -> px (-190, 120)
        self.assertAlmostEqual(a * 10.0 + tx, -190.0)
        self.assertAlmostEqual(d * 20.0 + ty, 120.0)

    def test_arrow_vertices_match_shape(self):
        tip, d = (0.0, 0.0), (1.0, 0.0)
        pts = ggr.arrow_screen_vertices(tip, d, 1.0)
        self.assertEqual(pts[0], (0.0, 0.0))
        self.assertAlmostEqual(pts[1][0], -10.0)
        self.assertAlmostEqual(pts[1][1], 4.5)
        self.assertAlmostEqual(pts[2][1], -4.5)


class SourceGuardTest(unittest.TestCase):
    def test_no_numpy_import(self):
        """发行版 spec 排除 numpy：GPU 模块绝不能 import 它。"""
        path = ggr.__file__
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("import numpy", src)
        self.assertNotIn("from numpy", src)


if __name__ == "__main__":
    unittest.main()
