"""触发器节点图面板（原生 QGraphicsView 实现，B 方案）。

设计约定（与网页版 trigger_graph.html 的语义 / 外观一致，纯 Python，无 HTML）：

* 节点来自地图文档里的**对象层**，包含矩形 / 椭圆 / 点 / 折线 / 多边形 /
  文字 / 图块等**所有形状**的对象；不会凭空造节点。默认显示全部对象层，
  顶部下拉可切换「只看某一对象层」；跨层引用仍能正确连线。
* 连线由对象自定义属性里的引用反推：alsoActivate（激活）、
  whenActivatedIds / activatedBy（前置）、deactivatedBy（停用）。
  （`allToActivate` 在 RW 里是布尔开关，不是名字引用，故不计入连线。）
* **新增对象必须在地图画布上操作**；右侧属性面板可以编辑名字 / 类型 /
  自定义属性，也能删除对象，全部走 ObjectCommand + QUndoStack（可撤销）。
* 右侧面板、中文类型 / 属性词典、点阵深色背景与配色均从网页版 1:1 迁移，
  但 TMX 里写回的键和值始终保持英文。
* 节点可以在画布里拖动摆位、滚轮缩放（矢量绘制，放大不会糊），
  拖动过程中不写回文档；仅在**保存时**把布局以私有属性 rwmap_node_pos
  写进各对象（以左上角对象为 (0,0) 的相对坐标），下次打开据此恢复。
  画布 sceneRect 是「全部节点内容框 + 四周各一个视口」的动态范围：平移到
  任一极限时该方向最近节点刚好完全移出视口，节点移动后范围随之重算。
* 连线由两层共同渲染：两端节点都在视口内时用逐边 EdgeItem（曲线 + 箭头 +
  标签），其余由常驻的全局连线层按**数据坐标**补画；缩小到概览模式时全局层
  画全部边。因此线与节点是否被渲染无关，缩放 / 虚拟化都不会让线消失。
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from PySide6.QtCore import (
    QLineF, QPoint, QPointF, QRectF, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QGraphicsItem, QGraphicsLineItem,
    QGraphicsObject, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSplitter,
    QVBoxLayout, QWidget, QInputDialog,
)

from ..commands.object_command import capture, push_object_edit
from ..document.layer import ObjectLayer
from ..document.map_document import allocate_trigger_id, used_trigger_ids
from ..document.map_object import object_uid
from ..document.properties import (
    EDITOR_GROUP_PROPERTY, EDITOR_NODE_POS_PROPERTY,
    HIDDEN_EDITOR_PROPERTIES,
)
from ..theme import ASH, INK, PANEL_BORDER_QSS, TEXT, TEXT_DIM, qc
from .dock_chrome import install_title_bar
from .trigger_inspector import (
    EDGE_COLORS, THEME, TYPE_COLORS, TriggerInspector, color_for,
    prop_label, summary_keys, type_hint, type_label,
)

# ---- 节点几何 ----
NODE_W = 218.0
NODE_H = 92.0
HEADER_H = 25.0
PORT_R = 6.0
# 端口画在节点左右两侧的**垂直正中**：线的起点是第一个模块右侧中间，
# 终点是第二个模块左侧中间。黄点、拖拽命中、EdgeItem、全局连线层全部共用。
PORT_Y = NODE_H / 2.0

# ---- 连线种类：kind -> (中文标签, 颜色, 方向) ----
# forward = 属性所在节点是源；reverse = 被引用的节点是源、属性所在节点是目标
EDGE_SPECS = {
    "alsoActivate": ("激活", EDGE_COLORS["alsoActivate"], "forward"),
    "whenActivatedIds": ("前置", EDGE_COLORS["whenActivatedIds"], "reverse"),
    "whenActivatedlds": ("前置(错拼)", EDGE_COLORS["whenActivatedlds"], "reverse"),
    "activatedBy": ("激活条件", EDGE_COLORS["activatedBy"], "reverse"),
    # 注：`allToActivate` 是 RW 的布尔开关（true/false = 是否激活全部其它触发器），
    # 不是「按名字引用」的列表，故不纳入连线反推，避免把 "true" 当成触发器名。
    "deactivatedBy": ("停用", EDGE_COLORS["deactivatedBy"], "reverse"),
    # 以下为适配其它地图 / 版本补充的链接属性
    "alsoDeactivate": ("停用激活", EDGE_COLORS["alsoDeactivate"], "forward"),
    "whenDeactivatedIds": ("停用前置", EDGE_COLORS["whenDeactivatedIds"], "reverse"),
    "requiredFor": ("需要前置", EDGE_COLORS["requiredFor"], "reverse"),
    "next": ("下一步", EDGE_COLORS["next"], "forward"),
    "nextUnit": ("下一单位", EDGE_COLORS["nextUnit"], "forward"),
    "parentTrigger": ("父触发器", EDGE_COLORS["parentTrigger"], "reverse"),
}
LINK_PROPS = tuple(EDGE_SPECS)

DEFAULT_COLOR = "#555555"

# ---- 性能：大规模节点图（别人写的「无分组」大地图）下的虚拟化 + LOD 概览 ----
# 屏幕上节点宽度小于此像素 → 切到「概览模式」：整张图只用 1 个 QGraphicsItem 画出
# 所有节点（小色块），不建逐节点交互对象，避免几千个 QGraphicsObject 同时进场景卡死。
OVERVIEW_MIN_NODE_PX = 28.0
# 视口外预建区（场景坐标像素）：多保留一圈，平移时不立刻缺图、少触发重建。
VIRT_MARGIN = 800.0

# 主题色别名：界面部分用双色（INK / ASH），语义色（连线 / 端口 / 节点类型）保持原样
_BG = QColor(THEME["bg"])
_NODE_BG = QColor(THEME["node"])
_NODE_BORDER = QColor(THEME["node_border"])
_TEXT = QColor(THEME["text"])
_MUTED = QColor(THEME["muted"])


def _dot_brush() -> QBrush:
    """网页版 CSS radial-gradient 圆点背景的 Qt 等价物（纹理平铺，性能好）。"""
    gap = THEME["dot_gap"]
    tile = QPixmap(gap, gap)
    tile.fill(_BG)
    p = QPainter(tile)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(THEME["dot"])))
    p.drawEllipse(QPointF(0.5, 0.5), 1.0, 1.0)
    p.end()
    return QBrush(tile)


def _parse_ids(raw) -> list[str]:
    """RW 引用列表解析。

    以逗号 / 分号 / 空白分隔多个引用；分隔只在括号**外**生效，因此形如
    `name(spawnChance=0.04, maxSpawnLimit=1)` 的生成项不会被内部逗号拆散。
    每个条目取 `(` 之前的部分作为引用名（括号内的生成参数是参数，不是名字）。

    例：'A, Q, jcroom34mechFlame(spawnChance=0.04,maxSpawnLimit=1)'
        -> ['A', 'Q', 'jcroom34mechFlame']
    """
    raw = str(raw or "")
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in raw:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif depth == 0 and ch in ",;\t\n\r ":
            tok = "".join(buf)
            if tok:
                out.append(tok.split("(", 1)[0].strip())
            buf = []
        else:
            buf.append(ch)
    tok = "".join(buf)
    if tok:
        out.append(tok.split("(", 1)[0].strip())
    return out


def _split_refs(raw) -> list[str]:
    """按**括号外**的逗号 / 分号 / 空白切分引用，保留 token 原文（含生成参数）。

    `_parse_ids` 只给出引用名，删 / 改连线时必须保留 `name(spawnChance=...)`
    这类 token 的完整文本，否则会把生成参数弄丢。
    """
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in str(raw or ""):
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif depth == 0 and ch in ",;\t\n\r ":
            tok = "".join(buf).strip()
            if tok:
                out.append(tok)
            buf = []
        else:
            buf.append(ch)
    tok = "".join(buf).strip()
    if tok:
        out.append(tok)
    return out


def _ref_name(token: str) -> str:
    return token.split("(", 1)[0].strip()


def _node_ref_names(node: GraphNode) -> set[str]:
    """一个节点可能被哪些字符串引用（小写）：自定义 id / object id / 名字 / 标签。"""
    obj = node.obj
    names = {
        str(node.tid), str(getattr(obj, "object_id", "")),
        str(getattr(obj, "name", "") or ""),
        str(getattr(obj, "label", "") or ""),
        str(obj.properties.get("name") or ""),
        str(obj.properties.get("label") or ""),
    }
    return {n.strip().lower() for n in names if n and n.strip()}


def _remove_ref_token(raw, node: GraphNode) -> tuple[str, bool]:
    """从属性串里删掉指向 ``node`` 的那一项，返回 (新值, 是否删到)。"""
    refs = _split_refs(raw)
    targets = _node_ref_names(node)
    kept = []
    removed = False
    for tok in refs:
        if not removed and _ref_name(tok).lower() in targets:
            removed = True
            continue
        kept.append(tok)
    return ", ".join(kept), removed


def _type_color(type_name: str) -> QColor:
    return QColor(color_for(type_name))


def _elide(painter: QPainter, text: str, width: float) -> str:
    fm = QFontMetrics(painter.font())
    return fm.elidedText(text, Qt.ElideRight, max(8, int(width)))


# ---------------------------------------------------------------- 数据
@dataclass
class GraphNode:
    key: str
    obj: object
    layer_name: str
    tid: str
    x: float = 0.0
    y: float = 0.0
    group: str = ""        # 编辑器私有分组名（EDITOR_GROUP_PROPERTY 的值）


@dataclass
class GraphEdge:
    src: str
    dst: str
    kind: str


@dataclass
class GraphData:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    dangling: int = 0
    layer_names: list[str] = field(default_factory=list)


def collect_graph(doc, layer_filter=None) -> GraphData:
    """从文档对象层读取对象，反推 id 引用连线（纯数据，不碰 Qt）。

    layer_filter 为 None 时显示**全部**对象层；为某层名时只显示该对象层。
    解析引用时始终走「全对象层」的 id / 名字空间，因此跨层引用也能正确连线
    （只不渲染目标层未显示的节点，不会把跨层引用误判成悬空）。
    """
    data = GraphData()
    if doc is None:
        return data

    all_layers = [l for l in doc.layers if isinstance(l, ObjectLayer)]
    if not all_layers:
        return data
    # 显示层：过滤后只剩目标层；否则全部对象层
    if layer_filter:
        layers_for_nodes = [l for l in all_layers if l.name == layer_filter]
        data.layer_names = [layer_filter]
    else:
        layers_for_nodes = all_layers
        data.layer_names = [l.name for l in all_layers]

    seen: dict[str, int] = {}
    # 键必须是「对象」而不是 (层名, object_id)：铁锈地图里 Tiled object id
    # 大量重复（实测一张图 160 个对象全是 1），用后者会让 #idx 唯一后缀
    # 全部互相覆盖，第二遍建节点时塌缩成两三个节点。
    key_of: dict[int, str] = {}
    by_tid: dict[str, str] = {}
    by_oid: dict[str, str] = {}
    by_name: dict[str, str] = {}

    def add_name(raw, key):
        if raw:
            by_name.setdefault(str(raw).strip().lower(), key)

    # 第一遍：跨全部对象层建索引，保证跨层引用也能解析到目标节点
    for layer in all_layers:
        for obj in layer.objects:
            base = f"{layer.name}::{obj.object_id}"
            idx = seen.get(base, 0)
            seen[base] = idx + 1
            key = base if idx == 0 else f"{base}#{idx}"
            key_of[id(obj)] = key
            tid = str(obj.properties.get("id") or obj.object_id)
            by_tid.setdefault(tid, key)
            by_oid.setdefault(str(obj.object_id), key)
            # 其它地图常按触发器「名字 / 标签」互相引用，而不仅用数字 id
            add_name(obj.name, key)
            add_name(obj.label, key)
            add_name(obj.properties.get("name"), key)
            add_name(obj.properties.get("label"), key)

    # 第二遍：只把要显示的层的对象建成节点
    for layer in layers_for_nodes:
        for obj in layer.objects:
            key = key_of[id(obj)]
            tid = str(obj.properties.get("id") or obj.object_id)
            grp = obj.properties.get(EDITOR_GROUP_PROPERTY) or ""
            data.nodes[key] = GraphNode(key, obj, layer.name, tid, group=grp)

    def resolve(raw_id: str):
        r = str(raw_id).strip()
        return by_tid.get(r) or by_oid.get(r) or by_name.get(r.lower())

    for node in data.nodes.values():
        props = node.obj.properties
        for prop, (_label, _color, direction) in EDGE_SPECS.items():
            raw = props.get(prop)
            if not raw:
                continue
            for rid in _parse_ids(raw):
                other = resolve(rid)
                if other is None:
                    data.dangling += 1
                    continue
                if other == node.key:
                    continue
                if direction == "forward":
                    src, dst = node.key, other
                else:
                    src, dst = other, node.key
                edge = GraphEdge(src, dst, prop)
                if edge not in data.edges:
                    data.edges.append(edge)
    return data


# ---------------------------------------------------------------- 图形项
def _draw_shape_icon(painter: QPainter, shape: str, x: float, y: float,
                     color: QColor):
    """12×12 的形状标记：矩形 / 点 / 线 / 文字 一眼能区分。"""
    painter.save()
    painter.setPen(QPen(color, 1.4))
    painter.setBrush(Qt.NoBrush)
    if shape == "point":
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(x + 6, y + 6), 3, 3)
    elif shape in ("polyline", "polygon"):
        painter.drawPolyline(QPolygonF([
            QPointF(x + 1, y + 9), QPointF(x + 4, y + 3),
            QPointF(x + 8, y + 9), QPointF(x + 11, y + 4),
        ]))
        if shape == "polygon":
            painter.drawLine(QPointF(x + 11, y + 4), QPointF(x + 1, y + 9))
    elif shape == "text":
        painter.setPen(QPen(color, 1.2))
        f = QFont(painter.font())
        f.setPointSize(8)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(QRectF(x, y - 1, 12, 14), Qt.AlignCenter, "T")
    elif shape == "ellipse":
        painter.drawEllipse(QRectF(x + 1, y + 2, 10, 8))
    elif shape == "tile":
        painter.setBrush(QBrush(color))
        painter.drawRect(QRectF(x + 2, y + 2, 8, 8))
    else:  # rect / image / 其它
        painter.drawRect(QRectF(x + 1, y + 2, 10, 8))
    painter.restore()


class NodeItem(QGraphicsObject):
    """一个触发器对象 = 一个节点。可拖动摆位，但绝不写回文档。"""

    def __init__(self, node: GraphNode, graph: "GraphScene"):
        super().__init__()
        self.node = node
        self.graph = graph
        obj = node.obj
        self.edges: list["EdgeItem"] = []
        self.group = node.group
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        # 节点层级高于连线（连线 z=1），边框永远压在绿色连线上，不会被盖住
        self.setZValue(20)
        self.setPos(node.x, node.y)
        # 不设 tooltip：右键框选后鼠标停在节点上，Qt 会延迟弹出一个深色圆角
        # QTipLabel，一闪而过（用户看到的「空框」）。节点信息在 banner 与右侧
        # 属性面板都有，去掉悬浮提示不影响任何功能。

    def boundingRect(self):
        return QRectF(-PORT_R - 2, -2, NODE_W + 2 * PORT_R + 4, NODE_H + 4)

    def port_out(self) -> QPointF:
        return QPointF(self.x() + NODE_W, self.y() + PORT_Y)

    def port_in(self) -> QPointF:
        return QPointF(self.x(), self.y() + PORT_Y)

    def scene_port_out(self) -> QPointF:
        # port_out() 已是场景坐标（EdgeItem 画边线即用此值），这里不能再 mapToScene，
        # 否则会把场景坐标当成局部坐标再投一次，导致命中端口比画出来的黄点整体偏移一个
        # 节点坐标 —— 之前「点中黄点却连不上线」的真正根因。
        return self.port_out()

    def scene_port_in(self) -> QPointF:
        return self.port_in()

    def paint(self, painter: QPainter, _option, _widget=None):
        obj = self.node.obj
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0, 0, NODE_W, NODE_H)

        # 底板：先只填充底色，边框留到最后再描（确保压在标题条 / 连线之上）
        border_col = QColor("#ffffff") if self.isSelected() else _NODE_BORDER
        if self.graph.dock._color_by_group and self.group:
            border_col = self.graph.dock._color_for_group(self.group)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_NODE_BG))
        painter.drawRoundedRect(rect, 7, 7)

        # 标题条（按触发器类型着色）；用底板圆角做裁剪，避免下缘出现圆角缺口
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 7, 7)
        painter.setClipPath(clip)
        painter.fillRect(QRectF(0, 0, NODE_W, HEADER_H),
                         QBrush(_type_color(obj.type_)))
        painter.restore()
        painter.setPen(QPen(QColor(INK)))
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(0, HEADER_H), QPointF(NODE_W, HEADER_H))

        # 形状小图标
        _draw_shape_icon(painter, obj.shape, 6, 6, QColor("#ffffff"))

        painter.setPen(QPen(QColor("#ffffff")))
        f = QFont(painter.font())
        f.setPointSize(9)
        f.setBold(True)
        painter.setFont(f)
        title = f"{obj.label}"
        painter.drawText(QRectF(22, 1, NODE_W - 68, HEADER_H - 2),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         _elide(painter, title, NODE_W - 72))
        # 右上角 trigger id
        f2 = QFont(painter.font())
        f2.setPointSize(8)
        f2.setBold(False)
        painter.setFont(f2)
        painter.setPen(QPen(QColor(TEXT)))
        painter.drawText(QRectF(NODE_W - 48, 1, 44, HEADER_H - 2),
                         Qt.AlignRight | Qt.AlignVCenter,
                         _elide(painter, "#" + self.node.tid, 42))

        # 主体：与网页版一致，第一行「类型 | 中文类型名」，下面按类型模板显示中文属性标签
        f3 = QFont(painter.font())
        f3.setPointSize(8)
        painter.setFont(f3)
        y = HEADER_H + 5
        painter.setPen(QPen(_MUTED))
        painter.drawText(QRectF(9, y, 74, 14), Qt.AlignLeft, "类型")
        painter.setPen(QPen(QColor(TEXT)))
        painter.drawText(QRectF(84, y, NODE_W - 93, 14), Qt.AlignRight,
                         _elide(painter, type_label(obj.type_), NODE_W - 95))
        y += 16

        props = obj.properties
        rows: list[tuple[str, str]] = []
        max_rows = 2 if self.group else 3
        for key in summary_keys(obj.type_) + tuple(props.values.keys()):
            if (key == "id" or key in LINK_PROPS
                    or key in HIDDEN_EDITOR_PROPERTIES
                    or any(r[0] == key for r in rows)):
                continue
            value = props.get(key)
            if value in (None, ""):
                continue
            rows.append((key, str(value)))
            if len(rows) >= max_rows:
                break
        if not rows:
            painter.setPen(QPen(QColor(TEXT_DIM)))
            painter.drawText(QRectF(9, y, NODE_W - 18, 14), Qt.AlignLeft,
                             _elide(painter, type_hint(obj.type_), NODE_W - 18))
        for key, value in rows:
            painter.setPen(QPen(_MUTED))
            painter.drawText(QRectF(9, y, 74, 14), Qt.AlignLeft,
                             _elide(painter, prop_label(key), 72))
            painter.setPen(QPen(_TEXT))
            painter.drawText(QRectF(84, y, NODE_W - 93, 14), Qt.AlignLeft,
                             _elide(painter, value.replace("\n", " "), NODE_W - 95))
            y += 15

        # 分组徽标（编辑器私有属性，不在右侧属性面板显示）
        if self.group:
            gcol = (self.graph.dock._color_for_group(self.group)
                    if self.graph.dock._color_by_group else QColor("#7fb2ff"))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(gcol))
            painter.drawRoundedRect(QRectF(9, NODE_H - 16, 8, 8), 2, 2)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(TEXT_DIM)))
            f = QFont(painter.font())
            f.setPointSize(8)
            painter.setFont(f)
            painter.drawText(QRectF(22, NODE_H - 17, NODE_W - 30, 12),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             _elide(painter, f"组：{self.group}", NODE_W - 32))

        # 边框最后描边：节点层级（z=20）高于连线（z=1），边框永远在连线之上
        painter.setPen(QPen(border_col, 2.0 if self.isSelected() else 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 7, 7)

        # 端口
        # 端口：网页版 .socket.in / .socket.out 配色
        for cx, cy, col in ((0, PORT_Y, QColor(THEME["port_in"])),
                            (NODE_W, PORT_Y, QColor(THEME["port_out"]))):
            painter.setPen(QPen(QColor(INK), 1.2))
            painter.setBrush(QBrush(col))
            painter.drawEllipse(QPointF(cx, cy), PORT_R, PORT_R)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for edge in self.edges:
                edge.rebuild()
            if self.graph is not None:
                self.graph.on_node_moved(self)
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.graph is not None:
            self.graph.dock.objectActivateRequested.emit(self.node.obj)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def _edge_endpoints(a: "GraphNode", b: "GraphNode"):
    """所有渲染路径共用的端点：a 的右侧中间 → b 的左侧中间。

    EdgeItem 用 live 节点的 port_out/port_in（同一 PORT_Y），本函数给全局连线层
    与概览层用「数据层坐标」补画时保持一致，端点不会一个在标题下、一个在正中。
    """
    return (QPointF(a.x + NODE_W, a.y + PORT_Y),
            QPointF(b.x, b.y + PORT_Y))


def _edge_path(a: QPointF, b: QPointF, lane: int, total: int) -> QPainterPath:
    """按端口坐标算贝塞尔连线；同起终点的多条线用车道错开。"""
    dx = max(56.0, abs(b.x() - a.x()) * 0.45)
    bow = (lane - (total - 1) / 2.0) * 46.0
    path = QPainterPath(a)
    path.cubicTo(QPointF(a.x() + dx, a.y() + bow),
                 QPointF(b.x() - dx, b.y() + bow), b)
    return path


def _paint_arrow(painter: QPainter, path: QPainterPath, col: QColor,
                 scale: float = 1.0):
    """在曲线终点画箭头（EdgeItem 与全局连线层共用）。

    ``scale`` 用于概览模式：场景单位会随缩放一起变小，传 ``1/zoom`` 才能让
    箭头在屏幕上始终是固定大小，不会因为缩小就看不见。
    """
    end = path.pointAtPercent(1.0)
    near = path.pointAtPercent(0.975)
    v = end - near
    length = math.hypot(v.x(), v.y()) or 1.0
    ux, uy = v.x() / length, v.y() / length
    px, py = -uy, ux
    size, half = 10.0 * scale, 4.5 * scale
    tip = QPointF(end.x() - scale, end.y())
    arrow = QPolygonF([
        tip,
        QPointF(tip.x() - ux * size + px * half,
                tip.y() - uy * size + py * half),
        QPointF(tip.x() - ux * size - px * half,
                tip.y() - uy * size - py * half),
    ])
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(col))
    painter.drawPolygon(arrow)


def _paint_edge_curve(painter: QPainter, a: "GraphNode", b: "GraphNode",
                      lane: int, total: int, col: QColor, zoom: float,
                      overview: bool = False):
    """按数据层坐标画一条完整的贝塞尔连线 + 箭头。

    细节模式、端点未 live 的补画、以及**缩小后的概览模式**都走这里：概览不再
    退化成中心点直线，线的形状 / 车道偏移 / 端点位置与放大后完全一致。
    cosmetic 笔宽按屏幕像素固定，缩到 0.05 倍时线也不会细到看不见；箭头按
    1/zoom 放大成场景单位，屏幕上仍是固定大小。
    """
    pa, pb = _edge_endpoints(a, b)
    path = _edge_path(pa, pb, lane, total)
    pen = QPen(col, 1.8)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    scale = 1.0 / max(zoom, 0.05) if overview else 1.0
    _paint_arrow(painter, path, col, scale)


class EdgeItem(QGraphicsItem):
    """贝塞尔连线 + 箭头 + 类型标签，全部矢量绘制。"""

    def __init__(self, src: NodeItem, dst: NodeItem, kind: str,
                 lane: int = 0, total: int = 1):
        super().__init__()
        self.src = src
        self.dst = dst
        self.kind = kind
        self.lane = lane           # 同起终点的多条线错开，避免互相盖住
        self.total = total
        self.label, self.color, _ = EDGE_SPECS[kind]
        self.setZValue(1)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self._path = QPainterPath()
        self._rect = QRectF()
        self._label_pos = QPointF()
        self.rebuild()
        src.edges.append(self)
        dst.edges.append(self)

    def rebuild(self):
        self.prepareGeometryChange()
        a = self.src.port_out()
        b = self.dst.port_in()
        # 同一对节点之间有多条关系时，用不同的弯曲量把线分开
        path = _edge_path(a, b, self.lane, self.total)
        self._path = path
        # 标签沿曲线错开，避免同组连线上的文字叠在一起
        t = max(0.22, min(0.78,
                          0.5 + (self.lane - (self.total - 1) / 2.0) * 0.20))
        self._label_pos = path.pointAtPercent(t) + QPointF(0, -8 - self.lane * 11)
        self._rect = path.boundingRect().adjusted(-46, -60, 46, 46)
        self.update()

    def boundingRect(self):
        return self._rect

    def shape(self):
        # 让边不拦截鼠标：图形视图的平移判断只看 NodeItem
        return QPainterPath()

    def paint(self, painter: QPainter, _option, _widget=None):
        col = QColor(self.color)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # cosmetic 笔宽：按屏幕像素固定，缩放时连线粗细不变（与全局连线层一致）
        pen = QPen(col, 1.8)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._path)

        # 终点箭头
        _paint_arrow(painter, self._path, col)

        # 标签
        f = QFont(painter.font())
        f.setPointSize(8)
        painter.setFont(f)
        painter.setPen(QPen(col))
        label_rect = QRectF(self._label_pos.x() - 40,
                            self._label_pos.y() - 14, 80, 14)
        painter.fillRect(label_rect, qc(INK, 190))
        painter.drawText(label_rect, Qt.AlignCenter, self.label)


class GraphOverviewItem(QGraphicsItem):
    """LOD 概览层：把整张图所有（通过过滤的）节点画成一个轻量色块层。

    整张图只占 **1 个** QGraphicsItem，paint 里直接画所有节点，故几千节点也不会
    像逐节点建 QGraphicsObject 那样卡。非交互（不接鼠标），双击视图可放大进入
    可编辑的细节模式。用于「打开别人写的大而无分组地图」时的快速浏览。
    """

    def __init__(self, dock: "TriggerGraphDock"):
        super().__init__()
        self.dock = dock
        # 概览节点色块必须盖在连线之上：全局连线层 z=6，故这里设 7。
        # 概览模式没有 live NodeItem（z=20），不存在与真实节点的层级冲突。
        self.setZValue(7)
        self.setAcceptedMouseButtons(Qt.NoButton)
        # 让 paint 拿到 exposedRect，局部重绘时只画视口附近的色块，
        # 而不是每次都遍历几千个节点（缩小/平移时的流畅度关键）。
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)
        self._nodes: list = []
        self._edges: list = []
        self._draw_edges = False
        self._rect = QRectF(0, 0, 1, 1)

    def set_content(self, nodes, edges, draw_edges: bool):
        self._nodes = nodes
        self._edges = edges
        self._draw_edges = draw_edges
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        for n in nodes:
            x0 = min(x0, n.x); y0 = min(y0, n.y)
            x1 = max(x1, n.x + NODE_W); y1 = max(y1, n.y + NODE_H)
        self._rect = (QRectF(x0 - 20, y0 - 20, (x1 - x0) + 40, (y1 - y0) + 40)
                      if x1 >= x0 else QRectF(0, 0, 1, 1))
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self):
        return self._rect

    def paint(self, painter, option, _widget=None):
        # 概览下节点在屏幕上不足 28px：抗锯齿/圆角既看不出又很贵，直接画色块。
        # 画贝塞尔曲线时仍开抗锯齿，否则缩小后曲线会变成明显的折线锯齿。
        painter.setRenderHint(QPainter.Antialiasing, self._draw_edges)
        exposed = option.exposedRect if option is not None else None
        cull = exposed is not None and not exposed.isEmpty()
        if self._draw_edges:
            col = qc(ASH, 110)
            view = self.dock.view
            zoom = view.zoom() if view is not None else 1.0
            nodes = self.dock.data.nodes
            for e in self._edges:
                a = nodes.get(e.src)
                b = nodes.get(e.dst)
                if a is None or b is None:
                    continue
                if cull:
                    ex = QRectF(QPointF(min(a.x, b.x), min(a.y, b.y)),
                                QPointF(max(a.x, b.x) + NODE_W,
                                        max(a.y, b.y) + NODE_H))
                    if not exposed.intersects(ex):
                        continue
                lane, total = self.dock._edge_lanes.get(id(e), (0, 1))
                _paint_edge_curve(painter, a, b, lane, total, col, zoom,
                                  overview=True)
        for n in self._nodes:
            box = QRectF(n.x, n.y, NODE_W, NODE_H)
            if cull and not exposed.intersects(box):
                continue
            col = _type_color(n.obj.type_)
            painter.setPen(QPen(QColor(INK), 1.0))
            painter.setBrush(QBrush(col))
            painter.drawRect(box)


class GraphEdgeLayerItem(QGraphicsItem):
    """全局连线层：常驻 scene，按**数据层坐标**画所有没被 live EdgeItem 画到的线。

    逐边 ``EdgeItem`` 只在两端节点都 live（在视口内）时才存在；一旦端点被视口
    虚拟化回收、或整体缩到概览模式（零 live 节点），那些线就跟着消失。本层补上
    这部分，保证「不管缩小、还是连接的节点没有渲染，线都渲染」：

    * 细节模式：补画「至少一端未 live」的边，用完整贝塞尔曲线 + 箭头；
    * 概览模式：补画全部边，仍是**同一条贝塞尔曲线**（不再退化成中心点直线），
      只省掉类型标签、笔宽略细，缩放很小时也看得出连线走向；
    * 始终按 ``exposedRect`` 裁剪，整层只占 1 个 QGraphicsItem，不随节点数增长。
    """

    def __init__(self, dock: "TriggerGraphDock"):
        super().__init__()
        self.dock = dock
        # 概览色块 z=5 之上、节点 z=20 之下：概览下线不会被不透明色块盖住
        self.setZValue(6)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)
        self._rect = QRectF(0, 0, 1, 1)
        self._boxes: dict[int, QRectF] = {}

    def _edge_box(self, e, nodes) -> QRectF | None:
        """一条边的裁剪盒：两端节点框 + 整条贝塞尔曲线的凸包，再放宽余量。

        裁剪盒必须包住整条曲线（凸包 = 两个端点 + 两个控制点），否则概览模式
        按 exposedRect 裁剪时会把弯曲的那一段切掉。抽成单独方法供全量/增量共用。
        """
        a = nodes.get(e.src)
        b = nodes.get(e.dst)
        if a is None or b is None:
            return None
        pa, pb = _edge_endpoints(a, b)
        lane, total = self.dock._edge_lanes.get(id(e), (0, 1))
        bow = (lane - (total - 1) / 2.0) * 46.0
        dx = max(56.0, abs(pb.x() - pa.x()) * 0.45)
        pts = (pa, pb,
               QPointF(pa.x() + dx, pa.y() + bow),
               QPointF(pb.x() - dx, pb.y() + bow))
        curve = QRectF(
            QPointF(min(p.x() for p in pts), min(p.y() for p in pts)),
            QPointF(max(p.x() for p in pts), max(p.y() for p in pts)))
        return QRectF(
            QPointF(min(a.x, b.x), min(a.y, b.y)),
            QPointF(max(a.x, b.x) + NODE_W,
                    max(a.y, b.y) + NODE_H)).united(curve).adjusted(
                        -70, -70, 70, 70)

    def set_content(self):
        """refresh 后重算每条边的裁剪盒与整层包围盒。"""
        nodes = self.dock.data.nodes
        r = QRectF()
        boxes: dict[int, QRectF] = {}
        for e in self.dock.data.edges:
            box = self._edge_box(e, nodes)
            if box is not None:
                boxes[id(e)] = box
                r |= box
        for n in nodes.values():
            r |= QRectF(n.x, n.y, NODE_W, NODE_H)
        self._boxes = boxes
        self.prepareGeometryChange()
        self._rect = (r.adjusted(-120, -120, 120, 120)
                      if not r.isEmpty() else QRectF(0, 0, 1, 1))
        self.update()

    def refresh_node_boxes(self, node_key: str):
        """节点被拖动后，只重算与该节点相连的边的裁剪盒（O(度数)）。

        旧实现只在 refresh 时算一次盒；节点拖动后坐标变了、盒却停在旧位置，
        paint 里 `visible.intersects(box)` 用旧盒判断，把已经移到视口内的线误裁
        掉 —— 表现就是「拖动节点后有的连线不见了」。这里增量更新受影响的边。
        """
        nodes = self.dock.data.nodes
        changed = False
        new_rect = self._rect
        for e in self.dock.data.edges:
            if e.src != node_key and e.dst != node_key:
                continue
            box = self._edge_box(e, nodes)
            if box is None:
                continue
            self._boxes[id(e)] = box
            new_rect |= box
            changed = True
        if changed:
            self.prepareGeometryChange()
            self._rect = new_rect
        self.update()

    def expand_to(self, x: float, y: float):
        """拖动节点时按需扩展包围盒（O(1)，不重算全图）。"""
        r = self._rect
        if (r.contains(QPointF(x, y))
                and r.contains(QPointF(x + NODE_W, y + NODE_H))):
            return
        self.prepareGeometryChange()
        self._rect = r.united(
            QRectF(x - 120, y - 120, NODE_W + 240, NODE_H + 240))
        self.update()

    def boundingRect(self):
        return self._rect

    def paint(self, painter, option, _widget=None):
        dock = self.dock
        overview = dock._overview_item is not None
        active = dock._active_cache
        if active is None:
            active = set(dock.data.nodes)
        live_edges = set(dock._live_edges)
        nodes = dock.data.nodes
        # 曲线需要抗锯齿，概览模式也开：否则缩小后贝塞尔会变成锯齿折线。
        painter.setRenderHint(QPainter.Antialiasing, True)
        view = dock.view
        zoom = view.zoom() if view is not None else 1.0

        # 以「当前视口矩形」作为裁剪判据（而非 Qt 的 exposedRect）：一条连线只要其
        # 曲线与视口相交就独立渲染，与两端节点是否在视口内、是否被虚拟化回收无关。
        # 因此两个对象相距极远、两端都不在视野里，只要连线穿过视野就一定显示。
        visible = None
        if view is not None:
            vp = view.viewport()
            if vp is not None and vp.width() > 0 and vp.height() > 0:
                tl = view.mapToScene(0, 0)
                br = view.mapToScene(vp.width(), vp.height())
                visible = QRectF(tl, br)

        for e in dock.data.edges:
            eid = id(e)
            if eid in live_edges:          # 细节模式：这条已由 EdgeItem 画过
                continue
            if e.src not in active or e.dst not in active:
                continue
            a = nodes.get(e.src)
            b = nodes.get(e.dst)
            if a is None or b is None:
                continue
            box = self._boxes.get(eid)
            if visible is not None and box is not None and not visible.intersects(box):
                continue
            col = QColor(EDGE_SPECS[e.kind][1])
            lane, total = dock._edge_lanes.get(eid, (0, 1))
            _paint_edge_curve(painter, a, b, lane, total, col, zoom,
                              overview)


# ---------------------------------------------------------------- 视图 / 场景
class GraphScene(QGraphicsScene):
    def __init__(self, dock: "TriggerGraphDock"):
        super().__init__(dock)
        self.dock = dock
        self._dots = _dot_brush()
        self.setBackgroundBrush(QBrush(_BG))

    def drawBackground(self, painter: QPainter, rect: QRectF):
        """网页版 #canvas 的深色 + 24px 圆点网格（纹理平铺，缩放时不重画）。"""
        painter.fillRect(rect, _BG)
        painter.fillRect(rect, self._dots)

    def on_node_moved(self, item: NodeItem):
        self.dock.remember_position(item.node.key, item.pos())


