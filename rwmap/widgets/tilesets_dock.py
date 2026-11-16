"""图块集面板：Tiled 风格 —— 顶部横向标签栏 + 整张图集原位置显示。

- 不再把瓦片切出来重排，直接把整张 PNG 按原始坐标画出来（含 margin/spacing），
  `trans` 色键（如 #008a76 青色）在 load_tileset_image 里已转 alpha，透明处露出面板底色；
- 叠加瓦片网格线，鼠标拖拽可框选一个矩形（多行多列），供画笔一次盖章；
- 配色与主窗口深色主题一致。
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDockWidget, QMenu, QScrollArea, QTabBar, QToolTip,
    QVBoxLayout, QWidget,
)

from ..tilesets.image_loader import image_grid, load_tileset_image
from ..tilesets.tile_cache import pil_to_qpixmap
from ..theme import ASH, INK, TEXT
from ..view.selection_style import SELECT_FILL as _SELECT_FILL, SELECT as _SELECT
from .dock_chrome import install_title_bar

# 深色主题，与图层/属性/单位面板一致；界面区分色收敛为 INK + ASH 双色。
_BG = QColor(INK)
_GRID = QColor(ASH)
_MISSING = QColor(ASH)
# 不再画鼠标悬停的 1×1 浅蓝小框（用户要求）；选中框仍保留，
# 颜色与地图虚影共用 selection_style，避免两处颜色漂移。

_TAB_STYLE = f"""
QTabBar {{ background: {ASH}; border-bottom: 1px solid {INK}; }}
QTabBar::tab {{
    background: {INK};
    border: 1px solid {INK};
    border-bottom: none;
    padding: 3px 10px 4px 10px;
    margin-right: 1px;
    color: {TEXT};
    font-size: 11px;
}}
QTabBar::tab:selected {{ background: {ASH}; color: {TEXT}; }}
QTabBar::tab:hover:!selected {{ background: rgba(36, 36, 36, 160); }}
QTabBar::scroller {{ width: 18px; }}
"""

_SCROLL_STYLE = f"""
QScrollArea {{ background: {INK}; border: none; }}
QScrollBar:vertical {{ background: {INK}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {ASH}; min-height: 20px; }}
QScrollBar:horizontal {{ background: {INK}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {ASH}; min-width: 20px; }}
"""

# 图块集视图里不叠加任何瓦片编号：整张图集按原位置显示，数字会挡住图块。
SHOW_TILE_IDS = False

_ATLAS_CACHE: dict[tuple, tuple[QPixmap, int, int]] = {}


def clear_atlas_cache() -> None:
    """换图 / 撤销换图后清空整图预览缓存，否则会一直显示旧图集。"""
    _ATLAS_CACHE.clear()


class TilePalette(QWidget):
    """整张图集视图：图块留在原始位置，支持拖拽框选矩形。"""

    tilePicked = Signal(int)                    # 单击：gid（兼容旧接口）
    rectSelected = Signal(int, int, int, int)  # c0, r0, c1, r1（瓦片坐标，含端点）
    settingsRequested = Signal(object)          # 右键「设置图集」：传出当前 Tileset

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ts = None
        self.doc = None
        self._pix: QPixmap | None = None
        self.atlas_w = 0
        self.atlas_h = 0
        self.columns = 1            # 图集列数
        self.rows = 0               # 图集行数
        self.cell_w = 20
        self.cell_h = 20
        # ---- 缩放 / 网格开关（Shift+滚轮缩放、右键菜单开关网格） ----
        self._zoom = 1.0
        self._show_grid = True
        self._native_w = 0
        self._native_h = 0
        self._tile_w = 20
        self._tile_h = 20
        self._spacing = 0
        self._margin = 0
        self._margin_x = 0
        self._margin_y = 0
        self.selected = -1          # 最近一次单击的 local_id
        self.hover = -1
        self.selection: tuple[int, int, int, int] | None = None
        self._anchor = None
        self._view_w = 0
        self._view_h = 0
        self._laid_out = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    # ---------------- 布局 ----------------
    def set_tileset(self, ts, doc):
        self.ts, self.doc = ts, doc
        self.selected = -1
        self.hover = -1
        self.selection = None
        self._anchor = None
        self._pix = None
        self.atlas_w = self.atlas_h = 0
        self.columns, self.rows = 1, 0
        self._native_w = 0
        self._native_h = 0
        self._tile_w = 20
        self._tile_h = 20
        self._spacing = 0
        self._margin = 0
        if ts is not None and doc is not None:
            key = (ts.firstgid, ts.name, ts.image_source, ts.trans,
                   ts.tile_w, ts.tile_h, ts.columns, ts.spacing, ts.margin,
                   len(ts.embedded_png or ""))
            entry = _ATLAS_CACHE.get(key)
            if entry is None:
                img = load_tileset_image(ts, doc.resolver)
                if img is not None:
                    entry = (pil_to_qpixmap(img), *image_grid(img, ts))
                    _ATLAS_CACHE[key] = entry
            if entry is not None and not entry[0].isNull():
                self._pix, self.columns, self.rows = entry
                self._native_w, self._native_h = self._pix.width(), self._pix.height()
                self._tile_w = max(1, ts.tile_w)
                self._tile_h = max(1, ts.tile_h)
                self._spacing = max(0, ts.spacing)
                self._margin = int(getattr(ts, "margin", 0) or 0)
        self._apply_zoom()
        self._laid_out = False
        self._relayout()

    def set_zoom(self, zoom: float):
        """Shift+滚轮调用的缩放入口；约束在 [0.25, 8]，几何按缩放重算。"""
        zoom = max(0.25, min(8.0, float(zoom)))
        if abs(zoom - self._zoom) < 1e-3:
            return
        self._zoom = zoom
        self._apply_zoom()
        self._laid_out = False
        self._relayout()
        self.update()

    def _apply_zoom(self):
        """把 native 图集参数按当前 _zoom 折算成绘制用几何（格距、图集尺寸、留白）。"""
        z = self._zoom
        self.cell_w = max(1, int((self._tile_w + self._spacing) * z))
        self.cell_h = max(1, int((self._tile_h + self._spacing) * z))
        self.atlas_w = int(self._native_w * z)
        self.atlas_h = int(self._native_h * z)
        self._margin_x = int(self._margin * z)
        self._margin_y = int(self._margin * z)

    def set_view_size(self, w: int, h: int):
        if self._laid_out and (w, h) == (self._view_w, self._view_h):
            return
        self._view_w, self._view_h = max(0, w), max(0, h)
        self._laid_out = False
        self._relayout()

    def _relayout(self):
        view_w = self._view_w or max(1, self.width())
        view_h = self._view_h or max(1, self.height())
        width = max(view_w, self.atlas_w)
        height = max(view_h, self.atlas_h)
        if self.width() != width or self.height() != height:
            self.setFixedSize(width, height)
        self._laid_out = True
        self.update()

    # ---------------- 坐标 ----------------
    def _margin(self) -> int:
        """返回 native 留白（兼容性保留）；绘制/命中统一用缩放后的 _margin_x/_margin_y。"""
        return int(getattr(self.ts, "margin", 0) or 0) if self.ts else 0

    def _tile_at(self, pos):
        if self.ts is None or not self.columns or not self.rows:
            return None
        x, y = int(pos.x()) - self._margin_x, int(pos.y()) - self._margin_y
        if x < 0 or y < 0:
            return None
        c, r = x // self.cell_w, y // self.cell_h
        if not (0 <= c < self.columns and 0 <= r < self.rows):
            return None
        return int(c), int(r)

    def _tile_rect(self, c: int, r: int) -> QRect:
        return QRect(self._margin_x + c * self.cell_w, self._margin_y + r * self.cell_h,
                     self.cell_w, self.cell_h)

    def _selection_rect(self) -> QRect | None:
        if self.selection is None:
            return None
        c0, r0, c1, r1 = self.selection
        a = self._tile_rect(c0, r0)
        b = self._tile_rect(c1, r1)
        return QRect(a.left(), a.top(), b.right() - a.left() + 1,
                     b.bottom() - a.top() + 1)

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), _BG)
        if self._pix is not None:
            # 缩放预览：把整张图集按 _zoom 等比拉伸绘制，坐标与缩放后几何一致。
            painter.setRenderHint(QPainter.SmoothPixmapTransform, self._zoom > 1)
            painter.drawPixmap(QRectF(0, 0, self.atlas_w, self.atlas_h),
                               self._pix,
                               QRectF(0, 0, self._pix.width(), self._pix.height()))
        # 网格（右键菜单可关闭）：描在缩放后的留白/格距上。
        if self._show_grid and self.atlas_w and self.atlas_h:
            painter.setPen(QPen(_GRID, 1))
            for c in range(self.columns + 1):
                x = self._margin_x + c * self.cell_w
                painter.drawLine(x, 0, x, self.atlas_h)
            for r in range(self.rows + 1):
                y = self._margin_y + r * self.cell_h
                painter.drawLine(0, y, self.atlas_w, y)
        # 悬停态只用于 tooltip，不再画 1×1 蓝色小框。
        sel = self._selection_rect()
        if sel is not None:
            painter.fillRect(sel, _SELECT_FILL)
            painter.setPen(QPen(_SELECT, 0.6))
            painter.drawRect(sel.adjusted(1, 1, -1, -1))
        if SHOW_TILE_IDS:               # 默认关闭；调试时才会叠加瓦片编号
            self._draw_tile_ids(painter, event.rect())
        painter.end()

    def _draw_tile_ids(self, painter, visible):
        """仅调试用：把 local_id 小字叠在每格左上角。正式界面始终关闭。"""
        painter.save()
        painter.setPen(QPen(QColor(TEXT), 1))
        for r in range(self.rows):
            for c in range(self.columns):
                box = self._tile_rect(c, r)
                if not visible.intersects(box):
                    continue
                painter.drawText(box.left() + 1, box.top() + 9,
                                 str(r * self.columns + c))
        painter.restore()

    def hover_text(self, local_id: int) -> str:
        """悬停提示只有图集名和自定义属性，不出现 gid / local_id 编号。"""
        props = dict(self.ts.tile_props(local_id).items()) if self.ts else {}
        text = (self.ts.name if self.ts and self.ts.name else "图块集")
        if props:
            text += "\n" + "  ".join(f"{k}={v}" for k, v in props.items())
        return text

    # ---------------- 交互 ----------------
    def contextMenuEvent(self, event):
        """右键菜单：切换网格、重置缩放、设置图集。"""
        menu = QMenu(self)
        grid_act = menu.addAction("显示网格")
        grid_act.setCheckable(True)
        grid_act.setChecked(self._show_grid)
        grid_act.triggered.connect(self._toggle_grid)
        reset_act = menu.addAction("重置缩放 (100%)")
        reset_act.triggered.connect(lambda: self.set_zoom(1.0))
        zoom_in = menu.addAction("放大")
        zoom_in.triggered.connect(lambda: self.set_zoom(self._zoom * 1.25))
        zoom_out = menu.addAction("缩小")
        zoom_out.triggered.connect(lambda: self.set_zoom(self._zoom / 1.25))
        menu.addSeparator()
        settings_act = menu.addAction("设置图集…")
        settings_act.setEnabled(self.ts is not None)
        settings_act.triggered.connect(
            lambda: self.settingsRequested.emit(self.ts) if self.ts is not None else None)
        menu.exec(event.globalPos())

    def _toggle_grid(self, checked: bool | None = None):
        self._show_grid = (not self._show_grid) if checked is None else bool(checked)
        self.update()

    def _emit_selection(self):
        if self.selection is None or self.ts is None:
            return
        c0, r0, c1, r1 = self.selection
        self.selected = r0 * self.columns + c0
        self.tilePicked.emit(int(self.ts.firstgid + self.selected))
        self.rectSelected.emit(c0, r0, c1, r1)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        tile = self._tile_at(event.position())
        if tile is None:
            return
        self._anchor = tile
        self.selection = (tile[0], tile[1], tile[0], tile[1])
        self.update()

    def mouseMoveEvent(self, event):
        tile = self._tile_at(event.position())
        hover = -1 if tile is None else tile[1] * self.columns + tile[0]
        if hover != self.hover:
            self.hover = hover
            self.update()
        if self._anchor is not None and event.buttons() & Qt.LeftButton:
            c, r = tile if tile is not None else self._anchor
            a = self._anchor
            self.selection = (min(a[0], c), min(a[1], r), max(a[0], c), max(a[1], r))
            self.update()
        if tile is not None and self.ts is not None:
            local_id = tile[1] * self.columns + tile[0]
            QToolTip.showText(event.globalPosition().toPoint(),
                              self.hover_text(local_id), self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._anchor is not None:
            self._anchor = None
            self._emit_selection()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self.hover >= 0:
            self.hover = -1
            self.update()
        QToolTip.hideText()
        super().leaveEvent(event)


class _AtlasScrollArea(QScrollArea):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        pal = self.widget()
        if isinstance(pal, TilePalette):
            pal.set_view_size(self.viewport().width(), self.viewport().height())

    def wheelEvent(self, event):
        # 按住 Shift + 滚轮 = 缩放图集预览（其它修饰键或普通滚轮照常滚动）。
        if event.modifiers() & Qt.ShiftModifier and isinstance(self.widget(), TilePalette):
            delta = event.angleDelta().y()
            if delta:
                pal = self.widget()
                pal.set_zoom(pal._zoom * (1.15 if delta > 0 else 1 / 1.15))
                event.accept()
                return
        super().wheelEvent(event)


class TilesetsDock(QDockWidget):
    gidSelected = Signal(int)
    tileSelected = Signal(object, int, object)     # tileset, local_id, props
    stampSelected = Signal(object)                 # 2D gid 数组（矩形选区）
    settingsRequested = Signal(object)              # 右键「设置图集」：传出当前 Tileset
    tilesetChanged = Signal(object)                # 当前图集随标签切换而变更：传出新 Tileset

    def __init__(self, parent=None):
        super().__init__("图块集", parent)
        self.doc = None

        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setMovable(False)
        self.tabs.setTabsClosable(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideNone)
        self.tabs.setStyleSheet(_TAB_STYLE)

        self.combo = QComboBox()          # 旧调用方的隐藏数据镜像
        self.combo.hide()

        self.palette = TilePalette()
        self.scroll = _AtlasScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setWidget(self.palette)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(_SCROLL_STYLE)
        self.scroll.viewport().setStyleSheet(f"background: {INK};")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs)
        layout.addWidget(self.scroll, 1)
        holder = QWidget()
        holder.setLayout(layout)
        holder.setStyleSheet(f"background: {INK};")
        self.setWidget(holder)
        install_title_bar(self)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        self.palette.tilePicked.connect(self._on_pick)
        self.palette.rectSelected.connect(self._on_rect)
        self.palette.settingsRequested.connect(
            lambda ts: self.settingsRequested.emit(ts))

    # ---------------- 文档 ----------------
    def set_document(self, doc):
        self.doc = doc
        self.tabs.blockSignals(True)
        self.combo.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.combo.clear()
        if doc is not None:
            for ts in doc.tilesets:
                label = ts.name or ts.source or "tileset"
                index = self.tabs.addTab(label)
                self.tabs.setTabData(index, int(ts.firstgid))
                self.tabs.setTabToolTip(
                    index, f"{label}\n{ts.count} 块 · {ts.tile_w}x{ts.tile_h}")
                self.combo.addItem(label, int(ts.firstgid))
        self.tabs.blockSignals(False)
        self.combo.blockSignals(False)
        if self.tabs.count():
            self.tabs.setCurrentIndex(0)
            self._on_tab_changed(0)
        else:
            self.palette.set_tileset(None, None)
            self.scroll.verticalScrollBar().setValue(0)

    # ---------------- 选择 ----------------
    def _tileset_at(self, index: int):
        if self.doc is None or index < 0:
            return None
        firstgid = self.tabs.tabData(index)
        return next((t for t in self.doc.tilesets if t.firstgid == firstgid), None)

    def current_tileset(self):
        return self._tileset_at(self.tabs.currentIndex())

    def current_firstgid(self):
        return self.tabs.tabData(self.tabs.currentIndex())

    def select_tileset(self, firstgid: int) -> bool:
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) == firstgid:
                self.tabs.setCurrentIndex(i)
                return True
        return False

    def select_gid(self, gid: int) -> bool:
        """按 gid 切到对应图块集并高亮那一格（临时吸管的同步选中态）。"""
        if self.doc is None:
            return False
        located = self.doc.local_tile(int(gid))
        if located is None:
            return False
        ts, local_id = located
        if not self.select_tileset(int(ts.firstgid)):
            return False
        # 切 tab 会重建 palette 并清空 selection，所以高亮要放在切换之后。
        columns = max(1, self.palette.columns)
        c, r = local_id % columns, local_id // columns
        self.palette.selected = local_id
        self.palette.selection = (c, r, c, r)
        self.palette.update()
        # 滚动到目标瓦片，让被拾取/选中的瓦片进入可视区域。
        tile_rect = self.palette._tile_rect(c, r)
        self.scroll.ensureVisible(
            tile_rect.x(), tile_rect.y(), tile_rect.width(), tile_rect.height())
        return True

    def _on_tab_changed(self, index: int):
        if self.combo.currentIndex() != index:
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(index)
            self.combo.blockSignals(False)
        ts = self._tileset_at(index)
        self.palette.set_tileset(ts, self.doc)
        self.scroll.verticalScrollBar().setValue(0)
        self.scroll.horizontalScrollBar().setValue(0)
        self.tilesetChanged.emit(ts)

    def _on_combo_changed(self, index: int):
        if self.tabs.currentIndex() != index:
            self.tabs.setCurrentIndex(index)

    def _load_tiles(self, index: int):          # 兼容旧方法名
        if index != self.tabs.currentIndex():
            self.tabs.setCurrentIndex(index)
        else:
            self._on_tab_changed(index)

    def _on_pick(self, gid: int):
        ts = self.current_tileset()
        self.gidSelected.emit(gid)
        if ts is not None:
            local_id = gid - ts.firstgid
            self.tileSelected.emit(ts, local_id, ts.tile_props(local_id))

    def _on_rect(self, c0: int, r0: int, c1: int, r1: int):
        """把矩形选区转成 2D gid 数组，供画笔一次盖章。"""
        ts = self.current_tileset()
        if ts is None:
            return
        cols = self.palette.columns
        gids = []
        for r in range(r0, r1 + 1):
            row = []
            for c in range(c0, c1 + 1):
                local_id = r * cols + c
                row.append(int(ts.firstgid + local_id) if local_id < ts.count else 0)
            gids.append(row)
        self.stampSelected.emit(gids)
