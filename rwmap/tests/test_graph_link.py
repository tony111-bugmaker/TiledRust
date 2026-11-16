"""回归：节点图端口拖拽连线。

之前「从黄点拖拽」没有任何响应——端口只是装饰、没有任何 handler 接收该操作。
这里验证：抓端口拖到另一个节点会建立一条 alsoActivate「激活」连线（写进源触发器
对象的 alsoActivate 属性），节点图里随即出现对应边；重复连线去重；自身不连。
同时覆盖新增的视图命中辅助 _hit_port / _target_node_at，以及完整鼠标事件序列。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math  # noqa: E402

import unittest  # noqa: E402

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.document.properties import PropertyBag  # noqa: E402
from rwmap.widgets.trigger_graph_dock import PORT_R, TriggerGraphDock  # noqa: E402


def _bag(**kwargs):
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


def make_two_doc():
    """A(id=1) 与 B(id=2)，彼此无任何连线属性。"""
    objs = [
        MapObject(object_id=1, name="A", type_="unitDetect", shape="rect",
                  x=0, y=0, width=100, height=100, properties=_bag(id="1")),
        MapObject(object_id=2, name="B", type_="unitRemove", shape="point",
                  x=400, y=40, properties=_bag(id="2")),
    ]
    return MapDocument(width=32, height=20,
                       layers=[ObjectLayer(name="Triggers", objects=objs)])


class LinkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dock = TriggerGraphDock()
        self.dock.set_document(make_two_doc())
        self.dock.refresh(fit=False)
        # 离屏下给视图一个真实尺寸，使 mapToScene/mapFromScene 能正确往返
        # （否则视图 0 尺寸时投影退化、_hit_port 的屏幕像素判定会失效）
        self.dock.view.resize(900, 600)
        self.dock.view.viewport().setGeometry(0, 0, 900, 600)
        self.dock.view.resetTransform()

    def _node(self, tid):
        return next(n for n in self.dock.data.nodes.values() if n.tid == tid)

    def test_create_link_writes_also_activate(self):
        src = self._node("1")
        dst = self._node("2")
        self.dock.create_link(src, dst)
        # 源对象属性里写入 alsoActivate=2
        self.assertEqual(
            self.dock.doc.layers[0].objects[0].properties.get("alsoActivate"), "2")
        self.dock.doc.layers[0].objects[1].properties.get  # B 不受影响
        # 节点图出现一条 alsoActivate 边
        pairs = {(e.src, e.dst, e.kind) for e in self.dock.data.edges}
        self.assertIn((src.key, dst.key, "alsoActivate"), pairs)
        self.assertTrue(self.dock.doc.dirty)

    def test_create_link_dedup(self):
        src = self._node("1")
        dst = self._node("2")
        self.dock.create_link(src, dst)
        self.dock.create_link(src, dst)  # 重复应去重
        ids = [t for t in self.dock.doc.layers[0].objects[0]
               .properties.get("alsoActivate").split(",")]
        self.assertEqual(ids, ["2"])
        count = sum(1 for e in self.dock.data.edges
                    if e.src == src.key and e.dst == dst.key
                    and e.kind == "alsoActivate")
        self.assertEqual(count, 1)

    def test_create_link_self_noop(self):
        src = self._node("1")
        before = dict(self.dock.doc.layers[0].objects[0].properties.values)
        self.dock.create_link(src, src)
        self.assertEqual(
            dict(self.dock.doc.layers[0].objects[0].properties.values), before)

    def test_hit_port_detects_ports(self):
        itemA = self.dock.node_items[self._node("1").key]
        hit = self.dock.view._hit_port(itemA.scene_port_out())
        self.assertEqual(hit[1], "out")
        self.assertIs(hit[0], itemA)
        miss = self.dock.view._hit_port(QPointF(-9999, -9999))
        self.assertIsNone(miss)

    def test_hit_port_only_within_ball(self):
        """命中区约 12 屏幕像素（zoom=1 时即 12 个场景单位）：球心命中、12 单位内命中、
        远超 12 单位（如 40）不命中。"""
        from unittest.mock import patch

        itemA = self.dock.node_items[self._node("1").key]
        out = itemA.scene_port_out()
        # 把 mapFromScene 桩成恒等，使「屏幕像素距离」== 场景距离，便于精确断言阈值
        with patch.object(self.dock.view, "mapFromScene",
                          side_effect=lambda q: QPointF(q)):
            # 球心：必中
            hit = self.dock.view._hit_port(out)
            self.assertIs(hit[0], itemA)
            self.assertEqual(hit[1], "out")
            # 距球心 8 个场景单位（< 12px 命中半径）：仍命中
            near = QPointF(out.x() + 8.0, out.y())
            self.assertIsNotNone(self.dock.view._hit_port(near),
                                "12px 命中区内应命中")
            # 距球心 40 个场景单位（远超 12px）：不命中
            far = QPointF(out.x() + 40.0, out.y())
            self.assertIsNone(self.dock.view._hit_port(far),
                              "命中区外不应触发连线")

    def test_hover_port_shows_cross_cursor(self):
        """悬停在端口（未按任何键）时**viewport**光标应为十字；移开复位为箭头。

        关键点：鼠标事件实际落在 view 的 viewport 子控件上，显示的光标也是
        viewport 的光标，且追踪必须开在 viewport 上 —— 否则真实窗口里纯悬停不会
        进入 mouseMoveEvent、十字光标也改不到正确的控件上。
        """
        from unittest.mock import patch

        # viewport 必须开启鼠标追踪（事件落在 viewport 上）
        self.assertTrue(self.dock.view.viewport().hasMouseTracking(),
                        "viewport 必须开启鼠标追踪，否则悬停不触发 mouseMoveEvent")
        itemA = self.dock.node_items[self._node("1").key]
        p = itemA.scene_port_out()
        with patch.object(self.dock.view, "mapToScene", side_effect=lambda q: QPointF(q)), \
             patch.object(self.dock.view, "mapFromScene", side_effect=lambda q: QPointF(q)):
            # 悬停在端口（未按键）
            ev = QMouseEvent(QEvent.MouseMove, p, p, Qt.NoButton, Qt.NoButton,
                             Qt.NoModifier)
            self.dock.view.mouseMoveEvent(ev)
            self.assertEqual(self.dock.view.viewport().cursor().shape(),
                             Qt.CrossCursor,
                             "悬停端口应显示十字光标（viewport 的光标）")
            # 移开到空白处（远离任何端口）
            away = QPointF(-9999, -9999)
            ev2 = QMouseEvent(QEvent.MouseMove, away, away, Qt.NoButton,
                              Qt.NoButton, Qt.NoModifier)
            self.dock.view.mouseMoveEvent(ev2)
            self.assertNotEqual(self.dock.view.viewport().cursor().shape(),
                                Qt.CrossCursor,
                                "离开端口应复位光标")

    def test_target_node_at_inside_bounding(self):
        itemB = self.dock.node_items[self._node("2").key]
        center = itemB.sceneBoundingRect().center()
        self.assertIs(self.dock.view._target_node_at(center), itemB)
        self.assertIsNone(self.dock.view._target_node_at(QPointF(-9999, -9999)))

    def test_drag_event_sequence_creates_link(self):
        """完整模拟：在 A 的 out 端口按下 → 拖到 B 节点 → 松开。

        离屏环境下视图尺寸为 0，mapToScene/mapFromScene 无法正确往返；真实窗口里
        这两者会正确互逆。这里把 mapToScene 打桩成恒等（即「视图坐标 == 场景坐标」，
        与 resetTransform 的真实行为一致），从而精确验证鼠标事件链与连线逻辑。
        """
        from unittest.mock import patch

        itemA = self.dock.node_items[self._node("1").key]
        itemB = self.dock.node_items[self._node("2").key]

        def send(kind, pos: QPointF, button=Qt.LeftButton,
                 buttons=Qt.LeftButton):
            ev = QMouseEvent(kind, pos, pos, button, buttons, Qt.NoModifier)
            if kind == QEvent.MouseButtonPress:
                self.dock.view.mousePressEvent(ev)
            elif kind == QEvent.MouseMove:
                self.dock.view.mouseMoveEvent(ev)
            else:
                self.dock.view.mouseReleaseEvent(ev)

        # 用事件里的「场景坐标」直接驱动；mapToScene 桩为恒等，等价于真实窗口的往返
        with patch.object(self.dock.view, "mapToScene",
                          side_effect=lambda p: QPointF(p)):
            send(QEvent.MouseButtonPress, itemA.scene_port_out())
            self.assertIsNotNone(self.dock.view._link_src,
                                 "按下端口应进入连线状态")
            send(QEvent.MouseMove, itemB.sceneBoundingRect().center())
            self.assertIsNotNone(self.dock.view._link_tmp,
                                 "拖拽中应存在临时连线")
            send(QEvent.MouseButtonRelease,
                 itemB.sceneBoundingRect().center())
        self.assertIsNone(self.dock.view._link_src, "松开后连线状态应清空")
        self.assertEqual(
            self.dock.doc.layers[0].objects[0].properties.get("alsoActivate"), "2")

    def test_real_painted_dot_matches_hit_port(self):
        """回归守卫：scene_port_out 必须等于「画出来的黄点」真实位置。

        之前 scene_port_* 在 port_out（已是场景坐标）上又 mapToScene 一次，导致命中
        端口比黄点偏移一个节点坐标 —— 节点不在原点时，点在黄点上也连不上（用户
        「范围内也没用」的真正根因）。这里把节点挪到非原点，断言两者一致，且在该点
        真实屏幕像素处按下能进入连线态。
        """
        from rwmap.widgets.trigger_graph_dock import NODE_W, PORT_Y

        itemA = self.dock.node_items[self._node("1").key]
        itemA.setPos(300, 200)  # 非原点：让「双重 mapToScene」的偏移暴露出来
        itemA.update()
        # 画出来的黄点（out 端口）真实场景坐标 = 局部 (NODE_W, PORT_Y) 投到场景
        real_dot = itemA.mapToScene(QPointF(NODE_W, PORT_Y))
        self.assertEqual(itemA.scene_port_out(), real_dot,
                         "scene_port_out 必须等于真实黄点位置，否则点中黄点也连不上")
        # 在该黄点的真实屏幕像素处按下 → 应进入连线态
        screen = self.dock.view.mapFromScene(real_dot)
        ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(screen), QPointF(screen),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        self.dock.view.mousePressEvent(ev)
        self.assertIsNotNone(self.dock.view._link_src,
                             "在真实黄点像素处按下应进入连线状态")


if __name__ == "__main__":
    unittest.main()
