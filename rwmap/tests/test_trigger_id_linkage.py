"""回归：铁锈战争触发器 id 规范化与 alsoActivate 链接。

根因：RW 的 alsoActivate 引用的是触发器对象的**自定义 id 属性**（作者赋的数字），
而不是 Tiled 的 object id。之前节点图把 Tiled object id 写进了 alsoActivate，且目标
触发器常常根本没有自定义 id，于是游戏加载时找不到对应触发器，连线在游戏内不生效
（表现为「矩形检测不到 / 触发不了点的生成」）。

这里验证：
1) ensure_trigger_ids 给缺 id 的触发器补上 id（默认取稳定且唯一的 object_id），
   不覆盖已有 id、不给非触发器对象加 id、并把改动层标记 dirty；
2) create_link 在目标缺 id 时自动补 id，并把目标的 id（而非 object id）写进 alsoActivate；
   若目标已有与 object_id 不同的自定义 id，则写那个 id；
3) 端到端：手工写了 alsoActivate 但没 id 的地图，保存后 id 真正落盘，游戏内可解析。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument, ensure_trigger_ids  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import PropertyBag  # noqa: E402
from rwmap.io.tmx_reader import read_document  # noqa: E402
from rwmap.io.tmx_writer import write_document  # noqa: E402


def _bag(**kwargs):
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


# ---------------------------------------------------------------------------
# 1) ensure_trigger_ids 单元行为
# ---------------------------------------------------------------------------
class EnsureTriggerIdsTest(unittest.TestCase):
    def _doc(self):
        triggers = [
            # 缺 id 的 unitDetect：应补 id=1
            MapObject(object_id=1, name="detect", type_="unitDetect", shape="rect",
                      x=0, y=0, width=10, height=10,
                      properties=_bag(alsoActivate="2")),
            # 已有自定义 id（且与 object_id 不同）：不动
            MapObject(object_id=2, name="spawn", type_="unitAdd", shape="point",
                      x=40, y=40, properties=_bag(id="99")),
        ]
        # 非触发器对象层（普通单位）：不应加 id
        units = [
            MapObject(object_id=3, name="tank", type_="units", shape="rect",
                      x=0, y=0, width=10, height=10),
        ]
        return MapDocument(width=32, height=20,
                           layers=[ObjectLayer(name="triggers", objects=triggers),
                                   ObjectLayer(name="units", objects=units)])

    def test_adds_missing_id_defaults_to_object_id(self):
        doc = self._doc()
        n = ensure_trigger_ids(doc)
        self.assertEqual(n, 1)  # 只有 unitDetect 缺 id
        trig = doc.layers[0].objects
        self.assertEqual(trig[0].properties.get("id"), "1")
        # 改动层被标记 dirty，保证写回时落盘
        self.assertTrue(doc.layers[0].dirty)

    def test_keeps_existing_custom_id(self):
        doc = self._doc()
        ensure_trigger_ids(doc)
        self.assertEqual(doc.layers[0].objects[1].properties.get("id"), "99")

    def test_skips_non_trigger_objects(self):
        doc = self._doc()
        ensure_trigger_ids(doc)
        # units 层不是 triggers，且 type_=units 不在触发器类型集合里 → 不加 id
        self.assertIsNone(doc.layers[1].objects[0].properties.get("id"))
        self.assertFalse(doc.layers[1].dirty)

    def test_idempotent(self):
        doc = self._doc()
        first = ensure_trigger_ids(doc)
        second = ensure_trigger_ids(doc)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # 第二次已都有 id


# ---------------------------------------------------------------------------
# 2) create_link 写目标 id（用真实节点图，离线）
# ---------------------------------------------------------------------------
class CreateLinkWritesTargetIdTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _doc(self, dst_id=None):
        props_b = _bag()
        if dst_id is not None:
            props_b.values["id"] = str(dst_id)
        objs = [
            MapObject(object_id=1, name="A", type_="unitDetect", shape="rect",
                      x=0, y=0, width=100, height=100, properties=_bag()),
            MapObject(object_id=2, name="B", type_="unitAdd", shape="point",
                      x=400, y=40, properties=props_b),
        ]
        return MapDocument(width=32, height=20,
                           layers=[ObjectLayer(name="Triggers", objects=objs)])

    def _setup_dock(self, doc):
        from rwmap.widgets.trigger_graph_dock import TriggerGraphDock
        dock = TriggerGraphDock()
        dock.set_document(doc)
        dock.refresh(fit=False)
        dock.view.resize(900, 600)
        dock.view.viewport().setGeometry(0, 0, 900, 600)
        dock.view.resetTransform()
        return dock

    def _node(self, dock, tid):
        return next(n for n in dock.data.nodes.values() if n.tid == tid)

    def test_missing_id_auto_assigned_and_written(self):
        """目标没 id：create_link 应补 id=object_id，并把该 id 写进 alsoActivate。"""
        doc = self._doc(dst_id=None)
        dock = self._setup_dock(doc)
        src = self._node(dock, "1")
        dst = self._node(dock, "2")  # tid 此时回退到 object_id=2
        dock.create_link(src, dst)
        # 目标被补了 id=2（= 它的 object_id）
        self.assertEqual(dst.obj.properties.get("id"), "2")
        self.assertEqual(dst.obj.properties.get("id"), str(dst.obj.object_id))
        # 源 alsoActivate 写的是目标的 id（=2），即 object_id
        self.assertEqual(
            doc.layers[0].objects[0].properties.get("alsoActivate"), "2")

    def test_existing_custom_id_is_used_not_object_id(self):
        """目标已有与 object_id 不同的自定义 id=99：写的是 99，不是 object_id=2。"""
        doc = self._doc(dst_id=99)
        dock = self._setup_dock(doc)
        src = self._node(dock, "1")
        dst = self._node(dock, "99")
        dock.create_link(src, dst)
        # 关键断言：写的是目标的自定义 id，而非 Tiled object id
        self.assertEqual(
            doc.layers[0].objects[0].properties.get("alsoActivate"), "99")
        self.assertEqual(dst.obj.properties.get("id"), "99")


# ---------------------------------------------------------------------------
# 3) 端到端：保存后 id 真正落盘（手工写了 alsoActivate 但没 id）
# ---------------------------------------------------------------------------
_MINIMAL_TMX = """<?xml version="1.0" encoding="UTF-8"?>
<map version="1.10" tiledversion="1.10.2" orientation="orthogonal" renderorder="right-down" width="32" height="20" tilewidth="20" tileheight="20" nextobjectid="3" infinite="0">
 <objectgroup name="triggers" id="1" width="32" height="20">
  <object id="1" name="detect" type="unitDetect" x="0" y="0" width="100" height="100">
   <properties>
    <property name="alsoActivate" value="2"/>
   </properties>
  </object>
  <object id="2" name="spawn" type="unitAdd" x="400" y="40">
   <properties>
    <property name="spawnUnits" value="heavyTank"/>
   </properties>
  </object>
 </objectgroup>
