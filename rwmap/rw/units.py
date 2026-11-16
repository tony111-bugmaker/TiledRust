"""单位表：raw name 字符串，不是数字 ID。

来源优先级：units.tsx 的 tile 属性 -> assets/units/**/*.ini -> 已加载 .rwmod 内 ini。
社区整理表只是版本快照，不作为数据源。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..io.tsx_reader import read_tsx
from ..utils.paths import as_path

_NAME_RE = re.compile(r"^\s*name\s*[:=]\s*(.+?)\s*$", re.IGNORECASE)
_DISPLAY_RE = re.compile(r"^\s*displayText\s*[:=]\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class UnitEntry:
    raw_name: str
    team: str = ""
    source: str = ""
    display_name: str = ""


def _parse_ini(text: str, source: str) -> list[UnitEntry]:
    in_core = False
    name = ""
    display = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_core and name:
                return [UnitEntry(name, "", source, display)]
            in_core = stripped.lower() == "[core]"
            name, display = "", ""
            continue
        if not in_core:
            continue
        match = _NAME_RE.match(line)
        if match and not name:
            name = match.group(1).strip()
            continue
        match = _DISPLAY_RE.match(line)
        if match and not display:
            display = match.group(1).strip()
    if name:
        return [UnitEntry(name, "", source, display)]
    return []


def _parse_units_tsx(path: Path) -> list[UnitEntry]:
    try:
        ts = read_tsx(path, 1)
    except Exception:
        return []
    out = []
    for local_id, props in ts.tiles.items():
        raw = props.get("unit")
        if raw:
            out.append(UnitEntry(raw, props.get("team", ""), str(path)))
    return out


def collect_units(profile) -> list[UnitEntry]:
    entries: list[UnitEntry] = []
    seen: set[str] = set()
    for src in profile.units_sources:
        expanded = profile.expand(src)
        if "**" in expanded:
            base = Path(expanded.split("**")[0].rstrip("/"))
            paths = list(base.rglob("*.ini")) if base.is_dir() else []
        else:
            path = Path(expanded)
            paths = [path] if path.is_file() else list(path.parent.glob(path.name))
        for path in paths:
            path = as_path(path)
            if not path.is_file():
                continue
            if path.suffix.lower() == ".tsx":
                found = _parse_units_tsx(path)
            elif path.suffix.lower() == ".ini":
                try:
                    found = _parse_ini(path.read_text(encoding="utf-8", errors="ignore"), str(path))
                except OSError:
                    found = []
            else:
                found = []
            for entry in found:
                if entry.raw_name not in seen:
                    seen.add(entry.raw_name)
                    entries.append(entry)
    return entries


def load_mod_units(mod_path) -> list[UnitEntry]:
    from .mod_reader import iter_mod_ini
    out = []
    for name, text in iter_mod_ini(mod_path):
        out.extend(_parse_ini(text, f"{mod_path}!/{name}"))
    return out
