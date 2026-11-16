"""编辑器装饰对象 ↔ 铁锈战争可读取的内容。

铁锈战争（1.15）不认识 Tiled 的 ``<text>`` 对象，也没有「图片对象」。
直接把这两种对象写进 ``Triggers`` 层，游戏加载时会报：

    desync:Error: Skipping trigger:文字 - no properties found
    desync:Error: Unknown type: found on 图片

本模块把二者翻译成游戏真正支持的格式：

* **文字** ``shape=text`` → ``type=mapText`` 触发器，属性 ``text`` /
  ``textColor`` / ``textSize``。官方地图 ``22P宋金战争剧情加强版.tmx``：
  ``<object name="建康府" type="mapText" ...>`` + 这三个属性。
* **图片** ``shape=image`` → 对象本身降级为 ``type=info`` 注释节点（官方
  ``embedded_custom_tileset_example.tmx`` 里就是这么写 readme 的，游戏会忽略），
  再把图片切成瓦片做成 ``embedded_png`` 内嵌图集，铺进 ``Items`` 瓦片层，
  游戏里就能真正看到这张图。

官方说明原文（嵌在示例地图的 tileset 属性里）：
    "Embedded images are created by creating a string called 'embedded_png'
     in each tileset property you have a custom image for. ... paste that
     string into the text of this property."

保存时全量重投影：图片对象被移动 / 缩放 / 删除后，下次保存会先清掉旧的
装饰瓦片再重新铺，因此永远不会留下「幽灵贴图」。内存里的 Items 层 cells
在写盘后恢复原样，编辑器里看到的仍是干净的地图数据。
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from lxml import etree

from ..document.layer import ObjectLayer, TileLayer
from ..io.layer_codec import decode

DECOR_TILESET_PREFIX = "rwmap_decor_"
IMAGE_NOTE_TYPE = "info"
TEXT_TRIGGER_TYPE = "mapText"

_LAYER_TAGS = {"layer", "objectgroup", "imagelayer"}


def _int(value, default=0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 文字：shape=text <-> type=mapText
# ---------------------------------------------------------------------------

def _rw_color(value: str) -> str:
    """Tiled 的 #RRGGBB / #AARRGGBB 归一成游戏认的 #RRGGBB。"""
    text = (value or "").strip()
    if not text:
        return "#000000"
    if text.startswith("#") and len(text) == 9:
        return "#" + text[3:]
    return text


def text_props_for_write(obj) -> dict[str, str]:
    """生成 mapText 触发器属性，不改动内存里的 PropertyBag。

    文字内容优先取自定义属性 ``text``（属性面板可以直接改），否则用
    ``obj.text``，这样两条编辑入口都能生效。
    """
    bag = obj.properties
    text = bag.get("text")
    if not text:
        text = obj.text or "文字"
    style = obj.text_style or {}
    size = style.get("pixelsize", "16")
    try:
        size_i = max(1, int(float(size)))
    except (TypeError, ValueError):
        size_i = 16
    return {
        "text": str(text),
        "textColor": bag.get("textColor") or _rw_color(style.get("color", "")),
        "textSize": bag.get("textSize") or str(size_i),
    }


def restore_text_from_props(obj) -> None:
    """读回 type=mapText 的对象时，还原成编辑器里的文字对象。"""
    bag = obj.properties
    obj.shape = "text"
    obj.text = bag.get("text", "") or ""
    style = dict(obj.text_style or {})
    style.setdefault("fontfamily", "sans-serif")
    style.setdefault("halign", "center")
    style.setdefault("valign", "center")
    if bag.get("textColor"):
        style["color"] = bag.get("textColor")
    if bag.get("textSize"):
        style["pixelsize"] = bag.get("textSize")
    obj.text_style = style


# ---------------------------------------------------------------------------
# 图片：内嵌图集贴图
# ---------------------------------------------------------------------------

def _decor_tileset_elements(root):
    return [el for el in root.findall("tileset")
            if (el.get("name") or "").startswith(DECOR_TILESET_PREFIX)]


def _old_gid_ranges(elements):
    out = []
    for el in elements:
        first = _int(el.get("firstgid"))
        count = _int(el.get("tilecount"))
        if first > 0 and count > 0:
            out.append((first, first + count - 1))
    return out


def _next_firstgid(doc, root) -> int:
    """给新图集找一个不与任何现有图集冲突的 firstgid。"""
    entries = []
    for el in root.findall("tileset"):
        if (el.get("name") or "").startswith(DECOR_TILESET_PREFIX):
            continue
        first = _int(el.get("firstgid"))
        entries.append((first, _int(el.get("tilecount"))))
    entries.sort()
    end = 0
    for i, (first, count) in enumerate(entries):
        if not count:
            # 外部 .tsx 引用没有 tilecount：先从已解析的模型里补，
            # 模型也没有（tsx 缺失）就用下一个图集的 firstgid 反推上界，
            # 避免新贴图 gid 撞进 units.tsx 之类的区段。
            for ts in doc.tilesets:
                if ts.firstgid == first:
                    count = ts.count
                    break
            if not count and i + 1 < len(entries):
                count = max(1, entries[i + 1][0] - first)
        end = max(end, first + max(count, 1))
    return max(1, end)


def _items_layer(doc, create: bool):
    """返回 (Items 瓦片层, 是否新建)。"""
    layer = doc.layer_by_name("Items")
    if isinstance(layer, TileLayer):
        return layer, False
    if not create:
        return None, False
    layer = TileLayer(
        width=doc.width, height=doc.height, name="Items", kind="tile",
        cells=[0] * (doc.width * doc.height),
        encoding="base64", compression="gzip", dirty=False,
    )
    # 渲染顺序：Ground 之后、Units / Triggers 之前。
    idx = next((i + 1 for i, cur in enumerate(doc.layers)
                if isinstance(cur, TileLayer) and cur.name == "Ground"),
               len(doc.layers))
    doc.layers.insert(idx, layer)
    return layer, True


