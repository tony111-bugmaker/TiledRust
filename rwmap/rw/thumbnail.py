"""缩略图：Tiled tmxrasterizer + Pillow，替换官方 Python2/ImageMagick 脚本。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PIL import Image

from ..utils.paths import as_path


def make_thumbnail(tmx_path, profile, out_path=None, size: int = 160):
    # 子进程 cwd 会改成 Tiled 目录，所有路径必须绝对化，否则输出落到 <Tiled目录> 下。
    tmx = as_path(tmx_path).resolve()
    out = (as_path(out_path) if out_path else tmx.with_name(tmx.stem + "_map.png")).resolve()
    rasterizer = as_path(profile.rasterizer_exe).resolve()
    if not rasterizer.is_file():
        return {"ok": False, "error": f"找不到 tmxrasterizer: {rasterizer}"}
    tmp = out.with_name(out.stem + "_raster.png")
    cmd = [
        str(rasterizer), "-a", "--ignore-visibility",
        "--hide-layer", "set", "-s", "0.5", str(tmx), str(tmp),
    ]
    # tmxrasterizer 是 Qt 程序，只带 windows 平台插件。父进程若设置过
    # QT_QPA_PLATFORM=offscreen（离屏自测），会被子进程继承并弹出
    # "no Qt platform plugin could be initialized" 错误框。必须清理。
    env = os.environ.copy()
    for key in ("QT_QPA_PLATFORM", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH",
                "QT_QPA_PLATFORMTHEME", "QT_OPENGL"):
        env.pop(key, None)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=60,
            cwd=str(rasterizer.parent), env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    if not tmp.is_file():
        return {"ok": False, "error": (proc.stderr or b"").decode("utf-8", "ignore")[:500]}
    try:
        img = Image.open(tmp).convert("RGBA")
        img.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
        canvas.save(out)
        bbox = img.getbbox()
        ok = bbox is not None and img.width > 1 and img.height > 1
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return {"ok": ok, "path": str(out), "size": size}
