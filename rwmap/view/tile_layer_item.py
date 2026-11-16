"""单层整块绘制：按 exposedRect 逐格贴图，不创建上万个 QGraphicsItem。

性能：缩放到整图可见时，160x160 = 25600 格/层逐格 drawPixmap 太慢。
对「格子多 + 视野缩放 <= 0.75」的层预渲染一张半分辨率缓存，
之后每帧只做 1 次 drawPixmap；编辑时仅重画受影响的那几格。

缓存正确性：
- 图块查询用小 LRU（旧实现只记「上一个 raw」，交错地形每格都 miss）；
- 所有缓存都带 tile_cache.cache_version() 代次，换图集图片后自动失效，
  不会残留旧图；
- invalidate_all() 不再丢掉 mini：只标记内容过期，把全量重画推迟到
  真正要贴 mini 的时候（或后续局部失效时一次性同步），避免「整层失效 →
  下次低缩放 paint 又重建 25600 格」的连锁。
"""
from __future__ import annotations

import math
from collections import OrderedDict

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsItem

from ..document.gid import split_gid
from ..document.layer import TileLayer
from ..tilesets.tile_cache import cache_version, get_pixmap

_MINI_SCALE = 0.5          # 缓存分辨率相对场景坐标
_MINI_VIEW_SCALE = 0.75    # 视野缩放 <= 该值走缓存
_MINI_MIN_CELLS = 400      # 稀疏层（Items / Units）直接画更省内存
_PIX_LRU_CAP = 16          # 交错地形常用的 raw 数量足够，命中率接近 100%

_PLACEHOLDER = None


def _placeholder() -> QPixmap:
    global _PLACEHOLDER
    if _PLACEHOLDER is None:
        pix = QPixmap(20, 20)
        pix.fill(QColor(90, 40, 90, 255))
        _PLACEHOLDER = pix
    return _PLACEHOLDER


