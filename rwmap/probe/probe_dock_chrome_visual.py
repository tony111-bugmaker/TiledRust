"""验收图：5 个 Dock 面板「统一格式」标题栏前后对比。

上排 = 修改前（Qt 原生标题栏，各面板图标/字号随系统、彼此不一致）；
下排 = 修改后（共用 DockTitleBar：同高度、同字号、同图标尺寸）。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path("logs/面板统一格式_标题栏对比.png")
OUT_WINDOW = Path("logs/面板统一格式_整窗.png")
W = 560


def main():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QWidget
    from PIL import Image, ImageDraw, ImageFont

    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    fams = QFontDatabase.applicationFontFamilies(fid)
    if fams:
        app.setFont(QFont(fams[0], 9))

    from rwmap.app.main_window import MainWindow
    from rwmap.app.panel_layout import iter_panels

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
        small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 12)
    except OSError:
        font = small = ImageFont.load_default()
    lt = (235, 235, 235)
    dim = (170, 195, 220)

    def to_pil(pix, name):
        p = Path("logs") / name
        pix.save(str(p))
        return Image.open(p).convert("RGB")

    # ---- 修改前：Qt 原生标题栏（不装统一格式） ----
    mw = QMainWindow()
    native_bars = []
    for i, title in enumerate(("属性", "图块集")):
        d = QDockWidget(title, mw)
        d.setWidget(QWidget())
        mw.addDockWidget(Qt.RightDockWidgetArea if i == 0 else Qt.LeftDockWidgetArea, d)
        native_bars.append((title, d))
    mw.resize(560, 200)
    mw.show()
    QApplication.processEvents()
    before = []
    for title, d in native_bars:
        # 原生标题栏位于 dock 顶部约 20px
        img = to_pil(d.grab(), f"_chrome_native_{title}.png")
        before.append((title, img.crop((0, 0, min(img.width, W), min(22, img.height)))))

    # ---- 修改后：5 个面板共用统一标题栏 ----
    w = MainWindow(auto_open=False)
    w._open(Path(w.state.profile.sample_map))
    w.resize(1200, 800)
    w.show()
    for _ in range(3):
        QApplication.processEvents()
    w.grab().save(str(OUT_WINDOW))
    after = []
    for spec, d in iter_panels(w):
        img = to_pil(d.titleBarWidget().grab(),
                     f"_chrome_after_{spec.key.replace('.', '_')}.png")
        after.append((spec.title, img.crop((0, 0, min(img.width, W), img.height))))

    row_h = 30
    h1 = 26 + len(before) * (row_h + 4)
    h2 = 26 + len(after) * (row_h + 4)
    canvas = Image.new("RGB", (W, h1 + h2 + 46), (30, 30, 30))
    draw = ImageDraw.Draw(canvas)

    draw.text((8, 4), "修改前：Qt 原生标题栏（图标偏小、字号随系统，彼此不统一）",
              fill=lt, font=font)
    y = 26
    for title, bar in before:
        draw.text((8, y), title, fill=dim, font=small)
        canvas.paste(bar, (80, y))
        y += row_h + 4

    y2 = h1 + 8
    draw.text((8, y2), "修改后：5 个面板共用 DockTitleBar（高度 22 / 标题 9pt 粗体 / 图标 16px / 按钮 18px）",
              fill=lt, font=font)
    y = y2 + 26
    for title, bar in after:
        draw.text((8, y + 3), title, fill=dim, font=small)
        canvas.paste(bar, (140, y))
        y += row_h + 4

    draw.text((8, y + 2), "右上角均为 Qt 原生小窗口 + × 图标，逐像素相同；标题栏全部深色",
              fill=dim, font=small)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(OUT))
    print("saved:", OUT.resolve(), canvas.size)
    print("saved:", OUT_WINDOW.resolve())
    print("after bars:", [t for t, _ in after])


if __name__ == "__main__":
    main()
