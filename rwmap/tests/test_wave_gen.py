"""回归：生存模式「波次生成器」——链条校验 + 编译原版 survivalWaves。

覆盖：
- waveGen 节点识别（内存 type / 磁盘重开靠私有标记）
- 链完整（单节点 / 三节点链）无问题
- 断链：孤立节点、多起点、分叉、多入边、成环、指向无效、自环
- 编译：时间归一化（0:10 / 70s / +0:10）、单位归一化（原版 / unit*count）
- map_info 判定 + 写入 survivalWaves、无节点不写
- 写盘映射：waveGen type 映射成空、私有属性被剔除
- 类型限显辅助：is_survival_map
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rwmap.document.layer import ObjectLayer
from rwmap.document.map_document import MapDocument
from rwmap.document.map_object import MapObject
from rwmap.document.properties import (
    HIDDEN_EDITOR_PROPERTIES, WAVE_DISPATCH_MARK_PROPERTY,
    WAVE_DISPATCH_WAVE_PROPERTY, WAVE_SPAWN_PROXY_MARK_PROPERTY,
    WAVE_SPAWN_PROXY_SOURCE_PROPERTY, WAVE_GEN_TYPE, WAVE_LINK_PROPERTY,
    WAVE_MARK_PROPERTY, WAVE_NAME_PROPERTY, WAVE_NEXT_PROPERTY, WAVE_TIME_PROPERTY,
    WAVE_UNITS_PROPERTY,     WAVE_TABLE_MARK_PROPERTY, WAVE_TABLE_NEXT_PROPERTY,
    WAVE_TABLE_TEXT_PROPERTY, WAVE_TABLE_TYPE, PropertyBag,
)
from rwmap.rw import wave_gen as wg


def _bag(**kwargs) -> PropertyBag:
    return PropertyBag(values={k: str(v) for k, v in kwargs.items()})


def wave_obj(oid: int, name: str, time="", units="", next_="",
             ident=None, marked=True) -> MapObject:
    vals = {}
    if marked:
        vals[WAVE_MARK_PROPERTY] = "1"
    if time:
        vals[WAVE_TIME_PROPERTY] = time
    vals[WAVE_NAME_PROPERTY] = name
    if units:
        vals[WAVE_UNITS_PROPERTY] = units
    if next_:
        vals[WAVE_NEXT_PROPERTY] = next_
    if ident:
        vals["id"] = ident
    return MapObject(object_id=oid, name=name, type_=WAVE_GEN_TYPE,
                     shape="point", x=oid * 10, y=0, properties=PropertyBag(values=vals))


def doc_of(*objs, map_type="survival") -> MapDocument:
    all_objs = list(objs)
    info = MapObject(object_id=999, name="map_info", type_="", shape="rect",
                     x=0, y=0, properties=_bag(type=map_type))
    all_objs.append(info)
    return MapDocument(width=32, height=20,
                       layers=[ObjectLayer(name="Triggers", objects=all_objs)])


class RecognizeTest(unittest.TestCase):
    def test_type_and_mark_both_recognized(self):
        by_type = MapObject(object_id=1, name="波次1", type_=WAVE_GEN_TYPE,
                            shape="point", properties=PropertyBag())
        by_mark = MapObject(object_id=2, name="波次2", type_="", shape="point",
                            properties=_bag(**{WAVE_MARK_PROPERTY: "1"}))
        self.assertTrue(wg.is_wave_node(by_type))
        self.assertTrue(wg.is_wave_node(by_mark))
        normal = MapObject(object_id=3, name="x", type_="unitAdd", shape="point")
        self.assertFalse(wg.is_wave_node(normal))

    def test_private_props_hidden_from_game(self):
        for key in (WAVE_MARK_PROPERTY, WAVE_NAME_PROPERTY, WAVE_TIME_PROPERTY,
                    WAVE_UNITS_PROPERTY, WAVE_NEXT_PROPERTY):
            self.assertIn(key, HIDDEN_EDITOR_PROPERTIES)


class ValidateTest(unittest.TestCase):
    def test_single_node_ok(self):
        self.assertEqual(wg.validate_wave_chain(doc_of(wave_obj(1, "唯一波"))), [])

    def test_three_node_chain_ok(self):
        a = wave_obj(1, "第一波", next_="第二波")
        b = wave_obj(2, "第二波", next_="第三波")
        c = wave_obj(3, "第三波")
        self.assertEqual(wg.validate_wave_chain(doc_of(a, b, c)), [])

    def test_chain_by_id_ok(self):
        a = wave_obj(1, "第一波", next_="w2")
        b = wave_obj(2, "第二波", ident="w2")
        self.assertEqual(wg.validate_wave_chain(doc_of(a, b)), [])

    def test_no_nodes_ok(self):
        doc = doc_of()
        # 只有 map_info，没有波次节点
        self.assertEqual(wg.validate_wave_chain(doc), [])

    def test_isolated_node_reported(self):
        a = wave_obj(1, "第一波", next_="第二波")
        b = wave_obj(2, "第二波")
        c = wave_obj(3, "孤立波")
        problems = wg.validate_wave_chain(doc_of(a, b, c))
        self.assertTrue(any("孤立" in p or "断链" in p for p in problems), problems)

    def test_multiple_starts_reported(self):
        a = wave_obj(1, "甲")
        b = wave_obj(2, "乙")
        problems = wg.validate_wave_chain(doc_of(a, b))
        self.assertTrue(any("起点" in p for p in problems), problems)

    def test_fork_reported(self):
        a = wave_obj(1, "第一波", next_="第二波,第三波")
        b = wave_obj(2, "第二波")
        c = wave_obj(3, "第三波")
        problems = wg.validate_wave_chain(doc_of(a, b, c))
        self.assertTrue(any("分叉" in p for p in problems), problems)

    def test_multi_incoming_reported(self):
        a = wave_obj(1, "A", next_="C")
        b = wave_obj(2, "B", next_="C")
        c = wave_obj(3, "C")
        problems = wg.validate_wave_chain(doc_of(a, b, c))
        self.assertTrue(any("多入边" in p for p in problems), problems)

    def test_cycle_reported(self):
        a = wave_obj(1, "A", next_="B")
        b = wave_obj(2, "B", next_="A")
        problems = wg.validate_wave_chain(doc_of(a, b))
        self.assertTrue(any("环" in p for p in problems), problems)

    def test_invalid_target_reported(self):
        a = wave_obj(1, "A", next_="不存在")
        b = wave_obj(2, "B")
        problems = wg.validate_wave_chain(doc_of(a, b))
        self.assertTrue(any("找不到" in p for p in problems), problems)


class CompileTest(unittest.TestCase):
    def test_time_formats(self):
        self.assertEqual(wg._parse_time("0:10"), "0:10")
        self.assertEqual(wg._parse_time("+0:10"), "0:10")
        self.assertEqual(wg._parse_time("10s"), "0:10")
        self.assertEqual(wg._parse_time("70"), "1:10")
        self.assertEqual(wg._parse_time("1分30秒"), "1:30")

    def test_unit_formats(self):
        self.assertEqual(wg._parse_units("6 bugMelee,2 heavyTank"),
                         "6 bugMelee,2 heavyTank")
        self.assertEqual(wg._parse_units("bugMelee*6,heavyTank*2"),
                         "6 bugMelee,2 heavyTank")

    def test_compile_chain_order(self):
        a = wave_obj(1, "第一波", time="0:10", units="6 bugMelee,2 heavyTank", next_="第二波")
        b = wave_obj(2, "第二波", time="0:30", units="mechFlame*4", next_="第三波")
        c = wave_obj(3, "第三波", time="40s", units="2 experimentalDropship")
        text = wg.compile_survival_waves(doc_of(a, b, c))
        self.assertEqual(text.splitlines(), [
            "+0:10 第一波 - 6 bugMelee,2 heavyTank",
            "+0:30 第二波 - 4 mechFlame",
            "+0:40 第三波 - 2 experimentalDropship",
        ])

    def test_apply_writes_map_info(self):
        a = wave_obj(1, "第一波", time="10s", units="6 bugMelee")
        doc = doc_of(a)
        text = wg.apply_survival_waves(doc)
        info = wg.find_map_info(doc)
        self.assertEqual(info.properties.get("survivalWaves"), text)
        self.assertEqual(text, "+0:10 第一波 - 6 bugMelee")

    def test_no_wave_nodes_does_not_write(self):
        doc = doc_of()
        info = wg.find_map_info(doc)
        info.properties.set("survivalWaves", "手写内容")
        text = wg.apply_survival_waves(doc)
        self.assertEqual(text, "")
        self.assertEqual(info.properties.get("survivalWaves"), "手写内容")


class ModeTest(unittest.TestCase):
    def test_is_survival_map(self):
        self.assertTrue(wg.is_survival_map(doc_of(map_type="survival")))
        self.assertFalse(wg.is_survival_map(doc_of(map_type="skirmish")))


class WriteRoundtripTest(unittest.TestCase):
    """写盘验证：私有属性不进对象 property，重开后节点 / 连线能恢复。"""

    SRC = "<游戏目录1.15>/mods/maps/[p2]example_skirmish_(2p).tmx"

    def test_wave_props_not_written_to_object_but_layout_keeps_them(self):
        from lxml import etree
        from rwmap.io.tmx_writer import _write_object, _properties_for_write

        obj = wave_obj(1, "第一波", time="0:10", units="6 bugMelee",
                       next_="第二波")
        el = etree.Element("object")
        _write_object(el, obj)
        # 空 type 会让游戏 TriggerEngine 报 "no properties found"；
        # 写 point 才会被无条件跳过（jar 字节码确认）。
        self.assertEqual(el.get("type"), "point", "waveGen 写盘应为 point")
        self.assertIsNone(el.get("class"))
        xml = etree.tostring(el, encoding="unicode")
        for key in (WAVE_MARK_PROPERTY, WAVE_TIME_PROPERTY, WAVE_NAME_PROPERTY,
                    WAVE_UNITS_PROPERTY, WAVE_NEXT_PROPERTY):
            self.assertNotIn(f'name="{key}"', xml, f"{key} 不应写进对象属性")

        # 属性展开副本里也被剔除
        copy = _properties_for_write(obj.properties, obj)
        for key in (WAVE_MARK_PROPERTY, WAVE_NEXT_PROPERTY):
            self.assertNotIn(key, copy.values)

    def test_full_roundtrip(self):
        from pathlib import Path
        import tempfile
        from rwmap.io.tmx_reader import read_document
        from rwmap.io.tmx_writer import write_document

        src = Path(self.SRC)
        if not src.exists():
            self.skipTest("示例地图缺失")
        doc = read_document(src)
        layer = next((l for l in doc.layers
                      if getattr(l, "kind", "") == "object"), None)
        if layer is None:
            self.skipTest("示例地图无对象层")

        # 示例地图可能已被用来做过生存模板，里面本来就有波次节点。
        # 本测试只验证「新建 3 节点 → 写盘 → 重开」这一条链，先把已有波次节点摘掉，
        # 否则重开后会数出 6 个节点，误判成产品 bug。
        layer.objects[:] = [o for o in layer.objects if not wg.is_wave_node(o)]

        a = wave_obj(9001, "第一波", time="0:10", units="6 bugMelee", next_="第二波")
        b = wave_obj(9002, "第二波", time="0:30", units="2 heavyTank", next_="第三波")
        c = wave_obj(9003, "第三波", time="40s", units="1 mammothTank")
        for o in (a, b, c):
            o.type_ = ""   # 模拟磁盘重开时写盘映射成空 type
            layer.objects.append(o)
        layer.dirty = True

        out = Path(tempfile.gettempdir()) / "rwmap_wave_roundtrip.tmx"
        write_document(doc, out)
        xml = out.read_text(encoding="utf-8")
        self.assertIn("rwmap_layout", xml, "布局应写进地图根属性")
        self.assertIn(WAVE_NEXT_PROPERTY, xml, "波次连线数据应在布局里")
        self.assertNotIn(f'name="{WAVE_NEXT_PROPERTY}" value=', xml,
                         "波次私有属性不应作为对象属性出现")

        # 重开后 type_ 已退化为空，写盘必须靠私有标记补 type=point，
        # 否则游戏 TriggerEngine 会再报 "no properties found" desync。
        from lxml import etree as _ET
        out_root = _ET.parse(str(out)).getroot()
        wave_els = [o for o in out_root.iter("object")
                    if (o.get("name") or "") in ("第一波", "第二波", "第三波")]
        self.assertEqual(len(wave_els), 3)
        self.assertTrue(all(o.get("type") == "point" for o in wave_els),
                        "重开重存后波次节点 type 仍须为 point")

        doc2 = read_document(out)
        nodes = wg.collect_wave_nodes(doc2)
        self.assertEqual(len(nodes), 3, "重开后应恢复 3 个波次节点")
        self.assertEqual(wg.validate_wave_chain(doc2), [], "重开后链条应完整")
        text = wg.compile_survival_waves(doc2)
        self.assertEqual(text.splitlines(), [
            "+0:10 第一波 - 6 bugMelee",
            "+0:30 第二波 - 2 heavyTank",
            "+0:40 第三波 - 1 mammothTank",
        ])

    def test_read_old_map_auto_restores_empty_wave_type(self):
        """老地图：波次节点写盘是空 type + 私有标记。读盘应自动认回。

        这是 "no properties found" desync 二次复发的根因：只改别名表没用，
        因为重开后 type_ 已退化为空。必须在读盘时按私有标记恢复。
        """
        from pathlib import Path
        import tempfile
        from lxml import etree
        from rwmap.io.tmx_reader import read_document, _restore_editor_node_types
        from rwmap.io.tmx_writer import write_document

        src = Path(self.SRC)
        doc = read_document(src)
        layer = next(l for l in doc.layers if getattr(l, "kind", "") == "object")
        layer.objects[:] = [o for o in layer.objects if not wg.is_wave_node(o)]
        a = wave_obj(9101, "第一波", time="0:10", units="6 bugMelee")
        a.type_ = ""            # 模拟老地图里退化成空的 type
        a.visible = True        # 模拟早期脚本建成可见的点
        layer.objects.append(a)

        # 未走自动检测前：靠私有标记仍能认出节点，但 type_ 是空的
        self.assertEqual(len(wg.collect_wave_nodes(doc)), 1)
        self.assertEqual(a.type_, "")

        fixed = _restore_editor_node_types(doc)
        self.assertEqual(fixed, 1)
        self.assertEqual(a.type_, WAVE_GEN_TYPE)
        self.assertFalse(a.visible)

        out = Path(tempfile.gettempdir()) / "rwmap_autodetect_type.tmx"
        write_document(doc, out)
        root = etree.parse(str(out)).getroot()
        el = next(o for o in root.iter("object") if (o.get("name") or "") == "第一波")
        self.assertEqual(el.get("type"), "point", "自动检测修复后写盘应为 point")


class UITest(unittest.TestCase):
    """类型下拉限显 + 属性面板显示（真实 Qt widget，offscreen）。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_type_picker_survival_only(self):
        from rwmap.widgets.trigger_inspector import TriggerInspector, WAVE_GEN_TYPE

        ins = TriggerInspector()
        # 生存地图：下拉里应有波次生成器
        wave = wave_obj(1, "第一波")
        doc_s = doc_of(wave, map_type="survival")
        ins.set_context(doc_s, doc_s.layers[0])
        node = type("N", (), {"obj": wave, "tid": "1"})()
        ins.show_node(node)
        keys = [ins.f_type.itemData(i) for i in range(ins.f_type.count())]
        self.assertIn(WAVE_GEN_TYPE, keys)

        # 遭遇战地图：普通节点下拉里不应有波次生成器这一可选项
        normal = MapObject(object_id=2, name="普通触发", type_="unitAdd",
                           shape="point", properties=_bag(id="2"))
        doc_k = doc_of(normal, map_type="skirmish")
        ins.set_context(doc_k, doc_k.layers[0])
        node2 = type("N", (), {"obj": normal, "tid": "2"})()
        ins.show_node(node2)
        keys = [ins.f_type.itemData(i) for i in range(ins.f_type.count())]
        self.assertNotIn(WAVE_GEN_TYPE, keys)

    def test_wave_props_visible_in_inspector(self):
        from rwmap.widgets.trigger_inspector import TriggerInspector

        ins = TriggerInspector()
        wave = wave_obj(1, "第一波", time="0:10", units="6 bugMelee")
        doc = doc_of(wave, map_type="survival")
        ins.set_context(doc, doc.layers[0])
        node = type("N", (), {"obj": wave, "tid": "1"})()
        ins.show_node(node)
        rows = {k for k, _e, _h in ins._prop_rows}
        self.assertIn(WAVE_TIME_PROPERTY, rows)
        self.assertIn(WAVE_UNITS_PROPERTY, rows)
        # 名字就是顶上的「名字」字段（obj.name），不再单独露一个波次名称
        self.assertNotIn(WAVE_NAME_PROPERTY, rows)
        self.assertEqual(ins.f_name.text(), "第一波")
        # 标记 / 连线私有属性仍隐藏
        self.assertNotIn(WAVE_MARK_PROPERTY, rows)
        self.assertNotIn(WAVE_NEXT_PROPERTY, rows)


