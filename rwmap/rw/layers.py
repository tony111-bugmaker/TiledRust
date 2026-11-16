"""图层语义白名单。未知层一律只读保真，不猜测语义。"""
from __future__ import annotations

from dataclasses import dataclass

from ..utils.paths import CONFIG_DIR, load_json

_CONFIG = None


def _config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_json(CONFIG_DIR / "layer_rules.json", {})
    return _CONFIG


@dataclass
class LayerRule:
    name: str
    kind: str
    writable: bool
    note: str
    known: bool = True


def known_layer_names() -> list[str]:
    return list(_config().get("rules", {}).keys())


def hidden_layer_names() -> list[str]:
    """游戏完全忽略的辅助层（Tiled 生成缩略图时也会 --hide-layer set）。"""
    return [str(name) for name in _config().get("hidden_by_default", [])]


def classify(name: str, kind: str = "tile", profile=None) -> LayerRule:
    cfg = _config()
    rules = cfg.get("rules", {})
    lookup = {key.lower(): value for key, value in rules.items()}
    key = name.lower()
    if key.startswith("set-"):
        key = "set"
    rule = lookup.get(key)
    if rule is None:
        return LayerRule(
            name=name, kind=kind, writable=False, known=False,
            note="未知层：只读保真，不解释语义（避免写入推测值）",
        )
    writable = bool(rule.get("writable", False))
    note = rule.get("note", "")
    if profile is not None:
        if not profile.write_enabled:
            writable = False
            note = f"[{profile.version_id} 只读优先] {note}"
        if name in getattr(profile, "extra_readonly_layers", []):
            writable = False
    return LayerRule(name=name, kind=kind, writable=writable, note=note, known=True)
