"""缩略图生成校验（tmxrasterizer + Pillow）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..rw.thumbnail import make_thumbnail
from ..rw.version import load_profile
from ..utils.paths import PROJECT_ROOT

SAMPLE = Path("<游戏目录1.15>/mods/maps/[p2]example_skirmish_(2p).tmx")
TMP = PROJECT_ROOT / "logs" / "probe_tmp"


def run() -> dict:
    TMP.mkdir(parents=True, exist_ok=True)
    profile = load_profile("1.15")
    out = TMP / "probe_thumb.png"
    result = make_thumbnail(SAMPLE, profile, out)
    errors = []
    if not result.get("ok"):
        errors.append(result.get("error", "生成失败"))
    else:
        try:
            img = Image.open(result["path"])
            if img.size != (160, 160):
                errors.append(f"尺寸不符: {img.size}")
            if img.convert("RGBA").getbbox() is None:
                errors.append("图像全透明")
        except OSError as exc:
            errors.append(str(exc))
    return {
        "name": "缩略图生成（160x160）",
        "ok": not errors,
        "details": result,
        "errors": errors,
    }
