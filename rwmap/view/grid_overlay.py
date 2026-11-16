"""网格线（只画瓦片边界，不再画光标高亮）。

曾经的「鼠标悬停黄色单格框」已按需求移除：它只是画笔光标提示，用户
只想去掉这一个框，右键拖动抓取 / 多格图章的蓝色框、对象层黄色对象框、
矩形选框都要保留，所以这里不再绘制任何跟随光标的矩形，也不再因为
hover 移动标脏旧 / 新两格。

网格本身仍按「只画暴露区域」裁剪：``boundingRect()`` 覆盖整张地图，
若整层 update() 会让 Qt 重画大图 overlay，鼠标每动一下都重绘全图。

优化（零视觉改动）：
- QPen / QColor 提为模块常量，避免每帧构造；仍然 width(0) cosmetic，
  线宽恒为 1 设备像素、颜色仍是 (255,255,255,35)；
- 网格线坐标在 __init__ 预算一次，paint 不再逐线做乘法；
- 用户开关上移为 setVisible()，但 0.5 缩放判断必须留在 paint() 内：
  scene.render() 用的是 painter 自己的变换，持久 setVisible 会让导出 /
  离屏渲染路径丢网格线；
- 曾试过用一次 QPainterPath 描边替代逐条 drawLine：逐像素一致，
  但公平微基准（每次清空背景 + 交替顺序）显示耗时基本持平、噪声内
  甚至略慢，因此保留原本的 drawLine，不做无收益的改动。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsItem

_GRID_PEN = QPen(QColor(255, 255, 255, 35))
_GRID_PEN.setWidth(0)                 # cosmetic：1 设备像素，不随缩放变粗
_GRID_MIN_SCALE = 0.5


class GridOverlay(QGraphicsItem):
    def __init__(self, doc, show_grid: bool = True):
        super().__init__()
        self.doc = doc
        self.cell: tuple[int, int] | None = None
        self.show_grid = bool(show_grid)
        self.setVisible(self.show_grid)
        self.setZValue(10000)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)
        # 坐标表含右侧 / 底部闭合边界，长度 width+1 / height+1。
        self._xs = tuple(c * doc.tile_w for c in range(doc.width + 1))
        self._ys = tuple(r * doc.tile_h for r in range(doc.height + 1))
        self._w = doc.width * doc.tile_w
        self._h = doc.height * doc.tile_h

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def set_show_grid(self, visible: bool):
        """用户开关：持久可见性由 Qt 管理，paint 内只判缩放。"""
        self.show_grid = bool(visible)
        self.setVisible(self.show_grid)

    def set_cell(self, col: int, row: int):
        """兼容旧调用：只记录光标格，不绘制、不标脏。

        ``MapScene.set_cursor_cell`` 每次 hover 都会调到这里；保留 ``cell``
        以便诊断或日后恢复高亮，但不再触发 update（没有可见内容需要重画）。
        """
        self.cell = (col, row)

    def paint(self, painter, option, widget=None):
        scale = painter.worldTransform().m11()
        if not self.show_grid or scale < _GRID_MIN_SCALE:
            return
        rect = option.exposedRect
        painter.setPen(_GRID_PEN)
        # 端点与旧实现逐值相同，保证逐像素等价。
        left = max(0, rect.left())
        right = min(self._w, rect.right())
        top = max(0, rect.top())
        bottom = min(self._h, rect.bottom())
        tw, th = self.doc.tile_w, self.doc.tile_h
        col0 = max(0, int(rect.left() // tw))
        col1 = min(self.doc.width, int(rect.right() // tw) + 1)
        row0 = max(0, int(rect.top() // th))
        row1 = min(self.doc.height, int(rect.bottom() // th) + 1)
        for col in range(col0, col1 + 1):
            x = self._xs[col]
            painter.drawLine(x, top, x, bottom)
        for row in range(row0, row1 + 1):
            y = self._ys[row]
            painter.drawLine(left, y, right, y)
        # 悬停黄色单格框已移除：这里刻意不画任何跟随光标的矩形。
