"""TMX 读取：XML DOM -> MapDocument，保留原树用于保真写回。"""
from __future__ import annotations

import json
from pathlib import Path

from ..document.layer import ObjectLayer, TileLayer
from ..document.map_document import MapDocument
from ..document.map_object import MapObject, parse_points
from ..document.properties import (
    HIDDEN_EDITOR_PROPERTIES,
    PropertyBag,
    WAVE_GEN_TYPE,
    WAVE_TABLE_TYPE,
    WAVE_MARK_PROPERTY,
    WAVE_TABLE_MARK_PROPERTY,
)
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


def read_document(path, profile=None, resolver: ResourceResolver | None = None,
                  progress=None) -> MapDocument:
    """读取 TMX 文档。

    ``progress`` 为可选的 ``(percent: int, message: str) -> None`` 回调，用于在
    异步打开地图时报告解析进度；不传时（默认）解析行为与旧版完全一致。
    """
    def report(pct: int, msg: str):
        if progress is not None:
            progress(pct, msg)

    p = as_path(path)
    report(2, "解析地图结构…")
    tree, root = parse_tmx(p)
    report(8, "加载图块集与图层…")
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

    children = list(iter_elements(root))
    nchild = max(1, len(children))
    for ci, child in enumerate(children):
        child_pct = 8 + int(78 * ci / nchild)      # 8% → 86%
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
            # 对象数量常达数千，是解析最大头：在本 child 的百分比份额内细分上报。
            _obj_children = list(iter_elements(child))
            _nobj = max(1, sum(1 for sub in _obj_children if sub.tag == "object"))
            for _oi, sub in enumerate(_obj_children):
                if sub.tag != "object":
                    continue
                if _oi % 256 == 0:
                    report(child_pct + int(78 / nchild * _oi / _nobj),
                           "解析地图对象…")
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
                if obj.shape == "image":
                    obj.image_flip_h = str(obj.properties.get("imageFlipH", "")) in ("1", "true", "True")
                    obj.image_flip_v = str(obj.properties.get("imageFlipV", "")) in ("1", "true", "True")
                # 游戏格式回读：mapText 触发器 -> 编辑器里的文字对象。
                # 必须在形状推断之后，因为这类对象没有 <text> 子节点。
                if (obj.type_ == game_decor.TEXT_TRIGGER_TYPE
                        and "text" in obj.properties.values):
                    game_decor.restore_text_from_props(obj)
                # 链式 spawnChance -> 反推「随机生成单位」独立属性（可带权重）
                if (obj.type_ == "unitAdd"
                        and "randomUnits" not in obj.properties.values):
                    triples = unit_names.units_from_spawn_units(
                        obj.properties.get("spawnUnits", ""))
                    if triples:
                        obj.properties.set(
                            "randomUnits", unit_names.format_units(triples))
                layer.objects.append(obj)
            doc.layers.append(layer)

    report(88, "整理图层…")
    _apply_editor_layout(doc, p)
    _restore_editor_node_types(doc)
    _strip_decor_cells(doc)
    doc.rebuild_index()
    report(94, "应用图层规则…")
    _apply_layer_rules(doc)
    report(100, "完成")
    return doc


def _apply_editor_layout(doc: MapDocument, path: Path | None = None) -> None:
    """从地图根属性 rwmap_layout 还原节点图布局。

    游戏只校验触发器对象的属性名，地图根属性随便写（真实生存图就有
    fill / tag / sw1..sw6 等自定义键）。因此布局数据存在根属性里，既留在
    TMX 又不触发游戏的 Key was not used 报错。旧版单独 JSON 文件也兼容读取。
    """
    raw = doc.properties.get("rwmap_layout", "")
    if not raw:
        # 兼容早期版本的 .rwmap-layout.json sidecar
        if path is None:
            return
        sidecar = path.with_name(path.stem + ".rwmap-layout.json")
        if not sidecar.exists():
            return
        try:
            raw = sidecar.read_text(encoding="utf-8")
        except OSError:
            return
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return
    objects = data.get("objects", {}) if isinstance(data, dict) else {}
    if not isinstance(objects, dict):
        return
    for layer in doc.layers:
        if not isinstance(layer, ObjectLayer):
            continue
        for obj in layer.objects:
            private = objects.get(f"{layer.name}:{obj.object_id}")
            if not isinstance(private, dict):
                continue
            for key, value in private.items():
                if (key in HIDDEN_EDITOR_PROPERTIES
                        and key not in obj.properties.values):
                    obj.properties.set(key, str(value), "string")


