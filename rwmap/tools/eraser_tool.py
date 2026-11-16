"""橡皮：写 gid=0。"""
from __future__ import annotations

from .tool_base import Tool


class EraserTool(Tool):
    id = "eraser"
    name = "橡皮 (E)"

    def desired_gid(self, state, layer, col, row):
        return 0
