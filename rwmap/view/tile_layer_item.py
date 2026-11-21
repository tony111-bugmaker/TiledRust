"""单层整块绘制：按 exposedRect 逐格贴图，不创建上万个 QGraphicsItem。

性能：缩放到整图可见时，160x160 = 25600 格/层逐格 drawPixmap 太慢。
对「格子多 + 缩小视图」的层预渲染整层缓存，之后每帧只做 1 次 drawPixmap；
编辑时仅重画受影响的那几格。缓存档位（1/2、1/4、1/8、1/16）按
视图缩放 × 显示器 devicePixelRatio 由 render_lod.choose_render_lod 决定：
缩得越小越粗（省内存和填充），高分屏则保留更多细节。

缓存正确性：
- 图块查询用小 LRU（旧实现只记「上一个 raw」，交错地形每格都 miss）；
- 所有缓存都带 tile_cache.cache_version() 代次，换图集图片后自动失效，
  不会残留旧图；
- 每档缓存独立保存；局部失效会同步所有已建档位，切档不露旧画面；
- invalidate_all() 不丢掉 mini：只标记内容过期，把全量重画推迟到
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
from ..tilesets.tile_cache import cache_version, get_pixmap_fitted
from .render_lod import choose_render_lod, viewport_pixel_ratio


def get_pixmap(ts, local_id, flags, tw, th, resolver=None):
    """模块内取图块入口：等比缩放塞进 tw×th 的地图格子。

    保留 ``get_pixmap`` 这个名字是为了让既有性能/缓存测试能继续 mock 本模块
    的取图入口；行为已升级为等比适配格子尺寸。
    """
    return get_pixmap_fitted(ts, local_id, flags, tw, th, resolver)

_MINI_SCALE = 0.5          # 兼容旧调用的默认 LOD；实际档位由 render_lod 决定
_MINI_VIEW_SCALE = 0.75    # 兼容旧调用；不再作为固定阈值使用
_MINI_MIN_CELLS = 400      # 稀疏层（Items / Units）直接画更省内存
_PIX_LRU_CAP = 16          # 交错地形常用的 raw 数量足够，命中率接近 100%

_PLACEHOLDER = None


def _placeholder(w=20, h=20) -> QPixmap:
    """缺图占位；按目标格子尺寸生成，占满整格（不留缝、不溢出）。"""
    global _PLACEHOLDER
    if _PLACEHOLDER is None or _PLACEHOLDER.width() != w or _PLACEHOLDER.height() != h:
        pix = QPixmap(max(1, w), max(1, h))
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
        # 多级缩略缓存：LOD 比例 -> QPixmap。缩放越远用越粗的档位，
        # 每档只在首次用到时构建；编辑局部失效时所有已建档位一起更新。
        self._minis: dict[float, QPixmap] = {}
        self._mini_versions: dict[float, int] = {}
        self._mini_dirty: set[float] = set()
        self._lod = _MINI_SCALE         # 当前档位（兼容旧测试的单缓存属性）
        self._eligible_cache = None    # 惰性算「是否值得用 mini」
        self._pix_lru = OrderedDict()  # raw -> QPixmap
        self._pix_version = -1
        self._pix_cell_size = None     # doc 格子尺寸变化后 LRU 内的缩放图需重建

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
        cell_size = (self.doc.tile_w, self.doc.tile_h)
        if self._pix_version != version or self._pix_cell_size != cell_size:
            self._pix_lru.clear()
            self._pix_version = version
            self._pix_cell_size = cell_size
        lru = self._pix_lru
        if raw in lru:
            lru.move_to_end(raw)
            return lru[raw]
        gid, h, v, d = split_gid(raw)
        located = self.doc.local_tile(gid)
        tw, th = self.doc.tile_w, self.doc.tile_h
        if located is None:
            pix = _placeholder(tw, th)
        else:
            ts, local_id = located
            # 图块等比缩放居中塞进一个地图格子（图集尺寸 != 地图格子尺寸时
            # 不再溢出邻格 / 留缝）。doc 格子尺寸变化也要让 LRU 失效。
            pix = get_pixmap(ts, local_id, (h, v, d), tw, th,
                             self.doc.resolver) or _placeholder(tw, th)
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
    # 旧测试/外部诊断仍按「单张 mini」读取这三个属性：映射到当前档位。
    @property
    def _mini(self) -> QPixmap | None:
        return self._minis.get(self._lod)

    @property
    def _mini_version(self) -> int:
        return self._mini_versions.get(self._lod, -1)

    def _is_mini_eligible(self) -> bool:
        """只在第一次需要判断时统计一次，语义与旧的构造期统计一致。"""
        if self._eligible_cache is None:
            self._eligible_cache = (
                sum(1 for raw in self.layer.cells if raw) >= _MINI_MIN_CELLS)
        return self._eligible_cache

    def _paint_mini_full(self, pix: QPixmap, lod: float | None = None):
        """把整层按指定档位重画进 pix（新建与复用共用同一套画法）。"""
        s = self._lod if lod is None else lod
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.scale(s, s)
        self._draw_cells(painter, 0, 0, self.layer.width - 1, self.layer.height - 1)
        painter.end()

    def _build_mini(self, lod: float | None = None):
        s = self._lod if lod is None else lod
        tw, th = self.doc.tile_w, self.doc.tile_h
        w = max(1, math.ceil(self.layer.width * tw * s))
        h = max(1, math.ceil(self.layer.height * th * s))
        pix = QPixmap(w, h)
        self._paint_mini_full(pix, s)
        self._minis[s] = pix
        self._mini_versions[s] = cache_version()
        self._mini_dirty.discard(s)

    def _sync_mini(self, lod: float | None = None):
        """贴 mini 前确保内容与图集代次一致；全量重画是懒执行的。"""
        s = self._lod if lod is None else lod
        version = cache_version()
        if s not in self._minis or self._mini_versions.get(s) != version:
            self._build_mini(s)
        elif s in self._mini_dirty:
            self._paint_mini_full(self._minis[s], s)
            self._mini_versions[s] = version
            self._mini_dirty.discard(s)

    def _draw_mini(self, painter: QPainter, rect: QRectF,
                   lod: float | None = None):
        s = self._lod if lod is None else lod
        self._sync_mini(s)
        mini = self._minis[s]
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
        version = cache_version()
        # 所有已建档位都要跟上编辑结果：否则缩放切档时会露出旧画面。
        for s, pix in list(self._minis.items()):
            if self._mini_versions.get(s) != version:
                # 换图集：交给 _sync_mini 重建，防串图。
                self._minis.pop(s, None)
                self._mini_versions.pop(s, None)
                self._mini_dirty.discard(s)
                continue
            if s in self._mini_dirty:
                # 整层失效后又有局部编辑：先同步到当前数据，再做局部更新。
                self._paint_mini_full(pix, s)
                self._mini_versions[s] = version
                self._mini_dirty.discard(s)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.scale(s, s)
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
        # 旧实现无条件丢掉像素图：下次低缩放 paint 又要逐格画满整图。
        # 这里保留所有档位的 QPixmap，只标记「内容过期」，把重画推迟到
        # _sync_mini / invalidate_cells 真正需要的时候；换图集仍然强制重建。
        self._pix_lru.clear()
        self._pix_version = -1
        version = cache_version()
        for s in list(self._minis):
            if self._mini_versions.get(s) == version:
                self._mini_dirty.add(s)
            else:
                self._minis.pop(s, None)
                self._mini_versions.pop(s, None)
                self._mini_dirty.discard(s)
        self.update(self.boundingRect())

    # ---------------- 绘制 ----------------
    def paint(self, painter: QPainter, option, widget=None):
        layer = self.layer
        tw, th = self.doc.tile_w, self.doc.tile_h
        rect = option.exposedRect.intersected(self.boundingRect())
        if rect.isEmpty():
            return
        scale = abs(painter.worldTransform().m11()) or 1.0
        if self._is_mini_eligible():
            # 粗糙程度 = 一格在屏幕上占的物理像素预算：
            # 视图缩放 × 显示器像素比。高分屏同倍率下保留更多细节，
            # 小窗口 / 缩小视图则自动降到更粗的档位。
            lod = choose_render_lod(scale, viewport_pixel_ratio(widget))
            if lod < 1.0:
                if lod != self._lod:
                    self._lod = lod
                    # 视口里其它区域还是旧档位的像素，整层标脏一次统一换档。
                    self.update(self.boundingRect())
                self._draw_mini(painter, rect, lod)
                return
        col0 = max(0, int(rect.left() // tw))
        col1 = min(layer.width - 1, int(rect.right() // tw))
        row0 = max(0, int(rect.top() // th))
        row1 = min(layer.height - 1, int(rect.bottom() // th))
        if col1 < col0 or row1 < row0:
            return
        self._draw_cells(painter, col0, row0, col1, row1)
