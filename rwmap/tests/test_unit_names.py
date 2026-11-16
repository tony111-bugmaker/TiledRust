"""单位中文名映射 + 「随机生成单位」独立属性（等概率 spawnUnits 展开 / 反推）测试。

覆盖：raw<->中文、等概率公式、spawnUnits 解析 / 生成、中文输入归一化、
TMX 写回把 randomUnits 展开成 spawnUnits（且文件不含 randomUnits）、
读回再把等概率 spawnUnits 反推成 randomUnits；非等概率串不应被反推。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

from rwmap.document.layer import ObjectLayer
from rwmap.document.map_object import MapObject
from rwmap.document.properties import PropertyBag
from rwmap.io.tmx_reader import read_document
from rwmap.io.tmx_writer import create_blank_document
from rwmap.rw import unit_names
from rwmap.rw.version import active_profile


class UnitNameMapTest(TestCase):
    def test_cn_and_raw_roundtrip(self):
        self.assertEqual(unit_names.to_cn("heavyTank"), "重型坦克")
        self.assertEqual(unit_names.to_raw("重型坦克"), "heavyTank")
        self.assertEqual(unit_names.to_raw("HEAVYTANK"), "heavyTank")  # 大小写容错
        self.assertEqual(unit_names.to_cn("unknownXYZ"), "unknownXYZ")  # 未知原样

    def test_split_units_handles_cn_and_delimiters(self):
        self.assertEqual(
            unit_names.split_units("重型坦克、猛犸坦克, 坦克"),
            ["heavyTank", "mammothTank", "tank"],
        )


class EqualChanceTest(TestCase):
    def test_equal_chances_formula(self):
        self.assertEqual(unit_names.equal_chances(2), [0.5, 1.0])
        self.assertEqual(unit_names.equal_chances(3),
                         [round(1 / 3, 6), 0.5, 1.0])

    def test_build_and_detect_2way(self):
        s = unit_names.build_spawn_units(["heavyTank", "mammothTank"])
        self.assertEqual(
            s, "heavyTank(spawnChance=0.5,maxSpawnLimit=1),"
               "mammothTank(spawnChance=1,maxSpawnLimit=1)")
        parsed = unit_names.parse_spawn_units(s)
        self.assertTrue(unit_names.is_equal_chances(parsed))

    def test_build_and_detect_3way(self):
        s = unit_names.build_spawn_units(["builder", "tank", "scout"])
        parsed = unit_names.parse_spawn_units(s)
        self.assertTrue(unit_names.is_equal_chances(parsed))
        # 链式 spawnChance 让每支「整体」概率都是 1/3
        chances = [c for _n, c, _m in parsed]
        prob = 1.0
        overall = []
        for c in chances:
            overall.append(prob * c)
            prob *= (1 - c)
        for p in overall:
            self.assertAlmostEqual(p, 1 / 3, places=3)

    def test_non_equal_not_detected(self):
        s = "builder*10,tank*2"  # 普通刷兵串，不是等概率
        self.assertFalse(unit_names.is_equal_chances(
            unit_names.parse_spawn_units(s)))

    def test_parse_respects_parentheses(self):
        s = "heavyTank(spawnChance=0.5,maxSpawnLimit=1),mammothTank(spawnChance=1,maxSpawnLimit=1)"
        parsed = unit_names.parse_spawn_units(s)
        self.assertEqual(parsed, [("heavyTank", 0.5, 1), ("mammothTank", 1.0, 1)])

    def test_normalize_accepts_cn_spawn_units(self):
        raw = unit_names.normalize_spawn_units(
            "重型坦克(spawnChance=0.5,maxSpawnLimit=1),猛犸坦克(spawnChance=1,maxSpawnLimit=1)")
        self.assertEqual(
            raw, "heavyTank(spawnChance=0.5,maxSpawnLimit=1),"
                 "mammothTank(spawnChance=1,maxSpawnLimit=1)")


class WeightedChanceTest(TestCase):
    """权重 -> 链式 spawnChance 的换算（RW 没有权重参数，全靠折算）。"""

    def test_overall_chances_normalized(self):
        self.assertEqual(unit_names.overall_chances([3, 2, 1]),
                         [0.5, 1 / 3, 1 / 6])
        self.assertEqual(unit_names.overall_chances([1, 1]), [0.5, 0.5])
        # 权重全 0 时退化为均分，避免除零
        self.assertEqual(unit_names.overall_chances([0, 0]), [0.5, 0.5])

    def test_chain_chances_reproduce_weights(self):
        for weights in ([3, 2, 1], [1, 1], [5, 1, 1, 1], [2, 7, 3]):
            chances = unit_names.weighted_chances(weights)
            self.assertEqual(chances[-1], 1.0)
            # 用链式概率反算实际命中率，应等于 w_i / Σw
            got, remain = [], 1.0
            for c in chances:
                got.append(remain * c)
                remain *= (1 - c)
            want = unit_names.overall_chances(weights)
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=4)

    def test_build_spawn_units_weighted(self):
        s = unit_names.build_spawn_units_weighted(
            [("heavyTank", 3), ("tank", 2), ("scout", 1)])
        self.assertEqual(
            s,
            "heavyTank(spawnChance=0.5,maxSpawnLimit=1),"
            "tank(spawnChance=0.6667,maxSpawnLimit=1),"
            "scout(spawnChance=1,maxSpawnLimit=1)")
        # 单个单位 / 权重为 0 被剔除
        self.assertEqual(unit_names.build_spawn_units_weighted([("tank", 9)]),
                         "tank(spawnChance=1,maxSpawnLimit=1)")
        self.assertEqual(
            unit_names.build_spawn_units_weighted([("tank", 3), ("scout", 0)]),
            "tank(spawnChance=1,maxSpawnLimit=1)")
        self.assertEqual(unit_names.build_spawn_units_weighted([]), "")

    def test_parse_and_format_weighted(self):
        pairs = unit_names.parse_weighted("重型坦克:3, 坦克×2, scout")
        self.assertEqual(pairs, [("heavyTank", 3.0), ("tank", 2.0), ("scout", 1.0)])
        self.assertEqual(unit_names.format_weighted(pairs),
                         "heavyTank:3,tank:2,scout")
        # 同一单位写两次合并权重
        self.assertEqual(unit_names.parse_weighted("坦克:1,坦克:2"),
                         [("tank", 3.0)])
        # 没有权重的旧写法仍然可用
        self.assertEqual(unit_names.parse_weighted("重型坦克、猛犸坦克"),
                         [("heavyTank", 1.0), ("mammothTank", 1.0)])

    def test_describe_weights(self):
        text = unit_names.describe_weights([("heavyTank", 3), ("tank", 2),
                                            ("scout", 1)])
        self.assertIn("重型坦克 50%", text)
        self.assertIn("侦察者 16.67%", text)

    def test_reverse_weights_from_chain(self):
        s = unit_names.build_spawn_units_weighted(
            [("heavyTank", 3), ("tank", 2), ("scout", 1)])
        self.assertEqual(unit_names.weights_from_spawn_units(s),
                         [("heavyTank", 3.0), ("tank", 2.0), ("scout", 1.0)])
        # 等概率串反推回权 1
        equal = unit_names.build_spawn_units(["builder", "tank", "scout"])
        self.assertEqual(unit_names.weights_from_spawn_units(equal),
                         [("builder", 1.0), ("tank", 1.0), ("scout", 1.0)])

    def test_no_reverse_for_plain_spawn_units(self):
        # 普通刷兵串 / 带额外参数 / maxSpawnLimit≠1 都不该被当权重
        for text in ("builder*10,tank*2",
                     "mechFlame(spawnChance=0.04,maxSpawnLimit=1),"
                     "turret(offsetRandomX=0,offsetRandomY=80,falling=true)",
                     "tank(spawnChance=0.9,maxSpawnLimit=2),"
                     "scout(spawnChance=1,maxSpawnLimit=1)"):
            self.assertIsNone(unit_names.weights_from_spawn_units(text), text)


def _save_with_random_units(raw_list, as_cn: bool = False) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="rw_runittest_"))
    doc = create_blank_document(active_profile(), 40, 30, 20, 20, path=tmp / "t.tmx")
    bag = PropertyBag()
    bag.set("id", "roll")
    value = "、".join(raw_list) if as_cn else ",".join(raw_list)
    bag.set("randomUnits", value)
    obj = MapObject(object_id=1, type_="unitAdd", name="roll",
                    x=0, y=0, width=20, height=20, properties=bag)
    doc.layers = [ObjectLayer(name="Triggers", kind="object", objects=[obj])]
    return doc.save(tmp / "t.tmx")


class RandomUnitsRoundTripTest(TestCase):
    def test_export_expands_and_file_has_no_randomunits(self):
        out = _save_with_random_units(["heavyTank", "mammothTank"])
        xml = out.read_text(encoding="utf-8")
        self.assertIn('name="spawnUnits" value="heavyTank(spawnChance=0.5,maxSpawnLimit=1),'
                      'mammothTank(spawnChance=1,maxSpawnLimit=1)"', xml)
        self.assertNotIn("randomUnits", xml)

    def test_export_accepts_cn_input(self):
        out = _save_with_random_units(["重型坦克", "猛犸坦克"], as_cn=True)
        xml = out.read_text(encoding="utf-8")
        self.assertIn("heavyTank(spawnChance=0.5,maxSpawnLimit=1)", xml)

    def test_reimport_reconstructs_random_units(self):
        out = _save_with_random_units(["heavyTank", "mammothTank"])
        doc = read_document(out, active_profile())
        obj = doc.layers[0].objects[0]
        self.assertEqual(obj.properties.get("randomUnits"), "heavyTank,mammothTank")
        self.assertIn("spawnUnits", obj.properties.values)

    def test_existing_equal_spawnunits_reconstructed(self):
        # 直接写等概率 spawnUnits（如示例地图），读回应反推为 randomUnits
        tmp = Path(tempfile.mkdtemp(prefix="rw_runittest2_"))
        doc = create_blank_document(active_profile(), 40, 30, 20, 20, path=tmp / "t.tmx")
        bag = PropertyBag()
        bag.set("id", "roll")
        bag.set("spawnUnits",
                "builder(spawnChance=0.333333,maxSpawnLimit=1),"
                "tank(spawnChance=0.5,maxSpawnLimit=1),"
                "scout(spawnChance=1,maxSpawnLimit=1)")
        obj = MapObject(object_id=1, type_="unitAdd", name="roll",
                        x=0, y=0, width=20, height=20, properties=bag)
        doc.layers = [ObjectLayer(name="Triggers", kind="object", objects=[obj])]
        out = doc.save(tmp / "t.tmx")
        doc2 = read_document(out, active_profile())
        o = doc2.layers[0].objects[0]
        self.assertEqual(o.properties.get("randomUnits"), "builder,tank,scout")

    def test_weighted_round_trip(self):
        out = _save_with_random_units(["heavyTank:3", "tank:2"])
        xml = out.read_text(encoding="utf-8")
        self.assertIn("heavyTank(spawnChance=0.6,maxSpawnLimit=1)", xml)
        self.assertIn("tank(spawnChance=1,maxSpawnLimit=1)", xml)
        doc = read_document(out, active_profile())
        obj = doc.layers[0].objects[0]
        self.assertEqual(obj.properties.get("randomUnits"), "heavyTank:3,tank:2")

    def test_non_equal_spawnunits_not_reconstructed(self):
        tmp = Path(tempfile.mkdtemp(prefix="rw_runittest3_"))
        doc = create_blank_document(active_profile(), 40, 30, 20, 20, path=tmp / "t.tmx")
        bag = PropertyBag()
        bag.set("id", "roll")
        bag.set("spawnUnits", "builder*10,tank*2")
        obj = MapObject(object_id=1, type_="unitAdd", name="roll",
                        x=0, y=0, width=20, height=20, properties=bag)
        doc.layers = [ObjectLayer(name="Triggers", kind="object", objects=[obj])]
        out = doc.save(tmp / "t.tmx")
        doc2 = read_document(out, active_profile())
        o = doc2.layers[0].objects[0]
        self.assertIsNone(o.properties.get("randomUnits"))


class UnitPickerTest(TestCase):
    """选择器对话框：中文去重、别名归并、搜索过滤、全选只作用于可见项。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_catalog_covers_full_roster(self):
        """单位表来自游戏文件，覆盖全部原版单位 / 建筑（旧版只有 38 个）。"""
        rows = unit_names.catalog()
        self.assertGreaterEqual(len(rows), 100)
        raws = {r["raw"] for r in rows}
        # 各阵营 / 各类型都要有
        for raw in ("tank", "heavyTank", "mechGun", "bomber", "battleShip",
                    "turret", "extractor", "commandCenter", "bugMelee",
                    "nukeLaucher"):
            self.assertIn(raw, raws)
        # 可见项都有中文名，否则界面会出现英文裸名
        self.assertTrue(all(r["cn"] for r in rows),
                        [r["raw"] for r in rows if not r["cn"]])
        self.assertEqual(len({r["raw"] for r in unit_names.CATALOG}),
                         len(unit_names.CATALOG))     # raw 不重复

    def test_catalog_groups_and_hidden(self):
        groups = {r["group"] for r in unit_names.catalog()}
        for name in ("陆军", "机甲", "空军", "海军", "防御", "经济", "虫族"):
            self.assertIn(name, groups)
        # 战役变体默认隐藏，切到「显示内部变体」才出现
        self.assertTrue(unit_names.is_hidden("c_tank"))
        self.assertNotIn("c_tank", {r["raw"] for r in unit_names.catalog()})
        self.assertIn("c_tank",
                      {r["raw"] for r in unit_names.catalog(True)})
        self.assertEqual(unit_names.group_of("mechGun"), "机甲")

    def test_cn_names_come_from_official_translation(self):
        self.assertEqual(unit_names.to_cn("mechGun"), "基础机甲")
        self.assertEqual(unit_names.to_cn("bugSpore"), "孢子虫")
        self.assertEqual(unit_names.to_raw("等离子机甲"), "mechLaser")
        # 官方译名覆盖内置兜底（火炮 -> 自行火炮）
        self.assertEqual(unit_names.to_cn("artillery"), "自行火炮")

    def test_case_variant_mapped_to_canonical(self):
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog(current=["HEAVYTANK"])
        self.assertEqual(["heavyTank"], dlg.selected)
        dlg.deleteLater()

    def test_unknown_unit_is_preserved(self):
        """mod / 自定义单位不在表里也要保留，不能被「确定」悄悄删掉。"""
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog(current=["myModTank"])
        self.assertEqual(["myModTank"], dlg.selected)
        dlg.deleteLater()

    def test_picker_weights(self):
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog(current=[("heavyTank", 3), ("tank", 2)])
        self.assertEqual(dlg.selected_weighted,
                         [("heavyTank", 3.0), ("tank", 2.0)])
        # 未勾选的单位权重框禁用，默认 1；勾上后可改
        scout = next(row for row in dlg._boxes if row[0] == "scout")
        raw, _cn, box, spin = scout
        self.assertFalse(spin.isEnabled())
        self.assertEqual(spin.value(), 1)
        box.setChecked(True)
        self.assertTrue(spin.isEnabled())
        spin.setValue(5)
        self.assertIn((raw, 5.0), dlg.selected_weighted)
        dlg.deleteLater()

    def test_group_filter_and_hidden_toggle(self):
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog()
        self.assertGreaterEqual(len(dlg._boxes), 100)      # 全部原版单位
        dlg.group_combo.setCurrentText("海军")
        self.assertIn("battleShip", dlg._visible)
        self.assertNotIn("tank", dlg._visible)
        self.assertIn("gunBoat", dlg._visible)             # 机枪艇归海军
        dlg.group_combo.setCurrentText("全部分类")
        self.assertNotIn("c_tank", dlg._visible)           # 战役变体默认折叠
        dlg.show_hidden.setChecked(True)
        self.assertIn("c_tank", dlg._visible)
        dlg.deleteLater()

    def test_search_filters_and_select_all_respects_filter(self):
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog()
        dlg.search.setText("坦克")
        self.assertTrue(dlg._visible)
        self.assertNotIn("builder", dlg._visible)
        dlg._set_all(True)
        # 只有筛出来的坦克类被勾上，没筛到的建造者不会被顺带勾上
        self.assertTrue(dlg.selected)
        self.assertIn("tank", dlg.selected)
        self.assertNotIn("builder", dlg.selected)
        self.assertNotIn("worker", dlg.selected)
        dlg.search.setText("")
        self.assertIn("builder", dlg._visible)
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()