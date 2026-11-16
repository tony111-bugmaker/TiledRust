"""可视化验收：Tiled 风格「调整地图尺寸」对话框 + 可拖动预览 + 溢出红框。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_resize_dialog_visual
输出： logs/调整地图尺寸_对话框.png、logs/调整地图尺寸_溢出红框.png
探针脚本，验收后按项目约定清理。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT_DIR = Path("logs")
SHOT1 = OUT_DIR / "调整地图尺寸_对话框.png"
SHOT2 = OUT_DIR / "调整地图尺寸_溢出红框.png"


def _events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    fams = QFontDatabase.applicationFontFamilies(fid)
    if fams:
        app.setFont(QFont(fams[0], 9))

    from rwmap.io.tmx_writer import create_blank_document
    from rwmap.document.layer import TileLayer, ObjectLayer
    from rwmap.document.map_object import MapObject
    from rwmap.rw.version import active_profile
    from rwmap.widgets.resize_map_dialog import ResizeMapDialog

    doc = create_blank_document(active_profile(), 40, 24, 20, 20)
    layer = TileLayer(width=40, height=24, name="Ground", cells=[0] * (40 * 24))
    # 左上一大块已绘制内容 + 右下一块，方便看出平移与溢出
    for r in range(2, 18):
        for c in range(2, 30):
            layer.cells[r * 40 + c] = 1 + ((r + c) % 5)
    for r in range(19, 23):
        for c in range(30, 39):
            layer.cells[r * 40 + c] = 3
    objs = [MapObject(object_id=1, shape="rect", x=60, y=80, width=120, height=60),
            MapObject(object_id=2, shape="rect", x=460, y=260, width=200, height=60)]
    doc.layers = [layer, ObjectLayer(name="Objects", objects=objs)]
    doc.rebuild_index()

    dlg = ResizeMapDialog(doc)
    dlg.resize(600, 520)
    dlg.show()
    _events()
    dlg.grab().save(str(SHOT1))
    print("saved", SHOT1)

    # 制造溢出：宽度缩到 30，X 向右挪 4 -> 右侧一列内容越界；Y 向下挪 2
    dlg.sp_width.setValue(30)
    dlg.sp_height.setValue(22)
    dlg.sp_x.setValue(4)
    dlg.sp_y.setValue(2)
    _events()
    print("overflow info:", dlg.preview.overflow_info())
    dlg.grab().save(str(SHOT2))
    print("saved", SHOT2)
    dlg.close()


if __name__ == "__main__":
    main()
