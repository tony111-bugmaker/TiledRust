"""对象复制时按来源组询问新组名的对话框。

一个来源组问一次；三个出口：
- 「归到原来的组中」：副本沿用原组名；
- OK：使用输入框里的新组名；
- Cancel：放弃整个复制操作。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

KEEP_ORIGINAL = "keep_original"
RENAME = "rename"
CANCEL = "cancel"


class CopyGroupDialog(QDialog):
    def __init__(self, group_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("复制组")
        self.setModal(True)
        self._action = CANCEL

        label = QLabel(f"从「{group_name}」复制的内容名字改为：")
        self.name_edit = QLineEdit(f"{group_name}_副本")
        self.name_edit.selectAll()

        self._keep_btn = QPushButton("归到原来的组中")
        self._ok_btn = QPushButton("OK")
        self._cancel_btn = QPushButton("Cancel")
        self._ok_btn.setDefault(True)
        self._keep_btn.clicked.connect(self._on_keep)
        self._ok_btn.clicked.connect(self.accept)
        self._cancel_btn.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(self._keep_btn)
        row.addStretch(1)
        row.addWidget(self._ok_btn)
        row.addWidget(self._cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(self.name_edit)
        layout.addLayout(row)
        self.name_edit.setFocus()

    def _on_keep(self):
        self._action = KEEP_ORIGINAL
        self.accept()

    def exec_data(self) -> tuple[str, str]:
        """返回 (KEEP_ORIGINAL / RENAME / CANCEL, 新组名)。"""
        if self.exec() != QDialog.Accepted:
            return CANCEL, ""
        if self._action == KEEP_ORIGINAL:
            return KEEP_ORIGINAL, ""
        return RENAME, self.name_edit.text().strip()


def ask_copy_group_name(parent, group_name: str) -> tuple[str, str]:
    """模态询问一个来源组的目标组名，便于在主窗口里统一打桩测试。"""
    return CopyGroupDialog(group_name, parent).exec_data()
