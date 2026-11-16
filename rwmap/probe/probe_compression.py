"""层数据编解码矩阵：gzip / zlib / zstd(可选) / 无压缩 / csv。"""
from __future__ import annotations

from lxml import etree

from ..io.layer_codec import decode, encode

CELLS = [0, 1, 2, 3, 2147483648 | 5, 0xFFFFFFFF, 42, 7]


def _elem(encoding, compression):
    node = etree.Element("data")
    if encoding:
        node.set("encoding", encoding)
    if compression:
        node.set("compression", compression)
    return node


def run() -> dict:
    results = {}
    errors = []
    for compression in ("gzip", "zlib", "zstd", None):
        node = _elem("base64", compression)
        try:
            node.text = encode(CELLS, "base64", compression)
            decoded, enc, comp = decode(node, len(CELLS))
            ok = decoded == CELLS
            results[str(compression)] = {"ok": ok, "encoding": enc, "compression": comp}
            if not ok:
                errors.append(f"{compression}: 往返不一致")
        except Exception as exc:  # noqa: BLE001
            if compression == "zstd":
                results["zstd"] = {"ok": True, "skipped": "未安装 zstandard（可选）"}
            else:
                results[str(compression)] = {"ok": False, "error": str(exc)}
                errors.append(f"{compression}: {exc}")
    for encoding in ("csv",):
        node = _elem(encoding, None)
        try:
            node.text = encode(CELLS, encoding, None)
            decoded, _, _ = decode(node, len(CELLS))
            results[encoding] = {"ok": decoded == CELLS}
        except Exception as exc:  # noqa: BLE001
            results[encoding] = {"ok": False, "error": str(exc)}
            errors.append(f"{encoding}: {exc}")

    # magic 探测：声明 gzip 但载荷是 zlib，仍应正确解码
    node = _elem("base64", "gzip")
    node.text = encode(CELLS, "base64", "zlib")
    try:
        decoded, _, _ = decode(node, len(CELLS))
        results["magic_fallback"] = {"ok": decoded == CELLS}
        if decoded != CELLS:
            errors.append("magic 探测失败")
    except Exception as exc:  # noqa: BLE001
        results["magic_fallback"] = {"ok": False, "error": str(exc)}
        errors.append(f"magic 探测: {exc}")

    return {"name": "层数据编解码矩阵", "ok": not errors, "details": results, "errors": errors}
