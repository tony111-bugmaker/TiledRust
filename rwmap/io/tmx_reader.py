"""TMX 读取：XML DOM -> MapDocument，保留原树用于保真写回。"""
from __future__ import annotations

from pathlib import Path

from ..document.layer import ObjectLayer, TileLayer
from ..document.map_document import MapDocument
from ..document.map_object import MapObject, parse_points
from ..document.properties import PropertyBag
from ..rw import game_decor, unit_names
from ..utils.paths import as_path
from .layer_codec import decode
from .path_resolver import ResourceResolver
from .tsx_reader import parse_tileset_element, read_tsx
from .xml_preserve import iter_elements, parse_tmx


def _float(value, default=1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _common_layer_fields(elem, layer):
    layer.name = elem.get("name", "")
    layer.layer_id = _int(elem.get("id"), 0) or None
    layer.opacity = _float(elem.get("opacity"), 1.0)
    layer.visible = elem.get("visible", "1") not in ("0", "false")
    layer.locked = elem.get("locked", "0") in ("1", "true")
    layer.offsetx = _float(elem.get("offsetx"), 0.0)
    layer.offsety = _float(elem.get("offsety"), 0.0)
    layer.xml_element = elem
    for child in iter_elements(elem):
        if child.tag == "properties":
            layer.properties = PropertyBag.from_element(child)
            break
    return layer


def read_document(path, profile=None, resolver: ResourceResolver | None = None) -> MapDocument:
    p = as_path(path)
    tree, root = parse_tmx(p)
    resolver = resolver or ResourceResolver(p, profile)
    doc = MapDocument(
        path=p,
        tree=tree,
        root=root,
        width=_int(root.get("width"), 0),
        height=_int(root.get("height"), 0),
        tile_w=_int(root.get("tilewidth"), 20),
        tile_h=_int(root.get("tileheight"), 20),
        orientation=root.get("orientation", "orthogonal"),
        renderorder=root.get("renderorder", "right-down"),
        version=root.get("version", ""),
        tiledversion=root.get("tiledversion", ""),
        nextobjectid=_int(root.get("nextobjectid"), 0),
        resolver=resolver,
        profile=profile,
    )

    for child in iter_elements(root):
        if child.tag == "properties":
            doc.properties = PropertyBag.from_element(child)
        elif child.tag == "tileset":
            firstgid = _int(child.get("firstgid"), 0)
            source = child.get("source")
            if source:
                tsx_path = resolver.resolve_tsx(source)
                if tsx_path is None:
                    # 保真：即使外部 tsx 缺失也建一个占位，不丢失引用
                    doc.tilesets.append(parse_tileset_element(
                        child, firstgid, p.parent, resolver, source=source
                    ))
                else:
                    doc.tilesets.append(read_tsx(tsx_path, firstgid, resolver, source))
            else:
                doc.tilesets.append(parse_tileset_element(
                    child, firstgid, p.parent, resolver
                ))
        elif child.tag == "layer":
            width = _int(child.get("width"), doc.width)
            height = _int(child.get("height"), doc.height)
            layer = TileLayer(width=width, height=height, kind="tile")
            _common_layer_fields(child, layer)
            data = None
            for sub in iter_elements(child):
                if sub.tag == "data":
                    data = sub
                    break
            if data is not None:
                cells, encoding, compression = decode(data, width * height)
                layer.cells = cells
                layer.encoding = encoding
                layer.compression = compression
                layer.data_element = data
            else:
                layer.cells = [0] * (width * height)
            doc.layers.append(layer)
        elif child.tag == "objectgroup":
            layer = ObjectLayer(kind="object")
            _common_layer_fields(child, layer)
            for sub in iter_elements(child):
                if sub.tag != "object":
                    continue
                obj = MapObject(
                    object_id=_int(sub.get("id"), 0),
                    name=sub.get("name", ""),
                    type_=sub.get("type", sub.get("class", "")),
                    gid=_int(sub.get("gid"), 0),
                    x=_float(sub.get("x"), 0.0),
                    y=_float(sub.get("y"), 0.0),
                    width=_float(sub.get("width"), 0.0),
                    height=_float(sub.get("height"), 0.0),
                    rotation=_float(sub.get("rotation"), 0.0),
                    visible=sub.get("visible", "1") not in ("0", "false"),
                    template=sub.get("template", ""),
                    xml_element=sub,
                )
                for node in iter_elements(sub):
                    tag = node.tag
                    if tag == "properties":
                        obj.properties = PropertyBag.from_element(node)
                    elif tag == "ellipse":
                        obj.shape = "ellipse"
                    elif tag == "point":
                        obj.shape = "point"
                    elif tag == "polygon":
                        obj.shape = "polygon"
                        obj.points = parse_points(node.get("points", ""))
                    elif tag == "polyline":
                        obj.shape = "polyline"
                        obj.points = parse_points(node.get("points", ""))
                    elif tag == "text":
                        obj.shape = "text"
                        obj.text = node.text or ""
                        obj.text_style = dict(node.attrib)
                    elif tag == "image":
                        # Tiled 本身不写这个节点，但兼容外部工具生成的 TMX。
                        obj.shape = "image"
                        obj.image_source = node.get("source", "")
                # 没有形状标记时按 gid / image 属性推断。
                if obj.shape == "rect":
                    if obj.gid:
                        obj.shape = "tile"
                    else:
                        source = obj.properties.get("image", "")
                        if source:
                            obj.shape = "image"
                            obj.image_source = source
                if obj.shape == "image" and not obj.image_source:
                    obj.image_source = obj.properties.get("image", "")
                # 游戏格式回读：mapText 触发器 -> 编辑器里的文字对象。
                # 必须在形状推断之后，因为这类对象没有 <text> 子节点。
                if (obj.type_ == game_decor.TEXT_TRIGGER_TYPE
                        and "text" in obj.properties.values):
                    game_decor.restore_text_from_props(obj)
                # 链式 spawnChance -> 反推「随机生成单位」独立属性（可带权重）
                if (obj.type_ == "unitAdd"
                        and "randomUnits" not in obj.properties.values):
                    pairs = unit_names.weights_from_spawn_units(
                        obj.properties.get("spawnUnits", ""))
                    if pairs:
                        obj.properties.set(
                            "randomUnits", unit_names.format_weighted(pairs))
                layer.objects.append(obj)
            doc.layers.append(layer)

    _strip_decor_cells(doc)
    doc.rebuild_index()
    _apply_layer_rules(doc)
    return doc


def _strip_decor_cells(doc: MapDocument) -> None:
    """读盘时把上一轮投影产生的装饰瓦片从内存 cells 里抹掉。

    这些 gid 是编辑器自己写进去的游戏贴图，不是用户画的地图数据。留着会
    让 Items 层在画布上和图片对象重叠显示一份；DOM 里仍保留（游戏要读），
    下次保存时 ``game_decor`` 会全量重投影。
    """
    ranges = [(ts.firstgid, ts.last_gid) for ts in doc.tilesets
              if (ts.name or "").startswith(game_decor.DECOR_TILESET_PREFIX)]
    if not ranges:
        return
    for layer in doc.tile_layers():
        for index, raw in enumerate(layer.cells):
            gid = int(raw) & 0x0FFFFFFF
            if any(lo <= gid <= hi for lo, hi in ranges):
                layer.cells[index] = 0


def _apply_layer_rules(doc: MapDocument) -> None:
    """层语义由适配层配置驱动；未知层只读保真。"""
    try:
        from ..rw.layers import classify, hidden_layer_names
    except ImportError:
        return
    hidden = {name.lower() for name in hidden_layer_names()}
    for layer in doc.layers:
        rule = classify(layer.name, layer.kind, doc.profile)
        layer.readonly = not rule.writable
        layer.note = rule.note
        if layer.name.lower() in hidden:
            # 只改内存中的显示状态；保真写回不会改 XML 的 visible 属性。
            layer.visible = False
            layer.note = f"{rule.note}；游戏完全忽略，默认隐藏（可在图层面板勾选显示）"
