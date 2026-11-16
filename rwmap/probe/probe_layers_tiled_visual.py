"""可视化验收：图层面板「Tiled 原版 / 旧版 / 新版 Tiled 风格深色」对比。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_layers_tiled_visual
输出： logs/图层面板_Tiled风格对比.png
旧版与 Tiled 截图来自用户提供的剪贴板图片，缺失时自动跳过该行。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = Path("logs/图层面板_Tiled风格对比.png")
CLIP = Path("<用户目录>/.workbuddy/clipboard-images")
OLD = CLIP / "clipboard-2026-11-10T11-34-22-136Z-577f8319.png"
TILED = CLIP / "clipboard-2026-11-10T11-34-22-137Z-3869604d.png"


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    app = QApplication.instance() or QApplication([])
    fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    families = QFontDatabase.applicationFontFamilies(fid)
    if families:
        app.setFont(QFont(families[0], 9))

    from PIL import Image, ImageDraw, ImageFont
    from rwmap.app.main_window import MainWindow

    w = MainWindow(auto_open=False)
    w._open(Path(w.state.profile.sample_map))
    w.resize(1100, 760)
    w.show()
    app.processEvents()
    dock = w.layers_dock
    # 选中 Units 行，与用户 Tiled 截图的蓝色行一致
    units = next((l for l in w.state.doc.layers if l.name == "Units"), None)
    if units is not None:
        dock.select_layer(units)
    dock.grab().save("logs/_layers_tiled_new.png")
    app.processEvents()

    rows = [
        ("① Tiled 原版（白底，仅作图标/布局参照）", TILED),
        ("② 旧版图层面板（深色，但用复选框 + [tile] 文本）", OLD),
        ("③ 新版：Tiled 原生图标 + 行内眼睛/锁 + 图标按钮栏（保持深色）",
         Path("logs/_layers_tiled_new.png")),
    ]
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 17)
    except OSError:
        font = ImageFont.load_default()

    blocks = []
    for label, path in rows:
        if not path.is_file():
            print("skip missing:", path)
            continue
        blocks.append((label, Image.open(path).convert("RGB")))
    width = max(im.width for _, im in blocks) + 20
    height = sum(im.height + 40 for _, im in blocks)
    out = Image.new("RGB", (width, height), (238, 238, 238))
    draw = ImageDraw.Draw(out)
    top = 0
    for label, im in blocks:
        draw.text((10, top + 8), label, fill=(20, 20, 20), font=font)
        top += 36
        out.paste(im, (10, top))
        top += im.height + 4
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(OUT))
    print("saved:", OUT.resolve(), out.size)
    Path("logs/_layers_tiled_new.png").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
