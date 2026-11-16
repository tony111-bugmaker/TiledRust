"""第九轮界面验收：工具栏（铅笔 / 橡皮 / 蓝色边框）、自由坐标对象的缩放手柄。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"   # offscreen 无字体，中文会变方块
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402
from rwmap.utils.paths import as_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
FONT = r"C:\Windows\Fonts\msyh.ttc"


def caption(path: Path, lines, out: Path):
    im = Image.open(path).convert("RGB")
    font = ImageFont.truetype(FONT, 17)
    band = 20 + 26 * len(lines)
    canvas = Image.new("RGB", (im.width, im.height + band), (32, 36, 42))
    canvas.paste(im, (0, band))
    d = ImageDraw.Draw(canvas)
    for i, text in enumerate(lines):
        d.text((14, 8 + i * 26), text, font=font, fill=(150, 205, 250))
    canvas.save(out)


def main():
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei", 9))
    w = MainWindow(auto_open=False)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1440, 900)
    w.show()
    QApplication.processEvents()

    w._open(as_path(load_profile("1.15").sample_map))
    QApplication.processEvents()

    layer = next(l for l in w.state.doc.layers
                 if isinstance(l, ObjectLayer))
    # 故意用非 20 倍数的坐标：默认自由摆放，四角不再贴在瓦片网格线上。
    free = MapObject(object_id=901, name="自由矩形", shape="rect",
                     x=313, y=287, width=137, height=93)
    snap = MapObject(object_id=902, name="Shift 吸附", shape="rect",
                     x=480, y=400, width=120, height=80)
    layer.objects = [free, snap]
    layer.dirty = True
    w.state.doc.dirty = True
    w.scene.set_document(w.state.doc)
    w.view.set_document(w.state.doc)
    w.scene.current_layer = w.state.current_layer
    w.trigger_graph_dock.refresh()
    w.scene.set_selected_object(free)
    w.trigger_graph_dock.select_object(free)
    w._set_object_tool("select")
    w.statusBar().showMessage(
        "选择对象：蓝色轮廓 + 8 个缩放手柄自由拉伸；默认自由坐标，按 Shift 才吸附网格")
    w.view.fitInView(QRectF(280, 250, 380, 280), Qt.KeepAspectRatio)
    QApplication.processEvents()

    full = LOGS / "第九轮_全窗口.png"
    w.grab().save(str(full))
    shot = Image.open(full).convert("RGB")

    bar = LOGS / "第九轮_工具栏特写.png"
    shot.crop((0, 0, 1000, 112)).resize((2000, 224), Image.LANCZOS).save(bar)
    caption(bar, [
        "主工具栏已隐藏 4 个菜单里重复的文字按钮（仅留版本下拉）",
        "图标行：斜放铅笔 = 瓦片画笔，第二个 = 橡皮；每个按钮都有蓝色边框",
    ], LOGS / "第九轮_工具栏_标注.png")

    detail = LOGS / "第九轮_缩放手柄特写.png"
    shot.crop((250, 105, 1130, 690)).resize((1320, 877), Image.LANCZOS).save(detail)
    caption(detail, [
        "默认自由坐标：矩形四角不再贴着瓦片网格线（按 Shift 才吸附）",
        "选中对象：蓝色实线轮廓 + 白色虚线框 + 四角 / 四边共 8 个缩放手柄",
    ], LOGS / "第九轮_缩放手柄_标注.png")

    print("full      ", full.resolve())
    print("toolbar   ", (LOGS / "第九轮_工具栏_标注.png").resolve())
    print("handles   ", (LOGS / "第九轮_缩放手柄_标注.png").resolve())


if __name__ == "__main__":
    main()
