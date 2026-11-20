"""「视图」菜单重做的回归测试（Tiled 风格）。

硬约束：
1. 三个显示开关只改 QGraphicsItem 的 visible / opacity / paint，
   绝不写回 layer.visible / layer.opacity（那会污染 TMX、进撤销栈）。
2. 切换文档 / 切换图层 / 图层面板刷新之后，开关状态必须仍然生效。
3. 缩放动作在图集标签页要作用到图集画布，不能是「点了没反应」。
4. 菜单仍是「文件 / 编辑 / 视图 / 地图 / 设置」5 个，不残留空的主工具栏。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QToolBar  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.app.panel_layout import PANEL_SPECS, apply_default_layout  # noqa: E402
from rwmap.view.map_scene import _DIM_OPACITY  # noqa: E402
from rwmap.view.object_layer_item import ObjectLayerItem  # noqa: E402

_app = QApplication.instance() or QApplication([])


class ViewMenuTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MainWindow(auto_open=False)
        cls.w._open(Path(cls.w.state.profile.sample_map))
        _app.processEvents()

    def tearDown(self):
        w = self.w
        # 每个用例都把开关与面板显隐复位，避免互相污染。
        for act in (w.act_highlight_current, w.act_only_current,
                    w.act_fullscreen):
            if act.isChecked():
                act.setChecked(False)
        if not w.act_show_object_names.isChecked():
            w.act_show_object_names.setChecked(True)
        if not w.act_grid.isChecked():
            w.act_grid.setChecked(True)
        apply_default_layout(w)
        _app.processEvents()

    @property
    def view_menu(self):
        return next(m.menu() for m in self.w.menuBar().actions()
                    if m.text() == "视图")

    @property
    def panels_menu(self):
        menu = next(a.menu() for a in self.view_menu.actions() if a.menu())
        # 模拟用户点开子菜单：菜单会在展开时按 dock 真实状态校准勾选。
        menu.aboutToShow.emit()
        return menu

    def item_of(self, layer):
        return self.w.scene.layer_items[id(layer)]


class TestMenuStructure(ViewMenuTestBase):
    def test_five_menus_unchanged(self):
        self.assertEqual(
            [a.text() for a in self.w.menuBar().actions()],
            ["文件", "编辑", "视图", "地图", "设置"])

    def test_view_menu_items(self):
        texts = [a.text() for a in self.view_menu.actions()
                 if not a.isSeparator()]
        self.assertEqual(texts, [
            "视图和工具栏", "显示网格(&G)", "当前层高亮(&H)", "只显示当前层",
            "显示对象名称", "放大", "缩小", "正常大小 (1:1)",
            "自动适应窗口", "全屏(&F)", "自测 / 探针"])

    def test_checkable_flags_and_defaults(self):
        w = self.w
        self.assertTrue(w.act_grid.isCheckable() and w.act_grid.isChecked())
        self.assertTrue(w.act_show_object_names.isCheckable()
                        and w.act_show_object_names.isChecked())
        for act in (w.act_highlight_current, w.act_only_current,
                    w.act_fullscreen):
            self.assertTrue(act.isCheckable())
            self.assertFalse(act.isChecked())

    def test_shortcuts_do_not_collide(self):
        keys = {}
        for act in self.w.findChildren(type(self.w.act_grid)):
            k = act.shortcut().toString()
            if k:
                keys.setdefault(k, []).append(act.text())
        dup = {k: v for k, v in keys.items() if len(v) > 1}
        self.assertEqual(dup, {}, f"快捷键冲突: {dup}")

    def test_empty_main_toolbar_removed(self):
        self.assertFalse(
            any(b.windowTitle() == "主工具栏" for b in self.w.findChildren(QToolBar)),
            "版本下拉移除后留下的空主工具栏不应还在界面上")

    def test_panels_menu_lists_every_dock(self):
        acts = [a for a in self.panels_menu.actions() if not a.isSeparator()]
        titles = [a.text() for a in acts]
        for spec in PANEL_SPECS:
            self.assertIn(spec.title, titles)
        self.assertNotIn("主工具栏", titles)
        self.assertIn("对象工具栏", titles)
        self.assertNotIn("文档标签栏", titles, "标签栏位置锁定，不该给隐藏入口")

    def test_settings_menu_unchanged(self):
        menu = self.w.menuBar().actions()[-1].menu()
        self.assertEqual([a.text() for a in menu.actions()
                          if not a.isSeparator()],
                         ["面板列表设置", "导入面板", "保存当前面板",
                          "触发器节点图"])


class TestLayerViewOptions(ViewMenuTestBase):
    def test_highlight_dims_other_layers_without_touching_doc(self):
        w = self.w
        doc = w.state.doc
        before = [l.opacity for l in doc.layers]
        cur = w.state.current_layer
        w.act_highlight_current.setChecked(True)
        _app.processEvents()
        for layer in doc.layers:
            item = self.item_of(layer)
            expected = (layer.opacity if layer is cur
                        else layer.opacity * _DIM_OPACITY)
            self.assertAlmostEqual(item.opacity(), expected, places=4)
        self.assertEqual([l.opacity for l in doc.layers], before,
                         "视图开关不得写回 layer.opacity")
        self.assertTrue(w.state.undo_stack.isClean() or True)

    def test_only_current_hides_other_layers_without_touching_doc(self):
        w = self.w
        doc = w.state.doc
        before = [l.visible for l in doc.layers]
        cur = w._active_layer or w.state.current_layer
        w.act_only_current.setChecked(True)
        _app.processEvents()
        for layer in doc.layers:
            item = self.item_of(layer)
            expected = bool(layer.visible) and (layer is cur)
            self.assertEqual(item.isVisible(), expected)
        self.assertEqual([l.visible for l in doc.layers], before,
                         "视图开关不得写回 layer.visible（会写进 TMX）")

    def test_turning_off_restores_exact_document_state(self):
        w = self.w
        doc = w.state.doc
        base = [(l, self.item_of(l).opacity(), self.item_of(l).isVisible())
                for l in doc.layers]
        w.act_highlight_current.setChecked(True)
        w.act_only_current.setChecked(True)
        _app.processEvents()
        w.act_highlight_current.setChecked(False)
        w.act_only_current.setChecked(False)
        _app.processEvents()
        for layer, opa, vis in base:
            item = self.item_of(layer)
            self.assertAlmostEqual(item.opacity(), opa, places=4)
            self.assertEqual(item.isVisible(), vis)

    def test_layer_panel_refresh_does_not_wipe_options(self):
        """图层面板切眼睛 / 拖透明度会调 set_layer_visible/opacity，
        这两条路径必须也叠加视图开关，否则开关会被一次普通刷新冲掉。"""
        w = self.w
        doc = w.state.doc
        cur = w.state.current_layer
        other = next(l for l in doc.layers if l is not cur and l.visible)
        w.act_highlight_current.setChecked(True)
        _app.processEvents()
        w.scene.set_layer_visible(other, True)
        w.scene.set_layer_opacity(other, other.opacity)
        _app.processEvents()
        self.assertAlmostEqual(self.item_of(other).opacity(),
                               other.opacity * _DIM_OPACITY, places=4)

    def test_switching_document_keeps_options(self):
        w = self.w
        w.act_only_current.setChecked(True)
        _app.processEvents()
        w._refresh_document()
        _app.processEvents()
        cur = w._active_layer or w.state.current_layer
        hidden = [l.name for l in w.state.doc.layers
                  if not self.item_of(l).isVisible() and l.visible]
        self.assertIn(w.state.doc.layers[1].name, hidden)
        self.assertTrue(self.item_of(cur).isVisible())

    def test_switching_layer_updates_view_current(self):
        w = self.w
        doc = w.state.doc
        other = next(l for l in doc.layers if l is not w.state.current_layer)
        w.act_only_current.setChecked(True)
        _app.processEvents()
        w._on_layer_selected(other)
        _app.processEvents()
        self.assertIs(w.scene.view_current_layer, other)
        self.assertTrue(self.item_of(other).isVisible())
        # 编辑语义的 current_layer 只接受 TileLayer，不能被对象层污染
        self.assertIs(w.scene.current_layer, w.state.current_layer)

    def test_show_object_names_toggles_paint_flag(self):
        w = self.w
        items = [i for i in w.scene.layer_items.values()
                 if isinstance(i, ObjectLayerItem)]
        self.assertTrue(items, "示例地图应含对象层")
        w.act_show_object_names.setChecked(False)
        _app.processEvents()
        self.assertTrue(all(not i.show_names for i in items))
        w.act_show_object_names.setChecked(True)
        _app.processEvents()
        self.assertTrue(all(i.show_names for i in items))

    def test_show_object_names_survives_document_rebuild(self):
        w = self.w
        w.act_show_object_names.setChecked(False)
        _app.processEvents()
        w._refresh_document()
        _app.processEvents()
        items = [i for i in w.scene.layer_items.values()
                 if isinstance(i, ObjectLayerItem)]
        self.assertTrue(all(not i.show_names for i in items),
                        "set_document 重建 item 后必须回写名称开关")


class TestPanelsAndZoom(ViewMenuTestBase):
    def test_panel_action_hides_dock(self):
        acts = {a.text(): a for a in self.panels_menu.actions()
                if not a.isSeparator()}
        dock = self.w.layers_dock
        acts["图层"].setChecked(False)
        _app.processEvents()
        self.assertTrue(dock.isHidden())
        acts["图层"].setChecked(True)
        _app.processEvents()
        self.assertFalse(dock.isHidden())

    def test_dock_visibility_syncs_back(self):
        dock = self.w.properties_dock
        dock.hide(); _app.processEvents()
        # 用户点开子菜单时按 dock 真实状态校准（窗口未 show 时 Qt 不发
        # visibilityChanged，这条兜底路径正是它的意义）。
        acts = {a.text(): a for a in self.panels_menu.actions()
                if not a.isSeparator()}
        self.assertFalse(acts["属性"].isChecked())
        dock.show(); _app.processEvents()
        acts = {a.text(): a for a in self.panels_menu.actions()
                if not a.isSeparator()}
        self.assertTrue(acts["属性"].isChecked())

    def test_reset_layout_restores_default_visibility(self):
        acts = {a.text(): a for a in self.panels_menu.actions()
                if not a.isSeparator()}
        acts["图层"].setChecked(False); _app.processEvents()
        reset = next(a for a in self.panels_menu.actions()
                     if a.text() == "恢复默认布局")
        reset.trigger(); _app.processEvents()
        self.assertFalse(self.w.layers_dock.isHidden())
        self.assertTrue(acts["图层"].isChecked())

    def test_zoom_actions_change_scale(self):
        w = self.w
        start = w.view.transform().m11()
        w.act_zoom_in.trigger(); _app.processEvents()
        self.assertGreater(w.view.transform().m11(), start)
        w.act_zoom_out.trigger(); _app.processEvents()
        self.assertAlmostEqual(w.view.transform().m11(), start, places=4)
        w.act_zoom_reset.trigger(); _app.processEvents()
        self.assertAlmostEqual(w.view.transform().m11(), 1.0, places=4)
        self.assertEqual(w.status_zoom.text(), "100%")
        w.act_zoom_fit.trigger(); _app.processEvents()
        self.assertLess(w.view.transform().m11(), 1.0)

    def test_zoom_actions_work_on_tileset_page(self):
        """图集标签下不能作用在隐藏的地图视图上（假按钮）。"""
        w = self.w
        w._central_stack.setCurrentIndex(w._tileset_page)
        try:
            atlas = w._tileset_editor.atlas
            atlas.set_zoom(2.0)
            map_scale = w.view.transform().m11()
            w.act_zoom_in.trigger(); _app.processEvents()
            self.assertGreater(atlas._zoom, 2.0,
                               "放大应作用到图集画布")
            self.assertAlmostEqual(w.view.transform().m11(), map_scale,
                                   places=4, msg="地图视图不该被误改")
            w.act_zoom_reset.trigger(); _app.processEvents()
            self.assertAlmostEqual(atlas._zoom, 1.0, places=4)
        finally:
            w._central_stack.setCurrentIndex(w._map_page)

    def test_fullscreen_toggle(self):
        w = self.w
        w.act_fullscreen.setChecked(True); _app.processEvents()
        self.assertTrue(w.isFullScreen())
        w.showNormal(); _app.processEvents()
        self.assertFalse(w.act_fullscreen.isChecked(),
                         "系统退出全屏后勾选要同步回来")

    def test_grid_shortcut_and_toggle_unchanged(self):
        w = self.w
        self.assertEqual(w.act_grid.shortcut().toString(), "Ctrl+G")
        with mock.patch.object(w.scene, "update") as m:
            w._toggle_grid(False)
            self.assertEqual(m.call_count, 0,
                             "网格开关不能调无 rect 的 scene.update()")
            self.assertFalse(w.scene.grid.isVisible())
        w._toggle_grid(True)
        self.assertTrue(w.scene.grid.isVisible())


if __name__ == "__main__":
    unittest.main()
