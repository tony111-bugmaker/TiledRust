"""PNG 装载、color-key 透明、按 spacing/margin/columns 切图。

trans="008a76" 是色键而非 alpha 通道；units.tsx 无 trans 且 spacing/margin=1。
切图参数按每个 tileset 独立读取，不写死。
"""
from __future__ import annotations

from PIL import Image, ImageChops

from ..document.tileset import Tileset
from ..utils.paths import as_path
from .embedded_png import decode_embedded_png

_CACHE: dict[str, dict[int, Image.Image]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _color_key(trans: str | None) -> tuple[int, int, int] | None:
    value = (trans or "").lstrip("#")
    if len(value) != 6:
        return None
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return None


def _apply_trans(img: Image.Image, trans: str | None) -> Image.Image:
    """色键转 alpha：整张图用 C 级 ImageChops 运算，避免逐像素 Python 循环。"""
    key = _color_key(trans)
    if key is None:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    diff = ImageChops.lighter(
        ImageChops.lighter(
            ImageChops.difference(r, Image.new("L", img.size, key[0])),
            ImageChops.difference(g, Image.new("L", img.size, key[1])),
        ),
        ImageChops.difference(b, Image.new("L", img.size, key[2])),
    )
    # diff==0 的像素就是色键；其余像素保留原 alpha。
    keep = diff.point(lambda v: 0 if v == 0 else 255)
    img.putalpha(ImageChops.multiply(a, keep))
    return img


def load_tileset_image(ts: Tileset, resolver=None) -> Image.Image | None:
    """返回 RGBA 图集，失败返回 None（调用方显示占位图）。"""
    if ts.embedded_png:
        decoded = decode_embedded_png(ts.embedded_png)
        return _apply_trans(decoded, ts.trans) if decoded is not None else None
    if not ts.image_source or resolver is None:
        return None
    tsx_dir = ts.base_dir if ts.is_external else None
    path = resolver.resolve(ts.image_source, tsx_dir=tsx_dir)
    if path is None:
        return None
    try:
        with Image.open(path) as fh:
            img = fh.convert("RGBA")
    except OSError:
        return None
    return _apply_trans(img, ts.trans)


def image_grid(img: Image.Image, ts: Tileset) -> tuple[int, int]:
    """(列数, 行数)，尊重 margin/spacing；columns 缺失时按 Tiled 规则推。"""
    step_x = max(1, ts.tile_w + ts.spacing)
    step_y = max(1, ts.tile_h + ts.spacing)
    cols = ts.columns or max(1, (img.width - 2 * ts.margin + ts.spacing) // step_x)
    rows = max(1, (img.height - 2 * ts.margin + ts.spacing) // step_y)
    return cols, rows


def slice_tiles(img: Image.Image, ts: Tileset) -> dict[int, Image.Image]:
    cols, rows = image_grid(img, ts)
    out: dict[int, Image.Image] = {}
    count = ts.count or cols * rows
    for local_id in range(min(count, cols * rows)):
        col, row = local_id % cols, local_id // cols
        x = ts.margin + col * (ts.tile_w + ts.spacing)
        y = ts.margin + row * (ts.tile_h + ts.spacing)
        if x + ts.tile_w > img.width or y + ts.tile_h > img.height:
            break
        out[local_id] = img.crop((x, y, x + ts.tile_w, y + ts.tile_h))
    return out


def get_tile_image(ts: Tileset, local_id: int, resolver=None) -> Image.Image | None:
    key = (
        f"{ts.source_path or ts.base_dir}|{ts.firstgid}|{ts.name}|{ts.trans}|"
        f"{ts.tile_w}x{ts.tile_h}|{ts.columns}|{ts.spacing}|{ts.margin}|"
        f"{len(ts.embedded_png or '')}"
    )
    if key not in _CACHE:
        img = load_tileset_image(ts, resolver)
        if img is None:
            return None
        _CACHE[key] = slice_tiles(img, ts)
    return _CACHE[key].get(local_id)
