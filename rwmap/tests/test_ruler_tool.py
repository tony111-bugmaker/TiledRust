"""回归：标尺工具（两点测距 / 分段均分 / 多段平均 / 右键清除 / 切工具保留）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.view.ruler_overlay import (  # noqa: E402
    cell_distance, segment_average, split_cells,
)
from rwmap.widgets.object_toolbar import TOOLS  # noqa: E402

_app = QApplication.instance() or QApplication([])


class RulerMathTest(unittest.TestCase):
    def test_distance_includes_both_cells(self):
        self.assertEqual(cell_distance(2, 3, 2, 3), 1)
        self.assertEqual(cell_distance(2, 3, 9, 3), 8)
        self.assertEqual(cell_distance(2, 3, 2, 8), 6)
        self.assertEqual(cell_distance(2, 3, 5, 6), 4)

    def test_split_cells_even_division(self):
        self.assertEqual(split_cells(0, 0, 9, 0, 1), [])
        self.assertEqual(split_cells(0, 0, 9, 0, 2), [(5, 0)])
        self.assertEqual(split_cells(0, 0, 9, 0, 3), [(3, 0), (6, 0)])
        self.assertEqual(split_cells(0, 0, 9, 0, 4), [(2, 0), (5, 0), (7, 0)])

    def test_segment_average(self):
        # 3 段分别 3/5/7 格，分 1 段 → 平均 5 格/段
        segs = [(0, 0, 2, 0), (0, 0, 4, 0), (0, 0, 6, 0)]
        self.assertAlmostEqual(segment_average(segs, 1), 5.0)
        self.assertAlmostEqual(segment_average(segs, 2), 2.5)
        self.assertEqual(segment_average([], 1), 0.0)


class RulerToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from rwmap.document.layer import TileLayer
        from rwmap.document.map_document import MapDocument
        self.w = MainWindow(auto_open=False)
        layer = TileLayer(width=20, height=20, name="Ground",
                          cells=[0] * 400, readonly=False)
        doc = MapDocument(width=20, height=20, tile_w=20, tile_h=20,
                          layers=[layer], tilesets=[])
        self.w.state.doc = doc
        self.w.state.current_layer = layer
        self.w.scene.doc = doc
        self.w.view.set_document(doc)
        self.w.resize(1100, 760)
        QApplication.processEvents()

    def tearDown(self):
        self.w.close()
        QApplication.processEvents()

    def _cell_pixel(self, col, row):
        doc = self.w.state.doc
        return self.w.view.mapFromScene(
            QPointF(col * doc.tile_w + doc.tile_w / 2,
                    row * doc.tile_h + doc.tile_h / 2))

    def _click(self, col, row, button=Qt.LeftButton):
        pos = self._cell_pixel(col, row)
        self.w.view.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, pos, button, button, Qt.NoModifier))
        self.w.view.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, pos, button, button, Qt.NoModifier))
        QApplication.processEvents()

    def _move(self, col, row):
        pos = self._cell_pixel(col, row)
        self.w.view.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, pos, Qt.NoButton,
            Qt.LeftButton, Qt.NoModifier))
        QApplication.processEvents()

    # ---- 工具栏 ----
    def test_tool_and_spinbox_exist(self):
        ids = [t[0] for t in TOOLS]
        self.assertEqual(ids[ids.index("select") + 1], "ruler")
        self.assertIn("ruler", self.w.object_toolbar.actions_by_id)
        spin = self.w.object_toolbar._segments_spin
        self.assertIsNotNone(spin)
        self.assertEqual(spin.value(), 1)
        self.assertEqual(spin.minimum(), 1)

    def test_segments_controls_visible_only_for_ruler(self):
        bar = self.w.object_toolbar
        # 初始（非标尺）隐藏
        self.assertFalse(bar._ruler_label_action.isVisible())
        self.assertFalse(bar._ruler_spin_action.isVisible())
        self.w._set_object_tool("ruler")
        self.assertTrue(bar._ruler_label_action.isVisible())
        self.assertTrue(bar._ruler_spin_action.isVisible())
        self.w._set_object_tool("select")
        self.assertFalse(bar._ruler_label_action.isVisible())
        self.assertFalse(bar._ruler_spin_action.isVisible())
        self.w._set_object_tool("brush")
        self.assertFalse(bar._ruler_spin_action.isVisible())

    def test_segments_spinbox_text_visible(self):
        # 回归：局部 QSS 的 border 会把 NoButtons 数字框内部 QLineEdit 压成 1px
        spin = self.w.object_toolbar._segments_spin
        self.assertGreater(spin.lineEdit().width(), 10,
                           "数字框内部行编辑被 QSS 压扁，数字看不见")
        self.assertEqual(spin.lineEdit().text(), "1")

    # ---- 两点测距 ----
    def test_two_clicks_measure_one_segment(self):
        self.w._set_object_tool("ruler")
        self._click(2, 3)
        self.assertEqual(len(self.w._ruler_segments), 0)
        self._click(9, 3)
        self.assertEqual(self.w._ruler_segments, [(2, 3, 9, 3)])
        self.assertIn("8 格", self.w.statusBar().currentMessage())
        self.assertIsNotNone(self.w.scene._ruler)

    def test_multi_segment_marking(self):
        self.w._set_object_tool("ruler")
        self._click(1, 1)
        self._click(4, 1)
        self._click(6, 6)
        self._click(6, 12)
        self.assertEqual(self.w._ruler_segments,
                         [(1, 1, 4, 1), (6, 6, 6, 12)])
        self.assertIn("已标 2 段", self.w.statusBar().currentMessage())

    def test_pending_is_point_only_no_preview_line(self):
        """点第一格只留一个点：鼠标移动不画临时线、不改状态；第二格才连线。"""
        self.w._set_object_tool("ruler")
        self._click(2, 3)
        ruler = self.w.scene._ruler
        self.assertEqual(ruler.segments, [])
        self.assertEqual(ruler.pending, (2, 3))

        # 绘制层面：点第一点后只有 (2,3) 一个方块点，不能拉出任何长线。
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        def blue_pixels():
            img = QImage(400, 300, QImage.Format_RGB32)
            img.fill(0xFF14181E)
            p = QPainter(img)
            self.w.scene.render(p, QRectF(0, 0, 400, 300), QRectF(0, 0, 400, 300))
            p.end()
            return [(x, y) for y in range(img.height()) for x in range(img.width())
                    if (lambda c: c.blue() > 140 and c.blue() > c.red() + 50)(
                        img.pixelColor(x, y))]
        pts = blue_pixels()
        self.assertGreater(len(pts), 0, "起点方块未画出")
        # 所有蓝色像素应落在起点格 (2,3) 中心 (50,70) 附近 ±15px，不能 spread
        self.assertTrue(all(abs(x - 50) <= 15 and abs(y - 70) <= 15 for x, y in pts),
                        f"第一点却画出了预览线，蓝色像素范围过大: "
                        f"x[{min(p[0] for p in pts)},{max(p[0] for p in pts)}] "
                        f"y[{min(p[1] for p in pts)},{max(p[1] for p in pts)}]")

        # 移动鼠标到别处：不得产生草稿线、不得改 pending、不得加段
        self._move(15, 15)
        self.assertEqual(ruler.segments, [])
        self.assertEqual(ruler.pending, (2, 3))
        self.assertTrue(all(abs(x - 50) <= 15 and abs(y - 70) <= 15
                            for x, y in blue_pixels()), "鼠标移动画出了预览线")

        # 第二格落定成线
        self._click(9, 3)
        self.assertEqual(ruler.segments, [(2, 3, 9, 3)])
        self.assertIsNone(ruler.pending)

        # 第三格又是单独一个点，不影响已落定的第一段
        self._click(6, 6)
        self.assertEqual(ruler.segments, [(2, 3, 9, 3)])
        self.assertEqual(ruler.pending, (6, 6))
        self._move(18, 18)
        self.assertEqual(ruler.segments, [(2, 3, 9, 3)])

        # 第四格落定第二段，第一段位置不变
        self._click(6, 12)
        self.assertEqual(ruler.segments, [(2, 3, 9, 3), (6, 6, 6, 12)])

    # ---- 分段 ----
    def test_segments_spin_updates_overlay_and_status(self):
        self.w._set_object_tool("ruler")
        self._click(0, 0)
        self._click(9, 0)
        self.w.object_toolbar._segments_spin.setValue(3)
        QApplication.processEvents()
        self.assertEqual(self.w.scene._ruler.parts, 3)
        self.assertEqual(split_cells(0, 0, 9, 0, 3), [(3, 0), (6, 0)])
        self.assertIn("分 3 段", self.w.statusBar().currentMessage())

    # ---- 持久 / 右键清除 ----
    def test_ruler_survives_tool_switch(self):
        self.w._set_object_tool("ruler")
        self._click(2, 2)
        self._click(7, 2)
        self.w._set_object_tool("select")
        self.w._set_object_tool("brush")
        self.assertEqual(self.w._ruler_segments, [(2, 2, 7, 2)])
        self.assertIsNotNone(self.w.scene._ruler)
        self.assertEqual(self.w.scene._ruler.segments, [(2, 2, 7, 2)])

    def test_right_click_only_clears_when_ruler_active(self):
        self.w._set_object_tool("ruler")
        self._click(2, 2)
        self._click(7, 2)
        # 非标尺工具右键不清
        self.w._set_object_tool("select")
        self._click(3, 3, Qt.RightButton)
        self.assertEqual(len(self.w._ruler_segments), 1)
        # 标尺工具右键清
        self.w._set_object_tool("ruler")
        self._click(3, 3, Qt.RightButton)
        self.assertEqual(self.w._ruler_segments, [])
        self.assertIsNone(self.w._ruler_pending)
        self.assertEqual(self.w.scene._ruler.segments, [])

    def test_overlay_paints_line_and_marks(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        self.w._set_object_tool("ruler")
        self._click(2, 3)
        self._click(9, 3)
        self.w.object_toolbar._segments_spin.setValue(3)
        QApplication.processEvents()
        img = QImage(400, 300, QImage.Format_RGB32)
        img.fill(0xFF14181E)
        p = QPainter(img)
        self.w.scene.render(p, QRectF(0, 0, 400, 300), QRectF(0, 0, 400, 300))
        p.end()
        blue = amber = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if c.blue() > 140 and c.blue() > c.red() + 50:
                    blue += 1
                if c.red() > 200 and c.green() > 150 and c.blue() < 120:
                    amber += 1
        self.assertGreater(blue, 20, "测线未画出")
        self.assertGreater(amber, 4, "等分标记未画出")

    def test_label_rects_inside_dirty_region(self):
        """回归：文字标签矩形必须落在脏区内，否则旧文字擦不掉、反复叠加发虚。"""
        from PySide6.QtCore import QPointF
        from rwmap.view.ruler_overlay import (
            segment_label_rect, segment_label_text,
            summary_label_rect, summary_label_text,
        )
        r = self.w.scene.ruler_overlay()
        segs = [(0, 0, 9, 0), (2, 8, 2, 18)]   # 一横一竖：覆盖两种标签放置
        r.set_segments(segs, 3)
        QApplication.processEvents()
        dirty = r._content_rect()
        self.assertFalse(dirty.isEmpty())

        for c0, rr0, c1, rr1 in segs:
            p0, p1 = r._cell_center(c0, rr0), r._cell_center(c1, rr1)
            text = segment_label_text(c0, rr0, c1, rr1, 3)
            lab = segment_label_rect(p0, p1, text, r._scale)
            self.assertTrue(dirty.contains(lab),
                            f"单段标签 {text!r} 矩形 {lab} 超出脏区 {dirty}")

        last = r._seg_rect(segs[-1])
        s = 1.0 / max(abs(r._scale), 0.05)
        anchor = QPointF(last.center().x(), last.bottom() - 6 * s)
        text = summary_label_text(len(segs), segs, 3)
        lab = summary_label_rect(anchor, text, r._scale)
        self.assertTrue(dirty.contains(lab),
                        f"总计标签 {text!r} 矩形 {lab} 超出脏区 {dirty}")

    def test_right_click_clears_pending_too(self):
        self.w._set_object_tool("ruler")
        self._click(2, 2)
        self.assertIsNotNone(self.w._ruler_pending)
        self._click(3, 3, Qt.RightButton)
        self.assertIsNone(self.w._ruler_pending)


if __name__ == "__main__":
    unittest.main()
