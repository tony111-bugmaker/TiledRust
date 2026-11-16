""".rwmod 读取（本质是 ZIP）。只读小文件，不解压大资源。"""
from __future__ import annotations

import zipfile
from pathlib import Path

from ..utils.paths import as_path


def list_mods(profile) -> list[Path]:
    roots = [profile.mods_maps, profile.game_root / "mods"]
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(sorted(root.rglob("*.rwmod")))
    seen, out = set(), []
    for path in found:
        if str(path) not in seen:
            seen.add(str(path))
            out.append(path)
    return out


def read_mod_info(mod_path) -> dict:
    path = as_path(mod_path)
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith("mod-info.txt")]
            if not names:
                return {}
            text = zf.read(names[0]).decode("utf-8", errors="ignore")
    except (OSError, zipfile.BadZipFile):
        return {}
    info = {}
    for line in text.splitlines():
        if ":" in line and not line.strip().startswith("["):
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
    return info


def iter_mod_ini(mod_path, limit: int = 400):
    path = as_path(mod_path)
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".ini"):
                    continue
                if limit <= 0:
                    break
                limit -= 1
                yield name, zf.read(name).decode("utf-8", errors="ignore")
    except (OSError, zipfile.BadZipFile):
        return
