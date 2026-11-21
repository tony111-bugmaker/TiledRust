"""按视图缩放与显示器像素比选择瓦片渲染精度（LOD）。

背景
----
地图格子在屏幕上的物理像素数 = 格子场景尺寸 × 视图缩放 × 显示器像素比。
以前缩略缓存写死 0.5 分辨率、0.75 倍缩放才启用：在 1080p 上缩放 0.3 时
缓存仍按每格 10px 渲染（屏幕只占 6px），在 4K / 150% 缩放下又可能过糊。
这里把「粗糙程度」改成由实际显示像素预算决定：

* 缩放越小 → 每格占的屏幕像素越少 → 用越粗的缓存（省填充与内存）；
* 显示器像素比越高（高分屏 / 系统缩放）→ 同样倍率下每格物理像素越多
  → 保留更高精度，避免把高分屏画面糊掉。

LOD 是 2 的幂级（1, 1/2, 1/4, 1/8, 1/16），只在这几档之间切换，
避免每次滚轮缩放都重建缓存；1.0 表示不降级、逐格绘制全分辨率瓦片。
"""
from __future__ import annotations

# 缓存分辨率相对场景坐标的比例。最小 1/16：20px 格子降到 1.25px，
# 再低只会变成无意义的色块，且 drawPixmap 次数不再下降。
LOD_LEVELS = (1.0, 0.5, 0.25, 0.125, 0.0625)
LOD_MIN = LOD_LEVELS[-1]

# 每格缓存像素允许略高于实际屏幕像素，避免最近邻放大时边缘抖动；
# 1.25 意味着「缓存分辨率最多是显示需求的 1.25 倍」。
_OVERSAMPLE = 1.25


def choose_render_lod(view_scale: float, pixel_ratio: float = 1.0) -> float:
    """返回当前视图下合适的 LOD 比例（LOD_LEVELS 之一）。

    ``view_scale`` 是 QGraphicsView 的缩放倍率，``pixel_ratio`` 是显示器
    每逻辑像素对应的物理像素数（``QWidget.devicePixelRatioF()``）。

    档位只取决于比例：格子场景尺寸变大时，它占的屏幕像素也同比变大，
    所需 LOD 比例不变，因此不需要传格子像素尺寸。

    纯函数、无 Qt 依赖，便于单测；无显示器信息时 pixel_ratio 默认 1.0。
    """
    try:
        scale = abs(float(view_scale))
    except (TypeError, ValueError):
        scale = 1.0
    try:
        dpr = float(pixel_ratio)
    except (TypeError, ValueError):
        dpr = 1.0
    if scale <= 0.0:
        scale = 1.0
    if dpr <= 0.0:
        dpr = 1.0

    # 缓存像素 / 屏幕物理像素 = level / (scale × dpr)，
    # 超采样 1.25 倍避免最近邻放大时边缘抖动。
    budget = scale * dpr * _OVERSAMPLE
    for level in LOD_LEVELS:
        if level <= budget:
            return level
    return LOD_MIN


def viewport_pixel_ratio(widget=None) -> float:
    """取 viewport 所在显示器的像素比；无 widget / 离屏渲染时回退 1.0。

    Qt6 的 QWidget.devicePixelRatioF() 已包含屏幕物理分辨率和系统缩放
    （例如 4K 屏 150% 缩放约为 2.0），不需要再叠加 physicalDotsPerInch，
    否则会把同一因素乘两次。
    """
    if widget is None:
        return 1.0
    try:
        ratio = float(widget.devicePixelRatioF())
    except (AttributeError, TypeError, ValueError):
        return 1.0
    if ratio <= 0.0:
        return 1.0
    return min(max(ratio, 0.25), 8.0)
