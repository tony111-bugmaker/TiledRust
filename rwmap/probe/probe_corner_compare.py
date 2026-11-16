"""生成「内凹拐角」修复前后对比图。

用官方示例地图的图块集构造一片 L 形 + T 形草地伸进水里的地形，
分别用「修复后」与「修复前（外接矩形补通配）」的规则算出 Ground 并渲染。
"""
from __future__ import annotations

import copy
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from rwmap.document.gid import GID_MASK
from rwmap.io.tmx_reader import read_document
from rwmap.rw.automapping import AutoMapper
from rwmap.rw.version import load_profile
from rwmap.tilesets.image_loader import get_tile_image

WATER, GRASS = 186, 126
BOX = (38, 40, 72, 64)          # 展示区域
FOCUS = (43, 42, 55, 60)       # 内凹拐角特写
OUT = Path("logs/拐角_修复前后对比.png")


def build_scene(doc):
    """在 set 层画 L 形 + T 形草地，其余是水。"""
    s = doc.layer_by_name("set")
    for y in range(BOX[1], BOX[3] + 1):
        for x in range(BOX[0], BOX[2] + 1):
            s.cells[s.index(x, y)] = WATER

    def fill(x0, y0, x1, y1, gid):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                s.cells[s.index(x, y)] = gid

    fill(44, 42, 64, 46, GRASS)     # 顶部横条
    fill(44, 42, 48, 60, GRASS)     # 左侧竖条 → 与横条形成 L 形内凹角
    fill(40, 56, 68, 62, GRASS)     # 底部横条 → 与竖条形成 T 形内凹角
    return doc


def old_style_rules(mapper):
    """模拟修复前：把 2x2 外接矩形里缺的那一格补成 strict wildcard。"""
    for r in mapper.rules:
        if r.w == 2 and r.h == 2 and len(r.inputs) == 3:
            occupied = {(dx, dy) for dx, dy, _g, _t in r.inputs}
            gaps = [(dx, dy) for dy in range(2) for dx in range(2)
                    if (dx, dy) not in occupied]
            for dx, dy in gaps:
                r.wildcards.append((dx, dy, "set"))
            r.wildcards.sort()
    return mapper


def ground_for(base, mapper, emulate_old=False):
    doc = copy.deepcopy(base)
    ground = doc.layer_by_name("Ground")
    for i in range(len(ground.cells)):
        ground.cells[i] = 0
    m = copy.copy(mapper)
    m.rules = copy.deepcopy(mapper.rules)
    m._index_cache = None
    if emulate_old:
        old_style_rules(m)
    for target, entries in m.compute(doc, only_diff=False).items():
        layer = doc.layer_by_name(target)
        if layer is None:
            continue
        for x, y, gid in entries:
            layer.cells[layer.index(x, y)] = gid
    return doc


def render(doc, box, scale):
    tw, th = doc.tile_w, doc.tile_h
    x0, y0, x1, y1 = box
    canvas = Image.new("RGBA", ((x1 - x0 + 1) * tw, (y1 - y0 + 1) * th), (0, 0, 0, 255))
    for layer_name in ("set", "Ground"):
        layer = doc.layer_by_name(layer_name)
        if layer is None:
            continue
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                gid = int(layer.get(x, y)) & GID_MASK
                if not gid:
                    continue
                located = doc.local_tile(gid)
                if located is None:
                    continue
                ts, local_id = located
                tile = get_tile_image(ts, local_id, doc.resolver)
                if tile is None:
                    continue
                tile = tile.convert("RGBA")
                canvas.alpha_composite(tile, ((x - x0) * tw, (y - y0) * th))
    if scale != 1:
        canvas = canvas.resize((canvas.width * scale, canvas.height * scale),
                               Image.NEAREST)
    return canvas.convert("RGB")


def font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main():
    profile = load_profile("1.15")
    mapper = AutoMapper(profile)
    mapper.load()
    base = build_scene(read_document(Path(profile.sample_map), profile))
    before = ground_for(base, mapper, emulate_old=True)
    after = ground_for(base, mapper)

    scale = 2
    full_before = render(before, BOX, scale)
    full_after = render(after, BOX, scale)
    detail_before = render(before, FOCUS, 5)
    detail_after = render(after, FOCUS, 5)

    label = font(24)
    small = font(18)
    pad, gap, head = 20, 24, 46
    w = pad * 2 + gap + full_before.width * 2
    h = head + full_before.height + head + detail_before.height + pad + 30
    out = Image.new("RGB", (w, h), (24, 26, 30))
    draw = ImageDraw.Draw(out)
    draw.text((pad, 12), "修复前：拐角规则被外接矩形卡死，岸线在凹角断开",
              fill=(255, 150, 150), font=label)
    draw.text((pad + full_before.width + gap, 12),
              "修复后：严格按 L 形区域匹配，凹角正确包边",
              fill=(150, 240, 170), font=label)
    y = head
    out.paste(full_before, (pad, y))
    out.paste(full_after, (pad + full_before.width + gap, y))
    y += full_before.height + 12
    draw.text((pad, y), "内凹拐角特写", fill=(220, 220, 220), font=small)
    y += head - 12
    out.paste(detail_before, (pad, y))
    out.paste(detail_after, (pad + full_before.width + gap, y))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT)

    # 统计拐角瓦片数量，作为文字结论
    corners = {812, 814, 818, 820, 845, 847, 851, 853, 919, 920, 922, 923, 925}
    def count(doc):
        g = doc.layer_by_name("Ground")
        return sum(1 for y in range(BOX[1], BOX[3] + 1)
                   for x in range(BOX[0], BOX[2] + 1)
                   if (int(g.get(x, y)) & GID_MASK) in corners)
    print("拐角瓦片数  修复前:", count(before), " 修复后:", count(after))
    print("输出:", OUT.resolve(), out.size)


if __name__ == "__main__":
    main()
