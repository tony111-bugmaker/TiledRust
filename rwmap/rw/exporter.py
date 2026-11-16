"""导出到 mods/maps，并补齐 tmx 内相对路径引用的资源副本。

assets/maps 官方图同目录没有资源目录，直接另存会让 Tiled 缺图，
因此导出时必须按 tmx 内记录的相对路径复制图集与 .tsx。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..io.tmx_writer import write_document
from ..utils.paths import as_path


def _copy(src: Path, dst: Path, copied: list[str]) -> None:
    if not src.is_file() or dst.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied.append(str(dst))


def export_map(doc, target_dir, profile=None, with_thumbnail: bool = True, target_name: str | None = None) -> dict:
    target_dir = as_path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    tmx_out = target_dir / (target_name or doc.path.name)
    write_document(doc, tmx_out)

    resolver = doc.resolver
    copied: list[str] = []
    missing: list[str] = []
    for ts in doc.tilesets:
        if ts.source:
            src = resolver.resolve_tsx(ts.source) if resolver else None
            if src:
                _copy(src, target_dir / ts.source, copied)
                # tsx 内的 image 相对 tsx 自身目录
                if ts.image_source:
                    img_src = resolver.resolve(ts.image_source, tsx_dir=src.parent)
                    rel = Path(ts.source).parent / ts.image_source
                    if img_src:
                        _copy(img_src, target_dir / rel, copied)
                    else:
                        missing.append(ts.image_source)
            else:
                missing.append(ts.source)
        elif ts.image_source:
            img_src = resolver.resolve(ts.image_source) if resolver else None
            if img_src:
                _copy(img_src, target_dir / ts.image_source, copied)
            else:
                missing.append(ts.image_source)

    result = {"tmx": str(tmx_out), "resources": len(copied), "missing": missing}
    if with_thumbnail and profile is not None:
        from .thumbnail import make_thumbnail
        result["thumbnail"] = make_thumbnail(tmx_out, profile)
    return result
