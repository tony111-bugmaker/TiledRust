"""真实大图对象渲染优化回归：cosmetic 线宽 + 超大场景分块 LOD 缓存。

真实样本「生存变态加强技能版尸如潮水1v3031p.tmx」有 5516 个对象，
坐标跨度达 1.4 万像素：单张整层缓存超像素上限建不起来，退化为每帧逐对象
绘制（缩放 0.25 时实测 208ms/帧）。分块 LOD 缓存把命中帧降到 ~5ms。

同时验证用户反馈的「缩小后线变透明」：QPen 场景坐标宽度在缩小时被抗锯齿
稀释（alpha 190→47），cosmetic pen 让各倍率 alpha 恒定。
"""
from __future__ import annotations

import math
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QImage, QPainter, QTransform, Qt,
)
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem  # noqa: E402

_app = QApplication.instance() or QApplication([])

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
import rwmap.view.object_layer_item as oli  # noqa: E402


def _doc(w=330, h=265):
    doc = MapDocument(path=None)
    doc.tile_w = doc.tile_h = 20
    doc.width, doc.height = w, h
    doc.resolver = None
    return doc


def _scattered(n=260, span=14000):
    """跨越大范围的对象，模拟真实图的空间跨度。"""
    objs = [
        MapObject(object_id=i, name=f"o{i}", shape="rect",
                  x=(i * 137) % span - span / 2,
                  y=(i * 89) % span - span / 2,
                  width=20.0, height=20.0)
        for i in range(n)
    ]
    return objs


def _render(item, scale=1.0, viewport=(400, 300), center=(0.0, 0.0),
            show_names=None):
    w, h = viewport
    if show_names is not None:
        item.show_names = show_names
    vx = center[0] - w / (2 * scale)
    vy = center[1] - h / (2 * scale)
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setTransform(QTransform().scale(scale, scale))
    p.translate(-vx, -vy)
    opt = QStyleOptionGraphicsItem()
    opt.exposedRect = QRectF(vx, vy, w / scale, h / scale)
    item.paint(p, opt)
    p.end()
    return img


class CosmeticPenTest(unittest.TestCase):
    """对象框线在各缩放倍率下 alpha 恒定，不随缩放稀释。"""

    def test_pens_are_cosmetic(self):
        self.assertTrue(oli._PEN_NORMAL.isCosmetic())
        self.assertTrue(oli._PEN_SELECTED.isCosmetic())
        self.assertTrue(oli._PEN_PREVIEW.isCosmetic())

    def test_direct_draw_alpha_constant_across_scales(self):
        for scale in (1.0, 0.5, 0.25):
            with self.subTest(scale=scale):
                img = QImage(300, 300, QImage.Format_ARGB32)
                img.fill(QColor(0, 0, 0, 0))
                p = QPainter(img)
                p.setTransform(QTransform().scale(scale, scale))
                p.setPen(oli._PEN_NORMAL)
                p.setBrush(Qt.NoBrush)
                p.drawRect(QRectF(100, 100, 60, 60))
                p.end()
                maxa = 0
                for yy in range(300):
                    for xx in range(300):
                        c = img.pixel(xx, yy)
                        if (c >> 16 & 255, c >> 8 & 255, c & 255) == (255, 200, 60):
                            maxa = max(maxa, c >> 24 & 255)
                self.assertEqual(maxa, 190,
                                 f"scale={scale} 框线 alpha 应恒为 190，实际 {maxa}")


class LODChoiceTest(unittest.TestCase):
    def test_levels_monotonic(self):
        # scale 递增 → lod 档位递增（升序）
        levels = [oli._choose_tile_lod(s / 100.0)
                  for s in range(5, 205, 5)]
        self.assertEqual(levels, sorted(levels))

    def test_bounds(self):
        self.assertEqual(oli._choose_tile_lod(1.0), 1.0)
        self.assertEqual(oli._choose_tile_lod(0.5), 0.5)
        self.assertEqual(oli._choose_tile_lod(0.25), 0.25)
        self.assertEqual(oli._choose_tile_lod(0.01), oli._TILE_LODS[-1])

    def test_width_compensation_formula(self):
        for lod in oli._TILE_LODS:
            w = max(1, int(math.ceil(1.0 / lod)))
            self.assertGreaterEqual(w * lod, 1.0)


