"""悬停黄色单格框已移除，其它框保持不动（离屏像素级回归）。

用户要求：地图编辑时鼠标悬停跟随的那一个黄色格框去掉，但**只去这一个**；
右键拖动抓取 / 多格图章的蓝色框、对象层黄色对象框、矩形选框都要保留。

- GridOverlay 单独渲染：set_cell 前后逐像素一致、没有黄色像素，网格线仍在。
- 完整窗口：_on_hover 前后场景渲染逐像素一致（悬停不再带来任何画面变化）。
- 多格图章 hover 仍有蓝色像素（右键拖动后的蓝框没被误删）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsScene  # noqa: E402

from rwmap.view.grid_overlay import GridOverlay  # noqa: E402

_BG = QColor(12, 24, 36)


def _is_yellow(c) -> bool:
    return (c.red() > 200 and c.green() > 165 and c.blue() < 135
            and c.red() - c.blue() > 80)


def _is_blue(c) -> bool:
    # 蓝色描边 (47,134,220) / 半透明填充，抗锯齿后色相仍偏蓝。
    return c.blue() > 110 and c.blue() > c.red() + 60


def _count(img: QImage, predicate) -> int:
    return sum(1 for y in range(img.height()) for x in range(img.width())
               if predicate(img.pixelColor(x, y)))


def _diff(a: QImage, b: QImage) -> int:
    return sum(1 for y in range(a.height()) for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


def _blank(w=200, h=200) -> QImage:
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(_BG)
    return img


def _render(scene, w=200, h=200) -> QImage:
    img = _blank(w, h)
    p = QPainter(img)
    scene.render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
    p.end()
    return img


class GridOverlayHoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = SimpleNamespace(width=10, height=10, tile_w=20, tile_h=20)
        self.grid = GridOverlay(self.doc)
        self.scene = QGraphicsScene()
        self.scene.addItem(self.grid)

    def test_grid_lines_survive_but_no_yellow_highlight(self):
        # 先自检判定函数：原高亮色 (255,220,80) 必须能被认成黄色。
        self.assertTrue(_is_yellow(QColor(255, 220, 80)))
        self.assertTrue(_is_blue(QColor(47, 134, 220)))
        before = _render(self.scene)
        # 网格线仍在（画面不是一片纯底色）
        bg = _BG.rgb()
        self.assertGreater(
            sum(1 for y in range(before.height())
                for x in range(before.width())
                if before.pixel(x, y) != bg),
            0, "网格线被一起删掉了")
        self.assertEqual(_count(before, _is_yellow), 0)

        self.grid.set_cell(3, 3)
        QApplication.processEvents()
        after = _render(self.scene)

        self.assertEqual(self.grid.cell, (3, 3))   # 旧接口仍可用
        self.assertEqual(_diff(before, after), 0,
                         "hover 仍画出了黄色单格高亮框")
        self.assertEqual(_count(after, _is_yellow), 0)

    def test_many_hover_moves_never_draw_highlight(self):
        first = _render(self.scene)
        for col, row in ((1, 1), (5, 7), (9, 9), (0, 0)):
            self.grid.set_cell(col, row)
            QApplication.processEvents()
            self.assertEqual(_diff(first, _render(self.scene)), 0)


class HoverViewportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        from rwmap.document.layer import TileLayer
        from rwmap.document.map_document import MapDocument

        w = MainWindow(auto_open=False)
        layer = TileLayer(width=10, height=10, name="Ground",
                          cells=[0] * 100, readonly=False)
        doc = MapDocument(width=10, height=10, tile_w=20, tile_h=20,
                          layers=[layer], tilesets=[])
        w.state.doc = doc
        w.state.current_layer = layer
        w.state.stamp = None
        w.state.brush_gid = 0
        w.scene.set_document(doc)
        w.view.set_document(doc)
        w.show()
        QApplication.processEvents()
        cls.w, cls.layer, cls.doc = w, layer, doc

    def test_hover_changes_no_pixels(self):
        before = _render(self.w.scene)
        self.w._on_hover(3, 3, 0)
        QApplication.processEvents()
        after = _render(self.w.scene)
        self.assertEqual(_diff(before, after), 0,
                         "悬停仍在地图上画了高亮框")
        self.assertEqual(_count(after, _is_yellow), 0)
        self.assertEqual(self.w.scene.grid.cell, (3, 3))

    def test_multicell_stamp_blue_frame_still_drawn(self):
        # 只去黄框：右键拖动 / 多格图章的蓝色框必须保留。
        self.w.state.tool_id = "brush"
        self.w.state.stamp = [[1, 2], [3, 4]]
        try:
            self.w._on_hover(4, 4, 0)
            QApplication.processEvents()
            img = _render(self.w.scene)
            self.assertGreater(_count(img, _is_blue), 0,
                               "多格图章的蓝色框被误删了")
        finally:
            self.w.state.stamp = None
            self.w.scene.clear_stamp_preview()


if __name__ == "__main__":
    unittest.main()
