"""自动定位本机铁锈战争游戏本体。"""
from __future__ import annotations

from pathlib import Path


def locate_games(roots: list[Path] | None = None) -> dict[str, Path]:
    """扫描各磁盘根目录下的 Rusted Warfare* 目录，按目录名推断版本号。"""
    if roots is None:
        drive = Path("D:/")
        roots = sorted(drive.glob("Rusted Warfare*")) if drive.is_dir() else []
    found: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        if not (root / "assets" / "maps").is_dir():
            continue
        name = root.name
        if "1.14" in name:
            found["1.14"] = root
        elif "1.15" in name:
            found["1.15"] = root
        else:
            found.setdefault("unknown", root)
    return found
