"""对象层中的对象（Triggers / UnitObjects）。

支持 Tiled 的全部对象形状：rect / ellipse / point / polygon / polyline /
tile（gid 图块对象）/ text（文字对象），以及本编辑器扩展的 image（图片对象）。
TMX 原生没有 image object，图片路径以自定义属性 ``image`` 保存，Tiled 打开
不会报错，只是显示为一个带 image 属性的矩形；本编辑器内会真正画出图片。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .properties import PropertyBag

SHAPES = ("rect", "ellipse", "point", "polygon", "polyline",
          "tile", "text", "image")

SHAPE_LABELS = {
    "rect": "矩形", "ellipse": "椭圆", "point": "点",
    "polygon": "多边形", "polyline": "折线", "tile": "图块",
    "text": "文字", "image": "图片",
}

# <text> 的 Tiled 默认值；渲染时用它补齐只写了部分属性的文字对象。
TEXT_DEFAULTS = {
    "fontfamily": "sans-serif", "pixelsize": "16", "wrap": "0",
    "color": "#000000", "bold": "0", "italic": "0",
    "underline": "0", "strikeout": "0", "kerning": "1",
    "halign": "left", "valign": "top",
}


def _num(value) -> str:
    """与 TMX 写回一致的短小数格式。"""
    f = float(value)
    if f.is_integer():
        return str(int(f))
    return ("%.6f" % f).rstrip("0").rstrip(".")


def parse_points(raw: str) -> list[tuple[float, float]]:
    """解析 ``"0,0 20,0 20,20"`` 形式的相对坐标串。"""
    out: list[tuple[float, float]] = []
    for token in (raw or "").split():
        parts = token.split(",")
        if len(parts) != 2:
            continue
        try:
            out.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return out


def format_points(points) -> str:
    return " ".join(f"{_num(x)},{_num(y)}" for x, y in points)


@dataclass
class MapObject:
    object_id: int = 0
    name: str = ""
    type_: str = ""
    gid: int = 0
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    rotation: float = 0.0
    visible: bool = True
    properties: PropertyBag = field(default_factory=PropertyBag)
    xml_element: object | None = None

    # ---- 形状 ----
    shape: str = "rect"
    points: list[tuple[float, float]] = field(default_factory=list)
    text: str = ""
    text_style: dict = field(default_factory=dict)
    image_source: str = ""
    template: str = ""

    @property
    def label(self) -> str:
        return self.name or self.type_ or f"#{self.object_id}"

    @property
    def shape_label(self) -> str:
        return SHAPE_LABELS.get(self.shape, self.shape)

    @property
    def text_attr(self) -> dict:
        merged = dict(TEXT_DEFAULTS)
        merged.update({k: str(v) for k, v in self.text_style.items()})
        return merged

    def bounds(self, tile_w: int = 20, tile_h: int = 20):
        """返回 (x, y, w, h)：不同形状的默认尺寸规则不同。"""
        if self.shape == "point":
            return self.x - 6, self.y - 6, 12, 12
        if self.shape in ("polygon", "polyline") and self.points:
            xs = [p[0] for p in self.points]
            ys = [p[1] for p in self.points]
            x0, y0 = min(xs), min(ys)
            return (self.x + x0, self.y + y0,
                    max(max(xs) - x0, 4), max(max(ys) - y0, 4))
        return self.x, self.y, self.width or tile_w, self.height or tile_h


def object_uid(obj) -> tuple:
    """同一个逻辑对象在 refresh / 撤销 / 快照替换之间的稳定身份。

    铁锈地图里大量对象的 Tiled ``object_id`` 完全相同（例如全部为 1），游戏
    实际靠自定义属性 ``id`` 区分，因此所有「按对象找对象」的地方都不能再用
    ``object_id`` 作主键。

    ``commands.object_command._clone()`` 用 ``copy.copy`` 克隆对象但**保留
    ``xml_element`` 引用**，所以撤销/重做整层替换 objects 后，同一逻辑对象的
    ``xml_element`` 仍是同一个 lxml 元素 → 用它的身份最稳定。测试/新建对象没有
    ``xml_element`` 时退化为实例身份（同一次调用内仍唯一），调用方另保留
    ``object_id`` 兜底以兼容旧引用。
    """
    xe = getattr(obj, "xml_element", None)
    if xe is not None:
        return ("xml", id(xe))
    return ("obj", id(obj))
