"""对象层绘制：Tiled 全形状 + 名称标签 + 8 个黄色缩放箭头 + 拖拽预览。

对象层是「一层一个 QGraphicsItem」，Qt 的 BSP 索引只能剔除整层，
帮不到层内对象。对象数量大时（实测 2000 个矩形 ~190ms/帧），瓶颈在
「每帧线性遍历全部对象 + 逐对象构造 Pen/Font + 逐对象 drawText +
图片对象每帧重新 transformed()」。这里按四层优化：

1. 均匀网格空间索引：paint 只遍历与 exposedRect 相交的桶；
2. 变形图片缓存：翻转/旋转后的 QPixmap 按 (路径, 翻转, 角度) 缓存；
3. Pen / Font 提为模块常量，不再逐对象构造；
4. 缩小到 0.5 倍以下自动隐藏名称标签（文字缩放后本就不可读）。

刷新仍走局部脏矩形；对象移动时索引按对象增量更新，不逐帧全量重建。
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import QGraphicsItem

from ..document.gid import split_gid
from ..document.layer import ObjectLayer
from ..document.map_object import MapObject
from ..document.properties import EDITOR_GROUP_PROPERTY
from ..tilesets.tile_cache import get_pixmap

_IMAGE_CACHE: dict[str, QPixmap] = {}
# 翻转 / 旋转后的图片：QPixmap.transformed(SmoothTransformation) 每帧重做
# 会直接吃掉整帧预算（大图尤其明显），按变形参数缓存结果。
_TRANSFORM_CACHE: dict[tuple, QPixmap] = {}
_MAX_TRANSFORM_CACHE = 256

# 空间索引桶大小（场景单位）。20px 格子约 12.8 格一桶；视口 800x600
# 同倍率下只碰约十几个桶，而不是遍历全部对象。
_GRID_CELL = 256.0
_MAX_CELLS_PER_OBJECT = 64   # 超大对象放进 overflow，避免一张图占满所有桶

# 缩到 0.5 倍以下文字不足 8px，画出来只是一团糊且 drawText 很贵。
_LABEL_MIN_SCALE = 0.5

# 大量对象时的整层缓存：对象增删不频繁、逐帧全量重绘却很贵。
# 小场景 (<200) 保持逐对象路径，避免缓存维护成本盖过收益。
_CACHE_MIN_OBJECTS = 200
_CACHE_MAX_SIDE = 4096

# 超大场景（对象分布跨度 > _CACHE_MAX_SIDE，例如真实 330x265 图跨度达
# 1.4 万像素）走分块 LOD 缓存：整层一张图会超像素上限而建不起来，退化为
# 每帧逐对象绘制（实测缩放 0.25 时 208ms/帧）。分块后只渲染视口相交的块，
# 缓存命中帧降到 ~5ms。
_TILE_CACHE = 640.0                    # 每块场景边长
_TILE_CACHE_MAX = 256                  # 块缓存上限（LRU 淘汰）
_TILE_LODS = (1.0, 0.5, 0.25, 0.125)   # 与瓦片层 LOD 档位一致
_TILE_CACHE_MIN_SPAN = 4200.0          # 跨度超此值才启用分块（单图上限 4096）

_HALIGN = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
           "right": Qt.AlignRight, "justify": Qt.AlignJustify}
_VALIGN = {"top": Qt.AlignTop, "center": Qt.AlignVCenter,
           "bottom": Qt.AlignBottom}


def object_rect(obj, tile_w: int = 20, tile_h: int = 20) -> QRectF:
    x, y, w, h = obj.bounds(tile_w, tile_h)
    return QRectF(x, y, w, h)


def _cells_for(rect: QRectF) -> tuple[tuple[int, int], ...]:
    """矩形覆盖的网格桶坐标（含标签 / 箭头留白后传入）。"""
    if rect.isEmpty():
        return ()
    gx0 = math.floor(rect.left() / _GRID_CELL)
    gx1 = math.floor(rect.right() / _GRID_CELL)
    gy0 = math.floor(rect.top() / _GRID_CELL)
    gy1 = math.floor(rect.bottom() / _GRID_CELL)
    return tuple((gx, gy) for gy in range(gy0, gy1 + 1)
                 for gx in range(gx0, gx1 + 1))


def _choose_tile_lod(scale: float) -> float:
    """按视图缩放选缓存 LOD 档位；缩得越小缓存分辨率越低（省内存、建块快）。"""
    budget = max(abs(scale) or 1.0, 0.02)
    for lod in _TILE_LODS:
        if lod <= budget:
            return lod
    return _TILE_LODS[-1]


@lru_cache(maxsize=256)
def group_color(name: str) -> QColor:
    """按组名稳定哈希出一个明显区别于对象黄（255,200,60）的颜色。"""
    h = 0
    for ch in name.encode("utf-8", "ignore"):
        h = (h * 31 + ch) & 0xFFFFFFFF
    return QColor.fromHsv(h % 360, 150, 235)


# ---- 对象框与缩放箭头 ----
# 对象轮廓本来就是黄色；选中时既不变蓝也不加边框线，只多出 8 个同色箭头。
OBJECT_YELLOW = QColor(255, 200, 60, 255)
_LABEL_YELLOW = QColor(255, 235, 160, 230)

# 绘制状态常量：paint 里逐对象 setPen/setFont 构造临时对象是明确热点。
# cosmetic 笔宽恒为 1 设备像素：过去 width=1 是「场景坐标 1 单位」，视图缩到
# 0.5/0.25 时线宽摊成 0.5/0.25 设备像素，抗锯齿把 alpha 190 一路稀释到 95/47，
# 用户看到「缩小后线变透明」。cosmetic 让线宽不随缩放变化，各倍率下 alpha 恒定。
_PEN_NORMAL = QPen(QColor(255, 200, 60, 190), 1)
_PEN_SELECTED = QPen(QColor(255, 200, 60, 255), 1)
_PEN_PREVIEW = QPen(QColor(255, 200, 60, 210), 2, Qt.DashLine)
for _pen in (_PEN_NORMAL, _PEN_SELECTED, _PEN_PREVIEW):
    _pen.setCosmetic(True)
_LABEL_FONT = QFont()
_GROUP_FONT = QFont()
_GROUP_FONT.setBold(True)

# 纯黄色双向箭头，屏幕固定尺寸；放大到第九轮 9px 的 1.5 倍。
HANDLE_NAMES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
HANDLE_VIEW_SIZE = 13.5

# 8 个箭头的方向（单位向量），四角斜向、四边水平/垂直。
_SQRT2 = math.sqrt(2.0)
_ARROW_DIRS = {
    "nw": (-1.0 / _SQRT2, -1.0 / _SQRT2),
    "n": (0.0, -1.0),
    "ne": (1.0 / _SQRT2, -1.0 / _SQRT2),
    "e": (1.0, 0.0),
    "se": (1.0 / _SQRT2, 1.0 / _SQRT2),
    "s": (0.0, 1.0),
    "sw": (-1.0 / _SQRT2, 1.0 / _SQRT2),
    "w": (-1.0, 0.0),
}


def handle_size(scale: float) -> float:
    """场景坐标下箭头的边长；scale 是视图变换的 m11。"""
    return HANDLE_VIEW_SIZE / max(abs(scale) or 1.0, 0.02)


def effective_handle_size(rect: QRectF, scale: float = 1.0) -> float:
    """小对象 / 缩小视图时收窄箭头，避免 8 个箭头互相盖住整个对象。"""
    return min(handle_size(scale),
               max(6.0, min(rect.width(), rect.height()) / 2.5))


def handle_positions(rect: QRectF, scale: float = 1.0) -> dict[str, QPointF]:
    cx = (rect.left() + rect.right()) / 2
    cy = (rect.top() + rect.bottom()) / 2
    return {
        "nw": QPointF(rect.left(), rect.top()),
        "n": QPointF(cx, rect.top()),
        "ne": QPointF(rect.right(), rect.top()),
        "e": QPointF(rect.right(), cy),
        "se": QPointF(rect.right(), rect.bottom()),
        "s": QPointF(cx, rect.bottom()),
        "sw": QPointF(rect.left(), rect.bottom()),
        "w": QPointF(rect.left(), cy),
    }


def handle_hit(rect: QRectF, x: float, y: float, scale: float = 1.0) -> str | None:
    """命中测试用与绘制相同的屏幕尺寸，返回箭头名或 None。"""
    reach = effective_handle_size(rect, scale) * 0.85
    nearest = None
    best = reach
    for name, point in handle_positions(rect, scale).items():
        dx, dy = abs(x - point.x()), abs(y - point.y())
        if dx <= reach and dy <= reach and max(dx, dy) < best:
            best, nearest = max(dx, dy), name
    return nearest


def handle_hit_arrow(rect: QRectF, x: float, y: float,
                     scale: float = 1.0) -> str | None:
    """命中「画在框外的黄色双向箭头」，返回箭头名或 None。

    ``draw_selection()`` 把整支箭头沿外法线挪了 ``half``（内尖仍贴在框线上），
    所以命中中心也必须跟着挪；直接用旧的 ``handle_hit()``（以框线锚点为中心）
    会让用户点箭头尖时落空，甚至误触发框内拖动。
    """
    side = effective_handle_size(rect, scale)
    half = side * 0.78
    reach = side * 0.9
    # 视图缩得越小，箭头在场景坐标里越大（屏幕固定 13.5px / scale）。若不加
    # 这道框内排除，命中方框会盖进对象内部，点一下对象就变成缩放、拖不动了。
    # 框内只允许贴着边线的一条窄带（约 4 屏幕像素）算命中，其余一律走拖动。
    tol = min(side * 0.15, 4.0 / max(scale, 0.02))
    inner = rect.adjusted(tol, tol, -tol, -tol)
    if inner.width() > 0 and inner.height() > 0 and inner.contains(QPointF(x, y)):
        return None
    nearest = None
    best = reach
    for name, point in handle_positions(rect, scale).items():
        ux, uy = _ARROW_DIRS[name]
        cx, cy = point.x() + ux * half, point.y() + uy * half
        dx, dy = abs(x - cx), abs(y - cy)
        if dx <= reach and dy <= reach and max(dx, dy) < best:
            best, nearest = max(dx, dy), name
    return nearest


def draw_selection(painter, rect: QRectF, scale: float = 1.0):
    """只画 8 个纯黄色双向缩放箭头。

    不画任何选中边框线、不画白色虚线框、不画手柄方块底：
    对象框本身就是黄色，选中态仅靠四角 / 四边的箭头表达。
    笔刷和填充都是 OBJECT_YELLOW，与矩形框完全同色。
    """
    side = effective_handle_size(rect, scale)
    half = side * 0.78          # 双向箭头半长
    head = side * 0.42          # 箭头三角长度
    wing = side * 0.50          # 箭头三角半宽
    shaft = side * 0.12         # 箭杆半宽
    inner = half - head

    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(OBJECT_YELLOW))
    for name, point in handle_positions(rect, scale).items():
        ux, uy = _ARROW_DIRS[name]   # 由矩形边线指向框外的单位向量
        nx, ny = -uy, ux
        # 整支箭头沿外向量挪半个身位：内尖正好顶在矩形线上，整支落在框外、
        # 不再覆盖矩形内部区域（at(-half) 处的内尖 = 原边线锚点）。
        cx = point.x() + ux * half
        cy = point.y() + uy * half

        def at(t, s):
            return QPointF(cx + ux * t + nx * s, cy + uy * t + ny * s)

        arrow = QPolygonF([
            at(-half, 0.0),
            at(-inner, -wing),
            at(-inner, -shaft),
            at(inner, -shaft),
            at(inner, -wing),
            at(half, 0.0),
            at(inner, wing),
            at(inner, shaft),
            at(-inner, shaft),
            at(-inner, wing),
        ])
        painter.drawPolygon(arrow)
    painter.restore()


def object_pixmap(doc, obj) -> QPixmap | None:
    """图块对象 / 图片对象的位图；失败返回 None，由调用方画占位框。"""
    if obj.shape == "tile" and obj.gid:
        gid, h, v, d = split_gid(obj.gid)
        located = doc.local_tile(gid) if doc is not None else None
        if located is None:
            return None
        ts, local_id = located
        return get_pixmap(ts, local_id, (h, v, d),
                          getattr(doc, "resolver", None))
    if obj.shape == "image" and obj.image_source:
        source = Path(obj.image_source)
        if not source.is_absolute() and doc is not None and doc.path is not None:
            source = Path(doc.path).parent / source
        key = str(source)
        if key not in _IMAGE_CACHE:
            _IMAGE_CACHE[key] = QPixmap(key)
        pix = _IMAGE_CACHE[key]
        if pix.isNull():
            return None
        # 图片对象的翻转 / 旋转：镜像用 scale(-1,1)，90° 旋转用 QTransform.rotate，
        # 缓存原始 pixmap，变形只在返回值上做，避免污染缓存 / 反复读盘。
        flip_h = getattr(obj, "image_flip_h", False)
        flip_v = getattr(obj, "image_flip_v", False)
        rotation = getattr(obj, "rotation", 0.0) or 0.0
        if flip_h or flip_v or rotation:
            tkey = (key, bool(flip_h), bool(flip_v), rotation)
            cached = _TRANSFORM_CACHE.get(tkey)
            if cached is None:
                cx, cy = pix.width() / 2.0, pix.height() / 2.0
                tx = QTransform()
                tx.translate(cx, cy)
                if rotation:
                    tx.rotate(rotation)
                if flip_h:
                    tx.scale(-1, 1)
                if flip_v:
                    tx.scale(1, -1)
                tx.translate(-cx, -cy)
                cached = pix.transformed(tx, Qt.SmoothTransformation)
                if len(_TRANSFORM_CACHE) >= _MAX_TRANSFORM_CACHE:
                    _TRANSFORM_CACHE.clear()
                _TRANSFORM_CACHE[tkey] = cached
            return cached
        return pix
    return None


def clear_image_cache() -> None:
    _IMAGE_CACHE.clear()
    _TRANSFORM_CACHE.clear()


class ObjectLayerItem(QGraphicsItem):
    def __init__(self, layer: ObjectLayer, doc, z_value: int = 0):
        super().__init__()
        self.layer = layer
        self.doc = doc
        self.selected: list = []      # 选中对象列表（支持框选多选）
        self._sel_ids: set = set()    # 选中对象的 id()，快速判等
        self.preview = None            # 拖拽中的临时对象
        self.show_nodes = False        # 「编辑节点」工具下显示顶点手柄
        self.show_names = True         # 「视图 → 显示对象名称」；默认保持历史行为
        self._scale = 1.0             # 最近一次绘制的视图缩放，脏矩形按它换算
        self._content_rect = None      # 缓存的包围盒（覆盖所有对象 + 地图，含地图外）
        # 层内空间索引：Qt 的 BSP 只能剔除整层，层内几千对象要自己分桶。
        self._grid: dict[tuple[int, int], list] = {}
        self._obj_cells: dict[int, tuple] = {}
        self._overflow: list = []       # 跨桶过多的大对象，每次查询都带上
        self._index_dirty = True
        # 大量对象时的整层场景坐标缓存（只含未选中对象，选中态单独覆盖）。
        self._obj_cache: QImage | None = None   # QImage 非预乘，避免文字边缘偏色
        self._cache_rect = QRectF()
        self._cache_dirty = False
        self._cache_labels = True
        # _cache_bounds() 要遍历全部对象做 united；这张真实图有 5500+ 对象、
        # 坐标跨度 1.4 万像素，每帧重算要 12ms 且结果几乎不变。缓存起来，
        # 只在增删（refresh_all）时失效，移动（refresh_rects）时只扩张。
        self._cb_rect: QRectF | None = None
        # 超大场景的分块 LOD 缓存：{(lod, bx, by): QImage}。每块是场景坐标
        # 固定网格，缩放只切 LOD 档，平移复用已有块。
        self._tile_cache: dict = {}
        self._tile_lru: list = []          # 最近使用的 key，超限淘汰
        self._tile_labels: bool = True     # 缓存块里是否烘焙了标签
        self._tile_dirty_all: bool = False # 增删后置位，下帧清空重建
        self._tile_span: float | None = None
        self.setZValue(z_value)
        self.setOpacity(layer.opacity)
        self.setVisible(layer.visible)
        # 必须开启：否则 paint() 拿不到可靠 exposedRect，逐对象视口裁剪会失效。
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)

    # ---------------- 几何（覆盖所有对象，含地图外） ----------------
    # 包围盒必须覆盖「所有对象实际范围」而非仅地图矩形：否则当视图缩放后落在地图
    # 矩形之外、但那里存在对象时，QGraphicsView 会判定 item 不相交而整层剔除 → 对象消失。
    _BOUNDS_MARGIN = 64.0   # 固定留白（场景单位），覆盖标签 / 缩放箭头溢出

    def _obj_bounds(self, obj) -> QRectF:
        """单个对象在绘制时会占用的区域（框 + 标签 + 箭头），用于裁剪与包围盒。"""
        r = object_rect(obj, self.doc.tile_w, self.doc.tile_h)
        m = effective_handle_size(r, self._scale) * 0.78 + 18
        return r.adjusted(-m, -m - 18, m, m)

    def _recompute_content_rect(self):
        tw, th = self.doc.tile_w, self.doc.tile_h
        rect = QRectF(0, 0, tw * self.doc.width, th * self.doc.height)
        for obj in self.layer.objects:
            rect = rect.united(self._obj_bounds(obj))
        if self.preview is not None:
            rect = rect.united(self._obj_bounds(self.preview))
        self._content_rect = rect

    # ---------------- 层内空间索引 ----------------
    def _rebuild_index(self):
        """全量分桶（仅在对象增删 / refresh_all 时调用，拖动走增量）。"""
        self._grid.clear()
        self._obj_cells.clear()
        self._overflow = []
        self._obj_order = {id(o): i for i, o in enumerate(self.layer.objects)}
        for obj in self.layer.objects:
            cells = _cells_for(self._obj_bounds(obj))
            if len(cells) > _MAX_CELLS_PER_OBJECT:
                self._overflow.append(obj)
                continue
            self._obj_cells[id(obj)] = cells
            for cell in cells:
                self._grid.setdefault(cell, []).append(obj)
        self._index_dirty = False

    def _move_in_index(self, obj, rect):
        """对象位置改变后只挪它自己占用的桶，不逐帧重建整张索引。"""
        oid = id(obj)
        old = self._obj_cells.pop(oid, ())
        for cell in old:
            bucket = self._grid.get(cell)
            if bucket is not None:
                bucket[:] = [o for o in bucket if id(o) != oid]
        # 注意 MapObject 是 dataclass（== 按字段比较），去重必须按身份。
        self._overflow = [o for o in self._overflow if id(o) != oid]
        cells = _cells_for(self._obj_bounds_of_rect(rect))
        if len(cells) > _MAX_CELLS_PER_OBJECT:
            self._overflow.append(obj)
            return
        self._obj_cells[oid] = cells
        for cell in cells:
            self._grid.setdefault(cell, []).append(obj)

    def _candidates(self, visible: QRectF) -> list:
        """只返回与视口相交的对象；顺序按 layer.objects 保持遮挡关系。"""
        if visible.isEmpty():
            return list(self.layer.objects)
        found = {}
        for cell in _cells_for(visible):
            for obj in self._grid.get(cell, ()):
                found[id(obj)] = obj
        for obj in self._overflow:
            found[id(obj)] = obj
        order = getattr(self, "_obj_order", None)
        if not order:
            return list(found.values())
        fallback = len(self.layer.objects)
        return sorted(found.values(),
                      key=lambda o: order.get(id(o), fallback))

    def boundingRect(self) -> QRectF:
        if self._content_rect is None or self._content_rect.isEmpty():
            self._recompute_content_rect()
        # 兜底：极端情况下（无任何对象且地图为空）也要有一个非零包围盒，
        # 否则 QGraphicsView 可能因零面积把 item 永久剔除。
        if self._content_rect is None or self._content_rect.isEmpty():
            tw, th = self.doc.tile_w, self.doc.tile_h
            self._content_rect = QRectF(-self._BOUNDS_MARGIN, -self._BOUNDS_MARGIN,
                                        tw * self.doc.width + 2 * self._BOUNDS_MARGIN,
                                        th * self.doc.height + 2 * self._BOUNDS_MARGIN)
        return self._content_rect

    # ---------------- 选中 / 局部刷新 ----------------
    def _dirty_rect(self, rect: QRectF) -> QRectF:
        # 标签画在框上方；箭头整体已挪到框外、从边线往外伸出约 2*half，
        # 余量要留够，否则拖走 / 缩放后框外那截箭头残影擦不掉。
        pad = effective_handle_size(rect, self._scale) * 2.2 + 10
        return rect.adjusted(-pad, -pad - 6, pad, pad)

    def set_selected(self, objs):
        """设置选中对象列表（``objs`` 为可迭代；``MapObject`` 不可哈希，按 id 判等）。

        只标脏「新增 / 移除」的对象矩形区域，避免整张地图重绘；与一次性整体替换
        不同，这里做差量刷新。
        """
        # 兼容单对象调用（旧测试 / 单选）与批量调用（框选多选）。
        if isinstance(objs, MapObject):
            objs = [objs]
        else:
            objs = list(objs)
        new_ids = {id(o) for o in objs}
        if new_ids == self._sel_ids:
            return
        removed = [o for o in self.selected if id(o) not in new_ids]
        added = [o for o in objs if id(o) not in self._sel_ids]
        for o in removed:
            self.update(self._dirty_rect(object_rect(o)))
        for o in added:
            self.update(self._dirty_rect(object_rect(o)))
        self.selected = objs
        self._sel_ids = new_ids

    def set_preview(self, obj):
        old = self.preview
        if old is not None:
            self.update(self._dirty_rect(object_rect(old)))
        if obj is not None:
            # 预览对象可能出现在任意位置（含地图外），扩张包围盒并令索引失效，
            # 否则视图在地图外时预览对象会被整层剔除而不显示。
            self.prepareGeometryChange()
            if self._content_rect is not None:
                self._content_rect = self._content_rect.united(
                    self._obj_bounds(obj))
            self.update(self._dirty_rect(object_rect(obj)))
        else:
            # 预览清空：包围盒可能收缩，整体重算。
            self.prepareGeometryChange()
            self._recompute_content_rect()
        self.preview = obj

    def set_show_nodes(self, enabled: bool):
        self.show_nodes = bool(enabled)
        self.refresh_all()

    def set_show_names(self, enabled: bool):
        """「视图 → 显示对象名称」。标签本来就画在 _obj_bounds 的留白内，
        开关只影响 paint，不需要 prepareGeometryChange；整层标脏一次即可。"""
        value = bool(enabled)
        if self.show_names == value:
            return
        self.show_names = value
        self._cache_dirty = True   # 缓存里烘焙了标签，开关切换要重建
        self._tile_dirty_all = True
        self.update(self.boundingRect())

    def refresh_rects(self, rects, objects=None):
        # 对象移动：位置变化会使包围盒改变，先令索引失效再扩张（只增不减，安全，
        # 不会因缩放/平移把层错剔）。
        self.prepareGeometryChange()
        if self._content_rect is not None:
            for rect in rects:
                self._content_rect = self._content_rect.united(
                    self._obj_bounds_of_rect(rect))
        if self._cb_rect is not None:
            for rect in rects:
                self._cb_rect = self._cb_rect.united(
                    rect.adjusted(-24, -24, 24, 24))
        for rect in rects:
            self.update(self._dirty_rect(rect))
        # 空间索引按对象增量更新：拖动每帧只挪几个桶，不遍历全层。
        if objects is not None and len(objects) == len(rects):
            if self._index_dirty:
                self._rebuild_index()
            for rect, obj in zip(rects, objects):
                if obj is not None:   # extra_rects 只标脏，没有对应对象
                    self._move_in_index(obj, rect)
        else:
            # 旧调用只给矩形、没有对象身份：下一帧全量重建一次即可。
            self._index_dirty = True
        # 分块缓存：对象旧位置所在块要重画。新位置由下次 paint 生成新块。
        if self._tile_cache:
            for rect in rects:
                self._invalidate_tiles_for_rect(rect)
            if objects is not None:
                for obj in objects:
                    if obj is not None:
                        self._invalidate_tiles_for_rect(object_rect(obj))
        self._update_cache(rects)

    def _obj_bounds_of_rect(self, rect: QRectF) -> QRectF:
        """给定对象矩形（已是场景坐标矩形），返回带标签/箭头留白的绘制范围。"""
        m = effective_handle_size(rect, self._scale) * 0.78 + 18
        return rect.adjusted(-m, -m - 18, m, m)

    def refresh_all(self):
        # 增删 / 翻转 / 节点编辑后几何可能整体收缩，prepareGeometryChange 让 BSP
        # 索引丢弃旧包围盒、用新值重新索引，避免视图按旧范围把层剔除。
        self.prepareGeometryChange()
        self._recompute_content_rect()
        self._index_dirty = True   # 增删后桶内容全变，下帧 paint 前重建
        self._cache_dirty = True
        self._cb_rect = None       # 增删可能改变整体范围，下帧 _cache_bounds 重算
        self._tile_dirty_all = True  # 分块缓存同样失效
        self.update()

    # ---------------- 大量对象时的整层缓存 ----------------
    # 只缓存「未选中对象」的场景坐标位图；选中态 / 预览 / 组框每帧单独画。
    # 小场景不走这条路，避免大 QPixmap 和维护成本。
    def _cache_bounds(self) -> QRectF:
        if self._cb_rect is not None:
            return self._cb_rect
        tw, th = self.doc.tile_w, self.doc.tile_h
        rect = QRectF(0, 0, tw * self.doc.width, th * self.doc.height)
        for obj in self.layer.objects:
            if getattr(obj, "visible", True):
                rect = rect.united(
                    object_rect(obj, tw, th).adjusted(-24, -24, 24, 24))
        self._cb_rect = rect
        return rect

    def _ensure_cache(self, show_labels: bool) -> QImage | None:
        if len(self.layer.objects) < _CACHE_MIN_OBJECTS or self._scale > 1.0:
            self._obj_cache = None
            return None
        cache = self._obj_cache
        if cache is not None and not self._cache_dirty \
                and self._cache_labels == show_labels:
            return cache
        rect = self._cache_bounds()
        w, h = max(1, math.ceil(rect.width())), max(1, math.ceil(rect.height()))
        if w > _CACHE_MAX_SIDE or h > _CACHE_MAX_SIDE:
            self._obj_cache = None
            return None
        if cache is None or cache.width() != w or cache.height() != h:
            cache = QImage(w, h, QImage.Format_ARGB32)
        cache.fill(Qt.transparent)
        p = QPainter(cache)
        p.translate(-rect.left(), -rect.top())
        for obj in self.layer.objects:
            if getattr(obj, "visible", True):
                self._paint_object(p, obj, False, show_label=show_labels)
        p.end()
        self._obj_cache = cache
        self._cache_rect = rect
        self._cache_dirty = False
        self._cache_labels = show_labels
        return cache

    # ---------------- 超大场景分块 LOD 缓存 ----------------
    def _use_tile_cache(self) -> bool:
        """对象量大且整体跨度超过单图像素上限时启用分块缓存。"""
        if len(self.layer.objects) < _CACHE_MIN_OBJECTS or self._scale > 1.0:
            return False
        if self._tile_span is None:
            b = self._cache_bounds()
            self._tile_span = max(b.width(), b.height())
        return self._tile_span > _TILE_CACHE_MIN_SPAN

    def _tile_key_range(self, visible: QRectF, lod: float):
        """视口（场景坐标）覆盖的块索引 (bx, by)。"""
        if visible.isEmpty():
            return ()
        x0 = math.floor(visible.left() / _TILE_CACHE)
        x1 = math.floor(visible.right() / _TILE_CACHE)
        y0 = math.floor(visible.top() / _TILE_CACHE)
        y1 = math.floor(visible.bottom() / _TILE_CACHE)
        return tuple((bx, by) for by in range(y0, y1 + 1)
                     for bx in range(x0, x1 + 1))

    def _tile_image(self, bx: int, by: int, lod: float,
                    show_labels: bool) -> QImage:
        """取（必要时生成）一块缓存。块内用补偿线宽，缩放后 alpha 与直接绘制一致。"""
        key = (lod, bx, by)
        img = self._tile_cache.get(key)
        if img is not None and not self._tile_dirty_all \
                and self._tile_labels == show_labels:
            # 命中：更新 LRU
            try:
                self._tile_lru.remove(key)
            except ValueError:
                pass
            self._tile_lru.append(key)
            return img
        # 生成/重建：按 lod 降分辨率，线宽按 1/lod 补偿。
        # 例：lod=0.25 → 160px 图表示 640 场景单位，线宽 4px；drawImage 放大
        # 4 倍后线宽回到 1 设备像素、alpha 190，与 cosmetic 直接绘制一致。
        side = max(1, int(math.ceil(_TILE_CACHE * lod)))
        if img is None or img.width() != side:
            img = QImage(side, side, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.scale(lod, lod)
        p.translate(-bx * _TILE_CACHE, -by * _TILE_CACHE)
        line_w = max(1, int(math.ceil(1.0 / lod)))
        rect = QRectF(bx * _TILE_CACHE, by * _TILE_CACHE,
                      _TILE_CACHE, _TILE_CACHE)
        rect = rect.adjusted(-32, -48, 32, 32)   # 标签/箭头溢出余量
        for obj in self._candidates(rect):
            if getattr(obj, "visible", True) and self._obj_bounds(obj).intersects(rect):
                self._paint_object(p, obj, False, show_label=show_labels,
                                   line_width=line_w)
        p.end()
        self._tile_cache[key] = img
        self._tile_labels = show_labels
        if key not in self._tile_lru:
            self._tile_lru.append(key)
        self._evict_tiles()
        return img

    def _evict_tiles(self):
        while len(self._tile_cache) > _TILE_CACHE_MAX:
            old = self._tile_lru.pop(0) if self._tile_lru else next(iter(self._tile_cache))
            self._tile_cache.pop(old, None)

    def _paint_tile_cache(self, painter, visible: QRectF, show_labels: bool):
        """分块缓存路径：逐块 drawImage，块内只渲染视口相交的对象。"""
        if self._tile_dirty_all:
            self._tile_cache.clear()
            self._tile_lru.clear()
            self._tile_dirty_all = False
        lod = _choose_tile_lod(self._scale)
        for bx, by in self._tile_key_range(visible, lod):
            img = self._tile_image(bx, by, lod, show_labels)
            painter.drawImage(
                QRectF(bx * _TILE_CACHE, by * _TILE_CACHE,
                       _TILE_CACHE, _TILE_CACHE),
                img, QRectF(0, 0, img.width(), img.height()))
        # 缓存块里烘焙的是未选中样式；选中态 / 预览 / 组框每帧单独覆盖。
        sel_ids = self._sel_ids
        for obj in self.selected:
            if not getattr(obj, "visible", True):
                continue
            if not visible.isEmpty() and not self._obj_bounds(obj).intersects(visible):
                continue
            self._paint_object(painter, obj, True, show_label=False)
        if self.preview is not None:
            if visible.isEmpty() or self._obj_bounds(self.preview).intersects(visible):
                self._paint_object(painter, self.preview, False, preview=True)

    def _invalidate_tiles_for_rect(self, rect: QRectF):
        """对象编辑后标脏覆盖范围涉及的块（所有 LOD 档）。"""
        if not self._tile_cache:
            return
        x0 = math.floor(rect.left() / _TILE_CACHE) - 1
        x1 = math.floor(rect.right() / _TILE_CACHE) + 1
        y0 = math.floor(rect.top() / _TILE_CACHE) - 1
        y1 = math.floor(rect.bottom() / _TILE_CACHE) + 1
        for key in list(self._tile_cache):
            _lod, bx, by = key
            if x0 <= bx <= x1 and y0 <= by <= y1:
                self._tile_cache.pop(key, None)
                try:
                    self._tile_lru.remove(key)
                except ValueError:
                    pass

    def _update_cache(self, rects):
        """对象移动 / 编辑后只重画受影响的那一小块缓存。"""
        cache = self._obj_cache
        if cache is None or self._cache_dirty:
            return
        dirty = QRectF()
        for rect in rects:
            dirty = dirty.united(rect)
        if dirty.isEmpty():
            return
        if not self._cache_rect.contains(dirty):
            self._cache_dirty = True   # 移出缓存覆盖范围：下帧全量重建
            return
        # 标签画在框上方、箭头伸到框外，留白必须一起覆盖，否则会留残影。
        dirty = dirty.adjusted(-28, -40, 28, 28)
        p = QPainter(cache)
        p.translate(-self._cache_rect.left(), -self._cache_rect.top())
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillRect(dirty, Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        for obj in self._candidates(dirty):
            if getattr(obj, "visible", True) \
                    and self._obj_bounds(obj).intersects(dirty):
                self._paint_object(p, obj, False, show_label=self._cache_labels)
        p.end()

    # ---------------- 绘制 ----------------
    def paint(self, painter, option, widget=None):
        self._scale = abs(painter.worldTransform().m11()) or 1.0
        sel_ids = self._sel_ids
        # 视口裁剪：只绘制与当前可见区域（exposedRect）相交的对象，缩放后不再渲染
        # 视口外的内容；option 为 None 或 exposedRect 为空（整层刷新）时退化为全部绘制，
        # 保证不漏画。
        visible = option.exposedRect if option is not None else QRectF()
        cull = not visible.isEmpty()
        if self._index_dirty:
            self._rebuild_index()
        candidates = self._candidates(visible)
        # 缩得太小时文字不足 8px：既看不清，drawText 又占整帧大头。
        show_labels = self.show_names and self._scale >= _LABEL_MIN_SCALE

        # 组合可视化：只算包围盒数据，实际绘制放到对象之后（标签再放最上层）。
        groups = {}
        for obj in candidates:
            if not getattr(obj, "visible", True):
                continue
            g = obj.properties.get(EDITOR_GROUP_PROPERTY) if obj.properties else None
            if g:
                groups.setdefault(g, []).append(obj)
        group_boxes = []   # (hull, color, label)
        if groups:
            for gname, members in groups.items():
                hull = None
                for o in members:
                    r = object_rect(o, self.doc.tile_w, self.doc.tile_h)
                    hull = r if hull is None else hull.united(r)
                if hull is None:
                    continue
                hull = hull.adjusted(-6, -6, 6, 6)
                if cull and not hull.intersects(visible):
                    continue
                group_boxes.append((hull, group_color(gname), gname))

        # 超大场景（对象跨度超过单图像素上限）：分块 LOD 缓存。
        if self._use_tile_cache():
            self._paint_tile_cache(painter, visible, show_labels)
            self._paint_group_frames(painter, group_boxes)
            self._paint_group_labels(painter, group_boxes, show_labels)
            return

        cache = self._ensure_cache(show_labels)
        if cache is not None:
            # 大对象量：整层一次 drawPixmap，此前逐对象绘制全部省掉。
            src = visible.intersected(self._cache_rect) if cull else self._cache_rect
            if not src.isEmpty():
                painter.drawImage(
                    src, cache,
                    src.translated(-self._cache_rect.left(),
                                   -self._cache_rect.top()))
            # 缓存里画的是未选中样式；选中态 / 节点手柄单独覆盖这几帧。
            for obj in self.selected:
                if not getattr(obj, "visible", True):
                    continue
                if cull and not self._obj_bounds(obj).intersects(visible):
                    continue
                # 缓存里已有未选中标签，叠加时只覆盖框 / 节点，标签别画两遍。
                self._paint_object(painter, obj, True, show_label=False)
            if self.preview is not None:
                if not cull or self._obj_bounds(self.preview).intersects(visible):
                    self._paint_object(painter, self.preview, False, preview=True)
            self._paint_group_frames(painter, group_boxes)
            self._paint_group_labels(painter, group_boxes, show_labels)
            return

        self._paint_group_frames(painter, group_boxes)
        for obj in candidates:
            if not getattr(obj, "visible", True):
                continue
            if cull and not self._obj_bounds(obj).intersects(visible):
                continue
            self._paint_object(painter, obj, id(obj) in sel_ids,
                               show_label=show_labels)
        if self.preview is not None:
            if not cull or self._obj_bounds(self.preview).intersects(visible):
                self._paint_object(painter, self.preview, False, preview=True)
        self._paint_group_labels(painter, group_boxes, show_labels)

    def _paint_group_frames(self, painter, group_boxes):
        for hull, col, _gname in group_boxes:
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(col.red(), col.green(), col.blue(), 38))
            painter.drawRoundedRect(hull, 6, 6)
            painter.restore()
            painter.setPen(QPen(col, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(hull, 6, 6)

    def _paint_group_labels(self, painter, group_boxes, show_labels: bool):
        if not group_boxes or not show_labels:
            return
        painter.setFont(_GROUP_FONT)
        for hull, col, gname in group_boxes:
            painter.setPen(col)
            painter.drawText(QPointF(hull.x() + 3, hull.y() - 4),
                            f"组：{gname}")

    def _paint_object(self, painter, obj, is_selected: bool, preview: bool = False,
                      show_label: bool = True, line_width: int | None = None):
        rect = object_rect(obj, self.doc.tile_w, self.doc.tile_h)
        # 对象框恒为黄色：选中不再改成蓝色，也不额外加一层边框线。
        if line_width is not None and line_width > 1:
            # 分块缓存块内：线宽按 1/lod 补偿，保证缩放后回到 1 设备像素、
            # alpha 不随缩放稀释（见 _tile_image 注释）。
            base = QColor(255, 200, 60, 190)
            pen = QPen(base, line_width)
            painter.setPen(pen)
        else:
            painter.setPen(_PEN_PREVIEW if preview else
                           (_PEN_SELECTED if is_selected else _PEN_NORMAL))
        painter.setBrush(Qt.NoBrush)

        shape = obj.shape
        if shape == "ellipse":
            painter.drawEllipse(rect)
        elif shape == "point":
            center = QPointF(obj.x, obj.y)
            painter.drawEllipse(center, 6.0, 6.0)
            painter.drawLine(center + QPointF(-10, 0), center + QPointF(10, 0))
            painter.drawLine(center + QPointF(0, -10), center + QPointF(0, 10))
        elif shape in ("polygon", "polyline"):
            poly = QPolygonF([QPointF(obj.x + px, obj.y + py)
                              for px, py in obj.points])
            if shape == "polygon":
                painter.setBrush(QBrush(QColor(255, 200, 60, 30)))
                painter.drawPolygon(poly)
                painter.setBrush(Qt.NoBrush)
            else:
                painter.drawPolyline(poly)
        elif shape in ("tile", "image"):
            pix = object_pixmap(self.doc, obj)
            if pix is not None:
                painter.drawPixmap(rect, pix, QRectF(pix.rect()))
            else:
                painter.drawRect(rect)
                painter.drawText(rect, Qt.AlignCenter, "图片缺失")
        elif shape == "text":
            style = obj.text_attr
            painter.fillRect(rect, QColor(255, 248, 205, 210))
            painter.drawRect(rect)
            font = QFont(style["fontfamily"])
            try:
                font.setPixelSize(max(1, int(float(style["pixelsize"]))))
            except (ValueError, TypeError):
                font.setPixelSize(16)
            font.setBold(style["bold"] in ("1", "true", "True"))
            font.setItalic(style["italic"] in ("1", "true", "True"))
            font.setUnderline(style["underline"] in ("1", "true", "True"))
            font.setStrikeOut(style["strikeout"] in ("1", "true", "True"))
            painter.setFont(font)
            color = QColor(style["color"])
            if not color.isValid():
                color = QColor("#000000")
            painter.setPen(color)
            flags = (_HALIGN.get(style["halign"], Qt.AlignLeft)
                     | _VALIGN.get(style["valign"], Qt.AlignTop))
            if style["wrap"] in ("1", "true", "True"):
                flags |= Qt.TextWordWrap
            painter.drawText(rect.adjusted(2, 1, -2, -1), flags, obj.text)
        else:  # rect
            painter.drawRect(rect)

        # 选中态不再画 8 个黄色缩放箭头（对象框本身即黄色）；整体选中范围由
        # MapScene 的「选中包围框」统一表达（含所有选中对象，无缩放手柄）。

        if is_selected and self.show_nodes and shape in ("polygon", "polyline"):
            painter.setPen(QPen(QColor(255, 200, 60, 255), 1))
            painter.setBrush(QBrush(QColor(90, 70, 20, 230)))
            for px, py in obj.points:
                painter.drawRect(QRectF(obj.x + px - 3, obj.y + py - 3, 6, 6))
            painter.setBrush(Qt.NoBrush)

        # 名称标签：只画有名字 / 类型的对象，避免画布上出现 "#12" 这类编号。
        if show_label and not preview and (obj.name or obj.type_):
            painter.setPen(_LABEL_YELLOW)
            painter.setFont(_LABEL_FONT)
            painter.drawText(QPointF(rect.x(), rect.y() - 4), obj.label)
