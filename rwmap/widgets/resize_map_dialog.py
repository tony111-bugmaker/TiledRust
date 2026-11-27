"""「地图 → 调整地图尺寸」对话框（仿 Tiled 的调整大小窗口）。

布局
----
上排两个分组框：``大小``（宽度 / 高度，单位「块」）与 ``挪动``（X / Y，
单位「块」，可正可负）。中部是地图预览：

- 绿色矩形 = 调整后的新地图范围；虚线 = 新地图边界；
- 蓝色小矩形 = 对象层里的对象；
- 真正的瓦片内容按当前 X/Y 挪动量摆放在预览里，**可以直接拖动**，
  拖动时实时改写 X/Y 微调框（等价于在「挪动」里输数值）；
- 平移后落在新地图之外的瓦片（溢出瓦片）用**连通块红框**圈住 —— 一段连续
  的溢出格只画一个大框，且线宽 1px（与图章越界红框同款、保持细线）。

底部是「移除地图之外的对象」勾选框 + 确定 / 取消。所有数值由 ``values()``
一次性取走，真正的图层重建交给 ``rw.resize`` 的纯算法 + 撤销命令。

挪动限制（与 Tiled 一致）：新尺寸等于旧尺寸时锁 0；新尺寸更大时旧内容整块
留在新图内；新尺寸更小时新图整块留在旧内容内。两轴独立计算。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QSize, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ..document.gid import split_gid
from ..widgets.plain_spinbox import PlainSpinBox
from ..rw.resize import connected_blocks
from ..theme import INK, TEXT_DIM
from ..tilesets.tile_cache import get_pixmap

# 预览语义色：界面仍是双色暗色，这些是「有含义的图形」（同小地图视口框 /
# 触发器连线），按项目惯例保留彩色。红框颜色与图章越界提示一致。
_MAP_GREEN = QColor(86, 138, 86)
_OUTSIDE = QColor(48, 48, 48)
_OBJ_BLUE = QColor(90, 158, 224)
_DASH = QColor(206, 206, 206)
_RED = QColor(255, 70, 70)
_RED_LINE = 1  # 用户要求：红框线条粗细与参考图一致（细线，非逐格 2px）


def clamp_offset(dx, dy, nw, nh, ow, oh):
    """把挪动量限制在合法区间：两轴各自按新旧尺寸关系取闭区间。

    - 新 == 旧：只能 0，不能挪。
    - 新 > 旧：旧内容不能越出新图 -> 0 <= d <= 新 - 旧。
    - 新 < 旧：新图不能越出旧内容 -> 旧 - 新 的负值 <= d <= 0。
    """
    def axis(d, new, old):
        if new == old:
            return 0
        delta = new - old
        return max(min(0, delta), min(d, max(0, delta)))

    return axis(dx, nw, ow), axis(dy, nh, oh)


class ResizePreview(QWidget):
    """可拖动的地图预览。拖动内容 → 发 ``offsetChanged(dx, dy)``。"""

    offsetChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = None
        self._nw = self._nh = 1
        self._dx = self._dy = 0
        self._remove = False
        self._thumb: QPixmap | None = None   # 旧地图瓦片合成图
        self._thumb_tile = 1                 # 缩略图里每格像素
        self._scale = 1.0                    # 世界（格）→ 控件像素
        self._origin = QPointF(0, 0)         # 世界原点的控件坐标
        self._overflow_cells: set = set()
        self._overflow_blocks: list = []
        self._dragging = False
        self._drag_pos = None
        self._drag_offset = (0, 0)
        self._drag_scale = 1.0   # 按下瞬间锁定缩放，拖动过程中换算保持线性
        self._drag_origin = QPointF(0, 0)  # 按下瞬间锁定原点：内容严格跟随鼠标
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)

    # ---------------- 数据 ----------------
    def set_context(self, doc):
        """换文档 / 图集结构变化时重建缩略图。"""
        self._doc = doc
        self._build_thumbnail()
        self.update_geometry_and_repaint()

    def set_params(self, nw: int, nh: int, dx: int, dy: int, remove: bool = False):
        need_thumb = self._doc is not None and (nw, nh) != (self._nw, self._nh)
        self._nw, self._nh = max(1, nw), max(1, nh)
        ow = self._doc.width if self._doc is not None else self._nw
        oh = self._doc.height if self._doc is not None else self._nh
        self._dx, self._dy = clamp_offset(dx, dy, self._nw, self._nh, ow, oh)
        self._remove = remove
        if need_thumb:
            self._build_thumbnail()
        self.update_geometry_and_repaint()

    def preview_params(self):
        """供测试读取当前预览状态。"""
        return (self._nw, self._nh, self._dx, self._dy, self._remove)

    def overflow_info(self):
        """返回 ``(格数, 连通块数, 块外接矩形列表[新图坐标])``。

        ``_overflow_blocks`` 在 ``_recompute_overflow`` 里已按 (dx,dy) 平移过，
        这里直接返回，不能再加一次偏移。
        """
        rects = list(self._overflow_blocks)
        return len(self._overflow_cells), len(rects), rects

    def _build_thumbnail(self):
        doc = self._doc
        if doc is None or doc.width <= 0 or doc.height <= 0:
            self._thumb = None
            self._overflow_cells = set()
            self._overflow_blocks = []
            return
        # 缩略图每格像素：大地图缩到 2048px 以内，避免开超大 QImage。
        tpx = max(1, min(4, 2048 // max(doc.width, doc.height)))
        self._thumb_tile = tpx
        iw, ih = doc.width * tpx, doc.height * tpx
        img = QImage(iw, ih, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for layer in doc.tile_layers():
            if not layer.visible:
                continue
            p.setOpacity(max(0.0, min(1.0, layer.opacity)))
            for r in range(doc.height):
                base = r * layer.width
                for c in range(doc.width):
                    raw = layer.cells[base + c] if base + c < len(layer.cells) else 0
                    if not raw:
                        continue
                    gid, fh, fv, fd = split_gid(raw)
                    loc = doc.local_tile(gid)
                    if loc is None:
                        continue
                    ts, lid = loc
                    pix = get_pixmap(ts, lid, (fh, fv, fd), doc.resolver)
                    if pix is None or pix.isNull():
                        # 图集缺图时仍画出「这里有内容」的占位，预览才看得出拖动对象
                        p.fillRect(c * tpx, r * tpx, tpx, tpx, QColor(150, 150, 150))
                        continue
                    p.drawPixmap(QRectF(c * tpx, r * tpx, tpx, tpx), pix,
                                 QRectF(0, 0, pix.width(), pix.height()))
        p.end()
        self._thumb = QPixmap.fromImage(img)
        self._recompute_overflow()

    def _recompute_overflow(self):
        """旧图里有内容、但按 (dx,dy) 平移后落在新地图之外的格。"""
        doc = self._doc
        if doc is None:
            self._overflow_cells = set()
            self._overflow_blocks = []
            return
        cells: set = set()
        for layer in doc.tile_layers():
            if not layer.visible:
                continue
            for r in range(doc.height):
                base = r * layer.width
                for c in range(doc.width):
                    if base + c < len(layer.cells) and layer.cells[base + c]:
                        cells.add((c, r))
        self._overflow_cells = {
            (c, r) for (c, r) in cells
            if not (0 <= c + self._dx < self._nw and 0 <= r + self._dy < self._nh)
        }
        # 连通块按「平移后坐标」判定相邻 -> 红框直接落在新图坐标系里。
        shifted = {(c + self._dx, r + self._dy) for (c, r) in self._overflow_cells}
        self._overflow_blocks = connected_blocks(shifted)

    # ---------------- 几何 ----------------
    def _content_bounds(self):
        doc = self._doc
        ow = doc.width if doc is not None else 0
        oh = doc.height if doc is not None else 0
        x0 = min(0, self._dx)
        y0 = min(0, self._dy)
        x1 = max(self._nw, self._dx + ow)
        y1 = max(self._nh, self._dy + oh)
        return x0, y0, x1, y1

    def update_geometry_and_repaint(self):
        self._recompute_overflow()
        if self._dragging:
            # 拖动中：冻结缩放和原点，内容严格 1:1 跟随鼠标；否则内容边界
            # 随 offset 变大 -> scale 变小 -> 出现“拖不动/橡皮筋”的限制感。
            # 松手后再重算几何，把溢出内容纳入视野。
            self.update()
            return
        x0, y0, x1, y1 = self._content_bounds()
        cols, rows = max(1, x1 - x0), max(1, y1 - y0)
        margin = 12
        avail_w, avail_h = max(1, self.width() - 2 * margin), max(1, self.height() - 2 * margin)
        self._scale = max(0.01, min(avail_w / cols, avail_h / rows))
        used_w, used_h = cols * self._scale, rows * self._scale
        self._origin = QPointF(margin + (avail_w - used_w) / 2 - x0 * self._scale,
                               margin + (avail_h - used_h) / 2 - y0 * self._scale)
        self.update()

    def _world_to_widget(self, c, r):
        origin = self._drag_origin if self._dragging else self._origin
        return (origin.x() + c * self._scale,
                origin.y() + r * self._scale)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_geometry_and_repaint()

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), INK)
        x0, y0, x1, y1 = self._content_bounds()
        # 可视整体外框
        tl = self._world_to_widget(x0, y0)
        br = self._world_to_widget(x1, y1)
        painter.fillRect(QRectF(tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]), _OUTSIDE)

        # 新地图绿色范围
        mtl = self._world_to_widget(0, 0)
        mbr = self._world_to_widget(self._nw, self._nh)
        painter.fillRect(QRectF(mtl[0], mtl[1], mbr[0] - mtl[0], mbr[1] - mtl[1]), _MAP_GREEN)

        # 旧图瓦片内容（按当前挪动量摆放）
        if self._thumb is not None and not self._thumb.isNull() and self._doc is not None:
            ctl = self._world_to_widget(self._dx, self._dy)
            cbr = self._world_to_widget(self._dx + self._doc.width,
                                        self._dy + self._doc.height)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawPixmap(QRectF(ctl[0], ctl[1], cbr[0] - ctl[0], cbr[1] - ctl[1]),
                               self._thumb,
                               QRectF(0, 0, self._thumb.width(), self._thumb.height()))

        # 对象层：蓝色小矩形（同 Tiled 预览），坐标按挪动量平移
        if self._doc is not None:
            tw, th = self._doc.tile_w, self._doc.tile_h
            painter.setPen(QPen(_OBJ_BLUE, 1))
            painter.setBrush(Qt.NoBrush)
            for layer in self._doc.layers:
                if getattr(layer, "kind", "tile") != "object" or not layer.visible:
                    continue
                for obj in layer.objects:
                    ox, oy, ow, oh = obj.bounds(tw, th)
                    a = self._world_to_widget(ox / tw + self._dx, oy / th + self._dy)
                    b = self._world_to_widget((ox + ow) / tw + self._dx,
                                              (oy + oh) / th + self._dy)
                    if b[0] < tl[0] or b[1] < tl[1] or a[0] > br[0] or a[1] > br[1]:
                        continue
                    painter.drawRect(QRectF(a[0], a[1], b[0] - a[0], b[1] - a[1]))

        # 新地图边界虚线（参考图里地图与空白之间那条竖虚线）
        painter.setPen(QPen(_DASH, 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(mtl[0], mtl[1], mbr[0] - mtl[0], mbr[1] - mtl[1]).adjusted(0, 0, -0.5, -0.5))

        # 溢出瓦片：连通块红框（一段连续格只画一个大框），线宽 1px
        if self._overflow_blocks:
            painter.setPen(QPen(_RED, _RED_LINE))
            painter.setBrush(Qt.NoBrush)
            for (c0, r0, c1, r1) in self._overflow_blocks:
                a = self._world_to_widget(c0, r0)
                b = self._world_to_widget(c1 + 1, r1 + 1)
                painter.drawRect(QRectF(a[0], a[1], b[0] - a[0], b[1] - a[1]).adjusted(0, 0, -1, -1))

    # ---------------- 拖动 → 改挪动数值 ----------------
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self._dragging = True
        self._drag_pos = event.position().toPoint()
        self._drag_offset = (self._dx, self._dy)
        self._drag_scale = self._scale
        self._drag_origin = QPointF(self._origin)
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging or self._drag_scale <= 0:
            return super().mouseMoveEvent(event)
        pos = event.position().toPoint()
        dx_px = pos.x() - self._drag_pos.x()
        dy_px = pos.y() - self._drag_pos.y()
        ndx = self._drag_offset[0] + int(round(dx_px / self._drag_scale))
        ndy = self._drag_offset[1] + int(round(dy_px / self._drag_scale))
        doc = self._doc
        ow = doc.width if doc is not None else self._nw
        oh = doc.height if doc is not None else self._nh
        ndx, ndy = clamp_offset(ndx, ndy, self._nw, self._nh, ow, oh)
        if (ndx, ndy) != (self._dx, self._dy):
            self.offsetChanged.emit(ndx, ndy)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._drag_pos = None
        self.setCursor(Qt.OpenHandCursor)
        # 松手后重算几何，让溢出到新图之外的内容也进入视野并看到红框。
        self.update_geometry_and_repaint()
        return super().mouseReleaseEvent(event)


class ResizeMapDialog(QDialog):
    """调整地图尺寸：收集 width / height / X / Y / remove_outside。"""

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self._doc = doc
        self.setWindowTitle("调整地图尺寸")
        self.resize(560, 520)

        def spin(value, minimum=-10000, maximum=10000):
            s = PlainSpinBox(self)
            s.setRange(minimum, maximum)
            s.setValue(value)
            s.setSuffix(" 块")
            s.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return s

        self.sp_width = spin(doc.width, 1, 100000)
        self.sp_height = spin(doc.height, 1, 100000)
        self.sp_x = spin(0)
        self.sp_y = spin(0)

        box_size = QGroupBox("大小", self)
        form_size = QFormLayout(box_size)
        form_size.addRow("宽度:", self.sp_width)
        form_size.addRow("高度:", self.sp_height)

        box_move = QGroupBox("挪动", self)
        form_move = QFormLayout(box_move)
        form_move.addRow("X:", self.sp_x)
        form_move.addRow("Y:", self.sp_y)

        top = QHBoxLayout()
        top.addWidget(box_size)
        top.addWidget(box_move)
        top.addStretch(1)

        self.preview = ResizePreview(self)
        self.preview.set_context(doc)

        self.lbl_overflow = QLabel(self)
        self.lbl_overflow.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")

        self.chk_remove = QCheckBox("移除地图之外的对象", self)
        self.chk_remove.setChecked(True)

        # 同其他对话框：中文按钮 + 足够点击区域（Windows11 样式默认按钮过小）
        buttons = QDialogButtonBox(self)
        ok = buttons.addButton("确定", QDialogButtonBox.AcceptRole)
        ok.setDefault(True)
        cancel = buttons.addButton("取消", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        for btn in (ok, cancel):
            btn.setMinimumSize(76, 28)
        self.buttons = buttons

        bottom = QHBoxLayout()
        bottom.addWidget(self.chk_remove)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.lbl_overflow)
        layout.addLayout(bottom)

        # 预览拖动 ↔ 微调框双向同步（setParams 不回发 offsetChanged，故无环）。
        self.preview.offsetChanged.connect(self._on_preview_offset)
        for s in (self.sp_width, self.sp_height, self.sp_x, self.sp_y):
            s.valueChanged.connect(self._on_params_changed)
        self.chk_remove.toggled.connect(self._on_params_changed)
        self._on_params_changed()

    def _on_preview_offset(self, dx, dy):
        doc = self._doc
        ow = doc.width if doc is not None else self.sp_width.value()
        oh = doc.height if doc is not None else self.sp_height.value()
        dx, dy = clamp_offset(dx, dy, self.sp_width.value(),
                              self.sp_height.value(), ow, oh)
        if self.sp_x.value() != dx:
            self.sp_x.setValue(dx)
        if self.sp_y.value() != dy:
            self.sp_y.setValue(dy)

    def _on_params_changed(self, *_):
        self.preview.set_params(self.sp_width.value(), self.sp_height.value(),
                                self.sp_x.value(), self.sp_y.value(),
                                self.chk_remove.isChecked())
        cells, blocks, _ = self.preview.overflow_info()
        if cells:
            self.lbl_overflow.setText(f"溢出瓦片：{cells} 格 / {blocks} 块（红框标出，确认后将裁掉）")
        else:
            self.lbl_overflow.setText("无溢出瓦片")

    def values(self) -> dict:
        return {
            "width": self.sp_width.value(),
            "height": self.sp_height.value(),
            "dx": self.sp_x.value(),
            "dy": self.sp_y.value(),
            "remove_outside": self.chk_remove.isChecked(),
        }


__all__ = ["ResizeMapDialog", "ResizePreview", "clamp_offset"]
