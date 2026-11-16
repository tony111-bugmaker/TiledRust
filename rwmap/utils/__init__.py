"""通用工具。"""
from .paths import PROJECT_ROOT, CONFIG_DIR, as_path, load_json, save_json, within
from .geometry import Camera
from .log import get_logger, report_path

__all__ = [
    "PROJECT_ROOT", "CONFIG_DIR", "as_path", "load_json", "save_json", "within",
    "Camera", "get_logger", "report_path",
]
