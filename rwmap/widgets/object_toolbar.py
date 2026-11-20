"""Tiled 风格「插入对象」工具栏：自绘蓝色图标 + 自适应网格布局。

工具 id 与 MapView.object_mode / MainWindow._object_tool 共用：
``brush`` 表示回到瓦片画笔，其余都是对象工具。

第三十四轮起「框选（M）」与「选择对象（S）」合并成同一个 ``select`` 工具：
图标用鼠标箭头，瓦片层拖空白框选瓦片、对象层拖空白框选对象，
点对象则选中 / 拖动 / 缩放。旧 id ``tileselect`` 仅作为兼容别名保留。

布局要点：按钮不再交给 ``QToolBar`` 一维堆叠，而是放进 :class:`FlowHost`
的流式布局里。横排（默认在窗口顶部）时 15 个图标铺成一行；被拖到左/右侧
变成竖排时，按面板宽度自动换行——能塞几个塞几个，不再是一个按钮占一整行。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QBrush, QColor, QCursor, QFont, QIcon, QKeySequence,
    QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import QLabel, QToolBar, QToolButton

from ..document.layer import ObjectLayer
from ..theme import ASH, INK, TEXT, TEXT_DIM
from .flow_layout import FlowHost

# (id, 名称, 快捷键, 说明)
TOOLS = (
    ("brush", "瓦片画笔", "B", "图章：在瓦片层绘制（B）"),
    ("pen", "钢笔", "A", "贝塞尔矢量曲线：右键加点 / 拖出控制柄，左键调点、点空白完成（A）"),
    ("eraser", "橡皮", "E", "擦除瓦片（E）"),
    ("fill", "填充", "F", "洪水填充：把点击格所在的同色区域替换为当前笔刷（F）"),
    # 合并工具：一个鼠标箭头同时负责「点选 / 拖动 / 缩放对象」与「拖空白框选」。
    ("select", "选择 / 框选", "S",
     "选择 / 框选：点对象选中并拖动、拖手柄缩放；瓦片层拖空白框选瓦片，"
     "对象层拖空白框选多个对象（S / M）"),
    ("nodes", "编辑节点", "N", "拖动多边形或折线的顶点"),
    ("rect", "插入矩形", "R", "拖出一个矩形对象"),
    ("point", "插入点", "P", "单击放置一个点对象"),
    ("ellipse", "插入椭圆", "O", "拖出一个椭圆对象"),
    ("polygon", "插入多边形", "G", "逐点单击，双击或回车结束"),
    ("polyline", "插入折线", "L", "逐点单击，双击或回车结束"),
    ("tile", "插入图块", "T", "用当前笔刷 gid 放一个图块对象"),
    ("template", "插入模板", "Shift+T", "选一个 Tiled .tx 模板文件放成对象"),
    ("image", "插入图片", "Shift+I", "选一张本地图片；保存时切成 20px 瓦片内嵌进地图，游戏内可见"),
    ("text", "插入文字", "X", "拖出文字框输入内容；保存时转成 mapText 触发器，游戏内显示"),
)

# 一个工具可以挂多个快捷键：合并后 S / M 都切到同一个「选择 / 框选」。
TOOL_EXTRA_SHORTCUTS = {
    "select": ("M",),
}

# 旧工具 id → 合并后的工具 id。旧测试 / 探针里还会写 _set_object_tool("tileselect")，
# 统一在这里归一，避免两套状态机并存。
LEGACY_TOOL_ALIASES = {"tileselect": "select"}

# 这些工具只能在对象层使用；当前层不是对象层时整组置灰。
# 与截图对应：编辑节点 → 插入文字这一整排（不含瓦片画笔 / 钢笔 / 橡皮 / 填充 /
# 选择，它们在瓦片层同样要用）。4 个变换键由 MainWindow 另行同步。
OBJECT_ONLY_TOOLS = (
    "nodes", "rect", "point", "ellipse", "polygon", "polyline",
    "tile", "template", "image", "text",
)


def canonical_tool_id(tid: str) -> str:
    return LEGACY_TOOL_ALIASES.get(tid, tid)


# 图标内部图形的蓝色描边（不是按钮外框）：亮一些，深色工具栏上也看得清。
_ICON_BLUE = QColor(80, 170, 245)


def _polygon(points):
    return QPolygonF([QPointF(x, y) for x, y in points])


def make_object_icon(kind: str) -> QIcon:
    """画一枚 28x28 的图标，形态对齐 Tiled 对象工具栏。

    蓝色描边画在**图标里的图形**上（箭头、T、矩形轮廓…），
    按钮外面那个方块不加任何边框。

    地图变换 4 按钮（翻转 / 旋转）按本软件统一线条风格绘制
    （亮蓝描边 + 淡蓝填充 + 绿色箭头，见 transform_icons）：轮廓参考 Tiled，
    但不是抠图位图。
    """
    if kind in ("flip_h", "flip_v", "rot_cw", "rot_ccw"):
        from .transform_icons import transform_icon
        return transform_icon(kind)
    pix = QPixmap(28, 28)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    # 所有图标统一：亮蓝描边 + 淡蓝填充，深色工具栏上也能一眼认出。
    p.setPen(QPen(_ICON_BLUE, 1.9))
    p.setBrush(QColor(90, 175, 245, 55))

    if kind == "brush":
        # 图章 / 盖章：手柄在上、方底座在下，底座里画一格瓦片表示"盖下去"。
        p.setBrush(QColor(90, 175, 245, 55))
        p.drawRoundedRect(4, 16, 20, 9, 2, 2)        # 方底座
        p.setPen(QPen(_ICON_BLUE, 3.2))
        p.drawLine(14, 16, 14, 8)                    # 立柱
        p.setPen(QPen(_ICON_BLUE, 1.9))
        p.setBrush(QColor(90, 175, 245, 130))
        p.drawEllipse(QPointF(14, 6), 4.5, 4.0)     # 顶部把手
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_ICON_BLUE, 1.3))
        p.drawRect(9, 18.5, 10, 4.5)                 # 底座里的瓦片格
    elif kind == "pen":
        # 钢笔尖 + 一条曲线：亮蓝描边，和其余工具图标同一套视觉语言。
        p.setPen(QPen(_ICON_BLUE, 1.9))
        p.setBrush(QColor(90, 175, 245, 55))
        p.drawPolygon(_polygon([(5, 22), (8, 13), (18, 4),
                                (23, 9), (14, 19)]))
        p.setPen(QPen(_ICON_BLUE, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawLine(8, 13, 18, 4)                     # 笔尖中缝
        p.setBrush(_ICON_BLUE)
        p.drawEllipse(QPointF(5, 22), 2.6, 2.6)      # 笔尖落点
        p.setPen(QPen(_ICON_BLUE, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(_polygon([(3, 26), (9, 24), (15, 25),
                                 (21, 21), (25, 22)]))  # 画出的曲线
    elif kind == "fill":
        # 斜放的油漆桶（**没有提手**）：桶口朝右下倾，颜料顺着桶口往下滴。
        # 桶身是上宽下窄的梯形，整体绕左上旋 32°；桶口用椭圆表示敞口。
        p.save()
        p.translate(11.5, 10.0)
        p.rotate(32)
        body = QPainterPath()
        body.moveTo(-6.5, -6.0)
        body.lineTo(6.5, -6.0)
        body.lineTo(4.8, 8.0)
        body.quadTo(0.0, 9.6, -4.8, 8.0)
        body.closeSubpath()
        p.setBrush(QColor(90, 175, 245, 60))
        p.drawPath(body)
        # 桶口里的颜料面：比桶身更实一点，和滴落的颜料同色。
        p.setBrush(QColor(90, 175, 245, 155))
        p.drawEllipse(QPointF(0.0, -6.0), 6.5, 2.4)
        p.setPen(QPen(_ICON_BLUE, 1.3))
        p.setBrush(Qt.NoBrush)
        p.drawLine(-6.1, 0.6, 6.1, 0.6)          # 桶身箍线
        p.restore()
        # 从桶口挂下来的颜料：一条下坠液柱 + 两滴正在掉落的颜料。
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(90, 175, 245, 200))
        stream = QPainterPath()
        stream.moveTo(19.2, 9.6)
        stream.cubicTo(19.4, 13.0, 20.6, 14.4, 20.2, 18.0)
        stream.lineTo(22.8, 18.0)
        stream.cubicTo(23.0, 14.0, 22.0, 12.6, 21.6, 9.2)
        stream.closeSubpath()
        p.drawPath(stream)
        p.drawEllipse(QPointF(21.4, 21.6), 2.1, 2.6)
        p.drawEllipse(QPointF(24.2, 24.8), 1.5, 1.9)
    elif kind == "tileselect":
        # 旧的「框选」虚线矩形：按钮已合并，保留绘制仅为兼容外部直接调用。
        p.setPen(QPen(_ICON_BLUE, 1.6, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(4, 4, 20, 20)
        p.setPen(QPen(_ICON_BLUE, 2.2))
        for x, y in ((4, 4), (24, 24)):
            p.drawLine(x - 2, y, x + 2, y)
            p.drawLine(x, y - 2, x, y + 2)
    elif kind == "eraser":
        # 斜放橡皮：蓝色轮廓 + 浅色擦面，和铅笔同一套线条风格。
        p.save()
        p.translate(14, 15)
        p.rotate(-30)
        p.drawRoundedRect(-10, -5.5, 20, 11, 2, 2)
        p.setBrush(QColor(225, 240, 255, 215))
        p.drawRect(-10, -5.5, 6.5, 11)
        p.setBrush(Qt.NoBrush)
        p.drawLine(-3.5, -5.5, -3.5, 5.5)
        p.restore()
    elif kind == "select":
        # 鼠标箭头：深色实心 + 一圈亮蓝描边（蓝色画在箭头本身，不是按钮框）。
        p.setBrush(QColor(28, 32, 39))
        p.drawPolygon(_polygon([(5, 3), (5, 23), (10, 18), (13, 25),
                                (16, 24), (13, 17), (20, 17)]))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(_polygon([(21, 4), (21, 12), (25, 12)]))
    elif kind == "nodes":
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(_polygon([(4, 21), (10, 8), (19, 16), (24, 6)]))
        for x, y in ((4, 21), (10, 8), (19, 16), (24, 6)):
            p.setBrush(QColor(235, 248, 255, 240))
            p.drawRect(x - 2, y - 2, 5, 5)
        p.setBrush(Qt.NoBrush)
    elif kind == "rect":
        p.drawRect(4, 6, 20, 16)
    elif kind == "point":
        p.drawEllipse(QPointF(14, 12), 6.0, 7.0)
        p.drawPolygon(_polygon([(9, 16), (19, 16), (14, 25)]))
    elif kind == "ellipse":
        p.drawEllipse(4, 7, 20, 14)
    elif kind == "polygon":
        p.drawPolygon(_polygon([(5, 10), (14, 4), (23, 10),
                                (23, 20), (14, 25), (5, 20)]))
    elif kind == "polyline":
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(_polygon([(4, 22), (14, 5), (24, 22)]))
    elif kind == "tile":
        p.setBrush(QColor(255, 236, 180, 150))
        p.drawRect(4, 5, 20, 18)
        p.setPen(QPen(_ICON_BLUE, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(19, 10), 2.6, 2.6)
        p.drawPolyline(_polygon([(5, 20), (11, 13), (15, 18), (19, 14), (23, 21)]))
    elif kind == "template":
        p.setPen(QPen(_ICON_BLUE, 1.5))
        p.setBrush(QColor(245, 170, 55, 215))
        p.drawEllipse(QPointF(14, 9), 4.5, 4.5)          # 头
        p.drawRoundedRect(8, 14, 12, 9, 3, 3)           # 身体
        p.setBrush(QColor(225, 240, 255, 225))
        p.drawRect(16, 12, 9, 11)                        # 后面的模板文档
        p.drawLine(18, 16, 23, 16)
        p.drawLine(18, 19, 23, 19)
    elif kind == "image":
        # 外框用蓝色虚线，内部相片保留 Tiled 的粉色作为识别色。
        p.setPen(QPen(_ICON_BLUE, 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(3, 4, 22, 20)
        p.setPen(QPen(_ICON_BLUE, 1.3))
        p.setBrush(QColor(226, 140, 200, 175))
        p.drawRoundedRect(6, 8, 10, 8, 2, 2)
        p.drawRoundedRect(13, 13, 10, 8, 2, 2)
    elif kind == "text":
        # 「T」字形：走 QPainterPath 描边，蓝色轮廓 + 淡蓝填充。
        font = QFont("Arial", 21)
        font.setBold(True)
        path = QPainterPath()
        path.addText(QPointF(6.0, 22.5), font, "T")
        p.setBrush(QColor(90, 175, 245, 90))
        p.drawPath(path)
    p.end()
    return QIcon(pix)


def make_select_cursor() -> QCursor:
    """选择对象工具的光标：白色箭头 + 蓝色轮廓 + 深色外描边。

    系统白箭头在绿色草地或深蓝水面上几乎看不见，这里给它描一圈亮蓝边。
    """
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addPolygon(QPolygonF([
        QPointF(2, 2), QPointF(2, 25), QPointF(8.5, 18.5),
        QPointF(13, 28), QPointF(17, 26), QPointF(12.5, 17),
        QPointF(22, 17),
    ]))
    path.closeSubpath()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(8, 30, 55, 225), 4.6))
    p.drawPath(path)
    p.setBrush(QBrush(QColor(250, 253, 255, 245)))
    p.setPen(QPen(QColor(45, 150, 240, 255), 2.4))
    p.drawPath(path)
    p.end()
    return QCursor(pix, 2, 2)


# 按钮方块本身不加任何描边：彩色只出现在图标内部的图形上（图标彩色按需求保留）。
# 选中态用背景色区分（和第八轮一致），不再画彩色方框。
# 界面区分色收敛为双色：INK（近黑）+ ASH（偏黑灰）。
_BUTTON_STYLE = f"""
QToolButton {{
    border: none;
    background: transparent;
    padding: 2px;
    color: {TEXT};
}}
QToolButton:checked {{
    background: {ASH};
    border-radius: 3px;
    color: {TEXT};
}}
QToolButton:hover {{ background: rgba(36, 36, 36, 160); border-radius: 3px; }}
QToolButton:disabled {{ background: transparent; color: {TEXT_DIM}; }}
QToolButton:checked:disabled {{ background: transparent; }}
"""


class ObjectToolBar(QToolBar):
    toolChanged = Signal(str)
    layerChanged = Signal(object)

    # 竖排时一排排几个：16 个工具按 4 列排就是 4×4 的方格，比 16 行窄一半还多。
    # 面板被拉宽时 FlowHost 会自己塞进更多列，这个值只是「初始几列」。
    VERTICAL_COLUMNS = 4
    BUTTON_SIZE = 34

    def __init__(self, parent=None):
        super().__init__("对象工具", parent)
        self.setObjectName("ObjectToolBar")
        # 每个图标按钮都带一圈蓝色边框，选中态更亮，深色主题下不会糊成一片。
        self.setStyleSheet(_BUTTON_STYLE)
        self._doc = None
        self._target_layer = None       # 新对象落到哪个对象层（由文档决定）
        self.group = QActionGroup(self)
        self.group.setExclusive(True)
        self.actions_by_id: dict[str, QAction] = {}
        self.buttons_by_id: dict[str, QToolButton] = {}
        # 工具按钮全部装进流式容器：宽度变了自己换行。
        # columns_hint = 0 表示「横排铺一行」，变成竖排时再按 VERTICAL_COLUMNS 收。
        self.grid = FlowHost(columns_hint=0, spacing=3)
        for tid, name, shortcut, tip in TOOLS:
            action = QAction(make_object_icon(tid), name, self)
            action.setCheckable(True)
            keys = (shortcut,) + tuple(TOOL_EXTRA_SHORTCUTS.get(tid, ()))
            shortcut_text = " / ".join(k for k in keys if k)
            if shortcut_text:
                shortcut_text = f" ({shortcut_text})"
            action.setToolTip(f"{name}{shortcut_text} — {tip}")
            action.setStatusTip(f"{name}：{tip}")
            # B / E 的快捷键由主窗口 QAction 统一注册（那里还要同步两套工具状态），
            # 这里只把快捷键写进 tooltip，避免两个 QAction 抢同一个按键。
            # 合并后的选择工具同时挂 S / M 两个键，QAction 支持多快捷键。
            if keys and all(keys) and tid not in ("brush", "eraser"):
                action.setShortcuts([QKeySequence(k) for k in keys])
            action.triggered.connect(
                lambda _checked=False, value=tid: self.toolChanged.emit(value))
            self.group.addAction(action)
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setIconSize(QSize(28, 28))
            button.setFixedSize(self.BUTTON_SIZE, self.BUTTON_SIZE)
            button.setFocusPolicy(Qt.NoFocus)
            self.grid.add_button(button)
            # action 挂到容器上（不是 addAction 到工具栏），快捷键照旧生效，
            # 又不会在工具栏里多出一个重复按钮。
            self.grid.addAction(action)
            self.actions_by_id[tid] = action
            self.buttons_by_id[tid] = button
        self.actions_by_id["brush"].setChecked(True)
        self.addWidget(self.grid)

        # 对象层下拉已移除：新对象固定落到文档里的对象层（Triggers / 第一个
        # 对象层，见 set_document），需要改层时在「图层」面板里点那一层即可。
        self.addSeparator()
        self.hint = QLabel("瓦片画笔")
        self.hint.setStyleSheet(f"color:{TEXT_DIM}; padding-left:4px;")
        self._hint_action = self.addWidget(self.hint)
        self.orientationChanged.connect(self._on_orientation_changed)

        # 变换 4 按钮（水平/垂直翻转、顺/逆时针旋转）由 MainWindow 在
        # _build_toolbar() 里通过 add_transform_actions 追加；排在本文件的
        # 「瓦片画笔」文字标签之前（图标在左、文字在右）。
        self._transform_actions: list[QAction] = []

    def set_object_layer_active(self, active: bool):
        """当前层是否为对象层：控制「编辑节点 → 插入文字」整排按钮的启用态。

        非对象层时按钮置灰（Qt 自动把图标画成禁用色，且 :checked 背景也被
        QSS 去掉）；切回对象层后恢复。变换 4 键不在这里管，由 MainWindow
        的 ``_refresh_transform_actions`` 同步。
        """
        for tid in OBJECT_ONLY_TOOLS:
            action = self.actions_by_id.get(tid)
            if action is not None:
                action.setEnabled(active)

    def add_transform_actions(self, items):
        """把 4 个变换按钮排在「瓦片画笔」文字标签之前（图标左、文字右）。

        ``items`` 是 ``[(QAction, icon_kind), ...]``；action 在 MainWindow 里
        已经连好分发入口，这里只负责配图标并做成图标按钮。`__init__` 里先放了
        「竖线 + 瓦片画笔」，这里把文字临时摘下，复用它前面那条竖线当作按钮的
        分隔，追加按钮后再补一条竖线、把文字放回最右端。
        """
        self.removeAction(self._hint_action)  # 文字暂时摘下，竖线留着复用
        for action, kind in items:
            action.setIcon(make_object_icon(kind))
            self.addAction(action)
            btn = self.widgetForAction(action)
            if btn is not None:
                btn.setObjectName(f"Transform_{kind}")
                btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
                btn.setIconSize(QSize(28, 28))
                btn.setFixedSize(self.BUTTON_SIZE, self.BUTTON_SIZE)
            self._transform_actions.append(action)
        self.addSeparator()                   # 按钮与「瓦片画笔」之间的竖线
        self._hint_action = self.addWidget(self.hint)   # 文字移到最右（新 action）

    # ---------------- 布局：竖排时按宽度自动换行 ----------------
    def _grid_columns(self) -> int:
        """竖排时的参考列数（不超过工具总数）。"""
        return max(1, min(len(self.buttons_by_id) or 1,
                          self.VERTICAL_COLUMNS))

    def _grid_inner_width(self) -> int:
        return self.grid.flow.width_for_columns(self._grid_columns())

    def _on_orientation_changed(self, *_args):
        vertical = self.orientation() == Qt.Vertical
        self.grid.set_columns_hint(self._grid_columns() if vertical else 0)
        self.updateGeometry()

    def sizeHint(self):                              # noqa: N802
        hint = super().sizeHint()
        if self.orientation() != Qt.Vertical:
            return hint
        # 竖排：宽度收窄到「几列」，高度按实际行数重算（否则会被裁掉）。
        inner = self._grid_inner_width()
        width = inner + 8
        others = hint.height() - self.grid.sizeHint().height()
        return QSize(width, others + self.grid.height_for_width(inner))

    def minimumSizeHint(self):                       # noqa: N802
        if self.orientation() == Qt.Vertical:
            return self.sizeHint()
        return super().minimumSizeHint()

    # ---------------- 文档 ----------------
    def set_document(self, doc):
        self._doc = doc
        self._target_layer = self._default_object_layer(doc)
        self.layerChanged.emit(self._target_layer)

    # 默认落点的优先顺序：铁锈地图的触发器几乎都在 Triggers 对象层。
    PREFERRED_LAYER_NAMES = ("triggers", "unitobjects", "objects")

    @classmethod
    def _default_object_layer(cls, doc):
        """默认目标对象层：Triggers → UnitObjects → Objects → 第一个对象层。"""
        if doc is None:
            return None
        layers = [l for l in doc.layers if isinstance(l, ObjectLayer)]
        by_name = {(l.name or "").strip().lower(): l for l in layers}
        for wanted in cls.PREFERRED_LAYER_NAMES:
            if wanted in by_name:
                return by_name[wanted]
        return layers[0] if layers else None

    def set_target_layer(self, layer):
        """外部（图层面板选中对象层）指定新对象的落点。"""
        if not isinstance(layer, ObjectLayer) or layer is self._target_layer:
            return
        self._target_layer = layer
        self.layerChanged.emit(layer)

    def current_layer(self):
        return self._target_layer

    # ---------------- 工具 ----------------
    def current_tool(self) -> str:
        action = self.group.checkedAction()
        for tid, act in self.actions_by_id.items():
            if act is action:
                return tid
        return "brush"

    def select_tool(self, tid: str) -> None:
        """同步选中态；不触发 toolChanged（由 MainWindow 统一切换状态）。"""
        action = self.actions_by_id.get(canonical_tool_id(tid))
        if action is None or action.isChecked():
            return
        action.setChecked(True)
        self.set_hint(tid)

    def set_hint(self, tid: str) -> None:
        tid = canonical_tool_id(tid)
        label = next((name for t, name, _s, _d in TOOLS if t == tid), tid)
        self.hint.setText(label)
