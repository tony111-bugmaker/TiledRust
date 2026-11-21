"""图集编辑器：只读图集视图（点击选瓦片）+ 每瓦片属性编辑器。

与地图编辑器不同，这里**不画画**，只做一件事：调每个瓦片的属性
（与 Tiled 里双击瓦片编辑 tile properties 等价）。适合给铁锈的地形/单位
图集逐格标注属性。

右侧属性面板模仿触发器节点图的 TriggerInspector：分组标题 + 中文标签 +
点选控件，键名一律不用手打（只有自定义属性需要自己输键名）。
顶部按「地形 / 单位 / 其他」三个类别分页：
* 一个瓦片不能同时是地形又是单位 —— 写入某一类会自动清掉另一类；
* 地形类自身也是互斥单选，选一个自动替换同类其它键；
* 「其他」放自定义 / 未知属性，可自由叠加、改键名、加自定义键。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..commands.tileset_command import snapshot
from ..document.properties import PropertyBag
from ..document.tileset import Tileset
from ..tilesets.image_loader import load_tileset_image
from ..tilesets.tile_cache import pil_to_qpixmap
from ..view.selection_style import SELECT as _SELECT, SELECT_FILL as _SELECT_FILL
from .tile_property_defs import (
    CAT_OTHER, CAT_TERRAIN, CAT_UNIT, CATEGORY_LABELS, CATEGORY_ORDER,
    CATEGORY_TIPS, KNOWN_TILE_KEYS, TERRAIN_KEYS, TERRAIN_LABELS, TERRAIN_PROPS,
    VALUE_LABELS, VALUE_PRESETS, VALUE_PROPS,
    category_of, conflicting_keys, cross_category_conflicts,
    detect_category, other_category_keys,
)
from .trigger_inspector import THEME
from ..theme import ASH, INK, TEXT, TEXT_DIM, WIN11_BLUE


_BG = QColor(INK)
_GRID = QColor(ASH)
# 瓦片分界线：旧版用 ASH(#2a2e34) 画在深绿/深红图集上几乎看不见，
# 换成半透明浅灰，两种底色的图集都能看清；保留 _GRID 供主题检查用。
_GRID_TILE = QColor(216, 216, 216, 128)
# 间距/边距空隙的提示底色（只压暗一点点，不盖住图集内容）。
_GRID_GAP = QColor(216, 216, 216, 26)

# 单选按钮的选中圆球用 Windows 11 默认强调蓝；复选框仍是双色灰阶。
_RADIO_CHECKED_STYLE = (
    f"QRadioButton::indicator:checked{{background:{WIN11_BLUE};"
    f"border:1px solid {WIN11_BLUE};}}"
)


def _clear_layout(layout):
    """递归清空布局并销毁其中的控件（重建面板时用）。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.blockSignals(True)
            sub = widget.layout()
            if sub is not None:
                _clear_layout(sub)
            # 先隐藏再摘父级：可见控件 setParent(None) 会瞬间变成顶层窗口，
            # Windows 给它配无标题的系统三键标题栏，一闪而过（空窗假象）。
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


