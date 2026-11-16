"""工具接口：返回要写入的 raw_gid，None 表示不写入。"""
from __future__ import annotations

import importlib
from typing import Any

from PySide6.QtCore import Qt


class Tool:
    id = ""
    name = ""
    cursor = Qt.CrossCursor

    def desired_gid(self, state: Any, layer, col: int, row: int) -> int | None:
        return None


_TOOL_CLASSES = {
    "brush": ("rwmap.tools.brush_tool", "BrushTool"),
    "eraser": ("rwmap.tools.eraser_tool", "EraserTool"),
    # 吸管不再是可切换工具：I 键 / Alt+左键直接临时取色，见 picker_tool.py
    "fill": ("rwmap.tools.fill_tool", "FillTool"),
    "tileselect": ("rwmap.tools.select_tool", "SelectTool"),
}

TOOLS = {key: value[1] for key, value in _TOOL_CLASSES.items()}


def get_tool(tool_id: str) -> Tool:
    module_name, class_name = _TOOL_CLASSES.get(tool_id, _TOOL_CLASSES["brush"])
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls()
