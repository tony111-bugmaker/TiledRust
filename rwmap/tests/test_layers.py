"""图层增 / 删 / 复制 / 排序 / 合并 / 锁定的端到端回归（离屏）。

覆盖：结构类操作经 ``LayerStructureCommand`` 落一条撤销可还原；
合并经 ``MergeLayerCommand`` 还原目标层内容并插回源层；
锁定经 ``LayerFlagCommand`` 还原；以及新建 / 删除 / 排序后写回 TMX 不丢层。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402


def _make_window():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    return MainWindow(auto_open=False)


def _three_layers():
    layers = []
    for name in ("A", "B", "C"):
        cells = [0] * 12
        cells[0] = ord(name)  # 用可区分的非零值标出 (0,0)
        layers.append(TileLayer(width=4, height=3, name=name, cells=cells,
                               readonly=False))
    return layers


class LayerOpsTest(unittest.TestCase):
    def setUp(self):
        self.w = _make_window()
        layers = _three_layers()
        doc = MapDocument(width=4, height=3, tile_w=20, tile_h=20,
                         layers=layers, tilesets=[])
        self.w.state.doc = doc
        self.w.state.current_layer = layers[0]
        self.w.state.profile.write_enabled = True
        self.w._refresh_document()
        # 让 doc 不绑定真实树，验证纯内存结构操作也正确。
        self.doc = doc
        self.A, self.B, self.C = layers

    def _select(self, layer):
        self.w.layers_dock.select_layer(layer)

    def test_add_tile_layer_at_bottom(self):
        self._select(self.A)
        before = len(self.doc.layers)
        self.w._add_layer("tile")
        self.assertEqual(len(self.doc.layers), before + 1)
        # 新层加到最下侧：数组 index 0（面板显示最底行），原层顺序不变。
        self.assertIsInstance(self.doc.layers[0], TileLayer)
        self.assertEqual(self.doc.layers[0].name, "图层 4")
        self.assertEqual(self.doc.layers[1:], [self.A, self.B, self.C])
        self.w.state.undo_stack.undo()
        self.assertEqual(len(self.doc.layers), before)
        self.assertEqual(self.doc.layers, [self.A, self.B, self.C])

    def test_delete_layer_keeps_docs_consistent(self):
        self._select(self.B)
        before = len(self.doc.layers)
        self.w._delete_layer(self.B)
        self.assertEqual(len(self.doc.layers), before - 1)
        self.assertNotIn(self.B, self.doc.layers)
        self.w.state.undo_stack.undo()
        self.assertEqual(len(self.doc.layers), before)
        self.assertIn(self.B, self.doc.layers)

    def test_delete_last_layer_is_blocked(self):
        # 删到只剩一层时不应再删
        self.w._delete_layer(self.A)
        self.w._delete_layer(self.B)
        self.assertEqual(len(self.doc.layers), 1)
        self.w._delete_layer(self.doc.layers[0])
        self.assertEqual(len(self.doc.layers), 1)

    def test_duplicate_layer_copies_cells(self):
        self._select(self.B)
        before = len(self.doc.layers)
        self.w._duplicate_layer(self.B)
        self.assertEqual(len(self.doc.layers), before + 1)
        copy = self.doc.layers[self.doc.layers.index(self.B) + 1]
        self.assertEqual(copy.cells, self.B.cells)
        self.assertNotEqual(id(copy), id(self.B))
        self.w.state.undo_stack.undo()
        self.assertEqual(len(self.doc.layers), before)

    def test_move_layer_up_swaps_order(self):
        self._select(self.B)  # index 1
        self.w._move_layer(self.B, 1)  # 上移
        names = [l.name for l in self.doc.layers]
        self.assertEqual(names, ["A", "C", "B"])
        self.w.state.undo_stack.undo()
        self.assertEqual([l.name for l in self.doc.layers], ["A", "B", "C"])

    def test_move_layer_at_top_is_noop(self):
        self._select(self.C)
        self.w._move_layer(self.C, 1)
        self.assertEqual([l.name for l in self.doc.layers], ["A", "B", "C"])

    def test_merge_down_combines_and_restores(self):
        # C 合并到下方的 B；C 在 (0,0) 有值 'C'，B 在 (0,0) 有值 'B'。
        self.w.state.current_layer = self.B
        before = len(self.doc.layers)
        self.w._merge_layer_down(self.C)
        self.assertEqual(len(self.doc.layers), before - 1)
        self.assertNotIn(self.C, self.doc.layers)
        # B 的 (0,0) 被 C 覆盖
        self.assertEqual(self.B.get(0, 0), ord("C"))
        # 撤销：C 回来，B 恢复
        self.w.state.undo_stack.undo()
        self.assertEqual(len(self.doc.layers), before)
        self.assertIn(self.C, self.doc.layers)
        self.assertEqual(self.B.get(0, 0), ord("B"))

    def test_merge_requires_same_kind(self):
        obj = ObjectLayer(name="O", objects=[MapObject(object_id=1, shape="rect",
                                                     x=0, y=0, width=10, height=10)])
        self.doc.layers.append(obj)
        self.w._refresh_document()
        # 试着把对象层 O（index 3，下方是 C tile 层）向下合并 → 类型不同应被拒绝
        before = len(self.doc.layers)
        self.w._merge_layer_down(obj)
        self.assertEqual(len(self.doc.layers), before)

    def test_lock_blocks_editing_and_restores(self):
        self.w.state.current_layer = self.B
        self.assertFalse(self.B.locked)
        self.assertTrue(self.w.state.can_edit())
        self.w._toggle_layer_lock(self.B)
        self.assertTrue(self.B.locked)
        self.assertFalse(self.w.state.can_edit())
        self.w.state.undo_stack.undo()
        self.assertFalse(self.B.locked)
        self.assertTrue(self.w.state.can_edit())

    def test_lock_other_layers_batch_is_one_undo(self):
        """「锁定/解锁其它所有层」：其它层一次全锁、再点全解，各算一条撤销。"""
        self.w.state.current_layer = self.B
        self.w.layers_dock.select_layer(self.B)
        count = self.w.state.undo_stack.count()
        self.w.layers_dock._toggle_other_layers_lock()
        self.assertTrue(self.A.locked)
        self.assertTrue(self.C.locked)
        self.assertFalse(self.B.locked)                 # 当前层不动
        self.assertEqual(self.w.state.undo_stack.count(), count + 1)
        idx = self.w.state.undo_stack.index()
        self.w.state.undo_stack.undo()
        self.assertFalse(self.A.locked)
        self.assertFalse(self.C.locked)
        self.assertEqual(self.w.state.undo_stack.index(), idx - 1)


class LayerWriteBackTest(unittest.TestCase):
    """新建 / 删除 / 排序后写回 TMX，重新读取层数与顺序应一致。"""

    @classmethod
    def setUpClass(cls):
        from rwmap.rw.version import load_profile
        cls.profile = load_profile("1.15")
        cls.sample = Path(cls.profile.sample_map)
        if not cls.sample.is_file():
            raise unittest.SkipTest("缺少 1.15 示例地图")

    def setUp(self):
        self.w = _make_window()
        self.w.state.profile = self.profile
        self.w._open(self.sample)
        self.doc = self.w.state.doc

    def _saved_reload_layers(self, target_name="层级自测.tmx"):
        out = Path("logs") / target_name
        out.parent.mkdir(parents=True, exist_ok=True)
        result = self.w.state.doc.save(out)
        from rwmap.io.tmx_reader import read_document
        reloaded = read_document(Path(result), self.profile)
        return reloaded

    def test_add_layer_persists(self):
        before = len(self.doc.layers)
        self.w._add_layer("tile")
        self.assertEqual(len(self.doc.layers), before + 1)
        reloaded = self._saved_reload_layers("层级_新增.tmx")
        self.assertEqual(len(reloaded.layers), before + 1)

    def test_delete_layer_persists(self):
        before = len(self.doc.layers)
        target = next(l for l in self.doc.layers if isinstance(l, TileLayer))
        self.w.layers_dock.select_layer(target)
        self.w._delete_layer(target)
        self.assertEqual(len(self.doc.layers), before - 1)
        reloaded = self._saved_reload_layers("层级_删除.tmx")
        self.assertEqual(len(reloaded.layers), before - 1)

    def test_reorder_persists(self):
        names_before = [l.name for l in self.doc.layers]
        # 把最底层上移，顺序应改变并写回
        bottom = self.doc.layers[0]
        self.w.layers_dock.select_layer(bottom)
        self.w._move_layer(bottom, 1)
        names_after = [l.name for l in self.doc.layers]
        self.assertNotEqual(names_before, names_after)
        reloaded = self._saved_reload_layers("层级_排序.tmx")
        self.assertEqual([l.name for l in reloaded.layers], names_after)


if __name__ == "__main__":
    unittest.main()