</map>
"""


class WriterRoundTripTest(unittest.TestCase):
    def test_missing_ids_written_on_save(self):
        tmp = Path(tempfile.gettempdir()) / "rwmap_trigger_id_roundtrip.tmx"
        tmp.write_text(_MINIMAL_TMX, encoding="utf-8")
        doc = read_document(tmp)
        # 保存前：两个触发器都没有自定义 id
        trig = next(l for l in doc.layers if l.kind == "object")
        self.assertIsNone(trig.objects[0].properties.get("id"))
        self.assertIsNone(trig.objects[1].properties.get("id"))

        write_document(doc, tmp)  # 内部 ensure_trigger_ids 补 id 并标记 dirty
        reloaded = read_document(tmp)

        trig2 = next(l for l in reloaded.layers if l.kind == "object")
        # 保存后：两个触发器都拿到了 id（= 各自 object_id），且 alsoActivate 仍指向 2
        self.assertEqual(trig2.objects[0].properties.get("id"), "1")
        self.assertEqual(trig2.objects[1].properties.get("id"), "2")
        self.assertEqual(trig2.objects[0].properties.get("alsoActivate"), "2")
        # 游戏侧语义：alsoActivate=2 能对应到 id=2 的 unitAdd 触发器 → 联动生效
        ids = {o.properties.get("id") for o in trig2.objects}
        self.assertIn(trig2.objects[0].properties.get("alsoActivate"), ids)


if __name__ == "__main__":
    unittest.main()
