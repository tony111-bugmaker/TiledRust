"""版本配置加载与切换。"""
from __future__ import annotations

from ..document.version_profile import VersionProfile, available_profiles
from ..utils.paths import CONFIG_DIR, load_json


def load_profile(version_id: str) -> VersionProfile:
    return VersionProfile.load(version_id, CONFIG_DIR)


def list_profiles() -> list[str]:
    """返回 "1.14" / "1.15" 形式，与 app_config.json 中的 profile 值一致。"""
    return [f"{v[0]}.{v[1:]}" if len(v) > 1 else v for v in available_profiles(CONFIG_DIR)]


def active_profile() -> VersionProfile:
    cfg = load_json(CONFIG_DIR / "app_config.json", {})
    return load_profile(cfg.get("profile", "1.15"))


def set_active_profile(version_id: str) -> None:
    from ..utils.paths import save_json
    cfg = load_json(CONFIG_DIR / "app_config.json", {})
    cfg["profile"] = version_id
    save_json(CONFIG_DIR / "app_config.json", cfg)
