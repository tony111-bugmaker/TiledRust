"""应用生命周期。支持 --no-sample（不自动打开示例）与 --probe（无 GUI 跑探针）。"""
from __future__ import annotations

import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from ..utils.log import get_logger


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--probe" in argv:
        from ..probe.probe_report import main as probe_main
        return probe_main()

    app = QApplication(argv)
    app.setApplicationName("rwmap")
    app.setOrganizationName("rwmap")

    logger = get_logger("rwmap.app")
    try:
        from .main_window import MainWindow
        window = MainWindow(auto_open="--no-sample" not in argv)
        window.show()
        return app.exec()
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc()
        logger.error("启动失败\n%s", detail)
        try:
            QMessageBox.critical(None, "启动失败", detail[-1200:])
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
