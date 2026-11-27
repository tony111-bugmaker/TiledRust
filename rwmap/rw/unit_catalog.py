"""游戏单位目录：中文名 / 贴图 / 多部件拼装外观（编辑器与图鉴共用）。

只读游戏本体 ``assets/units`` 与官方中文翻译，不写任何游戏文件。
结果带进程级缓存；换游戏目录或资源后调 ``clear_cache()``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .unit_compose import compose, load_frame, resolve_image
from .version import active_profile

# 目录名 -> 中文分类（游戏资源目录本身是英文名）
CATEGORY_ZH = {
    "tanks": "坦克装甲", "turrets": "防御炮塔", "mechs_small": "机甲·小型",
    "mechs_large": "机甲·大型", "mech_factory": "机甲工厂", "mech_engineer": "机甲工程",
    "classic_bugs": "虫族", "bug_base": "虫族基地", "bug_melee": "虫族近战",
    "nukes": "核武器", "resource_deposits": "资源矿点", "miscellaneous": "杂项",
    "shared": "共用资源", "helicopter": "直升机", "interceptor": "截击机",
    "bomber": "轰炸机", "heavy_battleship": "重型舰艇", "heavy_sub": "潜艇",
    "laboratory": "实验室", "outpost": "前哨站", "extractor": "采集器",
    "fabricator": "制造厂", "scout": "侦察单位", "spy_drone": "间谍无人机",
}
_IGNORE_PROPS = {"dont_load", "name", "copyfrom", "overrideandreplace", "displaylocalekey"}


def cat_zh(name: str) -> str:
    return CATEGORY_ZH.get(name, name)


def game_root() -> Path | None:
    """当前版本配置里的游戏根目录。"""
    try:
        root = Path(active_profile().game_root)
        return root if root.is_dir() else None
    except Exception:
        return None


@dataclass
class UnitInfo:
    name: str                       # 规范内部名（overrideAndReplace 归一后）
    aliases: list[str] = field(default_factory=list)
    zh: str = ""
    en: str = ""
    desc: str = ""
    category: str = ""
    categories: list[str] = field(default_factory=list)
    props: dict[str, str] = field(default_factory=dict)
    source: str = ""                # ini 相对路径
    ini_path: Path | None = None
    image_path: Path | None = None
    frames: int = 1
    thumb: Image.Image | None = None
    composed: Image.Image | None = None
    parts: list[dict] = field(default_factory=list)

    @property
    def multi(self) -> bool:
        return self.composed is not None and len(self.parts) > 1

    @property
    def label(self) -> str:
        return self.zh or self.en or self.name

    @property
    def category_label(self) -> str:
        return cat_zh(self.category)


def _parse_properties(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _parse_ini(text: str) -> tuple[dict[str, str], dict[str, str]]:
    core: dict[str, str] = {}
    gfx: dict[str, str] = {}
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            continue
        if ":" not in line or s.startswith("#"):
            continue
        key, value = line.split(":", 1)
        key, value = key.strip().lower(), value.split("#", 1)[0].strip()
        if not value or value.upper() == "NONE":
            continue
        if section == "core":
            core.setdefault(key, value)
        elif section == "graphics":
            gfx.setdefault(key, value)
    return core, gfx


def _first_frame(path: Path | None, frames: int) -> Image.Image | None:
    if path is None or not path.is_file():
        return None
    try:
        with Image.open(path) as raw:
            img = raw.convert("RGBA")
    except OSError:
        return None
    return img.crop((0, 0, max(1, img.width // max(1, frames)), img.height))


_CACHE: dict[Path, list[UnitInfo]] = {}


def scan_units(root: Path | None = None) -> list[UnitInfo]:
    """扫描全部单位定义；同名版本合并，主版本优先取中文名和贴图。"""
    root = root or game_root()
    if root is None:
        return []
    root = Path(root)
    if root in _CACHE:
        return _CACHE[root]
    units_root = root / "assets" / "units"
    if not units_root.is_dir():
        return []

    lang = _parse_properties(root / "assets/translations/Strings_zh_cn.properties")
    english = _parse_properties(root / "assets/translations/Strings.properties")
    grouped: dict[str, UnitInfo] = {}

    for ini in sorted(units_root.rglob("*.ini")):
        try:
            core, gfx = _parse_ini(ini.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if not core or str(core.get("dont_load", "")).lower() in {"true", "1"}:
            continue
        raw_name = core.get("name", "")
        effective = core.get("overrideandreplace") or raw_name
        if not effective:
            continue

        rec = grouped.get(effective)
        if rec is None:
            rec = UnitInfo(name=effective, category=ini.parent.name)
            grouped[effective] = rec
        if raw_name and raw_name not in rec.aliases:
            rec.aliases.append(raw_name)
        if ini.parent.name not in rec.categories:
            rec.categories.append(ini.parent.name)

        is_main = not raw_name.startswith("c_")
        if is_main or not rec.zh:
            lk = core.get("displaylocalekey", "")
            rec.zh = (lang.get(f"units.{lk}.name") or lang.get(f"units.{effective}.name")
                      or lang.get(f"units.{raw_name}.name") or "")
            rec.desc = (lang.get(f"units.{lk}.description")
                        or lang.get(f"units.{effective}.description")
                        or lang.get(f"units.{raw_name}.description") or "")
            rec.en = (english.get(f"units.{lk}.name")
                      or english.get(f"units.{effective}.name") or "")
            rec.category = ini.parent.name
            rec.frames = int(gfx.get("total_frames", "1") or 1)
            image_path = resolve_image(gfx.get("image", ""), ini, root)
            if image_path:
                rec.image_path = image_path
                rec.thumb = _first_frame(image_path, rec.frames)
            rec.ini_path = ini
            rec.source = ini.relative_to(root).as_posix()
            for key, value in core.items():
                if key not in _IGNORE_PROPS and key not in rec.props:
                    rec.props[key] = value

            if is_main:
                try:
                    composed, parts = compose(ini, root)
                except Exception:
                    composed, parts = None, []
                if composed is not None and len(parts) > 1:
                    rec.composed = composed
                    agg: dict[str, dict] = {}
                    for part in parts:
                        key = part["name"]
                        if key in agg:
                            agg[key]["count"] += 1
                        else:
                            agg[key] = dict(part, count=1)
                    for part in agg.values():
                        part["image"] = load_frame(
                            resolve_image(part["name"], ini, root))
                    rec.parts = list(agg.values())

    result = sorted(grouped.values(), key=lambda u: (u.zh or u.name).lower())
    _CACHE[root] = result
    return result


def find_unit(name: str, root: Path | None = None) -> UnitInfo | None:
    """按内部名 / 别名找单位（大小写不敏感）。"""
    if not name:
        return None
    want = name.lower()
    for unit in scan_units(root):
        if unit.name.lower() == want or any(a.lower() == want for a in unit.aliases):
            return unit
    return None


def clear_cache() -> None:
    _CACHE.clear()
