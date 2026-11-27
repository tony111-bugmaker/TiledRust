"""回归：保存 / 另存为后标签页名字同步；打开文件后保存直接写回、不再弹另存为。

bug 1：_save_as 里 export_map 已更新 doc.path，但只刷了主窗口标题，
       漏刷标签页标题，截图里标签仍显示旧文件名。
bug 2：_finish_open 把 _own_file 置 False，_save 见到就强制转另存为，
       导致「文件→打开」的地图按保存还会弹另存对话框。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402


class SaveTabTitleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from rwmap.app.main_window import MainWindow
        self.w = MainWindow(auto_open=False)
        self.w._open(Path(self.w.state.profile.sample_map))
        self.assertTrue(self.w.doc_tabs.count() >= 1)

    def tearDown(self):
        self.w.hide()
        self.w.deleteLater()

    def test_opened_file_save_writes_back_without_save_as_dialog(self):
        """打开文件后 _own_file 为真；按保存直接写回，不弹另存为。"""
        self.assertTrue(self.w._own_file, "打开的文件应可直接保存")
        doc = self.w.state.doc
        original = doc.path
        with patch("rwmap.app.main_window.QMessageBox.critical") as err, \
             patch.object(self.w, "_save_as") as save_as:
            self.w._save()
        # 未走另存为分支，且无错误弹窗
        save_as.assert_not_called()
        err.assert_not_called()
        self.assertEqual(doc.path, original)
        self.assertEqual(self.w.windowTitle().split(" — ")[0], original.name)

    def test_save_clears_dirty_star_on_tab(self):
        """保存后标签上的未保存星号消失。"""
        doc = self.w.state.doc
        doc.dirty = True
        self.w._update_active_tab_title()
        self.assertTrue(self.w.doc_tabs.tabText(self.w._active_index).endswith(" *"))
        self.w._save()
        self.assertFalse(self.w.doc_tabs.tabText(self.w._active_index).endswith(" *"))

    def test_save_as_updates_tab_name_to_new_file(self):
        """另存为改名后，标签页标题显示新文件名而非旧的。"""
        import tempfile
        doc = self.w.state.doc
        old_name = doc.path.name
        with tempfile.TemporaryDirectory() as td:
            new_path = Path(td) / "新名字.tmx"
            self.assertNotEqual(new_path.name, old_name)
            with patch("rwmap.app.main_window.QFileDialog.getSaveFileName",
                       return_value=(str(new_path), "")):
                # 不生成缩略图，避免依赖外部 rasterizer
                with patch("rwmap.rw.thumbnail.make_thumbnail",
                           return_value={"ok": False, "error": "test"}):
                    self.w._save_as()
            self.assertEqual(doc.path, new_path)
            tab = self.w.doc_tabs.tabText(self.w._active_index)
            self.assertIn(new_path.name, tab)
            self.assertNotIn(old_name, tab)
            self.assertIn(new_path.name, self.w.windowTitle())


if __name__ == "__main__":
    unittest.main()
