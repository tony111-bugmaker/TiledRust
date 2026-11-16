"""资源路径五级候选解析。

官方 assets/maps 同目录没有资源目录，PNG 平铺在 assets/tilesets/bitmaps/，
直接按 Tiled 语义相对 tmx 解析会落空，因此必须兜底。
"""
from __future__ import annotations

from pathlib import Path

from ..utils.paths import as_path


class ResourceResolver:
    def __init__(self, tmx_path, profile=None, game_root=None, tilesets_root=None, mods_maps=None):
        self.tmx_dir = as_path(tmx_path).parent
        self.profile = profile
        self.tilesets_root = as_path(tilesets_root or (profile.tilesets_root if profile else self.tmx_dir))
        self.mods_maps = as_path(mods_maps or (profile.mods_maps if profile else self.tmx_dir))
        self.game_root = as_path(game_root or (profile.game_root if profile else self.tmx_dir))
        self.misses: list[str] = []
        self.hits: dict[str, str] = {}
        self.tried: int = 0

    def candidates(self, source: str, tsx_dir=None) -> list[Path]:
        src = as_path(source)
        out = [self.tmx_dir / src]
        if tsx_dir is not None:
            out.append(as_path(tsx_dir) / src)
        out.append(self.tilesets_root / src)
        out.append(self.tilesets_root / "bitmaps" / src.name)
        out.append(self.mods_maps / src)
        seen, unique = set(), []
        for item in out:
            key = str(item)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def resolve(self, source: str, tsx_dir=None) -> Path | None:
        key = f"{tsx_dir or ''}|{source}"
        if key in self.hits:
            return Path(self.hits[key])
        for candidate in self.candidates(source, tsx_dir):
            self.tried += 1
            if candidate.is_file():
                self.hits[key] = str(candidate)
                return candidate
        if source not in self.misses:
            self.misses.append(source)
        return None

    def resolve_tsx(self, source: str) -> Path | None:
        """外部 .tsx 专用：tmx 目录 / tilesets_root / mods_maps / assets/maps。"""
        src = as_path(source)
        candidates = [
            self.tmx_dir / src,
            self.tilesets_root / src,
            self.mods_maps / src,
            self.game_root / "assets" / "maps" / src,
        ]
        seen, unique = set(), []
        for item in candidates:
            if str(item) not in seen:
                seen.add(str(item))
                unique.append(item)
        for candidate in unique:
            if candidate.is_file():
                return candidate
        if source not in self.misses:
            self.misses.append(source)
        return None

    @property
    def hit_rate(self) -> float:
        total = len(self.hits) + len(self.misses)
        return 1.0 if total == 0 else len(self.hits) / total
