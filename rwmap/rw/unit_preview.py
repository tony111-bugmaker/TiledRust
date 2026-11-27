"""单位外观预览小图标（随机单位选择器左侧用）。

数据源优先级（都来自游戏本体 assets，不依赖编辑器自己维护的图片）：
1. ``units.tsx`` 图集里 tile 的 ``unit`` 属性 —— 游戏建造菜单用的 20×20
   单位头像，最准确（heavyTank / artillery / builder 等 60 个）。
2. ``assets/units/**/*.ini`` 的 ``[core] name`` + ``[graphics] image`` ——
   精灵条主图，取第一帧缩放。覆盖机甲 / 虫族等 tsx 没有的单位。
   若该 ini 有腿 / 炮塔多部件（含 copyFrom 继承），改用与软件内图鉴相同的
   拼装结果，和离线图鉴看到的完整外观一致。
3. 都没有 -> 中性占位图（分类首字 / 问号），不影响列表可用性。

图标按需加载并缓存；本模块只依赖 PIL，转 QPixmap 时才导入 PySide6。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..io.path_resolver import ResourceResolver
from ..io.tsx_reader import read_tsx
from .unit_compose import compose, load_sections
from .version import active_profile

ICON_SIZE = 22          # 列表左侧预览框边长（像素）
_FALLBACK_BG = (28, 32, 38, 255)
_FALLBACK_FG = (150, 160, 172, 255)

# 进程级缓存：raw -> Image.Image(ICON_SIZE×ICON_SIZE RGBA)
_CACHE: dict[str, Image.Image | None] = {}
_INDEX_READY = False
_TSX_ICON: dict[str, int] = {}                    # raw -> tile local_id
_SPRITE: dict[str, tuple[Path, str, int]] = {}    # raw -> (ini目录, image名, total_frames)
_TS = None
_RESOLVER = None


@dataclass(frozen=True)
class _SpriteInfo:
    directory: Path
    image: str
    frames: int
    ini: Path | None = None
    multi: bool = False


def _snake(text: str) -> str:
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text).lower()


def _find_game_root() -> Path | None:
    try:
        return Path(active_profile().game_root)
    except Exception:
        return None


def _read_tsx_icons(profile) -> dict[str, int]:
    """读 units.tsx 的 (raw -> tile id)，team0 / 第一个优先。"""
    out: dict[str, int] = {}
    for src in profile.units_sources:
        expanded = profile.expand(src)
        if not expanded.lower().endswith(".tsx"):
            continue
        path = Path(expanded)
        if not path.is_file():
            continue
        try:
            resolver = ResourceResolver(Path("__unit_preview__.tmx"), profile)
            ts = read_tsx(path, 1, resolver=resolver)
        except Exception:
            continue
        for local_id, props in sorted(ts.tiles.items()):
            raw = props.get("unit")
            if not raw:
                continue
            team = str(props.get("team", "") or "")
            # team0 优先：后面的 team0 覆盖前面的其它队伍
            if raw not in out or team == "0":
                out[raw] = local_id
        break
    return out


def _read_ini_core_and_graphics(text: str) -> tuple[str, str, int]:
    """返回 (name, image, total_frames)——只认 [core] 和 [graphics] 两段。"""
    name = image = ""
    frames = 1
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip().lower(), value.split("#", 1)[0].strip()
        if not value or value.upper() == "NONE":
            continue
        if section == "core" and key == "name" and not name:
            name = value
        elif section == "graphics":
            if key == "image" and not image:
                image = value
            elif key == "total_frames":
                try:
                    frames = max(1, int(float(value)))
                except ValueError:
                    pass
    return name, image, frames


def _read_sprites(root: Path) -> dict[str, _SpriteInfo]:
    """扫描 assets/units 下所有 ini，按 [core] name 建 raw -> 主图信息。"""
    out: dict[str, _SpriteInfo] = {}
    for ini in root.rglob("*.ini"):
        try:
            name, image, frames = _read_ini_core_and_graphics(
                ini.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if not name or not image or ":" in image:   # 跳过 SHARED:blk.png 这类引用
            continue
        # 腿 / 炮塔（含 copyFrom 继承的）用小图看不出结构，改用拼装图
        try:
            secs = load_sections(ini)
        except Exception:
            secs = {}
        multi = any(k.startswith("leg_") or k.startswith("turret_") for k in secs)
        # 同名多版本（c_tank / tank / 大小变体）时取先出现的，通常就是主版本
        out.setdefault(name, _SpriteInfo(ini.parent, image, frames, ini, multi))
    return out


def _ensure_index() -> None:
    global _INDEX_READY, _TSX_ICON, _SPRITE, _TS, _RESOLVER
    if _INDEX_READY:
        return
    _INDEX_READY = True
    try:
        profile = active_profile()
        _RESOLVER = ResourceResolver(Path("__unit_preview__.tmx"), profile)
        _TSX_ICON = _read_tsx_icons(profile)
    except Exception:
        _TSX_ICON = {}
        _RESOLVER = None
    root = _find_game_root()
    _SPRITE = _read_sprites(root / "assets" / "units") if root and (root / "assets" / "units").is_dir() else {}


def _tsx_image(raw: str) -> Image.Image | None:
    """从 units.tsx 图集取 20×20 头像。"""
    from ..tilesets.image_loader import get_tile_image
    global _TS
    local_id = _TSX_ICON.get(raw)
    if local_id is None:
        return None
    if _TS is None:
        try:
            profile = active_profile()
            path = Path(profile.expand(profile.units_sources[0]))
            _TS = read_tsx(path, 1, resolver=_RESOLVER)
        except Exception:
            return None
    try:
        return get_tile_image(_TS, local_id, _RESOLVER)
    except Exception:
        return None


def _sprite_image(raw: str) -> Image.Image | None:
    """从精灵条取第一帧（含 debug 别名兜底：mechFlame -> mech_flame）。"""
    info = _SPRITE.get(raw)
    if info is None:
        alt = _SPRITE.get(_snake(raw))
        if alt is None:
            # 再试下划线/驼峰互换
            swapped = raw.replace("_", "") if "_" in raw else _snake(raw)
            alt = _SPRITE.get(swapped)
        if alt is None:
            return None
        info = alt
    if info.multi and info.ini is not None:
        game_root = _find_game_root()
        if game_root is not None:
            try:
                composed, parts = compose(info.ini, game_root)
            except Exception:
                composed, parts = None, []
            if composed is not None and len(parts) > 1:
                return composed
    path = info.directory / info.image
    if not path.is_file():
        return None
    try:
        with Image.open(path) as img:
            img = img.convert("RGBA")
    except OSError:
        return None
    frame_w = max(1, img.width // info.frames)
    return img.crop((0, 0, frame_w, img.height))


def _placeholder(text: str) -> Image.Image:
    """中性占位图：画一个问号，避免空白格看着像 bug。"""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), _FALLBACK_BG)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "?", font=font)
    x = (ICON_SIZE - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (ICON_SIZE - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((x, y), "?", font=font, fill=_FALLBACK_FG)
    return img


def _fit(img: Image.Image) -> Image.Image:
    """等比缩放居中到 ICON_SIZE×ICON_SIZE 透明画布。"""
    if img.size == (ICON_SIZE, ICON_SIZE):
        return img
    img = img.copy()
    img.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((ICON_SIZE - img.width) // 2,
                                 (ICON_SIZE - img.height) // 2))
    return canvas


def unit_preview_image(raw: str) -> Image.Image:
    """取 raw 名的 22×22 预览图（PIL Image），无图返回占位图。"""
    _ensure_index()
    if raw in _CACHE:
        return _CACHE[raw] or _placeholder(raw)
    img = _tsx_image(raw)
    if img is None:
        img = _sprite_image(raw)
    result = _fit(img) if img is not None else None
    _CACHE[raw] = result
    return result or _placeholder(raw)


def unit_preview_pixmap(raw: str):
    """取 raw 名的 22×22 预览 QPixmap（懒导入 PySide6）。"""
    from ..tilesets.tile_cache import pil_to_qpixmap
    return pil_to_qpixmap(unit_preview_image(raw))


def clear_cache() -> None:
    """换版本/换游戏目录后调用，重建索引与缓存。"""
    global _CACHE, _INDEX_READY, _TSX_ICON, _SPRITE, _TS, _RESOLVER
    _CACHE.clear()
    _INDEX_READY = False
    _TSX_ICON = {}
    _SPRITE = {}
    _TS = None
    _RESOLVER = None


__all__ = ["ICON_SIZE", "unit_preview_image", "unit_preview_pixmap", "clear_cache"]
