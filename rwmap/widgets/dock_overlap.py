"""重合（标签合并）面板的拖出处理。

Qt 自带 QMainWindow 的 tabify 合并：多个 QDockWidget 共用一个标签栏，
视觉上就是「面板重合」。但本项目所有面板都换了 DockTitleBar，原生 Dock
标题栏的拖拽入口被接管，实际使用时标签拖出经常不生效，或者浮出的窗口
落在默认位置而不是鼠标处。

这里给主窗口挂一个应用级事件过滤器：

- 只在「主窗口里、标签文字与某个 QDockWidget 标题一致」的 QTabBar 上生效，
  不碰文档标签栏、图集标签栏；
- 左键按住某个面板标签，拖出该标签栏范围即解除重合（setFloating(True)）；
- 浮出面板的左上角直接对齐当前鼠标位置，并且在左键松开前持续跟随鼠标，
  不需要松开再拖第二次；
- 不对标签数量做假设，两个、三个或更多面板重合都走同一条逻辑。

新增面板不需要额外登记：只要它是主窗口里的 QDockWidget、参与 tabify，
就会自动被识别。
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QDockWidget, QTabBar


def _global_pos(event):
    """兼容 Qt6 的 globalPosition()/旧版 globalPos()。"""
    getter = getattr(event, "globalPosition", None)
    if getter is not None:
        try:
            point = getter()
            return QPoint(int(point.x()), int(point.y()))
        except Exception:  # noqa: BLE001
            pass
    try:
        return QPoint(event.globalPos())
    except Exception:  # noqa: BLE001
        return None


class DockTabDetachFilter(QObject):
    """识别面板标签的拖出手势并浮出对应面板。"""

    def __init__(self):
        super().__init__()
        self._windows: list[weakref.ReferenceType] = []
        self._installed = False
        # (bar, index, window, mode, dock)；mode: pending / floating
        self._press = None
        self._dragging_dock: QDockWidget | None = None
        self._cursor_origin: QPoint | None = None
        self._cursor_tracking = False
        # 鼠标移出主窗口后 MouseMove 会送给别的进程，这里轮询兜底。
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(16)
        self._move_timer.timeout.connect(self._follow_cursor)

    def register(self, window) -> None:
        """登记一个主窗口；重复调用无副作用。"""
        ref = weakref.ref(window)
        if not any(old() is window for old in self._windows):
            self._windows.append(ref)
        app = QApplication.instance()
        if app is not None and not self._installed:
            app.installEventFilter(self)
            self._installed = True

    # ---- 归属判断 -----------------------------------------------------
    def _live_window(self, owner):
        for ref in tuple(self._windows):
            window = ref()
            if window is None:
                continue
            node = owner
            while node is not None:
                if node is window:
                    return window
                node = node.parent()
        return None

    @staticmethod
    def _dock_for_tab(window, bar: QTabBar, index: int):
        label = bar.tabText(index).replace("&", "").strip()
        if not label:
            return None
        for dock in window.findChildren(QDockWidget):
            if dock.windowTitle().replace("&", "").strip() == label:
                return dock
        return None

    # ---- 事件入口 -----------------------------------------------------
    def eventFilter(self, obj, event):
        if self._press is None:
            # 按下阶段只认面板标签栏，避免碰文档标签 / 图集标签。
            if not isinstance(obj, QTabBar):
                return super().eventFilter(obj, event)
            event_type = event.type()
            if event_type != QEvent.Type.MouseButtonPress:
                return super().eventFilter(obj, event)
            if event.button() != Qt.LeftButton:
                return super().eventFilter(obj, event)
            local = getattr(event, "position", None)
            if local is not None:
                try:
                    local_pos = QPoint(int(local().x()), int(local().y()))
                except Exception:  # noqa: BLE001
                    local_pos = event.pos()
            else:
                local_pos = event.pos()
            index = obj.tabAt(local_pos)
            if index < 0:
                return super().eventFilter(obj, event)
            window = self._live_window(obj)
            if window is None:
                return super().eventFilter(obj, event)
            if self._dock_for_tab(window, obj, index) is None:
                # 文档标签 / 图集标签：不是面板重合，交给原控件处理
                return super().eventFilter(obj, event)
            # 接管按下：自己切换标签并吃掉事件，杜绝 Qt 原生 Dock 标签拖拽
            # 启动。后面由我们自己把面板跟着鼠标移动。
            obj.setCurrentIndex(index)
            self._press = (obj, index, window, "pending", None)
            event.accept()
            return True

        # 按住标签后鼠标可能移出 QTabBar / 主窗口，事件目标随之变化，
        # 所以这里不再限定 obj 类型，任何控件的鼠标事件都参与手势判断。
        bar, index, window, mode, dock = self._press
        event_type = event.type()
        if event_type == QEvent.Type.MouseMove:
            if not (event.buttons() & Qt.LeftButton):
                self._end_drag()
                return super().eventFilter(obj, event)
            current = _global_pos(event) or QCursor.pos()

            if mode == "floating" and dock is not None:
                # 已浮出：保持鼠标按住状态继续移动，左上角始终跟随指针。
                self._move_dock(dock, current)
                self._check_cursor_tracking()
                event.accept()
                return True

            origin = bar.mapToGlobal(QPoint(0, 0))
            bar_rect = QRect(origin, bar.size())
            # 只在真正拖出标签栏范围后解散；栏内移动留给 Qt 做标签切换。
            if bar_rect.contains(current):
                return super().eventFilter(obj, event)
            dock = self._dock_for_tab(window, bar, index)
            if dock is None:
                self._end_drag()
                return super().eventFilter(obj, event)
            self._detach(dock, current)
            self._press = (bar, index, window, "floating", dock)
            self._dragging_dock = dock
            self._cursor_origin = QCursor.pos()
            self._cursor_tracking = False
            self._move_timer.start()
            event.accept()
            return True

        if event_type in (QEvent.Type.MouseButtonRelease,
                          QEvent.Type.MouseButtonDblClick):
            self._end_drag()
        return super().eventFilter(obj, event)

    # ---- 拖动辅助 -----------------------------------------------------
    def _end_drag(self) -> None:
        self._press = None
        self._dragging_dock = None
        self._cursor_origin = None
        self._cursor_tracking = False
        if self._move_timer.isActive():
            self._move_timer.stop()

    def _check_cursor_tracking(self) -> None:
        """真实 Windows 下 QCursor.pos() 会变；离屏测试里它是常量，不能用。"""
        if (not self._cursor_tracking and self._cursor_origin is not None
                and QCursor.pos() != self._cursor_origin):
            self._cursor_tracking = True

    @staticmethod
    def _move_dock(dock: QDockWidget, pos: QPoint) -> None:
        """让浮出面板左上角跟随鼠标，不改变尺寸。"""
        if dock is not None and dock.isFloating():
            dock.move(pos.x(), pos.y())

    def _follow_cursor(self) -> None:
        """鼠标移出程序时收不到 MouseMove，用定时器按全局坐标兜底。"""
        buttons = QApplication.mouseButtons()
        if buttons == Qt.NoButton or not (buttons & Qt.LeftButton):
            self._end_drag()
            return
        dock = self._dragging_dock
        if dock is None or not dock.isFloating():
            self._end_drag()
            return
        # 某些平台（尤其 offscreen）QCursor.pos() 是常量，定时器不要用它
        # 覆盖 MouseMove 事件给出的真实坐标；真实 Windows 下会自行启用。
        if self._cursor_tracking:
            self._move_dock(dock, QCursor.pos())

    @staticmethod
    def _detach(dock: QDockWidget, pos: QPoint) -> None:
        """解除重合，并把浮出面板左上角放到鼠标处。"""
        width = max(dock.width(), dock.minimumWidth())
        height = max(dock.height(), dock.minimumHeight())
        was_visible = not dock.isHidden()
        if not dock.isFloating():
            dock.setFloating(True)
        if was_visible:
            dock.show()
        # 先固定尺寸再定位，避免 setFloating 后首帧布局未收敛导致跳动。
        dock.resize(width, height)
        dock.setGeometry(QRect(pos.x(), pos.y(), width, height))
        dock.raise_()


_FILTER: DockTabDetachFilter | None = None


def install_dock_tab_detach(window) -> DockTabDetachFilter:
    """给主窗口安装面板标签拖出处理器；重复调用无副作用。"""
    global _FILTER
    if _FILTER is None:
        _FILTER = DockTabDetachFilter()
    _FILTER.register(window)
    return _FILTER


def overlap_panels(window, docks) -> int:
    """把多个面板合并成一组重合标签；返回实际合并的面板数。

    不限制数量：第一个作为基准，其余逐个 tabify 到同一组。用于测试与外部
    脚本；界面上仍由用户拖动标签完成重合。
    """
    items = list(docks)
    if len(items) < 2:
        return len(items)
    first = items[0]
    for dock in items[1:]:
        window.tabifyDockWidget(first, dock)
    return len(items)
