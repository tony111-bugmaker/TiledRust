"""地图变换 4 图标（水平/垂直翻转、顺/逆时针旋转）。

外形参考 Tiled 对象工具栏：两个镜像三角 + 绿色弯曲箭头。
画法按本软件统一图标风格重绘（不是从截图抠图）：

* 亮蓝描边 + 淡蓝半透明填充 —— 与 object_toolbar 其余工具图标同一套视觉语言；
* 旋转箭头保留语义绿色（功能色，不参与双色收敛）；
* 28×28 透明底，抗锯齿绘制，缩放不糊。

``transform_icon(name)`` 的接口保持不变，object_toolbar 直接调用。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

# 与 object_toolbar._ICON_BLUE 完全一致（不 import 那边，避免循环依赖）。
_STROKE = QColor(80, 170, 245)
_FILL = QColor(90, 175, 245, 70)
_ARROW_FILL = QColor(126, 203, 72)
_ARROW_EDGE = QColor(72, 145, 46)
_PEN = QPen(_STROKE, 1.9)
_PEN.setJoinStyle(Qt.RoundJoin)


def _poly(points):
    return QPolygonF([QPointF(x, y) for x, y in points])


def _tri(p: QPainter, points) -> None:
    p.setPen(_PEN)
    p.setBrush(_FILL)
    p.drawPolygon(_poly(points))


def _draw_flip_h(p: QPainter) -> None:
    """左右两个镜像直角三角形，中间留 4px 缝（Tiled 水平翻转的轮廓）。"""
    _tri(p, [(4.0, 24.0), (12.0, 4.5), (12.0, 24.0)])
    _tri(p, [(16.0, 4.5), (16.0, 24.0), (24.0, 24.0)])


def _draw_flip_v(p: QPainter) -> None:
    """上下两个镜像直角三角形，尖角朝右（Tiled 垂直翻转的轮廓）。"""
    _tri(p, [(4.0, 12.0), (24.0, 4.0), (24.0, 12.0)])
    _tri(p, [(4.0, 16.0), (24.0, 16.0), (24.0, 24.0)])


def _arc_point(cx, cy, rx, ry, deg):
    """Qt 的角度：0° 在右、90° 在上，y 轴向下，故 sin 取负。"""
    a = math.radians(deg)
    return cx + rx * math.cos(a), cy - ry * math.sin(a)


def _draw_rotate(p: QPainter, mirror: bool = False) -> None:
    """两个全等直角三角形（原样 + 旋转后）+ 顶部拱形绿箭头。

    ``mirror`` 时整组水平镜像成逆时针，两个三角与箭头的相对关系不变。
    """
    p.save()
    if mirror:
        p.translate(28, 0)
        p.scale(-1, 1)

    # 左：竖放的直角三角形，直角在右下，长直角边竖直（12px）、短边水平（7px），
    #     斜边由左下向右上 —— 这是「旋转前」的形状。
    _tri(p, [(4.0, 25.0), (11.0, 13.0), (11.0, 25.0)])
    # 右：同一三角形顺时针转 90° —— 长边转为水平、短边竖直，直角在左下，
    #     斜边由左上向右下。两片全等（12×7），只是朝向不同，中间留 4px 缝。
    _tri(p, [(15.0, 18.0), (15.0, 25.0), (27.0, 25.0)])

    # 顶部弧形箭头：从左上方绕到右上方的顺时针弧（Qt 正角朝上，负向扫过 90°）。
    box = QRectF(7.0, 3.0, 14.0, 9.0)
    path = QPainterPath()
    # 先把当前点移到弧起点，否则 arcTo 会从 (0,0) 连一条多余的斜线过来。
    sx, sy = _arc_point(14.0, 7.5, 7.0, 4.5, 160)
    path.moveTo(sx, sy)
    path.arcTo(box, 160, -140)
    p.setPen(QPen(_ARROW_EDGE, 2.4))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(QPen(_ARROW_FILL, 1.4))
    p.drawPath(path)

    # 箭头头部落在弧末端（20°），沿切线指向右下。
    ex, ey = _arc_point(14.0, 7.5, 7.0, 4.5, 20)
    head = _poly([(ex + 0.9, ey + 2.4), (ex - 2.2, ey - 0.3),
                  (ex + 1.5, ey - 1.6)])
    p.setPen(QPen(_ARROW_EDGE, 1.1))
    p.setBrush(_ARROW_FILL)
    p.drawPolygon(head)
    p.restore()


_DRAW = {
    "flip_h": _draw_flip_h,
    "flip_v": _draw_flip_v,
    "rot_cw": _draw_rotate,
    "rot_ccw": lambda p: _draw_rotate(p, mirror=True),
}


def transform_icon(name: str) -> QIcon:
    """按语义名画一枚 28×28 的统一风格变换图标。"""
    pix = QPixmap(28, 28)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    _DRAW[name](p)
    p.end()
    return QIcon(pix)
