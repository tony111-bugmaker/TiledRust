"""翻转 / 旋转（单元格 + 对象）与地图变换（新建 / 翻转 / 旋转 / 改尺寸）的回归。"""
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.rw.transform import (  # noqa: E402
    flip_cells, rotate_cells_90, transform_objects,
)


class TransformPureTest(unittest.TestCase):
    def test_flip_horizontal(self):
        cells = [1, 2, 3, 4]  # 2x2: row0 [1,2] row1 [3,4]
        self.assertEqual(flip_cells(cells, 2, 2, True), [2, 1, 4, 3])

    def test_flip_vertical(self):
        cells = [1, 2, 3, 4]
        self.assertEqual(flip_cells(cells, 2, 2, False), [3, 4, 1, 2])

    def test_rotate_90_cw_known(self):
        cells = [1, 2, 3, 4, 5, 6]  # 2x3
        out, w, h = rotate_cells_90(cells, 2, 3, True)
        self.assertEqual((w, h), (3, 2))
        self.assertEqual(out, [5, 3, 1, 6, 4, 2])

    def test_rotate_90_ccw_known(self):
        cells = [1, 2, 3, 4, 5, 6]
        out, w, h = rotate_cells_90(cells, 2, 3, False)
        self.assertEqual((w, h), (3, 2))
        self.assertEqual(out, [2, 4, 6, 1, 3, 5])

    def test_rotate_cw_then_ccw_is_identity(self):
        cells = [i for i in range(12)]  # 3x4
        cw, _, _ = rotate_cells_90(cells, 3, 4, True)
        back, w, h = rotate_cells_90(cw, 4, 3, False)
        self.assertEqual((w, h), (3, 4))
        self.assertEqual(back, cells)

    def test_transform_rect_flip_h(self):
        objs = [MapObject(object_id=1, shape="rect", x=0, y=0, width=10, height=20)]
        out = transform_objects(objs, 4, 3, 10, 10, "flip_h")
        self.assertEqual((out[0].x, out[0].y, out[0].width, out[0].height),
                         (30, 0, 10, 20))  # 40px 宽，镜像后左缘 40-0-10=30

    def test_transform_rect_rotate_cw_swaps_size(self):
        objs = [MapObject(object_id=1, shape="rect", x=0, y=0, width=10, height=20)]
        out = transform_objects(objs, 4, 3, 10, 10, "rot_cw")
        self.assertAlmostEqual(out[0].width, 20, delta=1)
        self.assertAlmostEqual(out[0].height, 10, delta=1)


class MapTransformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.MainWindow = MainWindow
        from rwmap.rw.version import load_profile
        cls.profile = load_profile("1.15")
        cls.sample = cls.profile.sample_map

    def setUp(self):
        if not Path(self.sample).is_file():
            self.skipTest("缺少 1.15 示例地图")
        self.w = self.MainWindow(auto_open=False)
        self.w.state.profile = self.profile
        self.w._open(Path(self.sample))
        self.doc = self.w.state.doc

    def test_flip_undo_restores(self):
        before = [list(l.cells) if isinstance(l, TileLayer) else None
                 for l in self.doc.layers]
        w0, h0 = self.doc.width, self.doc.height
        self.w._transform_map("flip_h")
        self.assertEqual((self.doc.width, self.doc.height), (w0, h0))
        for l, old in zip(self.doc.layers, before):
            if isinstance(l, TileLayer):
                self.assertNotEqual(list(l.cells), old)
        self.w.state.undo_stack.undo()
        for l, old in zip(self.doc.layers, before):
            if isinstance(l, TileLayer):
                self.assertEqual(list(l.cells), old)

    def test_rotate_swaps_dimensions_and_undo(self):
        w0, h0 = self.doc.width, self.doc.height
        self.w._transform_map("rot_cw")
        self.assertEqual((self.doc.width, self.doc.height), (h0, w0))
        self.w.state.undo_stack.undo()
        self.assertEqual((self.doc.width, self.doc.height), (w0, h0))

    def test_rotate_persists_to_tmx(self):
        w0, h0 = self.doc.width, self.doc.height
        self.w._transform_map("rot_cw")
        out = Path("logs") / "旋转_自测.tmx"
        out.parent.mkdir(parents=True, exist_ok=True)
        result = self.doc.save(out)
        from rwmap.io.tmx_reader import read_document
        re = read_document(Path(result), self.profile)
        self.assertEqual((re.width, re.height), (h0, w0))
        self.assertEqual(len(re.layers), len(self.doc.layers))

    def test_new_blank_map_saves_and_reloads(self):
        from rwmap.io.tmx_writer import create_blank_document
        doc = create_blank_document(self.profile, 8, 6, 20, 20)
        doc.layers = [TileLayer(width=8, height=6, name="Ground",
                              cells=[0] * 48)]
        doc.rebuild_index()
        self.w.state.open_document(doc)
        self.w._own_file = True
        self.w._refresh_document()
        # 画一格再保存
        self.w._on_gid(5)
        self.w._on_press(0, 0)
        self.w._on_move(1, 0)
        self.w.view.cellReleased.emit(1, 0)
        out = Path("logs") / "新地图_自测.tmx"
        out.parent.mkdir(parents=True, exist_ok=True)
        result = doc.save(out)
        from rwmap.io.tmx_reader import read_document
        re = read_document(Path(result), self.profile)
        self.assertEqual((re.width, re.height), (8, 6))
        self.assertEqual(re.tile_layers()[0].get(0, 0), 5)

    def test_resize_persists(self):
        from unittest import mock
        from PySide6.QtWidgets import QDialog
        w0, h0 = self.doc.width, self.doc.height
        vals = {"width": 64, "height": 64, "dx": 0, "dy": 0,
                "remove_outside": True}
        with mock.patch("rwmap.widgets.resize_map_dialog.ResizeMapDialog.exec",
                        return_value=QDialog.Accepted), \
             mock.patch("rwmap.widgets.resize_map_dialog.ResizeMapDialog.values",
                        return_value=vals):
            self.w._resize_map()
        self.assertEqual((self.doc.width, self.doc.height), (64, 64))
        out = Path("logs") / "改尺寸_自测.tmx"
        out.parent.mkdir(parents=True, exist_ok=True)
        result = self.doc.save(out)
        from rwmap.io.tmx_reader import read_document
        re = read_document(Path(result), self.profile)
        self.assertEqual((re.width, re.height), (64, 64))
        self.assertEqual(len(re.layers), len(self.doc.layers))


if __name__ == "__main__":
    unittest.main()
