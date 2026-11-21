"""图块等比缩放适配地图格子（用户反馈：自定义非 20x20 图集绘制错位）。

问题：切图用图集自身 ts.tile_w/h，绘制却按图块原始像素贴到 doc.tile_w/h
的格子上，两个尺寸不等时图块会溢出邻格（偏大）或四周留缝（偏小）。
修复：get_pixmap_fitted 把图块等比缩放居中到一个地图格子，主绘制与
图章预览都改用它。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.document.tileset import Tileset  # noqa: E402
import rwmap.tilesets.tile_cache as tile_cache  # noqa: E402
from rwmap.view.tile_layer_item import TileLayerItem  # noqa: E402


def _solid(size, color=(220, 60, 60)):
    if isinstance(size, int):
        size = (size, size)
    p = QPixmap(size[0], size[1])
    p.fill(QColor(*color))
    return p


class _Doc:
    width = 3
    height = 3
    tile_w = 20
    tile_h = 20
    resolver = None

    def __init__(self, ts):
        self._ts = ts

    def local_tile(self, gid):
        return (self._ts, gid - 1)


def _alpha(img, x, y):
    return img.pixelColor(x, y).alpha()


class FittedPixmapTest(unittest.TestCase):
    def setUp(self):
        tile_cache._FIT_CACHE.clear()

    def tearDown(self):
        tile_cache._FIT_CACHE.clear()

    def test_same_size_returns_original(self):
        """图块尺寸 == 格子尺寸：零开销直通，返回原对象。"""
        base = _solid(20)
        with mock.patch.object(tile_cache, "get_pixmap", return_value=base):
            out = tile_cache.get_pixmap_fitted(
                Tileset(), 1, (False, False, False), 20, 20)
        self.assertIs(out, base)

    def test_oversize_scaled_into_cell(self):
        """图块 40x40 塞进 20x20 格子：结果画布正好是格子尺寸。"""
        with mock.patch.object(tile_cache, "get_pixmap", return_value=_solid(40)):
            out = tile_cache.get_pixmap_fitted(
                Tileset(), 1, (False, False, False), 20, 20)
        self.assertEqual((out.width(), out.height()), (20, 20))

    def test_undersize_scaled_up_into_cell(self):
        """图块 10x10 放大塞进 20x20 格子。"""
        with mock.patch.object(tile_cache, "get_pixmap", return_value=_solid(10)):
            out = tile_cache.get_pixmap_fitted(
                Tileset(), 1, (False, False, False), 20, 20)
        self.assertEqual((out.width(), out.height()), (20, 20))
        self.assertEqual(_alpha(out.toImage(), 19, 19), 255)

    def test_keep_aspect_ratio_centered(self):
        """40x20 长方形图块等比缩放到 20x20 画布：内容 20x10 居中。"""
        with mock.patch.object(tile_cache, "get_pixmap", return_value=_solid((40, 20))):
            out = tile_cache.get_pixmap_fitted(
                Tileset(), 1, (False, False, False), 20, 20)
        self.assertEqual((out.width(), out.height()), (20, 20))
        im = out.toImage()
        self.assertEqual(_alpha(im, 10, 10), 255)   # 中间绿带
        self.assertEqual(_alpha(im, 10, 2), 0)      # 上方透明留白
        self.assertEqual(_alpha(im, 10, 18), 0)     # 下方透明留白

    def test_result_is_cached(self):
        """相同 key 复用缓存结果。"""
        ts = Tileset(firstgid=1)
        with mock.patch.object(tile_cache, "get_pixmap",
                               return_value=_solid(40)) as gp:
            a = tile_cache.get_pixmap_fitted(
                ts, 1, (False, False, False), 20, 20)
            b = tile_cache.get_pixmap_fitted(
                ts, 1, (False, False, False), 20, 20)
        self.assertIs(a, b)
        self.assertEqual(gp.call_count, 1)

    def test_clear_cache_invalidates_fit_cache(self):
        with mock.patch.object(tile_cache, "get_pixmap", return_value=_solid(40)):
            tile_cache.get_pixmap_fitted(
                Tileset(), 1, (False, False, False), 20, 20)
        self.assertGreater(len(tile_cache._FIT_CACHE), 0)
        tile_cache.clear_cache()
        self.assertEqual(len(tile_cache._FIT_CACHE), 0)


class TileLayerFitTest(unittest.TestCase):
    """主绘制：图块不得溢出格子 / 不得留缝。"""

    def _render(self, tile_size, center_empty=False):
        ts = Tileset(firstgid=1, tile_w=tile_size, tile_h=tile_size)
        cells = [1] * 9
        if center_empty:
            cells[4] = 0
        layer = TileLayer(width=3, height=3, name="G",
                          cells=cells, readonly=False)
        item = TileLayerItem(layer, _Doc(ts), 0)
        img = QImage(60, 60, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        with mock.patch.object(tile_cache, "get_pixmap",
                               return_value=_solid(tile_size)):
            tile_cache._FIT_CACHE.clear()
            item._draw_cells(p, 0, 0, 2, 2)
        p.end()
        return img

    def test_oversize_tile_does_not_invade_empty_neighbor(self):
        """40x40 图块画进 20x20 格子：空格中心保持透明。"""
        img = self._render(40, center_empty=True)
        self.assertEqual(_alpha(img, 30, 30), 0)   # 中心空格不被侵入
        self.assertEqual(_alpha(img, 10, 10), 255)  # 有内容的格正常

    def test_undersize_tile_fills_cell(self):
        """10x10 图块放大：格子右下角不留缝。"""
        img = self._render(10)
        self.assertEqual(_alpha(img, 18, 18), 255)

    def test_cell_size_change_rebuilds_scaled_tiles(self):
        """doc 格子尺寸变化后 LRU 失效，重新按新尺寸缩放。"""
        ts = Tileset(firstgid=1, tile_w=40, tile_h=40)
        doc = _Doc(ts)
        layer = TileLayer(width=3, height=3, name="G",
                          cells=[1] * 9, readonly=False)
        item = TileLayerItem(layer, doc, 0)
        with mock.patch.object(tile_cache, "get_pixmap", return_value=_solid(40)):
            tile_cache._FIT_CACHE.clear()
            self.assertEqual(item._pixmap_for(1).width(), 20)
            doc.tile_w = doc.tile_h = 32
            self.assertEqual(item._pixmap_for(1).width(), 32)


if __name__ == "__main__":
    unittest.main()
