"""「新建图集」对话框：用一张图集大图生成铁锈瓦片图集（.tsx）。

用户选一张游戏导出的图集大图，填名称与瓦片宽高（默认 20，对齐当前地图），
按图片尺寸算出 columns / tilecount，构建 TilesetDocument。不在此处画画，
图集建好后由图集编辑器（TilesetEditorWidget）调每个瓦片的属性。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from ..document.tileset import Tileset
from ..widgets.plain_spinbox import PlainSpinBox
from ..document.tileset_document import TilesetDocument
from ..theme import TEXT_DIM


class NewTilesetDialog(QDialog):
    def __init__(self, parent=None, default_tile: int = 20):
        super().__init__(parent)
        self.setWindowTitle("新建图集（铁锈瓦片）")
        self.image_path: Path | None = None
        self.document: TilesetDocument | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.image_edit = QLineEdit()
        self.image_edit.setReadOnly(True)
        pick_btn = QPushButton("选择图集大图…")
        pick_btn.clicked.connect(self._pick_image)
        img_row = QHBoxLayout()
        img_row.addWidget(self.image_edit, 1)
        img_row.addWidget(pick_btn)
        form.addRow("图集图片", img_row)

        self.name_edit = QLineEdit("新图集")
        form.addRow("名称", self.name_edit)

        self.tw_spin = PlainSpinBox()
        self.tw_spin.setRange(1, 512)
        self.tw_spin.setValue(default_tile)
        form.addRow("瓦片宽 (px)", self.tw_spin)

        self.th_spin = PlainSpinBox()
        self.th_spin.setRange(1, 512)
        self.th_spin.setValue(default_tile)
        form.addRow("瓦片高 (px)", self.th_spin)

        self.margin_spin = PlainSpinBox()
        self.margin_spin.setRange(0, 64)
        self.margin_spin.setValue(0)
        form.addRow("边距 margin", self.margin_spin)

        self.spacing_spin = PlainSpinBox()
        self.spacing_spin.setRange(0, 64)
        self.spacing_spin.setValue(0)
        form.addRow("间隔 spacing", self.spacing_spin)

        self.info = QLabel("请先选择一张图集大图")
        self.info.setStyleSheet(f"color:{TEXT_DIM};")
        form.addRow(self.info)

        layout.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("创建")
        ok.setDefault(True)
        ok.clicked.connect(self._on_accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    # ---------------- 交互 ----------------
    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图集大图", "",
            "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)")
        if not path:
            return
        self.image_path = Path(path)
        self.image_edit.setText(self.image_path.name)
        self._update_info()

    def _update_info(self):
        if self.image_path is None or not self.image_path.is_file():
            self.info.setText("请先选择一张图集大图")
            return
        try:
            from PIL import Image
            with Image.open(self.image_path) as img:
                w, h = img.size
        except Exception as exc:  # noqa: BLE001
            self.info.setText(f"无法读取图片：{exc}")
            return
        cols, rows = self._grid(w, h)
        if cols <= 0 or rows <= 0:
            self.info.setText(
                f"图片 {w}×{h}：按当前参数切不出瓦片（检查宽高/边距/间隔）")
            return
        self.info.setText(
            f"图片 {w}×{h} → {cols} 列 × {rows} 行 = {cols * rows} 块")

    def _grid(self, w: int, h: int):
        tw = self.tw_spin.value()
        th = self.th_spin.value()
        m = self.margin_spin.value()
        s = self.spacing_spin.value()
        step_x = max(1, tw + s)
        step_y = max(1, th + s)
        cols = max(0, (w - 2 * m + s) // step_x)
        rows = max(0, (h - 2 * m + s) // step_y)
        return cols, rows

    def _on_accept(self):
        if self.image_path is None or not self.image_path.is_file():
            self.info.setText("请先选择一张图集大图")
            return
        from PIL import Image
        with Image.open(self.image_path) as img:
            w, h = img.size

        tw = self.tw_spin.value()
        th = self.th_spin.value()
        m = self.margin_spin.value()
        s = self.spacing_spin.value()
        cols, rows = self._grid(w, h)
        if cols <= 0 or rows <= 0:
            self.info.setText("按当前参数切不出瓦片，请调整尺寸/边距/间隔")
            return

        ts = Tileset(
            firstgid=0,
            name=self.name_edit.text().strip() or "新图集",
            tile_w=tw, tile_h=th, spacing=s, margin=m,
            tilecount=cols * rows, columns=cols,
            image_source=self.image_path.name,
            image_w=w, image_h=h,
        )
        self.document = TilesetDocument(
            path=Path(self.image_path).with_suffix(".tsx"),
            tileset=ts,
            image_path=self.image_path,
            dirty=True,
        )
        self.accept()
