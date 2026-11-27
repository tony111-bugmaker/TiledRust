"""软件内单位图鉴：浏览游戏本体的单位外观、中文名、属性与贴图部件。

只读游戏资源；与离线图鉴 ``logs/单位贴图图鉴.html`` 共用
``rwmap.rw.unit_catalog`` 的扫描和拼装实现。多部件单位显示拼好的完整外观。
"""
from __future__ import annotations

import html

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
    QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from ..rw import unit_catalog
from ..rw.unit_catalog import UnitInfo, cat_zh, game_root, scan_units
from ..theme import ASH, INK, TEXT, TEXT_DIM
from ..tilesets.tile_cache import pil_to_qpixmap

_ICON = 72


def _fit(unit: UnitInfo, size: int = 220) -> QPixmap:
    """优先拼装图，其次精灵条首帧；等比缩放，不拉伸。"""
    img: Image.Image | None = unit.composed or unit.thumb
    if img is None:
        return QPixmap()
    img = img.copy()
    img.thumbnail((size, size), Image.LANCZOS)
    return pil_to_qpixmap(img)


class UnitGalleryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("单位图鉴 · 外观 / 贴图 / 中文名")
        self.resize(920, 640)
        self.setStyleSheet(
            f"QDialog{{background:{INK};color:{TEXT};}}"
            f"QLineEdit,QComboBox,QListWidget,QTextBrowser{{"
            f"background:{INK};color:{TEXT};border:1px solid {ASH};"
            f"border-radius:3px;padding:3px;}}"
            f"QPushButton{{background:{ASH};color:{TEXT};border:1px solid {ASH};"
            f"border-radius:3px;padding:4px 12px;}}"
            f"QCheckBox{{color:{TEXT_DIM};}}"
        )
        self._units: list[UnitInfo] = []
        self._loaded = False
        self._current: UnitInfo | None = None

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索中文名 / 内部名 / 描述")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search, 1)

        self.category = QComboBox()
        self.category.setFixedWidth(120)
        self.category.addItem("全部分类")
        self.category.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.category)

        self.only_multi = QCheckBox("仅多部件")
        self.only_multi.toggled.connect(self._apply_filter)
        bar.addWidget(self.only_multi)
        self.only_zh = QCheckBox("仅中文名")
        self.only_zh.toggled.connect(self._apply_filter)
        bar.addWidget(self.only_zh)

        refresh = QPushButton("刷新")
        refresh.setToolTip("游戏资源变动后重新扫描（已缓存则直接重读）")
        refresh.clicked.connect(self._reload)
        bar.addWidget(refresh)
        root.addLayout(bar)

        self.status = QLabel("正在准备单位目录…")
        self.status.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        root.addWidget(self.status)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(_ICON, _ICON))
        self.list.setSpacing(8)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setUniformItemSizes(True)
        self.list.itemClicked.connect(self._show_detail)
        split.addWidget(self.list)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setMinimumHeight(240)
        self.picture.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.picture.setStyleSheet(f"background:{INK};")
        rv.addWidget(self.picture)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        rv.addWidget(self.detail, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        copy_name = QPushButton("复制内部名")
        copy_name.clicked.connect(self._copy_name)
        foot.addWidget(copy_name)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        foot.addWidget(close_btn)
        root.addLayout(foot)

        QTimer.singleShot(0, self._reload)

    # ---- 数据 ----
    def _reload(self) -> None:
        root = game_root()
        if root is None:
            self.status.setText("未找到游戏目录，请在配置中设置 1.15 游戏根目录")
            self.detail.setPlainText("无法读取单位资源。")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            unit_catalog.clear_cache()
            self._units = scan_units(root)
            self._loaded = True
            cats = sorted({c for u in self._units for c in u.categories}, key=cat_zh)
            current = self.category.currentText()
            self.category.blockSignals(True)
            self.category.clear()
            self.category.addItem("全部分类")
            self.category.addItems([cat_zh(c) for c in cats])
            idx = self.category.findText(current)
            self.category.setCurrentIndex(max(0, idx))
            self.category.blockSignals(False)
        finally:
            QApplication.restoreOverrideCursor()
        if not self._units:
            self.status.setText(f"没找到单位定义：{root / 'assets' / 'units'}")
            return
        self._apply_filter()

    def _visible(self) -> list[UnitInfo]:
        q = self.search.text().strip().lower()
        cat = self.category.currentText()
        out = []
        for u in self._units:
            if cat != "全部分类" and cat not in [cat_zh(c) for c in u.categories]:
                continue
            if self.only_multi.isChecked() and not u.multi:
                continue
            if self.only_zh.isChecked() and not u.zh:
                continue
            if q:
                hay = " ".join([u.zh, u.en, u.name, u.desc, *u.aliases]).lower()
                if q not in hay:
                    continue
            out.append(u)
        return out

    def _apply_filter(self) -> None:
        if not self._loaded:
            return
        units = self._visible()
        self.list.clear()
        for u in units:
            item = QListWidgetItem(u.label)
            item.setIcon(pil_to_qpixmap(
                (u.composed or u.thumb or Image.new("RGBA", (16, 16), (0, 0, 0, 0)))))
            item.setData(Qt.UserRole, u)
            item.setToolTip(f"{u.label}（{u.name}）"
                            + (" · 多部件拼接" if u.multi else ""))
            self.list.addItem(item)
        self.status.setText(
            f"{len(units)} / {len(self._units)} 个单位 · 多部件 "
            f"{sum(1 for u in self._units if u.multi)} 个"
            + (" · 点卡片看大图与部件" if units else " · 没有匹配的单位"))
        self.picture.clear()
        self.detail.clear()
        self._current = None
        if units:
            self.list.setCurrentRow(0)
            self._show_detail(self.list.item(0))

    def _show_detail(self, item: QListWidgetItem) -> None:
        unit: UnitInfo | None = item.data(Qt.UserRole) if item else None
        if unit is None:
            return
        self._current = unit
        self.picture.setPixmap(_fit(unit))
        esc = html.escape
        lines = [
            f'<h3 style="color:{TEXT};margin:0 0 6px 0">'
            f'{esc(unit.label)} <span style="color:{TEXT_DIM};font-size:11px">'
            f'{esc(unit.name)}</span></h3>',
        ]
        if unit.desc:
            lines.append(f'<p style="color:{TEXT_DIM}">{esc(unit.desc)}</p>')
        lines.append('<table style="color:' + TEXT + ';font-size:12px">')
        rows = [("分类", " / ".join(map(cat_zh, unit.categories)))]
        for key in ("price", "maxhp", "techlevel", "buildspeed", "speed", "radius",
                    "mass", "isbio", "isbuilding", "isunit", "canattack"):
            if key in unit.props:
                rows.append((key, unit.props[key]))
        rows.append(("帧数", str(unit.frames)))
        rows.append(("定义文件", unit.source))
        for k, v in rows:
            lines.append(f'<tr><td style="color:{TEXT_DIM};padding-right:10px">'
                         f'{esc(k)}</td><td>{esc(str(v))}</td></tr>')
        lines.append('</table>')
        if unit.multi:
            lines.append(f'<p style="color:{TEXT_DIM};margin:10px 0 3px 0">'
                         f'组成部件（{len(unit.parts)} 种，按 ini 坐标拼成静止姿态）</p><ul>')
            for part in unit.parts:
                lines.append(f'<li>{esc(part["kind"])}：{esc(part["name"])}'
                             f' × {part.get("count", 1)}</li>')
            lines.append('</ul>')
        if unit.aliases:
            lines.append(f'<p style="color:{TEXT_DIM};font-size:11px">别名：'
                         f'{esc(", ".join(unit.aliases))}</p>')
        self.detail.setHtml("".join(lines))
        self.status.setText(
            f"{len(self._visible())} / {len(self._units)} 个单位 · 当前：{unit.label}"
            + (" · 多部件拼接" if unit.multi else ""))

    def _copy_name(self) -> None:
        if self._current:
            QApplication.clipboard().setText(self._current.name)
            self.status.setText(f"已复制内部名：{self._current.name}")


__all__ = ["UnitGalleryDialog"]
