"""Block flash "empty windows": untitled TOP-LEVEL QWidget OR QWindow.

Qt 6 emits Show for every child widget too, so we MUST keep the top-level
check. A real flash window is a top-level (isWindow) with no title and a
normal Window type (not Popup/Tool). Menus, combo popups, dialogs, the main
window and all child widgets are left alone.

RW_WINDOW_GUARD=0 disables it.
"""
from __future__ import annotations

import os
import traceback

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QWidget

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG = os.path.join(_ROOT, "logs", "empty_window_guard.log")


class EmptyWindowGuard(QObject):
    def __init__(self):
        super().__init__()
        self.hits = 0
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("=== empty-window guard armed (top-level only) ===\n")

    def _record(self, obj, kind):
        self.hits += 1
        if self.hits <= 30:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write("\n=== BLOCK #%d kind=%s %s ===\n"
                        % (self.hits, kind, type(obj).__name__))
                f.write("".join(traceback.format_stack()[-18:]))

    def eventFilter(self, obj, event):
        try:
            if event.type() != QEvent.Type.Show:
                return False
            is_qwidget = isinstance(obj, QWidget)
            is_qwindow = not is_qwidget and type(obj).__name__ == "QWindow"
            if not (is_qwidget or is_qwindow):
                return False

            # —— 必须是顶层窗口 ——
            if is_qwidget:
                if not obj.isWindow():
                    return False
                flags = obj.windowFlags() & Qt.WindowType_Mask
                # 弹层/工具窗/Popup 都不拦
                if flags in (Qt.WindowType.Popup, Qt.WindowType.Tool):
                    return False
                title = obj.windowTitle()
            else:
                # QWindow：看它是否顶层（无 parent）以及 type
                try:
                    flags = obj.flags() & Qt.WindowType_Mask
                except Exception:
                    return False
                if flags in (Qt.WindowType.Popup, Qt.WindowType.Tool):
                    return False
                title = obj.title()

            if title and title.strip():
                return False

            self._record(obj, "widget" if is_qwidget else "qwindow")
            obj.hide()
        except Exception as exc:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write("GUARD ERROR %r\n" % (exc,))
        return False
