"""极低缩放下对象框四条边必须完整可见（哪怕屏幕只有 1px 线宽）。

背景：5% 缩放下 20×20 场景单位的矩形对象只有 1×1 像素，四条边挤成点；
分块缓存又按 1/lod 补偿线宽（view_scale < lod 时屏幕线宽不足 1px），
抗锯齿把边稀释成 L 形 / 断点。修复后用 min_visible_rect 撑到 4×4 屏幕
像素 + 按 view_scale 补偿线宽。本测试锁住两条绘制路径。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem  # noqa: E402

_app = QApplication.instance() or QApplication([])

import rwmap.view.object_layer_item as oli  # noqa: E402
from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402


def _doc():
    d = MapDocument(path=None)
    d.tile_w = d.tile_h = 20
    d.width = d.height = 1000
    d.resolver = None
    return d


def _make(n, span):
    doc = _doc()
    objs = [
        MapObject(object_id=i, shape="rect",
                  x=60 + (i % 20) * (span / 20), y=60 + (i // 20) * (span / 20),
                  width=20.0, height=20.0)
        for i in range(n)
    ]
    item = oli.ObjectLayerItem(ObjectLayer(name="O", objects=objs), doc)
    item._async_tiles_override = False
    return item, objs


def _render(item, scale, viewport=(1000, 800)):
    w, h = viewport
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 255))
    p = QPainter(img)
    p.scale(scale, scale)
    opt = QStyleOptionGraphicsItem()
    opt.exposedRect = QRectF(0, 0, w / scale, h / scale)
    item.paint(p, opt)
    p.end()
    return img


def _yellow_near(img, obj, scale):
    """对象框中心附近的黄色像素数；四条边闭合时至少 12+。"""
    cx = int((obj.x + obj.width / 2) * scale)
    cy = int((obj.y + obj.height / 2) * scale)
    cnt = 0
    for yy in range(max(0, cy - 3), min(img.height(), cy + 4)):
        for xx in range(max(0, cx - 3), min(img.width(), cx + 4)):
            c = img.pixelColor(xx, yy)
            if c.red() > 80 and c.red() > c.blue() + 30:
                cnt += 1
    return cnt


class MinVisibleRectTest(unittest.TestCase):
    def test_small_object_expanded_to_screen_min(self):
        rect = QRectF(100, 100, 20, 20)
        got = oli.min_visible_rect(rect, 0.05)
        # 4px / 0.05 = 80 场景单位，中心不变
        self.assertAlmostEqual(got.width(), 80.0, places=3)
        self.assertAlmostEqual(got.height(), 80.0, places=3)
        self.assertAlmostEqual(got.center().x(), 110.0, places=3)
        self.assertAlmostEqual(got.center().y(), 110.0, places=3)

    def test_large_object_untouched(self):
        rect = QRectF(0, 0, 500, 300)
        self.assertEqual(oli.min_visible_rect(rect, 0.05), rect)

    def test_direct_path_four_edges_at_5pct(self):
        item, objs = _make(20, span=1000)
        img = _render(item, 0.05)
        for o in objs[:20]:
            self.assertGreaterEqual(
                _yellow_near(img, o, 0.05), 12,
                "直接绘制：极小对象的四条边必须完整可见")

    def test_tile_cache_path_four_edges_at_5pct(self):
        item, objs = _make(2200, span=14000)
        img = _render(item, 0.05)
        self.assertTrue(item._tile_cache, "大场景应走分块缓存")
        for o in objs[:20]:
            self.assertGreaterEqual(
                _yellow_near(img, o, 0.05), 12,
                "分块缓存：极小对象的四条边必须完整可见")

    def test_tile_line_width_compensates_by_view_scale(self):
        """缺边根因回归：缓存线宽必须按 view_scale 而非 lod 补偿。"""
        # 5% 缩放 → 1/0.05 = 20 场景单位线宽，屏幕才 ≥1px
        self.assertEqual(oli.compensated_line_width(0.05), 20)
        # 旧逻辑按 lod=0.125 补偿只有 8，屏幕 0.4px 会缺边
        self.assertGreater(oli.compensated_line_width(0.05),
                           int(1 / 0.125))

    def test_at_1x_no_expansion(self):
        item, _objs = _make(20, span=1000)
        img = _render(item, 1.0)
        # 1x 下 20px 对象正常绘制为 20×20 空心框；中心 ±3 采样落在框内空区，
        # 所以这里用整幅图的黄色像素数断言，确认没有被异常撑大/消失。
        yellow = sum(1 for y in range(img.height()) for x in range(img.width())
                     if (lambda c: c.red() > 80 and c.red() > c.blue() + 30)(
                         img.pixelColor(x, y)))
        self.assertGreater(yellow, 0)


def _border_screen_width(img, x_scene, y_scene, scale):
    """在对象左边框中点横向扫描，返回连续黄色像素数 = 边框屏幕线宽。

    用一个「大对象」(200×200 场景单位) 避免 min_visible_rect 的 4px 撑大
    干扰线宽测量：大对象在任何缩放下都不需要撑大，测到的就是纯线宽。
    """
    cx, cy = int(x_scene * scale), int(y_scene * scale)
    run = best = 0
    for x in range(max(0, cx - 6), min(img.width(), cx + 7)):
        c = img.pixelColor(x, cy)
        if c.red() > 80 and c.red() > c.blue() + 30:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _big_object_item(n=20, span=1000, obj_size=200):
    """构造含 n 个 200×200 大对象的 item，x/y 起点 60。"""
    from rwmap.document.layer import ObjectLayer
    from rwmap.document.map_object import MapObject
    objs = [
        MapObject(object_id=i, shape="rect",
                  x=60 + (i % 20) * (max(span, obj_size + 40) / 20),
                  y=60 + (i // 20) * (max(span, obj_size + 40) / 20),
                  width=obj_size, height=obj_size)
        for i in range(n)
    ]
    item = oli.ObjectLayerItem(ObjectLayer(name="O", objects=objs), _doc())
    item._async_tiles_override = False
    return item


class ConstantOnePixelBorderTest(unittest.TestCase):
    """对象框边框恒 1 屏幕像素（与节点图 cosmetic 连线同机制）。

    缓存位图只烘焙内容，边框由 paint() 每帧以 _PEN_NORMAL(cosmetic) 描，
    因此线宽不随视图缩放变化。此前缓存路径用整数场景线宽补偿，
    屏幕宽度在 1~2px 间跳变。本测试锁住 0.05x~4x 全程 1px。
    """

    SCALES = (0.05, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0)

    def test_pen_is_cosmetic(self):
        self.assertTrue(oli._PEN_NORMAL.isCosmetic())

    def test_direct_path_one_pixel_all_scales(self):
        # 大对象：任何缩放都不触发 min_visible_rect 撑大，测纯线宽。
        # 左边框 x=60，取 y=150（在 60..260 之间），横扫应为 1px。
        for scale in self.SCALES:
            with self.subTest(scale=scale):
                item = _big_object_item()
                img = _render(item, scale, viewport=(1600, 1600))
                self.assertEqual(_border_screen_width(img, 60, 150, scale), 1,
                                 "直接绘制 %sx 边框应为 1 屏幕像素" % scale)

    def test_cache_path_one_pixel_all_scales(self):
        # 300 个大对象跨度 > 单图上限于低缩放走分块缓存；
        # 无论哪条缓存路径，边框都在 paint() 统一 cosmetic 描，恒 1px。
        for scale in (0.05, 0.125, 0.25, 0.5):
            with self.subTest(scale=scale):
                item = _big_object_item(n=300, span=14000)
                img = _render(item, scale, viewport=(1600, 1600))
                self.assertEqual(_border_screen_width(img, 60, 150, scale), 1,
                                 "缓存路径 %sx 边框应为 1 屏幕像素" % scale)

    def test_min_visible_rect_keeps_four_edges_at_extreme_zoom(self):
        # 极小对象仍撑到 4×4 屏幕像素，四边闭合（上一轮行为的回归）。
        item, objs = _make(20, span=1000)
        img = _render(item, 0.05)
        for o in objs[:20]:
            self.assertGreaterEqual(_yellow_near(img, o, 0.05), 12)


if __name__ == "__main__":
    unittest.main()
