"""格数据绘制命令：一次拖拽 = 一条命令（cell diff）。"""
from __future__ import annotations

import inspect

from PySide6.QtGui import QUndoCommand


class DrawCommand(QUndoCommand):
    """格数据绘制命令：一次拖拽 = 一条命令（cell diff）。

    ``selection`` / ``apply_selection`` 是可选的一组「选区快照」：
    移动 / 粘贴选区会同时改变选区位置，撤销 / 重做时要把选框一起带回
    对应时刻的位置。``selection=(before, after)`` 与 ``_apply(0)=撤销``、
    ``_apply(1)=重做`` 一一对应；不带这组参数的普通绘制命令行为不变。
    """

    def __init__(self, doc, layer, changes: list[tuple[int, int, int]], refresh,
                 text="绘制图块", *, selection=None, apply_selection=None,
                 skip_first_redo=False, merge_id=None):
        super().__init__(text)
        self.doc = doc
        self.layer = layer
        self.changes = changes          # (cell_index, old_raw_gid, new_raw_gid)
        self.refresh = refresh
        self.selection = selection
        self.apply_selection = apply_selection
        # AppState 的笔划在 stroke 阶段就已经把 new 写进 layer.cells，并批量
        # 刷新过画面。QUndoStack.push() 会立刻执行一次 redo，默认会把同一批
        # 格子再写一遍、再刷一次（大图章松手时明显掉帧）。带此标志时只跳过
        # 「push 触发的首次 redo」，之后的 undo/redo 仍照常回放。
        self._skip_first_redo = skip_first_redo
        # 只有带同一 merge_id 的自动映射命令会尝试合并；其余绘制命令
        # id() 返回 -1，QUndoStack 不会动它们。
        self.merge_id = merge_id
        # 兼容只接受 layer 的旧回调（探针/第三方脚本）。
        try:
            self._accepts_cells = len(inspect.signature(refresh).parameters) >= 2
        except (TypeError, ValueError):
            self._accepts_cells = True

    def _apply(self, value_index: int):
        for index, old, new in self.changes:
            self.layer.cells[index] = (old, new)[value_index]
        self.layer.dirty = True
        self.doc.dirty = True
        if self._accepts_cells:
            cells = [(index % self.layer.width, index // self.layer.width)
                     for index, _old, _new in self.changes]
            self.refresh(self.layer, cells)
        else:
            self.refresh(self.layer)
        if self.selection is not None and self.apply_selection is not None:
            self.apply_selection(self.selection[value_index])

    def redo(self):
        if self._skip_first_redo:
            # 只跳过 push() 触发的这一次；此后 redo（undo 之后再重做）必须写回 new。
            self._skip_first_redo = False
            return
        self._apply(1)

    def undo(self):
        self._apply(0)

    def id(self):
        """带 merge_id 时让 QUndoStack 尝试合并同批自动映射命令。"""
        if self.merge_id is None:
            return -1
        return (hash(self.merge_id) & 0x7FFFFFF0) or 1

    def mergeWith(self, other):
        """把相邻的同层自动映射结果并进一条命令，减少 macro 内子命令数。

        实测 QUndoStack 的顺序是「先执行 other.redo()，再尝试 mergeWith」，
        因此 other 的格子已经写好、画面已经刷新过，这里绝不能再写一遍；
        只需合并 changes，保证 undo 时用最早的 old 一次性还原全部格子。
        """
        if not isinstance(other, DrawCommand):
            return False
        if self.merge_id is None or self.merge_id != getattr(other, "merge_id", None):
            return False
        if self.layer is not other.layer or self.doc is not other.doc:
            return False
        if self.selection is not None or other.selection is not None:
            return False
        merged = {index: (old, new) for index, old, new in self.changes}
        for index, old, new in other.changes:
            if index in merged:
                merged[index] = (merged[index][0], new)   # 保留最早 old、取最新 new
            else:
                merged[index] = (old, new)
        self.changes = [(i, o, n) for i, (o, n) in merged.items()]
        return True
