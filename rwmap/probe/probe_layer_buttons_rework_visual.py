"""可视化验收：图层按钮栏改版——新建弹菜单 / 删除向下合并 / 锁定其它层。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_layer_buttons_rework_visual
输出： logs/图层按钮栏_改版验收.png
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path("logs") / "图层按钮栏_改版验收.png"


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtCore import QPoint
    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    fams = QFontDatabase.applicationFontFamilies(fid)
    if fams:
        app.setFont(QFont(fams[0], 9))
    from rwmap.theme import apply_app_theme
    apply_app_theme(app)

    from PIL import Image, ImageDraw, ImageFont
    from rwmap.widgets.layers_dock import LayersDock

    d = LayersDock()
    d.resize(300, 220)
    d.show()
    app.processEvents()

    # 1) 整面板（看按钮栏：无「向下合并」）
    panel_path = Path("logs") / "_layer_panel_rework.png"
    d.grab().save(str(panel_path))

    # 2) 新建按钮的弹出菜单
    menu = d.btn_new.menu()
    menu.show()
    app.processEvents()
    menu_path = Path("logs") / "_new_layer_menu.png"
    menu.grab().save(str(menu_path))
    menu.hide()

    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 17)
    font_sm = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
    panel = Image.open(panel_path).convert("RGB")
    menu_im = Image.open(menu_path).convert("RGB")

    s = 3
    panel = panel.resize((panel.width * s, panel.height * s), Image.NEAREST)
    menu_im = menu_im.resize((menu_im.width * s, menu_im.height * s), Image.NEAREST)

    W = panel.width + menu_im.width + 60
    H = max(panel.height, menu_im.height) + 130
    canvas = Image.new("RGB", (W, H), (24, 24, 26))
    dr = ImageDraw.Draw(canvas)

    dr.text((20, 12), "图层按钮栏改版验收", font=font, fill=(235, 235, 235))
    canvas.paste(panel, (20, 80))
    dr.text((20, panel.height + 84), "① 无「向下合并」 ② 锁定=其它所有层",
            font=font_sm, fill=(170, 170, 175))

    mx = 20 + panel.width + 20
    canvas.paste(menu_im, (mx, 80))
    dr.text((mx, 46), "③ 点「新建图层」弹出此菜单，选哪项建哪项",
            font=font_sm, fill=(170, 170, 175))
    dr.text((mx, panel.height + 84), "新层加到图层列表最下侧",
            font=font_sm, fill=(170, 170, 175))

    canvas.save(str(OUT))
    print("saved", OUT)
    print("popup mode:", d.btn_new.popupMode())
    print("has merge button:", hasattr(d, "btn_merge"))
    print("lock tooltip:", d.btn_lock.toolTip())


if __name__ == "__main__":
    main()
