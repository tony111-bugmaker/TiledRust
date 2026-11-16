"""地图文档级撤销命令：外部图集引用、地图属性编辑。

与图层命令分开：这两类改动动的是 ``<map>`` 的子节点（tileset / properties），
不属于图层，也不该走 MapTransformCommand 的 layers 快照。
"""
from __future__ import annotations

from lxml import etree
from PySide6.QtGui import QUndoCommand

from ..io.tmx_writer import _write_properties

# TMX 中排在 tileset 之后的元素；新增 <tileset> 要插到它们前面。
_AFTER_TILESET_TAGS = {"layer", "objectgroup", "imagelayer", "group"}


class AddExternalTilesetCommand(QUndoCommand):
    """添加 / 移除一个外部图集引用（doc.tilesets + <map> 的 <tileset> 节点）。

    QUndoStack.push() 会立即调用一次 redo()，所以 redo/undo 都写成幂等：
    redo 只在对象不在列表里时才 append，undo 按身份过滤而非按值。
    """

    def __init__(self, doc, ts, source: str, firstgid: int,
                 refresh=None, label="添加外部图集"):
        super().__init__(label)
        self.doc = doc
        self.ts = ts
        self.source = source
        self.firstgid = firstgid
        self.refresh = refresh
        self.dom_element = None

    def _sync_dom(self, add: bool):
        root = self.doc.tree.getroot() if self.doc.tree is not None else None
        if root is None:
            return
        if add:
            if self.dom_element is not None and self.dom_element.getparent() is not None:
                return                       # 已经在树里，避免重复插入
            node = etree.Element("tileset")
            node.set("firstgid", str(self.firstgid))
            node.set("source", self.source)
            for i, child in enumerate(root):
                if isinstance(child.tag, str) and child.tag in _AFTER_TILESET_TAGS:
                    root.insert(i, node)
                    break
            else:
                root.append(node)
            self.dom_element = node
        else:
            if self.dom_element is not None and self.dom_element.getparent() is not None:
                self.dom_element.getparent().remove(self.dom_element)

    def _apply(self, add: bool):
        if add:
            if not any(t is self.ts for t in self.doc.tilesets):
                self.doc.tilesets.append(self.ts)
                self.doc.rebuild_index()
        else:
            self.doc.tilesets[:] = [t for t in self.doc.tilesets if t is not self.ts]
            self.doc.rebuild_index()
        self._sync_dom(add)
        self.doc.dirty = True
        if self.refresh is not None:
            self.refresh()

    def redo(self):
        self._apply(True)

    def undo(self):
        self._apply(False)


def _bag_snapshot(bag):
    return (dict(bag.values), dict(bag.types), set(bag.text_props))


class EditMapPropertiesCommand(QUndoCommand):
    """编辑地图根 <properties>：内存 PropertyBag 与 DOM 同步替换。

    只在用户主动打开「地图属性」对话框并确认时创建，不影响其它地图的保真写回。
    """

    def __init__(self, doc, before_bag, after_values: dict,
                 refresh=None, label="编辑地图属性"):
        super().__init__(label)
        self.doc = doc
        self.before = _bag_snapshot(before_bag)
        # 保留旧的属性类型 / text 形态，只替换值集合
        self.after = (
            dict(after_values),
            {k: t for k, t in before_bag.types.items() if k in after_values},
            {k for k in before_bag.text_props if k in after_values},
        )
        self.refresh = refresh

    def _apply(self, snap):
        bag = self.doc.properties
        bag.values, bag.types, bag.text_props = (
            dict(snap[0]), dict(snap[1]), set(snap[2]))
        root = self.doc.tree.getroot() if self.doc.tree is not None else None
        if root is not None:
            _write_properties(root, bag)
        self.doc.dirty = True
        if self.refresh is not None:
            self.refresh()

    def redo(self):
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)
