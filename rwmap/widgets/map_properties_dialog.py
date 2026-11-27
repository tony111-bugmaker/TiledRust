"""地图属性编辑对话框：编辑 <map> 级别自定义属性的键值对。

只负责收集字符串键值，DOM / PropertyBag 的写回交给 EditMapPropertiesCommand，
保证「一条撤销可还原」。禁止改 RW 内部键（保留原样由命令层处理）。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class MapPropertiesDialog(QDialog):
    """可增删改行的两列属性表：确定后 ``values()`` 返回新字典。"""

    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("地图属性")
        self.resize(420, 320)

        self.table = QTableWidget(len(values), 2, self)
        self.table.setHorizontalHeaderLabels(["键", "值"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        for r, (k, v) in enumerate(values.items()):
            self.table.setItem(r, 0, QTableWidgetItem(str(k)))
            self.table.setItem(r, 1, QTableWidgetItem(str(v)))

        btn_add = QPushButton("添加", self)
        btn_add.clicked.connect(self._add_row)
        btn_del = QPushButton("删除", self)
        btn_del.clicked.connect(self._del_row)
        row_btns = QHBoxLayout()
        row_btns.addWidget(btn_add)
        row_btns.addWidget(btn_del)
        row_btns.addStretch(1)

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
        layout.addWidget(self.table, 1)
        layout.addLayout(row_btns)
        layout.addWidget(buttons)

    def _add_row(self):
        self.table.insertRow(self.table.rowCount())

    def _del_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def values(self) -> dict:
        out = {}
        for r in range(self.table.rowCount()):
            k_item = self.table.item(r, 0)
            v_item = self.table.item(r, 1)
            key = k_item.text().strip() if k_item is not None else ""
            if not key:
                continue
            out[key] = v_item.text() if v_item is not None else ""
        return out