def _base_cells(layer: TileLayer, old_ranges):
    """以盘面上的真实 cells 为基底，清掉上一轮投影留下的装饰瓦片。"""
    cells = None
    if layer.data_element is not None and layer.encoding:
        try:
            cells, enc, comp = decode(layer.data_element,
                                      layer.width * layer.height)
            layer.encoding = enc or layer.encoding
            layer.compression = comp
        except Exception:
            cells = None
    if cells is None:
        cells = list(layer.cells)
    need = layer.width * layer.height
    if len(cells) < need:
        cells.extend([0] * (need - len(cells)))
    cells = cells[:need]
    if not old_ranges:
        return cells
    cleaned = []
    for raw in cells:
        gid = int(raw) & 0x0FFFFFFF
        cleaned.append(0 if any(lo <= gid <= hi for lo, hi in old_ranges)
                       else raw)
    return cleaned


def _image_objects(doc):
    out = []
    for layer in doc.layers:
        if not isinstance(layer, ObjectLayer):
            continue
        for obj in layer.objects:
            if obj.shape == "image" and obj.image_source:
                out.append(obj)
    return out


def _resolve_image(doc, obj) -> Path | None:
    src = Path(obj.image_source)
    if not src.is_absolute():
        src = Path(doc.path).parent / src
    return src if src.is_file() else None


def _decor_png(src: Path, obj, doc, cache):
    """把图片缩放成 cols×rows 个 20px 瓦片的大图，返回 (png, cols, rows)。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    tw, th = doc.tile_w or 20, doc.tile_h or 20
    cols = max(1, round((obj.width or tw) / tw))
    rows = max(1, round((obj.height or th) / th))
    key = (str(src), cols, rows)
    if key in cache:
        return cache[key]
    try:
        with Image.open(src) as raw:
            image = raw.convert("RGBA")
        image = image.resize((cols * tw, rows * th), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        result = (buf.getvalue(), cols, rows)
    except Exception:
        return None
    cache[key] = result
    return result


def sync_decor_to_game(doc, target) -> tuple[object, int]:
    """在写盘前把图片对象投影成游戏可见的内嵌图集贴图。

    返回 ``(restore, count)``；调用方必须在写完文件后（含异常路径）执行
    ``restore()``，把内存里的 Items 层 cells 还原成用户编辑的干净数据。
    ``count`` 是本次成功贴图的图片数。
    """
    root = doc.tree.getroot()
    old = _decor_tileset_elements(root)
    old_ranges = _old_gid_ranges(old)
    for el in old:
        root.remove(el)
    doc.tilesets[:] = [ts for ts in doc.tilesets
                       if not (ts.name or "").startswith(DECOR_TILESET_PREFIX)]
    doc.rebuild_index()

    # 老地图读进来时对象层未必 dirty，也要强制重写才能把 <text> / 无 type
    # 的对象改写成 mapText / info。
    for layer in doc.layers:
        if isinstance(layer, ObjectLayer) and any(
                o.shape in ("text", "image") for o in layer.objects):
            layer.dirty = True

    images = _image_objects(doc)
    layer, created = _items_layer(doc, create=bool(images or old_ranges))
    if layer is None:
        return (lambda: None), 0
    saved_cells, saved_dirty = list(layer.cells), layer.dirty

    def restore():
        layer.cells = saved_cells
        layer.dirty = saved_dirty

    cells = _base_cells(layer, old_ranges)
    if not images:
        layer.cells = cells
        layer.dirty = True
        return restore, 0

    firstgid = _next_firstgid(doc, root)
    insert_at = next((i for i, child in enumerate(root)
                      if isinstance(getattr(child, "tag", None), str)
                      and child.tag in _LAYER_TAGS), len(root))
    cache: dict = {}
    made = 0
    for obj in images:
        src = _resolve_image(doc, obj)
        if src is None:
            continue
        built = _decor_png(src, obj, doc, cache)
        if built is None:
            continue
        png, cols, rows = built
        count = cols * rows
        name = f"{DECOR_TILESET_PREFIX}{made}"
        el = etree.Element("tileset", attrib={
            "firstgid": str(firstgid),
            "name": name,
            "tilewidth": str(doc.tile_w),
            "tileheight": str(doc.tile_h),
            "tilecount": str(count),
            "columns": str(cols),
        })
        props = etree.SubElement(el, "properties")
        prop = etree.SubElement(props, "property", attrib={"name": "embedded_png"})
        prop.text = "\n" + base64.b64encode(png).decode("ascii") + "\n"
        etree.SubElement(el, "image", attrib={
            "source": f"{name}.png",
            "width": str(cols * doc.tile_w),
            "height": str(rows * doc.tile_h),
        })
        root.insert(insert_at, el)
        insert_at += 1
        try:
            (Path(target).parent / f"{name}.png").write_bytes(png)
        except OSError:
            pass    # 游戏读 embedded_png，外部 PNG 只是给 Tiled 看的副本

        col0 = max(0, round(obj.x / (doc.tile_w or 20)))
        row0 = max(0, round(obj.y / (doc.tile_h or 20)))
        for row in range(rows):
            for col in range(cols):
                if layer.in_bounds(col0 + col, row0 + row):
                    cells[layer.index(col0 + col, row0 + row)] = (
                        firstgid + row * cols + col)
        firstgid += count
        made += 1

    layer.cells = cells
    layer.dirty = True
    return restore, made
