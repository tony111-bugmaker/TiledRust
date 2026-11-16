"""可视化验收：新建图层按钮整块判定区 + hover 与上移按钮一致。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_new_layer_button_visual
输出： logs/新建图层按钮_判定区.png
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path("logs") / "新建图层按钮_判定区.png"


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    fams = QFontDatabase.applicationFontFamilies(fid)
    if fams:
        app.setFont(QFont(fams[0], 9))

    from PIL import Image, ImageDraw, ImageFont
    from rwmap.widgets.layers_dock import LayersDock

    d = LayersDock()
    d.resize(300, 240)
    d.show()
    app.processEvents()

    shots = []
    for btn, name in ((d.btn_new, "新建图层"), (d.btn_up, "上移图层")):
        QTest.mouseMove(btn, QPoint(btn.width() // 2, btn.height() // 2))
        app.processEvents()
        p = btn.grab()
        path = Path("logs") / f"_btn_{name}.png"
        p.save(str(path))
        shots.append((name, path))

    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    scale = 6
    cols = Image.new("RGB", (len(shots) * 26 * scale + 40, 26 * scale + 60), (30, 30, 30))
    dr = ImageDraw.Draw(cols)
    for i, (name, path) in enumerate(shots):
        im = Image.open(path).convert("RGB").resize((26 * scale, 26 * scale), Image.NEAREST)
        x = 10 + i * (26 * scale + 20)
        cols.paste(im, (x, 30))
        dr.text((x, 6), f"{name}（hover）", font=font, fill=(230, 230, 230))
    cols.save(str(OUT))
    print("saved", OUT)
    print("popup mode:", d.btn_new.popupMode())


if __name__ == "__main__":
    main()
