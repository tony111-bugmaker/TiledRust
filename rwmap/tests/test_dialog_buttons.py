"""回归：新建图集 / 转到图块对话框的底部按钮必须可点、够大。

2026-11-23：手写 QPushButton 在 Windows11 样式下只有 26×17，紧贴窗口
底边，用户点文字边缘会落空，表现为“创建 / 取消按了没反应”。现在统一
用 QDialogButtonBox 且给最小点击区域，这里锁住尺寸和信号。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QDialogButtonBox, QPushButton,
)

from rwmap.widgets.goto_tile_dialog import GotoTileDialog  # noqa: E402
from rwmap.widgets.new_tileset_dialog import NewTilesetDialog  # noqa: E402


_app = QApplication.instance() or QApplication([])


def _buttons(dlg) -> dict[str, QPushButton]:
    box = dlg.findChild(QDialogButtonBox)
    if box is None:
        return {}
    return {b.text(): b for b in box.findChildren(QPushButton)}


class DialogButtonsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_tileset_buttons_are_large_enough(self):
        dlg = NewTilesetDialog()
        btns = _buttons(dlg)
        self.assertEqual(set(btns), {"创建", "取消"})
        for btn in btns.values():
            self.assertGreaterEqual(btn.minimumWidth(), 60)
            self.assertGreaterEqual(btn.minimumHeight(), 24)
        dlg.close()

    def test_goto_tile_buttons_are_large_enough(self):
        dlg = GotoTileDialog(max_col=10, max_row=10)
        btns = _buttons(dlg)
        self.assertEqual(set(btns), {"确定", "取消"})
        for btn in btns.values():
            self.assertGreaterEqual(btn.minimumWidth(), 60)
            self.assertGreaterEqual(btn.minimumHeight(), 24)
        dlg.close()

    def test_cancel_rejects_dialog(self):
        dlg = NewTilesetDialog()
        results: list[int] = []
        dlg.finished.connect(lambda r: results.append(r))
        QTest.mouseClick(_buttons(dlg)["取消"], Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(results, [QDialog.Rejected])
        dlg.close()

    def test_create_without_image_stays_open(self):
        dlg = NewTilesetDialog()
        results: list[int] = []
        dlg.finished.connect(lambda r: results.append(r))
        QTest.mouseClick(_buttons(dlg)["创建"], Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(results, [])
        self.assertIn("请先选择", dlg.info.text())
        dlg.close()

    def test_create_with_image_accepts_and_builds_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            img_path = Path(tmp) / "atlas.png"
            Image.new("RGB", (40, 40), (30, 30, 30)).save(img_path)
            dlg = NewTilesetDialog()
            dlg.image_path = img_path
            results: list[int] = []
            dlg.finished.connect(lambda r: results.append(r))
            QTest.mouseClick(_buttons(dlg)["创建"], Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(results, [QDialog.Accepted])
            self.assertIsNotNone(dlg.document)
            self.assertEqual(dlg.document.tileset.tilecount, 4)
            dlg.close()


if __name__ == "__main__":
    unittest.main()
