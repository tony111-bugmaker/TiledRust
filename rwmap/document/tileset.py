"""图块集模型（内嵌 tileset 或外部 .tsx 引用）。"""
from dataclasses import dataclass, field
from pathlib import Path

from .properties import PropertyBag


@dataclass
class Tileset:
    firstgid: int = 0
    name: str = ""
    source: str | None = None
    tile_w: int = 20
    tile_h: int = 20
    spacing: int = 0
    margin: int = 0
    tilecount: int = 0
    columns: int = 0
    image_source: str | None = None
    image_w: int = 0
    image_h: int = 0
    trans: str | None = None
    embedded_png: str | None = None
    tiles: dict[int, PropertyBag] = field(default_factory=dict)
    properties: PropertyBag = field(default_factory=PropertyBag)
    base_dir: Path = field(default_factory=lambda: Path("."))
    xml_element: object | None = None
    source_path: Path | None = None

    @property
    def count(self) -> int:
        return max(self.tilecount, (max(self.tiles) + 1) if self.tiles else 0)

    @property
    def last_gid(self) -> int:
        return self.firstgid + self.count - 1

    @property
    def is_external(self) -> bool:
        return self.source is not None

    @property
    def is_embedded_image(self) -> bool:
        return self.embedded_png is not None

    def tile_props(self, local_id: int) -> PropertyBag:
        return self.tiles.get(local_id, PropertyBag())
