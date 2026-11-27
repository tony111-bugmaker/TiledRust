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


class UnitCountTest(TestCase):
    """数量字段：解析 / 写盘 / 反推往返 / 选择器 UI。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_parse_units_with_count(self):
        self.assertEqual(
            unit_names.parse_units("重型坦克:3#2, 坦克:2, scout#5"),
            [("heavyTank", 3.0, 2), ("tank", 2.0, 1), ("scout", 1.0, 5)])
        # 老写法（无 #）数量默认 1；重复项权重累加、数量取最大
        self.assertEqual(unit_names.parse_units("tank,tank:2#3"),
                         [("tank", 3.0, 3)])

    def test_format_units_omits_defaults(self):
        self.assertEqual(
            unit_names.format_units([("heavyTank", 3.0, 2),
                                     ("tank", 2.0, 1), ("scout", 1.0, 1)]),
            "heavyTank:3#2,tank:2,scout")
        self.assertEqual(unit_names.parse_units(
            unit_names.format_units([("heavyTank", 3.0, 2)])),
            [("heavyTank", 3.0, 2)])

    def test_build_spawn_units_with_count(self):
        s = unit_names.build_spawn_units_full(
            [("heavyTank", 3.0, 2), ("tank", 1.0, 1)])
        self.assertEqual(
            s, "heavyTank*2(spawnChance=0.75,maxSpawnLimit=2),"
               "tank(spawnChance=1,maxSpawnLimit=1)")

    def test_count_round_trip_through_spawn_units(self):
        triples = [("heavyTank", 3.0, 2), ("tank", 2.0, 1), ("scout", 1.0, 5)]
        text = unit_names.build_spawn_units_full(triples)
        self.assertEqual(unit_names.units_from_spawn_units(text), triples)

    def test_plain_quantity_spawn_not_mistaken_for_weights(self):
        # 真实图里的 `单位*数量` 但无 spawnChance 链，不能被当成随机权重表
        self.assertIsNone(unit_names.units_from_spawn_units("builder*10,tank*2"))
        # maxSpawnLimit 与 *N 不一致，也拒绝
        self.assertIsNone(unit_names.units_from_spawn_units(
            "tank*3(spawnChance=0.5,maxSpawnLimit=1),scout(spawnChance=1,maxSpawnLimit=1)"))

    def test_picker_count_and_green_style(self):
        from rwmap.widgets.unit_picker import SPAWN_GREEN, UnitPickerDialog
        dlg = UnitPickerDialog(current=[("heavyTank", 3.0, 2)])
        ht = next(row for row in dlg._boxes if row[0] == "heavyTank")
        raw, _cn, box, spin = ht
        count_spin = dlg._count_spins[raw]
        self.assertTrue(box.isChecked())
        self.assertEqual(spin.value(), 3)
        self.assertEqual(count_spin.value(), 2)
        # 选中态用生成单位触发器同款绿，且数量框随勾选启用
        self.assertIn(SPAWN_GREEN, box.styleSheet())
        self.assertEqual(SPAWN_GREEN, "#2f8f52")
        self.assertTrue(count_spin.isEnabled())
        count_spin.setValue(7)
        self.assertIn((raw, 3.0, 7), dlg.selected_units)
        # 未勾选项：数量框禁用、默认 1
        self.assertFalse(dlg._count_spins["scout"].isEnabled())
        self.assertEqual(dlg._count_spins["scout"].value(), 1)
        dlg.deleteLater()

    def test_random_units_round_trip_keeps_count_in_tmx(self):
        """写完 TMX 后 spawnUnits 带 *N；重开读回 randomUnits 数量不丢。"""
        cfg = active_profile()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "count.tmx"
            doc = create_blank_document(cfg, 40, 30, 20, 20, path=path)
            obj = MapObject(object_id=1, name="随机出兵", type_="unitAdd",
                            shape="point", x=0, y=0)
            obj.properties.set("randomUnits", "heavyTank:3#4,tank:1", "string")
            doc.layers = [ObjectLayer(name="Triggers", kind="object", objects=[obj])]
            doc.save(path)

            raw = path.read_text(encoding="utf-8")
            self.assertIn("heavyTank*4(spawnChance=", raw)
            self.assertIn("maxSpawnLimit=4", raw)
            self.assertNotIn("randomUnits", raw)

            reopened = read_document(path)
            ro = next(o for l in reopened.layers if isinstance(l, ObjectLayer)
                      for o in l.objects if o.name == "随机出兵")
            self.assertEqual(ro.properties.get("randomUnits"), "heavyTank:3#4,tank")

    def test_spin_boxes_visible_and_digits_not_clipped(self):
        """两个数字框必须完整可见、内部 QLineEdit 不能被 QSS 压成 1px。

        Qt 6.12 Windows11 下给 NoButtons 的 QSpinBox 设 border/border-radius/
        padding 会把内部 lineEdit 压成 1px，值在但看不见；长单位名按钮的默认
        sizeHint 又会把右侧「数量」框挤出滚动 viewport。两条都在此回归。
        """
        from PySide6.QtWidgets import QScrollArea
        from rwmap.widgets.unit_picker import UnitPickerDialog

        dlg = UnitPickerDialog(current=[("artillery", 2.0, 3)])
        dlg.show()
        raw, _cn, box, spin = next(r for r in dlg._boxes if r[0] == "artillery")
        count_spin = dlg._count_spins[raw]
        # 1) 数字有显示空间（1px 就是被 QSS 压扁的坏状态）
        self.assertGreater(spin.lineEdit().width(), 10)
        self.assertGreater(count_spin.lineEdit().width(), 10)
        # 2) 最右的数量框完整落在滚动 viewport 内
        scroll = next(w for w in dlg.findChildren(QScrollArea))
        app = dlg.parent() or None
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        self.assertLessEqual(count_spin.x() + count_spin.width(),
                             scroll.viewport().width())
        dlg.deleteLater()


class UnitPreviewTest(TestCase):
    """单位外观预览模块：真实图 / 兜底 / 占位 / 缓存。"""

    @classmethod
    def setUpClass(cls):
        from rwmap.rw import unit_preview
        unit_preview.clear_cache()

    def test_tsx_and_sprite_and_placeholder(self):
        from rwmap.rw import unit_preview as up
        up._ensure_index()
        # tsx 头像：这些是游戏建造菜单里的单位，一定有图
        for key in ("heavyTank", "artillery", "builder", "bugMelee"):
            self.assertIn(key, up._TSX_ICON, key)
        # 精灵条兜底：机甲不在 tsx 里，但能从 ini [graphics] image 取到主图
        self.assertNotIn("mechFlame", up._TSX_ICON)
        self.assertIn("mechFlame", up._SPRITE)
        # 三类来源都产出固定尺寸的 RGBA 图
        for key in ("heavyTank", "mechFlame", "definitely_not_a_unit_zzz"):
            img = up.unit_preview_image(key)
            self.assertEqual(img.size, (up.ICON_SIZE, up.ICON_SIZE))
            self.assertEqual(img.mode, "RGBA")
        # 真实图与占位图可区分（缓存里 None 表示走了占位）
        self.assertIsNotNone(up._CACHE.get("heavyTank"))
        self.assertIsNone(up._CACHE.get("definitely_not_a_unit_zzz"))

    def test_sprite_first_frame_crop(self):
        """5 帧精灵条只取第一帧，不是整条塞进图标。"""
        from rwmap.rw import unit_preview as up
        up._ensure_index()
        info = up._SPRITE.get("bugMelee")
        if info is None:
            self.skipTest("本机无 bugMelee 精灵条")
        self.assertGreaterEqual(info.frames, 1)
        img = up._sprite_image("bugMelee")
        self.assertIsNotNone(img)
        self.assertLess(img.width, 40)   # 整条 105px，第一帧约 21px

    def test_cache_reuse(self):
        from rwmap.rw import unit_preview as up
        a = up.unit_preview_image("heavyTank")
        b = up.unit_preview_image("heavyTank")
        self.assertIs(a, b)   # 同一缓存对象


class UnitPreviewUITest(TestCase):
    """选择器里每行左侧的预览图标 label。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_icons_present_and_filtered(self):
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog(current=[("heavyTank", 1.0, 1)])
        # 每个 raw 都有对应图标 label，且尺寸=ICON_SIZE
        from rwmap.rw import unit_preview as up
        self.assertEqual(len(dlg._icons), len(dlg._boxes))
        ico = dlg._icons["heavyTank"]
        self.assertEqual(ico.width(), up.ICON_SIZE)
        self.assertFalse(ico.pixmap().isNull())
        # 搜索过滤时图标随行隐藏
        dlg.search.setText("heavyTank")
        dlg._apply_filter()
        # 用 isHidden()（显式 setVisible 的标记），对话框未 show 时 isVisible() 恒 False
        self.assertFalse(dlg._icons["heavyTank"].isHidden())
        self.assertTrue(dlg._icons["artillery"].isHidden())
        dlg.deleteLater()


