"""对象层异步分块渲染：纯绘制函数 / 快照 / 异步建块的回归。

阶段 1 验证：
- build_tile_image 纯函数与旧同步绘制路径逐像素一致；
- 位图"注入 QImage (drawImage)" 与 "回退 QPixmap (drawPixmap)" 两条路径
  逐像素一致——这是工作线程安全方案（主线程 toImage() 快照）正确性的前提。

阶段 2 验证（后续追加）：异步建块结果与同步块一致、失效代次丢弃旧结果、
QPixmap 完全隔离。
"""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QImage, QPainter, QPixmap, QFont, Qt,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

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


def _diff(a: QImage, b: QImage) -> int:
    if a.size() != b.size():
        return -1
    return sum(1 for y in range(a.height())
               for x in range(a.width())
               if a.pixel(x, y) != b.pixel(x, y))


def _paint_one(obj, doc, *, images=None, label_font=None, line_width=None) -> QImage:
    """用纯函数在透明画布上画单个对象（场景 0..120 区域）。"""
    img = QImage(160, 160, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    oli._paint_object_pure(p, obj, doc, images=images,
                           label_font=label_font, line_width=line_width,
                           show_label=True)
    p.end()
    return img


class PureDrawParityTest(unittest.TestCase):
    """纯函数 drawImage 注入路径与 drawPixmap 回退路径逐像素一致。"""

    def test_geometric_shapes_parity(self):
        """几何形状不依赖注入位图；两条路径本就同一代码，验证画得出内容。"""
        doc = _doc()
        shapes = [
            MapObject(object_id=1, name="r", shape="rect", x=10, y=10,
                      width=40, height=30),
            MapObject(object_id=2, name="e", shape="ellipse", x=60, y=10,
                      width=40, height=30),
            MapObject(object_id=3, name="p", shape="point", x=100, y=25),
            MapObject(object_id=4, name="poly", shape="polygon", x=10, y=60,
                      points=[(0, 0), (30, 0), (20, 25)]),
            MapObject(object_id=5, name="line", shape="polyline", x=60, y=60,
                      points=[(0, 0), (30, 20), (45, 0)]),
        ]
        for obj in shapes:
            with self.subTest(shape=obj.shape, oid=obj.object_id):
                img = _paint_one(obj, doc)
                self.assertGreater(img.size().width(), 0)
                # 画布上应有非透明像素（对象确实被画出来了）
                self.assertGreater(
                    sum(1 for y in range(img.height())
                        for x in range(img.width())
                        if (img.pixel(x, y) >> 24) & 255), 0)

    def test_image_injected_qimage_matches_pixmap_fallback(self):
        """核心：注入 QImage 与回退 QPixmap 逐像素一致（含翻转/旋转）。"""
        pix = QPixmap(40, 40)
        pix.fill(QColor(40, 120, 200, 255))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "img.png")
            pix.save(path)
            doc = _doc()
            for flip_h, flip_v, rot in (
                    (False, False, 0.0), (True, False, 0.0),
                    (False, True, 0.0), (True, True, 30.0)):
                with self.subTest(flip_h=flip_h, flip_v=flip_v, rot=rot):
                    obj = MapObject(object_id=10, name="img", shape="image",
                                    x=10, y=10, width=48, height=48,
                                    image_source=path,
                                    image_flip_h=flip_h, image_flip_v=flip_v,
                                    rotation=rot)
                    oli.clear_image_cache()
                    # 回退路径：object_pixmap → drawPixmap
                    fallback = _paint_one(obj, doc)
                    # 注入路径：主线程 toImage() 快照 → drawImage
                    src_pix = oli.object_pixmap(doc, obj)
                    qimg = src_pix.toImage()
                    injected = _paint_one(obj, doc, images={id(obj): qimg})
                    self.assertEqual(_diff(fallback, injected), 0,
                                     f"flip={flip_h}/{flip_v} rot={rot} 两条路径不一致")
            oli.clear_image_cache()

    def test_label_font_injection_matches_default(self):
        """注入的标签字体副本与默认模块字体应画出相同结果。"""
        doc = _doc()
        obj = MapObject(object_id=20, name="named", shape="rect",
                        x=10, y=40, width=40, height=30)
        ref = _paint_one(obj, doc)
        got = _paint_one(obj, doc, label_font=QFont(oli._LABEL_FONT))
        self.assertEqual(_diff(ref, got), 0)


