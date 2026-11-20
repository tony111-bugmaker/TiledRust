"""回归：地形过渡覆盖（含 Lava↔Stone 岩浆-岩石过渡）。

探针版本见 rwmap/probe/probe_transition_coverage.py，可输出完整「已实现/未实现」清单。
本测试锁定若干关键过渡，防止规则解析/归一/匹配逻辑回归导致过渡失效。
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402

from rwmap.document.gid import GID_MASK  # noqa: E402
from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.rw.automapping import AutoMapper, normalize_marker_gid  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402

W = H = 12


class TransitionCoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile("1.15")
        cls.mapper = AutoMapper(cls.profile)
        cls.mapper.load()

    def _transition(self, left: int, right: int) -> dict[int, int]:
        """构造 left|right 边界，跑自动映射，返回非输入地形的过渡瓦片计数。"""
        d = MapDocument(width=W, height=H)
        s = TileLayer(name="set", width=W, height=H, cells=[0] * (W * H))
        g = TileLayer(name="Ground", width=W, height=H, cells=[0] * (W * H))
        for y in range(H):
            for x in range(W):
                s.cells[s.index(x, y)] = left if x < W // 2 else right
        d.layers = [s, g]
        d.rebuild_index()

        changes = self.mapper.compute(d, only_diff=False)
        for target, entries in changes.items():
            layer = d.layer_by_name(target)
            if layer:
                for col, row, gid in entries:
                    layer.cells[layer.index(col, row)] = gid

        out: dict[int, int] = {}
        for v in g.cells:
            v &= GID_MASK
            if v and v not in (left, right):
                out[v] = out.get(v, 0) + 1
        return out

    def test_ruleset_selftest_all_tilesets(self):
        """把规则图自身图集全部导入、用其 input_set 自测，所有期望输出格都应复现。

        这是最严格的验证：直接用官方规则图的输入/输出布局，覆盖该文件定义的
        每一条过渡。用户反馈「没导入图集时以为岩浆岩过渡没实现」，本测试锁住
        导入全部图集后 Stone-Lava-Ridge(989-1003) 期望格 100% 产出。
        """
        import copy
        from pathlib import Path
        from rwmap.io.tmx_reader import read_document

        rulemap = Path(self.profile.mods_maps) / "rules" / "ruleset1.tmx"
        if not rulemap.is_file():
            self.skipTest(f"缺少规则图: {rulemap}")
        doc = read_document(rulemap, self.profile)
        ins = doc.layer_by_name("input_set")
        out = doc.layer_by_name("output_Ground")

        work = copy.deepcopy(doc)
        setl = TileLayer(name="set", width=work.width, height=work.height,
                         cells=[0] * (work.width * work.height))
        for i in range(len(setl.cells)):
            setl.cells[i] = int(ins.cells[i]) & 0xFFFFFFFF
        work.layers = [l for l in work.layers
                       if (not l.name.startswith("output"))
                       and l.name not in ("set", "Ground", "Items")]
        ground = TileLayer(name="Ground", width=work.width, height=work.height,
                           cells=[0] * (work.width * work.height))
        work.layers.extend([setl, ground])
        work.rebuild_index()

        changes = self.mapper.compute(work, only_diff=False)
        for target, entries in changes.items():
            layer = work.layer_by_name(target)
            if layer:
                for col, row, gid in entries:
                    layer.cells[layer.index(col, row)] = gid

        lava_cells = [(x, y, int(out.get(x, y)) & GID_MASK)
                      for y in range(work.height) for x in range(work.width)
                      if 989 <= (int(out.get(x, y)) & GID_MASK) <= 1003]
        self.assertGreater(len(lava_cells), 0, "规则图中未找到岩-岩浆过渡格")
        ok = sum(1 for x, y, e in lava_cells
                 if (int(ground.get(x, y)) & GID_MASK) == e)
        self.assertEqual(ok, len(lava_cells),
                         f"导入全部图集后岩-岩浆过渡仅 {ok}/{len(lava_cells)} 产出")

    def test_lava_stone_transition_implemented(self):
        """岩浆-岩石过渡（Stone Lava - Ridge）必须能产出，探针观察到用户怀疑失效。"""
        # 规范标记：Stone=945, Lava=978
        forward = self._transition(945, 978)
        reverse = self._transition(978, 945)
        self.assertTrue(forward, "Stone|Lava 边界未产出任何过渡瓦片")
        self.assertTrue(reverse, "Lava|Stone 边界未产出任何过渡瓦片")
        # 过渡瓦片必须落在 Stone Lava - Ridge (989-1003)
        self.assertTrue(all(989 <= g <= 1003 for g in forward),
                        f"Stone|Lava 产出非预期瓦片: {sorted(forward)}")
        self.assertTrue(all(989 <= g <= 1003 for g in reverse),
                        f"Lava|Stone 产出非预期瓦片: {sorted(reverse)}")

    def test_lava_stone_rule_count(self):
        """官方规则应定义足量岩-岩浆过渡规则（输入同时含 945 与 978）。"""
        rules = [r for r in self.mapper.rules
                 if {945, 978} <= {g for _dx, _dy, g, _t in r.inputs}]
        self.assertGreaterEqual(len(rules), 10,
                                f"岩-岩浆过渡规则过少: {len(rules)}")

    def test_canonical_markers_for_stone_lava(self):
        """首格必须归一到规则认可的规范标记，否则过渡规则永远匹配不上。"""
        canon = self.mapper.canonical_marker_gids
        self.assertEqual(normalize_marker_gid(None, 938, canon), 945)
        self.assertEqual(normalize_marker_gid(None, 974, canon), 978)
        self.assertEqual(normalize_marker_gid(None, 945, canon), 945)
        self.assertEqual(normalize_marker_gid(None, 978, canon), 978)

    def test_known_transitions_implemented(self):
        """若干官方地图实际存在的过渡不能回归为「无产出」。"""
        # (左, 右, 该过渡 tileset 的 gid 组)——相邻两格为一对，单方向只产出其一
        cases = [
            (1, 36, {218, 220}),       # Mountain↔Snow
            (1, 126, {836, 838}),      # Mountain↔LongGrass
            (126, 176, {353, 355}),    # LongGrass↔Dirt
            (770, 945, {1007, 1009}),  # Sand↔Stone
            (186, 945, {1163, 1165}),  # ShallowWater↔Stone
        ]
        for left, right, expect in cases:
            with self.subTest(left=left, right=right):
                out = self._transition(left, right)
                self.assertTrue(expect & out.keys(),
                                f"{left}|{right} 未产出期望过渡 {sorted(expect)}: {sorted(out)}")


if __name__ == "__main__":
    unittest.main()
