"""回归：纯 Python 版触发器属性面板（网页版右侧功能 + 背景迁移）。

覆盖：中文类型 / 属性标签、属性值中文释义、切换类型严格替换属性、
类型切换经 QUndoStack 撤销（面板无撤销按钮）、编辑写回文档并走 QUndoStack、
增删属性、删除节点、主题常量、三种造兵类型统一绿色、类型下拉顺序、
类型 / 属性下拉滚轮禁用、触发器 ID 隐藏、类型下拉按类型颜色着色。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QUndoStack, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QLabel, QMessageBox, QPushButton,
)

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import (  # noqa: E402
    HIDDEN_EDITOR_PROPERTIES, LEGACY_EDITOR_NODE_POS_PROPERTY,
    PropertyBag,
)
from rwmap.theme import ASH, INK  # noqa: E402
from rwmap.widgets.trigger_graph_dock import TriggerGraphDock  # noqa: E402
from rwmap.widgets.trigger_inspector import (  # noqa: E402
    THEME, TYPE_COLORS, NoWheelComboBox, _TypeColorDelegate, color_for,
    describe_value, effective_type, prop_label, summary_keys, type_label,
    type_props, TYPE_DEFS, INITIAL_UNIT_TYPE,
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
        # 生成单位默认用固定列表 spawnUnits；随机生成单位才用 randomUnits
        self.assertEqual(summary_keys("unitAdd")[:2], ("spawnUnits", "team"))
        self.assertEqual(summary_keys("unitAddRandom")[:2], ("randomUnits", "team"))
        self.assertEqual(summary_keys("unitDetect")[:2], ("unitType", "minUnits"))

    def test_random_spawn_type_label_and_template(self):
        """新增的「随机生成单位」类型：中文标签 + randomUnits 模板。"""
        self.assertEqual(type_label("unitAddRandom"), "随机生成单位")
        self.assertEqual(type_props("unitAddRandom"),
                         ("randomUnits", "team", "warmup", "repeatDelay"))
        self.assertIn("spawnUnits", type_props("unitAdd"))
        self.assertNotIn("randomUnits", type_props("unitAdd"))

    def test_initial_unit_type_and_unit_translation(self):
        """新增的「地图初始单位」类型与 unit 属性中文翻译。"""
        self.assertIn(INITIAL_UNIT_TYPE, TYPE_DEFS)
        self.assertEqual(type_label(INITIAL_UNIT_TYPE), "地图初始单位")
        self.assertEqual(type_props(INITIAL_UNIT_TYPE), ("unit", "team"))
        self.assertEqual(prop_label("unit"), "初始单位")
        self.assertEqual(summary_keys(INITIAL_UNIT_TYPE)[:2], ("unit", "team"))

    def test_effective_type_distinguishes_initial_unit_from_unclassified(self):
        """只有空 type + unit 属性才算初始单位；map_info / 矩形等保持未分类。"""
        from rwmap.document.properties import PropertyBag
        # 初始单位：空 type + unit
        unit_obj = MapObject(object_id=1, type_="", shape="point",
                             properties=PropertyBag(values={"unit": "tank"}))
        self.assertEqual(effective_type(unit_obj), INITIAL_UNIT_TYPE)
        # 地图设置 / 矩形辅助对象：空 type、无 unit
        other = MapObject(object_id=2, type_="", shape="rect",
                          properties=PropertyBag(values={"type": "skirmish"}))
        self.assertEqual(effective_type(other), "")
        bare = MapObject(object_id=3, type_="", shape="rect",
                         properties=PropertyBag())
        self.assertEqual(effective_type(bare), "")

    def test_three_unit_types_share_spawn_green(self):
        """生成单位 / 随机生成单位 / 地图初始单位统一用生成单位的绿色。"""
        green = TYPE_COLORS["unitAdd"]
        self.assertEqual(green, "#2f8f52")
        self.assertEqual(TYPE_COLORS["unitAddRandom"], green)
        self.assertEqual(TYPE_COLORS["mapUnit"], green)
        # color_for 也走同一映射（mapUnit 是内部 key，节点标题按 effective_type 上色）
        self.assertEqual(color_for("unitAdd"), green)
        self.assertEqual(color_for("unitAddRandom"), green)
        self.assertEqual(color_for(INITIAL_UNIT_TYPE), green)

    def test_map_unit_type_placed_right_after_random_spawn(self):
        """类型字典顺序即下拉顺序：地图初始单位紧跟随机生成单位。"""
        keys = list(TYPE_DEFS.keys())
        self.assertEqual(keys[keys.index("unitAddRandom") + 1],
                         INITIAL_UNIT_TYPE)
        self.assertLess(keys.index(INITIAL_UNIT_TYPE), keys.index("unitDetect"))

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
        # 18 个原有类型 + mapUnit + 「未分类」空项；有类型的对象不会再加未知项
        self.assertEqual(self.ins.f_type.count(), 20)
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

    def test_type_combo_order_matches_dictionary(self):
        """下拉顺序：地图初始单位紧跟随机生成单位，且两者都在检测单位之前。"""
        data = [self.ins.f_type.itemData(i) for i in range(self.ins.f_type.count())]
        self.assertEqual(data[data.index("unitAddRandom") + 1], INITIAL_UNIT_TYPE)
        self.assertEqual(data[data.index(INITIAL_UNIT_TYPE) + 1], "unitDetect")

    def test_type_combo_items_colored_by_type(self):
        """类型下拉每项文字按其标题条颜色着色（生成绿/检测蓝/移除红…）。"""
        from PySide6.QtGui import QColor
        self.assertIsInstance(self.ins.f_type.itemDelegate(), _TypeColorDelegate)
        for i in range(self.ins.f_type.count()):
            key = self.ins.f_type.itemData(i) or ""
            fg = self.ins.f_type.itemData(i, Qt.ForegroundRole)
            self.assertIsInstance(fg, QColor, f"{self.ins.f_type.itemText(i)} 应有前景色")
            self.assertEqual(fg.name().lower(), color_for(key).lower(),
                             f"{self.ins.f_type.itemText(i)} 颜色应匹配类型色")

    def test_type_and_prop_combos_ignore_wheel(self):
        """类型 / 属性下拉禁用滚轮切换：滚轮事件被忽略，不改变选中项。"""
        self.assertIsInstance(self.ins.f_type, NoWheelComboBox)
        self.assertIsInstance(self.ins.prop_select, NoWheelComboBox)

        def wheel_evt():
            return QWheelEvent(QPoint(0, 0), QPoint(0, 0), QPoint(0, 0),
                               QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                               Qt.NoScrollPhase, False)

        # 直接调 wheelEvent：NoWheelComboBox 必须 ignore（把滚轮让给父滚动区）
        for combo in (self.ins.f_type, self.ins.prop_select):
            before = combo.currentIndex()
            evt = wheel_evt()
            evt.accept()
            combo.wheelEvent(evt)
            self.assertFalse(evt.isAccepted(), f"{combo} 应忽略滚轮事件")
            self.assertEqual(combo.currentIndex(), before,
                             "下拉框选中项不应随滚轮变化")

        # 对照证明 wheelEvent 覆写确实起作用：原生 QComboBox 会 accept 滚轮
        plain = QComboBox()
        evt = wheel_evt()
        evt.accept()
        plain.wheelEvent(evt)
        self.assertTrue(evt.isAccepted(), "原生 QComboBox 默认接受滚轮事件")

    def test_trigger_id_field_is_hidden(self):
        """触发器 ID 输入框不显示：控件保留数据，但不加入布局 / 不可见。"""
        self.assertIsNotNone(self.ins.f_id)
        self.assertFalse(self.ins.f_id.isVisible())
        self.assertIsNone(self.ins.f_id.parent())
        self.assertNotIn("触发器 ID（被引用）",
                         [l.text() for l in self.ins.findChildren(QLabel)])

    def test_empty_type_object_shows_initial_unit_combo(self):
        """空 type + unit 的初始单位对象：下拉自动选中「地图初始单位」。"""
        obj = self._obj()
        obj.type_ = ""
        obj.properties.set("unit", "experimentalHoverTank")
        obj.properties.set("team", "-1")
        self.ins.show_node(self.ins.node)
        QApplication.processEvents()
        self.assertEqual(self.ins.f_type.currentData(), INITIAL_UNIT_TYPE)
        self.assertEqual(self.ins.f_type.currentText(), "地图初始单位（mapUnit）")
        labels = [l.text() for l in self.ins.findChildren(QLabel)]
        self.assertIn("初始单位", labels)  # unit 已翻译成中文
        rows = {k: e for k, e, _ in self.ins._prop_rows}
        self.assertEqual(rows["unit"].text(), "experimentalHoverTank")

    def test_empty_type_without_unit_shows_unclassified(self):
        """纯空 type（如 map_info / 矩形）：下拉显示未分类，不误当初始单位。"""
        obj = self._obj()
        obj.type_ = ""
        obj.properties.set("type", "skirmish")  # 地图设置对象的属性
        self.ins.show_node(self.ins.node)
        QApplication.processEvents()
        self.assertEqual(self.ins.f_type.currentData(), "")
        self.assertEqual(self.ins.f_type.currentText(), "未分类（空 type）")

    def test_private_editor_props_are_hidden_but_kept(self):
        """rwmap_group / rwmap_node_pos / rwmap_noc 只在文件里，界面不显示。

        截图里出现的 ``rwmap_noc`` 是节点图位置属性；分组属性此前已经隐藏。
        现在两种位置属性都归入同一隐藏集合：属性行、添加下拉、节点图搜索
        都看不到它们；切换触发器类型时也不能把这些布局信息删掉。
        """
        obj = self._obj()
        obj.properties.set("rwmap_group", "Alpha", "string")
        obj.properties.set("rwmap_node_pos", "12,34", "string")
        obj.properties.set(LEGACY_EDITOR_NODE_POS_PROPERTY, "0,0", "string")
        self.ins.show_node(self.ins.node)
        QApplication.processEvents()
        rows = {k for k, _e, _h in self.ins._prop_rows}
        picker = {self.ins.prop_select.itemData(i)
                  for i in range(self.ins.prop_select.count())}
        for key in HIDDEN_EDITOR_PROPERTIES:
            self.assertNotIn(key, rows)
            self.assertNotIn(key, picker)
        # 数据层仍然保留，供 TMX 往返与节点图布局恢复使用
        for key in HIDDEN_EDITOR_PROPERTIES:
            self.assertIn(key, obj.properties)

        self.dock._group_page = "ALL"
        self.dock.search_edit.setText("rwmap_noc")
        QApplication.processEvents()
        self.assertFalse(any(self.dock._matches(n)
                             for n in self.dock.data.nodes.values()))
        self.dock.search_edit.clear()
        QApplication.processEvents()

        self.ins.f_type.setCurrentIndex(self.ins.f_type.findData("point"))
        QApplication.processEvents()
        for key in HIDDEN_EDITOR_PROPERTIES:
            self.assertIn(key, self._obj().properties)

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
        # 类型切换后的「不适配属性 / Ctrl+Z 撤销」提示框已按要求删除
        self.assertFalse(any("不适配属性" in l.text() or "Ctrl+Z" in l.text()
                             for l in self.dock.inspector.findChildren(QLabel)))

    def test_type_switch_undo_via_stack_restores_everything(self):
        """类型切换走 QUndoStack：Ctrl+Z / 编辑菜单撤销即可还原，面板无撤销按钮。"""
        before = dict(self._obj().properties.values)
        self.ins.f_type.setCurrentIndex(self.ins.f_type.findData("point"))
        QApplication.processEvents()
        self.assertEqual(set(self._obj().properties.values), {"id"})
        # 面板里不再有「撤销切换」按钮
        self.assertFalse(any(b.text() == "撤销切换"
                             for b in self.dock.inspector.findChildren(QPushButton)))
        self.stack.undo()
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


class NoFlashWindowTest(unittest.TestCase):
    """回归：属性面板重建时不得闪出无标题顶层窗。

    真凶：vhint 是无 parent 的 QLabel，若在 addWidget 之前调用
    setVisible(True)，Qt 会把它当顶层窗口显示（带系统三键、无文字），
    addWidget 后才挂回父级 → 一闪而过的空窗，属性越多弹得越多。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from rwmap.widgets.trigger_graph_dock import collect_graph
        self.doc = make_doc()
        data = collect_graph(self.doc)
        self.node = next(iter(data.nodes.values()))
        self.ins = __import__(
            "rwmap.widgets.trigger_inspector", fromlist=["TriggerInspector"]
        ).TriggerInspector()

    def test_setvisible_called_only_when_parented(self):
        """监听所有 setVisible(True)：调用瞬间控件必须已有 parent。"""
        from PySide6.QtWidgets import QWidget
        original = QWidget.setVisible
        offenders = []

        def guarded(self_widget, visible):
            if visible and self_widget.parent() is None:
                offenders.append((type(self_widget).__name__,
                                  self_widget.windowTitle()))
            return original(self_widget, visible)

        QWidget.setVisible = guarded
        try:
            for _ in range(5):
                self.ins.show_node(self.node, [], self.doc.layers[0])
                QApplication.processEvents()
        finally:
            QWidget.setVisible = original
        self.assertEqual(offenders, [],
                         "仍有控件在无 parent 时 setVisible(True): %r"
                         % (offenders,))

    def test_no_new_visible_toplevels_during_rebuild(self):
        """真实重建：不得新增可见顶层窗。"""
        self.ins.show_node(self.node, [], self.doc.layers[0])
        QApplication.processEvents()
        base = {id(w) for w in QApplication.topLevelWidgets()}
        for _ in range(8):
            self.ins.show_node(self.node, [], self.doc.layers[0])
            QApplication.processEvents()
        new_visible = [
            w for w in QApplication.topLevelWidgets()
            if id(w) not in base and w.isVisible()
        ]
        self.assertEqual(new_visible, [],
                         "重建冒出可见顶层窗: %r"
                         % [(type(w).__name__, w.windowTitle())
                            for w in new_visible])
