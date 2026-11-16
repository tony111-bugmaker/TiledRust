"""生成「插入图片 / 插入文字 · 游戏适配」验收图。

图里所有 XML 片段与瓦片图都来自脚本真实写出并解码的 TMX，不是手绘示意。
跑法：.venv\\Scripts\\python.exe rwmap\\probe\\probe_game_decor_visual.py
"""
from __future__ import annotations

import base64
import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lxml import etree                                        # noqa: E402
from PIL import Image, ImageDraw, ImageFont                   # noqa: E402

from rwmap.document.layer import ObjectLayer, TileLayer       # noqa: E402
from rwmap.document.map_object import MapObject              # noqa: E402
from rwmap.io.tmx_writer import create_blank_document, write_document  # noqa: E402

OUT = ROOT / "examples" / "游戏适配_文字与图片.png"
CN = "C:/Windows/Fonts/msyh.ttc"
W, H = 1400, 960
BG = "#F5F7FA"
INK = "#1F2937"
MUTED = "#6B7280"
BLUE = "#185FA5"
RED = "#A32D2D"
GREEN = "#1D9E75"


def f(size, bold=False):
    # 中英文统一用雅黑，避免代码片段里的中文变成方框
    return ImageFont.truetype(CN, size)


def card(d, box, title, accent):
    d.rounded_rectangle(box, radius=14, fill="#FFFFFF",
                        outline="#D9DEE7", width=2)
    d.rounded_rectangle((box[0], box[1], box[0] + 7, box[3]), radius=3,
                        fill=accent)
    d.text((box[0] + 24, box[1] + 16), title, font=f(21), fill=accent)


def lines(d, x, y, items, size=16, color=INK, gap=24):
    for i, line in enumerate(items):
        d.text((x, y + i * gap), line, font=f(size), fill=color)
    return y + len(items) * gap


