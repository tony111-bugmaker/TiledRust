"""蓝框选中样式：图块集面板选中框与地图上的图章/抓取虚影共用同一套颜色。

严格照搬 `tilesets_dock` 原本的字面值，避免两处各自维护导致颜色漂移。
"""
from __future__ import annotations

from PySide6.QtGui import QColor

SELECT = QColor(0x2F, 0x86, 0xDC)          # 47, 134, 220
SELECT_FILL = QColor(0x2F, 0x86, 0xDC, 85)
