"""填充（Flood Fill / 桶填充）：把点击格所在的同色四连通区域替换为当前笔刷 GID。

单格的 ``desired_gid`` 返回 None，由 ``MainWindow._on_press`` 检测到
``tool_id == "fill"`` 后改走 ``fill_region``，一次改写整片区域（一条撤销）。
"""
from __future__ import annotations

from typing import List, Tuple

from .tool_base import Tool


class FillTool(Tool):
    id = "fill"
    name = "填充 (F)"

    def desired_gid(self, state, layer, col, row):
        # 填充不走单格写入流程，由 _on_press 的 fill 分支接管。
        return None

    def fill_region(self, state, layer, col: int, row: int):
        """计算洪水填充要改写的格子，返回 ``[(col, row, new_gid), ...]``。

        无法填充（越界 / 目标与起点同色）返回 ``None``。
        """
        target = int(state.brush_gid)
        if not layer.in_bounds(col, row):
            return None
        match = layer.cells[layer.index(col, row)]
        if match == target:
            return None
        w, h = layer.width, layer.height
        cells = layer.cells
        index = layer.index
        stack: List[Tuple[int, int]] = [(col, row)]
        out: List[Tuple[int, int, int]] = []
        seen: set = set()
        while stack:
            c, r = stack.pop()
            if (c, r) in seen:
                continue
            seen.add((c, r))
            if cells[index(c, r)] != match:
                continue
            out.append((c, r, target))
            for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nc, nr = c + dc, r + dr
                if 0 <= nc < w and 0 <= nr < h and (nc, nr) not in seen:
                    stack.append((nc, nr))
        return out
