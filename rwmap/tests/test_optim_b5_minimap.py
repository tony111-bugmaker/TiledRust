"""优化批次 5 回归：迷你地图局部增量重绘 + 自动映射命令合并。

- refresh_cells 的结果必须与「同内容的全量 refresh_overview」逐像素一致；
- 结构变化 / 换图集 / set_document 必须回退全量；
- DrawCommand 只在带同一 merge_id 且同层时合并，合并后 undo 一次还原全部；
- 实时自动映射语义（compute 次数 / 参数 / macro）由既有 3 个测试文件守护。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtGui import QUndoStack, QImage, QPixmap, QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from rwmap.commands.draw_command import DrawCommand  # noqa: E402
from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.tilesets import tile_cache  # noqa: E402
from rwmap.widgets.minimap import MiniMap  # noqa: E402


def _tile_pixmap():
    pix = QPixmap(20, 20)
    pix.fill(QColor(110, 170, 90))
    return pix


class _FakeDoc:
    def __init__(self, width=16, height=16):
        self.width = width
        self.height = height
        self.tile_w = 20
        self.tile_h = 20
        self.resolver = None
        self.layers = [TileLayer(width=width, height=height, name="Ground",
                                 cells=[0] * (width * height), readonly=False)]

    def tile_layers(self):
        return list(self.layers)

    def local_tile(self, raw):
        return (object(), raw)


def _diff(a: QImage, b: QImage) -> int:
    return sum(1 for y in range(a.height()) for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


class MiniMapIncrementalTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("rwmap.widgets.minimap.get_pixmap",
                             return_value=_tile_pixmap())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.doc = _FakeDoc()
        self.mm = MiniMap()
        self.mm.set_document(self.doc)

    def _change(self, cells_values):
        layer = self.doc.layers[0]
        for col, row, raw in cells_values:
            layer.cells[row * layer.width + col] = raw
        return [(c, r) for c, r, _ in cells_values]

    def test_partial_refresh_matches_full_pixelwise(self):
        changes = [(3, 3, 1), (4, 3, 2), (10, 10, 3)]
        dirty = self._change(changes)
        self.mm.note_dirty_cells(dirty)
        self.mm.refresh_cells(self.mm.take_dirty_cells())
        partial = self.mm._overview.toImage().copy()
        self.mm.refresh_overview()
        full = self.mm._overview.toImage().copy()
        self.assertEqual(_diff(partial, full), 0)

    def test_partial_refresh_at_corners_and_adjacent_cells(self):
        changes = [(0, 0, 1), (15, 15, 2), (7, 7, 3), (8, 7, 4)]
        self._change(changes)
        self.mm.refresh_cells([(c, r) for c, r, _ in changes])
        partial = self.mm._overview.toImage().copy()
        self.mm.refresh_overview()
        self.assertEqual(_diff(partial, self.mm._overview.toImage()), 0)

    def test_full_rebuild_caches_tile_pixmap_per_raw_gid(self):
        """概览每格不足 1px：同一 gid 只应解析/缩放一次，而非逐格重复。"""
        raw_a, raw_b = 11, 22
        cells = [(c, r, raw_a if (c + r) % 3 else raw_b)
                 for r in range(16) for c in range(16)]
        self._change(cells)
        with mock.patch("rwmap.widgets.minimap.get_pixmap",
                        return_value=_tile_pixmap()) as gp:
            self.mm.refresh_overview()
        self.assertEqual(gp.call_count, 2)

    def test_local_redraw_covers_far_fewer_cells_than_full(self):
        with mock.patch.object(self.mm, "_draw_cells_region",
                               wraps=self.mm._draw_cells_region) as m:
            self.mm.refresh_cells([(4, 4)])
        args = m.call_args[0]
        drawn = (args[3] - args[1] + 1) * (args[4] - args[2] + 1)
        self.assertLess(drawn, 16 * 16 // 4)

    def test_full_invalidate_forces_full_rebuild(self):
        self.mm.invalidate_full()
        with mock.patch.object(self.mm, "refresh_overview",
                               wraps=self.mm.refresh_overview) as m:
            self.mm.refresh_cells([(3, 3)])
            self.assertEqual(m.call_count, 1)
        self.assertFalse(self.mm._full_invalidate)

    def test_clear_cache_falls_back_to_full(self):
        tile_cache.clear_cache()
        with mock.patch.object(self.mm, "refresh_overview",
                               wraps=self.mm.refresh_overview) as m:
            self.mm.refresh_cells([(3, 3)])
            self.assertEqual(m.call_count, 1)
        self.assertEqual(self.mm._overview_version, tile_cache.cache_version())

    def test_set_document_clears_dirty_and_versions(self):
        self.mm.note_dirty_cells([(2, 2)])
        self.mm.set_document(_FakeDoc())
        self.assertEqual(self.mm._dirty_cells, set())
        self.assertEqual(self.mm._overview_version, tile_cache.cache_version())
        self.assertFalse(self.mm._full_invalidate)

    def test_refresh_layer_view_marks_full(self):
        from rwmap.app.main_window import MainWindow
        w = MainWindow(auto_open=False)
        w._refresh_layer_view(self.doc.layers[0])
        self.assertTrue(w.minimap_dock.minimap._full_invalidate)

    def test_refresh_layer_records_dirty_cells(self):
        from rwmap.app.main_window import MainWindow
        w = MainWindow(auto_open=False)
        w.state.doc = self.doc
        w.state.current_layer = self.doc.layers[0]
        mm = w.minimap_dock.minimap
        mm.set_document(self.doc)
        with mock.patch.object(w, "_update_title"):
            w._refresh_layer(self.doc.layers[0], [(3, 3), (4, 3)])
            self.assertEqual(mm._dirty_cells, {(3, 3), (4, 3)})
            w._refresh_layer(self.doc.layers[0], None)
        self.assertTrue(mm._full_invalidate)


class AutoMapMergeTest(unittest.TestCase):
    def setUp(self):
        self.doc = _FakeDoc()
        self.layer = self.doc.layers[0]
        self.stack = QUndoStack()
        self.refresh_calls = []

        def refresh(layer, cells=None):
            self.refresh_calls.append(list(cells or []))

        self.refresh = refresh

    def test_same_layer_commands_merge_and_undo_once(self):
        i1 = self.layer.index(3, 3)
        i2 = self.layer.index(4, 4)
        self.stack.beginMacro("绘制并自动映射")
        self.stack.push(DrawCommand(self.doc, self.layer, [(i1, 0, 126)],
                                    self.refresh, merge_id="automap"))
        self.stack.push(DrawCommand(self.doc, self.layer, [(i1, 126, 130)],
                                    self.refresh, merge_id="automap"))
        self.stack.push(DrawCommand(self.doc, self.layer, [(i2, 0, 126)],
                                    self.refresh, merge_id="automap"))
        self.stack.endMacro()
        self.assertEqual(self.stack.count(), 1)
        self.assertEqual(self.stack.command(0).childCount(), 1)
        self.assertEqual(self.layer.cells[i1], 130)
        self.assertEqual(self.layer.cells[i2], 126)
        self.stack.undo()
        self.assertEqual(self.layer.cells[i1], 0)
        self.assertEqual(self.layer.cells[i2], 0)
        self.stack.redo()
        self.assertEqual(self.layer.cells[i1], 130)
        self.assertEqual(self.layer.cells[i2], 126)

    def test_plain_commands_do_not_merge(self):
        i1 = self.layer.index(1, 1)
        i2 = self.layer.index(2, 2)
        self.stack.push(DrawCommand(self.doc, self.layer, [(i1, 0, 1)], self.refresh))
        self.stack.push(DrawCommand(self.doc, self.layer, [(i2, 0, 2)], self.refresh))
        self.assertEqual(self.stack.count(), 2)

    def test_different_layers_do_not_merge(self):
        other = TileLayer(width=16, height=16, name="Other",
                          cells=[0] * 256, readonly=False)
        self.doc.layers.append(other)
        i1 = self.layer.index(1, 1)
        i2 = other.index(2, 2)
        self.stack.beginMacro("m")
        self.stack.push(DrawCommand(self.doc, self.layer, [(i1, 0, 1)],
                                    self.refresh, merge_id="automap"))
        self.stack.push(DrawCommand(self.doc, other, [(i2, 0, 2)],
                                    self.refresh, merge_id="automap"))
        self.stack.endMacro()
        self.assertEqual(self.stack.command(0).childCount(), 2)


if __name__ == "__main__":
    unittest.main()
