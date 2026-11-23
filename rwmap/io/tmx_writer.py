"""保真写回：只替换被编辑层的 <data> 文本 / <objectgroup> 内容，其余 DOM 不动。"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..document.layer import ObjectLayer
from ..document.map_document import MapDocument, ensure_trigger_ids
from ..document.map_object import format_points, parse_points
from ..document.properties import PropertyBag
from ..rw import game_decor, unit_names
from ..utils.paths import as_path
from .layer_codec import encode
from .xml_preserve import iter_elements, serialize

_SHAPE_TAGS = {"ellipse", "point", "polygon", "polyline", "text", "image"}

# 编辑器内部类型 -> 游戏认识的 type。新增的「随机生成单位」只是属性模板不同
# （randomUnits），对铁锈战争仍是 unitAdd，不能把内部 key 写进 TMX。
GAME_TYPE_ALIASES = {
    "unitAddRandom": "unitAdd",
    # 地图初始单位在 TMX 里没有 type，游戏靠 unit + team 识别；写回空串
    "mapUnit": "",
}

# 铁锈战争把这些属性按整数解析；若写出空字符串 ''，游戏会报
# "Unexpected integer value:''" 并拒绝加载地图（例如 team）。
# 这些键值为空时直接跳过，不写入 TMX（游戏对缺失取默认值）。
_RW_INT_KEYS = {
    "team", "credits", "allyGroup", "lockAiDifficulty", "dir",
    "minUnits", "maxUnits", "index", "textSize", "spawnX", "spawnY",
    "repeatDelay", "warmup", "delay", "resetActivationAfter",
}


def _num(value) -> str:
    """坐标写成 Tiled 习惯的短小数，避免 123 → "123.0"。"""
    f = float(value)
    if f.is_integer():
        return str(int(f))
    return ("%.6f" % f).rstrip("0").rstrip(".")


def _write_properties(obj_element, bag) -> None:
    """重建对象的 <properties>：保留 text 型属性与 type 标注。"""
    for child in list(obj_element):
        if getattr(child, "tag", None) == "properties":
            obj_element.remove(child)
    if not bag.values:
        return
    props = etree.SubElement(obj_element, "properties")
    for name, value in bag.values.items():
        if value == "" and name in _RW_INT_KEYS:
            # 整数型属性空值会让铁锈战争崩溃，跳过写出（缺省取默认值）。
            continue
        node = etree.SubElement(props, "property")
        node.set("name", name)
        if name in bag.types:
            node.set("type", bag.types[name])
        if name in bag.text_props:
            node.text = value
        else:
            node.set("value", value)


def _shape_spec(obj):
    """返回 (tag, attrib, text)；None 表示该形状不需要子节点。"""
    if obj.shape == "ellipse":
        return "ellipse", {}, None
    if obj.shape == "point":
        return "point", {}, None
    if obj.shape in ("polygon", "polyline"):
        return obj.shape, {"points": format_points(obj.points)}, None
    # 文字由 game_decor 转成 mapText 触发器的 text 属性；<text> 子节点游戏
    # 不认识，写进 Triggers 层会报 "Skipping trigger - no properties found"。
    return None


def _shape_matches(nodes, spec) -> bool:
    """形状没变时完全不动原节点，保证只改名字/坐标的对象字节不变。"""
    if spec is None:
        return not nodes
    if len(nodes) != 1 or nodes[0].tag != spec[0]:
        return False
    node = nodes[0]
    if (node.text or "") != (spec[2] or ""):
        return False
    if spec[0] in ("polygon", "polyline"):
        return parse_points(node.get("points", "")) == parse_points(spec[1]["points"])
    return dict(node.attrib) == dict(spec[1])


def _sync_shape_element(element, obj) -> None:
    existing = [c for c in list(element)
                if getattr(c, "tag", None) in _SHAPE_TAGS]
    if obj.shape == "image":
        # TMX 没有原生 image object：图片路径存到 image 自定义属性里。
        # 若外部工具原本写了 <image>，保留并只更新 source。
        keep = [c for c in existing if c.tag == "image"]
        for child in existing:
            if not keep or child is not keep[0]:
                element.remove(child)
        if keep and obj.image_source:
            keep[0].set("source", obj.image_source)
        return
    spec = _shape_spec(obj)
    if _shape_matches(existing, spec):
        return
    for child in existing:
        element.remove(child)
    if spec is not None:
        node = etree.SubElement(element, spec[0])
        for key, value in spec[1].items():
            node.set(key, value)
        if spec[2] is not None:
            node.text = spec[2]


def _write_object(element, obj) -> None:
    """只改已知字段，未知属性 / 未知子节点原样保留。"""
    element.set("id", str(obj.object_id))
    if obj.name:
        element.set("name", obj.name)
    else:
        element.attrib.pop("name", None)

    # Tiled 1.9+ 用 class，旧版用 type；按元素原有的写法写回。
    type_key = "class" if "class" in element.attrib else "type"
    other_key = "type" if type_key == "class" else "class"
    # 编辑器装饰对象要翻译成游戏认识的 type，且必须写成 type（游戏不读 class）。
    game_type = ""
    if obj.shape == "text":
        game_type = game_decor.TEXT_TRIGGER_TYPE
    elif obj.shape == "image":
        game_type = game_decor.IMAGE_NOTE_TYPE
    if game_type:
        element.set("type", game_type)
        element.attrib.pop("class", None)
    elif obj.type_:
        # 编辑器内部的「随机生成单位」类型对外仍是 unitAdd：游戏只认 unitAdd，
        # 随机刷兵靠 randomUnits 属性展开成 spawnChance 表达，不另造 type。
        # 「地图初始单位」映射成空 type（游戏靠 unit + team 识别），同样不写出。
        game_type_for_obj = GAME_TYPE_ALIASES.get(obj.type_, obj.type_)
        if game_type_for_obj:
            element.set(type_key, game_type_for_obj)
            element.attrib.pop(other_key, None)
        else:
            element.attrib.pop("type", None)
            element.attrib.pop("class", None)
    else:
        element.attrib.pop("type", None)
        element.attrib.pop("class", None)

    element.set("x", _num(obj.x))
    element.set("y", _num(obj.y))
    for key, value in (("width", obj.width), ("height", obj.height)):
        if value > 0:
            element.set(key, _num(value))
        else:
            element.attrib.pop(key, None)
    if obj.gid:
        element.set("gid", str(obj.gid))
    else:
        element.attrib.pop("gid", None)
    if obj.rotation:
        element.set("rotation", _num(obj.rotation))
    else:
        element.attrib.pop("rotation", None)
    if not obj.visible:
        element.set("visible", "0")
    else:
        element.attrib.pop("visible", None)
    if obj.template:
        element.set("template", obj.template)
    else:
        element.attrib.pop("template", None)
    # TMX 没有原生图片对象：路径写进 image 自定义属性，读回时据此还原形状。
    # 必须在 _write_properties 之前设置，否则属性不会出现在 <properties> 里。
    if obj.shape == "image":
        obj.properties.set("image", obj.image_source, "file")
    _write_properties(element, _properties_for_write(obj.properties, obj))
    _sync_shape_element(element, obj)


def _properties_for_write(bag: PropertyBag, obj=None) -> PropertyBag:
    """写回前的属性展开，一律在副本上做，不污染内存里的 PropertyBag。

    1. 「随机生成单位」独立属性 -> 原生 spawnUnits（游戏不认 randomUnits）。
    2. 文字对象 -> mapText 的 text / textColor / textSize。
    """
    copy = PropertyBag()
    copy.values = dict(bag.values)
    copy.types = dict(bag.types)
    copy.text_props = set(bag.text_props)
    if "randomUnits" in bag.values:
        pairs = unit_names.parse_weighted(bag.get("randomUnits", ""))
        copy.values.pop("randomUnits", None)
        copy.types.pop("randomUnits", None)
        copy.text_props.discard("randomUnits")
        if pairs:
            copy.values["spawnUnits"] = unit_names.build_spawn_units_weighted(pairs)
    if obj is not None and obj.shape == "text":
        for key, value in game_decor.text_props_for_write(obj).items():
            copy.values[key] = value
            copy.types.pop(key, None)
            copy.text_props.discard(key)   # 统一写 value 属性，游戏最稳
    if obj is not None and obj.shape == "image":
        # 图片对象的翻转状态：TMX 无原生字段，用自定义属性持久化（rotation 走原生）。
        if getattr(obj, "image_flip_h", False):
            copy.values["imageFlipH"] = "1"
        else:
            copy.values.pop("imageFlipH", None)
        if getattr(obj, "image_flip_v", False):
            copy.values["imageFlipV"] = "1"
        else:
            copy.values.pop("imageFlipV", None)
        copy.types.pop("imageFlipH", None)
        copy.types.pop("imageFlipV", None)
        copy.text_props.discard("imageFlipH")
        copy.text_props.discard("imageFlipV")
    return copy


def _create_layer_element(doc: MapDocument, layer):
    """为内存中新建、尚无 DOM 元素的图层补建 <layer> / <objectgroup>。"""
    root = doc.tree.getroot()
    if layer.kind == "tile":
        el = etree.SubElement(root, "layer")
        el.set("name", layer.name)
        el.set("width", str(layer.width))
        el.set("height", str(layer.height))
        if layer.opacity < 1.0:
            el.set("opacity", _num(layer.opacity))
        if not layer.visible:
            el.set("visible", "0")
        data = etree.SubElement(el, "data")
        data.set("encoding", "base64")
        data.set("compression", "gzip")
        layer.xml_element = el
        layer.data_element = data
        layer.encoding = "base64"
        layer.compression = "gzip"
        layer.dirty = True
        return el
    el = etree.SubElement(root, "objectgroup")
    el.set("name", layer.name)
    if layer.opacity < 1.0:
        el.set("opacity", _num(layer.opacity))
    if not layer.visible:
        el.set("visible", "0")
    layer.xml_element = el
    layer.dirty = True
    return el


def _sync_layer_order(doc: MapDocument) -> None:
    """把 DOM 的图层元素与 ``doc.layers`` 对齐：补建新层、删除已移除层、调整顺序。

    仅移动 / 重建图层元素，不动任何非图层子节点（tileset / properties / wangsets）。
    """
    root = doc.tree.getroot()
    layer_tags = {"layer", "objectgroup", "imagelayer"}
    for layer in doc.layers:
        if layer.xml_element is None:
            _create_layer_element(doc, layer)
    due = [l.xml_element for l in doc.layers if l.xml_element is not None]
    due_ids = {id(e) for e in due}
    orig = list(root)
    new_children = []
    due_idx = 0
    for ch in orig:
        if getattr(ch, "tag", None) in layer_tags:
            if due_idx < len(due):
                new_children.append(due[due_idx])
                due_idx += 1
            # 已删除的图层元素：跳过（从 DOM 移除）
        else:
            new_children.append(ch)
    while due_idx < len(due):
        new_children.append(due[due_idx])
        due_idx += 1
    root[:] = new_children


def _sync_object_layer(doc: MapDocument, layer: ObjectLayer) -> None:
    """把内存里的对象列表同步回 <objectgroup> DOM。

    已有对象沿用原来的 <object> 元素（保留未知属性/子节点），只更新字段；
    新增对象补建元素，删除对象移除元素，最后按内存顺序重排。
    """
    group = layer.xml_element
    if group is None:
        return
    keep = {id(obj.xml_element) for obj in layer.objects
            if obj.xml_element is not None}
    for child in list(iter_elements(group)):
        if child.tag == "object" and id(child) not in keep:
            group.remove(child)
    for obj in layer.objects:
        element = obj.xml_element
        if element is None:
            element = etree.Element("object")
            obj.xml_element = element
        _write_object(element, obj)
        group.append(element)       # 已存在时 append = 移动到末尾，用于保序
    layer.dirty = False


def write_document(doc: MapDocument, path=None) -> Path:
    target = as_path(path) if path is not None else doc.path
    # 图片对象 -> 内嵌图集贴图：必须在 _sync_layer_order 之前（可能新建 Items 层）；
    # 写盘后 restore 把内存里的 Items cells 还原成用户编辑的干净数据。
    restore_decor, _decor_count = game_decor.sync_decor_to_game(doc, target)
    try:
        return _write_document_unlocked(doc, target)
    finally:
        restore_decor()


def _write_document_unlocked(doc: MapDocument, target) -> Path:
    _sync_layer_order(doc)
    # 铁锈战争按触发器的自定义 id 反查 alsoActivate 等链接。保存前补齐缺失的 id
    # （优先 object_id；重复时用唯一前缀，铁锈地图的 Tiled id 常大量重复），
    # 确保游戏内能真正触发联动。
    ensure_trigger_ids(doc)
    # 翻转 / 旋转 / 改尺寸会改变地图宽高与瓦片尺寸，必须同步回 <map> 根属性，
    # 否则保存后重载会读回旧尺寸。
    root = doc.tree.getroot()
    root.set("width", str(doc.width))
    root.set("height", str(doc.height))
    root.set("tilewidth", str(doc.tile_w))
    root.set("tileheight", str(doc.tile_h))
    for layer in doc.tile_layers():
        if not layer.dirty:
            continue
        if layer.data_element is None:
            continue
        if layer.encoding is None:
            # XML <tile gid> 形式：首版不支持增量改写，保持原样并提示
            from ..utils.log import get_logger
            get_logger(__name__).warning("层 %s 使用 XML tile 数据，跳过写回", layer.name)
            continue
        layer.data_element.text = encode(
            layer.cells, layer.encoding, layer.compression
        )
        layer.dirty = False

    object_layers = [l for l in doc.layers if isinstance(l, ObjectLayer) and l.dirty]
    if object_layers:
        for layer in object_layers:
            _sync_object_layer(doc, layer)
        # 新建对象后必须推进 nextobjectid，否则 Tiled 下次分配 id 会撞车。
        max_id = max((obj.object_id for layer in doc.layers
                      if isinstance(layer, ObjectLayer)
                      for obj in layer.objects), default=0)
        root = doc.tree.getroot()
        if max_id >= getattr(doc, "nextobjectid", 0):
            root.set("nextobjectid", str(max_id + 1))
            doc.nextobjectid = max_id + 1

    serialize(doc.tree, target, doc.path)
    doc.dirty = False
    doc.path = target
    return target


def create_blank_document(profile, width: int, height: int,
                         tile_w: int = 20, tile_h: int = 20, path=None) -> MapDocument:
    """构建一张全新的空地图（含最小化的 ``<map>`` DOM），可直接保存与编辑。"""
    from lxml import etree
    root = etree.Element("map", attrib={
        "version": "1.10",
        "tiledversion": "1.10.2",
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "width": str(width),
        "height": str(height),
        "tilewidth": str(tile_w),
        "tileheight": str(tile_h),
        "nextobjectid": "1",
        "infinite": "0",
    })
    tree = etree.ElementTree(root)
    target = Path(path) if path is not None else Path(profile.export_dir) / "新地图.tmx"
    doc = MapDocument(
        path=target, tree=tree, root=root, width=width, height=height,
        tile_w=tile_w, tile_h=tile_h, profile=profile,
        tilesets=[], layers=[],
    )
    doc.rebuild_index()
    return doc
