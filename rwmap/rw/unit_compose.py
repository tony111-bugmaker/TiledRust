"""把铁锈单位 ini 的多张图片按坐标"拼"成一张近似游戏内外观的图。

规则不是猜的，是从 game-lib.jar 的 LegManager.draw()/update() 字节码反推的：
- [graphics] image        = 车身/主体（total_frames 取第 0 帧）
- [graphics] image_turret = 共享炮塔（SHARED:xxx.png -> assets/units/shared/）
- [turret_XXX] image/x/y  = 独立炮塔，x/y 是相对单位中心的偏移
- [leg_N]：
  * image_leg  = middleImage（腿身），image_foot = endImage（脚端）
  * 游戏默认朝向 90°，ini 的 x/y 与 attach_x/y 都要变换成屏幕坐标：
        foot   = (-x, y)          # update(): targetX/targetY
        attach = (-attach_x, attach_y)   # draw(): 旋转后的 attachX/Y
  * 腿身**不是整张缩放**：源矩形取图片垂直中段 y = halfHeight ± length，
        length = min(halfHeight, dist(foot, attach))；画在以脚为中心、高 2*length 的目标框里。
        画布旋转 angleBetweenPoints(foot, attach)+90（PIL 用 atan2(dx,dy) 等效）。
  * 脚端用 drawImageRotated 画在脚位置，角度 = leg.angle = 朝向 90° + endDirOffset。
  * 默认 drawFootOnTop=false，即脚画在腿身下面。
- copyFrom: xxx.ini / copyFrom: 1 = 继承别的 ini / 同文件另一个同名段
- 路径前缀：SHARED: -> assets/units/shared/，CORE: -> assets/，SHADOW: 是阴影不画

只读取，不修改游戏文件。"""
from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image


