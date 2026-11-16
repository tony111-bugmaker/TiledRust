"""图块属性别名归一（官方存在 large-clift 错拼）。"""
from __future__ import annotations

from ..utils.paths import CONFIG_DIR, load_json

_CFG = None


def _cfg() -> dict:
    global _CFG
    if _CFG is None:
        _CFG = load_json(CONFIG_DIR / "property_aliases.json", {})
    return _CFG


def aliases() -> dict:
    return _cfg().get("aliases", {})


def normalize(name: str) -> str:
    return aliases().get(name, name)


def is_known(name: str) -> bool:
    return normalize(name) in _cfg().get("known", [])
