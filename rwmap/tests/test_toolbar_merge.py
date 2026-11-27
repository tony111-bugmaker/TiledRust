"""第三十四轮回归：两个选择按钮合并 + 填充图标改「斜桶滴漆」。

覆盖：
1. 工具栏只剩一个「选择 / 框选」鼠标箭头按钮，旧 ``tileselect`` 只作兼容别名；
2. S / M 两个快捷键都切到同一个工具；
3. 一个工具同时具备：瓦片层框选瓦片、对象层框选多个对象、点对象选中 /
   拖动 / 缩放箭头、右键菜单、自定义鼠标光标；
4. 填充图标是斜放油漆桶 + 右下滴落的颜料，没有直立桶的提手。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeySequence, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.view.map_scene import ObjectSelectionBoxItem  # noqa: E402
from rwmap.widgets.object_toolbar import (  # noqa: E402
    TOOLS, TOOL_EXTRA_SHORTCUTS, canonical_tool_id, make_object_icon,
)


def _build_window():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    w = MainWindow(auto_open=False)
    tile = TileLayer(width=10, height=10, name="Ground",
                     cells=[0] * 100, readonly=False)
    objs = [
        MapObject(object_id=1, name="A", shape="rect",
                  x=40, y=40, width=40, height=40),
        MapObject(object_id=2, name="B", shape="rect",
                  x=120, y=40, width=40, height=40),
        MapObject(object_id=3, name="C", shape="rect",
                  x=400, y=400, width=40, height=40),
    ]
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    doc = MapDocument(width=30, height=30, tile_w=20, tile_h=20,
                      layers=[tile, obj_layer], tilesets=[])
    w.state.doc = doc
    w.scene.set_document(doc)
    w.state.profile.write_enabled = True
    w.state.undo_stack.clear()
    return w, tile, obj_layer, objs


class ToolbarMergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w, self.tile, self.obj_layer, self.objs = _build_window()

    # ---- 1. 按钮与快捷键 ----
    def test_only_one_select_button(self):
        ids = [t[0] for t in TOOLS]
        self.assertNotIn("tileselect", ids)
        self.assertIn("select", ids)
        self.assertEqual(len(ids), 16)          # 合并后 15 个 + 标尺
        self.assertEqual(ids[:6],
                         ["brush", "pen", "eraser", "fill", "select", "ruler"])
        self.assertEqual(set(self.w.object_toolbar.buttons_by_id), set(ids))
        self.assertEqual(TOOL_EXTRA_SHORTCUTS["select"], ("M",))

    def test_select_action_has_both_s_and_m_shortcuts(self):
        action = self.w.object_toolbar.actions_by_id["select"]
        keys = action.shortcuts()
        self.assertIn(QKeySequence("S"), keys)
        self.assertIn(QKeySequence("M"), keys)
        self.assertIn("S / M", action.toolTip())
        self.assertEqual(action.text(), "选择 / 框选")

    def test_legacy_tileselect_alias_maps_to_merged_tool(self):
        self.assertEqual(canonical_tool_id("tileselect"), "select")
        self.w._set_object_tool("tileselect")
        self.assertEqual(self.w._object_tool, "select")
        self.assertEqual(self.w.view.object_mode, "select")
        self.assertEqual(self.w.object_toolbar.current_tool(), "select")
        self.assertEqual(self.w.object_toolbar.hint.text(), "选择 / 框选")

    def test_merged_button_uses_mouse_arrow_icon(self):
        action = self.w.object_toolbar.actions_by_id["select"]
        got = action.icon().pixmap(28, 28).toImage().convertToFormat(
            QImage.Format_ARGB32)
        arrow = make_object_icon("select").pixmap(28, 28).toImage().convertToFormat(
            QImage.Format_ARGB32)
        old_box = make_object_icon("tileselect").pixmap(28, 28).toImage().convertToFormat(
            QImage.Format_ARGB32)
        self.assertEqual(got, arrow)            # 用的就是鼠标箭头
        self.assertNotEqual(got, old_box)       # 不是被合并掉的虚线框

    # ---- 2. 一个工具，两套框选 + 点选对象 ----
    def test_drag_blank_on_tile_layer_selects_tiles(self):
        w = self.w
        w._on_layer_selected(self.tile)
        w._set_object_tool("select")
        w.view.objectToolPressed.emit(200.0, 200.0)
        w.view.objectToolMoved.emit(260.0, 240.0)
        w.view.objectToolReleased.emit(260.0, 240.0)
        self.assertEqual(w._tile_selection, (10, 10, 3, 2))
        self.assertFalse(w._obj_marquee)
        self.assertEqual(len(w.scene.selected_objects), 0)

    def test_drag_blank_on_object_layer_selects_objects(self):
        w = self.w
        w._on_layer_selected(self.obj_layer)
        w._set_object_tool("select")
        w.view.objectToolPressed.emit(20.0, 20.0)
        w.view.objectToolMoved.emit(180.0, 100.0)
        w.view.objectToolReleased.emit(180.0, 100.0)
        got = {o.object_id for o in w.scene.selected_objects}
        self.assertEqual(got, {1, 2})
        # 用户要求：对象层框选只选对象、不碰瓦片选区；框到多个对象时
        # 选框贴合对象外接矩形并保留。
        self.assertIsNone(w._tile_selection)
        self.assertIsNotNone(w._sel_rect)

    def test_click_object_from_tile_layer_is_ignored(self):
        """瓦片层激活时对象对鼠标透明：点对象不选中、不出 8 个黄色箭头。"""
        w = self.w
        w._on_layer_selected(self.tile)
        w._set_object_tool("select")
        w.view.objectToolPressed.emit(60.0, 60.0)
        w.view.objectToolReleased.emit(60.0, 60.0)
        self.assertEqual(list(w.scene.selected_objects), [])
        item = w.scene._obj_sel_box
        self.assertTrue(item is None or not item.isVisible())
        self.assertIsNone(w._tile_selection)   # 点对象不该顺手框出瓦片选区

    def test_drag_object_is_one_undo_step(self):
        w = self.w
        w._on_layer_selected(self.obj_layer)
        w._set_object_tool("select")
        w.view.objectToolPressed.emit(60.0, 60.0)
        w.view.objectToolMoved.emit(100.0, 60.0)
        w.view.objectToolReleased.emit(100.0, 60.0)
        moved = next(o for o in self.obj_layer.objects if o.object_id == 1)
        self.assertEqual(moved.x, 80.0)
        self.assertEqual(w.state.undo_stack.count(), 1)
        w.state.undo_stack.undo()
        # 撤销会按层快照还原对象列表，必须按 id 重新取对象。
        restored = next(o for o in self.obj_layer.objects if o.object_id == 1)
        self.assertEqual(restored.x, 40.0)

    def test_right_click_menu_still_available(self):
        w = self.w
        w._on_layer_selected(self.obj_layer)
        w._set_object_tool("select")
        w.scene.set_selected_objects([self.objs[0], self.objs[1]])
        with mock.patch.object(w, "_show_object_context_menu") as popup:
            w._on_object_right_press(60.0, 60.0)
        popup.assert_called_once()

    def test_real_m_and_s_keys_switch_to_merged_tool(self):
        """QAction 挂两个快捷键：真实按键 M / S 都切到同一个工具。"""
        w = self.w
        w.resize(1200, 800)
        w.show()
        QApplication.processEvents()
        for key in (Qt.Key_M, Qt.Key_S):
            w._set_object_tool("brush")
            QTest.keyClick(w, key)
            QApplication.processEvents()
            self.assertEqual(w._object_tool, "select")
            self.assertEqual(w.view.object_mode, "select")
            self.assertEqual(w.object_toolbar.current_tool(), "select")

    def test_real_mouse_drag_on_object_layer_marquees(self):
        """走完整 QMouseEvent → mapToScene 链路，确认合并工具没有吞事件。"""
        w = self.w
        w.resize(1000, 700)
        w.show()
        QApplication.processEvents()
        w.view.set_document(w.state.doc)
        QApplication.processEvents()
        w._on_layer_selected(self.obj_layer)
        w._set_object_tool("select")

        def send(kind, button, buttons, sx, sy):
            pos = QPointF(w.view.mapFromScene(QPointF(float(sx), float(sy))))
            if kind == "press":
                w.view.mousePressEvent(QMouseEvent(
                    QEvent.Type.MouseButtonPress, pos, button, buttons,
                    Qt.NoModifier))
            elif kind == "move":
                w.view.mouseMoveEvent(QMouseEvent(
                    QEvent.Type.MouseMove, pos, Qt.NoButton, buttons,
                    Qt.NoModifier))
            else:
                w.view.mouseReleaseEvent(QMouseEvent(
                    QEvent.Type.MouseButtonRelease, pos, button, Qt.NoButton,
                    Qt.NoModifier))

        send("press", Qt.LeftButton, Qt.LeftButton, 20.0, 20.0)
        send("move", Qt.NoButton, Qt.LeftButton, 180.0, 100.0)
        send("release", Qt.LeftButton, Qt.NoButton, 180.0, 100.0)
        self.assertEqual({o.object_id for o in w.scene.selected_objects}, {1, 2})

    def test_merged_tool_uses_custom_mouse_cursor(self):
        w = self.w
        w._set_object_tool("select")
        self.assertNotEqual(w.view.cursor().shape(), Qt.ArrowCursor)
        w._restore_select_cursor()
        self.assertNotEqual(w.view.cursor().shape(), Qt.ArrowCursor)


class FillIconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.img = make_object_icon("fill").pixmap(28, 28).toImage().convertToFormat(
            QImage.Format_ARGB32)

    @staticmethod
    def _blue(x, y):
        c = FillIconTest.img.pixelColor(x, y)
        return (c.alpha() > 150 and c.blue() > 170
                and c.blue() > c.red() + 40 and c.green() > 80)

    def _count(self, predicate):
        return sum(1 for y in range(28) for x in range(28)
                   if self._blue(x, y) and predicate(x, y))

    def test_paint_drips_from_lower_right(self):
        # 桶口右下有液柱 + 两滴颜料；旧的向下三角箭头不通不过这个断言。
        self.assertGreaterEqual(self._count(lambda x, y: x >= 19 and y >= 19), 8)

    def test_bucket_is_tilted(self):
        # 桶身左下角伸到 x<=6 / y>=11，正立桶（x≈6~18）不会在这里有像素。
        self.assertGreater(self._count(lambda x, y: x <= 6 and y >= 11), 5)

    def test_no_handle_above_bucket(self):
        # 提手是一条悬在桶口上方的圆弧；新图标桶口以上（左上角）是空的。
        # 桶身倾斜后左上 1/4 区域只有少量桶壁像素，绝不会出现独立的弧线。
        self.assertLess(self._count(lambda x, y: x <= 5 and y <= 3), 2)


if __name__ == "__main__":
    unittest.main()
