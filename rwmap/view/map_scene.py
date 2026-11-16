"""QGraphicsScene：文档切换、局部刷新、层可见性/透明度。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsRectItem

from ..document.gid import split_gid
from ..tilesets.tile_cache import cache_version, get_pixmap

# 图章影像预渲染缓存的像素上限（约 32 MB @ RGBA）。超过就回退逐格绘制，
# 避免用户抓整张地图当图章时一次性申请上百 MB 显存。
_MAX_PREVIEW_PIXELS = 8_000_000

# 图章 / 抓取虚影标脏矩形的外扩余量（场景坐标像素）。
# 蓝 / 红轮廓用 QPen(SELECT, 1.6)，描边中心压在 _content_rect() 的边界上，
# 外侧半线宽 + 抗锯齿会落到内容矩形之外。MapView 用 MinimalViewportUpdate，
# 只重绘被标脏的矩形；若标脏范围恰好等于内容矩形，那半条线永远不会被任何
# item 重绘覆盖，hover 每移一格就留下一根蓝线（用户看到的「拖影」）。
# 与 TileLayerItem.invalidate_cells 的 ±1、对象框 boundingRect 外扩是同一个坑。
_PREVIEW_DIRTY_MARGIN = 3.0

# 「当前层高亮」时其它层的透明度乘数。只改 QGraphicsItem.opacity，
# 绝不写回 layer.opacity —— 后者会进 TMX，属于纯视图开关不该有的副作用。
_DIM_OPACITY = 0.35
from .grid_overlay import GridOverlay
from .layer_renderer import build_layer_items
from .object_layer_item import (
    ObjectLayerItem, draw_selection, effective_handle_size, object_rect,
)
from .pen_preview import PenPathPreviewItem
from .selection_style import SELECT, SELECT_FILL


class ObjectSelectionBoxItem(QGraphicsRectItem):
    """对象选中整体包围框：浅蓝虚线框 + 8 个纯黄色双向缩放箭头。

    继承 ``QGraphicsRectItem`` 以保留 ``setRect`` / ``rect`` / ``setVisible``
    等旧接口；箭头整支画在框**外**（见 ``draw_selection``），所以 boundingRect
    必须外扩，否则 MinimalViewportUpdate 下框外的箭头画不出来、拖走后还会留残影。
    箭头尺寸是屏幕固定像素，视图缩放后由 ``set_view_scale`` 同步。
    """

    def __init__(self):
        super().__init__()
        self.setPen(QPen(QColor(150, 210, 255), 0.6, Qt.DashLine))
        self.setBrush(QColor(150, 210, 255, 28))
        self.setZValue(2001)
        self._scale = 1.0
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)

    def set_view_scale(self, scale: float):
        scale = abs(scale) or 1.0
        if abs(scale - self._scale) > 1e-3:
            self.prepareGeometryChange()
            self._scale = scale
            self.update()

    def boundingRect(self) -> QRectF:
        r = self.rect()
        pad = effective_handle_size(r, self._scale) * 2.2 + 6
        return r.adjusted(-pad, -pad, pad, pad)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        r = self.rect()
        if r.isEmpty():
            return
        draw_selection(painter, r,
                       abs(painter.worldTransform().m11()) or 1.0)


class TileStampPreviewItem(QGraphicsItem):
    """跟随光标的半透明图章/抓取预览：把多格 2D gid 整块画在指定位置。

    anchor_mode="center"：几何中心对齐 (col,row)（图章预览，与盖章落点一致）；
    anchor_mode="topleft"：左上角对齐 (col,row)（右键拖动抓取区域时的实时虚影）。

    boundingRect() 固定为「地图 + 四周虚拟留白」，**不随 col/row/stamp 变化**：MapView
    用 MinimalViewportUpdate，若 item 几何随内容变化，旧位置像素不会被正确标脏重绘，
    hover 移动时就会留下一串拖影（与 GridOverlay 采用同一模式）。移动时只显式
    update 旧 / 新两块「内容矩形」，避免全图重绘。

    之所以要扩出地图外：图章/抓取虚影可拖到地图边界之外，越界格是真实可见的（红色
    边框提示），若 boundingRect 只锁在地图内，越界格所在的区域不再属于 item 的脏区，
    视图就不会重绘它们，于是越界部分「看不见」。留白宽度与 MapScene.set_document 的
    sceneRect 一致，保证光标能到达的越界范围都覆盖得到。
    """

    # 地图外留白倍数（与 MapScene.set_document 的 sceneRect padding 对齐）。
    _PAD_FACTOR = 4

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.stamp = None          # list[list[int]] 2D gid
        self.col = 0
        self.row = 0
        self.mode = "center"
        self._cached_blue_blocks = []   # 诊断/测试用，懒计算（不进 hover 热路径）
        self._cached_red_blocks = []
        self._blue_cells = set()   # 地图内的非零格 → 蓝区
        self._red_cells = set()    # 地图外的非零格 → 红区
        self._blue_fill = []       # 按行合并的填充矩形（图章局部坐标）
        self._red_fill = []
        self._blue_path = QPainterPath()   # 蓝区外轮廓（图章局部坐标，含凹角）
        self._red_path = QPainterPath()
        self._geom_in = None       # 整块在地图内时的几何缓存
        self._geom_out = None      # 整块在地图外时的几何缓存
        self._tile_pixmap = None   # 图章影像预渲染缓存（hover / 缩放每帧只贴一次）
        self._tile_pix_version = -1
        self._tri_state = None     # in / cross / out / empty：决定纯移动能否跳过几何重建
        self.setZValue(3000)       # 高于选框/网格，低于对象预览

    # ---------------- 几何：恒定 boundingRect + 局部内容矩形 ----------------
    def boundingRect(self):
        if self.doc is None:
            return QRectF(0, 0, 0, 0)
        w = self.doc.width * self.doc.tile_w
        h = self.doc.height * self.doc.tile_h
        pad = max(w, h) * self._PAD_FACTOR
        return QRectF(-pad, -pad, w + 2 * pad, h + 2 * pad)

    def _origin(self):
        if not self.stamp:
            return self.col, self.row
        cols, rows = len(self.stamp[0]), len(self.stamp)
        if self.mode == "topleft":
            return self.col, self.row
        return self.col - cols // 2, self.row - rows // 2

    def _content_rect(self):
        """当前 stamp 实际绘制到的矩形（场景坐标，item 局部坐标与之相同）。"""
        if not self.stamp:
            return QRectF(0, 0, 0, 0)
        tw, th = self.doc.tile_w, self.doc.tile_h
        rows, cols = len(self.stamp), len(self.stamp[0])
        ox, oy = self._origin()
        return QRectF(ox * tw, oy * th, cols * tw, rows * th)

    @staticmethod
    def _dirty_rect(rect: QRectF) -> QRectF:
        """标脏区域外扩，覆盖压在内容矩形边界上的整条描边。"""
        if rect.isEmpty():
            return rect
        m = _PREVIEW_DIRTY_MARGIN
        return rect.adjusted(-m, -m, m, m)

    def _state_for(self, stamp, col, row, anchor_mode):
        """按「即将设置」的内容与位置判断图章整体在地图内 / 跨边界 / 地图外。

        O(1)：只看外接矩形。几何缓存一律存图章局部坐标，图内 / 图外两态
        与绝对位置无关，纯平移可以复用；跨边界态的蓝 / 红格集合依赖
        ox/oy，平移后会变化，必须逐格重建。
        """
        if not stamp or not stamp[0] or self.doc is None:
            return "empty"
        tw, th = self.doc.tile_w, self.doc.tile_h
        cols, rows = len(stamp[0]), len(stamp)
        if anchor_mode == "topleft":
            ox, oy = col, row
        else:
            ox, oy = col - cols // 2, row - rows // 2
        content = QRectF(ox * tw, oy * th, cols * tw, rows * th)
        map_rect = QRectF(0, 0, self.doc.width * tw, self.doc.height * th)
        if map_rect.contains(content):
            return "in"
        if not map_rect.intersects(content):
            return "out"
        return "cross"

    @staticmethod
    def _blocks_from_cells(cells):
        """由格子集合算连通块外接矩形（只给诊断/测试用，绘制不依赖它）。"""
        remaining = set(cells)
        blocks = []
        while remaining:
            c0, r0 = remaining.pop()
            stack = [(c0, r0)]
            min_c = max_c = c0
            min_r = max_r = r0
            while stack:
                c, r = stack.pop()
                min_c, max_c = min(min_c, c), max(max_c, c)
                min_r, max_r = min(min_r, r), max(max_r, r)
                for nc, nr in ((c - 1, r), (c + 1, r), (c, r - 1), (c, r + 1)):
                    if (nc, nr) in remaining:
                        remaining.remove((nc, nr))
                        stack.append((nc, nr))
            blocks.append((min_c, min_r, max_c, max_r))
        return sorted(blocks, key=lambda b: (b[1], b[0]))

    @property
    def _blocks(self):
        """地图内格的连通块（仅在被读取时算，避免每帧 hover 做 flood fill）。"""
        if self._cached_blue_blocks is None:
            self._cached_blue_blocks = self._blocks_from_cells(self._blue_cells)
        return self._cached_blue_blocks

    @property
    def _red_blocks(self):
        """地图外格的连通块（同上，懒计算）。"""
        if self._cached_red_blocks is None:
            self._cached_red_blocks = self._blocks_from_cells(self._red_cells)
        return self._cached_red_blocks

    @staticmethod
    def _merge_runs(pairs):
        """把 [(a,b), ...] 里首尾相接 / 重叠的区间合并成连续段。"""
        if not pairs:
            return []
        ordered = sorted(pairs)
        out = [list(ordered[0])]
        for a, b in ordered[1:]:
            if a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return [tuple(x) for x in out]

    @classmethod
    def _region_geometry(cls, cells, ox, oy, tw, th):
        """按「实际格子集合」生成填充矩形 + 外轮廓路径（不再是外接矩形）。

        轮廓只画「相邻格不在这批格子里」的那些边，所以 L 形 / 凹角 / 空洞的
        **内侧边**也会有线（录屏里角落缺的两条线就是它）。线正好压在格子
        边界上，不再 ``adjusted(1,1,-1,-1)`` 往里缩 1px。同行连续格合并成
        一条填充矩形、共线边合并成一条线段，避免大图章逐格绘制拖慢 hover。
        """
        if not cells:
            return [], QPainterPath()
        rows = {}
        for c, r in cells:
            rows.setdefault(r, []).append((c, c + 1))
        fill = [QRectF((ox + c0) * tw, (oy + r) * th, (c1 - c0) * tw, th)
                for r, runs in rows.items()
                for c0, c1 in cls._merge_runs(runs)]

        horiz, vert = {}, {}
        for c, r in cells:
            if (c, r - 1) not in cells:
                horiz.setdefault(r, []).append((c, c + 1))
            if (c, r + 1) not in cells:
                horiz.setdefault(r + 1, []).append((c, c + 1))
            if (c - 1, r) not in cells:
                vert.setdefault(c, []).append((r, r + 1))
            if (c + 1, r) not in cells:
                vert.setdefault(c + 1, []).append((r, r + 1))
        path = QPainterPath()
        for y, runs in horiz.items():
            yy = (oy + y) * th
            for c0, c1 in cls._merge_runs(runs):
                path.moveTo((ox + c0) * tw, yy)
                path.lineTo((ox + c1) * tw, yy)
        for x, runs in vert.items():
            xx = (ox + x) * tw
            for r0, r1 in cls._merge_runs(runs):
                path.moveTo(xx, (oy + r0) * th)
                path.lineTo(xx, (oy + r1) * th)
        return fill, path

    def _split_blocks(self):
        """把非零格按「地图内 / 地图外」拆成蓝区 / 红区，并算好各自的轮廓。

        半透明预览随光标逐格移动，但只有跨地图边界时才需要真正逐格判定：
        整块在图内 / 图外时直接复用缓存的局部几何，hover 频繁调用也不慢。
        """
        self._cached_blue_blocks = None
        self._cached_red_blocks = None
        self._blue_cells = set()
        self._red_cells = set()
        self._blue_fill = []
        self._red_fill = []
        self._blue_path = QPainterPath()
        self._red_path = QPainterPath()
        if not self.stamp or not self.stamp[0] or self.doc is None:
            return
        tw, th = self.doc.tile_w, self.doc.tile_h
        map_rect = QRectF(0, 0, self.doc.width * tw, self.doc.height * th)
        content = self._content_rect()
        all_cells = {(c, r)
                     for r, line in enumerate(self.stamp)
                     for c, value in enumerate(line) if value}
        if map_rect.contains(content):
            self._blue_cells = all_cells
            if self._geom_in is None:
                self._geom_in = self._region_geometry(all_cells, 0, 0, tw, th)
            self._blue_fill, self._blue_path = self._geom_in
            return
        if not map_rect.intersects(content):
            self._red_cells = all_cells
            if self._geom_out is None:
                self._geom_out = self._region_geometry(all_cells, 0, 0, tw, th)
            self._red_fill, self._red_path = self._geom_out
            return
        ox, oy = self._origin()
        width, height = self.doc.width, self.doc.height
        # 格子与地图网格严格对齐，逐格判定只需整数比较，不必每格构造 QRectF
        # （旧写法在跨边界的大图章上会把 hover 拖到 100ms 量级）。

        def in_map(c, r):
            cc, rr = ox + c, oy + r
            return 0 <= cc < width and 0 <= rr < height

        self._blue_cells = {p for p in all_cells if in_map(*p)}
        self._red_cells = all_cells - self._blue_cells
        # 跨边界时才逐格重算轮廓；几何一律存「图章局部坐标」，paint 时整体平移，
        # 这样整块在图内 / 图外的常见情况可以缓存，大图章 hover 也不重新遍历。
        self._blue_fill, self._blue_path = self._region_geometry(
            self._blue_cells, 0, 0, tw, th)
        self._red_fill, self._red_path = self._region_geometry(
            self._red_cells, 0, 0, tw, th)

    # ---------------- 连通块（空格不涂蓝，按块分别画框）----------------
    @staticmethod
    def _connected_blocks(stamp, include=None):
        """返回非零格 4-邻接连通块的外接矩形 [(c0,r0,c1,r1), ...]（格坐标）。

        include(col,row)->bool 可选：只把满足条件的格纳入同一连通判定
        （默认 None = 全部非零格）。蓝框用 in_map、红框用 not in_map，
        两者各自按连通块画整框，而不是逐格小框。
        """
        if not stamp or not stamp[0]:
            return []
        rows, cols = len(stamp), len(stamp[0])
        seen = [[False] * cols for _ in range(rows)]
        blocks = []
        for r0 in range(rows):
            for c0 in range(cols):
                if seen[r0][c0] or not stamp[r0][c0]:
                    continue
                if include is not None and not include(c0, r0):
                    continue
                seen[r0][c0] = True
                stack = [(r0, c0)]
                min_c = max_c = c0
                min_r = max_r = r0
                while stack:
                    r, c = stack.pop()
                    min_c, max_c = min(min_c, c), max(max_c, c)
                    min_r, max_r = min(min_r, r), max(max_r, r)
                    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        nr, nc = r + dr, c + dc
                        if (0 <= nr < rows and 0 <= nc < cols
                                and not seen[nr][nc] and stamp[nr][nc]
                                and (include is None or include(nc, nr))):
                            seen[nr][nc] = True
                            stack.append((nr, nc))
                blocks.append((min_c, min_r, max_c, max_r))
        return blocks

    # ---------------- 内容更新 ----------------
    def set_cells(self, stamp, col: int, row: int, anchor_mode: str = "center"):
        # hover 每格都会触发；内容 / 位置全未变时直接跳过，省掉无谓重绘。
        # 上层（state.stamp / 抓取虚影缓存）复用同一个 list 对象，这里用身份
        # 比较即可，避免大图章每帧两次 O(N²) 的 list-of-list 深比较。
        if (stamp is self.stamp and self.col == col and self.row == row
                and self.mode == anchor_mode):
            return
        old_rect = self._content_rect()
        same_content = stamp is self.stamp and anchor_mode == self.mode
        new_state = self._state_for(stamp, col, row, anchor_mode)
        # 几何一律存「图章局部坐标」，paint 时整体平移。整块在图内 / 图外时
        # 蓝 / 红拆分与绝对位置无关，纯移动可以跳过 _split_blocks()；
        # 跨边界态下 in_map() 依赖 ox/oy，平移会改变拆分，必须重建。
        can_skip = (same_content and new_state == self._tri_state
                    and new_state in ("in", "out"))
        if not same_content:
            self._geom_in = None
            self._geom_out = None
            self._tile_pixmap = None   # 内容变了，影像缓存必须重建
        self.stamp = stamp
        self.col, self.row = col, row
        self.mode = anchor_mode
        if not can_skip:
            self._split_blocks()
            self._tri_state = new_state
        # 几何不变，无需 prepareGeometryChange；只标脏旧、新两块内容矩形。
        # 必须外扩：轮廓线压在边界上，外侧半线宽在内容矩形外，不外扩就会
        # 永远不被重绘、留下拖影（见 _PREVIEW_DIRTY_MARGIN 注释）。
        # 空矩形不传给 update()，个别 Qt 版本会把它当成「重绘全部」。
        if not old_rect.isEmpty():
            self.update(self._dirty_rect(old_rect))
        new_rect = self._content_rect()
        if not new_rect.isEmpty():
            self.update(self._dirty_rect(new_rect))

    def clear_cells(self):
        """复用同一 item 清空内容：只标脏旧区域，避免 removeItem 触发整图脏区。"""
        if self.stamp is None:
            return
        old_rect = self._content_rect()
        self.stamp = None
        self._cached_blue_blocks = []
        self._cached_red_blocks = []
        self._blue_cells = set()
        self._red_cells = set()
        self._blue_fill = []
        self._red_fill = []
        self._blue_path = QPainterPath()
        self._red_path = QPainterPath()
        self._geom_in = None
        self._geom_out = None
        self._tile_pixmap = None
        self._tri_state = None
        if not old_rect.isEmpty():
            self.update(self._dirty_rect(old_rect))

    # ---------------- 图章影像预渲染 ----------------
    def _tile_preview_pixmap(self):
        """把图章的半透明瓦片影像预渲染成一张 QPixmap。

        旧实现每帧都遍历全部格子做 split_gid / local_tile / get_pixmap /
        drawPixmap：离屏实测 20×20 约 2.6ms、40×40 约 10ms、60×60 约 19ms，
        光这一层就能吃掉一帧，而 hover 移动和滚轮缩放都会反复触发。改成
        「内容不变时只贴一张图」后每帧只剩 1 次 drawPixmap。

        失效：set_cells 发现图章内容变化时清空；图集换图走 tile_cache.clear_cache()
        会让 cache_version() 递增，这里同步重建。超大图章（超过像素上限）
        返回 None，paint 回退到逐格绘制（同样按可见区域裁剪）。
        """
        if not self.stamp or not self.stamp[0] or self.doc is None:
            return None
        cols, rows = len(self.stamp[0]), len(self.stamp)
        tw, th = self.doc.tile_w, self.doc.tile_h
        w, h = cols * tw, rows * th
        if w <= 0 or h <= 0 or w * h > _MAX_PREVIEW_PIXELS:
            return None
        version = cache_version()
        if self._tile_pixmap is not None and self._tile_pix_version == version:
            return self._tile_pixmap
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        for dy, line in enumerate(self.stamp):
            y = dy * th
            for dx, raw in enumerate(line):
                if not raw:
                    continue
                # split_gid 返回 (gid, 水平翻转, 垂直翻转, 对角翻转)。
                gid, flip_h, flip_v, flip_d = split_gid(raw)
                located = self.doc.local_tile(gid)
                if located is None:
                    continue
                ts, local_id = located
                tile = get_pixmap(ts, local_id, (flip_h, flip_v, flip_d),
                                  self.doc.resolver)
                if tile is not None:
                    p.drawPixmap(dx * tw, y, tile)
        p.end()
        self._tile_pixmap = pix
        self._tile_pix_version = version
        return pix

    # ---------------- 绘制 ----------------
    def paint(self, painter, option, widget=None):
        if not self.stamp:
            return
        tw, th = self.doc.tile_w, self.doc.tile_h
        ox, oy = self._origin()
        tx, ty = ox * tw, oy * th
        rows, cols = len(self.stamp), len(self.stamp[0])
        map_rect = QRectF(0, 0, self.doc.width * tw, self.doc.height * th)
        content = self._content_rect()
        # exposedRect 是 item 局部坐标（本 item 无变换，等同场景坐标）。
        # 只处理与可见区域相交的部分：放大到 800% 时视口往往只覆盖图章一角，
        # 旧代码却把屏幕外的几千格也逐格 drawPixmap 了一遍。
        exposed = getattr(option, "exposedRect", None) if option is not None else None
        visible = content if exposed is None else exposed.intersected(content)
        if visible.isEmpty():
            return
        local_visible = visible.translated(-tx, -ty)

        # ---- 半透明瓦片影像 ----
        cache = self._tile_preview_pixmap()
        painter.setOpacity(0.5)
        if cache is not None:
            src = local_visible.intersected(
                QRectF(0, 0, cache.width(), cache.height()))
            if not src.isEmpty():
                painter.drawPixmap(
                    QRectF(tx + src.left(), ty + src.top(), src.width(), src.height()),
                    cache, src)
        else:
            # 超大图章回退：仍然只遍历与可见区域相交的格。
            c0 = max(0, int((visible.left() - tx) // tw))
            c1 = min(cols - 1, int((visible.right() - tx) // tw))
            r0 = max(0, int((visible.top() - ty) // th))
            r1 = min(rows - 1, int((visible.bottom() - ty) // th))
            for dy in range(r0, r1 + 1):
                line = self.stamp[dy]
                for dx in range(c0, c1 + 1):
                    raw = line[dx]
                    if not raw:
                        continue
                    gid, flip_h, flip_v, flip_d = split_gid(raw)
                    located = self.doc.local_tile(gid)
                    if located is None:
                        continue
                    ts, local_id = located
                    tile = get_pixmap(ts, local_id, (flip_h, flip_v, flip_d),
                                      self.doc.resolver)
                    if tile is not None:
                        painter.drawPixmap(tx + dx * tw, ty + dy * th, tile)
        painter.setOpacity(1.0)
        # 按「实际格子集合」画填充 + 外轮廓：每条线都压在区域边界上（不内缩），
        # L 形 / 凹角的内侧边也在轮廓里，录屏角落缺的两条线由此补齐。
        # 越界（地图外）的格用红色半透明填充 + 红色轮廓提示；地图内的格用蓝色
        # 填充 + 蓝色轮廓。用户之前说「去掉蓝色边框」只针对 1×1 的单格悬停
        # 小蓝框，右键拖动抓取 / 多格图章的蓝框必须保留。
        single_cell = len(self.stamp) == 1 and len(self.stamp[0]) == 1
        show_blue = (bool(self._blue_cells)
                     and (self.mode == "topleft" or not single_cell))
        red_pen = QPen(QColor(255, 70, 70), 1.6)
        red_fill = QColor(255, 70, 70, 70)
        # tx,ty 已在开头算好：局部几何 → 场景坐标的平移量。
        # 填充矩形同样按可见区域过滤，避免大图章每帧上千次 fillRect。
        if self._red_cells:
            painter.save()
            painter.translate(tx, ty)
            for rect in self._red_fill:
                if rect.intersects(local_visible):
                    painter.fillRect(rect, red_fill)
            painter.restore()
        if show_blue:
            # 顺序：红填充 → 蓝填充 + 蓝轮廓 → 红轮廓（裁到地图外）。
            # 共享边界上蓝贴内侧半条、红贴外侧半条，冲突处蓝色占优。
            painter.save()
            painter.translate(tx, ty)
            for rect in self._blue_fill:
                if rect.intersects(local_visible):
                    painter.fillRect(rect, SELECT_FILL)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(SELECT, 1.6))
            painter.drawPath(self._blue_path)
            painter.restore()
        if self._red_cells:
            # 裁剪区 = item 绘制范围减去地图矩形（OddEvenFill 即「挖洞」），
            # 用「平移后的局部坐标」构造：红线只落在地图外，不会渗进地图内；
            # L 形内侧的两条边因此保留外侧半条，与蓝线内侧半条并排贴在分界上。
            outside = QPainterPath()
            outside.setFillRule(Qt.OddEvenFill)
            outside.addRect(self.boundingRect().translated(-tx, -ty))
            outside.addRect(map_rect.translated(-tx, -ty))
            painter.save()
            painter.translate(tx, ty)
            painter.setClipPath(outside)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(red_pen)
            painter.drawPath(self._red_path)
            painter.restore()


class MapScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        self.layer_items: dict[int, object] = {}
        self.grid: GridOverlay | None = None
        self._selected: list = []         # 当前选中对象（支持框选多选），保持顺序
        self._primary = None             # 主对象：属性面板 / 删除 / 拖动编辑用它
        self._selection_item = None
        self._obj_sel_box = None       # 对象选中包围框（含全部选中对象的整体外接矩形）
        self._stamp_preview = None
        self._pen_preview = None
        # ---- 纯视图显示开关（不写文档，不进撤销栈，不进 TMX） ----
        # current_layer 是「编辑语义」的当前层（只能是 TileLayer，map_view 靠它
        # 取 raw_gid），由 main_window 动态赋值；不要把它改成 property，否则对象
        # 层选中时会被写成 None 影响取色。视图开关另用 view_current_layer。
        self.current_layer = None
        self._view_current_layer = None   # 视图语义的当前层（允许对象层）
        self._highlight_current = False   # 当前层高亮：其它层变暗
        self._only_current = False        # 只显示当前层
        self._show_object_names = True    # 显示对象名称（默认与历史行为一致）

    @property
    def view_current_layer(self):
        return self._view_current_layer

    @view_current_layer.setter
    def view_current_layer(self, layer):
        self._view_current_layer = layer
        if self._highlight_current or self._only_current:
            self._apply_layer_view_state()

    @property
    def selected_object(self):
        """向后兼容：返回主对象（列表里的第一个）。"""
        return self._primary

    @selected_object.setter
    def selected_object(self, obj):
        self.set_selected_object(obj)

    @property
    def selected_objects(self):
        return self._selected

    def set_document(self, doc):
        self.clear()
        self.doc = doc
        self._stamp_preview = None
        self._pen_preview = None      # clear() 会销毁 item，引用必须一并清空
        # clear() 会销毁场景里所有 item（包括下面的选框），Python 引用必须一并清空，
        # 否则下次 set_selection_rect/set_object_selection_box 会访问已删除的 C++ 对象。
        self._selection_item = None
        self._obj_sel_box = None
        # 先重置内部选中态；新 item 默认空选，不要在这里迭代旧 item
        # （clear() 已销毁它们，访问会触发 C++ 对象已删除）。
        self._selected = []
        self._primary = None
        self._obj_sel_box = None
        # clear() 销毁了全部 item，新建的 ObjectLayerItem 默认画名称标签；
        # 这里必须回写用户开关，否则「关掉名称 → 切文档」标签会自己回来。
        self._view_current_layer = None
        self.layer_items = build_layer_items(doc)
        for item in self.layer_items.values():
            self.addItem(item)
        # 新建 item 的 visible / opacity 来自文档字段，必须叠加用户的视图开关，
        # 否则「高亮当前层 / 只显示当前层 → 切文档」会失效。
        self._apply_layer_view_state()
        self._apply_show_object_names()
        if doc is not None:
            self.grid = GridOverlay(doc)
            self.addItem(self.grid)
            # 四周留一圈虚拟空间：地图 fit 后可能比视口还窄，Qt 会把内容
            # 强制居中且滚动条范围归零，导致缩放锚点无法补偿（视图乱跳）。
            # sceneRect 变大后滚动条始终有行程；fitInView 仍只用地图本身的矩形。
            w, h = doc.width * doc.tile_w, doc.height * doc.tile_h
            pad = max(w, h) * 4
            self.setSceneRect(-pad, -pad, w + 2 * pad, h + 2 * pad)
        else:
            self.grid = None
            self.setSceneRect(0, 0, 800, 600)

    def refresh_layer(self, layer, cells=None):
        item = self.layer_items.get(id(layer))
        if item is None:
            return
        if cells is None:
            item.invalidate_all()
        elif hasattr(item, "invalidate_cells"):
            item.invalidate_cells(cells)
        else:
            item.update()

    # ---------------- 对象层选中 / 刷新 ----------------
    def set_selected_object(self, obj):
        """单选一个对象（兼容旧调用）；传 None 清空。"""
        self.set_selected_objects([obj] if obj is not None else [])

    def set_selected_objects(self, objs):
        """批量设置选中对象（框选多选用）。

        ``MapObject`` 是 dataclass，自动生成的 ``__eq__`` 会禁用 ``__hash__``，
        所以这里用列表保存、按对象 identity 判等；``item.set_selected`` 内部只标脏
        真正变化的部分，避免整张地图重绘。主对象取列表第一个，供属性面板 / 删除 / 拖动。
        """
        new = [o for o in objs if o is not None]
        if [id(o) for o in new] == [id(o) for o in self._selected]:
            return
        self._selected = new
        self._primary = new[0] if new else None
        for item in self.layer_items.values():
            if isinstance(item, ObjectLayerItem):
                item.set_selected(self._selected)
        # 选中态变化后同步更新「整体包围框」（含 8 个可拖拽的黄色缩放箭头）。
        self.set_object_selection_box(self._selection_combined_bounds())

    def refresh_objects(self):
        for item in self.layer_items.values():
            if isinstance(item, ObjectLayerItem):
                item.refresh_all()

    def set_show_object_nodes(self, enabled: bool):
        for item in self.layer_items.values():
            if isinstance(item, ObjectLayerItem):
                item.set_show_nodes(enabled)

    def set_object_preview(self, obj):
        for item in self.layer_items.values():
            if isinstance(item, ObjectLayerItem):
                item.set_preview(obj)

    def refresh_object_rects(self, layer, rects):
        item = self.layer_items.get(id(layer))
        if isinstance(item, ObjectLayerItem):
            item.refresh_rects(rects)

    def refresh_object_region(self, obj):
        """对象被移动 / 改名时，只标脏它自己的那块矩形。"""
        item = None
        for candidate in self.layer_items.values():
            if isinstance(candidate, ObjectLayerItem) and obj in candidate.layer.objects:
                item = candidate
                break
        if item is not None:
            item.refresh_rects([object_rect(obj)])

    # ---------------- 纯视图显示开关 ----------------
    # 三个开关只决定「QGraphicsItem 怎么画」，永远不改 layer.visible /
    # layer.opacity（那两个会写进 TMX、进撤销栈）。所有可能覆盖 item 状态的
    # 路径（图层面板眼睛、透明度滑杆、切层、切文档）都必须经过下面两个
    # _effective_* 计算，否则开关会被一次普通刷新悄悄冲掉。
    def _effective_visible(self, layer, base: bool) -> bool:
        if not base:
            return False
        if self._only_current and self._view_current_layer is not None \
                and layer is not self._view_current_layer:
            return False
        return True

    def _effective_opacity(self, layer, base: float) -> float:
        if self._highlight_current and self._view_current_layer is not None \
                and layer is not self._view_current_layer:
            return base * _DIM_OPACITY
        return base

    def _apply_layer_view_state(self):
        for item in self.layer_items.values():
            layer = getattr(item, "layer", None)
            if layer is None:
                continue
            vis = self._effective_visible(layer, bool(layer.visible))
            opa = self._effective_opacity(layer, float(layer.opacity))
            # setVisible / setOpacity 由 Qt 标脏该 item，不能调无 rect 的
            # scene.update()（会标脏覆盖全图的 overlay，README 明确禁止）。
            if item.isVisible() != vis:
                item.setVisible(vis)
            if abs(item.opacity() - opa) > 1e-6:
                item.setOpacity(opa)

    def set_view_layer_options(self, *, highlight: bool, only_current: bool,
                               show_names: bool):
        """「视图」菜单里三个显示开关的统一入口。"""
        self._highlight_current = bool(highlight)
        self._only_current = bool(only_current)
        self._show_object_names = bool(show_names)
        self._apply_layer_view_state()
        self._apply_show_object_names()

    def _apply_show_object_names(self):
        for item in self.layer_items.values():
            if isinstance(item, ObjectLayerItem):
                item.set_show_names(self._show_object_names)

    def set_show_object_names(self, enabled: bool):
        self._show_object_names = bool(enabled)
        self._apply_show_object_names()

    def set_layer_visible(self, layer, visible: bool):
        item = self.layer_items.get(id(layer))
        if item is not None:
            item.setVisible(self._effective_visible(layer, bool(visible)))

    def set_layer_opacity(self, layer, value: float):
        item = self.layer_items.get(id(layer))
        if item is not None:
            item.setOpacity(self._effective_opacity(layer, float(value)))

    def set_cursor_cell(self, col: int, row: int):
        if self.grid is not None:
            # 悬停黄色单格框已移除；这里只记录光标格，不再产生任何重绘，
            # 也绝不能调用 scene/grid 的全局 update（整图重绘极慢）。
            self.grid.set_cell(col, row)

    def set_grid_visible(self, visible: bool):
        """「视图 → 网格」开关 / 切文档时同步网格可见性。"""
        if self.grid is not None:
            self.grid.set_show_grid(visible)

    def set_selection_rect(self, rect):
        """在画布上画 / 清除瓦片矩形选框（像素坐标 rect 或 None）。

        复用同一个 QGraphicsRectItem 原地更新，避免每次 move 重建 item 导致
        MinimalViewportUpdate 下旧选框像素不重绘而留下拖影。
        """
        if rect is None:
            item = self._selection_item
            if item is not None:
                # 文档重载后 clear() 可能已销毁该 item，访问会抛
                # "Internal C++ object already deleted"，这里容错并释放引用。
                try:
                    item.setVisible(False)
                except RuntimeError:
                    self._selection_item = None
            return
        x0, y0, x1, y1 = rect
        if self._selection_item is None:
            item = QGraphicsRectItem()
            item.setPen(QPen(QColor(80, 170, 245), 0.6, Qt.DashLine))
            item.setBrush(QColor(80, 170, 245, 45))
            item.setZValue(2000)
            self.addItem(item)
            self._selection_item = item
        self._selection_item.setRect(x0, y0, x1 - x0, y1 - y0)
        self._selection_item.setVisible(True)

    # ---------------- 对象选中包围框 ----------------
    def _selection_combined_bounds(self):
        """所有选中对象的整体外接矩形（场景坐标）；无选中返回 None。"""
        objs = self._selected
        if not objs or self.doc is None:
            return None
        tw, th = self.doc.tile_w, self.doc.tile_h
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        for o in objs:
            x, y, w, h = o.bounds(tw, th)
            x0, y0 = min(x0, x), min(y0, y)
            x1, y1 = max(x1, x + w), max(y1, y + h)
        if x1 < x0 or y1 < y0:
            return None
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    def set_object_selection_box(self, rect):
        """画 / 清除对象选中包围框（场景坐标 QRectF 或 None）。

        复用同一个 item 原地更新，避免每次重建在 MinimalViewportUpdate 下留下
        旧框拖影。框表达「整体选中范围」，四角 / 四边另画 8 个黄色双向缩放箭头
        （与对象框同色，可拖拽改尺寸）。
        """
        if rect is None:
            item = self._obj_sel_box
            if item is not None:
                try:
                    item.setVisible(False)
                except RuntimeError:
                    self._obj_sel_box = None
            return
        if self._obj_sel_box is None:
            item = ObjectSelectionBoxItem()
            self.addItem(item)
            self._obj_sel_box = item
        views = self.views()
        if views:
            self._obj_sel_box.set_view_scale(views[0].transform().m11())
        self._obj_sel_box.setRect(rect)
        self._obj_sel_box.setVisible(True)

    def refresh_selection_handles_scale(self, scale: float):
        """视图缩放变化时同步箭头尺寸（缩放是视图操作，不该进撤销栈）。"""
        item = self._obj_sel_box
        if item is None:
            return
        try:
            item.set_view_scale(scale)
        except RuntimeError:
            self._obj_sel_box = None

    def set_stamp_preview(self, stamp, col: int, row: int):
        """显示跟随光标的半透明图章预览（中心格对齐 col,row）。"""
        if self.doc is None:
            return
        if self._stamp_preview is None:
            self._stamp_preview = TileStampPreviewItem(self.doc)
            self.addItem(self._stamp_preview)
        self._stamp_preview.set_cells(stamp, col, row, "center")

    def set_capture_preview(self, cells_2d, col0: int, row0: int):
        """右键拖动抓取时：把要抓的区域（当前层实时格）以半透明虚影显示在抓取位置。"""
        if self.doc is None:
            return
        if self._stamp_preview is None:
            self._stamp_preview = TileStampPreviewItem(self.doc)
            self.addItem(self._stamp_preview)
        self._stamp_preview.set_cells(cells_2d, col0, row0, "topleft")

    def clear_capture_preview(self):
        # 复用同一 item，只清空内容并标脏旧区域；不 removeItem，
        # 否则固定整图 boundingRect 会让 Qt 把整张地图标脏（一次全图重绘）。
        if self._stamp_preview is not None:
            self._stamp_preview.clear_cells()

    def clear_stamp_preview(self):
        # 抓取虚影与图章虚影共用同一 item，松手 / 移出画布时统一清除。
        self.clear_capture_preview()

    # ---------------- 钢笔矢量路径预览 ----------------
    def set_pen_path(self, path, active=None):
        """显示 / 刷新编辑中的贝塞尔曲线；复用同一 item，避免 MinimalViewportUpdate 拖影。"""
        if self.doc is None:
            return
        if self._pen_preview is None:
            self._pen_preview = PenPathPreviewItem(self.doc)
            self.addItem(self._pen_preview)
        self._pen_preview.set_path(path, active)

    def clear_pen_preview(self):
        # 只清空内容并标脏旧区域；不 removeItem（会触发整图脏区）。
        if self._pen_preview is not None:
            self._pen_preview.clear()
