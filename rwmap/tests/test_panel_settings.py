"""第二十轮回归：设置菜单 / 面板清单 / 面板布局导入导出。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.app.panel_layout import (  # noqa: E402
    LAYOUT_FORMAT, PANEL_SPECS, apply_default_layout, apply_layout,
    capture_layout, describe_panels, ensure_panel_object_names, iter_panels,
    load_layout, panel_inventory, save_layout,
)
from rwmap.widgets import PanelSettingsDialog  # noqa: E402


class PanelSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)

    def tearDown(self):
        # 每个用例都可能改显隐 / 停靠区，统一复位，避免互相污染
        apply_default_layout(self.w)

    # ---- 菜单 ----
    def test_menu_bar_has_settings_as_fifth_menu(self):
        actions = self.w.menuBar().actions()
        self.assertEqual([a.text() for a in actions],
                         ["文件", "编辑", "视图", "地图", "设置"])

    def test_settings_menu_items(self):
        menu = self.w.menuBar().actions()[-1].menu()
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(texts, ["面板列表设置", "导入面板", "保存当前面板",
                                 "触发器节点图"])
        self.assertEqual(texts[0], self.w.act_panel_settings.text())
        self.assertEqual(texts[1], self.w.act_import_panels.text())
        self.assertEqual(texts[2], self.w.act_save_panels.text())
        self.assertEqual(texts[3], self.w.act_trigger_graph.text())
        self.assertTrue(self.w.act_trigger_graph.isCheckable())

    # ---- 面板清单 ----
    def test_specs_match_real_docks(self):
        docks = self.w.findChildren(QDockWidget)
        self.assertEqual(len(docks), len(PANEL_SPECS))
        self.assertEqual(len(list(iter_panels(self.w))), len(PANEL_SPECS))
        keys = [s.key for s in PANEL_SPECS]
        self.assertEqual(len(keys), len(set(keys)))
        for spec, dock in iter_panels(self.w):
            self.assertIs(dock, getattr(self.w, spec.attr))
            self.assertEqual(dock.objectName(), spec.key)
            self.assertEqual(dock.windowTitle(), spec.title)
            self.assertTrue(dock.objectName())
        self.assertEqual(ensure_panel_object_names(self.w), len(PANEL_SPECS))

    def test_default_dock_areas_unchanged(self):
        w = self.w
        self.assertEqual(w.dockWidgetArea(w.layers_dock), Qt.RightDockWidgetArea)
        self.assertEqual(w.dockWidgetArea(w.properties_dock), Qt.RightDockWidgetArea)
        self.assertEqual(w.dockWidgetArea(w.tilesets_dock), Qt.RightDockWidgetArea)
        self.assertEqual(w.dockWidgetArea(w.tileset_tile_props_dock),
                         Qt.LeftDockWidgetArea)
        # 单位面板已删除；旧的 Triggers / 对象 树也已删掉；节点图是初始隐藏的独立浮动窗口
        self.assertFalse(hasattr(w, "units_dock"))
        self.assertFalse(hasattr(w, "triggers_dock"))
        self.assertTrue(w.trigger_graph_dock.isFloating())
        self.assertTrue(w.trigger_graph_dock.isHidden())
        self.assertFalse(w.act_trigger_graph.isChecked())

    def test_inventory_and_markdown_list(self):
        rows = panel_inventory(self.w)
        self.assertEqual([r["title"] for r in rows],
                         [s.title for s in PANEL_SPECS])
        self.assertTrue(all(r["description"] for r in rows))
        # 节点图默认隐藏，其余面板默认显示
        self.assertEqual([r["visible"] for r in rows],
                         [s.visible for s in PANEL_SPECS])
        self.assertEqual([r["floating"] for r in rows],
                         [s.floating for s in PANEL_SPECS])
        md = describe_panels(self.w)
        for spec in PANEL_SPECS:
            self.assertIn(spec.title, md)
            self.assertIn(spec.description, md)
        self.assertIn("| 面板 | 默认停靠 | 默认显示 | 功能 |", md)

    # ---- 面板列表设置对话框 ----
    def test_dialog_lists_all_panels_with_checkboxes(self):
        d = PanelSettingsDialog(self.w)
        self.assertEqual(d.tree.topLevelItemCount(), len(PANEL_SPECS))
        self.assertEqual(d.tree.columnCount(), 3)
        self.assertEqual(len(d.checked_keys()),
                         sum(s.visible for s in PANEL_SPECS))
        for key, item in d._items.items():
            spec = next(s for s in PANEL_SPECS if s.key == key)
            self.assertEqual(
                item.checkState(0),
                Qt.Checked if spec.visible else Qt.Unchecked)
            self.assertEqual(item.data(0, Qt.UserRole), key)

    def test_dialog_accept_applies_visibility(self):
        d = PanelSettingsDialog(self.w)
        d._set_all(False)
        self.assertEqual(d.checked_keys(), set())
        d._set_all(True)
        d._items["panels.minimap"].setCheckState(0, Qt.Unchecked)
        d.accept()
        self.assertTrue(self.w.minimap_dock.isHidden())
        self.assertFalse(self.w.layers_dock.isHidden())
        self.assertEqual(d.applied_count, len(PANEL_SPECS) - 1)

    def test_dialog_reject_keeps_layout(self):
        d = PanelSettingsDialog(self.w)
        d._set_all(False)
        d.reject()
        for spec, dock in iter_panels(self.w):
            self.assertEqual(dock.isHidden(), not spec.visible)

    def test_dialog_restore_default(self):
        self.w.minimap_dock.hide()
        self.w.addDockWidget(Qt.LeftDockWidgetArea, self.w.layers_dock)
        d = PanelSettingsDialog(self.w)
        d._restore_default()
        self.assertFalse(self.w.minimap_dock.isHidden())
        self.assertEqual(self.w.dockWidgetArea(self.w.layers_dock),
                         Qt.RightDockWidgetArea)
        self.assertEqual(len(d.checked_keys()),
                         sum(s.visible for s in PANEL_SPECS))
        self.assertTrue(self.w.trigger_graph_dock.isHidden())

    # ---- 布局采集 / 应用 ----
    def test_capture_apply_roundtrip(self):
        before = capture_layout(self.w)
        self.assertEqual(before["format"], LAYOUT_FORMAT)
        self.assertEqual(len(before["panels"]), len(PANEL_SPECS))
        self.assertTrue(before["qt_state"])
        self.w.minimap_dock.hide()
        self.w.tilesets_dock.hide()
        report = apply_layout(self.w, before)
        self.assertEqual(report,
                         {"restored": len(PANEL_SPECS), "unknown": [], "warnings": []})
        self.assertFalse(self.w.minimap_dock.isHidden())
        self.assertFalse(self.w.tilesets_dock.isHidden())

    def test_apply_layout_rejects_bad_data(self):
        with self.assertRaises(ValueError):
            apply_layout(self.w, {"format": "other/1", "panels": []})
        with self.assertRaises(ValueError):
            apply_layout(self.w, [])
        with self.assertRaises(ValueError):
            apply_layout(self.w, {"foo": 1})

    def test_apply_layout_reports_unknown_and_missing_state(self):
        report = apply_layout(self.w, {
            "format": LAYOUT_FORMAT,
            "panels": [{"key": "panels.unknown", "visible": True}],
        })
        self.assertEqual(report["restored"], 0)
        self.assertEqual(report["unknown"], ["panels.unknown"])
        self.assertTrue(any("Qt 状态" in s for s in report["warnings"]))

    # ---- 文件导入 / 导出 ----
    def test_save_and_load_layout_file(self):
        with tempfile.TemporaryDirectory() as td:
            target = save_layout(self.w, Path(td) / "我的布局")
            self.assertEqual(target.suffix, ".json")
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(data["format"], LAYOUT_FORMAT)
            self.assertEqual(len(data["panels"]), len(PANEL_SPECS))
            self.w.minimap_dock.hide()
            self.w.properties_dock.hide()
            report = load_layout(self.w, target)
            self.assertEqual(report["restored"], len(PANEL_SPECS))
            self.assertFalse(self.w.minimap_dock.isHidden())
            self.assertFalse(self.w.properties_dock.isHidden())

    def test_load_broken_json_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_layout(self.w, path)

    # ---- 主窗口三个动作的端到端 ----
    def test_save_and_import_actions_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "panels.json")
            with patch("rwmap.app.main_window.QFileDialog.getSaveFileName",
                       return_value=(path, "")):
                self.w.act_save_panels.trigger()
            self.assertTrue(Path(path).is_file())
            self.assertIn("已保存当前面板布局", self.w.statusBar().currentMessage())

            self.w.minimap_dock.hide()
            with patch("rwmap.app.main_window.QFileDialog.getOpenFileName",
                       return_value=(path, "")):
                self.w.act_import_panels.trigger()
            self.assertFalse(self.w.minimap_dock.isHidden())
            self.assertIn("已导入面板布局", self.w.statusBar().currentMessage())

    def test_actions_cancel_when_no_path(self):
        with patch("rwmap.app.main_window.QFileDialog.getSaveFileName",
                   return_value=("", "")), \
             patch("rwmap.app.main_window.QFileDialog.getOpenFileName",
                   return_value=("", "")):
            self.w.act_save_panels.trigger()
            self.w.act_import_panels.trigger()

    def test_import_bad_file_shows_warning_without_hanging(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text("{oops", encoding="utf-8")
            with patch("rwmap.app.main_window.QFileDialog.getOpenFileName",
                       return_value=(str(path), "")), \
                 patch("rwmap.app.main_window.QMessageBox.warning") as warn:
                self.w.act_import_panels.trigger()
            self.assertTrue(warn.called)


if __name__ == "__main__":
    unittest.main()
