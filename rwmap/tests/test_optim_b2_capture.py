"""优化批次 2 回归：右键抓区域去重 + 图章纯移动跳过几何重建。

- map_view：同一格内连续 mouseMove 不再重复 emit captureRectChanged；
- main_window：同区域同层复用已读出的 2D 虚影，编辑后缓存失效；
- map_scene：内容身份不变且整块在图内 / 图外时，纯移动可跳过
  _split_blocks()；跨边界态恒重建（蓝 / 红拆分依赖 ox/oy）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem  # noqa: E402

from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.view.map_scene import TileStampPreviewItem  # noqa: E402

# 单独跑本模块时也要先建应用实例（QPixmap 需要 QGuiApplication）。
_app = QApplication.instance() or QApplication([])


def _window():
    from rwmap.app.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow(auto_open=False)
    cells = [0] * 100
    for r in range(3):
        for c in range(3):
            cells[r * 10 + c] = 1 + r * 3 + c
    layer = TileLayer(width=10, height=10, name="Ground",
                      cells=cells, readonly=False)
    doc = MapDocument(width=10, height=10, tile_w=20, tile_h=20,
                      layers=[layer], tilesets=[])
    w.state.doc = doc
    w.state.current_layer = layer
    w.scene.doc = doc
    w.state.profile.write_enabled = True
    w.state.brush_gid = 0
    w.state.stamp = None
    return w, layer, doc


class _FakeDoc:
    width = 20
    height = 20
    tile_w = 20
    tile_h = 20
    resolver = None

    def local_tile(self, raw_gid):
        return (object(), raw_gid)


def _tile_pixmap():
    pix = QPixmap(20, 20)
    pix.fill(QColor(200, 100, 50))
    return pix


def _render(item, size=600):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(10, 30, 60))
    p = QPainter(img)
    exposed = QRectF(0, 0, size, size)
    opt = QStyleOptionGraphicsItem()
    opt.exposedRect = exposed
    p.setClipRect(exposed)
    item.paint(p, opt)
    p.end()
    return img


def _diff(a: QImage, b: QImage) -> int:
    return sum(1 for y in range(a.height()) for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


class CaptureRectDedupTest(unittest.TestCase):
    def setUp(self):
        self.w, self.layer, self.doc = _window()
        self.w.view.set_document(self.doc)
        self.rects = []
        self.w.view.captureRectChanged.connect(self.rects.append)

    def _cell_pixel(self, col, row):
        return self.w.view.mapFromScene(
            QPointF(col * self.doc.tile_w + 1, row * self.doc.tile_h + 1))

    def _press(self, col, row):
        self.w.view.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, self._cell_pixel(col, row),
            Qt.RightButton, Qt.RightButton, Qt.NoModifier))

    def _move(self, col, row):
        self.w.view.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, self._cell_pixel(col, row),
            Qt.NoButton, Qt.RightButton, Qt.NoModifier))

    def _release(self, col, row):
        self.w.view.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, self._cell_pixel(col, row),
            Qt.RightButton, Qt.NoButton, Qt.NoModifier))

    def test_same_cell_moves_emit_only_once(self):
        self._press(2, 2)
        for _ in range(4):
            self._move(5, 5)
        self.assertEqual(self.rects, [(2, 2, 5, 5)],
                         "同一格内连续移动应只发一次区域")
        self._move(6, 5)
        self.assertEqual(self.rects, [(2, 2, 5, 5), (2, 2, 6, 5)])
        self._release(6, 5)
        self.assertIsNone(self.rects[-1])
        self.assertIsNone(self.w.view._rcap_last_region)


class CapturePreviewCacheTest(unittest.TestCase):
    def setUp(self):
        self.w, self.layer, self.doc = _window()

    def test_same_region_reuses_cells(self):
        with mock.patch.object(self.w.scene, "set_capture_preview") as m:
            self.w._on_capture_rect((2, 2, 5, 5))
            self.assertEqual(m.call_count, 1)
            self.w._on_capture_rect((2, 2, 5, 5))
            self.assertEqual(m.call_count, 1)
            self.w._on_capture_rect((2, 2, 6, 5))
            self.assertEqual(m.call_count, 2)
        self.assertEqual(self.w._capture_cache[0], (2, 2, 6, 5))

    def test_edit_invalidates_cached_live_layer(self):
        self.w._on_capture_rect((2, 2, 5, 5))
        first = self.w._capture_cache[2]
        self.layer.set(3, 3, 123)
        self.w._refresh_layer(self.layer, [(3, 3)])
        self.assertIsNone(self.w._capture_cache)
        self.w._on_capture_rect((2, 2, 5, 5))
        self.assertIsNot(self.w._capture_cache[2], first)
        self.assertEqual(self.w._capture_cache[2][1][1], 123)

    def test_none_clears_cache(self):
        self.w._on_capture_rect((2, 2, 5, 5))
        self.w._on_capture_rect(None)
        self.assertIsNone(self.w._capture_cache)

    def test_switch_layer_does_not_reuse_old_snapshot(self):
        self.w._on_capture_rect((2, 2, 5, 5))
        other = TileLayer(width=10, height=10, name="Other",
                          cells=[9] * 100, readonly=False)
        self.w.state.current_layer = other
        with mock.patch.object(self.w.scene, "set_capture_preview") as m:
            self.w._on_capture_rect((2, 2, 5, 5))
            self.assertEqual(m.call_count, 1)
        self.assertEqual(self.w._capture_cache[2][0][0], 9)


class StampPureMoveTest(unittest.TestCase):
    def setUp(self):
        self.item = TileStampPreviewItem(_FakeDoc())
        self.patcher = mock.patch("rwmap.view.map_scene.get_pixmap",
                                  return_value=_tile_pixmap())
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_in_map_pure_move_skips_rebuild_and_keeps_pixmap(self):
        stamp = [[1] * 5 for _ in range(5)]
        self.item.set_cells(stamp, 5, 5, "center")
        self.assertEqual(self.item._tri_state, "in")
        first = self.item._tile_preview_pixmap()
        with mock.patch.object(self.item, "_split_blocks") as m:
            self.item.set_cells(stamp, 6, 5, "center")
            self.assertEqual(m.call_count, 0)
        self.assertIs(self.item._tile_preview_pixmap(), first)
        self.assertEqual((self.item.col, self.item.row), (6, 5))

    def test_out_of_map_pure_move_also_skips(self):
        stamp = [[1] * 3 for _ in range(3)]
        self.item.set_cells(stamp, 30, 30, "center")
        self.assertEqual(self.item._tri_state, "out")
        with mock.patch.object(self.item, "_split_blocks") as m:
            self.item.set_cells(stamp, 31, 30, "center")
            self.assertEqual(m.call_count, 0)

    def test_cross_state_always_rebuilds(self):
        stamp = [[1] * 4 for _ in range(4)]
        self.item.set_cells(stamp, 18, 5, "topleft")   # 右侧 2 列越界
        self.assertEqual(self.item._tri_state, "cross")
        blue_before = set(self.item._blue_cells)
        red_before = set(self.item._red_cells)
        with mock.patch.object(self.item, "_split_blocks",
                               wraps=self.item._split_blocks) as m:
            self.item.set_cells(stamp, 17, 5, "topleft")  # 仍是 cross，但少越界一列
            self.assertEqual(m.call_count, 1)
        self.assertNotEqual(set(self.item._blue_cells), blue_before)
        self.assertNotEqual(set(self.item._red_cells), red_before)

    def test_pure_move_still_dirties_old_and_new(self):
        from rwmap.view.map_scene import _PREVIEW_DIRTY_MARGIN
        stamp = [[1] * 3 for _ in range(3)]
        self.item.set_cells(stamp, 5, 5, "center")
        old = QRectF(self.item._content_rect())
        rects = []
        with mock.patch.object(self.item, "update",
                               side_effect=lambda r: rects.append(QRectF(r))):
            self.item.set_cells(stamp, 7, 5, "center")
        new = QRectF(self.item._content_rect())
        # 脏区必须外扩：轮廓线压在内容矩形边界上，外侧半线宽也在脏区内，
        # 否则 MinimalViewportUpdate 下旧线擦不掉（见 test_preview_no_ghost）。
        self.assertTrue(any(r.contains(old) for r in rects),
                        "旧位置未被完整标脏")
        self.assertTrue(any(r.contains(new) for r in rects),
                        "新位置未被完整标脏")
        self.assertTrue(all(r.width() >= old.width() + 2 * _PREVIEW_DIRTY_MARGIN - 1e-6
                            for r in rects), "脏区没有按描边宽度外扩")

    def test_skip_vs_forced_rebuild_is_pixel_identical(self):
        cases = [
            ("in_full", [[1] * 4 for _ in range(4)], 6, 6, "center"),
            ("in_hole", [[1, 1, 1], [1, 0, 1], [1, 1, 1]], 6, 6, "center"),
            ("out_full", [[1] * 3 for _ in range(3)], 28, 6, "center"),
            ("cross", [[1] * 4 for _ in range(4)], 18, 5, "topleft"),
        ]
        for _name, stamp, col, row, mode in cases:
            with self.subTest(stamp=_name):
                optimized = TileStampPreviewItem(_FakeDoc())
                forced = TileStampPreviewItem(_FakeDoc())
                optimized.set_cells(stamp, col, row, mode)
                forced.set_cells(stamp, col, row, mode)
                optimized.set_cells(stamp, col + 1, row, mode)
                forced.set_cells(stamp, col + 1, row, mode)
                if optimized._tri_state in ("in", "out"):
                    forced._split_blocks()     # 模拟旧实现每次都重建
                self.assertEqual(_diff(_render(optimized), _render(forced)), 0)


if __name__ == "__main__":
    unittest.main()
