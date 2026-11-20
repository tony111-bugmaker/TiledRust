"""第十轮回归：纯黄色缩放箭头、图标内蓝色描边、默认 UI 布局。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from collections import Counter  # noqa: E402

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolBar  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.view.object_layer_item import (  # noqa: E402
    HANDLE_VIEW_SIZE, OBJECT_YELLOW, ObjectLayerItem, draw_selection,
    handle_positions,
)
from rwmap.widgets.object_toolbar import TOOLS, make_object_icon  # noqa: E402


def _pixels(img: QImage) -> Counter:
    img = img.convertToFormat(QImage.Format_ARGB32)
    return Counter(
        (img.pixelColor(x, y).red(), img.pixelColor(x, y).green(),
         img.pixelColor(x, y).blue(), img.pixelColor(x, y).alpha())
        for y in range(img.height()) for x in range(img.width())
    )


def _is_yellow(c) -> bool:
    r, g, b, a = c
    return a > 180 and r > 200 and 150 < g < 240 and b < 110


def _is_blue(c) -> bool:
    r, g, b, a = c
    return a > 150 and b > 170 and b > r + 40 and g > 80


def _is_white(c) -> bool:
    r, g, b, a = c
    return a > 180 and r > 225 and g > 225 and b > 225


class Round10UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.w = MainWindow(auto_open=False)
        cls.w.resize(1400, 900)
        cls.w.show()
        QApplication.processEvents()

    # ---- 1. 8 个纯黄色箭头 ----
    def test_arrow_size_is_one_and_a_half_times_previous(self):
        self.assertAlmostEqual(HANDLE_VIEW_SIZE, 13.5)   # 9.0 * 1.5

    def test_selection_draws_only_yellow_arrows(self):
        """绿色底上画选中态：只有纯黄箭头，没有蓝线 / 白框 / 白方块。"""
        bg = (20, 80, 20, 255)
        img = QImage(160, 130, QImage.Format_ARGB32)
        img.fill(QColor(*bg[:3]))
        p = QPainter(img)
        draw_selection(p, QRectF(30, 30, 100, 70), 1.0)
        p.end()
        drawn = {c for c in _pixels(img) if c != bg}

        def yellowish(c):
            # 抗锯齿边缘是同色半透明像素，按 RGB 判定色相即可。
            return c[3] > 0 and c[0] > 200 and 150 < c[1] < 240 and c[2] < 110

        self.assertTrue(drawn, "没画出任何箭头")
        self.assertTrue(all(yellowish(c) for c in drawn),
                        f"出现了非黄色的描边/背景: "
                        f"{[c for c in drawn if not yellowish(c)][:6]}")
        self.assertFalse(any(_is_blue(c) for c in drawn), "还残留蓝色轮廓")
        self.assertFalse(any(_is_white(c) for c in drawn), "还有白色虚线或白色方块底")
        self.assertGreater(sum(n for c, n in _pixels(img).items() if _is_yellow(c)),
                           250, "箭头像素太少，可能没放大")

    def test_arrows_are_same_color_as_object_frame(self):
        self.assertEqual((OBJECT_YELLOW.red(), OBJECT_YELLOW.green(),
                          OBJECT_YELLOW.blue()), (255, 200, 60))

    def test_eight_arrow_positions_each_have_yellow_pixels(self):
        rect = QRectF(20, 20, 120, 90)
        img = QImage(200, 160, QImage.Format_ARGB32)
        img.fill(QColor(20, 80, 20))
        p = QPainter(img)
        draw_selection(p, rect, 1.0)
        p.end()
        for name, point in handle_positions(rect, 1.0).items():
            x, y = int(point.x()), int(point.y())
            patch = img.copy(max(0, x - 3), max(0, y - 3), 7, 7)
            self.assertTrue(any(_is_yellow(c) for c in _pixels(patch)),
                            f"{name} 位置没有黄色箭头")

    def test_selected_object_frame_stays_yellow(self):
        """选中对象本身仍是黄色框，不再因为选中变蓝。"""
        from rwmap.rw.version import load_profile
        from rwmap.utils.paths import as_path
        path = as_path(load_profile("1.15").sample_map)
        if not path.is_file():
            self.skipTest("缺少示例地图")
        self.w._open(path)
        doc = self.w.state.doc
        layer = next(l for l in doc.layers if isinstance(l, ObjectLayer))
        obj = MapObject(object_id=99001, name="黄框", shape="rect",
                        x=100.0, y=100.0, width=80.0, height=50.0)
        layer.objects.append(obj)
        item = ObjectLayerItem(layer, doc)
        item.set_selected(obj)
        img = QImage(140, 110, QImage.Format_ARGB32)
        img.fill(QColor(20, 80, 20))
        p = QPainter(img)
        p.translate(-90, -90)
        item.paint(p, None)
        p.end()
        drawn = [c for c in _pixels(img) if c != (20, 80, 20, 255)]
        self.assertTrue(any(_is_yellow(c) for c in drawn), "黄色框/箭头不见了")
        self.assertFalse(any(_is_blue(c) for c in drawn), "选中时出现了蓝色")

    # ---- 2. 蓝色描边画在图标里，不是按钮外框 ----
    def test_button_squares_have_no_border(self):
        sheet = self.w.object_toolbar.styleSheet()
        self.assertIn("border: none", sheet)
        self.assertNotIn("QToolButton:checked {\n    border", sheet)
        # 「绘制时自动地图」已按要求只在「地图」菜单显示，工具栏不再有该按钮。
        btn = self.w.object_toolbar.widgetForAction(self.w.act_automap_while)
        self.assertIsNone(btn)
        self.assertFalse(self.w.act_automap_while in self.w.object_toolbar.actions())

    def test_all_tool_icons_have_blue_outline(self):
        for tid, _name, _shortcut, _tip in TOOLS:
            icon = make_object_icon(tid)
            pix = icon.pixmap(28, 28)
            colors = _pixels(pix.toImage())
            self.assertTrue(any(_is_blue(c) for c in colors),
                            f"{tid} 图标没有蓝色描边")
        # 特别点名的两个：选择对象的鼠标箭头、插入文字的 T。
        for tid in ("select", "text"):
            colors = _pixels(make_object_icon(tid).pixmap(28, 28).toImage())
            self.assertGreater(sum(n for c, n in colors.items() if _is_blue(c)),
                               20, f"{tid} 图标蓝色描边太少")

    def test_text_icon_is_a_T_glyph(self):
        """文字工具图标是 T 字形，且用蓝色路径描边（不是深色 A）。"""
        colors = _pixels(make_object_icon("text").pixmap(28, 28).toImage())
        opaque = {c: n for c, n in colors.items() if c[3] > 150}
        self.assertFalse(any(r < 80 and g < 80 and b < 80 for r, g, b, _a in opaque),
                         "文字图标仍有深色实心字母")
        self.assertTrue(any(_is_blue(c) for c in opaque))

    # ---- 3. 默认 UI 布局 ----
    def test_default_dock_layout(self):
        w = self.w
        self.assertFalse(hasattr(w, "units_dock"))  # 单位面板已删
        self.assertEqual(w.dockWidgetArea(w.tilesets_dock), Qt.RightDockWidgetArea)
        # Triggers / 对象 树已由「触发器节点图」取代：浮动独立窗口、初始隐藏
        self.assertTrue(w.trigger_graph_dock.isFloating())
        self.assertTrue(w.trigger_graph_dock.isHidden())
        self.assertLess(w.layers_dock.y(), w.properties_dock.y())
        self.assertLess(w.properties_dock.y(), w.tilesets_dock.y())

    def test_object_toolbar_has_own_row_below_tabs(self):
        self.assertFalse(
            any(b.windowTitle() == "主工具栏" for b in self.w.findChildren(QToolBar)),
            "不应残留只有版本下拉的空工具栏行")
        self.assertGreater(
            self.w.object_toolbar.y(), self.w.tab_toolbar.y(),
            "对象工具栏应独占文档标签栏下方一行")


if __name__ == "__main__":
    unittest.main()
