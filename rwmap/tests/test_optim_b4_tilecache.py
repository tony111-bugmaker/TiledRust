"""优化批次 4 回归：TileLayerItem 小 LRU + mini 缓存失效。

- 交错地形 A/B/A/B 时 _pixmap_for 不再每格 miss；
- cache_version() 变化后 LRU 与 mini 都失效（换图集不串图）；
- invalidate_all() 保留 QPixmap、延迟全量重画，且重画结果与
  「全新 item 的 _build_mini()」逐像素一致。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# QPixmap / QPainter 需要 QGuiApplication；单独跑本模块时也要先建应用实例。
_app = QApplication.instance() or QApplication([])

from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.tilesets import tile_cache  # noqa: E402
from rwmap.view.tile_layer_item import TileLayerItem  # noqa: E402


class _FakeDoc:
    width = 16
    height = 16
    tile_w = 20
    tile_h = 20
    resolver = None

    def local_tile(self, gid):
        return (object(), gid)


def _layer(values=None):
    cells = list(values) if values is not None else [0] * 256
    return TileLayer(width=16, height=16, name="Ground",
                     cells=cells, readonly=False)


def _tile_pixmap():
    pix = QPixmap(20, 20)
    pix.fill(QColor(90, 160, 70))
    return pix


def _render_mini(item, size=200):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    item._draw_mini(p, QRectF(0, 0, size, size))
    p.end()
    return img


def _diff(a: QImage, b: QImage) -> int:
    return sum(1 for y in range(a.height()) for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


class TilePixmapLRUTest(unittest.TestCase):
    def setUp(self):
        self.layer = _layer()
        self.item = TileLayerItem(self.layer, _FakeDoc())
        patcher = mock.patch("rwmap.view.tile_layer_item.get_pixmap",
                             return_value=_tile_pixmap())
        self.get_pixmap = patcher.start()
        self.addCleanup(patcher.stop)

    def test_interleaved_raws_only_resolve_once_each(self):
        for raw in (1, 2, 1, 2, 1, 2, 3, 4, 3, 4):
            self.item._pixmap_for(raw)
        self.assertEqual(self.get_pixmap.call_count, 4)
        self.assertIs(self.item._pixmap_for(1), self.item._pixmap_for(1))
        self.assertEqual(self.get_pixmap.call_count, 4)

    def test_lru_evicts_oldest_when_over_capacity(self):
        for raw in range(20):
            self.item._pixmap_for(raw)
        self.assertEqual(len(self.item._pix_lru), 16)
        self.assertNotIn(0, self.item._pix_lru)
        self.assertIn(19, self.item._pix_lru)

    def test_clear_cache_invalidates_lru(self):
        self.item._pixmap_for(1)
        self.assertEqual(self.get_pixmap.call_count, 1)
        tile_cache.clear_cache()
        self.item._pixmap_for(1)
        self.assertEqual(self.get_pixmap.call_count, 2)

    def test_missing_tile_falls_back_to_placeholder(self):
        with mock.patch.object(_FakeDoc, "local_tile", return_value=None):
            pix = self.item._pixmap_for(7)
            self.assertFalse(pix.isNull())
        self.assertEqual(self.get_pixmap.call_count, 0)


class MiniCacheTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("rwmap.view.tile_layer_item.get_pixmap",
                             return_value=_tile_pixmap())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _item(self, values=None):
        return TileLayerItem(_layer(values), _FakeDoc())

    def test_eligible_is_lazy(self):
        item = self._item()
        self.assertIsNone(item._eligible_cache)
        self.assertIsInstance(item._is_mini_eligible(), bool)
        self.assertIsNotNone(item._eligible_cache)

    def test_invalidate_all_keeps_pixmap_and_defers_rebuild(self):
        item = self._item()
        item._build_mini()
        first = item._mini
        item.invalidate_all()
        self.assertIs(item._mini, first)
        self.assertTrue(item._mini_dirty)
        with mock.patch.object(item, "_build_mini") as m:
            _render_mini(item)
            m.assert_not_called()
        self.assertFalse(item._mini_dirty)

    def test_invalidate_all_result_matches_fresh_build(self):
        values = [0] * 256
        for c, r in ((2, 2), (3, 2), (5, 5)):
            values[r * 16 + c] = 1 if c < 4 else 2
        stale = self._item(values)
        stale._build_mini()
        values[2 * 16 + 2] = 3          # 整层内容更新
        stale.layer.cells[:] = values
        stale.invalidate_all()
        stale_img = _render_mini(stale)
        fresh = self._item(values)
        fresh_img = _render_mini(fresh)
        self.assertEqual(_diff(stale_img, fresh_img), 0)

    def test_local_invalidate_after_all_stays_pixel_correct(self):
        values = [0] * 256
        values[2 * 16 + 2] = 1
        item = self._item(values)
        item._build_mini()
        item.invalidate_all()
        item.layer.cells[2 * 16 + 2] = 2
        item.invalidate_cells([(2, 2)])
        self.assertFalse(item._mini_dirty)
        values[2 * 16 + 2] = 2
        fresh = self._item(values)
        self.assertEqual(_diff(_render_mini(item), _render_mini(fresh)), 0)

    def test_clear_cache_rebuilds_mini(self):
        item = self._item()
        item._build_mini()
        version = item._mini_version
        tile_cache.clear_cache()
        self.assertNotEqual(tile_cache.cache_version(), version)
        with mock.patch.object(item, "_build_mini", wraps=item._build_mini) as m:
            _render_mini(item)
            self.assertEqual(m.call_count, 1)
        self.assertEqual(item._mini_version, tile_cache.cache_version())


if __name__ == "__main__":
    unittest.main()
