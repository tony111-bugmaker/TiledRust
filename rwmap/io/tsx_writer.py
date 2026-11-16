"""独立 .tsx 图集文件写回（standalone tileset，供「制作图集」功能使用）。

生成的 .tsx 与 Tiled 完全一致，能被本项目 ``tsx_reader.parse_tileset_element``
原样读回：

    <tileset version="1.10" tiledversion="1.10.2" name="..." tilewidth="20"
             tileheight="20" spacing="0" margin="0" tilecount="N" columns="C">
      <image source="terrain.png" width="W" height="H"/>
      <tile id="3">
        <properties>
          <property name="k" value="v"/>
        </properties>
      </tile>
    </tileset>

图片会被复制到 .tsx 同目录并以相对名引用，方便随 mod 一起分发。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from lxml import etree

from ..document.properties import PropertyBag
from ..document.tileset import Tileset
from ..utils.paths import as_path
from .xml_preserve import serialize


def _props_to_element(bag: PropertyBag) -> "etree._Element":
    pe = etree.Element("properties")
    for name, value in bag.values.items():
        prop = etree.SubElement(pe, "property", attrib={"name": name})
        typ = bag.types.get(name)
        if typ:
            prop.set("type", typ)
        if name in bag.text_props:
            prop.text = value
        else:
            prop.set("value", value)
    return pe


def write_tileset(ts: Tileset, image_path: Path, target: Path) -> Path:
    """把图集写回独立 ``.tsx``；图片复制到 target 同目录并相对引用。

    ``ts.image_source`` 会被改写成相对文件名（供读回时按 base_dir 解析）；
    ``ts.base_dir`` 同步为 target 所在目录。返回最终写出的 .tsx 路径。
    """
    target = as_path(target)
    if target.suffix.lower() != ".tsx":
        target = target.with_suffix(".tsx")
    target_dir = target.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    image_path = as_path(image_path)
    img_name = image_path.name
    dest_img = target_dir / img_name
    if image_path.resolve() != dest_img.resolve():
        shutil.copyfile(image_path, dest_img)

    ts.image_source = img_name
    ts.base_dir = target_dir

    root = etree.Element("tileset", attrib={
        "version": "1.10",
        "tiledversion": "1.10.2",
        "name": ts.name or "",
        "tilewidth": str(ts.tile_w),
        "tileheight": str(ts.tile_h),
        "spacing": str(ts.spacing),
        "margin": str(ts.margin),
        "tilecount": str(ts.count),
        "columns": str(ts.columns),
    })
    img = etree.SubElement(root, "image", attrib={
        "source": img_name,
        "width": str(ts.image_w),
        "height": str(ts.image_h),
    })
    if ts.trans:
        img.set("trans", ts.trans)

    # 内嵌 PNG 图集（无真实图片文件时）靠 <properties> 里的 base64 保存。
    # 图集设置面板换图后会把新的 base64 写进 ts.embedded_png，另存 .tsx 时
    # 必须一起写出，否则保存后新图又变回旧图。
    if ts.embedded_png:
        props = etree.SubElement(root, "properties")
        prop = etree.SubElement(props, "property", attrib={"name": "embedded_png"})
        prop.text = ts.embedded_png

    for local_id in sorted(ts.tiles):
        bag = ts.tiles[local_id]
        if not bag:
            continue
        tile = etree.SubElement(root, "tile", attrib={"id": str(local_id)})
        tile.append(_props_to_element(bag))

    serialize(etree.ElementTree(root), target, target)
    return target
