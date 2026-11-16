"""图集设置面板（``TilesetPropsPanel``，停靠在右侧）。

提供名称 / Tile Size / 边距 / 间距 / 图像 / 透明色 + 底部「应用 / 重置」。

与游戏无关、或改动会引发游戏报错的 Tiled 专有能力（方向 / 绘制偏移 / 填充模式 /
背景色 / 类）一律不显示，避免误操作；这些键仍原样保留在图集属性里、不丢失。
数值框去掉上下箭头、禁用滚轮，只能点击输入数字。
"""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIntValidator, QPalette, QUndoCommand
from PySide6.QtWidgets import (
    QColorDialog, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..document.tileset import Tileset
from ..theme import ASH, INK, TEXT, TEXT_DIM

# 「图集设置」专用统一底色：整块面板（标题栏 / 内容 / 分组框 / 底部按钮行）
# 全部用同一个颜色，避免某一层露出父窗口的深色底（用户截图里底部黑条）。
PANEL_BG = INK

# 界面区分色收敛为双色：INK（近黑底层）+ ASH（偏黑灰抬起层）。
_FLAT = {
    "bg": PANEL_BG,        # 所有背景层统一
    "input": INK,         # 输入框与背景同色，靠边框显形
    "text": TEXT,         # 主文字（深底浅字）
    "muted": TEXT_DIM,    # 次要文字
    "accent": ASH,        # 强调（应用按钮）：不再是蓝色，统一用 ASH
    "group_border": ASH,
    "btn_bg": ASH,
    "btn_border": INK,
    "btn_hover": ASH,
}


def apply_panel_bg(widget, color: str = PANEL_BG):
    """给任意 QWidget（含自定义子类）可靠地填底色。

    Qt 对**自定义 QWidget 子类**默认不绘制样式表里的 background，样式表失效时
    该区域就透明、露出父窗口深色底 —— 这正是底部「应用 / 重置」条发黑的原因。
    用 QPalette + autoFillBackground 兜底，不依赖样式表能否解析成功。
    """
    widget.setAutoFillBackground(True)
    pal = widget.palette()
    pal.setColor(QPalette.Window, QColor(color))
    widget.setPalette(pal)


class _ColorButton(QPushButton):
    """色块按钮：显示当前颜色；空 = 无（透明色 / 背景色可省略）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = ""
        self.setFixedSize(64, 22)
        self.clicked.connect(self._pick)
        self._refresh()

    def _refresh(self):
        if self._color:
            # Tiled 的 trans 常写成不带 # 的裸十六进制（如 108A7C），Qt 的
            # QColor / 样式表都不认这种写法，色块会变黑；显示前补上 #，
            # 但 get_color() 仍返回原值，避免写回 .tsx 时格式被改动。
            hexv = self._color.lstrip("#")
            c = QColor("#" + hexv)
            self.setStyleSheet(
                f"QPushButton{{background:#{hexv};border:1px solid {INK};"
                f"color:{'#000' if c.lightness() > 128 else '#fff'};}}")
            self.setText(self._color.upper())
        else:
            self.setStyleSheet(
                f"QPushButton{{background:{_FLAT['input']};"
                f"border:1px solid {_FLAT['btn_border']};"
                f"color:{_FLAT['muted']};}}")
            self.setText("无")

    def _pick(self):
        init = QColor(self._color) if self._color else QColor("#808080")
        c = QColorDialog.getColor(init, self, "选择颜色")
        if c.isValid():
            self._color = c.name().upper()
            self._refresh()

    def get_color(self) -> str:
        return self._color

    def set_color(self, value: str):
        self._color = (value or "").upper()
        self._refresh()


class _NumBox(QLineEdit):
    """数值输入框：去掉原生上下箭头、禁用滚轮，只允许用键盘输入数字。

    用 ``QLineEdit`` + ``QIntValidator`` 替代 ``QSpinBox``：``QSpinBox`` 在自定义
    深色样式下其内部编辑框的文字不会渲染（已知 Qt 问题，表现为数值框一片空白），
    而 ``QLineEdit`` 正常显示文字；这里模拟 ``value()`` / ``setValue()`` /
    ``setRange()`` / ``valueChanged`` 接口，上层代码无需改动。

    上下方向键 / PageUp / PageDown 不再步进数值，左 / 右 / Home / End / 删除键
    与正常打字不受影响；滚轮事件交给上层（滚动面板内容）。
    """

    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min = 0
        self._max = 9999
        self.setValidator(QIntValidator(self._min, self._max, self))
        self.setAlignment(Qt.AlignCenter)
        # 水平方向用 Ignored：文本再长也不撑开最小宽度，从而不会把面板内容
        # 顶出 dock 边框、触发横向滚动条（输入框被窗口右缘截断的根源）。
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.textEdited.connect(self._on_edit)

    def setRange(self, lo: int, hi: int):
        self._min = lo
        self._max = hi
        self.setValidator(QIntValidator(lo, hi, self))

    def value(self) -> int:
        try:
            return max(self._min, min(self._max, int(self.text() or 0)))
        except ValueError:
            return 0

    def setValue(self, v: int):
        v = max(self._min, min(self._max, int(v)))
        self.setText(str(v))

    def _on_edit(self, _text: str):
        if self.text() == "":
            self.valueChanged.emit(0)
            return
        try:
            self.valueChanged.emit(int(self.text()))
        except ValueError:
            pass

    def wheelEvent(self, event):
        # 滚轮事件交给上层（滚动面板内容），绝不变动数值
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down,
                           Qt.Key_PageUp, Qt.Key_PageDown):
            event.ignore()
            return
        super().keyPressEvent(event)


class TilesetSettingsCommand:
    """地图/图集撤销栈上的一条命令：在 before / after 两份快照间来回恢复图集设置。"""

    def __init__(self, ts: Tileset, before: dict, after: dict, text: str,
                 doc=None):
        self.ts = ts
        self.before = before
        self.after = after
        self.text = text
        self.doc = doc

    def _apply(self, snap: dict):
        self.ts.name = snap["name"]
        self.ts.tile_w = snap["tile_w"]
        self.ts.tile_h = snap["tile_h"]
        self.ts.margin = snap["margin"]
        self.ts.spacing = snap["spacing"]
        self.ts.image_source = snap["image_source"]
        self.ts.trans = snap["trans"]
        self.ts.image_w = snap["image_w"]
        self.ts.image_h = snap["image_h"]
        self.ts.columns = snap["columns"]
        self.ts.tilecount = snap["tilecount"]
        self.ts.embedded_png = snap.get("embedded_png")
        self.ts.properties = copy.deepcopy(snap["properties"])
        if self.doc is not None and hasattr(self.doc, "image_path"):
            self.doc.image_path = snap.get("image_path")
        # 换图 / 撤销换图后，各级图块缓存都必须失效，否则仍显示旧图。
        from ..tilesets import image_loader, tile_cache
        from . import tilesets_dock as _tdock
        image_loader.clear_cache()
        tile_cache.clear_cache()
        _tdock.clear_atlas_cache()

    def undo(self):
        self._apply(self.before)

    def redo(self):
        self._apply(self.after)


def _snapshot(ts: Tileset) -> dict:
    return {
        "name": ts.name,
        "tile_w": ts.tile_w,
        "tile_h": ts.tile_h,
        "margin": ts.margin,
        "spacing": ts.spacing,
        "image_source": ts.image_source,
        "trans": ts.trans,
        "embedded_png": ts.embedded_png,
        "image_w": ts.image_w,
        "image_h": ts.image_h,
        "columns": ts.columns,
        "tilecount": ts.tilecount,
        "properties": copy.deepcopy(ts.properties),
    }


class _QUndoWrapper(QUndoCommand):
    """把轻量 TilesetSettingsCommand 适配成标准 QUndoCommand。"""

    def __init__(self, inner: TilesetSettingsCommand):
        super().__init__(inner.text)
        self._inner = inner

    def undo(self):
        self._inner.undo()

    def redo(self):
        self._inner.redo()


# ----------------------------------------------------------------------
# 图集设置（右侧面板）
# ----------------------------------------------------------------------
class TilesetPropsPanel(QWidget):
    """图集设置面板（名称 / 瓦片尺寸 / 边距 / 间距 / 图像 / 透明色 + 应用/重置）。

    只管本面板字段：改动后压一条可撤销命令写回 Tileset。
    """

    applied = Signal(object)        # 传出被修改的 Tileset

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ts: Tileset | None = None
        self.doc = None
        self.undo_stack = None
        self._new_image: Path | None = None

        # 自定义 QWidget 子类不会自动绘制样式表 background，必须显式兜底，
        # 否则底部「应用 / 重置」那一层透明、露出父窗口深色底。
        self.setObjectName("TilesetPropsPanel")
        apply_panel_bg(self)
        self.setStyleSheet(self._panel_style())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # 本面板永不出现横向滚动条：内容应始终填满 dock 框，绝不溢出到框外。
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        apply_panel_bg(scroll)
        apply_panel_bg(scroll.viewport())
        scroll.setStyleSheet(f"QScrollArea{{background:{_FLAT['bg']};border:0;}}")
        content = QWidget()
        apply_panel_bg(content)
        content.setStyleSheet(f"background:{_FLAT['bg']};")
        # 内容区水平方向 Ignored：子控件（长路径文本框等）再宽也被压缩进
        # 视口，不会把内容撑得比 dock 边框还宽（这是「内容溢出边框」的根因）。
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body = QVBoxLayout(content)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(10)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self._build_attributes(body)

        btns = QHBoxLayout()
        btns.setContentsMargins(12, 10, 12, 12)
        btns.addStretch(1)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.clicked.connect(self._apply)
        self.reset_btn = QPushButton("重置")
        self.reset_btn.clicked.connect(self._reset)
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.reset_btn)
        outer.addLayout(btns)

        self._set_enabled(False)

    # ---------------- 样式 ----------------
    def _panel_style(self) -> str:
        F = _FLAT
        return f"""
        #TilesetPropsPanel, QWidget{{background:{F['bg']};}}
        QLabel{{color:{F['text']};font-size:12px;background:transparent;}}
        QLineEdit{{background:{F['input']};color:{F['text']};
            border:1px solid {F['btn_border']};border-radius:4px;padding:3px 6px;font-size:12px;}}
        QLineEdit:focus{{border:1px solid {F['accent']};}}
        QComboBox::drop-down{{border:0;}}
        QPushButton{{background:{F['btn_bg']};color:{F['text']};border:1px solid {F['btn_border']};
            border-radius:4px;padding:4px 14px;font-size:12px;}}
        QPushButton:hover{{background:{F['btn_hover']};}}
        QPushButton#Primary{{background:{F['accent']};color:{INK};border:1px solid {F['accent']};}}
        QPushButton#Primary:hover{{background:rgba(36, 36, 36, 200);}}
        QPushButton:disabled{{background:{F['bg']};color:{F['muted']};border:1px solid {F['group_border']};}}
        """

    def _add_row(self, box, label: str, widget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        if label:
            lab = QLabel(label)
            lab.setFixedWidth(64)
            lab.setAlignment(Qt.AlignCenter)
            row.addWidget(lab)
        else:
            # 无标签的行（图像说明）用等宽留白对齐，避免出现一个空的边框小框。
            row.addSpacing(64 + 8)
        if isinstance(widget, QLayout):
            row.addLayout(widget, 1)
        else:
            row.addWidget(widget, 1)
        box.addLayout(row)
        return row

    # ---------------- 图集属性（照搬 Tiled）----------------
    def _build_attributes(self, body: QVBoxLayout):
        group = QWidget()
        apply_panel_bg(group)
        # 分组框只包住内容，不随 dock 高度拉成一大片空白框；剩余空间交给
        # body 底部的 stretch，保持底色统一。
        group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        # 分组框背景与整块面板完全一致，只留一圈细边框作视觉区分。
        # 注意：f-string 里 {{ / }} 必须成对转义，末尾多一个 } 会让 Qt 判定
        # 整条样式表语法错误（运行时刷 "Could not parse stylesheet"）。
        group.setStyleSheet(f"QWidget{{background:{_FLAT['bg']};"
                            f"border:1px solid {_FLAT['group_border']};"
                            f"border-radius:5px;}}")
        g = QVBoxLayout(group)
        g.setContentsMargins(10, 12, 10, 12)
        g.setSpacing(8)
        # 不再放「图集属性」分组标题：它会被 QVBoxLayout 拉成一大块空白，
        # 把下面所有行挤到面板底部。末尾的 stretch 让所有行顶到最上面。

        self.name_edit = QLineEdit()
        self.name_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.name_edit.setAlignment(Qt.AlignCenter)
        self.name_edit.editingFinished.connect(self._mark_dirty)
        self._add_row(g, "名称", self.name_edit)

        self.tw_spin = _NumBox(); self.tw_spin.setRange(1, 512)
        self.th_spin = _NumBox(); self.th_spin.setRange(1, 512)
        # 两个数值框用 Ignored 策略避免长文本撑宽面板；必须同时给 stretch
        # 和最小宽度，否则 addStretch 会把它们压成 0 宽（数值完全看不见）。
        self.tw_spin.setMinimumWidth(48)
        self.th_spin.setMinimumWidth(48)
        size_row = QHBoxLayout(); size_row.setSpacing(6)
        size_row.addWidget(self.tw_spin, 1)
        xl = QLabel("×")
        xl.setAlignment(Qt.AlignCenter)
        xl.setStyleSheet(f"QLabel{{color:{_FLAT['muted']};background:transparent;}}")
        size_row.addWidget(xl)
        size_row.addWidget(self.th_spin, 1)
        self._add_row(g, "瓦片尺寸", size_row)
        self.tw_spin.valueChanged.connect(self._mark_dirty)
        self.th_spin.valueChanged.connect(self._mark_dirty)

        self.margin_spin = _NumBox(); self.margin_spin.setRange(0, 64)
        self._add_row(g, "边距", self.margin_spin)
        self.margin_spin.valueChanged.connect(self._mark_dirty)
        self.spacing_spin = _NumBox(); self.spacing_spin.setRange(0, 64)
        self._add_row(g, "间距", self.spacing_spin)
        self.spacing_spin.valueChanged.connect(self._mark_dirty)

        self.image_edit = QLineEdit(); self.image_edit.setReadOnly(True)
        self.image_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.image_edit.setAlignment(Qt.AlignCenter)
        self.image_btn = QPushButton("选择…")
        self.image_btn.clicked.connect(self._pick_image)
        img_row = QHBoxLayout(); img_row.setSpacing(6)
        img_row.addWidget(self.image_edit, 1); img_row.addWidget(self.image_btn)
        self._add_row(g, "图像", img_row)

        self.image_note = QLabel("")
        self.image_note.setWordWrap(True)
        self.image_note.setAlignment(Qt.AlignCenter)
        self.image_note.setStyleSheet(
            f"QLabel{{color:{_FLAT['muted']};font-size:10px;background:transparent;}}")
        self._add_row(g, "", self.image_note)

        self.trans_color = _ColorButton()
        self.trans_color.clicked.connect(self._mark_dirty)
        self._add_row(g, "透明色", self.trans_color)

        # 没有这段 stretch，说明文字这类可纵向伸展的 QLabel 会吃掉全部剩余
        # 高度，形成截图里的大片空白框。
        g.addStretch(1)
        body.addWidget(group)
        body.addStretch(1)

    # ---------------- 数据装载 ----------------
    def set_context(self, ts: Tileset, doc, undo_stack):
        self.doc = doc
        self.undo_stack = undo_stack
        self.set_tileset(ts)

    def set_tileset(self, ts: Tileset | None):
        self.ts = ts
        self._new_image = None
        if ts is None:
            self._set_enabled(False)
            self.name_edit.clear()
            self.tw_spin.setValue(1); self.th_spin.setValue(1)
            self.margin_spin.setValue(0); self.spacing_spin.setValue(0)
            self.image_edit.clear(); self.image_note.clear()
            self.trans_color.set_color("")
            return
        self._set_enabled(True)
        self._load()

    def _set_enabled(self, on: bool):
        for w in (self.name_edit, self.tw_spin, self.th_spin, self.margin_spin,
                  self.spacing_spin, self.image_btn, self.apply_btn,
                  self.reset_btn, self.trans_color):
            w.setEnabled(on)

    def _load(self):
        ts = self.ts
        self.name_edit.setText(ts.name or "")
        self.tw_spin.setValue(ts.tile_w)
        self.th_spin.setValue(ts.tile_h)
        self.margin_spin.setValue(int(ts.margin or 0))
        self.spacing_spin.setValue(int(ts.spacing or 0))
        self.image_edit.setText(ts.image_source or "")
        self.trans_color.set_color(ts.trans or "")
        # 「选择…」始终可用：内嵌 PNG 图集也能换图（转成 base64 写回），
        # 只是换完要点「应用」才生效。旧版在这里把按钮禁用，导致内嵌图集
        # 完全无法换图。
        self.image_note.setText(
            "选好新图片后点「应用」生效：内嵌图集会同步更新，外部 .tsx 会把图片复制到同目录。")

    # ---------------- 图像选择 ----------------
    def _pick_image(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图集大图", "",
            "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)")
        if not path:
            return
        self._new_image = Path(path)
        self.image_edit.setText(self._new_image.name)
        self._mark_dirty()          # 换图也是改动：立刻点亮「应用」

    def _replace_image(self, ts: Tileset):
        """把「选择…」挑中的图片真正换进图集（内嵌图集也可用）。

        * 内嵌 PNG 图集：新图统一转成 PNG + base64，写回 ``embedded_png``、
          ``properties`` 和原始 DOM 节点（否则导出地图时 xml_preserve 会把旧
          base64 原样写出去）。
        * 外部 .tsx 且已落盘：图片复制到 .tsx 同目录并更新引用。
        * 新建、尚未落盘的图集：只记路径，保存时由 TilesetDocument 复制。
        """
        import base64
        import io
        import shutil

        from PIL import Image

        src = Path(self._new_image)
        with Image.open(src) as im:
            ts.image_w, ts.image_h = im.size
            rgba = im.convert("RGBA")

        external = (ts.embedded_png is None
                    and ts.source_path is not None
                    and Path(ts.source_path).exists())
        if external:
            dest = Path(ts.source_path).parent / src.name
            if src.resolve() != dest.resolve():
                shutil.copyfile(src, dest)
            new_path = dest
        else:
            new_path = src
        ts.image_source = src.name

        if ts.embedded_png is not None:
            buf = io.BytesIO()
            rgba.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            ts.embedded_png = b64
            ts.properties.set("embedded_png", b64)
            # 同步原始 XML 里的 <property name="embedded_png">，保存地图时会
            # 原样保留 DOM，只改内存里的 Tileset 是不够的。
            el = getattr(ts, "xml_element", None)
            if el is not None:
                try:
                    from ..io.xml_preserve import iter_elements
                    for node in iter_elements(el):
                        if node.tag != "properties":
                            continue
                        for prop in iter_elements(node):
                            if (prop.tag == "property"
                                    and prop.get("name") == "embedded_png"):
                                if "value" in prop.attrib:
                                    prop.set("value", b64)
                                else:
                                    prop.text = b64
                                break
                        break
                except Exception:                   # noqa: BLE001
                    pass

        # 图集任务的显示与另存都依赖 doc.image_path，必须指向新图。
        if hasattr(self.doc, "image_path"):
            self.doc.image_path = Path(new_path)
        self.image_edit.setText(ts.image_source or "")

        # 三级缓存全部失效：图集视图 / 地图瓦片立刻用新图重绘。
        from ..tilesets import image_loader, tile_cache
        from . import tilesets_dock as _tdock
        image_loader.clear_cache()
        tile_cache.clear_cache()
        _tdock.clear_atlas_cache()

    # ---------------- 应用 / 重置 ----------------
    def _mark_dirty(self, *_):
        self.apply_btn.setEnabled(True)

    def _apply(self):
        if self.ts is None or self.undo_stack is None:
            return
        before = _snapshot(self.ts)
        before["image_path"] = getattr(self.doc, "image_path", None)
        self._apply_to_tileset()
        after = _snapshot(self.ts)
        after["image_path"] = getattr(self.doc, "image_path", None)
        if before == after:
            self.apply_btn.setEnabled(False)
            return
        cmd = TilesetSettingsCommand(
            self.ts, before, after, f"设置图集「{self.ts.name or ''}」",
            doc=self.doc)
        self.undo_stack.push(_QUndoWrapper(cmd))
        if self.doc is not None:
            self.doc.dirty = True
        self.apply_btn.setEnabled(False)
        self.applied.emit(self.ts)

    def _reset(self):
        if self.ts is None:
            return
        self._new_image = None
        self._load()
        self.apply_btn.setEnabled(False)

    # ---------------- 写回 ----------------
    def _apply_to_tileset(self):
        ts = self.ts
        ts.name = self.name_edit.text().strip()
        ts.tile_w = self.tw_spin.value()
        ts.tile_h = self.th_spin.value()
        ts.margin = self.margin_spin.value()
        ts.spacing = self.spacing_spin.value()

        trans = self.trans_color.get_color()
        ts.trans = trans or None
        # 注意：trans 由 tsx_writer 写成 <image trans="..."> 属性，
        # 不写进 properties，避免导出后出现重复的自定义属性。

        # 换图：三种来源（内嵌 PNG / 外部 .tsx / 未落盘的新图集）都可换。
        if self._new_image is not None:
            try:
                self._replace_image(ts)
            except Exception:                       # noqa: BLE001
                # 图片损坏 / 无写权限时不能把其它设置一起丢掉。
                self.image_note.setText("图片读取失败：图集大图未更换，其它设置已生效。")
            self._new_image = None

        # 依图片重算 columns / tilecount（与「新建图集」同一套切格数学）
        if ts.image_w and ts.image_h:
            step_x = max(1, ts.tile_w + ts.spacing)
            step_y = max(1, ts.tile_h + ts.spacing)
            cols = max(0, (ts.image_w - 2 * ts.margin + ts.spacing) // step_x)
            rows = max(0, (ts.image_h - 2 * ts.margin + ts.spacing) // step_y)
            if cols > 0:
                ts.columns = cols
                ts.tilecount = cols * rows

