import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lxml import etree

from rwmap.document.map_object import MapObject
from rwmap.document.properties import PropertyBag
from rwmap.io.tmx_writer import (
    _write_object, _write_properties, GAME_TYPE_ALIASES,
)


def props_xml(bag):
    el = etree.Element("object")
    _write_properties(el, bag)
    return etree.tostring(el).decode()


class TeamEmptyWriteTest(unittest.TestCase):
    def test_empty_team_not_written(self):
        """空 team 不应写出，否则铁锈战争报 Unexpected integer value:''。"""
        bag = PropertyBag()
        bag.set("team", "")
        xml = props_xml(bag)
        self.assertNotIn("team", xml, "空 team 不应写入 TMX")

    def test_nonempty_team_written(self):
        bag = PropertyBag()
        bag.set("team", "1")
        xml = props_xml(bag)
        self.assertIn('name="team"', xml)
        self.assertIn('value="1"', xml)

    def test_other_empty_props_preserved(self):
        """非整数键的空值仍应保留（保真，铁锈地图常用 <property ... value=""/>）。"""
        bag = PropertyBag()
        bag.set("x", "")
        xml = props_xml(bag)
        self.assertIn('name="x"', xml)

    def test_empty_numeric_keys_skipped(self):
        """其余铁锈战争按整数解析的键，空值同样跳过。"""
        bag = PropertyBag()
        bag.set("dir", "")
        bag.set("credits", "")
        xml = props_xml(bag)
        self.assertNotIn('name="dir"', xml)
        self.assertNotIn('name="credits"', xml)

    def test_random_spawn_type_exports_as_unit_add(self):
        """「随机生成单位」内部类型写回 TMX 必须是 unitAdd（游戏只认这个）。"""
        self.assertEqual(GAME_TYPE_ALIASES["unitAddRandom"], "unitAdd")
        bag = PropertyBag(values={"randomUnits": "heavyTank:3, tank:2"})
        obj = MapObject(object_id=1, name="随机刷兵", type_="unitAddRandom",
                        shape="point", x=10, y=20, properties=bag)
        el = etree.Element("object")
        _write_object(el, obj)
        self.assertEqual(el.get("type"), "unitAdd")
        self.assertNotIn("unitAddRandom", etree.tostring(el).decode())
        # randomUnits 展开成 spawnUnits（带权重），不把内部属性写出去
        self.assertIn("spawnUnits", etree.tostring(el).decode())
        self.assertNotIn('name="randomUnits"', etree.tostring(el).decode())

    def test_initial_unit_type_exports_no_type(self):
        """「地图初始单位」mapUnit 写回 TMX 必须是空 type，只留 unit+team。"""
        self.assertEqual(GAME_TYPE_ALIASES["mapUnit"], "")
        bag = PropertyBag(values={"unit": "experimentalHoverTank", "team": "-1"})
        obj = MapObject(object_id=136, name="", type_="mapUnit", shape="point",
                        x=10, y=20, properties=bag)
        el = etree.Element("object")
        _write_object(el, obj)
        xml = etree.tostring(el).decode()
        self.assertIsNone(el.get("type"), "初始单位不应写出 type")
        self.assertIsNone(el.get("class"), "初始单位不应写 class")
        self.assertIn('name="unit"', xml)
        self.assertIn('value="experimentalHoverTank"', xml)
        self.assertIn('name="team"', xml)

    def test_truly_empty_type_still_exports_no_type(self):
        """纯空 type（map_info / 矩形等）行为不变：仍不写 type。"""
        bag = PropertyBag(values={"type": "skirmish"})
        obj = MapObject(object_id=2, name="map_info", type_="", shape="rect",
                        x=0, y=0, properties=bag)
        el = etree.Element("object")
        _write_object(el, obj)
        self.assertIsNone(el.get("type"))
        self.assertIsNone(el.get("class"))

    def test_real_roundtrip_loads(self):
        """用真实读→写验证：含 team='' 的对象写出后不再带该属性。"""
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        from rwmap.io.tmx_reader import read_document
        from rwmap.io.tmx_writer import write_document
        from rwmap.document.map_document import MapDocument
        import tempfile
        from pathlib import Path

        src = Path("<游戏目录1.15>/mods/maps/[p2]example_skirmish_(2p).tmx")
        if not src.exists():
            self.skipTest("示例地图缺失")
        doc = read_document(src)
        # 找到第一个对象层，给其第一个对象注入空 team
        obj_layer = next(l for l in doc.layers if l.kind == "object")
        obj = obj_layer.objects[0]
        obj.properties.set("team", "")
        out = Path(tempfile.gettempdir()) / "rwmap_team_empty.tmx"
        write_document(doc, out)
        xml = out.read_text(encoding="utf-8")
        self.assertNotIn('name="team" value=""', xml)


if __name__ == "__main__":
    unittest.main()
