"""Triggers / 对象语义编辑面板。

首版是只读树；现在支持：
- 双击编辑对象名称、类型、位置 (x, y, w, h) 和任意自定义属性；
- 增删自定义属性、新建 / 删除对象；
- 所有改动走 ObjectCommand → QUndoStack，Ctrl+Z 可还原；
- 选中对象时通知画布高亮，画布选中对象时同步到这里。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDockWidget, QHBoxLayout, QInputDialog, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..commands.object_command import ObjectCommand, snapshot
from ..document.layer import ObjectLayer
from ..document.map_object import MapObject, parse_points, format_points
from .dock_chrome import apply_panel_theme, install_title_bar

_FIELD = 0          # 伪属性：名称 / 类型 / 位置
_PROPERTY = 1       # 真实自定义属性


class TriggersDock(QDockWidget):
    objectSelected = Signal(object)

    def __init__(self, parent=None):
        super().__init__("Triggers / 对象", parent)
        self.doc = None
        self.undo_stack = None
        self.refresh_cb = None
        self._meta = {}             # id(item) -> (layer, obj, kind, key)
        self._loading = False

        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        buttons = QHBoxLayout()
        self.btn_add_prop = QPushButton("+ 属性")
        self.btn_del_prop = QPushButton("- 属性")
        self.btn_add_obj = QPushButton("+ 对象")
        self.btn_del_obj = QPushButton("- 对象")
        for btn in (self.btn_add_prop, self.btn_del_prop,
                    self.btn_add_obj, self.btn_del_obj):
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["对象 / 键", "值"])
        self.tree.setColumnWidth(0, 150)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.tree, 1)
        self.setWidget(holder)
        apply_panel_theme(holder)
        install_title_bar(self)

        self.btn_add_prop.clicked.connect(self._add_property)
        self.btn_del_prop.clicked.connect(self._del_property)
        self.btn_add_obj.clicked.connect(self._add_object)
        self.btn_del_obj.clicked.connect(self._del_object)

    # ---------------- 文档 ----------------
    def set_document(self, doc, undo_stack=None, refresh_cb=None):
        self.doc = doc
        self.undo_stack = undo_stack
        self.refresh_cb = refresh_cb
        self.refresh()

    def refresh(self):
        meta = self._current_meta()
        selected_id = meta[1].object_id if meta else None
        self._loading = True
        self.tree.blockSignals(True)
        self.tree.clear()
        self._meta.clear()
        if self.doc is not None:
            for layer in self.doc.layers:
                if not isinstance(layer, ObjectLayer):
                    continue
                top = QTreeWidgetItem(self.tree, [layer.name, f"{len(layer.objects)} 个对象"])
                top.setExpanded(True)
                for obj in layer.objects:
                    node = QTreeWidgetItem(top, [obj.label, obj.type_ or "-"])
                    node.setExpanded(True)
                    self._meta[id(node)] = (layer, obj, None, None)
                    self._add_field(node, layer, obj, "name", "名称", obj.name)
                    self._add_field(node, layer, obj, "type", "类型", obj.type_)
                    self._add_field(node, layer, obj, "rect", "位置",
                                    f"{obj.x:g}, {obj.y:g}, {obj.width:g}, {obj.height:g}")
                    self._add_field(node, layer, obj, "shape", "形状",
                                    obj.shape_label, editable=False)
                    if obj.shape == "text":
                        style = obj.text_attr
                        self._add_field(node, layer, obj, "text", "文字", obj.text)
                        self._add_field(node, layer, obj, "pixelsize", "字号",
                                        style["pixelsize"])
                        self._add_field(node, layer, obj, "color", "字色",
                                        style["color"])
                        self._add_field(node, layer, obj, "align", "对齐",
                                        f"{style['halign']},{style['valign']}")
                        self._add_field(node, layer, obj, "wrap", "换行",
                                        style["wrap"])
                    elif obj.shape == "image":
                        self._add_field(node, layer, obj, "image", "图片",
                                        obj.image_source)
                    elif obj.shape in ("polygon", "polyline"):
                        self._add_field(node, layer, obj, "points", "点集",
                                        format_points(obj.points))
                    elif obj.shape == "tile":
                        self._add_field(node, layer, obj, "gid", "图块 gid",
                                        str(obj.gid))
                    self._add_field(node, layer, obj, "rotation", "旋转",
                                    f"{obj.rotation:g}")
                    for key, value in obj.properties.items():
                        self._add_field(node, layer, obj, key, key, value,
                                        kind=_PROPERTY)
        self.tree.blockSignals(False)
        self._loading = False
        # 重建后恢复选中项，否则连续编辑会丢失目标并误发「取消选中」。
        if selected_id is not None:
            for top_index in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(top_index)
                for i in range(top.childCount()):
                    node = top.child(i)
                    found = self._meta.get(id(node))
                    if found and found[1].object_id == selected_id:
                        self.tree.setCurrentItem(node)
                        return

    def _add_field(self, parent, layer, obj, key, label, value,
                   kind=_FIELD, editable=True):
        item = QTreeWidgetItem(parent, [label, str(value)])
        if editable:
            item.setFlags(item.flags() | Qt.ItemIsEditable)
        self._meta[id(item)] = (layer, obj, kind, key)

    # ---------------- 选中 ----------------
    def _current_meta(self):
        item = self.tree.currentItem()
        return self._meta.get(id(item)) if item is not None else None

    def _current_object(self):
        meta = self._current_meta()
        return (meta[0], meta[1]) if meta else (None, None)

    def _on_current_changed(self, current, _previous):
        meta = self._meta.get(id(current)) if current is not None else None
        self.objectSelected.emit(meta[1] if meta else None)

    def select_object(self, obj):
        """画布选中对象时同步树里的选中项。"""
        if obj is None:
            return
        for layer_node_index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(layer_node_index)
            for i in range(top.childCount()):
                meta = self._meta.get(id(top.child(i)))
                if meta and meta[1] is obj:
                    self.tree.setCurrentItem(top.child(i))
                    return

    # ---------------- 编辑提交 ----------------
    def _after_change(self):
        self.refresh()
        if self.refresh_cb is not None:
            self.refresh_cb()

    def _commit(self, mutate, text, layer=None):
        # 允许直接以数据层调用（测试 / 画布操作），不强制依赖树的当前选中项。
        if layer is None:
            layer, _obj = self._current_object()
        if layer is None or self.doc is None:
            return
        before = snapshot(layer)
        mutate(layer)
        after = snapshot(layer)
        layer.dirty = True
        self.doc.dirty = True
        cmd = ObjectCommand(self.doc, layer, before, after, text,
                            self._after_change)
        if self.undo_stack is not None:
            self.undo_stack.push(cmd)
        else:
            cmd.redo()

    def _on_item_changed(self, item, column):
        if self._loading or column != 1:
            return
        meta = self._meta.get(id(item))
        if meta is None:
            return
        layer, obj, kind, key = meta
        text = item.text(column)
        if kind == _PROPERTY:
            if text == obj.properties.get(key, ""):
                return
            self._edit_value(layer, obj, key, text)
        else:
            self._edit_field(layer, obj, key, text)

    def _edit_value(self, layer, obj, key, value):
        def mutate(current_layer):
            target = next(o for o in current_layer.objects if o.object_id == obj.object_id)
            target.properties.set(key, value)
        self._commit(mutate, f"修改属性 {key}", layer)

    def _edit_field(self, layer, obj, key, text):
        values = None
        parsed = None
        if key == "rect":
            try:
                values = [float(v) for v in text.replace("，", ",").split(",")]
            except ValueError:
                values = None
            if values is None or len(values) != 4:
                self.refresh()
                return
        elif key in ("pixelsize", "gid", "rotation"):
            try:
                parsed = float(text)
            except ValueError:
                self.refresh()
                return
            if key in ("pixelsize", "gid"):
                parsed = int(parsed)
        elif key == "points":
            parsed = parse_points(text)
            if not parsed:
                self.refresh()
                return
        elif key == "align":
            parts = [p.strip() for p in text.replace("，", ",").split(",")]
            if len(parts) != 2:
                self.refresh()
                return
            parsed = parts
        elif key == "wrap":
            parsed = ("1" if text.strip().lower() in
                      ("1", "true", "yes", "on", "是") else "0")

        def _style(target, **changes):
            merged = dict(target.text_style)
            merged.update(changes)
            target.text_style = merged

        def mutate(current_layer):
            target = next(o for o in current_layer.objects
                          if o.object_id == obj.object_id)
            if key == "name":
                target.name = text
            elif key == "type":
                target.type_ = text
            elif key == "text":
                target.text = text
                if target.shape == "text":
                    target.properties.set("text", text)
            elif key == "image":
                target.image_source = text.strip()
            elif key == "pixelsize":
                _style(target, pixelsize=str(max(1, parsed)))
                if target.shape == "text":
                    target.properties.set("textSize", str(max(1, parsed)))
            elif key == "color":
                color = text.strip()
                _style(target, color=color)
                if target.shape == "text":
                    # 游戏只认 #RRGGBB，Tiled 的 #AARRGGBB 要去掉 alpha 段。
                    rw_color = ("#" + color[3:]
                                if color.startswith("#") and len(color) == 9
                                else color)
                    target.properties.set("textColor", rw_color)
            elif key == "align":
                _style(target, halign=parsed[0], valign=parsed[1])
            elif key == "wrap":
                _style(target, wrap=parsed)
            elif key == "points":
                target.points = parsed
            elif key == "gid":
                target.gid = max(0, parsed)
            elif key == "rotation":
                target.rotation = parsed
            elif values is not None:
                target.x, target.y, target.width, target.height = values
        self._commit(mutate, f"修改对象 {key}", layer)

    # ---------------- 增删 ----------------
    def _add_property(self):
        layer, obj = self._current_object()
        if obj is None:
            return
        name, ok = QInputDialog.getText(self, "新增属性", "属性名：")
        if not ok or not name.strip():
            return
        name = name.strip()
        value, ok = QInputDialog.getText(self, "新增属性", f"{name} 的值：")
        if not ok:
            return
        self._edit_value(layer, obj, name, value)

    def _del_property(self):
        meta = self._current_meta()
        if meta is None or meta[2] != _PROPERTY:
            return
        layer, obj, _kind, key = meta

        def mutate(current_layer):
            target = next(o for o in current_layer.objects if o.object_id == obj.object_id)
            target.properties.values.pop(key, None)
            target.properties.types.pop(key, None)
            target.properties.text_props.discard(key)
        self._commit(mutate, f"删除属性 {key}")

    def _object_layer(self):
        layer, _obj = self._current_object()
        if layer is not None:
            return layer
        return next((l for l in (self.doc.layers if self.doc else [])
                     if isinstance(l, ObjectLayer)), None)

    def _add_object(self):
        layer = self._object_layer()
        if layer is None or self.doc is None:
            return
        name, ok = QInputDialog.getText(self, "新建对象", "对象名称：", text="trigger")
        if not ok:
            return
        new_id = max((o.object_id for l in self.doc.layers
                      if isinstance(l, ObjectLayer)
                      for o in l.objects), default=0) + 1

        def mutate(current_layer):
            current_layer.objects.append(MapObject(
                object_id=new_id, name=name.strip() or f"object{new_id}",
                x=self.doc.width * self.doc.tile_w / 2,
                y=self.doc.height * self.doc.tile_h / 2,
                width=20, height=20,
            ))
        self._commit(mutate, "新建对象")

    def _del_object(self):
        layer, obj = self._current_object()
        if obj is None:
            return

        def mutate(current_layer):
            current_layer.objects = [o for o in current_layer.objects
                                     if o.object_id != obj.object_id]
        self._commit(mutate, f"删除对象 {obj.label}")
