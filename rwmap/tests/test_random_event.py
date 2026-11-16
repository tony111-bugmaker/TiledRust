"""回归：等概率「随机触发事件」模板。

覆盖：等概率公式真的等概率、spawnUnits 写法、整条链的对象 / id / 连线、
条件与动作共用矩形、多次插入不串线、TMX 往返、对话框参数、菜单一键插入与撤销。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import TestCase, mock  # noqa: E402

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.io.tmx_reader import read_document  # noqa: E402
from rwmap.io.tmx_writer import create_blank_document  # noqa: E402
from rwmap.rw.random_event import (  # noqa: E402
    RandomEventSpec, branch_labels, build_random_event_objects, describe,
    equal_chances, find_or_create_triggers_layer, insert_random_event,
    spawn_units_value, unique_prefix,
)
from rwmap.rw.version import active_profile  # noqa: E402
from rwmap.widgets.random_event_dialog import RandomEventDialog  # noqa: E402
from rwmap.widgets.trigger_graph_dock import collect_graph  # noqa: E402
from rwmap.widgets.trigger_inspector import TYPE_DEFS  # noqa: E402


def _by_id(objs):
    return {o.properties.get("id"): o for o in objs}


def _blank_doc(tmp: Path):
    doc = create_blank_document(active_profile(), 32, 24, 20, 20,
                                path=tmp / "m.tmx")
    doc.layers = [TileLayer(name="Ground", width=32, height=24,
                            cells=[0] * (32 * 24))]
    return doc


class EqualChanceTest(TestCase):
    def test_each_branch_really_gets_one_over_n(self):
        """条件概率 1/N, 1/(N-1), …, 1 乘上「前面都没中」后各得 1/N。"""
        for n in (2, 3, 4, 5):
            chances = equal_chances(n)
            self.assertEqual(len(chances), n)
            self.assertEqual(chances[-1], 1.0)
            missed = 1.0
            for p in chances:
                self.assertAlmostEqual(missed * p, 1.0 / n, places=9)
                missed *= (1.0 - p)
            self.assertAlmostEqual(missed, 0.0, places=9)

    def test_spawn_units_value_is_game_parseable(self):
        spec = RandomEventSpec()
        self.assertEqual(
            spawn_units_value(spec),
            "builder(spawnChance=0.5,maxSpawnLimit=1),"
            "tank(spawnChance=1,maxSpawnLimit=1)")
        four = spawn_units_value(RandomEventSpec(branch_count=4))
        self.assertIn("spawnChance=0.25", four)
        self.assertIn("spawnChance=0.333333", four)
        self.assertNotIn(", ", four)          # 铁锈列表逗号后不带空格
        self.assertEqual(four.count("maxSpawnLimit=1"), 4)

    def test_labels_and_description(self):
        spec = RandomEventSpec(name="坦克事件", rect=(10, 20, 30, 40))
        self.assertEqual(branch_labels(spec),
                         ["建造者 · 50%", "坦克 · 50%"])
        text = describe(spec)
        self.assertIn("50%", text)
        self.assertIn("重型坦克", text)        # 单位显示中文
        self.assertIn("unitRemove 不支持指定兵种", text)


class ChainShapeTest(TestCase):
    def test_only_real_game_types(self):
        objs = build_random_event_objects(RandomEventSpec())
        for obj in objs:
            self.assertIn(obj.type_, TYPE_DEFS,
                          "不能造游戏不认识的触发器 type")

    def test_five_nodes_and_types(self):
        objs = build_random_event_objects(RandomEventSpec())
        self.assertEqual(len(objs), 5)
        self.assertEqual(
            [o.type_ for o in objs],
            ["unitAdd", "unitDetect", "unitDetect", "unitDetect", "unitRemove"])
        self.assertEqual([o.object_id for o in objs], [1, 2, 3, 4, 5])

    def test_ids_unique_and_links_resolve(self):
        objs = build_random_event_objects(RandomEventSpec())
        ids = [o.properties.get("id") for o in objs]
        self.assertEqual(len(set(ids)), len(ids))
        by = _by_id(objs)
        self.assertEqual(by["re_b1"].properties.get("activatedBy"), "re_roll")
        self.assertEqual(by["re_b2"].properties.get("activatedBy"), "re_roll")
        self.assertEqual(by["re_b1"].properties.get("alsoActivate"), "re_check")
        self.assertEqual(by["re_check"].properties.get("alsoActivate"),
                         "re_remove")
        self.assertIsNone(by["re_b2"].properties.get("alsoActivate"),
                          "未抽中的分支不能有后续动作")
        # 每个链接目标都真实存在
        for obj in objs:
            for key in ("activatedBy", "alsoActivate"):
                ref = obj.properties.get(key)
                if ref:
                    self.assertIn(ref, by)

    def test_condition_and_action_share_the_rect(self):
        """unitRemove 不支持指定兵种，必须靠与条件相同的矩形 + team 收窄。"""
        spec = RandomEventSpec(rect=(100, 200, 160, 80), team="3")
        by = _by_id(build_random_event_objects(spec))
        for key in ("re_check", "re_remove"):
            obj = by[key]
            self.assertEqual((obj.x, obj.y, obj.width, obj.height),
                             (100.0, 200.0, 160.0, 80.0))
            self.assertEqual(obj.properties.get("team"), "3")
        self.assertEqual(by["re_check"].properties.get("unitType"), "heavyTank")
        self.assertEqual(by["re_check"].properties.get("minUnits"), "1")
        self.assertIsNone(by["re_check"].properties.get("maxUnits"))

    def test_limit_one_and_custom_unit(self):
        spec = RandomEventSpec(unit_type="mammothTank", limit_one=True)
        by = _by_id(build_random_event_objects(spec))
        self.assertEqual(by["re_check"].properties.get("unitType"),
                         "mammothTank")
        self.assertEqual(by["re_check"].properties.get("maxUnits"), "1")

    def test_four_branches_build_seven_nodes(self):
        spec = RandomEventSpec(branch_count=4)
        objs = build_random_event_objects(spec)
        self.assertEqual(len(objs), 1 + 4 + 2)
        self.assertEqual(sum(1 for o in objs if o.type_ == "unitDetect"), 5)


class InsertAndRoundTripTest(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rw_randevent_"))

    def test_creates_triggers_layer(self):
        doc = _blank_doc(self.tmp)
        objs = insert_random_event(doc, RandomEventSpec())
        layer = find_or_create_triggers_layer(doc)
        self.assertEqual([l.name for l in doc.layers], ["Ground", "Triggers"])
        self.assertEqual(len(layer.objects), 5)
        self.assertEqual(len(objs), 5)
        self.assertTrue(layer.dirty and doc.dirty)

    def test_two_inserts_never_share_ids_or_object_ids(self):
        doc = _blank_doc(self.tmp)
        a = insert_random_event(doc, RandomEventSpec(name="A"))
        b = insert_random_event(doc, RandomEventSpec(name="B"))
        layer = find_or_create_triggers_layer(doc)
        self.assertEqual(len(layer.objects), 10)
        self.assertFalse({o.properties.get("id") for o in a}
                         & {o.properties.get("id") for o in b})
        self.assertEqual(len({o.object_id for o in layer.objects}), 10)
        # 已有 re_ / re2_ 两组时，第三组自动避让成 re3_
        self.assertEqual(unique_prefix(doc), "re3")

    def test_tmx_roundtrip_keeps_props_and_edges(self):
        doc = _blank_doc(self.tmp)
        insert_random_event(doc, RandomEventSpec(rect=(200, 160, 120, 120)))
        out = doc.save(self.tmp / "demo.tmx")
        doc2 = read_document(out, active_profile())
        layer = next(l for l in doc2.layers if isinstance(l, ObjectLayer))
        self.assertEqual(layer.name, "Triggers")
        self.assertEqual(len(layer.objects), 5)
        by = _by_id(layer.objects)
        self.assertIn("spawnChance=0.5", by["re_roll"].properties.get("spawnUnits"))
        self.assertEqual(by["re_check"].properties.get("unitType"), "heavyTank")
        self.assertEqual((by["re_remove"].x, by["re_remove"].y,
                          by["re_remove"].width, by["re_remove"].height),
                         (200.0, 160.0, 120.0, 120.0))
        graph = collect_graph(doc2)
        self.assertEqual(len(graph.nodes), 5)
        self.assertEqual(sorted(e.kind for e in graph.edges),
                         ["activatedBy", "activatedBy",
                          "alsoActivate", "alsoActivate"])


class DialogTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_defaults_and_preview(self):
        dlg = RandomEventDialog()
        spec = dlg.spec()
        self.assertEqual(spec.unit_type, "heavyTank")
        self.assertEqual(spec.team, "0")
        self.assertEqual(spec.branch_count, 2)
        self.assertIn("50%", dlg.prob_label.text())
        dlg.branch_spin.setValue(4)
        self.assertIn("25%", dlg.prob_label.text())
        self.assertEqual(dlg.spec().branch_count, 4)

    def test_default_rect_follows_map_size(self):
        doc = _blank_doc(Path(tempfile.mkdtemp(prefix="rw_randdlg_")))
        dlg = RandomEventDialog(doc=doc)
        x, y, w, h = dlg.spec().rect
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        self.assertLess(x + w, doc.width * doc.tile_w)

    def test_zero_size_rect_is_rejected(self):
        dlg = RandomEventDialog()
        dlg.rect_spins[2].setValue(0)
        dlg._on_accept()
        self.assertIn("宽和高", dlg.note_label.text())
        self.assertNotEqual(dlg.result(), QDialog.Accepted)


class MenuInsertTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_action_exists_and_insert_is_undoable(self):
        from rwmap.app.main_window import MainWindow

        w = MainWindow(auto_open=False)
        try:
            doc = _blank_doc(Path(tempfile.mkdtemp(prefix="rw_randmenu_")))
            w.state.open_document(doc)
            w._refresh_document()
            QApplication.processEvents()

            self.assertTrue(hasattr(w, "act_random_event"))
            in_menu = any(
                action.text() == "插入随机事件…"
                for menu_action in w.menuBar().actions()
                if menu_action.menu() is not None
                for action in menu_action.menu().actions())
            self.assertTrue(in_menu, "「地图」菜单里要有插入随机事件")

            class FakeDlg:
                def __init__(self, *a, **k):
                    pass
                def exec(self):
                    return QDialog.Accepted
                def spec(self):
                    return RandomEventSpec(name="测试事件")

            with mock.patch("rwmap.app.main_window.RandomEventDialog", FakeDlg):
                w._insert_random_event()
            layer = next(l for l in doc.layers if isinstance(l, ObjectLayer))
            self.assertEqual(layer.name, "Triggers")
            self.assertEqual(len(layer.objects), 5)
            w.state.undo_stack.undo()
            self.assertEqual(len(layer.objects), 0,
                             "一次 Ctrl+Z 应该把整条链还原掉")
        finally:
            w.deleteLater()


if __name__ == "__main__":
    unittest.main()
