"""「设置 → 面板列表设置」对话框：勾选显隐 + 面板清单 + 恢复默认布局。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from ..app.panel_layout import (
    NON_DOCK_UI, apply_default_layout, describe_panels, iter_panels,
    panel_inventory,
)
from ..theme import ASH, INK, TEXT, TEXT_DIM

# 双色暗色主题：INK（近黑底层）+ ASH（偏黑灰抬起层），文字用可读浅色。
_TREE_STYLE = f"""
QTreeWidget {{
    background: {INK}; color: {TEXT};
    border: 1px solid {ASH}; font-size: 12px;
}}
QTreeWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:selected {{ background: {ASH}; }}
QHeaderView::section {{
    background: {ASH}; color: {TEXT_DIM};
    border: 1px solid {INK}; padding: 3px 6px;
}}
"""


class PanelSettingsDialog(QDialog):
    """勾选要显示的面板；「确定」才生效，「取消」保持原状。"""

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.applied_count = 0
        self._items: dict[str, QTreeWidgetItem] = {}
        self.setWindowTitle("面板列表设置")
        self.resize(760, 400)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["面板（勾选 = 显示）", "当前停靠", "功能说明"])
        self.tree.setStyleSheet(_TREE_STYLE)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setColumnWidth(0, 190)
        self.tree.setColumnWidth(1, 90)

        note = QLabel(
            "非停靠界面：" + "；".join(f"{n}（{d}）" for n, d in NON_DOCK_UI)
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px;")

        self.btn_all = QPushButton("全部显示")
        self.btn_none = QPushButton("全部隐藏")
        self.btn_default = QPushButton("恢复默认布局")
        self.btn_copy = QPushButton("复制清单")
        self.btn_cancel = QPushButton("取消")
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setDefault(True)

        row = QHBoxLayout()
        for btn in (self.btn_all, self.btn_none, self.btn_default, self.btn_copy):
            row.addWidget(btn)
        row.addStretch(1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_ok)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.tree, 1)
        layout.addWidget(note)
        layout.addLayout(row)

        self.btn_all.clicked.connect(lambda: self._set_all(True))
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        self.btn_default.clicked.connect(self._restore_default)
        self.btn_copy.clicked.connect(self._copy_inventory)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)
        self._refresh()

    def _refresh(self):
        self.tree.clear()
        self._items.clear()
        rows = panel_inventory(self.window)
        for row in rows:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, row["title"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if row["visible"] else Qt.Unchecked)
            item.setData(0, Qt.UserRole, row["key"])
            item.setText(1, row["area"])
            item.setText(2, row["description"])
            item.setToolTip(2, row["description"])
            self._items[row["key"]] = item
        shown = sum(1 for r in rows if r["visible"])
        self.hint.setText(
            f"共 {len(rows)} 个可停靠面板，当前显示 {shown} 个。"
            "取消勾选只是隐藏，不会删除面板或改动地图数据。"
        )

    def _set_all(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for item in self._items.values():
            item.setCheckState(0, state)

    def _restore_default(self):
        apply_default_layout(self.window)
        self._refresh()
        self.hint.setText(self.hint.text() + "  已恢复默认布局。")

    def _copy_inventory(self):
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(describe_panels(self.window))
        self.hint.setText(self.hint.text() + "  清单已复制到剪贴板。")

    def checked_keys(self) -> set[str]:
        return {key for key, item in self._items.items()
                if item.checkState(0) == Qt.Checked}

    def apply_visibility(self) -> int:
        """把勾选状态写到 dock 上，返回显示中的面板数。"""
        checked = self.checked_keys()
        shown = 0
        for spec, dock in iter_panels(self.window):
            visible = spec.key in checked
            if spec.floating and visible and not dock.isFloating():
                # 独立窗口类面板：在「面板列表设置」里勾选时也要以浮动窗口打开
                dock.setFloating(True)
            dock.setVisible(visible)
            shown += int(visible)
        self.applied_count = shown
        return shown

    def accept(self):
        self.apply_visibility()
        super().accept()