def parse_sections(text: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            out.setdefault(section, {})
            continue
        if not section or ":" not in line or s.startswith("#"):
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.split("#", 1)[0].strip()
        if value:
            out[section][key] = value
    return out


def load_sections(ini: Path, _seen: set[Path] | None = None) -> dict[str, dict[str, str]]:
    """解析 ini 并按 copyFrom 继承（父先、子覆盖），返回所有段。"""
    seen = set() if _seen is None else _seen
    if ini in seen or not ini.is_file():
        return {}
    seen.add(ini)
    try:
        data = parse_sections(ini.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return {}

    merged: dict[str, dict[str, str]] = {}
    parent = data.get("core", {}).get("copyfrom", "")
    if parent and parent.lower().endswith(".ini"):
        for p in (ini.parent / parent, ini.parent.parent / parent):
            if p.is_file():
                merged = load_sections(p, seen)
                break

    for name, sec in data.items():
        merged.setdefault(name, {}).update(sec)

    # 段内 copyFrom: 1 -> 复制本文件 [leg_1] / [turret_1] 的缺省值
    for name, sec in list(merged.items()):
        ref = sec.get("copyfrom", "")
        if ref.isdigit():
            m = re.match(r"([a-z_]+)_\d+$", name)
            if m:
                base = merged.get(f"{m.group(1)}_{ref}", {})
                for k, v in base.items():
                    sec.setdefault(k, v)
    return merged


def num(sec: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(sec.get(key, default))
    except (ValueError, TypeError):
        return default


def resolve_image(value: str, ini: Path, game_root: Path) -> Path | None:
    if not value or value.upper() in {"AUTO", "NONE", "SHADOW"}:
        return None
    if ":" in value:
        prefix, rest = value.split(":", 1)
        prefix = prefix.upper()
        if prefix == "SHARED":
            return game_root / "assets" / "units" / "shared" / Path(rest).name
        if prefix == "CORE":
            return game_root / "assets" / rest
        if prefix == "SHADOW":
            return None
        rest = value
    else:
        rest = value
    p = Path(rest)
    for cand in (ini.parent / p, game_root / "assets" / "units" / p):
        if cand.is_file():
            return cand
    return None


def load_frame(path: Path | None, frames: int = 1) -> Image.Image | None:
    if path is None or not path.is_file():
        return None
    try:
        with Image.open(path) as raw:
            img = raw.convert("RGBA")
    except OSError:
        return None
    fw = max(1, img.width // max(1, frames))
    return img.crop((0, 0, fw, img.height))


def _place(canvas: Image.Image, img: Image.Image, cx: float, cy: float) -> None:
    x = int(round(cx - img.width / 2))
    y = int(round(cy - img.height / 2))
    canvas.alpha_composite(img, (x, y))


def _leg_image(leg: Image.Image, foot: tuple[float, float],
               attach: tuple[float, float]) -> Image.Image:
    """复刻 LegManager.draw() 的 middleImage 绘制：

    1. 源矩形取腿图的**垂直中段**：y = halfHeight ± length
       length = min(halfHeight, dist(foot, attach))，不会整张缩放。
    2. 画布绕**脚的位置**旋转 angleBetweenPoints(foot, attach)+90。
    3. 目标矩形以脚为中心，高 2*length，即腿从脚往 attach 方向伸出。

    注意：游戏的 Canvas.rotate 是顺时针，PIL 的 rotate 是逆时针；
    angleBetweenPoints+90 直接搬到 PIL 会正好差 180°，腿条的钩会朝外。
    这里额外 +180 修正（用 units.tsx 官方头像逐像素对照验证过）。
    """
    fx, fy = foot
    ax, ay = attach
    dx, dy = ax - fx, ay - fy
    dist = math.hypot(dx, dy)
    half = leg.height / 2
    length = dist if dist < half - 2 else half
    if length < 1:
        return leg
    y0, y1 = int(half - length), int(round(half + length))
    y0 = max(0, min(y0, leg.height - 1))
    y1 = max(y0 + 1, min(y1, leg.height))
    piece = leg.crop((0, y0, leg.width, y1))
    deg = math.degrees(math.atan2(dx, dy)) + 180
    return piece.rotate(deg, resample=Image.BICUBIC, expand=True)


def compose(ini: Path, game_root: Path, pad: int = 12) -> tuple[Image.Image | None, list[dict]]:
    """返回 (合成图, 部件清单)。部件清单每项 {kind,name,path,x,y,copy}。"""
    secs = load_sections(ini)
    gfx = secs.get("graphics", {})
    frames = int(num(gfx, "total_frames", 1))

    parts: list[dict] = []
    body = load_frame(resolve_image(gfx.get("image", ""), ini, game_root), frames)
    if body is not None:
        parts.append({"kind": "主体", "name": gfx["image"], "path": str(ini.parent / gfx["image"]), "x": 0, "y": 0})

    turret_imgs: list[tuple[Image.Image, float, float, str]] = []
    shared_turret = load_frame(resolve_image(gfx.get("image_turret", ""), ini, game_root), frames)
    if shared_turret is not None:
        turret_imgs.append((shared_turret, 0, 0, gfx["image_turret"]))
        parts.append({"kind": "共享炮塔", "name": gfx["image_turret"], "path": gfx["image_turret"], "x": 0, "y": 0})
    for name, sec in secs.items():
        if not name.startswith("turret_") or "image" not in sec:
            continue
        im = load_frame(resolve_image(sec["image"], ini, game_root))
        if im is None:
            continue
        x, y = num(sec, "x"), num(sec, "y")
        turret_imgs.append((im, x, y, sec["image"]))
        parts.append({"kind": "炮塔", "name": sec["image"], "path": sec["image"], "x": x, "y": y})

    leg_imgs: list[tuple[Image.Image, float, float]] = []
    for name, sec in secs.items():
        if not name.startswith("leg_"):
            continue
        leg = load_frame(resolve_image(sec.get("image_leg", ""), ini, game_root))
        foot = load_frame(resolve_image(sec.get("image_foot", ""), ini, game_root))
        # 游戏默认朝向 f7=90°：targetX/targetY = (-ini_x, ini_y)
        # draw() 里 attach 同样 rotate 后为 (-attach_x, attach_y)
        ax, ay = -num(sec, "attach_x"), num(sec, "attach_y")
        fx, fy = -num(sec, "x"), num(sec, "y")
        if foot is not None:                # 默认 drawFootOnTop=false：脚在腿身下面
            # draw() 用 leg.angle(=朝向90°+endDirOffset) 画脚，PIL 与 Canvas 旋转方向相反
            end_offset = num(sec, "end_dir_offset")
            foot_draw = foot.rotate(-(90 + end_offset), resample=Image.BICUBIC, expand=True)
            leg_imgs.append((foot_draw, fx, fy))
            parts.append({"kind": "脚", "name": sec["image_foot"], "path": sec["image_foot"], "x": fx, "y": fy})
        if leg is not None:
            leg_imgs.append((_leg_image(leg, (fx, fy), (ax, ay)), fx, fy))
            parts.append({"kind": "腿", "name": sec["image_leg"], "path": sec["image_leg"], "x": ax, "y": ay})

    if not parts:
        return None, parts

    # 画布范围：所有部件的外接矩形
    boxes = []
    if body is not None:
        boxes.append((-body.width / 2, -body.height / 2, body.width / 2, body.height / 2))
    for im, x, y, *_ in [(t[0], t[1], t[2], t[3]) for t in turret_imgs] + leg_imgs:
        boxes.append((x - im.width / 2, y - im.height / 2, x + im.width / 2, y + im.height / 2))
    minx = min(b[0] for b in boxes) - pad
    miny = min(b[1] for b in boxes) - pad
    maxx = max(b[2] for b in boxes) + pad
    maxy = max(b[3] for b in boxes) + pad
    W, H = int(maxx - minx), int(maxy - miny)
    if W <= 0 or H <= 0 or W > 2000 or H > 2000:
        return None, parts
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ox, oy = -minx, -miny

    tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if body is not None:
        _place(canvas, body, ox, oy)
    for im, x, y in leg_imgs:            # 腿在身体下面
        _place(tmp, im, x + ox, y + oy)
    canvas = Image.alpha_composite(tmp, canvas)
    tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for im, x, y, *_ in turret_imgs:     # 炮塔在最上面
        _place(tmp, im, x + ox, y + oy)
    return Image.alpha_composite(canvas, tmp), parts


def main() -> None:
    root = Path(r"<游戏目录1.15>")
    out = Path(__file__).resolve().parent.parent / "logs" / "tmp_compose.png"
    targets = [
        root / "assets/units/experimental_spider/experimental_spider.ini",
        root / "assets/units/mechs_large/mech_artillery.ini",
        root / "assets/units/mechs_small/mech_gun.ini",
        root / "assets/units/tanks/artillery.ini",
    ]
    cards = []
    for ini in targets:
        img, parts = compose(ini, root)
        print(ini.name, "部件", len(parts), "合成图", img.size if img else None)
        if img is None:
            continue
        img = img.resize((img.width * 3, img.height * 3), Image.NEAREST)
        cards.append((ini.name, img))
    W = sum(c[1].width + 30 for c in cards) + 20
    H = max(c[1].height for c in cards) + 50
    sheet = Image.new("RGB", (W, H), (28, 32, 38))
    from PIL import ImageDraw
    d = ImageDraw.Draw(sheet)
    x = 20
    for name, img in cards:
        sheet.paste(img, (x, 10), img)
        d.text((x, H - 28), name, fill=(230, 235, 240))
        x += img.width + 30
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main()
