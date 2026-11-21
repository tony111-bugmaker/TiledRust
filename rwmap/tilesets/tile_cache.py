"""Qt 图块缓存（按需导入 PySide6，探针可在无 GUI 下运行）。"""
from __future__ import annotations

from PIL import Image

_QP_CACHE: dict[tuple, object] = {}
_VERSION = 0
# 等比缩放缓存：图集切出的图块像素尺寸常被地图格子尺寸不同（自定义图集），
# 绘制时要等比缩放塞进一个格子。key 含目标尺寸，故单独一层缓存。
_FIT_CACHE: dict[tuple, object] = {}


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


def get_pixmap_fitted(ts, local_id: int, flags: tuple[bool, bool, bool],
                      target_w: int, target_h: int, resolver=None):
    """取图块并等比缩放、居中放进 target_w×target_h 的瓦片格子。

    图集切图用的是图集自身的 ts.tile_w/tile_h，而地图格子的像素尺寸是
    doc.tile_w/tile_h，两者常不一致（用户导入非 20×20 的图集）。如果直接
    按图块原始像素贴到格子上，图块会溢出到邻格（图块偏大）或四周留缝
    （图块偏小）。这里统一等比缩放（保持宽高比）居中到格子尺寸，四周
    填透明，使每个图块都恰好落在一个瓦片格子里。

    尺寸已一致时直接返回原 pixmap（零开销，走快速路径）。结果按
    (图块身份 + 翻转 + 目标尺寸 + 缓存代次) 缓存。
    """
    tw, th = max(1, int(target_w)), max(1, int(target_h))
    key = (
        id(ts), ts.firstgid, ts.name, ts.image_source, ts.trans,
        ts.tile_w, ts.tile_h, ts.spacing, ts.margin,
        local_id, tuple(flags), tw, th, _VERSION,
    )
    hit = _FIT_CACHE.get(key)
    if hit is not None:
        return hit
    base = get_pixmap(ts, local_id, flags, resolver)
    if base is None or base.isNull():
        return base
    if base.width() == tw and base.height() == th:
        return base
    from PySide6.QtCore import Qt
    scaled = base.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    from PySide6.QtGui import QColor, QPainter, QPixmap
    canvas = QPixmap(tw, th)
    canvas.fill(QColor(0, 0, 0, 0))
    p = QPainter(canvas)
    x = (tw - scaled.width()) // 2
    y = (th - scaled.height()) // 2
    p.drawPixmap(x, y, scaled)
    p.end()
    # 缓存数量上限：地图图块种类有限，但换多张图集累积后仍需设防。
    if len(_FIT_CACHE) > 4096:
        _FIT_CACHE.clear()
    _FIT_CACHE[key] = canvas
    return canvas


def clear_cache() -> None:
    global _VERSION
    _QP_CACHE.clear()
    _FIT_CACHE.clear()
    _VERSION += 1
