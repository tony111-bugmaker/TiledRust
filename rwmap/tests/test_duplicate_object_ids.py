"""回归：铁锈地图里 Tiled ``object_id`` 大量重复时的节点图 / 编辑正确性。

真实样本 `重拦单挑.tmx`：161 个对象里有 160 个 ``id="1"``（游戏靠自定义
property ``id`` 区分）。旧代码用 ``key_of[(层名, object_id)]`` 存节点 key，
把 150 个对象压成 2 个节点；删除 / 选中 / 属性编辑 / 补触发器 id 也全部按
object_id 匹配，会误删一片、改错对象、保存时写出几十个重复的 ``id="1"``。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import (  # noqa: E402
    MapDocument, allocate_trigger_id, ensure_trigger_ids, used_trigger_ids,
)
from rwmap.document.map_object import MapObject, object_uid  # noqa: E402
from rwmap.document.properties import PropertyBag  # noqa: E402
from rwmap.io.tmx_reader import read_document  # noqa: E402
from rwmap.io.tmx_writer import write_document  # noqa: E402
from rwmap.widgets.trigger_graph_dock import (  # noqa: E402
    GraphEdge, TriggerGraphDock, collect_graph,
)


def _bag(**kwargs) -> PropertyBag:
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


def _obj(i, *, oid=1, custom_id=None, activated_by=None, type_="unitDetect"):
    props = {}
    if custom_id:
        props["id"] = custom_id
    if activated_by:
        props["activatedBy"] = activated_by
    return MapObject(
        object_id=oid, name=f"节点{i}", type_=type_, shape="point",
        x=float(i * 10), y=0.0, properties=_bag(**props),
        xml_element=object(),          # 模拟 lxml 元素的稳定身份
    )


def make_dup_doc(n_trig=150, n_units=11) -> MapDocument:
    """复刻真实地图：Triggers 150（1 个 id=23 + 149 个 id=1）、UnitObjects 11。

    前 52 个触发器有互不重复的中文自定义 id，前 91 个用 activatedBy 按这些
    名字互相引用，用来验证「节点不塌缩 + 连线不会串到错对象」。
    """
    trig = [_obj(0, oid=23, type_="")]
    for i in range(1, n_trig):
        cid = f"触发器{i}" if i <= 52 else None
        ref = f"触发器{(i + 1) % 52 or 52}" if i <= 91 else None
        trig.append(_obj(i, custom_id=cid, activated_by=ref))
    units = [_obj(1000 + i, type_="units") for i in range(n_units)]
    return MapDocument(
        width=100, height=140,
        layers=[ObjectLayer(name="Triggers", objects=trig),
                ObjectLayer(name="UnitObjects", objects=units)])


def small_dup_doc() -> MapDocument:
    """两个 object_id=1、都无自定义 id 的触发器（连线 / 改线型用）。"""
    a = _obj(0)
    b = _obj(1)
    a.properties.set("alsoActivate", "1")
    return MapDocument(width=32, height=20,
                       layers=[ObjectLayer(name="Triggers", objects=[a, b])])


class DuplicateIdGraphDataTest(unittest.TestCase):
    def test_collect_graph_keeps_every_object(self):
        doc = make_dup_doc()
        t = collect_graph(doc, "Triggers")
        u = collect_graph(doc, "UnitObjects")
        a = collect_graph(doc, None)
        self.assertEqual((len(t.nodes), len(u.nodes), len(a.nodes)),
                         (150, 11, 161))
        self.assertEqual(doc.stats()["objects"], 161)
        self.assertIn("Triggers::23", t.nodes)
        self.assertIn("Triggers::1", t.nodes)
        self.assertIn("Triggers::1#148", t.nodes)
        self.assertIn("UnitObjects::1#10", u.nodes)
        # 每个节点必须指向不同的真实对象，不能只是键不同、obj 互相覆盖
        self.assertEqual(len({id(n.obj.xml_element) for n in t.nodes.values()}),
                         150)

    def test_edges_resolve_by_unique_custom_ids(self):
        data = collect_graph(make_dup_doc(), "Triggers")
        self.assertEqual(data.dangling, 0)
        self.assertEqual(len(data.edges), 91)
        keys = set(data.nodes)
        self.assertTrue(all(e.src in keys and e.dst in keys
                            for e in data.edges))

    def test_object_uid_survives_snapshot_clone(self):
        from rwmap.commands.object_command import snapshot, restore
        doc = make_dup_doc(5, 2)
        layer = doc.layers[0]
        before = [object_uid(o) for o in layer.objects]
        snap = snapshot(layer)
        restore(layer, snap)
        self.assertEqual([object_uid(o) for o in layer.objects], before)

    def test_allocate_trigger_id_is_globally_unique(self):
        doc = make_dup_doc()
        used = used_trigger_ids(doc)
        self.assertIn("触发器1", used)
        got = [allocate_trigger_id(o, used)
               for o in doc.layers[0].objects[:6]]
        self.assertEqual(len(got), len(set(got)))
        self.assertFalse(any(g in {"触发器1", "触发器2"} for g in got))
        # 生成的 id 不能被引用解析器拆开
        self.assertFalse(any(c in g for g in got
                             for c in ",;() \t"))

    def test_ensure_trigger_ids_never_writes_duplicate_ids(self):
        doc = make_dup_doc()
        doc.layers[0].objects[1].properties.set("id", "管理员")
        added = ensure_trigger_ids(doc)
        self.assertGreater(added, 0)
        ids = [o.properties.get("id") for o in doc.layers[0].objects
               if o.properties.get("id")]
        self.assertEqual(len(ids), len(set(ids)), "保存时会写出重复的触发器 id")
        self.assertEqual(doc.layers[0].objects[1].properties.get("id"), "管理员")
        self.assertEqual(ensure_trigger_ids(doc), 0, "补齐操作必须幂等")


class DuplicateIdDockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dock(self, doc):
        dock = TriggerGraphDock()
        dock.hide()
        dock.view.resetTransform()
        dock._exit_overview()
        dock.search_edit.clear()
        dock._group_page = ""
        stack = QUndoStack()
        dock.set_document(doc, stack, None)
        dock.refresh(fit=False)
        return dock, stack

    def test_keys_stable_across_refresh_and_layer_switch(self):
        doc = make_dup_doc()
        dock, _ = self._dock(doc)
        keys = set(dock.data.nodes)
        self.assertEqual(len(keys), 150)
        dock.refresh(fit=False)
        self.assertEqual(set(dock.data.nodes), keys)
        self.assertTrue(all(k in dock._pos_cache for k in keys))
        combo = dock.layer_combo
        combo.setCurrentIndex(combo.findData("UnitObjects"))
        self.assertEqual(len(dock.data.nodes), 11)
        combo.setCurrentIndex(combo.findData("Triggers"))
        self.assertEqual(set(dock.data.nodes), keys, "切层后节点 key 不应漂移")

    def test_delete_one_node_deletes_exactly_one_object(self):
        doc = make_dup_doc()
        dock, stack = self._dock(doc)
        trig = doc.layers[0]
        victim = trig.objects[5]
        uid = object_uid(victim)
        dock._delete_object(victim)
        self.assertEqual(len(trig.objects), 149)
        self.assertNotIn(uid, [object_uid(o) for o in trig.objects])
        self.assertEqual(len(dock.data.nodes), 149)
        stack.undo()
        self.assertEqual(len(trig.objects), 150)
        self.assertIn(uid, [object_uid(o) for o in trig.objects])

    def test_select_and_inspector_bind_the_right_object(self):
        doc = make_dup_doc()
        dock, _ = self._dock(doc)
        trig = doc.layers[0]
        victim = trig.objects[7]
        dock.select_objects([victim])
        node = dock._node_of_obj(victim)
        self.assertIs(node.obj, victim)
        self.assertEqual(dock._selected_keys, {node.key})
        self.assertIs(dock.inspector.obj, victim)
        dock.inspector._commit(
            lambda o: o.properties.set("team", "7"), "编辑属性")
        self.assertEqual(victim.properties.get("team"), "7")
        self.assertEqual(sum(1 for o in trig.objects
                             if o.properties.get("team") == "7"), 1,
                         "属性编辑串到了别的同 object_id 对象")

    def test_status_reports_full_node_count_even_in_overview(self):
        doc = make_dup_doc()
        dock, _ = self._dock(doc)
        self.assertIn("全量节点 150", dock.status.text())
        self.assertIn("连线 91", dock.status.text())
        dock._enter_overview()
        dock._update_status()
        self.assertEqual(len(dock.node_items), 0)
        self.assertIn("全量节点 150", dock.status.text(),
                      "概览模式不能用 live 节点数当总数")
        dock._exit_overview()
        dock.layer_combo.setCurrentIndex(
            dock.layer_combo.findData("UnitObjects"))
        self.assertIn("全量节点 11", dock.status.text())

    def test_create_link_assigns_unique_ids_and_is_undoable(self):
        doc = small_dup_doc()
        dock, stack = self._dock(doc)
        nodes = list(dock.data.nodes.values())
        src, dst = nodes[0], nodes[1]
        uid_a, uid_b = object_uid(src.obj), object_uid(dst.obj)
        dock.create_link(src, dst)
        # ObjectCommand 用快照替换 layer.objects，必须从文档里取当前实例
        cur_a = next(o for o in doc.layers[0].objects
                     if object_uid(o) == uid_a)
        cur_b = next(o for o in doc.layers[0].objects
                     if object_uid(o) == uid_b)
        sid = cur_a.properties.get("id")
        did = cur_b.properties.get("id")
        self.assertTrue(sid and did)
        self.assertNotEqual(sid, did, "重复 object_id 不能补出相同 id")
        self.assertIn(did, (cur_a.properties.get("alsoActivate") or ""))
        self.assertEqual(stack.count(), 1)
        stack.undo()
        undo_a = next(o for o in doc.layers[0].objects
                      if object_uid(o) == uid_a)
        self.assertEqual(undo_a.properties.get("alsoActivate"), "1")
        self.assertIsNone(undo_a.properties.get("id"))

    def test_change_edge_type_assigns_unique_ids(self):
        doc = small_dup_doc()
        dock, _ = self._dock(doc)
        nodes = list(dock.data.nodes.values())
        edge = GraphEdge(nodes[0].key, nodes[1].key, "alsoActivate")
        dock.change_edge_type(edge, "whenActivatedIds")
        ids = [o.properties.get("id") for o in doc.layers[0].objects]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(ids))
        self.assertNotEqual(ids[0], ids[1])


class DuplicateIdRoundTripTest(unittest.TestCase):
    _TMX = """<?xml version="1.0" encoding="UTF-8"?>
<map version="1.10" tiledversion="1.10.2" orientation="orthogonal" renderorder="right-down" width="8" height="8" tilewidth="20" tileheight="20" nextobjectid="2" infinite="0">
 <objectgroup name="Triggers" id="1" width="8" height="8">
  <object id="1" name="a" type="unitDetect" x="0" y="0"/>
  <object id="1" name="b" type="unitAdd" x="20" y="20"/>
  <object id="1" name="c" type="unitRemove" x="40" y="40"/>
 </objectgroup>
</map>
"""

    def test_saved_trigger_ids_are_unique_and_land_in_tmx(self):
        tmp = Path(tempfile.gettempdir()) / "rwmap_dup_id_roundtrip.tmx"
        tmp.write_text(self._TMX, encoding="utf-8")
        doc = read_document(tmp)
        self.assertEqual(len(doc.layers[0].objects), 3)
        write_document(doc, tmp)
        reloaded = read_document(tmp)
        ids = [o.properties.get("id") for o in reloaded.layers[0].objects]
        self.assertEqual(len(ids), len(set(ids)), "落盘后仍是重复的 id=1")
        self.assertTrue(all(ids))


if __name__ == "__main__":
    unittest.main()