class WaveTableTest(unittest.TestCase):
    """回归：官方波次表节点（waveTable）。"""

    def _make(self, text, oid=50, marked=True):
        vals = {}
        if marked:
            vals[WAVE_TABLE_MARK_PROPERTY] = "1"
        if text:
            vals[WAVE_TABLE_TEXT_PROPERTY] = text
        obj = MapObject(object_id=oid, name="官方波次表", type_=WAVE_TABLE_TYPE,
                        shape="point", x=0, y=0, properties=PropertyBag(values=vals))
        obj.visible = False
        return obj

    def test_recognize_by_type_and_mark(self):
        by_type = MapObject(object_id=1, name="x", type_=WAVE_TABLE_TYPE)
        by_mark = MapObject(object_id=2, name="x", type_="",
                            properties=_bag(**{WAVE_TABLE_MARK_PROPERTY: "1"}))
        self.assertTrue(wg.is_wave_table_node(by_type))
        self.assertTrue(wg.is_wave_table_node(by_mark))
        normal = MapObject(object_id=3, name="x", type_="unitAdd")
        self.assertFalse(wg.is_wave_table_node(normal))
        # 波次生成器和波次表互不混淆
        self.assertFalse(wg.is_wave_node(by_type))

    def test_private_props_hidden(self):
        from rwmap.document.properties import (
            WAVE_TABLE_MARK_PROPERTY, WAVE_TABLE_NEXT_PROPERTY,
        )
        for key in (WAVE_TABLE_MARK_PROPERTY, WAVE_TABLE_TEXT_PROPERTY,
                    WAVE_TABLE_NEXT_PROPERTY):
            self.assertIn(key, HIDDEN_EDITOR_PROPERTIES)

    def test_text_and_apply(self):
        text = ("+0:10 第一波 - 6 bugMelee,2 heavyTank\n"
                "+0:40 最终波 - 2 experimentalDropship,6 heavyTank")
        doc = doc_of(self._make(text))
        self.assertEqual(wg.wave_table_text(doc), text)
        written = wg.apply_wave_table(doc)
        self.assertEqual(written, text)
        info = wg.find_map_info(doc)
        self.assertEqual(info.properties.get("survivalWaves"), text)

    def test_empty_text_does_not_write(self):
        doc = doc_of(self._make(""))
        self.assertEqual(wg.apply_wave_table(doc), "")
        info = wg.find_map_info(doc)
        self.assertNotIn("survivalWaves", info.properties.values)

    def test_write_mapping_and_visible(self):
        from rwmap.io.tmx_writer import GAME_TYPE_ALIASES, _properties_for_write
        self.assertEqual(GAME_TYPE_ALIASES.get(WAVE_TABLE_TYPE), "point")
        obj = self._make("+0:10 x - 1 tank")
        self.assertFalse(obj.visible)
        out = _properties_for_write(obj.properties, obj)
        for key in (WAVE_TABLE_MARK_PROPERTY, WAVE_TABLE_TEXT_PROPERTY,
                    WAVE_TABLE_NEXT_PROPERTY):
            self.assertNotIn(key, out.values)

    def test_type_only_in_survival(self):
        from rwmap.widgets.trigger_inspector import TriggerInspector
        from rwmap.document.properties import WAVE_TABLE_TYPE as WT
        ins = TriggerInspector()
        table = self._make("+0:10 x - 1 tank")
        doc_s = doc_of(table, map_type="survival")
        ins.set_context(doc_s, doc_s.layers[0])
        node = type("N", (), {"obj": table, "tid": "50"})()
        ins.show_node(node)
        keys = [ins.f_type.itemData(i) for i in range(ins.f_type.count())]
        self.assertIn(WT, keys)
        # 普通节点 + 遭遇战：下拉里不应有
        normal = MapObject(object_id=2, name="普通", type_="unitAdd",
                           properties=_bag(id="2"))
        doc_k = doc_of(normal, map_type="skirmish")
        ins.set_context(doc_k, doc_k.layers[0])
        node2 = type("N", (), {"obj": normal, "tid": "2"})()
        ins.show_node(node2)
        keys = [ins.f_type.itemData(i) for i in range(ins.f_type.count())]
        self.assertNotIn(WT, keys)

    def test_text_prop_visible_in_inspector(self):
        from rwmap.widgets.trigger_inspector import TriggerInspector
        ins = TriggerInspector()
        table = self._make("+0:10 x - 1 tank")
        doc = doc_of(table, map_type="survival")
        ins.set_context(doc, doc.layers[0])
        node = type("N", (), {"obj": table, "tid": "50"})()
        ins.show_node(node)
        rows = {k for k, _e, _h in ins._prop_rows}
        self.assertIn(WAVE_TABLE_TEXT_PROPERTY, rows)
        self.assertNotIn(WAVE_TABLE_MARK_PROPERTY, rows)


