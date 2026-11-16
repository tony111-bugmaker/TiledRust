"""第二十一轮回归：图层面板照搬 Tiled（图标 / 行内眼睛·锁 / 深色主题）。

覆盖：Tiled 原生图标已深色化、面板无白底、行布局无复选框、按钮图标化、
行内点击发信号、双击重命名走撤销、右键菜单、显示/隐藏其它层、旧功能保持。
"""
from __future__ import annotations

import io
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget  # noqa: E402

from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.theme import ASH  # noqa: E402
from rwmap.widgets.layers_dock import LayersDock  # noqa: E402
from rwmap.widgets.tiled_icons import ICON_NAMES, tiled_pixmap  # noqa: E402


def _layers():
    return [
        TileLayer(width=4, height=3, name="Ground", cells=[0] * 12),
        TileLayer(width=4, height=3, name="Units", cells=[0] * 12),
        ObjectLayer(name="Triggers", objects=[]),
        TileLayer(width=4, height=3, name="set", cells=[0] * 12),
    ]


class TiledIconsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_icons_present_and_non_empty(self):
        self.assertEqual(len(ICON_NAMES), 15)
        for name in ICON_NAMES:
            pix = tiled_pixmap(name)
            self.assertFalse(pix.isNull(), name)
            self.assertIn(pix.width(), (14, 16), name)

    def test_icons_have_no_white_opaque_background(self):
        """深色化后不应残留不透明白底（会在深色面板上变成白方块）。"""
        import base64
        from rwmap.widgets import tiled_icons
        for name in ICON_NAMES:
            raw = base64.b64decode(tiled_icons._DATA[name])
            im = Image.open(io.BytesIO(raw)).convert("RGBA")
            white = sum(1 for r, g, b, a in im.getdata()
                        if a > 200 and min(r, g, b) > 235)
            self.assertEqual(white, 0, f"{name} 还有 {white} 个不透明白像素")


class TiledLayersDockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.w = MainWindow(auto_open=False)
        cls.w.resize(1400, 900)
        cls.w.show()
        QApplication.processEvents()

    def setUp(self):
        w = self.w
        layers = _layers()
        doc = MapDocument(width=4, height=3, tile_w=20, tile_h=20,
                          layers=layers, tilesets=[])
        w.state.doc = doc
        w.state.current_layer = layers[0]
        w.state.profile.write_enabled = True
        w._refresh_document()
        QApplication.processEvents()
        self.doc, self.dock = doc, w.layers_dock
        self._names = [l.name for l in layers]

    def tearDown(self):
        for layer, name in zip(self.doc.layers, self._names):
            layer.name = name
            layer.visible = True
            layer.locked = False
        self.dock.set_document(self.doc)

    def _click_icon(self, row: int, which: str):
        item = self.dock.list.item(row)
        rect = self.dock.list.visualItemRect(item)
        eye, lock = self.dock.list.itemDelegate().icon_rects(rect)
        QTest.mouseClick(self.dock.list.viewport(), Qt.LeftButton,
                         Qt.NoModifier, (eye if which == "eye" else lock).center())

    # ---- 行布局 ----
    def test_rows_have_no_checkbox_and_no_kind_text(self):
        d = self.dock
        self.assertEqual(d.list.count(), 4)
        self.assertEqual([d.list.item(i).text() for i in range(4)],
                         ["set", "Triggers", "Units", "Ground"])
        for i in range(4):
            item = d.list.item(i)
            self.assertFalse(item.flags() & Qt.ItemIsUserCheckable)
            self.assertTrue(item.flags() & Qt.ItemIsEditable)
            self.assertEqual(item.sizeHint().height(), 24)
            self.assertNotIn("[tile]", item.text())
            self.assertNotIn("[object]", item.text())
        self.assertEqual(type(d.list.itemDelegate()).__name__, "_LayerRowDelegate")

    def test_buttons_are_icons_not_text_and_in_tiled_order(self):
        d = self.dock
        buttons = (d.btn_new, d.btn_up, d.btn_down, d.btn_dup, d.btn_del,
                   d.btn_lock, d.btn_others)
        for btn in buttons:
            self.assertEqual(btn.text(), "")
            self.assertFalse(btn.icon().isNull(), btn.toolTip())
            self.assertTrue(btn.toolTip())
        self.assertFalse(hasattr(d, "btn_merge"))    # 向下合并按钮已移除
        bar = d.findChild(type(d.btn_new.parent()), "LayerToolBar")
        self.assertIsNotNone(bar)
        widgets = [bar.layout().itemAt(i).widget() for i in range(6)]
        self.assertEqual(widgets, list(buttons[:6]))

    def test_new_button_pops_menu_and_creates_chosen_kind(self):
        """点新建按钮弹「瓦片层 / 对象层」菜单，选中哪项才建哪项；整块判定区、hover 一致。"""
        d = self.dock
        from PySide6.QtWidgets import QToolButton
        # InstantPopup：点整块按钮立即弹菜单；不用 MenuButtonPopup（拆成两半）。
        self.assertNotEqual(d.btn_new.popupMode(), QToolButton.MenuButtonPopup)
        self.assertEqual(d.btn_new.popupMode(), QToolButton.InstantPopup)
        self.assertIsNotNone(d.btn_new.menu())
        self.assertEqual([a.text() for a in d.btn_new.menu().actions()],
                         ["瓦片层", "对象层"])
        self.assertEqual(d.btn_new.size(), d.btn_up.size())

        seen = []
        d.addLayerRequested.connect(lambda kind: seen.append(kind))
        # 点按钮本身不直接建层；触发菜单项才建对应类型。
        for x in (5, d.btn_new.width() // 2, d.btn_new.width() - 4):
            seen.clear()
            QTest.mouseClick(d.btn_new, Qt.LeftButton, Qt.NoModifier,
                             QPoint(x, d.btn_new.height() // 2))
            QApplication.processEvents()
            self.assertEqual(seen, [], f"x={x} 点按钮不应直接建层")
        d.btn_new.menu().actions()[0].trigger()
        self.assertEqual(seen, ["tile"])
        seen.clear()
        d.btn_new.menu().actions()[1].trigger()
        self.assertEqual(seen, ["object"])
        d.btn_new.menu().close()          # 关掉菜单，避免影响 hover 像素
        QApplication.processEvents()

        # hover 渲染与 btn_up 一致（同样的边框/底色，不再是半块高亮）
        QTest.mouseMove(d.btn_new, QPoint(13, 13))
        QApplication.processEvents()
        new_pix = d.btn_new.grab().toImage()
        QTest.mouseMove(d.btn_up, QPoint(13, 13))
        QApplication.processEvents()
        up_pix = d.btn_up.grab().toImage()
        for x in (1, d.btn_new.width() // 2, d.btn_new.width() - 2):
            self.assertEqual(new_pix.pixelColor(x, 1), up_pix.pixelColor(x, 1))

    def test_panel_and_title_bar_are_dark_not_white(self):
        d = self.dock
        self.assertIsNotNone(d.titleBarWidget())
        # 双色暗色主题：标题栏底色取自 theme.ASH。
        self.assertIn(ASH, d.titleBarWidget().styleSheet())
        pix = d.grab()
        path = Path("logs/_layers_pixel_check.png")
        pix.save(str(path))
        im = Image.open(path).convert("RGB")
        pixels = list(im.getdata())
        white = sum(1 for r, g, b in pixels if min(r, g, b) > 235)
        self.assertLess(white / len(pixels), 0.03, "面板出现大片白色")
        # 选中行用主题 ASH 灰，不再是 Tiled 蓝；分量从常量解析，换色后自动跟随。
        ac = QColor(ASH)
        ar, ag, ab = ac.red(), ac.green(), ac.blue()
        ash = sum(1 for r, g, b in pixels
                  if abs(r - ar) < 6 and abs(g - ag) < 6 and abs(b - ab) < 6)
        self.assertGreater(ash, 100, "缺少 ASH 灰色的选中行")

    def test_title_bar_uses_native_dock_icons(self):
        """浮动/关闭按钮必须与「属性」等原生 Dock 完全同款，不能是 ◇ / × 字符
        （用户字体把它渲染成蓝橙圆环）。"""
        d = self.dock
        self.assertIsNotNone(d.titleBarWidget())
        for btn in (d.btn_float, d.btn_close):
            self.assertEqual(btn.text(), "")
            self.assertFalse(btn.icon().isNull())
            self.assertTrue(btn.toolTip())
        native = {b.objectName(): b
                  for b in self.w.properties_dock.findChildren(QWidget)
                  if b.objectName().startswith("qt_dockwidget_")}
        self.assertEqual(set(native),
                         {"qt_dockwidget_floatbutton", "qt_dockwidget_closebutton"})
        for name, btn in (("qt_dockwidget_floatbutton", d.btn_float),
                          ("qt_dockwidget_closebutton", d.btn_close)):
            a = native[name].icon().pixmap(16, 16).toImage()
            b = btn.icon().pixmap(16, 16).toImage()
            self.assertEqual(a, b, f"{name} 图标与原生不一致")
            self.assertEqual(btn.iconSize().width(), 16)

    def test_title_bar_float_button_toggles_floating(self):
        d = self.dock
        before = d.isFloating()
        d.btn_float.click()
        QApplication.processEvents()
        self.assertEqual(d.isFloating(), not before)
        d.btn_float.click()
        QApplication.processEvents()
        self.assertEqual(d.isFloating(), before)

    def test_title_bar_close_button_hides_panel(self):
        d = LayersDock()
        d.show()
        QApplication.processEvents()
        self.assertTrue(d.isVisible())
        d.btn_close.click()
        QApplication.processEvents()
        self.assertFalse(d.isVisible())
        d.deleteLater()

    # ---- 行内交互 ----
    def test_eye_click_toggles_visibility(self):
        d = self.dock
        layer = d._layer_at(0)
        self.assertTrue(layer.visible)
        received = []
        d.layerVisibilityChanged.connect(lambda l, v: received.append((l, v)))
        self._click_icon(0, "eye")
        QApplication.processEvents()
        self.assertEqual(received, [(layer, False)])
        self.assertFalse(layer.visible)
        self._click_icon(0, "eye")
        QApplication.processEvents()
        self.assertTrue(layer.visible)

    def test_lock_click_emits_toggle(self):
        d = self.dock
        layer = d._layer_at(0)
        received = []
        d.lockLayerToggled.connect(lambda l: received.append(l))
        self._click_icon(0, "lock")
        QApplication.processEvents()
        self.assertEqual(received, [layer])
        self.assertTrue(getattr(layer, "locked", False))

    def test_double_click_rename_goes_through_undo(self):
        d, w = self.dock, self.w
        layer = d._layer_at(0)
        received = []
        d.layerRenameRequested.connect(lambda l, n: received.append((l, n)))
        delegate = d.list.itemDelegate()
        editor = QLineEdit()
        editor.setText("新层名")
        index = d.list.model().index(0, 0)
        count = w.state.undo_stack.count()
        # setModelData 发信号，主窗口的 _rename_layer 同步落命令并刷新列表
        delegate.setModelData(editor, d.list.model(), index)
        self.assertEqual(received, [(layer, "新层名")])
        self.assertEqual(layer.name, "新层名")
        self.assertEqual(w.state.undo_stack.count(), count + 1)
        w.state.undo_stack.undo()
        self.assertEqual(layer.name, "set")

    def test_rename_rejects_empty_or_unchanged(self):
        w, layer = self.w, self.dock._layer_at(0)
        before = w.state.undo_stack.count()
        w._rename_layer(layer, "   ")
        w._rename_layer(layer, "set")
        self.assertEqual(layer.name, "set")
        self.assertEqual(w.state.undo_stack.count(), before)

    def test_show_hide_other_layers(self):
        d = self.dock
        current = d.selected_layer()
        self.assertIsNotNone(current)
        d._toggle_other_layers()
        self.assertTrue(current.visible)
        self.assertFalse(any(l.visible for l in self.doc.layers if l is not current))
        d._toggle_other_layers()
        self.assertTrue(all(l.visible for l in self.doc.layers))

    def test_context_menu_has_tiled_actions(self):
        d = self.dock
        rect = d.list.visualItemRect(d.list.item(0))
        menu = d._build_context_menu(QPoint(rect.center()))   # 不弹模态菜单
        texts = [a.text() for a in menu.actions() if a.text()]
        for expected in ("新建瓦片层", "新建对象层", "上移", "下移",
                         "复制图层", "删除图层", "锁定"):
            self.assertIn(expected, texts)
        self.assertNotIn("向下合并", texts)   # 该入口已移除
        self.assertEqual(d.list.contextMenuPolicy(), Qt.CustomContextMenu)

    def test_context_menu_outside_rows_only_offers_new(self):
        d = self.dock
        menu = d._build_context_menu(QPoint(5, d.list.height() - 2))
        self.assertEqual([a.text() for a in menu.actions() if a.text()],
                         ["新建瓦片层", "新建对象层"])

    # ---- 旧功能保持 ----
    def test_select_layer_and_opacity_still_work(self):
        d = self.dock
        target = self.doc.layers[1]
        d.select_layer(target)
        self.assertIs(d.selected_layer(), target)
        got = []
        d.layerOpacityChanged.connect(lambda l, v: got.append((l, v)))
        d.slider.setValue(40)
        self.assertEqual(got, [(target, 0.4)])
        self.assertIn("40%", d.label.text())

    def test_lock_button_toggles_all_other_layers(self):
        """锁定按钮作用于「其它所有层」，不随当前层状态翻转图标/提示。"""
        d = self.dock
        self.assertIn("其它所有层", d.btn_lock.toolTip())
        d.selected_layer().locked = True
        d._update_buttons()
        # 不再随当前层锁定状态翻转文案（始终是「其它所有层」）
        self.assertIn("其它所有层", d.btn_lock.toolTip())
        self.assertNotIn("当前", d.btn_lock.toolTip())
        self.assertFalse(d.btn_lock.icon().isNull())

    def test_lock_other_layers_emits_batch(self):
        """批量锁定信号只包含其它层、目标状态为锁定；不含当前层。"""
        d = self.dock
        current = d.selected_layer()
        received = []
        d.layersLockChanged.connect(lambda changes: received.append(changes))
        d._toggle_other_layers_lock()
        self.assertEqual(len(received), 1)
        changes = received[0]
        self.assertTrue(all(l is not current for l, _ in changes))
        self.assertTrue(all(locked for _, locked in changes))
        self.assertEqual(len(changes), len(self.doc.layers) - 1)


if __name__ == "__main__":
    unittest.main()
