"""右键抓取区域作图章 + 右键拾取单格 + 图章/抓取浮动预览（离屏）。

验证：
- 右键拖动 → regionCaptureRequested → state.stamp 为对应 2D gid 区域（含翻转标志）。
- 右键单击 → rightPickRequested → 严格当前层拾取、空格不改笔刷、并同步图块集高亮。
- 多格图章以「中间格」对齐光标：press/move 原点 = 光标格 - (W//2, H//2)。
- 图章浮动预览层 set_stamp_preview / clear_stamp_preview 的创建与移除。
- 右键拖动时不再画蓝色选框，改为实时区域虚影：captureRectChanged 发格坐标，
  _on_capture_rect 读当前层实时格生成 topleft 对齐的抓取虚影；松手 clear。
- map_view 鼠标事件层：右键单击走拾取、右键拖动走抓区域、并实时发抓取区域格坐标。
"""
import os
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _build_window_and_doc():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    from rwmap.document.layer import TileLayer
    from rwmap.document.map_document import MapDocument

    w = MainWindow(auto_open=False)
    # 10x10 单瓦片层，左上角放一个 3x3 的 1..9 图案，其余为 0。
    block = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    cells = [0] * 100
    for r in range(3):
        for c in range(3):
            cells[r * 10 + c] = block[r][c]
    layer = TileLayer(width=10, height=10, name="Ground",
                      cells=cells, readonly=False)
    doc = MapDocument(width=10, height=10, tile_w=20, tile_h=20,
                      layers=[layer], tilesets=[])
    w.state.doc = doc
    w.state.current_layer = layer
    w.scene.doc = doc
    w.state.profile.write_enabled = True
    w.state.brush_gid = 0
    w.state.stamp = None
    w._hover_cell = (-1, -1)
    return w, layer, doc


class RightClickCaptureTest(unittest.TestCase):
    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()

    def test_rightdrag_captures_region(self):
        # 模拟右键拖动抓取左上 3x3 区域
        self.w.view.regionCaptureRequested.emit(0, 0, 2, 2)
        stamp = self.w.state.stamp
        self.assertIsNotNone(stamp)
        self.assertEqual(len(stamp), 3)
        self.assertEqual(len(stamp[0]), 3)
        # 与图层单元格一致（含 raw gid，这里没有翻转标志）
        self.assertEqual(stamp[0], [1, 2, 3])
        self.assertEqual(stamp[1], [4, 5, 6])
        self.assertEqual(stamp[2], [7, 8, 9])

    def test_rightclick_click_not_region(self):
        # 右键单击（发射 rightPickRequested）应走拾取分支，不生成区域图章
        self.w.state.stamp = [[1, 1], [1, 1]]
        with mock.patch.object(self.w.tilesets_dock, "select_gid",
                               return_value=True) as m:
            self.w.view.rightPickRequested.emit(1, 1)   # 该格 gid=5
        self.assertIsNone(self.w.state.stamp)
        self.assertEqual(self.w.state.brush_gid, 5)
        m.assert_called_once_with(5)


class RightClickPickTest(unittest.TestCase):
    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()

    def test_pick_single_sets_brush_and_highlights(self):
        with mock.patch.object(self.w.tilesets_dock, "select_gid",
                               return_value=True) as m:
            self.w._right_pick_gid(1, 1)   # gid=5
        self.assertEqual(self.w.state.brush_gid, 5)
        self.assertIsNone(self.w.state.stamp)
        m.assert_called_once_with(5)

    def test_pick_empty_cell_keeps_brush(self):
        self.w.state.brush_gid = 42
        with mock.patch.object(self.w.tilesets_dock, "select_gid",
                               return_value=True) as m:
            self.w._right_pick_gid(5, 5)   # 空格 = 0，未拾取
        self.assertEqual(self.w.state.brush_gid, 42)   # 笔刷不变
        self.assertIsNone(self.w.state.stamp)
        m.assert_not_called()


