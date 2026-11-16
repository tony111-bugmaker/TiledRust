"""「制作图集」功能测试：.tsx 写回 / 读回往返、图集编辑器、多文档标签接入。

第二十五轮：单张图集大图 → 生成铁锈瓦片图集（.tsx），
在独立标签里逐格调属性（不画画）。
第二十六轮：右侧属性面板改为「地形 / 单位 / 其他」类别分页，
地形与单位互斥，其他页可加自定义键名。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import TestCase, mock

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from rwmap.document.properties import PropertyBag
from rwmap.document.tileset import Tileset
from rwmap.document.tileset_document import TilesetDocument
from rwmap.io.tsx_reader import read_tsx
from rwmap.widgets.tile_property_defs import (
    CAT_OTHER, CAT_TERRAIN, CAT_UNIT, category_of,
    cross_category_conflicts, detect_category, other_category_keys,
)
from rwmap.widgets.tileset_editor import TilesetEditorWidget


def _make_atlas(path: Path, w: int = 60, h: int = 40):
    """生成一张 w×h 的测试图集大图（交错两色，便于肉眼区分）。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    col_a = (255, 80, 80, 255)
    col_b = (80, 160, 255, 255)
    for r in range(0, h, 20):
        for c in range(0, w, 20):
            draw.rectangle([c, r, c + 19, r + 19],
                           fill=col_a if (c // 20 + r // 20) % 2 == 0 else col_b)
    img.save(path)
    return path


class TilesetTsxRoundTripTest(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rw_tileset_"))
        self.img = _make_atlas(self.tmp / "terrain.png", 60, 40)

    def _build(self) -> TilesetDocument:
        ts = Tileset(
            firstgid=0, name="地形", tile_w=20, tile_h=20, spacing=0, margin=0,
            tilecount=6, columns=3, image_source=self.img.name,
            image_w=60, image_h=40,
        )
        ts.tiles[3] = PropertyBag()
        ts.tiles[3].set("type", "grass")
        ts.tiles[3].set("walkable", "1")
        return TilesetDocument(
            path=self.tmp / "地形.tsx", tileset=ts, image_path=self.img)

    def test_save_writes_tsx_and_copies_image(self):
        doc = self._build()
        out = doc.save()
        self.assertTrue(out.is_file())
        self.assertTrue((out.parent / "terrain.png").is_file(),
                        "图片应复制到 .tsx 同目录")

    def test_read_back_preserves_grid_and_props(self):
        doc = self._build()
        out = doc.save()
        ts2 = read_tsx(out, firstgid=1)
        self.assertEqual(ts2.name, "地形")
        self.assertEqual(ts2.tile_w, 20)
        self.assertEqual(ts2.tile_h, 20)
        self.assertEqual(ts2.columns, 3)
        self.assertEqual(ts2.tilecount, 6)
        self.assertEqual(ts2.count, 6)
        self.assertEqual(ts2.image_source, "terrain.png")
        # 每瓦片属性往返一致
        bag = ts2.tile_props(3)
        self.assertEqual(bag.get("type"), "grass")
        self.assertEqual(bag.get("walkable"), "1")
        # 图片尺寸由真实 PNG 头读出
        self.assertEqual(ts2.image_w, 60)
        self.assertEqual(ts2.image_h, 40)

    def test_resave_updates_in_place(self):
        doc = self._build()
        out = doc.save()
        ts = out  # 复用写回产物
        ts2 = read_tsx(ts, firstgid=1)
        self.assertEqual(ts2.tilecount, 6)


class TilesetEditorTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rw_tsedit_"))
        self.img = _make_atlas(self.tmp / "t.png", 40, 40)
        ts = Tileset(name="单位", tile_w=20, tile_h=20, tilecount=4, columns=2,
                     image_source=self.img.name, image_w=40, image_h=40)
        self.tdoc = TilesetDocument(
            path=self.tmp / "单位.tsx", tileset=ts, image_path=self.img)

    def _boot(self):
        """构造图集编辑器 + 左侧「瓦片属性」停靠面板，并把点格事件接过去。

        之后测试仍可直接用 ``editor._on_tile(n)`` / ``editor.props``，
        行为与原先内嵌编辑器完全一致，只是属性面板现在独立成 dock。
        """
        from rwmap.widgets.tileset_settings_dock import TilesetTilePropsDock
        editor = TilesetEditorWidget()
        dock = TilesetTilePropsDock()
        editor.tileClicked.connect(dock.select_tile)
        editor.set_tileset(self.tdoc.tileset, self.tdoc)
        dock.set_context(self.tdoc.tileset, self.tdoc, None)
        editor.props = dock.panel
        editor._on_tile = lambda n: dock.select_tile(n)
        return editor, dock

    def test_point_select_terrain_writes_empty_value(self):
        """点选地形属性即可写入，键存在即生效（值留空），不用打字。"""
        editor, dock = self._boot()
        editor._on_tile(1)
        props = editor.props
        self.assertTrue(props.terrain_group.exclusive())
        self.assertIn("water", props.terrain_radios)
        self.assertIn("lava", props.terrain_radios)
        props.select_terrain("water")
        bag = self.tdoc.tileset.tile_props(1)
        self.assertEqual(bag.get("water"), "")
        self.assertEqual(len(bag), 1)
        editor.deleteLater()
        dock.deleteLater()

    def test_switching_terrain_removes_old_key(self):
        """互斥组：换选另一个地形属性要自动删掉旧键，防止冲突。"""
        editor, dock = self._boot()
        editor._on_tile(1)
        editor.props.select_terrain("water")
        editor.props.select_terrain("lava")
        bag = self.tdoc.tileset.tile_props(1)
        self.assertEqual(list(bag.values), ["lava"])
        self.assertEqual(bag.get("lava"), "")
        # 「普通陆地（不设置）」= 清掉所有地形键，且不留下空 bag
        editor.props.select_terrain(None)
        self.assertNotIn(1, self.tdoc.tileset.tiles)
        editor.deleteLater()
        dock.deleteLater()

    def test_value_prop_uses_preset_and_can_be_unchecked(self):
        """单位属性从预设取值，且与地形互斥：写入一类自动清掉另一类。"""
        editor, dock = self._boot()
        editor._on_tile(1)
        props = editor.props
        props.set_value_prop("team", True, "3")
        bag = self.tdoc.tileset.tile_props(1)
        self.assertEqual(bag.get("team"), "3")
        self.assertEqual(props.category, CAT_UNIT)
        # 切到地形并选中 water → team 自动移除，复选框也同步取消勾选
        props.select_terrain("water")
        self.assertEqual(set(bag.values), {"water"})
        self.assertFalse(props.value_checks["team"].isChecked())
        # 反过来勾选单位 → 地形键自动移除，单选回到「普通陆地」
        props.set_value_prop("team", True, "3")
        self.assertEqual(set(bag.values), {"team"})
        self.assertTrue(props.terrain_radios[None].isChecked())
        props.set_value_prop("team", False)
        self.assertNotIn("team", bag.values)
        self.assertNotIn(1, self.tdoc.tileset.tiles)
        editor.deleteLater()
        dock.deleteLater()

    def test_conflict_banner_and_normalize(self):
        """读入旧 .tsx 里的多重冲突时告警，一键整理只留当前类别的属性。"""
        bag = PropertyBag()
        bag.set("water", "")
        bag.set("lava", "")
        bag.set("team", "2")
        self.tdoc.tileset.tiles[1] = bag
        editor, dock = self._boot()
        editor._on_tile(1)
        self.assertIsNotNone(editor.props.conflict_bar)
        # headless 下父窗口没 show，isVisible() 恒 False，用 isHidden() 判断
        self.assertFalse(editor.props.conflict_bar.isHidden())
        self.assertEqual(editor.props.category, CAT_TERRAIN)
        editor.props.normalize_conflicts()
        # 地形内互斥只留第一个，且地形 / 单位跨类别冲突清掉 team
        self.assertEqual(set(bag.values), {"water"})
        self.assertIsNone(editor.props.conflict_bar)
        editor.deleteLater()
        dock.deleteLater()

    def test_unknown_keys_preserved_and_editable(self):
        """未知键原样保留，键名 / 值都可改、可删，绝不清洗。"""
        bag = PropertyBag()
        bag.set("custom", "abc")
        self.tdoc.tileset.tiles[1] = bag
        editor, dock = self._boot()
        editor._on_tile(1)
        self.assertIn("custom", editor.props.extra_rows)
        editor.props.extra_rows["custom"].setText("xyz")
        self.assertEqual(bag.get("custom"), "xyz")
        editor.props._remove_extra("custom")
        self.assertNotIn(1, self.tdoc.tileset.tiles)
        editor.deleteLater()
        dock.deleteLater()

    def test_add_from_known_replaces_terrain_instead_of_conflicting(self):
        bag = PropertyBag()
        bag.set("water", "")
        self.tdoc.tileset.tiles[1] = bag
        editor, dock = self._boot()
        editor._on_tile(1)
        combo = editor.props.more_combo
        idx = combo.findData("lava")
        self.assertGreaterEqual(idx, 0)
        combo.setCurrentIndex(idx)
        editor.props.more_btn.click()
        self.assertEqual(list(bag.values), ["lava"])
        editor.deleteLater()
        dock.deleteLater()

    def test_category_switch_only_shows_matching_section_and_keeps_data(self):
        """顶部类别按钮：只显示对应那一页；切换本身不删任何属性。"""
        bag = PropertyBag()
        bag.set("water", "")
        bag.set("myFlag", "yes")
        self.tdoc.tileset.tiles[1] = bag
        editor, dock = self._boot()
        editor._on_tile(1)
        props = editor.props
        self.assertEqual(props.category, CAT_TERRAIN)   # 冲突时优先地形
        self.assertFalse(props.sections[CAT_TERRAIN].isHidden())
        self.assertTrue(props.sections[CAT_UNIT].isHidden())
        self.assertTrue(props.sections[CAT_OTHER].isHidden())
        self.assertEqual(len(props.cat_buttons), 3)
        self.assertEqual(props.cat_group.exclusive(), True)

        props.cat_buttons[CAT_UNIT].click()
        self.assertEqual(props.category, CAT_UNIT)
        self.assertTrue(props.sections[CAT_TERRAIN].isHidden())
        self.assertFalse(props.sections[CAT_UNIT].isHidden())
        self.assertTrue(props.sections[CAT_OTHER].isHidden())
        # 点类别只是「看」，不能悄悄删数据
        self.assertEqual(set(bag.values), {"water", "myFlag"})

        props.cat_buttons[CAT_OTHER].click()
        self.assertEqual(props.category, CAT_OTHER)
        self.assertFalse(props.sections[CAT_OTHER].isHidden())
        self.assertIn("myFlag", props.extra_rows)
        editor.deleteLater()
        dock.deleteLater()

    def test_category_auto_detected_from_existing_props(self):
        """换瓦片时按已有属性自动选页：单位→单位页，只有自定义→其他页。"""
        editor, dock = self._boot()
        editor._on_tile(0)
        self.assertEqual(editor.props.category, CAT_TERRAIN)

        unit_bag = PropertyBag()
        unit_bag.set("unit", "tank")
        self.tdoc.tileset.tiles[1] = unit_bag
        editor._on_tile(1)
        self.assertEqual(editor.props.category, CAT_UNIT)

        other_bag = PropertyBag()
        other_bag.set("myFlag", "1")
        self.tdoc.tileset.tiles[2] = other_bag
        editor._on_tile(2)
        self.assertEqual(editor.props.category, CAT_OTHER)
        editor.deleteLater()
        dock.deleteLater()

    def test_custom_property_add_rename_and_validation(self):
        """「其他」页能加自定义键名，能改键名且不残留旧键，非法输入被拦下。"""
        editor, dock = self._boot()
        editor._on_tile(1)
        props = editor.props
        props.set_category(CAT_OTHER)
        props.custom_key.setText("myFlag")
        props.custom_value.setText("7")
        props.custom_btn.click()
        bag = self.tdoc.tileset.tile_props(1)
        self.assertEqual(bag.get("myFlag"), "7")
        self.assertEqual(props.category, CAT_OTHER)
        self.assertIn("myFlag", props.extra_key_rows)

        # 空键名 / 已知键名都不允许走自定义入口
        props.custom_key.setText("   ")
        props.custom_btn.click()
        self.assertIn("键名不能为空", props.custom_hint.text())
        props.custom_key.setText("water")
        props.custom_btn.click()
        self.assertIn("已知属性", props.custom_hint.text())
        self.assertNotIn("water", bag.values)
        # 重名也被拦下
        props.custom_key.setText("myFlag")
        props.custom_btn.click()
        self.assertIn("已存在", props.custom_hint.text())

        # 改键名：旧键必须消失，值要跟着迁移
        props.extra_key_rows["myFlag"].setText("myFlag2")
        props.extra_key_rows["myFlag"].editingFinished.emit()
        self.assertNotIn("myFlag", bag.values)
        self.assertEqual(bag.get("myFlag2"), "7")
        self.assertIn("myFlag2", props.extra_rows)
        # 改成已知键名被拒绝并回滚
        props.extra_key_rows["myFlag2"].setText("lava")
        props.extra_key_rows["myFlag2"].editingFinished.emit()
        self.assertEqual(bag.get("myFlag2"), "7")
        self.assertNotIn("lava", bag.values)
        self.assertEqual(props.extra_key_rows["myFlag2"].text(), "myFlag2")
        editor.deleteLater()
        dock.deleteLater()

    def test_other_props_can_coexist_with_terrain(self):
        """自定义 / 未知键属「其他」，不参与地形与单位的互斥。"""
        editor, dock = self._boot()
        editor._on_tile(1)
        props = editor.props
        props.set_category(CAT_OTHER)
        props.custom_key.setText("myFlag")
        props.custom_value.setText("x")
        props.custom_btn.click()
        props.select_terrain("water")
        self.assertEqual(set(self.tdoc.tileset.tile_props(1).values),
                         {"myFlag", "water"})
        editor.deleteLater()
        dock.deleteLater()

    def test_add_from_known_switches_category_and_clears_opposite(self):
        """在「其他」页从已知键里加单位键，自动切到单位页并清掉地形。"""
        bag = PropertyBag()
        bag.set("lava", "")
        self.tdoc.tileset.tiles[1] = bag
        editor, dock = self._boot()
        editor._on_tile(1)
        props = editor.props
        props.set_category(CAT_OTHER)
        idx = props.more_combo.findData("unit")
        self.assertGreaterEqual(idx, 0)
        props.more_combo.setCurrentIndex(idx)
        props.more_btn.click()
        self.assertEqual(props.category, CAT_UNIT)
        self.assertEqual(set(bag.values), {"unit"})
        self.assertEqual(bag.get("unit"), "builder")
        self.assertFalse(props.sections[CAT_UNIT].isHidden())
        editor.deleteLater()
        dock.deleteLater()

    def test_atlas_loads_image_and_all_rows_are_selectable(self):
        """新建图集（还没落盘、base_dir 仍是 "."）也必须显示图片；
        且第二行往后的瓦片要能点中，rows 不能恒为 1。"""
        editor, dock = self._boot()
        atlas = editor.atlas
        self.assertIsNotNone(atlas._pix)
        self.assertFalse(atlas._pix.isNull())
        self.assertEqual((atlas.columns, atlas.rows), (2, 2))
        # (10,10) → 第 0 行第 0 列；(30,30) → 第 1 行第 1 列 = #3
        self.assertEqual(atlas._tile_at(QPointF(10, 10)), 0)
        self.assertEqual(atlas._tile_at(QPointF(30, 30)), 3)
        editor.deleteLater()
        dock.deleteLater()

    def test_atlas_falls_back_to_base_dir_for_saved_tsx(self):
        """读回已有 .tsx 时没有 image_path，应能按 base_dir + image_source 找到图。"""
        self.tdoc.tileset.base_dir = self.tmp
        self.tdoc.image_path = None
        editor, dock = self._boot()
        self.assertIsNotNone(editor.atlas._pix)
        self.assertFalse(editor.atlas._pix.isNull())
        self.assertEqual(editor.atlas.rows, 2)
        editor.deleteLater()
        dock.deleteLater()


class TilePropertyCategoryTest(TestCase):
    """类别词典的纯函数：类别归属、初始页推断、跨类别互斥判定。"""

    def test_category_of(self):
        self.assertEqual(category_of("water-bridge"), CAT_TERRAIN)
        self.assertEqual(category_of("lava"), CAT_TERRAIN)
        self.assertEqual(category_of("unit"), CAT_UNIT)
        self.assertEqual(category_of("team"), CAT_UNIT)
        self.assertEqual(category_of("whatever_custom"), CAT_OTHER)

    def test_detect_category(self):
        self.assertEqual(detect_category([]), CAT_TERRAIN)
        self.assertEqual(detect_category(["myFlag"]), CAT_OTHER)
        self.assertEqual(detect_category(["team"]), CAT_UNIT)
        # 冲突数据优先按地形页打开，让用户看到地形冲突
        self.assertEqual(detect_category(["water", "unit"]), CAT_TERRAIN)

    def test_cross_category_conflicts(self):
        self.assertEqual(cross_category_conflicts(["water", "myFlag"]), {})
        found = cross_category_conflicts(["water", "lava", "team", "myFlag"])
        self.assertEqual(found[CAT_TERRAIN], ["water", "lava"])
        self.assertEqual(found[CAT_UNIT], ["team"])

    def test_other_category_keys(self):
        self.assertIn("team", other_category_keys(CAT_TERRAIN))
        self.assertIn("water", other_category_keys(CAT_UNIT))
        self.assertEqual(other_category_keys(CAT_OTHER), ())


class TilesetMultiDocTest(TestCase):
    """多文档标签接入：新建图集 → 作为 tileset 类型标签打开。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rw_tsmd_"))
        self.img = _make_atlas(self.tmp / "t.png", 40, 40)
        ts = Tileset(name="地形", tile_w=20, tile_h=20, tilecount=4, columns=2,
                     image_source=self.img.name, image_w=40, image_h=40)
        self.tdoc = TilesetDocument(
            path=self.tmp / "地形.tsx", tileset=ts, image_path=self.img)

    def _make_window(self):
        from rwmap.app.main_window import MainWindow
        w = MainWindow(auto_open=False)
        QApplication.processEvents()
        return w

    def test_new_tileset_opens_as_tab(self):
        from rwmap.app.app_state import AppState

        class FakeDlg:
            def __init__(self, *a, **k):
                self.document = self._doc = None
            def exec(self):
                return QDialog.Accepted

        # 让 _new_tileset 拿到我们准备好的 TilesetDocument
        def fake_dialog(*a, **k):
            d = FakeDlg()
            d.document = self.tdoc
            return d

        w = self._make_window()
        try:
            with mock.patch("rwmap.app.main_window.NewTilesetDialog", fake_dialog):
                before = len(w._contexts)
                w._new_tileset()
                QApplication.processEvents()
                self.assertEqual(len(w._contexts), before + 1)
                ctx = w._contexts[-1]
                self.assertEqual(ctx.kind, "tileset")
                self.assertIs(ctx.tileset_doc, self.tdoc)
                # 中央应切到图集页，地图专属面板隐藏
                self.assertEqual(w._central_stack.currentIndex(), w._tileset_page)
                self.assertIs(
                    w._central_stack.widget(w._central_stack.currentIndex()),
                    w._tileset_editor)
                self.assertFalse(w.layers_dock.isVisible())
                # 回归：标签栏必须在页面堆栈外，切到图集页后依然可见，
                # 否则整条标签栏随地图页隐藏，用户就切不回地图了。
                w.show()
                QApplication.processEvents()
                self.assertTrue(w.doc_tabs.isVisible())
                self.assertEqual(w.doc_tabs.count(), 2)
                # 标签标题显示图集名
                self.assertEqual(w._tab_title(ctx), "地形")
        finally:
            w.deleteLater()


class TilesetPropsPanelTest(TestCase):
    """图集设置面板：数值框真的显示数值、文字居中、换图按钮可用且可撤销。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import base64
        import io

        from PIL import Image
        from PySide6.QtGui import QUndoStack
        from rwmap.widgets.tileset_settings_panel import TilesetPropsPanel

        self.tmp = Path(tempfile.mkdtemp(prefix="rw_props_"))
        old = self.tmp / "old.png"
        Image.new("RGBA", (40, 40), (0, 140, 120, 255)).save(old)
        buf = io.BytesIO()
        Image.new("RGBA", (40, 40), (0, 140, 120, 255)).save(buf, format="PNG")
        self.b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        self.ts = Tileset(name="Mountain", tile_w=20, tile_h=24,
                          margin=3, spacing=2, tilecount=4, columns=2,
                          image_source=old.name, image_w=40, image_h=40,
                          embedded_png=self.b64)
        self.ts.properties.set("embedded_png", self.b64)
        self.tdoc = TilesetDocument(path=self.tmp / "m.tsx",
                                    tileset=self.ts, image_path=old)
        self.stack = QUndoStack()
        self.panel = TilesetPropsPanel()
        self.panel.set_context(self.ts, self.tdoc, self.stack)
        self.panel.resize(300, 700)
        self.panel.show()
        QApplication.processEvents()

    def tearDown(self):
        self.panel.deleteLater()
        QApplication.processEvents()

    def test_numbers_displayed_and_centered(self):
        self.assertEqual(self.panel.tw_spin.text(), "20")
        self.assertEqual(self.panel.th_spin.text(), "24")
        self.assertEqual(self.panel.margin_spin.text(), "3")
        self.assertEqual(self.panel.spacing_spin.text(), "2")
        # 旧版两个尺寸框被 addStretch 压成 0 宽，数字完全不可见。
        self.assertGreater(self.panel.tw_spin.width(), 30)
        self.assertGreater(self.panel.th_spin.width(), 30)
        self.assertEqual(self.panel.tw_spin.alignment(), Qt.AlignCenter)
        self.assertEqual(self.panel.name_edit.alignment(), Qt.AlignCenter)
        self.assertEqual(self.panel.image_edit.alignment(), Qt.AlignCenter)
        self.assertFalse(
            [w for w in self.panel.findChildren(QLabel)
             if w.text() == "图集属性"],
            "「图集属性」分组标题应已移除")
        self.assertFalse(any("Tile Size" == w.text()
                             for w in self.panel.findChildren(QLabel)),
                         "Tile Size 应译成中文")

    def test_choose_image_enabled_and_replaces_embedded_png(self):
        from PIL import Image

        self.assertTrue(self.panel.image_btn.isEnabled(),
                        "内嵌图集不该禁用「选择…」")
        new_img = self.tmp / "new.png"
        Image.new("RGBA", (60, 40), (255, 0, 0, 255)).save(new_img)
        self.panel._new_image = new_img
        self.panel._mark_dirty()
        self.panel._apply()
        self.assertEqual(self.ts.image_w, 60, "图片尺寸没跟着新图更新")
        self.assertEqual(self.ts.image_source, "new.png")
        self.assertEqual(self.tdoc.image_path, new_img)
        self.assertNotEqual(self.ts.embedded_png, self.b64)
        self.assertEqual(self.ts.properties.get("embedded_png"),
                         self.ts.embedded_png)
        self.assertEqual(self.stack.count(), 1)
        self.stack.undo()
        self.assertEqual(self.ts.embedded_png, self.b64, "撤销没还原旧图")
        self.assertEqual(self.ts.image_w, 40)
        self.assertEqual(self.tdoc.image_path, self.tmp / "old.png")

    def test_saving_tsx_keeps_replaced_embedded_png(self):
        from lxml import etree
        from PIL import Image

        new_img = self.tmp / "another.png"
        Image.new("RGBA", (40, 60), (10, 20, 30, 255)).save(new_img)
        self.panel._new_image = new_img
        self.panel._apply()
        out = self.tdoc.save(self.tmp / "saved.tsx")
        tree = etree.parse(str(out))
        values = tree.xpath("//property[@name='embedded_png']/text()")
        self.assertEqual(values, [self.ts.embedded_png],
                         "另存 .tsx 时换了的新图没写进 embedded_png")


if __name__ == "__main__":
    unittest.main()
