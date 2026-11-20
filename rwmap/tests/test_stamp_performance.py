"""大图章性能回归：批量刷新 + 半透明预览预渲染（离屏）。

对应两处确定性卡顿：
1. ``AppState._write_cell`` 曾经每写 1 格就 refresh 一次（QPainter 构造 /
   标脏 / 刷新标题重复 N 次），大图章拖动掉帧。
2. ``TileStampPreviewItem.paint`` 曾经每帧遍历全部格子逐格 drawPixmap，
   且不按 exposedRect 裁剪，40×40 图章实测约 10ms/帧，hover / 缩放都触发。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem  # noqa: E402

from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.tilesets import tile_cache  # noqa: E402
from rwmap.view.map_scene import TileStampPreviewItem  # noqa: E402


def _small_window():
    from rwmap.app.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(auto_open=False)
    layer = TileLayer(width=12, height=12, name="Ground",
                      cells=[0] * 144, readonly=False)
    doc = MapDocument(width=12, height=12, tile_w=20, tile_h=20,
                      layers=[layer], tilesets=[])
    w.state.doc = doc
    w.state.current_layer = layer
    w.state.profile.write_enabled = True
    w.state.brush_gid = 0
    w.state.stamp = None
    w.scene.set_document(doc)
    w.view.set_document(doc)
    return w, layer, doc


class _FakeDoc:
    """不依赖磁盘图集的最小文档：瓦片影像由测试注入固定 QPixmap。"""
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


def _render(item, exposed=None, force_fallback=False):
    img = QImage(400, 400, QImage.Format_ARGB32)
    img.fill(QColor(10, 30, 60))
    p = QPainter(img)
    opt = QStyleOptionGraphicsItem()
    opt.exposedRect = exposed if exposed is not None else QRectF(0, 0, 400, 400)
    p.setClipRect(opt.exposedRect)
    if force_fallback:
        item._tile_preview_pixmap = lambda: None
    item.paint(p, opt)
    p.end()
    return img


def _diff(a: QImage, b: QImage) -> int:
    return sum(1 for y in range(a.height()) for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


class BatchedRefreshTest(unittest.TestCase):
    """一次笔划 / 一次区域写入只刷新一次，但结果与逐格刷新完全一致。"""

    def setUp(self):
        self.w, self.layer, self.doc = _small_window()

    def test_large_stamp_refreshes_once_per_move(self):
        stamp = [[7] * 5 for _ in range(5)]
        self.w.state.tool_id = "brush"
        self.w.state.brush_gid = 0
        self.w.state.stamp = stamp
        with mock.patch.object(self.w, "_refresh_layer",
                               wraps=self.w._refresh_layer) as refresh:
            self.w._on_press(6, 6)
            self.assertEqual(refresh.call_count, 1,
                             "25 格图章不该触发 25 次刷新")
            self.assertEqual(len(refresh.call_args[0][1]), 25)
            self.w._on_move(7, 6)
            self.assertLessEqual(refresh.call_count, 2)
            self.w._on_release()
        # 写入结果仍然正确（5×5 图章以中心格对齐 (6,6) → 原点 (4,4)）
        self.assertEqual(self.layer.get(4, 4), 7)
        self.assertEqual(self.layer.get(8, 8), 7)
        self.assertEqual(self.layer.get(3, 3), 0)
        # 一条撤销记录即可还原
        self.w.state.undo_stack.undo()
        self.assertEqual(self.layer.get(4, 4), 0)

    def test_region_stroke_flushes_at_end_stroke(self):
        calls = []

        def refresh(layer, cells=None):
            calls.append(list(cells or []))

        state = self.w.state
        state.begin_region_stroke(self.layer, refresh, text="填充")
        for r in range(5):
            for c in range(5):
                state.write_region_cell(c, r, 9)
        self.assertEqual(calls, [], "区域写入期间不应逐格刷新")
        state.end_stroke()
        # 批量刷新 1 次；skip_first_redo 已消除 QUndoStack.push 首次 redo 的重复刷新。
        # 关键是每次都是整批 25 格，而不是 25 次各 1 格。
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(len(batch) == 25 for batch in calls))

    def test_on_change_keeps_working(self):
        """批量刷新不能破坏「绘制时自动映射」的 on_change 回调。"""
        seen = []
        state = self.w.state
        state.begin_stroke(self.layer, 2, 2, 3, lambda *_: None,
                           on_change=lambda bounds: seen.append(bounds))
        self.assertEqual(len(seen), 1)
        state.stroke_continue(3, 2)
        self.assertEqual(len(seen), 2)
        state.end_stroke()


class StampPreviewCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = _FakeDoc()
        self.stamp = [[7] * 5 for _ in range(5)]
        self.pix = _tile_pixmap()

    def _item(self):
        with mock.patch("rwmap.view.map_scene.get_pixmap",
                        return_value=self.pix):
            item = TileStampPreviewItem(self.doc)
            item.set_cells(self.stamp, 5, 5, "center")
            return item

    def test_pixmap_is_built_once_and_reused(self):
        with mock.patch("rwmap.view.map_scene.get_pixmap",
                        return_value=self.pix) as gp:
            item = TileStampPreviewItem(self.doc)
            item.set_cells(self.stamp, 5, 5, "center")
            first = item._tile_preview_pixmap()
            second = item._tile_preview_pixmap()
            self.assertIs(first, second)
            self.assertEqual(gp.call_count, 25)      # 只在首次构建时取瓦片
            item.set_cells(self.stamp, 6, 5, "center")  # 只移光标，内容不变
            self.assertIs(item._tile_preview_pixmap(), first)
            self.assertEqual(gp.call_count, 25)

    def test_cache_invalidates_on_stamp_change_and_clear_cache(self):
        with mock.patch("rwmap.view.map_scene.get_pixmap",
                        return_value=self.pix):
            item = TileStampPreviewItem(self.doc)
            item.set_cells(self.stamp, 5, 5, "center")
            first = item._tile_preview_pixmap()
            changed = [[8] * 5 for _ in range(5)]
            item.set_cells(changed, 5, 5, "center")
            self.assertIsNot(item._tile_preview_pixmap(), first)
            second = item._tile_preview_pixmap()
            before = tile_cache.cache_version()
            tile_cache.clear_cache()
            self.assertGreater(tile_cache.cache_version(), before)
            self.assertIsNot(item._tile_preview_pixmap(), second)

    def test_cached_and_per_cell_rendering_are_pixel_identical(self):
        for exposed in (None, QRectF(60, 60, 45, 45), QRectF(0, 0, 400, 400)):
            with mock.patch("rwmap.view.map_scene.get_pixmap",
                            return_value=self.pix):
                a = TileStampPreviewItem(self.doc)
                b = TileStampPreviewItem(self.doc)
                a.set_cells(self.stamp, 5, 5, "center")
                b.set_cells(self.stamp, 5, 5, "center")
                cached = _render(a, exposed)
                fallback = _render(b, exposed, force_fallback=True)
            self.assertEqual(_diff(cached, fallback), 0,
                             f"exposed={exposed} 时缓存改变了画面")

    def test_fallback_only_draws_visible_cells(self):
        # 超限图章回退逐格绘制时，必须按 exposedRect 裁剪，不能画全图。
        big = [[7] * 12 for _ in range(12)]
        with mock.patch("rwmap.view.map_scene._MAX_PREVIEW_PIXELS", 100), \
             mock.patch("rwmap.view.map_scene.get_pixmap",
                        return_value=self.pix) as gp:
            item = TileStampPreviewItem(self.doc)
            item.set_cells(big, 8, 8, "center")
            self.assertIsNone(item._tile_preview_pixmap())
            _render(item, QRectF(0, 0, 60, 60))
            self.assertLess(gp.call_count, 144,
                            "可见区域外仍被逐格绘制")


class StampDrawTest(unittest.TestCase):
    def test_picking_tile_or_rect_switches_back_to_brush(self):
        """选瓦片/矩形印章必须回到画笔，否则 MapView 不派发落笔事件。"""
        w, layer, _doc = _small_window()
        w._set_object_tool("rect")
        self.assertEqual(w.view.object_mode, "rect")

        w._on_gid(7)
        self.assertEqual(w.state.tool_id, "brush")
        self.assertEqual(w.view.object_mode, "brush")
        self.assertEqual(w.state.brush_gid, 7)

        w._set_object_tool("rect")
        w._on_stamp([[7, 8], [9, 10]])
        self.assertEqual(w.view.object_mode, "brush")
        self.assertEqual(w.state.stamp, [[7, 8], [9, 10]])

        w._on_press(5, 5)
        w._on_release()
        self.assertEqual([layer.get(c, r) for r in (4, 5) for c in (4, 5)],
                         [7, 8, 9, 10])

    def test_automap_toggle_restores_set_layer_display(self):
        """绘制时自动地图关闭后，set 层不该保持半透明可见。"""
        from rwmap.document.layer import TileLayer
        from rwmap.document.map_document import MapDocument

        w, ground, _ = _small_window()
        marker = TileLayer(width=12, height=12, name="set",
                           cells=[0] * 144, readonly=False)
        marker.visible = False
        marker.opacity = 1.0
        doc = MapDocument(width=12, height=12, tile_w=20, tile_h=20,
                          layers=[ground, marker], tilesets=[])
        w.state.doc = doc
        w.state.current_layer = ground
        w.scene.set_document(doc)

        with mock.patch.object(w, "_load_automap_rules", return_value=True):
            w._toggle_automap(True)
            self.assertTrue(marker.visible)
            self.assertAlmostEqual(marker.opacity, 0.35)
            self.assertIs(w.state.current_layer, marker)

            w._toggle_automap(False)
            self.assertFalse(marker.visible)
            self.assertAlmostEqual(marker.opacity, 1.0)
            self.assertIs(w.state.current_layer, ground)


if __name__ == "__main__":
    unittest.main()
