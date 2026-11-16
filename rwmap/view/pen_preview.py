"""钢笔工具的矢量路径预览：曲线 + 锚点方块 + 对称控制柄。

无拖影三原则（与 TileStampPreviewItem / GridOverlay 同构，MapView 用
MinimalViewportUpdate，违反任何一条都会在拖点时留下一串残影）：
  1. boundingRect() 恒为整张地图，不随路径变化；
  2. 复用同一个 item，绝不 removeItem / addItem；
  3. 内容变化时只 update(旧内容矩形) + update(新内容矩形)，空矩形不传 update()。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

from .selection_style import SELECT, SELECT_FILL


class PenPathPreviewItem(QGraphicsItem):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self._path = None
        self._active = None          # ("anchor"|"in"|"out", index) 或 None
        self._scale = 1.0            # 最近一次 paint 的视图缩放，用于屏幕固定尺寸
        self.setZValue(11000)        # 压过 GridOverlay(10000)，细曲线不被网格盖住

    # ---------------- 几何 ----------------
    def boundingRect(self):
        if self.doc is None:
            return QRectF(0, 0, 0, 0)
        return QRectF(0, 0, self.doc.width * self.doc.tile_w,
                      self.doc.height * self.doc.tile_h)

    def _content_rect(self):
        if self._path is None or not self._path.anchors:
            return QRectF()
        b = self._path.bounds()
        # 外扩覆盖：屏幕固定尺寸的手柄方块/圆点、曲线笔宽、虚线端点。
        pad = 24.0 / max(abs(self._scale), 0.05)
        return QRectF(b[0] - pad, b[1] - pad,
                      b[2] - b[0] + 2 * pad, b[3] - b[1] + 2 * pad)

    # ---------------- 内容更新 ----------------
    def set_path(self, path, active=None):
        old = self._content_rect()
        self._path = path.copy() if path is not None else None
        self._active = active
        if not old.isEmpty():
            self.update(old)
        new = self._content_rect()
        if not new.isEmpty():
            self.update(new)

    def clear(self):
        if self._path is None:
            return
        old = self._content_rect()
        self._path = None
        self._active = None
        if not old.isEmpty():
            self.update(old)

    # ---------------- 绘制 ----------------
    def paint(self, painter, option, widget=None):
        path = self._path
        if path is None or not path.anchors:
            return
        scale = painter.worldTransform().m11() or 1.0
        self._scale = scale
        s = 1.0 / max(abs(scale), 0.05)     # 屏幕像素 → 场景单位
        anchors = path.anchors

        # 1) 曲线（一条 QPainterPath 画完所有段）
        curve = QPainterPath()
        curve.moveTo(QPointF(*anchors[0].pos()))
        for p0, p1, p2, p3 in path.segments():
            curve.cubicTo(QPointF(*p1), QPointF(*p2), QPointF(*p3))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(SELECT, 1.8 * s))
        painter.drawPath(curve)

        # 2) 控制柄：锚点 → 柄端点的虚线 + 小圆点
        for i, a in enumerate(anchors):
            hp_in, hp_out = a.in_pt(), a.out_pt()
            if a.hin == (0.0, 0.0) and a.hout == (0.0, 0.0):
                continue
            painter.setPen(QPen(SELECT, 1.0 * s, Qt.DashLine))
            painter.drawLine(QPointF(*hp_in), QPointF(*hp_out))
            for kind, hp in (("in", hp_in), ("out", hp_out)):
                is_active = self._active == (kind, i)
                painter.setPen(QPen(SELECT, 1.2 * s))
                painter.setBrush(SELECT if is_active else SELECT_FILL)
                painter.drawEllipse(QPointF(*hp), 3.5 * s, 3.5 * s)

        # 3) 锚点小方块（最后画，保证在虚线之上、命中判定直观）
        for i, a in enumerate(anchors):
            is_active = self._active == ("anchor", i)
            x, y = a.pos()
            painter.setPen(QPen(SELECT, 1.6 * s))
            painter.setBrush(SELECT if is_active else SELECT_FILL)
            painter.drawRect(QRectF(x - 4 * s, y - 4 * s, 8 * s, 8 * s))
