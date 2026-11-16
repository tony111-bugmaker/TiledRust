"""图集文档（独立 .tsx 的运行时模型）。

与 MapDocument 平级，是「制作图集」产出的另一种文档：一个图块集文件，
不画画，只维护每个瓦片的属性（见 ``widgets/tileset_editor``）。可直接作为
多文档标签打开。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .tileset import Tileset

if TYPE_CHECKING:  # 避免循环导入
    from .version_profile import VersionProfile


@dataclass
class TilesetDocument:
    path: Path = Path(".")
    tileset: Tileset | None = None
    # 当前图片的绝对路径：首次由「新建图集」对话框设定；保存时据此复制到 .tsx 旁。
    image_path: Path | None = None
    profile: "VersionProfile | None" = None
    dirty: bool = False

    def save(self, path=None) -> Path:
        """写回 .tsx（含复制图片）。path 省略则写回 self.path。"""
        from ..io.tsx_writer import write_tileset

        target = Path(path) if path is not None else self.path
        if self.tileset is None or self.image_path is None:
            raise RuntimeError("图集尚未初始化，无法保存")
        out = write_tileset(self.tileset, self.image_path, target)
        self.path = out
        self.dirty = False
        self.tileset.source_path = out
        return out

    @property
    def name(self) -> str:
        if self.tileset is not None and self.tileset.name:
            return self.tileset.name
        return self.path.stem or "新图集"
