"""XML 保真读写：保留注释节点、属性顺序、空白文本，不生成新注释。"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

DEFAULT_DECL = b'<?xml version="1.0" encoding="UTF-8"?>\n'


def parse_tmx(path):
    parser = etree.XMLParser(
        remove_blank_text=False,
        strip_cdata=False,
        huge_tree=True,
        resolve_entities=False,
    )
    tree = etree.parse(str(path), parser)
    return tree, tree.getroot()


def read_declaration(path) -> bytes:
    try:
        with open(path, "rb") as fh:
            first = fh.readline()
        if first.lstrip().startswith(b"<?xml"):
            return first if first.endswith(b"\n") else first + b"\n"
    except OSError:
        pass
    return DEFAULT_DECL


def serialize(tree, target: Path, original_path=None) -> None:
    """以原始 XML 声明 + 原样 DOM 写出，未改动部分字节保持一致。"""
    decl = read_declaration(original_path) if original_path else DEFAULT_DECL
    body = etree.tostring(tree.getroot(), encoding="UTF-8", xml_declaration=False)
    if not body.endswith(b"\n"):
        body += b"\n"
    Path(target).write_bytes(decl + body)


def iter_elements(elem):
    """跳过注释 / 处理指令节点。"""
    for node in elem:
        if isinstance(getattr(node, "tag", None), str):
            yield node
