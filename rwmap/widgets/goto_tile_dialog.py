"""「转到图块」对话框：列 X / 行 Y 一次输入、一次确认。

替代此前两次 QInputDialog.getInt（先列后行）。窗口纵向排列两个输入框，
高度随内容增长。数字框沿用项目统一的 PlainSpinBox（无箭头、无滚轮）。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout,
)

from ..widgets.plain_spinbox import PlainSpinBox


class GotoTileDialog(QDialog):
    def __init__(self, parent=None, max_col=0, max_row=0, col=0, row=0):
        super().__init__(parent)
        self.setWindowTitle("转到图块")

        form = QFormLayout()
        self.col_spin = PlainSpinBox()
        self.col_spin.setRange(0, max(0, max_col))
        self.col_spin.setValue(min(max(0, col), max(0, max_col)))
        form.addRow("列 (X):", self.col_spin)

        self.row_spin = PlainSpinBox()
        self.row_spin.setRange(0, max(0, max_row))
        self.row_spin.setValue(min(max(0, row), max(0, max_row)))
        form.addRow("行 (Y):", self.row_spin)

        self.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        layout.addLayout(form)

        # 与新建图集对话框相同：按钮给足点击区域，避免误点落空。
        buttons = QDialogButtonBox(self)
        ok = buttons.addButton("确定", QDialogButtonBox.AcceptRole)
        ok.setDefault(True)
        cancel = buttons.addButton("取消", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        for btn in (ok, cancel):
            btn.setMinimumSize(72, 28)
        layout.addWidget(buttons)

    def values(self) -> tuple[int, int]:
        return self.col_spin.value(), self.row_spin.value()
