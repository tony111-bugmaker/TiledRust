"""回归：绘制时自动地图必须逐格实时、无空洞、与全图重建一致、一次撤销还原。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402


class LiveAutoMappingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rwmap.io.tmx_reader import read_document
        from rwmap.rw.automapping import AutoMapper
        from rwmap.rw.version import load_profile
        cls.profile = load_profile("1.15")
        cls.sample = Path(cls.profile.sample_map)
        if not cls.sample.is_file():
            raise unittest.SkipTest(f"缺少示例地图: {cls.sample}")
        cls.mapper = AutoMapper(cls.profile)
        cls.mapper.load()
        cls.base = read_document(cls.sample, cls.profile)

    @staticmethod
    def _apply(doc, changes):
        for target, entries in changes.items():
            layer = doc.layer_by_name(target)
            if layer is None:
                continue
            for col, row, gid in entries:
                layer.cells[layer.index(col, row)] = gid

    def test_local_matches_full_rebuild_and_has_no_holes(self):
        import copy
        for (x0, y0), w, h, gid in (
                ((118, 11), 2, 9, 126), ((32, 32), 1, 1, 1),
                ((60, 60), 3, 3, 186), ((80, 80), 2, 6, 126)):
            with self.subTest(spot=(x0, y0)):
                doc = copy.deepcopy(self.base)
                set_layer = doc.layer_by_name("set")
                for dy in range(h):
                    for dx in range(w):
                        set_layer.cells[set_layer.index(x0 + dx, y0 + dy)] = gid
                full = copy.deepcopy(doc)
                ground_full = full.layer_by_name("Ground")
                for i in range(len(ground_full.cells)):
                    ground_full.cells[i] = 0
                self._apply(full, self.mapper.compute(full, only_diff=False))
                area = (x0, y0, x0 + w - 1, y0 + h - 1)
                self._apply(doc, self.mapper.compute(doc, area=area, only_diff=True))
                local_ground = doc.layer_by_name("Ground")
                self.assertEqual(sum(1 for v in local_ground.cells if not v), 0,
                                 "局部自动映射挖出了深色空洞")
                w0, w1 = max(0, x0 - 2), min(doc.width - 1, x0 + w + 1)
                h0, h1 = max(0, y0 - 2), min(doc.height - 1, y0 + h + 1)
                for y in range(h0, h1 + 1):
                    for x in range(w0, w1 + 1):
                        self.assertEqual(int(local_ground.get(x, y)),
                                         int(ground_full.get(x, y)),
                                         f"局部重算与全图重建不一致 @({x},{y})")

    def test_incremental_live_matches_single_pass(self):
        """逐格实时重算的最终结果，必须与「整笔一次性重算」一致。"""
        import copy
        cells = [(70 + i, 62 + i // 4) for i in range(9)]
        live = copy.deepcopy(self.base)
        once = copy.deepcopy(self.base)
        for x, y in cells:
            layer = live.layer_by_name("set")
            layer.cells[layer.index(x, y)] = 126
            self._apply(live, self.mapper.compute(
                live, area=(x, y, x, y), only_diff=True))
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        bounds = (min(xs), min(ys), max(xs), max(ys))
        # 松手时主窗口会按整笔范围再收敛一次
        self._apply(live, self.mapper.compute(
            live, area=bounds, only_diff=True))
        for x, y in cells:
            layer = once.layer_by_name("set")
            layer.cells[layer.index(x, y)] = 126
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        bounds = (min(xs), min(ys), max(xs), max(ys))
        self._apply(once, self.mapper.compute(
            once, area=bounds, only_diff=True))
        lg = live.layer_by_name("Ground")
        og = once.layer_by_name("Ground")
        for y in range(bounds[1] - 2, bounds[3] + 3):
            for x in range(bounds[0] - 2, bounds[2] + 3):
                self.assertEqual(int(lg.get(x, y)), int(og.get(x, y)),
                                 f"逐格实时与一次性重算不一致 @({x},{y})")

    def test_local_output_is_bounded_to_window(self):
        changes = self.mapper.compute(
            self.base, area=(30, 30, 34, 34), only_diff=False)
        for target, entries in changes.items():
            for col, row, _gid in entries:
                self.assertTrue(28 <= col <= 36, f"{target} 越界到 {col}")
                self.assertTrue(28 <= row <= 36, f"{target} 越界到 {row}")

    def test_live_drawing_grows_concave_corners(self):
        """逐格画 L 形草地伸进水里（实际用法）必须实时长出包角瓦片。

        回归：旧实现把 L 形规则按 2x2 外接矩形解析，凹角规则永远匹配不上，
        结果是横条与竖条的岸线在拐角处断开。
        """
        import copy
        from rwmap.document.gid import GID_MASK
        doc = copy.deepcopy(self.base)
        set_layer = doc.layer_by_name("set")
        ground = doc.layer_by_name("Ground")

        # 一片水，然后逐格画 L 形草地：横条 + 左竖条
        for y in range(39, 65):
            for x in range(37, 74):
                set_layer.cells[set_layer.index(x, y)] = 186
        cells = ([(x, y) for y in range(42, 47) for x in range(44, 65)]
                 + [(x, y) for y in range(47, 61) for x in range(44, 49)])
        for x, y in cells:
            set_layer.cells[set_layer.index(x, y)] = 126
            self._apply(doc, self.mapper.compute(
                doc, area=(x, y, x, y), radius=2, only_diff=True))
        # 松手时主窗口会按整笔 bounds 再收敛一次（_on_release 的真实行为）
        self._apply(doc, self.mapper.compute(
            doc, area=(44, 42, 64, 60), radius=2, only_diff=True))

        # 与「整图一次性重建」逐格一致
        full = copy.deepcopy(doc)
        fg = full.layer_by_name("Ground")
        for i in range(len(fg.cells)):
            fg.cells[i] = 0
        self._apply(full, self.mapper.compute(full, only_diff=False))
        for y in range(40, 63):
            for x in range(42, 67):
                self.assertEqual(int(ground.get(x, y)) & GID_MASK,
                                 int(fg.get(x, y)) & GID_MASK,
                                 f"逐格实时与全图重建不一致 @({x},{y})")

        # 凹角处必须出现拐角专用瓦片，而不是清一色直边（813/815/817/819）
        corners = {812, 814, 818, 820}
        n = sum(1 for y in range(40, 63) for x in range(42, 67)
                if (int(ground.get(x, y)) & GID_MASK) in corners)
        self.assertGreaterEqual(n, 1, "L 形绘制没有生成任何包角瓦片")
        self.assertIn(int(ground.get(49, 47)) & GID_MASK, corners,
                      "L 形内凹角被画成了直边")


class LiveAutoMappingUiTest(unittest.TestCase):
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

    def _reset(self):
        w = self.w
        w.state.undo_stack.clear()
        w.act_automap_while.setChecked(False)
        QApplication_processEvents()
        w.act_automap_while.setChecked(True)
        QApplication_processEvents()

    def test_press_updates_ground_before_release(self):
        w = self.w
        self._reset()
        doc = w.state.doc
        ground, set_layer = doc.layer_by_name("Ground"), doc.layer_by_name("set")
        col, row = 32, 32
        old_set = set_layer.get(col, row)
        before = [ground.get(col + dx, row + dy)
                  for dy in range(-2, 3) for dx in range(-2, 3)]
        try:
            w._on_gid(1 if old_set != 1 else 186)
            w._on_press(col, row)          # 故意不松开鼠标
            after = [ground.get(col + dx, row + dy)
                     for dy in range(-2, 3) for dx in range(-2, 3)]
            self.assertNotEqual(before, after, "按下后没有实时更新 Ground")
            self.assertTrue(w._automap_macro)
        finally:
            w.view.cellReleased.emit(col, row)
            QApplication_processEvents()
            w.state.undo_stack.clear()

    def test_drag_updates_each_cell_and_undo_once_restores_all(self):
        w = self.w
        self._reset()
        doc = w.state.doc
        ground, set_layer = doc.layer_by_name("Ground"), doc.layer_by_name("set")
        col, row = 32, 32
        old_line = [set_layer.get(col + i, row) for i in range(8)]
        before = [ground.get(col + i, row) for i in range(8)]
        target_gid = 1 if set_layer.get(col, row) != 1 else 186
        count_before = w.state.undo_stack.count()
        try:
            w._on_gid(target_gid)
            w._on_press(col, row)
            for i in range(1, 8):
                w._on_move(col + i, row)
            w.view.cellReleased.emit(col + 7, row)
            QApplication_processEvents()
            self.assertEqual(w.state.undo_stack.count(), count_before + 1,
                             "整笔应合并成一条撤销命令")
            self.assertFalse(w._automap_macro)
            self.assertNotEqual([ground.get(col + i, row) for i in range(8)], before)
            w.state.undo_stack.undo()
            QApplication_processEvents()
            self.assertEqual([set_layer.get(col + i, row) for i in range(8)], old_line)
            self.assertEqual([ground.get(col + i, row) for i in range(8)], before)
        finally:
            w.state.undo_stack.clear()
            w.act_automap_while.setChecked(False)
            QApplication_processEvents()

    def test_no_holes_after_stamp(self):
        w = self.w
        self._reset()
        doc = w.state.doc
        ground = doc.layer_by_name("Ground")
        w.state.stamp = [[126 if dy < 4 else 186 for _ in range(2)]
                         for dy in range(9)]
        try:
            w._on_gid(126)
            col, row = 118, 11
            w._on_press(col, row)
            w.view.cellReleased.emit(col, row)
            QApplication_processEvents()
            self.assertEqual(sum(1 for v in ground.cells if not v), 0)
        finally:
            w.state.stamp = None
            w.state.undo_stack.clear()
            w.act_automap_while.setChecked(False)
            QApplication_processEvents()

    def test_pen_line_updates_ground_and_undo_once(self):
        """第十七轮：钢笔矢量线落笔也要走「绘制时自动地图」，且仍只占一条撤销。"""
        from PySide6.QtWidgets import QApplication
        w = self.w
        self._reset()
        doc = w.state.doc
        ground, set_layer = doc.layer_by_name("Ground"), doc.layer_by_name("set")
        col, row = 32, 32
        old_line = [set_layer.get(col + i, row) for i in range(8)]
        before = [ground.get(col + dx, row + dy)
                  for dy in range(-2, 3) for dx in range(-2, 3)]
        target_gid = 186 if all(v == 1 for v in old_line) else 1
        count_before = w.state.undo_stack.count()
        tw, th = doc.tile_w, doc.tile_h
        try:
            w._on_gid(target_gid)
            w.state.stamp = None
            w._set_object_tool("pen")
            y = (row + 0.5) * th
            x0, x1 = (col + 0.5) * tw, (col + 7.5) * tw
            for x in (x0, x1):
                w.view.objectToolRightPressed.emit(x, y)
                w.view.objectToolRightReleased.emit(x, y)
            # 远离曲线的空白处左键单击 = 完成并落笔
            w.view.objectToolPressed.emit(x0, y + 1000.0)
            w.view.objectToolReleased.emit(x0, y + 1000.0)
            QApplication_processEvents()
            self.assertNotEqual(
                [set_layer.get(col + i, row) for i in range(8)], old_line,
                "钢笔线没有写进 set 层")
            after = [ground.get(col + dx, row + dy)
                     for dy in range(-2, 3) for dx in range(-2, 3)]
            self.assertNotEqual(after, before, "钢笔线没有触发自动地图")
            self.assertEqual(w.state.undo_stack.count(), count_before + 1,
                             "钢笔线 + 自动映射应合成一条撤销命令")
            w.state.undo_stack.undo()
            QApplication_processEvents()
            self.assertEqual([set_layer.get(col + i, row) for i in range(8)],
                             old_line)
            self.assertEqual([ground.get(col + dx, row + dy)
                              for dy in range(-2, 3) for dx in range(-2, 3)],
                             before)
        finally:
            w._pen_cancel()
            w._set_object_tool("brush")
            w.state.stamp = None
            w.state.undo_stack.clear()
            w.act_automap_while.setChecked(False)
            QApplication_processEvents()

    def test_fill_with_live_automap_updates_ground_and_undo_once(self):
        """填充也要走实时自动映射：Ground 立刻更新，且整次填充只占一条撤销。"""
        from PySide6.QtWidgets import QApplication
        w = self.w
        self._reset()
        doc = w.state.doc
        ground, set_layer = doc.layer_by_name("Ground"), doc.layer_by_name("set")
        saved_set, saved_ground = list(set_layer.cells), list(ground.cells)
        c0, r0 = 6, 6
        try:
            # 造一块被草地包围的小水域，保证洪水填充只覆盖这 36 格。
            for r in range(r0 - 1, r0 + 7):
                for c in range(c0 - 1, c0 + 7):
                    inside = r0 <= r < r0 + 6 and c0 <= c < c0 + 6
                    set_layer.cells[set_layer.index(c, r)] = 186 if inside else 126
            before = [ground.get(c0 + dx, r0 + dy)
                      for dx in (-1, 0, 1, 5, 6, 7) for dy in (-1, 0, 1, 5, 6, 7)]
            count_before = w.state.undo_stack.count()
            w._on_gid(126)                     # 选瓦片会自动切回画笔，再切填充
            w._set_object_tool("fill")
            w._on_press(c0 + 2, r0 + 2)
            QApplication.processEvents()
            after = [ground.get(c0 + dx, r0 + dy)
                     for dx in (-1, 0, 1, 5, 6, 7) for dy in (-1, 0, 1, 5, 6, 7)]
            self.assertNotEqual(after, before, "填充后 Ground 没有实时更新")
            self.assertEqual(w.state.undo_stack.count(), count_before + 1,
                             "填充 + 自动映射应合成一条撤销")
            w.state.undo_stack.undo()
            QApplication.processEvents()
            restored = [ground.get(c0 + dx, r0 + dy)
                        for dx in (-1, 0, 1, 5, 6, 7) for dy in (-1, 0, 1, 5, 6, 7)]
            self.assertEqual(restored, before, "一次撤销没有还原 Ground")
        finally:
            set_layer.cells[:] = saved_set
            ground.cells[:] = saved_ground
            w.state.undo_stack.clear()
            w._set_object_tool("brush")
            w.state.stamp = None
            w.act_automap_while.setChecked(False)
            QApplication.processEvents()

    # ---- 第十八轮：set 层标记归一 + 落笔固定写 set 层 ----
    def _pen_draw_line(self, col, row, gid=130, stamp=None, n=8):
        w = self.w
        doc = w.state.doc
        tw, th = doc.tile_w, doc.tile_h
        w._on_gid(gid)
        w.state.stamp = stamp
        w._set_object_tool("pen")
        y = (row + 0.5) * th
        x0, x1 = (col + 0.5) * tw, (col + (n - 0.5)) * tw
        for x in (x0, x1):
            w.view.objectToolRightPressed.emit(x, y)
            w.view.objectToolRightReleased.emit(x, y)
        w.view.objectToolPressed.emit(x0, y + 1000.0)
        w.view.objectToolReleased.emit(x0, y + 1000.0)
        QApplication_processEvents()

    def _pen_cleanup(self):
        w = self.w
        w._pen_cancel()
        w._set_object_tool("brush")
        w.state.stamp = None
        w.state.undo_stack.clear()
        w.act_automap_while.setChecked(False)
        QApplication_processEvents()

    def _near_ground(self, ground, col, row):
        return [ground.get(col + dx, row + dy)
                for dy in range(-2, 3) for dx in range(-2, 3)]

    def test_pen_non_base_tile_generates_transitions(self):
        """选了 Long Grass 第 5 格(130) 也必须生成过渡：归一到首格 126。"""
        w = self.w
        self._reset()
        doc = w.state.doc
        ground, set_layer = doc.layer_by_name("Ground"), doc.layer_by_name("set")
        col, row = 32, 32
        old_line = [set_layer.get(col + i, row) for i in range(8)]
        before = self._near_ground(ground, col, row)
        count_before = w.state.undo_stack.count()
        try:
            self._pen_draw_line(col, row, gid=130)
            self.assertEqual(set_layer.get(col, row), 126,
                             "非首格瓦片没有归一到基础图集首格")
            self.assertNotEqual(self._near_ground(ground, col, row), before,
                                "归一后仍没有生成水陆过渡")
            self.assertEqual(w.state.undo_stack.count(), count_before + 1)
            w.state.undo_stack.undo()
            QApplication_processEvents()
            self.assertEqual([set_layer.get(col + i, row) for i in range(8)],
                             old_line)
            self.assertEqual(self._near_ground(ground, col, row), before)
        finally:
            self._pen_cleanup()

    def test_pen_on_ground_layer_writes_set_only(self):
        """开关开启时手动切到 Ground，落笔仍写 set 层，不会把线画到 Ground 再被擦掉。"""
        w = self.w
        self._reset()
        doc = w.state.doc
        ground, set_layer = doc.layer_by_name("Ground"), doc.layer_by_name("set")
        w.state.current_layer = ground
        w.scene.current_layer = ground
        col, row = 32, 32
        before = self._near_ground(ground, col, row)
        try:
            self._pen_draw_line(col, row, gid=130)
            self.assertEqual(set_layer.get(col, row), 126)
            self.assertNotIn(130, [ground.get(col + i, row) for i in range(8)],
                             "标记被写进了 Ground 层")
            self.assertNotEqual(self._near_ground(ground, col, row), before,
                                "切层后没有生成过渡")
        finally:
            self._pen_cleanup()

    def test_pen_stamp_transition_tiles_are_repaired(self):
        """第十九轮：图章里的过渡瓦片由正反双向遍历补成有效地形标记，
        set 层不留空洞，那一段边界也能正常生成过渡。"""
        w = self.w
        self._reset()
        doc = w.state.doc
        ground, set_layer = doc.layer_by_name("Ground"), doc.layer_by_name("set")
        col, row = 32, 32
        before = self._near_ground(ground, col, row)
        stamp = [[126, 130, 813], [130, 126, 820], [186, 126, 126]]
        try:
            self._pen_draw_line(col, row, gid=126, stamp=stamp)
            box = [int(set_layer.get(c, r)) & 0x0FFFFFFF
                   for r in range(row - 2, row + 5)
                   for c in range(col - 2, col + 11)]
            self.assertNotIn(813, box)
            self.assertNotIn(820, box)
            self.assertIn(126, box)
            self.assertNotEqual(self._near_ground(ground, col, row), before)
            self.assertIn("双向补齐", w.statusBar().currentMessage())
            self.assertNotIn("已跳过", w.statusBar().currentMessage())
        finally:
            self._pen_cleanup()


def QApplication_processEvents():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
