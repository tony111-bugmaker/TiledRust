"""图层面板：照搬 Tiled 的行布局与图标，但保持深色主题。

- 每行：左侧类型图标（瓦片层蓝网格 / 对象层紫图标）+ 层名（当前层粗体）+
  右侧可点击的眼睛（可见性）与锁（锁定）图标，取代原来的复选框 / [tile] 文本；
- 当前层整行蓝色高亮，隐藏层名字变灰；双击层名行内重命名，右键出操作菜单；
- 底部图标按钮栏按 Tiled 顺序：新建 / 上移 / 下移 / 复制 / 删除 /
  锁定其它所有层 / 显示或隐藏其它层（向下合并已移除）；
  透明度滑块保留在按钮栏上方；
- 图标全部来自 Tiled 原生资源（见 tiled_icons.py），面板/列表/按钮栏均为深色。
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QDockWidget, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QSlider, QStyledItemDelegate, QStyle, QToolButton, QVBoxLayout,
    QWidget,
)

from .dock_chrome import install_title_bar
from .tiled_icons import tiled_icon, tiled_pixmap
from ..theme import ASH, INK, TEXT, TEXT_DIM, WIN11_BLUE

# 界面区分色收敛为双色：INK（近黑底层）+ ASH（偏黑灰抬起层）。
_BG = QColor(INK)
_ROW_HOVER = QColor(ASH)
_ROW_SELECT = QColor(ASH)          # 选中行也用 ASH，靠边框/文字区分
_TEXT = QColor(TEXT)
_TEXT_DIM = QColor(TEXT_DIM)
_TEXT_ON_SELECT = QColor(TEXT)
ROW_HEIGHT = 24
_EYE_W = 18
_LOCK_W = 18
_RIGHT_PAD = 6

# 透明度拖动球用 Win11 蓝：属于"当前操作点"功能色，不参与双色底色收敛。
_PANEL_STYLE = f"""
QWidget#LayerPanelHolder {{ background: {INK}; }}
QListWidget {{ background: {INK}; border: none; color: {TEXT}; }}
QToolButton {{ background: transparent; border: 1px solid transparent;
              border-radius: 3px; padding: 2px; }}
QToolButton:hover {{ background: {ASH}; border: 1px solid {INK}; }}
QToolButton:pressed {{ background: {INK}; border: 1px solid {INK}; }}
QToolButton:disabled {{ background: transparent; }}
QLabel {{ color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {ASH}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ASH}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {WIN11_BLUE}; width: 10px;
                            margin: -4px 0; border-radius: 5px; }}
