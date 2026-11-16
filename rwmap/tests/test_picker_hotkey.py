"""回归：吸管改为临时取色（I 键 / Alt+左键），不再是可切换的工具。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402


class PickerHotkeyTest(unittest.TestCase):
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
            raise unittest.SkipTest("缺少示例地图")
        cls.w._open(sample)
        QApplication.processEvents()
        # 固定到 Ground，找一个非空格作为取色目标
        cls.layer = cls.w.state.doc.layer_by_name("Ground")
        cls.w.state.current_layer = cls.layer
        cls.w.scene.current_layer = cls.layer
        from rwmap.document.gid import GID_MASK
        idx = next(i for i, v in enumerate(cls.layer.cells) if int(v) & GID_MASK)
        cls.col, cls.row = idx % cls.layer.width, idx // cls.layer.width
        cls.raw = int(cls.layer.get(cls.col, cls.row))
        cls.gid = cls.raw & GID_MASK

    def setUp(self):
        self.w.state.tool_id = "brush"
        self.w.state.brush_gid = 0
        self.w.state.stamp = None
        self.w.state.undo_stack.clear()

    def test_picker_button_removed_from_toolbar_and_menu(self):
        from PySide6.QtWidgets import QToolBar
        texts = []
        for bar in self.w.findChildren(QToolBar):
            texts += [a.text() for a in bar.actions()]
        edit_menu = next(m for m in self.w.menuBar().actions() if m.text() == "编辑")
        texts += [a.text() for a in edit_menu.menu().actions()]
        self.assertFalse(any("吸管" in t for t in texts),
                         f"工具栏/菜单里仍有吸管按钮: {texts}")

    def test_i_shortcut_exists_but_is_not_a_tool_switch(self):
        from rwmap.tools import TOOLS
        self.assertEqual(self.w.act_picker.shortcut().toString(), "I")
        self.assertNotIn("picker", TOOLS)
        self.assertIn(self.w.act_picker, self.w.actions())

    def test_i_key_picks_gid_without_changing_tool(self):
        self.w.view.hoverCell.emit(self.col, self.row, self.raw)
        self.w.act_picker.trigger()
        self.assertEqual(self.w.state.brush_gid, self.gid)
        self.assertEqual(self.w.state.tool_id, "brush")

    def test_pick_clears_stamp_and_highlights_tileset(self):
        self.w.state.stamp = [[self.gid, self.gid], [self.gid, self.gid]]
        self.w._pick_gid(self.col, self.row)
        self.assertIsNone(self.w.state.stamp)
        self.assertIsNotNone(self.w.tilesets_dock.palette.selection)
        self.assertEqual(self.w.tilesets_dock.palette.selected,
                         self.gid - self.w.tilesets_dock.current_firstgid())

    def test_alt_pick_signal_does_not_write_or_add_undo(self):
        before = list(self.layer.cells)
        count = self.w.state.undo_stack.count()
        self.w.view.pickRequested.emit(self.col, self.row)
        self.assertEqual(list(self.layer.cells), before)
        self.assertEqual(self.w.state.undo_stack.count(), count)
        self.assertEqual(self.w.state.brush_gid, self.gid)

    def test_alt_left_click_goes_to_picker_not_draw(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        pressed = []
        picked = []
        on_pressed = lambda c, r: pressed.append((c, r))
        on_picked = lambda c, r: picked.append((c, r))
        self.w.view.cellPressed.connect(on_pressed)
        self.w.view.pickRequested.connect(on_picked)
        try:
            center = self.w.view.viewport().rect().center()
            QTest.mousePress(self.w.view.viewport(), Qt.LeftButton, Qt.AltModifier, center)
            QTest.mouseRelease(self.w.view.viewport(), Qt.LeftButton, Qt.NoModifier, center)
            self.assertEqual(pressed, [], "Alt+左键不应开始绘制笔划")
            self.assertTrue(picked, "Alt+左键应触发临时吸管")
        finally:
            self.w.view.cellPressed.disconnect(on_pressed)
            self.w.view.pickRequested.disconnect(on_picked)

    def test_empty_cell_pick_keeps_brush(self):
        # 找一个全层皆空的坐标很难，这里直接验证越界坐标不会改笔刷
        self.w.state.brush_gid = self.gid
        self.w._pick_gid(-1, -1)
        self.assertEqual(self.w.state.brush_gid, self.gid)


if __name__ == "__main__":
    unittest.main()
