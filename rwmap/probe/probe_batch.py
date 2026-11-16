"""批量解析回归：1.14 + 1.15 共 168 张官方地图。"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..document.layer import ObjectLayer, TileLayer
from ..io.tmx_reader import read_document
from ..rw.version import load_profile

ROOTS = {
    "1.14": Path("<游戏目录1.14>/assets/maps"),
    "1.15": Path("<游戏目录1.15>/assets/maps"),
}


def run() -> dict:
    details = {"versions": {}, "totals": Counter()}
    errors: list[str] = []
    for version, root in ROOTS.items():
        if not root.is_dir():
            errors.append(f"缺少目录: {root}")
            details["versions"][version] = {"files": 0}
            continue
        profile = load_profile(version)
        files = sorted(root.rglob("*.tmx"))
        stat = Counter()
        layer_names: Counter = Counter()
        map_info_types: Counter = Counter()
        for path in files:
            try:
                doc = read_document(path, profile)
            except Exception as exc:  # noqa: BLE001 - 探针需要收集全部失败
                errors.append(f"{path.name}: {exc}")
                continue
            stat["parsed"] += 1
            stat["tilesets"] += len(doc.tilesets)
            stat["embedded"] += sum(1 for t in doc.tilesets if not t.is_external)
            stat["external"] += sum(1 for t in doc.tilesets if t.is_external)
            stat["flipped"] += doc.stats()["flipped_gids"]
            for layer in doc.layers:
                layer_names[f"{layer.kind}:{layer.name}"] += 1
                if isinstance(layer, TileLayer):
                    stat[f"compression_{layer.compression or 'none'}"] += 1
                if isinstance(layer, ObjectLayer):
                    for obj in layer.objects:
                        if obj.name == "map_info" and "type" in obj.properties:
                            map_info_types[obj.properties.get("type")] += 1
        details["versions"][version] = {
            "files": len(files),
            "parsed": stat["parsed"],
            "compression": {k.replace("compression_", ""): v for k, v in stat.items() if k.startswith("compression_")},
            "tilesets": stat["tilesets"],
            "embedded": stat["embedded"],
            "external": stat["external"],
            "flipped_gids": stat["flipped"],
            "layers": dict(layer_names),
            "map_info_types": dict(map_info_types),
        }
        for key in ("files", "parsed"):
            details["totals"][key] += details["versions"][version].get(key, 0)

    ok = (
        not errors
        and all(v.get("files") == 84 and v.get("parsed") == 84 for v in details["versions"].values())
        and all("gzip" in v.get("compression", {}) for v in details["versions"].values())
    )
    return {
        "name": "批量解析回归（168 张官方图）",
        "ok": ok,
        "details": {"versions": details["versions"], "totals": dict(details["totals"])},
        "errors": errors[:20],
    }
