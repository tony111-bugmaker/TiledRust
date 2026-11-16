"""回归：纯 Python 版触发器属性面板（网页版右侧功能 + 背景迁移）。

覆盖：中文类型 / 属性标签、属性值中文释义、切换类型严格替换属性、
撤销切换、编辑写回文档并走 QUndoStack、增删属性、删除节点、主题常量。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from PySide6.QtGui import QUndoStack  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QMessageBox, QPushButton,
)

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import PropertyBag  # noqa: E402
from rwmap.theme import ASH, INK  # noqa: E402
from rwmap.widgets.trigger_graph_dock import TriggerGraphDock  # noqa: E402
from rwmap.widgets.trigger_inspector import (  # noqa: E402
    THEME, describe_value, prop_label, summary_keys, type_label, type_props,
)


def _bag(**kwargs) -> PropertyBag:
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


def make_doc() -> MapDocument:
    objs = [
        MapObject(object_id=1, name="检测猛犸", type_="unitDetect", shape="rect",
                  x=0, y=0, width=100, height=100,
                  properties=_bag(id="1", unitType="mammothTank", minUnits="2",
                                  team="1", warmup="30s", globalMessage="来了")),
        MapObject(object_id=2, name="删除猛犸", type_="unitRemove", shape="point",
                  x=200, y=40, properties=_bag(id="2", team="1")),
    ]
    return MapDocument(width=32, height=20,
                       layers=[ObjectLayer(name="Triggers", objects=objs)])


def button(widget, text: str) -> QPushButton:
    return next(b for b in widget.findChildren(QPushButton) if b.text() == text)


class DictionaryTest(unittest.TestCase):
    def test_chinese_type_and_property_labels(self):
        self.assertEqual(type_label("unitDetect"), "检测单位")
        self.assertEqual(type_label("changeCredits"), "修改资金")
        self.assertEqual(prop_label("unitType"), "单位类型")
        self.assertEqual(prop_label("resetActivationAfter"), "复位时间")
        self.assertEqual(type_props("point"), ())

    def test_value_hints_stay_display_only(self):
        self.assertEqual(describe_value("unitType", "mammothTank"), "猛犸坦克")
        self.assertEqual(describe_value("unitType", "builder、坦克"),
                         "建造者、坦克")
        self.assertEqual(describe_value("warmup", "30s"), "30 秒")
        self.assertEqual(describe_value("showOnMap", "true"), "是")
        self.assertEqual(describe_value("team", "1"), "")

    def test_summary_keys_follow_type(self):
        self.assertEqual(summary_keys("unitAdd")[:2], ("randomUnits", "team"))
        self.assertEqual(summary_keys("unitDetect")[:2], ("unitType", "minUnits"))

    def test_theme_uses_two_color_palette(self):
        """界面区分色收敛为双色：INK（近黑）+ ASH（偏黑灰）。

        语义色（端口 / 连线）不在收敛范围内，保持原样。
        """
        self.assertEqual(THEME["bg"], INK)
        self.assertEqual(THEME["panel"], INK)
        self.assertEqual(THEME["node"], ASH)
        self.assertEqual(THEME["dot"], ASH)
        self.assertEqual(THEME["dot_gap"], 24)
        # 语义色保持原样
        self.assertEqual(THEME["port_out"], "#e09a3a")


class InspectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = make_doc()
        self.stack = QUndoStack()
        self.dock = TriggerGraphDock()
        self.dock.set_document(self.doc, self.stack,
                               lambda: self.dock.refresh(fit=False))
        self.dock.show()
        self.dock.refresh(fit=False)
        QApplication.processEvents()
        # 节点图已做视口虚拟化：node_items 只包含视口内节点，不能用
        # next(iter(...)) 依赖首项。显式按 object_id 找到 object 1 的节点选中。
        key = next(k for k, n in self.dock.data.nodes.items()
                   if n.obj.object_id == 1)
        item = self.dock.node_items.get(key)
        self.assertIsNotNone(item, "fixture 的第一个对象应落在初始视口内")
        item.setSelected(True)
        QApplication.processEvents()
        self.ins = self.dock.inspector
        self.oid = 1

    def tearDown(self):
        self.dock.hide()
        self.dock.deleteLater()

    def _obj(self):
        return next(o for o in self.doc.layers[0].objects
                    if o.object_id == self.oid)

    def test_type_combo_and_prop_rows_are_chinese(self):
        self.assertEqual(self.ins.f_type.count(), 17)
        self.assertEqual(self.ins.f_type.currentText(), "检测单位（unitDetect）")
        labels = [l.text() for l in self.ins.findChildren(QLabel)]
        self.assertIn("单位类型", labels)
        self.assertIn("最少单位数", labels)
        self.assertIn("作用队伍", labels)
        self.assertIn("预热时间", labels)
        self.assertIn("属性（中文标签 · 导出仍为英文键）", labels)
        rows = {k: e for k, e, _ in self.ins._prop_rows}
        self.assertEqual(rows["unitType"].text(), "mammothTank")
        self.assertEqual(rows["minUnits"].placeholderText(), "2")
        self.assertTrue(any("猛犸坦克" in l.text()
                            for l in self.ins.findChildren(QLabel)))

    def test_switch_type_removes_all_non_matching_props(self):
        before = dict(self._obj().properties.values)
        self.assertIn("unitType", before)
        self.assertIn("globalMessage", before)
        idx = self.ins.f_type.findData("changeCredits")
        self.ins.f_type.setCurrentIndex(idx)
        QApplication.processEvents()
        obj = self._obj()
        self.assertEqual(obj.type_, "changeCredits")
        self.assertEqual(set(obj.properties.values),
                         {"id", *type_props("changeCredits")})
        self.assertNotIn("unitType", obj.properties.values)
        self.assertNotIn("warmup", obj.properties.values)
        self.assertNotIn("globalMessage", obj.properties.values)
        self.assertEqual(self.stack.count(), 1)
        self.assertTrue(any("不适配属性" in l.text()
                            for l in self.dock.inspector.findChildren(QLabel)))

    def test_undo_switch_button_restores_everything(self):
        before = dict(self._obj().properties.values)
        self.ins.f_type.setCurrentIndex(self.ins.f_type.findData("point"))
        QApplication.processEvents()
        self.assertEqual(set(self._obj().properties.values), {"id"})
        button(self.dock.inspector, "撤销切换").click()
        QApplication.processEvents()
        self.assertEqual(self._obj().type_, "unitDetect")
        self.assertEqual(dict(self._obj().properties.values), before)

    def test_edit_property_writes_back_and_is_undoable(self):
        rows = {k: e for k, e, _ in self.ins._prop_rows}
        rows["minUnits"].setText("7")
        rows["minUnits"].editingFinished.emit()
        QApplication.processEvents()
        self.assertEqual(self._obj().properties.get("minUnits"), "7")
        self.assertEqual(self.stack.count(), 1)
        self.stack.undo()
        QApplication.processEvents()
        self.assertEqual(self._obj().properties.get("minUnits"), "2")

    def test_add_and_remove_property(self):
        idx = self.dock.inspector.prop_select.findData("maxUnits")
        self.assertGreaterEqual(idx, 0)
        self.dock.inspector.prop_select.setCurrentIndex(idx)
        button(self.dock.inspector, "＋").click()
        QApplication.processEvents()
        self.assertIn("maxUnits", self._obj().properties.values)
        rows = {k: e for k, e, _ in self.dock.inspector._prop_rows}
        self.assertIn("maxUnits", rows)
        # 删除该属性
        self.dock.inspector._remove_prop("maxUnits")
        QApplication.processEvents()
        self.assertNotIn("maxUnits", self._obj().properties.values)

    def test_delete_node_removes_object_and_undo_restores(self):
        with patch("rwmap.widgets.trigger_inspector.QMessageBox.question",
                   return_value=QMessageBox.Yes):
            button(self.ins, "删除此节点").click()
        QApplication.processEvents()
        self.assertEqual(len(self.doc.layers[0].objects), 1)
        self.stack.undo()
        QApplication.processEvents()
        self.assertEqual(len(self.doc.layers[0].objects), 2)
        self.assertIsNotNone(self._obj())

    def test_dotted_background_is_installed(self):
        self.assertIsNotNone(self.dock.scene._dots)
        self.assertGreater(self.dock.inspector.width(), 0)
        pix = self.dock.grab()
        self.assertGreater(pix.width(), 100)
