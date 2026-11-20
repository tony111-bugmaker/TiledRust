"""无上下箭头、滚轮不改变数值的数字输入框。

软件统一约定（2026-11-17）：所有数字输入框去掉 QSpinBox 原生的上下箭头，
且鼠标滚轮悬停时不再增减数值——避免误碰滚轮改掉尺寸/权重等参数。

实现：
- setButtonSymbols(NoButtons)：去掉上下箭头；
- wheelEvent 直接忽略事件：数值不变，事件继续交给父控件（如滚动区）处理。
"""
from __future__ import annotations

from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QSpinBox


class _NoWheelMixin:
    """滚轮不改数值：吞掉 wheel 事件但不接受，让父控件仍可滚动。"""

    def wheelEvent(self, event: QWheelEvent):  # noqa: N802
        event.ignore()


class PlainSpinBox(_NoWheelMixin, QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)


class PlainDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
