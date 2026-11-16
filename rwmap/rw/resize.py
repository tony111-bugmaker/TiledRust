"""调整地图尺寸 + 挪动的纯算法（与 Qt 无关，便于单测）。

坐标约定
--------
``dx`` / ``dy`` 是「挪动」量，单位为格：**把现有内容整体平移 (dx, dy)**。
正数向右 / 向下，负数向左 / 向上。于是

    新图 (c + dx, r + dy) = 旧图 (c, r)

反过来说，新图某格取自旧图的 (c - dx, r - dy)，越界处为空格 0。
旧图格 (c, r) 若平移后落到新图范围外（c+dx < 0 或 >= 新宽 等），
即为「溢出瓦片」，会被裁掉；预览里用连通块红框标出这些格。
"""
from __future__ import annotations

import copy


def shift_cells(cells: list[int], width: int, height: int,
                new_w: int, new_h: int, dx: int, dy: int):
    """把旧层内容平移 (dx, dy) 放进 new_w×new_h 的新层。

    返回 ``(new_cells, overflow)``：
    - ``new_cells`` 线性数组，长度 new_w*new_h；
    - ``overflow`` 是旧坐标下被裁掉的格集合 ``{(c, r), ...}``（仅含非零格）。
    """
    out = [0] * (new_w * new_h)
    overflow: set[tuple[int, int]] = set()
    for r in range(height):
        base = r * width
        for c in range(width):
            raw = cells[base + c]
            if not raw:
                continue
            nc, nr = c + dx, r + dy
            if 0 <= nc < new_w and 0 <= nr < new_h:
                out[nr * new_w + nc] = raw
            else:
                overflow.add((c, r))
    return out, overflow


def connected_blocks(cells):
    """把格坐标集合按 4-邻接分组，返回每组的外接矩形列表。

    入参 ``cells`` 是 ``(c, r)`` 的可迭代集合（任意坐标系：旧图坐标或
    已平移后的坐标均可）。返回 ``[(c0, r0, c1, r1), ...]``，首尾格都包含。

    这正是图章预览越界红框用的「连通块整框」逻辑：一段连续的溢出格
    只画一个大框，而不是一格一个小框。
    """
    remain = set(cells)
    blocks: list[tuple[int, int, int, int]] = []
    while remain:
        start = next(iter(remain))
        remain.discard(start)
        stack = [start]
        min_c = max_c = start[0]
        min_r = max_r = start[1]
        while stack:
            c, r = stack.pop()
            min_c, max_c = min(min_c, c), max(max_c, c)
            min_r, max_r = min(min_r, r), max(max_r, r)
            for nb in ((c - 1, r), (c + 1, r), (c, r - 1), (c, r + 1)):
                if nb in remain:
                    remain.discard(nb)
                    stack.append(nb)
        blocks.append((min_c, min_r, max_c, max_r))
    return blocks


def shift_objects(objects, dx: int, dy: int, tile_w: int, tile_h: int,
                  new_w: int, new_h: int, remove_outside: bool):
    """把对象整体平移 (dx*tile_w, dy*tile_h) 像素。

    ``remove_outside`` 为真时，丢弃完全落到新地图矩形之外的对象
    （对象外接矩形与新地图无交集才算溢出，压线/部分在内都保留）。
    返回深拷贝后的新列表，不改原对象。
    """
    out = []
    map_w, map_h = new_w * tile_w, new_h * tile_h
    for obj in objects:
        no = copy.deepcopy(obj)
        no.xml_element = None
        no.x = no.x + dx * tile_w
        no.y = no.y + dy * tile_h
        if remove_outside:
            x, y, w, h = no.bounds(tile_w, tile_h)
            if x + w <= 0 or y + h <= 0 or x >= map_w or y >= map_h:
                continue
        out.append(no)
    return out
