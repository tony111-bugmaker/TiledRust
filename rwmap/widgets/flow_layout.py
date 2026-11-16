"""自适应流式布局：按可用宽度把控件一行行排下去（能塞几个就塞几个）。

Qt 自带的 ``QBoxLayout`` 只能一维堆叠：竖排工具栏里 16 个图标就是 16 行，
既占高度又浪费横向空间。``FlowLayout`` 把控件当作流式文本排：从左往右填，
一行放不下就换行，所以同一个布局在宽面板里是 1 行、窄面板里自动变多行。

按 Qt 官方 Flow Layout 示例实现，额外加了两点项目里要用的能力：

* :meth:`width_for_columns` / :meth:`columns_for_width` 让调用方能在
  「按几列排」和「这个宽度能塞几列」之间换算（工具栏用它算自己的宽度）；
* 布局只统计可见控件，隐藏按钮（如某些版本没有的工具）不占格子。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """从左到右、从上到下排控件，宽度不够自动换行。"""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 2):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    # ---------------- QLayout 接口 ----------------
    def addItem(self, item):                     # noqa: N802 (Qt 命名)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):                # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):                # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):               # noqa: N802
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:         # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, max(1, width), 0), test_only=True)

    def setGeometry(self, rect):                 # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:                 # noqa: N802
        # 只要求「至少放得下一个格子」；具体排几列由父控件按宽度决定。
        return self.minimumSize()

    def minimumSize(self) -> QSize:              # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # ---------------- 尺寸换算 ----------------
    def _visible_items(self) -> list:
        out = []
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            out.append(item)
        return out

    def _cell(self) -> QSize:
        """单格尺寸（取第一个可见控件的 sizeHint）。"""
        items = self._visible_items()
        return items[0].sizeHint() if items else QSize(1, 1)

    def columns_for_width(self, width: int) -> int:
        """这个宽度一行能塞下几个控件。"""
        cell = self._cell()
        step = cell.width() + self._spacing
        if step <= 0:
            return 1
        m = self.contentsMargins()
        usable = max(1, width - m.left() - m.right())
        return max(1, (usable + self._spacing) // step)

    def width_for_columns(self, columns: int) -> int:
        """按 ``columns`` 列排版需要的宽度。"""
        cell = self._cell()
        m = self.contentsMargins()
        columns = max(1, columns)
        return (cell.width() * columns + self._spacing * (columns - 1)
                + m.left() + m.right())

    def _row_count(self, width: int) -> int:
        items = self._visible_items()
        columns = self.columns_for_width(width)
        if not items:
            return 0
        return (len(items) + columns - 1) // columns

    def height_for_width(self, width: int) -> int:
        cell = self._cell()
        rows = self._row_count(width)
        if rows <= 0:
            return 0
        m = self.contentsMargins()
        return (cell.height() * rows + self._spacing * (rows - 1)
                + m.top() + m.bottom())

    # ---------------- 实际排布 ----------------
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        right = effective.x() + effective.width()      # 不含右边距的可用右边界
        x, y, line_height = effective.x(), effective.y(), 0
        for item in self._visible_items():
            hint = item.sizeHint()
            if line_height > 0 and x + hint.width() > right:
                x = effective.x()
                y = y + line_height + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = x + hint.width() + self._spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


class FlowHost(QWidget):
    """装流式布局的容器，尺寸完全由「几列」决定，不依赖当前宽度（避免抖动）。

    * ``columns_hint > 0``：按这么多列算宽度（竖排工具栏用）。
    * ``columns_hint == 0``：全部塞进一行（横排工具栏用）。
    """

    def __init__(self, parent=None, columns_hint: int = 0, spacing: int = 2):
        super().__init__(parent)
        self.flow = FlowLayout(self, margin=0, spacing=spacing)
        self.columns_hint = max(0, columns_hint)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    # ---------------- 内容 ----------------
    def add_button(self, button) -> None:
        self.flow.addWidget(button)

    def buttons(self) -> list:
        return [item.widget() for item in self.flow._visible_items()]

    def preferred_width(self) -> int:
        """首选宽度：``columns_hint`` >0 按列数算；0 = 全部塞进一行。"""
        items = self.flow._visible_items()
        columns = self.columns_hint or max(1, len(items))
        return self.flow.width_for_columns(columns)

    def set_columns_hint(self, columns: int) -> None:
        columns = max(0, columns)
        if columns == self.columns_hint:
            return
        self.columns_hint = columns
        self.updateGeometry()

    def row_count(self) -> int:
        return self.flow._row_count(self.preferred_width())

    # ---------------- 尺寸 ----------------
    def height_for_width(self, width: int) -> int:
        return self.flow.height_for_width(width)

    def sizeHint(self) -> QSize:                 # noqa: N802
        width = self.preferred_width()
        return QSize(width, self.flow.height_for_width(width))

    def minimumSizeHint(self) -> QSize:          # noqa: N802
        return self.sizeHint()

    def hasHeightForWidth(self) -> bool:         # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self.flow.height_for_width(width)
