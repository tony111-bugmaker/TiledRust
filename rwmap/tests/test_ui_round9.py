"""第九轮回归：自由摆放 / Shift 吸附、缩放手柄、选择光标、工具栏与图块集编号。

第十轮把「蓝色轮廓 + 白色方块手柄」换成「8 个纯黄色箭头」，相关断言见
``test_ui_round10.py``。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolBar  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.view.object_layer_item import (  # noqa: E402
    effective_handle_size, handle_hit, handle_positions,
)
from rwmap.widgets.object_toolbar import make_select_cursor  # noqa: E402
from rwmap.widgets import tilesets_dock as td  # noqa: E402

SHIFT = "rwmap.app.main_window.QApplication.keyboardModifiers"


def _blue_pixels(pix) -> int:
    img = (pix if isinstance(pix, QImage) else pix.toImage()).convertToFormat(
        QImage.Format_ARGB32)
    count = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 120 and c.blue() > 190 and c.red() < 150 \
                    and c.green() > 90:
                count += 1
    return count


class Round9UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.w = MainWindow(auto_open=False)
        cls.w.resize(1400, 900)
        cls.w.show()
        QApplication.processEvents()
        cls.sample = Path(cls.w.state.profile.sample_map)
        if not cls.sample.is_file():
            raise unittest.SkipTest("缺少示例地图")

    def setUp(self):
        w = self.w
        w._open(self.sample)
        w.state.undo_stack.clear()
        w.scene.set_selected_object(None)
        w._set_object_tool("brush")

    # ---- 工具 ----
    def _layer(self) -> ObjectLayer:
        return next(l for l in self.w.state.doc.layers
                    if isinstance(l, ObjectLayer))

    def _add(self, shape="rect", x=100.0, y=100.0,
             width=60.0, height=40.0, **extra):
        layer = self._layer()
        obj = MapObject(object_id=90000 + len(layer.objects),
                        name="测试对象", shape=shape, x=x, y=y,
                        width=width, height=height, **extra)
        layer.objects.append(obj)
        layer.dirty = True
        self.w.state.doc.dirty = True
        self.w.scene.set_selected_object(obj)
        return obj

    def _current(self, object_id):
        return next(o for o in self._layer().objects
                    if o.object_id == object_id)

    # ---- 1. 默认自由，Shift 才吸附 ----
    def test_drag_creation_is_free_by_default(self):
        w = self.w
        w._set_object_tool("rect")
        w.view.objectToolPressed.emit(103.0, 97.0)
        w.view.objectToolMoved.emit(157.0, 143.0)
        w.view.objectToolReleased.emit(157.0, 143.0)
        obj = self._layer().objects[-1]
        self.assertEqual((obj.x, obj.y, obj.width, obj.height),
                         (103.0, 97.0, 54.0, 46.0))

    def test_shift_snaps_creation_to_grid(self):
        w = self.w
        w._set_object_tool("rect")
        with patch(SHIFT, return_value=Qt.ShiftModifier):
            w.view.objectToolPressed.emit(103.0, 97.0)
            w.view.objectToolMoved.emit(157.0, 143.0)
            w.view.objectToolReleased.emit(157.0, 143.0)
        obj = self._layer().objects[-1]
        self.assertEqual((obj.x, obj.y, obj.width, obj.height),
                         (100.0, 100.0, 60.0, 40.0))

    def test_snap_xy_switch_is_explicit(self):
        self.assertEqual(self.w._snap_xy(103.0, 97.0, False), (103.0, 97.0))
        self.assertEqual(self.w._snap_xy(103.0, 97.0, True), (100.0, 100.0))

    def test_move_is_free_and_shift_snaps_per_cell(self):
        w = self.w
        obj = self._add(x=33.0, y=47.0)
        w.scene.set_selected_objects([obj])
        # 中心点落在选中包围框内 → 开始拖动整组
        w.view.objectPressed.emit(53.0, 67.0)
        self.assertIsNotNone(w._sel_move_drag)
        w.view.objectMoved.emit(78.0, 92.0)
        w.view.objectReleased.emit(78.0, 92.0)
        # 自由移动：位移 = 鼠标位移 (25,25)
        self.assertEqual((self._current(obj.object_id).x,
                          self._current(obj.object_id).y), (58.0, 72.0))

        other = self._add(x=33.0, y=47.0)
        w.scene.set_selected_objects([other])
        with patch(SHIFT, return_value=Qt.ShiftModifier):
            w.view.objectPressed.emit(53.0, 67.0)
            w.view.objectMoved.emit(78.0, 92.0)
            w.view.objectReleased.emit(78.0, 92.0)
        # Shift：位移按整格吸附（25→20），即「鼠标走一格、对象走一格」
        self.assertEqual((self._current(other.object_id).x,
                          self._current(other.object_id).y), (53.0, 67.0))

    # ---- 2. 缩放手柄 ----
    def test_handle_positions_cover_eight_directions(self):
        rect = QRectF(10, 20, 60, 40)
        spots = handle_positions(rect, 1.0)
        self.assertEqual(set(spots), {"nw", "n", "ne", "e",
                                      "se", "s", "sw", "w"})
        self.assertEqual((spots["nw"].x(), spots["nw"].y()), (10.0, 20.0))
        self.assertEqual((spots["se"].x(), spots["se"].y()), (70.0, 60.0))
        self.assertEqual(handle_hit(rect, 70.0, 60.0, 1.0), "se")
        self.assertIsNone(handle_hit(rect, 40.0, 40.0, 1.0))

    def test_handles_shrink_on_small_objects(self):
        small = QRectF(0, 0, 20, 20)
        self.assertLessEqual(effective_handle_size(small, 0.2), 8.0)
        self.assertIsNone(handle_hit(small, 10.0, 10.0, 0.2))

    # 注：对象缩放手柄（8 个黄色箭头 + 拖拽缩放）已按需求移除；
    # 选中改为「整体包围框 + 框内拖动移动」，相关回归见 test_object_selection_group.py。

    # ---- 3. 选择光标 ----
    def test_select_tool_uses_blue_outlined_cursor(self):
        w = self.w
        w._set_object_tool("select")
        self.assertNotEqual(w.view.cursor().shape(), Qt.ArrowCursor)
        self.assertGreater(_blue_pixels(make_select_cursor().pixmap()), 20)

    # ---- 4. 工具栏 ----
    def test_main_toolbar_hides_menu_duplicates(self):
        w = self.w
        main = next(b for b in w.findChildren(QToolBar)
                    if b.windowTitle() == "主工具栏")
        texts = [a.text() for a in main.actions()]
        for duplicated in ("打开 .tmx", "保存", "画笔 (B)", "橡皮 (E)",
                           "网格", "生成缩略图", "绘制时自动地图"):
            self.assertNotIn(duplicated, texts)
        file_menu = next(m for m in w.menuBar().actions()
                         if m.text() == "文件")
        self.assertIn("打开 .tmx",
                      [a.text() for a in file_menu.menu().actions()])

    def test_pen_button_right_of_brush(self):
        w = self.w
        names = [a.text() for a in w.object_toolbar.group.actions()]
        # 钢笔紧挨图章（瓦片画笔）右侧，其后才是橡皮 / 填充。
        self.assertEqual(names[:4], ["瓦片画笔", "钢笔", "橡皮", "填充"])
        # 按钮方块本身无描边（蓝色只画在图标图形里）。
        self.assertIn("border: none", w.object_toolbar.styleSheet())
        w._set_object_tool("eraser")
        self.assertEqual(w.state.tool_id, "eraser")
        self.assertEqual(w.view.object_mode, "brush")
        self.assertEqual(w.object_toolbar.current_tool(), "eraser")
        w.act_brush.trigger()
        self.assertEqual(w.state.tool_id, "brush")
        self.assertEqual(w.object_toolbar.current_tool(), "brush")

    def test_pencil_icon_is_not_empty(self):
        action = self.w.object_toolbar.actions_by_id["brush"]
        self.assertFalse(action.icon().isNull())
        self.assertFalse(
            self.w.object_toolbar.actions_by_id["eraser"].icon().isNull())

    # ---- 5. 图块集不显示编号 ----
    def test_tileset_never_shows_tile_ids(self):
        w = self.w
        self.assertFalse(td.SHOW_TILE_IDS)
        pal = w.tilesets_dock.palette
        self.assertNotIn("gid", pal.hover_text(0).lower())
        for i in range(w.tilesets_dock.tabs.count()):
            self.assertNotIn("gid",
                             w.tilesets_dock.tabs.tabToolTip(i).lower())


if __name__ == "__main__":
    unittest.main()
