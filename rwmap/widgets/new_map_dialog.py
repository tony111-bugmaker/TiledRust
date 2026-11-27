"""「地图 → 新建地图」对话框。

只要三类输入，符合铁锈地图的实际用法：

- 宽度（块）/ 高度（块）；
- 要包含哪些图集（从版本配置里的 tilesets_root 列出 .tsx，可勾选）；
- 勾选时可预览图集缩略图（开关控制显隐）。

不再让用户输入「瓦片宽 / 瓦片高」——铁锈原生固定 20×20；若勾选的图集
里写明了 tilewidth/tileheight，则自动采用第一个图集的尺寸。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

from ..commands.document_command import AddExternalTilesetCommand
from ..widgets.plain_spinbox import PlainSpinBox
from ..io.path_resolver import ResourceResolver
from ..io.tsx_reader import read_tsx
from ..io.tmx_writer import create_blank_document
from ..theme import ASH, TEXT_DIM
from ..tilesets.image_loader import load_tileset_image
from ..tilesets.tile_cache import pil_to_qpixmap

# 铁锈原生瓦片尺寸（图集没写 tilewidth 时的兜底）
RW_TILE_SIZE = 20


def _available_tilesets(profile):
    """列出版本 tilesets_root 下全部 .tsx，返回 (相对标签, 绝对路径)。"""
    root = Path(profile.tilesets_root)
    if not root.exists():
        return []
    out = []
    for tsx in sorted(root.rglob("*.tsx")):
        try:
            label = tsx.relative_to(root).as_posix()
        except ValueError:
            label = tsx.name
        out.append((label, str(tsx)))
    return out


def _default_selected(label: str) -> bool:
    """默认勾选基础地形图集（含 units.tsx），其余（ ridges 等）留给用户选。"""
    low = label.replace("\\", "/").lower()
    return low.startswith("terrain/") or low == "units.tsx"


_PREVIEW_MAX = QSize(280, 176)


def _tileset_preview(tsx_path: str, resolver, max_size: QSize = _PREVIEW_MAX):
    """返回 (缩略图, 说明文字)；读取失败返回 (None, 错误文字)。

    复用图集 loader：trans 色键会转成 alpha，透明处露出预览框底色。
    """
    path = Path(tsx_path)
    try:
        ts = read_tsx(path, 1, resolver, path.name)
        img = load_tileset_image(ts, resolver)
        if img is None:
            return None, "无法读取图集图片"
        pix = pil_to_qpixmap(img).scaled(
            max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        cols = ts.columns or max(1, (img.width - 2 * ts.margin + ts.spacing)
                                 // max(1, ts.tile_w + ts.spacing))
        rows = ts.tilecount // cols if cols and ts.tilecount else max(
            1, (img.height - 2 * ts.margin + ts.spacing)
            // max(1, ts.tile_h + ts.spacing))
        return pix, (f"{ts.name or path.name}  ·  {ts.tile_w}×{ts.tile_h}  ·  "
                     f"{cols}×{rows} 格  ·  原图 {img.width}×{img.height}")
    except Exception as exc:  # noqa: BLE001 - 坏图集只影响预览，不挡新建地图
        return None, f"预览失败：{exc}"


def build_new_document(profile, width: int, height: int, tsx_paths: list[str]):
    """构建新地图文档：铁锈标准 5 层 + 勾选的外部图集引用。

    纯函数式构造，不走撤销栈（文档还没挂到窗口上下文）。
    """
    doc = create_blank_document(profile, width, height)
    # 关键：外部 .tsx 不写 tilecount，靠 <image> 指向的 PNG 实际尺寸推算。
    # 没有 resolver 时 read_tsx 只能拿到 0 → GID 段全重叠 → 图块集面板空白。
    from ..io.path_resolver import ResourceResolver
    doc.resolver = ResourceResolver(doc.path, profile)

    # 瓦片尺寸：优先取第一个可读取图集里写明的尺寸，否则用铁锈默认 20。
    tile_w = tile_h = RW_TILE_SIZE
    for raw in tsx_paths:
        try:
            probe = read_tsx(Path(raw), 1, doc.resolver, Path(raw).name)
        except Exception:  # noqa: BLE001 - 读失败交给后面正式添加时提示
            continue
        tile_w, tile_h = max(1, probe.tile_w), max(1, probe.tile_h)
        break
    doc.tile_w, doc.tile_h = tile_w, tile_h
    doc.root.set("tilewidth", str(tile_w))
    doc.root.set("tileheight", str(tile_h))

    # 和自带示例地图一致的图层骨架，新建即可直接画 / 放触发器 / 看自动映射。
    from ..document.layer import ObjectLayer, TileLayer
    cells = [0] * (width * height)
    doc.layers = [
        TileLayer(width=width, height=height, name="Ground", cells=list(cells)),
        TileLayer(width=width, height=height, name="Items", cells=list(cells)),
        TileLayer(width=width, height=height, name="Units", opacity=0.59,
                  cells=list(cells)),
        ObjectLayer(name="Triggers", readonly=True),
        TileLayer(width=width, height=height, name="set", visible=False,
                  opacity=0.25, cells=list(cells)),
    ]

    base_dir = Path(doc.path).parent
    existing: set[str] = set()
    for raw in tsx_paths:
        tsx = Path(raw)
        if not tsx.is_file():
            continue
        try:
            source = os.path.relpath(tsx, base_dir).replace("\\", "/")
        except ValueError:
            source = str(tsx).replace("\\", "/")
        if source in existing:
            continue
        firstgid = max((t.last_gid for t in doc.tilesets), default=0) + 1
        try:
            ts = read_tsx(tsx, firstgid, doc.resolver, source)
        except Exception:  # noqa: BLE001
            continue
        cmd = AddExternalTilesetCommand(doc, ts, source, firstgid, None, "添加图集")
        cmd.redo()  # 同步 doc.tilesets + DOM + rebuild_index
        existing.add(source)

    doc.rebuild_index()
    return doc


class NewMapDialog(QDialog):
    """收集 宽度 / 高度 / 图集勾选，``values()`` 一次性取走。"""

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建地图")
        self.resize(860, 560)

        def spin(value, lo, hi):
            s = PlainSpinBox(self)
            s.setRange(lo, hi)
            s.setValue(value)
            s.setSuffix(" 块")
            s.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return s

        self.sp_width = spin(64, 1, 100000)
        self.sp_height = spin(64, 1, 100000)

        box_size = QGroupBox("大小", self)
        form = QFormLayout(box_size)
        form.addRow("宽度:", self.sp_width)
        form.addRow("高度:", self.sp_height)

        self.list_tilesets = QListWidget(self)
        # 单击选中用于预览；勾选仍靠 ItemIsUserCheckable，两者互不干扰
        self.list_tilesets.setSelectionMode(QAbstractItemView.SingleSelection)
        items = _available_tilesets(profile)
        for label, path in items:
            it = QListWidgetItem(label)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if _default_selected(label) else Qt.Unchecked)
            it.setData(Qt.UserRole, path)
            self.list_tilesets.addItem(it)
        if not items:
            ph = QListWidgetItem("（版本配置里没有找到 .tsx 图集）")
            ph.setFlags(ph.flags() & ~Qt.ItemIsUserCheckable)
            self.list_tilesets.addItem(ph)

        box_ts = QGroupBox("包含图集", self)
        left = QVBoxLayout()
        left.addWidget(self.list_tilesets)
        row_ts = QHBoxLayout()
        btn_all = QPushButton("全选", self)
        btn_none = QPushButton("清空", self)
        btn_all.clicked.connect(lambda: self._check_all(True))
        btn_none.clicked.connect(lambda: self._check_all(False))
        row_ts.addWidget(btn_all)
        row_ts.addWidget(btn_none)
        row_ts.addStretch(1)
        left.addLayout(row_ts)

        self.chk_preview = QCheckBox("预览", self)
        self.chk_preview.setChecked(True)
        self.lbl_preview = QLabel(self)
        self.lbl_preview.setMinimumSize(_PREVIEW_MAX)
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setStyleSheet(
            f"background: {ASH}; border: 1px solid {ASH};")
        self.lbl_preview_info = QLabel("单击左侧图集查看预览", self)
        self.lbl_preview_info.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.lbl_preview_info.setWordWrap(True)
        right = QVBoxLayout()
        right.addWidget(self.chk_preview, 0, Qt.AlignRight)
        right.addWidget(self.lbl_preview, 1)
        right.addWidget(self.lbl_preview_info)
        right.setStretch(1, 1)

        lay_ts = QHBoxLayout(box_ts)
        lay_ts.addLayout(left, 1)
        lay_ts.addLayout(right, 0)

        self._resolver = ResourceResolver(Path("__preview__.tmx"), profile)
        self._preview_cache: dict[str, tuple[QPixmap | None, str]] = {}
        self.list_tilesets.currentItemChanged.connect(self._on_preview_item)
        self.chk_preview.toggled.connect(self._on_preview_toggled)
        if items:
            self.list_tilesets.setCurrentRow(0)

        self.lbl_hint = QLabel(
            f"已勾选 {self._checked_count()} 个图集 · 瓦片尺寸取自图集（默认 {RW_TILE_SIZE}×{RW_TILE_SIZE}）",
            self)
        self.lbl_hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.list_tilesets.itemChanged.connect(self._on_check_changed)

        # 中文按钮 + 足够点击区域（与其他对话框统一）
        buttons = QDialogButtonBox(self)
        ok = buttons.addButton("确定", QDialogButtonBox.AcceptRole)
        ok.setDefault(True)
        cancel = buttons.addButton("取消", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        for btn in (ok, cancel):
            btn.setMinimumSize(76, 28)

        layout = QVBoxLayout(self)
        layout.addWidget(box_size)
        layout.addWidget(box_ts, 1)
        layout.addWidget(self.lbl_hint)
        layout.addWidget(buttons)

    def _check_all(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.list_tilesets.count()):
            it = self.list_tilesets.item(i)
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(state)

    def _checked_count(self) -> int:
        return sum(1 for i in range(self.list_tilesets.count())
                   if (it := self.list_tilesets.item(i)) is not None
                   and (it.flags() & Qt.ItemIsUserCheckable)
                   and it.checkState() == Qt.Checked)

    def _on_check_changed(self, *_):
        self.lbl_hint.setText(
            f"已勾选 {self._checked_count()} 个图集 · 瓦片尺寸取自图集（默认 {RW_TILE_SIZE}×{RW_TILE_SIZE}）")

    def _on_preview_item(self, current: QListWidgetItem | None, *_):
        if current is None:
            self.lbl_preview.clear()
            self.lbl_preview_info.setText("单击左侧图集查看预览")
            return
        path = current.data(Qt.UserRole)
        if not path:
            self.lbl_preview.clear()
            self.lbl_preview_info.setText(current.text())
            return
        if path not in self._preview_cache:
            self._preview_cache[path] = _tileset_preview(path, self._resolver)
        pix, info = self._preview_cache[path]
        if pix is not None and not pix.isNull():
            self.lbl_preview.setPixmap(pix)
        else:
            self.lbl_preview.clear()
        self.lbl_preview_info.setText(info)

    def _on_preview_toggled(self, checked: bool):
        self.lbl_preview.setVisible(checked)
        self.lbl_preview_info.setVisible(checked)

    def values(self) -> dict:
        paths = []
        for i in range(self.list_tilesets.count()):
            it = self.list_tilesets.item(i)
            if it is not None and (it.flags() & Qt.ItemIsUserCheckable) \
                    and it.checkState() == Qt.Checked:
                paths.append(it.data(Qt.UserRole))
        return {
            "width": self.sp_width.value(),
            "height": self.sp_height.value(),
            "tilesets": paths,
        }


__all__ = ["NewMapDialog", "build_new_document", "RW_TILE_SIZE"]
