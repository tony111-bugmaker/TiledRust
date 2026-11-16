"""优化批次 1 回归：消除 QUndoStack.push 首次 redo 的重复写入/刷新。

AppState 的笔划在 stroke 阶段已经写过 layer.cells 并批量刷新过画面，
而 push() 会立刻执行一次 redo —— 旧行为把同一批格子再写一遍、再刷一次。
本文件验证：跳过只作用于 push 触发的首次 redo，其余撤销语义完全不变。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.commands.draw_command import DrawCommand  # noqa: E402
from rwmap.document.layer import TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402


def _window():
    from rwmap.app.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow(auto_open=False)
    layer = TileLayer(width=12, height=12, name="Ground",
                      cells=[0] * 144, readonly=False)
    doc = MapDocument(width=12, height=12, tile_w=20, tile_h=20,
                      layers=[layer], tilesets=[])
    w.state.doc = doc
    w.state.current_layer = layer
    w.state.profile.write_enabled = True
    w.state.brush_gid = 7
    w.state.tool_id = "brush"
    w.state.stamp = None
    w.scene.set_document(doc)
    w.view.set_document(doc)
    return w, layer, doc


class SkipFirstRedoTest(unittest.TestCase):
    def setUp(self):
        self.w, self.layer, self.doc = _window()

    def test_push_no_longer_adds_a_refresh(self):
        """完整笔划：press/move 各批量刷新 1 次，release 不再多刷一次。"""
        with mock.patch.object(self.w, "_refresh_layer",
                               wraps=self.w._refresh_layer) as m:
            self.w._on_press(4, 4)
            self.assertEqual(m.call_count, 1)
            self.w._on_move(5, 4)
            self.assertEqual(m.call_count, 2)
            self.w._on_release()
            self.assertEqual(m.call_count, 2,
                             "push 首次 redo 不应再刷新一次")
        self.assertEqual(self.layer.get(4, 4), 7)
        self.assertEqual(self.layer.get(5, 4), 7)

    def test_undo_and_redo_still_replay_values_and_refresh(self):
        """标志只消耗一次：undo 写回 old，之后 redo 写回 new 并刷新。"""
        with mock.patch.object(self.w, "_refresh_layer",
                               wraps=self.w._refresh_layer) as m:
            self.w._on_press(4, 4)
            self.w._on_release()
            self.assertEqual(m.call_count, 1)
            self.w.state.undo_stack.undo()
            self.assertEqual(self.layer.get(4, 4), 0)
            self.assertEqual(m.call_count, 2)
            self.w.state.undo_stack.redo()
            self.assertEqual(self.layer.get(4, 4), 7)
            self.assertEqual(m.call_count, 3)

    def test_region_stroke_also_skips_first_redo(self):
        calls = []

        def refresh(layer, cells=None):
            calls.append(list(cells or []))

        state = self.w.state
        state.begin_region_stroke(self.layer, refresh, text="填充")
        for r in range(3):
            for c in range(3):
                state.write_region_cell(c, r, 9)
        self.assertEqual(calls, [])
        state.end_stroke()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 9)
        state.undo_stack.undo()
        self.assertEqual(self.layer.get(1, 1), 0)


class OtherDrawCommandsStillRedoTest(unittest.TestCase):
    """其余 4 处直接构造的 DrawCommand 不带标志，push 仍是唯一写入路径。"""

    def setUp(self):
        self.w, self.layer, self.doc = _window()

    def test_push_redo_writes_data_and_applies_selection(self):
        seen = []
        index = self.layer.index(3, 3)
        cmd = DrawCommand(
            self.doc, self.layer, [(index, 0, 9)], self.w._refresh_layer,
            selection=("before", "after"),
            apply_selection=lambda value: seen.append(value),
        )
        self.assertEqual(self.layer.get(3, 3), 0)
        self.w.state.undo_stack.push(cmd)
        self.assertEqual(self.layer.get(3, 3), 9)
        self.assertEqual(seen, ["after"])
        self.w.state.undo_stack.undo()
        self.assertEqual(self.layer.get(3, 3), 0)
        self.assertEqual(seen, ["after", "before"])

    def test_explicit_skip_flag_only_skips_once(self):
        index = self.layer.index(2, 2)
        cmd = DrawCommand(self.doc, self.layer, [(index, 0, 5)],
                          self.w._refresh_layer, skip_first_redo=True)
        self.w.state.undo_stack.push(cmd)
        # 调用方自己负责预写，这里数据仍是 old（符合 skip 的契约）。
        self.layer.cells[index] = 5
        self.w.state.undo_stack.undo()
        self.assertEqual(self.layer.get(2, 2), 0)
        self.w.state.undo_stack.redo()
        self.assertEqual(self.layer.get(2, 2), 5)


if __name__ == "__main__":
    unittest.main()
