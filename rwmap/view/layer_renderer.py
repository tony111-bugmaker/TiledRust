"""按 XML 顺序构建图层 item 并设置 ZValue。"""
from __future__ import annotations

from ..document.layer import ObjectLayer, TileLayer
from .object_layer_item import ObjectLayerItem
from .tile_layer_item import TileLayerItem


def build_layer_items(doc):
    items = {}
    for index, layer in enumerate(doc.layers):
        if isinstance(layer, TileLayer):
            item = TileLayerItem(layer, doc, index)
        elif isinstance(layer, ObjectLayer):
            item = ObjectLayerItem(layer, doc, index)
        else:
            continue
        items[id(layer)] = item
    return items
