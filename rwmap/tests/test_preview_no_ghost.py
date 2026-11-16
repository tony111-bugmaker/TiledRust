"""图章 / 抓取虚影「拖影」像素级回归（离屏）。

MapView 使用 ``MinimalViewportUpdate``：只重绘被标脏的矩形。蓝 / 红轮廓用
``QPen(SELECT, 1.6)``，描边中心压在 ``_content_rect()`` 的边界上，外侧半线宽
落在内容矩形**之外**。若 ``set_cells`` / ``clear_cells`` 只标脏内容矩形，那半条
线永远不会被任何 item 覆盖重绘，鼠标每移一格就留下一根蓝线（用户看到的拖影）。

这里不能用 ``scene.render()`` 全量渲染做验证——那会把整张图重画一遍，天然
没有残影，测不出问题。做法是模拟 Qt 的真实局部重绘：把画面保存在一张**持久**
QImage 上，只把 ``item.update()`` 标脏的矩形交给 ``scene.render()`` 重画，其余
像素保留不动。背景 item 铺满纯色，负责在脏区内覆盖旧的轮廓线。
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
)

# PySide 绑定对象（QPixmap / QPainter）必须先有 QApplication，单独跑本模块
# 否则会直接崩，且 Git Bash 里表现为 exit 127、没有 traceback。
_app = QApplication.instance() or QApplication([])

from rwmap.document.layer import TileLayer
from rwmap.document.map_document import MapDocument
import rwmap.view.map_scene as map_scene
from rwmap.view.map_scene import TileStampPreviewItem, _PREVIEW_DIRTY_MARGIN


_W = _H = 20
_TW = _TH = 20
_BG = QColor(40, 90, 40)


def _is_blue(c: QColor) -> bool:
    return c.blue() > 150 and c.blue() - c.red() > 60 and c.blue() - c.green() > 20


class _SpyStamp(TileStampPreviewItem):
    """记录 set_cells / clear_cells 标脏的矩形，供局部重绘模拟使用。"""

    def __init__(self, doc, calls):
        super().__init__(doc)
        self._calls = calls

    def update(self, *args):
        rect = args[0] if args else None
        self._calls.append(QRectF(rect) if rect is not None else None)
        if rect is None:
            return super().update()
        return super().update(rect)


class PreviewGhostTest(unittest.TestCase):
    def setUp(self):
        layer = TileLayer(width=_W, height=_H, name="Ground",
                          cells=[0] * (_W * _H), readonly=False)
        self.doc = MapDocument(width=_W, height=_H, tile_w=_TW, tile_h=_TH,
                               layers=[layer], tilesets=[])
        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QBrush(Qt.NoBrush))
        self.scene.setSceneRect(0, 0, _W * _TW, _H * _TH)
        bg = QGraphicsRectItem(0, 0, _W * _TW, _H * _TH)
        bg.setBrush(_BG)
        bg.setPen(Qt.NoPen)
        bg.setZValue(-10)
        self.scene.addItem(bg)
        # 真实 view + MinimalViewportUpdate，让 Qt 自己走脏区路径。
        self.view = QGraphicsView(self.scene)
        self.view.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.view.resize(_W * _TW, _H * _TH)
        self.view.show()
        _app.processEvents()

        self.calls = []
        self.item = _SpyStamp(self.doc, self.calls)
        self.scene.addItem(self.item)
        self.canvas = QImage(_W * _TW, _H * _TH, QImage.Format_ARGB32)
        self.canvas.fill(_BG)
        self.stamp = [[1] * 4 for _ in range(4)]

    def tearDown(self):
        self.view.close()

    # ---------------- 局部重绘模拟 ----------------
    def _render_dirty(self):
        painter = QPainter(self.canvas)
        for rect in [r for r in self.calls if r is not None]:
            self.scene.render(painter, rect, rect)
        painter.end()

    def _render_full(self):
        painter = QPainter(self.canvas)
        whole = QRectF(0, 0, _W * _TW, _H * _TH)
        self.scene.render(painter, whole, whole)
        painter.end()

    def _blue_in(self, rect: QRectF) -> int:
        n = 0
        x0, y0 = max(0, int(rect.left())), max(0, int(rect.top()))
        x1 = min(self.canvas.width() - 1, int(rect.right()))
        y1 = min(self.canvas.height() - 1, int(rect.bottom()))
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if _is_blue(self.canvas.pixelColor(x, y)):
                    n += 1
        return n

    def _place(self, col, row, mode="center", full=False):
        self.calls.clear()
        self.item.set_cells(self.stamp, col, row, mode)
        if full:
            self._render_full()
        else:
            self._render_dirty()

    def _whole(self):
        return QRectF(0, 0, _W * _TW, _H * _TH)

    def _current_frame(self):
        return QRectF(self.item._content_rect()).adjusted(-4, -4, 4, 4)

    # ---------------- 测试 ----------------
    def test_dirty_rect_covers_pen_half_width(self):
        # 1.6px 笔宽：中心压边界，外侧 0.8px + 抗锯齿都必须落进脏区。
        r = QRectF(80, 80, 60, 60)
        dirty = TileStampPreviewItem._dirty_rect(r)
        self.assertTrue(dirty.contains(r))
        self.assertGreaterEqual((dirty.width() - r.width()) / 2, 1.6 / 2 + 0.5)
        self.assertGreaterEqual(_PREVIEW_DIRTY_MARGIN, 1.6 / 2 + 0.5)
        self.assertTrue(TileStampPreviewItem._dirty_rect(QRectF()).isEmpty(),
                        "空矩形不能外扩成非空（会被 update 当成重绘全部）")

    def test_single_move_clears_old_position(self):
        self._place(4, 4, full=True)
        old = QRectF(self.item._content_rect())
        self.assertGreater(self._blue_in(old), 0, "初始蓝框没画出来")

        self._place(9, 4)   # 原点 60 → 160，与旧位置完全不重叠
        self.assertEqual(self._blue_in(old), 0,
                         "旧位置残留蓝线（描边外侧半线宽没被标脏重绘）")
        new = QRectF(self.item._content_rect())
        self.assertGreater(self._blue_in(new), 0, "新位置蓝框没画出来")

    def test_multistep_move_leaves_no_trail(self):
        self._place(4, 4, full=True)
        for col, row in ((9, 4), (9, 8), (14, 8), (14, 13), (6, 13), (6, 6)):
            self._place(col, row)
        whole, cur = self._whole(), self._current_frame()
        stale = self._blue_in(whole) - self._blue_in(cur)
        self.assertEqual(stale, 0,
                         "连续移动后画布上还有 %d 个游离蓝像素（拖影）" % stale)

    def test_clear_removes_every_trail(self):
        self._place(4, 4, full=True)
        for col, row in ((9, 4), (9, 8), (14, 8), (14, 13), (6, 13), (6, 6)):
            self._place(col, row)
        self.calls.clear()
        self.item.clear_cells()
        self._render_dirty()
        self.assertEqual(self._blue_in(self._whole()), 0,
                         "clear_cells 后仍有蓝线残留")

    def test_new_frame_stroke_is_complete(self):
        # 外扩不仅要擦旧线，还要让新框压在边界外侧的半条线画得出来。
        self._place(4, 4, full=True)
        self._place(9, 4)
        r = QRectF(self.item._content_rect())
        x, y, w, h = r.left(), r.top(), r.width(), r.height()
        edges = {
            "top": QRectF(x, y - 2, w, 4),
            "bottom": QRectF(x, y + h - 2, w, 4),
            "left": QRectF(x - 2, y, 4, h),
            "right": QRectF(x + w - 2, y, 4, h),
        }
        for name, box in edges.items():
            self.assertGreater(self._blue_in(box), 0,
                               "新框 %s 边缺线（脏区没覆盖描边）" % name)

    def test_capture_preview_topleft_has_no_ghost(self):
        # 右键拖动抓区域走 topleft 对齐，同一套脏区逻辑。
        self._place(2, 2, mode="topleft", full=True)
        old = QRectF(self.item._content_rect())
        self._place(10, 6, mode="topleft")
        self.assertEqual(self._blue_in(old), 0, "抓取虚影留下了拖影")
        self.assertGreater(self._blue_in(QRectF(self.item._content_rect())), 0)

    def test_probe_would_catch_missing_margin(self):
        # 探针自证：把外扩临时改回 0，必须能测出拖影，避免测试假通过。
        self._place(4, 4, full=True)
        old = QRectF(self.item._content_rect())
        with mock.patch.object(map_scene, "_PREVIEW_DIRTY_MARGIN", 0.0):
            self._place(9, 4)
        self.assertGreater(self._blue_in(old), 0,
                           "margin=0 时探针没测到拖影，说明这个像素探针失效了")


if __name__ == "__main__":
    unittest.main()
