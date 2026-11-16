"""资源路径解析命中率（五级候选）。"""
from __future__ import annotations

from pathlib import Path

from ..io.tmx_reader import read_document
from ..rw.version import load_profile

SAMPLES = [
    Path("<游戏目录1.15>/mods/maps/[p2]example_skirmish_(2p).tmx"),
    Path("<游戏目录1.15>/mods/maps/embedded_custom_tileset_example.tmx"),
    Path("<游戏目录1.15>/assets/maps/skirmish/[p2]Big Island (2p).tmx"),
    Path("<游戏目录1.14>/assets/maps/skirmish/[p2]Big Island (2p).tmx"),
]


def run() -> dict:
    details = {"files": []}
    errors = []
    for path in SAMPLES:
        if not path.is_file():
            errors.append(f"样本不存在: {path}")
            continue
        version = "1.14" if "1.14" in str(path) else "1.15"
        doc = read_document(path, load_profile(version))
        resolver = doc.resolver
        checked = 0
        for ts in doc.tilesets:
            if ts.source:
                checked += 1
                if resolver.resolve_tsx(ts.source) is None:
                    errors.append(f"{path.name}: 外部 tsx 未命中 {ts.source}")
            if ts.image_source:
                checked += 1
                hit = resolver.resolve(ts.image_source, tsx_dir=ts.base_dir if ts.is_external else None)
                if hit is None:
                    errors.append(f"{path.name}: 图集未命中 {ts.image_source}")
        details["files"].append({
            "file": path.name,
            "tilesets": len(doc.tilesets),
            "checked": checked,
            "misses": list(resolver.misses)[:5],
            "hit_rate": round(resolver.hit_rate, 4),
        })
    return {
        "name": "资源路径五级候选命中率",
        "ok": not errors,
        "details": details,
        "errors": errors[:20],
    }
