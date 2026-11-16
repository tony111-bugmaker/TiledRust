"""保真写回探针：未改动语义全等；改一格仅目标层 data 变化。"""
from __future__ import annotations

from pathlib import Path

from ..document.layer import TileLayer
from ..io.path_resolver import ResourceResolver
from ..io.tmx_reader import read_document
from ..io.tmx_writer import write_document
from ..rw.version import load_profile
from ..utils.paths import PROJECT_ROOT

SAMPLE = Path("<游戏目录1.15>/mods/maps/[p2]example_skirmish_(2p).tmx")
TMP = PROJECT_ROOT / "logs" / "probe_tmp"


def _cells(doc):
    return {
        layer.name: list(layer.cells)
        for layer in doc.layers if isinstance(layer, TileLayer)
    }


def _structure(doc):
    return {
        "map": {k: v for k, v in doc.root.attrib.items()},
        "layers": [(l.name, l.kind, l.opacity, l.visible) for l in doc.layers],
        "tilesets": [(t.firstgid, t.name, t.source, t.count) for t in doc.tilesets],
        "objects": [
            (o.name, o.type_, o.x, o.y, dict(o.properties.items()))
            for l in doc.layers if l.kind == "object" for o in l.objects
        ],
    }


def run() -> dict:
    TMP.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    profile = load_profile("1.15")
    if not SAMPLE.is_file():
        return {"name": "保真写回", "ok": False, "details": {}, "errors": [f"样本不存在: {SAMPLE}"]}

    original_bytes = SAMPLE.read_bytes()

    # 探针产物放在 logs/ 下，相对路径不同；统一用样本目录作资源基准，
    # 比较的是「写回保真」而不是新目录的资源发现能力。
    def _read(path):
        return read_document(path, profile, resolver=ResourceResolver(SAMPLE, profile))

    doc = _read(SAMPLE)
    plain_out = TMP / "roundtrip_plain.tmx"
    write_document(doc, plain_out)
    plain_bytes = plain_out.read_bytes()
    doc_plain = _read(plain_out)
    if _cells(doc) != _cells(doc_plain):
        errors.append("未改动写回：格数据不一致")
    if _structure(doc) != _structure(doc_plain):
        errors.append("未改动写回：结构/属性不一致")
    byte_equal = plain_bytes == original_bytes
    byte_equal_ignore_tail = plain_bytes.rstrip() == original_bytes.rstrip()

    doc2 = _read(SAMPLE)
    target = next((l for l in doc2.layers if isinstance(l, TileLayer) and l.name == "Ground"), None)
    if target is None:
        return {"name": "保真写回", "ok": False, "details": {}, "errors": ["样本缺少 Ground 层"]}
    old = target.get(10, 10)
    new = (old + 1) if old == 0 else 0
    doc2.set_cell(target, 10, 10, new)
    edited_out = TMP / "roundtrip_edited.tmx"
    write_document(doc2, edited_out)
    doc_edit = _read(edited_out)
    before, after = _cells(doc), _cells(doc_edit)
    changed = [
        (name, i)
        for name in before
        for i, (a, b) in enumerate(zip(before[name], after[name]))
        if a != b
    ]
    expected = [("Ground", 10 * target.width + 10)]
    if changed != expected:
        errors.append(f"改一格后变化位置不符: {changed[:5]}，期望 {expected}")
    if _structure(doc) != _structure(doc_edit):
        errors.append("改一格后结构发生变化")

    return {
        "name": "保真写回 + 单格编辑",
        "ok": not errors,
        "details": {
            "sample": str(SAMPLE),
            "byte_equal": byte_equal,
            "byte_equal_ignore_tail": byte_equal_ignore_tail,
            "original_size": len(original_bytes),
            "written_size": len(plain_bytes),
            "changed_cells": len(changed),
            "expected": expected,
            "edited_cell": {"col": 10, "row": 10, "old": old, "new": new},
        },
        "errors": errors,
    }
