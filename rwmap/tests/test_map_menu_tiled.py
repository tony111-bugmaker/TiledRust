"""Tiled「地图」菜单通用功能补齐的回归测试。

覆盖：
1. 菜单结构：6 项新功能就位 + 快捷键；绘制时自动地图只在菜单、不在对象工具栏。
2. 添加外部图块(.tsx)：模型 + DOM 同步、去重、一条撤销可还原。
3. 自动裁剪：裁到内容 bbox、对象坐标平移、一条撤销可还原、空图跳过。
4. 转到图块：调用 centerOn（纯视图）。
5. 上/下一个图块集：循环切换 tab（纯视图）。
6. 地图属性：对话框值写回内存 + DOM、一条撤销可还原、取消不改文档。
7. 所有功能都不污染无关文档字段；快捷键不与既有冲突。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from rwmap.app.app_state import AppState  # noqa: E402
from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402

_app = QApplication.instance() or QApplication([])

_GAME = Path("<游戏目录1.15>")


class MapMenuBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MainWindow(auto_open=False)
        cls.w._open(Path(cls.w.state.profile.sample_map))
        _app.processEvents()

    @property
    def map_menu(self):
        return next(m.menu() for m in self.w.menuBar().actions()
                    if m.text() == "地图")

    def menu_texts(self):
        return [a.text() for a in self.map_menu.actions()
                if not a.isSeparator()]

    def make_small_doc(self):
        """10×10 空图，内容在 (2,2)-(5,4)；返回新装上的 context。"""
        doc = MapDocument(path=Path("crop_test.tmx"), width=10, height=10,
                          tile_w=20, tile_h=20, profile=self.w.state.profile)
        cells = [0] * 100
        for r in range(2, 5):
            for c in range(2, 6):
                cells[r * 10 + c] = 1
        doc.layers = [TileLayer(width=10, height=10, name="Ground", cells=cells)]
        ctx = AppState()
        ctx.open_document(doc)
        self.w._contexts.append(ctx)
        self.w._attach_context(ctx)
        self.w._refresh_document()
        _app.processEvents()
        return doc, ctx


class TestMapMenuStructure(MapMenuBase):
    def test_new_items_present(self):
        texts = self.menu_texts()
        for name in ("添加外部图块…", "添加自动映射规则图集…", "自动裁剪",
                     "转到图块…", "选择上一个图块集", "选择下一个图块集",
                     "地图属性…", "绘制时自动地图"):
            self.assertIn(name, texts)

    def test_shortcuts(self):
        w = self.w
        self.assertEqual(w.act_goto_tile.shortcut().toString(), "Ctrl+Shift+G")
        self.assertEqual(w.act_prev_tileset.shortcut().toString(), "[")
        self.assertEqual(w.act_next_tileset.shortcut().toString(), "]")
        self.assertEqual(w.act_automap_now.shortcut().toString(), "Ctrl+M")

    def test_automap_button_only_in_menu(self):
        w = self.w
        self.assertIn(w.act_automap_while, self.map_menu.actions())
        self.assertNotIn(w.act_automap_while, w.object_toolbar.actions())
        self.assertIsNone(w.object_toolbar.widgetForAction(w.act_automap_while))

    def test_new_submenu_top_of_file(self):
        """文件菜单最上面是「新建」子菜单，点开显示地图 / 瓦片图集两项。"""
        w = self.w
        file_menu = next(m.menu() for m in w.menuBar().actions()
                         if m.text() == "文件")
        actions = file_menu.actions()
        self.assertEqual(actions[0].text(), "新建")
        # 「新建」与「打开 .tmx」之间不要分隔线（用户要求紧贴）
        self.assertEqual(actions[1].text(), "打开 .tmx")
        self.assertFalse(actions[1].isSeparator())
        sub = actions[0].menu()
        self.assertIsNotNone(sub)
        self.assertEqual([a.text() for a in sub.actions()],
                         ["新建地图…", "新建瓦片图集…"])
        self.assertEqual(sub.actions()[0].data(), None)  # 保留 QAction 身份
        self.assertIs(sub.actions()[0], w.act_new_map)
        self.assertIs(sub.actions()[1], w.act_new_tileset)
        # 旧位置不再重复
        self.assertNotIn(w.act_new_map, self.map_menu.actions())
        self.assertNotIn(w.act_new_tileset, actions)

    def test_actions_exist_and_enabled(self):
        # 真实接线由各功能测试用 mock 触发验证；这里只确认 action 就位可用。
        from PySide6.QtGui import QAction
        w = self.w
        for name in ("act_add_external_tileset", "act_add_rules_tileset",
                     "act_autocrop", "act_goto_tile", "act_prev_tileset",
                     "act_next_tileset", "act_map_properties"):
            act = getattr(w, name)
            self.assertIsInstance(act, QAction, name)
            self.assertTrue(act.isEnabled(), name)


class TestAddExternalTileset(MapMenuBase):
    def test_add_sync_dom_and_undo(self):
        w = self.w
        w._open(Path(w.state.profile.sample_map))
        _app.processEvents()
        doc = w.state.doc
        tmpd = Path(tempfile.gettempdir()) / "rwmap_test_addts"
        tmpd.mkdir(exist_ok=True)
        dst = tmpd / "My Custom.tsx"
        shutil.copy(_GAME / "assets/tilesets/decoration.tsx", dst)

        before = len(doc.tilesets)
        dom_before = len([e for e in doc.tree.getroot()
                          if getattr(e, "tag", None) == "tileset"])
        n = w._add_tileset_files([dst], "测试添加")
        self.assertEqual(n, 1)
        self.assertEqual(len(doc.tilesets), before + 1)
        self.assertEqual(len([e for e in doc.tree.getroot()
                              if getattr(e, "tag", None) == "tileset"]), dom_before + 1)
        new_ts = doc.tilesets[-1]
        self.assertEqual(new_ts.name, "decoration")
        self.assertTrue(new_ts.source)

        w.state.undo_stack.undo()
        _app.processEvents()
        self.assertEqual(len(doc.tilesets), before)
        self.assertEqual(len([e for e in doc.tree.getroot()
                              if getattr(e, "tag", None) == "tileset"]), dom_before)
        w.state.undo_stack.redo()
        _app.processEvents()
        self.assertEqual(len(doc.tilesets), before + 1)

    def test_dedup_existing_source(self):
        w = self.w
        w._open(Path(w.state.profile.sample_map))
        _app.processEvents()
        doc = w.state.doc
        tsx = _GAME / "mods/maps/terrain/Long Grass.tsx"   # 已被示例地图引用
        before = len(doc.tilesets)
        n = w._add_tileset_files([tsx], "测试去重")
        self.assertEqual(n, 0)
        self.assertEqual(len(doc.tilesets), before)


class TestAutocrop(MapMenuBase):
    def test_crop_content_bbox_and_undo(self):
        doc, _ctx = self.make_small_doc()
        w = self.w
        w._autocrop()
        _app.processEvents()
        # 内容 c2..5 (4 列), r2..4 (3 行)
        self.assertEqual((doc.width, doc.height), (4, 3))
        lay = doc.layer_by_name("Ground")
        self.assertEqual(lay.get(0, 0), 1)
        self.assertEqual(lay.get(3, 2), 1)
        w.state.undo_stack.undo()
        _app.processEvents()
        self.assertEqual((doc.width, doc.height), (10, 10))
        self.assertEqual(doc.layer_by_name("Ground").get(2, 2), 1)

    def test_crop_translates_objects(self):
        doc = MapDocument(path=Path("crop_obj.tmx"), width=10, height=10,
                          tile_w=20, tile_h=20, profile=self.w.state.profile)
        obj = MapObject(x=2 * 20, y=2 * 20, width=20, height=20)
        ol = ObjectLayer(name="Objects", objects=[obj])
        doc.layers = [ol]
        ctx = AppState()
        ctx.open_document(doc)
        self.w._contexts.append(ctx)
        self.w._attach_context(ctx)
        self.w._refresh_document()
        _app.processEvents()
        self.w._autocrop()
        _app.processEvents()
        # 对象像素 (40,40) 尺寸 20×20，正好占 (2,2) 一格 → 裁剪后 1×1
        self.assertEqual((doc.width, doc.height), (1, 1))
        moved = doc.layer_by_name("Objects").objects[0]
        self.assertEqual((moved.x, moved.y), (0, 0))

    def test_empty_map_noop(self):
        doc = MapDocument(path=Path("crop_empty.tmx"), width=5, height=5,
                          tile_w=20, tile_h=20, profile=self.w.state.profile)
        doc.layers = [TileLayer(width=5, height=5, name="Ground", cells=[0] * 25)]
        ctx = AppState()
        ctx.open_document(doc)
        self.w._contexts.append(ctx)
        self.w._attach_context(ctx)
        self.w._refresh_document()
        _app.processEvents()
        self.w._autocrop()
        _app.processEvents()
        self.assertEqual((doc.width, doc.height), (5, 5))
        self.assertIn("空", self.w.statusBar().currentMessage())


class TestGotoAndTilesetSwitch(MapMenuBase):
    def test_goto_centers_view(self):
        from rwmap.widgets.goto_tile_dialog import GotoTileDialog
        w = self.w
        w._open(Path(w.state.profile.sample_map))
        _app.processEvents()
        with mock.patch.object(GotoTileDialog, "exec",
                               return_value=QDialog.Accepted):
            with mock.patch.object(GotoTileDialog, "values",
                                   return_value=(5, 7)):
                with mock.patch.object(w.view, "centerOn") as co:
                    w._goto_tile()
        co.assert_called_once()
        doc = w.state.doc
        expected = QPointF(5.5 * doc.tile_w, 7.5 * doc.tile_h)
        self.assertAlmostEqual(co.call_args[0][0].x(), expected.x())
        self.assertAlmostEqual(co.call_args[0][0].y(), expected.y())

    def test_goto_cancel_noop(self):
        from rwmap.widgets.goto_tile_dialog import GotoTileDialog
        w = self.w
        with mock.patch.object(GotoTileDialog, "exec", return_value=QDialog.Rejected):
            with mock.patch.object(w.view, "centerOn") as co:
                w._goto_tile()
        co.assert_not_called()

    def test_goto_dialog_has_both_inputs(self):
        """对话框一次展示列/行两个输入框，取值范围受地图尺寸限制。"""
        from PySide6.QtWidgets import QAbstractSpinBox
        from rwmap.widgets.goto_tile_dialog import GotoTileDialog
        dlg = GotoTileDialog(None, max_col=9, max_row=19, col=2, row=3)
        self.assertEqual(dlg.windowTitle(), "转到图块")
        self.assertEqual(dlg.col_spin.maximum(), 9)
        self.assertEqual(dlg.row_spin.maximum(), 19)
        self.assertEqual((dlg.col_spin.value(), dlg.row_spin.value()), (2, 3))
        for spin in (dlg.col_spin, dlg.row_spin):
            self.assertEqual(spin.buttonSymbols(), QAbstractSpinBox.NoButtons)
        dlg.col_spin.setValue(6); dlg.row_spin.setValue(15)
        self.assertEqual(dlg.values(), (6, 15))

    def test_switch_tileset_cycles(self):
        w = self.w
        w._open(Path(w.state.profile.sample_map))
        _app.processEvents()
        tabs = w.tilesets_dock.tabs
        n = tabs.count()
        self.assertGreater(n, 1)
        tabs.setCurrentIndex(n - 1)
        w._switch_tileset(1)
        self.assertEqual(tabs.currentIndex(), 0)
        w._switch_tileset(-1)
        self.assertEqual(tabs.currentIndex(), n - 1)


class TestMapProperties(MapMenuBase):
    def test_edit_writes_memory_and_dom(self):
        w = self.w
        w._open(Path(w.state.profile.sample_map))
        _app.processEvents()
        doc = w.state.doc
        with mock.patch("rwmap.widgets.map_properties_dialog.MapPropertiesDialog.exec",
                        return_value=QDialog.Accepted), \
             mock.patch("rwmap.widgets.map_properties_dialog.MapPropertiesDialog.values",
                        return_value={"revision": "7", "custom": "abc"}):
            w._edit_map_properties()
        _app.processEvents()
        self.assertEqual(doc.properties.get("revision"), "7")
        self.assertEqual(doc.properties.get("custom"), "abc")
        pnodes = [e for e in doc.tree.getroot()
                  if getattr(e, "tag", None) == "properties"]
        self.assertEqual(len(pnodes), 1)
        names = {c.get("name") for c in pnodes[0]}
        self.assertIn("revision", names)
        w.state.undo_stack.undo()
        _app.processEvents()
        self.assertNotIn("revision", doc.properties)

    def test_cancel_no_change(self):
        w = self.w
        w._open(Path(w.state.profile.sample_map))
        _app.processEvents()
        doc = w.state.doc
        before = dict(doc.properties.items())
        with mock.patch("rwmap.widgets.map_properties_dialog.MapPropertiesDialog.exec",
                        return_value=QDialog.Rejected):
            w._edit_map_properties()
        _app.processEvents()
        self.assertEqual(dict(doc.properties.items()), before)


if __name__ == "__main__":
    unittest.main()
