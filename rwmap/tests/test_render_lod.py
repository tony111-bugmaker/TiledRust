"""缩小时按显示器像素比自适应降低渲染精度（LOD）的回归测试。

覆盖：
- choose_render_lod 的档位选择 / 高分屏保真 / 单调性；
- viewport_pixel_ratio 的离屏回退；
- TileLayerItem 多级缓存尺寸、切档、局部失效同步；
- paint() 真正按 view 缩放 × 显示器像素比选档。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem  # noqa: E402

_app = QApplication.instance() or QApplication([])

from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.view.render_lod import (  # noqa: E402
    LOD_LEVELS, choose_render_lod, viewport_pixel_ratio,
)
from rwmap.view.tile_layer_item import TileLayerItem  # noqa: E402


class ChooseLodTest(unittest.TestCase):
    def test_levels_are_powers_of_two(self):
        self.assertEqual(LOD_LEVELS, (1.0, 0.5, 0.25, 0.125, 0.0625))

    def test_zoom_out_coarsens(self):
        # 20px 格子，每格屏幕物理像素 = 20 * scale * dpr。
        cases = [
            (1.0, 1.0),    # 20px  → 全分辨率
            (0.7, 0.5),    # 14px
            (0.3, 0.25),   # 6px
            (0.1, 0.125),  # 2px
            (0.03, 0.0625),
        ]
        for scale, expected in cases:
            with self.subTest(scale=scale):
                self.assertEqual(choose_render_lod(scale), expected)

    def test_high_dpi_keeps_more_detail(self):
        """同倍率下，高分屏每格物理像素更多，不能糊成同一档。"""
        self.assertEqual(choose_render_lod(0.3, 1.0), 0.25)
        self.assertEqual(choose_render_lod(0.3, 2.0), 0.5)
        self.assertEqual(choose_render_lod(0.15, 2.0), 0.25)
        self.assertEqual(choose_render_lod(0.7, 2.0), 1.0)

    def test_monotonic_and_bounded(self):
        levels = [choose_render_lod(s / 20.0) for s in range(1, 41)]
        self.assertEqual(levels, sorted(levels))
        self.assertEqual(choose_render_lod(1e-6), LOD_LEVELS[-1])
        self.assertEqual(choose_render_lod(1e6), 1.0)

    def test_invalid_inputs_fall_back(self):
        for args in ((None, None), (0.0, -1), ("bad", "bad")):
            with self.subTest(args=args):
                self.assertEqual(choose_render_lod(*args), 1.0)


class PixelRatioTest(unittest.TestCase):
    def test_no_widget_is_offscreen_default(self):
        self.assertEqual(viewport_pixel_ratio(None), 1.0)

    def test_reads_widget_device_pixel_ratio(self):
        widget = mock.Mock()
        widget.devicePixelRatioF.return_value = 2.0
        self.assertEqual(viewport_pixel_ratio(widget), 2.0)

    def test_bad_ratio_falls_back(self):
        widget = mock.Mock()
        widget.devicePixelRatioF.return_value = 0.0
        self.assertEqual(viewport_pixel_ratio(widget), 1.0)
        widget.devicePixelRatioF.return_value = 100.0
        self.assertEqual(viewport_pixel_ratio(widget), 8.0)


def _tile_pixmap():
    pix = QPixmap(20, 20)
    pix.fill(QColor(90, 160, 70))
    return pix


class _Doc:
    width = 20
    height = 20
    tile_w = 20
    tile_h = 20
    resolver = None

    def local_tile(self, gid):
        return (object(), gid)


def _item(values=None):
    cells = list(values) if values is not None else [1] * 400
    layer = TileLayer(width=20, height=20, name="G",
                      cells=cells, readonly=False)
    return TileLayerItem(layer, _Doc())


def _paint(item, scale, widget=None):
    img = QImage(200, 200, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.scale(scale, scale)
    opt = QStyleOptionGraphicsItem()
    opt.exposedRect = QRectF(0, 0, 400, 400)
    item.paint(p, opt, widget)
    p.end()
    return img


class MultiLodMiniTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("rwmap.view.tile_layer_item.get_pixmap",
                             return_value=_tile_pixmap())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mini_size_shrinks_with_lod(self):
        item = _item()
        item._build_mini(0.5)
        item._build_mini(0.25)
        item._build_mini(0.125)
        self.assertEqual(item._minis[0.5].width(), 200)
        self.assertEqual(item._minis[0.25].width(), 100)
        self.assertEqual(item._minis[0.125].width(), 50)

    def test_switching_lod_keeps_old_cache(self):
        item = _item()
        item._build_mini(0.5)
        _paint(item, 0.3)
        self.assertEqual(item._lod, 0.25)
        self.assertIn(0.5, item._minis)
        self.assertIn(0.25, item._minis)

    def test_all_built_levels_follow_local_edit(self):
        item = _item()
        item._build_mini(0.5)
        item._build_mini(0.25)
        item.layer.cells[5 * 20 + 6] = 2
        item.invalidate_cells([(6, 5)])
        for lod in (0.5, 0.25):
            with self.subTest(lod=lod):
                self.assertNotIn(lod, item._mini_dirty)

    def test_invalidate_all_marks_every_level(self):
        item = _item()
        item._build_mini(0.5)
        item._build_mini(0.25)
        item.invalidate_all()
        self.assertEqual(item._mini_dirty, {0.5, 0.25})

    def test_paint_uses_view_scale(self):
        item = _item()
        _paint(item, 0.7)
        self.assertEqual(item._lod, 0.5)
        _paint(item, 0.1)
        self.assertEqual(item._lod, 0.125)

    def test_paint_uses_display_pixel_ratio(self):
        low = _item()
        high = _item()
        widget = mock.Mock()
        widget.devicePixelRatioF.return_value = 2.0
        _paint(low, 0.3)
        _paint(high, 0.3, widget)
        self.assertEqual(low._lod, 0.25)
        self.assertEqual(high._lod, 0.5)
        # 高分屏档位更精细 → 缓存尺寸更大，但不会被降成同一档。
        self.assertGreater(high._minis[high._lod].width(),
                           low._minis[low._lod].width())

    def test_full_scale_does_not_build_mini(self):
        item = _item()
        _paint(item, 1.0)
        self.assertEqual(item._minis, {})

    def test_sparse_layer_still_draws_cells(self):
        values = [0] * 400
        values[2 * 20 + 3] = 1
        item = _item(values)
        _paint(item, 0.1)
        self.assertEqual(item._minis, {})


if __name__ == "__main__":
    unittest.main()