class TileCacheTest(unittest.TestCase):
    def _item(self, n=260, span=14000):
        doc = _doc()
        objs = _scattered(n, span)
        layer = ObjectLayer(name="Triggers", objects=objs)
        item = oli.ObjectLayerItem(layer, doc)
        return item, layer, objs

    def test_large_span_uses_tile_cache(self):
        item, _layer, _objs = self._item()
        item._scale = 1.0
        self.assertTrue(item._use_tile_cache())

    def test_compact_scene_uses_single_image_cache(self):
        # 小地图 + 集中对象 → 跨度小于阈值 → 不走分块，保持原单图缓存路径。
        doc = _doc(w=30, h=30)
        objs = [MapObject(object_id=i, shape="rect", x=(i % 20) * 20.0,
                          y=(i // 20) * 20.0, width=14.0, height=12.0)
                for i in range(260)]
        item = oli.ObjectLayerItem(ObjectLayer(name="T", objects=objs), doc)
        item._scale = 1.0
        self.assertFalse(item._use_tile_cache())

    def test_tile_cache_generated_and_reused(self):
        item, _layer, _objs = self._item()
        item.show_names = False
        center = (_objs[0].x, _objs[0].y)
        _render(item, scale=0.5, center=center)
        self.assertGreater(len(item._tile_cache), 0)
        first = dict(item._tile_cache)
        cache_keys = set(first)
        _render(item, scale=0.5, center=center)
        self.assertEqual(set(item._tile_cache), cache_keys)
        for k in cache_keys:
            self.assertIs(item._tile_cache[k], first[k])

    def test_zoom_out_uses_lower_lod(self):
        item, _layer, _objs = self._item()
        item.show_names = False
        center = (_objs[0].x, _objs[0].y)
        _render(item, scale=1.0, center=center)
        lod1 = next(iter(item._tile_cache))[0]
        item._tile_dirty_all = True
        _render(item, scale=0.25, center=center)
        lod025 = next(iter(item._tile_cache))[0]
        self.assertLess(lod025, lod1)

    def test_partial_edit_invalidates_only_covered_tiles(self):
        item, _layer, objs = self._item()
        item.show_names = False
        center = (objs[0].x, objs[0].y)
        # 大视口覆盖多个块，确保移走一个对象后还有无关块保留。
        _render(item, scale=1.0, center=center, viewport=(2000, 1600))
        before = dict(item._tile_cache)
        self.assertGreater(len(before), 1, "测试需要至少两个块")
        old = QRectF(objs[0].x, objs[0].y, 20, 20)
        objs[0].x += 5
        item.refresh_rects([old], [objs[0]])
        after_keys = set(item._tile_cache)
        removed = set(before) - after_keys
        self.assertGreater(len(removed), 0, "受影响块应失效")
        self.assertGreater(len(after_keys), 0, "无关块应保留")

    def test_show_names_toggle_invalidates_all_tiles(self):
        item, _layer, _objs = self._item()
        center = (_objs[0].x, _objs[0].y)
        _render(item, scale=1.0, center=center, show_names=True)
        self.assertGreater(len(item._tile_cache), 0)
        item.set_show_names(False)
        self.assertTrue(item._tile_dirty_all)
        _render(item, scale=1.0, center=center)
        self.assertFalse(item._tile_dirty_all)

    def test_tile_line_alpha_constant_when_zoomed(self):
        """分块缓存路径下，框线 alpha 不随缩放稀释（用户核心诉求）。"""
        doc = _doc()
        objs = [MapObject(object_id=0, shape="rect", x=10.0, y=10.0,
                          width=40.0, height=40.0)]
        objs += [MapObject(object_id=i, shape="rect",
                           x=(i * 500) % 14000 - 7000,
                           y=(i * 311) % 14000 - 7000,
                           width=20.0, height=20.0)
                 for i in range(1, 260)]
        item = oli.ObjectLayerItem(ObjectLayer(name="T", objects=objs), doc)
        item.show_names = False
        for scale in (1.0, 0.5, 0.25):
            with self.subTest(scale=scale):
                item._tile_dirty_all = True
                item._tile_cache.clear()
                item._tile_lru.clear()
                img = _render(item, scale=scale, center=(30.0, 30.0))
                ox = int(img.width() / 2 + (10 - 30) * scale)
                oy = int(img.height() / 2 + (10 - 30) * scale)
                maxa = 0
                for yy in range(max(0, oy - 3), min(img.height(), oy + int(40 * scale) + 4)):
                    for xx in range(max(0, ox - 3), min(img.width(), ox + int(40 * scale) + 4)):
                        c = img.pixel(xx, yy)
                        if (c >> 16 & 255, c >> 8 & 255, c & 255) == (255, 200, 60):
                            maxa = max(maxa, c >> 24 & 255)
                self.assertGreaterEqual(
                    maxa, 180,
                    f"scale={scale} 分块缓存框线 alpha 应 >=180，实际 {maxa}")


if __name__ == "__main__":
    unittest.main()
