"""瓦片矩形选区 / 复制 / 粘贴 / 移动的纯逻辑（与 Qt 无关，便于单测）。

与 ``main_window`` 中对应的实例方法逻辑保持一致；``main_window`` 直接调用这里
的算法来改写图层。
"""
from __future__ import annotations


def norm_rect(p0, p1):
    x0, y0 = min(p0[0], p1[0]), min(p0[1], p1[1])
    x1, y1 = max(p0[0], p1[0]), max(p0[1], p1[1])
    return (x0, y0, x1, y1)


def rect_to_cells(rect, tile_w, tile_h, boundary_eps=1e-6):
    """像素矩形 → 格矩形；右 / 下边界是 [x0, x1) 半开区间。

    ``boundary_eps`` 容忍「鼠标正好落在格线上」时的亚像素量化误差：
    GUI 里视口坐标会先被取整再映射回场景，缩放后 60/40 这类格线值可能
    变成 40.13，不用容差就会多选一行。调用方可按当前缩放传入 1 屏幕像素
    对应的场景长度。
    """
    x0, y0, x1, y1 = rect
    col0 = int(x0 // tile_w)
    row0 = int(y0 // tile_h)
    col1 = int((x1 - boundary_eps) // tile_w)
    row1 = int((y1 - boundary_eps) // tile_h)
    return (col0, row0, max(col1 - col0 + 1, 1), max(row1 - row0 + 1, 1))


def cells_to_rect(sel, tile_w, tile_h):
    col0, row0, cols, rows = sel
    return (col0 * tile_w, row0 * tile_h,
            (col0 + cols) * tile_w, (row0 + rows) * tile_h)


def in_rect(rect, x, y):
    if not rect:
        return False
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


def moved_rect(rect, dx, dy):
    if not rect:
        return None
    x0, y0, x1, y1 = rect
    return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def read_selection(layer, sel):
    col0, row0, cols, rows = sel
    # TileLayer.cells 是定长一维表，get() 不查边界：负索引绕回表尾、
    # 正越界 IndexError。地图外拖框 / 撤销恢复的旧选区都可能越界，
    # 越界格一律当空格 0（与 main_window._capture_cells 一致）。
    return [[layer.get(col0 + c, row0 + r)
             if layer.in_bounds(col0 + c, row0 + r) else 0
             for c in range(cols)]
            for r in range(rows)]


def compute_move_changes(layer, sel, dc, dr):
    """计算把 ``sel`` 区域移动 ``(dc, dr)`` 格的 cell diff（一步撤销用）。

    移动语义：原区域中未被新位置覆盖的格清空（0），新位置写入原内容；
    重叠部分取移动后的内容。返回 ``[(cell_index, old, new)]``。
    """
    col0, row0, cols, rows = sel
    content = read_selection(layer, sel)
    orig = {(col0 + c, row0 + r) for c in range(cols) for r in range(rows)}
    newpos = {(col0 + c + dc, row0 + r + dr) for c in range(cols) for r in range(rows)}
    final = {}
    for (nc, nr) in newpos:
        c = nc - dc - col0
        r = nr - dr - row0
        final[(nc, nr)] = content[r][c]
    for (oc, or_) in orig:
        if (oc, or_) not in newpos:
            final[(oc, or_)] = 0
    changes = []
    for (c, r), new in final.items():
        if not layer.in_bounds(c, r):
            continue
        idx = layer.index(c, r)
        old = layer.cells[idx]
        if old != new:
            changes.append((idx, old, new))
    return changes
