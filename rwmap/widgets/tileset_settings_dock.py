"""图集设置 / 每瓦片属性：两块独立停靠面板（一左一右）。

- ``TilesetPropsDock``（图集设置，右侧）：名称 / 瓦片尺寸 / 边距 / 间距 /
  图像 / 透明色 + 应用/重置；
- ``TilesetTilePropsDock``（瓦片属性，左侧）：点图集里的瓦片后按类别分页编辑
  该格的属性（地形 / 单位 / 其他）。它就是「图集设置」里原来的「自定义属性」
  面板的位置，现在换成更有用的逐格属性编辑。

标题栏复用 dock_chrome；`TilesetPropsDock`（图集设置）用「统一底色」
主题（标题栏 + 内容 + 底部按钮行全部 INK，`install_title_bar(light=True)`）。
所有面板（含本面板）外框统一 1px ASH 暗线，沿用 theme.PANEL_BORDER_QSS。
两块都只在「图集任务」里出现（地图任务隐藏）。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDockWidget, QVBoxLayout, QWidget

from ..document.tileset import Tileset
from ..theme import PANEL_BORDER_QSS
from .dock_chrome import install_title_bar
from .tileset_editor import TilePropertyEditor
from .tileset_settings_panel import PANEL_BG, TilesetPropsPanel, apply_panel_bg


class TilesetPropsDock(QDockWidget):
    """图集设置专用停靠面板（右侧）；随当前图集任务自动绑定。"""

    applied = Signal(object)        # 传出被修改的 Tileset

    def __init__(self, parent=None):
        super().__init__("图集设置", parent)
        self.ts: Tileset | None = None
        self.doc = None
        self.undo_stack = None
        # 三层都要显式填同一个底色：自定义 QWidget 子类不会自动绘制
        # 样式表 background，少一层就会露出主窗口的深色（底部黑条）。
        # 外框按全面板统一规则加 2px 暗线。
        self.setStyleSheet(
            f"QDockWidget{{background:{PANEL_BG};{PANEL_BORDER_QSS}}}")
        apply_panel_bg(self)

        self.panel = TilesetPropsPanel()
        holder = QWidget()
        apply_panel_bg(holder)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.panel)
        self.setWidget(holder)
        install_title_bar(self, light=True)

        self.panel.applied.connect(lambda ts: self.applied.emit(ts))

    def set_context(self, ts: Tileset, doc, undo_stack):
        self.ts = ts
        self.doc = doc
        self.undo_stack = undo_stack
        self.panel.set_context(ts, doc, undo_stack)

    def set_tileset(self, ts: Tileset | None):
        self.ts = ts
        self.panel.set_tileset(ts)


class TilesetTilePropsDock(QDockWidget):
    """每瓦片属性专用停靠面板（左侧）；点图集里的瓦片后编辑该格属性。

    取代原先的「自定义属性」面板位置：选中瓦片时显示「瓦片 #N · M 个属性」，
    按地形 / 单位 / 其他三页分页点选编辑；未选瓦片时提示先点格。
    """

    changed = Signal()               # 透传内部编辑器的改动信号

    def __init__(self, parent=None):
        super().__init__("瓦片属性", parent)
        self._ts: Tileset | None = None
        self.doc = None
        self.undo_stack = None

        self.panel = TilePropertyEditor()
        self.panel.changed.connect(self.changed.emit)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.panel)
        self.setWidget(holder)
        install_title_bar(self)

    def set_context(self, ts: Tileset, doc, undo_stack):
        self._ts = ts
        self.doc = doc
        self.undo_stack = undo_stack
        self.panel.set_context(doc, undo_stack)
        self.panel.set_tile(ts, -1)

    def set_tileset(self, ts: Tileset | None):
        self._ts = ts

    def select_tile(self, local_id: int):
        """由图集里的选格事件触发：把当前选中的瓦片交给内部编辑器。"""
        self.panel.set_tile(self._ts, local_id)