class NextWaveNameTest(unittest.TestCase):
    """右键插入波次时的自动命名：第一波 → 第二波 → 第三波 …"""

    def test_empty_starts_at_first(self):
        self.assertEqual(wg.next_wave_name(doc_of()), "第一波")

    def test_increments_from_existing(self):
        d = doc_of(wave_obj(1, "第一波"), wave_obj(2, "第二波"))
        self.assertEqual(wg.next_wave_name(d), "第三波")

    def test_arabic_numbering_recognized(self):
        d = doc_of(wave_obj(1, "第3波"))
        self.assertEqual(wg.next_wave_name(d), "第四波")

    def test_skips_existing_name(self):
        d = doc_of(wave_obj(1, "第一波"), wave_obj(2, "第三波"))
        # 最大编号 3 → 第四波
        self.assertEqual(wg.next_wave_name(d), "第四波")

    def test_title_is_wave_name(self):
        """编译用节点标题（顶上显示的字），私有波次名只兜底。"""
        obj = wave_obj(1, "开场潮", time="0:10", units="6 bugMelee")
        obj.properties.set(WAVE_NAME_PROPERTY, "旧名字")
        self.assertEqual(wg.compile_line(wg.WaveNode(obj, "1")),
                         "+0:10 开场潮 - 6 bugMelee")


