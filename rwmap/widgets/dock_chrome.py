"""面板统一格式（Dock 标题栏 + 内容区深色底）。

所有 QDockWidget（图层 / 属性 / 图块集 / 单位 / Triggers）共用这一套标题栏，
统一的内容：

- 标题栏背景色、固定高度、左右内边距；
- 左上角标题的字号、粗细、颜色；
- 右上角「浮动 / 关闭」两个按钮的图标（直接取 Qt 原生 Dock 按钮的 QIcon）、
  图标尺寸、按钮尺寸、悬浮/按下反馈；
- 双击标题栏切换浮动（弥补自定义标题栏取代系统标题栏后的原生行为）；
- 覆盖整个面板的 1px ASH 描边（QSS 的 QDockWidget 边框在停靠态不渲染，
  因此用透明子控件统一画，鼠标事件完全穿透）。

样式写法注意：`setStyleSheet()` 里的**选择器规则不会可靠地作用到子控件**，
所以每个控件（标题栏本体 / QLabel / QToolButton / 树 / 按钮）都各自设样式，
并同时设置 QPalette 兜底，保证离屏渲染与系统深色/浅色主题下都是深底浅字。

另提供 `apply_panel_theme()`，给没有自带深色样式的面板内容区统一底色。
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QPushButton, QStyle, QToolButton, QTreeWidget, QWidget,
)

from ..theme import (
    ASH, INK, PANEL_BORDER_COLOR, PANEL_BORDER_PX, TEXT, TEXT_DIM,
)

# ---- 统一常量（改一处，5 个面板一起变） ----
# 界面区分色收敛为双色：INK（近黑，底层） + ASH（偏黑灰，抬起层）。
TITLE_BAR_BG = ASH
TITLE_BAR_BG_FLAT = INK         # 图集设置专用：标题栏与内容区同色
PANEL_BG = INK
TITLE_COLOR = TEXT
TITLE_COLOR_FLAT = TEXT         # 该面板深底浅字
TITLE_BAR_HEIGHT = 22
TITLE_FONT_PT = 9
ICON_SIZE = 16
BUTTON_SIZE = 18
TITLE_TEXT_MARGIN = 7

_BG_QCOLOR = QColor(TITLE_BAR_BG)
_PANEL_QCOLOR = QColor(PANEL_BG)
_TEXT_QCOLOR = QColor(TITLE_COLOR)

_TITLE_BAR_QSS = f"background: {TITLE_BAR_BG};"
_LABEL_QSS = f"color: {TITLE_COLOR}; background: transparent;"
# 悬停 / 按下用 ASH 的不同透明度表达，不再引入新的灰阶。
_BUTTON_QSS = """
QToolButton { background: transparent; border: none; }
QToolButton:hover { background: rgba(36, 36, 36, 160); }
QToolButton:pressed { background: rgba(36, 36, 36, 255); }
"""
_BUTTON_QSS_FLAT = """
QToolButton { background: transparent; border: none; }
QToolButton:hover { background: rgba(36, 36, 36, 200); }
QToolButton:pressed { background: rgba(16, 16, 16, 255); }
"""

_PANEL_BASE_QSS = f"""
background: {PANEL_BG};
color: {TITLE_COLOR};
alternate-background-color: {ASH};
"""
_TREE_QSS = _PANEL_BASE_QSS + f"""
QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {ASH}; color: {TEXT};
}}
"""
_HEADER_QSS = f"""
QHeaderView::section {{ background: {ASH}; color: {TEXT_DIM};
                       border: none; border-right: 1px solid {INK};
                       padding: 2px 4px; }}
"""
_PUSH_QSS = f"""
QPushButton {{ background: {ASH}; color: {TEXT};
              border: 1px solid {INK}; border-radius: 3px; padding: 2px 6px; }}
