"""世界格坐标 <-> 屏幕坐标换算（参考 RTS游戏/game/camera.py 的逻辑）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Camera:
    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    tile_w: int = 20
    tile_h: int = 20

    def to_screen(self, col: int, row: int) -> tuple[float, float]:
        return (
            col * self.tile_w * self.scale + self.offset_x,
            row * self.tile_h * self.scale + self.offset_y,
        )

    def to_world(self, sx: float, sy: float) -> tuple[int, int]:
        return (
            int((sx - self.offset_x) / (self.tile_w * self.scale)),
            int((sy - self.offset_y) / (self.tile_h * self.scale)),
        )

    def clamp_scale(self, value: float) -> float:
        # 160x160 的地图 fitInView 后约 0.25，下限不能卡在 0.25，
        # 否则首次滚轮会被「拉回」到 0.25，看起来像乱跳。
        return max(0.05, min(8.0, value))