def _restore_editor_node_types(doc: MapDocument) -> int:
    """读盘后自动检测并修复编辑器私有编排节点，返回修复个数。

    老地图 / 早期脚本建的波次节点，写盘时 type 被写成空（靠 rwmap_layout 里
    的私有标记还原）。重开后 ``type_`` 是空的，只留下私有标记。这里按标记把
    它们认回来，做两件事：

    1. 恢复内部 ``type_``（waveGen / waveTable）。这样保存时写盘别名表能命中
       ``waveGen -> point``，不会再写出空 type —— 根治游戏反复弹
       ``Skipping trigger: X - no properties found`` 的 desync 错误。
    2. 顺带归一为隐藏，避免老脚本建的可见节点跑到画布左上角。

    必须在 ``_apply_editor_layout`` 之后调用（私有标记那一步才还原到对象上）。
    已经是正确 type 且隐藏的节点不动，不无谓标脏。
    """
    fixed = 0
    for layer in getattr(doc, "layers", []) or []:
        if not isinstance(layer, ObjectLayer):
            continue
        for obj in layer.objects:
            values = getattr(getattr(obj, "properties", None), "values", {}) or {}
            mark_wave = str(values.get(WAVE_MARK_PROPERTY, "") or "").strip() == "1"
            mark_table = str(values.get(WAVE_TABLE_MARK_PROPERTY, "") or "").strip() == "1"
            if not (mark_wave or mark_table):
                continue
            want = WAVE_GEN_TYPE if mark_wave else WAVE_TABLE_TYPE
            changed = False
            if str(getattr(obj, "type_", "") or "") != want:
                obj.type_ = want
                changed = True
            if obj.visible:
                obj.visible = False
                changed = True
            if changed:
                layer.dirty = True
                fixed += 1
    if fixed:
        doc.dirty = True
    return fixed


def _strip_decor_cells(doc: MapDocument) -> None:
    """读盘时把上一轮投影产生的装饰瓦片从内存 cells 里还原掉。

    这些 gid 是编辑器自己写进去的游戏贴图（铺在 Ground 层），不是用户画的
    地形。装饰 tile 定义里记了 ``rwmap_original_gid``（被覆盖的原地形 gid），
    读到时还原成原 gid，画布上就能显示原本的地形（图片仍由对象层对象呈现）；
    DOM 里仍保留装饰瓦片（游戏要读），下次保存 ``game_decor`` 会全量重投影。
    旧版本文件没有 original_gid 记录时，退回抹成 0。
    """
    ranges: list[tuple[int, int]] = []
    originals: dict[int, int] = {}
    for ts in doc.tilesets:
        if not (ts.name or "").startswith(game_decor.DECOR_TILESET_PREFIX):
            continue
        ranges.append((ts.firstgid, ts.last_gid))
        for local_id, bag in ts.tiles.items():
            raw = bag.get(game_decor.DECOR_ORIGINAL_GID_PROP)
            if raw is not None:
                try:
                    originals[ts.firstgid + local_id] = int(raw)
                except (TypeError, ValueError):
                    pass
    if not ranges:
        return
    for layer in doc.tile_layers():
        for index, raw in enumerate(layer.cells):
            gid = int(raw) & 0x0FFFFFFF
            if any(lo <= gid <= hi for lo, hi in ranges):
                layer.cells[index] = originals.get(gid, 0)


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
