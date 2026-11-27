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

    def test_top_level_windows_suppress_empty_context_menu(self):
        """右键拖动后一闪而过的空框 = 冒泡到顶层的原生空菜单。

        右键释放的 QContextMenuEvent 会沿 viewport→view→…→顶层一路冒泡，
        即便各视图都是 NoContextMenu，只要顶层还是默认策略就会弹空菜单。
        因此 QMainWindow 与浮动 dock 自身都必须是 NoContextMenu。
        """
        from PySide6.QtCore import Qt
        dock = self._setup()

        self.assertEqual(self.w.contextMenuPolicy(), Qt.NoContextMenu,
                         "QMainWindow 顶层不能弹默认空右键菜单")
        self.assertEqual(dock.contextMenuPolicy(), Qt.NoContextMenu,
                         "浮动 dock 作为顶层窗口也不能弹默认空右键菜单")

    def test_select_nodes_spawns_no_untitled_toplevel_windows(self):
        """选中节点、重建属性面板时不得闪出无标题顶层窗。

        真凶不是 tooltip，而是属性行的 vhint 在 addWidget 之前 setVisible(True)
        （parent=None 会被 Qt 当顶层窗口显示）。这里从实际选中路径回归。
        """
        dock = self._setup()

        from rwmap.widgets.trigger_graph_dock import NodeItem
        nodes = [it for it in dock.scene.items() if isinstance(it, NodeItem)]
        self.assertTrue(nodes, "测试文档应含节点")

        # 先渲染一次稳定基线，再反复选中记录新增的可见顶层窗
        nodes[0].setSelected(True)
        QApplication.processEvents()
        base = {id(w) for w in QApplication.topLevelWidgets()}
        for it in nodes:
            it.setSelected(True)
            QApplication.processEvents()
        leaked = [w for w in QApplication.topLevelWidgets()
                  if id(w) not in base and w.isVisible()
                  and (w.windowTitle() or "").strip() == ""]
        self.assertEqual(leaked, [],
                         "选中节点冒出无标题顶层窗: %r"
                         % [type(w).__name__ for w in leaked])

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
        # 用户反馈：右键拖动框选结束会闪一个空白小窗。根因是鼠标事件实际
        # 落在 viewport 子控件上，它默认 DefaultContextMenu，必须一并关掉。
        self.assertEqual(dock.view.viewport().contextMenuPolicy(),
                         Qt.NoContextMenu)

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

    # ---------------- 动态平移范围 / 全局连线层 ----------------
    def _assert_scene_rect_is_dynamic(self, dock, msg=""):
        """断言 sceneRect == 内容框四周各扩一个视口尺寸（场景坐标）。"""
        from PySide6.QtCore import QRectF
        content = dock._content_rect()
        vp = dock.view.viewport()
        z = max(dock.view.zoom(), 0.05)
        mx = vp.width() / z if vp.width() > 0 else 0.0
        my = vp.height() / z if vp.height() > 0 else 0.0
        want = content.adjusted(-mx, -my, mx, my)
        got = dock.scene.sceneRect()
        delta = 2.0  # 像素级容差
        self.assertAlmostEqual(got.left(), want.left(), delta=delta, msg=msg)
        self.assertAlmostEqual(got.top(), want.top(), delta=delta, msg=msg)
        self.assertAlmostEqual(got.right(), want.right(), delta=delta, msg=msg)
        self.assertAlmostEqual(got.bottom(), want.bottom(), delta=delta, msg=msg)
        return got, want, mx, my

    def test_scene_rect_is_content_plus_one_viewport(self):
        """平移范围 = 内容框 + 四周各一个视口，不再是固定 3000 大留白。

        数学：视口左上角可移动到 [sceneRect.left, sceneRect.right-W]。左右各扩
        W 后区间为 [contentLeft-W, contentRight]；推到最右时视口左边缘正好落在
        最右节点右边缘 —— 即「最近节点刚好完全移出视口即停」。
        """
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        dock._ensure_scene_rect()
        got, want, mx, my = self._assert_scene_rect_is_dynamic(dock)
        # 留白不再是固定 3000：应接近一个视口尺寸
        self.assertLess(mx, 3000)
        self.assertGreater(mx, 0)
        # sceneRect 左右各扩 mx，正好等于内容框左右 ± mx
        content = dock._content_rect()
        self.assertAlmostEqual(got.right() - mx, content.right(), delta=2)
        self.assertAlmostEqual(got.left() + mx, content.left(), delta=2)

    def test_scene_rect_shrinks_when_node_moved_back(self):
        """节点往回拖 → 内容框收缩 → sceneRect 跟着收缩（不再只增不减）。"""
        from PySide6.QtCore import QPointF
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        dock._ensure_scene_rect()
        before = dock.scene.sceneRect()
        key = next(iter(dock.data.nodes))
        # 把最左边的节点拖到内容框内部（靠右），内容框左边界应右移
        dock.remember_position(key, QPointF(5000, 5000))
        dock._ensure_scene_rect()
        after = dock.scene.sceneRect()
        # 拖远后范围扩展包含新位置
        self.assertTrue(after.contains(QPointF(5000, 5000)))
        # 再拖回中心附近，范围应收缩（右边界明显小于拖远后的右边界）
        dock.remember_position(key, QPointF(300, 60))
        dock._ensure_scene_rect()
        back = dock.scene.sceneRect()
        self.assertLess(back.right(), after.right() - 1000,
                        "节点拖回后画布范围应收缩，不是只增不减")
        self._assert_scene_rect_is_dynamic(dock, msg="收缩后仍满足动态公式")

    def test_scene_rect_expands_to_hold_dragged_node(self):
        """节点拖到远处 → 范围扩展包含它，视图才能滚过去（不回归「拖不动」）。"""
        from PySide6.QtCore import QPointF
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        key = next(iter(dock.data.nodes))
        dock.remember_position(key, QPointF(9000, -4000))
        dock._ensure_scene_rect()
        self.assertTrue(
            dock.scene.sceneRect().contains(QPointF(9000, -4000)),
            "拖到远处后画布范围应自动扩展，视图才能滚过去")
        self._assert_scene_rect_is_dynamic(dock)

    def test_pan_limit_edge_means_nearest_node_just_offscreen(self):
        """平移极限的几何含义：视口推到最右，最近节点刚好完全在视口外。"""
        from PySide6.QtCore import QPointF
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        dock._ensure_scene_rect()
        vp = dock.view.viewport()
        z = max(dock.view.zoom(), 0.05)
        mx = vp.width() / z
        r = dock.scene.sceneRect()
        content = dock._content_rect()
        # 视口左上角最大 = sceneRect.right - W = content.right，
        # 此刻视口左边缘 == 最右节点右边缘 → 该节点刚好完全不显示
        viewport_left_at_max = r.right() - mx
        self.assertAlmostEqual(viewport_left_at_max, content.right(), delta=2)
        rightmost = max(n.x + 218.0 for n in dock.data.nodes.values())
        self.assertAlmostEqual(content.right(), rightmost, delta=2)

    def test_drag_node_outside_does_not_recurse(self):
        """拖节点出视口再拖回不应因 sceneRect 连锁回调无限递归爆栈。

        回归：setSceneRect→scrollContentsBy→itemChange→on_node_moved→
        remember_position→_ensure_scene_rect→setSceneRect…（_scene_rect_syncing 锁）。
        """
        from PySide6.QtCore import QPointF
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        key = next(iter(dock.data.nodes))
        for i in range(25):
            dock.remember_position(key, QPointF(10000 + i * 10, -8000 - i * 5))
            dock.remember_position(key, QPointF(i * 10, i * 5))
        self.assertFalse(dock._scene_rect_syncing, "重入锁应在每次调用后复位")
        self._assert_scene_rect_is_dynamic(dock)

    def test_edge_item_pen_is_cosmetic(self):
        """细节模式连线笔宽按屏幕像素固定：缩放时线条粗细不变。"""
        dock = self._setup()
        item = next(iter(dock.edge_items))
        import inspect
        from rwmap.widgets.trigger_graph_dock import (
            _edge_color, _edge_resources, _paint_edge_curve,
        )
        # 笔宽创建集中到 _edge_resources（避免每帧几千次 setCosmetic）
        self.assertIn("setCosmetic(True)", inspect.getsource(_edge_resources))
        pen = _edge_resources(item._color)[1]
        self.assertTrue(pen.isCosmetic(),
                        "EdgeItem 连线笔宽必须 cosmetic，缩放才不变粗/细")
        self.assertTrue(_edge_resources(_edge_color(item.kind))[1].isCosmetic(),
                        "全局连线层的贝塞尔笔宽也必须 cosmetic")
        # 两条绘制路径都从同一份 cosmetic 资源取笔
        self.assertIn("_edge_resources(", inspect.getsource(_paint_edge_curve))

    def test_overview_box_draws_above_edges(self):
        """缩小概览：节点色块 z 必须高于全局连线层（矩形在线条之上）。"""
        dock = self._setup()
        dock._enter_overview()
        self.assertIsNotNone(dock._overview_item)
        self.assertGreater(dock._overview_item.zValue(), dock.edge_layer.zValue(),
                           "概览节点矩形应盖在连线之上")

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
        # 把视口移到远离图形处验证裁剪：动态平移范围会把视口 clamp 回节点附近，
        # 因此这里临时放大 sceneRect 以隔离验证 edge_layer 自身的视口裁剪逻辑。
        dock.view.resetTransform()
        old_rect = dock.scene.sceneRect()
        try:
            dock.scene.setSceneRect(old_rect.adjusted(-1e9, -1e9, 1e9, 1e9))
            dock.view.centerOn(1e9, 1e9)
            p2 = _CountingPainter()
            dock.edge_layer.paint(p2, QStyleOptionGraphicsItem(), None)
            self.assertEqual(p2.paths, 0)
        finally:
            dock.scene.setSceneRect(old_rect)
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

    # ---------------- 本轮三项改动 ----------------
    def test_auto_scroll_is_time_throttled_and_slow(self):
        """拖到边缘的视口跟随按时间节流、步长变小（窗口不再瞬间窜出去）。"""
        from PySide6.QtCore import QPoint
        dock = self._setup()
        dock.show()
        QApplication.processEvents()
        view = dock.view
        self.assertGreaterEqual(view.AUTO_SCROLL_INTERVAL_MS, 30)
        self.assertLessEqual(view.AUTO_SCROLL_PX, 6)
        bar = view.horizontalScrollBar()
        # 连续两次贴边调用（同一毫秒内）只滚一步，而不是每次事件都滚
        view._last_auto_scroll_ms = 0
        bar.setValue(bar.minimum() + 100)
        view._auto_scroll_paused = False
        v0 = bar.value()
        edge = QPoint(view.viewport().width() - 5,
                      view.viewport().height() // 2)
        view._auto_scroll_on_drag(edge)
        v1 = bar.value()
        view._auto_scroll_on_drag(edge)     # 紧接着第二次：应被节流拦住
        v2 = bar.value()
        self.assertGreater(v1, v0)
        self.assertEqual(v2, v1, "同一节流窗口内不应再次滚动")
        # 离开边缘后时间戳清零，下次贴边立即响应
        view._auto_scroll_on_drag(QPoint(200, 200))
        self.assertEqual(getattr(view, "_last_auto_scroll_ms", 0), 0)

    def test_node_drag_refreshes_edge_clip_box(self):
        """节点拖动后全局连线层的裁剪盒跟随更新（连线不再凭空消失）。"""
        from PySide6.QtCore import QPointF, QRectF
        dock = self._setup()
        # 造一条 1 -> 2 的连线（1 有 alsoActivate="2, 3"，2 有 whenActivatedIds="1"）
        e = next(e for e in dock.data.edges
                 if e.src.endswith("::1") and e.dst.endswith("::2"))
        eid = id(e)
        box_before = QRectF(dock.edge_layer._boxes[eid])
        k1 = next(k for k in dock.data.nodes if k.endswith("::1"))
        dock.remember_position(k1, QPointF(5000, 5000))
        box_after = dock.edge_layer._boxes[eid]
        self.assertNotEqual(box_before, box_after,
                            "拖动节点后相连边的裁剪盒应更新到新位置")
        # 新盒必须包住被拖到 (5000,5000) 的节点右缘；旧盒不会覆盖这里
        self.assertGreaterEqual(box_after.right(), 5000 + 218,
                                "裁剪盒应扩展到被拖节点的新位置")

    def test_write_positions_relative_to_top_left_and_readback(self):
        """保存时以左上角对象为(0,0)写相对坐标，读回可恢复布局。"""
        from PySide6.QtCore import QPointF
        from rwmap.document.properties import EDITOR_NODE_POS_PROPERTY
        dock = self._setup()
        # 手动把 4 个节点摆成已知布局，最左上是 object 1 @ (100, 50)
        keys = sorted(dock.data.nodes)  # 文档顺序：1,2,3,4
        layout = [(100, 50), (180, 50), (260, 50), (340, 50)]
        for k, (x, y) in zip(keys, layout):
            dock.remember_position(k, QPointF(x, y))
        n = dock.write_positions_to_properties()
        self.assertEqual(n, 4)
        # object 1 是左上角 → (0,0)，其余按相对偏移
        obj1 = dock.data.nodes[keys[0]].obj
        obj3 = dock.data.nodes[keys[2]].obj
        self.assertEqual(obj1.properties.get(EDITOR_NODE_POS_PROPERTY), "0,0")
        self.assertEqual(obj3.properties.get(EDITOR_NODE_POS_PROPERTY), "160,0")
        # 对象层被置脏，writer 才会把属性落盘
        self.assertTrue(dock.doc.layers[0].dirty)
        # 读回：清空缓存后从属性种子恢复，坐标与写入前一致
        saved = dock._saved_positions()
        self.assertEqual(saved[keys[0]], QPointF(0, 0))
        self.assertEqual(saved[keys[2]], QPointF(160, 0))

    def test_write_positions_noop_when_graph_never_opened(self):
        """节点图没数据时不写属性，避免误覆盖。"""
        dock = self._setup()
        dock.data = None
        self.assertEqual(dock.write_positions_to_properties(), 0)

    def test_node_pos_property_survives_tmx_roundtrip(self):
        """布局写进地图根属性（游戏不校验），对象属性里不含 rwmap_node_pos。

        保存 → 重新读取 → 节点位置仍能恢复，且游戏不会报 Key was not used。
        """
        import tempfile
        from pathlib import Path
        from PySide6.QtCore import QPointF
        from rwmap.io.tmx_writer import write_document
        from rwmap.io.tmx_reader import read_document
        from rwmap.document.properties import EDITOR_NODE_POS_PROPERTY
        from rwmap.document.layer import ObjectLayer

        from rwmap.io.tmx_writer import create_blank_document
        profile = self.w.state.profile
        out = Path(tempfile.gettempdir()) / "rwmap_nodepos_roundtrip.tmx"
        # make_doc 对象是现成的，塞进一张带真实 DOM 的文档才能写盘
        src = make_doc()
        doc = create_blank_document(profile, 32, 20, path=out)
        doc.layers = list(src.layers)
        doc.rebuild_index()
        dock = self.w.trigger_graph_dock
        dock.set_document(doc)
        dock.refresh(fit=False)
        keys = sorted(dock.data.nodes)
        for k, (x, y) in zip(keys, [(100, 50), (180, 50), (260, 50), (340, 50)]):
            dock.remember_position(k, QPointF(x, y))
        dock.write_positions_to_properties()
        write_document(doc, out)
        try:
            xml = out.read_text(encoding="utf-8")
            # 布局写进地图根属性（游戏不校验），不能出现在触发器对象属性里
            self.assertIn('name="rwmap_layout"', xml)
            self.assertNotIn(
                f'<property name="{EDITOR_NODE_POS_PROPERTY}"', xml)
            re_doc = read_document(out, profile)
            re_objs = [o for l in re_doc.layers if isinstance(l, ObjectLayer)
                       for o in l.objects]
            vals = [o.properties.get(EDITOR_NODE_POS_PROPERTY) for o in re_objs]
            self.assertIn("0,0", vals)
            self.assertIn("160,0", vals)
        finally:
            out.unlink(missing_ok=True)

    # ---------------- 边缘判定区 + 切层边界 ----------------
    def test_edge_zone_visual_hidden_but_function_kept(self):
        """判定区按用户要求隐藏：paintEvent 不再画四条半透明边条，
        但边缘持续滚动的功能逻辑（宽度常量 / 方向判定）必须保留。"""
        import inspect
        dock = self._setup()
        view = dock.view
        # 功能保留
        self.assertGreaterEqual(view.EDGE_ZONE_PX, 8)
        self.assertTrue(callable(getattr(view, "_edge_direction", None)))
        self.assertTrue(callable(getattr(view, "_auto_scroll_tick", None)))
        # 视觉隐藏：paintEvent 源码不再出现画边条的 fillRect / 半透明色
        src = inspect.getsource(type(view).paintEvent)
        self.assertNotIn("fillRect", src, "判定区边条不应再被绘制")
        self.assertNotIn("120, 180, 255", src)

    def test_edge_zone_is_small_and_direction_correct(self):
        """四周判定区宽度合理（"不要太大"），方向判定正确。"""
        from PySide6.QtCore import QPoint
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        view = dock.view
        self.assertLessEqual(view.EDGE_ZONE_PX, 30)
        self.assertGreaterEqual(view.EDGE_ZONE_PX, 8)
        vp = view.viewport()
        self.assertEqual(view._edge_direction(QPoint(2, vp.height() // 2)), (-1, 0))
        self.assertEqual(view._edge_direction(QPoint(vp.width() - 2, 300)), (1, 0))
        self.assertEqual(view._edge_direction(QPoint(400, 2)), (0, -1))
        self.assertEqual(view._edge_direction(QPoint(400, vp.height() - 2)), (0, 1))
        self.assertEqual(view._edge_direction(QPoint(400, 300)), (0, 0))

    def test_auto_scroll_timer_keeps_scrolling_without_mouse_move(self):
        """进入判定区后，定时器持续滚动（鼠标不再发 move 事件也一直走）。"""
        from PySide6.QtCore import QPoint
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        view = dock.view
        bar = view.horizontalScrollBar()
        bar.setValue(bar.minimum() + 200)
        vp = view.viewport()
        # 模拟拖动中：dragging_key 非空 + 鼠标停在左边缘判定区
        dock._dragging_key = next(iter(dock.data.nodes))
        try:
            view._auto_scroll_on_drag(QPoint(2, vp.height() // 2))
            self.assertTrue(view._auto_scroll_timer.isActive(),
                            "进入判定区应启动持续滚动定时器")
            v_before = bar.value()
            # 不再调用 mouseMove，连打多次 tick 模拟定时器持续滚动
            for _ in range(5):
                view._auto_scroll_tick()
            self.assertLess(bar.value(), v_before,
                            "鼠标停住时定时器仍应持续滚动视口")
        finally:
            dock._dragging_key = None
            view._stop_auto_scroll()

    def test_auto_scroll_moves_dragged_node_with_viewport(self):
        """视口持续滚动时被拖节点同向移动（保持停在鼠标边缘，不被甩在原地）。"""
        from PySide6.QtCore import QPoint
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        view = dock.view
        key = next(iter(dock.data.nodes))
        item = dock.node_items[key]
        x0 = item.pos().x()
        bar = view.horizontalScrollBar()
        bar.setValue(bar.minimum() + 200)
        dock._dragging_key = key
        vp = view.viewport()
        try:
            view._auto_scroll_on_drag(QPoint(vp.width() - 2, vp.height() // 2))
            for _ in range(4):
                view._auto_scroll_tick()
            self.assertGreater(item.pos().x(), x0,
                               "向右持续滚动时被拖节点应向右跟随")
        finally:
            dock._dragging_key = None
            view._stop_auto_scroll()

    def test_stop_auto_scroll_on_leave_and_release(self):
        """离开判定区 / 松手后停止持续滚动。"""
        from PySide6.QtCore import QPoint
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        view = dock.view
        vp = view.viewport()
        dock._dragging_key = next(iter(dock.data.nodes))
        try:
            view._auto_scroll_on_drag(QPoint(2, vp.height() // 2))
            self.assertTrue(view._auto_scroll_timer.isActive())
            # 离开判定区：停定时器
            view._auto_scroll_on_drag(QPoint(400, 300))
            self.assertFalse(view._auto_scroll_timer.isActive())
            self.assertEqual(view._edge_dir, (0, 0))
            # 再次进入，再离开（模拟松手路径）
            view._auto_scroll_on_drag(QPoint(2, vp.height() // 2))
            self.assertTrue(view._auto_scroll_timer.isActive())
            view._stop_auto_scroll()
            self.assertFalse(view._auto_scroll_timer.isActive())
        finally:
            dock._dragging_key = None
            view._stop_auto_scroll()

    def test_scene_rect_follows_current_layer_on_switch(self):
        """切换对象层后 sceneRect 按新层节点重算，不被旧层手摆位置撑住。"""
        from PySide6.QtCore import QPointF
        dock = self._setup()
        dock.resize(900, 600)
        dock.show()
        QApplication.processEvents()
        # 造第二个对象层（单节点，默认在原点附近）
        doc = dock.doc
        from rwmap.document.layer import ObjectLayer
        from rwmap.document.map_object import MapObject
        from rwmap.document.properties import PropertyBag
        extra = ObjectLayer(name="Effects", objects=[
            MapObject(object_id=99, name="fx", type_="unitAdd", x=0, y=0,
                      properties=PropertyBag(values={"id": "99"}))])
        doc.layers.append(extra)
        dock.refresh(fit=False)
        QApplication.processEvents()
        # 当前层（Triggers）某节点手摆到很远 → 内容框被撑大
        key = next(iter(dock.data.nodes))
        dock.remember_position(key, QPointF(9000, 9000))
        dock._ensure_scene_rect()
        self.assertGreater(dock._content_rect().right(), 8000)
        # 切到 Effects 层：内容框与 sceneRect 应回到新层节点附近
        idx = dock.layer_combo.findData("Effects")
        self.assertGreaterEqual(idx, 0)
        dock.layer_combo.setCurrentIndex(idx)
        dock._on_layer_changed()
        dock._ensure_scene_rect()
        QApplication.processEvents()
        self.assertEqual(dock._layer_filter, "Effects")
        self.assertLess(dock._content_rect().right(), 1000,
                        "切层后内容框不应被旧层手摆坐标撑住")
        self._assert_scene_rect_is_dynamic(dock, msg="切层后边界按新层重算")


class BoxSelectPerformanceTest(unittest.TestCase):
    """右键框选 N 个节点时，选中回调与属性面板重建必须只跑 1 次。

    旧实现逐个 setSelected(True)，每个都触发 scene.selectionChanged →
    _on_selection_changed → 全量重建属性面板 + 回主画布刷新选中手柄。
    框住 N 个就跑 N 次整条链，对象一多明显卡顿。修复后批量 blockSignals，
    循环结束统一回调一次，选中结果不变。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)

    def tearDown(self):
        self.w.trigger_graph_dock.hide()
        self.w.act_trigger_graph.setChecked(False)
        apply_default_layout(self.w)

    def _build_dock(self, count):
        dock = self.w.trigger_graph_dock
        dock.hide()
        dock.view.resetTransform()
        dock._exit_overview()
        dock.search_edit.clear()
        dock._group_page = ""
        dock._empty_groups.clear()
        objs = [
            MapObject(object_id=i, name=f"n{i}", type_="unitAdd", shape="point",
                      x=(i % 20) * 30, y=(i // 20) * 30,
                      properties=_bag(id=str(i)))
            for i in range(1, count + 1)
        ]
        doc = MapDocument(width=64, height=64,
                          layers=[ObjectLayer(name="Triggers", objects=objs)])
        dock.set_document(doc)
        dock.refresh(fit=False)
        QApplication.processEvents()
        return dock

    def _box_select_all(self, dock):
        """构造一个覆盖全部节点的右键选框并释放，返回框内节点数。"""
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QGraphicsRectItem

        items = list(dock.node_items.values())
        self.assertGreater(len(items), 1, "测试文档应含多个节点")
        rect = items[0].sceneBoundingRect()
        for it in items[1:]:
            rect = rect.united(it.sceneBoundingRect())
        rect.adjust(-5, -5, 5, 5)
        band = QGraphicsRectItem(rect)
        dock.scene.addItem(band)
        dock.view._band = band
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(0, 0),
                         Qt.RightButton, Qt.RightButton, Qt.NoModifier)
        dock.view.mouseReleaseEvent(ev)
        return len(items)

    def test_single_callback_for_few_and_many_nodes(self):
        for count in (4, 60):
            with self.subTest(count=count):
                dock = self._build_dock(count)
                with patch.object(dock, "_on_selection_changed",
                                  wraps=dock._on_selection_changed) as cb, \
                     patch.object(dock.inspector, "show_node",
                                  wraps=dock.inspector.show_node) as sh:
                    n = self._box_select_all(dock)
                self.assertEqual(cb.call_count, 1,
                                 f"框选 {n} 个节点只应触发 1 次选中回调，实际 {cb.call_count}")
                self.assertEqual(sh.call_count, 1,
                                 f"属性面板只应重建 1 次，实际 {sh.call_count}")

    def test_all_nodes_are_still_selected(self):
        """批量 blockSignals 后选中结果必须与逐个 setSelected 完全一致。"""
        dock = self._build_dock(40)
        n = self._box_select_all(dock)
        selected = [i for i in dock.scene.selectedItems()
                    if hasattr(i, "node")]
        self.assertEqual(len(selected), n,
                         f"框选应选中全部 {n} 个节点，实际 {len(selected)}")
        self.assertEqual(len(dock._selected_keys), n)


class GraphSelectionAndContextMenuTest(unittest.TestCase):
    """选中态属于数据层，视口虚拟化只影响渲染；右键可批量移动组 / 删除。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)

    def tearDown(self):
        self.w.trigger_graph_dock.hide()
        self.w.act_trigger_graph.setChecked(False)
        apply_default_layout(self.w)

    def _setup(self):
        from rwmap.widgets.trigger_graph_dock import EDITOR_GROUP_PROPERTY
        dock = self.w.trigger_graph_dock
        dock.hide()
        dock.view.resetTransform()
        dock._exit_overview()
        dock.search_edit.clear()
        dock._group_page = ""
        dock._empty_groups.clear()
        dock.set_document(make_doc())
        dock.refresh(fit=False)
        return dock

    def test_recycled_and_overview_nodes_keep_selection(self):
        """节点被视口裁剪 / 概览回收时只停渲染，数据层选中与属性都保留。"""
        dock = self._setup()
        keys = list(dock.data.nodes)
        objs = [n.obj for n in dock.data.nodes.values()]
        dock.select_objects(objs[:2])
        self.assertEqual(dock._selected_keys, set(keys[:2]))

        with patch.object(dock, "_clear_detail", wraps=dock._clear_detail) as clr:
            # 裁掉一个「已选中」节点：removeItem 不能把数据层选中清空
            dock._rebuild_live({keys[0]}, set(keys), None)
            self.assertEqual(dock._selected_keys, set(keys[:2]))
            self.assertNotIn(keys[1], dock.node_items)
            # 进入概览会回收全部 live 节点，选中态同样不能丢
            dock._enter_overview()
            self.assertEqual(dock.node_items, {})
            self.assertEqual(dock._selected_keys, set(keys[:2]))
        clr.assert_not_called()

        dock._exit_overview()
        dock._rebuild_live(set(keys), set(keys), None)
        self.assertEqual({k for k, it in dock.node_items.items()
                          if it.isSelected()}, set(keys[:2]))

    def test_context_menu_moves_all_selected_including_unrendered(self):
        """右键菜单把选中对象整批移组，未渲染的那个也必须写进文档。"""
        from rwmap.widgets.trigger_graph_dock import EDITOR_GROUP_PROPERTY
        dock = self._setup()
        keys = list(dock.data.nodes)
        dock.select_objects([n.obj for n in dock.data.nodes.values()])
        dock._rebuild_live({keys[0]}, set(keys), None)   # 3 个已选节点不渲染
        dock._empty_groups.add("组X")

        menu = dock.node_context_menu()
        labels = [a.text() for a in menu.actions()]
        self.assertTrue(any("加入已有组" in t for t in labels), labels)
        self.assertTrue(any("加入新建组" in t for t in labels), labels)
        self.assertTrue(any("删除（4" in t for t in labels), labels)
        sub = next(a.menu() for a in menu.actions() if a.text() == "加入已有组")
        act = next(a for a in sub.actions() if a.text() == "组X")
        act.trigger()

        layer = dock.doc.layers[0]
        self.assertEqual(len(layer.objects), 4)
        self.assertTrue(all(o.properties.get(EDITOR_GROUP_PROPERTY) == "组X"
                            for o in layer.objects))

    def test_context_menu_deletes_all_selected_including_unrendered(self):
        """右键批量删除不能漏掉「选中但没渲染」的对象，且整批只算一步。"""
        dock = self._setup()
        keys = list(dock.data.nodes)
        dock.select_objects([n.obj for n in dock.data.nodes.values()])
        dock._rebuild_live({keys[0]}, set(keys), None)

        menu = dock.node_context_menu()
        act_del = next(a for a in menu.actions()
                       if a.text().startswith("删除"))
        act_del.trigger()

        self.assertEqual(dock.doc.layers[0].objects, [])
        self.assertEqual(dock._selected_keys, set())
        self.assertEqual(len(dock.data.nodes), 0)

    def test_right_click_blank_cancels_selection_and_no_menu(self):
        """右键空白处（无选区）= 取消选择，且绝不弹节点菜单。"""
        from PySide6.QtCore import QEvent, QPointF, QRectF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QGraphicsRectItem

        dock = self._setup()
        keys = list(dock.data.nodes)
        # 先制造一个选中态（模拟框选后右键空白的旧场景）
        dock._selected_keys = {keys[0]}
        dock.node_items[keys[0]].setSelected(True)
        self.assertTrue(dock._selected_keys)

        # 构造一个零选区的右键 band（单击空白，不拖拽）
        band = QGraphicsRectItem(QRectF(10, 10, 0, 0))
        dock.scene.addItem(band)
        dock.view._band = band

        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(10, 10),
                         Qt.RightButton, Qt.RightButton, Qt.NoModifier)
        shown = []

        class _Menu:
            def exec(self, _pos): shown.append(_pos)

        with patch.object(dock, "node_context_menu", return_value=_Menu()):
            dock.view.mouseReleaseEvent(ev)

        self.assertEqual(shown, [], "右键空白处不应弹出节点菜单")
        self.assertEqual(dock._selected_keys, set(), "右键空白处应清空数据层选中态")
        self.assertEqual(dock.scene.selectedItems(), [], "右键空白处应清空场景选中态")

    def test_right_click_release_on_node_opens_menu(self):
        """view 关掉系统右键菜单后，必须在鼠标释放时显式弹出节点菜单。"""
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        dock = self._setup()
        keys = list(dock.data.nodes)
        item = dock.node_items[keys[0]]
        pos = dock.view.mapFromScene(item.sceneBoundingRect().center())
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, pos,
                         Qt.RightButton, Qt.RightButton, Qt.NoModifier)
        shown = []

        class _Menu:
            def exec(self, _pos): shown.append(_pos)

        with patch.object(dock, "node_context_menu", return_value=_Menu()):
            dock.view.mouseReleaseEvent(ev)
        self.assertEqual(len(shown), 1,
                         "节点上右键释放必须弹出「加入已有组 / 新建组 / 删除」菜单")
        self.assertEqual(dock._selected_keys, {keys[0]})

    def test_right_click_unselected_node_selects_it_first(self):
        """右键未选中的单个节点：只操作它，不误带上上次残留的选中批。"""
        from PySide6.QtCore import QPoint
        from rwmap.widgets.trigger_graph_dock import NodeItem

        dock = self._setup()
        keys = list(dock.data.nodes)
        dock._selected_keys = {keys[1]}
        item = dock.node_items[keys[0]]
        shown = []

        class _Event:
            def screenPos(self): return QPoint(0, 0)
            def accept(self): shown.append("accepted")

        class _Menu:
            def exec(self, _pos): shown.append("exec")

        with patch.object(dock, "node_context_menu", return_value=_Menu()):
            NodeItem.contextMenuEvent(item, _Event())
        self.assertEqual(dock._selected_keys, {keys[0]})
        self.assertIn("exec", shown)
        self.assertIn("accepted", shown)


class GraphGroupLayoutAndSelectionTest(unittest.TestCase):
    """分组后的自动布局只算当前组；节点图点选不连带同组对象。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)

    def tearDown(self):
        self.w.trigger_graph_dock.hide()
        self.w.act_trigger_graph.setChecked(False)
        apply_default_layout(self.w)

    def _setup(self, groups=None):
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        dock = self.w.trigger_graph_dock
        dock.hide()
        dock.view.resetTransform()
        dock._exit_overview()
        dock.search_edit.clear()
        dock._empty_groups.clear()
        doc = make_doc()
        if groups:
            for obj, group in zip(doc.layers[0].objects, groups):
                if group:
                    obj.properties.set(EDITOR_GROUP_PROPERTY, group)
        dock.set_document(doc)
        dock._group_page = ""
        dock.refresh(fit=False)
        return dock

    def test_auto_layout_only_current_group_starts_at_origin(self):
        dock = self._setup(["A", "A", "B", "B"])
        keys = list(dock.data.nodes)
        groups = {k: dock.data.nodes[k].group for k in keys}
        a_keys = [k for k in keys if groups[k] == "A"]
        b_keys = [k for k in keys if groups[k] == "B"]
        before_b = {k: (dock.data.nodes[k].x, dock.data.nodes[k].y)
                    for k in b_keys}
        dock.set_group_page("A")
        with patch.object(dock, "fit_view"):
            dock.auto_layout()
        pos_a = [(dock.data.nodes[k].x, dock.data.nodes[k].y) for k in a_keys]
        self.assertEqual(min(x for x, _y in pos_a), 0.0)
        self.assertEqual(min(y for _x, y in pos_a), 0.0)
        for key in b_keys:
            node = dock.data.nodes[key]
            self.assertEqual((node.x, node.y), before_b[key],
                             "自动布局不应改动其它组的坐标")

    def test_graph_selection_does_not_pull_in_group_mates(self):
        dock = self._setup(["A", "A", "A", "A"])
        keys = list(dock.data.nodes)
        dock._rebuild_live(set(keys), set(keys), None)
        dock.scene.clearSelection()
        dock.node_items[keys[0]].setSelected(True)
        dock._on_selection_changed()
        self.assertEqual(dock._selected_keys, {keys[0]})


    def test_auto_layout_on_ungrouped_page_leaves_other_group_alone(self):
        """「选择已有组」= 未分组这一组，不是全部：别的组坐标不能动。"""
        dock = self._setup([None, None, "A", "A"])
        keys = list(dock.data.nodes)
        groups = {k: dock.data.nodes[k].group for k in keys}
        free = [k for k in keys if not groups[k]]
        grouped = [k for k in keys if groups[k]]
        before = {k: (dock.data.nodes[k].x, dock.data.nodes[k].y)
                  for k in grouped}
        self.assertEqual(dock._group_page, "")
        with patch.object(dock, "fit_view"):
            dock.auto_layout()
        pos = [(dock.data.nodes[k].x, dock.data.nodes[k].y) for k in free]
        self.assertEqual(min(x for x, _y in pos), 0.0)
        self.assertEqual(min(y for _x, y in pos), 0.0)
        for key in grouped:
            node = dock.data.nodes[key]
            self.assertEqual((node.x, node.y), before[key])

    def test_auto_layout_is_one_undoable_step(self):
        dock = self._setup(["A", "A", None, None])
        dock.undo_stack = self.w.state.undo_stack
        dock.set_group_page("A")
        keys_a = [k for k, n in dock.data.nodes.items() if n.group == "A"]
        before = {k: (dock.data.nodes[k].x, dock.data.nodes[k].y)
                  for k in keys_a}
        depth = dock.undo_stack.count()
        with patch.object(dock, "fit_view"):
            dock.auto_layout()
        self.assertEqual(dock.undo_stack.count(), depth + 1)
        with patch.object(dock, "fit_view"):
            dock.undo_stack.undo()
        for key in keys_a:
            node = dock.data.nodes[key]
            self.assertEqual((node.x, node.y), before[key], "撤销应恢复原布局")
        with patch.object(dock, "fit_view"):
            dock.undo_stack.redo()
        pos = [(dock.data.nodes[k].x, dock.data.nodes[k].y) for k in keys_a]
        self.assertEqual(min(x for x, _y in pos), 0.0)
        self.assertEqual(min(y for _x, y in pos), 0.0)

    def test_object_edit_does_not_refit_graph_view(self):
        """改对象后重建节点图，但别把用户调好的视角弹走。"""
        with patch.object(self.w.trigger_graph_dock, "refresh") as rf:
            self.w._refresh_objects()
        rf.assert_called_once_with(fit=False)


class GraphEdgeRenderCacheTest(unittest.TestCase):
    """连线几何/资源缓存的回归：加速后必须与逐边重算逐像素一致。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make(self, n=40):
        from types import SimpleNamespace
        from rwmap.widgets.trigger_graph_dock import (
            EDGE_SPECS, GraphData, GraphEdge, GraphEdgeLayerItem, GraphNode,
        )
        kinds = list(EDGE_SPECS)

        class _Obj:
            def __init__(self, t):
                self.type_ = t

        nodes, edges, lanes = {}, [], {}
        for i in range(n):
            k = f"n{i}"
            nodes[k] = GraphNode(k, _Obj(kinds[i % len(kinds)]), "L", str(i),
                                 x=(i % 8) * 260.0, y=(i // 8) * 130.0)
        for i in range(n - 1):
            e = GraphEdge(f"n{i}", f"n{i+1}", kinds[i % len(kinds)])
            edges.append(e)
            lanes[id(e)] = (i % 3, 3)
        data = GraphData(nodes=nodes, edges=edges)

        class _View:
            def __init__(self, z):
                self.z = z

            def zoom(self):
                return self.z

            def viewport(self):
                return None

        def dock(zoom):
            return SimpleNamespace(data=data, _edge_lanes=lanes,
                                   _live_edges=set(), _active_cache=set(nodes),
                                   _overview_item=None, view=_View(zoom))

        return data, lanes, dock, GraphEdgeLayerItem

    def _paint_cached(self, dock, layer_cls):
        from PySide6.QtGui import QImage, QPainter
        layer = layer_cls(dock)
        layer.set_content()
        img = QImage(900, 600, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        layer.paint(p, None)
        p.end()
        return img, layer

    def _paint_uncached(self, dock):
        from PySide6.QtGui import QImage, QPainter
        from rwmap.widgets.trigger_graph_dock import (
            _edge_color, _paint_edge_curve,
        )
        img = QImage(900, 600, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        for e in dock.data.edges:
            a, b = dock.data.nodes[e.src], dock.data.nodes[e.dst]
            lane, total = dock._edge_lanes[id(e)]
            _paint_edge_curve(p, a, b, lane, total, _edge_color(e.kind),
                              dock.view.zoom(), dock._overview_item is not None)
        p.end()
        return img

    def _assert_same_pixels(self, a, b):
        self.assertEqual(a.size(), b.size())
        self.assertEqual(bytes(a.constBits()), bytes(b.constBits()),
                         "缓存路径与逐边重算的渲染结果必须逐像素一致")

    def test_cached_matches_uncached_detail(self):
        """细节模式：复用曲线/箭头几何不得改变任何像素。"""
        _data, _lanes, dock, layer_cls = self._make()
        cached, _ = self._paint_cached(dock(1.0), layer_cls)
        self._assert_same_pixels(cached, self._paint_uncached(dock(1.0)))

    def test_cached_matches_uncached_overview(self):
        """概览模式：箭头按 1/zoom 放大的路径也不得改变任何像素。"""
        _data, _lanes, dock, layer_cls = self._make()
        d = dock(0.15)
        d._overview_item = object()      # 触发 overview 分支
        cached, _ = self._paint_cached(d, layer_cls)
        self._assert_same_pixels(cached, self._paint_uncached(d))

    def test_geometry_reused_then_rebuilt_on_move(self):
        """重绘复用曲线；节点移动后只重建与之相连的边。"""
        import rwmap.widgets.trigger_graph_dock as tg
        data, _lanes, dock, layer_cls = self._make()
        d = dock(1.0)
        layer = layer_cls(d)
        layer.set_content()
        from PySide6.QtGui import QImage, QPainter

        def draw():
            img = QImage(900, 600, QImage.Format_ARGB32)
            p = QPainter(img)
            layer.paint(p, None)
            p.end()

        real = tg._edge_path
        with patch.object(tg, "_edge_path", wraps=real) as spy:
            draw()
            self.assertEqual(spy.call_count, len(data.edges))
            spy.reset_mock()
            draw()
            self.assertEqual(spy.call_count, 0, "节点没动就不该重新构造曲线")
            data.nodes["n20"].x += 30.0
            draw()
            # 链式边：n19->n20、n20->n21 两条受影响
            self.assertEqual(spy.call_count, 2)

    def test_edge_resources_are_cosmetic_and_reused(self):
        """连线笔/箭头画刷按颜色缓存，且仍是 cosmetic 笔宽。"""
        from PySide6.QtGui import QColor
        from rwmap.widgets.trigger_graph_dock import _edge_resources
        a = _edge_resources(QColor("#57c878"))
        b = _edge_resources(QColor("#57c878"))
        self.assertIs(a[1], b[1])
        self.assertIs(a[2], b[2])
        self.assertTrue(a[1].isCosmetic())

    def test_long_curve_clipped_to_viewport_is_pixel_identical(self):
        """超长曲线只画视口内一段：视口内每个像素必须与画整条完全一致。

        真实图的连线跨度中位数 3.2 万场景单位，视口仅 ~2800；裁剪时若端点
        露进视口，FlatCap 端帽就会出现可见接缝。这里用 6 万单位的曲线穿过
        小视口，逐像素比对裁剪路径和整条路径。
        """
        from types import SimpleNamespace
        from PySide6.QtCore import QPointF, QRect
        from PySide6.QtGui import QImage, QPainter, QColor
        from rwmap.widgets.trigger_graph_dock import (
            EDGE_SPECS, GraphData, GraphEdge, GraphEdgeLayerItem, GraphNode,
            _edge_color, _paint_edge_curve,
        )

        class _Obj:
            type_ = "unitAdd"

        nodes = {
            "a": GraphNode("a", _Obj(), "L", "1", x=0.0, y=200.0),
            "b": GraphNode("b", _Obj(), "L", "2", x=60000.0, y=200.0),
        }
        edge = GraphEdge("a", "b", "alsoActivate")
        data = GraphData(nodes=nodes, edges=[edge])

        class _View:
            def zoom(self):
                return 1.0

            def viewport(self):
                return QRect(0, 0, 800, 400)

            def mapToScene(self, x, y):
                return QPointF(x + 20000.0, y + 100.0)

        dock = SimpleNamespace(data=data, _edge_lanes={id(edge): (0, 1)},
                               _live_edges=set(), _active_cache=set(nodes),
                               _overview_item=None, view=_View())

        img_cached = QImage(800, 400, QImage.Format_ARGB32)
        img_cached.fill(0)
        p = QPainter(img_cached)
        p.setRenderHint(QPainter.Antialiasing, True)
        # 把绘制坐标从「场景」搬到测试画布：视口场景左上角 (20000, 100) -> (0, 0)
        p.translate(-20000.0, -100.0)
        layer = GraphEdgeLayerItem(dock)
        layer.set_content()
        layer.paint(p, None)
        p.end()

        img_full = QImage(800, 400, QImage.Format_ARGB32)
        img_full.fill(0)
        q = QPainter(img_full)
        q.setRenderHint(QPainter.Antialiasing, True)
        q.translate(-20000.0, -100.0)
        _paint_edge_curve(q, nodes["a"], nodes["b"], 0, 1,
                          _edge_color("alsoActivate"), 1.0, False)
        q.end()

        self.assertTrue(any(v is not None for v in layer._clip_cache.values()),
                        "长曲线应走裁剪路径，否则本测试没覆盖到目标分支")
        self._assert_same_pixels(img_cached, img_full)


if __name__ == "__main__":
    unittest.main()