class ModeSyncTest(unittest.TestCase):
    """地图模式切换时自动增删第一波节点。"""

    def test_ensure_first_wave_creates_hidden_node_once(self):
        d = doc_of(map_type="survival")
        obj = wg.ensure_first_wave(d)
        self.assertIsNotNone(obj)
        self.assertEqual(obj.name, "第一波")
        self.assertEqual(obj.type_, WAVE_GEN_TYPE)
        self.assertFalse(obj.visible)
        self.assertEqual(obj.properties.get(WAVE_TIME_PROPERTY), "0:10")
        self.assertEqual(len(wg.collect_wave_nodes(d)), 1)
        self.assertIsNone(wg.ensure_first_wave(d), "已有波次节点时不重复创建")
        self.assertEqual(len(wg.collect_wave_nodes(d)), 1)

    def test_ensure_first_wave_keeps_existing_nodes(self):
        existing = wave_obj(1, "第二波", time="0:30", units="2 heavyTank")
        d = doc_of(existing, map_type="survival")
        self.assertIsNone(wg.ensure_first_wave(d))
        self.assertEqual(len(wg.collect_wave_nodes(d)), 1)

    def test_hide_existing_wave_nodes_is_idempotent(self):
        gen = wave_obj(1, "第一波")
        gen.visible = True
        d = doc_of(gen, map_type="survival")
        self.assertEqual(wg.hide_wave_nodes(d), 1)
        self.assertFalse(gen.visible)
        self.assertEqual(wg.hide_wave_nodes(d), 0)

    def test_remove_wave_nodes_removes_gen_and_table(self):
        gen = wave_obj(1, "第一波")
        table = MapObject(object_id=2, name="官方波次表",
                          type_=WAVE_TABLE_TYPE, shape="point")
        d = doc_of(gen, table, map_type="skirmish")
        self.assertEqual(wg.remove_wave_nodes(d), 2)
        self.assertEqual(wg.collect_wave_nodes(d), [])
        self.assertEqual(wg.collect_wave_table_nodes(d), [])
        self.assertEqual(wg.remove_wave_nodes(d), 0)

    def test_empty_wave_is_not_compiled(self):
        empty = wave_obj(1, "第一波", time="0:10", units="")
        full = wave_obj(2, "第二波", time="0:30", units="2 heavyTank")
        empty.properties.set(WAVE_NEXT_PROPERTY, "第二波")
        d = doc_of(empty, full, map_type="survival")
        self.assertEqual(wg.compile_survival_waves(d).splitlines(),
                         ["+0:30 第二波 - 2 heavyTank"])