class TileLayerItem(QGraphicsItem):
    def __init__(self, layer: TileLayer, doc, z_value: int = 0):
        super().__init__()
        self.layer = layer
        self.doc = doc
        self.setZValue(z_value)
        self.setOpacity(layer.opacity)
        self.setVisible(layer.visible)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)
        self._mini: QPixmap | None = None
        self._mini_version = -1        # 生成 mini 时的图集缓存代次
        self._mini_dirty = False       # invalidate_all 后延迟全量重画
        self._eligible_cache = None    # 惰性算「是否值得用 mini」
        self._pix_lru = OrderedDict()  # raw -> QPixmap
        self._pix_version = -1

    def boundingRect(self) -> QRectF:
        return QRectF(
            0, 0,
            self.layer.width * self.doc.tile_w,
            self.layer.height * self.doc.tile_h,
        )

    # ---------------- 图块查询 ----------------
    def _pixmap_for(self, raw: int) -> QPixmap:
        # 大片水面 / 草地 gid 相同，但交错地形 A/B/A/B 会让「只记上一个」
        # 每格都 miss。小 LRU 命中时省掉 split_gid + local_tile + get_pixmap。
        version = cache_version()
        if self._pix_version != version:
            self._pix_lru.clear()
            self._pix_version = version
        lru = self._pix_lru
        if raw in lru:
            lru.move_to_end(raw)
            return lru[raw]
        gid, h, v, d = split_gid(raw)
        located = self.doc.local_tile(gid)
        if located is None:
            pix = _placeholder()
        else:
            ts, local_id = located
            pix = get_pixmap(ts, local_id, (h, v, d), self.doc.resolver) or _placeholder()
        lru[raw] = pix
        if len(lru) > _PIX_LRU_CAP:
            lru.popitem(last=False)
        return pix

    def _draw_cells(self, painter: QPainter, col0: int, row0: int, col1: int, row1: int):
        layer = self.layer
        tw, th = self.doc.tile_w, self.doc.tile_h
        cells = layer.cells
        for row in range(row0, row1 + 1):
            base = row * layer.width
            y = row * th
            for col in range(col0, col1 + 1):
                raw = cells[base + col]
                if raw:
                    painter.drawPixmap(col * tw, y, self._pixmap_for(raw))

    # ---------------- 缩略缓存 ----------------
    def _is_mini_eligible(self) -> bool:
        """只在第一次需要判断时统计一次，语义与旧的构造期统计一致。"""
        if self._eligible_cache is None:
            self._eligible_cache = (
                sum(1 for raw in self.layer.cells if raw) >= _MINI_MIN_CELLS)
        return self._eligible_cache

    def _paint_mini_full(self, pix: QPixmap):
        """把整层按半分辨率重画进 pix（新建与复用共用同一套画法）。"""
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.scale(_MINI_SCALE, _MINI_SCALE)
        self._draw_cells(painter, 0, 0, self.layer.width - 1, self.layer.height - 1)
        painter.end()

    def _build_mini(self):
        tw, th = self.doc.tile_w, self.doc.tile_h
        w = max(1, math.ceil(self.layer.width * tw * _MINI_SCALE))
        h = max(1, math.ceil(self.layer.height * th * _MINI_SCALE))
        pix = QPixmap(w, h)
        self._paint_mini_full(pix)
        self._mini = pix
        self._mini_version = cache_version()
        self._mini_dirty = False

    def _sync_mini(self):
        """贴 mini 前确保内容与图集代次一致；全量重画是懒执行的。"""
        version = cache_version()
        if self._mini is None or self._mini_version != version:
            self._build_mini()
        elif self._mini_dirty:
            self._paint_mini_full(self._mini)
            self._mini_version = version
            self._mini_dirty = False

    def _draw_mini(self, painter: QPainter, rect: QRectF):
        self._sync_mini()
        mini = self._mini
        s = _MINI_SCALE
        src = QRectF(rect.left() * s, rect.top() * s,
                     rect.width() * s, rect.height() * s)
        src = src.intersected(QRectF(0, 0, mini.width(), mini.height()))
        if src.isEmpty():
            return
        target = QRectF(src.left() / s, src.top() / s,
                        src.width() / s, src.height() / s)
        painter.drawPixmap(target, mini, src)

    # ---------------- 局部失效 ----------------
    def invalidate_cells(self, cells):
        """编辑/撤销后调用：只重画这几格，且只把这几格标脏。"""
        if not cells:
            return
        layer = self.layer
        tw, th = self.doc.tile_w, self.doc.tile_h
        valid = [(col, row) for col, row in cells if layer.in_bounds(col, row)]
        if not valid:
            return
        if self._mini is not None:
            version = cache_version()
            if self._mini_version != version:
                self._mini = None      # 换图集：交给 _sync_mini 重建，防串图
            elif self._mini_dirty:
                # 整层失效后又有局部编辑：先把 mini 同步到当前数据，再做局部更新。
                self._paint_mini_full(self._mini)
                self._mini_version = version
                self._mini_dirty = False
        if self._mini is not None:
            painter = QPainter(self._mini)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.scale(_MINI_SCALE, _MINI_SCALE)
            for col, row in valid:
                box = QRectF(col * tw, row * th, tw, th)
                painter.setCompositionMode(QPainter.CompositionMode_Clear)
                painter.fillRect(box, Qt.transparent)
                painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                raw = layer.cells[layer.index(col, row)]
                if raw:
                    painter.drawPixmap(col * tw, row * th, self._pixmap_for(raw))
            painter.end()
        dirty = QRectF()
        for col, row in valid:
            dirty = dirty.united(QRectF(col * tw - 1, row * th - 1, tw + 2, th + 2))
        self.update(dirty)

    def invalidate_all(self):
        # 旧实现无条件 self._mini = None：下次低缩放 paint 又要逐格画满整图。
        # 这里保留 QPixmap，只标记「内容过期」，把重画推迟到 _sync_mini /
        # invalidate_cells 真正需要的时候；换图集仍然强制重建。
        self._pix_lru.clear()
        self._pix_version = -1
        if self._mini is not None and self._mini_version == cache_version():
            self._mini_dirty = True
        else:
            self._mini = None
            self._mini_dirty = False
        self.update(self.boundingRect())

    # ---------------- 绘制 ----------------
    def paint(self, painter: QPainter, option, widget=None):
        layer = self.layer
        tw, th = self.doc.tile_w, self.doc.tile_h
        rect = option.exposedRect.intersected(self.boundingRect())
        if rect.isEmpty():
            return
        scale = painter.worldTransform().m11()
        if self._is_mini_eligible() and scale <= _MINI_VIEW_SCALE:
            self._draw_mini(painter, rect)
            return
        col0 = max(0, int(rect.left() // tw))
        col1 = min(layer.width - 1, int(rect.right() // tw))
        row0 = max(0, int(rect.top() // th))
        row1 = min(layer.height - 1, int(rect.bottom() // th))
        if col1 < col0 or row1 < row0:
            return
        self._draw_cells(painter, col0, row0, col1, row1)
