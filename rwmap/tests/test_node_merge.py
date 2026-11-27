"""回归：节点图「整合体」——多个同配置对象折叠成一个节点同步编辑。

覆盖：右键菜单门槛 / 整合断线 / 撤销 / 右侧面板同步改名改名与属性 /
整合后连线同步写所有成员 / 选中整合体在地图上高亮全部成员 /
私有属性随 rwmap_layout 持久化。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PySide6.QtGui import QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import (  # noqa: E402
    MERGE_GROUP_PROPERTY, MERGE_PRIMARY_PROPERTY, PropertyBag,
    WAVE_GEN_TYPE, WAVE_LINK_PROPERTY, WAVE_MARK_PROPERTY,
)
from rwmap.io.tmx_writer import collect_editor_layout  # noqa: E402
from rwmap.widgets.trigger_graph_dock import (  # noqa: E402
    TriggerGraphDock, _parse_ids, collect_graph,
)


def _bag(**kwargs) -> PropertyBag:
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


def make_doc():
    """三个完全同配置的 attack_point + 一个被它们激活的 boss。"""
    aps = [MapObject(object_id=10 + i, name="attack_point", type_="attackPoint",
                     shape="point", x=100 + i * 100, y=0,
                     properties=_bag(id=f"ap{i}"))
           for i in range(3)]
    aps[0].properties.set("alsoActivate", "boss", "string")
    boss = MapObject(object_id=99, name="boss", type_="unitAdd", shape="point",
                     x=0, y=0, properties=_bag(id="boss"))
    doc = MapDocument(width=32, height=20,
                      layers=[ObjectLayer(name="Triggers", objects=aps + [boss])])
    return doc


class MergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = make_doc()
        self.stack = QUndoStack()
        self.dock = TriggerGraphDock()
        self.dock.set_document(self.doc, self.stack)
        self.dock.refresh(fit=False)

    def _aps(self):
        return [o for o in self.doc.layers[0].objects
                if o.name == "attack_point"]

    def _merged(self):
        g = collect_graph(self.doc)
        return [n for n in g.nodes.values() if n.merge_count > 1]

    def _select(self, names=("attack_point",)):
        g = collect_graph(self.doc)
        self.dock.data = g
        keys = [k for k, n in g.nodes.items() if n.obj.name in names]
        self.dock._selected_keys = set(keys)
        return g

    def test_menu_merge_only_for_same_config(self):
        doc = self.doc
        other = MapObject(object_id=50, name="其它", type_="mapText",
                          shape="text", x=0, y=0, properties=_bag(id="other"))
        doc.layers[0].objects.append(other)
        self._select(("attack_point", "其它"))
        labels = {a.text(): a for a in self.dock.node_context_menu().actions()}
        self.assertIn("整合（所选对象配置不同）", labels)
        self.assertFalse(labels["整合（所选对象配置不同）"].isEnabled())

        self._select(("attack_point",))
        labels = {a.text(): a for a in self.dock.node_context_menu().actions()}
        self.assertIn("整合为 1 个（3 个同配置对象）", labels)
        self.assertTrue(labels["整合为 1 个（3 个同配置对象）"].isEnabled())

    def test_merge_collapses_nodes_disconnects_and_undo(self):
        self._select()
        self.dock._merge_selected_nodes()
        g = collect_graph(self.doc)
        merged = [n for n in g.nodes.values() if n.merge_count > 1]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].merge_count, 3)
        self.assertEqual(len(merged[0].merge_members), 3)
        # 三个对象在节点图里只剩一个，且旧连线全部断开
        self.assertEqual(len([n for n in g.nodes.values()
                              if n.obj.name == "attack_point"]), 1)
        self.assertEqual(g.edges, [])
        self.assertEqual([o.properties.get("alsoActivate") for o in self._aps()],
                         [None, None, None])
        # 一个组 id，恰好一个代表
        gid = {o.properties.get(MERGE_GROUP_PROPERTY) for o in self._aps()}
        self.assertEqual(len(gid), 1)
        self.assertNotIn(None, gid)
        primaries = [o for o in self._aps()
                     if o.properties.get(MERGE_PRIMARY_PROPERTY) == "1"]
        self.assertEqual(len(primaries), 1)

        self.stack.undo()
        g2 = collect_graph(self.doc)
        self.assertEqual(g2.dangling, 0)
        self.assertEqual(len([n for n in g2.nodes.values()
                              if n.obj.name == "attack_point"]), 3)
        self.assertEqual(len(g2.edges), 1)
        self.assertTrue(any(o.properties.get(MERGE_GROUP_PROPERTY)
                            for o in self._aps()) is False)

    def test_dissolve_restores_all_nodes(self):
        self._select()
        self.dock._merge_selected_nodes()
        node = self._merged()[0]
        self._select()
        labels = {a.text(): a for a in self.dock.node_context_menu().actions()}
        self.assertIn("解散整合（恢复 3 个节点）", labels)
        self.dock._dissolve_merge(node)
        self.assertEqual(len([n for n in collect_graph(self.doc).nodes.values()
                              if n.obj.name == "attack_point"]), 3)

    def test_inspector_edits_sync_all_members(self):
        self._select()
        self.dock._merge_selected_nodes()
        node = self._merged()[0]
        self.dock._show_detail(node)
        # 改名字：三个真实对象都要跟着改（撤销命令会克隆替换对象，每次
        # 编辑后都从文档重新取，不能沿用旧引用）
        self.dock.inspector.f_name.setText("统一刷兵点")
        self.dock.inspector.f_name.editingFinished.emit()
        objs = [o for o in self.doc.layers[0].objects
                if o.name == "统一刷兵点"]
        self.assertEqual(len(objs), 3)
        # 改普通属性：同样同步，且不会把非代表变成第二个代表
        self.dock.inspector._commit_prop_value("team", "2")
        objs = [o for o in self.doc.layers[0].objects
                if o.name == "统一刷兵点"]
        self.assertEqual([o.properties.get("team") for o in objs], ["2"] * 3)
        primaries = [o for o in objs
                     if o.properties.get(MERGE_PRIMARY_PROPERTY) == "1"]
        self.assertEqual(len(primaries), 1)

    def test_links_after_merge_sync_to_all_members(self):
        self._select()
        self.dock._merge_selected_nodes()
        g = collect_graph(self.doc)
        node = next(n for n in g.nodes.values() if n.merge_count > 1)
        boss = next(n for n in g.nodes.values() if n.obj.name == "boss")
        self.dock.data = g
        self.dock.create_link(node, boss)
        self.assertEqual([o.properties.get("alsoActivate") for o in self._aps()],
                         ["boss"] * 3)
        # 点连线右侧的叉：三份引用一起删
        g2 = collect_graph(self.doc)
        edge = next(e for e in g2.edges if e.kind == "alsoActivate")
        self.dock.data = g2
        self.dock.delete_edge(edge)
        self.assertEqual([o.properties.get("alsoActivate") for o in self._aps()],
                         [None] * 3)

    def test_link_from_normal_node_to_merged_node_has_no_dangling_refs(self):
        self._select()
        self.dock._merge_selected_nodes()
        g = collect_graph(self.doc)
        boss = next(n for n in g.nodes.values() if n.obj.name == "boss")
        node = next(n for n in g.nodes.values() if n.merge_count > 1)
        self.dock.data = g
        self.dock.create_link(boss, node)
        g2 = collect_graph(self.doc)
        self.assertEqual(g2.dangling, 0)
        self.assertEqual(len([e for e in g2.edges
                              if e.kind == "alsoActivate" and e.src == boss.key]), 1)
        # 整合体每个成员都要被这个触发器激活
        self.assertTrue(all(o.properties.get("id") in
                            _parse_ids(boss.obj.properties.get("alsoActivate") or "")
                            for o in node.merge_members))

    def test_wave_link_to_merged_node_writes_all_refs_and_splits_on_dissolve(self):
        """「波次关联」连到整合体：底层给每个成员各一条线，解散后展开。"""
        wave = MapObject(object_id=200, name="第一波", type_=WAVE_GEN_TYPE,
                         shape="point", x=0, y=0,
                         properties=_bag(**{WAVE_MARK_PROPERTY: "1"}))
        self.doc.layers[0].objects.append(wave)
        self.dock.refresh(fit=False)

        self._select()
        self.dock._merge_selected_nodes()
        g = collect_graph(self.doc)
        self.dock.data = g
        wave_node = next(n for n in g.nodes.values() if n.obj.name == "第一波")
        merged = next(n for n in g.nodes.values() if n.merge_count > 1)
        self.dock.create_link(wave_node, merged)

        wave_obj = next(o for o in self.doc.layers[0].objects
                        if o.name == "第一波")
        refs = _parse_ids(wave_obj.properties.get(WAVE_LINK_PROPERTY) or "")
        self.assertEqual(len(refs), 3, "整合体每个成员都要各写一条引用")
        self.assertEqual(set(refs), set(merged.merge_member_keys))

        g2 = collect_graph(self.doc)
        self.assertEqual(g2.dangling, 0)
        folded = [e for e in g2.edges
                  if e.kind == WAVE_LINK_PROPERTY and e.src == wave_node.key]
        self.assertEqual(len(folded), 1, "整合期间仍折叠显示一条线")

        # 解散整合：3 条引用自然展开成 3 条独立连线
        node2 = next(n for n in g2.nodes.values() if n.merge_count > 1)
        self.dock._dissolve_merge(node2)
        g3 = collect_graph(self.doc)
        self.assertEqual(g3.dangling, 0)
        edges = [e for e in g3.edges
                 if e.kind == WAVE_LINK_PROPERTY and e.src == wave_node.key]
        self.assertEqual(len(edges), 3)
        self.assertEqual(len({e.dst for e in edges}), 3)

        # 单条删除只摘掉对应成员，剩下 2 条还在
        self.dock.data = g3
        self.dock.delete_edge(edges[0])
        left = _parse_ids(next(o for o in self.doc.layers[0].objects
                               if o.name == "第一波").properties.get(
                                   WAVE_LINK_PROPERTY) or "")
        self.assertEqual(len(left), 2)

    def test_select_merged_node_highlights_all_on_map(self):
        self._select()
        self.dock._merge_selected_nodes()
        received = []
        self.dock.objectsSelected.connect(received.append)
        g = collect_graph(self.doc)
        node = next(n for n in g.nodes.values() if n.merge_count > 1)
        item = self.dock.node_items.get(node.key)
        self.assertIsNotNone(item)
        self.dock._loading = False
        self.dock.scene.clearSelection()
        item.setSelected(True)
        self.assertEqual(len(received), 1)
        self.assertEqual(len(received[0]), 3)
        self.assertEqual({id(o) for o in received[0]},
                         {id(o) for o in node.merge_members})

    def test_deleting_merged_node_deletes_all_members(self):
        self._select()
        self.dock._merge_selected_nodes()
        self._select()
        self.dock._delete_selected_objects()
        self.assertEqual(self._aps(), [])
        # 地图上无关的 boss 不能受牵连
        self.assertEqual(len(self.doc.layers[0].objects), 1)
        self.stack.undo()
        self.assertEqual(len(self._aps()), 3)

    def test_merge_properties_are_persisted_as_private_layout(self):
        self._select()
        self.dock._merge_selected_nodes()
        layout = collect_editor_layout(self.doc)
        payloads = [v for k, v in layout.items() if k.startswith("Triggers:")]
        self.assertTrue(any(MERGE_GROUP_PROPERTY in p for p in payloads))
        self.assertTrue(any(MERGE_PRIMARY_PROPERTY in p for p in payloads))


if __name__ == "__main__":
    unittest.main()