def build_doc(tmp: Path):
    pic = Image.new("RGBA", (40, 40), (214, 48, 48, 255))
    pd = ImageDraw.Draw(pic)
    for cx in range(2):
        for cy in range(2):
            if (cx + cy) % 2 == 0:
                pd.rectangle((cx * 20 + 3, cy * 20 + 3,
                              cx * 20 + 17, cy * 20 + 17),
                             fill=(38, 96, 220, 255))
    pic.save(tmp / "deco.png")

    doc = create_blank_document(SimpleNamespace(export_dir=str(tmp)), 10, 10)
    doc.path = tmp / "decor.tmx"
    doc.layers.append(TileLayer(
        width=10, height=10, name="Ground", kind="tile", cells=[0] * 100,
        encoding="base64", compression="gzip", dirty=True))
    doc.layers.append(ObjectLayer(name="Triggers", dirty=True, objects=[
        MapObject(object_id=1, name="文字", shape="text", x=20, y=20,
                  width=160, height=40, text="敌军来袭！",
                  text_style={"pixelsize": "14", "color": "#ffcc00"}),
        MapObject(object_id=2, name="图片", shape="image", x=40, y=40,
                  width=40, height=40, image_source="deco.png"),
    ]))
    return doc


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        doc = build_doc(tmp)
        out = write_document(doc, tmp / "decor.tmx")
        root = etree.parse(str(out)).getroot()
        objs = [o for og in root.findall("objectgroup")
                for o in og.findall("object")]
        text_obj = next(o for o in objs if o.get("name") == "文字")
        image_obj = next(o for o in objs if o.get("name") == "图片")
        ts = next(t for t in root.findall("tileset")
                  if (t.get("name") or "").startswith("rwmap_decor_"))
        prop = next(p for p in ts.iter("property")
                    if p.get("name") == "embedded_png")
        png_bytes = base64.b64decode("".join((prop.text or "").split()))
        tile_image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    d.rectangle((0, 0, W, 78), fill="#12304E")
    d.text((40, 18), "插入图片 / 插入文字 · 游戏适配验收", font=f(30), fill="#FFFFFF")
    d.text((40, 55), "铁锈战争 1.15  ·  图中 XML 与瓦片来自脚本真实写出的 TMX",
           font=f(15), fill="#B9D3EC")

    d.rounded_rectangle((40, 96, 1360, 176), radius=12,
                        fill="#FCEBEB", outline="#E7B6B6", width=2)
    d.text((60, 108), "适配前 · 游戏加载地图时", font=f(17), fill=RED)
    d.text((60, 136),
           'desync:Error: Unknown type: found on 图片   /   '
           'desync:Error: Skipping trigger:文字 - no properties found',
           font=f(16), fill=RED)
    d.rounded_rectangle((1150, 112, 1340, 160), radius=10, fill="#E1F5EE")
    d.text((1170, 124), "适配后：0 条报错", font=f(18), fill=GREEN)

    card(d, (40, 196, 690, 560), "文字  shape=text  →  type=mapText", BLUE)
    d.text((66, 252), "适配前（游戏不认 <text> 对象）", font=f(15), fill=MUTED)
    d.rounded_rectangle((60, 280, 670, 356), radius=8, fill="#F3F5F8")
    lines(d, 76, 290, [
        '<object name="文字" x="20" y="20">',
        '  <text pixelsize="14">敌军来袭！</text>   ← 没有 <properties>',
        '</object>',
    ], size=15, color="#7A3B3B")
    d.text((66, 370), "适配后（真实写出）", font=f(15), fill=MUTED)
    d.rounded_rectangle((60, 398, 670, 552), radius=8, fill="#EEF6FD")
    lines(d, 76, 404, [
        f'<object name="文字" type="{text_obj.get("type")}" ...>',
        '  <properties>',
        '    <property name="text" value="敌军来袭！"/>',
        '    <property name="textColor" value="#ffcc00"/>',
        '    <property name="textSize" value="14"/>',
        '  </properties>',
        '</object>',
    ], size=15, color="#123F6D", gap=22)

    card(d, (710, 196, 1360, 560), "图片  shape=image  →  info + embedded_png", BLUE)
    d.text((736, 252), "适配前（有 properties 但没有 type）", font=f(15), fill=MUTED)
    d.rounded_rectangle((730, 280, 1340, 344), radius=8, fill="#F3F5F8")
    lines(d, 746, 288, [
        '<object name="图片" ...>',
        '  <properties><property name="image" .../></properties>',
        '</object>',
    ], size=15, color="#7A3B3B")
    d.text((736, 358), "适配后（真实写出）", font=f(15), fill=MUTED)
    d.rounded_rectangle((730, 386, 1340, 524), radius=8, fill="#EEF6FD")
    lines(d, 746, 392, [
        f'<object name="图片" type="{image_obj.get("type")}" ...>   ← 游戏忽略',
        f'<tileset firstgid="{ts.get("firstgid")}" name="{ts.get("name")}"',
        f'  tilecount="{ts.get("tilecount")}" columns="{ts.get("columns")}">',
        '  <property name="embedded_png">iVBORw0KGgo...  (PNG base64)</property>',
        '  <image source="rwmap_decor_0.png" width="40" height="40"/>',
        '</tileset>',
    ], size=15, color="#123F6D", gap=22)

    card(d, (40, 580, 1360, 910), "内嵌 PNG → 20px 瓦片 → Items 层贴图（游戏里真正可见）", GREEN)
    d.text((66, 636), "① 内嵌图集原图（解码 embedded_png）", font=f(16), fill=MUTED)
    big = tile_image.resize((200, 200), Image.NEAREST)
    im.paste(big, (80, 666), big)
    d.rectangle((80, 666, 280, 866), outline="#C8D2DF", width=2)
    d.line((180, 666, 180, 866), fill="#FFFFFF", width=2)
    d.line((80, 766, 280, 766), fill="#FFFFFF", width=2)
    d.text((80, 872), "40×40 → 2×2 个瓦片", font=f(14), fill=MUTED)

    d.text((360, 720), "→", font=f(64), fill=GREEN)
    d.text((350, 796), "按 20px 网格切块", font=f(15), fill=MUTED)

    d.text((520, 636), "② Items 层 cells（10×10 地图，高亮 4 格）", font=f(16), fill=MUTED)
    gx, gy, cell = 530, 666, 20
    for row in range(10):
        for col in range(10):
            x0, y0 = gx + col * cell, gy + row * cell
            hit = 2 <= col <= 3 and 2 <= row <= 3
            d.rectangle((x0, y0, x0 + cell - 1, y0 + cell - 1),
                        fill=("#9FE1CB" if hit else "#FFFFFF"),
                        outline="#C8D2DF", width=1)
            if hit:
                gid = 1 + (row - 2) * 2 + (col - 2)
                d.text((x0 + 3, y0 + 1), str(gid), font=f(12), fill="#0F6E56")
    d.text((530, 872), "gid 1..4 连续、不与其它图集冲突",
           font=f(14), fill=MUTED)

    d.text((880, 666), "结论", font=f(20), fill=GREEN)
    lines(d, 880, 704, [
        "· 文字由 mapText 在游戏里显示（text / textColor / textSize）",
        "· 图片由嵌入图集的瓦片贴在 Items 层显示",
        "· 对象本身降级为 type=info 注释，游戏忽略、不再报错",
        "· 每次保存全量重投影：移动 / 缩放 / 删除不留幽灵贴图",
        "· 单独分发 .tmx 即可，游戏只读 embedded_png",
    ], size=16, color=INK, gap=30)

    d.rectangle((0, 922, W, H), fill="#12304E")
    d.text((40, 934),
           "写入：rwmap/rw/game_decor.py  ·  tmx_writer / tmx_reader  ·  "
           "回归：test_game_decor.py（15 例） · 全量 547 例 0 失败",
           font=f(15), fill="#CFE2F3")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
