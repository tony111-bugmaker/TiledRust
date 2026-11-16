"""优化批次 3 回归：网格线零视觉优化。

核心护栏：把改动前的网格绘制实现作为参考，与优化后的
GridOverlay.paint 在多种 exposedRect / 变换下逐像素比对，必须完全一致。
另验证「0.5 缩放判断留在 paint 内」：view 缩到低倍率后 scene.render()
仍要画网格（若误用持久 setVisible 会丢网格线）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem  # noqa: E402

from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.view import grid_overlay as grid_module  # noqa: E402
from rwmap.view.grid_overlay import GridOverlay  # noqa: E402

# 单独跑本模块时也要先建应用实例（QGraphicsItem / QPainter 路径）。
_app = QApplication.instance() or QApplication([])

_BG = QColor(24, 28, 34)
_W = 400


def _doc():
    return MapDocument(width=16, height=16, tile_w=20, tile_h=20,
                       layers=[TileLayer(width=16, height=16, name="Ground",
                                         cells=[0] * 256, readonly=False)],
                       tilesets=[])


def _legacy_paint(painter, grid, exposed):
    """改动前 grid_overlay.py:38-57 的逐条 drawLine 实现（参考）。"""
    doc = grid.doc
    scale = painter.worldTransform().m11()
    if not grid.show_grid or scale < 0.5:
        return
    pen = QPen(QColor(255, 255, 255, 35))
    pen.setWidth(0)
    painter.setPen(pen)
    width = doc.width * doc.tile_w
    height = doc.height * doc.tile_h
    col0 = max(0, int(exposed.left() // doc.tile_w))
    col1 = min(doc.width, int(exposed.right() // doc.tile_w) + 1)
    row0 = max(0, int(exposed.top() // doc.tile_h))
    row1 = min(doc.height, int(exposed.bottom() // doc.tile_h) + 1)
    for col in range(col0, col1 + 1):
        x = col * doc.tile_w
        painter.drawLine(x, max(0, exposed.top()), x, min(height, exposed.bottom()))
    for row in range(row0, row1 + 1):
        y = row * doc.tile_h
        painter.drawLine(max(0, exposed.left()), y, min(width, exposed.right()), y)


def _render(draw, exposed, scale=1.0):
    img = QImage(_W, _W, QImage.Format_ARGB32)
    img.fill(_BG)
    p = QPainter(img)
    if scale != 1.0:
        p.setWorldTransform(QTransform(scale, 0, 0, scale, 0, 0))
    opt = QStyleOptionGraphicsItem()
    opt.exposedRect = exposed
    draw(p, opt)
    p.end()
    return img


def _diff(a: QImage, b: QImage) -> int:
    return sum(1 for y in range(a.height()) for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


class GridPixelEquivalenceTest(unittest.TestCase):
    def setUp(self):
        self.grid = GridOverlay(_doc())

    def test_grid_lines_match_legacy_paint_pixelwise(self):
        rects = [
            QRectF(0, 0, 200, 200),
            QRectF(60, 60, 45, 45),
            QRectF(150, 150, 300, 300),      # 部分越出地图
            QRectF(0, 0, 320, 320),         # 整张地图
        ]
        for exposed in rects:
            for scale in (1.0, 0.6, 2.0):
                with self.subTest(rect=tuple(exposed.getRect()), scale=scale):
                    legacy = _render(
                        lambda p, o: _legacy_paint(p, self.grid, o.exposedRect),
                        exposed, scale)
                    optimized = _render(
                        lambda p, o: self.grid.paint(p, o), exposed, scale)
                    self.assertEqual(_diff(legacy, optimized), 0)

    def test_low_scale_does_not_draw_grid(self):
        exposed = QRectF(0, 0, 200, 200)
        img = _render(lambda p, o: self.grid.paint(p, o), exposed, 0.4)
        non_bg = sum(1 for y in range(_W) for x in range(_W)
                     if img.pixel(x, y) != _BG.rgb())
        self.assertEqual(non_bg, 0)

    def test_pen_is_cosmetic_and_unchanged(self):
        pen = grid_module._GRID_PEN
        self.assertEqual(pen.width(), 0)
        self.assertEqual(pen.color().red(), 255)
        self.assertEqual(pen.color().green(), 255)
        self.assertEqual(pen.color().blue(), 255)
        self.assertEqual(pen.color().alpha(), 35)
        self.assertEqual(self.grid.zValue(), 10000)

    def test_paint_does_not_construct_pens(self):
        exposed = QRectF(0, 0, 200, 200)
        with mock.patch.object(grid_module, "QPen") as pen_cls:
            _render(lambda p, o: self.grid.paint(p, o), exposed)
            pen_cls.assert_not_called()


class GridVisibilityTest(unittest.TestCase):
    def setUp(self):
        from rwmap.app.main_window import MainWindow
        QApplication.instance() or QApplication([])
        self.w = MainWindow(auto_open=False)
        self.doc = _doc()
        self.w.state.doc = self.doc
        self.w.state.current_layer = self.doc.layers[0]
        self.w.scene.set_document(self.doc)
        self.w.view.set_document(self.doc)

    def test_toggle_uses_setvisible_without_scene_update(self):
        with mock.patch.object(self.w.scene, "update") as m:
            self.w._toggle_grid(False)
            self.assertEqual(m.call_count, 0)
        self.assertFalse(self.w.scene.grid.isVisible())
        self.w._toggle_grid(True)
        self.assertTrue(self.w.scene.grid.isVisible())

    def test_scene_render_draws_grid_even_after_low_view_scale(self):
        """裁决 A：view 缩到 <0.5 后，scene.render() 仍必须画出网格线。"""
        self.w.view.scale(0.25, 0.25)
        img = QImage(200, 200, QImage.Format_ARGB32)
        img.fill(_BG)
        p = QPainter(img)
        self.w.scene.render(p, QRectF(0, 0, 200, 200), QRectF(0, 0, 200, 200))
        p.end()
        non_bg = sum(1 for y in range(200) for x in range(200)
                     if img.pixel(x, y) != _BG.rgb())
        self.assertGreater(non_bg, 0, "网格线不应因 view 低倍率而消失")

    def test_hover_still_changes_no_pixels(self):
        before = QImage(200, 200, QImage.Format_ARGB32)
        before.fill(_BG)
        p = QPainter(before)
        self.w.scene.render(p, QRectF(0, 0, 200, 200), QRectF(0, 0, 200, 200))
        p.end()
        self.w.scene.set_cursor_cell(3, 3)
        after = QImage(200, 200, QImage.Format_ARGB32)
        after.fill(_BG)
        p = QPainter(after)
        self.w.scene.render(p, QRectF(0, 0, 200, 200), QRectF(0, 0, 200, 200))
        p.end()
        self.assertEqual(_diff(before, after), 0)


if __name__ == "__main__":
    unittest.main()
