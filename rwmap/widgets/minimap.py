"""右侧迷你地图：整张地图缩小概览 + 当前视口框，点击/拖动可平移主视图。

- ``MiniMap`` 把文档的全部瓦片层合成到一张缩略 QPixmap（按最长边 ≤ 512px 渲染，
  打开 / 换图 / 结构变化时全量重绘；编辑后按脏格局部增量重绘，不再每轮
  防抖都逐格扫描整张地图）；
- 视口框每帧从主视图真实可视区域反算，平移/缩放实时跟随；
- 鼠标左键按下 / 拖动 → 把主视图中心移到对应场景坐标。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDockWidget, QWidget

from ..tilesets.tile_cache import cache_version, get_pixmap
from ..theme import ASH, INK

_BG = QColor(INK)
_VP = QColor(120, 200, 255)          # 视口框描边（功能彩色，按需求保留）
_VP_FILL = QColor(120, 200, 255, 40)
_OVERVIEW_MAX = 512                  # 概览图最长边像素上限


class MiniMap(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = None
        self._view = None
        self._overview: QPixmap | None = None
        self._scale = 1.0             # 场景像素 → 概览像素
        self._drawn: QRect | None = None   # 概览在 widget 中实际绘制的矩形
        self._widget_scale = 1.0      # 概览像素 → widget 像素（fit）
        self._dragging = False
        self._overview_version = -1   # 生成概览时的图集缓存代次
        self._dirty_cells: set[tuple[int, int]] = set()
        self._full_invalidate = True  # 结构变化 → 强制全量重建
        self.setMinimumSize(160, 120)
        self.setMouseTracking(True)

    # ---------------- 数据 ----------------
    def set_document(self, doc):
        self._doc = doc
        self._dirty_cells.clear()
        self._full_invalidate = True
        self.refresh_overview()

    def set_view(self, view):
        """绑定主视图，同步视口框并跟随平移 / 缩放。"""
        if self._view is not None:
            try:
                self._view.zoomChanged.disconnect(self.update)
                self._view.horizontalScrollBar().valueChanged.disconnect(self.update)
                self._view.verticalScrollBar().valueChanged.disconnect(self.update)
            except Exception:                 # noqa: BLE001
                pass
        self._view = view
        if view is not None:
            view.zoomChanged.connect(self.update)
            view.horizontalScrollBar().valueChanged.connect(self.update)
            view.verticalScrollBar().valueChanged.connect(self.update)
        self.update()

    def _draw_cells_region(self, painter, col0: int, row0: int,
                           col1: int, row1: int):
        """把 [col0..col1] x [row0..row1] 范围内的所有可见层画进概览。

        全量重建与局部增量共用这一套绘制语句（SmoothPixmapTransform、
        setOpacity、目标 / 源矩形），保证局部结果与全量逐像素一致。
        """
        doc = self._doc
        cw = doc.tile_w * self._scale
        ch = doc.tile_h * self._scale
        for layer in doc.tile_layers():
            if not layer.visible:
                continue
            painter.setOpacity(max(0.0, min(1.0, layer.opacity)))
            for row in range(row0, row1 + 1):
                base = row * layer.width
                for col in range(col0, col1 + 1):
                    raw = layer.cells[base + col]
                    if raw == 0:
                        continue
                    loc = doc.local_tile(raw)
                    if loc is None:
                        continue
                    ts, local_id = loc
                    pix = get_pixmap(ts, local_id, (False, False, False), doc.resolver)
                    if pix is None or pix.isNull():
                        continue
                    painter.drawPixmap(
                        QRectF(col * cw, row * ch, cw, ch), pix,
                        QRectF(0, 0, pix.width(), pix.height()))

    def refresh_overview(self):
        """全量重建概览：打开 / 换文档 / 图层结构 / 换图集后调用。"""
        doc = self._doc
        if doc is None:
            self._overview = None
            self.update()
            return
        map_w = doc.width * doc.tile_w
        map_h = doc.height * doc.tile_h
        if map_w <= 0 or map_h <= 0:
            self._overview = None
            self.update()
            return
        scale = _OVERVIEW_MAX / max(map_w, map_h)
        tw = max(1, int(map_w * scale))
        th = max(1, int(map_h * scale))
        img = QImage(tw, th, QImage.Format_ARGB32)
        img.fill(_BG)
        self._scale = scale          # _draw_cells_region 依赖它
        painter = QPainter(img)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self._draw_cells_region(painter, 0, 0, doc.width - 1, doc.height - 1)
        painter.end()
        self._overview = QPixmap.fromImage(img)
        self._overview_version = cache_version()
        self._dirty_cells.clear()
        self._full_invalidate = False
        self.update()

    # ---------------- 编辑后的增量更新 ----------------
    def note_dirty_cells(self, cells):
        """记录本轮编辑改动的格；防抖触发时只重画这些格附近。"""
        if cells:
            self._dirty_cells.update((c, r) for c, r in cells
                                     if isinstance(c, int) and isinstance(r, int))

    def invalidate_full(self):
        """图层可见性 / 透明度 / 结构 / 换图集等：下一次刷新强制全量。"""
        self._full_invalidate = True

    def take_dirty_cells(self):
        cells, self._dirty_cells = self._dirty_cells, set()
        return cells

    def refresh_cells(self, cells):
        """只重画脏格附近的一小块概览；任何失效条件成立则回退全量。"""
        doc = self._doc
        if (doc is None or self._overview is None or self._overview.isNull()
                or self._full_invalidate
                or self._overview_version != cache_version()):
            self.refresh_overview()
            return
        if not cells:
            self.update()
            return
        cw = doc.tile_w * self._scale
        ch = doc.tile_h * self._scale
        # 外扩一格：覆盖 drawPixmap 缩放溢出，且相邻格一起重画，
        # 让 clip 边界处的绘制结果与全量重建完全一致。
        col0 = max(0, min(c for c, _ in cells) - 1)
        col1 = min(doc.width - 1, max(c for c, _ in cells) + 1)
        row0 = max(0, min(r for _, r in cells) - 1)
        row1 = min(doc.height - 1, max(r for _, r in cells) + 1)
        px0 = max(0, int(col0 * cw))
        py0 = max(0, int(row0 * ch))
        px1 = min(self._overview.width(), int((col1 + 1) * cw) + 1)
        py1 = min(self._overview.height(), int((row1 + 1) * ch) + 1)
        if px1 <= px0 or py1 <= py0:
            self.update()
            return
        clip = QRect(px0, py0, px1 - px0, py1 - py0)
        # 与全量重建一样画在 QImage 上（而不是直接画 QPixmap），
        # 避免设备格式差异造成颜色偏差。
        img = self._overview.toImage()
        painter = QPainter(img)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setClipRect(clip)
        painter.fillRect(clip, _BG)
        self._draw_cells_region(painter, col0, row0, col1, row1)
        painter.end()
        self._overview = QPixmap.fromImage(img)
        self.update()

    # ---------------- 视口框 ----------------
    def _viewport_scene_rect(self) -> QRectF | None:
        view = self._view
        if view is None or view._doc is None:
            return None
        vp = view.viewport()
        if vp.width() < 2 or vp.height() < 2:
            return None
        tl = view.mapToScene(0, 0)
        br = view.mapToScene(vp.width(), vp.height())
        return QRectF(tl.x(), tl.y(), br.x() - tl.x(), br.y() - tl.y())

    def _fit_rect(self) -> QRect:
        if self._overview is None or self._overview.isNull():
            return QRect(0, 0, self.width(), self.height())
        w = self.width()
        h = self.height()
        iw, ih = self._overview.width(), self._overview.height()
        s = min(w / iw, h / ih)
        dw, dh = int(iw * s), int(ih * s)
        dx, dy = (w - dw) // 2, (h - dh) // 2
        return QRect(dx, dy, dw, dh)

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), _BG)
        if self._overview is None or self._overview.isNull():
            painter.setPen(QColor(ASH))
            painter.drawText(self.rect(), Qt.AlignCenter, "无地图")
            return
        drawn = self._fit_rect()
        self._drawn = drawn
        self._widget_scale = drawn.width() / self._overview.width()
        painter.drawPixmap(drawn, self._overview)
        # 视口框：场景坐标 → 概览像素 → widget 像素
        vp = self._viewport_scene_rect()
        if vp is not None and self._scale > 0 and self._widget_scale > 0:
            x = drawn.x() + vp.x() * self._scale * self._widget_scale
            y = drawn.y() + vp.y() * self._scale * self._widget_scale
            w = vp.width() * self._scale * self._widget_scale
            h = vp.height() * self._scale * self._widget_scale
            rect = QRect(int(x), int(y), max(2, int(w)), max(2, int(h)))
            painter.fillRect(rect, _VP_FILL)
            painter.setPen(QPen(_VP, 2))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setPen(QPen(QColor(ASH)))
        painter.drawRect(drawn.adjusted(0, 0, -1, -1))

    # ---------------- 交互：点击 / 拖动平移主视图 ----------------
    def _scene_point(self, pos):
        if self._drawn is None or self._overview is None:
            return None
        d = self._drawn
        if not (d.left() <= pos.x() <= d.right() and d.top() <= pos.y() <= d.bottom()):
            return None
        ox = (pos.x() - d.x()) / self._widget_scale
        oy = (pos.y() - d.y()) / self._widget_scale
        return ox / self._scale, oy / self._scale      # 场景像素坐标

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self._view is None:
            return super().mousePressEvent(event)
        sp = self._scene_point(event.position().toPoint())
        if sp is None:
            return
        self._dragging = True
        self._view.centerOn(sp[0], sp[1])
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and self._view is not None:
            sp = self._scene_point(event.position().toPoint())
            if sp is not None:
                self._view.centerOn(sp[0], sp[1])
                self.update()
            event.accept()
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        return super().mouseReleaseEvent(event)


class MiniMapDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("迷你地图", parent)
        self.minimap = MiniMap()
        self.setWidget(self.minimap)
        from .dock_chrome import install_title_bar
        install_title_bar(self)
