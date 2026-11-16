"""可视化验收：设置菜单三项 + 面板列表设置对话框。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_panel_settings_visual
输出： logs/设置菜单_面板列表.png
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path("logs/设置菜单_面板列表.png")


def _events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def _shot(widget, path: Path):
    pix = widget.grab()
    pix.save(str(path))
    return path


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    app = QApplication.instance() or QApplication([])
    # offscreen 平台不带任何字体，中文会渲染成豆腐块；手动挂载系统字体，
    # 仅影响本验收脚本，真实 GUI 启动不需要。
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    families = QFontDatabase.applicationFontFamilies(fid)
    if families:
        app.setFont(QFont(families[0], 9))
    from PIL import Image, ImageDraw, ImageFont
    from rwmap.app.main_window import MainWindow
    from rwmap.widgets import PanelSettingsDialog

    w = MainWindow(auto_open=False)
    w.resize(1200, 760)
    w.show()
    _events()

    tmp = Path("logs/_panel_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    shots = []

    # 1) 主窗口全貌
    shots.append(("① 菜单栏这一行新增「设置」", _shot(w, tmp / "window.png")))

    # 2) 菜单栏特写（放大 2 倍）
    full = Image.open(shots[-1][1]).convert("RGB")
    bar = full.crop((0, 0, min(full.width, 700), 32))
    bar = bar.resize((bar.width * 2, bar.height * 2), Image.LANCZOS)
    bar.save(str(tmp / "menubar.png"))
    shots.append(("② 菜单：文件 / 编辑 / 视图 / 地图 / 设置", tmp / "menubar.png"))

    # 3) 展开「设置」菜单
    menu = w.menuBar().actions()[-1].menu()
    menu.show()
    _events()
    shots.append(("③ 设置下拉：面板列表设置 / 导入面板 / 保存当前面板",
                  _shot(menu, tmp / "menu.png")))
    menu.hide()

    # 4) 面板列表设置对话框
    dlg = PanelSettingsDialog(w)
    dlg.show()
    _events()
    shots.append(("④ 面板列表设置：5 个可停靠面板清单 + 显隐勾选",
                  _shot(dlg, tmp / "dialog.png")))

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
    except OSError:
        font = ImageFont.load_default()

    max_w = 940
    blocks = []
    for label, path in shots:
        im = Image.open(path).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, int(im.height * max_w / im.width)),
                           Image.LANCZOS)
        blocks.append((label, im))
    width = max(im.width for _, im in blocks)
    height = sum(im.height + 36 for _, im in blocks)
    out = Image.new("RGB", (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(out)
    top = 0
    for label, im in blocks:
        draw.text((12, top + 7), label, fill=(25, 25, 25), font=font)
        top += 36
        out.paste(im, (0, top))
        top += im.height
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(OUT))
    for p in tmp.glob("*.png"):
        p.unlink()
    tmp.rmdir()
    print("saved:", OUT.resolve(), "size:", out.size)
    print("panels:", [s.title for s in
                      __import__("rwmap.app.panel_layout",
                                 fromlist=["PANEL_SPECS"]).PANEL_SPECS])


if __name__ == "__main__":
    main()
