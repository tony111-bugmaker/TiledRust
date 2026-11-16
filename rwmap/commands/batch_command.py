"""批量命令容器（多条子命令合并为一次撤销）。"""
from __future__ import annotations

from PySide6.QtGui import QUndoCommand


class BatchCommand(QUndoCommand):
    def __init__(self, children=None, text="批量编辑"):
        super().__init__(text)
        self.children = list(children or [])

    def add(self, command):
        self.children.append(command)

    def redo(self):
        for command in self.children:
            command.redo()

    def undo(self):
        for command in reversed(self.children):
            command.undo()
