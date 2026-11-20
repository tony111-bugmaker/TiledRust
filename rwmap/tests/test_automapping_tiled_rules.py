"""回归：照搬 Tiled rules.txt 语义（嵌套 txt / [glob] 文件名过滤器 / 注释）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rwmap.rw.automapping import AutoMapper  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402


class RulesTxtSemanticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile("1.15")
        cls.rules_dir = Path(cls.profile.mods_maps) / "rules"
        cls.r1 = cls.rules_dir / "ruleset1.tmx"
        cls.r2 = cls.rules_dir / "ruleset2.tmx"
        if not cls.r1.is_file() or not cls.r2.is_file():
            raise unittest.SkipTest("缺少官方规则图")

    def _mapper(self, txt: Path, map_name=None):
        m = AutoMapper(self.profile, txt)
        m.target_map_name = map_name
        m.load()
        return m

    def test_nested_txt_recurses(self):
        """rules.txt 里引用另一个 .txt 时递归加载其中的规则图。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub = root / "sub"
            sub.mkdir()
            # 把两张规则图链到临时目录（相对路径按各自 txt 所在目录解析）
            (sub / "r1.tmx").write_bytes(self.r1.read_bytes())
            (root / "inner.txt").write_text("./sub/r1.tmx\n", encoding="utf-8")
            outer = root / "rules.txt"
            outer.write_text("# 顶层注释\n// 另一行注释\n./inner.txt\n", encoding="utf-8")
            m = self._mapper(outer)
            self.assertTrue(m.loaded)
            self.assertEqual(len(m.rule_files), 1, m.errors)
            self.assertTrue(m.rule_files[0].endswith("r1.tmx"))

    def test_glob_filter_selects_rules_by_map_name(self):
        """[glob] 之后的规则图只对匹配文件名的地图生效，[*] 恢复全部。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.tmx").write_bytes(self.r1.read_bytes())
            (root / "b.tmx").write_bytes(self.r2.read_bytes())
            txt = root / "rules.txt"
            txt.write_text(
                "[a*]\n./a.tmx\n[*]\n./b.tmx\n", encoding="utf-8")
            # 地图名 a_测试 → 只命中 a.tmx + 恢复后的 b.tmx = 2 张
            m = self._mapper(txt, "a_测试.tmx")
            self.assertEqual(len(m.rule_files), 2, m.errors)
            # 地图名 xyz → a.tmx 被过滤，只剩 [*] 后的 b.tmx
            m2 = self._mapper(txt, "xyz.tmx")
            self.assertEqual(len(m2.rule_files), 1, m2.errors)
            self.assertTrue(m2.rule_files[0].endswith("b.tmx"))

    def test_no_filter_when_map_name_none(self):
        """target_map_name=None 时不按过滤器裁剪，保持旧行为。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.tmx").write_bytes(self.r1.read_bytes())
            txt = root / "rules.txt"
            txt.write_text("[never*]\n./a.tmx\n", encoding="utf-8")
            m = self._mapper(txt)  # map_name=None
            self.assertEqual(len(m.rule_files), 1, m.errors)

    def test_cycle_guard(self):
        """嵌套 txt 循环引用不应死循环，应记录错误并退出。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            txt = root / "rules.txt"
            txt.write_text("./rules.txt\n", encoding="utf-8")
            m = self._mapper(txt)
            self.assertTrue(any("循环引用" in e for e in m.errors), m.errors)


_MATCHTYPE_TMX = '''<?xml version="1.0" encoding="UTF-8"?>
<map version="1.0" orientation="orthogonal" width="3" height="3" tilewidth="20" tileheight="20">
 <tileset firstgid="1" name="T" tilewidth="20" tileheight="20" tilecount="2" columns="2">
  <image source="t.png" width="40" height="20"/>
  <tile id="1"><properties><property name="MatchType" value="Empty"/></properties></tile>
 </tileset>
 <layer name="regions" width="3" height="3"><data encoding="csv">
0,0,0,
0,1,0,
0,0,0
</data></layer>
 <layer name="input_set" width="3" height="3"><data encoding="csv">
0,0,0,
0,2,0,
0,0,0
</data></layer>
 <layer name="output_Ground" width="3" height="3"><data encoding="csv">
0,0,0,
0,1,0,
0,0,0
</data></layer>
</map>'''


class MatchTypeSemanticsTest(unittest.TestCase):
    """照搬 Tiled 新版 tile 属性 MatchType / StrictEmpty / IgnoreFlip。"""

    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile("1.15")

    @classmethod
    def _make_mapper(cls, root: Path, tmx_text: str):
        (root / "rule.tmx").write_text(tmx_text, encoding="utf-8")
        (root / "rules.txt").write_text("./rule.tmx\n", encoding="utf-8")
        m = AutoMapper(cls.profile, root / "rules.txt")
        m.load()
        return m

    @staticmethod
    def _workdoc(set_center: int):
        from rwmap.document.layer import TileLayer
        from rwmap.document.map_document import MapDocument
        d = MapDocument(width=3, height=3)
        s = TileLayer(name="set", width=3, height=3, cells=[0] * 9)
        g = TileLayer(name="Ground", width=3, height=3, cells=[0] * 9)
        s.cells[4] = set_center
        d.layers = [s, g]
        d.rebuild_index()
        return d

    def test_matchtype_empty_matches_only_empty_cell(self):
        """input tile 的 MatchType=Empty 只匹配工作地图的空格。"""
        with tempfile.TemporaryDirectory() as td:
            m = self._make_mapper(Path(td), _MATCHTYPE_TMX)
            self.assertEqual(len(m.rules), 1, m.errors)
            self.assertTrue(any(s[2] == "empty" for s in m.rules[0].any_of),
                            m.rules[0].any_of)
            changes_empty = m.compute(self._workdoc(0), only_diff=False)
            changes_set = m.compute(self._workdoc(126), only_diff=False)
            written_empty = {(x, y) for x, y, _g in changes_empty.get("Ground", [])}
            written_set = {(x, y) for x, y, _g in changes_set.get("Ground", [])}
            # 中心格 (1,1) 空输入时匹配、非空时不匹配
            self.assertIn((1, 1), written_empty)
            self.assertNotIn((1, 1), written_set)

    def test_ignore_flip_mask_parsed(self):
        """IgnoreHorizontalFlip 图层属性 → flip mask 去掉 H 位。"""
        from rwmap.document.gid import FLAG_H, FLAG_V, FLAG_D
        from rwmap.document.layer import TileLayer
        from rwmap.document.properties import PropertyBag
        layer = TileLayer(name="input_set", width=1, height=1, cells=[0])
        layer.properties = PropertyBag()
        layer.properties.set("IgnoreHorizontalFlip", True)
        from rwmap.rw.automapping import _layer_flip_mask
        mask = _layer_flip_mask(layer)
        self.assertFalse(mask & FLAG_H)
        self.assertTrue(mask & FLAG_V)
        self.assertTrue(mask & FLAG_D)


if __name__ == "__main__":
    unittest.main()


_VARIANT_TMX = '''<?xml version="1.0" encoding="UTF-8"?>
<map version="1.0" orientation="orthogonal" width="3" height="3" tilewidth="20" tileheight="20">
 <tileset firstgid="1" name="T" tilewidth="20" tileheight="20" tilecount="3" columns="3">
  <image source="t.png" width="60" height="20"/>
 </tileset>
 <layer name="regions" width="3" height="3"><data encoding="csv">
0,0,0,
0,1,0,
0,0,0
</data></layer>
 <layer name="input_set" width="3" height="3"><data encoding="csv">
0,0,0,
0,1,0,
0,0,0
</data></layer>
 <layer name="output_Ground" width="3" height="3"><data encoding="csv">
0,0,0,
0,1,0,
0,0,0
</data></layer>
 <layer name="output2_Ground" width="3" height="3"><data encoding="csv">
0,0,0,
0,2,0,
0,0,0
</data></layer>
</map>'''


class RandomVariantTest(unittest.TestCase):
    """照搬 Tiled 随机输出：默认关闭保复现，开启后按 seed 确定性选择。"""

    def _mapper(self, rand, seed):
        from rwmap.rw.automapping import AutoMapper
        p = load_profile("1.15")
        m = AutoMapper(p, None)
        m.rules = []
        m.loaded = True
        # 直接挂一个内存构造的规则（两个 variant：gid 1 和 gid 2）
        from rwmap.rw.automapping import Rule
        r = Rule(name="v#0", w=1, h=1, outputs={"Ground": [[(0, 0, 1)], [(0, 0, 2)]]},
                 inputs=[(0, 0, 1, "set")], order=("v", 0, 0))
        r.finish()
        m.rules = [r]
        m.match_in_order = False
        m.random_variants = rand
        m.random_seed = seed
        m._index_cache = None
        return m

    def _out_gid(self, m):
        from rwmap.document.layer import TileLayer
        from rwmap.document.map_document import MapDocument
        d = MapDocument(width=1, height=1)
        s = TileLayer(name="set", width=1, height=1, cells=[1])
        g = TileLayer(name="Ground", width=1, height=1, cells=[0])
        d.layers = [s, g]
        d.rebuild_index()
        ch = m.compute(d, only_diff=False)
        return ch.get("Ground", [(0, 0, None)])[0][2]

    def test_default_uses_first_variant(self):
        self.assertEqual(self._out_gid(self._mapper(False, 12345)), 1)

    def test_random_is_deterministic_per_seed(self):
        a = self._out_gid(self._mapper(True, 12345))
        b = self._out_gid(self._mapper(True, 12345))
        self.assertEqual(a, b, "同 seed 结果必须一致")
        # 找一个能选出 variant2 的 seed，证明随机真的生效
        picked = {self._out_gid(self._mapper(True, s)) for s in range(1, 40)}
        self.assertIn(2, picked, "随机输出从未选中第二个 variant")