class BuildTileImageTest(unittest.TestCase):
    """build_tile_image 纯函数用快照生成块。"""

    def test_builds_same_block_as_manual_loop(self):
        doc = _doc()
        objs = [
            MapObject(object_id=i, name=f"o{i}", shape="rect",
                      x=20 + (i % 5) * 30, y=20 + (i // 5) * 30,
                      width=20, height=20)
            for i in range(20)
        ]
        entries = [(o, oli.object_rect(o, 20, 20)) for o in objs]
        job = oli.TileBuildJob(key=(1.0, 0, 0), lod=1.0, bx=0, by=0,
                               doc=doc, entries=entries)
        got = oli.build_tile_image(job)

        # 手工用同一 pure 函数按相同变换画一遍，应逐像素一致
        side = 640
        ref = QImage(side, side, QImage.Format_ARGB32)
        ref.fill(Qt.transparent)
        p = QPainter(ref)
        p.scale(1.0, 1.0)
        p.translate(0, 0)
        for o in objs:
            oli._paint_object_pure(p, o, doc, is_selected=False,
                                   show_label=True, line_width=1)
        p.end()
        self.assertEqual(_diff(got, ref), 0)

    def test_lod_changes_block_resolution(self):
        doc = _doc()
        objs = [MapObject(object_id=1, shape="rect", x=20, y=20,
                          width=20, height=20)]
        entries = [(o, oli.object_rect(o, 20, 20)) for o in objs]
        sizes = []
        for lod in oli._TILE_LODS:
            job = oli.TileBuildJob(key=(lod, 0, 0), lod=lod, bx=0, by=0,
                                   doc=doc, entries=entries)
            sizes.append(oli.build_tile_image(job).width())
        # lod 越小像素边长越小，且符合 ceil(640*lod)
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 640)
        self.assertEqual(sizes[-1], int(640 * oli._TILE_LODS[-1]))


class SnapshotIsolationTest(unittest.TestCase):
    """阶段 1 的快照只取候选，不深拷贝（深拷贝在阶段 2）；这里验证候选正确。"""

    def test_snapshot_entries_cover_block_and_overflow(self):
        doc = _doc()
        objs = [
            MapObject(object_id=1, shape="rect", x=20, y=20, width=20, height=20),
            MapObject(object_id=2, shape="rect", x=100, y=100, width=20, height=20),
            MapObject(object_id=3, shape="rect", x=2000, y=2000, width=20, height=20),
        ]
        layer = ObjectLayer(name="T", objects=objs)
        item = oli.ObjectLayerItem(layer, doc)
        item._rebuild_index()
        entries, images = item._snapshot_tile_entries(0, 0, True)
        ids = {id(o) for o, _ in entries}
        self.assertIn(id(objs[0]), ids)
        self.assertIn(id(objs[1]), ids)
        self.assertNotIn(id(objs[2]), ids, "块外对象不应进入快照")
        self.assertEqual(images, {})