class StampCenterAnchorTest(unittest.TestCase):
    """多格图章以「中间格」对齐光标：原点 = 光标格 - (W//2, H//2)。"""

    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()
        # 3x3 多格图章
        self.w.state.stamp = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
        self.w.state.brush_gid = 5

    def test_press_centers_on_cursor(self):
        self.w._on_press(5, 5)   # 光标在中心格
        stroke = self.w.state._stroke
        self.assertIsNotNone(stroke)
        # 左上角原点应为 (5-1, 5-1) = (4,4)
        self.assertEqual(stroke["bounds"][:2], [4, 4])
        self.w._on_release()

    def test_move_centers_on_cursor(self):
        # 先按下形成一个图章笔划，再移动到 (7,8)：图章中心应落在 (7,8)。
        self.w._on_press(5, 5)
        self.w._on_move(7, 8)
        # 移动后原点 = (7-1, 8-1) = (6,7)，图章左上角 (6,7) 应为 stamp[0][0]=1，
        # 中心 (7,8) 应为 stamp[1][1]=5。
        self.assertEqual(self.layer.get(6, 7), 1)
        self.assertEqual(self.layer.get(7, 8), 5)
        self.w._on_release()


class StampPreviewTest(unittest.TestCase):
    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()

    def test_preview_shows_and_clears(self):
        self.w.state.stamp = [[1, 2], [3, 4]]
        self.w.state.tool_id = "brush"
        # hover 触发预览（多格 stamp → set_stamp_preview）
        self.w._on_hover(3, 3, 1)
        item = self.w.scene._stamp_preview
        self.assertIsNotNone(item)
        self.assertEqual(item.stamp, [[1, 2], [3, 4]])
        self.assertEqual((item.col, item.row), (3, 3))
        # 无图章 / 单格时清空预览（复用同一 item，只清内容，不 removeItem）
        self.w.state.stamp = None
        self.w._on_hover(3, 3, 1)
        self.assertIs(self.w.scene._stamp_preview, item)
        self.assertIsNone(item.stamp)
        self.assertEqual(item._blocks, [])


class MapViewRightClickRoutingTest(unittest.TestCase):
    """直接驱动 view 的鼠标事件，验证右键「单击=拾取 / 拖动=抓区域 + 实时区域虚影」。"""

    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()
        # 让 view 持有 doc；用 mapFromScene 反推像素，避免依赖具体变换。
        self.w.view.set_document(self.doc)
        self.w.state.brush_gid = 0
        self.w.state.stamp = None
        self.received = []

    def _cell_pixel(self, col, row):
        # 取该格中心附近的像素（避开格边界，防止取整漂移）。
        return self.w.view.mapFromScene(
            QPointF(col * self.doc.tile_w + 1, row * self.doc.tile_h + 1))

    def _press_cell(self, col, row):
        self.w.view.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, self._cell_pixel(col, row),
            Qt.RightButton, Qt.RightButton, Qt.NoModifier))

    def _move_cell(self, col, row, buttons=Qt.RightButton):
        self.w.view.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, self._cell_pixel(col, row),
            Qt.NoButton, buttons, Qt.NoModifier))

    def _release_cell(self, col, row):
        self.w.view.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, self._cell_pixel(col, row),
            Qt.RightButton, Qt.NoButton, Qt.NoModifier))

    def test_click_emits_pick_not_region(self):
        # 右键单击同格：(2,2)
        self.w.view.rightPickRequested.connect(
            lambda c, r: self.received.append(("pick", c, r)))
        self.w.view.regionCaptureRequested.connect(
            lambda *a: self.received.append(("region",) + a))
        self._press_cell(2, 2)
        self._release_cell(2, 2)
        kinds = [e[0] for e in self.received]
        self.assertIn("pick", kinds)
        self.assertNotIn("region", kinds)
        self.assertIn(("pick", 2, 2), self.received)

    def test_drag_emits_region_and_live_ghost(self):
        rects = []
        self.w.view.regionCaptureRequested.connect(
            lambda *a: self.received.append(("region",) + a))
        self.w.view.captureRectChanged.connect(rects.append)
        # (2,2) 拖到 (5,5)
        self._press_cell(2, 2)
        self._move_cell(5, 5)
        # 拖动中应发出抓取区域格坐标（不再是像素选框）
        self.assertIn((2, 2, 5, 5), rects)
        # 且应生成实时区域虚影（读取当前层实时格，topleft 对齐）
        item = self.w.scene._stamp_preview
        self.assertIsNotNone(item)
        self.assertEqual(item.mode, "topleft")
        self.assertEqual((item.col, item.row), (2, 2))
        # 抓取虚影内容应等于当前层该区域实时格
        expected = [[self.layer.get(c, r) for c in range(2, 6)]
                    for r in range(2, 6)]
        self.assertEqual(item.stamp, expected)
        self._release_cell(5, 5)
        self.assertIn(("region", 2, 2, 5, 5), self.received)
        # release 末尾应清抓取虚影（发出 None → 复用 item 清空内容）
        self.assertTrue(rects[-1] is None)
        self.assertIs(self.w.scene._stamp_preview, item)
        self.assertIsNone(item.stamp)
        # 抓区域结果应写入 state.stamp（4x4 区域）
        self.assertEqual(len(self.w.state.stamp), 4)
        self.assertEqual(len(self.w.state.stamp[0]), 4)


