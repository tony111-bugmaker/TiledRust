"""可视化验收：Tiled 风格「新建地图」对话框（宽/高 + 图集勾选）。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_new_map_dialog_visual
输出： logs/新建地图_对话框.png
探针脚本，验收后按项目约定清理。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path("logs") / "新建地图_对话框.png"


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    fams = QFontDatabase.applicationFontFamilies(fid)
    if fams:
        app.setFont(QFont(fams[0], 9))

    from rwmap.rw.version import active_profile
    from rwmap.widgets.new_map_dialog import NewMapDialog

    dlg = NewMapDialog(active_profile())
    dlg.show()
    QApplication.processEvents()
    dlg.grab().save(str(OUT))
    print("saved", OUT)
    print("values:", dlg.values())
    dlg.close()


if __name__ == "__main__":
    main()
