"""Tiled 模板文件 (.tx)：读取 <template><object> 作为插入对象的字段来源。

模板实例在 TMX 里通常只写 id/x/y/template，其余字段从 .tx 继承。
本编辑器为了能直接渲染，插入时把模板字段展开到对象上，同时保留
``template`` 属性；对象自身字段优先于模板，Tiled 打开结果一致。
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..document.map_object import MapObject, parse_points
from ..document.properties import PropertyBag


def _float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_template_object(path, object_id: int, x: float, y: float,
                         template_ref: str = "") -> MapObject | None:
    """解析一个 .tx 模板；没有 <object> 节点时返回 None。"""
    root = etree.parse(str(Path(path))).getroot()
    node = root.find("object")
    if node is None:
        return None
    obj = MapObject(
        object_id=object_id,
        name=node.get("name", ""),
        type_=node.get("type", node.get("class", "")),
        gid=_int(node.get("gid"), 0),
        x=x, y=y,
        width=_float(node.get("width"), 0.0),
        height=_float(node.get("height"), 0.0),
        rotation=_float(node.get("rotation"), 0.0),
        template=template_ref,
    )
    for child in node:
        if not isinstance(child.tag, str):
            continue
        if child.tag == "properties":
            obj.properties = PropertyBag.from_element(child)
        elif child.tag == "ellipse":
            obj.shape = "ellipse"
        elif child.tag == "point":
            obj.shape = "point"
        elif child.tag in ("polygon", "polyline"):
            obj.shape = child.tag
            obj.points = parse_points(child.get("points", ""))
        elif child.tag == "text":
            obj.shape = "text"
            obj.text = child.text or ""
            obj.text_style = dict(child.attrib)
    if obj.shape == "rect" and obj.gid:
        obj.shape = "tile"
    return obj