def _settle_all(item, visible, target_keys, timeout_ms=10000):
    """反复 paint + 泵事件，直到所有目标块就绪。

    异步建块每帧最多派发 _TILE_MAX_JOBS_PER_FRAME 块（渐进填充），因此单帧
    paint 不足以让全部块进入 cache；这里模拟连续多帧直到铺满。
    """
    import time
    from PySide6.QtGui import QTransform
    from PySide6.QtWidgets import QStyleOptionGraphicsItem
    need = set(target_keys)
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if need.issubset(set(item._tile_cache)) and not item._tile_inflight:
            _app.processEvents()
            return True
        # 再画一帧，派发下一批块
        img = QImage(320, 240, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        p.setTransform(QTransform().scale(item._scale, item._scale))
        p.translate(-visible.left(), -visible.top())
        opt = QStyleOptionGraphicsItem()
        opt.exposedRect = visible
        item.paint(p, opt)
        p.end()
        _app.processEvents()
        time.sleep(0.005)
    return need.issubset(set(item._tile_cache))


def _pump_until_idle(item, timeout_ms=8000):
    """泵事件循环，等所有在途异步块回来（含 queued signal 投递）。"""
    import time
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        _app.processEvents()
        if not item._tile_inflight:
            # 再泵一次，确保最后一个 tileReady 信号已处理完
            _app.processEvents()
            return True
        time.sleep(0.005)
    return False


def _visible_rect(scale=1.0, viewport=(400, 300), center=(0.0, 0.0)):
    w, h = viewport
    return QRectF(center[0] - w / (2 * scale), center[1] - h / (2 * scale),
                  w / scale, h / scale)


def _render_item(item, scale=1.0, viewport=(400, 300), center=(0.0, 0.0)):
    from PySide6.QtGui import QTransform
    from PySide6.QtWidgets import QStyleOptionGraphicsItem
    w, h = viewport
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


class AsyncTileCacheTest(unittest.TestCase):
    """阶段 2：异步建块结果正确、失效代次生效、位图完全隔离。"""

    def _item(self):
        doc = _doc()
        objs = [
            MapObject(object_id=i, name=f"o{i}", shape="rect",
                      x=(i * 137) % 600, y=(i * 89) % 500,
                      width=14, height=12)
            for i in range(260)
        ]
        return oli.ObjectLayerItem(ObjectLayer(name="T", objects=objs), doc), objs

    def test_async_result_matches_sync_pixelwise(self):
        """强制异步建块，待全部回来后与同步块逐像素一致。"""
        item, _objs = self._item()
        # 统一视口：viewport=(1280,960), center=(640,480) → visible=(0,0,1280,960)
        sync_view = QRectF(0, 0, 1280, 960)
        # 同步生成基准（同一 item 强制走同步）；先重建索引，与 paint 内部行为一致
        item._scale = 1.0
        item._rebuild_index()
        item._async_tiles_override = False
        for bx, by in item._tile_key_range(sync_view, 1.0):
            item._tile_image(bx, by, 1.0, True)
        sync_blocks = dict(item._tile_cache)
        self.assertGreater(len(sync_blocks), 0)

        # 清空后走异步重建（相同视口）
        item._clear_tile_cache()
        item._tile_dirty_all = True
        item._async_tiles_override = True
        _render_item(item, scale=1.0, viewport=(1280, 960), center=(640, 480))
        self.assertTrue(_settle_all(item, sync_view, sync_blocks.keys()),
                        "块未在超时内全部铺满")
        self.assertEqual(set(item._tile_cache), set(sync_blocks))
        for key in sync_blocks:
            self.assertEqual(_diff(item._tile_cache[key], sync_blocks[key]), 0,
                             f"块 {key} 异步与同步不一致")

    def test_stale_epoch_result_discarded(self):
        """任务在途时对象移动触发失效，旧代次结果不得落地。"""
        item, objs = self._item()
        item._scale = 1.0
        item._async_tiles_override = True
        _render_item(item, scale=1.0)
        self.assertGreater(len(item._tile_inflight), 0, "应有在途任务")
        old_epoch = item._tile_epoch
        stale_key = next(iter(item._tile_inflight))

        # 整体失效（增删/翻转路径）：必定 bump epoch 并清空在途
        item.refresh_all()
        self.assertGreater(item._tile_epoch, old_epoch)

        # 手动构造一个"旧代次结果"尝试落地，应被丢弃
        stale_img = QImage(10, 10, QImage.Format_ARGB32)
        stale_img.fill(QColor(255, 0, 0))
        item._apply_tile_result(oli.TileBuildResult(
            stale_key, stale_img, old_epoch, True))
        self.assertNotIn(stale_key, item._tile_cache)

        _pump_until_idle(item)

    def test_refresh_rects_bumps_epoch_for_inflight(self):
        """局部移动对象也要让覆盖块的在途任务失效。"""
        item, objs = self._item()
        item._scale = 1.0
        item._async_tiles_override = True
        _render_item(item, scale=1.0, viewport=(1280, 960), center=(640, 480))
        self.assertGreater(len(item._tile_inflight), 0)
        old_epoch = item._tile_epoch

        # 取一个在途块，构造覆盖它的对象矩形去调 refresh_rects
        a_key = next(iter(item._tile_inflight))
        _lod, bx, by = a_key
        r = QRectF(bx * oli._TILE_CACHE + 10, by * oli._TILE_CACHE + 10, 20, 20)
        obj = objs[0]
        item.refresh_rects([r], [obj])
        self.assertGreater(item._tile_epoch, old_epoch,
                           "覆盖范围命中在途块时应 bump epoch")
        _pump_until_idle(item)

    def test_no_qpixmap_usage_in_worker_build(self):
        """工作线程建块路径不得触碰 QPixmap。

        做法是让 QPixmap 的构造在工作线程直接抛错：快照（toImage）在 GUI 线程
        完成，worker 只碰 QImage；若 worker 路径误用 QPixmap，任务会失败、结果
        永远回不来，_pump_until_idle 超时即暴露问题。
        """
        import threading
        from unittest import mock

        real_init = QPixmap.__init__

        def guarded_init(self, *a, **kw):
            if threading.current_thread() is not threading.main_thread():
                raise AssertionError("QPixmap 在工作线程被构造！")
            return real_init(self, *a, **kw)

        item, _objs = self._item()
        item._scale = 1.0
        item._async_tiles_override = True
        with mock.patch.object(QPixmap, "__init__", guarded_init):
            _render_item(item, scale=1.0)
            done = _pump_until_idle(item)
        self.assertTrue(done, "worker 未完成——可能触碰了被禁用的 QPixmap")
        self.assertGreater(len(item._tile_cache), 0)

    def test_snapshot_deep_copies_objects(self):
        """异步快照必须深拷贝：工作线程拿到的不是原对象，且改快照不影响原对象。"""
        doc = _doc()
        objs = [MapObject(object_id=5, name="o5", shape="rect",
                          x=100, y=100, width=20, height=20)]
        item = oli.ObjectLayerItem(ObjectLayer(name="T", objects=objs), doc)
        item._scale = 1.0
        item._rebuild_index()
        entries, images = item._snapshot_tile_entries(0, 0, True, for_async=True)
        self.assertGreater(len(entries), 0)
        originals = {o.object_id: o for o in objs}
        for snap, _b in entries:
            orig = originals[snap.object_id]
            self.assertIsNot(snap, orig, "快照对象不应是原对象引用")
            self.assertIsNot(snap.properties, orig.properties)
            self.assertIsNot(snap.points, orig.points)
        # 改快照的 x 不应影响原对象
        snap0 = entries[0][0]
        before = originals[snap0.object_id].x
        snap0.x += 123
        self.assertEqual(originals[snap0.object_id].x, before)

    def test_image_object_async_matches_sync(self):
        """image 对象（含翻转/旋转）的异步块必须与同步块一致。"""
        pix = QPixmap(40, 40)
        pix.fill(QColor(40, 120, 200, 255))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "img.png")
            pix.save(path)
            doc = _doc()
            objs = [
                MapObject(object_id=1, name="img", shape="image",
                          x=300, y=300, width=48, height=48,
                          image_source=path, image_flip_h=True, rotation=30.0)
                for _ in range(1)
            ]
            from unittest import mock
            item = oli.ObjectLayerItem(ObjectLayer(name="T", objects=objs), doc)
            item._scale = 1.0
            item._rebuild_index()
            view = QRectF(0, 0, 1280, 960)
            # 仅 1 个对象不足 _CACHE_MIN_OBJECTS，强制启用分块路径来测 image 位图
            with mock.patch.object(oli.ObjectLayerItem, "_use_tile_cache",
                                   return_value=True):
                item._async_tiles_override = False
                for bx, by in item._tile_key_range(view, 1.0):
                    item._tile_image(bx, by, 1.0, True)
                sync = dict(item._tile_cache)

                item._clear_tile_cache()
                item._tile_dirty_all = True
                item._async_tiles_override = True
                _render_item(item, scale=1.0, viewport=(1280, 960), center=(640, 480))
                self.assertTrue(_settle_all(item, view, sync.keys()))
                self.assertEqual(set(item._tile_cache), set(sync))
                for key, ref in sync.items():
                    self.assertEqual(_diff(item._tile_cache[key], ref), 0,
                                     f"image 块 {key} 异步与同步不一致")
            oli.clear_image_cache()

    def test_failed_build_clears_inflight_for_retry(self):
        """建块失败（image=None）也要清理 inflight，否则该块永久空白。"""
        item, _objs = self._item()
        item._scale = 1.0
        item._async_tiles_override = True
        # 手动登记一个在途块
        key = (1.0, 0, 0)
        item._tile_inflight[key] = item._tile_epoch
        # 模拟 worker 失败结果
        item._apply_tile_result(oli.TileBuildResult(
            key, None, item._tile_epoch, True))
        self.assertNotIn(key, item._tile_inflight, "失败后应清理在途登记")
        self.assertNotIn(key, item._tile_cache, "失败结果不应写入 cache")
        # 下一帧可以重新派发
        _render_item(item, scale=1.0, viewport=(1280, 960), center=(640, 480))
        self.assertIn(key, item._tile_inflight, "失败后下一帧应能重试")
        _settle_all(item, QRectF(0, 0, 1280, 960), [key])


