"""从本机 Tiled 的 tilededitor.dll 提取图层面板图标，做深色主题适配，
生成自包含的 rwmap/widgets/tiled_icons.py（base64 内嵌 PNG，打包 exe 无需外部资源）。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.extract_tiled_icons
来源： <Tiled目录>/tilededitor.dll 内嵌 Qt 资源 :/images/14|16/*.png （Qt6 RCC，
      每个 PNG 前有 4 字节大端长度）。编号按 DLL 内出现顺序，见 ICON_IDS。
输出： rwmap/widgets/tiled_icons.py + logs/tiled_icons_dark/（验收用）
"""
from __future__ import annotations

import base64
import io
import struct
import textwrap
from pathlib import Path

DLL = Path("<Tiled目录>/tilededitor.dll")
OUT_MODULE = Path("rwmap/widgets/tiled_icons.py")
OUT_DIR = Path("logs/tiled_icons_dark")

# 语义名 -> (提取编号, Tiled 资源名)
ICON_IDS = {
    "visible":      (116, "14/visible.png"),
    "hidden":       (117, "14/hidden.png"),
    "locked":       (114, "14/locked.png"),
    "unlocked":     (115, "14/unlocked.png"),
    "tile":         (131, "16/layer-tile.png"),
    "object":       (125, "16/layer-object.png"),
    "new":          (120, "16/document-new.png"),
    "up":           (128, "16/go-up.png"),
    "down":         (159, "16/go-down.png"),
    "duplicate":    (144, "16/stock-duplicate-16.png"),
    "delete":       (134, "16/edit-delete.png"),
    "merge":        (163, "16/merge-down.png"),
    "lock_button":  (122, "16/locked.png"),
    "unlock_button": (136, "16/unlocked.png"),
    "show_others":  (162, "16/show_hide_others.png"),
}


def _extract_all() -> list[bytes]:
    data = DLL.read_bytes()
    sig = b"\x89PNG\r\n\x1a\n"
    out, pos = [], 0
    while True:
        p = data.find(sig, pos)
        if p < 0:
            break
        end = data.find(b"IEND", p)
        if end < 0:
            break
        blob = data[p:end + 8]
        assert struct.unpack(">I", data[p - 4:p])[0] == len(blob)
        out.append(blob)
        pos = end + 8
    return out


def _dark_theme(blob: bytes):
    """把 Tiled 亮色主题图标适配到深色面板。

    Tiled 的 PNG 自带**不透明白底 / 黑底**，直接放深色面板上会出现刺眼方块。
    这里把接近纯白 / 纯黑的中性像素换成面板底色（#2b2b2b），中间调提亮成
    亮灰蓝，彩色（蓝网格 / 紫对象 / 黄星 / 绿箭头）只提亮、保留色相。
    形状不变，视觉上等价于深色主题版的 Tiled 图标。
    """
    from PIL import Image
    BG = (43, 43, 43)
    im = Image.open(io.BytesIO(blob)).convert("RGBA")
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a < 8:
                continue
            lum = (r * 299 + g * 587 + b * 114) // 1000
            sat = max(r, g, b) - min(r, g, b)
            if sat < 42:                       # 中性：白底 / 黑底 / 灰线条
                if lum > 210 or lum < 45:
                    rgb = BG                   # 与面板底色融为一体
                else:
                    v = 150 + int(lum * 0.34)
                    rgb = (v, min(255, v + 4), min(255, v + 10))
            else:                              # 蓝 / 紫 / 黄 / 绿：提亮保色相
                k = 1.0 + (1.0 - lum / 255) * 0.35
                rgb = tuple(min(255, int(c * k)) for c in (r, g, b))
            px[x, y] = (*rgb, a)
    return im


def main():
    from PIL import Image, ImageDraw, ImageFont
    if not DLL.is_file():
        raise SystemExit(f"找不到 {DLL}，无法提取 Tiled 图标")
    blobs = _extract_all()
    print("DLL 内 PNG 资源：", len(blobs))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    encoded = {}
    for name, (idx, qrc) in ICON_IDS.items():
        im = _dark_theme(blobs[idx])
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        raw = buf.getvalue()
        encoded[name] = base64.b64encode(raw).decode("ascii")
        im.save(OUT_DIR / f"{name}.png")
        print(f"  {name:14s} #{idx:3d} {qrc:34s} {im.size}  {len(raw)}B")

    lines = [
        '"""Tiled 原生图层面板图标（深色主题适配版，自包含）。',
        "",
        "来源：本机 <Tiled目录>/tilededitor.dll 内嵌 Qt 资源 PNG，按提取编号见",
        "rwmap/probe/extract_tiled_icons.py 的 ICON_IDS；深色化只改像素亮度/色相，",
        "不改形状，面板整体保持深色。图标数据内嵌为 base64，打包 exe 无需附带文件。",
        '"""',
        "from __future__ import annotations",
        "",
        "import base64",
        "",
        "from PySide6.QtCore import QByteArray",
        "from PySide6.QtGui import QIcon, QPixmap",
        "",
        "_DATA: dict[str, str] = {",
    ]
    for name, value in encoded.items():
        chunks = textwrap.wrap(value, 76)
        lines.append(f'    "{name}": (')
        for ch in chunks:
            lines.append(f'        "{ch}"')
        lines.append("    ),")
    lines += [
        "}",
        "",
        "",
        "def tiled_pixmap(name: str) -> QPixmap:",
        '    """按语义名取一张 14/16 px 图标；未知名字返回空 QPixmap。"""',
        "    value = _DATA.get(name)",
        "    pix = QPixmap()",
        "    if value:",
        "        pix.loadFromData(QByteArray(base64.b64decode(value)))",
        "    return pix",
        "",
        "",
        "def tiled_icon(name: str) -> QIcon:",
        "    return QIcon(tiled_pixmap(name))",
        "",
        "",
        "ICON_NAMES = tuple(_DATA)",
        "",
    ]
    OUT_MODULE.write_text("\n".join(lines), encoding="utf-8")
    print("written:", OUT_MODULE.resolve(), OUT_MODULE.stat().st_size, "bytes")

    # 验收拼图：深色背景上显示所有图标
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 13)
    except OSError:
        font = ImageFont.load_default()
    names = list(ICON_IDS)
    cols, cell = 5, 96
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 18)), (43, 43, 43))
    d = ImageDraw.Draw(sheet)
    for i, name in enumerate(names):
        x, y = (i % cols) * cell, (i // cols) * (cell + 18)
        pic = Image.open(OUT_DIR / f"{name}.png").convert("RGBA")
        # 放大后按自身 alpha 合成，透明区露出面板底色（不能直接 convert("RGB")，
        # 否则透明像素携带的白/黑 RGB 会被画成实心方块）
        pic = pic.resize((48, 48), Image.NEAREST)
        tile = Image.new("RGBA", (48, 48), (43, 43, 43, 255))
        tile.alpha_composite(pic)
        sheet.paste(tile.convert("RGB"), (x + 24, y + 6))
        d.text((x + 6, y + 60), name, fill=(225, 225, 225), font=font)
    sheet.save("logs/tiled_icons_dark_sheet.png")
    print("sheet:", sheet.size)


if __name__ == "__main__":
    main()
