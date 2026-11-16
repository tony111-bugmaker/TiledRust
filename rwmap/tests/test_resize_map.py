"""回归：Tiled 风格「调整地图尺寸」对话框 + 挪动偏移算法。

覆盖：shift_cells 的平移/裁切、连通块红框、对象平移与移除、预览拖动改 X/Y、
对话框收集的数值、主窗口一条撤销可还原。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.io.tmx_writer import create_blank_document  # noqa: E402
from rwmap.rw.resize import connected_blocks, shift_cells, shift_objects  # noqa: E402
from rwmap.rw.version import active_profile  # noqa: E402
from rwmap.widgets.resize_map_dialog import ResizeMapDialog, ResizePreview  # noqa: E402


_app = QApplication.instance() or QApplication([])


class ResizePureTest(unittest.TestCase):
    def test_shift_cells_no_offset_crops_bottom_right(self):
        cells = [1, 2, 3, 4]  # 2x2
        out, overflow = shift_cells(cells, 2, 2, 3, 3, 0, 0)
        self.assertEqual(len(out), 9)
        self.assertEqual([out[0], out[1], out[3], out[4]], [1, 2, 3, 4])
        self.assertEqual(overflow, set())

    def test_shift_cells_dx_dy_and_overflow(self):
        # 3x3，只在 (2,2) 有内容；向右挪 1 => 新 (3,2) 越界（新宽 3）
        cells = [0] * 9
        cells[2 * 3 + 2] = 7
        out, overflow = shift_cells(cells, 3, 3, 3, 3, 1, 0)
        self.assertEqual(overflow, {(2, 2)})
        self.assertNotIn(7, out)

    def test_shift_cells_negative_offset(self):
        cells = [0, 0, 5, 0, 0, 0, 0, 0, 0]  # 3x3, (2,0)=5
        out, overflow = shift_cells(cells, 3, 3, 3, 3, -2, 0)
        self.assertEqual(out[0], 5)  # 落到 (0,0)
        self.assertEqual(overflow, set())

    def test_connected_blocks_groups_and_thin(self):
        # 一段连续 + 一个孤立 => 2 块
        cells = {(0, 0), (1, 0), (2, 0), (5, 5)}
        blocks = connected_blocks(cells)
        self.assertEqual(len(blocks), 2)
        self.assertIn((0, 0, 2, 0), blocks)
        self.assertIn((5, 5, 5, 5), blocks)

    def test_shift_objects_moves_and_removes(self):
        objs = [
            MapObject(object_id=1, shape="rect", x=0, y=0, width=10, height=10),
            MapObject(object_id=2, shape="rect", x=190, y=190, width=10, height=10),
        ]
        # 新地图 10x10 格 (200x200px)，挪动 (+1,+1)：对象1->(20,20) 保留；
        # 对象2->(210,210) 完全在外 -> 移除
        kept = shift_objects(objs, 1, 1, 20, 20, 10, 10, True)
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0].x, kept[0].y), (20.0, 20.0))
        # 不勾选 => 两个都保留，只是平移
        allkept = shift_objects(objs, 1, 1, 20, 20, 10, 10, False)
        self.assertEqual(len(allkept), 2)


class ResizeDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = create_blank_document(active_profile(), 8, 8, 20, 20)
        self.doc.layers = [TileLayer(width=8, height=8, name="Ground",
                                     cells=[0] * 64)]
        self.doc.layers[0].set(7, 7, 9)  # 右下角有内容，向右挪 1 就溢出
        self.doc.rebuild_index()

    def test_dialog_initial_values(self):
        dlg = ResizeMapDialog(self.doc)
        v = dlg.values()
        self.assertEqual((v["width"], v["height"]), (8, 8))
        self.assertEqual((v["dx"], v["dy"]), (0, 0))
        self.assertTrue(v["remove_outside"])
        dlg.close()

    def test_spin_change_updates_preview_overflow(self):
        dlg = ResizeMapDialog(self.doc)
        dlg.sp_x.setValue(1)  # (7,7) -> (8,7)，新宽 8 => 溢出
        QApplication.processEvents()
        cells, blocks, rects = dlg.preview.overflow_info()
        self.assertEqual(cells, 1)
        self.assertEqual(blocks, 1)
        self.assertEqual(rects, [(8, 7, 8, 7)])
        dlg.close()

    def test_preview_drag_signal(self):
        dlg = ResizeMapDialog(self.doc)
        pv = dlg.preview
        seen = []
        pv.offsetChanged.connect(lambda dx, dy: seen.append((dx, dy)))
        # 直接发信号模拟拖动改值（拖动坐标→格的换算由 paint 几何决定，
        # 这里验证信号 ↔ 微调框 ↔ 预览参数这条链路）
        pv.offsetChanged.emit(2, -1)
        QApplication.processEvents()
        self.assertIn((2, -1), seen)
        dlg.close()

    def test_preview_offset_signal_updates_spinboxes(self):
        dlg = ResizeMapDialog(self.doc)
        dlg.preview.offsetChanged.emit(3, -2)
        QApplication.processEvents()
        self.assertEqual(dlg.sp_x.value(), 3)
        self.assertEqual(dlg.sp_y.value(), -2)
        self.assertEqual(dlg.preview.preview_params()[2:4], (3, -2))
        dlg.close()

    def test_drag_freezes_scale_and_maps_1to1(self):
        """拖动时缩放/原点必须冻结，内容按鼠标像素 1:1 移动（无橡皮筋限制）。"""
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        dlg = ResizeMapDialog(self.doc)
        dlg.resize(560, 520)
        QApplication.processEvents()
        pv = dlg.preview
        c = pv.rect().center()
        scale_before = pv._scale

        press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(c),
                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        pv.mousePressEvent(press)
        # 连续右移 100px（分 5 步），期间缩放/原点不得变化
        for i in range(1, 6):
            pos = QPointF(c.x() + i * 20, c.y())
            move = QMouseEvent(QMouseEvent.Type.MouseMove, pos,
                               Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            pv.mouseMoveEvent(move)
            self.assertAlmostEqual(pv._scale, scale_before, places=6)
            self.assertAlmostEqual(pv._origin.x(), pv._drag_origin.x(), places=6)
        self.assertGreater(dlg.sp_x.value(), 0)  # 右移 => 正 X

        release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease,
                              QPointF(c.x() + 100, c.y()),
                              Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        pv.mouseReleaseEvent(release)
        self.assertFalse(pv._dragging)  # 松手后解除冻结
        dlg.close()


class ResizeIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_resize_with_offset_and_undo(self):
        from rwmap.app.main_window import MainWindow
        w = MainWindow(auto_open=False)
        doc = create_blank_document(active_profile(), 8, 8, 20, 20)
        layer = TileLayer(width=8, height=8, name="Ground", cells=[0] * 64)
        layer.set(0, 0, 11)
        doc.layers = [layer]
        doc.rebuild_index()
        w.state.open_document(doc)
        w._refresh_document()
        vals = {"width": 8, "height": 8, "dx": 1, "dy": 0,
                "remove_outside": True}
        with mock.patch("rwmap.widgets.resize_map_dialog.ResizeMapDialog.exec",
                        return_value=QDialog.Accepted), \
             mock.patch("rwmap.widgets.resize_map_dialog.ResizeMapDialog.values",
                        return_value=vals):
            w._resize_map()
        QApplication.processEvents()
        # (0,0) 的瓦片平移到 (1,0)
        self.assertEqual(doc.tile_layers()[0].get(1, 0), 11)
        self.assertEqual(doc.tile_layers()[0].get(0, 0), 0)
        w.state.undo_stack.undo()
        QApplication.processEvents()
        self.assertEqual(doc.tile_layers()[0].get(0, 0), 11)
        w.close()


if __name__ == "__main__":
    unittest.main()
