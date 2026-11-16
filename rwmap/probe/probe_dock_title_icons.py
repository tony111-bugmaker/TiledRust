"""验收图：图层面板标题栏的浮动/关闭图标是否与其它 Dock 原生按钮一致。

离屏渲染里 Qt 原生标题栏是浅色的，因此这里只取原生按钮的图标贴到深色
画布上对比；像素一致性由 test_layers_dock_tiled.py 断言。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path("logs/图层标题栏_浮动图标统一.png")


def _brighten(im):
    """仅用于验收图：离屏下 Qt 原生图标是深灰，放大时提亮便于看清形状。"""
    from PIL import Image
    src = im.convert("RGBA")
    px = src.load()
    for y in range(src.height):
        for x in range(src.width):
            r, g, b, a = px[x, y]
            if a > 60 and max(r, g, b) < 200:
                v = 150 + (r * 299 + g * 587 + b * 114) // 4000
                px[x, y] = (min(255, v), min(255, v + 4), min(255, v + 10), a)
    return src


def main():
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QWidget
    from PIL import Image, ImageDraw, ImageFont

    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    fams = QFontDatabase.applicationFontFamilies(fid)
    if fams:
        app.setFont(QFont(fams[0], 9))

    from rwmap.app.main_window import MainWindow

    w = MainWindow(auto_open=False)
    w._open(Path(w.state.profile.sample_map))
    w.resize(1100, 760)
    w.show()
    QApplication.processEvents()

    custom_pix = w.layers_dock.titleBarWidget().grab()
    custom_pix.save("logs/_tb_custom.png")
    custom = Image.open("logs/_tb_custom.png").convert("RGB")

    native = {b.objectName(): b for b in w.properties_dock.findChildren(QWidget)
              if b.objectName().startswith("qt_dockwidget_")}
    f = native["qt_dockwidget_floatbutton"].icon().pixmap(16, 16)
    c = native["qt_dockwidget_closebutton"].icon().pixmap(16, 16)
    f.save("logs/_tb_native_float.png")
    c.save("logs/_tb_native_close.png")
    fi = _brighten(Image.open("logs/_tb_native_float.png"))
    ci = _brighten(Image.open("logs/_tb_native_close.png"))

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
    except OSError:
        font = ImageFont.load_default()

    scale = 3
    bar_w, bar_h = custom.width * scale, custom.height * scale
    W = max(bar_w, 560)
    H = 30 + bar_h + 18 + 24 + 70 + 34
    canvas = Image.new("RGB", (W, H), (30, 30, 30))
    d = ImageDraw.Draw(canvas)

    d.text((8, 5), "修改后：图层面板标题栏（深色，右侧为原生浮动 / 关闭图标）",
           fill=(235, 235, 235), font=font)
    big = custom.resize((bar_w, bar_h), Image.NEAREST)
    canvas.paste(big, (0, 28))

    y2 = 28 + bar_h + 14
    d.text((8, y2), "参照：属性面板原生 Dock 的两个按钮图标（放大 6 倍，仅提亮显示）",
           fill=(235, 235, 235), font=font)
    y2 += 26
    d.rectangle((0, y2, W, y2 + 100), fill=(51, 51, 51))
    for i, icon in enumerate((fi, ci)):
        zoom = icon.resize((16 * 6, 16 * 6), Image.NEAREST)
        x = 60 + i * 130
        canvas.paste(zoom, (x, y2 + 2), zoom)   # 用自身 alpha 作蒙版

    d.text((8, y2 + 108),
           "旧版是文字 ◇ / ×，部分字体会渲染成彩色圆环；现在两处图标与原生逐像素相同",
           fill=(170, 195, 220), font=font)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(OUT))
    print("saved:", OUT.resolve(), canvas.size)
    print("custom icon == native float:",
          w.layers_dock.btn_float.icon().pixmap(16, 16).toImage() == f.toImage())
    print("custom icon == native close:",
          w.layers_dock.btn_close.icon().pixmap(16, 16).toImage() == c.toImage())


if __name__ == "__main__":
    main()