class TilesetAtlasView(QWidget):
    """只读图集：整张图按原位置显示，点击选一格瓦片（不画画）。

    交互：滚轮以光标为中心缩放；按住鼠标中键拖动平移画面（通过外层滚动区）。
    """

    tileClicked = Signal(int)  # local_id
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ts: Tileset | None = None
        self._pix: QPixmap | None = None
        self._zoom = 1.0
        self.columns = 1
        self.rows = 0
        self.cell_w = 20
        self.cell_h = 20
        self._tile_w = 20
        self._tile_h = 20
        self._spacing = 0
        self._margin = 0
        self._margin_x = 0
        self._margin_y = 0
        self._native_w = 0
        self._native_h = 0
        self.selected = -1
        self.hover = -1
        self._panning = False
        self._pan_start = (0.0, 0.0)
        self._pan_sb_h = 0
        self._pan_sb_v = 0
        self._pan_scroll = None       # 外层滚动区，用于中键平移
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def set_pan_scroll(self, scroll):
        """把外层滚动区登记进来，中键拖动时改它的滚动条。"""
        self._pan_scroll = scroll

    # ---------------- 数据 ----------------
    def set_tileset(self, ts: Tileset, doc):
        self.ts = ts
        self.selected = -1
        self.hover = -1
        self._pix = None
        self.columns, self.rows = 1, 0
        if ts is not None:
            from PIL import Image

            img = None
            # 独立 .tsx 文档：新建图集后还没落盘，图片只记录在 doc.image_path 上，
            # 此时 ts.base_dir 仍是默认的 "."，靠 load_tileset_image 会读不到图。
            p = getattr(doc, "image_path", None) if doc is not None else None
            if p is not None and Path(p).is_file():
                try:
                    img = Image.open(Path(p)).convert("RGBA")
                except OSError:
                    img = None
            if img is None:
                img = load_tileset_image(ts, getattr(doc, "resolver", None)) if doc else None
            if img is None and ts.image_source:
                q = Path(ts.base_dir or Path(".")) / ts.image_source
                if q.is_file():
                    try:
                        img = Image.open(q).convert("RGBA")
                    except OSError:
                        img = None
            if img is not None:
                self._pix = pil_to_qpixmap(img)
                self.columns = max(1, ts.columns)
                self._tile_w = max(1, ts.tile_w)
                self._tile_h = max(1, ts.tile_h)
                self._spacing = max(0, ts.spacing)
                self._margin = int(getattr(ts, "margin", 0) or 0)
                # 行数必须按图片尺寸算出来：漏了它 `_tile_at` 只允许 r=0，
                # 第二行往后的瓦片点不中，横向网格线也画不全。
                step_x = max(1, self._tile_w + self._spacing)
                step_y = max(1, self._tile_h + self._spacing)
                self.rows = max(0, (self._pix.height() - 2 * self._margin
                                   + self._spacing) // step_y)
                if ts.columns <= 0:
                    self.columns = max(1, (self._pix.width() - 2 * self._margin
                                          + self._spacing) // step_x)
            self._native_w = self._pix.width() if self._pix else 0
            self._native_h = self._pix.height() if self._pix else 0
        self._apply_zoom()
        self.setFixedSize(max(1, self.atlas_w), max(1, self.atlas_h))
        self.update()

    def _apply_zoom(self):
        z = self._zoom
        self.cell_w = max(1, int((self._tile_w + self._spacing) * z))
        self.cell_h = max(1, int((self._tile_h + self._spacing) * z))
        self.atlas_w = int(self._native_w * z)
        self.atlas_h = int(self._native_h * z)
        self._margin_x = int(self._margin * z)
        self._margin_y = int(self._margin * z)

    def set_zoom(self, zoom: float):
        z = max(0.25, min(8.0, float(zoom)))
        if z == self._zoom:
            return
        self._zoom = z
        self._apply_zoom()
        self.setFixedSize(max(1, self.atlas_w), max(1, self.atlas_h))
        self.update()
        self.zoomChanged.emit(z)

    # ---------------- 坐标 ----------------
    def _nx(self, native_x: float) -> float:
        """图集源像素横坐标 → 屏幕横坐标（与 drawPixmap 的拉伸完全一致）。"""
        if self._native_w:
            return native_x * (self.atlas_w / self._native_w)
        return native_x

    def _ny(self, native_y: float) -> float:
        if self._native_h:
            return native_y * (self.atlas_h / self._native_h)
        return native_y

    def _col_x(self, c: int) -> int:
        """第 c 块瓦片的左边界屏幕 x（= 边距 + c×(瓦片宽+间距)）。"""
        step = self._tile_w + self._spacing
        return int(round(self._nx(self._margin + c * step)))

    def _row_y(self, r: int) -> int:
        """第 r 块瓦片的上边界屏幕 y（= 边距 + r×(瓦片高+间距)）。"""
        step = self._tile_h + self._spacing
        return int(round(self._ny(self._margin + r * step)))

    def _col_right(self, c: int) -> int:
        """第 c 块瓦片的右边界屏幕 x（左边界 + 一个瓦片宽，不含间距）。"""
        step = self._tile_w + self._spacing
        return int(round(self._nx(self._margin + c * step + self._tile_w)))

    def _row_bottom(self, r: int) -> int:
        """第 r 块瓦片的下边界屏幕 y（上边界 + 一个瓦片高，不含间距）。"""
        step = self._tile_h + self._spacing
        return int(round(self._ny(self._margin + r * step + self._tile_h)))

    def _tile_at(self, pos):
        if self.ts is None or not self.columns or not self.rows:
            return None
        if not self._native_w or not self._native_h:
            return None
        # 屏幕坐标反推回源像素坐标（forward 映射的连续逆运算，不受取整影响），
        # 再按瓦片步长求行列——与绘制网格用的 _col_x / _row_y 严格一致。
        nx = pos.x() * self._native_w / self.atlas_w - self._margin
        ny = pos.y() * self._native_h / self.atlas_h - self._margin
        if nx < 0 or ny < 0:
            return None
        step_x = self._tile_w + self._spacing
        step_y = self._tile_h + self._spacing
        c = int(nx) // step_x
        r = int(ny) // step_y
        if not (0 <= c < self.columns and 0 <= r < self.rows):
            return None
        return r * self.columns + c

    def _tile_rect(self, local_id: int):
        c = local_id % self.columns
        r = local_id // self.columns
        x0 = self._col_x(c)
        y0 = self._row_y(r)
        # 选中框只框瓦片本身（左..右 / 上..下），不含后面的间距空隙。
        return (x0, y0, self._col_right(c) - x0, self._row_bottom(r) - y0)

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), _BG)
        if self._pix is not None and not self._pix.isNull():
            # 放大（zoom>=1）用最近邻：每个源像素是清晰方块，像素明显可见；
            # 只有缩小（zoom<1）才平滑，避免锯齿。瓦片边界灰线位置不变。
            painter.setRenderHint(QPainter.SmoothPixmapTransform, self._zoom < 1)
            painter.drawPixmap(0, 0, self.atlas_w, self.atlas_h, self._pix)
        if self._pix is not None and self.atlas_w and self.atlas_h:
            # 瓦片分界线：把「瓦片本体」和「间距/边距空隙」分开画。
            #
            # 旧版每格只画一条线，落在 margin + c×(tile+spacing)（即每块左边界），
            # 于是 20px 瓦片连同后面的 1px 间距被当成同一个 21px 格子，间距那条
            # 空隙完全没被表示出来——看起来就像「分界线没算间距和边距」。
            # 现在：边距带 + 每块瓦片的左右/上下边界 + 间距带，都按 native 坐标
            # 经 _nx/_ny 映射，保证与图片缩放位置一致、高倍放大不漂移。
            # ① 边距带（图片边缘 → 第一块瓦片）和间距带（每块瓦片之间）淡色提示
            if self._margin_x > 0:
                painter.fillRect(0, 0, self._margin_x, self.atlas_h, _GRID_GAP)
                painter.fillRect(0, 0, self.atlas_w, self._margin_y, _GRID_GAP)
            if self._spacing > 0:
                for c in range(self.columns):
                    x0, x1 = self._col_right(c), self._col_x(c + 1)
                    if x1 > x0:
                        painter.fillRect(x0, 0, x1 - x0, self.atlas_h, _GRID_GAP)
                for r in range(self.rows):
                    y0, y1 = self._row_bottom(r), self._row_y(r + 1)
                    if y1 > y0:
                        painter.fillRect(0, y0, self.atlas_w, y1 - y0, _GRID_GAP)
            # ② 每块瓦片本体四边：左/右、上/下边界各一条
            painter.setPen(QPen(_GRID_TILE, 1))
            for c in range(self.columns):
                xl, xr = self._col_x(c), self._col_right(c)
                painter.drawLine(xl, 0, xl, self.atlas_h)
                painter.drawLine(xr, 0, xr, self.atlas_h)
            for r in range(self.rows):
                yt, yb = self._row_y(r), self._row_bottom(r)
                painter.drawLine(0, yt, self.atlas_w, yt)
                painter.drawLine(0, yb, self.atlas_w, yb)
        # 仅显示「已点击选中」的瓦片（蓝色填充 + 蓝边）；不再画「鼠标悬停」的
        # 浅蓝预览框（用户要求去掉悬停蓝框）。
        if self.selected >= 0:
            x, y, w, h = self._tile_rect(self.selected)
            painter.fillRect(x, y, w, h, _SELECT_FILL)
            painter.setPen(QPen(_SELECT, 1.5))
            painter.drawRect(x, y, w - 1, h - 1)
        painter.end()

    # ---------------- 交互 ----------------
    def wheelEvent(self, event):
        """滚轮以光标为中心缩放（仅图集画布，不影响面板滚动）。"""
        if self.ts is None or self._pan_scroll is None:
            return super().wheelEvent(event)
        sb_h = self._pan_scroll.horizontalScrollBar()
        sb_v = self._pan_scroll.verticalScrollBar()
        ax = event.position().x()
        ay = event.position().y()
        old_zoom = self._zoom
        factor = 1.1 if event.angleDelta().y() > 0 else 1.0 / 1.1
        new_zoom = max(0.25, min(8.0, old_zoom * factor))
        if new_zoom == old_zoom:
            event.accept()
            return
        self.set_zoom(new_zoom)
        # 让光标下的 native 点保持不动：缩放后该点的 atlas 本地坐标 = (ax/old)*new，
        # 期望它仍在原视口位置 vp = ax - sb_old => 新滚动 = 该本地坐标 - vp。
        vp_x = ax - sb_h.value()
        vp_y = ay - sb_v.value()
        sb_h.setValue(int((ax / old_zoom) * new_zoom - vp_x))
        sb_v.setValue(int((ay / old_zoom) * new_zoom - vp_y))
        event.accept()

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_scroll is not None:
            dx = event.position().x() - self._pan_start[0]
            dy = event.position().y() - self._pan_start[1]
            self._pan_scroll.horizontalScrollBar().setValue(self._pan_sb_h - int(dx))
            self._pan_scroll.verticalScrollBar().setValue(self._pan_sb_v - int(dy))
            event.accept()
            return
        tid = self._tile_at(event.position())
        h = -1 if tid is None else tid
        if h != self.hover:
            self.hover = h
            self.update()
        return super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            # 中键按住 = 平移：记录起点与当前滚动位置。
            self._panning = True
            self._pan_start = (event.position().x(), event.position().y())
            if self._pan_scroll is not None:
                self._pan_sb_h = self._pan_scroll.horizontalScrollBar().value()
                self._pan_sb_v = self._pan_scroll.verticalScrollBar().value()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        tid = self._tile_at(event.position())
        if tid is None:
            return
        self.selected = tid
        self.tileClicked.emit(tid)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        return super().mouseReleaseEvent(event)


