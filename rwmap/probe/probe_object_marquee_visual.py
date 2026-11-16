"""可视化验证：对象层用 M 工具框选多对象 + 切图层清选框。

离屏跑真实示例地图，截图放到 logs/ 下便于人工核对。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from rwmap.document.layer import ObjectLayer, TileLayer
from rwmap.document.map_document import MapDocument
from rwmap.document.map_object import MapObject
from rwmap.utils.paths import as_path

LOGS = Path(r"<项目目录>/logs")
LOGS.mkdir(parents=True, exist_ok=True)


def grab(w, name):
    img = QImage(w.view.viewport().size(), QImage.Format_ARGB32)
    w.view.viewport().render(img)
    path = LOGS / name
    img.save(str(path))
    return path


def main():
    app = QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    from rwmap.document.map_document import MapDocument

    # 合成地图：瓦片层 + 一个含 8 个矩形对象的对象层（4x2 网格，间距 120px）。
    tile = TileLayer(width=40, height=24, name="Ground",
                     cells=[0] * (40 * 24), readonly=False)
    objs = []
    oid = 1
    for r in range(2):
        for c in range(4):
            objs.append(MapObject(
                object_id=oid, name=f"T{oid}", shape="rect",
                x=40 + c * 120, y=40 + r * 120, width=60, height=60,
                type_="unit"))
            oid += 1
    obj_layer = ObjectLayer(name="Triggers", objects=objs)
    doc = MapDocument(width=40, height=24, tile_w=20, tile_h=20,
                      layers=[tile, obj_layer], tilesets=[])

    w = MainWindow(auto_open=False)
    w.state.open_document(doc)
    w.state.profile.write_enabled = True
    w.scene.set_document(doc)
    w.layers_dock.doc = doc            # 否则 select_layer 会因 doc 为空直接返回
    w.resize(1000, 700)
    w.show()
    QApplication.processEvents()
    w.view.fitInView(w.scene.sceneRect(), Qt.KeepAspectRatio)
    QApplication.processEvents()

    tile_layer = tile
    # 切到对象层（走真实路径）。
    w.layers_dock.select_layer(obj_layer)
    QApplication.processEvents()
    if w._active_layer is not obj_layer:   # 兜底：直接走真实处理函数
        w._on_layer_selected(obj_layer)
    print("active_layer is obj_layer:", w._active_layer is obj_layer)

    # 框选覆盖第 1 行的 3 个对象（场景像素：T1~T3 在 y=40~100，x=40~280）。
    x0, y0, x1, y1 = 20.0, 20.0, 340.0, 140.0
    print("框选区域(场景像素):", (x0, y0, x1, y1))

    w._set_object_tool("tileselect")
    w.view.objectToolPressed.emit(x0, y0)
    w.view.objectToolMoved.emit(x1, y1)
    w.view.objectToolReleased.emit(x1, y1)
    QApplication.processEvents()
    selected = list(w.scene.selected_objects)
    print("框选选中对象数:", len(selected), "ids:", [o.object_id for o in selected])
    assert len(selected) >= 3, "应框选到至少 3 个对象（第 1 行）"

    before = grab(w, "obj_marquee_after.png")
    print("截图(对象层框选后):", before)

    # 切回瓦片层 → 对象选区应被清掉（与图层面板点击等价：都走 _on_layer_selected）。
    w._on_layer_selected(tile_layer)
    QApplication.processEvents()
    print("切瓦片层后 选中对象数:", len(w.scene.selected_objects))
    assert len(w.scene.selected_objects) == 0
    w.view.fitInView(w.scene.sceneRect(), Qt.KeepAspectRatio)
    QApplication.processEvents()
    after = grab(w, "obj_marquee_after_switch.png")
    print("截图(切瓦片层后):", after)

    # 拼一张对照图。
    from PIL import Image, ImageDraw, ImageFont
    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    tw = 640
    a = a.resize((tw, int(a.height * tw / a.width)))
    b = b.resize((tw, int(b.height * tw / b.width)))
    band = 60
    canvas = Image.new("RGB", (tw * 2 + 12, a.height + band), (32, 36, 42))
    canvas.paste(a, (0, band))
    canvas.paste(b, (tw + 12, band))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
    except Exception:
        font = ImageFont.load_default()
    d.text((12, 10),
           "左：对象层 M 工具框选 → 框内对象出现黄色选中箭头（多选）",
           font=font, fill=(255, 220, 120))
    d.text((12, 34),
           "右：切到瓦片层 → 对象选区清空、瓦片选框也清除",
           font=font, fill=(255, 220, 120))
    out = LOGS / "obj_marquee_compare.png"
    canvas.save(out)
    print("对照图:", out)
    print("PASS")


if __name__ == "__main__":
    main()
