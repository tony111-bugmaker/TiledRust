"""回归：插入对象工具栏的「自适应网格」布局，以及移除「对象层」下拉后的行为。

覆盖：
* ``FlowLayout`` 的行/列/高度换算（宽度够就一行，不够就换行）；
* 真实工具栏横排 = 一行、竖排 = 4 列 × 4 行，且按钮不越出容器；
* 工具栏不再包含「对象层」下拉（落点改由文档 / 图层面板决定）；
* 网格按钮与 QAction 双向同步（点按钮切工具、外部切工具时按钮跟着选中）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QToolButton, QWidget  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.widgets.flow_layout import FlowHost, FlowLayout  # noqa: E402
from rwmap.widgets.object_toolbar import TOOLS, ObjectToolBar  # noqa: E402


def _make_host(count: int, columns_hint: int) -> FlowHost:
    host = FlowHost(columns_hint=columns_hint, spacing=2)
    for i in range(count):
        button = QToolButton()
        button.setText(str(i))
        button.setFixedSize(QSize(30, 30))
        host.add_button(button)
    return host


class FlowLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_one_row_when_columns_hint_zero(self):
        host = _make_host(10, 0)
        hint = host.sizeHint()
        self.assertEqual(hint.width(), 30 * 10 + 2 * 9)
        self.assertEqual(hint.height(), 30)
        self.assertEqual(host.row_count(), 1)

    def test_wraps_into_rows_for_column_hint(self):
        host = _make_host(10, 4)
        self.assertEqual(host.row_count(), 3)          # 10 个按 4 列 → 3 行
        self.assertEqual(host.sizeHint().width(), 30 * 4 + 2 * 3)
        self.assertEqual(host.sizeHint().height(), 30 * 3 + 2 * 2)

    def test_columns_for_width_matches_width_for_columns(self):
        host = _make_host(8, 0)
        layout = host.flow
        for columns in (1, 2, 3, 4, 5):
            width = layout.width_for_columns(columns)
            self.assertEqual(layout.columns_for_width(width), columns)

    def test_layout_geometry_keeps_buttons_inside(self):
        host = _make_host(9, 3)
        host.resize(host.sizeHint())
        layout: FlowLayout = host.flow
        layout.setGeometry(host.rect())
        rect = host.rect()
        positions = []
        for button in host.buttons():
            geo = button.geometry()
            self.assertTrue(rect.contains(geo), f"{geo} 越出 {rect}")
            positions.append((geo.x(), geo.y()))
        self.assertEqual(len(positions), 9)
        self.assertEqual({y for _x, y in positions}, {0, 32, 64})   # 3 行
        self.assertEqual(len({x for x, _y in positions}), 3)        # 3 列

    def test_hidden_buttons_are_skipped(self):
        host = _make_host(4, 0)
        host.buttons()[1].hide()
        self.assertEqual(len(host.buttons()), 3)        # 隐藏的不参与排布
        self.assertEqual(host.row_count(), 1)
        host.resize(host.sizeHint())
        host.flow.setGeometry(host.rect())
        # 隐藏的按钮不占位置：剩下的 3 个依次贴在第 1 个右边
        first, third = host.buttons()[0], host.buttons()[2]
        self.assertEqual(third.geometry().x(), first.geometry().x() + 2 * 32)


class ToolbarGridTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rwmap.app.main_window import MainWindow

        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)
        cls.w.resize(1200, 820)
        cls.w.show()
        QApplication.processEvents()

    def tearDown(self):
        # 用例可能把工具栏挪到左侧，恢复默认（顶部一行）以免互相影响
        bar = self.w.object_toolbar
        self.w.addToolBar(Qt.TopToolBarArea, bar)
        QApplication.processEvents()

    def _settle(self, times: int = 5):
        for _ in range(times):
            QApplication.processEvents()

    # ---- 横排 / 竖排 ----
    def test_horizontal_puts_all_tools_in_one_row(self):
        bar = self.w.object_toolbar
        self._settle()
        self.assertEqual(bar.orientation(), Qt.Horizontal)
        self.assertEqual(bar.grid.columns_hint, 0)
        ys = {b.geometry().y() for b in bar.grid.buttons()}
        self.assertEqual(ys, {0})                      # 只有一行
        self.assertEqual(len(bar.grid.buttons()), len(TOOLS))

    def test_vertical_wraps_several_tools_per_row(self):
        bar = self.w.object_toolbar
        self.w.addToolBar(Qt.LeftToolBarArea, bar)
        self._settle(8)
        self.assertEqual(bar.orientation(), Qt.Vertical)
        self.assertEqual(bar.grid.columns_hint, ObjectToolBar.VERTICAL_COLUMNS)
        buttons = bar.grid.buttons()
        self.assertEqual(len(buttons), len(TOOLS))
        rows: dict[int, list[int]] = {}
        for button in buttons:
            geo = button.geometry()
            rows.setdefault(geo.y(), []).append(geo.x())
        # 4 列 × 4 行：前几行满 4 个，最后一行放剩下的（合并后共 15 个）。
        expected_rows = (len(TOOLS) + ObjectToolBar.VERTICAL_COLUMNS - 1) // ObjectToolBar.VERTICAL_COLUMNS
        self.assertEqual(len(rows), expected_rows)
        counts = sorted(len(xs) for xs in rows.values())
        self.assertEqual(counts[-1], ObjectToolBar.VERTICAL_COLUMNS)
        self.assertEqual(sum(counts), len(TOOLS))
        self.assertTrue(all(1 <= n <= ObjectToolBar.VERTICAL_COLUMNS for n in counts), rows)
        host_rect = bar.grid.rect()
        for button in buttons:
            self.assertTrue(host_rect.contains(button.geometry()),
                            f"{button.geometry()} 越出 {host_rect}")
        # 竖排宽度明显小于横排，且能一行放下 4 个按钮
        self.assertLess(bar.width(), 200)
        self.assertGreaterEqual(bar.grid.width(), bar.grid.preferred_width())

    def test_switching_back_restores_single_row(self):
        bar = self.w.object_toolbar
        self.w.addToolBar(Qt.LeftToolBarArea, bar)
        self._settle(8)
        self.assertTrue(bar.grid.row_count() > 1)
        self.w.addToolBar(Qt.TopToolBarArea, bar)
        self._settle(8)
        self.assertEqual(bar.orientation(), Qt.Horizontal)
        self.assertEqual(bar.grid.row_count(), 1)
        self.assertEqual({b.geometry().y() for b in bar.grid.buttons()}, {0})

    # ---- 「对象层」下拉已移除 ----
    def test_object_layer_combo_is_gone(self):
        bar = self.w.object_toolbar
        self.assertFalse(hasattr(bar, "layer_combo"))
        self.assertEqual(bar.findChildren(QComboBox), [])
        labels = [w.text() for w in bar.findChildren(QWidget)
                  if hasattr(w, "text") and isinstance(w.text(), str)]
        self.assertNotIn(" 对象层 ", labels)

    def test_default_layer_is_triggers_object_layer(self):
        bar = self.w.object_toolbar
        doc = MapDocument(width=32, height=20, layers=[
            ObjectLayer(name="Objects"),
            ObjectLayer(name="Triggers"),
        ])
        bar.set_document(doc)
        self.assertIsNotNone(bar.current_layer())
        self.assertEqual(bar.current_layer().name, "Triggers")

    def test_layer_follows_layers_dock_selection(self):
        bar = self.w.object_toolbar
        other = ObjectLayer(name="Objects2")
        doc = MapDocument(width=32, height=20, layers=[
            ObjectLayer(name="Triggers"), other])
        bar.set_document(doc)
        seen = []
        bar.layerChanged.connect(seen.append)
        self.w._on_layer_selected(other)
        self.assertIs(bar.current_layer(), other)
        self.assertEqual(seen, [other])

    # ---- 按钮 ↔ 动作 ----
    def test_button_click_switches_tool(self):
        bar = self.w.object_toolbar
        self.w._set_object_tool("brush")
        self._settle()
        bar.buttons_by_id["rect"].click()
        self._settle()
        self.assertEqual(bar.current_tool(), "rect")
        self.assertEqual(self.w._object_tool, "rect")
        self.assertFalse(bar.buttons_by_id["brush"].isChecked())

    def test_external_tool_switch_updates_checked_button(self):
        bar = self.w.object_toolbar
        self.w._set_object_tool("eraser")
        self._settle()
        self.assertTrue(bar.buttons_by_id["eraser"].isChecked())
        self.assertFalse(bar.buttons_by_id["brush"].isChecked())
        self.assertEqual(bar.current_tool(), "eraser")
        self.w._set_object_tool("brush")

    def test_every_tool_has_a_grid_button(self):
        bar = self.w.object_toolbar
        self.assertEqual(set(bar.buttons_by_id), {t[0] for t in TOOLS})
        for tid, button in bar.buttons_by_id.items():
            self.assertIs(button.defaultAction(), bar.actions_by_id[tid])


if __name__ == "__main__":
    unittest.main()
