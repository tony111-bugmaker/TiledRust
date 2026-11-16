"""全局状态：当前文档、profile、层、工具、笔刷、撤销栈与笔划聚合。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QUndoStack

from ..commands.draw_command import DrawCommand
from ..document.layer import TileLayer
from ..io.tmx_reader import read_document
from ..rw.version import active_profile, load_profile, set_active_profile
from ..tools import get_tool
from ..utils.paths import as_path


class AppState:
    def __init__(self):
        self.profile = active_profile()
        self.doc = None
        self.undo_stack = QUndoStack()
        self.current_layer: TileLayer | None = None
        self.brush_gid: int = 0
        self.tool_id: str = "brush"
        self.stamp: list[list[int]] | None = None   # 图块集矩形选区（2D gid）
        self._stroke: dict | None = None
        # 多文档标签支持两类文档：地图 ("map") 与图集 ("tileset")。
        # 图集不画画，只维护每个瓦片的属性。
        self.kind: str = "map"
        self.tileset_doc = None                      # TilesetDocument | None

    @property
    def tool(self):
        return get_tool(self.tool_id)

    def open(self, path) -> None:
        self.doc = read_document(as_path(path), self.profile)
        self.undo_stack.clear()
        self.current_layer = next(
            (layer for layer in self.doc.layers
             if isinstance(layer, TileLayer) and not layer.readonly),
            None,
        )
        if self.current_layer is None:
            self.current_layer = next(
                (layer for layer in self.doc.layers if isinstance(layer, TileLayer)), None
            )
        self.brush_gid = 0
        self.stamp = None
        self._stroke = None

    def set_profile(self, version_id: str) -> None:
        set_active_profile(version_id)
        self.profile = load_profile(version_id)
        if self.doc is not None:
            path = self.doc.path
            self.open(path)

    def open_document(self, doc) -> None:
        """直接装入一个已构造好的文档（如新建的空白地图），不经磁盘读取。"""
        self.doc = doc
        self.undo_stack.clear()
        self.current_layer = next(
            (layer for layer in doc.layers
             if isinstance(layer, TileLayer) and not layer.readonly),
            None,
        )
        if self.current_layer is None:
            self.current_layer = next(
                (layer for layer in doc.layers if isinstance(layer, TileLayer)), None)
        self.brush_gid = 0
        self.stamp = None
        self._stroke = None

    def can_edit(self) -> bool:
        return (
            self.doc is not None
            and self.current_layer is not None
            and not self.current_layer.readonly
            and not getattr(self.current_layer, "locked", False)
            and self.profile.write_enabled
        )

    # ---- 笔划：一次按下→拖拽→松开聚合为一条 undo 命令 ----
    def begin_stroke(self, layer: TileLayer, col: int, row: int, raw_gid: int,
                     refresh_cb, stamp: list[list[int]] | None = None,
                     on_change=None):
        """on_change(bounds) 在每次真正写入格子后触发，用于绘制时自动地图。"""
        self._stroke = {
            "layer": layer,
            "changes": {},
            "refresh": refresh_cb,
            "gid": int(raw_gid),
            "stamp": stamp,
            "bounds": [col, row, col, row],
            "changed_bounds": None,
            "on_change": on_change,
            "pending_refresh": [],
        }
        self.stroke_to(col, row, raw_gid, stamp)

    def _write_cell(self, stroke: dict, col: int, row: int, new: int) -> None:
        layer: TileLayer = stroke["layer"]
        if not layer.in_bounds(col, row):
            return
        index = layer.index(col, row)
        if index in stroke["changes"]:
            return
        new = int(new) & 0xFFFFFFFF
        old = layer.cells[index]
        if old == new:
            return
        stroke["changes"][index] = (old, new)
        stroke["last_gid"] = new
        layer.cells[index] = new
        layer.dirty = True
        self.doc.dirty = True
        b = stroke["bounds"]
        b[0], b[1] = min(b[0], col), min(b[1], row)
        b[2], b[3] = max(b[2], col), max(b[3], row)
        cb = stroke["changed_bounds"]
        stroke["changed_bounds"] = (
            col, row, col, row) if cb is None else (
            min(cb[0], col), min(cb[1], row), max(cb[2], col), max(cb[3], row))
        # 大图章一次要写几百 / 几千格。逐格 refresh 会重复创建 QPainter、
        # 重复标脏、重复刷新标题 N 次，拖动时明显掉帧。这里只累积坐标，
        # 由 stroke_to（每帧鼠标移动）/ end_stroke（一次性区域笔划）批量刷新一次。
        # 兼容外部手工构造、没有该字段的旧 stroke 字典：退回原来的即时刷新。
        pending = stroke.get("pending_refresh")
        if pending is None:
            stroke["refresh"](layer, [(col, row)])
        else:
            pending.append((col, row))

    @staticmethod
    def _flush_refresh(stroke: dict) -> None:
        """把累积的改动格一次性交给刷新回调。"""
        pending = stroke.get("pending_refresh")
        if not pending:
            return
        cells = pending[:]          # 回调里可能再触发写入，先拷贝再清空
        pending.clear()
        stroke["refresh"](stroke["layer"], cells)

    def stroke_to(self, col: int, row: int, raw_gid: int | None = None,
                  stamp: list[list[int]] | None = None) -> None:
        stroke = self._stroke
        if stroke is None or self.doc is None:
            return
        stamp = stamp if stamp is not None else stroke.get("stamp")
        gid = stroke["gid"] if raw_gid is None else int(raw_gid)
        stroke["changed_bounds"] = None
        if stamp:
            for dy, line in enumerate(stamp):
                for dx, value in enumerate(line):
                    self._write_cell(stroke, col + dx, row + dy, value)
        else:
            self._write_cell(stroke, col, row, gid)
        bounds = stroke["changed_bounds"]
        self._flush_refresh(stroke)   # 先刷新画面，再给自动映射看新格
        callback = stroke.get("on_change")
        if bounds and callback is not None:
            callback(bounds)

    def stroke_continue(self, col: int, row: int) -> None:
        if self._stroke is not None:
            self.stroke_to(col, row)

    def end_stroke(self) -> dict | None:
        """结束笔划并入撤销栈；返回本次笔划范围，供 automapping 使用。"""
        stroke = self._stroke
        self._stroke = None
        if not stroke:
            return None
        self._flush_refresh(stroke)   # 填充 / 框选粘贴等一次性区域笔划在这里收口
        if not stroke["changes"] or self.doc is None:
            return None
        changes = [(index, old, new) for index, (old, new) in stroke["changes"].items()]
        command = DrawCommand(
            self.doc, stroke["layer"], changes, stroke["refresh"],
            text=f"{stroke.get('text', '绘制')} {len(changes)} 格",
            skip_first_redo=True,   # 数据已写、画面已刷新，跳过 push 的首次 redo
        )
        self.undo_stack.push(command)
        return {"layer": stroke["layer"], "bounds": tuple(stroke["bounds"])}

    # ---- 区域笔划：一次性改写多格（填充 / 框选粘贴等） ----
    def begin_region_stroke(self, layer: TileLayer, refresh_cb, on_change=None,
                            text: str = "填充"):
        """开启一个区域笔划：不预先写任何格，由调用方逐个 ``write_region_cell``。

        ``bounds`` 初始化为反向极值，``_write_cell`` 会在写入时收敛到真实范围。
        """
        self._stroke = {
            "layer": layer,
            "changes": {},
            "refresh": refresh_cb,
            "gid": 0,
            "stamp": None,
            "bounds": [10**9, 10**9, -10**9, -10**9],
            "changed_bounds": None,
            "on_change": on_change,
            "text": text,
            "pending_refresh": [],
        }

    def write_region_cell(self, col: int, row: int, new: int) -> None:
        if self._stroke is None:
            return
        self._write_cell(self._stroke, col, row, new)
