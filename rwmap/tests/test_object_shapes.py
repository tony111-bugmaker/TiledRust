"""回归：Tiled 全对象形状（含文字 / 图片）的读写保真、插入工具栏与画布交互。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from PIL import Image  # noqa: E402
from lxml import etree  # noqa: E402

from rwmap.document.layer import ObjectLayer  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402
from rwmap.io.path_resolver import ResourceResolver  # noqa: E402
from rwmap.io.tmx_reader import read_document  # noqa: E402
from rwmap.io.tmx_writer import write_document  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402


def object_layers(doc):
    return [l for l in doc.layers if isinstance(l, ObjectLayer)]


def legacy_editor(w):
    """旧语义树面板已移除；编辑命令回归用独立实例。"""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from rwmap.widgets.triggers_dock import TriggersDock

    dock = TriggersDock()
    dock.set_document(w.state.doc, w.state.undo_stack, w._refresh_objects)
    return dock


def make_objects():
    """覆盖 Tiled 全部对象形状。"""
    return [
        MapObject(object_id=101, name="矩形", shape="rect",
                  x=20, y=20, width=40, height=40),
        MapObject(object_id=102, name="椭圆", shape="ellipse",
                  x=80, y=20, width=60, height=40),
        MapObject(object_id=103, name="点", shape="point", x=160, y=40),
        MapObject(object_id=104, name="多边形", shape="polygon",
                  x=200, y=20, points=[(0, 0), (40, 0), (40, 40), (0, 40)]),
        MapObject(object_id=105, name="折线", shape="polyline",
                  x=260, y=20, points=[(0, 0), (20, 30), (60, 10)]),
        MapObject(object_id=106, name="图块", shape="tile",
                  x=340, y=20, width=20, height=20, gid=126),
        MapObject(object_id=107, name="文字", shape="text",
                  x=380, y=20, width=160, height=40,
                  text="铁锈战争\n测试文字",
                  text_style={"fontfamily": "sans-serif", "pixelsize": "18",
                              "wrap": "1", "color": "#ffcc00", "bold": "1",
                              "halign": "center", "valign": "center"}),
        MapObject(object_id=108, name="图片", shape="image",
                  x=560, y=20, width=64, height=48,
                  image_source="pic.png"),
    ]


class ShapeRoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile("1.15")
        cls.sample = Path(cls.profile.sample_map)
        if not cls.sample.is_file():
            raise unittest.SkipTest("缺少示例地图")
        cls.resolver = ResourceResolver(cls.sample, cls.profile)

    def _read(self):
        return read_document(self.sample, self.profile, resolver=self.resolver)

    def _reload(self, path):
        return read_document(path, self.profile, resolver=self.resolver)

    def _write(self, doc, path):
        write_document(doc, path)
        return self._reload(path)

    def test_all_shapes_survive_round_trip(self):
        doc = self._read()
        layer = object_layers(doc)[0]
        layer.objects = make_objects()
        layer.dirty = True
        doc.dirty = True
        with tempfile.TemporaryDirectory() as tmp:
            again = self._write(doc, Path(tmp) / "shapes.tmx")
        got = object_layers(again)[0].objects
        self.assertEqual([o.shape for o in got],
                         ["rect", "ellipse", "point", "polygon", "polyline",
                          "tile", "text", "image"])
        self.assertEqual(got[3].points, [(0.0, 0.0), (40.0, 0.0),
                                         (40.0, 40.0), (0.0, 40.0)])
        self.assertEqual(got[4].points, [(0.0, 0.0), (20.0, 30.0),
                                         (60.0, 10.0)])
        self.assertEqual(got[5].gid, 126)
        self.assertEqual(got[6].text, "铁锈战争\n测试文字")
        self.assertEqual(got[6].text_style["pixelsize"], "18")
        self.assertEqual(got[6].text_style["color"], "#ffcc00")
        self.assertEqual(got[7].image_source, "pic.png")
        self.assertEqual(got[7].properties.get("image"), "pic.png")

    def test_shape_xml_elements_are_written(self):
        doc = self._read()
        layer = object_layers(doc)[0]
        layer.objects = make_objects()
        layer.dirty = True
        doc.dirty = True
        with tempfile.TemporaryDirectory() as tmp:
            out = write_document(doc, Path(tmp) / "shapes.tmx")
            root = etree.parse(str(out)).getroot()
        groups = root.findall("objectgroup")
        objects = groups[0].findall("object")
        tags = [[c.tag for c in o if isinstance(c.tag, str)
                 and c.tag in ("ellipse", "point", "polygon",
                               "polyline", "text", "properties")]
                for o in objects]
        self.assertIn("ellipse", tags[1])
        self.assertIn("point", tags[2])
        self.assertIn("polygon", tags[3])
        self.assertIn("polyline", tags[4])
        # 文字 / 图片要适配铁锈战争：不写 Tiled 形状子节点，改成 mapText /
        # info 触发器属性（游戏不认 <text> / <image> 对象）。
        self.assertNotIn("text", tags[6])
        self.assertIn("properties", tags[6])
        self.assertEqual(objects[6].get("type"), "mapText")
        text_props = {p.get("name"): p.get("value") or p.text
                      for p in objects[6].findall("properties/property")}
        self.assertEqual(text_props["text"], "铁锈战争\n测试文字")
        self.assertEqual(text_props["textColor"], "#ffcc00")
        self.assertIn("textSize", text_props)
        self.assertEqual(objects[7].get("type"), "info")
        image_prop = objects[7].find("properties/property[@name='image']")
        self.assertIsNotNone(image_prop)
        self.assertEqual(image_prop.get("value"), "pic.png")

    def test_unrelated_edit_keeps_shape_node_untouched(self):
        """只改名称时，mapText 的 text 属性字节必须完全不变。"""
        doc = self._read()
        layer = object_layers(doc)[0]
        layer.objects = make_objects()
        layer.dirty = True
        doc.dirty = True
        with tempfile.TemporaryDirectory() as tmp:
            first = write_document(doc, Path(tmp) / "a.tmx")
            again = self._reload(first)
            obj = object_layers(again)[0].objects[6]
            prop_path = "properties/property[@name='text']"
            before = etree.tostring(obj.xml_element.find(prop_path))
            obj.name = "改个名字"
            object_layers(again)[0].dirty = True
            again.dirty = True
            second = write_document(again, Path(tmp) / "b.tmx")
            final = self._reload(second)
            obj2 = object_layers(final)[0].objects[6]
            after = etree.tostring(obj2.xml_element.find(prop_path))
        self.assertEqual(obj2.name, "改个名字")
        self.assertEqual(before, after)

    def test_point_and_polygon_bounds(self):
        objs = make_objects()
        self.assertEqual(objs[2].bounds()[:2], (154, 34))
        self.assertEqual(objs[3].bounds()[2:], (40, 40))


class ObjectToolsUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.w = MainWindow(auto_open=False)
        cls.w.resize(1400, 900)
        cls.w.show()
        QApplication.processEvents()
        cls.sample = Path(cls.w.state.profile.sample_map)
        if not cls.sample.is_file():
            raise unittest.SkipTest("缺少示例地图")

    def setUp(self):
        self.w._open(self.sample)
        self.w.state.undo_stack.clear()
        self.w.scene.set_selected_object(None)
        self.w._set_object_tool("brush")

    def _layer(self):
        return object_layers(self.w.state.doc)[0]

    def _count(self):
        return len(self._layer().objects)

    def _drag(self, tool, x0=100, y0=100, x1=160, y1=160):
        w = self.w
        w._set_object_tool(tool)
        w.view.objectToolPressed.emit(float(x0), float(y0))
        w.view.objectToolMoved.emit(float(x1), float(y1))
        w.view.objectToolReleased.emit(float(x1), float(y1))

    def test_toolbar_exposes_all_insert_tools(self):
        bar = self.w.object_toolbar
        names = [a.text() for a in bar.group.actions()]
        self.assertEqual(len(names), 16)      # 第三十四轮合并框选，后又加入「标尺」
        self.assertEqual(names[:4], ["瓦片画笔", "钢笔", "橡皮", "填充"])
        for expected in ("插入矩形", "插入椭圆", "插入点", "插入多边形",
                         "插入折线", "插入图块", "插入模板", "插入文字",
                         "插入图片", "编辑节点", "选择 / 框选"):
            self.assertIn(expected, names)
        self.assertTrue(all(not a.icon().isNull()
                            for a in bar.group.actions()))
        # 「对象层」下拉已移除：默认落点由文档里的 Triggers / 第一个对象层决定。
        self.assertIsInstance(bar.current_layer(), ObjectLayer)

    def test_tool_switch_and_return_to_brush(self):
        w = self.w
        w._set_object_tool("rect")
        self.assertEqual(w.view.object_mode, "rect")
        self.assertEqual(w.object_toolbar.current_tool(), "rect")
        w._set_tool("brush")
        self.assertEqual(w.view.object_mode, "brush")
        self.assertEqual(w.object_toolbar.current_tool(), "brush")

    def test_drag_creates_rect_and_ellipse_and_undo(self):
        for tool, shape in (("rect", "rect"), ("ellipse", "ellipse")):
            before = self._count()
            self._drag(tool)
            layer = self._layer()
            self.assertEqual(len(layer.objects), before + 1)
            obj = layer.objects[-1]
            self.assertEqual(obj.shape, shape)
            self.assertEqual((obj.x, obj.y, obj.width, obj.height),
                             (100.0, 100.0, 60.0, 60.0))
            self.w.state.undo_stack.undo()
            self.assertEqual(self._count(), before)

    def test_point_tool_click_places_point(self):
        before = self._count()
        w = self.w
        w._set_object_tool("point")
        w.view.objectToolPressed.emit(200.0, 200.0)
        w.view.objectToolReleased.emit(200.0, 200.0)
        obj = self._layer().objects[-1]
        self.assertEqual(self._count(), before + 1)
        self.assertEqual(obj.shape, "point")
        self.assertEqual((obj.x, obj.y), (200.0, 200.0))
        self.assertEqual((obj.width, obj.height), (0.0, 0.0))

    def test_polygon_double_click_and_polyline_enter(self):
        w = self.w
        before = self._count()
        w._set_object_tool("polygon")
        for x, y in ((100, 100), (160, 100), (160, 160)):
            w.view.objectToolPressed.emit(float(x), float(y))
            w.view.objectToolReleased.emit(float(x), float(y))
        w.view.objectToolDoubleClicked.emit(160.0, 160.0)
        poly = self._layer().objects[-1]
        self.assertEqual(poly.shape, "polygon")
        self.assertEqual(len(poly.points), 3)
        self.assertEqual(self._count(), before + 1)
        w._object_tool = "brush"          # 清掉未完成的预览状态
        w._cancel_object_tool()

        w._set_object_tool("polyline")
        for x, y in ((200, 200), (240, 260)):
            w.view.objectToolPressed.emit(float(x), float(y))
            w.view.objectToolReleased.emit(float(x), float(y))
        w._finish_poly()
        line = self._layer().objects[-1]
        self.assertEqual(line.shape, "polyline")
        self.assertEqual(len(line.points), 2)
        w.state.undo_stack.undo()
        w.state.undo_stack.undo()
        self.assertEqual(self._count(), before)

    def test_text_tool_creates_text_object_and_writes_back(self):
        w = self.w
        with patch("rwmap.app.main_window.QInputDialog") as dlg:
            dlg.getMultiLineText.return_value = ("地图说明", True)
            self._drag("text", 100, 100, 260, 160)
        obj = self._layer().objects[-1]
        self.assertEqual(obj.shape, "text")
        self.assertEqual(obj.text, "地图说明")
        self.assertEqual(obj.text_attr["wrap"], "1")
        layer = self._layer()
        layer.dirty = True
        w.state.doc.dirty = True
        with tempfile.TemporaryDirectory() as tmp:
            out = write_document(w.state.doc, Path(tmp) / "text.tmx")
            again = read_document(out, w.state.profile,
                                  resolver=w.state.doc.resolver)
        self.assertEqual(object_layers(again)[0].objects[-1].text, "地图说明")

    def test_image_tool_creates_image_object_and_writes_back(self):
        w = self.w
        with tempfile.TemporaryDirectory() as tmp:
            pic = Path(tmp) / "note.png"
            Image.new("RGBA", (32, 24), (210, 90, 130, 255)).save(pic)
            with patch("rwmap.app.main_window.QFileDialog") as fd:
                fd.getOpenFileName.return_value = (str(pic), "")
                self._drag("image", 100, 100, 100, 100)
            obj = self._layer().objects[-1]
            self.assertEqual(obj.shape, "image")
            self.assertTrue(obj.image_source)
            self.assertEqual(obj.properties.get("image"), obj.image_source)
            layer = self._layer()
            layer.dirty = True
            w.state.doc.dirty = True
            out = write_document(w.state.doc, Path(tmp) / "image.tmx")
            again = read_document(out, w.state.profile,
                                  resolver=w.state.doc.resolver)
        self.assertEqual(object_layers(again)[0].objects[-1].shape, "image")

    def test_template_tool_inserts_template_object(self):
        w = self.w
        with tempfile.TemporaryDirectory() as tmp:
            tpl = Path(tmp) / "demo.tx"
            tpl.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<template><object name="仙人掌" type="prop" '
                'width="30" height="44"><ellipse/></object></template>',
                encoding="utf-8")
            with patch("rwmap.app.main_window.QFileDialog") as fd:
                fd.getOpenFileName.return_value = (str(tpl), "")
                self._drag("template", 100, 100, 100, 100)
            obj = self._layer().objects[-1]
            self.assertEqual(obj.shape, "ellipse")
            self.assertEqual(obj.name, "仙人掌")
            self.assertEqual(obj.type_, "prop")
            self.assertEqual((obj.width, obj.height), (30.0, 44.0))
            self.assertTrue(obj.template)
            layer = self._layer()
            layer.dirty = True
            w.state.doc.dirty = True
            out = write_document(w.state.doc, Path(tmp) / "tpl.tmx")
            again = read_document(out, w.state.profile,
                                  resolver=w.state.doc.resolver)
        self.assertTrue(object_layers(again)[0].objects[-1].template)

    def test_node_tool_drags_polygon_vertex_with_undo(self):
        w = self.w
        layer = self._layer()
        obj = MapObject(object_id=9999, name="可编辑多边形", shape="polygon",
                        x=100, y=100,
                        points=[(0, 0), (40, 0), (40, 40)])
        from rwmap.commands.object_command import ObjectCommand, snapshot
        before = snapshot(layer)
        layer.objects.append(obj)
        w.state.undo_stack.push(ObjectCommand(
            w.state.doc, layer, before, snapshot(layer), "测试多边形",
            w._refresh_objects))
        current = layer.objects[-1]
        w.scene.set_selected_object(current)
        w._set_object_tool("nodes")
        w.view.objectToolPressed.emit(140.0, 100.0)
        w.view.objectToolMoved.emit(180.0, 120.0)
        w.view.objectToolReleased.emit(180.0, 120.0)
        self.assertEqual(layer.objects[-1].points[1], (80.0, 20.0))
        w.state.undo_stack.undo()
        self.assertEqual(layer.objects[-1].points[1], (40.0, 0.0))

    def test_dock_edits_text_and_points(self):
        w = self.w
        layer = self._layer()
        obj = MapObject(object_id=9998, name="文字对象", shape="text",
                        x=40, y=40, width=120, height=40, text="旧文字")
        layer.objects.append(obj)
        layer.dirty = True
        w.state.doc.dirty = True
        dock = legacy_editor(w)
        dock.refresh()
        dock._edit_field(layer, obj, "text", "新文字")
        self.assertEqual(self._layer().objects[-1].text, "新文字")
        w.state.undo_stack.undo()
        self.assertEqual(self._layer().objects[-1].text, "旧文字")

    def test_render_all_shapes_does_not_crash(self):
        from PySide6.QtWidgets import QApplication
        layer = self._layer()
        layer.objects.extend(make_objects())
        self.w.scene.refresh_objects()
        self.w.view.grab()
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
