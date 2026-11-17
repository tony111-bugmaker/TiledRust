"""对象编辑撤销命令：以「对象层快照」统一覆盖属性、坐标、增删。

对象数量很少（官方图通常 1~几十个），整层快照比逐字段命令更不容易漏掉
PropertyBag 的 text/type 标记，也能让「删除对象」的撤销把原 <object> 元素带回。

``MultiObjectCommand`` 是同一思路的多层版本：组合 / 对齐 / 分布 / 跨层删除
这类操作一次可能动到几个对象层，用一条命令同时替换多层的对象列表，
撤销时也只算**一步**。
"""
from __future__ import annotations

import copy

from PySide6.QtGui import QUndoCommand

from ..document.layer import ObjectLayer


def _clone(obj):
    """浅拷贝对象 + 深拷贝属性容器；xml_element 引用必须保留（保真写回靠它）。"""
    clone = copy.copy(obj)
    bag = copy.copy(obj.properties)
    bag.values = dict(obj.properties.values)
    bag.types = dict(obj.properties.types)
    bag.text_props = set(obj.properties.text_props)
    clone.properties = bag
    # 形状字段全是可变容器，必须一并拷贝，否则节点编辑会污染撤销快照。
    clone.points = [tuple(p) for p in obj.points]
    clone.text_style = dict(obj.text_style)
    return clone


def snapshot(layer: ObjectLayer):
    return [_clone(obj) for obj in layer.objects]


def restore(layer: ObjectLayer, snap) -> None:
    # 再克隆一次，避免文档里的对象被后续操作修改时污染撤销快照。
    layer.objects = [_clone(obj) for obj in snap]
    layer.dirty = True


class ObjectCommand(QUndoCommand):
    def __init__(self, doc, layer: ObjectLayer, before, after,
                 text: str = "编辑对象", refresh=None):
        super().__init__(text)
        self.doc = doc
        self.layer = layer
        self.before = before
        self.after = after
        self.refresh = refresh

    def _apply(self, snap):
        restore(self.layer, snap)
        self.doc.dirty = True
        if self.refresh is not None:
            self.refresh()

    def redo(self):
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)


def _signature_of(objects):
    """对象列表的轻量指纹：用来判断一次操作到底有没有真的改动。"""
    return [
        (o.object_id, o.name, o.shape, o.x, o.y, o.width, o.height,
         getattr(o, "rotation", 0.0), tuple(o.points),
         getattr(o, "image_flip_h", False), getattr(o, "image_flip_v", False),
         dict(o.properties.values), dict(o.properties.types),
         set(o.properties.text_props))
        for o in objects
    ]


def capture(layers):
    """对若干对象层做「操作前」快照，返回 ``[(layer, snap), ...]``。

    顺序按传入顺序去重（同一层只快照一次），供 :func:`push_object_edit` 使用。
    """
    out = []
    taken = []
    for layer in layers:
        if layer is None or any(layer is done for done in taken):
            continue
        taken.append(layer)
        out.append((layer, snapshot(layer)))
    return out


def push_object_edit(doc, stack, before, text="编辑对象", refresh=None) -> bool:
    """把 before 快照与当前对象层内容登记成一条撤销命令；返回是否真的入栈。

    没有实际变化的层会被跳过，整批都没变化时连命令都不产生，
    避免「点了对齐但本来已经对齐」也在撤销栈里留一条空记录。
    """
    changed = []
    for layer, snap in before:
        if _signature_of(layer.objects) == _signature_of(snap):
            continue
        changed.append((layer, snap, snapshot(layer)))
    if not changed:
        return False
    cmd = MultiObjectCommand(doc, changed, text, refresh)
    if stack is not None:
        stack.push(cmd)
    else:
        cmd.redo()
    return True


class MultiObjectCommand(QUndoCommand):
    """跨多个对象层的一次编辑：每条命令携带若干 ``(layer, before, after)``。"""

    def __init__(self, doc, entries, text="编辑对象", refresh=None):
        super().__init__(text)
        self.doc = doc
        self.entries = list(entries)
        self.refresh = refresh

    def _apply(self, use_after: bool):
        for layer, before, after in self.entries:
            restore(layer, after if use_after else before)
        self.doc.dirty = True
        if self.refresh is not None:
            self.refresh()

    def redo(self):
        self._apply(True)

    def undo(self):
        self._apply(False)
