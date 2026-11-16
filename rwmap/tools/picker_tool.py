"""临时吸管：读取光标下的 GID 设为笔刷，不切换当前工具。

首版把吸管做成了可切换的工具（工具栏按钮 + I 切到 picker），用起来要
「切过去 → 点一下 → 再切回画笔」。Tiled 的习惯是直接按 I 取光标下的瓦片、
或按住 Alt 点击取色，取完继续画。现在 picker 不再注册为工具，只保留取色
逻辑，由 MainWindow 的快捷键 / MapView.pickRequested 信号直接调用。
"""
from __future__ import annotations

from ..document.gid import split_gid
from ..document.layer import TileLayer
from .tool_base import Tool


def pick_gid(state, doc, col: int, row: int) -> int | None:
    """取该格可见层里最靠上的非空 gid（去掉翻转标志）。

    优先当前层；当前层为空时按图层绘制顺序从上层往下找，避免在水/草底图上
    吸出 0。返回 None 表示整列都是空的。
    """
    if doc is None or not (0 <= col < doc.width and 0 <= row < doc.height):
        return None
    ordered = []
    current = state.current_layer
    if isinstance(current, TileLayer):
        ordered.append(current)
    for layer in reversed(doc.layers):
        if (isinstance(layer, TileLayer) and layer is not current
                and getattr(layer, "visible", True)):
            ordered.append(layer)
    for layer in ordered:
        if not layer.in_bounds(col, row):
            continue
        gid, _h, _v, _d = split_gid(int(layer.get(col, row)))
        if gid:
            return gid
    return None


class PickerTool(Tool):
    """兼容保留：不再注册到工具表，请勿用 _set_tool("picker") 切换。"""
    id = "picker"
    name = "吸管（临时）"

    def desired_gid(self, state, layer, col, row):
        if layer is not None and layer.in_bounds(col, row):
            gid, _h, _v, _d = split_gid(int(layer.get(col, row)))
            state.brush_gid = gid
        return None
