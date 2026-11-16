"""可视化验收：单位面板已删除，主窗口布局正常。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_no_units_panel_visual
输出： logs/删除单位面板_主窗口.png
探针脚本，验收后按项目约定清理。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path("logs") / "删除单位面板_主窗口.png"


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    fams = QFontDatabase.applicationFontFamilies(fid)
    if fams:
        app.setFont(QFont(fams[0], 9))

    from rwmap.app.main_window import MainWindow
    from rwmap.app.panel_layout import PANEL_SPECS

    w = MainWindow(auto_open=False)
    w._open(Path(w.state.profile.sample_map))
    w.resize(1180, 780)
    w.show()
    app.processEvents()
    w.grab().save(str(OUT))
    print("saved", OUT)
    print("dock count:", len(PANEL_SPECS))
    print("titles:", [s.title for s in PANEL_SPECS])
    print("has units_dock:", hasattr(w, "units_dock"))
    w.close()


if __name__ == "__main__":
    main()