class GraphView(QGraphicsView):
    zoomChanged = Signal(float)

    def __init__(self, scene: GraphScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing |
                            QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.NoDrag)
        # 右键只用于「框选」，绝不弹任何上下文菜单 / 小窗口
        self.setContextMenuPolicy(Qt.NoContextMenu)
        # 鼠标事件实际落在 viewport 子控件上；只关 view 不够，viewport 默认
        # DefaultContextMenu，右键拖动结束会弹一个空白小菜单（一闪而过）。
        self.viewport().setContextMenuPolicy(Qt.NoContextMenu)
        # 关键：鼠标事件实际落在 view 的 viewport 子控件上，且显示的光标也是
        # viewport 的光标。所以追踪与 setCursor 必须作用在 viewport 上，否则：
        # ① 纯悬停（不按键）的 mouseMoveEvent 永不触发 → 十字光标不出现；
        # ② setCursor 改的是 view 自己的光标，viewport 仍显示箭头。
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setBackgroundBrush(QBrush(_BG))
        # 保留滚动条（隐藏式）：手写平移依赖 setValue，AlwaysOff 下部分平台会失效
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._panning = False
        self._pan_start = QPointF()
        self._band = None            # 右键框选用的橡皮筋选框
        self._band_start = QPointF()
        self._link_src = None        # 端口拖拽连线：{'item':NodeItem,'port':'out'/'in'}
        self._link_tmp = None        # 拖拽中的临时连线（QGraphicsLineItem）
        # 边缘判定区：拖动节点进入视口四周的细边条时，定时器持续滚动视口，
        # 即使鼠标停在判定区里不动也会一直滚（旧实现只在 mouseMove 事件里滚一步）。
        self._edge_dir = (0, 0)      # 当前命中方向 (dx, dy)，(0,0)=未命中
        self._last_auto_scroll_ms = 0
        self._auto_scroll_paused = False
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(self.AUTO_SCROLL_INTERVAL_MS)
        self._auto_scroll_timer.timeout.connect(self._auto_scroll_tick)

    def paintEvent(self, event):
        # 边缘判定区（四周窄带）此前会在拖动节点时画半透明色条，按用户要求隐藏：
        # 只保留「进入边缘持续滚动」的功能逻辑（_edge_direction / 定时器），
        # 不再有任何可见提示。判定的功能测试不依赖绘制，故直接去掉绘制段。
        super().paintEvent(event)

    def zoom(self) -> float:
        return float(self.transform().m11())

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        new_zoom = self.zoom() * factor
        if not 0.08 <= new_zoom <= 4.0:
            event.accept()
            return
        self.scale(factor, factor)
        self.zoomChanged.emit(self.zoom())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 先判断是否抓在节点端口（黄点）上：是则进入「拖拽连线」模式
            hit = self._hit_port(self.mapToScene(event.pos()))
            if hit is not None:
                item, port = hit
                self._link_src = {"item": item, "port": port}
                p0 = item.scene_port_out() if port == "out" else item.scene_port_in()
                self._link_tmp = QGraphicsLineItem(QLineF(p0, p0))
                self._link_tmp.setPen(
                    QPen(QColor(THEME["port_out"]), 2.0, Qt.DashLine))
                self._link_tmp.setZValue(6000)
                self.scene().addItem(self._link_tmp)
                event.accept()
                return
            item = self.itemAt(event.pos())
            if not isinstance(item, NodeItem):
                self._panning = True
                self._pan_start = event.pos()
                self.viewport().setCursor(Qt.ClosedHandCursor)
                event.accept()
                return
            # 拖动节点期间禁止虚拟化回收它：拖出视口也不能被删掉，否则拖动会
            # 突然中断，表现就是「移动范围有限制」。
            dock = self.scene().dock if self.scene() is not None else None
            if dock is not None:
                dock._dragging_key = item.node.key
        elif event.button() == Qt.RightButton:
            # 右键空白框选：拉一个矩形选区，松开后选中其中的节点
            if not isinstance(self.itemAt(event.pos()), NodeItem):
                self._band_start = self.mapToScene(event.pos())
                self._band = QGraphicsRectItem()
                self._band.setPen(QPen(QColor(120, 180, 255), 1.2, Qt.DashLine))
                self._band.setBrush(QBrush(QColor(120, 180, 255, 28)))
                self._band.setZValue(5000)
                self.scene().addItem(self._band)
                self._band.setRect(QRectF(self._band_start, self._band_start))
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._link_src is not None and self._link_tmp is not None:
            src = self._link_src["item"]
            p0 = (src.scene_port_out() if self._link_src["port"] == "out"
                  else src.scene_port_in())
            p1 = self.mapToScene(event.pos())
            self._link_tmp.setLine(QLineF(p0, p1))
            event.accept()
            return
        if self._band is not None:
            p = self.mapToScene(event.pos())
            self._band.setRect(QRectF(self._band_start, p).normalized())
            event.accept()
            return
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        # 空闲悬停（未按任何键）：鼠标落在端口（黄球）上时显示十字光标，
        # 提示「可从此拖拽连线」；移开则复位。仅在无按键时处理，避免干扰节点拖动。
        if event.buttons() == Qt.NoButton:
            if self._hit_port(self.mapToScene(event.pos())) is not None:
                self.viewport().setCursor(Qt.CrossCursor)
            else:
                self.viewport().unsetCursor()
        super().mouseMoveEvent(event)
        # 拖动节点到视口边缘时让视图滚动跟随，节点就能一直被拖着走
        dock = self.scene().dock if self.scene() is not None else None
        if (dock is not None and dock._dragging_key is not None
                and (event.buttons() & Qt.LeftButton)
                and not self._panning and self._link_src is None
                and self._band is None):
            self._auto_scroll_on_drag(event.pos())

    def mouseReleaseEvent(self, event):
        dock = self.scene().dock if self.scene() is not None else None
        if (event.button() == Qt.LeftButton and dock is not None
                and dock._dragging_key is not None):
            dock._dragging_key = None
            self._stop_auto_scroll()
            dock._schedule_sync()
        if (self._link_src is not None and self._link_tmp is not None
                and event.button() == Qt.LeftButton):
            src_item = self._link_src["item"]
            src_port = self._link_src["port"]
            self.scene().removeItem(self._link_tmp)
            self._link_tmp = None
            self._link_src = None
            target = self._target_node_at(self.mapToScene(event.pos()))
            # 源节点的 out 端口 + 目标节点的 in 端口（或反向）即建立一条「激活」连线
            if target is not None and target is not src_item:
                if src_port == "out":
                    self.scene().dock.create_link(src_item.node, target.node)
                else:
                    self.scene().dock.create_link(target.node, src_item.node)
            self.viewport().unsetCursor()
            event.accept()
            return
        if self._band is not None and event.button() == Qt.RightButton:
            rect = self._band.rect()
            self.scene().removeItem(self._band)
            self._band = None
            dock = self.scene().dock
            items = [i for i in dock.node_items.values()
                     if i.sceneBoundingRect().intersects(rect)]
            # 批量选中：先屏蔽 selectionChanged，避免每 setSelected 一次就跑
            # 一遍 _on_selection_changed（全量重建属性面板 + 回主画布刷新选中
            # 手柄）。框住 N 个节点原本会把整条链跑 N 次，对象多时明显卡顿；
            # 循环结束统一跑一次即可，选中结果完全相同。
            self.scene().blockSignals(True)
            try:
                self.scene().clearSelection()
                for it in items:
                    it.setSelected(True)
            finally:
                self.scene().blockSignals(False)
            # 一次回调完成：同组扩容 + 更新 _selected_keys + 重建一次属性面板
            # + 只回主画布一次。
            dock._on_selection_changed()
            event.accept()
            return
        if self._panning and event.button() == Qt.LeftButton:
            self._panning = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def scrollContentsBy(self, dx: int, dy: int):
        super().scrollContentsBy(dx, dy)
        dock = self.scene().dock if self.scene() is not None else None
        if dock is not None:
            # 平移后视口内的节点集合变了：合并到 30ms 一次重建，避免每帧 O(N) 卡顿
            dock._schedule_sync()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        dock = self.scene().dock if self.scene() is not None else None
        if dock is not None:
            dock._schedule_sync()

    def mouseDoubleClickEvent(self, event):
        dock = self.scene().dock if self.scene() is not None else None
        if dock is not None and dock._overview_item is not None:
            # 概览模式双击：以点击处为中心放大，进入可编辑的细节模式
            sp = self.mapToScene(event.pos())
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
            self.scale(2.2, 2.2)
            self.centerOn(sp)
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            dock._schedule_sync()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # 边缘判定区：视口四周各一条窄带（屏幕像素，不随缩放变化）。拖动节点
    # 进入判定区即启动定时器持续滚动，鼠标停住也一直走；离开 / 松手即停。
    EDGE_ZONE_PX = 22                 # 判定区宽度（"不要太大"）
    AUTO_SCROLL_INTERVAL_MS = 32      # 定时器间隔 / 节流间隔
    AUTO_SCROLL_PX = 5                # 每步滚动的屏幕像素

    def _auto_scroll_enabled(self) -> bool:
        return not getattr(self, "_auto_scroll_paused", False)

    def _edge_direction(self, pos):
        """鼠标落在视口边缘判定区时返回 (dx, dy)，否则 (0, 0)。"""
        vp = self.viewport()
        x, y = pos.x(), pos.y()
        dx = -1 if x < self.EDGE_ZONE_PX else (
            1 if x > vp.width() - self.EDGE_ZONE_PX else 0)
        dy = -1 if y < self.EDGE_ZONE_PX else (
            1 if y > vp.height() - self.EDGE_ZONE_PX else 0)
        return dx, dy

    def _auto_scroll_tick(self):
        """定时器回调：只要还在判定区且正在拖动，就持续滚动一小步。

        滚动视口的同时把被拖节点沿同方向移动对应场景距离，让它保持在鼠标所在
        的屏幕边缘 —— 否则鼠标停住时视口滚走、节点却停在原场景位置，看起来
        「拖到边缘就不动」。用滚动条**实际变化量**驱动节点位移，滚到极限时节点
        也不会脱离视口。
        """
        if not self._auto_scroll_enabled() or self._edge_dir == (0, 0):
            self._stop_auto_scroll()
            return
        dock = self.scene().dock if self.scene() is not None else None
        if dock is None or dock._dragging_key is None:
            self._stop_auto_scroll()
            return
        dx, dy = self._edge_dir
        step = max(1, int(round(self.AUTO_SCROLL_PX / max(self.zoom(), 0.08))))
        moved_x = moved_y = 0
        if dx:
            bar = self.horizontalScrollBar()
            old = bar.value()
            bar.setValue(old + dx * step)
            moved_x = bar.value() - old
        if dy:
            bar = self.verticalScrollBar()
            old = bar.value()
            bar.setValue(old + dy * step)
            moved_y = bar.value() - old
        if moved_x or moved_y:
            self._move_dragged_node_by(moved_x, moved_y, dock)

    def _move_dragged_node_by(self, screen_dx: int, screen_dy: int, dock):
        """被拖节点跟随视口滚动：屏幕位移换算成场景坐标并前移节点。

        setPos 会触发 itemChange → on_node_moved → remember_position，从而同步
        数据层坐标、连线裁剪盒、动态平移范围；这些回调已有重入锁保护。
        """
        item = dock.node_items.get(dock._dragging_key)
        if item is None:
            return
        z = max(self.zoom(), 0.08)
        pos = item.pos()
        item.setPos(pos.x() + screen_dx / z, pos.y() + screen_dy / z)

    def _stop_auto_scroll(self):
        """停止持续滚动并清掉判定区高亮。"""
        self._edge_dir = (0, 0)
        self._last_auto_scroll_ms = 0
        if self._auto_scroll_timer.isActive():
            self._auto_scroll_timer.stop()
        self.viewport().update()

    def _auto_scroll_on_drag(self, pos):
        """拖动节点到边缘判定区：更新方向、启动持续滚动，并立即走一步。

        保留「鼠标事件驱动的一步滚动 + 时间节流」（兼容原有行为与测试），
        在此基础上新增 QTimer 持续驱动：鼠标停在判定区里不动时也会一直滚。
        """
        if not self._auto_scroll_enabled():
            self._stop_auto_scroll()
            return
        direction = self._edge_direction(pos)
        self._edge_dir = direction
        if direction == (0, 0):
            # 离开边缘区：停定时器 + 清节流戳，下次贴边能马上响应
            self._stop_auto_scroll()
            return
        if not self._auto_scroll_timer.isActive():
            self._auto_scroll_timer.start()
        now = int(time.monotonic() * 1000)
        last = getattr(self, "_last_auto_scroll_ms", 0)
        if last and now - last < self.AUTO_SCROLL_INTERVAL_MS:
            return
        self._last_auto_scroll_ms = now
        # 即时走一步：不检查 dragging_key（兼容脱离真实拖动的直接调用 / 测试）。
        # 定时器后续步骤才检查 dragging_key，保证松手后不再持续滚。
        dx, dy = direction
        step = max(1, int(round(self.AUTO_SCROLL_PX / max(self.zoom(), 0.08))))
        if dx:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() + dx * step)
        if dy:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + dy * step)

    def _hit_port(self, sp: QPointF):
        """命中某个节点的端口（黄点）则返回 (item, 'out'/'in')，否则 None。

        判定区按「屏幕像素」固定（默认 12px），不随 fit 缩放变小 —— 否则节点一多、
        fit 把视图缩得很小，黄球在屏幕上不到 1px，根本点不中（之前一直连不上的根因）。
        这里把端口与光标都投影到屏幕坐标再比较，任何缩放下都有舒适的命中范围。
        """
        tol = 12.0
        tol2 = tol * tol
        for item in self.scene().dock.node_items.values():
            if not item.isVisible():
                continue
            for port, name in ((item.scene_port_out(), "out"),
                               (item.scene_port_in(), "in")):
                pp = self.mapFromScene(port)
                ps = self.mapFromScene(sp)
                dx = pp.x() - ps.x()
                dy = pp.y() - ps.y()
                if dx * dx + dy * dy <= tol2:
                    return (item, name)
        return None

    def _target_node_at(self, sp: QPointF):
        """拖拽落点所在（或最近）的节点；返回 NodeItem 或 None（仅可见节点）。"""
        best = None
        best_d = None
        for item in self.scene().dock.node_items.values():
            if not item.isVisible():
                continue
            r = item.sceneBoundingRect().adjusted(-8, -8, 8, 8)
            if not r.contains(sp):
                continue
            d = (item.sceneBoundingRect().center() - sp).manhattanLength()
            if best_d is None or d < best_d:
                best, best_d = item, d
        return best

    def keyPressEvent(self, event):
        # 画布上不支持键盘删除（避免误删）；删除请用右侧属性面板的按钮
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            event.accept()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event):
        self.viewport().unsetCursor()
        super().leaveEvent(event)


