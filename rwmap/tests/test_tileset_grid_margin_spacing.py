"""回归：图集编辑器网格线必须把边距/间距与瓦片本体分开表示。

旧版只在 margin + c*(tile+spacing) 画一条线，看起来像漏算了边距和间距；
且用 ASH(#2a2e34) 在深色图集上几乎看不见。现在每块瓦片左右/上下各一条
边界线，间距与边距空隙用浅色带单独标出，线色改为可见的半透明浅灰。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402


class _FakeDoc:
    image_path = str(
        (Path(__file__).resolve().parents[1] / ".." / "logs" / "selftest" /
         "bitmaps" / "units.png").resolve())
    resolver = None


class _PaintEvent:
    def rect(self):
        from PySide6.QtCore import QRect
        return QRect(0, 0, 200, 615)


class _PainterSpy:
    """记录 paintEvent 里的 drawLine / fillRect，用来断言网格几何。"""

    def __init__(self):
        self.lines = []
        self.rects = []

    def fillRect(self, *a):
        self.rects.append(tuple(a))

    def drawLine(self, x1, y1, x2, y2):
        self.lines.append((x1, y1, x2, y2))

    def setPen(self, pen):
        self.pen = pen

    def setRenderHint(self, *a):
        pass

    def drawPixmap(self, *a):
        pass

    def save(self):
        pass

    def restore(self):
        pass

    def end(self):
        pass


class AtlasGridGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.document.tileset import Tileset
        from rwmap.widgets.tileset_editor import TilesetAtlasView
        cls.Tileset = Tileset
        cls.TilesetAtlasView = TilesetAtlasView

    def _view(self, *, tile=20, spacing=1, margin=1, cols=9):
        ts = self.Tileset(
            name="units", tile_w=tile, tile_h=tile, spacing=spacing,
            margin=margin, columns=cols, image_source="bitmaps/units.png",
            image_w=200, image_h=615)
        v = self.TilesetAtlasView()
        v.set_tileset(ts, _FakeDoc())
        return v

    def test_margin_and_spacing_are_separate_bands(self):
        """边距=1 间距=1 瓦片=20：左边界 1、右边界 21、下一块左边界 22。"""
        v = self._view()
        self.assertEqual(v._margin, 1)
        self.assertEqual(v._spacing, 1)
        self.assertEqual(v._col_x(0), 1)       # 边距 1
        self.assertEqual(v._col_right(0), 21)  # 瓦片宽 20
        self.assertEqual(v._col_x(1), 22)      # 间距 1
        self.assertEqual(v._col_x(1) - v._col_right(0), 1)
        self.assertEqual(v._row_y(0), 1)
        self.assertEqual(v._row_bottom(0), 21)
        self.assertEqual(v._row_y(1), 22)

    def test_selection_rect_excludes_spacing(self):
        """选中框只框瓦片本体 20×20，不含后面的 1px 间距。"""
        v = self._view()
        self.assertEqual(v._tile_rect(0), (1, 1, 20, 20))
        self.assertEqual(v._tile_rect(1), (22, 1, 20, 20))

    def test_zero_margin_zero_spacing(self):
        """边距/间距都为 0 时，左边界 0、右边界 20、下一块左边界 20。"""
        v = self._view(spacing=0, margin=0)
        self.assertEqual(v._col_x(0), 0)
        self.assertEqual(v._col_right(0), 20)
        self.assertEqual(v._col_x(1), 20)
        self.assertEqual(v._tile_rect(0), (0, 0, 20, 20))

    def test_large_spacing(self):
        """间距=5：瓦片右边界 21、下一块左边界 26，中间留 5px。"""
        v = self._view(spacing=5)
        self.assertEqual(v._col_right(0), 21)
        self.assertEqual(v._col_x(1), 26)
        self.assertEqual(v._col_x(1) - v._col_right(0), 5)

    def test_grid_color_is_visible(self):
        from rwmap.widgets import tileset_editor
        c = tileset_editor._GRID_TILE
        # 半透明浅灰：R/G/B 明显高于旧色 ASH(#2a2e34)
        self.assertGreater(c.red(), 180)
        self.assertGreater(c.green(), 180)
        self.assertGreater(c.blue(), 180)
        self.assertLess(c.alpha(), 255)

    def test_paint_draws_both_edges_and_gap_bands(self):
        v = self._view()
        v._pix = v._pix  # 确保已加载（set_tileset 已做）
        spy = _PainterSpy()
        import rwmap.widgets.tileset_editor as te
        fake_painter = lambda widget: spy
        fake_painter.SmoothPixmapTransform = te.QPainter.SmoothPixmapTransform
        orig = te.QPainter
        te.QPainter = fake_painter
        try:
            v.paintEvent(_PaintEvent())
        finally:
            te.QPainter = orig
        # 每块瓦片左右各一条竖线，第一行即：1、21、22、42 …
        xs = sorted({ln[0] for ln in spy.lines
                     if ln[0] == ln[2] and ln[1] == 0 and ln[3] == v.atlas_h})
        self.assertIn(1, xs)     # 边距后第一块左边界
        self.assertIn(21, xs)    # 第一块右边界
        self.assertIn(22, xs)    # 第二块左边界（跳过 1px 间距）
        self.assertIn(42, xs)
        # 间距带用 fillRect 标出（第一块右 21 到第二块左 22）
        self.assertTrue(any(r[0] == 21 and r[2] == 1 for r in spy.rects),
                        "应画出 1px 的间距提示带")
        # 边距带：左侧 0..1
        self.assertTrue(any(r[0] == 0 and r[1] == 0 and r[2] == 1 for r in spy.rects),
                        "应画出 1px 的边距提示带")


if __name__ == "__main__":
    unittest.main()