QPushButton:hover {{ background: rgba(36, 36, 36, 200); }}
QPushButton:pressed {{ background: rgba(16, 16, 16, 255); }}
"""


def _dark_palette(widget: QWidget, bg: QColor, text: QColor) -> QPalette:
    pal = widget.palette()
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        pal.setColor(role, bg)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(role, text)
    pal.setColor(QPalette.AlternateBase, QColor(ASH))
    return pal


@lru_cache(maxsize=1)
def native_dock_icons() -> tuple[QIcon, QIcon]:
    """取 Qt 原生 QDockWidget 标题栏的「浮动 / 关闭」图标。

    自定义深色标题栏只是替换了背景，按钮图标必须与 Qt 原生标题栏完全一致；
    用文字字符 ◇ / × 会被字体渲染成彩色圆环等奇怪形状。
    QIcon 来自样式引擎，会随系统明暗主题自动变色，比抓静态 QPixmap 可靠。
    """
    float_icon = close_icon = None
    probe = QDockWidget()                 # 无需显示即已创建两个标题栏按钮
    for child in probe.findChildren(QWidget):
        if child.objectName() == "qt_dockwidget_floatbutton":
            float_icon = QIcon(child.icon())
        elif child.objectName() == "qt_dockwidget_closebutton":
            close_icon = QIcon(child.icon())
    probe.deleteLater()
    app = QApplication.instance()
    style = app.style() if app is not None else None
    if float_icon is None or float_icon.isNull():
        float_icon = (style.standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
                      if style is not None else QIcon())
    if close_icon is None or close_icon.isNull():
        close_icon = (style.standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
                      if style is not None else QIcon())
    return float_icon, close_icon


def _title_button(parent, icon: QIcon, tip: str, slot, light: bool = False) -> QToolButton:
    btn = QToolButton(parent)
    btn.setIcon(icon)
    btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    btn.setToolTip(tip)
    btn.setAutoRaise(True)
    btn.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    btn.setStyleSheet(_BUTTON_QSS_FLAT if light else _BUTTON_QSS)
    btn.clicked.connect(slot)
    return btn


def flat_dock_icons(color: str = TITLE_COLOR_FLAT) -> tuple[QIcon, QIcon]:
    """统一深底标题栏用的浅色图标（× / 浮动方块），不依赖系统主题色。

    系统原生 dock 图标在深色底上可能是深色而看不见，这里自绘浅色字形兜底。
    """
    def draw(glyph: str) -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(color))
        pen.setWidthF(1.6)
        p.setPen(pen)
        if glyph == "close":
            p.drawLine(4.5, 4.5, 11.5, 11.5)
            p.drawLine(11.5, 4.5, 4.5, 11.5)
        else:  # float
            p.drawRect(4.5, 4.5, 7, 7)
        p.end()
        return QIcon(pm)
    return draw("float"), draw("close")


class DockTitleBar(QWidget):
    """统一标题栏本体；挂在 dock.titleBarWidget() 上。"""

    def __init__(self, dock: QDockWidget, title: str, light: bool = False):
        super().__init__(dock)
        self._light = light
        bg_q = QColor(TITLE_BAR_BG_FLAT) if light else _BG_QCOLOR
        text_q = QColor(TITLE_COLOR_FLAT) if light else _TEXT_QCOLOR
        self._bg_qcolor = bg_q
        self._text_qcolor = text_q
        self.setObjectName("DockTitleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.setStyleSheet(f"background: {bg_q.name()};")
        self.setAutoFillBackground(True)
        self.setPalette(_dark_palette(self, bg_q, text_q))
        self._dock = dock

        row = QHBoxLayout(self)
        row.setContentsMargins(TITLE_TEXT_MARGIN, 2, 4, 2)
        row.setSpacing(2)

        self.label = QLabel(title, self)
        font = QFont(self.label.font())
        font.setPointSize(TITLE_FONT_PT)
        font.setBold(True)
        self.label.setFont(font)
        self.label.setStyleSheet(f"color: {text_q.name()}; background: transparent;")
        self.label.setPalette(_dark_palette(self.label, bg_q, text_q))
        row.addWidget(self.label)
        row.addStretch(1)

        if light:
            float_icon, close_icon = flat_dock_icons()
        else:
            float_icon, close_icon = native_dock_icons()
        self.btn_float = _title_button(
            self, float_icon, "浮动",
            lambda: dock.setFloating(not dock.isFloating()), light)
        self.btn_close = _title_button(self, close_icon, "关闭", dock.hide, light)
        row.addWidget(self.btn_float)
        row.addWidget(self.btn_close)

    def set_title(self, title: str):
        self.label.setText(title)

    def paintEvent(self, event):
        # 作为 QDockWidget 的子控件渲染时，样式表 / QPalette 在某些平台插件下
        # 会被原生标题栏底色盖掉；直接填充一次最可靠（子控件随后自行绘制）。
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._bg_qcolor)
        painter.end()

    def mouseDoubleClickEvent(self, event):
        # 自定义标题栏取代系统标题栏后，双击浮动这一原生行为要补回来
        if event.button() == Qt.LeftButton:
            self._dock.setFloating(not self._dock.isFloating())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class DockBorder(QWidget):
    """覆盖整个面板的 1px ASH 描边层。

    QSS 里的 ``QDockWidget { border: ... }`` 在停靠状态下不一定被 Qt 真正画出，
    因此统一标题栏安装时再挂一层透明子控件：只画边框、鼠标完全穿透，
    面板缩放 / 浮动 / 停靠时跟随 dock 的 rect。
    """

    def __init__(self, dock: QDockWidget):
        super().__init__(dock)
        self.setObjectName("DockBorder")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._dock = dock
        dock.installEventFilter(self)
        self.setGeometry(dock.rect())

    def eventFilter(self, obj, event):
        if obj is self._dock and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move):
            self.setGeometry(self._dock.rect())
            self.raise_()
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor(PANEL_BORDER_COLOR)
        w = self.width()
        h = self.height()
        line = PANEL_BORDER_PX
        # 实心矩形而非 QPen 描边：1px 时也能精确占满最外圈像素，不会被
        # 半像素对齐吃掉或抗锯齿成灰边。
        painter.fillRect(0, 0, w, line, color)
        painter.fillRect(0, h - line, w, line, color)
        painter.fillRect(0, 0, line, h, color)
        painter.fillRect(w - line, 0, line, h, color)
        painter.end()


def install_dock_border(dock: QDockWidget) -> DockBorder:
    """幂等安装统一描边层；所有面板共用，新增面板调用一次即可。"""
    border = dock.findChild(DockBorder, "DockBorder")
    if border is None:
        border = DockBorder(dock)
    border.raise_()
    return border


def install_title_bar(dock: QDockWidget, title: str | None = None,
                     light: bool = False) -> DockTitleBar:
    """给任意 QDockWidget 装统一标题栏；重复调用不会叠加。

    同时把 `btn_float` / `btn_close` 挂到 dock 上，便于测试与后续扩展。
    `light=True` 时用「统一底色」主题（标题栏与内容区同色、浅字 + 自绘浅色图标），
    仅作用于图集设置面板；其余面板保持 ASH 标题栏与原生图标。
    """
    bar = dock.titleBarWidget()
    if isinstance(bar, DockTitleBar):
        if title is not None:
            bar.set_title(title)
        install_dock_border(dock)
        return bar
    bar = DockTitleBar(dock, title if title is not None else dock.windowTitle(), light)
    dock.setTitleBarWidget(bar)
    dock.btn_float = bar.btn_float
    dock.btn_close = bar.btn_close
    install_dock_border(dock)
    return bar


def apply_panel_theme(widget: QWidget | None) -> QWidget | None:
    """给面板内容区套统一深色样式（已有自己样式的面板不要调用）。

    对本体和每个相关子控件单独设样式 + QPalette，避免只依赖父级样式表。
    """
    if widget is None:
        return None
    if not widget.objectName():
        widget.setObjectName("DockPanelHolder")
    widget.setStyleSheet(_TREE_QSS)
    widget.setPalette(_dark_palette(widget, _PANEL_QCOLOR, _TEXT_QCOLOR))
    widget.setAutoFillBackground(True)

    if isinstance(widget, (QTreeWidget, QListWidget)) and hasattr(widget, "header"):
        header = widget.header()
        if isinstance(header, QHeaderView):
            header.setStyleSheet(_HEADER_QSS)
            header.setPalette(_dark_palette(header, QColor(ASH), _TEXT_QCOLOR))
    for child in widget.findChildren(QTreeWidget) + widget.findChildren(QListWidget):
        child.setStyleSheet(_TREE_QSS)
        child.setPalette(_dark_palette(child, _PANEL_QCOLOR, _TEXT_QCOLOR))
        if isinstance(child, QTreeWidget):
            child.header().setStyleSheet(_HEADER_QSS)
    for child in widget.findChildren(QPushButton):
        child.setStyleSheet(_PUSH_QSS)
        child.setPalette(_dark_palette(child, QColor(ASH), _TEXT_QCOLOR))
    return widget