# ---------------------------------------------------------------- 面板
class TriggerGraphDock(QDockWidget):
    """触发器节点图，默认隐藏；从「设置」打开时是浮动独立窗口。

    右侧为网页版同款属性面板（可编辑、走撤销栈），新增对象仍在地图画布上做。
    """

    objectSelected = Signal(object)          # 单击节点 → 高亮地图上的对象
    objectActivateRequested = Signal(object)  # 双击节点 → 地图定位

    def __init__(self, parent=None):
        super().__init__("触发器节点图", parent)
        self.setObjectName("panels.trigger_graph")
        # 浮动状态下本 dock 是独立顶层窗口：右键菜单事件冒泡到这里时，若仍是
        # 默认策略会弹出一个空的原生菜单（一闪而过的空框）。顶层一律关掉。
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.doc = None
        self.data = GraphData()
        self.undo_stack = None
        self.on_change = None
        self.node_items: dict[str, NodeItem] = {}
        self.edge_items: list[EdgeItem] = []
        # 虚拟化：scene 里只保留「视口内 + 一圈外」的 live 节点/连线；其余按需增删。
        self._live_edges: dict[int, EdgeItem] = {}
        self._selected_keys: set[str] = set()      # 数据层选中态，跨虚拟化存活
        self._overview_item: "GraphOverviewItem" | None = None
        # 正在拖动的节点：虚拟化不得回收它，否则拖出视口就「拖不动 / 节点消失」
        self._dragging_key: str | None = None
        # setSceneRect 引发滚动→itemChange 连锁回调时的重入锁，防拖动出视口爆栈
        self._scene_rect_syncing: bool = False
        # 全局连线层的过滤缓存与同起终点「车道」表（None = 尚未计算）
        self._active_cache: set[str] | None = None
        self._edge_lanes: dict[int, tuple[int, int]] = {}
        # 平移/缩放时把 _sync_visible 合并到 30ms 一次，避免每帧 O(N) 遍历卡顿
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._sync_visible)
        self._manual_pos: dict[str, QPointF] = {}
        # 所有见过的节点位置（含自动布局的结果），按 key 缓存。refresh 会重建
        # data.nodes，只有按 key 恢复坐标，才能保证「改属性 / 连一条线 / 撤销 /
        # 切层再切回来」时节点不跳位。换文档时清空。
        self._pos_cache: dict[str, QPointF] = {}
        # 当前右侧详情对应的对象身份。重复 object_id 的地图里
        # inspector.current_object_id() 无法区分对象，refresh / 撤销后必须靠它重绑。
        self._detail_uid = None
        self._detail_oid = None
        self._dirty = True
        self._pending_fit = False
        self._loading = False
        self._color_by_group = False
        # 当前显示的对象层过滤：只可能是某个真实对象层名；没有对象层时为 None
        self._layer_filter = None
        # 分组「页」：'' 选择已有组（未分组，默认）/ 具体组名；
        # 'ALL' 仅保留给外部代码显式请求「显示全部」，下拉里默认不再有这一档。
        self._group_page = ""
        # 会话内的「空组」：新建组时没选中对象也能建；写不进文档，因为分组名
        # 是从对象的私有属性反推的，一个对象都没有时保存后自然不存在。
        self._empty_groups: set[str] = set()
        # 连线编辑后主窗口 on_change 会 refresh(fit=True)，用它抑制一次自动适应视图，
        # 避免点一下叉整张图就跳回居中。
        self._suppress_fit_once = False

        holder = QWidget(self)
        holder.setObjectName("TriggerGraphHolder")
        holder.setStyleSheet(f"""
            QWidget#TriggerGraphHolder{{background:{THEME['bg']};}}
            QPushButton{{background:{THEME['node']};color:{THEME['text']};border:1px solid {INK};
                        border-radius:4px;padding:4px 9px;font-size:11px;}}
            QPushButton:hover{{background:rgba(36, 36, 36, 200);border-color:{INK};}}
            QComboBox,QLineEdit{{background:{THEME['input']};color:{THEME['text']};
                        border:1px solid {INK};border-radius:4px;
                        padding:3px 6px;font-size:11px;}}
            QComboBox::drop-down{{border:0;}}
            QLabel{{color:{THEME['muted']};font-size:11px;}}
            QSplitter::handle{{background:{THEME['node']};}}
            QScrollBar:vertical,QScrollBar:horizontal{{background:{THEME['panel']};
                        width:8px;height:8px;}}
            QScrollBar::handle:vertical,QScrollBar::handle:horizontal{{
                        background:{THEME['node']};border-radius:4px;}}
            QScrollBar::add-line,QScrollBar::sub-line{{height:0;width:0;}}
        """)
        root = QVBoxLayout(holder)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ---- 工具栏 ----
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.btn_refresh = QPushButton("刷新")
        self.btn_layout = QPushButton("自动布局")
        self.btn_fit = QPushButton("适应视图")
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_out = QPushButton("−")
        for btn in (self.btn_refresh, self.btn_layout, self.btn_fit):
            bar.addWidget(btn)
        self.btn_zoom_in.setFixedWidth(24)
        self.btn_zoom_out.setFixedWidth(24)
        bar.addWidget(self.btn_zoom_in)
        bar.addWidget(self.btn_zoom_out)
        bar.addSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索名称 / 类型 / 属性…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(180)
        bar.addWidget(self.search_edit)
        bar.addSpacing(8)
        self.layer_combo = QComboBox()
        self.layer_combo.setFixedWidth(150)
        # 不设 tooltip：见 NodeItem 说明，避免右键操作后弹出一闪而过的 QTipLabel
        bar.addWidget(self.layer_combo)
        bar.addStretch(1)
        root.addLayout(bar)

        # ---- 分组工具栏（分页：每组合一页，a 组不显示 b 组）----
        gbar = QHBoxLayout()
        gbar.setSpacing(4)
        self.btn_new_group = QPushButton("新建组")
        self.group_combo = QComboBox()
        self.group_combo.setFixedWidth(140)
        # 不设 tooltip：见 NodeItem 说明，避免右键操作后弹出一闪而过的 QTipLabel
        self.btn_color_by_group = QPushButton("按组着色")
        self.btn_color_by_group.setCheckable(True)
        for btn in (self.btn_new_group, self.group_combo,
                    self.btn_color_by_group):
            gbar.addWidget(btn)
        gbar.addStretch(1)
        gbar_label = QLabel("分组（仅本软件识别，铁锈不读取）")
        gbar_label.setStyleSheet(f"QLabel{{color:{TEXT_DIM};font-size:10px;}}")
        gbar.addWidget(gbar_label)
        root.addLayout(gbar)

        self.banner = QLabel(
            "节点 = 对象层里的全部对象（矩形 / 点 / 线 / 文字…，顶部可切换只看某一对象层）"
            " · 新增对象请在地图画布上操作 · 右侧属性可直接编辑（走撤销栈）"
            " · 拖空白平移 / 滚轮缩放 / 双击节点定位到地图"
            " · 从黄点（端口）拖到另一个节点即可连出「激活」线"
            " · 右侧连线列表里点叉删除连线，右键连线行可改线型")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            f"QLabel{{background:{INK};color:{TEXT_DIM};border:1px solid {ASH};"
            "border-radius:3px;padding:3px 6px;font-size:11px;}")
        root.addWidget(self.banner)

        # ---- 画布 + 网页版同款属性面板 ----
        self.scene = GraphScene(self)
        # 全局连线层常驻 scene：不随节点增删，缩小 / 端点未渲染时线依然存在
        self.edge_layer = GraphEdgeLayerItem(self)
        self.scene.addItem(self.edge_layer)
        self.view = GraphView(self.scene, self)
        self.inspector = TriggerInspector()
        self.inspector.setMinimumWidth(260)
        splitter = QSplitter(Qt.Horizontal, holder)
        splitter.addWidget(self.view)
        splitter.addWidget(self.inspector)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([760, 290])
        root.addWidget(splitter, 1)

        self.status = QLabel("未载入地图")
        self.status.setStyleSheet(f"QLabel{{color:{TEXT_DIM};font-size:11px;}}")
        root.addWidget(self.status)

        self.setWidget(holder)
        install_title_bar(self)
        # 浮动 QDockWidget 的原生边框在部分平台是白色，显式压成统一的
        # 1px ASH 暗线（所有面板共用 theme.PANEL_BORDER_QSS）。
        self.setStyleSheet(
            f"QDockWidget{{{PANEL_BORDER_QSS}background:{INK};}}")
        self.setMinimumSize(560, 380)

        # ---- 信号 ----
        self.btn_refresh.clicked.connect(lambda: self.refresh(fit=False))
        self.btn_layout.clicked.connect(self.auto_layout)
        self.btn_fit.clicked.connect(self.fit_view)
        self.btn_zoom_in.clicked.connect(lambda: self._zoom_by(1.2))
        self.btn_zoom_out.clicked.connect(lambda: self._zoom_by(1 / 1.2))
        self.search_edit.textChanged.connect(self._apply_filter)
        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        self.view.zoomChanged.connect(self._update_status)
        self.view.zoomChanged.connect(self._schedule_sync)
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self.inspector.objectDeleteRequested.connect(self._delete_object)
        self.inspector.edgeDeleteRequested.connect(self.delete_edge)
        self.inspector.edgeMenuRequested.connect(self._show_edge_menu)
        self.btn_new_group.clicked.connect(self._new_group_dialog)
        self.group_combo.currentIndexChanged.connect(self._on_group_page_changed)
        self.btn_color_by_group.toggled.connect(self._toggle_color_by_group)

    # ---------------- 生命周期 ----------------
    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty or self._pending_fit or not self.node_items:
            self.refresh(fit=False)
            self._pending_fit = False
            # 布局要在 show 之后一帧才拿到正确的 viewport 尺寸
            QTimer.singleShot(0, self.fit_view)

    def set_document(self, doc, undo_stack=None, on_change=None):
        self.doc = doc
        self.undo_stack = undo_stack
        self.on_change = on_change
        self._manual_pos.clear()
        self._pos_cache.clear()
        self._detail_uid = None
        self._detail_oid = None
        self._layer_filter = self._default_layer_name(doc)
        self._group_page = ""
        self._empty_groups.clear()
        self._dirty = True
        self._clear_detail(reset=True)
        self.inspector.set_context(doc, None, undo_stack, self._after_object_change)
        if self.isVisible():
            self.refresh()

    # ---------------- 对象层 / 过滤默认值 ----------------
    def _object_layer_names(self) -> list[str]:
        """文档里所有非空名字的对象层（不重复，保持文档顺序）。"""
        return self._object_layer_names_from(self.doc)

    @classmethod
    def _default_layer_name(cls, doc):
        """默认停在 Triggers 层；没有就叫第一个对象层；都没有则 None。"""
        names = cls._object_layer_names_from(doc)
        for name in names:
            if name.lower() == "triggers":
                return name
        return names[0] if names else None

    @staticmethod
    def _object_layer_names_from(doc) -> list[str]:
        if doc is None:
            return []
        names: list[str] = []
        for layer in doc.layers:
            if isinstance(layer, ObjectLayer):
                name = (layer.name or "").strip()
                if name and name not in names:
                    names.append(name)
        return names

    def remember_position(self, key: str, pos: QPointF):
        if self._loading:
            return
        self._manual_pos[key] = QPointF(pos)
        self._pos_cache[key] = QPointF(pos)
        # 关键：数据层坐标必须跟着拖动同步。否则视口裁剪、全局连线层、刷新后恢复
        # 布局用的都还是旧坐标 —— 线留在原地，节点看起来「拖了却没生效」。
        node = self.data.nodes.get(key)
        if node is not None:
            node.x, node.y = pos.x(), pos.y()
        self.edge_layer.expand_to(pos.x(), pos.y())
        # 增量重算该节点相连边的裁剪盒：否则盒停在旧位置，全局连线层会把移到
        # 视口内的线裁掉（拖完节点后有的连线消失）。
        self.edge_layer.refresh_node_boxes(key)
        self.edge_layer.update()
        # 节点位置已同步到 data，重算动态平移范围（可增可减）
        self._ensure_scene_rect()

    # ---------- 节点图位置属性（仅保存时写，不入撤销栈） ----------
    @staticmethod
    def _parse_node_pos(raw) -> tuple[float, float] | None:
        if not raw:
            return None
        try:
            xs, ys = str(raw).split(",", 1)
            return float(xs), float(ys)
        except (ValueError, TypeError):
            return None

    def write_positions_to_properties(self) -> int:
        """保存前调用：把当前节点图布局写进每个对象的私有属性。

        以「左上角」节点（x+y 最小，平局取更靠左上者）为原点 (0,0)，其余节点
        写相对偏移 ``dx,dy``。这样整张图平移不会让坐标整体漂移，原点对象恒为
        (0,0)。只在保存时调用——拖动过程不写、不入撤销栈。写后置所在对象层
        dirty，writer 才会把属性落盘。

        返回写入的对象数；节点图从未打开（data 为空）时返回 0，不误写。
        """
        if self.data is None or not self.data.nodes:
            return 0
        nodes = list(self.data.nodes.values())
        origin = min(nodes, key=lambda n: (n.x + n.y, n.x, n.y))
        ox, oy = origin.x, origin.y
        touched: set[str] = set()
        count = 0
        for n in nodes:
            obj = n.obj
            if obj is None or obj.properties is None:
                continue
            dx = int(round(n.x - ox))
            dy = int(round(n.y - oy))
            obj.properties.set(EDITOR_NODE_POS_PROPERTY, f"{dx},{dy}", "string")
            touched.add(n.layer_name)
            count += 1
        for name in touched:
            for layer in (self.doc.layers if self.doc else []):
                if isinstance(layer, ObjectLayer) and layer.name == name:
                    layer.dirty = True
        return count

    def _saved_positions(self) -> dict[str, QPointF]:
        """从对象属性读回保存时的相对布局；属性值即绝对坐标（原点恒在 0,0）。

        只在「缓存为空」（首次打开文档）时由 refresh 采用，会话内手动摆位优先。
        """
        out: dict[str, QPointF] = {}
        for key, n in self.data.nodes.items():
            if n.obj is None or n.obj.properties is None:
                continue
            p = self._parse_node_pos(
                n.obj.properties.get(EDITOR_NODE_POS_PROPERTY))
            if p is not None:
                out[key] = QPointF(p[0], p[1])
        return out

    # ---------- 动态平移范围 ----------
    # 设计：sceneRect = 全部节点内容框 + 四周各「一个视口尺寸」（场景坐标）。
    # QGraphicsView 视口左上角的可移动区间是 [sceneRect.left, sceneRect.right-W]，
    # 其中 W = 视口宽/zoom。左右各扩 W 后，该区间变成
    # [contentLeft-W, contentRight]：视口推到最左，右边缘正好落在最左节点左边缘
    # （该节点刚好完全移出）；推到最右，左边缘落在最右节点右边缘。上下同理。
    # 于是平移极限 = 最近节点刚好完全不显示；视口在节点群中间时区间很大、自由
    # 平移；节点被拖走 → 内容框变 → 范围重算，视口被 Qt clamp 到新合法位置。
    @staticmethod
    def _rect_fuzzy_equal(a: QRectF, b: QRectF, eps: float = 1.0) -> bool:
        return (abs(a.left() - b.left()) <= eps and abs(a.top() - b.top()) <= eps
                and abs(a.right() - b.right()) <= eps
                and abs(a.bottom() - b.bottom()) <= eps)

    def _content_rect(self) -> QRectF:
        """当前显示层全部数据节点的内容框（用 data 层坐标，虚拟化回收不影响）。

        只统计 ``data.nodes``（已按当前对象层过滤）里的节点：节点的 x/y 在拖动
        时由 remember_position 同步，所以 data 坐标就是最新布局，无需再叠加
        _manual_pos。旧实现无条件遍历整个 _manual_pos，而它的 key 带层名前缀、
        跨层累积，切到别的层后仍被上一层手摆到远处的坐标撑大 —— 表现就是
        「切换对象层时视口边界不变，一直在算别的对象」。改用当前层节点集合后，
        边界随层切换正确收缩 / 扩展。
        """
        r = QRectF()
        for n in self.data.nodes.values():
            r |= QRectF(n.x, n.y, NODE_W, NODE_H)
        if r.isEmpty():
            r = QRectF(0, 0, NODE_W, NODE_H)
        return r

    def _desired_scene_rect(self) -> QRectF:
        """目标 sceneRect = 内容框 + 四周各一个视口尺寸（场景坐标）。"""
        r = self._content_rect()
        vp = self.view.viewport()
        z = max(self.view.zoom(), 0.05)
        mx = (vp.width() / z) if vp is not None and vp.width() > 0 else 0.0
        my = (vp.height() / z) if vp is not None and vp.height() > 0 else 0.0
        return r.adjusted(-mx, -my, mx, my)

    def _ensure_scene_rect(self, point=None):
        """把 sceneRect 同步到动态范围（可增可减）。

        ``point`` 仅为兼容旧调用保留，已不再使用：范围始终按全部节点重算，
        节点往回拖时才能收缩。容差比较避免 rect 未变时反复 setSceneRect。

        重入保护：setSceneRect 会改变滚动条 → scrollContentsBy → 移动 item →
        itemChange → on_node_moved → remember_position → 又调到这里。若不加锁，
        拖动节点出视口时会形成无限递归（实测爆栈）。用 ``_scene_rect_syncing``
        把这次 setSceneRect 引发的连锁回调挡在门外。
        """
        if self.scene is None or self.view is None or self._scene_rect_syncing:
            return
        target = self._desired_scene_rect()
        if self._rect_fuzzy_equal(self.scene.sceneRect(), target):
            return
        self._scene_rect_syncing = True
        try:
            self.scene.setSceneRect(target)
        finally:
            self._scene_rect_syncing = False

    def select_object(self, obj):
        """地图画布选中对象时，同步高亮对应节点（不发 objectSelected，避免回环）。"""
        if obj is None:
            return
        self.select_objects([obj])

    def _clear_detail(self, reset: bool = False):
        """清空右侧详情并同步丢弃「当前详情对象」身份缓存。"""
        self._detail_uid = None
        self._detail_oid = None
        if reset:
            self.inspector.reset()
        else:
            self.inspector.clear()

    def _object_layers(self):
        if self.doc is None:
            return []
        return [l for l in self.doc.layers if isinstance(l, ObjectLayer)]

    def _find_object(self, target):
        """在**当前文档**里找 (当前实例, 所属对象层)。

        ``target`` 可以是对象（按稳定身份 uid 精确匹配）或 object_id（旧引用
        兜底）。uid 基于 ``xml_element``，跨 ObjectCommand 快照替换仍指向同一
        逻辑对象，因此重复 object_id 时不会认错。
        """
        layers = self._object_layers()
        if not layers:
            return None, None
        uid, oid = None, None
        if hasattr(target, "properties"):
            uid = object_uid(target)
            oid = getattr(target, "object_id", None)
        else:
            oid = target
        if uid is not None:
            for layer in layers:
                for obj in layer.objects:
                    if object_uid(obj) == uid:
                        return obj, layer
        if oid is not None:
            for layer in layers:
                for obj in layer.objects:
                    if obj.object_id == oid:
                        return obj, layer
        return None, None

    def _node_of_obj(self, obj):
        """在 data.nodes 里按 uid 找节点；找不到再按 object_id 兜底。"""
        uid = object_uid(obj)
        for node in self.data.nodes.values():
            if object_uid(node.obj) == uid:
                return node
        oid = getattr(obj, "object_id", None)
        if oid is not None:
            for node in self.data.nodes.values():
                if node.obj.object_id == oid:
                    return node
        return None

    def _node_for_object(self, obj, layer_name: str) -> GraphNode:
        """对象不在当前显示的对象层时，临时构造一个只用于右侧详情的节点。"""
        key = next((n.key for n in self.data.nodes.values()
                    if object_uid(n.obj) == object_uid(obj)), None)
        if key is None:
            # 独立前缀：绝不和真实节点 key 混进 _pos_cache / 连线端点。
            key = f"@detail::{layer_name}::{obj.object_id}#{id(obj)}"
        return GraphNode(
            key, obj, layer_name,
            str(obj.properties.get("id") or obj.object_id),
            group=str(obj.properties.get(EDITOR_GROUP_PROPERTY) or ""))

    def select_objects(self, objs):
        """批量高亮：框选多个对象时，节点图里对应节点全选并展示第一个详情。

        匹配按 ``object_uid``（基于 xml_element 的稳定身份）而不是 ``object_id``：
        铁锈地图里 Tiled object id 大量重复，按 id 会把任意对象都匹配到第一个
        节点。ObjectCommand 用快照整体替换 layer.objects 时 uid 仍稳定；无
        xml_element 的测试对象退化为 object_id 兜底。节点没被渲染（视口虚拟化
        / 概览模式）时也要能显示详情。
        """
        if not objs:
            return
        targets: list[tuple[GraphNode, object]] = []
        for obj in objs:
            node = self._node_of_obj(obj)
            if node is not None:
                targets.append((node, self.node_items.get(node.key)))
        if not targets:
            # 目标不在当前分组页 / 对象层里：清掉旧选中态，避免「新建组」
            # 「加入组」作用在上一批残留节点上。右侧仍按文档里的真实对象
            # 显示属性（跨层选中），实在找不到才清空，绝不残留旧数据。
            self._loading = True
            self.scene.blockSignals(True)
            self.scene.clearSelection()
            self.scene.blockSignals(False)
            self._loading = False
            self._selected_keys = set()
            for obj in objs:
                current, layer = self._find_object(obj)
                if current is not None:
                    self._show_detail(self._node_for_object(current, layer.name))
                    return
            self._clear_detail()
            return
        self._loading = True
        self.scene.blockSignals(True)
        self.scene.clearSelection()
        for node, item in targets:
            if item is not None:
                item.setSelected(True)
        self.scene.blockSignals(False)
        self._loading = False
        # 跨虚拟化保留选中态：节点滚回视口重建时仍高亮
        self._selected_keys = {node.key for node, _ in targets}
        self._show_detail(targets[0][0])

    # ---------------- 构建 ----------------
    # ---------------- 性能：虚拟化 + LOD 概览 ----------------
    def _schedule_sync(self):
        """合并多次滚动/缩放事件，30ms 内只跑一次 _sync_visible。"""
        if not self._sync_timer.isActive():
            self._sync_timer.start(30)

    def _active_keys(self) -> set:
        """当前过滤/分组/搜索下应当显示的节点 key 集合。"""
        return {n.key for n in self.data.nodes.values() if self._matches(n)}

    def _use_overview(self) -> bool:
        """节点在屏幕上小于阈值 → 用概览模式（单 item 画全部），不建逐节点对象。"""
        if not self.data.nodes:
            return False
        zoom = self.view.zoom() if self.view is not None else 1.0
        return (NODE_W * zoom) < OVERVIEW_MIN_NODE_PX

    def _remove_edge_item(self, item: "EdgeItem"):
        if item in self.edge_items:
            self.edge_items.remove(item)
        for k, v in list(self._live_edges.items()):
            if v is item:
                del self._live_edges[k]
        try:
            item.src.edges.remove(item)
        except ValueError:
            pass
        try:
            item.dst.edges.remove(item)
        except ValueError:
            pass
        self.scene.removeItem(item)

    def _remove_node_item(self, key: str):
        it = self.node_items.pop(key, None)
        if it is None:
            return
        for e in list(it.edges):
            self._remove_edge_item(e)
        self.scene.removeItem(it)

    def _clear_live_items(self):
        for key in list(self.node_items.keys()):
            self._remove_node_item(key)
        for item in list(self.edge_items):
            self._remove_edge_item(item)

    def _exit_overview(self):
        if self._overview_item is not None:
            self.scene.removeItem(self._overview_item)
            self._overview_item = None

    def _enter_overview(self):
        if self._overview_item is None:
            self._clear_live_items()
            self._overview_item = GraphOverviewItem(self)
            self.scene.addItem(self._overview_item)
        active = [n for n in self.data.nodes.values() if self._matches(n)]
        # 连线统一交给常驻的全局连线层：概览模式下它没有 live EdgeItem 可跳过，
        # 会把全部边按同一套贝塞尔曲线画出；不再有「超过 3000 条就不画」的限制。
        self._overview_item.set_content(active, [], False)
        self.edge_layer.update()

    def _sync_edges(self, active: set):
        """重建 live 连线：只保留两端都 live 且都 active 的边。"""
        live = set(self.node_items.keys())
        want = {id(e): e for e in self.data.edges
                if e.src in live and e.dst in live
                and e.src in active and e.dst in active}
        for eid, item in list(self._live_edges.items()):
            if eid not in want:
                self._remove_edge_item(item)
        self.scene.blockSignals(True)
        for eid, e in want.items():
            if eid in self._live_edges:
                continue
            # 与全局连线层共用同一张「车道」表，切换渲染路径时曲率不跳变
            lane, total = self._edge_lanes.get(eid, (0, 1))
            item = EdgeItem(self.node_items[e.src], self.node_items[e.dst],
                            e.kind, lane, total)
            self.scene.addItem(item)
            self._live_edges[eid] = item
            self.edge_items.append(item)
        self.scene.blockSignals(False)

    def _sync_visible(self):
        """按当前缩放/视口重建 scene 内容：概览 or 仅视口内的细节节点。

        核心优化点：scene 里永远只放「视口 + 一圈外」的节点与连线，而不是整张图
        几千个对象。打开超大无分组地图时首屏即概览（1 个 item），缩放进去才按需
        建少量可交互节点，从根本上消除卡顿。
        """
        if self._loading or self.doc is None or self.view is None:
            return
        active = self._active_keys()
        self._active_cache = set(active)
        # 同步动态平移范围（内容框 + 四周各一视口），缩放/节点移动后重算
        self._ensure_scene_rect()
        try:
            vp = self.view.viewport()
            visible_mode = (self.isVisible() and vp is not None
                            and vp.width() > 0 and vp.height() > 0)
            if not visible_mode:
                # 面板未显示（单元测试 offscreen / 后台重建）：直接构建全部节点，不做
                # 虚拟化、也不进概览，保证数据可被断言与复用（与旧行为一致）。
                self._exit_overview()
                self._rebuild_live(set(active), active, None)
                self._update_status()
                return
            if self._use_overview():
                self._enter_overview()
                self._update_status()
                return
            self._exit_overview()

            tl = self.view.mapToScene(0, 0)
            br = self.view.mapToScene(vp.width(), vp.height())
            rect = QRectF(tl, br).adjusted(-VIRT_MARGIN, -VIRT_MARGIN,
                                           VIRT_MARGIN, VIRT_MARGIN)
            needed = {k for k, n in self.data.nodes.items()
                      if k in active
                      and QRectF(n.x, n.y, NODE_W, NODE_H).intersects(rect)}
            # 正在拖动的节点绝不回收：否则鼠标拖出视口时 QGraphicsObject 被删掉，
            # 拖动会突然中断，看起来就是「移动范围有限制」。
            if self._dragging_key and self._dragging_key in active:
                needed.add(self._dragging_key)
            self._rebuild_live(needed, active, rect)
            self._update_status()
        finally:
            # active 集合 / live 边集合变了，全局连线层重画补位
            self.edge_layer.update()

    def _rebuild_live(self, needed: set, active: set, _rect):
        """按 needed 集合增删 live 节点/连线（needed=None 表示全量构建）。"""
        for key in list(self.node_items.keys()):
            if key not in needed and key != self._dragging_key:
                self._remove_node_item(key)
        # 关键：NodeItem 构造里 setPos 会触发 ItemPositionHasChanged →
        # on_node_moved → remember_position。这是「按数据建节点」，不是用户拖拽，
        # 必须屏蔽，否则自动布局坐标会被误记成手动摆位；下次 refresh 就会跳过
        # _auto_positions，所有节点堆在 (0,0)，视口裁剪与全部节点相交 → 虚拟化失效。
        prev_loading = self._loading
        self._loading = True
        self.scene.blockSignals(True)
        try:
            # 必须按 data.nodes 的文档顺序插入，不能遍历 set：Python 字符串 hash
            # 每个进程随机，set 迭代顺序不稳定，会让 node_items 的首项漂移。
            for key in (k for k in self.data.nodes if k in needed):
                if key not in self.node_items:
                    it = NodeItem(self.data.nodes[key], self.scene)
                    self.scene.addItem(it)
                    self.node_items[key] = it
                    if key in self._selected_keys:
                        it.setSelected(True)
            # 滚动时先删后增也会打乱 dict 顺序，统一重排为文档顺序，保证
            # node_items 的首项恒为文档第一个对象。
            ordered = {k: self.node_items[k] for k in self.data.nodes
                       if k in self.node_items}
            self.node_items.clear()
            self.node_items.update(ordered)
            self._sync_edges(active)
        finally:
            self.scene.blockSignals(False)
            self._loading = prev_loading

    def refresh(self, fit: bool = True):
        """重新从文档读取对象并重建节点图（不修改文档）。

        不再一次性把全部节点/连线建进 scene（几千个 QGraphicsObject 会卡），而是
        先算好数据，再交给 ``_sync_visible`` 按当前缩放/视口**只建视口内**的对象，
        或切到「概览模式」单 item 画全图——打开超大无分组地图也不卡。
        """
        if self._suppress_fit_once:
            # 主窗口 on_change 会 refresh(fit=True)；连线编辑后不要重新居中画面
            self._suppress_fit_once = False
            fit = False
        self._loading = True
        self.scene.blockSignals(True)
        self._clear_live_items()
        self._exit_overview()
        # 下拉里没有「全部对象层」了：过滤值必须是真实存在的层名，
        # 否则 collect_graph 会悄悄退回全层显示，与下拉显示不一致。
        names = self._object_layer_names()
        if self._layer_filter not in names:
            self._layer_filter = self._default_layer_name(self.doc)
        self.data = collect_graph(self.doc, self._layer_filter)
        self._dirty = False
        # 空组一旦真的有对象写进属性，就不再是「会话内空组」
        self._empty_groups -= set(self.current_groups())
        # 同起终点的多条关系按「车道」错开曲率；EdgeItem 与全局连线层共用此表
        groups: dict[tuple[str, str], list] = {}
        for e in self.data.edges:
            groups.setdefault((e.src, e.dst), []).append(e)
        self._edge_lanes = {}
        for arr in groups.values():
            for i, e in enumerate(arr):
                self._edge_lanes[id(e)] = (i, len(arr))

        # 位置稳定：先按 key 恢复全部已知坐标（手动摆位 + 上一次自动布局结果），
        # 只给「新出现」的节点找空位。以前只要没人手摆过就每次 refresh 重跑
        # 拓扑分层，连一条线 / 改个属性都会让整张图的节点跳到新位置。
        # 首次打开文档（缓存为空）：用对象里保存的相对布局作种子，避免每次打开
        # 都重跑自动布局、把用户上次摆好的位置打乱。会话内手动摆位优先级更高。
        if not self._pos_cache and not self._manual_pos:
            for key, pos in self._saved_positions().items():
                self._pos_cache[key] = pos
        for key, node in self.data.nodes.items():
            pos = self._manual_pos.get(key)
            if pos is None:
                pos = self._pos_cache.get(key)
            if pos is not None:
                node.x, node.y = pos.x(), pos.y()
        unknown = {k for k in self.data.nodes if k not in self._pos_cache}
        if len(unknown) == len(self.data.nodes):
            # 全新文档 / 切到从没显示过的对象层：整体拓扑分层
            self._auto_positions()
        elif unknown:
            # 已有节点一个都不动，新节点排在右侧空列
            self._auto_positions(only=unknown)
        for key, node in self.data.nodes.items():
            self._pos_cache[key] = QPointF(node.x, node.y)

        self.edge_layer.set_content()
        self.scene.blockSignals(False)
        self._loading = False
        # 只建视口内（或概览）的对象，其余按需增删
        self._sync_visible()
        self._refresh_groups()
        self._refresh_layer_combo()
        if fit and self.isVisible() and self.view.width() > 40:
            self._pending_fit = False
            self.fit_view()
        else:
            self._pending_fit = True
        self._update_status()
        # 文档对象可能已被 ObjectCommand 用快照整体替换（画布编辑 / 撤销 / 重做 /
        # 外部写文件），右侧面板持有的 obj 引用会指向旧对象、连线列表也是旧快照。
        # 按稳定身份重新绑定（重复 object_id 时不能靠 current_object_id 区分）。
        if self._detail_uid is not None or self._detail_oid is not None:
            self._rebind_inspector(self._detail_uid, self._detail_oid)

    def _auto_positions(self, only=None):
        """按连线做拓扑分层；同层过多时自动网格铺开，避免挤成一条竖线。

        ``only`` 不为 None 时只给这些 key 分配位置，其余节点坐标一律保持不变
        （编辑文档后新增的对象用，避免打乱用户已经摆好的整张图）。
        """
        keys = list(self.data.nodes)
        if not keys:
            return
        if only is not None:
            targets = [k for k in keys if k in only]
            if not targets:
                return
            existing = [n for k, n in self.data.nodes.items() if k not in only]
            x_base = max((n.x + NODE_W for n in existing), default=0.0) + 90.0
            y_base = min((n.y for n in existing), default=60.0)
            for i, key in enumerate(targets):
                col, row = divmod(i, 15)
                n = self.data.nodes[key]
                n.x = x_base + col * (NODE_W + 48)
                n.y = y_base + row * (NODE_H + 30)
            return
        adj = {k: [] for k in keys}
        indeg = {k: 0 for k in keys}
        for e in self.data.edges:
            if e.src in adj and e.dst in indeg:
                adj[e.src].append(e.dst)
                indeg[e.dst] += 1

        remaining = set(keys)
        layers: list[list[str]] = []
        while remaining:
            ready = sorted(k for k in remaining if indeg[k] == 0)
            if not ready:            # 有环：把剩余节点单独放一层
                ready = sorted(remaining)
            layers.append(ready)
            for k in ready:
                remaining.discard(k)
            for k in ready:
                for nxt in adj[k]:
                    if nxt in remaining:
                        indeg[nxt] -= 1

        # 节点多时按接近画布比例的网格铺开（宽:高 ≈ 1.4），避免挤成一条竖线
        rows = 15
        col_gap = NODE_W + 48
        row_gap = NODE_H + 30
        x_base = 60.0
        for group in layers:
            cols = max(1, math.ceil(len(group) / rows))
            for i, key in enumerate(group):
                col, row = divmod(i, rows)
                self.data.nodes[key].x = x_base + col * col_gap
                self.data.nodes[key].y = 60.0 + row * row_gap
            x_base += cols * col_gap + 90

    def auto_layout(self):
        self._manual_pos.clear()
        self._auto_positions()
        # 主动「自动布局」的结果也要写进位置缓存，否则下一次 refresh 会把
        # 节点恢复回整理之前的坐标，看起来像按钮没生效。
        for key, node in self.data.nodes.items():
            self._pos_cache[key] = QPointF(node.x, node.y)
        self._loading = True
        for key, node in self.data.nodes.items():
            item = self.node_items.get(key)
            if item is not None:
                item.setPos(node.x, node.y)
        self._loading = False
        for edge in self.edge_items:
            edge.rebuild()
        self.fit_view()

    def fit_view(self):
        # 用数据层所有 active 节点算包围盒（而不是仅 live 的视口内节点），
        # 否则虚拟化下只_fit 了当前可见的一小块。
        active = [n for n in self.data.nodes.values() if self._matches(n)]
        if not active:
            active = list(self.data.nodes.values())
        if not active:
            return
        rect = QRectF()
        for n in active:
            rect |= QRectF(n.x, n.y, NODE_W, NODE_H)
        rect = rect.adjusted(-50, -50, 50, 50)
        # fit_view 只负责把视口对准内容，不在这里 setSceneRect：平移范围由
        # _ensure_scene_rect 按「内容框 + 四周各一视口」动态维护（这里 emit 的
        # zoomChanged 会经 _schedule_sync → _sync_visible 触发重算），节点移动
        # 时范围随之扩展/收缩，视口被 clamp 到新合法位置。
        self.view.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.view.fitInView(rect, Qt.KeepAspectRatio)
        # 节点特别多时不要缩到看不清；用户可以滚轮缩小看全貌
        z = self.view.zoom()
        if z > 1.25:
            self.view.scale(1.25 / z, 1.25 / z)
        elif z < 0.32:
            self.view.scale(0.32 / z, 0.32 / z)
        self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        # 缩放变了 → 重新评估 LOD（概览/细节切换）+ 重建视口内对象
        self.view.zoomChanged.emit(self.view.zoom())
        self._update_status()

    def _zoom_by(self, factor: float):
        self.view.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.view.scale(factor, factor)
        self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.view.zoomChanged.emit(self.view.zoom())
        self._update_status()

    # ---------------- 过滤 / 详情 ----------------
    def _matches(self, node: GraphNode) -> bool:
        # 对象类型过滤已按需求移除：节点图只按分组页 + 搜索框筛选。
        # 分组页：'ALL' 全显示（兼容外部调用）；'' 只显示未分组的；组名只显示该组
        page = self._group_page
        if page == "":
            if node.group != "":
                return False
        elif page != "ALL":
            if node.group != page:
                return False
        needle = self.search_edit.text().strip().lower()
        if not needle:
            return True
        hay = [node.obj.label, node.obj.type_, node.tid, node.layer_name,
               node.obj.shape_label]
        hay += [f"{k}={v}" for k, v in node.obj.properties.items()
                if k not in HIDDEN_EDITOR_PROPERTIES]
        return any(needle in str(s).lower() for s in hay)

    def _apply_filter(self, *_args):
        # 过滤/分组/搜索改变 → 只重建「视口内 + 概览」的对象，而非全部
        self._sync_visible()
        self._update_status()

    def _on_selection_changed(self):
        if self._loading:
            return
        items = [i for i in self.scene.selectedItems()
                 if isinstance(i, NodeItem)]
        if not items:
            self._selected_keys = set()
            self._clear_detail()
            return
        # 选中一个节点时，自动把同组（同一 rwmap_group 名）的节点一起选中
        groups = {i.node.group for i in items if i.node.group}
        if groups:
            extras = [it for it in self.node_items.values()
                      if it.node.group in groups and it not in items]
            if extras:
                self._loading = True
                self.scene.blockSignals(True)
                for it in extras:
                    it.setSelected(True)
                self.scene.blockSignals(False)
                self._loading = False
                items = [i for i in self.scene.selectedItems()
                         if isinstance(i, NodeItem)]
        # 数据层选中态：跨虚拟化存活（节点滚出视口被回收后，滚回仍保持选中高亮）
        self._selected_keys = {i.node.key for i in items}
        self._show_detail(items[0].node)
        self.objectSelected.emit(items[0].node.obj)

    # ---------------- 分组（编辑器私有属性 rwmap_group） ----------------
    def _color_for_group(self, name: str) -> QColor:
        """同一组名恒定映射到同一种颜色（HSL 取哈希，色相分散）。"""
        hue = abs(hash(name)) % 360
        return QColor.fromHsl(hue, 150, 175)

    def current_groups(self) -> list[str]:
        """文档里真正出现过的所有组名（升序，跨全部对象层）；不含空（未分组）。

        组名是从对象的私有属性反推的：没写进任何对象的「空组」不会被保存，
        这是「新建组时没选对象也能建、但保存后不保留」的根本原因。
        """
        if self.doc is None:
            return []
        groups = set()
        for layer in self.doc.layers:
            if not isinstance(layer, ObjectLayer):
                continue
            for obj in layer.objects:
                g = obj.properties.get(EDITOR_GROUP_PROPERTY) if obj.properties else None
                if g:
                    groups.add(str(g))
        return sorted(groups)

    def empty_groups(self) -> set[str]:
        """本次会话里新建、但还没有任何对象的组（外部「加入组」菜单可列出来）。"""
        return set(self._empty_groups)

    def available_groups(self) -> list[str]:
        """下拉里能选的组页：文档里已有的组 + 会话内新建的空组。"""
        return sorted(set(self.current_groups()) | set(self._empty_groups))

    def _refresh_groups(self):
        """重建分组页下拉：选择已有组（默认）/ 各命名组。

        「全部」页按需求移除；只有外部代码显式 set_group_page("ALL") 时才
        临时补一档，保持旧脚本 / 探针可用，默认界面里不会出现。
        """
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        if self._group_page == "ALL":
            self.group_combo.addItem("全部", "ALL")
        self.group_combo.addItem("选择已有组", "")
        for g in self.available_groups():
            self.group_combo.addItem(g, g)
        idx = self.group_combo.findData(self._group_page)
        if idx < 0:
            idx = 0
            self._group_page = self.group_combo.itemData(0) or ""
        self.group_combo.setCurrentIndex(idx)
        self.group_combo.blockSignals(False)

    def _on_group_page_changed(self, *_args):
        """切换分组页：只显示该组的节点，并适应视图。"""
        data = self.group_combo.currentData()
        self._group_page = data if data is not None else "ALL"
        self._apply_filter()
        if self.isVisible() and self.view.width() > 40:
            QTimer.singleShot(0, self.fit_view)
        self._update_status()

    # ---------------- 对象层切换 ----------------
    def _refresh_layer_combo(self):
        """重建对象层下拉：每个真实对象层一档（没有「全部对象层」），默认 Triggers。"""
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for name in self._object_layer_names():
            self.layer_combo.addItem(name, name)
        idx = self.layer_combo.findData(self._layer_filter)
        if idx < 0:
            idx = 0 if self.layer_combo.count() else -1
        if idx >= 0:
            self.layer_combo.setCurrentIndex(idx)
            self._layer_filter = self.layer_combo.itemData(idx)
        self.layer_combo.blockSignals(False)

    def _on_layer_changed(self, *_args):
        """切换对象层：保留手摆位置（按 层名::object_id 区分），只重新收集该层节点。"""
        self._layer_filter = self.layer_combo.currentData()
        self.refresh(fit=True)

    def set_group_page(self, name: str):
        """外部（如画布「组合」）切到指定组页：'' 选择已有组 / 组名 / 'ALL' 全部。"""
        self._group_page = name if name is not None else ""
        self._refresh_groups()
        self._apply_filter()
        if self.isVisible() and self.view.width() > 40:
            QTimer.singleShot(0, self.fit_view)
        self._update_status()

    def _toggle_color_by_group(self, on: bool):
        self._color_by_group = on
        for item in self.node_items.values():
            item.update()
        if self._overview_item is not None:
            self._overview_item.update()
        self._update_status()

    def _selected_nodes(self):
        return [i for i in self.scene.selectedItems()
                if isinstance(i, NodeItem)]

    def _push_object_edit(self, before, text, message: str | None = None) -> bool:
        """把属性改动（分组 / 连线）登记成一条撤销命令；跨层也只算一步。

        ``before`` 由 :func:`capture` 在改动**之前**拍好。命令 push 后的 redo
        会走 ``_after_object_change``，把画布、节点图与右侧面板一起刷新；
        这里压掉一次性 fit，避免节点图跳回居中。
        """
        self._suppress_fit_once = True
        try:
            pushed = push_object_edit(self.doc, self.undo_stack, before, text,
                                      self._after_object_change)
        finally:
            self._suppress_fit_once = False
        if not pushed:
            self._after_object_change()   # 没有实际变化也要刷新连线列表 / 状态
        if message:
            self.status.setText(message)
        return pushed

    def _set_group_on_nodes(self, items, name: str):
        """把选中节点对应的对象都标上同一分组名（EDITOR_GROUP_PROPERTY）。

        改动登记成一条撤销命令（与画布右键「组合」同一套命令），
        完成后切到该组页，方便立刻看到分组结果。
        """
        if not items or not name:
            return
        objs = []
        before = capture([self._layer_of(item.node) for item in items])
        for item in items:
            item.node.obj.properties.set(EDITOR_GROUP_PROPERTY, name, "string")
            objs.append(item.node.obj)
        self._push_object_edit(
            before, f"组合 {len(objs)} 个对象",
            f"已将 {len(objs)} 个对象分到「{name}」（Ctrl+Z 可撤销）")
        self.set_group_page(name)
        self.select_objects(objs)

    def create_link(self, src: GraphNode, dst: GraphNode):
        """从源节点（out 端口）拖到目标节点（in 端口）：把目标触发器 id 写进源的
        `alsoActivate`，节点图里即出现一条绿色「激活」连线（与属性反推渲染一致）。

        这是写入文档的属性编辑（铁锈战争可识别 alsoActivate），走撤销命令。

        关键：铁锈战争按触发器的**自定义 id 属性**（不是 Tiled 的 object id）来
        反查 alsoActivate 指向的目标。所以写之前必须保证源 / 目标都有自定义 id——
        缺失时分配一个**全图唯一**的 id（优先 object_id，重复时用 rwmap_auto_N），
        否则游戏内这条连线不会真正触发。
        """
        if src is dst:
            return
        raw = src.obj.properties.get("alsoActivate") or ""
        tid = dst.obj.properties.get("id")
        if tid and tid in _parse_ids(raw):
            self.status.setText(f"连线已存在：{src.obj.label} 激活 {dst.obj.label}")
            return
        before = capture([self._layer_of(src), self._layer_of(dst)])
        # 保证两端都有可被引用的自定义 id。铁锈地图的 Tiled object_id 大量重复，
        # 不能直接拿它当 id：否则几十个触发器会拿到同一个 id，游戏内连线指向错乱。
        used = used_trigger_ids(self.doc)
        for node in (src, dst):
            if not node.obj.properties.get("id"):
                node.obj.properties.set("id", allocate_trigger_id(node.obj, used))
        tid = dst.obj.properties.get("id")
        if not tid:
            self.status.setText("目标节点没有可用 id，无法建立连线")
            return
        raw = src.obj.properties.get("alsoActivate") or ""
        ids = _parse_ids(raw)
        if tid in ids:
            self.status.setText(f"连线已存在：{src.obj.label} 激活 {dst.obj.label}")
            return
        ids.append(tid)
        src.obj.properties.set("alsoActivate", ", ".join(ids), "string")
        self._push_object_edit(
            before, f"建立连线 {src.obj.label} → {dst.obj.label}",
            f"已建立连线：{src.obj.label} 激活 {dst.obj.label}（Ctrl+Z 可撤销）")

    # ---------------- 连线：删除 / 改线型（右侧连线列表的数据接口）----
    def edge_context_menu(self, e: GraphEdge) -> QMenu:
        """右键连线弹出的线型菜单（纯构造，便于测试，不自动 exec）。"""
        menu = QMenu(self)
        menu.setTitle("连线类型")
        for kind, (label, _col, _dir) in EDGE_SPECS.items():
            if kind == "whenActivatedlds":   # 只作旧地图错拼兼容，不主动提供
                continue
            act = menu.addAction(f"{label}（{kind}）")
            act.setData(kind)
            if kind == e.kind:
                act.setCheckable(True)
                act.setChecked(True)
                act.setEnabled(False)
            act.triggered.connect(
                lambda _checked=False, k=kind, edge=e:
                self.change_edge_type(edge, k))
        menu.addSeparator()
        act_del = menu.addAction("删除这条连线")
        act_del.triggered.connect(
            lambda _checked=False, edge=e: self.delete_edge(edge))
        return menu

    @staticmethod
    def _write_ref(node: GraphNode, prop: str, value: str):
        """写 / 删一个引用属性；空值必须整键删掉，别留一个空 <property>。"""
        bag = node.obj.properties
        if value.strip():
            bag.set(prop, value, "string")
        else:
            bag.values.pop(prop, None)
            bag.types.pop(prop, None)
            bag.text_props.discard(prop)

    def delete_edge(self, e: GraphEdge):
        """点线中点的叉：把这条引用从持有它的对象属性里删掉。

        forward 类（alsoActivate 等）属性在 src 上、引用 dst；reverse 类
        （activatedBy / deactivatedBy 等）属性在 dst 上、引用 src。
        """
        if e is None or e.kind not in EDGE_SPECS:
            return
        forward = EDGE_SPECS[e.kind][2] == "forward"
        owner = self.data.nodes.get(e.src if forward else e.dst)
        other = self.data.nodes.get(e.dst if forward else e.src)
        if owner is None or other is None:
            return
        raw = owner.obj.properties.get(e.kind) or ""
        new_raw, removed = _remove_ref_token(raw, other)
        if not removed:
            self.status.setText("这条连线没有可删除的引用（文档可能已被外部改动）")
            return
        before = capture([self._layer_of(owner)])
        self._write_ref(owner, e.kind, new_raw)
        self._push_object_edit(
            before, f"删除连线 {owner.obj.label} → {other.obj.label}",
            f"已删除连线：{owner.obj.label} → {other.obj.label}"
            f"（{e.kind}，Ctrl+Z 可撤销）")

    def change_edge_type(self, e: GraphEdge, new_kind: str):
        """改线型：把引用从旧属性搬到新属性。

        方向相同的种类只是换属性名（属性还在同一个对象上）；方向不同则
        「持有属性的对象」和「被引用的对象」互换，保持线的两端不变。
        """
        if (e is None or new_kind == e.kind
                or new_kind not in EDGE_SPECS):
            return
        old_forward = EDGE_SPECS[e.kind][2] == "forward"
        new_forward = EDGE_SPECS[new_kind][2] == "forward"
        old_owner = self.data.nodes.get(e.src if old_forward else e.dst)
        old_other = self.data.nodes.get(e.dst if old_forward else e.src)
        new_owner = self.data.nodes.get(e.src if new_forward else e.dst)
        new_other = self.data.nodes.get(e.dst if new_forward else e.src)
        if None in (old_owner, old_other, new_owner, new_other):
            return
        raw = old_owner.obj.properties.get(e.kind) or ""
        new_raw, removed = _remove_ref_token(raw, old_other)
        if not removed:
            self.status.setText("改线型失败：找不到旧连线对应的引用")
            return
        before = capture([self._layer_of(n) for n in
                          (old_owner, old_other, new_owner, new_other)])
        self._write_ref(old_owner, e.kind, new_raw)
        # 铁锈战争按自定义 id 引用触发器；两端都得先有全图唯一的 id
        used = used_trigger_ids(self.doc)
        for node in (new_owner, new_other):
            if not node.obj.properties.get("id"):
                node.obj.properties.set(
                    "id", allocate_trigger_id(node.obj, used), "string")
        ref = str(new_other.obj.properties.get("id")
                  or new_other.obj.object_id)
        cur = new_owner.obj.properties.get(new_kind) or ""
        if ref not in _parse_ids(cur):
            self._write_ref(new_owner, new_kind,
                            ", ".join(_split_refs(cur) + [ref]))
        self._push_object_edit(
            before, f"改线型 {EDGE_SPECS[new_kind][0]}",
            f"线型已改为「{EDGE_SPECS[new_kind][0]}」（{new_kind}，Ctrl+Z 可撤销）")

    def _new_group_dialog(self):
        """新建组：没选中对象也允许建（会话内空组）。

        组名是从对象私有属性反推的，空组没有任何对象承载它，所以保存 / 重开
        之后自然不会保留；用户随后把对象加进这个组，它才会真正写进地图。
        """
        items = self._selected_nodes()
        default = f"组{len(self.available_groups()) + 1}"
        name, ok = QInputDialog.getText(
            self, "新建组",
            "组名（同名对象会被分到同一组，铁锈战争不读取此属性）：\n"
            "没选中对象也可以先建一个空组，之后再把对象加进来。",
            text=default)
        if not ok or not name.strip():
            return
        name = name.strip()
        if not items:
            self._empty_groups.add(name)
            self._refresh_groups()
            self.set_group_page(name)
            self.status.setText(
                f"已新建空组「{name}」：本次会话可用；没存任何对象，"
                "保存后不会保留（组名靠对象属性反推）")
            return
        self._set_group_on_nodes(items, name)
        self.status.setText(
            f"已新建组「{name}」，包含 {len(items)} 个对象（Ctrl+Z 可撤销）")

    def _join_selected_to_group(self, name: str):
        """把选中节点加入指定组；name='' 表示回到「选择已有组」（取消私有分组）。"""
        items = self._selected_nodes()
        if not items:
            self.status.setText("请先选中若干节点")
            return
        before = capture([self._layer_of(item.node) for item in items])
        if name == "":
            for item in items:
                obj = item.node.obj
                obj.properties.values.pop(EDITOR_GROUP_PROPERTY, None)
                obj.properties.types.pop(EDITOR_GROUP_PROPERTY, None)
            label = "选择已有组"
        else:
            for item in items:
                item.node.obj.properties.set(EDITOR_GROUP_PROPERTY, name, "string")
            label = name
        pushed = self._push_object_edit(
            before, f"加入组「{label}」",
            f"已将 {len(items)} 个对象加入「{label}」（Ctrl+Z 可撤销）")
        self.set_group_page(name)
        self.select_objects([i.node.obj for i in items])
        if not pushed:
            self.status.setText(f"已将 {len(items)} 个对象加入「{label}」（无变化）")

    # ---------------- 属性面板接线 ----------------
    def _layer_of(self, node: GraphNode):
        """按层名 / 对象身份找所属对象层（ObjectCommand 会替换 layer.objects）。"""
        layers = self._object_layers()
        for layer in layers:
            if layer.name == node.layer_name:
                return layer
        uid = object_uid(node.obj)
        for layer in layers:
            if any(object_uid(o) == uid for o in layer.objects):
                return layer
        oid = getattr(node.obj, "object_id", None)
        if oid is not None:
            for layer in layers:
                if any(o.object_id == oid for o in layer.objects):
                    return layer
        return None

    def _edges_for(self, node: GraphNode):
        """右侧连线列表的数据：(方向, 中文线型, 对方名字, 颜色, GraphEdge)。

        第 5 项把原始数据边传给属性面板，叉 / 右键菜单才知道要改哪一条引用。
        """
        out = []
        for e in self.data.edges:
            label, color, _direction = EDGE_SPECS[e.kind]
            if e.src == node.key:
                other = self.data.nodes.get(e.dst)
                out.append(("out", label,
                            other.obj.label if other else "?", color, e))
            elif e.dst == node.key:
                other = self.data.nodes.get(e.src)
                out.append(("in", label,
                            other.obj.label if other else "?", color, e))
        return out

    def _show_edge_menu(self, edge, global_pos):
        """右侧连线行右键：弹出线型菜单（画布右键仍只用于框选，不弹窗）。"""
        if edge is None:
            return
        # popup 非阻塞：不套一层模态事件循环，面板还能继续响应画布操作
        menu = self.edge_context_menu(edge)
        menu.setAttribute(Qt.WA_DeleteOnClose, True)
        menu.popup(global_pos)
        return menu

    def _show_detail(self, node: GraphNode):
        layer = self._layer_of(node)
        if layer is None:
            # 对象已不在文档里：清空，别把上一个节点的属性留在面板上
            self._clear_detail()
            return
        # 记住「当前详情是哪个对象」：refresh / 撤销后按 uid 精确重绑。
        self._detail_uid = object_uid(node.obj)
        self._detail_oid = getattr(node.obj, "object_id", None)
        self.inspector.set_context(self.doc, layer, self.undo_stack,
                                   self._after_object_change)
        self.inspector.show_node(node, self._edges_for(node), layer)

    def _activate_detail(self, node: GraphNode):
        item = self.node_items.get(node.key)
        self._loading = True
        if item is not None:
            self.scene.clearSelection()
            item.setSelected(True)
        self._loading = False
        self._show_detail(node)

    def _after_object_change(self):
        """ObjectCommand redo / undo 后刷新整图，并按稳定身份重新绑定属性面板。"""
        uid, oid = self._detail_uid, self._detail_oid
        if self.on_change is not None:
            self.on_change()          # 主窗口 _refresh_objects → 也会 self.refresh()
        else:
            self.refresh(fit=False)
        self._rebind_inspector(uid, oid)

    def _rebind_inspector(self, uid=None, oid=None):
        """按 uid 优先、object_id 兜底，把详情重绑到文档里的当前对象实例。"""
        if uid is None and oid is None:
            return
        if uid is not None:
            for node in self.data.nodes.values():
                if object_uid(node.obj) == uid:
                    self._activate_detail(node)
                    return
        if oid is None:
            oid = self.inspector.current_object_id()
        if oid is not None:
            for node in self.data.nodes.values():
                if node.obj.object_id == oid:
                    self._activate_detail(node)
                    return
        # 对象不在当前显示的对象层（切层 / 分组页 / 被过滤掉）：仍按文档里的
        # 当前对象重建详情，避免右侧留着已被快照替换掉的旧 obj 数据。
        obj, layer = self._find_object(oid)
        if obj is not None:
            self._show_detail(self._node_for_object(obj, layer.name))
        else:
            self._clear_detail()

    def _delete_object(self, obj):
        """右侧「删除此节点」：只从对象层移除**这一个**对象，走撤销栈。

        铁锈地图里 Tiled object_id 大量重复，旧代码用
        ``[o for o in ... if o.object_id != obj.object_id]`` 会一次删掉整层
        同 id 的对象（实测该地图会误删 149 个）。这里按稳定身份定位到具体下标，
        只删一个；找不到身份时才退化为「删第一个同 object_id 的对象」。
        """
        if self.doc is None:
            return
        from ..commands.object_command import ObjectCommand, snapshot

        layers = [l for l in self.doc.layers if isinstance(l, ObjectLayer)]
        uid = object_uid(obj)
        target = None                       # (layer, index)
        for layer in layers:
            for i, cur in enumerate(layer.objects):
                if object_uid(cur) == uid:
                    target = (layer, i)
                    break
            if target is not None:
                break
        if target is None:                  # xml_element 为空的旧引用兜底
            oid = getattr(obj, "object_id", None)
            for layer in layers:
                for i, cur in enumerate(layer.objects):
                    if cur.object_id == oid:
                        target = (layer, i)
                        break
                if target is not None:
                    break
        if target is None:
            return
        layer, idx = target
        before = snapshot(layer)
        layer.objects = [o for i, o in enumerate(layer.objects) if i != idx]
        after = snapshot(layer)
        layer.dirty = True
        self.doc.dirty = True
        cmd = ObjectCommand(self.doc, layer, before, after,
                            "删除触发器对象", self._after_object_change)
        if self.undo_stack is not None:
            self.undo_stack.push(cmd)
        else:
            cmd.redo()
        self._clear_detail(reset=True)

    def _update_status(self, *_args, visible_count: int | None = None):
        # 「对象数量」必须报全量数据节点数。node_items 只是视口虚拟化后已建
        # 出来的 live 节点，随缩放 / 平移变化；旧文案把它当总数显示，导致用户
        # 看到的数字随画面飘，且「节点 N」与「显示 N」恒等、毫无参考意义。
        total = len(self.data.nodes)
        vis = len(self._active_keys()) if visible_count is None else visible_count
        live = len(self.node_items)
        edges = len(self.data.edges)
        dangling = f" · 悬空引用 {self.data.dangling}" if self.data.dangling else ""
        layers = "、".join(self.data.layer_names) or "无对象层"
        zoom = self.view.zoom() * 100
        if self._overview_item is not None:
            self.status.setText(
                f"概览模式 · 全量节点 {total} · 当前显示 {vis} · 连线 {edges}"
                f" · 缩放 {zoom:.0f}%{dangling} · 双击放大进入细节可编辑")
            return
        self.status.setText(
            f"对象层：{layers} · 全量节点 {total} · 当前显示 {vis}（已建 {live}）"
            f" · 连线 {edges} · 缩放 {zoom:.0f}%{dangling} · 右侧属性可编辑")