class LowZoomSmoothFillTest(unittest.TestCase):
    """低倍率缩小时的「从上往下逐块加载」修复回归。"""

    def _item(self):
        doc = _doc()
        objs = [
            MapObject(object_id=i, name=f"o{i}", shape="rect",
                      x=(i * 137) % 600, y=(i * 89) % 500,
                      width=14, height=12)
            for i in range(260)
        ]
        return oli.ObjectLayerItem(ObjectLayer(name="T", objects=objs), doc)

    def test_missing_block_draws_low_detail_overview_placeholder(self):
        """未就绪块先贴整图占位，第一帧就有对象框，而不是空白逐块出现。"""
        from unittest import mock

        item = self._item()
        item._scale = 0.1
        item._async_tiles_override = True
        idle_pool = mock.Mock()
        with mock.patch.object(oli, "_tile_pool", return_value=idle_pool), \
                mock.patch.object(oli.ObjectLayerItem, "_use_tile_cache",
                                  return_value=True):
            img = _render_item(item, scale=0.1, viewport=(640, 480),
                               center=(300, 250))
            self.assertIsNotNone(item._tile_overview,
                                 "低倍率应生成整图占位图")
            with_overview = sum(
                1 for y in range(img.height()) for x in range(img.width())
                if (img.pixel(x, y) >> 24) & 255)

            item._tile_overview = None
            with mock.patch.object(item, "_build_tile_overview", lambda: None):
                img2 = _render_item(item, scale=0.1, viewport=(640, 480),
                                    center=(300, 250))
            without = sum(
                1 for y in range(img2.height()) for x in range(img2.width())
                if (img2.pixel(x, y) >> 24) & 255)
            self.assertGreater(with_overview, without,
                               "占位图应让缺块区域立刻出现对象框")

    def test_low_lod_cache_not_limited_by_old_256_blocks(self):
        """低 LOD 块 25KB，视口要近千块；按字节预算不能卡在 256 块。"""
        item = self._item()
        img = QImage(80, 80, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        for i in range(300):
            item._put_tile((0.125, i, 0), img)
        self.assertEqual(len(item._tile_cache), 300)
        self.assertEqual(item._tile_bytes, 300 * 80 * 80 * 4)

    def test_low_zoom_dispatch_is_center_out(self):
        """派发顺序应从视口中心向外，避免视觉上从上往下刷。"""
        item = self._item()
        item._scale = 0.1
        visible = QRectF(0, 0, 640 * 6, 640 * 6)
        keys = item._ordered_tile_keys(visible, 0.125)
        cx = visible.center().x() / oli._TILE_CACHE
        cy = visible.center().y() / oli._TILE_CACHE
        dist = [(bx + 0.5 - cx) ** 2 + (by + 0.5 - cy) ** 2
                for bx, by in keys]
        self.assertEqual(dist, sorted(dist))

    def test_invalidation_discards_overview_placeholder(self):
        item = self._item()
        item._scale = 0.1
        item._build_tile_overview()
        self.assertIsNotNone(item._tile_overview)
        item.refresh_all()
        self.assertIsNone(item._tile_overview)


if __name__ == "__main__":
    unittest.main()
