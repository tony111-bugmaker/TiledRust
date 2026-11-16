"""地图 / 图层翻转与旋转（与 Qt 无关的纯算法）。

约定：单元格数组 ``cells`` 以 ``idx = row * width + col`` 线性存储；
瓦片层翻转 / 旋转只搬动 raw gid（含翻转标志位），不改变每个格子的翻转语义；
对象层按像素坐标绕地图中心做刚体变换（矩形取旋转后包围盒，多边形旋转各相对点）。
"""
from __future__ import annotations

import copy


def flip_cells(cells: list[int], width: int, height: int, horizontal: bool) -> list[int]:
    """水平翻转镜像列、垂直翻转镜像行，尺寸不变。"""
    out = [0] * len(cells)
    for r in range(height):
        for c in range(width):
            if horizontal:
                src = r * width + (width - 1 - c)
            else:
                src = (height - 1 - r) * width + c
            out[r * width + c] = cells[src]
    return out


def rotate_cells_90(cells: list[int], width: int, height: int, cw: bool):
    """顺时针 / 逆时针旋转 90°，新尺寸 (new_w=height, new_h=width)。"""
    new_w, new_h = height, width
    out = [0] * (new_w * new_h)
    for r2 in range(new_h):
        for c2 in range(new_w):
            if cw:
                old_row = height - 1 - c2
                old_col = r2
            else:
                old_row = c2
                old_col = width - 1 - r2
            out[r2 * new_w + c2] = cells[old_row * width + old_col]
    return out, new_w, new_h


def _rot_point(px: float, py: float, cx: float, cy: float, cw: bool):
    dx, dy = px - cx, py - cy
    if cw:
        return cx + dy, cy - dx
    return cx - dy, cy + dx


def _rot_rel(px: float, py: float, cw: bool):
    return (py, -px) if cw else (-py, px)


def transform_objects(objects, width: int, height: int, tile_w: int, tile_h: int, op: str):
    """对对象列表做翻转 / 旋转，返回变换后的新对象列表（深拷贝，不改原对象）。

    ``op`` ∈ {"flip_h", "flip_v", "rot_cw", "rot_ccw"}。
    """
    cx = width * tile_w / 2.0
    cy = height * tile_h / 2.0
    Wpx, Hpx = width * tile_w, height * tile_h
    out = []
    for obj in objects:
        o = copy.deepcopy(obj)
        o.xml_element = None
        if op == "flip_h":
            o.x = Wpx - o.x - o.width
            if o.points:
                o.points = [(-px, py) for px, py in o.points]
        elif op == "flip_v":
            o.y = Hpx - o.y - o.height
            if o.points:
                o.points = [(px, -py) for px, py in o.points]
        elif op in ("rot_cw", "rot_ccw"):
            cw = op == "rot_cw"
            if o.shape in ("rect", "ellipse", "image", "tile", "text"):
                corners = [(o.x, o.y), (o.x + o.width, o.y),
                           (o.x, o.y + o.height), (o.x + o.width, o.y + o.height)]
                rc = [_rot_point(x, y, cx, cy, cw) for x, y in corners]
                xs = [p[0] for p in rc]
                ys = [p[1] for p in rc]
                o.x = min(xs)
                o.y = min(ys)
                o.width = max(max(xs) - min(xs), 1)
                o.height = max(max(ys) - min(ys), 1)
            elif o.shape == "point":
                o.x, o.y = _rot_point(o.x, o.y, cx, cy, cw)
            elif o.shape in ("polygon", "polyline"):
                o.x, o.y = _rot_point(o.x, o.y, cx, cy, cw)
                o.points = [_rot_rel(px, py, cw) for px, py in o.points]
        out.append(o)
    return out
