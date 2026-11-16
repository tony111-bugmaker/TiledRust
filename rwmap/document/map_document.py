"""MapDocument：唯一数据源，保留原始 lxml 树用于保真写回。"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

from .gid import split_gid
from .layer import Layer, ObjectLayer, TileLayer
from .properties import PropertyBag
from .tileset import Tileset
from .version_profile import VersionProfile


@dataclass
class MapDocument:
    path: Path = Path(".")
    tree: object | None = None
    root: object | None = None
    width: int = 0
    height: int = 0
    tile_w: int = 20
    tile_h: int = 20
    orientation: str = "orthogonal"
    renderorder: str = "right-down"
    version: str = ""
    tiledversion: str = ""
    nextobjectid: int = 0
    tilesets: list[Tileset] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    properties: PropertyBag = field(default_factory=PropertyBag)
    resolver: object | None = None
    profile: VersionProfile | None = None
    dirty: bool = False
    _firstgids: list[int] = field(default_factory=list, repr=False)

    def rebuild_index(self):
        self.tilesets.sort(key=lambda t: t.firstgid)
        self._firstgids = [t.firstgid for t in self.tilesets]

    def tileset_for_gid(self, raw_gid: int) -> Tileset | None:
        gid, _, _, _ = split_gid(raw_gid)
        if not self._firstgids:
            self.rebuild_index()
        i = bisect.bisect_right(self._firstgids, gid) - 1
        if i < 0:
            return None
        ts = self.tilesets[i]
        return ts if gid <= ts.last_gid else None

    def local_tile(self, raw_gid: int) -> tuple[Tileset, int] | None:
        ts = self.tileset_for_gid(raw_gid)
        if ts is None:
            return None
        gid, _, _, _ = split_gid(raw_gid)
        return ts, gid - ts.firstgid

    def tile_layers(self) -> list[TileLayer]:
        return [layer for layer in self.layers if isinstance(layer, TileLayer)]

    def layer_by_name(self, name: str) -> Layer | None:
        for layer in self.layers:
            if layer.name == name:
                return layer
        return None

    def set_cell(self, layer: TileLayer, col: int, row: int, raw_gid: int) -> int:
        old = layer.set(col, row, raw_gid)
        if old != (int(raw_gid) & 0xFFFFFFFF):
            self.dirty = True
        return old

    def save(self, path: str | Path | None = None) -> Path:
        from ..io.tmx_writer import write_document
        return write_document(self, path)

    def stats(self) -> dict:
        tile_layers = self.tile_layers()
        return {
            "path": str(self.path),
            "size": f"{self.width}x{self.height}",
            "tile": f"{self.tile_w}x{self.tile_h}",
            "layers": {layer.name: layer.kind for layer in self.layers},
            "tilesets": len(self.tilesets),
            "embedded_tilesets": sum(1 for t in self.tilesets if not t.is_external),
            "external_tilesets": sum(1 for t in self.tilesets if t.is_external),
            "tile_cells": sum(len(layer.cells) for layer in tile_layers),
            "flipped_gids": sum(
                1 for layer in tile_layers for cell in layer.cells if cell & 0xF0000000
            ),
            "objects": sum(
                len(layer.objects) for layer in self.layers if layer.kind == "object"
            ),
        }


# ---------------------------------------------------------------------------
# 铁锈战争触发器 id 规范化
#
# RW 的 alsoActivate / activatedBy 等链接属性引用的是触发器对象的**自定义 id
# 属性**（作者手动赋予的数字），而不是 Tiled 的 object id 属性。若目标触发器没有
# 这个自定义 id，游戏在加载时无法把 alsoActivate 的值对应到具体触发器，连线在
# 游戏内就不会生效（表现为「矩形检测不到 / 触发不了点的生成」）。
#
# 因此：任何缺少自定义 id 的触发器，默认用稳定且唯一的 Tiled object_id 作为它的
# id 写回。object_id 在整张地图内唯一，正好满足 RW 对 id 唯一性的要求。
# ---------------------------------------------------------------------------

# 会被当作「触发器」处理的对象类型（对应 trigger_inspector.TYPE_DEFS 的键）。
# 仅给这些类型补 id，避免给普通单位 / 装饰对象、以及 map_info / team_info /
# mapText / info 等配置说明类节点也加上无意义的 id 属性（后者不会被 alsoActivate
# 引用，强加 id 还会污染「未编辑文档写回字节一致」的保证）。
RW_TRIGGER_TYPES = {
    "unitAdd", "unitDetect", "unitRemove", "changeCredits", "move",
    "point", "basic", "camera_pan", "rotate", "moveCamera", "objective",
    "set_team", "ai_allow_full_use",
}


def _is_rw_trigger(obj, layer_name: str) -> bool:
    """判断一个对象是否是铁锈战争的触发器（需要可被 id 引用）。

    以 type 是否命中已知触发器类型为准。这样既能精确排除 map_info / team_info /
    mapText / info 等配置说明类节点，也不会给普通单位 / 装饰对象强加 id。

    注：节点图的「拖拽连线」(create_link) 不依赖此判定——它会显式给被连接的源 /
    目标两端补 id，所以即使某个触发器 type 暂未收录，只要用户连过线，保存时照样
    能拿到正确的 id。
    """
    return bool(obj.type_) and (obj.type_ or "").strip() in RW_TRIGGER_TYPES


def used_trigger_ids(doc) -> set[str]:
    """全文档已占用的自定义触发器 id（跨全部对象层，中文 id 也算）。

    铁锈战争的 ``alsoActivate`` / ``activatedBy`` 引用的是这个自定义属性，
    命名空间是整张地图而不是单个图层，因此补 id 前必须全局查重。
    """
    used: set[str] = set()
    for layer in getattr(doc, "layers", []) or []:
        if not isinstance(layer, ObjectLayer):
            continue
        for obj in layer.objects:
            val = obj.properties.get("id")
            if val:
                used.add(str(val).strip())
    return used


def allocate_trigger_id(obj, used: set[str]) -> str:
    """给缺自定义 id 的对象分配一个全图唯一、能被引用解析的 id。

    优先沿用 ``str(object_id)``（兼容旧地图与既有测试）；当 Tiled ``object_id``
    大量重复时（铁锈官方图常见 160 个对象全是 1），后续对象改用
    ``rwmap_auto_N``。该前缀不含逗号/分号/括号/空白，不会被节点图的
    ``_parse_ids`` / ``_split_refs`` 拆开。
    """
    cand = str(getattr(obj, "object_id", "") or "")
    if cand and cand not in used:
        used.add(cand)
        return cand
    n = 1
    while f"rwmap_auto_{n}" in used:
        n += 1
    cand = f"rwmap_auto_{n}"
    used.add(cand)
    return cand


def ensure_trigger_ids(doc) -> int:
    """为文档中每一个缺少自定义 id 的触发器补上**全图唯一**的 id。

    默认取 ``object_id``；当 Tiled object id 重复时（铁锈地图常见）改用
    ``rwmap_auto_N``，避免保存后几十个触发器全变成 ``id="1"``。
    返回新增的 id 数量。幂等：已有 id 的触发器不会被动。调用方通常应在写回 TMX
    前调用，以保证游戏内能正确解析 alsoActivate 等链接。

    注意：补 id 会顺带把**所属对象层标记 dirty**，这样写回时该层会被重新同步、
    新加的 id 属性才会真正落盘（否则仅在内存中存在，保存后游戏仍读不到）。
    """
    if doc is None:
        return 0
    added = 0
    used = used_trigger_ids(doc)   # 全图共享：重复 object_id 时不能都补成 "1"
    for layer in doc.layers:
        if not isinstance(layer, ObjectLayer):
            continue
        layer_changed = False
        for obj in layer.objects:
            if not _is_rw_trigger(obj, getattr(layer, "name", "")):
                continue
            if obj.properties.get("id"):
                continue
            obj.properties.set("id", allocate_trigger_id(obj, used))
            added += 1
            layer_changed = True
        if layer_changed:
            layer.dirty = True
    return added
