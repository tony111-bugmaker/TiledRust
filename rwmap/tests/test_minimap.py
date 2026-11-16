"""回归：右侧迷你地图渲染整图概览、跟随视口、点击平移主视图。

依赖示例地图（缺失则跳过），与 test_tilesets_dock 同样的 MainWindow 装配方式。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


class MiniMapTest(unittest.TestCase):
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
        cls.mm = cls.w.minimap_dock.minimap

    def test_dock_registered_and_visible(self):
        from PySide6.QtCore import Qt
        self.assertEqual(self.w.dockWidgetArea(self.w.minimap_dock), Qt.RightDockWidgetArea)
        self.assertTrue(self.w.minimap_dock.isVisible())

    def test_overview_rendered(self):
        self.assertIsNotNone(self.mm._overview)
        self.assertFalse(self.mm._overview.isNull())
        self.assertGreater(self.mm._scale, 0)

    def test_viewport_rect_follows_view(self):
        from PySide6.QtCore import QPointF
        r1 = self.mm._viewport_scene_rect()
        self.assertIsNotNone(r1)
        # 平移主视图后，视口框应随之改变
        self.w.view.centerOn(QPointF(0, 0))
        self.w.view.update()
        QApplication.processEvents()
        r2 = self.mm._viewport_scene_rect()
        self.assertIsNotNone(r2)
        self.assertNotEqual((round(r1.x()), round(r1.y())),
                            (round(r2.x()), round(r2.y())))

    def test_click_recenters_main_view(self):
        from PySide6.QtCore import QPoint, QPointF
        fit = self.mm._fit_rect()
        center = fit.center()
        sp = self.mm._scene_point(QPoint(int(center.x()), int(center.y())))
        self.assertIsNotNone(sp)
        before = self.w.view.mapToScene(
            self.w.view.viewport().width() // 2, self.w.view.viewport().height() // 2)
        self.w.view.centerOn(sp[0], sp[1])
        QApplication.processEvents()
        after = self.w.view.mapToScene(
            self.w.view.viewport().width() // 2, self.w.view.viewport().height() // 2)
        self.assertAlmostEqual(after.x(), sp[0], delta=6)
        self.assertAlmostEqual(after.y(), sp[1], delta=6)
        # before 与 after 一般不同（点的是概览中心而非当前视图中心）
        self.assertTrue(abs(before.x() - after.x()) > 1 or abs(before.y() - after.y()) > 1)


if __name__ == "__main__":
    unittest.main()
