"""回归：左上角多文档标签——打开多个、切换、关标签、视图变换保留、新建。

依赖示例地图（缺失则跳过）。与 test_minimap 同样的 MainWindow 装配方式，
但本用例额外验证 tab 数量、激活上下文切换、视图矩阵跨标签保留、新建与关闭。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


class MultiDocTest(unittest.TestCase):
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
        cls.sample = sample
        # 复用初始空 context 打开第一个；再打开第二个成新 tab。
        cls.w._open(sample)
        QApplication.processEvents()
        cls.w._open(sample)
        QApplication.processEvents()

    def test_one_tab_reuses_initial_context(self):
        # 首次打开应复用初始空 context，仅 1 个 tab。
        self.assertEqual(self.w.doc_tabs.count(), 2)
        self.assertEqual(len(self.w._contexts), 2)

    def test_switch_tab_activates_context(self):
        self.w._on_tab_changed(0)
        QApplication.processEvents()
        self.assertEqual(self.w._active_index, 0)
        self.assertEqual(self.w.state, self.w._contexts[0])

    def test_view_transform_preserved_across_tabs(self):
        from PySide6.QtCore import QPoint
        # 在 tab0 放大，切到 tab1 再切回，缩放应原样还原。
        center = self.w.view.viewport().rect().center()
        self.w._on_tab_changed(0)
        QApplication.processEvents()
        self.w.view.zoom_at(QPoint(int(center.x()), int(center.y())), 600)
        QApplication.processEvents()
        z0 = self.w.view.transform().m11()
        self.w._on_tab_changed(1)
        QApplication.processEvents()
        z1 = self.w.view.transform().m11()
        self.w._on_tab_changed(0)
        QApplication.processEvents()
        z0b = self.w.view.transform().m11()
        self.assertNotAlmostEqual(z1, z0, delta=1e-6)
        self.assertAlmostEqual(z0b, z0, delta=1e-6)

    def test_new_map_adds_tab(self):
        # 新建地图改为 Tiled 风格对话框：宽/高 + 图集勾选。
        from PySide6.QtWidgets import QDialog
        vals = {"width": 32, "height": 32, "tilesets": []}
        with mock.patch("rwmap.widgets.new_map_dialog.NewMapDialog.exec",
                        return_value=QDialog.Accepted), \
             mock.patch("rwmap.widgets.new_map_dialog.NewMapDialog.values",
                        return_value=vals):
            before = self.w.doc_tabs.count()
            self.w._new_map()
        QApplication.processEvents()
        self.assertEqual(self.w.doc_tabs.count(), before + 1)
        self.assertIn("新地图", self.w.state.doc.path.name)

    def test_close_active_tab_switches_neighbor(self):
        cur = self.w._active_index
        before = self.w.doc_tabs.count()
        self.w._close_tab(cur)
        QApplication.processEvents()
        self.assertEqual(self.w.doc_tabs.count(), before - 1)
        self.assertEqual(self.w.state, self.w._contexts[self.w._active_index])

    def test_close_non_active_tab_keeps_active(self):
        # 打开足够多标签，确保有非激活标签可关。
        self.w._open(self.sample)
        QApplication.processEvents()
        active_ctx = self.w.state            # 捕获激活上下文对象
        active = self.w._active_index
        target = 0 if active != 0 else 1
        before = self.w.doc_tabs.count()
        self.w._close_tab(target)
        QApplication.processEvents()
        self.assertEqual(self.w.doc_tabs.count(), before - 1)
        # 激活上下文对象不变，且仍处于激活位置（索引因前移而 -1）。
        self.assertIs(self.w.state, active_ctx)
        self.assertIs(self.w._contexts[self.w._active_index], active_ctx)


if __name__ == "__main__":
    unittest.main()
