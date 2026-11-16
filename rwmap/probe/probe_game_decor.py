"""探针：文字 / 图片对象的游戏侧适配（写盘 -> 读回 -> 二次写盘）。

跑法：
    .venv\\Scripts\\python.exe rwmap\\probe\\probe_game_decor.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lxml import etree                                       # noqa: E402
from PIL import Image, ImageDraw                              # noqa: E402

from rwmap.document.layer import ObjectLayer, TileLayer      # noqa: E402
from rwmap.document.map_object import MapObject              # noqa: E402
from rwmap.rw.version import load_profile                   # noqa: E402
from rwmap.io.layer_codec import decode                      # noqa: E402
from rwmap.io.tmx_reader import read_document                # noqa: E402
from rwmap.io.tmx_writer import create_blank_document, write_document  # noqa: E402


def build(tmp: Path):
    # 一张 40x40 的测试图（2x2 个 20px 瓦片）
    pic = Image.new("RGBA", (40, 40), (220, 40, 40, 255))
    ImageDraw.Draw(pic).rectangle((8, 8, 31, 31), fill=(40, 90, 220, 255))
    pic.save(tmp / "deco.png")

    doc = create_blank_document(SimpleNamespace(export_dir=str(tmp)), 10, 10)
    doc.path = tmp / "游戏适配.tmx"
    doc.layers.append(TileLayer(
        width=10, height=10, name="Ground", kind="tile",
        cells=[0] * 100, encoding="base64", compression="gzip", dirty=True))
    doc.layers.append(ObjectLayer(name="Triggers", dirty=True, objects=[
        MapObject(object_id=1, name="文字", shape="text", x=20, y=20,
                  width=160, height=40, text="敌军来袭！",
                  text_style={"fontfamily": "sans-serif", "pixelsize": "14",
                              "color": "#ffcc00", "bold": "1"}),
        MapObject(object_id=2, name="图片", shape="image", x=40, y=40,
                  width=40, height=40, image_source="deco.png"),
    ]))
    return doc


def inspect(path: Path, label: str):
    root = etree.parse(str(path)).getroot()
    print(f"\n=== {label} ===")
    for og in root.findall("objectgroup"):
        for o in og.findall("object"):
            props = {p.get("name"): (p.get("value") or p.text or "").strip()[:30]
                     for p in o.iter("property")}
            kids = [c.tag for c in o if isinstance(c.tag, str)]
            print("  object", o.get("name"), "type=", o.get("type"),
                  "children=", kids, "props=", props)
    decor = [t for t in root.findall("tileset")
             if (t.get("name") or "").startswith("rwmap_decor_")]
    print("  decor tilesets:", len(decor))
    for t in decor:
        p = next(p for p in t.iter("property") if p.get("name") == "embedded_png")
        print("   ", dict(t.attrib), "embedded_png_len=", len((p.text or "").strip()))
    for layer in root.findall("layer"):
        if layer.get("name") == "Items":
            data = layer.find("data")
            cells, enc, comp = decode(data, 100)
            nz = [(i, g) for i, g in enumerate(cells) if g]
            print("  Items data:", enc, comp, "non-zero cells:", nz[:8])
    return root


def main():
    profile = load_profile("1.15")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        doc = build(tmp)
        out = tmp / "游戏适配.tmx"
        write_document(doc, out)
        inspect(out, "第一次写盘")
        print("  内存 Items cells 是否干净:",
              all(g == 0 for g in doc.layer_by_name("Items").cells))

        again = read_document(out, profile)
        shapes = [o.shape for l in again.layers
                  if isinstance(l, ObjectLayer) for o in l.objects]
        texts = [o.text for l in again.layers
                 if isinstance(l, ObjectLayer) for o in l.objects if o.shape == "text"]
        srcs = [o.image_source for l in again.layers
                if isinstance(l, ObjectLayer) for o in l.objects if o.shape == "image"]
        print("  读回 shapes:", shapes, "texts:", texts, "image_source:", srcs)

        # 模拟用户把图片移到别处：重投影不能留下幽灵贴图
        for layer in again.layers:
            if isinstance(layer, ObjectLayer):
                for o in layer.objects:
                    if o.shape == "image":
                        o.x, o.y = 100, 100
                        layer.dirty = True
        write_document(again, out)
        root = inspect(out, "移动图片后二次写盘")
        decor = [t for t in root.findall("tileset")
                 if (t.get("name") or "").startswith("rwmap_decor_")]
        assert len(decor) == 1, f"装饰图集重复了: {len(decor)}"
        assert shapes == ["text", "image"], shapes
        assert texts == ["敌军来袭！"], texts
        assert srcs == ["deco.png"], srcs
        print("\nOK：文字转 mapText、图片转内嵌图集贴图、读回还原、重投影均正常")


if __name__ == "__main__":
    main()
