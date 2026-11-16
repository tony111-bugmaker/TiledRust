"""图集文档（``.tsx``）撤销命令：整份「瓦片属性表」快照替换。

图集没有图层、没有单元格，一次编辑动的永远是若干瓦片的 ``PropertyBag``
（用户点一次「整理冲突」可能同时清掉好几个键、甚至改到别的瓦片），
所以按整表快照做命令：实现最简单，也不会漏掉 type / text 标记这类细节。
"""
from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from ..document.properties import PropertyBag


def _bag_snapshot(bag: PropertyBag):
    return (dict(bag.values), dict(bag.types), set(bag.text_props))


def _bag_restore(bag: PropertyBag, snap) -> None:
    values, types, text = snap
    bag.values = dict(values)
    bag.types = dict(types)
    bag.text_props = set(text)


def snapshot(tdoc):
    """整份图集属性快照：每个瓦片的 bag + 图集级属性 + 图集名。"""
    ts = getattr(tdoc, "tileset", None)
    if ts is None:
        return None
    return {
        "tiles": {local_id: _bag_snapshot(bag) for local_id, bag in ts.tiles.items()},
        "props": _bag_snapshot(ts.properties),
        "name": ts.name,
    }


def restore(tdoc, snap) -> None:
    ts = getattr(tdoc, "tileset", None)
    if ts is None or snap is None:
        return
    tiles = {}
    for local_id, (values, types, text) in snap["tiles"].items():
        bag = PropertyBag()
        bag.values = dict(values)
        bag.types = dict(types)
        bag.text_props = set(text)
        tiles[local_id] = bag
    ts.tiles = tiles
    _bag_restore(ts.properties, snap["props"])
    ts.name = snap["name"]
    tdoc.dirty = True


class TilesetCommand(QUndoCommand):
    """一次图集属性编辑（redo / undo 都替换整表，再回调刷新界面）。

    ``merge_key`` 非空时允许与栈顶同键命令合并：输入框每敲一个字符都会写回
    一次，合并后一段连续输入只留**一条**撤销记录。
    """

    def __init__(self, tdoc, before, after, text="编辑瓦片属性", refresh=None,
                 merge_key: str | None = None):
        super().__init__(text)
        self.tdoc = tdoc
        self.before = before
        self.after = after
        self.refresh = refresh
        self.merge_key = merge_key

    def _apply(self, snap):
        restore(self.tdoc, snap)
        if self.refresh is not None:
            self.refresh()

    def redo(self):
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)

    # ---- 连续输入合并 ----
    def id(self) -> int:
        if not self.merge_key:
            return -1
        return hash(self.merge_key) & 0x7FFFFFF0

    def mergeWith(self, other) -> bool:      # noqa: N802 (Qt 命名)
        if not isinstance(other, TilesetCommand):
            return False
        if other.merge_key != self.merge_key or self.merge_key is None:
            return False
        if other.tdoc is not self.tdoc:
            return False
        self.after = other.after
        return True
