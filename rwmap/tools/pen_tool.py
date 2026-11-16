"""钢笔工具的纯数据模型（零 Qt 依赖）。

每个锚点带一对**严格对称**的控制柄：``hin == -hout``。
所有修改入口都维护这条不变式，界面层不提供任何能破坏对称的操作
（用户明确选了「全程严格对称」）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..utils.bezier import Point, Segment, flatten_segments


@dataclass
class Anchor:
    x: float
    y: float
    hin: tuple[float, float] = (0.0, 0.0)    # in 柄相对向量
    hout: tuple[float, float] = (0.0, 0.0)   # out 柄相对向量

    def pos(self) -> Point:
        return (self.x, self.y)

    def in_pt(self) -> Point:
        return (self.x + self.hin[0], self.y + self.hin[1])

    def out_pt(self) -> Point:
        return (self.x + self.hout[0], self.y + self.hout[1])

    def set_out(self, dx: float, dy: float) -> None:
        """拖 out 柄：in 柄镜像跟随。"""
        self.hout = (dx, dy)
        self.hin = (-dx, -dy)

    def set_in(self, dx: float, dy: float) -> None:
        """拖 in 柄：out 柄镜像跟随。"""
        self.hin = (dx, dy)
        self.hout = (-dx, -dy)

    def set_symmetric(self, hx: float, hy: float) -> None:
        self.hout = (hx, hy)
        self.hin = (-hx, -hy)

    @classmethod
    def corner(cls, x: float, y: float) -> "Anchor":
        return cls(x, y, (0.0, 0.0), (0.0, 0.0))


@dataclass
class PenPath:
    anchors: list[Anchor] = field(default_factory=list)

    def segments(self) -> list[Segment]:
        out: list[Segment] = []
        for i in range(len(self.anchors) - 1):
            a, b = self.anchors[i], self.anchors[i + 1]
            out.append((a.pos(), a.out_pt(), b.in_pt(), b.pos()))
        return out

    def insert_anchor(self, index: int, anchor: Anchor) -> None:
        self.anchors.insert(index, anchor)

    def flatten(self, tol: float = 0.5) -> list[Point]:
        return flatten_segments(self.segments(), tol)

    def bounds(self):
        """含控制柄的外接框 (x0, y0, x1, y1)；空路径返回 None。"""
        pts: list[Point] = []
        for a in self.anchors:
            pts.extend((a.pos(), a.in_pt(), a.out_pt()))
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def copy(self) -> "PenPath":
        return PenPath([Anchor(a.x, a.y, a.hin, a.hout) for a in self.anchors])