class UnitCatalogTest(TestCase):
    """软件内图鉴的数据源：扫描、中文名、多部件拼装。"""

    def test_scan_and_multi_unit(self):
        from rwmap.rw import unit_catalog

        root = unit_catalog.game_root()
        if root is None:
            self.skipTest("本机没有配置游戏目录")
        units = unit_catalog.scan_units(root)
        self.assertGreater(len(units), 100)
        spider = unit_catalog.find_unit("experimentalSpider")
        self.assertIsNotNone(spider)
        self.assertTrue(spider.multi)
        self.assertGreater(len(spider.parts), 4)
        self.assertIsNotNone(spider.composed)
        self.assertGreater(spider.composed.width, 50)
        self.assertTrue(any(p["kind"] == "腿" for p in spider.parts))

    def test_wrapper_module_reexports_compose(self):
        import importlib.util
        import sys

        path = Path(__file__).resolve().parent.parent.parent / "tools" / "unit_compose.py"
        spec = importlib.util.spec_from_file_location("_tools_unit_compose", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["_tools_unit_compose"] = module
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.compose))


class UnitGalleryDialogTest(TestCase):
    @classmethod
    def setUpClass(cls):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_lists_filters_and_detail(self):
        from rwmap.rw import unit_catalog
        if unit_catalog.game_root() is None:
            self.skipTest("本机没有配置游戏目录")
        from rwmap.widgets.unit_gallery_dialog import UnitGalleryDialog

        dlg = UnitGalleryDialog()
        self.app.processEvents()
        self.assertGreater(dlg.list.count(), 100)
        dlg.search.setText("experimentalSpider")
        self.app.processEvents()
        self.assertEqual(dlg.list.count(), 1)
        self.assertEqual(dlg._current.name, "experimentalSpider")
        self.assertIn("组成部件", dlg.detail.toHtml())
        self.assertFalse(dlg.picture.pixmap().isNull())
        dlg.only_multi.setChecked(True)
        dlg.search.clear()
        self.app.processEvents()
        self.assertGreater(dlg.list.count(), 20)
        dlg.close()
        dlg.deleteLater()


