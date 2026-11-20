"""回归：编辑器「插入文字 / 插入图片」在游戏侧的适配。

游戏（铁锈战争 1.15）不认 Tiled 的 <text> / <image> 对象，直接写进
Triggers 层会报 "Skipping trigger ... no properties found" 和
"Unknown type: found on ..."。这里验证：

* 文字 -> type=mapText 触发器（text / textColor / textSize）
* 图片 -> type=info 注释节点 + embedded_png 内嵌图集 + Ground 层贴图（瓦片
  继承被覆盖地形 tile 的通行属性，Items 层资源池不受影响）
* 读回仍还原成编辑器里的文字 / 图片对象，可继续编辑
* 每次保存全量重投影：移动 / 缩放 / 删除后不留幽灵贴图，firstgid 不撞车
"""
from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lxml import etree
from PIL import Image

from rwmap.document.layer import ObjectLayer, TileLayer
from rwmap.document.map_object import MapObject
from rwmap.io.layer_codec import decode
from rwmap.io.tmx_reader import read_document
from rwmap.io.tmx_writer import create_blank_document, write_document
from rwmap.rw import game_decor
from rwmap.rw.version import load_profile

W = H = 12


def make_png(tmp: Path, name="deco.png", size=(40, 40)) -> Path:
    image = Image.new("RGBA", size, (210, 40, 40, 255))
    for x in range(0, size[0], 10):
        for y in range(0, size[1], 10):
            if (x // 10 + y // 10) % 2 == 0:
                image.paste((30, 80, 220, 255), (x, y, x + 10, y + 10))
    path = tmp / name
    image.save(path)
    return path


def make_doc(tmp: Path, objects, *, existing_tileset=False):
    doc = create_blank_document(SimpleNamespace(export_dir=str(tmp)), W, H)
    doc.path = tmp / "decor.tmx"
    doc.layers.append(TileLayer(
        width=W, height=H, name="Ground", kind="tile",
        cells=[0] * (W * H), encoding="base64", compression="gzip", dirty=True))
    doc.layers.append(ObjectLayer(name="Triggers", objects=objects, dirty=True))
    if existing_tileset:
        el = etree.Element("tileset", attrib={
            "firstgid": "1", "name": "terrain", "tilewidth": "20",
            "tileheight": "20", "tilecount": "50", "columns": "10"})
        etree.SubElement(el, "image", attrib={
            "source": "terrain.png", "width": "200", "height": "100"})
        doc.tree.getroot().insert(0, el)
    return doc


def text_obj(text="敌军来袭！"):
    return MapObject(object_id=1, name="文字", shape="text", x=20, y=20,
                     width=160, height=40, text=text,
                     text_style={"fontfamily": "sans-serif", "pixelsize": "14",
                                 "color": "#ffcc00"})


def image_obj(tmp: Path, x=40, y=40, w=40, h=40, name="deco.png", oid=2):
    make_png(tmp, name)
    return MapObject(object_id=oid, name="图片", shape="image", x=x, y=y,
                     width=w, height=h, image_source=name)


def root_of(path: Path):
    return etree.parse(str(path)).getroot()


def object_nodes(root):
    return [o for og in root.findall("objectgroup")
            for o in og.findall("object")]


def triggers_of(doc):
    """按名字取 Triggers 对象层（避免依赖图层在列表中的位置）。"""
    return doc.layer_by_name("Triggers")


def props_of(node):
    return {p.get("name"): (p.get("value") or p.text or "")
            for p in node.iter("property")}


def decor_tilesets(root):
    return [t for t in root.findall("tileset")
            if (t.get("name") or "").startswith(game_decor.DECOR_TILESET_PREFIX)]


def layer_cells(root, name):
    for layer in root.findall("layer"):
        if layer.get("name") == name:
            cells, _, _ = decode(layer.find("data"), W * H)
            return cells
    return None


def items_cells(root):
    return layer_cells(root, "Items")


def ground_cells(root):
    return layer_cells(root, "Ground")


class GameDecorTest(unittest.TestCase):
    def setUp(self):
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_obj.name)
        self.out = self.tmp / "decor.tmx"
        self.profile = load_profile("1.15")

    def tearDown(self):
        self.tmp_obj.cleanup()

    def save(self, doc, path=None):
        return write_document(doc, path or self.out)

    def reload(self, path=None):
        return read_document(path or self.out, self.profile)

    # ---------------- 文字 -> mapText ----------------

    def test_text_becomes_maptext_trigger(self):
        doc = make_doc(self.tmp, [text_obj()])
        # 插入时由主窗口同步 text 属性；这里直接模拟属性包已有内容
        self.save(doc)
        node = object_nodes(root_of(self.out))[0]
        self.assertEqual(node.get("type"), "mapText")
        self.assertIsNone(node.find("text"))
        props = props_of(node)
        self.assertEqual(props.get("text"), "敌军来袭！")
        self.assertEqual(props.get("textColor"), "#ffcc00")
        self.assertEqual(props.get("textSize"), "14")

    def test_text_falls_back_to_obj_text(self):
        """属性包里没有 text（例如旧工程读入）时用 obj.text。"""
        doc = make_doc(self.tmp, [text_obj("旧工程文字")])
        self.save(doc)
        props = props_of(object_nodes(root_of(self.out))[0])
        self.assertEqual(props.get("text"), "旧工程文字")

    def test_property_panel_text_wins(self):
        obj = text_obj()
        obj.properties.set("text", "属性面板改过的字")
        doc = make_doc(self.tmp, [obj])
        self.save(doc)
        props = props_of(object_nodes(root_of(self.out))[0])
        self.assertEqual(props.get("text"), "属性面板改过的字")

    def test_text_roundtrip_restores_editor_shape(self):
        doc = make_doc(self.tmp, [text_obj("建康府")])
        self.save(doc)
        again = self.reload()
        obj = triggers_of(again).objects[0]
        self.assertEqual(obj.shape, "text")
        self.assertEqual(obj.type_, "mapText")
        self.assertEqual(obj.text, "建康府")
        self.assertEqual(obj.text_style.get("color"), "#ffcc00")
        self.assertEqual(obj.text_style.get("pixelsize"), "14")

    def test_legacy_text_node_is_migrated(self):
        """旧版 TMX 里的 <text> 对象，保存后要自动升级成 mapText。"""
        doc = make_doc(self.tmp, [])
        group = doc.layers[-1].xml_element
        if group is None:
            self.save(doc)
            group = doc.layers[-1].xml_element
        old = etree.SubElement(group, "object", attrib={
            "id": "9", "name": "老文字", "x": "20", "y": "20",
            "width": "160", "height": "40"})
        text = etree.SubElement(old, "text", attrib={"pixelsize": "18"})
        text.text = "遗留文字"
        legacy = self.tmp / "legacy.tmx"
        etree.ElementTree(doc.tree.getroot()).write(str(legacy),
                                                    encoding="UTF-8",
                                                    xml_declaration=True)
        again = self.reload(legacy)
        self.save(again)
        node = object_nodes(root_of(self.out))[0]
        self.assertEqual(node.get("type"), "mapText")
        self.assertIsNone(node.find("text"))
        self.assertEqual(props_of(node).get("text"), "遗留文字")

    # ---------------- 图片 -> info + 内嵌图集 ----------------

    def test_image_becomes_info_node(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        self.save(doc)
        node = object_nodes(root_of(self.out))[0]
        self.assertEqual(node.get("type"), "info")
        self.assertIsNone(node.find("image"))
        self.assertEqual(props_of(node).get("image"), "deco.png")

    def test_image_becomes_embedded_tileset(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        self.save(doc)
        root = root_of(self.out)
        tilesets = decor_tilesets(root)
        self.assertEqual(len(tilesets), 1)
        ts = tilesets[0]
        self.assertEqual(ts.get("tilecount"), "4")
        self.assertEqual(ts.get("columns"), "2")
        self.assertEqual(ts.get("tilewidth"), "20")
        prop = next(p for p in ts.iter("property")
                    if p.get("name") == "embedded_png")
        png = base64.b64decode("".join((prop.text or "").split()))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        with Image.open(io.BytesIO(png)) as im:
            self.assertEqual(im.mode, "RGBA")
            self.assertEqual(im.size, (40, 40))
        img = ts.find("image")
        self.assertEqual(img.get("width"), "40")
        self.assertEqual(img.get("height"), "40")
        self.assertTrue((self.tmp / "rwmap_decor_0.png").is_file())

    def test_ground_layer_holds_decor_gids(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        self.save(doc)
        cells = ground_cells(root_of(self.out))
        self.assertIsNotNone(cells)
        nz = [g for g in cells if g]
        self.assertEqual(len(nz), 4)
        self.assertEqual(min(nz), 1)
        self.assertEqual(max(nz), 4)
        # 40,40 -> 第 2 列第 2 行开始的 2x2
        self.assertEqual([cells[i] for i in (2 * W + 2, 2 * W + 3,
                                             3 * W + 2, 3 * W + 3)],
                         [1, 2, 3, 4])

    def test_memory_ground_cells_are_restored(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        self.save(doc)
        layer = doc.layer_by_name("Ground")
        self.assertIsInstance(layer, TileLayer)
        self.assertTrue(all(g == 0 for g in layer.cells))

    def test_firstgid_avoids_existing_tileset(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)], existing_tileset=True)
        self.save(doc)
        ts = decor_tilesets(root_of(self.out))[0]
        self.assertEqual(ts.get("firstgid"), "51")
        self.assertEqual(ground_cells(root_of(self.out))[2 * W + 2], 51)

    def test_no_image_no_decor_tileset(self):
        doc = make_doc(self.tmp, [text_obj()])
        self.save(doc)
        root = root_of(self.out)
        self.assertEqual(decor_tilesets(root), [])
        # 没有图片时不新建 Items 层（图片现在铺 Ground，本就不该有 Items）。
        self.assertIsNone(items_cells(root))

    # ---------------- 重投影 ----------------

    def test_reproject_after_move(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        self.save(doc)
        again = self.reload()
        obj = triggers_of(again).objects[0]
        obj.x, obj.y = 100, 100
        triggers_of(again).dirty = True
        self.save(again)
        root = root_of(self.out)
        self.assertEqual(len(decor_tilesets(root)), 1)
        cells = ground_cells(root)
        self.assertEqual(len([g for g in cells if g]), 4)
        self.assertEqual(cells[2 * W + 2], 0)      # 旧位置已清空
        self.assertEqual(cells[5 * W + 5], 1)

    def test_reproject_after_delete(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        self.save(doc)
        again = self.reload()
        triggers_of(again).objects = []
        triggers_of(again).dirty = True
        self.save(again)
        root = root_of(self.out)
        self.assertEqual(decor_tilesets(root), [])
        self.assertTrue(all(g == 0 for g in ground_cells(root)))

    def test_reproject_after_resize(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        self.save(doc)
        again = self.reload()
        obj = triggers_of(again).objects[0]
        obj.width, obj.height = 60, 20
        triggers_of(again).dirty = True
        self.save(again)
        ts = decor_tilesets(root_of(self.out))[0]
        self.assertEqual(ts.get("tilecount"), "3")
        self.assertEqual(ts.get("columns"), "3")
        self.assertEqual(len([g for g in ground_cells(root_of(self.out)) if g]), 3)

    def test_image_leaves_items_layer_untouched(self):
        """图片铺进 Ground，绝不碰 Items 层：资源池 / 树 / 石头保持原样。

        图片在 (40,40) 覆盖 2x2 格 (2,2)(2,3)(3,2)(3,3)；在 Items 层 (2,2)
        预置一个资源池瓦片 gid=22。保存后该格必须仍是 22，且 Ground 层对应格
        换成图片装饰瓦片。
        """
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        cells = [0] * (W * H)
        cells[2 * W + 2] = 22        # 资源池，位于图片覆盖范围内
        doc.layers.append(TileLayer(
            width=W, height=H, name="Items", kind="tile",
            cells=cells, encoding="base64", compression="gzip", dirty=True))
        self.save(doc)
        got_items = items_cells(root_of(self.out))
        self.assertEqual(got_items[2 * W + 2], 22, "Items 层资源点不能被图片改动")
        got_ground = ground_cells(root_of(self.out))
        # Ground 对应四格换成装饰瓦片（firstgid=1 起）；无地形 tileset 时
        # firstgid=1，装饰 gid 为 1/2/3/4。
        self.assertEqual(sorted(g for g in got_ground if g), [1, 2, 3, 4])

    def test_decor_tile_inherits_terrain_property_and_restores(self):
        """图片瓦片继承原地形 tile 的通行属性，且反复保存不丢。

        构造一个带 water 属性的内嵌地形 tileset（firstgid=1，local id 6 => gid 7），
        把图片覆盖的四格设成 gid 7。保存后装饰 tile 应带 water 属性和
        rwmap_original_gid=7；重新读盘时 Ground 应还原成地形 gid 7；再保存属性仍在。
        """
        from rwmap.io.tsx_reader import parse_tileset_element
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        root = doc.tree.getroot()
        ts_el = etree.Element("tileset", attrib={
            "firstgid": "1", "name": "terrain", "tilewidth": "20",
            "tileheight": "20", "tilecount": "50", "columns": "10"})
        etree.SubElement(ts_el, "image", attrib={
            "source": "t.png", "width": "200", "height": "100"})
        tile_el = etree.SubElement(ts_el, "tile", attrib={"id": "6"})
        tprops = etree.SubElement(tile_el, "properties")
        etree.SubElement(tprops, "property", attrib={"name": "water", "value": ""})
        root.insert(0, ts_el)
        doc.tilesets.append(parse_tileset_element(ts_el, 1, self.tmp))
        doc.rebuild_index()
        ground = doc.layer_by_name("Ground")
        for c, r in ((2, 2), (2, 3), (3, 2), (3, 3)):
            ground.cells[r * W + c] = 7
        self.save(doc)

        decor = decor_tilesets(root_of(self.out))[0]
        first = decor.find("tile")
        dprops = props_of(first)
        self.assertEqual(dprops.get(game_decor.DECOR_ORIGINAL_GID_PROP), "7")
        self.assertIn("water", dprops, "装饰瓦片必须继承原地形的 water 属性")

        again = self.reload()
        self.assertEqual(again.layer_by_name("Ground").cells[2 * W + 2], 7,
                         "读盘应把装饰 gid 还原成原地形 gid")
        self.save(again)
        dprops2 = props_of(decor_tilesets(root_of(self.out))[0].find("tile"))
        self.assertIn("water", dprops2, "反复保存后继承属性不能丢")

    def test_user_painted_tile_is_projection_base(self):
        """用户在 Ground 上新画的地形，作为下一次重投影的基底（不被抹掉）。

        _base_cells 以内存 cells 为准：用户画在装饰格上的瓦片（非装饰 gid）
        下次保存要能作为「原地形」被继承，而不是被旧 data_element 快照清掉。
        """
        from rwmap.io.tsx_reader import parse_tileset_element
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        root = doc.tree.getroot()
        ts_el = etree.Element("tileset", attrib={
            "firstgid": "1", "name": "terrain", "tilewidth": "20",
            "tileheight": "20", "tilecount": "50", "columns": "10"})
        etree.SubElement(ts_el, "image", attrib={
            "source": "t.png", "width": "200", "height": "100"})
        root.insert(0, ts_el)
        doc.tilesets.append(parse_tileset_element(ts_el, 1, self.tmp))
        doc.rebuild_index()
        self.save(doc)
        again = self.reload()
        ground = again.layer_by_name("Ground")
        # 读盘后 Ground 已还原成原地形（此时是 0）；用户手画 gid=9
        ground.cells[2 * W + 2] = 9
        ground.dirty = True
        self.save(again)
        decor = decor_tilesets(root_of(self.out))[0]
        # 该格对应的装饰 tile 记录的原始 gid 应为 9，而非 0。
        origs = {}
        for tile in decor.findall("tile"):
            origs[int(tile.get("id"))] = props_of(tile).get(
                game_decor.DECOR_ORIGINAL_GID_PROP)
        self.assertIn("9", origs.values(),
                      "用户新画的地形应成为装饰瓦片记录的原始地形")

    def test_multiple_images_get_disjoint_gids(self):
        doc = make_doc(self.tmp, [
            image_obj(self.tmp, x=20, y=20, name="a.png", oid=2),
            image_obj(self.tmp, x=100, y=100, name="b.png", oid=3),
        ])
        self.save(doc)
        tilesets = decor_tilesets(root_of(self.out))
        self.assertEqual(len(tilesets), 2)
        ranges = [(int(t.get("firstgid")),
                   int(t.get("firstgid")) + int(t.get("tilecount")) - 1)
                  for t in tilesets]
        self.assertLess(ranges[0][1], ranges[1][0])
        self.assertEqual(len([g for g in ground_cells(root_of(self.out)) if g]), 8)


class MissingGroundTest(unittest.TestCase):
    """异常地图：没有 Ground 层时图片投影安全地跳过，不报错。"""

    def setUp(self):
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_obj.name)
        self.out = self.tmp / "no_ground.tmx"

    def tearDown(self):
        self.tmp_obj.cleanup()

    def test_no_ground_still_safe(self):
        doc = make_doc(self.tmp, [image_obj(self.tmp)])
        doc.layers = [l for l in doc.layers if l.name != "Ground"]
        write_document(doc, self.out)
        self.assertTrue(self.out.is_file())
        self.assertEqual(decor_tilesets(root_of(self.out)), [])


if __name__ == "__main__":
    unittest.main()