class TilePropertyEditor(QWidget):
    """类别分页的点选式瓦片属性面板。

    * 顶部「地形 / 单位 / 其他」三选一，选中哪个只显示哪个下面的属性；
    * 地形与单位语义互斥：写入某一类会自动清掉另一类，杜绝一格既地形又单位；
    * 地形类自身互斥单选，单位 / 标记可多选，值从下拉预设里选（也允许自定义）；
    * 「其他」里能加自定义键名，未知键原样保留、可改键名 / 值 / 删除；
    * 「更多属性」从已知键名下拉里点选添加，杜绝拼错。
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TilePropertyEditor")
        self.ts: Tileset | None = None
        self.tdoc = None                  # TilesetDocument：撤销命令要写它的 dirty
        self.undo_stack = None            # QUndoStack（不传则就地生效、不入栈）
        self.local_id = -1
        self._loading = False
        self.category = CAT_TERRAIN
        self._committed = None            # 上一次「已入栈」的整表快照
        self._restoring = False           # 撤销 / 重做回放中，别再压新命令
        # 袋模式：编辑任意 PropertyBag（如「图集级」自定义属性），不绑定到某块瓦片。
        self._bag_mode = False
        self._standalone_bag: PropertyBag | None = None
        self._exclude: set[str] = set()

        self.title_label: QLabel | None = None
        self.cat_buttons: dict[str, QPushButton] = {}
        self.cat_group: QButtonGroup | None = None
        self.sections: dict[str, QWidget] = {}
        self.section_boxes: dict[str, QVBoxLayout] = {}
        self.terrain_radios: dict[str | None, QRadioButton] = {}
        self.terrain_group: QButtonGroup | None = None
        self.value_checks: dict[str, QCheckBox] = {}
        self.value_combos: dict[str, QComboBox] = {}
        self.extra_rows: dict[str, QLineEdit] = {}
        self.extra_key_rows: dict[str, QLineEdit] = {}
        self.conflict_bar: QFrame | None = None
        self.conflict_fix_btn: QPushButton | None = None
        self.more_combo: QComboBox | None = None
        self.more_btn: QPushButton | None = None
        self.custom_key: QLineEdit | None = None
        self.custom_value: QLineEdit | None = None
        self.custom_btn: QPushButton | None = None
        self.custom_hint: QLabel | None = None

        self.setStyleSheet(f"""
            QWidget#TilePropertyEditor{{background:{THEME['panel']};}}
            QScrollArea{{background:{THEME['panel']};border:0;}}
            QLabel{{color:{THEME['text']};font-size:12px;}}
            QRadioButton,QCheckBox{{color:{THEME['text']};font-size:12px;
                spacing:7px;padding:2px 0;background:transparent;}}
            QRadioButton::indicator{{width:13px;height:13px;border-radius:7px;
                border:1px solid {ASH};background:{THEME['input']};}}
            {_RADIO_CHECKED_STYLE}
            QCheckBox::indicator{{width:13px;height:13px;border-radius:3px;
                border:1px solid {ASH};background:{THEME['input']};}}
            QCheckBox::indicator:checked{{background:{THEME['accent']};
                border:1px solid {THEME['accent']};}}
            QComboBox,QLineEdit{{background:{THEME['input']};color:{THEME['text']};
                border:1px solid {INK};border-radius:4px;padding:3px 6px;font-size:12px;}}
            QComboBox:focus,QLineEdit:focus{{border:1px solid {THEME['accent']};}}
            QComboBox::drop-down{{border:0;}}
            QPushButton{{background:{ASH};color:{THEME['text']};border:1px solid {INK};
                border-radius:4px;padding:3px 9px;font-size:11px;}}
            QPushButton:hover{{background:rgba(36, 36, 36, 200);}}
            QPushButton#CatBtn{{background:{INK};color:{THEME['muted']};
                border:1px solid {INK};border-radius:4px;padding:5px 2px;font-size:11px;}}
            QPushButton#CatBtn:checked{{background:{ASH};color:{THEME['text']};
                border:1px solid {THEME['accent']};font-weight:bold;}}
            QPushButton#CatBtn:hover{{background:rgba(36, 36, 36, 160);}}
            QPushButton#CatBtn:checked:hover{{background:rgba(36, 36, 36, 220);}}
            QLabel#TileHint{{color:{THEME['muted']};font-size:10px;}}
            QScrollBar:vertical{{background:{THEME['panel']};width:8px;}}
            QScrollBar::handle:vertical{{background:{ASH};border-radius:4px;}}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setStyleSheet(f"background:{THEME['panel']};")
        self.vbox = QVBoxLayout(self.content)
        self.vbox.setContentsMargins(12, 10, 12, 12)
        self.vbox.setSpacing(4)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)
        self._render()

    # ---------------- 小工具 ----------------
    @staticmethod
    def _label(text: str, color: str | None = None, size: int = 11,
               bold: bool = False) -> QLabel:
        lab = QLabel(text)
        font = QFont(lab.font())
        font.setPointSize(size)
        font.setBold(bold)
        lab.setFont(font)
        lab.setStyleSheet(f"color:{color or THEME['muted']};font-size:{size}pt;")
        return lab

    def _group(self, text: str, box: QVBoxLayout | None = None):
        lab = self._label(text)
        lab.setStyleSheet(
            f"color:{TEXT_DIM};font-size:11px;border-bottom:1px solid {INK};"
            "padding-bottom:3px;margin-top:9px;")
        (box or self.vbox).addWidget(lab)

    def _hint(self, text: str, box: QVBoxLayout, color: str = TEXT_DIM):
        lab = self._label(text, color, 10)
        lab.setWordWrap(True)
        box.addWidget(lab)
        return lab

    def _clear_content(self):
        tracked = (list(self.cat_buttons.values())
                   + list(self.terrain_radios.values())
                   + list(self.value_checks.values())
                   + list(self.value_combos.values())
                   + list(self.extra_rows.values())
                   + list(self.extra_key_rows.values()))
        for widget in tracked:
            widget.blockSignals(True)
        for widget in (self.custom_key, self.custom_value, self.custom_btn,
                       self.more_combo, self.more_btn, self.conflict_fix_btn):
            if widget is not None:
                widget.blockSignals(True)
        _clear_layout(self.vbox)
        for group in (self.terrain_group, self.cat_group):
            if group is not None:
                group.blockSignals(True)
                group.deleteLater()
        self.title_label = None
        self.cat_buttons = {}
        self.cat_group = None
        self.sections = {}
        self.section_boxes = {}
        self.terrain_radios = {}
        self.terrain_group = None
        self.value_checks = {}
        self.value_combos = {}
        self.extra_rows = {}
        self.extra_key_rows = {}
        self.conflict_bar = None
        self.conflict_fix_btn = None
        self.more_combo = None
        self.more_btn = None
        self.custom_key = None
        self.custom_value = None
        self.custom_btn = None
        self.custom_hint = None

    # ---------------- 类别切换 ----------------
    def set_category(self, category: str):
        """只切换显示，不改动数据（点类别按钮不该悄悄删属性）。"""
        if category not in CATEGORY_ORDER:
            return
        self.category = category
        self._apply_category()

    def _apply_category(self):
        for cat, sec in self.sections.items():
            sec.setVisible(cat == self.category)
        btn = self.cat_buttons.get(self.category)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

    def _render_category_bar(self, keys: list[str]):
        counts = {cat: 0 for cat in CATEGORY_ORDER}
        for key in keys:
            counts[category_of(key)] += 1
        bar = QWidget()
        bar.setStyleSheet("background:transparent;")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 6, 0, 2)
        row.setSpacing(5)
        self.cat_group = QButtonGroup(self)
        self.cat_group.setExclusive(True)
        for cat in CATEGORY_ORDER:
            text = CATEGORY_LABELS[cat]
            if counts[cat]:
                text += f"  ·  {counts[cat]}"
            btn = QPushButton(text)
            btn.setObjectName("CatBtn")
            btn.setCheckable(True)
            btn.setToolTip(CATEGORY_TIPS[cat])
            self.cat_group.addButton(btn)
            btn.clicked.connect(
                lambda _checked=False, c=cat: self.set_category(c))
            row.addWidget(btn, 1)
            self.cat_buttons[cat] = btn
        self.vbox.addWidget(bar)
        self._hint("一个瓦片不能既是地形又是单位：选一类别后只显示该类属性，"
                   "设置时会自动清掉互斥的另一类。", self.vbox, TEXT_DIM)

    def _make_section(self, cat: str) -> QVBoxLayout:
        sec = QWidget()
        sec.setStyleSheet("background:transparent;")
        box = QVBoxLayout(sec)
        box.setContentsMargins(0, 2, 0, 0)
        box.setSpacing(4)
        self.vbox.addWidget(sec)
        self.sections[cat] = sec
        self.section_boxes[cat] = box
        return box

    @staticmethod
    def _cat_button_text(cat: str, count: int) -> str:
        return CATEGORY_LABELS[cat] + (f"  ·  {count}" if count else "")

    def _refresh_counts(self):
        """写回后同步类别按钮上的属性数，避免隐藏类别的计数过期。"""
        if self.ts is None or self.local_id < 0 or not self.cat_buttons:
            return
        keys = list(self.ts.tile_props(self.local_id).values)
        for cat, btn in self.cat_buttons.items():
            btn.setText(self._cat_button_text(
                cat, sum(1 for k in keys if category_of(k) == cat)))

    # ---------------- 渲染 ----------------
    def set_tile(self, ts: Tileset, local_id: int):
        self.ts = ts
        self.local_id = local_id
        self._bag_mode = False
        self._standalone_bag = None
        self._exclude = set()
        # 换瓦片时按它已有的属性推断该显示哪一页；只有未知键就直接进「其他」。
        if ts is None or local_id < 0:
            self.category = CAT_TERRAIN
        else:
            self.category = detect_category(list(ts.tile_props(local_id).values))
        self._render()

    def set_bag(self, bag: PropertyBag, exclude: list[str] | None = None):
        """袋模式：编辑任意 PropertyBag（如「图集级」自定义属性），不绑定到某块瓦片。

        复用与每瓦片属性完全相同的点选 / 增删 / 已知键下拉交互；地形 / 单位 /
        类别分页在此模式下隐藏，只显示「其它」式的键值编辑。`exclude` 里的键
        （如 Tiled 的绘制偏移 / 填充模式 / 背景色等元数据键）不在本面板出现，
        它们由「设置图集」对话框的专属字段控制。
        """
        self._bag_mode = True
        self._standalone_bag = bag
        self.ts = None
        self.local_id = -1
        self.category = CAT_OTHER
        self._exclude = set(exclude or [])
        self.set_context(None, None)
        self._render()

    # ---------------- 袋模式渲染 ----------------
    def _render_bag(self):
        self._clear_content()
        self._loading = True
        bag = self._standalone_bag
        if bag is None:
            self._loading = False
            return
        keys = [k for k in bag.values if k not in self._exclude]
        self.vbox.addWidget(
            self._label("图集级自定义属性", TEXT, 12, True))
        self._render_extras(bag, keys, self.vbox)
        self._render_custom(self.vbox)
        self._render_more(keys, self.vbox)
        self.vbox.addStretch(1)
        self._loading = False

    def _render(self):
        if self._bag_mode:
            self._render_bag()
            return
        self._clear_content()
        self._loading = True
        if self.ts is None or self.local_id < 0:
            lab = QLabel("点击左侧瓦片，再在这里选类别、点选属性")
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet(f"color:{TEXT_DIM};font-style:italic;font-size:12px;padding:24px 0;")
            self.vbox.addWidget(lab)
            self.vbox.addStretch(1)
            self._loading = False
            return

        bag = self.ts.tile_props(self.local_id)
        keys = list(bag.values)
        self.title_label = self._label(
            f"瓦片 #{self.local_id}  ·  {len(bag)} 个属性", TEXT, 12, True)
        self.vbox.addWidget(self.title_label)

        msgs = self._conflict_messages(keys)
        if msgs:
            self._render_conflict_bar(msgs)
        self._render_category_bar(keys)
        for cat in CATEGORY_ORDER:
            self._make_section(cat)

        self._render_terrain(bag, self.section_boxes[CAT_TERRAIN])
        self._render_values(bag, self.section_boxes[CAT_UNIT])
        extras = [k for k in keys if category_of(k) == CAT_OTHER]
        other_box = self.section_boxes[CAT_OTHER]
        self._render_extras(bag, extras, other_box)
        self._render_custom(other_box)
        self._render_more(keys, other_box)
        self.vbox.addStretch(1)
        self._apply_category()
        self._loading = False

    @staticmethod
    def _conflict_messages(keys: list[str]) -> list[str]:
        msgs: list[str] = []
        same = conflicting_keys(keys)
        if same:
            names = "、".join(TERRAIN_LABELS.get(k, k) for k in same)
            msgs.append(
                f"检测到 {len(same)} 个互斥地形属性同时存在：{names}。"
                "游戏可能只认其中一个。")
        cross = cross_category_conflicts(keys)
        if cross:
            t = "、".join(cross[CAT_TERRAIN])
            u = "、".join(cross[CAT_UNIT])
            msgs.append(
                f"地形（{t}）与单位（{u}）不能同时存在，"
                "游戏只会按其中一类处理。")
        return msgs

    def _render_conflict_bar(self, msgs: list[str]):
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame{{background:{INK};border:1px solid {ASH};border-radius:4px;}}")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        lab = self._label("<br>".join(msgs), TEXT, 10)
        lab.setWordWrap(True)
        row.addWidget(lab, 1)
        btn = QPushButton(f"按「{CATEGORY_LABELS[self.category]}」整理")
        btn.clicked.connect(self.normalize_conflicts)
        row.addWidget(btn, 0, Qt.AlignTop)
        self.vbox.addWidget(bar)
        self.conflict_bar = bar
        self.conflict_fix_btn = btn

    def _render_terrain(self, bag: PropertyBag, box: QVBoxLayout):
        self._group("地形 / 通行（单选 · 互斥）", box)
        self._hint("键存在即生效、值留空；选一项会自动替换同类其它项，"
                   "并移除单位 / 标记类属性。", box, TEXT_DIM)
        self.terrain_group = QButtonGroup(self)
        self.terrain_group.setExclusive(True)
        current = next((k for k in TERRAIN_KEYS if k in bag.values), None)

        none_radio = QRadioButton("普通陆地（不设置）")
        none_radio.setToolTip("不写任何地形属性，游戏按普通陆地处理")
        self.terrain_group.addButton(none_radio, 0)
        box.addWidget(none_radio)
        none_radio.toggled.connect(
            lambda checked, k=None: self._on_terrain(k, checked))
        self.terrain_radios[None] = none_radio

        for i, (key, label, tip) in enumerate(TERRAIN_PROPS, start=1):
            rb = QRadioButton(f"{label}  ·  {key}")
            rb.setToolTip(tip)
            self.terrain_group.addButton(rb, i)
            box.addWidget(rb)
            rb.toggled.connect(
                lambda checked, k=key: self._on_terrain(k, checked))
            self.terrain_radios[key] = rb

        # _loading 为 True，设定初态不会误触发写回
        (self.terrain_radios.get(current) or none_radio).setChecked(True)

    def _render_values(self, bag: PropertyBag, box: QVBoxLayout):
        self._group("单位 / 标记（可多选 · 值从下拉选）", box)
        self._hint("勾选任意一项时会自动移除地形类属性，避免一格又是地形又是单位。",
                   box, TEXT_DIM)
        for key, label, presets, tip in VALUE_PROPS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            cb = QCheckBox(f"{label}  ·  {key}")
            cb.setToolTip(tip)
            cb.setChecked(key in bag.values)
            cb.toggled.connect(
                lambda checked, k=key: self._on_value_toggled(k, checked))
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(list(presets))
            # 未勾选时显示空白，避免把「预设值」误读成「这个属性已存在」。
            if key in bag.values:
                combo.setCurrentText(bag.get(key, ""))
            else:
                combo.setCurrentIndex(-1)
            combo.setEnabled(key in bag.values)
            combo.setToolTip("从预设里选，或直接输入自定义值")
            combo.currentTextChanged.connect(
                lambda text, k=key: self._on_value_text(k, text))
            row.addWidget(cb, 1)
            row.addWidget(combo, 1)
            box.addLayout(row)
            self.value_checks[key] = cb
            self.value_combos[key] = combo

    def _render_extras(self, bag: PropertyBag, extras: list[str],
                       box: QVBoxLayout):
        self._group(f"其它属性（{len(extras)}）", box)
        if not extras:
            self._hint("无（自定义 / 未知键会原样保留，不会清洗）", box, TEXT_DIM)
        for key in extras:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            key_edit = QLineEdit(key)
            key_edit.setToolTip("键名可改；地形 / 单位的已知键请到对应类别里点选")
            key_edit.editingFinished.connect(
                lambda k=key: self._on_extra_key(k))
            val_edit = QLineEdit(bag.get(key, ""))
            val_edit.setToolTip("值可编辑；留空也是合法属性")
            val_edit.textChanged.connect(
                lambda text, k=key: self._on_extra_text(k, text))
            del_btn = QPushButton("×")
            del_btn.setFixedWidth(26)
            del_btn.setToolTip(f"删除属性 {key}")
            del_btn.clicked.connect(
                lambda _checked=False, k=key: self._remove_extra(k))
            row.addWidget(key_edit, 1)
            row.addWidget(val_edit, 1)
            row.addWidget(del_btn, 0)
            box.addLayout(row)
            self.extra_key_rows[key] = key_edit
            self.extra_rows[key] = val_edit

    def _render_custom(self, box: QVBoxLayout):
        self._group("添加自定义属性", box)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.custom_key = QLineEdit()
        self.custom_key.setPlaceholderText("自定义键名，如 myFlag")
        self.custom_value = QLineEdit()
        self.custom_value.setPlaceholderText("值（可留空）")
        row.addWidget(self.custom_key, 1)
        row.addWidget(self.custom_value, 1)
        box.addLayout(row)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        self.custom_btn = QPushButton("+ 添加自定义")
        self.custom_btn.clicked.connect(self._add_custom)
        row2.addWidget(self.custom_btn, 0)
        self.custom_hint = QLabel("")
        self.custom_hint.setObjectName("TileHint")
        self.custom_hint.setWordWrap(True)
        row2.addWidget(self.custom_hint, 1)
        box.addLayout(row2)
        self._hint("只有自定义键名需要打字；已知属性请到「地形 / 单位」里点选。",
                   box, TEXT_DIM)

    def _render_more(self, keys: list[str], box: QVBoxLayout):
        self._group("更多属性（从已知键名里选，避免拼错）", box)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        combo = QComboBox()
        for key in KNOWN_TILE_KEYS:
            if key in keys or key in self._exclude:
                continue
            label = TERRAIN_LABELS.get(key) or VALUE_LABELS.get(key, key)
            combo.addItem(f"{label}  ·  {key}", key)
        btn = QPushButton("+ 添加")
        btn.clicked.connect(self._add_from_known)
        btn.setEnabled(combo.count() > 0)
        row.addWidget(combo, 1)
        row.addWidget(btn, 0)
        box.addLayout(row)
        self._hint("添加地形 / 单位键后会自动切到对应类别并清掉互斥的另一类。",
                   box, TEXT_DIM)
        self.more_combo = combo
        self.more_btn = btn

    # ---------------- 数据写入 ----------------
    def _get_bag(self) -> PropertyBag | None:
        """取当前bag（不创建）：袋模式下返回独立bag，否则返回该瓦片已有的bag。"""
        if self._bag_mode:
            return self._standalone_bag
        if self.ts is None or self.local_id < 0:
            return None
        return self.ts.tiles.get(self.local_id)

    def _ensure_bag(self) -> PropertyBag | None:
        if self._bag_mode:
            return self._standalone_bag
        if self.ts is None or self.local_id < 0:
            return None
        bag = self.ts.tiles.get(self.local_id)
        if bag is None:
            bag = PropertyBag()
            self.ts.tiles[self.local_id] = bag
        return bag

    @staticmethod
    def _remove_key(bag: PropertyBag, key: str):
        bag.values.pop(key, None)
        bag.types.pop(key, None)
        bag.text_props.discard(key)

    def _enforce_category(self, bag: PropertyBag, category: str):
        """写入地形 / 单位前，清掉互斥的另一类键。"""
        for key in other_category_keys(category):
            self._remove_key(bag, key)

    def _cleanup_bag(self):
        if self.ts is None or self.local_id < 0:
            return
        bag = self.ts.tiles.get(self.local_id)
        if bag is not None and not bag:
            self.ts.tiles.pop(self.local_id, None)

    def _update_title(self):
        if self.title_label is not None and self.ts is not None:
            bag = self.ts.tile_props(self.local_id)
            self.title_label.setText(
                f"瓦片 #{self.local_id}  ·  {len(bag)} 个属性")

    def _sync_widgets(self):
        """互斥清理后同步另一类控件的勾选状态，避免界面与数据不一致。

        例如在「地形」页选 water 会删掉 team，此时「单位」页的 team 复选框
        必须自动取消勾选；反之亦然。全程 blockSignals，不再触发写回。
        """
        if self.ts is None or self.local_id < 0:
            return
        bag = self.ts.tile_props(self.local_id)
        for key, cb in self.value_checks.items():
            present = key in bag.values
            combo = self.value_combos.get(key)
            cb.blockSignals(True)
            cb.setChecked(present)
            cb.blockSignals(False)
            if combo is not None:
                combo.blockSignals(True)
                combo.setEnabled(present)
                if present:
                    combo.setCurrentText(bag.get(key, ""))
                else:
                    combo.setCurrentIndex(-1)
                combo.blockSignals(False)
        current = next((k for k in TERRAIN_KEYS if k in bag.values), None)
        target = self.terrain_radios.get(current) or self.terrain_radios.get(None)
        if target is not None and not target.isChecked():
            for rb in self.terrain_radios.values():
                rb.blockSignals(True)
            target.setChecked(True)
            for rb in self.terrain_radios.values():
                rb.blockSignals(False)

    # ---------------- 撤销：整表快照对比 ----------------
    def set_context(self, tdoc, undo_stack=None):
        """绑定图集文档与撤销栈，并把当前内容记为「已提交」基线。"""
        self.tdoc = tdoc
        self.undo_stack = undo_stack
        self._committed = snapshot(tdoc) if tdoc is not None else None

    def _push_edit(self, text: str, merge_key: str | None = None) -> None:
        """把「上次提交的快照 → 当前内容」登记成一条撤销命令。

        所有写回入口最后都会走到 ``_after_write``，所以这里只做一次 diff：
        内容没变就不产生命令（避免点一下已选中的项也留一条空撤销）。
        ``merge_key`` 相同的连续命令会合并（输入框逐字符写回只算一次）。
        """
        if self._restoring or self.tdoc is None:
            return
        after = snapshot(self.tdoc)
        before = self._committed
        if before is None or after == before:
            self._committed = after
            return
        from ..commands.tileset_command import TilesetCommand
        cmd = TilesetCommand(self.tdoc, before, after, text, self._replay,
                             merge_key=merge_key)
        self._committed = after
        if self.undo_stack is not None:
            self.undo_stack.push(cmd)
        else:
            self.doc_changed()

    def _replay(self) -> None:
        """撤销 / 重做之后：同步界面（不再压新命令）。"""
        self._restoring = True
        try:
            self._committed = snapshot(self.tdoc) if self.tdoc is not None else None
            self._sync_widgets()
            self._update_title()
            self._refresh_counts()
            self.doc_changed()
        finally:
            self._restoring = False

    def doc_changed(self) -> None:
        if self.tdoc is not None:
            self.tdoc.dirty = True
        self.changed.emit()

    def _after_write(self, text: str = "编辑瓦片属性",
                     merge_key: str | None = None):
        self._cleanup_bag()
        self._sync_widgets()
        if self.conflict_bar is not None:
            self.conflict_bar.setVisible(False)
        self._update_title()
        self._refresh_counts()
        self._push_edit(text, merge_key)
        self.doc_changed()

    def _on_terrain(self, key: str | None, checked: bool):
        if self._loading or not checked:
            return
        bag = self._ensure_bag()
        if bag is None:
            return
        # 地形类内部互斥：先清掉同类所有键，再写入选中的那个（值留空）
        for other in TERRAIN_KEYS:
            self._remove_key(bag, other)
        if key:
            # 地形与单位互斥
            self._enforce_category(bag, CAT_TERRAIN)
            bag.set(key, "")
        self._after_write("设置地形属性")

    def _on_value_toggled(self, key: str, checked: bool):
        if self._loading:
            return
        bag = self._ensure_bag()
        if bag is None:
            return
        combo = self.value_combos.get(key)
        if combo is not None:
            combo.setEnabled(checked)
        if checked:
            presets = VALUE_PRESETS.get(key, ())
            val = combo.currentText() if combo is not None else ""
            if not val:
                val = presets[0] if presets else ""
                if combo is not None:
                    # 自动填预设值时不触发 combo 的 currentTextChanged，
                    # 否则会多记一条「改值」撤销命令。
                    combo.blockSignals(True)
                    combo.setCurrentText(val)
                    combo.blockSignals(False)
            self._enforce_category(bag, CAT_UNIT)
            bag.set(key, val)
        else:
            self._remove_key(bag, key)
            if combo is not None:
                combo.setCurrentIndex(-1)
        self._after_write("设置单位属性")

    def _on_value_text(self, key: str, text: str):
        if self._loading or self.ts is None:
            return
        bag = self.ts.tiles.get(self.local_id)
        if bag is None or key not in bag.values:
            return
        bag.set(key, text)
        self._after_write("修改属性值", merge_key=f"值:{self.local_id}:{key}")

    def _on_extra_text(self, key: str, text: str):
        if self._loading:
            return
        bag = self._get_bag()
        if bag is None or key not in bag.values:
            return
        bag.set(key, text)
        self._after_write("修改属性值", merge_key=f"值:{self.local_id}:{key}")

    def _on_extra_key(self, old_key: str):
        """自定义 / 未知属性改键名：校验后迁移，不残留旧键。"""
        if self._loading:
            return
        edit = self.extra_key_rows.get(old_key)
        if edit is None:
            return
        bag = self._get_bag()
        if bag is None or old_key not in bag.values:
            return
        new_key = edit.text().strip()
        if not new_key or new_key == old_key:
            edit.setText(old_key)
            return
        if new_key in bag.values or new_key in KNOWN_TILE_KEYS:
            reason = ("该键名已存在" if new_key in bag.values
                      else "这是已知属性，请到「地形 / 单位」类别里点选")
            if self.custom_hint is not None:
                self.custom_hint.setText(f"改键名失败：{reason}")
            edit.setText(old_key)
            return
        bag.values[new_key] = bag.values.pop(old_key)
        if old_key in bag.types:
            bag.types[new_key] = bag.types.pop(old_key)
        if old_key in bag.text_props:
            bag.text_props.discard(old_key)
            bag.text_props.add(new_key)
        self._after_write("重命名属性")
        self._render()

    def _remove_extra(self, key: str):
        bag = self._get_bag()
        if bag is None:
            return
        self._remove_key(bag, key)
        self._after_write(f"删除属性 {key}")
        self._render()

    def _add_custom(self):
        """「其他」类别里添加完全自定义的键名 + 值。"""
        if self._loading or self.custom_key is None or self.custom_value is None:
            return
        key = self.custom_key.text().strip()
        value = self.custom_value.text()
        hint = self.custom_hint
        if not key:
            if hint is not None:
                hint.setText("键名不能为空")
            return
        if key in KNOWN_TILE_KEYS:
            if hint is not None:
                hint.setText("这是已知属性，请到「地形 / 单位」类别里点选")
            return
        bag = self._ensure_bag()
        if bag is None:
            return
        if key in bag.values:
            if hint is not None:
                hint.setText(f"属性「{key}」已存在，请在上面直接改值")
            return
        bag.set(key, value)
        self._after_write(f"添加属性 {key}")
        self._render()

    def _add_from_known(self):
        if self._loading or self.more_combo is None:
            return
        key = self.more_combo.currentData()
        if not key:
            return
        bag = self._ensure_bag()
        if bag is None:
            return
        cat = category_of(key)
        if cat == CAT_TERRAIN:
            for other in TERRAIN_KEYS:
                self._remove_key(bag, other)
            self._enforce_category(bag, CAT_TERRAIN)
            bag.set(key, "")
        elif cat == CAT_UNIT:
            self._enforce_category(bag, CAT_UNIT)
            presets = VALUE_PRESETS.get(key, ())
            bag.set(key, presets[0] if presets else "")
        else:
            bag.set(key, "")
        self.category = cat
        self._after_write(f"添加属性 {key}")
        self._render()

    def normalize_conflicts(self):
        """冲突整理：地形内部只留第一个；跨类别按当前页只留一类。"""
        if self.ts is None or self.local_id < 0:
            return
        bag = self._ensure_bag()
        if bag is None:
            return
        keys = list(bag.values)
        for key in conflicting_keys(keys)[1:]:
            self._remove_key(bag, key)
        terrain = [k for k in keys if category_of(k) == CAT_TERRAIN]
        unit = [k for k in keys if category_of(k) == CAT_UNIT]
        if terrain and unit:
            remove = terrain if self.category == CAT_UNIT else unit
            for key in remove:
                self._remove_key(bag, key)
        self._after_write("整理冲突属性")
        self._render()

    # ---------------- 供测试 / 外部调用的点选接口 ----------------
    def select_terrain(self, key: str | None):
        if self.category != CAT_TERRAIN:
            self.set_category(CAT_TERRAIN)
        rb = self.terrain_radios.get(key)
        if rb is not None:
            rb.setChecked(True)

    def set_value_prop(self, key: str, checked: bool, value: str | None = None):
        if self.category != CAT_UNIT:
            self.set_category(CAT_UNIT)
        cb = self.value_checks.get(key)
        if cb is None:
            return
        if value is not None:
            combo = self.value_combos.get(key)
            if combo is not None:
                combo.setCurrentText(value)
        cb.setChecked(checked)


