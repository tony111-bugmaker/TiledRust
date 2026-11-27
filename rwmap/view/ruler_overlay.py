"""标尺工具覆盖层：两点测距 + 等分标记 + 多段平均。

无拖影三原则（与 PenPathPreviewItem 一致，MapView 用 MinimalViewportUpdate）：
  1. boundingRect() 恒为整张地图，不随标记变化；
  2. 复用同一个 item，绝不 removeItem / addItem；
  3. 内容变化只 update(旧内容矩形) + update(新内容矩形)。

显示状态由 MainWindow 持有：切换工具不清、切文档（scene.clear）才重建。
右键仅在标尺工具激活时清除，见 MainWindow._on_object_right_press。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem

from .selection_style import SELECT

_MARK = QColor(255, 205, 70)          # 等分标记：琥珀，与蓝色测线区分
_TEXT_BG = QColor(20, 24, 30, 210)


def cell_distance(c0: int, r0: int, c1: int, r1: int) -> int:
    """两格之间的格数（含两端），按切比雪夫距离算直线覆盖的格数。"""
    return max(abs(c1 - c0), abs(r1 - r0)) + 1


def split_cells(c0: int, r0: int, c1: int, r1: int, parts: int) -> list[tuple[int, int]]:
    """把一段等分成 parts 份，返回中间 parts-1 个标记格（四舍五入到格）。"""
    if parts <= 1:
        return []
    marks = []
    for i in range(1, parts):
        t = i / parts
        # 四舍五入到格（不用 Python round：银行家舍入 4.5→4，视觉上偏左）
        marks.append((math.floor(c0 + (c1 - c0) * t + 0.5),
                      math.floor(r0 + (r1 - r0) * t + 0.5)))
    return marks


def segment_average(segments: list[tuple[int, int, int, int]], parts: int) -> float:
    """多段标记时每段的平均格数；无标记返回 0。"""
    if not segments:
        return 0.0
    total = sum(cell_distance(*seg) for seg in segments)
    return total / max(1, parts) / len(segments)


def _font_metrics(scale: float):
    """按当前缩放返回字体度量；scene 坐标里字号随 1/scale 放大，屏幕字高恒定。"""
    font = QFont()
    s = 1.0 / max(abs(scale), 0.05)
    font.setPixelSize(max(10, round(12 * s)))
    return QFontMetricsF(font), s


def segment_label_rect(p0: QPointF, p1: QPointF, text: str, scale: float) -> QRectF:
    """单段中点标签的矩形（横测放线上方、竖测放线右侧）。纯函数，供 paint 与脏区共用。"""
    fm, s = _font_metrics(scale)
    tw, th = fm.horizontalAdvance(text), fm.height()
    mid = QPointF((p0.x() + p1.x()) / 2, (p0.y() + p1.y()) / 2)
    if abs(p1.x() - p0.x()) >= abs(p1.y() - p0.y()):
        mid.setY(mid.y() - (th / 2 + 9 * s))   # 横测：线上方
    else:
        mid.setX(mid.x() + tw / 2 + 12 * s)    # 竖测：线右侧
    return QRectF(mid.x() - tw / 2 - 5 * s, mid.y() - th / 2 - 3 * s,
                  tw + 10 * s, th + 6 * s)


def summary_label_rect(anchor: QPointF, text: str, scale: float) -> QRectF:
    """总计标签矩形（画在 anchor 下方）。纯函数，供 paint 与脏区共用。"""
    fm, s = _font_metrics(scale)
    tw, th = fm.horizontalAdvance(text), fm.height()
    return QRectF(anchor.x() - tw / 2 - 5 * s, anchor.y(),
                  tw + 10 * s, th + 6 * s)


def segment_label_text(c0, r0, c1, r1, parts: int) -> str:
    dist = cell_distance(c0, r0, c1, r1)
    text = f"{dist} 格"
    if parts > 1:
        text += f" · 每段 {dist / parts:.1f} 格"
    return text


def summary_label_text(count: int, segments, parts: int) -> str:
    avg = segment_average(segments, parts)
    return f"共 {count} 段 · 平均 {avg:.1f} 格/段"


class RulerOverlay(QGraphicsItem):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.segments: list[tuple[int, int, int, int]] = []
        self.pending: tuple[int, int] | None = None   # 已点下起点，只显示一个点，等第二点连线
        self.parts = 1
        self._scale = 1.0            # 最近一次 paint 的缩放，脏区算文字矩形用
        self.setZValue(11500)        # 压过钢笔预览(11000)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)

    def boundingRect(self):
        if self.doc is None:
            return QRectF()
        return QRectF(0, 0, self.doc.width * self.doc.tile_w,
                      self.doc.height * self.doc.tile_h)

    # ---------------- 内容矩形（脏区） ----------------
    def _cell_center(self, col: int, row: int) -> QPointF:
        tw, th = self.doc.tile_w, self.doc.tile_h
        return QPointF((col + 0.5) * tw, (row + 0.5) * th)

    def _seg_rect(self, seg):
        c0, r0, c1, r1 = seg
        p0, p1 = self._cell_center(c0, r0), self._cell_center(c1, r1)
        rect = QRectF(p0, p1).normalized()
        pad = max(self.doc.tile_w, self.doc.tile_h) * 3
        return rect.adjusted(-pad, -pad, pad, pad)

    def _point_rect(self, cell):
        pc = self._cell_center(*cell)
        pad = max(self.doc.tile_w, self.doc.tile_h) * 2
        return QRectF(pc.x() - pad, pc.y() - pad, 2 * pad, 2 * pad)

    def _content_rect(self):
        rects = []
        for seg in self.segments:
            rects.append(self._seg_rect(seg))
            # 每段文字标签可能超出线段 bbox（尤其竖测长文字伸到线右侧），
            # 必须并进脏区，否则旧文字在内容变化时擦不掉 → 反复叠加发虚。
            c0, r0, c1, r1 = seg
            p0, p1 = self._cell_center(c0, r0), self._cell_center(c1, r1)
            rects.append(segment_label_rect(
                p0, p1, segment_label_text(c0, r0, c1, r1, self.parts),
                self._scale))
        # 总计标签画在最后一段下方，同样要把矩形并进脏区。
        if len(self.segments) > 1:
            last = self._seg_rect(self.segments[-1])
            s = 1.0 / max(abs(self._scale), 0.05)
            anchor = QPointF(last.center().x(), last.bottom() - 6 * s)
            rects.append(summary_label_rect(
                anchor, summary_label_text(len(self.segments),
                                           self.segments, self.parts),
                self._scale))
        if self.pending is not None:
            rects.append(self._point_rect(self.pending))
        out = QRectF()
        for r in rects:
            out = out.united(r) if not out.isEmpty() else QRectF(r)
        return out

    # ---------------- 状态更新 ----------------
    def _refresh(self, old: QRectF):
        if not old.isEmpty():
            self.update(old)
        new = self._content_rect()
        if not new.isEmpty():
            self.update(new)

    def set_segments(self, segments, parts=None):
        old = self._content_rect()
        self.segments = [tuple(s) for s in segments]
        if parts is not None:
            self.parts = max(1, int(parts))
        self._refresh(old)

    def set_pending(self, cell):
        old = self._content_rect()
        self.pending = cell
        self._refresh(old)

    def clear(self):
        old = self._content_rect()
        self.segments = []
        self.pending = None
        self._refresh(old)

    # ---------------- 绘制 ----------------
    def paint(self, painter, option, widget=None):
        if self.doc is None or (not self.segments and self.pending is None):
            return
        exposed = option.exposedRect
        scale = painter.worldTransform().m11() or 1.0
        s = 1.0 / max(abs(scale), 0.05)   # 屏幕像素 → 场景单位
        self._scale = scale               # 缓存给脏区算文字矩形
        painter.save()
        font = QFont()
        font.setPixelSize(max(10, round(12 * s)))
        painter.setFont(font)

        # 已完成的段才画成线；pending（第一点）只画一个点，避免第三点出现
        # "线突然冒出来 / 第四点线又挪位置"的错觉。
        for seg in self.segments:
            c0, r0, c1, r1 = seg
            p0, p1 = self._cell_center(c0, r0), self._cell_center(c1, r1)
            line = QRectF(p0, p1).normalized()
            if not exposed.intersects(line.adjusted(-40 * s, -40 * s, 40 * s, 40 * s)):
                continue
            color = QColor(SELECT)
            color.setAlpha(235)

            # 测线（cosmetic 宽度不随缩放变细/变粗）
            pen = QPen(color, 2.0 * s)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(p0, p1)

            # 两端格心方块
            painter.setBrush(QColor(SELECT))
            half = 4.0 * s
            for p in (p0, p1):
                painter.drawRect(QRectF(p.x() - half, p.y() - half,
                                        2 * half, 2 * half))

            # 等分标记：parts-1 个琥珀方点
            marks = split_cells(c0, r0, c1, r1, self.parts)
            painter.setPen(QPen(_MARK, 1.4 * s))
            painter.setBrush(QColor(_MARK))
            mh = 3.0 * s
            for mc, mr in marks:
                mp = self._cell_center(mc, mr)
                painter.drawRect(QRectF(mp.x() - mh, mp.y() - mh,
                                        2 * mh, 2 * mh))

            # 中点文字：格数（含两端）+ 每段平均
            text = segment_label_text(c0, r0, c1, r1, self.parts)
            tr = segment_label_rect(p0, p1, text, scale)
            painter.setPen(Qt.NoPen)
            painter.setBrush(_TEXT_BG)
            painter.drawRect(tr)
            painter.setPen(QPen(QColor(235, 242, 250), 1))
            painter.drawText(tr, Qt.AlignCenter, text)

        # 起点：点下第一格后只显示一个醒目的方块点，等第二格点击才连线。
        if self.pending is not None:
            pp = self._cell_center(*self.pending)
            if exposed.intersects(self._point_rect(self.pending)):
                painter.setPen(QPen(QColor(SELECT), 1.5 * s))
                painter.setBrush(QColor(SELECT))
                ph = 6.0 * s
                painter.drawRect(QRectF(pp.x() - ph, pp.y() - ph,
                                        2 * ph, 2 * ph))

        # 多段总计：平均每段格数（画在最后一段中点下方）
        if len(self.segments) > 1:
            last = self._seg_rect(self.segments[-1])
            anchor = QPointF(last.center().x(), last.bottom() - 6 * s)
            text = summary_label_text(len(self.segments), self.segments, self.parts)
            tr = summary_label_rect(anchor, text, scale)
            painter.setPen(Qt.NoPen)
            painter.setBrush(_TEXT_BG)
            painter.drawRect(tr)
            painter.setPen(QPen(QColor(255, 205, 70), 1))
            painter.drawText(tr, Qt.AlignCenter, text)
        painter.restore()


__all__ = ["RulerOverlay", "cell_distance", "split_cells", "segment_average"]
