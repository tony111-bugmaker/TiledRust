"""Qt 图块缓存（按需导入 PySide6，探针可在无 GUI 下运行）。"""
from __future__ import annotations

from PIL import Image

_QP_CACHE: dict[tuple, object] = {}
_VERSION = 0


def cache_version() -> int:
    """图块缓存代次：换图集图片后 clear_cache() 会递增，供上层预渲染缓存失效。"""
    return _VERSION


def pil_to_qpixmap(img: Image.Image, h: bool = False, v: bool = False, d: bool = False):
    from PySide6.QtGui import QImage, QPixmap, QTransform

    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888).copy()
    pix = QPixmap.fromImage(qimg)
    if h or v or d:
        transform = QTransform()
        if d:
            transform.rotate(90)
            pix = pix.transformed(transform)
            transform = QTransform()
        if h or v:
            transform.scale(-1 if h else 1, -1 if v else 1)
            pix = pix.transformed(transform)
    return pix


def get_pixmap(ts, local_id: int, flags: tuple[bool, bool, bool], resolver=None):
    from .image_loader import get_tile_image

    # 只用 id(ts) 会在「切换地图后 CPython 复用对象地址」时串图，
    # 补上图块集自身的稳定标识。
    key = (
        id(ts), ts.firstgid, ts.name, ts.image_source, ts.trans,
        ts.tile_w, ts.tile_h, ts.columns, ts.spacing, ts.margin,
        local_id, flags,
    )
    if key in _QP_CACHE:
        return _QP_CACHE[key]
    img = get_tile_image(ts, local_id, resolver)
    if img is None:
        return None
    pix = pil_to_qpixmap(img, *flags)
    _QP_CACHE[key] = pix
    return pix


def clear_cache() -> None:
    global _VERSION
    _QP_CACHE.clear()
    _VERSION += 1
