"""回归：缩放锚点、渲染配色、缩略缓存的局部失效。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402

from PIL import Image  # noqa: E402

OUT = Path("logs/probe_tmp/view_behavior.png")


class ViewBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.w = MainWindow(auto_open=False)
        cls.w.resize(1400, 900)
        cls.w.show()
        QApplication.processEvents()
        sample = Path(cls.w.state.profile.sample_map)
        if not sample.is_file():
            raise unittest.SkipTest(f"缺少示例地图: {sample}")
        cls.w._open(sample)
        QApplication.processEvents()

    def _render(self):
        OUT.parent.mkdir(parents=True, exist_ok=True)
        self.w.view.grab().save(str(OUT))
        return Image.open(OUT).convert("RGB")

    def test_zoom_keeps_anchor(self):
        from PySide6.QtCore import QPoint, Qt
        pos = QPoint(410, 300)
        for delta in (120, 120, -120, -120, 120):
            before = self.w.view.mapToScene(pos)
            self.w.view.zoom_at(pos, delta)
            after = self.w.view.mapToScene(pos)
            self.assertLess((after - before).manhattanLength(), 0.01,
                            f"缩放时锚点漂移 delta={delta}")
        scale = self.w.view.transform().m11()
        self.assertTrue(0.05 <= scale <= 8.0, f"缩放越界: {scale}")
        # 回到适合整图的缩放，供后续渲染用例使用。
        self.w.view.fitInView(self.w.scene.sceneRect(), Qt.KeepAspectRatio)
        self.app.processEvents()

    def test_no_placeholder_pixels(self):
        im = self._render()
        colors = Counter(im.getdata())
        self.assertEqual(colors.get((90, 40, 90), 0), 0, "仍有紫色占位图块")
        green = sum(n for (r, g, b), n in colors.items() if g > r + 20 and g > b + 20 and g > 40)
        blue = sum(n for (r, g, b), n in colors.items() if b > r + 40 and b > g + 40)
        self.assertGreater(green, 100000, "绿色草地缺失")
        self.assertGreater(blue, 100000, "蓝色水体缺失")

    def test_edit_invalidates_cached_tiles(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtWidgets import QApplication
        w = self.w
        layer = w.state.current_layer
        col, row = 80, 80
        pos = w.view.mapFromScene(QPointF(col * 20 + 10, row * 20 + 10))
        before = self._render().getpixel((pos.x(), pos.y()))
        old = layer.get(col, row)
        w._on_gid(1)
        w._on_press(col, row)
        w.view.cellReleased.emit(col, row)
        QApplication.processEvents()
        after = self._render().getpixel((pos.x(), pos.y()))
        self.assertNotEqual(before, after, "编辑后缩略缓存没有局部刷新")
        r, g, b = after[:3]
        self.assertGreater(r, g, "改成的山地图块应偏棕")

        w.state.undo_stack.undo()
        QApplication.processEvents()
        restored = self._render().getpixel((pos.x(), pos.y()))
        self.assertEqual(restored, before, "撤销后缩略缓存没有还原")
        self.assertEqual(layer.get(col, row), old)


if __name__ == "__main__":
    unittest.main()
