"""框选（首版桩，保留接口）。"""
from __future__ import annotations

from .tool_base import Tool


class SelectTool(Tool):
    id = "tileselect"
    name = "框选 (未实现)"

    def desired_gid(self, state, layer, col, row):
        return None
