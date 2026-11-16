"""embedded_png：tileset 的字符串属性，值是 base64 PNG（无 <image> 时使用）。"""
from __future__ import annotations

import base64
import io
import re
import struct

from PIL import Image

from ..document.properties import PropertyBag

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def extract_embedded_png(props: PropertyBag) -> str | None:
    value = props.get("embedded_png")
    return value if value else None


def png_size(data: bytes) -> tuple[int, int] | None:
    """只读 PNG IHDR，不解码像素（TSX 常缺 width/height）。"""
    if len(data) >= 24 and data[:8] == _PNG_MAGIC:
        return struct.unpack(">II", data[16:24])
    return None


def embedded_png_size(value: str) -> tuple[int, int] | None:
    clean = re.sub(r"\s+", "", value or "")
    if not clean:
        return None
    try:
        head = base64.b64decode(clean[:64])
    except (ValueError, base64.binascii.Error):
        return None
    return png_size(head)


def decode_embedded_png(value: str):
    clean = re.sub(r"\s+", "", value or "")
    if not clean:
        return None
    raw = base64.b64decode(clean)
    return Image.open(io.BytesIO(raw)).convert("RGBA")
