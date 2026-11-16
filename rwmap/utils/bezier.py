"""三次贝塞尔曲线的纯算法（零 Qt 依赖）。

供「钢笔 / 矢量曲线」工具复用：求值、自适应细分、de Casteljau 分割、
点到曲线最近参数、以及把曲线折线栅格化成连续的瓦片格序列。

与 Qt 解耦是为了能直接单测，也避免把手写几何逻辑散落在界面代码里。
"""
from __future__ import annotations

import math

Point = tuple[float, float]
Segment = tuple[Point, Point, Point, Point]   # P0, P1, P2, P3


def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def bezier_point(seg: Segment, t: float) -> Point:
    p0, p1, p2, p3 = seg
    u = 1.0 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def bezier_derivative(seg: Segment, t: float) -> Point:
    p0, p1, p2, p3 = seg
    u = 1.0 - t
    x = (3 * u * u * (p1[0] - p0[0]) + 6 * u * t * (p2[0] - p1[0])
         + 3 * t * t * (p3[0] - p2[0]))
    y = (3 * u * u * (p1[1] - p0[1]) + 6 * u * t * (p2[1] - p1[1])
         + 3 * t * t * (p3[1] - p2[1]))
    return (x, y)


def split_segment(seg: Segment, t: float) -> tuple[Segment, Segment]:
    """de Casteljau 分割：返回 (左段, 右段)，两段拼起来与原曲线完全重合。"""
    p0, p1, p2, p3 = seg
    a = _lerp(p0, p1, t)
    b = _lerp(p1, p2, t)
    c = _lerp(p2, p3, t)
    d = _lerp(a, b, t)
    e = _lerp(b, c, t)
    f = _lerp(d, e, t)          # 分割点，恰为 B(t)
    return (p0, a, d, f), (f, e, c, p3)


def tangent_handle(left: Segment, right: Segment) -> Point:
    """由分割结果给出一个**严格对称**的控制柄向量 h。

    de Casteljau 里 D(left[2])、F、E(right[1]) 三点共线且 F 在中间，
    取 (E - D)/2 作为切线方向上的柄，令 hin=-h / hout=+h。
    这样插入的锚点永远保持「双柄对称」（用户确认的语义），代价是
    插入瞬间曲线有极小的局部形变 —— 对称与零形变数学上不可兼得。
    """
    d, e = left[2], right[1]
    return ((e[0] - d[0]) * 0.5, (e[1] - d[1]) * 0.5)


def _flatness(seg: Segment) -> float:
    """两个控制点到弦 P0-P3 的最大距离，作为是否继续细分的判据。"""
    p0, p1, p2, p3 = seg
    dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    norm = math.hypot(dx, dy)
    if norm < 1e-9:
        return max(math.dist(p1, p0), math.dist(p2, p0))
    return max(abs((p[0] - p0[0]) * dy - (p[1] - p0[1]) * dx) / norm
               for p in (p1, p2))


def flatten_segment(seg: Segment, tol: float = 0.5,
                    max_depth: int = 16) -> list[tuple[Point, float]]:
    """自适应细分成折线，返回 [(点, 对应 t), ...]（含首尾端点）。

    记录 t 是为了「点到曲线」命中判定能在粗定位后再做区间细化。
    """
    out: list[tuple[Point, float]] = [(seg[0], 0.0)]

    def rec(s: Segment, t0: float, t1: float, depth: int) -> None:
        if depth >= max_depth or _flatness(s) <= tol:
            out.append((s[3], t1))
            return
        left, right = split_segment(s, 0.5)
        tm = (t0 + t1) * 0.5
        rec(left, t0, tm, depth + 1)
        rec(right, tm, t1, depth + 1)

    rec(seg, 0.0, 1.0, 0)
    return out


def flatten_segments(segments, tol: float = 0.5) -> list[Point]:
    """把多段曲线的折线采样拼成一条连续折线（去掉相邻段重复的端点）。"""
    pts: list[Point] = []
    for seg in segments:
        samples = flatten_segment(seg, tol)
        start = 0 if not pts else 1
        pts.extend(p for p, _t in samples[start:])
    return pts


def _point_segment_distance(p: Point, a: Point,
                            b: Point) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-18:
        return math.dist(p, a), 0.0
    u = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq
    u = min(1.0, max(0.0, u))
    return math.dist(p, (a[0] + dx * u, a[1] + dy * u)), u


def nearest_on_curve(p: Point, segments, tol: float = 0.5):
    """返回 (最小距离, 段索引, 精确 t)；segments 为空时返回 None。

    折线粗定位 → 在最优折线对应的 t 小区间做三分搜索细化。
    """
    best = None
    for si, seg in enumerate(segments):
        samples = flatten_segment(seg, tol)
        for k in range(len(samples) - 1):
            a, ta = samples[k]
            b, tb = samples[k + 1]
            d, u = _point_segment_distance(p, a, b)
            if best is None or d < best[0]:
                best = (d, si, ta, tb)
    if best is None:
        return None
    _d, si, ta, tb = best
    seg = segments[si]
    lo, hi = max(0.0, ta), min(1.0, tb)
    for _ in range(26):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        d1 = math.dist(p, bezier_point(seg, m1)) ** 2
        d2 = math.dist(p, bezier_point(seg, m2)) ** 2
        if d1 < d2:
            hi = m2
        else:
            lo = m1
    t = (lo + hi) * 0.5
    return math.dist(p, bezier_point(seg, t)), si, t


def rasterize_polyline_to_cells(pts, tile_w: int, tile_h: int):
    """折线 → 瓦片格序列（Amanatides-Woo 逐格推进，保序去重、4-连通不跳格）。

    只按固定 t 步长采样再取整会在高曲率 / 近水平段跳格，盖章就会出现断点，
    所以这里沿折线逐次跨一个格边界推进。
    """
    cells: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    def add(c: int, r: int) -> None:
        key = (c, r)
        if key not in seen:
            seen.add(key)
            cells.append(key)

    if not pts:
        return cells
    if len(pts) == 1:
        add(int(math.floor(pts[0][0] / tile_w)),
            int(math.floor(pts[0][1] / tile_h)))
        return cells

    inf = float("inf")
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        c = int(math.floor(x0 / tile_w))
        r = int(math.floor(y0 / tile_h))
        c1 = int(math.floor(x1 / tile_w))
        r1 = int(math.floor(y1 / tile_h))
        add(c, r)
        dx, dy = x1 - x0, y1 - y0
        step_c = 1 if dx > 0 else -1
        step_r = 1 if dy > 0 else -1
        if dx != 0:
            boundary_x = (c + (1 if step_c > 0 else 0)) * tile_w
            t_max_x = (boundary_x - x0) / dx
            t_delta_x = tile_w / abs(dx)
        else:
            t_max_x, t_delta_x = inf, inf
        if dy != 0:
            boundary_y = (r + (1 if step_r > 0 else 0)) * tile_h
            t_max_y = (boundary_y - y0) / dy
            t_delta_y = tile_h / abs(dy)
        else:
            t_max_y, t_delta_y = inf, inf
        guard = 0
        while (c, r) != (c1, r1) and guard < 100000:
            guard += 1
            if t_max_x < t_max_y:
                c += step_c
                t_max_x += t_delta_x
            elif t_max_y < inf:
                r += step_r
                t_max_y += t_delta_y
            else:
                break
            add(c, r)
    return cells
