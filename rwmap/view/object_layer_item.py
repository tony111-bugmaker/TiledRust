"""对象层绘制：Tiled 全形状 + 名称标签 + 8 个黄色缩放箭头 + 拖拽预览。

对象数量很少（官方图 1~几十个），按对象逐个绘制即可；
刷新仍走局部脏矩形，避免 hover 时带着整张地图重绘。
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import QGraphicsItem

from ..document.gid import split_gid
from ..document.layer import ObjectLayer
from ..document.map_object import MapObject
from ..document.properties import EDITOR_GROUP_PROPERTY
from ..tilesets.tile_cache import get_pixmap

_IMAGE_CACHE: dict[str, QPixmap] = {}

_HALIGN = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
           "right": Qt.AlignRight, "justify": Qt.AlignJustify}
_VALIGN = {"top": Qt.AlignTop, "center": Qt.AlignVCenter,
           "bottom": Qt.AlignBottom}


def object_rect(obj, tile_w: int = 20, tile_h: int = 20) -> QRectF:
    x, y, w, h = obj.bounds(tile_w, tile_h)
    return QRectF(x, y, w, h)


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
        return pix if not pix.isNull() else None
    return None


def clear_image_cache() -> None:
    _IMAGE_CACHE.clear()


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
        self.update(self.boundingRect())

    def refresh_rects(self, rects):
        # 对象移动：位置变化会使包围盒改变，先令索引失效再扩张（只增不减，安全，
        # 不会因缩放/平移把层错剔）。
        self.prepareGeometryChange()
        if self._content_rect is not None:
            for rect in rects:
                self._content_rect = self._content_rect.united(
                    self._obj_bounds_of_rect(rect))
        for rect in rects:
            self.update(self._dirty_rect(rect))

    def _obj_bounds_of_rect(self, rect: QRectF) -> QRectF:
        """给定对象矩形（已是场景坐标矩形），返回带标签/箭头留白的绘制范围。"""
        m = effective_handle_size(rect, self._scale) * 0.78 + 18
        return rect.adjusted(-m, -m - 18, m, m)

    def refresh_all(self):
        # 增删 / 翻转 / 节点编辑后几何可能整体收缩，prepareGeometryChange 让 BSP
        # 索引丢弃旧包围盒、用新值重新索引，避免视图按旧范围把层剔除。
        self.prepareGeometryChange()
        self._recompute_content_rect()
        self.update()

    # ---------------- 绘制 ----------------
    def paint(self, painter, option, widget=None):
        self._scale = abs(painter.worldTransform().m11()) or 1.0
        sel_ids = self._sel_ids
        # 视口裁剪：只绘制与当前可见区域（exposedRect）相交的对象，缩放后不再渲染
        # 视口外的内容；option 为 None 或 exposedRect 为空（整层刷新）时退化为全部绘制，
        # 保证不漏画。
        visible = option.exposedRect if option is not None else QRectF()
        cull = not visible.isEmpty()
        gfont = QFont()
        gfont.setBold(True)

        # 组合可视化（点阵图可见）：先按组名归并成员，画半透明组色包围框 + 组名标签。
        groups = {}
        for obj in self.layer.objects:
            if not getattr(obj, "visible", True):
                continue
            g = obj.properties.get(EDITOR_GROUP_PROPERTY) if obj.properties else None
            if g:
                groups.setdefault(g, []).append(obj)
        group_boxes = []   # (hull, color, label) —— 留到对象之后画标签，避免被盖住
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
                col = group_color(gname)
                painter.save()
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(col.red(), col.green(), col.blue(), 38))
                painter.drawRoundedRect(hull, 6, 6)
                painter.restore()
                painter.setPen(QPen(col, 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(hull, 6, 6)
                group_boxes.append((hull, col, gname))

        for obj in self.layer.objects:
            if not getattr(obj, "visible", True):
                continue
            if cull and not self._obj_bounds(obj).intersects(visible):
                continue
            self._paint_object(painter, obj, id(obj) in sel_ids)
        if self.preview is not None:
            if not cull or self._obj_bounds(self.preview).intersects(visible):
                self._paint_object(painter, self.preview, False, preview=True)

        # 组名标签画在最上层（不被对象框盖住），附在组包围框左上角外侧。
        if group_boxes:
            painter.setFont(gfont)
            for hull, col, gname in group_boxes:
                painter.setPen(col)
                painter.drawText(QPointF(hull.x() + 3, hull.y() - 4),
                                f"组：{gname}")

    def _paint_object(self, painter, obj, is_selected: bool, preview: bool = False):
        rect = object_rect(obj, self.doc.tile_w, self.doc.tile_h)
        if preview:
            pen = QPen(QColor(255, 200, 60, 210), 2, Qt.DashLine)
        else:
            # 对象框恒为黄色：选中不再改成蓝色，也不额外加一层边框线。
            pen = QPen(QColor(255, 200, 60, 255 if is_selected else 190), 1)
        painter.setPen(pen)
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
        if self.show_names and not preview and (obj.name or obj.type_):
            painter.setPen(_LABEL_YELLOW)
            painter.setFont(QFont())
            painter.drawText(QPointF(rect.x(), rect.y() - 4), obj.label)
