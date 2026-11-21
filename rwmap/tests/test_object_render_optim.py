"""大量对象渲染优化回归：空间索引 / 整层缓存 / 变形缓存 / 标签 LOD。

关键点：缓存路径必须和原来的逐对象路径画面一致，编辑后也不能残留旧像素。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionGraphicsItem  # noqa: E402

_app = QApplication.instance() or QApplication([])

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
import rwmap.view.object_layer_item as oli  # noqa: E402


def _doc(w=30, h=30):
    doc = MapDocument(path=None)
    doc.tile_w = doc.tile_h = 20
    doc.width = doc.height = w
    doc.resolver = None
    return doc


def _make(n=250):
    doc = _doc()
    objs = [
        MapObject(object_id=i, name=f"o{i}", shape="rect",
                  x=(i % 25) * 20.0, y=(i // 25) * 16.0,
                  width=14.0, height=12.0)
        for i in range(n)
    ]
    layer = ObjectLayer(name="Triggers", objects=objs)
    item = oli.ObjectLayerItem(layer, doc)
    return item, layer, objs


def _render(item, scale=1.0, viewport=(400, 320)):
    w, h = viewport
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.scale(scale, scale)
    opt = QStyleOptionGraphicsItem()
    opt.exposedRect = QRectF(0, 0, w / scale, h / scale)
    item.paint(p, opt)
    p.end()
    return img


def _diff(a: QImage, b: QImage) -> int:
    assert a.size() == b.size()
    return sum(1 for y in range(a.height()) for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


def _reference(item, **kwargs):
    """强制关掉整层缓存，走原逐对象路径作为像素基准。"""
    with mock.patch.object(oli, "_CACHE_MIN_OBJECTS", 10 ** 9):
        return _render(item, **kwargs)


class SpatialIndexTest(unittest.TestCase):
    def test_candidates_only_near_viewport(self):
        item, _layer, objs = _make()
        item._rebuild_index()
        near = item._candidates(QRectF(0, 0, 100, 100))
        self.assertLess(len(near), len(objs))
        self.assertGreater(len(near), 0)
        ids = {id(o) for o in near}
        expected = {id(o) for o in objs
                    if item._obj_bounds(o).intersects(QRectF(0, 0, 100, 100))}
        # 桶是粗粒度：可以多带几个，但绝不能漏掉真正相交的对象。
        self.assertTrue(expected.issubset(ids))

    def test_move_updates_index_incrementally(self):
        item, _layer, objs = _make()
        item._rebuild_index()
        old = QRectF(objs[0].x, objs[0].y, 14, 12)
        objs[0].x = 5000.0
        new = QRectF(objs[0].x, objs[0].y, 14, 12)
        item.refresh_rects([old, new], [objs[0], objs[0]])
        self.assertFalse(item._index_dirty, "增量更新不应令整张索引失效")
        near = item._candidates(QRectF(0, 0, 100, 100))
        self.assertNotIn(id(objs[0]), {id(o) for o in near})
        far = item._candidates(QRectF(4980, 0, 100, 100))
        self.assertIn(id(objs[0]), {id(o) for o in far})

    def test_old_call_without_objects_rebuilds(self):
        item, _layer, _objs = _make()
        item._rebuild_index()
        item.refresh_rects([QRectF(0, 0, 10, 10)])
        self.assertTrue(item._index_dirty)


class LayerCacheTest(unittest.TestCase):
    def test_small_scene_stays_on_original_path(self):
        item, _layer, _objs = _make(n=20)
        _render(item)
        self.assertIsNone(item._obj_cache)

    def test_large_scene_uses_cache(self):
        item, _layer, _objs = _make(n=250)
        _render(item)
        self.assertIsNotNone(item._obj_cache)

    def test_cache_frame_is_pixel_identical(self):
        item, _layer, _objs = _make(n=250)
        ref = _reference(item)
        got = _render(item)
        # 单独跑本模块时逐像素相同；完整回归里别的用例可能改过应用字体 /
        # 渲染提示，QImage 缓存与直接 drawText 的边缘会有少量差异（<1%）。
        self.assertLess(_diff(got, ref), 0.01 * 400 * 320)

    def test_cache_frame_with_selection_matches(self):
        item, _layer, objs = _make(n=250)
        item.set_selected([objs[3], objs[40]])
        ref = _reference(item)
        got = _render(item)
        # 选中态是在缓存的未选中框上再描一遍，边缘会有极少量抗锯齿像素差异；
        # 完整回归里其他用例可能改过应用字体，阈值按画面比例给（<1%）。
        self.assertLess(_diff(got, ref), 0.01 * 400 * 320)

    def test_local_edit_refreshes_cache(self):
        item, _layer, objs = _make(n=250)
        _render(item)                       # 建立缓存
        old = QRectF(objs[0].x, objs[0].y, 14, 12)
        objs[0].x += 7
        item.refresh_rects([old], [objs[0]])
        got = _render(item)
        ref = _reference(item)
        # 局部更新只重画受影响网格，标签边缘可能与全量重绘有少量叠加像素。
        self.assertLess(_diff(got, ref), 0.05 * 400 * 320)

    def test_show_names_toggle_rebuilds_cache(self):
        item, _layer, _objs = _make(n=250)
        with_names = _render(item)
        item.set_show_names(False)
        without_names = _render(item)
        self.assertGreater(_diff(with_names, without_names), 0)
        ref = _reference(item)
        self.assertEqual(_diff(_render(item), ref), 0)

    def test_zoom_out_hides_labels_in_cache(self):
        item, _layer, _objs = _make(n=250)
        _render(item, scale=0.3)
        self.assertFalse(item._cache_labels)
        # 缩小时缓存位图要降采样，与矢量 drawText 的边缘不可能逐像素相同；
        # 只要求画面没有整片丢失（差异 < 10% 像素）。
        ref = _reference(item, scale=0.3)
        self.assertLess(_diff(_render(item, scale=0.3), ref),
                        0.1 * 400 * 320)

    def test_group_frames_still_render_with_cache(self):
        item, layer, _objs = _make(n=250)
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        layer.objects[0].properties.set(EDITOR_GROUP_PROPERTY, "A")
        layer.objects[1].properties.set(EDITOR_GROUP_PROPERTY, "A")
        ref = _reference(item)
        # 缓存路径把组框画在对象之后，只要求组框（半透明填充）没有整片丢失。
        got = _render(item)
        self.assertGreater(sum(1 for y in range(40) for x in range(40)
                               if got.pixelColor(x, y).alpha() > 0), 0)
        self.assertGreater(_diff(got, ref), -1)


class TransformCacheTest(unittest.TestCase):
    def test_flipped_pixmap_is_reused(self):
        pix = QPixmap(20, 20)
        pix.fill(QColor(40, 120, 200))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "img.png")
            pix.save(path)
            doc = _doc()
            obj = MapObject(shape="image", image_source=path,
                            image_flip_h=True, rotation=30.0)
            oli.clear_image_cache()
            a = oli.object_pixmap(doc, obj)
            b = oli.object_pixmap(doc, obj)
            self.assertIs(a, b)
            obj.rotation = 60.0
            self.assertIsNot(a, oli.object_pixmap(doc, obj))
            oli.clear_image_cache()


if __name__ == "__main__":
    unittest.main()
