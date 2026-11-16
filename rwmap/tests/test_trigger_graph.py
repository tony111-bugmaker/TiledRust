"""回归：原生触发器节点图（B 方案）。

覆盖：Triggers 对象层里**所有形状**的对象都成为节点；id 引用反推连线与
方向；悬空引用计数；节点图只读（拖动不改文档、没有增/删节点入口）；
面板初始隐藏 + 设置菜单勾选后以独立浮动窗口打开；过滤与选中联动。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.app.panel_layout import PANEL_SPECS, apply_default_layout  # noqa: E402
from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import PropertyBag  # noqa: E402
from rwmap.widgets.trigger_graph_dock import collect_graph  # noqa: E402


def _bag(**kwargs) -> PropertyBag:
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


class _CountingPainter:
    """假 painter：只记录绘制调用，用来断言全局连线层确实画了线。"""

    def __init__(self):
        self.lines = 0
        self.paths = 0

    def setRenderHint(self, *a): pass
    def setPen(self, *a): pass
    def setBrush(self, *a): pass
    def drawLine(self, *a): self.lines += 1
    def drawPath(self, *a): self.paths += 1
    def drawPolygon(self, *a): pass


def make_doc() -> MapDocument:
    """矩形 / 点 / 折线 / 文字 4 种形状 + 4 种 id 引用关系。"""
    objs = [
        MapObject(object_id=1, name="检测猛犸", type_="unitDetect", shape="rect",
                  x=0, y=0, width=100, height=100,
                  properties=_bag(id="1", unitType="mammoth", minUnits="2",
                                  alsoActivate="2, 3")),
        MapObject(object_id=2, name="删除猛犸", type_="unitRemove", shape="point",
                  x=200, y=40,
                  properties=_bag(id="2", whenActivatedIds="1")),
        MapObject(object_id=3, name="生成机甲", type_="unitAdd", shape="polyline",
                  x=400, y=40, points=[(0, 0), (20, 10), (40, 0)],
                  properties=_bag(id="3", whenActivatedlds="1",
                                  deactivatedBy="99")),
        MapObject(object_id=4, name="提示文字", type_="mapText", shape="text",
                  x=600, y=40, text="hello",
                  properties=_bag(id="4")),
    ]
    return MapDocument(width=32, height=20,
                       layers=[ObjectLayer(name="Triggers", objects=objs)])


class GraphDataTest(unittest.TestCase):
    def test_all_object_shapes_become_nodes(self):
        data = collect_graph(make_doc())
        self.assertEqual(len(data.nodes), 4)
        self.assertEqual(data.layer_names, ["Triggers"])
        shapes = sorted(n.obj.shape for n in data.nodes.values())
        self.assertEqual(shapes, ["point", "polyline", "rect", "text"])
        self.assertEqual({n.tid for n in data.nodes.values()},
                         {"1", "2", "3", "4"})

    def test_edges_direction_and_typo_alias(self):
        data = collect_graph(make_doc())
        pairs = {(e.src, e.dst, e.kind) for e in data.edges}
        k1 = next(k for k, n in data.nodes.items() if n.tid == "1")
        k2 = next(k for k, n in data.nodes.items() if n.tid == "2")
        k3 = next(k for k, n in data.nodes.items() if n.tid == "3")
        self.assertIn((k1, k2, "alsoActivate"), pairs)
        self.assertIn((k1, k3, "alsoActivate"), pairs)
        self.assertIn((k1, k2, "whenActivatedIds"), pairs)   # reverse
        self.assertIn((k1, k3, "whenActivatedlds"), pairs)   # 常见错拼
        self.assertEqual(len(data.edges), 4)
        self.assertEqual(data.dangling, 1)                    # deactivatedBy=99

    def test_collect_is_read_only(self):
        doc = make_doc()
        layer = doc.layers[0]
        before = [(o.object_id, o.x, dict(o.properties.values))
                  for o in layer.objects]
        collect_graph(doc)
        after = [(o.object_id, o.x, dict(o.properties.values))
                 for o in layer.objects]
        self.assertEqual(before, after)
        self.assertFalse(doc.dirty)

    def test_no_triggers_layer_falls_back_to_all_object_layers(self):
        doc = MapDocument(layers=[
            ObjectLayer(name="Objects", objects=[MapObject(object_id=7)]),
        ])
        self.assertEqual(len(collect_graph(doc).nodes), 1)

    def test_layer_filter_shows_only_that_layer(self):
        l1 = ObjectLayer(name="Triggers", objects=[
            MapObject(object_id=1, name="A", type_="unitAdd", shape="rect",
                      properties=_bag(id="1", alsoActivate="2")),
        ])
        l2 = ObjectLayer(name="Triggers2", objects=[
            MapObject(object_id=2, name="B", type_="unitAdd", shape="point",
                      properties=_bag(id="2")),
        ])
        doc = MapDocument(layers=[l1, l2])
        full = collect_graph(doc)
        self.assertEqual(len(full.nodes), 2)
        self.assertEqual(sorted(full.layer_names), ["Triggers", "Triggers2"])
        only2 = collect_graph(doc, "Triggers2")
        self.assertEqual(len(only2.nodes), 1)
        self.assertEqual(only2.layer_names, ["Triggers2"])
        self.assertEqual(next(iter(only2.nodes.values())).tid, "2")

    def test_layer_filter_resolves_cross_layer_refs(self):
        """切到某一层时，跨层引用应解析成功（不误判悬空），只是目标节点不在本层
        所以这条边不会画出来。"""
        l1 = ObjectLayer(name="Triggers", objects=[
            MapObject(object_id=1, name="A", type_="unitAdd", shape="rect",
                      properties=_bag(id="1", alsoActivate="2")),
        ])
        l2 = ObjectLayer(name="Triggers2", objects=[
            MapObject(object_id=2, name="B", type_="unitAdd", shape="point",
                      properties=_bag(id="2")),
        ])
        doc = MapDocument(layers=[l1, l2])
        only1 = collect_graph(doc, "Triggers")
        self.assertEqual(only1.dangling, 0)
        self.assertEqual(len(only1.edges), 1)   # 边存在，但目标在另一层不渲染
        self.assertEqual({n.tid for n in only1.nodes.values()}, {"1"})


class GraphDockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)

    def tearDown(self):
        self.w.trigger_graph_dock.hide()
        self.w.act_trigger_graph.setChecked(False)
        apply_default_layout(self.w)

    def _setup(self):
        dock = self.w.trigger_graph_dock
        # 复位搜索 / 分组 UI（避免上个用例残留状态导致 active 为空、节点 0 个），
        # 并清掉可能残留的概览 item。对象类型过滤功能已移除，不再需要复位下拉。
        dock.hide()
        dock.view.resetTransform()
        dock._exit_overview()
        dock.search_edit.clear()
        dock._group_page = ""
        dock._empty_groups.clear()
        dock.set_document(make_doc())
        dock.refresh(fit=False)
        dock.group_combo.setCurrentIndex(0)
        return dock

    def test_spec_declares_hidden_floating_panel(self):
        spec = next(s for s in PANEL_SPECS if s.key == "panels.trigger_graph")
        self.assertFalse(spec.visible)
        self.assertTrue(spec.floating)
        self.assertIsInstance(self.w.trigger_graph_dock, QDockWidget)
        self.assertFalse(hasattr(self.w, "triggers_dock"))

    def test_settings_toggle_opens_independent_window(self):
        dock = self.w.trigger_graph_dock
        self.assertTrue(dock.isHidden())
        self.assertTrue(dock.isFloating())
        self.w.act_trigger_graph.trigger()
        QApplication.processEvents()
        self.assertFalse(dock.isHidden())
        self.assertTrue(dock.isFloating())
        self.assertNotEqual(dock.window(), self.w)
        self.assertTrue(self.w.act_trigger_graph.isChecked())
        dock.btn_close.click()
        QApplication.processEvents()
        self.assertTrue(dock.isHidden())
        self.assertFalse(self.w.act_trigger_graph.isChecked())

    def test_nodes_come_from_document_and_are_read_only(self):
        dock = self._setup()
        self.assertEqual(len(dock.node_items), 4)
        self.assertEqual(len(dock.edge_items), 4)
        layer = dock.doc.layers[0]
        obj = layer.objects[0]
        item = next(i for i in dock.node_items.values() if i.node.obj is obj)
        old = (obj.x, obj.y, dict(obj.properties.values))
        item.setPos(999, 888)                 # 只在节点图里挪，不写回文档
        self.assertEqual((obj.x, obj.y, dict(obj.properties.values)), old)
        self.assertFalse(dock.doc.dirty)
        for name in ("add_node", "remove_node", "delete_node", "add_object",
                     "del_object", "new_node"):
            self.assertFalse(hasattr(dock, name), name)

    def test_object_type_filter_removed(self):
        """按需求移除「全部对象 / 仅触发器 / 点…」类型过滤：控件和常量都不再有，
        所有形状的对象一律进节点图，只受分组页与搜索框影响。"""
        dock = self._setup()
        self.assertFalse(hasattr(dock, "filter_combo"))
        from rwmap.widgets import trigger_graph_dock as mod
        self.assertFalse(hasattr(mod, "FILTERS"))
        self.assertEqual(len(dock.node_items), 4)
        self.assertEqual(
            sorted(i.node.obj.shape for i in dock.node_items.values()),
            ["point", "polyline", "rect", "text"])

    def test_search_matches_property_values(self):
        dock = self._setup()
        dock.search_edit.setText("mammoth")
        self.assertEqual(sum(i.isVisible() for i in dock.node_items.values()), 1)
        dock.search_edit.setText("不存在的对象名")
        self.assertEqual(sum(i.isVisible() for i in dock.node_items.values()), 0)

    def test_click_node_emits_object_for_map_selection(self):
        dock = self._setup()
        received = []
        dock.objectSelected.connect(received.append)
        item = next(iter(dock.node_items.values()))
        item.setSelected(True)
        QApplication.processEvents()
        self.assertEqual(len(received), 1)
        self.assertIs(received[0], item.node.obj)
        from PySide6.QtWidgets import QLabel

        labels = [l.text() for l in dock.inspector.findChildren(QLabel)]
        self.assertTrue(any("输入连线" in t for t in labels))
        self.assertTrue(any("属性（中文标签" in t for t in labels))

    def test_panel_settings_checkbox_also_opens_floating(self):
        from rwmap.widgets import PanelSettingsDialog

        dock = self.w.trigger_graph_dock
        d = PanelSettingsDialog(self.w)
        d._items["panels.trigger_graph"].setCheckState(0, Qt.Checked)
        d.accept()
        QApplication.processEvents()
        self.assertFalse(dock.isHidden())
        self.assertTrue(dock.isFloating())

    def test_new_object_on_map_syncs_into_graph(self):
        """节点图不能加节点；在地图对象层里加一个，刷新后自动出现。"""
        dock = self._setup()
        before = len(dock.node_items)
        dock.doc.layers[0].objects.append(
            MapObject(object_id=99, name="地图里新增", type_="unitDetect",
                      shape="rect"))
        with patch.object(self.w.scene, "refresh_objects"), \
             patch.object(self.w, "_update_title"):
            self.w._refresh_objects()
        self.assertEqual(len(dock.node_items), before + 1)
        self.assertTrue(any(i.node.obj.object_id == 99
                            for i in dock.node_items.values()))

    def test_vector_zoom_does_not_change_item_geometry(self):
        dock = self._setup()
        item = next(iter(dock.node_items.values()))
        before = item.boundingRect()
        z0 = dock.view.zoom()
        dock._zoom_by(2.0)
        self.assertAlmostEqual(dock.view.zoom(), z0 * 2.0, places=3)
        self.assertEqual(item.boundingRect(), before)   # 矢量重绘，无位图拉伸

    def test_fit_view_does_not_lock_scene_rect(self):
        """fit_view 不能把 sceneRect 锁死在内容边界，否则把节点拖出边界后视图无法
        继续跟随滚动，节点表现为「移动范围受限 / 拖不动」。"""
        dock = self._setup()
        calls = []
        orig = dock.scene.setSceneRect

        def spy(rect, *a, **k):
            calls.append(rect)
        try:
            dock.scene.setSceneRect = spy
            dock.fit_view()
        finally:
            dock.scene.setSceneRect = orig
        self.assertEqual(
            calls, [],
            "fit_view 不应调用 setSceneRect 锁定画布边界，节点应当可拖到任意位置")

    def test_switch_object_layer_via_combo(self):
        """顶部对象层下拉只列真实层名（没有「全部对象层」），默认 Triggers，
        切到第二层后只显示该层节点。"""
        dock = self._setup()                      # make_doc 只有 Triggers 一层
        self.assertEqual(dock.layer_combo.count(), 1)
        self.assertEqual(dock._layer_filter, "Triggers")
        self.assertNotIn(None, [dock.layer_combo.itemData(i)
                               for i in range(dock.layer_combo.count())])
        dock.doc.layers.append(
            ObjectLayer(name="Triggers2", objects=[
                MapObject(object_id=5, name="二层节点", type_="unitAdd",
                          shape="point")]))
        dock.refresh(fit=False)
        names = [dock.layer_combo.itemData(i)
                 for i in range(dock.layer_combo.count())]
        self.assertIn("Triggers2", names)
        self.assertEqual(dock.layer_combo.count(), 2)
        # 切到 Triggers2：只剩二层那一个节点
        dock.layer_combo.setCurrentIndex(names.index("Triggers2"))
        QApplication.processEvents()
        self.assertEqual(dock._layer_filter, "Triggers2")
        self.assertEqual(len(dock.node_items), 1)
        self.assertEqual(next(iter(dock.node_items.values())).node.tid, "5")
        # 切回 Triggers：四个节点都在
        dock.layer_combo.setCurrentIndex(names.index("Triggers"))
        QApplication.processEvents()
        self.assertEqual(dock._layer_filter, "Triggers")
        self.assertEqual(len(dock.node_items), 4)

    def test_layer_combo_defaults_to_triggers(self):
        """没有「全部对象层」这一档；默认停在 Triggers，没它就退到第一个对象层。"""
        dock = self._setup()
        self.assertEqual(dock.layer_combo.itemText(0), "Triggers")
        doc = MapDocument(layers=[ObjectLayer(name="Objects", objects=[
            MapObject(object_id=9)])])
        dock.set_document(doc)
        dock.refresh(fit=False)
        self.assertEqual(dock._layer_filter, "Objects")
        self.assertEqual([dock.layer_combo.itemData(i)
                          for i in range(dock.layer_combo.count())], ["Objects"])

    def test_group_combo_has_no_all_page(self):
        """分组下拉去掉「全部」，默认停在「选择已有组」（未分组）。"""
        dock = self._setup()
        entries = [dock.group_combo.itemData(i)
                   for i in range(dock.group_combo.count())]
        self.assertNotIn("ALL", entries)
        self.assertEqual(entries[0], "")
        self.assertEqual(dock._group_page, "")

    def test_join_group_popup_removed(self):
        """多选不应再弹出「加入组」下拉菜单：按钮与菜单方法都已移除，
        且视图右键上下文菜单被禁用（右键只用于框选）。"""
        dock = self._setup()
        self.assertFalse(hasattr(dock, "btn_join_group"),
                         "不应再存在「加入组 ▾」按钮")
        self.assertFalse(hasattr(dock, "_join_group_menu"),
                         "不应再存在 _join_group_menu 弹窗逻辑")
        from PySide6.QtCore import Qt
        self.assertEqual(dock.view.contextMenuPolicy(), Qt.NoContextMenu)

    def test_hidden_dock_builds_all_nodes(self):
        """虚拟化契约：面板隐藏（如单元测试 offscreen）时构建全部节点，行为与旧版一致。"""
        dock = self._setup()
        self.assertEqual(len(dock.node_items), len(dock.data.nodes))
        self.assertIsNone(dock._overview_item)

    def test_use_overview_threshold(self):
        """缩放很小（节点在屏幕上小于阈值）时进入概览模式，不建逐节点对象。"""
        dock = self._setup()
        dock.view.resetTransform()
        dock.view.scale(0.05, 0.05)        # 节点在屏幕上约 11px < 28px 阈值
        self.assertTrue(dock._use_overview())
        dock.view.resetTransform()         # 还原
        self.assertFalse(dock._use_overview())

    def test_overview_mode_uses_single_item(self):
        """概览模式：清空逐节点对象，改用一个 GraphOverviewItem 画出全部节点。"""
        dock = self._setup()
        dock._enter_overview()
        self.assertIsNotNone(dock._overview_item)
        self.assertEqual(len(dock.node_items), 0)
        self.assertEqual(len(dock._overview_item._nodes), len(dock.data.nodes))

    def test_rebuild_live_keeps_only_needed(self):
        """_rebuild_live 只保留 needed 集合里的节点，其余回收（虚拟化增删核心）。"""
        dock = self._setup()
        keep = next(iter(dock.data.nodes))
        all_keys = set(dock.data.nodes)
        dock._rebuild_live({keep}, all_keys, None)
        self.assertEqual(set(dock.node_items), {keep})
        # 还原：重建全部
        dock._rebuild_live(all_keys, all_keys, None)
        self.assertEqual(set(dock.node_items), all_keys)

    def test_node_items_order_follows_document(self):
        """node_items 顺序必须恒等于文档顺序：既不能被 needed 这个 set 的迭代
        顺序（Python 每进程 hash 随机）打乱，也不能被滚动时「先删后增」打乱。
        依赖 next(iter(node_items.values())) 的调用方因此能稳定取到首个对象。
        """
        dock = self._setup()
        keys = list(dock.data.nodes)

        class _ReversedSet(set):
            def __iter__(self):
                return iter(list(set.__iter__(self))[::-1])

        # 故意给一个迭代顺序与文档相反的 needed：旧实现遍历 set 就会逆序插入
        dock._rebuild_live(_ReversedSet(keys), set(keys), None)
        self.assertEqual(list(dock.node_items), keys)

        # 模拟滚动：只留后半，再恢复全部，顺序仍须是文档顺序
        dock._rebuild_live(set(keys[len(keys) // 2:]), set(keys), None)
        dock._rebuild_live(set(keys), set(keys), None)
        self.assertEqual(list(dock.node_items), keys)

    def test_building_nodes_does_not_record_manual_positions(self):
        """根因回归：按数据建 NodeItem 时 setPos 会触发 itemChange，不能被记成
        「用户手动摆位」。否则下次 refresh 跳过 _auto_positions，节点全堆在
        (0,0)，视口矩形与所有节点相交 → 虚拟化失效，大地图又变回全量构建。
        """
        dock = self._setup()
        self.assertEqual(len(dock._manual_pos), 0)
        dock.refresh(fit=False)
        self.assertEqual(len(dock._manual_pos), 0)
        pos = [(n.x, n.y) for n in dock.data.nodes.values()]
        self.assertEqual(len(set(pos)), len(pos),
                         "自动布局应把每个节点铺开到不同坐标")

    def test_overview_paint_culls_by_exposed_rect(self):
        """概览层：开启 extended style option，paint 兼容 None 且按 exposedRect 裁剪。"""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem
        dock = self._setup()
        dock._enter_overview()
        item = dock._overview_item
        self.assertTrue(item.flags() & QGraphicsItem.ItemUsesExtendedStyleOption)
        img = QImage(400, 300, QImage.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        try:
            item.paint(painter, None)                 # 无 option：全画，不抛异常
            opt = QStyleOptionGraphicsItem()
            opt.exposedRect = QRectF(-1e9, -1e9, 1, 1)   # 远离所有节点
            item.paint(painter, opt)                  # 裁剪分支，不抛异常
        finally:
            painter.end()

    def test_large_ungrouped_map_opens_in_overview(self):
        """别人写的「无分组」大地图：打开即概览（1 个 item），不建几千个对象，不卡。"""
        from rwmap.document.map_object import MapObject
        big = MapDocument(width=200, height=200, layers=[
            ObjectLayer(name="Triggers", objects=[
                MapObject(object_id=i, name=f"t{i}", type_="unitAdd",
                          shape="rect", x=(i % 50) * 220, y=(i // 50) * 130)
                for i in range(800)
            ])
        ])
        dock = self.w.trigger_graph_dock
        dock.hide()
        dock.set_document(big)
        dock.refresh(fit=False)
        # 隐藏态仍构建全部（契约）；模拟「显示且缩放极小」即走概览路径
        dock._enter_overview()
        self.assertIsNotNone(dock._overview_item)
        self.assertEqual(len(dock._overview_item._nodes), 800)
        self.assertEqual(len(dock.node_items), 0)

    # ---------------- 拖动范围 / 全局连线层 ----------------
    def test_scene_rect_allows_dragging_far(self):
        """sceneRect 不能再收缩到「当前 live 节点」那一小块，否则滚动条只剩
        几十像素，节点拖出视口 / 平移到远处都做不到（移动范围有限制）。"""
        from PySide6.QtCore import QPointF
        dock = self._setup()
        r = dock.scene.sceneRect()
        self.assertGreater(r.width(), 3000)
        self.assertGreater(r.height(), 3000)
        key = next(iter(dock.data.nodes))
        dock.remember_position(key, QPointF(9000, -4000))
        self.assertTrue(
            dock.scene.sceneRect().contains(QPointF(9000, -4000)),
            "拖到远处后画布范围应自动扩展，视图才能滚过去")

    def test_drag_syncs_data_coordinates(self):
        """拖动必须同步数据层坐标，否则连线层 / 视口裁剪仍用旧位置。"""
        from PySide6.QtCore import QPointF
        dock = self._setup()
        key = next(iter(dock.data.nodes))
        dock.node_items[key].setPos(1234, 567)
        self.assertEqual(
            (dock.data.nodes[key].x, dock.data.nodes[key].y), (1234.0, 567.0))
        self.assertEqual(dock._manual_pos[key], QPointF(1234, 567))

    def test_dragging_node_survives_viewport_recycle(self):
        """正在拖动的节点即使出了视口也不许被虚拟化回收（否则拖到一半中断）。"""
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        dock.view.resetTransform()
        dock.view.scale(0.9, 0.9)
        dock._sync_visible()
        key = next(iter(dock.data.nodes))
        dock._dragging_key = key
        dock.data.nodes[key].x = 99999.0
        dock.node_items[key].setPos(99999.0, dock.data.nodes[key].y)
        dock._sync_visible()
        self.assertIn(key, dock.node_items)
        # 松手后不再保护，远离视口的节点正常回收
        dock._dragging_key = None
        dock._sync_visible()
        self.assertNotIn(key, dock.node_items)

    def test_auto_scroll_follows_node_drag(self):
        """拖到视口边缘时视图自动滚动跟随，不会「拖到边界就不动」。"""
        from PySide6.QtCore import QPoint
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        dock._sync_visible()
        bar = dock.view.horizontalScrollBar()
        before = bar.value()
        dock.view._auto_scroll_on_drag(QPoint(5, 300))
        self.assertLess(bar.value(), before)

    def test_edge_layer_is_permanent(self):
        """全局连线层常驻 scene，refresh 不会把它清掉。"""
        dock = self._setup()
        self.assertIn(dock.edge_layer, dock.scene.items())
        dock.refresh(fit=False)
        self.assertIn(dock.edge_layer, dock.scene.items())
        self.assertEqual(len(dock.edge_layer._boxes), len(dock.data.edges))

    def test_overview_draws_all_edges(self):
        """缩小到概览（零 live 节点）时，连线仍由全局层全部画出，且是曲线不是直线。"""
        from PySide6.QtWidgets import QStyleOptionGraphicsItem
        dock = self._setup()
        dock._enter_overview()
        self.assertEqual(len(dock._live_edges), 0)
        self.assertFalse(dock._overview_item._draw_edges)
        p = _CountingPainter()
        dock.edge_layer.paint(p, QStyleOptionGraphicsItem(), None)
        self.assertEqual(p.paths, len(dock.data.edges))
        self.assertEqual(p.lines, 0,
                         "概览模式不能用两点直线代替贝塞尔连线")

    def test_edges_drawn_when_endpoints_not_live(self):
        """连线两端节点没被渲染时，缺的线由全局层补画（曲线 + 箭头）。

        裁剪以「真实视口矩形」为准：视口内能看到的远连线照常画；视口外的线被裁剪，
        保证大图缩放/平移时不全量重绘。
        """
        from PySide6.QtWidgets import QStyleOptionGraphicsItem
        dock = self._setup()
        k3 = next(k for k, n in dock.data.nodes.items() if n.tid == "3")
        dock._remove_node_item(k3)
        missing = [e for e in dock.data.edges if id(e) not in dock._live_edges]
        self.assertGreater(len(missing), 0)
        # 视口正对图形：全局层补画这些缺的线
        p = _CountingPainter()
        dock.edge_layer.paint(p, QStyleOptionGraphicsItem(), None)
        self.assertEqual(p.paths, len(missing))
        # 把视口平移到远离图形处：这些线应被裁剪掉（不再全量重绘）
        dock.view.resetTransform()
        dock.view.centerOn(1e9, 1e9)
        p2 = _CountingPainter()
        dock.edge_layer.paint(p2, QStyleOptionGraphicsItem(), None)
        self.assertEqual(p2.paths, 0)
        dock.view.resetTransform()


    # ---------------- 叉 / 连线列表 / 空组 ----------------
    def _edge_rows(self, dock):
        from PySide6.QtWidgets import QWidget
        return [w for w in dock.inspector.findChildren(QWidget)
                if w.objectName() == "EdgeRow"]

    def _show_first_object(self, dock):
        obj = dock.doc.layers[0].objects[0]
        dock.select_objects([obj])
        QApplication.processEvents()
        return obj

    def test_symmetric_cross_is_geometrically_centered(self):
        """叉必须中心对称：镜像 / 旋转 180° 后像素完全一致（不用字体字符）。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor, QImage, QPainter
        from rwmap.widgets.trigger_inspector import draw_symmetric_cross
        img = QImage(80, 80, QImage.Format_ARGB32)
        img.fill(QColor("#202020"))
        p = QPainter(img)
        draw_symmetric_cross(p, QPointF(40, 40), 16, QColor("#ff8888"), 3.0)
        p.end()
        self.assertEqual(img.mirrored(True, False), img)
        self.assertEqual(img.mirrored(False, True), img)
        self.assertEqual(img.mirrored(True, True), img)
        self.assertNotEqual(img.pixelColor(40, 40), QColor("#202020"))
        self.assertEqual(img.pixelColor(2, 2), QColor("#202020"),
                         "叉之外不应画任何边框 / 底色")

    def test_edge_rows_have_borderless_cross(self):
        """右侧「输入 / 输出连线」每行末尾都有无边框的中心对称叉，右键可弹菜单。"""
        from PySide6.QtWidgets import QPushButton
        from rwmap.widgets.trigger_inspector import CrossButton
        dock = self._setup()
        self._show_first_object(dock)
        rows = self._edge_rows(dock)
        self.assertEqual(len(rows), 4)       # 节点 #1 牵着 4 条线
        crosses = [b for b in dock.inspector.findChildren(CrossButton)
                   if "border:0" in b.styleSheet()]
        self.assertEqual(len(crosses), 4)
        self.assertTrue(all(b.text() == "" for b in crosses),
                        "叉是几何绘制，不再用 ✕ 字符")
        self.assertEqual(rows[0].contextMenuPolicy(), Qt.CustomContextMenu)

    def test_edge_cross_click_deletes_only_that_reference(self):
        """点连线行的叉：删掉这一条引用，其余引用（含带括号的生成项）原样保留。"""
        dock = self._setup()
        obj = dock.doc.layers[0].objects[0]
        obj.properties.set(
            "alsoActivate",
            "2, jcroom34mechFlame(spawnChance=0.04,maxSpawnLimit=1)", "string")
        dock.refresh(fit=False)
        self._show_first_object(dock)
        e = next(e for e in dock.data.edges
                 if e.kind == "alsoActivate" and e.dst in dock.data.nodes
                 and dock.data.nodes[e.dst].tid == "2")
        # 直接走数据接口，验证「删一条、留其它」
        dock.delete_edge(e)
        self.assertEqual(
            obj.properties.get("alsoActivate"),
            "jcroom34mechFlame(spawnChance=0.04,maxSpawnLimit=1)")
        self.assertEqual(len(self._edge_rows(dock)), len(dock._edges_for(
            next(n for n in dock.data.nodes.values()
                 if n.obj.object_id == obj.object_id))))

    def test_edge_menu_changes_link_type(self):
        """右键菜单换线型：引用从旧属性搬到新属性，方向变了则持有者互换。"""
        dock = self._setup()
        obj1 = dock.doc.layers[0].objects[0]
        obj2 = dock.doc.layers[0].objects[1]
        e = next(e for e in dock.data.edges
                 if e.kind == "alsoActivate" and dock.data.nodes[e.dst].tid == "2")
        menu = dock.edge_context_menu(e)
        texts = [a.text() for a in menu.actions()]
        self.assertIn("停用（deactivatedBy）", texts)
        self.assertIn("删除这条连线", texts)
        self.assertFalse(any("whenActivatedlds" in t for t in texts),
                         "旧地图错拼兼容项不主动提供")
        current = next(a for a in menu.actions() if a.data() == "alsoActivate")
        self.assertTrue(current.isCheckable())
        self.assertFalse(current.isEnabled())
        stop = next(a for a in menu.actions() if a.data() == "deactivatedBy")
        stop.trigger()
        QApplication.processEvents()
        self.assertNotIn("2", (obj1.properties.get("alsoActivate") or "").split(", "))
        self.assertEqual(obj2.properties.get("deactivatedBy"), "1")
        self.assertIn("deactivatedBy", [x.kind for x in dock.data.edges])

    def test_edge_row_right_click_requests_menu(self):
        """右键连线行 → 发出带原始 GraphEdge 的菜单请求（画布右键仍是框选）。"""
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QContextMenuEvent
        from PySide6.QtWidgets import QApplication as QA
        dock = self._setup()
        self._show_first_object(dock)
        row = self._edge_rows(dock)[0]
        calls = []
        dock.inspector.edgeMenuRequested.connect(
            lambda e, p: calls.append((e, p)))
        QA.sendEvent(row, QContextMenuEvent(
            QContextMenuEvent.Mouse, QPoint(8, 4)))
        QA.processEvents()
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0][0], type(dock.data.edges[0]))

    def test_new_group_without_selection_creates_session_group(self):
        """没选中对象也能新建组：本次会话下拉里可选 / 画布可加入，但不写文档。"""
        from PySide6.QtWidgets import QInputDialog
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        dock = self._setup()
        with patch.object(QInputDialog, "getText",
                          return_value=("空组X", True)):
            dock._new_group_dialog()
        entries = [dock.group_combo.itemData(i)
                   for i in range(dock.group_combo.count())]
        self.assertIn("空组X", entries)
        self.assertEqual(dock._group_page, "空组X")
        self.assertEqual(dock.empty_groups(), {"空组X"})
        self.assertNotIn("空组X", dock.current_groups())
        self.assertTrue(all(EDITOR_GROUP_PROPERTY not in o.properties
                            for o in dock.doc.layers[0].objects))
        self.w.state.doc = dock.doc
        self.assertIn("空组X", self.w._current_groups())

    def test_empty_group_becomes_real_when_objects_join(self):
        """空组一旦有对象写入属性，就从会话态变成真正会保存的组。"""
        from PySide6.QtWidgets import QInputDialog
        dock = self._setup()
        with patch.object(QInputDialog, "getText",
                          return_value=("组A", True)):
            dock._new_group_dialog()
        dock._group_page = ""
        dock._apply_filter()                  # 回到未分组页，节点重新建出来
        item = next(iter(dock.node_items.values()))
        dock.scene.clearSelection()
        item.setSelected(True)
        dock._join_selected_to_group("组A")
        QApplication.processEvents()
        self.assertEqual(dock.empty_groups(), set())
        self.assertIn("组A", dock.current_groups())
        self.assertIn("组A", [dock.group_combo.itemData(i)
                             for i in range(dock.group_combo.count())])


    # ---------------- 端点 / 位置稳定 / 右侧同步 ----------------
    def test_edge_endpoints_are_left_right_midpoints(self):
        """连线从第一个节点右侧中间连到第二个节点左侧中间。"""
        from PySide6.QtCore import QPointF
        from rwmap.widgets.trigger_graph_dock import (
            NODE_H, NODE_W, PORT_Y, _edge_endpoints)
        self.assertEqual(PORT_Y, NODE_H / 2.0)
        dock = self._setup()
        nodes = list(dock.data.nodes.values())
        a, b = nodes[0], nodes[1]
        pa, pb = _edge_endpoints(a, b)
        self.assertEqual(pa, QPointF(a.x + NODE_W, a.y + NODE_H / 2.0))
        self.assertEqual(pb, QPointF(b.x, b.y + NODE_H / 2.0))
        ka = next(k for k, n in dock.data.nodes.items() if n is a)
        item = dock.node_items[ka]
        # 黄点端口、EdgeItem 曲线起点都用同一 PORT_Y
        self.assertEqual(item.scene_port_out(),
                         QPointF(a.x + NODE_W, a.y + NODE_H / 2.0))
        self.assertEqual(item.scene_port_in(),
                         QPointF(a.x, a.y + NODE_H / 2.0))
        edge = next(it for it in dock.edge_items
                    if it.src.node.key == ka)
        self.assertEqual(edge._path.pointAtPercent(0.0), item.scene_port_out())

    def test_edge_path_is_cubic_not_straight_line(self):
        """概览 / 细节共用的路径必须是三次贝塞尔，不是两点直线。"""
        from PySide6.QtCore import QPointF
        from rwmap.widgets.trigger_graph_dock import _edge_path
        a, b = QPointF(0, 46), QPointF(600, 46)
        path = _edge_path(a, b, 0, 1)
        self.assertGreater(path.elementCount(), 2)
        self.assertEqual(path.pointAtPercent(0.0), a)
        self.assertEqual(path.pointAtPercent(1.0), b)

    def test_create_link_keeps_node_positions_and_view(self):
        """连一条线后节点坐标与视图缩放都不变（不重排、不 fit 回居中）。"""
        dock = self._setup()
        dock.resize(900, 620)
        dock.show()
        QApplication.processEvents()
        dock.on_change = lambda: dock.refresh()   # 模拟主窗口 _refresh_objects
        dock.refresh(fit=True)
        QApplication.processEvents()
        before = {k: (n.x, n.y) for k, n in dock.data.nodes.items()}
        transform = dock.view.transform()
        k4 = next(k for k, n in dock.data.nodes.items() if n.tid == "4")
        k2 = next(k for k, n in dock.data.nodes.items() if n.tid == "2")
        count = len(dock.data.edges)
        dock.create_link(dock.data.nodes[k4], dock.data.nodes[k2])
        QApplication.processEvents()
        self.assertEqual(len(dock.data.edges), count + 1)
        self.assertEqual({k: (n.x, n.y) for k, n in dock.data.nodes.items()},
                         before)
        self.assertEqual(dock.view.transform(), transform)

    def test_refresh_keeps_layout_after_doc_edit(self):
        """改属性 / 撤销带来的 refresh 也不能把自动布局的节点重排一遍。"""
        dock = self._setup()
        before = {k: (n.x, n.y) for k, n in dock.data.nodes.items()}
        dock.doc.layers[0].objects[3].properties.set(
            "alsoActivate", "2", "string")
        dock.refresh(fit=False)
        self.assertEqual(len(dock.data.edges), 5)
        self.assertEqual({k: (n.x, n.y) for k, n in dock.data.nodes.items()},
                         before)

    def test_new_object_does_not_move_existing_nodes(self):
        """往文档里加对象后 refresh：老节点坐标不动，新节点排在空位。"""
        dock = self._setup()
        before = {k: (n.x, n.y) for k, n in dock.data.nodes.items()}
        dock.doc.layers[0].objects.append(
            MapObject(object_id=99, name="新对象", type_="unitAdd",
                      shape="point", properties=_bag(id="99")))
        dock.refresh(fit=False)
        after = {k: (n.x, n.y) for k, n in dock.data.nodes.items()}
        for k, pos in before.items():
            self.assertEqual(after[k], pos)
        new = [v for k, v in after.items() if k not in before]
        self.assertEqual(len(new), 1)
        self.assertNotIn(new[0], before.values())

    def test_inspector_rebinds_to_replaced_object(self):
        """ObjectCommand 用快照替换对象后，右侧显示的是新对象的当前值。"""
        from rwmap.commands.object_command import snapshot, restore
        dock = self._setup()
        old = self._show_first_object(dock)
        layer = dock.doc.layers[0]
        snap = snapshot(layer)
        snap[0].properties.values["warmup"] = "99s"
        restore(layer, snap)          # layer.objects 整体换成新实例
        dock.refresh(fit=False)
        self.assertIs(dock.inspector.obj, layer.objects[0])
        self.assertIsNot(dock.inspector.obj, old)
        self.assertEqual(dock.inspector.f_id.text(), "1")
        self.assertTrue(any(k == "warmup" and edit.text() == "99s"
                            for k, edit, _hint in dock.inspector._prop_rows))

    def test_select_objects_matches_by_id_and_without_live_item(self):
        """传进来的对象实例过期、或节点没被渲染，右侧仍显示当前文档里的它。"""
        import copy
        dock = self._setup()
        obj = dock.doc.layers[0].objects[1]
        stale = copy.copy(obj)               # 同 object_id，不同实例
        key = next(k for k, n in dock.data.nodes.items()
                   if n.obj.object_id == obj.object_id)
        dock._remove_node_item(key)          # 模拟虚拟化回收 / 概览模式
        dock.select_objects([stale])
        QApplication.processEvents()
        self.assertIs(dock.inspector.obj, obj)
        self.assertEqual(dock.inspector.f_name.text(), obj.name)
        self.assertEqual(dock._selected_keys, {key})

    def test_select_object_in_other_layer_shows_current_data(self):
        """选中不在当前显示层的对象时显示该对象，不残留上一个节点的数据。"""
        dock = self._setup()
        self._show_first_object(dock)
        other = ObjectLayer(name="Units", objects=[
            MapObject(object_id=77, name="别的层对象", type_="unitAdd",
                      shape="point", properties=_bag(id="77", team="3"))])
        dock.doc.layers.append(other)
        dock.select_objects([other.objects[0]])
        QApplication.processEvents()
        self.assertIs(dock.inspector.obj, other.objects[0])
        self.assertEqual(dock.inspector.f_name.text(), "别的层对象")
        self.assertEqual(dock.inspector.f_id.text(), "77")

    def test_edge_cross_is_red(self):
        """连线行删除叉统一红色，不再跟随线型颜色。"""
        from PySide6.QtGui import QColor
        from rwmap.widgets.trigger_inspector import (
            EDGE_CROSS_COLOR, CrossButton)
        dock = self._setup()
        self._show_first_object(dock)
        crosses = [b for b in dock.inspector.findChildren(CrossButton)
                   if b.accessibleName() == "删除" and not b.text()
                   and "border:0" in b.styleSheet()]
        self.assertEqual(len(crosses), 4)
        red = QColor(EDGE_CROSS_COLOR)
        self.assertTrue(all(b._cross_color == red for b in crosses))
        self.assertGreater(red.red(), red.green() * 2)
        self.assertGreater(red.red(), red.blue() * 2)

    def test_create_link_refreshes_edge_rows(self):
        """连完线右侧连线列表立刻重建，不再是旧快照。"""
        dock = self._setup()
        self._show_first_object(dock)
        rows_before = len(self._edge_rows(dock))
        k4 = next(k for k, n in dock.data.nodes.items() if n.tid == "4")
        k1 = next(k for k, n in dock.data.nodes.items() if n.tid == "1")
        dock.create_link(dock.data.nodes[k4], dock.data.nodes[k1])
        QApplication.processEvents()
        self.assertEqual(len(self._edge_rows(dock)), rows_before + 1)


if __name__ == "__main__":
    unittest.main()