class FixedSpawnTextTest(TestCase):
    """生成单位的 spawnUnits「单位*数量」解析/写回。"""

    def test_round_trip(self):
        from rwmap.rw import unit_names as u
        for text in ("heavyTank*10,builder*2", "tank",
                     "tank*3(falling=true),scout"):
            self.assertEqual(u.format_fixed_units(u.parse_fixed_units(text)),
                             text)
        self.assertEqual(u.parse_fixed_units("重型坦克*3,坦克"),
                         [("heavyTank", 3, ""), ("tank", 1, "")])


class WaveUnitsTextTest(TestCase):
    """波次刷兵列表的原版「数量 单位名」与选择器格式互转。"""

    def test_parse_and_format(self):
        from rwmap.rw import unit_names as u
        self.assertEqual(u.parse_wave_units("6 heavyTank,2 bugMelee"),
                         [("heavyTank", 6), ("bugMelee", 2)])
        self.assertEqual(u.parse_wave_units("heavyTank*6,重型坦克*2"),
                         [("heavyTank", 6), ("heavyTank", 2)])
        self.assertEqual(u.parse_wave_units("DropShip,6 heavyTank"),
                         [(u.to_raw("DropShip"), 1), ("heavyTank", 6)])
        self.assertEqual(u.format_wave_units(
            [("heavyTank", 6), ("bugMelee", 1)]), "6 heavyTank,bugMelee")

    def test_round_trip(self):
        from rwmap.rw import unit_names as u
        text = "6 heavyTank,2 bugMelee"
        self.assertEqual(u.format_wave_units(u.parse_wave_units(text)), text)