class WaveDispatchTest(unittest.TestCase):
    def test_wave_link_becomes_real_game_trigger(self):
        gen = wave_obj(1, "第一波", time="0:10", units="1 tank")
        msg = MapObject(object_id=2, name="播报第一波", type_="basic",
                        shape="point", properties=_bag(id="msg"))
        ap = MapObject(object_id=3, name="attack_point", shape="point",
                       properties=_bag(id="ap"))
        gen.properties.set(WAVE_LINK_PROPERTY, "Triggers::2, Triggers::3")
        d = doc_of(gen, msg, ap)

        self.assertEqual(wg.sync_wave_dispatch_triggers(d), 1)
        dispatch = next(o for o in d.layers[0].objects if wg._is_dispatch(o))
        self.assertEqual(dispatch.type_, "basic")
        self.assertFalse(dispatch.visible)
        self.assertEqual(dispatch.properties.get("delay"), "10s")
        # attack_point 不是触发器，不能写进 alsoActivate 否则游戏报 target not found
        self.assertEqual(dispatch.properties.get("alsoActivate"), "msg")
        self.assertEqual(dispatch.properties.get(WAVE_DISPATCH_WAVE_PROPERTY),
                         "第一波")
        self.assertIn(WAVE_DISPATCH_MARK_PROPERTY, HIDDEN_EDITOR_PROPERTIES)
        self.assertIn(WAVE_DISPATCH_WAVE_PROPERTY, HIDDEN_EDITOR_PROPERTIES)

        # 幂等：重存不会叠加调度器
        self.assertEqual(wg.sync_wave_dispatch_triggers(d), 1)
        self.assertEqual(len([o for o in d.layers[0].objects
                              if wg._is_dispatch(o)]), 1)

        # 改时间 / 改关联后重建
        gen.properties.set(WAVE_TIME_PROPERTY, "0:25")
        gen.properties.set(WAVE_LINK_PROPERTY, "Triggers::2")
        wg.sync_wave_dispatch_triggers(d)
        dispatch = next(o for o in d.layers[0].objects if wg._is_dispatch(o))
        self.assertEqual(dispatch.properties.get("delay"), "25s")
        self.assertEqual(dispatch.properties.get("alsoActivate"), "msg")

        # 只关联 attack_point 时不生成调度器
        gen.properties.set(WAVE_LINK_PROPERTY, "Triggers::3")
        self.assertEqual(wg.sync_wave_dispatch_triggers(d), 0)
        self.assertEqual([o for o in d.layers[0].objects
                          if wg._is_dispatch(o)], [])

    def test_dispatch_delay_uses_cumulative_wave_time(self):
        w1 = wave_obj(1, "第一波", time="0:10", units="1 tank")
        w2 = wave_obj(2, "第二波", time="0:30", units="1 tank")
        w1.properties.set(WAVE_NEXT_PROPERTY, "第二波")
        w2.properties.set(WAVE_LINK_PROPERTY, "Triggers::3")
        msg = MapObject(object_id=3, name="播报第二波", type_="basic",
                        shape="point", properties=_bag(id="msg2"))
        d = doc_of(w1, w2, msg)
        wg.sync_wave_dispatch_triggers(d)
        dispatch = next(o for o in d.layers[0].objects if wg._is_dispatch(o))
        # 10 + 30 = 40 秒，和游戏 Wave.e 的累计口径一致
        self.assertEqual(dispatch.properties.get("delay"), "40s")
        self.assertEqual(dispatch.properties.get("alsoActivate"), "msg2")

    def test_attack_point_unitadd_gets_hidden_spawn_proxy(self):
        """游戏按名字跳过 attack_point 触发器，必须复制影子刷兵器。"""
        from lxml import etree

        from rwmap.io.tmx_writer import _write_object

        gen = wave_obj(1, "第一波", time="0:10", units="1 tank")
        points = []
        for idx in range(3):
            points.append(MapObject(
                object_id=2 + idx, name="attack_point", type_="unitAdd",
                shape="point", x=100 + idx * 20, y=200, width=20, height=20,
                properties=_bag(spawnUnits="bugMeleeT31*100", warmup="0", id=f"ap{idx}"),
            ))
        team_info = MapObject(object_id=20, name="team_1_info", type_="team_info",
                              shape="rect", properties=_bag(team="1", ai="survival"))
        refs = ", ".join(f"Triggers::{p.object_id}" for p in points)
        gen.properties.set(WAVE_LINK_PROPERTY, refs)
        d = doc_of(gen, *points, team_info)

        self.assertEqual(wg.sync_wave_dispatch_triggers(d), 1)
        dispatch = next(o for o in d.layers[0].objects if wg._is_dispatch(o))
        proxies = [o for o in d.layers[0].objects if wg._is_spawn_proxy(o)]
        self.assertEqual(len(proxies), 3)

        ids = {o.properties.get("id") for o in proxies}
        self.assertEqual(set((dispatch.properties.get("alsoActivate") or "").split(", ")), ids)
        for src, proxy in zip(points, proxies):
            self.assertNotEqual(proxy.name, "attack_point")
            self.assertEqual(proxy.type_, "unitAdd")
            self.assertEqual(proxy.x, src.x)
            self.assertEqual(proxy.y, src.y)
            self.assertFalse(proxy.visible)
            self.assertEqual(proxy.properties.get("spawnUnits"), "bugMeleeT31*100")
            self.assertEqual(proxy.properties.get("team"), "1")
            self.assertEqual(proxy.properties.get(WAVE_SPAWN_PROXY_SOURCE_PROPERTY),
                             str(src.object_id))
            # 原对象仍是官方刷兵点，没被改名 / 补 team
            self.assertEqual(src.name, "attack_point")
            self.assertIsNone(src.properties.get("team"))

        self.assertIn(WAVE_SPAWN_PROXY_MARK_PROPERTY, HIDDEN_EDITOR_PROPERTIES)
        self.assertIn(WAVE_SPAWN_PROXY_SOURCE_PROPERTY, HIDDEN_EDITOR_PROPERTIES)

        # 幂等：再存一次仍是 3 份，不会叠加
        self.assertEqual(wg.sync_wave_dispatch_triggers(d), 1)
        self.assertEqual(len([o for o in d.layers[0].objects
                              if wg._is_spawn_proxy(o)]), 3)

        # 私有标记不漏进游戏 XML，真正的刷兵属性写进去了
        root = etree.Element("objectgroup")
        for p in proxies:
            _write_object(etree.SubElement(root, "object"), p)
        text = etree.tostring(root, encoding="unicode")
        self.assertNotIn(WAVE_SPAWN_PROXY_MARK_PROPERTY, text)
        self.assertNotIn(WAVE_SPAWN_PROXY_SOURCE_PROPERTY, text)
        self.assertEqual(text.count('name="spawnUnits"'), 3)
        self.assertEqual(text.count('value="bugMeleeT31*100"'), 3)
        self.assertEqual(text.count('name="team"'), 3)

        # 影子触发器不出现在节点图
        from rwmap.widgets.trigger_graph_dock import collect_graph
        graph = collect_graph(d)
        self.assertFalse(any(o is p for p in proxies
                             for o in (n.obj for n in graph.nodes.values())))


