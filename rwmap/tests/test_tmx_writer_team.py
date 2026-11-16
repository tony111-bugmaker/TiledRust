import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lxml import etree

from rwmap.document.properties import PropertyBag
from rwmap.io.tmx_writer import _write_properties


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
