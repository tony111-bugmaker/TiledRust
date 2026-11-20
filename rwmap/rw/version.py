"""版本配置加载。

用户要求：不再提供 1.14 地图模式。1.14 与 1.15 的 TMX 读写格式在本项目里
完全共用，差别只在 profile 的路径与写权限，所以读取 1.14 地图时直接用
1.15 profile 即为「自动转换为 1.15 处理」，不需要数据迁移。
"""
from __future__ import annotations

from ..document.version_profile import VersionProfile
from ..utils.paths import CONFIG_DIR, load_json

# 唯一生效的版本。即使旧配置文件里残留 "profile": "1.14"，也按 1.15 打开。
DEFAULT_PROFILE_ID = "1.15"


def load_profile(version_id: str) -> VersionProfile:
    """按版本 id 读配置；探针/回归脚本仍可显式加载 1.14 做兼容性检查。"""
    return VersionProfile.load(version_id, CONFIG_DIR)


def list_profiles() -> list[str]:
    """界面可选版本：只保留 1.15。"""
    return [DEFAULT_PROFILE_ID]


def active_profile() -> VersionProfile:
    """当前生效 profile：恒为 1.15。"""
    return load_profile(DEFAULT_PROFILE_ID)


def set_active_profile(version_id: str) -> None:
    """兼容旧调用：写回配置，但界面已不再切换版本，不影响 active_profile()。"""
    from ..utils.paths import save_json
    cfg = load_json(CONFIG_DIR / "app_config.json", {})
    cfg["profile"] = version_id
    save_json(CONFIG_DIR / "app_config.json", cfg)