class AttackPointRepairTest(unittest.TestCase):
    """整合体改名把 attack_point 冲掉 → 游戏第一波空列表闪退。"""

    def test_renamed_spawn_points_are_restored_on_save(self):
        """被波次关联引用、type=unitAdd 的对象会被改回 attack_point。"""
        gen = wave_obj(1, "第一波", time="0:05", units="6 bugMelee")
        pts = [
            MapObject(
                object_id=10 + i, name="刷虫子", type_="unitAdd",
                shape="point", x=100 + i * 20, y=200, width=20, height=20,
                properties=_bag(spawnUnits="bugMeleeT31*100", warmup="0", id="3"),
            )
            for i in range(3)
        ]
        gen.properties.set(WAVE_LINK_PROPERTY,
                           ", ".join(f"Triggers::{p.object_id}" for p in pts))
        d = doc_of(gen, *pts)

        self.assertEqual(wg.ensure_attack_points(d), 3)
        self.assertTrue(all(p.name == "attack_point" for p in pts))
        # 幂等
        self.assertEqual(wg.ensure_attack_points(d), 0)

        # 修复后影子刷兵器正常生成，delay 与关联照旧
        self.assertEqual(wg.sync_wave_dispatch_triggers(d), 1)
        proxies = [o for o in d.layers[0].objects if wg._is_spawn_proxy(o)]
        self.assertEqual(len(proxies), 3)

    def test_no_wave_no_repair(self):
        """没有波次节点的普通地图不去改用户的对象名字。"""
        obj = MapObject(object_id=5, name="刷虫子", type_="unitAdd",
                        shape="point", properties=_bag(spawnUnits="1 tank"))
        d = doc_of(obj)
        self.assertEqual(wg.ensure_attack_points(d), 0)
        self.assertEqual(obj.name, "刷虫子")

    def test_existing_attack_point_keeps_others_untouched(self):
        """已有任何 attack_point 时不再动别的名字。"""
        ap = MapObject(object_id=5, name="attack_point", type_="unitAdd",
                       shape="point", properties=_bag(spawnUnits="1 tank"))
        other = MapObject(object_id=6, name="刷虫子", type_="unitAdd",
                          shape="point", properties=_bag(spawnUnits="1 tank"))
        d = doc_of(wave_obj(1, "第一波", time="0:05", units="1 tank"), ap, other)
        self.assertEqual(wg.ensure_attack_points(d), 0)
        self.assertEqual(other.name, "刷虫子")

    def test_renamed_points_without_links_still_repaired(self):
        """没有连线时兜底：所有 unitAdd 对象都恢复成 attack_point。"""
        pts = [
            MapObject(object_id=10 + i, name="刷虫子", type_="unitAdd",
                      shape="point", properties=_bag(spawnUnits="1 tank"))
            for i in range(2)
        ]
        d = doc_of(wave_obj(1, "第一波", time="0:05", units="1 tank"), *pts)
        self.assertEqual(wg.ensure_attack_points(d), 2)
        self.assertTrue(all(p.name == "attack_point" for p in pts))


if __name__ == "__main__":
    unittest.main()
