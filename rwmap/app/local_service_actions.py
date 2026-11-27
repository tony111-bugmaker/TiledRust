"""GUI 线程侧的本地服务动作分发（mixin，由 MainWindow 继承）。

本模块所有方法**只在 GUI 线程执行**，由 LocalControlService 的
``request_received`` 信号（QueuedConnection）投递进来，执行完调用
``service.resolve(req_id, result)`` 把结果送回 HTTP 线程。

编辑类动作一律走 MainWindow 现有的命令路径（undo_stack.push），保证 Ctrl+Z 可撤销。
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, Qt
from PySide6.QtGui import QImage, QPainter


class LocalServiceActionsMixin:
    """给 MainWindow 加本地服务动作分发；不覆盖任何现有方法。"""

    # ---- 入口：由 LocalControlService 信号连接 ----
    def _on_local_request(self, req_id: int, action: str, payload: dict):
        service = self._local_service
        try:
            handler = getattr(self, "_svc_" + action, None)
            if handler is None:
                result = {"ok": False, "error": f"未知 action: {action}"}
            else:
                result = handler(payload or {})
                if not isinstance(result, dict):
                    result = {"ok": True, "data": result}
        except Exception as exc:
            import traceback
            traceback.print_exc()
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        service.resolve(req_id, result)

    # ---- 只读查询 ----
    def _svc_get_summary(self, p):
        doc = self.state.doc
        if doc is None:
            return {"ok": False, "error": "未打开地图"}
        return {"ok": True, "summary": doc.stats()}

    def _svc_get_state(self, p):
        doc = self.state.doc
        layer = self.state.current_layer
        return {"ok": True, "state": {
            "tool": self._object_tool if getattr(self, "_object_tool", None) else self.state.tool_id,
            "tile_tool": self.state.tool_id,
            "object_tool": getattr(self, "_object_tool", None),
            "brush_gid": self.state.brush_gid,
            "current_layer": layer.name if layer is not None else None,
            "doc_path": str(doc.path) if doc is not None else None,
            "dirty": bool(doc.dirty) if doc is not None else False,
            "zoom": getattr(self, "_zoom", None),
            "ruler_segments": getattr(self, "_ruler_segments", []),
        }}

    def _svc_get_layers(self, p):
        doc = self.state.doc
        if doc is None:
            return {"ok": False, "error": "未打开地图"}
        layers = [{
            "name": l.name, "kind": l.kind,
            "visible": l.visible, "locked": l.locked,
            "opacity": l.opacity, "readonly": getattr(l, "readonly", False),
            "width": getattr(l, "width", None), "height": getattr(l, "height", None),
            "object_count": len(l.objects) if l.kind == "object" else None,
        } for l in doc.layers]
        return {"ok": True, "layers": layers}

    def _find_object_layer(self, name):
        doc = self.state.doc
        if doc is None:
            return None, "未打开地图"
        if name:
            layer = doc.layer_by_name(str(name))
            if layer is None or layer.kind != "object":
                return None, f"对象层不存在: {name}"
            return layer, None
        # 默认取第一个对象层
        for l in doc.layers:
            if l.kind == "object":
                return l, None
        return None, "地图没有对象层"

    def _obj_to_dict(self, obj):
        return {
            "object_id": obj.object_id,
            "name": obj.name,
            "type": obj.type_,
            "shape": obj.shape,
            "x": obj.x, "y": obj.y,
            "width": obj.width, "height": obj.height,
            "rotation": obj.rotation,
            "visible": obj.visible,
            "properties": dict(obj.properties.values),
            "point_count": len(obj.points) if obj.points else 0,
        }

    def _svc_get_objects(self, p):
        layer, err = self._find_object_layer(p.get("layer"))
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "layer": layer.name,
                "objects": [self._obj_to_dict(o) for o in layer.objects]}

    def _svc_get_triggers(self, p):
        doc = self.state.doc
        if doc is None:
            return {"ok": False, "error": "未打开地图"}
        from ..document.map_document import RW_TRIGGER_TYPES
        out = []
        for layer in doc.layers:
            if layer.kind != "object":
                continue
            for obj in layer.objects:
                if obj.type_ in RW_TRIGGER_TYPES:
                    d = self._obj_to_dict(obj)
                    d["layer"] = layer.name
                    out.append(d)
        return {"ok": True, "triggers": out}

    def _svc_get_cells(self, p):
        doc = self.state.doc
        if doc is None:
            return {"ok": False, "error": "未打开地图"}
        name = p.get("layer")
        layer = doc.layer_by_name(str(name)) if name else next(
            (l for l in doc.tile_layers()), None)
        if layer is None or layer.kind != "tile":
            return {"ok": False, "error": f"瓦片层不存在: {name}"}
        x = max(0, int(p.get("x", 0)))
        y = max(0, int(p.get("y", 0)))
        w = int(p.get("w", layer.width - x))
        h = int(p.get("h", layer.height - y))
        w = min(w, layer.width - x)
        h = min(h, layer.height - y)
        if w <= 0 or h <= 0:
            return {"ok": False, "error": "区域为空"}
        rows = [[layer.get(c, r) for c in range(x, x + w)]
                for r in range(y, y + h)]
        return {"ok": True, "layer": layer.name, "x": x, "y": y,
                "w": w, "h": h, "cells": rows}

    def _svc_get_screen(self, p):
        pix = self.grab()
        img = pix.toImage()
        buf = QBuffer(QByteArray())
        buf.open(QBuffer.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        data = bytes(buf.data())
        return {"ok": True, "format": "png",
                "width": img.width(), "height": img.height(),
                "base64": base64.b64encode(data).decode("ascii")}

    # ---- 编辑动作（进撤销栈） ----
    def _svc_open_map(self, p):
        path = p.get("path")
        if not path:
            return {"ok": False, "error": "缺少 path"}
        fp = Path(path)
        if not fp.exists():
            return {"ok": False, "error": f"文件不存在: {path}"}
        # 同步打开（程序化入口），避免异步 token 时序让后续指令落空
        self._open(fp)
        return {"ok": True, "path": str(fp)}

    def _svc_save_map(self, p):
        if self.state.doc is None:
            return {"ok": False, "error": "未打开地图"}
        self._save()
        return {"ok": True, "path": str(self.state.doc.path)}

    def _svc_set_tool(self, p):
        tid = p.get("tool")
        if not tid:
            return {"ok": False, "error": "缺少 tool"}
        obj_tools = {"select", "ruler", "pen", "nodes", "rect", "point",
                     "ellipse", "polygon", "polyline", "tile", "template",
                     "image", "text"}
        if tid in obj_tools:
            self._set_object_tool(tid)
        else:
            self._set_tool(tid)
        return {"ok": True, "tool": tid}

    def _svc_set_gid(self, p):
        try:
            gid = int(p.get("gid"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "gid 必须是整数"}
        self._on_gid(gid)
        return {"ok": True, "gid": gid}

    def _svc_draw_cells(self, p):
        doc = self.state.doc
        if doc is None:
            return {"ok": False, "error": "未打开地图"}
        name = p.get("layer")
        layer = doc.layer_by_name(str(name)) if name else self.state.current_layer
        if layer is None or layer.kind != "tile":
            return {"ok": False, "error": f"瓦片层不存在: {name}"}
        cells = p.get("cells")
        if not isinstance(cells, list) or not cells:
            return {"ok": False, "error": "cells 必须是非空数组 [[col,row,gid],...]"}
        from ..commands.draw_command import DrawCommand
        changes = []
        for item in cells:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                return {"ok": False, "error": f"非法的 cell 项: {item!r}"}
            c, r, g = int(item[0]), int(item[1]), int(item[2])
            if not layer.in_bounds(c, r):
                return {"ok": False, "error": f"格子越界: {c},{r}"}
            old = layer.get(c, r)
            if old != g:
                changes.append((layer.index(c, r), g))
        if not changes:
            return {"ok": True, "changed": 0}
        # 先直接写入内存，再带 skip_first_redo push（避免命令首次 redo 重复应用）
        before = [(idx, layer.cells[idx]) for idx, _ in changes]
        for idx, g in changes:
            layer.cells[idx] = g
        layer.dirty = True
        doc.dirty = True
        cmd = DrawCommand(doc, layer, [(idx, old, new)
                                       for (idx, old), (_, new) in zip(before, changes)],
                          self._refresh_layer, "AI 绘制图块", skip_first_redo=True)
        self.state.undo_stack.push(cmd)
        self._refresh_layer(layer, [(idx % layer.width, idx // layer.width)
                                    for idx, _ in changes])
        return {"ok": True, "changed": len(changes)}

    def _svc_fill(self, p):
        doc = self.state.doc
        if doc is None:
            return {"ok": False, "error": "未打开地图"}
        name = p.get("layer")
        layer = doc.layer_by_name(str(name)) if name else self.state.current_layer
        if layer is None or layer.kind != "tile":
            return {"ok": False, "error": f"瓦片层不存在: {name}"}
        try:
            col, row = int(p.get("col")), int(p.get("row"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "col/row 必须是整数"}
        self._do_fill(layer, col, row)
        return {"ok": True, "col": col, "row": row}

    def _svc_add_object(self, p):
        from ..document.map_object import MapObject
        layer, err = self._find_object_layer(p.get("layer"))
        if err:
            return {"ok": False, "error": err}
        try:
            x, y = float(p.get("x", 0)), float(p.get("y", 0))
            w, h = float(p.get("width", 0)), float(p.get("height", 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "x/y/width/height 必须是数字"}
        obj = MapObject(
            object_id=self._new_object_id(),
            name=str(p.get("name", "")),
            type_=str(p.get("type", "")),
            x=x, y=y, width=w, height=h,
            shape=str(p.get("shape", "rectangle")),
        )
        for k, v in (p.get("properties") or {}).items():
            obj.properties.set(str(k), v)
        # 编排节点（波次生成器 / 官方波次表）只在节点图里出现，地图画布上必须
        # 隐藏。节点图渲染不读 obj.visible，所以设 False 不影响节点图连线和编辑。
        # 之前 API 建对象时漏了这一步，重开地图后它们会以「点」出现在画布左上角。
        from ..document.properties import (
            WAVE_GEN_TYPE, WAVE_TABLE_TYPE, WAVE_MARK_PROPERTY,
            WAVE_TABLE_MARK_PROPERTY,
        )
        _is_wave_node = (
            obj.type_ in (WAVE_GEN_TYPE, WAVE_TABLE_TYPE)
            or str(obj.properties.get(WAVE_MARK_PROPERTY) or "") == "1"
            or str(obj.properties.get(WAVE_TABLE_MARK_PROPERTY) or "") == "1"
        )
        if _is_wave_node and "visible" not in p:
            obj.visible = False
        self._commit_new_object(layer, obj, "AI 添加对象")
        return {"ok": True, "object_id": obj.object_id, "layer": layer.name}

    def _find_object_by_id(self, layer, oid):
        return next((o for o in layer.objects if o.object_id == int(oid)), None)

    def _apply_obj_changes(self, obj, changes: dict):
        """把 {属性:值} 应用到对象；返回未识别的键。"""
        simple = {"name", "type_", "x", "y", "width", "height",
                  "rotation", "shape", "visible"}
        unknown = []
        for k, v in changes.items():
            if k in simple:
                setattr(obj, k, v)
            else:
                obj.properties.set(str(k), v)
                unknown.append(k)
        return unknown

    def _svc_edit_object(self, p):
        layer, err = self._find_object_layer(p.get("layer"))
        if err:
            return {"ok": False, "error": err}
        if p.get("object_id") is None:
            return {"ok": False, "error": "缺少 object_id"}
        obj = self._find_object_by_id(layer, p["object_id"])
        if obj is None:
            return {"ok": False, "error": f"对象不存在: {p['object_id']}"}
        changes = p.get("changes")
        if not isinstance(changes, dict) or not changes:
            return {"ok": False, "error": "changes 必须是非空对象"}
        from ..commands.object_command import ObjectCommand, snapshot
        from ..rw import wave_gen as wg
        # 通过 API 改 map_info.type 也要和属性面板一样联动波次节点。
        # 多层快照保证撤销一步恢复模式 + 节点，不会只回滚其中一半。
        if str(obj.name or "").strip().lower() == "map_info" and "type" in changes:
            old_mode = str(obj.properties.get("type") or "").strip().lower()
            new_mode = str(changes["type"] or "").strip().lower()
            if old_mode != new_mode:
                from ..commands.object_command import capture, push_object_edit
                layers = [l for l in self.state.doc.layers if hasattr(l, "objects")]
                before_multi = capture(layers)
                self._apply_obj_changes(obj, changes)
                if new_mode == "survival" and old_mode != "survival":
                    wg.ensure_first_wave(self.state.doc)
                elif old_mode == "survival" and new_mode != "survival":
                    wg.remove_wave_nodes(self.state.doc)
                for l in layers:
                    l.dirty = True
                self.state.doc.dirty = True
                push_object_edit(self.state.doc, self.state.undo_stack,
                                 before_multi, "切换地图模式", self._refresh_objects)
                return {"ok": True, "object_id": obj.object_id,
                        "layer": layer.name, "changes": list(changes.keys()),
                        "mode_synced": True}
        before = snapshot(layer)
        self._apply_obj_changes(obj, changes)
        after = snapshot(layer)
        layer.dirty = True
        self.state.doc.dirty = True
        self.state.undo_stack.push(ObjectCommand(
            self.state.doc, layer, before, after, "AI 修改对象",
            self._refresh_objects))
        return {"ok": True, "object_id": obj.object_id,
                "layer": layer.name, "changes": list(changes.keys())}

    def _svc_delete_object(self, p):
        layer, err = self._find_object_layer(p.get("layer"))
        if err:
            return {"ok": False, "error": err}
        if p.get("object_id") is None:
            return {"ok": False, "error": "缺少 object_id"}
        obj = self._find_object_by_id(layer, p["object_id"])
        if obj is None:
            return {"ok": False, "error": f"对象不存在: {p['object_id']}"}
        from ..commands.object_command import ObjectCommand, snapshot
        before = snapshot(layer)
        layer.objects = [o for o in layer.objects if o is not obj]
        after = snapshot(layer)
        layer.dirty = True
        self.state.doc.dirty = True
        self.state.undo_stack.push(ObjectCommand(
            self.state.doc, layer, before, after, "AI 删除对象",
            self._refresh_objects))
        return {"ok": True, "object_id": int(p["object_id"])}

    def _svc_set_map_props(self, p):
        doc = self.state.doc
        if doc is None:
            return {"ok": False, "error": "未打开地图"}
        changes = p.get("changes")
        if not isinstance(changes, dict) or not changes:
            return {"ok": False, "error": "changes 必须是非空对象"}
        from ..commands.document_command import EditMapPropertiesCommand
        from ..document.properties import PropertyBag
        # 命令要求 before_bag 是 PropertyBag（读取 .types/.text_props），
        # 用当前 bag 的副本传入即可。
        before_bag = PropertyBag(
            values=dict(doc.properties.values),
            types=dict(doc.properties.types),
            text_props=set(doc.properties.text_props),
        )
        after_values = dict(changes)
        for k, v in changes.items():
            doc.properties.set(str(k), v)
            # 常见标量字段同步到文档属性
            if k in ("type", "version", "tiledversion", "orientation", "renderorder"):
                setattr(doc, k, v)
            elif k == "width":
                doc.width = int(v)
            elif k == "height":
                doc.height = int(v)
        doc.dirty = True
        self.state.undo_stack.push(EditMapPropertiesCommand(
            doc, before_bag, after_values, self._refresh_document,
            label="AI 修改地图属性"))
        return {"ok": True, "changes": list(changes.keys())}

    def _svc_autocrop(self, p):
        if self.state.doc is None:
            return {"ok": False, "error": "未打开地图"}
        self._autocrop()
        doc = self.state.doc
        return {"ok": True, "size": f"{doc.width}x{doc.height}"}

    def _svc_transform(self, p):
        if self.state.doc is None:
            return {"ok": False, "error": "未打开地图"}
        op = p.get("op")
        if op not in ("flip_h", "flip_v", "rot_cw", "rot_ccw"):
            return {"ok": False, "error": "op 必须是 flip_h/flip_v/rot_cw/rot_ccw"}
        self._transform_map(op)
        return {"ok": True, "op": op}

    def _svc_undo(self, p):
        if not self.state.undo_stack.canUndo():
            return {"ok": False, "error": "没有可撤销的操作"}
        self._undo()
        return {"ok": True}

    def _svc_redo(self, p):
        if not self.state.undo_stack.canRedo():
            return {"ok": False, "error": "没有可重做的操作"}
        self._redo()
        return {"ok": True}

    def _svc_goto(self, p):
        try:
            col, row = int(p.get("col", 0)), int(p.get("row", 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "col/row 必须是整数"}
        from PySide6.QtCore import QPointF
        self.view.centerOn(QPointF(col * self.state.doc.tile_w,
                                   row * self.state.doc.tile_h))
        return {"ok": True, "col": col, "row": row}

    def _svc_apply_ops(self, p):
        """批量执行指令；每条独立进撤销栈，返回逐条结果。"""
        ops = p.get("ops")
        if not isinstance(ops, list) or not ops:
            return {"ok": False, "error": "ops 必须是非空数组"}
        results = []
        for i, op in enumerate(ops):
            if not isinstance(op, dict) or "action" not in op:
                results.append({"index": i, "ok": False, "error": "缺少 action"})
                continue
            action = op["action"]
            payload = {k: v for k, v in op.items() if k != "action"}
            handler = getattr(self, "_svc_" + action, None)
            if handler is None:
                results.append({"index": i, "ok": False, "error": f"未知 action: {action}"})
                continue
            try:
                r = handler(payload)
                r["index"] = i
                r["action"] = action
                results.append(r)
            except Exception as exc:
                results.append({"index": i, "ok": False, "action": action,
                                "error": f"{type(exc).__name__}: {exc}"})
        return {"ok": True, "results": results}


__all__ = ["LocalServiceActionsMixin"]
