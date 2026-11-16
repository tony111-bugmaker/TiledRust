"""第三十四轮验收图：合并后的「选择 / 框选」按钮 + 斜放滴漆的油漆桶图标。

离屏渲染 object_toolbar 的自绘图标，产出
``examples/工具栏_合并选择与油漆桶.png``：
上排是真实工具栏的 15 个图标（选中的合并按钮带深蓝底），
下排是「填充」「选择 / 框选」的放大特写以及被合并掉的旧虚线框图标。

用法::

    .venv\\Scripts\\python.exe rwmap\\probe\\probe_toolbar_merge_icons.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.widgets.object_toolbar import TOOLS, make_object_icon  # noqa: E402

OUT = ROOT / "examples" / "工具栏_合并选择与油漆桶.png"
FONT = "C:/Windows/Fonts/msyh.ttc"


def _font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def _qt_icon(kind: str, size: int = 28) -> Image.Image:
    pix = make_object_icon(kind).pixmap(size, size)
    img = pix.toImage().convertToFormat(QImage.Format_ARGB32)
    data = bytes(img.bits())
    return Image.frombytes("RGBA", (img.width(), img.height()),
                           data, "raw", "BGRA", img.bytesPerLine(), 1)


def _card(icon: Image.Image, scale: int, checked: bool = False):
    """按 QToolButton 的深色样式渲染一枚按钮。"""
    side = 28 * scale
    card = Image.new("RGBA", (side, side),
                     (29, 65, 98, 255) if checked else (38, 42, 48, 0))
    big = icon.resize((side, side), Image.LANCZOS)
    card.alpha_composite(big)
    return card


def main():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    W, H = 1000, 500
    img = Image.new("RGB", (W, H), (30, 34, 40))
    draw = ImageDraw.Draw(img)
    title = _font(26)
    small = _font(15)
    label = _font(18)

    draw.text((24, 18), "第三十四轮 · 工具栏：两个按钮合并、填充图标改斜桶滴漆",
              font=title, fill=(232, 240, 250))
    draw.text((24, 56), "上：合并后共 15 个图标（S / M 都切到同一个鼠标箭头按钮）",
              font=small, fill=(143, 184, 216))

    # ---- 上排：真实图标行 ----
    x0, y0, scale = 24, 92, 2
    for i, (tid, name, _key, _tip) in enumerate(TOOLS):
        card = _card(_qt_icon(tid), scale, checked=(tid == "select"))
        img.paste(card, (x0 + i * 62, y0), card)
        if tid in ("fill", "select"):
            draw.text((x0 + i * 62, y0 + 58),
                      "F 填充" if tid == "fill" else "S/M 选择",
                      font=_font(12), fill=(120, 190, 245))
    draw.line((24, 178, W - 24, 178), fill=(70, 80, 92), width=1)

    # ---- 下排：放大特写 ----
    draw.text((24, 194), "填充（F）", font=label, fill=(232, 240, 250))
    draw.text((24, 220), "油漆桶斜放 · 桶口滴落颜料 · 没有提手", font=small,
              fill=(143, 184, 216))
    draw.text((520, 194), "选择 / 框选（S / M）", font=label,
              fill=(232, 240, 250))
    draw.text((520, 220), "一个鼠标箭头 = 点选对象 + 拖空白框选", font=small,
              fill=(143, 184, 216))

    big_scale = 6
    for kind, cx in (("fill", 40), ("select", 536)):
        card = _card(_qt_icon(kind), big_scale, checked=(kind == "select"))
        img.paste(card, (cx, 254), card)
        draw.rectangle((cx - 1, 253, cx + 28 * big_scale, 254 + 28 * big_scale),
                       outline=(70, 110, 150), width=1)

    # 被合并掉的旧虚线框图标（make_object_icon 仍保留绘制，工具栏已不再有按钮）
    old = _card(_qt_icon("tileselect"), 4)
    img.paste(old, (820, 270), old)
    draw.text((790, 398), "旧「框选 M」虚线框：已移除", font=_font(13),
              fill=(150, 160, 175))

    draw.text((24, 452),
              "合并后：瓦片层拖空白 → 框选瓦片；对象层拖空白 → 框选多个对象；"
              "点对象 → 选中 / 拖动 / 8 箭头缩放",
              font=_font(15), fill=(190, 205, 220))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print("saved:", OUT)

    # 颜料滴落区域（右下）必须真的画出了蓝色像素，供人工/自动验收。
    fill = _qt_icon("fill")
    drops = 0
    for y in range(19, 28):
        for x in range(19, 28):
            r, g, b, a = fill.getpixel((x, y))
            if a > 150 and b > 170 and b > r + 40 and g > 80:
                drops += 1
    print("fill icon drip pixels:", drops)
    assert drops >= 6, "右下角没画出滴落的颜料"


if __name__ == "__main__":
    main()
