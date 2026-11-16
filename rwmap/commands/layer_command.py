"""图层属性与结构命令（可见性 / 透明度 / 重命名 / 增删复制排序合并锁定）。"""
from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from ..document.layer import ObjectLayer, TileLayer


class LayerCommand(QUndoCommand):
    def __init__(self, layer, attr: str, old, new, apply_cb, text="修改图层"):
        super().__init__(text)
        self.layer = layer
        self.attr = attr
        self.old = old
        self.new = new
        self.apply_cb = apply_cb

    def _set(self, value):
        setattr(self.layer, self.attr, value)
        self.apply_cb(self.layer)

    def redo(self):
        self._set(self.new)

    def undo(self):
        self._set(self.old)


class LayerFlagCommand(QUndoCommand):
    """切换图层的单个布尔 / 标量属性（锁定、可见、透明度、名称），一条撤销可还原。

    ``merge_key`` 非空时允许与栈顶同键命令合并：拖透明度滑杆会连续产生几十次
    ``valueChanged``，合并后整段拖动只留**一条**撤销记录（撤销回到拖动前的值）。
    """

    def __init__(self, doc, layer, attr, old, new, label, refresh,
                 merge_key: str | None = None, dirty: bool = True):
        super().__init__(label)
        self.doc = doc
        self.layer = layer
        self.attr = attr
        self.old = old
        self.new = new
        self.refresh = refresh
        self.merge_key = merge_key
        # 可见性 / 透明度只影响本会话的显示（保真写回不动 XML 这两个属性），
        # 撤销要记，但别把文档标成「未保存」。
        self.mark_dirty = dirty

    def _set(self, value):
        setattr(self.layer, self.attr, value)
        if self.mark_dirty:
            self.doc.dirty = True
        if self.refresh:
            self.refresh()

    def redo(self):
        self._set(self.new)

    def undo(self):
        self._set(self.old)

    # ---- 连续微调合并（透明度滑杆 / 同名重命名连打） ----
    def id(self) -> int:
        if not self.merge_key:
            return -1
        # 用内容哈希而不是对象 id：同一次拖动每帧都会新建 key 字符串。
        return hash(self.merge_key) & 0x7FFFFFF0

    def mergeWith(self, other) -> bool:      # noqa: N802 (Qt 命名)
        if not isinstance(other, LayerFlagCommand):
            return False
        if other.merge_key != self.merge_key or other.layer is not self.layer:
            return False
        if other.attr != self.attr:
            return False
        self.new = other.new
        return True


class LayerStructureCommand(QUndoCommand):
    """整段替换 ``doc.layers`` 列表，用于增 / 删 / 复制 / 排序。

    这些操作只改 ``doc.layers`` 的成员与顺序，图层内容本身不变，
    因此存「前 / 后」两份图层列表引用即可在撤销 / 重做时精确还原。
    """

    def __init__(self, doc, before, after, label, refresh):
        super().__init__(label)
        self.doc = doc
        self.before = before
        self.after = after
        self.refresh = refresh

    def _apply(self, layers):
        self.doc.layers = layers
        self.doc.dirty = True
        if self.refresh:
            self.refresh()

    def redo(self):
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)


class MergeLayerCommand(QUndoCommand):
    """把 ``source`` 合并到其下方（render 顺序更低）的 ``target`` 层。

    合并结果在构造时算好；撤销时把目标层恢复到合并前内容，
    并把 ``source`` 按原索引插回 ``doc.layers``。
    """

    def __init__(self, doc, target, source, source_index, label, refresh):
        super().__init__(label)
        self.doc = doc
        self.target = target
        self.source = source
        self.source_index = source_index
        self.refresh = refresh
        if isinstance(target, TileLayer):
            self.old_target = list(target.cells)
            self.merged = list(target.cells)
            for i, v in enumerate(source.cells):
                if v:
                    self.merged[i] = v
        else:
            self.old_target = list(target.objects)
            self.merged = list(target.objects) + list(source.objects)

    def _apply_target(self, value):
        if isinstance(self.target, TileLayer):
            self.target.cells = list(value)
        else:
            self.target.objects = list(value)
        self.target.dirty = True

    def redo(self):
        self._apply_target(self.merged)
        self.doc.layers = [l for l in self.doc.layers if l is not self.source]
        self.doc.dirty = True
        if self.refresh:
            self.refresh()

    def undo(self):
        self._apply_target(self.old_target)
        layers = list(self.doc.layers)
        layers.insert(self.source_index, self.source)
        self.doc.layers = layers
        self.source.dirty = True
        self.doc.dirty = True
        if self.refresh:
            self.refresh()


class MapTransformCommand(QUndoCommand):
    """翻转 / 旋转整张地图：替换所有图层内容并重设地图宽高（旋转时互换）。

    ``before`` / ``after`` 是两份完整的图层列表；``old_dims`` / ``new_dims`` 为
    ``(width, height, tile_w, tile_h)``。
    """

    def __init__(self, doc, before, after, old_dims, new_dims, label, refresh):
        super().__init__(label)
        self.doc = doc
        self.before = before
        self.after = after
        self.old_dims = old_dims
        self.new_dims = new_dims
        self.refresh = refresh

    def _apply(self, layers, dims):
        self.doc.layers = layers
        (self.doc.width, self.doc.height,
         self.doc.tile_w, self.doc.tile_h) = dims
        self.doc.dirty = True
        if self.refresh:
            self.refresh()

    def redo(self):
        self._apply(self.after, self.new_dims)

    def undo(self):
        self._apply(self.before, self.old_dims)

