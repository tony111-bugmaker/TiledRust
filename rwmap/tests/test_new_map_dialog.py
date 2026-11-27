"""回归：Tiled 风格「新建地图」对话框（宽/高 + 图集，无瓦片尺寸输入）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.rw.version import active_profile  # noqa: E402
from rwmap.widgets.new_map_dialog import (  # noqa: E402
    NewMapDialog, RW_TILE_SIZE, build_new_document,
)


_app = QApplication.instance() or QApplication([])


class NewMapDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_has_only_width_height_and_tilesets(self):
        dlg = NewMapDialog(active_profile())
        v = dlg.values()
        self.assertIn("width", v)
        self.assertIn("height", v)
        self.assertIn("tilesets", v)
        # 不再有瓦片宽/高这类输入
        self.assertNotIn("tile_w", v)
        self.assertNotIn("tile_h", v)
        self.assertEqual((v["width"], v["height"]), (64, 64))
        self.assertGreater(dlg.list_tilesets.count(), 0)
        dlg.close()

    def test_check_all_toggles_every_tileset(self):
        dlg = NewMapDialog(active_profile())
        dlg._check_all(True)
        self.assertEqual(len(dlg.values()["tilesets"]), dlg.list_tilesets.count())
        dlg._check_all(False)
        self.assertEqual(len(dlg.values()["tilesets"]), 0)
        dlg.close()

    def test_preview_renders_selected_tileset(self):
        dlg = NewMapDialog(active_profile())
        # 构造时默认选中第一项，预览应有实际像素（不是空 QPixmap）
        pix = dlg.lbl_preview.pixmap()
        self.assertIsNotNone(pix)
        self.assertFalse(pix.isNull())
        self.assertGreater(pix.width(), 0)
        self.assertGreater(pix.height(), 0)
        self.assertIn("格", dlg.lbl_preview_info.text())
        dlg.close()

    def test_preview_toggle_hides_and_restores(self):
        dlg = NewMapDialog(active_profile())
        self.assertTrue(dlg.chk_preview.isChecked())
        dlg.chk_preview.setChecked(False)
        QApplication.processEvents()
        self.assertFalse(dlg.lbl_preview.isVisibleTo(dlg))
        dlg.chk_preview.setChecked(True)
        QApplication.processEvents()
        self.assertTrue(dlg.lbl_preview.isVisibleTo(dlg))
        dlg.close()

    def test_selecting_different_tileset_changes_preview(self):
        dlg = NewMapDialog(active_profile())
        if dlg.list_tilesets.count() < 2:
            self.skipTest("图集不足两项")
        first = dlg.list_tilesets.item(0).data(Qt.UserRole)
        dlg.list_tilesets.setCurrentRow(1)
        QApplication.processEvents()
        second = dlg.list_tilesets.currentItem().data(Qt.UserRole)
        self.assertNotEqual(first, second)
        self.assertIn(second, dlg._preview_cache)
        dlg.close()

    def test_build_new_document_with_tilesets_and_default_tile_size(self):
        dlg = NewMapDialog(active_profile())
        dlg._check_all(False)
        # 选一个真实存在的 base 图集（terrain/Dirt.tsx）
        chosen = None
        for i in range(dlg.list_tilesets.count()):
            it = dlg.list_tilesets.item(i)
            if it.data(0) and str(it.text()).lower().startswith("terrain/"):
                it.setCheckState(Qt.Checked)
                chosen = it.data(0)
                break
        vals = dlg.values()
        dlg.close()
        self.assertIsNotNone(chosen)
        doc = build_new_document(active_profile(), vals["width"], vals["height"],
                                 vals["tilesets"])
        self.assertEqual((doc.width, doc.height), (64, 64))
        # 铁锈标准 5 层：Ground / Items / Units / Triggers / set
        self.assertEqual([l.name for l in doc.layers],
                         ["Ground", "Items", "Units", "Triggers", "set"])
        self.assertEqual(len(doc.tilesets), 1)
        self.assertGreater(doc.tilesets[0].firstgid, 0)
        self.assertIn((doc.tile_w, doc.tile_h), [(20, 20), (RW_TILE_SIZE, RW_TILE_SIZE)])
        # DOM 里同步了 <tileset>
        self.assertIn("tileset", [el.tag for el in doc.tree.getroot()])

    def test_tilesets_have_real_gid_ranges(self):
        """外部 .tsx 靠 PNG 尺寸推算 tilecount；GID 段不能全压在 1。"""
        dlg = NewMapDialog(active_profile())
        dlg._check_all(True)
        paths = dlg.values()["tilesets"]
        dlg.close()
        doc = build_new_document(active_profile(), 32, 32, paths)
        self.assertIsNotNone(doc.resolver)
        self.assertGreater(len(doc.tilesets), 2)
        firstgids = [t.firstgid for t in doc.tilesets]
        self.assertEqual(len(set(firstgids)), len(firstgids), "GID 段重叠")
        self.assertTrue(all(t.count > 0 for t in doc.tilesets),
                        "有图集 tilecount=0，图块集面板会空白")
        self.assertEqual(firstgids, sorted(firstgids))
        # 相邻图集首格连续（无空洞）
        for a, b in zip(doc.tilesets, doc.tilesets[1:]):
            self.assertEqual(b.firstgid, a.last_gid + 1)

    def test_build_empty_document_uses_default_tile_size(self):
        doc = build_new_document(active_profile(), 10, 12, [])
        self.assertEqual((doc.width, doc.height), (10, 12))
        self.assertEqual((doc.tile_w, doc.tile_h), (RW_TILE_SIZE, RW_TILE_SIZE))
        self.assertEqual(len(doc.tilesets), 0)
        self.assertEqual(len(doc.layers), 5)
        self.assertEqual(doc.layers[0].name, "Ground")


class NewMapIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_new_map_uses_dialog(self):
        w = MainWindow(auto_open=False)
        vals = {"width": 24, "height": 18, "tilesets": []}
        with mock.patch("rwmap.widgets.new_map_dialog.NewMapDialog.exec",
                        return_value=QDialog.Accepted), \
             mock.patch("rwmap.widgets.new_map_dialog.NewMapDialog.values",
                        return_value=vals):
            w._new_map()
        QApplication.processEvents()
        self.assertEqual((w.state.doc.width, w.state.doc.height), (24, 18))
        self.assertIn("新地图", w.state.doc.path.name)
        w.close()


if __name__ == "__main__":
    unittest.main()
