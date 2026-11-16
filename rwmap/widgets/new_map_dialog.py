"""「地图 → 新建地图」对话框。

只要三类输入，符合铁锈地图的实际用法：

- 宽度（块）/ 高度（块）；
- 要包含哪些图集（从版本配置里的 tilesets_root 列出 .tsx，可勾选）。

不再让用户输入「瓦片宽 / 瓦片高」——铁锈原生固定 20×20；若勾选的图集
里写明了 tilewidth/tileheight，则自动采用第一个图集的尺寸。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QSpinBox, QVBoxLayout,
)

from ..commands.document_command import AddExternalTilesetCommand
from ..io.tsx_reader import read_tsx
from ..io.tmx_writer import create_blank_document
from ..theme import TEXT_DIM

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


def build_new_document(profile, width: int, height: int, tsx_paths: list[str]):
    """构建新地图文档：空白 Ground 瓦片层 + 勾选的外部图集引用。

    纯函数式构造，不走撤销栈（文档还没挂到窗口上下文）。
    """
    # 瓦片尺寸：优先取第一个可读取图集里写明的尺寸，否则用铁锈默认 20。
    tile_w = tile_h = RW_TILE_SIZE
    for raw in tsx_paths:
        try:
            probe = read_tsx(Path(raw), 1, None, Path(raw).name)
        except Exception:  # noqa: BLE001 - 读失败交给后面正式添加时提示
            continue
        tile_w, tile_h = max(1, probe.tile_w), max(1, probe.tile_h)
        break

    doc = create_blank_document(profile, width, height, tile_w, tile_h)
    from ..document.layer import TileLayer
    doc.layers = [TileLayer(width=width, height=height, name="Ground",
                            cells=[0] * (width * height))]

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
        self.resize(560, 560)

        def spin(value, lo, hi):
            s = QSpinBox(self)
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
        self.list_tilesets.setSelectionMode(QAbstractItemView.NoSelection)
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
        lay_ts = QVBoxLayout(box_ts)
        lay_ts.addWidget(self.list_tilesets)
        row_ts = QHBoxLayout()
        btn_all = QPushButton("全选", self)
        btn_none = QPushButton("清空", self)
        btn_all.clicked.connect(lambda: self._check_all(True))
        btn_none.clicked.connect(lambda: self._check_all(False))
        row_ts.addWidget(btn_all)
        row_ts.addWidget(btn_none)
        row_ts.addStretch(1)
        lay_ts.addLayout(row_ts)

        self.lbl_hint = QLabel(
            f"已勾选 {self._checked_count()} 个图集 · 瓦片尺寸取自图集（默认 {RW_TILE_SIZE}×{RW_TILE_SIZE}）",
            self)
        self.lbl_hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.list_tilesets.itemChanged.connect(self._on_check_changed)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

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
