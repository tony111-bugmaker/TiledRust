"""外部 .tsx 与内嵌 <tileset> 的解析（共用元素解析逻辑）。

关键适配：官方 1.14/1.15 的外部 .tsx **不写 tilecount/columns**，
图块数量由 <image> 指向的 PNG 实际尺寸隐含决定。若按 0 处理，
整段 GID 会解析不到 tileset，画布会画成占位紫色（典型症状：草地变紫）。
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree
from PIL import Image

from ..document.properties import PropertyBag
from ..document.tileset import Tileset
from ..utils.paths import as_path
from .xml_preserve import iter_elements


def _image_size(ts: Tileset, resolver) -> tuple[int, int]:
    """优先读真实 PNG 头；external .tsx 的 width/height 常缺失或与图片不符。"""
    if ts.embedded_png:
        from ..tilesets.embedded_png import embedded_png_size
        size = embedded_png_size(ts.embedded_png)
        if size:
            return size
    if ts.image_source and resolver is not None:
        tsx_dir = ts.base_dir if ts.is_external else None
        path = resolver.resolve(ts.image_source, tsx_dir=tsx_dir)
        if path is not None:
            try:
                with open(path, "rb") as fh:
                    head = fh.read(24)
                from ..tilesets.embedded_png import png_size
                size = png_size(head)
                if size:
                    return size
                with Image.open(path) as img:
                    return img.size
            except OSError:
                pass
    return ts.image_w, ts.image_h


def _implied_grid(ts: Tileset, resolver) -> tuple[int, int]:
    """Tiled 规则：(image - 2*margin + spacing) / (tile + spacing)。"""
    width, height = _image_size(ts, resolver)
    if width <= 0 or height <= 0:
        return 0, 0
    step_x = max(1, ts.tile_w + ts.spacing)
    step_y = max(1, ts.tile_h + ts.spacing)
    cols = max(0, (width - 2 * ts.margin + ts.spacing) // step_x)
    rows = max(0, (height - 2 * ts.margin + ts.spacing) // step_y)
    return cols, rows


def parse_tileset_element(elem, firstgid: int, base_dir: Path, resolver=None,
                          source: str | None = None, source_path: Path | None = None) -> Tileset:
    ts = Tileset(
        firstgid=int(firstgid),
        name=elem.get("name", ""),
        source=source,
        tile_w=int(elem.get("tilewidth", "20")),
        tile_h=int(elem.get("tileheight", "20")),
        spacing=int(elem.get("spacing", "0")),
        margin=int(elem.get("margin", "0")),
        tilecount=int(elem.get("tilecount", "0")),
        columns=int(elem.get("columns", "0")),
        base_dir=as_path(base_dir),
        xml_element=elem,
        source_path=source_path,
    )
    for child in iter_elements(elem):
        if child.tag == "image":
            ts.image_source = child.get("source")
            ts.image_w = int(child.get("width", "0"))
            ts.image_h = int(child.get("height", "0"))
            ts.trans = child.get("trans")
        elif child.tag == "tile":
            local_id = int(child.get("id", "0"))
            props = PropertyBag()
            for sub in iter_elements(child):
                if sub.tag == "properties":
                    props = PropertyBag.from_element(sub)
                    break
            ts.tiles[local_id] = props
        elif child.tag == "properties":
            ts.properties = PropertyBag.from_element(child)
    if "embedded_png" in ts.properties:
        ts.embedded_png = ts.properties.get("embedded_png")

    # 补齐隐含的 columns/tilecount：外部 .tsx 几乎都不写这两个属性。
    # 只在缺失时补，Tiled 显式声明的数值优先（GID 区间以声明为准）。
    cols, rows = _implied_grid(ts, resolver)
    if ts.columns <= 0 and cols > 0:
        ts.columns = cols
    if ts.tilecount <= 0 and cols > 0 and rows > 0:
        ts.tilecount = cols * rows
    return ts


def read_tsx(path, firstgid: int, resolver=None, source: str | None = None) -> Tileset:
    p = as_path(path)
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    root = etree.parse(str(p), parser).getroot()
    return parse_tileset_element(
        root, firstgid, p.parent, resolver,
        source=source or p.name, source_path=p,
    )