"""


@lru_cache(maxsize=None)
def _pix(name: str):
    """QPixmap 解码缓存：paint() 每帧要画十几次图标，不能每次重新 base64 解码。"""
    return tiled_pixmap(name)


class _LayerRowDelegate(QStyledItemDelegate):
    """自绘 Tiled 风格行；眼睛 / 锁图标区可点击，层名区可双击编辑。"""

    def __init__(self, dock):
        super().__init__(dock.list)
        self.dock = dock

    @staticmethod
    def icon_rects(rect: QRect):
        cy = rect.top() + (rect.height() - 14) // 2
        lock = QRect(rect.right() - _RIGHT_PAD - _LOCK_W + 2, cy, 14, 14)
        eye = QRect(lock.left() - _EYE_W, cy, 14, 14)
        return eye, lock

    def paint(self, painter: QPainter, option, index):
        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        layer = self.dock._layer_at(index.row())
        if layer is None:
            return
        if selected:
            painter.fillRect(rect, _ROW_SELECT)
        elif hover:
            painter.fillRect(rect, _ROW_HOVER)

        # 左侧类型图标
        kind = "object" if getattr(layer, "kind", "tile") == "object" else "tile"
        painter.drawPixmap(rect.left() + 5,
                           rect.top() + (rect.height() - 16) // 2,
                           _pix(kind))

        # 层名（选中行粗体，隐藏层变灰）
        font = QFont(option.font)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(_TEXT_ON_SELECT if selected
                       else (_TEXT_DIM if not layer.visible else _TEXT))
        eye, _lock = self.icon_rects(rect)
        painter.drawText(QRect(rect.left() + 26, rect.top(),
                              max(20, eye.left() - rect.left() - 30),
                              rect.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, layer.name)

        # 右侧状态图标
        painter.setOpacity(1.0 if layer.visible else 0.55)
        painter.drawPixmap(eye.left(), eye.top(),
                           _pix("visible" if layer.visible else "hidden"))
        painter.setOpacity(1.0)
        locked = bool(getattr(layer, "locked", False))
        painter.setOpacity(1.0 if locked else 0.40)
        painter.drawPixmap(_lock.left(), _lock.top(),
                           _pix("locked" if locked else "unlocked"))
        painter.setOpacity(1.0)

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_HEIGHT)

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            layer = self.dock._layer_at(index.row())
            if layer is None:
                return False
            eye, lock = self.icon_rects(option.rect)
            pos = event.position().toPoint()
            if eye.contains(pos):
                self.dock.layerVisibilityChanged.emit(layer, not layer.visible)
                self.dock.list.viewport().update()
                return True
            if lock.contains(pos):
                self.dock.lockLayerToggled.emit(layer)
                return True
        return super().editorEvent(event, model, option, index)

    # ---- 双击行内重命名 ----
    def createEditor(self, parent, option, index):
        ed = QLineEdit(parent)
        ed.setStyleSheet(f"background:{ASH}; color:{TEXT}; "
                         f"border:1px solid {INK}; padding:0 2px;")
        return ed

    def setEditorData(self, editor, index):
        layer = self.dock._layer_at(index.row())
        editor.setText(layer.name if layer is not None else "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        name = editor.text().strip()
        layer = self.dock._layer_at(index.row())
        if layer is not None and name and name != layer.name:
            self.dock.layerRenameRequested.emit(layer, name)
        # 不写回 model：层名由文档决定，主窗口重命名后会 refresh

    def updateEditorGeometry(self, editor, option, index):
        eye, _lock = self.icon_rects(option.rect)
        editor.setGeometry(QRect(option.rect.left() + 26, option.rect.top() + 2,
                                 max(60, eye.left() - option.rect.left() - 30),
                                 option.rect.height() - 4))


class LayersDock(QDockWidget):
    layerSelected = Signal(object)
    layerVisibilityChanged = Signal(object, bool)
    layerOpacityChanged = Signal(object, float)
    layerRenameRequested = Signal(object, str)
    # 一次改多层的可见性（「显示/隐藏其它图层」）：[(layer, visible), ...]
    # 单独走一个批量信号，主窗口才能把它们合成**一条**撤销记录。
    layersVisibilityChanged = Signal(list)
    # 一次改多层的锁定状态（「锁定/解锁其它所有层」），合成一条撤销。
    layersLockChanged = Signal(list)
    # 结构性操作（由 MainWindow 统一走 QUndoCommand 落撤销栈）
    addLayerRequested = Signal(str)          # kind: "tile" / "object"
    deleteLayerRequested = Signal(object)
    duplicateLayerRequested = Signal(object)
    moveLayerRequested = Signal(object, int)  # direction: +1 上移 / -1 下移
    lockLayerToggled = Signal(object)

    def __init__(self, parent=None):
        super().__init__("图层", parent)
        self.doc = None
        self._index: dict[int, object] = {}
        self.list = QListWidget()
        self.list.setObjectName("LayerList")
        self.list.setMouseTracking(True)
        self.list.setItemDelegate(_LayerRowDelegate(self))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setUniformItemSizes(True)

        # ---- 底部图标按钮栏（顺序照搬 Tiled 图层视图工具条） ----
        self.btn_new = self._tool("new", "新建图层（选择瓦片层 / 对象层）")
        menu = QMenu(self)
        act_tile = menu.addAction("瓦片层")
        act_obj = menu.addAction("对象层")
        self.btn_new.setMenu(menu)
        # InstantPopup：点一下整块按钮立即弹出「瓦片层 / 对象层」菜单，
        # 选中哪一项才新建哪一项（新层加到最下侧）。整块一个判定区、hover
        # 与其它按钮一致；不用 MenuButtonPopup（它把按钮拆成两半）。
        self.btn_new.setPopupMode(QToolButton.InstantPopup)
        act_tile.triggered.connect(lambda _a: self.addLayerRequested.emit("tile"))
        act_obj.triggered.connect(lambda _a: self.addLayerRequested.emit("object"))

        self.btn_up = self._tool("up", "上移图层")
        self.btn_down = self._tool("down", "下移图层")
        self.btn_dup = self._tool("duplicate", "复制图层")
        self.btn_del = self._tool("delete", "删除图层")
        # 「向下合并图层」按钮已移除。
        # 锁定按钮：与「显示 / 隐藏其它所有层」对称，点一下批量切换其它层的锁定状态。
        self.btn_lock = self._tool("lock_button", "锁定 / 解锁其它所有层")
        self.btn_others = self._tool("show_others", "显示 / 隐藏其它所有层")

        self.btn_dup.clicked.connect(
            lambda: self.duplicateLayerRequested.emit(self.selected_layer()))
        self.btn_del.clicked.connect(
            lambda: self.deleteLayerRequested.emit(self.selected_layer()))
        self.btn_up.clicked.connect(
            lambda: self.moveLayerRequested.emit(self.selected_layer(), 1))
        self.btn_down.clicked.connect(
            lambda: self.moveLayerRequested.emit(self.selected_layer(), -1))
        self.btn_lock.clicked.connect(self._toggle_other_layers_lock)
        self.btn_others.clicked.connect(self._toggle_other_layers)

        bar = QWidget()
        bar.setObjectName("LayerToolBar")
        bar.setStyleSheet(f"background:{ASH};")
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(2)
        for b in (self.btn_new, self.btn_up, self.btn_down, self.btn_dup,
                  self.btn_del, self.btn_lock):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_others)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.label = QLabel("透明度 100%")
        opacity_row = QHBoxLayout()
        opacity_row.setContentsMargins(6, 2, 6, 2)
        opacity_row.addWidget(self.label)
        opacity_row.addWidget(self.slider, 1)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.list, 1)
        layout.addLayout(opacity_row)
        layout.addWidget(bar)
        holder = QWidget()
        holder.setObjectName("LayerPanelHolder")
        holder.setLayout(layout)
        holder.setStyleSheet(_PANEL_STYLE)
        self.setWidget(holder)
        install_title_bar(self)          # 与其它 4 个面板共用统一深色标题栏

        self.list.currentRowChanged.connect(self._on_select)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        self.slider.valueChanged.connect(self._on_opacity)

    def _toggle_float(self):
        """保留旧接口；统一标题栏里的浮动按钮已直接调用 setFloating。"""
        self.setFloating(not self.isFloating())

    def _tool(self, icon: str, tip: str) -> QToolButton:
        btn = QToolButton()
        btn.setIcon(tiled_icon(icon))
        btn.setToolTip(tip)
        btn.setAutoRaise(True)
        btn.setFixedSize(26, 26)
        btn.setIconSize(QSize(16, 16))
        return btn

    def selected_layer(self):
        return self._layer_at(self.list.currentRow())

    def set_document(self, doc):
        self.doc = doc
        self._index.clear()
        self.list.blockSignals(True)
        self.list.clear()
        if doc is not None:
            for layer in reversed(doc.layers):
                item = QListWidgetItem(layer.name)
                # 显式去掉默认自带的复选框标志：可见性改由右侧眼睛图标控制
                item.setFlags((item.flags() | Qt.ItemIsEditable)
                              & ~Qt.ItemIsUserCheckable)
                item.setSizeHint(QSize(0, ROW_HEIGHT))
                item.setToolTip(layer.note or ("只读层" if layer.readonly else ""))
                self.list.addItem(item)
                self._index[self.list.count() - 1] = layer
            if self.list.count():
                self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self):
        layer = self.selected_layer()
        has = layer is not None
        for btn in (self.btn_dup, self.btn_del, self.btn_up, self.btn_down,
                    self.btn_lock, self.btn_others):
            btn.setEnabled(has)
        # 锁定按钮现在作用于「其它所有层」，图标/提示固定，不随当前层状态翻转。
        self.btn_lock.setIcon(tiled_icon("lock_button"))
        self.btn_lock.setToolTip("锁定 / 解锁其它所有层")

    def _layer_at(self, row: int):
        if row < 0 or self.doc is None:
            return None
        # 显示顺序是 XML 倒序
        idx = len(self.doc.layers) - 1 - row
        return self.doc.layers[idx] if 0 <= idx < len(self.doc.layers) else None

    def _on_select(self, row: int):
        layer = self._layer_at(row)
        if layer is None:
            self._update_buttons()
            return
        self.slider.blockSignals(True)
        self.slider.setValue(int(layer.opacity * 100))
        self.slider.blockSignals(False)
        self.label.setText(f"透明度 {int(layer.opacity * 100)}%")
        self._update_buttons()
        self.layerSelected.emit(layer)

    def _on_opacity(self, value: int):
        layer = self._layer_at(self.list.currentRow())
        if layer is None:
            return
        self.label.setText(f"透明度 {value}%")
        self.layerOpacityChanged.emit(layer, value / 100.0)

    def _toggle_other_layers(self):
        """Tiled 的 Show/Hide Other Layers：把其它层全隐藏，若已全隐藏则全显示。"""
        current = self.selected_layer()
        if current is None or self.doc is None:
            return
        others = [l for l in self.doc.layers if l is not current]
        any_visible = any(l.visible for l in others)
        changes = [(layer, not any_visible)
                   for layer in others if layer.visible == any_visible]
        if changes:
            self.layersVisibilityChanged.emit(changes)
        self.list.viewport().update()

    def _toggle_other_layers_lock(self):
        """锁定 / 解锁其它所有层：与「显示/隐藏其它层」同样的批量切换语义。"""
        current = self.selected_layer()
        if current is None or self.doc is None:
            return
        others = [l for l in self.doc.layers if l is not current]
        any_unlocked = any(not getattr(l, "locked", False) for l in others)
        changes = [(layer, any_unlocked)
                   for layer in others
                   if bool(getattr(layer, "locked", False)) != any_unlocked]
        if changes:
            self.layersLockChanged.emit(changes)
        self.list.viewport().update()

    def _show_context_menu(self, pos):
        self._build_context_menu(pos).exec(
            self.list.viewport().mapToGlobal(pos))

    def _build_context_menu(self, pos) -> QMenu:
        """只构建菜单、不弹窗（便于离屏回归；Tiled 右键菜单同款动作）。"""
        item = self.list.itemAt(pos)
        layer = self._layer_at(self.list.row(item)) if item is not None else None
        menu = QMenu(self)
        menu.addAction(tiled_icon("tile"), "新建瓦片层",
                       lambda: self.addLayerRequested.emit("tile"))
        menu.addAction(tiled_icon("object"), "新建对象层",
                       lambda: self.addLayerRequested.emit("object"))
        if layer is None:
            return menu
        menu.addSeparator()
        menu.addAction(tiled_icon("up"), "上移",
                       lambda: self.moveLayerRequested.emit(layer, 1))
        menu.addAction(tiled_icon("down"), "下移",
                       lambda: self.moveLayerRequested.emit(layer, -1))
        menu.addAction(tiled_icon("duplicate"), "复制图层",
                       lambda: self.duplicateLayerRequested.emit(layer))
        menu.addAction(tiled_icon("delete"), "删除图层",
                       lambda: self.deleteLayerRequested.emit(layer))
        menu.addSeparator()
        menu.addAction(tiled_icon("visible" if not layer.visible else "hidden"),
                       "隐藏" if layer.visible else "显示",
                       lambda: self.layerVisibilityChanged.emit(layer, not layer.visible))
        menu.addAction(tiled_icon("lock_button"),
                       "解锁" if getattr(layer, "locked", False) else "锁定",
                       lambda: self.lockLayerToggled.emit(layer))
        return menu

    def select_layer(self, layer):
        if self.doc is None or not any(item is layer for item in self.doc.layers):
            return
        index = next(i for i, item in enumerate(self.doc.layers) if item is layer)
        self.list.setCurrentRow(len(self.doc.layers) - 1 - index)
