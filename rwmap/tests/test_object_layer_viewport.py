"""对象层渲染：地图外对象不消失 + 只渲染当前视口内（exposedRect 裁剪）。

对应修复：
1. boundingRect 覆盖所有对象实际范围（含地图外），否则视图缩放后落在地图矩形
   之外、但那里有对象时，QGraphicsView 会整层剔除导致对象消失。
2. paint() 按 option.exposedRect 逐对象裁剪，只画与当前可见区域相交的对象。
"""
import unittest

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

from rwmap.document.layer import ObjectLayer
from rwmap.document.map_document import MapDocument
from rwmap.document.map_object import MapObject
from rwmap.view.object_layer_item import ObjectLayerItem


def _make_doc(tw=20, th=20, w=20, h=20):
    doc = MapDocument(path=None)
    doc.tile_w = tw
    doc.tile_h = th
    doc.width = w
    doc.height = h
    doc.resolver = None
    return doc


def _make_item(doc, objs):
    layer = ObjectLayer(name="Triggers")
    layer.width = doc.width
    layer.height = doc.height
    layer.objects = list(objs)
    item = ObjectLayerItem(layer, doc, z_value=10)
    return item, layer


def _yellow_count(img: QImage, x0, y0, x1, y1) -> int:
    """统计区域内接近对象黄 (255,200,60) 的像素数。"""
    n = 0
    for y in range(max(0, int(y0)), min(img.height(), int(y1))):
        for x in range(max(0, int(x0)), min(img.width(), int(x1))):
            c = img.pixelColor(x, y)
            if c.red() > 200 and 150 < c.green() < 230 and c.blue() < 120:
                n += 1
    return n


def _render(item: ObjectLayerItem, exposed: QRectF, w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    option = QStyleOptionGraphicsItem()
    option.exposedRect = exposed
    item.paint(painter, option)
    painter.end()
    return img


class ObjectLayerViewportTest(unittest.TestCase):
    def test_offmap_object_in_bounding_rect(self):
        doc = _make_doc()
        # 地图矩形 = [0,0, 400,400]；把一个对象放到地图外 (1000,1000)。
        a = MapObject(object_id=1, shape="rect", x=50, y=50, width=40, height=40)
        b = MapObject(object_id=2, shape="rect", x=1000, y=1000, width=40, height=40)
        item, _ = _make_item(doc, [a, b])
        br = item.boundingRect()
        # 包围盒应覆盖地图外对象 b（即扩张到 > 400）。
        self.assertGreater(br.right(), 400)
        self.assertGreater(br.bottom(), 400)
        self.assertTrue(br.intersects(QRectF(1000, 1000, 40, 40)))
        # 且包含地图内的 a。
        self.assertTrue(br.intersects(QRectF(50, 50, 40, 40)))

    def test_paint_culls_outside_exposed_rect(self):
        doc = _make_doc()
        a = MapObject(object_id=1, shape="rect", x=50, y=50, width=40, height=40)
        b = MapObject(object_id=2, shape="rect", x=1000, y=1000, width=40, height=40)
        item, _ = _make_item(doc, [a, b])
        # 只暴露地图内区域 [0,0,200,200]，b 远在视口外。
        img = _render(item, QRectF(0, 0, 200, 200), 1100, 1100)
        # a 在可见区域：应画出黄色。
        self.assertGreater(_yellow_count(img, 50, 50, 90, 90), 0)
        # b 在可见区域外：裁剪后不应出现黄色。
        self.assertEqual(_yellow_count(img, 1000, 1000, 1040, 1040), 0)

    def test_paint_draws_when_exposed_covers_offmap(self):
        doc = _make_doc()
        a = MapObject(object_id=1, shape="rect", x=50, y=50, width=40, height=40)
        b = MapObject(object_id=2, shape="rect", x=1000, y=1000, width=40, height=40)
        item, _ = _make_item(doc, [a, b])
        # 暴露区域覆盖地图外的 b：此时 b 必须被画出来（修复「地图外消失」）。
        img = _render(item, QRectF(900, 900, 200, 200), 1100, 1100)
        self.assertGreater(_yellow_count(img, 1000, 1000, 1040, 1040), 0)
        # a 在可见区域外，裁剪后不画。
        self.assertEqual(_yellow_count(img, 50, 50, 90, 90), 0)

    def test_exposed_empty_draws_all(self):
        doc = _make_doc()
        a = MapObject(object_id=1, shape="rect", x=50, y=50, width=40, height=40)
        b = MapObject(object_id=2, shape="rect", x=1000, y=1000, width=40, height=40)
        item, _ = _make_item(doc, [a, b])
        # exposedRect 为空（整层刷新）时退化为全部绘制，两个对象都要出现。
        img = _render(item, QRectF(), 1100, 1100)
        self.assertGreater(_yellow_count(img, 50, 50, 90, 90), 0)
        self.assertGreater(_yellow_count(img, 1000, 1000, 1040, 1040), 0)


if __name__ == "__main__":
    unittest.main()