class PickerModesTest(TestCase):
    """生成单位 / 地图初始单位共用的选择器：去权重 + 图鉴入口。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_spawn_mode_hides_weight_keeps_count(self):
        from PySide6.QtWidgets import QPushButton
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog(current=[("heavyTank", 3, 2)], mode="spawn")
        self.assertEqual(dlg.selected_units, [("heavyTank", 1.0, 2)])
        self.assertTrue(dlg._boxes[0][3].isHidden())
        self.assertFalse(dlg._count_spins["heavyTank"].isHidden())
        self.assertTrue(any(b.text() == "单位图鉴"
                            for b in dlg.findChildren(QPushButton)))
        dlg.deleteLater()

    def test_wave_mode_hides_weight_keeps_count(self):
        """刷兵列表只有数量列，不能显示随机生成的权重列。"""
        from PySide6.QtWidgets import QLabel, QPushButton
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog(current=[("heavyTank", 3, 6)], mode="wave")
        self.assertEqual(dlg.mode, "wave")
        self.assertFalse(dlg._with_weight)
        self.assertNotIn("权重", dlg.windowTitle())
        weight_labels = [lab for lab in dlg.findChildren(QLabel)
                         if lab.text() == "权重"]
        self.assertTrue(weight_labels)
        self.assertTrue(all(lab.isHidden() for lab in weight_labels))
        self.assertEqual(dlg.selected_units, [("heavyTank", 1.0, 6)])
        self.assertTrue(dlg._boxes[0][3].isHidden())       # 权重 spin
        self.assertFalse(dlg._count_spins["heavyTank"].isHidden())
        self.assertTrue(any(b.text() == "单位图鉴"
                            for b in dlg.findChildren(QPushButton)))
        dlg.deleteLater()

    def test_no_orphan_visible_widgets_in_any_mode(self):
        """四种模式构造时都不得把无父级控件 setVisible(True)，否则闪顶层空窗。

        坑：``addWidget`` 到尚未挂到父控件的 QHBoxLayout 时 parent() 仍是 None；
        必须等 ``root.addLayout(header)`` / ``hl = QHBoxLayout(row)`` 之后才能显示。
        """
        from PySide6.QtWidgets import QPushButton, QWidget
        from rwmap.widgets.unit_picker import UnitPickerDialog
        original = QWidget.setVisible
        offenders = []

        def guarded(widget, visible):
            if visible and widget.parent() is None:
                offenders.append((type(widget).__name__,
                                  getattr(widget, "text", lambda: "")()[:20]))
            return original(widget, visible)

        QWidget.setVisible = guarded
        try:
            for mode in ("random", "spawn", "wave", "initial"):
                offenders.clear()
                dlg = UnitPickerDialog(mode=mode)
                self.app.processEvents()
                self.assertEqual(offenders, [],
                                 f"{mode} 模式出现无父级显示: {offenders}")
                dlg.deleteLater()
        finally:
            QWidget.setVisible = original

    def test_initial_mode_single_no_weight_no_count(self):
        from PySide6.QtWidgets import QPushButton
        from rwmap.widgets.unit_picker import UnitPickerDialog
        dlg = UnitPickerDialog(current=[("heavyTank", 3, 2), ("tank", 1, 1)],
                               mode="initial")
        self.assertEqual(dlg.selected, ["heavyTank"])   # 多值只留第一个
        boxes = {raw: b for raw, _c, b, _s in dlg._boxes}
        boxes["tank"].setChecked(True)
        self.assertEqual(dlg.selected_units, [("tank", 1.0, 1)])
        self.assertFalse(boxes["heavyTank"].isChecked())
        self.assertTrue(dlg._count_spins["tank"].isHidden())
        self.assertTrue(any(b.text() == "单位图鉴"
                            for b in dlg.findChildren(QPushButton)))
        dlg.deleteLater()


class UnknownUnitCheckTest(TestCase):
    """无效单位名检测：避免填错名字导致游戏丢弃整个 unitAdd 触发器。"""

    def test_known_units(self):
        for text in ("heavyTank", "6 heavyTank,2 bugMelee",
                     "bugMeleeT31*100", "bugMeleeT31*1,heavyTank*2",
                     "tank(spawnChance=0.5)"):
            self.assertEqual(unit_names.unknown_units(text), [], text)

    def test_unknown_units_various_formats(self):
        self.assertEqual(unit_names.unknown_units("lecT31*100"), ["lecT31"])
        self.assertEqual(unit_names.unknown_units("6 lecT31"), ["lecT31"])
        self.assertEqual(unit_names.unknown_units("lecT31(spawnChance=0.5)"),
                         ["lecT31"])
        self.assertEqual(unit_names.unknown_units("heavyTank,fooBar,fooBar*2"),
                         ["fooBar"])
        self.assertEqual(unit_names.unknown_units(""), [])

    def test_case_insensitive(self):
        self.assertEqual(unit_names.unknown_units("HEAVYTANK*3"), [])
        self.assertFalse(unit_names.is_known_unit("nonexistentUnit"))


if __name__ == "__main__":
    unittest.main()