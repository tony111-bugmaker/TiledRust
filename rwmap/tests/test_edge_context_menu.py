"""回归：节点图右键交互。

1) 节点右键「触发 → 选目标」可直接建立激活连线（不用拖端口）；
2) 连线上右键弹菜单，含「删除这条连线」，点它只删这条、走撤销栈；
3) 连线几何命中 hit_edge_at 容差按屏幕像素、缩放下都好点。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PySide6.QtCore import QPoint, QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.app.panel_layout import apply_default_layout  # noqa: E402
from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import (  # noqa: E402
    PropertyBag, WAVE_LINK_PROPERTY, WAVE_NEXT_PROPERTY,
)
from rwmap.widgets.trigger_graph_dock import (  # noqa: E402
    _edge_controls, _cubic_at, _point_cubic_distance,
)


def _bag(**kwargs) -> PropertyBag:
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


def make_doc() -> MapDocument:
    objs = [
        MapObject(object_id=1, name="开始", type_="basic", shape="point",
                  x=0, y=0, properties=_bag(id="start")),
        MapObject(object_id=2, name="事件A", type_="unitDetect", shape="point",
                  x=300, y=0, properties=_bag(id="eventA")),
        MapObject(object_id=3, name="事件B", type_="basic", shape="point",
                  x=600, y=0, properties=_bag(id="eventB")),
    ]
    return MapDocument(width=32, height=20,
                       layers=[ObjectLayer(name="Triggers", objects=objs)])


class PointCubicDistanceTest(unittest.TestCase):
    def test_point_on_curve_is_zero(self):
        c = (QPointF(0, 0), QPointF(10, 0), QPointF(20, 10), QPointF(30, 10))
        p = _cubic_at(c, 0.5)
        self.assertLess(_point_cubic_distance(p, c, samples=200), 0.5)

    def test_far_point_is_far(self):
        c = (QPointF(0, 0), QPointF(10, 0), QPointF(20, 10), QPointF(30, 10))
        self.assertGreater(_point_cubic_distance(QPointF(0, 500), c), 100)


class RightClickTest(unittest.TestCase):
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
        dock.hide()
        dock.view.resetTransform()
        dock._exit_overview()
        dock.search_edit.clear()
        dock._group_page = ""
        dock._empty_groups.clear()
        dock.set_document(make_doc())
        dock.refresh(fit=False)
        return dock

    def test_node_menu_has_trigger_submenu_that_creates_link(self):
        dock = self._setup()
        keys = list(dock.data.nodes)
        start = dock.data.nodes[keys[0]]
        dock.select_objects([start.obj])

        menu = dock.node_context_menu()
        acts = menu.actions()
        trig = next((a for a in acts if a.text().startswith("触发 →")), None)
        self.assertIsNotNone(trig, [a.text() for a in acts])
        sub = trig.menu()
        self.assertIsNotNone(sub)
        labels = [a.text() for a in sub.actions()]
        self.assertIn("事件A", labels)
        self.assertIn("事件B", labels)
        self.assertNotIn("开始", labels)  # 不会连自己

        # 点「事件A」→ 源对象的 alsoActivate 写入目标的自定义 id
        act_a = next(a for a in sub.actions() if a.text() == "事件A")
        act_a.trigger()
        self.assertEqual(start.obj.properties.get("alsoActivate"), "eventA")

    def test_trigger_submenu_absent_when_no_other_nodes(self):
        dock = self._setup()
        # 删到只剩一个节点
        layer = dock.doc.layers[0]
        while len(layer.objects) > 1:
            layer.objects.pop()
        dock.refresh(fit=False)
        keys = list(dock.data.nodes)
        self.assertEqual(len(keys), 1)
        dock.select_objects([dock.data.nodes[keys[0]].obj])
        menu = dock.node_context_menu()
        self.assertIsNone(next(
            (a for a in menu.actions() if a.text().startswith("触发 →")), None))

    def test_edge_menu_has_delete_and_it_removes_one_reference(self):
        dock = self._setup()
        start = dock.doc.layers[0].objects[0]
        start.properties.set("alsoActivate", "eventA, eventB", "string")
        dock.refresh(fit=False)
        e = next(e for e in dock.data.edges if e.kind == "alsoActivate")
        menu = dock.edge_context_menu(e)
        labels = [a.text() for a in menu.actions()]
        self.assertIn("删除这条连线", labels)

        act_del = next(a for a in menu.actions() if a.text() == "删除这条连线")
        act_del.trigger()
        # 只删这一条（目标是事件A），另一条仍在
        self.assertNotIn("eventA", start.properties.get("alsoActivate") or "")
        self.assertIn("eventB", start.properties.get("alsoActivate") or "")

    def test_hit_edge_at_finds_edge_on_curve(self):
        dock = self._setup()
        start = dock.doc.layers[0].objects[0]
        start.properties.set("alsoActivate", "eventA", "string")
        dock.refresh(fit=False)
        # 造一条 live EdgeItem（若虚拟化没建，手动建）
        if not dock.edge_items:
            e = next(e for e in dock.data.edges if e.kind == "alsoActivate")
            from rwmap.widgets.trigger_graph_dock import EdgeItem
            item = EdgeItem(dock.node_items[e.src], dock.node_items[e.dst],
                            e.kind, 0, 1)
            dock.scene.addItem(item)
            dock.edge_items.append(item)
        item = dock.edge_items[0]
        c = _edge_controls(item.src.port_out(), item.dst.port_in(),
                           item.lane, item.total)
        mid_scene = _cubic_at(c, 0.5)
        # 把场景点投影到屏幕（mapFromScene 的逆），这里 viewport 变换是单位矩阵外
        # refresh(fit=False) 后通常是缩放的，用 mapFromScene 再反推屏幕点。
        screen = dock.view.mapFromScene(mid_scene)
        hit = dock.view.hit_edge_at(QPoint(int(screen.x()), int(screen.y())))
        self.assertIsNotNone(hit, "曲线中点应能命中连线")
        self.assertEqual(hit.kind, "alsoActivate")


class WaveChainRightClickTest(unittest.TestCase):
    """波次链：右键「在其后插入一波」+ 波次节点关联普通触发器。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.w = MainWindow(auto_open=False)

    def tearDown(self):
        self.w.trigger_graph_dock.hide()
        self.w.act_trigger_graph.setChecked(False)
        apply_default_layout(self.w)

    def _setup(self):
        from rwmap.document.properties import (
            WAVE_GEN_TYPE, WAVE_MARK_PROPERTY, WAVE_NEXT_PROPERTY,
            WAVE_TIME_PROPERTY, WAVE_UNITS_PROPERTY, WAVE_NAME_PROPERTY,
        )
        w1 = MapObject(object_id=1, name="第一波", type_=WAVE_GEN_TYPE,
                       shape="point", x=0, y=0,
                       properties=_bag(**{WAVE_MARK_PROPERTY: "1",
                                          WAVE_NAME_PROPERTY: "第一波",
                                          WAVE_TIME_PROPERTY: "0:10",
                                          WAVE_UNITS_PROPERTY: "6 bugMelee"}))
        broadcast = MapObject(object_id=2, name="播报第1波", type_="basic",
                              shape="point", x=400, y=0, properties=_bag(id="bc1"))
        doc = MapDocument(width=32, height=20,
                          layers=[ObjectLayer(name="Triggers", objects=[w1, broadcast])])
        info = MapObject(object_id=99, name="map_info", type_="", shape="rect",
                         properties=_bag(type="survival"))
        doc.layers[0].objects.append(info)
        dock = self.w.trigger_graph_dock
        dock.hide()
        dock.view.resetTransform()
        dock._exit_overview()
        dock.search_edit.clear()
        dock._group_page = ""
        dock._empty_groups.clear()
        dock.set_document(doc)
        dock.refresh(fit=False)
        return dock

    def test_menu_has_insert_action_only_on_wave_node(self):
        dock = self._setup()
        w1 = next(n for n in dock.data.nodes.values() if n.obj.name == "第一波")
        dock.select_objects([w1.obj])
        acts = [a.text() for a in dock.node_context_menu().actions()]
        self.assertIn("在其后插入一波", acts)

        bc = next(n for n in dock.data.nodes.values() if n.obj.name == "播报第1波")
        dock.select_objects([bc.obj])
        acts = [a.text() for a in dock.node_context_menu().actions()]
        self.assertNotIn("在其后插入一波", acts)

    def test_insert_wave_after_rewires_chain(self):
        dock = self._setup()
        w1 = next(n for n in dock.data.nodes.values() if n.obj.name == "第一波")
        dock.select_objects([w1.obj])
        act = next(a for a in dock.node_context_menu().actions()
                   if a.text() == "在其后插入一波")
        act.trigger()

        # 原第一波 → 新第二波（名字引用），链完整
        self.assertEqual(w1.obj.properties.get(WAVE_NEXT_PROPERTY), "第二波")
        waves = [o for o in dock.doc.layers[0].objects if o.name in ("第一波", "第二波")]
        self.assertEqual(len(waves), 2)
        self.assertFalse(any(o.name == "第三波" for o in dock.doc.layers[0].objects))
        # 源节点没分组时，新波次也不该凭空进组。
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        new_obj = next(o for o in waves if o.name == "第二波")
        self.assertIsNone(new_obj.properties.get(EDITOR_GROUP_PROPERTY))

        # 链完整、第二波已接入；新波次刷兵列表为空，按设计不编译进
        # survivalWaves（避免游戏解析空行），所以文本只含填了刷兵的第一波。
        from rwmap.rw.wave_gen import compile_survival_waves, validate_wave_chain
        self.assertEqual(validate_wave_chain(dock.doc), [])
        text = compile_survival_waves(dock.doc)
        self.assertIn("第一波", text)
        self.assertNotIn("第二波", text)

    def test_insert_wave_joins_clicked_nodes_group(self):
        """在分组里的波次上右键插波：新对象应留在同一组，不掉回默认组。"""
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        dock = self._setup()
        w1 = next(n for n in dock.data.nodes.values() if n.obj.name == "第一波")
        w1.obj.properties.set(EDITOR_GROUP_PROPERTY, "B组")
        dock.select_objects([w1.obj])
        act = next(a for a in dock.node_context_menu().actions()
                   if a.text() == "在其后插入一波")
        act.trigger()
        new_obj = next(o for o in dock.doc.layers[0].objects
                       if o.name == "第二波")
        self.assertEqual(new_obj.properties.get(EDITOR_GROUP_PROPERTY), "B组")

    def test_insert_second_time_gives_third_wave(self):
        dock = self._setup()
        for expected in ("第二波", "第三波"):
            # 每次重新取节点：push_object_edit 会克隆整层对象，旧引用会失效。
            w1 = next(n for n in dock.data.nodes.values()
                      if n.obj.name == "第一波")
            dock.select_objects([w1.obj])
            act = next(a for a in dock.node_context_menu().actions()
                       if a.text() == "在其后插入一波")
            act.trigger()
            # 新波次追加在链尾：第一波.next → 第二波 → 第三波
            first = next(o for o in dock.doc.layers[0].objects
                         if o.name == "第一波")
            current = first
            chain_names = []
            seen = set()
            while id(current) not in seen:
                seen.add(id(current))
                chain_names.append(current.name)
                nxt = current.properties.get(WAVE_NEXT_PROPERTY) or ""
                if not nxt:
                    break
                current = next((o for o in dock.doc.layers[0].objects
                                if o.name == nxt), None)
                if current is None:
                    break
            self.assertEqual(chain_names[-1], expected)
        names = [o.name for o in dock.doc.layers[0].objects]
        self.assertIn("第三波", names)

    def test_wave_can_link_to_normal_trigger(self):
        dock = self._setup()
        w1 = next(n for n in dock.data.nodes.values() if n.obj.name == "第一波")
        bc = next(n for n in dock.data.nodes.values() if n.obj.name == "播报第1波")
        dock.create_link(w1, bc)
        # 波次关联写目标节点的唯一 key，而不是 name —— attack_point 之类
        # 重名对象靠 name 会互相覆盖，只能连一个且连错目标。
        self.assertEqual(w1.obj.properties.get(WAVE_LINK_PROPERTY) or "", bc.key)
        # 不写进游戏的 alsoActivate（避免开局误触发）
        self.assertNotIn("alsoActivate", w1.obj.properties.values)

    def test_wave_can_link_multiple_same_named_triggers(self):
        """重名的多个 attack_point 必须能各自关联，不被误判成重复。

        真实生存图里几十个 attack_point 全叫同一个名字。旧实现用 name 作
        连线引用 → 第二个被判「已存在」，且重开后全部解析到第一个点。
        """
        from rwmap.widgets.trigger_graph_dock import collect_graph
        dock = self._setup()
        # 追加两个同名的 attack_point，模拟真实刷兵点布局
        for i in (10, 11):
            dock.doc.layers[0].objects.append(MapObject(
                object_id=i, name="attack_point", type_="attackPoint",
                shape="point", x=i * 100, y=0, properties=_bag()))
        dock.refresh(fit=False)

        for idx in range(2):
            # push_object_edit 会克隆对象，每轮都从最新数据取节点
            data = collect_graph(dock.doc)
            dock.data = data
            wk = next(n for n in data.nodes.values() if n.obj.name == "第一波")
            aps = sorted((n for n in data.nodes.values()
                          if n.obj.name == "attack_point"),
                         key=lambda n: n.obj.object_id)
            dock.create_link(wk, aps[idx])
            dock.refresh(fit=False)

        data = collect_graph(dock.doc)
        wk = next(n for n in data.nodes.values() if n.obj.name == "第一波")
        raw = wk.obj.properties.get(WAVE_LINK_PROPERTY) or ""
        edges = [e for e in data.edges
                 if e.src == wk.key and e.kind == WAVE_LINK_PROPERTY]
        self.assertEqual(len(edges), 2, "两个刷兵点应各自成一条连线")
        self.assertEqual(len({e.dst for e in edges}), 2,
                         "两条连线必须指向不同的节点")
        self.assertEqual(data.dangling, 0, "不应产生悬空连线")


if __name__ == "__main__":
    unittest.main()
