"""路径与配置工具。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller onedir：exe 所在目录就是发行版根目录，config / logs 放在它旁边
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def as_path(value) -> Path:
    """统一正斜杠，兼容 Windows 中文 / 空格 / 方括号路径。"""
    return Path(str(value).replace("\\", "/"))


def load_json(path: Path | str, default=None):
    p = as_path(path)
    if not p.is_file():
        return {} if default is None else default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path: Path | str, data) -> None:
    p = as_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
