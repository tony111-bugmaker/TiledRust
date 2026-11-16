"""回归：两个相距极远、连线穿过视口的节点，全局连线层必须独立渲染该线。

之前只有「两端节点都在视口内」时才有逐边 EdgeItem；端点被虚拟化回收后，远连线
可能整个消失。现由常驻的 GraphEdgeLayerItem 按「视口矩形」裁剪补画——只要曲线
穿过视野，无论两端节点在不在视野里都渲染。本测试断言：虚拟化后该边不是 live
EdgeItem（说明交给全局层），且其裁剪盒与视口相交（说明会被画出来）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import PropertyBag  # noqa: E402
from rwmap.widgets.trigger_graph_dock import NODE_W, TriggerGraphDock  # noqa: E402


def _bag(**kwargs):
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


def make_doc():
    objs = [
        MapObject(object_id=1, name="A", type_="unitDetect", shape="rect",
                  x=0, y=0, width=100, height=100,
                  properties=_bag(id="1", alsoActivate="2")),
        MapObject(object_id=2, name="B", type_="unitRemove", shape="point",
                  x=0, y=0, properties=_bag(id="2")),
    ]
    return MapDocument(width=2000, height=200,
                       layers=[ObjectLayer(name="Triggers", objects=objs)])


class FarEdgeRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dock = TriggerGraphDock()
        self.dock.set_document(make_doc())
        self.dock.refresh(fit=False)
        akey = next(k for k, n in self.dock.data.nodes.items() if n.tid == "1")
        bkey = next(k for k, n in self.dock.data.nodes.items() if n.tid == "2")
        # 把 B 放到极远处，A 留在原点；清掉自动布局缓存强制用指定位置
        self.dock._pos_cache = {akey: QPointF(0, 0), bkey: QPointF(40000, 0)}
        self.dock.refresh(fit=False)
        self.akey, self.bkey = akey, bkey
        self.view = self.dock.view
        self.view.resize(900, 600)
        self.view.viewport().setGeometry(0, 0, 900, 600)
        self.view.resetTransform()
        # 视口停在连线的正中点：两端节点都在视野外
        self.view.centerOn(QPointF(20000, 46))
        # 真实窗口下走虚拟化分支；离屏下强制开，确保覆盖「两端都不 live」的路径
        self.dock.isVisible = lambda: True
        self.dock._sync_visible()

    def _edge(self):
        return next(e for e in self.dock.data.edges
                    if e.src == self.akey and e.dst == self.bkey)

    def test_far_edge_not_live_but_box_crosses_viewport(self):
        e = self._edge()
        # 两端都不在视口内 → 不应有逐边 EdgeItem
        self.assertNotIn(id(e), self.dock._live_edges,
                         "远连线不应是 live EdgeItem，应交给全局连线层")
        self.assertNotIn(self.akey, self.dock.node_items)
        self.assertNotIn(self.bkey, self.dock.node_items)
        # 全局连线层会画它：裁剪盒与视口相交
        vp = self.view.viewport()
        tl = self.view.mapToScene(0, 0)
        br = self.view.mapToScene(vp.width(), vp.height())
        visible = QRectF(tl, br)
        box = self.dock.edge_layer._boxes.get(id(e))
        self.assertIsNotNone(box, "全局层应为该边计算裁剪盒")
        self.assertTrue(box.intersects(visible),
                        "远连线的裁剪盒必须穿过视口，否则线会消失")


if __name__ == "__main__":
    unittest.main()
