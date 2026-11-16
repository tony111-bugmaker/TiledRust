"""版本配置（1.14 / 1.15）。路径统一用正斜杠，pathlib 可直接使用。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..utils.paths import CONFIG_DIR


@dataclass
class VersionProfile:
    version_id: str
    display_name: str = ""
    game_root: Path = Path(".")
    tilesets_root: Path = Path(".")
    mods_maps: Path = Path(".")
    export_dir: Path = Path(".")
    tiled_exe: Path = Path(".")
    rasterizer_exe: Path = Path(".")
    units_sources: list[str] = field(default_factory=list)
    compression_default: str = "gzip"
    write_enabled: bool = True
    extra_readonly_layers: list[str] = field(default_factory=list)
    sample_map: Path = Path(".")
    raw: dict = field(default_factory=dict)

    def expand(self, text: str) -> str:
        mapping = {
            "<game_root>": str(self.game_root),
            "<tilesets_root>": str(self.tilesets_root),
            "<mods_maps>": str(self.mods_maps),
            "<export_dir>": str(self.export_dir),
        }
        for key, value in mapping.items():
            text = text.replace(key, value)
        return text

    @classmethod
    def load(cls, version_id: str, config_dir: Path | None = None) -> "VersionProfile":
        base = config_dir or CONFIG_DIR
        path = base / "profiles" / f"rw{version_id.replace('.', '')}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            version_id=data.get("version_id", version_id),
            display_name=data.get("display_name", version_id),
            game_root=Path(data["game_root"]),
            tilesets_root=Path(data["tilesets_root"]),
            mods_maps=Path(data["mods_maps"]),
            export_dir=Path(data["export_dir"]),
            tiled_exe=Path(data["tiled_exe"]),
            rasterizer_exe=Path(data["rasterizer_exe"]),
            units_sources=data.get("units_sources", []),
            compression_default=data.get("compression_default", "gzip"),
            write_enabled=bool(data.get("write_enabled", True)),
            extra_readonly_layers=data.get("extra_readonly_layers", []),
            sample_map=Path(data.get("sample_map", "")),
            raw=data,
        )


def available_profiles(config_dir: Path | None = None) -> list[str]:
    base = config_dir or CONFIG_DIR
    return sorted(p.stem.replace("rw", "") for p in (base / "profiles").glob("rw*.json"))
