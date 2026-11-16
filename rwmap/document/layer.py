"""图层模型。层在列表中的顺序 == XML 顺序 == 渲染自下而上的顺序。"""
import copy
from dataclasses import dataclass, field

from .map_object import MapObject
from .properties import PropertyBag


def copy_layer(layer):
    """深拷贝一个图层用于「复制图层」；新图层不携带原 DOM 元素（写回时重建）。"""
    if isinstance(layer, TileLayer):
        new = TileLayer(
            width=layer.width, height=layer.height,
            name=layer.name + " 副本", cells=list(layer.cells),
            readonly=False,
        )
    elif isinstance(layer, ObjectLayer):
        objs = [copy.deepcopy(o) for o in layer.objects]
        for o in objs:
            o.xml_element = None
        new = ObjectLayer(name=layer.name + " 副本", objects=objs,
                          readonly=False)
    else:
        return None
    new.visible = layer.visible
    new.opacity = layer.opacity
    new.locked = False
    new.offsetx = layer.offsetx
    new.offsety = layer.offsety
    new.properties = copy.deepcopy(layer.properties)
    return new


@dataclass
class Layer:
    name: str = ""
    kind: str = "tile"            # tile | object
    layer_id: int | None = None
    opacity: float = 1.0
    visible: bool = True
    locked: bool = False
    offsetx: float = 0.0
    offsety: float = 0.0
    properties: PropertyBag = field(default_factory=PropertyBag)
    xml_element: object | None = None
    readonly: bool = False
    note: str = ""


@dataclass
class TileLayer(Layer):
    width: int = 0
    height: int = 0
    cells: list[int] = field(default_factory=list)   # raw_gid，保留翻转标志
    encoding: str | None = None
    compression: str | None = None
    data_element: object | None = None
    dirty: bool = False

    def index(self, col: int, row: int) -> int:
        return row * self.width + col

    def in_bounds(self, col: int, row: int) -> bool:
        return 0 <= col < self.width and 0 <= row < self.height

    def get(self, col: int, row: int) -> int:
        return self.cells[self.index(col, row)]

    def set(self, col: int, row: int, raw_gid: int) -> int:
        idx = self.index(col, row)
        old = self.cells[idx]
        self.cells[idx] = int(raw_gid) & 0xFFFFFFFF
        self.dirty = True
        return old


@dataclass
class ObjectLayer(Layer):
    kind: str = "object"        # 覆盖 Layer 默认的 "tile"
    objects: list[MapObject] = field(default_factory=list)
    dirty: bool = False          # 对象被增删改/移动后置位，写回时同步 <objectgroup>
