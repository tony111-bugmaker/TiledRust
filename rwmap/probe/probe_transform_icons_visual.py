"""可视化验收：地图变换 4 图标按软件统一风格重绘（亮蓝描边 + 淡蓝填充 + 绿色箭头）。

用法： python -m rwmap.probe.probe_transform_icons_visual
输出： examples/验证_变换图标统一风格.png
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "examples" / "验证_变换图标统一风格.png"


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSize
    app = QApplication.instance() or QApplication([])

    from PIL import Image, ImageDraw, ImageFont
    from rwmap.widgets.object_toolbar import make_object_icon

    kinds = [("flip_h", "水平翻转"), ("flip_v", "垂直翻转"),
             ("rot_cw", "顺时针"), ("rot_ccw", "逆时针")]
    scale = 10
    cell = 28 * scale
    pad = 20
    total_w = len(kinds) * (cell + pad) + pad
    canvas = Image.new("RGB", (total_w, cell + 90), (30, 30, 30))
    dr = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
    except OSError:
        font = ImageFont.load_default()

    for i, (kind, label) in enumerate(kinds):
        icon = make_object_icon(kind)
        pix = icon.pixmap(QSize(28, 28))
        path = ROOT / "logs" / f"_xf_{kind}.png"
        pix.save(str(path))
        im = Image.open(path).convert("RGBA")
        im = im.resize((cell, cell), Image.Resampling.NEAREST)
        x = pad + i * (cell + pad)
        canvas.paste(im, (x, 20), im)
        dr.text((x + 10, 20 + cell + 8), label, font=font, fill=(220, 220, 225))

    dr.text((pad, 2), "统一风格：亮蓝描边 + 淡蓝填充 + 绿色箭头", font=font,
            fill=(150, 200, 240))
    canvas.save(str(OUT))
    print("saved", OUT)


if __name__ == "__main__":
    main()