class TilesetEditorWidget(QWidget):
    """图集编辑器主体：只读瓦片图集（点击选瓦片）。

    每瓦片属性面板已独立成左侧停靠面板（``TilesetTilePropsDock``），
    这里只保留图集大图与点击选格；点击的瓦片通过 ``tileClicked`` 传出，
    由主窗口转发给左侧属性面板。
    """

    tileClicked = Signal(int)      # 选中瓦片的 local_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ts: Tileset | None = None
        self.doc = None

        self.atlas = TilesetAtlasView()
        # 选格信号直接透传给主窗口，由它转发给左侧「瓦片属性」停靠面板。
        self.atlas.tileClicked.connect(self.tileClicked.emit)

        # 铁锈的图集大图往往远大于窗口，包一层滚动区才能看到全部瓦片；
        # atlas 保持 fixedSize，鼠标坐标仍是它自身的坐标系，滚动不影响选格。
        # 左对齐（非居中）以便滚轮缩放时按光标精确定位（居中对齐在缩小到比视口
        # 小时会带来额外偏移，破坏缩放定位数学）。
        atlas_scroll = QScrollArea()
        atlas_scroll.setWidget(self.atlas)
        atlas_scroll.setWidgetResizable(False)
        atlas_scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.atlas.set_pan_scroll(atlas_scroll)
        atlas_scroll.setStyleSheet(
            f"QScrollArea{{background:{THEME['bg']};border:0;}}"
            f"QScrollArea > QWidget > QWidget{{background:{THEME['bg']};}}")

        main = QHBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.addWidget(atlas_scroll, 1)

    def set_tileset(self, ts: Tileset, doc, undo_stack=None):
        self.ts = ts
        self.doc = doc
        self.atlas.set_tileset(ts, doc)
