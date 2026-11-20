"""gid 空间错位回归：真实地图图集布局与规则图不同时，落笔/匹配/输出须正确。

bug 背景：工作图 Lava 图集首格 941（规则图 974），用户画 Lava+4=gid 945，
而 945 恰好是规则图 Stone 的规范标记。旧引擎用绝对 gid 匹配，把岩浆当石头，
输出规则图 Stone-Lava-Ridge 989-1003 —— 在工作图里那是 LongGrass Ridge，
表现为一整排「黑色獠牙」。修复：按图集名+相对索引在两套 gid 空间间投影。
"""
import os
import copy
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rwmap.io.tmx_reader import read_document
from rwmap.rw.automapping import AutoMapper
from rwmap.rw.version import load_profile


LAVA_MAP = Path(r"<游戏目录1.15>\assets\maps\skirmish\[z;p10]Large Lava Divide (10p).tmx")


class GidSpaceProjectionTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile("1.15")
        cls.mapper = AutoMapper(cls.profile)
        cls.mapper.load()
        if LAVA_MAP.is_file():
            cls.work = read_document(LAVA_MAP, cls.profile)
        else:
            cls.work = None

    def setUp(self):
        if self.work is None:
            self.skipTest(f"测试地图不存在: {LAVA_MAP}")

    def test_lava_tiles_normalize_to_work_map_canonical(self):
        """工作图 Lava 任意格都归一到工作图规范标记 941+4=945。"""
        for gid in (941, 942, 945, 961):
            self.assertEqual(self.mapper.normalize_marker(self.work, gid), 945,
                             f"Lava+{gid - 941} 应归一到 945")

    def test_normalize_does_not_mistake_lava_for_stone(self):
        """关键回归：945 在工作图是 Lava+4，不能被当成规则图的 Stone 标记。"""
        self.assertEqual(self.mapper.normalize_marker(self.work, 945), 945)
        # 工作图根本没 Stone 图集，规范标记 945 只能来自 Lava
        stone_names = [ts.name for ts in self.work.tilesets
                       if (ts.name or "").lower() == "stone"]
        self.assertEqual(stone_names, [])

    def test_lava_paint_no_rock_cliff_artifacts(self):
        """画 Lava+4（旧 bug 触发格）不再产出 Stone-Lava-Ridge 獠牙瓦片。"""
        d = copy.deepcopy(self.work)
        sl, gl = d.layer_by_name("set"), d.layer_by_name("Ground")
        x0, y0, sz = 50, 50, 8
        for y in range(y0, y0 + sz):
            for x in range(x0, x0 + sz):
                sl.cells[sl.index(x, y)] = 945
                gl.cells[gl.index(x, y)] = 0
        changes = self.mapper.compute(d, area=(x0, y0, x0 + sz - 1, y0 + sz - 1),
                                      only_diff=False)
        ground_gids = [g for x, y, g in changes.get("Ground", [])
                       if 0 <= x < d.width and 0 <= y < d.height]
        artifacts = [g for g in ground_gids if 989 <= g <= 1003]
        self.assertEqual(artifacts, [],
                         f"不应产出 Stone-Lava-Ridge 獠牙瓦片: {artifacts}")
        self.assertIn(945, ground_gids, "岩浆本体瓦片应正常产出")

    def test_conflicting_missing_tileset_gid_is_dropped(self):
        """输出 mapper：缺图集且 gid 落在工作图别的图集区间时必须丢弃。"""
        from rwmap.rw.automapping import _doc_tileset_ranges, _make_gid_mapper
        src = self.mapper.rules[0].source_tilesets
        omap = _make_gid_mapper(src, _doc_tileset_ranges(self.work),
                                keep_missing=False)
        # 989 = 规则图 Stone-Lava-Ridge+0，工作图这里是 LongGrass Ridge，
        # 必须丢弃（返回 None），不能写错瓦片。
        self.assertIsNone(omap(989))
        self.assertIsNone(omap(997))
        # 而工作图真实存在的图集（Lava 978->945）应正常映射
        self.assertEqual(omap(978), 945)


if __name__ == "__main__":
    unittest.main()
