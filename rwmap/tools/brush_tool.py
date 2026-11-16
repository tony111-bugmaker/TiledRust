"""画笔：写入当前笔刷 GID。"""
from __future__ import annotations

from PySide6.QtCore import Qt

from .tool_base import Tool


class BrushTool(Tool):
    id = "brush"
    name = "画笔 (B)"
    cursor = Qt.CrossCursor

    def desired_gid(self, state, layer, col, row):
        return int(state.brush_gid)
