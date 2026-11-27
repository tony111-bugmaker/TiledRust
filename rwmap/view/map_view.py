"""QGraphicsView：缩放、平移、格坐标事件转发。

缩放锚点必须自己算：本类完全接管了 mouseMoveEvent（不调用基类），
Qt 内部的 lastMouseMoveViewPoint 永远停在 (0,0)，
导致 AnchorUnderMouse 把「鼠标下方」当成左上角，缩放时视图跳到左上角。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGraphicsView

from ..theme import INK
from ..utils.geometry import Camera


class MapView(QGraphicsView):
    cellPressed = Signal(int, int)
    cellMoved = Signal(int, int)
    cellReleased = Signal(int, int)
    hoverCell = Signal(int, int, int)     # col, row, raw_gid
    pickRequested = Signal(int, int)      # Alt+左键：临时吸管，不触发绘制
    objectPressed = Signal(float, float)  # Ctrl+左键：对象选择 / 拖动（场景坐标）
    objectMoved = Signal(float, float)
    objectReleased = Signal(float, float)
    # 对象工具：插入形状 / 逐点多边形 / 拖动节点（场景坐标）
    objectToolPressed = Signal(float, float)
    objectToolMoved = Signal(float, float)
    objectToolReleased = Signal(float, float)
    objectToolDoubleClicked = Signal(float, float)
    objectToolCancelled = Signal()
    objectHover = Signal(float, float)    # 选择对象工具下的悬停（场景坐标，用于缩放光标）
    zoomChanged = Signal(float)
    # 右键：拖动抓区域作图章 / 单击拾取单格（仅在瓦片画笔模式 object_mode=="brush"）
    rightPickRequested = Signal(int, int)             # 右键单击：拾取该格瓦片
    regionCaptureRequested = Signal(int, int, int, int)  # 右键拖动：抓取 (c0,r0,c1,r1)
    captureRectChanged = Signal(object)               # 抓取区域格坐标 (c0,r0,c1,r1) 或 None
    # 钢笔等对象工具的右键序列（场景浮点坐标）；仅 object_mode != "brush" 时发出。
    objectToolRightPressed = Signal(float, float)
    objectToolRightMoved = Signal(float, float)
    objectToolRightReleased = Signal(float, float)
    # 复制粘贴：对象吸附在鼠标上，左键放置 / 右键或 Esc 取消
    pasteMoveRequested = Signal(float, float)
    pastePlaceRequested = Signal(float, float, object)  # x, y, Qt.KeyboardModifiers
    pasteCancelRequested = Signal()

    def __init__(self, scene=None, parent=None):
        super().__init__(scene, parent)
        self.paste_mode = False   # 复制品跟随鼠标，等待左键放置
        self.camera = Camera()
        self._panning = False
        self._last_pos = None
        self._object_drag = False
        self._obj_tool_drag = False
        self.object_mode = "brush"   # "brush" = 瓦片编辑；其余为对象工具 id
        self._doc = None
        # 右键抓取区域状态：press 起点 / 当前格 / 是否真拖动过
        self._rcap = False
        self._rcap_start = None
        self._rcap_cur = None
        self._rcap_drag = False
        # 上次已发出的抓取矩形：同一格内连续 mouseMove 不再重复 emit，
        # 避免主窗口每帧重建 N² 的 2D 图章虚影。
        self._rcap_last_region = None
        # 对象工具（钢笔）右键序列：press 起点 / 是否发生拖动 / 是否进行中
        self._rpen_active = False
        self._rpen_start = None
        self._rpen_drag = False
        self._needs_fit = False
        self.setMouseTracking(True)
        # fitInView 四周留白用深色，避免刺眼的白边（Tiled 风格）。
        self.setBackgroundBrush(QColor(INK))
        # 去掉 QGraphicsView 原生的 1px 浅色边框：它是系统灰白框，
        # 落在双色主题里会变成一圈突兀的白边。
        self.setFrameShape(QGraphicsView.NoFrame)
        # 逐格画 QPixmap：关掉平滑/抗锯齿，缩放时更快也更接近像素风原图。
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        # Minimal 只重画显式 update(rect) 的区域；BoundingRect 会把
        # 覆盖全图的 GridOverlay.boundingRect 当成脏区，导致每次移动鼠标全图重绘。
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.DontSavePainterState, True)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.NoDrag)

    # ---------------- 适配视口 ----------------
    def set_document(self, doc):
        self._doc = doc
        if doc is None:
            self.resetTransform()
            self._needs_fit = False
            return
        self.camera.tile_w = doc.tile_w
        self.camera.tile_h = doc.tile_h
        self.resetTransform()
        self._needs_fit = True
        self._fit_if_ready()

    def _fit_if_ready(self):
        # 构造/布局尚未完成时 viewport 可能只有几十像素，fitInView 会得到极小缩放。
        if not self._needs_fit or self._doc is None:
            return
        if self.viewport().width() < 120 or self.viewport().height() < 120:
            QTimer.singleShot(0, self._fit_if_ready)
            return
        doc = self._doc
        # 只按地图矩形 fit，不把 sceneRect 的虚拟边距算进去。
        from PySide6.QtCore import QRectF
        self.fitInView(QRectF(0, 0, doc.width * doc.tile_w,
                              doc.height * doc.tile_h), Qt.KeepAspectRatio)
        self._needs_fit = False
        self.camera.scale = self.transform().m11()
        self.zoomChanged.emit(self.camera.scale)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._needs_fit:
            QTimer.singleShot(0, self._fit_if_ready)

    # ---------------- 缩放 ----------------
    def zoom_at(self, pos, delta: int):
        """以 pos（视口坐标）为锚点缩放，保持该点下的场景坐标不动。"""
        if not delta or self._doc is None:
            return
        from PySide6.QtCore import QPoint
        if not isinstance(pos, QPoint):
            pos = QPoint(int(pos.x()), int(pos.y()))
        before = self.mapToScene(pos)
        current = self.transform().m11()
        new_scale = Camera.clamp_scale(self.camera, current * (1.15 if delta > 0 else 1 / 1.15))
        factor = new_scale / current
        if abs(factor - 1.0) < 1e-9:
            return
        self.scale(factor, factor)
        after = self.mapToScene(pos)
        offset = after - before
        self.translate(offset.x(), offset.y())
        self.camera.scale = self.transform().m11()
        self.zoomChanged.emit(self.camera.scale)

    # ---------------- 菜单用的缩放动作 ----------------
    # 滚轮缩放走 zoom_at（以光标为锚点）；菜单动作没有光标位置，
    # 统一以视口中心为锚点，保证「放大/缩小」不会把画面甩到左上角。
    def _viewport_center(self) -> QPoint:
        vp = self.viewport()
        return QPoint(vp.width() // 2, vp.height() // 2)

    def zoom_in(self):
        """放大一档（1.15×），以视口中心为锚点。"""
        self.zoom_at(self._viewport_center(), 1)

    def zoom_out(self):
        """缩小一档（1/1.15×），以视口中心为锚点。"""
        self.zoom_at(self._viewport_center(), -1)

    def zoom_reset(self):
        """正常大小：回到 1:1（100%），并把地图左上角对齐到视口中心附近。"""
        if self._doc is None:
            return
        self.resetTransform()
        self._needs_fit = False
        self.camera.scale = 1.0
        # 让地图仍落在可见区域内，而不是贴在场景坐标 (0,0) 的虚拟留白里。
        w = self._doc.width * self._doc.tile_w
        h = self._doc.height * self._doc.tile_h
        self.centerOn(w / 2, h / 2)
        self.zoomChanged.emit(1.0)

    def zoom_fit(self):
        """自动适应：按地图矩形 fit 到视口（复用 set_document 的同一套逻辑）。"""
        if self._doc is None:
            return
        self._needs_fit = True
        self._fit_if_ready()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta:
            return
        self.zoom_at(event.position().toPoint(), delta)
        event.accept()

    # ---------------- 格坐标 ----------------
    def _cell(self, pos):
        if self._doc is None:
            return -1, -1
        scene_pos = self.mapToScene(pos)
        return int(scene_pos.x() // self._doc.tile_w), int(scene_pos.y() // self._doc.tile_h)

    def _in_bounds(self, col: int, row: int) -> bool:
        return self._doc is not None and 0 <= col < self._doc.width and 0 <= row < self._doc.height

    def _scene_xy(self, pos):
        point = self.mapToScene(pos)
        return point.x(), point.y()

    # ---------------- 输入 ----------------
    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if self.paste_mode:
            x, y = self._scene_xy(pos)
            if event.button() == Qt.LeftButton:
                self.pastePlaceRequested.emit(x, y, event.modifiers())
                event.accept()
                return
            if event.button() == Qt.RightButton:
                self.pasteCancelRequested.emit()
                event.accept()
                return
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._last_pos = pos
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            if event.modifiers() & Qt.AltModifier:
                # 临时吸管：按住 Alt 点击只取色，不改变当前工具、不开始笔划。
                col, row = self._cell(pos)
                if self._in_bounds(col, row):
                    self.pickRequested.emit(col, row)
                event.accept()
                return
            if (self.object_mode == "brush"
                    and event.modifiers() & Qt.ControlModifier
                    and self._doc is not None):
                # 瓦片画笔模式下 Ctrl+左键仍是「临时点选 / 拖动对象」的快捷入口。
                # 其它对象工具（rect/select/…）下 Ctrl 是修饰键（吸附 / 按格），
                # 必须透传给 objectToolPressed，否则 Ctrl+创建对象会被这里吞掉。
                scene_pos = self.mapToScene(pos)
                if self._in_bounds(int(scene_pos.x() // self._doc.tile_w),
                                   int(scene_pos.y() // self._doc.tile_h)):
                    self._object_drag = True
                    self.objectPressed.emit(scene_pos.x(), scene_pos.y())
                event.accept()
                return
            if self.object_mode != "brush":
                # 对象工具接管左键：拖动创建 / 逐点绘制 / 拖动顶点。
                x, y = self._scene_xy(pos)
                self._obj_tool_drag = True
                self.objectToolPressed.emit(x, y)
                event.accept()
                return
            col, row = self._cell(pos)
            if self._in_bounds(col, row):
                self.cellPressed.emit(col, row)
            event.accept()
            return
        if event.button() == Qt.RightButton:
            # 瓦片画笔模式下：右键拖动抓区域作图章 / 右键单击拾取单格。
            if self.object_mode == "brush":
                col, row = self._cell(pos)
                if self._in_bounds(col, row):
                    self._rcap = True
                    self._rcap_start = (col, row)
                    self._rcap_cur = (col, row)
                    self._rcap_drag = False
                    self._rcap_last_region = None
                    event.accept()
                    return
            elif self._doc is not None:
                # 对象工具（钢笔）右键：按下即放点，拖动则同时拉出控制柄。
                # brush 分支与这里互斥，不会与 _rcap 抓图章冲突。
                xy = self._scene_xy(pos)
                self._rpen_active = True
                self._rpen_start = xy
                self._rpen_drag = False
                self.objectToolRightPressed.emit(*xy)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self.paste_mode:
            self.pasteMoveRequested.emit(*self._scene_xy(pos))
            event.accept()
            return
        if self._panning and self._last_pos is not None:
            delta = pos - self._last_pos
            self._last_pos = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._obj_tool_drag and event.buttons() & Qt.LeftButton:
            self.objectToolMoved.emit(*self._scene_xy(pos))
            event.accept()
            return
        if self._rpen_active and event.buttons() & Qt.RightButton:
            xy = self._scene_xy(pos)
            scale = abs(self.transform().m11()) or 1.0
            thresh = 4.0 / max(scale, 0.02)     # 4 屏幕像素 → 场景单位
            sx, sy = self._rpen_start
            if ((xy[0] - sx) ** 2 + (xy[1] - sy) ** 2) ** 0.5 > thresh:
                self._rpen_drag = True
            self.objectToolRightMoved.emit(*xy)
            event.accept()
            return
        if self._object_drag and event.buttons() & Qt.LeftButton:
            scene_pos = self.mapToScene(pos)
            self.objectMoved.emit(scene_pos.x(), scene_pos.y())
            event.accept()
            return
        if self._rcap and event.buttons() & Qt.RightButton:
            col, row = self._cell(pos)
            self._rcap_cur = (col, row)
            if self._rcap_cur != self._rcap_start:
                self._rcap_drag = True
            c0, r0 = self._rcap_start
            c1, r1 = self._rcap_cur
            region = (min(c0, c1), min(r0, r1), max(c0, c1), max(r0, r1))
            # 同一格内连续 mouseMove 会反复触发，但矩形没变：主窗口没必要
            # 每帧重读整块层、重建 N² 的 2D 虚影。
            if region != self._rcap_last_region:
                self._rcap_last_region = region
                # 发格坐标 (c0,r0,c1,r1)，由 main_window 读取当前层实时格生成
                # 抓取虚影，不再画蓝色选框（MinimalViewportUpdate 下会留拖影）。
                self.captureRectChanged.emit(region)
            event.accept()
            return
        if self.object_mode == "select":
            # 只在「选择对象」工具且没有拖动时发悬停：主窗口据此把光标换成
            # 对应方向的缩放箭头，其它工具完全不受影响。
            self.objectHover.emit(*self._scene_xy(pos))
        col, row = self._cell(pos)
        if self._doc is not None:
            raw = 0
            inside = self._in_bounds(col, row)
            if inside:
                scene = self.scene()
                state_layer = getattr(scene, "current_layer", None)
                if state_layer is not None and state_layer.in_bounds(col, row):
                    raw = state_layer.get(col, row)
            # 悬停 / 图章虚影必须能跟到地图外：预览层本身支持红色越界格，
            # 只在地图内发 hoverCell 会让右键抓出来的图章停在边界上，
            # 没法「整块移到地图外」。真正写格仍由下面的 cellMoved 限制在界内。
            self.hoverCell.emit(col, row, raw)
            if (inside and event.buttons() & Qt.LeftButton
                    and not (event.modifiers() & Qt.AltModifier)
                    and self.object_mode == "brush"):
                self.cellMoved.emit(col, row)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        # 右键序列必须排在 _obj_tool_drag 之前：后者原本不判断按键类型，
        # 会把右键松开吞成「对象工具左键松开」，钢笔就永远放不下锚点。
        if event.button() == Qt.RightButton and self._rpen_active:
            self._rpen_active = False
            xy = self._scene_xy(event.position().toPoint())
            if self._rpen_drag:
                # 补发末次坐标：保证主窗口算出的柄向量正好是「松开位置 - 按下位置」。
                self.objectToolRightMoved.emit(*xy)
            self.objectToolRightReleased.emit(*xy)
            self._rpen_start = None
            self._rpen_drag = False
            event.accept()
            return
        if self._obj_tool_drag and event.button() == Qt.LeftButton:
            self._obj_tool_drag = False
            self.objectToolReleased.emit(*self._scene_xy(event.position().toPoint()))
            event.accept()
            return
        if self._object_drag and event.button() == Qt.LeftButton:
            self._object_drag = False
            scene_pos = self.mapToScene(event.position().toPoint())
            self.objectReleased.emit(scene_pos.x(), scene_pos.y())
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            col, row = self._cell(event.position().toPoint())
            self.cellReleased.emit(col, row)
            event.accept()
            return
        if event.button() == Qt.RightButton and self._rcap:
            c0, r0 = self._rcap_start
            c1, r1 = self._rcap_cur
            if self._rcap_drag:
                self.regionCaptureRequested.emit(
                    min(c0, c1), min(r0, r1), max(c0, c1), max(r0, r1))
            else:
                self.rightPickRequested.emit(c0, r0)
            self._rcap = False
            self._rcap_last_region = None
            self.captureRectChanged.emit(None)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.object_mode != "brush" and event.button() == Qt.LeftButton:
            self._obj_tool_drag = False
            self.objectToolDoubleClicked.emit(*self._scene_xy(event.position().toPoint()))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.paste_mode:
            self.pasteCancelRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape and self.object_mode != "brush":
            self._obj_tool_drag = False
            self.objectToolCancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)
