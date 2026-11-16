"""回归：外部 .tsx 省略 tilecount/columns 时，GID 区间必须由图集尺寸补全。

历史 bug：这些 tileset 的 count=0，导致草地/深棕土块全部画成紫色占位图。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from rwmap.document.gid import split_gid
from rwmap.io.tmx_reader import read_document
from rwmap.rw.version import load_profile
from rwmap.tilesets.image_loader import get_tile_image

SAMPLE = Path("<游戏目录1.15>/mods/maps/[p2]example_skirmish_(2p).tmx")


class TilesetCountsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SAMPLE.is_file():
            raise unittest.SkipTest(f"缺少示例地图: {SAMPLE}")
        cls.doc = read_document(SAMPLE, load_profile("1.15"))

    def test_every_tileset_has_count(self):
        for ts in self.doc.tilesets:
            self.assertGreater(ts.count, 0, f"{ts.name}(firstgid={ts.firstgid}) count=0")
            self.assertGreater(ts.columns, 0, f"{ts.name} columns=0")

    def test_firstgid_ranges_are_contiguous(self):
        sets = sorted(self.doc.tilesets, key=lambda t: t.firstgid)
        for ts, nxt in zip(sets, sets[1:]):
            self.assertEqual(ts.last_gid + 1, nxt.firstgid,
                             f"{ts.name} 末尾 {ts.last_gid} 与下一图块集 {nxt.name} 不连续")

    def test_all_gids_resolve(self):
        doc = self.doc
        unresolved = set()
        for layer in doc.tile_layers():
            for raw in layer.cells:
                gid = split_gid(raw)[0]
                if gid and doc.local_tile(gid) is None:
                    unresolved.add(gid)
        self.assertEqual(unresolved, set(), f"未解析 GID: {sorted(unresolved)[:10]}")

    def test_grass_is_green_dirt_is_dark_brown(self):
        doc = self.doc
        grass = doc.tileset_for_gid(126)
        dirt = doc.tileset_for_gid(176)
        self.assertIsNotNone(grass)
        self.assertIsNotNone(dirt)
        g_img = get_tile_image(grass, 0, doc.resolver)
        d_img = get_tile_image(dirt, 0, doc.resolver)
        self.assertIsNotNone(g_img)
        self.assertIsNotNone(d_img)
        gr, gg, gb = g_img.getpixel((0, 0))[:3]
        dr, dg, db = d_img.getpixel((0, 0))[:3]
        self.assertGreater(gg, gr + 20, "草地应为绿色")
        self.assertGreater(gg, gb + 20, "草地应为绿色")
        self.assertGreater(dr, dg, "深棕土块 R>G")
        self.assertGreater(dg, db, "深棕土块 G>B")
        self.assertLess(dr, 160, "深棕土块应是暗棕色，不是浅粉")


if __name__ == "__main__":
    unittest.main()