class CapturePreviewTest(unittest.TestCase):
    """抓取虚影：读取当前层实时格、topleft 对齐、可清除。"""

    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()

    def _render(self):
        """把整张场景渲染成 QImage（黑底），用于像素级断言。"""
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtCore import QRectF, Qt
        tw, th = self.doc.tile_w, self.doc.tile_h
        W, H = self.doc.width * tw, self.doc.height * th
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        self.w.scene.render(p, QRectF(0, 0, W, H), QRectF(0, 0, W, H))
        p.end()
        return img

    @staticmethod
    def _is_blue(c):
        # 匹配蓝色描边 (47,134,220)。描边线宽调细到 0.6px 后，抗锯齿会把
        # 描边与黑底/半透明填充混合，实测约为 (34,98,160)；半透明填充叠黑底
        # 则是暗蓝 (16,45,73)。因此按「明显偏蓝且足够亮」判定，只认描边、
        # 不认填充，也不会把越界红框误判成蓝框。
        return (c.blue() > 110
                and c.blue() > c.red() + 60
                and c.green() > c.red() + 30
                and c.green() > 60)

    def test_capture_preview_reads_live_layer(self):
        # 在 (3,4) 放一个非空 gid，抓取含它的区域，虚影应反映实时值。
        self.layer.set(3, 4, 123)
        self.w._on_capture_rect((2, 3, 5, 6))   # c0,r0,c1,r1
        item = self.w.scene._stamp_preview
        self.assertIsNotNone(item)
        self.assertEqual(item.mode, "topleft")
        self.assertEqual((item.col, item.row), (2, 3))
        # stamp 行/列：rows r0..r1 (3..6)，cols c0..c1 (2..5)
        # 第 (4-3)=1 行、第 (3-2)=1 列 应为 123
        self.assertEqual(item.stamp[1][1], 123)
        # 左上角 (2,3) 该层原为 0
        self.assertEqual(item.stamp[0][0], 0)

    def test_capture_preview_clear(self):
        self.w._on_capture_rect((0, 0, 2, 2))
        item = self.w.scene._stamp_preview
        self.assertIsNotNone(item)
        self.w._on_capture_rect(None)
        self.assertIs(self.w.scene._stamp_preview, item)   # 复用，不 removeItem
        self.assertIsNone(item.stamp)
        self.assertEqual(item._blocks, [])

    def test_multicell_preview_keeps_blue_border(self):
        # 用户澄清：要去掉的只是 1×1 那个小蓝框；右键拖动抓取 / 多格图章
        # 的蓝色边框必须保留。3×3 抓取虚影全在地图内 → 应有蓝色描边。
        img = self._render()
        base = sum(self._is_blue(img.pixelColor(x, y))
                   for y in range(img.height()) for x in range(img.width()))
        self.assertEqual(base, 0, "基线（无预览）不该有蓝线，测试前提失效")
        self.w._on_capture_rect((0, 0, 2, 2))
        img = self._render()
        blue = sum(self._is_blue(img.pixelColor(x, y))
                   for y in range(img.height()) for x in range(img.width()))
        self.assertGreater(blue, 0,
                           "右键拖动 / 多格图章的蓝色边框被误删了")
        item = self.w.scene._stamp_preview
        self.assertIsNotNone(item)
        self.assertEqual(item._blocks, [(0, 0, 2, 2)])

    def test_single_cell_preview_has_no_blue_border(self):
        # 1×1 单格悬停预览：这正是用户不要的那个蓝色小框，不能有蓝像素。
        self.w.scene.set_stamp_preview([[1]], 3, 3)
        img = self._render()
        blue = sum(self._is_blue(img.pixelColor(x, y))
                   for y in range(img.height()) for x in range(img.width()))
        self.assertEqual(blue, 0, "1×1 单格预览仍画了蓝色小框")

    def test_border_height_covers_all_rows(self):
        # 回归「描边拿不到高度」：3 行全非空、整块越界时，底部行也必须有红色边框。
        # （旧代码把行数 h 解包覆盖成 flip_h(0/1)，rect 高度恒为 0~1 格，底行空白；
        # 该 bug 同时影响红 / 蓝路径，这里用越界红框验证。）
        stamp = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
        # 用 topleft 把图章左上角直接对齐到地图右下角外侧，整块 3x3 全部越界。
        # center 模式会把原点左移一格，左上角那格留在地图内，渲染视窗也会与
        # 图章原点错开一格，导致底边线不在预期位置。
        self.w.scene.set_capture_preview(stamp, self.doc.width, self.doc.height)
        item = self.w.scene._stamp_preview
        self.assertEqual(item._red_blocks, [(0, 0, 2, 2)])
        tw, th = self.doc.tile_w, self.doc.tile_h
        ox, oy = self.doc.width * tw, self.doc.height * th   # 越界起点（场景坐标）
        # 底边横线正好压在 y = 3*th 上，渲染视窗留 4px 余量才看得到它。
        pad = 4
        img = self._render_rect((ox, oy, 3 * tw + pad, 3 * th + pad),
                                3 * tw + pad, 3 * th + pad)
        # 第三行（图像 y = 2*th .. 3*th）内应能找到红色边框像素
        self.assertTrue(
            self._red_in(img, 0, 2 * th, 3 * tw, 3 * th + pad),
            "红框高度只有 1 格：底部两行没有边框像素")
        # 底边（压在 y = 3*th 的横线上）尤其要有
        self.assertTrue(
            self._red_in(img, 0, 3 * th - 2, 3 * tw, 3 * th + pad),
            "底边横线没画到最下面一行")

    def test_disconnected_blocks_separate_borders(self):
        # 左右两列瓦片被中间一列空格隔开 → 两个独立红框，中间空格不涂红。
        stamp = [[1, 0, 1], [1, 0, 1], [1, 0, 1]]
        # 同样用 topleft 让整块落在地图外：center 模式的原点偏移会把 (0,0)
        # 留在地图内，使其不属于红区，连通块就会少一格。
        self.w.scene.set_capture_preview(stamp, self.doc.width, self.doc.height)
        item = self.w.scene._stamp_preview
        self.assertEqual(item._red_blocks, [(0, 0, 0, 2), (2, 0, 2, 2)])
        tw, th = self.doc.tile_w, self.doc.tile_h
        ox, oy = self.doc.width * tw, self.doc.height * th
        img = self._render_rect((ox, oy, 3 * tw, 3 * th), 3 * tw, 3 * th)
        self.assertTrue(self._red_in(img, 0, 0, tw, 3 * th), "左块缺红框")
        self.assertTrue(self._red_in(img, 2 * tw, 0, 3 * tw, 3 * th), "右块缺红框")
        # 中间空列（留 3px 余量避开相邻边框）必须无红色
        self.assertFalse(
            self._red_in(img, tw + 3, 3, 2 * tw - 3, 3 * th - 3),
            "空格列被涂红 / 画了框")

    def test_bounding_rect_stable_full_map(self):
        # boundingRect 固定（不随 col/row/stamp 变化）是「无拖影」的前提；同时要比地图大
        # 圈留白，才能把拖到地图外的图章格也纳入脏区、让越界部分可见。
        item = self.w.scene._stamp_preview
        self.assertIsNone(item)
        self.w.scene.set_capture_preview([[1, 1], [1, 1]], 0, 0)
        item = self.w.scene._stamp_preview
        tw, th = self.doc.tile_w, self.doc.tile_h
        w = self.doc.width * tw
        h = self.doc.height * th
        pad = max(w, h) * 4   # 与 TileStampPreviewItem._PAD_FACTOR 对齐
        full = QRectF(-pad, -pad, w + 2 * pad, h + 2 * pad)
        self.assertEqual(item.boundingRect(), full)
        self.w.scene.set_stamp_preview([[1, 1, 1], [1, 1, 1]], 6, 7)
        self.assertEqual(item.boundingRect(), full)
        self.w.scene.clear_stamp_preview()
        self.assertEqual(item.boundingRect(), full)

    @staticmethod
    def _is_red(c):
        # 红框 (255,70,70)：R 明显高于 G/B。
        return c.red() > 150 and c.green() < 130 and c.blue() < 130

    def _red_in(self, img, x0, y0, x1, y1):
        return any(self._is_red(img.pixelColor(x, y))
                   for y in range(y0, y1) for x in range(x0, x1))

    @staticmethod
    def _is_blank(img, x0, y0, x1, y1):
        return all(img.pixelColor(x, y).black() == 0
                   for y in range(y0, y1) for x in range(x0, x1))

    def test_offmap_cells_show_red_border(self):
        # 把 2x2 图章拖到地图右下角之外（右侧/下侧格落在地图外）：越界格应可见且带红框。
        # 该测试只渲染越界那一格所在的场景区域（地图外），故需自定义 render 范围。
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtCore import QRectF, Qt
        self.w.scene.set_stamp_preview([[1, 1], [1, 1]], self.doc.width, self.doc.height)
        tw, th = self.doc.tile_w, self.doc.tile_h
        ox = self.doc.width * tw      # 越界格起点（地图右下角外侧）
        oy = self.doc.height * th
        img = QImage(tw, th, QImage.Format_RGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        self.w.scene.render(p, QRectF(0, 0, tw, th), QRectF(ox, oy, tw, th))
        p.end()
        self.assertFalse(
            self._is_blank(img, 0, 0, tw, th),
            "越界图章未显示（应在地图外可见）")
        self.assertTrue(
            self._red_in(img, 0, 0, tw, th),
            "越界格缺少红色边框提示")

    def test_offmap_red_drawn_per_block_not_per_cell(self):
        # 越界红框应按连通块画整框（与蓝框一致），而不是逐格小框：
        # 2x2 全部越界的连续区域应只返回一个外接块，而非 4 个逐格块。
        from PySide6.QtCore import QRectF
        from rwmap.view.map_scene import TileStampPreviewItem
        tw, th = self.doc.tile_w, self.doc.tile_h
        stamp = [[1, 1], [1, 1]]
        # 整个 2x2 都放在地图右下角之外，四格都满足越界谓词
        ox, oy = self.doc.width * tw + tw, self.doc.height * th + th
        map_rect = QRectF(0, 0, self.doc.width * tw, self.doc.height * th)
        out_of_map = lambda dx, dy: not map_rect.intersects(
            QRectF((ox + dx) * tw, (oy + dy) * th, tw, th))
        blocks = TileStampPreviewItem._connected_blocks(
            stamp, include=out_of_map)
        self.assertEqual(
            len(blocks), 1,
            "越界红框应合并为整块外框，而不是逐格小框")
        self.assertEqual(blocks[0], (0, 0, 1, 1))

    def _render_rect(self, rect, w, h):
        """只把场景的 rect 区域渲染进 w×h 的图（用于看得见地图外的越界格）。"""
        from PySide6.QtGui import QImage, QPainter
        x, y, rw, rh = rect
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        self.w.scene.render(p, QRectF(0, 0, w, h), QRectF(x, y, rw, rh))
        p.end()
        return img

    def test_red_does_not_bleed_into_map_when_crossing_edge(self):
        # 回归录屏里的 bug：4x4 图章压在右下角，右 2 列 + 下 2 行出界。
        # 越界格连成 L 形，其外接矩形几乎就是整个图章；旧实现先蓝后红，
        # 红框按外接矩形整块填充 → 地图内本该蓝色的 2x2 全被涂红。
        # 现在红 / 蓝各自按自己的格算连通块，且红被裁剪在地图之外，绝不会
        # 侵入地图内部；地图内格子保留蓝色填充 + 蓝色边框。
        self.w.scene.set_capture_preview([[1] * 4] * 4, 8, 8)   # topleft (8,8)
        item = self.w.scene._stamp_preview
        self.assertEqual(item._blocks, [(0, 0, 1, 1)], "in-map 块应只含地图内的格")
        self.assertEqual(item._red_blocks, [(0, 0, 3, 3)], "越界格应整体成一块红块")
        img = self._render_rect((160, 160, 80, 80), 80, 80)
        # 地图内象限（场景 160..200）一点红都不能有（红框只画在地图外）
        red = any(self._is_red(img.pixelColor(x, y))
                  for y in range(2, 40) for x in range(2, 40))
        self.assertFalse(red, "红框侵入了地图内部（地图内应只有蓝框）")
        # 地图外的越界格仍要有红框
        self.assertTrue(
            any(self._is_red(img.pixelColor(x, y))
                for y in range(0, 80) for x in range(0, 80)
                if x >= 40 or y >= 40),
            "越界格缺少红色提示")

    def test_red_fill_never_bleeds_into_map_interior(self):
        # 图章压在左上角：整个首行 / 首列都在地图外，红块外接矩形横跨地图内的
        # 空白格。这些格不属于图章（没有蓝块），旧实现会被红填充整块染红；
        # 现在红色被裁剪在地图之外，地图内一格都不涂。
        stamp = [[1, 1, 1, 1],
                 [1, 0, 0, 0],
                 [1, 0, 0, 0],
                 [1, 0, 0, 0]]
        self.w.scene.set_capture_preview(stamp, -1, -1)   # topleft (-1,-1)
        item = self.w.scene._stamp_preview
        self.assertEqual(item._blocks, [], "所有非零格都在地图外，不该有蓝块")
        self.assertEqual(item._red_blocks, [(0, 0, 3, 3)])
        img = self._render_rect((-20, -20, 80, 80), 80, 80)
        # 地图区域在图中是 (20..80, 20..80)，避开边界 2px 的描边溢出
        red = [(x, y) for y in range(22, 80) for x in range(22, 80)
               if self._is_red(img.pixelColor(x, y))]
        self.assertEqual(red[:3], [], "红色填充越界涂进了地图内部")

    def test_corner_shared_border_has_red_line(self):
        # 录屏那个角落缺的两条线：这里只验证越界侧的红色 L 形内侧边。
        # 按实际格子集合画轮廓，越界区右 / 下边界正好压在 x/y=200 的地图边界上。
        self.w.scene.set_capture_preview([[1] * 4] * 4, 8, 8)
        img = self._render_rect((160, 160, 100, 100), 100, 100)
        # 竖分界：图 x=40（场景 x=200），红线落在越界侧（图像 x 40..44）
        self.assertTrue(
            any(self._is_red(img.pixelColor(x, y))
                for y in range(5, 35) for x in range(40, 44)),
            "角落缺红色竖线（红 L 内侧边）")
        # 横分界：图 y=40（场景 y=200）
        self.assertTrue(
            any(self._is_red(img.pixelColor(x, y))
                for x in range(5, 35) for y in range(40, 44)),
            "角落缺红色横线（红 L 内侧边）")

    def test_border_sits_on_cell_edge_not_inset(self):
        # 「线贴在单元格外沿，不要在里面」：2x2 图章完全在地图外（topleft (10,10)），
        # 外接矩形的红框必须正好压在单元格边缘（场景 x=200/240、y=200/240），
        # 而不是旧实现 ``adjusted(1,1,-1,-1)`` 缩进后的 201 / 239。
        # render 视窗覆盖场景 160..260（图像坐标 = 场景坐标 - 160）。
        img0 = self._render_rect((160, 160, 100, 100), 100, 100)
        self.assertFalse(self._red_in(img0, 38, 45, 42, 55),
                         "基线（无预览）不该有红边，测试前提失效")
        self.w.scene.set_capture_preview([[1, 1], [1, 1]], self.doc.width, self.doc.height)
        img = self._render_rect((160, 160, 100, 100), 100, 100)
        # 首列越界格左边缘正好压在地图边界（图像 x≈40 = 场景 x=200）
        self.assertTrue(self._red_in(img, 38, 45, 42, 55), "左/上红框没贴在外沿")
        # 右边缘（图像 x≈80 = 场景 x=240）
        self.assertTrue(self._red_in(img, 78, 45, 82, 55), "右红框往里缩了（应压在 x=240）")
        # 上边缘（图像 y≈40 = 场景 y=200）
        self.assertTrue(self._red_in(img, 45, 38, 55, 42), "上红框没贴在外沿")
        # 下边缘（图像 y≈80 = 场景 y=240）
        self.assertTrue(self._red_in(img, 45, 78, 55, 82), "下红框往里缩了（应压在 y=240）")

    def test_hole_cells_are_not_tinted(self):
        # 环形图章中间的空格不能被蓝填充涂色：蓝框恢复后依然按「实际格子
        # 集合」填充，外接矩形中心的空格必须保持干净，而外圈仍有蓝色描边。
        self.w.scene.set_capture_preview(
            [[1, 1, 1], [1, 0, 1], [1, 1, 1]], 0, 0)
        item = self.w.scene._stamp_preview
        self.assertNotIn((1, 1), item._blue_cells, "空格不该进入 in-map 格集合")
        img = self._render_rect((0, 0, 60, 60), 60, 60)
        self.assertLess(img.pixelColor(30, 30).blue(), 20,
                        "空格中心被填充涂到（外接矩形填充没拆开）")
        self.assertTrue(
            any(self._is_blue(img.pixelColor(x, y))
                for y in range(60) for x in range(60)),
            "多格图章的蓝色边框应保留")

    def test_hover_move_dirties_old_and_new_only(self):
        # 移动时只标脏「旧内容矩形 + 新内容矩形」，不整图重绘，也不留旧像素。
        # 脏区必须**外扩**：蓝/红轮廓压在内容矩形边界上，外侧半线宽在矩形外，
        # 不外扩就永远不被重绘（拖影）。见 _PREVIEW_DIRTY_MARGIN。
        from rwmap.view.map_scene import _PREVIEW_DIRTY_MARGIN
        self.w.scene.set_stamp_preview([[1, 1], [1, 1]], 3, 3)
        item = self.w.scene._stamp_preview
        old = QRectF(item._content_rect())
        rects = []
        with mock.patch.object(item, "update", side_effect=lambda r: rects.append(QRectF(r))):
            self.w.scene.set_stamp_preview([[1, 1], [1, 1]], 12, 12)
        new = QRectF(item._content_rect())
        self.assertTrue(any(r.contains(old) for r in rects),
                        "旧位置未被完整标脏 → 会留拖影")
        self.assertTrue(any(r.contains(new) for r in rects),
                        "新位置未被完整标脏")
        self.assertTrue(all(r.width() >= old.width() + 2 * _PREVIEW_DIRTY_MARGIN - 1e-6
                            and r.height() >= old.height() + 2 * _PREVIEW_DIRTY_MARGIN - 1e-6
                            for r in rects),
                        "脏区没有外扩，压在边界上的描边擦不掉")

        tw, th = self.doc.tile_w, self.doc.tile_h
        # 新位置 (12,12) center → 原点 (11,11)，全在地图外 → 应有红色边框
        new_img = self._render_rect(
            (11 * tw, 11 * th, 2 * tw, 2 * th), 2 * tw, 2 * th)
        self.assertTrue(self._red_in(new_img, 0, 0, 2 * tw, 2 * th),
                        "新位置越界格缺少红色边框")
        # 旧位置 (3,3) → 原点 (2,2)，全在地图内 → 不应有红框（无拖影残留）
        old_img = self._render_rect(
            (2 * tw, 2 * th, 2 * tw, 2 * th), 2 * tw, 2 * th)
        self.assertFalse(self._red_in(old_img, 0, 0, 2 * tw, 2 * th),
                         "旧位置残留了红框（拖影）")


class BrushWithoutTilesetTest(unittest.TestCase):
    """笔刷模式没用任何图集瓦片时，左键必须是彻底的空操作（不能当橡皮擦）。"""

    def setUp(self):
        self.w, self.layer, self.doc = _build_window_and_doc()
        self.layer.set(4, 4, 77)
        self.w.state.tool_id = "brush"
        self.w.state.brush_gid = 0
        self.w.state.stamp = None

    def test_press_without_brush_does_nothing(self):
        self.w._on_press(4, 4)
        self.w._on_move(5, 4)
        self.w._on_release()
        self.assertEqual(self.layer.get(4, 4), 77, "空笔刷把原有格子擦掉了")
        self.assertEqual(self.layer.get(5, 4), 0, "空笔刷还写了别的格")
        self.assertIsNone(self.w.state._stroke, "空笔刷不该开启笔划")
        self.assertEqual(self.w.state.undo_stack.count(), 0,
                         "空笔刷不该写入撤销栈")

    def test_all_zero_stamp_does_nothing(self):
        self.w.state.stamp = [[0, 0], [0, 0]]
        self.w._on_press(4, 4)
        self.w._on_release()
        self.assertEqual(self.layer.get(4, 4), 77, "全空图章也不该落笔擦除")

    def test_picking_tile_unlocks_brush(self):
        self.w._on_gid(5)
        self.w._on_press(4, 4)
        self.w._on_release()
        self.assertEqual(self.layer.get(4, 4), 5)

    def test_right_drag_stamp_unlocks_brush(self):
        self.w.view.regionCaptureRequested.emit(0, 0, 1, 1)
        self.assertIsNotNone(self.w.state.stamp)
        self.w._on_press(4, 4)
        self.w._on_release()
        self.assertNotEqual(self.layer.get(4, 4), 77, "抓到图章后应能盖章")

    def test_eraser_still_erases(self):
        self.w._set_tool("eraser")
        self.w._on_press(4, 4)
        self.w._on_release()
        self.assertEqual(self.layer.get(4, 4), 0,
                         "橡皮擦是独立工具，不该被空笔刷守卫拦住")


if __name__ == "__main__":
    unittest.main()
