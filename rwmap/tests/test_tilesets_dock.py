"""回归：图块集面板为 Tiled 风格（横向标签栏 / 无序号 / 懒绘制 / 底部停靠）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402


class TilesetsDockTest(unittest.TestCase):
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
        cls.dock = cls.w.tilesets_dock
        cls.picked = []
        cls.dock.gidSelected.connect(cls.picked.append)

    def test_tab_count_and_no_index_labels(self):
        from PySide6.QtCore import Qt
        d = self.dock
        self.assertEqual(d.tabs.count(), len(self.w.state.doc.tilesets))
        self.assertGreater(d.tabs.count(), 1)
        for i in range(d.tabs.count()):
            text = d.tabs.tabText(i)
            self.assertTrue(text)
            self.assertNotIn("·", text)                 # 旧版 "176 · Dirt"
            self.assertFalse(text.strip().isdigit())
            self.assertIsNone(re.match(r"^\d+\b", text))
        self.assertEqual(d.tabs.elideMode(), Qt.ElideNone)   # 不压成首字母

    def test_palette_is_not_a_text_list(self):
        from PySide6.QtWidgets import QListWidget
        self.assertNotIsInstance(self.dock.palette, QListWidget)
        self.assertFalse(hasattr(self.dock.palette, "item"))   # 没有带文字的 item

    def test_dock_sits_at_right_below_properties(self):
        from PySide6.QtCore import Qt
        # 第十轮默认布局：图块集从底部横跨移到右侧最下层。
        self.assertEqual(self.w.dockWidgetArea(self.dock), Qt.RightDockWidgetArea)

    def test_click_emits_gid(self):
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        self.dock.select_tileset(1)
        QApplication.processEvents()
        before = len(self.picked)
        QTest.mouseClick(self.dock.palette, Qt.LeftButton, pos=QPoint(8, 8))
        self.assertEqual(len(self.picked), before + 1)
        self.assertEqual(self.picked[-1], self.dock.current_firstgid())
        self.assertEqual(self.dock.palette.selected, 0)

    def test_switch_tileset_recomputes_grid(self):
        from PySide6.QtWidgets import QApplication
        d = self.dock
        target = next(t for t in self.w.state.doc.tilesets if t.firstgid == 176)
        self.assertTrue(d.select_tileset(176))
        QApplication.processEvents()
        self.assertEqual(d.current_tileset().name, target.name)
        self.assertEqual(
            d.palette.rows,
            max(1, (target.count + d.palette.columns - 1) // d.palette.columns),
        )
        self.assertEqual(d.palette.selected, -1)        # 切换后清空选中

    def test_all_tabs_switch_without_error(self):
        for ts in self.w.state.doc.tilesets:
            self.assertTrue(self.dock.select_tileset(ts.firstgid))

    def test_combo_compat_mirror(self):
        d = self.dock
        self.assertEqual(d.combo.count(), d.tabs.count())
        d.combo.setCurrentIndex(3)
        self.assertEqual(d.tabs.currentIndex(), 3)

    def test_palette_paint_does_not_crash(self):
        self.assertFalse(self.dock.palette.grab().isNull())

    def _select_ice(self):
        from PySide6.QtWidgets import QApplication
        ts = next((t for t in self.w.state.doc.tilesets
                   if t.name == "Ice Ice - Flat"), None)
        if ts is None:
            self.skipTest("示例地图缺少 Ice Ice - Flat 图块集")
        self.assertTrue(self.dock.select_tileset(ts.firstgid))
        QApplication.processEvents()
        return ts

    def test_atlas_shown_at_original_position(self):
        """整图集按原尺寸 1:1 显示，不重排瓦片。"""
        ts = self._select_ice()
        pal = self.dock.palette
        self.assertEqual((pal.atlas_w, pal.atlas_h), (60, 140))
        self.assertEqual((pal.columns, pal.rows), (3, 7))
        self.assertEqual(ts.count, 21)
        self.assertIsNotNone(pal._pix)

    def test_color_key_is_transparent(self):
        """ice2ice_flat.png 的 #008a76 青色必须转成 alpha=0，而不是画成青块。"""
        from rwmap.tilesets.image_loader import load_tileset_image
        ts = self._select_ice()
        img = load_tileset_image(ts, self.w.state.doc.resolver).convert("RGBA")
        cyan = [p for p in img.getdata() if p[:3] == (0, 138, 118)]
        self.assertGreater(len(cyan), 1000)
        self.assertTrue(all(p[3] == 0 for p in cyan))

    def test_drag_selects_rectangle_and_emits_stamp(self):
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        self._select_ice()
        pal = self.dock.palette
        QTest.mousePress(pal, Qt.LeftButton, pos=QPoint(5, 5))
        QTest.mouseMove(pal, pos=QPoint(45, 45))
        QTest.mouseRelease(pal, Qt.LeftButton, pos=QPoint(45, 45))
        QApplication.processEvents()
        self.assertEqual(pal.selection, (0, 0, 2, 2))
        stamp = self.w.state.stamp
        self.assertEqual(len(stamp), 3)
        self.assertEqual(len(stamp[0]), 3)
        firstgid = self.dock.current_firstgid()
        self.assertEqual(stamp[0][0], firstgid)
        self.assertEqual(stamp[2][2], firstgid + 2 * 3 + 2)

    def test_stamp_paints_multiple_cells_and_undoes(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtWidgets import QApplication
        self._select_ice()
        pal = self.dock.palette
        QTest.mousePress(pal, Qt.LeftButton, pos=QPoint(5, 5))
        QTest.mouseMove(pal, pos=QPoint(25, 5))
        QTest.mouseRelease(pal, Qt.LeftButton, pos=QPoint(25, 5))
        QApplication.processEvents()
        layer = self.w.state.current_layer
        col, row = 60, 60
        stamp = self.w.state.stamp
        w, h = len(stamp[0]), len(stamp)
        # 多格图章以「中间格」对齐光标：原点 = 光标格 - (W//2, H//2)。
        oc, orow = col - w // 2, row - h // 2
        before = [[layer.get(oc + x, orow + y) for x in range(w)]
                  for y in range(h)]
        count = self.w.state.undo_stack.count()
        self.w._on_press(col, row)
        self.w.view.cellReleased.emit(col, row)
        QApplication.processEvents()
        # 落点区域应与图章完全一致（验证中心对齐：原点 = col-W//2, row-H//2）
        painted = [[layer.get(oc + x, orow + y) for x in range(w)]
                   for y in range(h)]
        self.assertEqual(painted, stamp)
        for _ in range(self.w.state.undo_stack.count() - count):
            self.w.state.undo_stack.undo()
        self.assertEqual([[layer.get(oc + x, orow + y) for x in range(w)]
                         for y in range(h)], before)


    def test_set_zoom_scales_geometry_and_hit_test(self):
        from PySide6.QtCore import QPoint

        ts = self._select_ice()
        pal = self.dock.palette
        pal.set_zoom(1.0)
        base_w, base_h = pal.atlas_w, pal.atlas_h
        base_cell = pal.cell_w
        self.assertGreater(base_w, 0)
        # 2x：图集尺寸与格距都翻倍
        pal.set_zoom(2.0)
        self.assertEqual(pal.atlas_w, base_w * 2)
        self.assertEqual(pal.atlas_h, base_h * 2)
        self.assertEqual(pal.cell_w, base_cell * 2)
        # 命中测试：放大后某格中心仍映射到同一格
        cx = pal._margin_x + 1 * pal.cell_w + pal.cell_w // 2
        cy = pal._margin_y + 1 * pal.cell_h + pal.cell_h // 2
        self.assertEqual(pal._tile_at(QPoint(cx, cy)), (1, 1))
        # 0.5x：尺寸减半
        pal.set_zoom(0.5)
        self.assertEqual(pal.atlas_w, base_w // 2)
        self.assertEqual(pal.cell_w, max(1, base_cell // 2))
        # 还原，避免影响后续用例
        pal.set_zoom(1.0)
        self.assertEqual((pal.atlas_w, pal.atlas_h), (base_w, base_h))

    def test_grid_toggle(self):
        pal = self.dock.palette
        self.assertTrue(pal._show_grid)
        pal._toggle_grid(False)
        self.assertFalse(pal._show_grid)
        self.assertFalse(pal.grab().isNull())      # 关掉网格后仍能正常绘制
        pal._toggle_grid()                          # 不带参 = 取反
        self.assertTrue(pal._show_grid)

    def test_shift_wheel_zooms_tileset(self):
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtCore import Qt

        self._select_ice()
        pal = self.dock.palette
        pal.set_zoom(1.0)
        scroll = self.dock.scroll
        before = pal._zoom
        evt = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                          QPoint(0, 120), Qt.NoButton, Qt.ShiftModifier,
                          Qt.ScrollPhase.ScrollUpdate, False)
        scroll.wheelEvent(evt)
        self.assertGreater(pal._zoom, before)        # Shift+滚轮放大
        # 普通滚轮（无 Shift）不改变缩放
        z = pal._zoom
        evt2 = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                           QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                           Qt.ScrollPhase.ScrollUpdate, False)
        scroll.wheelEvent(evt2)
        self.assertEqual(pal._zoom, z)
        pal.set_zoom(1.0)


if __name__ == "__main__":
    unittest.main()
