"""回归：官方 rules.txt automapping 规则、Ground 复现率、局部映射与 UI 开关。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

# 官方 ruleset1 里水/草、山/草过渡的「内凹 / 外凸拐角」瓦片。
# 这些瓦片只由 L 形（2x2 缺一角）规则产出，是外接矩形解析错误的直接受害者。
CORNER_GIDS = frozenset({812, 814, 818, 820, 845, 847, 851, 853,
                         919, 920, 922, 923, 925})


class AutoMappingEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rwmap.document.layer import TileLayer
        from rwmap.io.tmx_reader import read_document
        from rwmap.rw.automapping import AutoMapper
        from rwmap.rw.version import load_profile
        cls.profile = load_profile("1.15")
        cls.sample = Path(cls.profile.sample_map)
        if not cls.sample.is_file():
            raise unittest.SkipTest(f"缺少示例地图: {cls.sample}")
        cls.mapper = AutoMapper(cls.profile)
        cls.info = cls.mapper.load()
        cls.doc = read_document(cls.sample, cls.profile)
        cls.original = {
            l.name: list(l.cells) for l in cls.doc.layers if isinstance(l, TileLayer)
        }

    def _ground(self):
        return self.doc.layer_by_name("Ground")

    def test_rules_loaded(self):
        self.assertTrue(self.info.get("ok"), self.info)
        self.assertEqual(len(self.info["rule_maps"]), 3)
        self.assertGreaterEqual(len(self.mapper.rules), 200)
        self.assertFalse(self.info["errors"])

    def test_missing_rules_file(self):
        from rwmap.rw.automapping import AutoMapper
        m = AutoMapper(self.profile, Path("不存在的目录/rules.txt"))
        self.assertFalse(m.load()["ok"])

    def test_compute_does_not_mutate_document(self):
        g = self._ground()
        before = list(g.cells)
        self.mapper.compute(self.doc, area=(30, 30, 34, 34), radius=2)
        self.assertEqual(before, list(g.cells))

    def test_pattern_fast_index_matches_naive_scan(self):
        """图案索引快路必须与逐规则试匹配产出完全一致（含局部窗口）。"""
        import copy
        doc = copy.deepcopy(self.doc)
        set_layer = doc.layer_by_name("set")
        # 造一块连片草地，触发大量 2x2 过渡规则。
        for row in range(38, 52):
            for col in range(40, 60):
                set_layer.cells[set_layer.index(col, row)] = 126
        rules = self.mapper._project_rules(doc)
        bucket, fast_ids = self.mapper._fast_index(rules)
        self.assertGreater(len(fast_ids), 0)
        self.assertLess(len(fast_ids), len(rules))   # 非常规规则仍走慢路
        area = (38, 38, 61, 54)
        try:
            self.mapper._fast_index_cache = ({}, set())
            self.mapper._fast_rules_ref = rules
            slow = self.mapper.compute(doc, area=area, only_diff=True)
        finally:
            self.mapper._fast_index_cache = None
            self.mapper._fast_rules_ref = None
        fast = self.mapper.compute(doc, area=area, only_diff=True)
        self.assertEqual({k: sorted(v) for k, v in slow.items()},
                         {k: sorted(v) for k, v in fast.items()})

    def test_full_map_reproduces_official_ground(self):
        g = self._ground()
        for i in range(len(g.cells)):
            g.cells[i] = 0
        try:
            changes = self.mapper.compute(self.doc, only_diff=False)
            self.assertIn("Ground", changes)
            for target, entries in changes.items():
                layer = self.doc.layer_by_name(target)
                if layer is None:
                    continue
                for col, row, gid in entries:
                    layer.cells[layer.index(col, row)] = gid
            same = sum(1 for a, b in zip(g.cells, self.original["Ground"]) if a == b)
            rate = same / len(g.cells)
            self.assertGreaterEqual(rate, 0.999, f"Ground 复现率仅 {rate:.2%}")
        finally:
            for name, cells in self.original.items():
                layer = self.doc.layer_by_name(name)
                if layer is not None:
                    layer.cells[:] = cells

    def test_l_shaped_corner_rules_are_not_padded_to_bbox(self):
        """拐角规则是 2x2 缺一角的 L 形；缺口那格不属于规则，不能被补成通配条件。"""
        corners = [r for r in self.mapper.rules
                   if r.name.startswith("ruleset1") and r.w == 2 and r.h == 2
                   and len(r.inputs) == 3]
        self.assertGreaterEqual(len(corners), 50, "L 形拐角规则解析丢失")
        for r in corners:
            self.assertFalse(r.wildcards,
                             f"{r.name} 的缺口被外接矩形补成了通配条件")

    def test_concave_corner_tiles_match_official(self):
        """官方图里所有拐角瓦片，全图重建后必须逐格一致（曾差 531 格）。"""
        import copy
        from rwmap.document.gid import GID_MASK
        doc = copy.deepcopy(self.doc)
        ground = doc.layer_by_name("Ground")
        corners = [(i % doc.width, i // doc.width, int(v) & GID_MASK)
                   for i, v in enumerate(self.original["Ground"])
                   if (int(v) & GID_MASK) in CORNER_GIDS]
        self.assertGreater(len(corners), 100, "官方示例图缺少拐角样本")
        for i in range(len(ground.cells)):
            ground.cells[i] = 0
        for target, entries in self.mapper.compute(doc, only_diff=False).items():
            layer = doc.layer_by_name(target)
            if layer is None:
                continue
            for col, row, gid in entries:
                layer.cells[layer.index(col, row)] = gid
        bad = [(x, y, want, int(ground.get(x, y)) & GID_MASK)
               for x, y, want in corners
               if (int(ground.get(x, y)) & GID_MASK) != want]
        self.assertFalse(bad, f"拐角瓦片丢失 {len(bad)} 格，例如 {bad[:5]}")

    def test_live_local_recompute_keeps_corners(self):
        """逐格局部重算（绘制时自动地图）不得把已生成的拐角抹成直边。"""
        import copy
        from rwmap.document.gid import GID_MASK
        doc = copy.deepcopy(self.doc)
        ground = doc.layer_by_name("Ground")
        corners = [(i % doc.width, i // doc.width, int(v) & GID_MASK)
                   for i, v in enumerate(self.original["Ground"])
                   if (int(v) & GID_MASK) in CORNER_GIDS]
        step = max(1, len(corners) // 8)
        for x, y, want in corners[::step][:8]:
            changes = self.mapper.compute(doc, area=(x, y, x, y), radius=2)
            for target, entries in changes.items():
                layer = doc.layer_by_name(target)
                if layer is None:
                    continue
                for col, row, gid in entries:
                    layer.cells[layer.index(col, row)] = gid
            self.assertEqual(int(ground.get(x, y)) & GID_MASK, want,
                             f"局部重算把拐角 @({x},{y}) 改成了直边")

    def test_local_compute_is_bounded(self):
        changes = self.mapper.compute(
            self.doc, area=(30, 30, 34, 34), radius=2, only_diff=False)
        self.assertTrue(changes)
        for entries in changes.values():
            for col, row, _gid in entries:
                self.assertTrue(16 <= col <= 48, f"局部映射越界到 {col}")
                self.assertTrue(16 <= row <= 48, f"局部映射越界到 {row}")

    def test_only_diff_filters_unchanged_cells(self):
        full = self.mapper.compute(self.doc, only_diff=False)
        diff = self.mapper.compute(self.doc, only_diff=True)
        self.assertLess(sum(len(v) for v in diff.values()),
                        sum(len(v) for v in full.values()))

    # ---- 第十八轮：set 层标记归一（用户随便点图集里某一格也要能出过渡） ----
    def test_normalize_marker_gid(self):
        from rwmap.rw.automapping import normalize_marker_gid
        f = lambda raw: normalize_marker_gid(self.doc, raw)
        self.assertEqual(f(126), 126)          # Long Grass 首格
        self.assertEqual(f(130), 126)          # 同图集第 5 格 → 首格
        self.assertEqual(f(175), 126)
        self.assertEqual(f(1), 1)              # Mountain 首格
        self.assertEqual(f(35), 1)
        self.assertEqual(f(186), 186)          # Water 首格
        self.assertEqual(f(813), -1)           # 过渡图集瓦片 → 不写进 set 层
        self.assertEqual(f(0), 0)
        self.assertEqual(f(126 | 0x80000000), 126 | 0x80000000,
                         "翻转标志要保留")

    def test_normalize_without_tilesets_keeps_original(self):
        """无图集信息时不能把落笔整片丢掉。"""
        from rwmap.document.map_document import MapDocument
        from rwmap.rw.automapping import normalize_marker_gid
        bare = MapDocument(width=2, height=2, tile_w=20, tile_h=20,
                           layers=[], tilesets=[])
        self.assertEqual(normalize_marker_gid(bare, 130), 130)
        self.assertEqual(normalize_marker_gid(bare, 813), 813)

    # ---- 岩石/岩浆过渡失效：规范标记 gid 不是图集首格 ----
    def test_canonical_markers_derived_from_rules(self):
        """规则派生的规范标记里，Stone/Lava 不是首格（938->945 / 974->978）。"""
        canon = self.mapper.canonical_marker_gids
        self.assertEqual(canon.get(938), 945, "Stone 标记应为 945 而非首格 938")
        self.assertEqual(canon.get(974), 978, "Lava 标记应为 978 而非首格 974")
        # 其它图集标记 == 首格，派生结果不应改变它们
        for first in (1, 36, 116, 126, 176, 186, 770):
            self.assertEqual(canon.get(first), first)

    def test_normalize_stone_and_lava_to_canonical(self):
        """石头/岩浆图集不在工作地图里时，首格与规范格都要归一到规范标记。"""
        from rwmap.rw.automapping import normalize_marker_gid
        canon = self.mapper.canonical_marker_gids
        f = lambda raw: normalize_marker_gid(self.doc, raw, canon)
        self.assertEqual(f(938), 945)   # Stone 首格
        self.assertEqual(f(945), 945)   # Stone 规范格
        self.assertEqual(f(974), 978)   # Lava 首格
        self.assertEqual(f(978), 978)   # Lava 规范格
        self.assertEqual(f(130), 126)   # 普通图集回归：仍归一到首格

    def test_stone_on_sand_generates_transitions(self):
        """石头画到沙地必须生成 Stone-Sand 过渡瓦片（截图里的阶梯硬边回归）。

        这曾因 Stone 被归一到首格 938 而完全失效：规则要求 945，匹配不上，
        reset_base 只写回裸石头块，形成没有圆滑过渡的阶梯状边界。
        """
        import copy
        from rwmap.rw.automapping import normalize_marker_gid, BASE_GIDS
        canon = self.mapper.canonical_marker_gids
        d = copy.deepcopy(self.doc)
        sl, gl = d.layer_by_name("set"), d.layer_by_name("Ground")
        c0, r0 = 60, 60
        for y in range(r0 - 3, r0 + 4):
            for x in range(c0 - 3, c0 + 4):
                sl.cells[sl.index(x, y)] = 770       # 沙地底
                gl.cells[gl.index(x, y)] = 0
        marker = normalize_marker_gid(d, 938, canon)
        self.assertEqual(marker, 945)
        sl.cells[sl.index(c0, r0)] = marker
        changes = self.mapper.compute(d, area=(c0, r0, c0, r0),
                                      radius=2, only_diff=False)
        transitions = [g for _x, _y, g in changes.get("Ground", [])
                       if g not in BASE_GIDS]
        self.assertGreater(len(transitions), 0,
                           "石头画到沙地应生成过渡瓦片，不能只剩裸石头块")
        # 这些 gid 来自 Stone-Sand - Flat 图集（1004-1024）
        self.assertTrue(any(1004 <= g <= 1024 for g in transitions),
                        f"应出现 Stone-Sand 过渡瓦片，实际 {transitions}")


class AutoMappingUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.w = MainWindow(auto_open=False)
        cls.w.resize(1400, 900)
        cls.w.show()
        QApplication.processEvents()
        sample = Path(cls.w.state.profile.sample_map)
        if not sample.is_file():
            raise unittest.SkipTest("缺少示例地图")
        cls.w._open(sample)
        QApplication.processEvents()

    def test_toggle_switches_to_set_layer(self):
        from PySide6.QtWidgets import QApplication
        w = self.w
        w.act_automap_while.setChecked(True)
        QApplication.processEvents()
        self.assertTrue(w._automap_while_drawing)
        self.assertIsNotNone(w._automapper)
        self.assertEqual(w.state.current_layer.name, "set")
        self.assertTrue(w.state.doc.layer_by_name("set").visible)
        w.act_automap_while.setChecked(False)
        QApplication.processEvents()
        self.assertFalse(w._automap_while_drawing)

    def test_draw_on_set_updates_ground_and_undo(self):
        from PySide6.QtWidgets import QApplication
        w = self.w
        w.act_automap_while.setChecked(True)
        QApplication.processEvents()
        doc = w.state.doc
        g, s = doc.layer_by_name("Ground"), doc.layer_by_name("set")
        col, row = 32, 32
        old_set = s.get(col, row)
        before = [g.get(col + dx, row + dy)
                  for dy in range(-2, 3) for dx in range(-2, 3)]
        count_before = w.state.undo_stack.count()
        try:
            target_gid = 1 if old_set != 1 else 186
            w._on_gid(target_gid)
            w._on_press(col, row)
            w.view.cellReleased.emit(col, row)
            QApplication.processEvents()
            self.assertEqual(s.get(col, row), target_gid)
            self.assertGreater(w.state.undo_stack.count(), count_before)
            after = [g.get(col + dx, row + dy)
                     for dy in range(-2, 3) for dx in range(-2, 3)]
            self.assertNotEqual(before, after, "绘制 set 层后 Ground 未自动更新")
            for _ in range(w.state.undo_stack.count() - count_before):
                w.state.undo_stack.undo()
            self.assertEqual(s.get(col, row), old_set)
            restored = [g.get(col + dx, row + dy)
                        for dy in range(-2, 3) for dx in range(-2, 3)]
            self.assertEqual(before, restored)
        finally:
            w.state.undo_stack.clear()
            w.act_automap_while.setChecked(False)
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
