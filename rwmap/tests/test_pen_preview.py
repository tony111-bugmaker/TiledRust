"""钢笔路径预览层：恒定 boundingRect、局部脏区、蓝色渲染、复用 item（离屏）。"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from rwmap.tools.pen_tool import Anchor, PenPath


def _build():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
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
    w.scene.doc = doc
    return w, layer, doc


def _sample_path():
    a = Anchor.corner(30.0, 40.0)
    a.set_symmetric(20.0, -25.0)
    b = Anchor.corner(140.0, 110.0)
    b.set_symmetric(-30.0, 10.0)
    return PenPath([a, b])


class PenPreviewTest(unittest.TestCase):

    def setUp(self):
        self.w, self.layer, self.doc = _build()

    def _render(self):
        tw, th = self.doc.tile_w, self.doc.tile_h
        W, H = self.doc.width * tw, self.doc.height * th
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        self.w.scene.render(p, QRectF(0, 0, W, H), QRectF(0, 0, W, H))
        p.end()
        return img

    @staticmethod
    def _is_blue(c):
        return (abs(c.red() - 47) < 60
                and abs(c.green() - 134) < 60
                and abs(c.blue() - 220) < 60)

    def _blue_count(self, img):
        return sum(1 for y in range(img.height()) for x in range(img.width())
                   if self._is_blue(img.pixelColor(x, y)))

    def test_bounding_rect_stable_full_map(self):
        full = QRectF(0, 0, self.doc.width * self.doc.tile_w,
                      self.doc.height * self.doc.tile_h)
        self.w.scene.set_pen_path(_sample_path())
        item = self.w.scene._pen_preview
        self.assertEqual(item.boundingRect(), full)
        moved = _sample_path()
        moved.anchors[1].x = 20.0
        self.w.scene.set_pen_path(moved)
        self.assertEqual(item.boundingRect(), full)
        self.w.scene.clear_pen_preview()
        self.assertEqual(item.boundingRect(), full)

    def test_set_path_dirties_old_and_new_only(self):
        self.w.scene.set_pen_path(_sample_path())
        item = self.w.scene._pen_preview
        old = QRectF(item._content_rect())
        calls = []
        with mock.patch.object(item, "update",
                               side_effect=lambda *a: calls.append(a)):
            moved = _sample_path()
            moved.anchors[1].x += 60.0
            self.w.scene.set_pen_path(moved)
        new = QRectF(item._content_rect())
        rects = [a[0] for a in calls if a]
        self.assertIn(old, rects, "未标脏旧位置 → 会留拖影")
        self.assertIn(new, rects, "未标脏新位置")
        self.assertEqual(len(rects), 2, "不应有额外的全图 update")
        for r in rects:
            self.assertFalse(r.isEmpty(), "空矩形不能传给 update()")

    def test_clear_reuses_same_item(self):
        self.w.scene.set_pen_path(_sample_path())
        item = self.w.scene._pen_preview
        self.assertEqual(item, self.w.scene._pen_preview)
        old = QRectF(item._content_rect())
        calls = []
        with mock.patch.object(item, "update",
                               side_effect=lambda *a: calls.append(a)):
            self.w.scene.clear_pen_preview()
        self.assertIs(self.w.scene._pen_preview, item, "clear 不应 removeItem")
        self.assertIsNone(item._path)
        rects = [a[0] for a in calls if a]
        self.assertEqual(rects, [old])

    def test_blue_curve_anchors_handles_render(self):
        self.assertEqual(self._blue_count(self._render()), 0)
        self.w.scene.set_pen_path(_sample_path())
        img = self._render()
        self.assertGreater(self._blue_count(img), 20, "曲线/锚点/控制柄未画出蓝色像素")
        # 锚点 (30,40) 附近有蓝色方块
        self.assertTrue(any(self._is_blue(img.pixelColor(x, y))
                            for y in range(34, 47) for x in range(24, 37)))
        self.w.scene.clear_pen_preview()
        self.assertEqual(self._blue_count(self._render()), 0,
                         "clear 后仍残留预览像素（拖影）")

    def test_z_value_above_grid(self):
        self.w.scene.set_pen_path(_sample_path())
        item = self.w.scene._pen_preview
        self.assertGreater(item.zValue(), 10000)   # GridOverlay = 10000
        self.assertEqual(item.zValue(), 11000)

    def test_empty_path_does_not_pass_empty_rect(self):
        self.w.scene.set_pen_path(_sample_path())
        item = self.w.scene._pen_preview
        calls = []
        with mock.patch.object(item, "update",
                               side_effect=lambda *a: calls.append(a)):
            self.w.scene.set_pen_path(None)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][0].isEmpty())


if __name__ == "__main__":
    unittest.main()
