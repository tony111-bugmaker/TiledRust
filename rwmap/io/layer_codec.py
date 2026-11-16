"""TMX 层数据编解码。

实测：官方 assets/maps 168 张图 640 个 <data> 全为 base64+gzip；
mods/maps/embedded_custom_tileset_example.tmx 为 base64+zlib。
读入用 magic 探测兼容四种，写出沿用原文件压缩格式。
"""
from __future__ import annotations

import base64
import gzip
import struct
import zlib

try:
    import zstandard as _zstd
except ImportError:
    _zstd = None


def _decompress(payload: bytes, declared: str | None) -> bytes:
    if payload[:2] == b"\x1f\x8b":
        return gzip.decompress(payload)
    if payload[:4] == b"\x28\xb5\x2f\xfd":
        if _zstd is None:
            raise RuntimeError("需要 zstandard 包才能读取 zstd 压缩层数据")
        return _zstd.ZstdDecompressor().decompress(payload)
    if payload[:1] == b"\x78":
        return zlib.decompress(payload)
    if declared in ("gzip", "zlib", "zstd"):
        if declared == "gzip":
            return gzip.decompress(payload)
        if declared == "zlib":
            return zlib.decompress(payload)
        if _zstd is None:
            raise RuntimeError("需要 zstandard 包才能读取 zstd 压缩层数据")
        return _zstd.ZstdDecompressor().decompress(payload)
    return payload  # 裸 uint32 数组


def decode(data_elem, expected: int | None = None):
    """返回 (cells, encoding, compression)。"""
    encoding = data_elem.get("encoding")
    compression = data_elem.get("compression")
    text = data_elem.text or ""

    if encoding is None:
        cells = []
        for child in data_elem:
            if isinstance(getattr(child, "tag", None), str) and child.tag == "tile":
                cells.append(int(child.get("gid", "0")) & 0xFFFFFFFF)
    elif encoding == "csv":
        cells = [int(part.strip()) & 0xFFFFFFFF for part in text.split(",") if part.strip()]
    elif encoding == "base64":
        payload = base64.b64decode("".join(text.split()))
        payload = _decompress(payload, compression)
        count = len(payload) // 4
        cells = list(struct.unpack(f"<{count}I", payload[: count * 4]))
    else:
        raise ValueError(f"不支持的层数据编码: {encoding}")

    if expected is not None and len(cells) != expected:
        if len(cells) < expected:
            cells.extend([0] * (expected - len(cells)))
        else:
            cells = cells[:expected]
    return cells, encoding, compression


def encode(cells, encoding: str | None = "base64", compression: str | None = "gzip") -> str:
    """返回应写入 <data> 元素的文本。"""
    if encoding is None:
        return ""  # XML <tile gid> 形式由调用方单独处理
    if encoding == "csv":
        return ",".join(str(int(c) & 0xFFFFFFFF) for c in cells)

    payload = struct.pack(f"<{len(cells)}I", *[int(c) & 0xFFFFFFFF for c in cells])
    if compression == "gzip":
        payload = gzip.compress(payload, mtime=0)
    elif compression == "zlib":
        payload = zlib.compress(payload, 9)
    elif compression == "zstd":
        if _zstd is None:
            raise RuntimeError("需要 zstandard 包才能写 zstd 压缩层数据")
        payload = _zstd.ZstdCompressor().compress(payload)
    elif compression not in (None, ""):
        raise ValueError(f"不支持的压缩格式: {compression}")
    return base64.b64encode(payload).decode("ascii")
