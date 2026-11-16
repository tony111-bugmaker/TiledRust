"""回归：_parse_ids 对 RW 生成语法 name(params) 的处理 +
allToActivate（布尔开关）不再被当成名字引用。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import PropertyBag  # noqa: E402
from rwmap.widgets.trigger_graph_dock import (  # noqa: E402
    EDGE_SPECS, _parse_ids, collect_graph,
)


def _bag(**kwargs) -> PropertyBag:
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


class ParseIdsTest(unittest.TestCase):
    def test_plain_list(self):
        self.assertEqual(_parse_ids("2, 3;4 5"), ["2", "3", "4", "5"])

    def test_strip_spawn_params(self):
        # RW deactivatedBy 里常混入 `name(spawnChance=..,maxSpawnLimit=1)` 的生成项
        raw = "A, Q, jcroom34mechFlame(spawnChance=0.04,maxSpawnLimit=1)"
        self.assertEqual(
            _parse_ids(raw),
            ["A", "Q", "jcroom34mechFlame"],
        )

    def test_unit_spawn_only_stays_as_name(self):
        # 纯单位生成项（无触发器同名）应被解析成名字，留待 resolve 判定悬空
        self.assertEqual(_parse_ids("mammothTank(spawnChance=0.07)"), ["mammothTank"])


class AllToActivateIsBooleanTest(unittest.TestCase):
    def test_allToActivate_not_an_edge_spec(self):
        self.assertNotIn("allToActivate", EDGE_SPECS)

    def test_allToActivate_true_does_not_create_dangling(self):
        # 布尔开关 true 不应被当成触发器引用，也不应产生悬空计数
        objs = [
            MapObject(object_id=1, name="触发A", type_="unitDetect", shape="rect",
                      x=0, y=0,
                      properties=_bag(id="1", allToActivate="true",
                                      alsoActivate="2")),
            MapObject(object_id=2, name="触发B", type_="unitAdd", shape="point",
                      x=100, y=0, properties=_bag(id="2")),
        ]
        doc = MapDocument(layers=[ObjectLayer(name="Triggers", objects=objs)])
        data = collect_graph(doc)
        # 只有 alsoActivate=2 一条正向边；allToActivate=true 不计入
        self.assertEqual(len(data.edges), 1)
        self.assertEqual(data.dangling, 0)
        self.assertEqual({e.kind for e in data.edges}, {"alsoActivate"})

    def test_deactivatedBy_with_spawn_syntax_resolves_trigger_names(self):
        # deactivatedBy 同时含真实触发器名(A,Q)与单位生成项；真实名应连线，
        # 单位生成项（非触发器）保持悬空——这才是诚实的悬空计数。
        objs = [
            MapObject(object_id=1, name="源", type_="unitDetect", shape="rect",
                      x=0, y=0, properties=_bag(id="1")),
            MapObject(object_id=2, name="A", type_="unitAdd", shape="point",
                      x=100, y=0, properties=_bag(id="2")),
            MapObject(object_id=3, name="Q", type_="unitAdd", shape="point",
                      x=200, y=0, properties=_bag(id="3")),
            MapObject(object_id=4, name="目标", type_="unitRemove", shape="point",
                      x=300, y=0,
                      properties=_bag(
                          id="4",
                          deactivatedBy="A, Q, mammothTank(spawnChance=0.07)")),
        ]
        doc = MapDocument(layers=[ObjectLayer(name="Triggers", objects=objs)])
        data = collect_graph(doc)
        # 目标 被 A、Q 停用 -> 2 条 reverse 边；mammothTank 不是触发器 -> 1 悬空
        self.assertEqual(len(data.edges), 2)
        self.assertEqual(data.dangling, 1)


if __name__ == "__main__":
    unittest.main()
