"""双色暗色主题的固化测试。

用户需求：暗色模式只保留两个界面区分色 —— 近黑 ``#1e1e1e``（INK）与
偏黑灰 ``#2a2e34``（ASH），其余全部靠这两个色的深浅/透明度表达。
2026-11-18 面板边界线定为 1px ASH（两色里较浅的暗色），不新增第三种色。

这里锁住三条不变量：

1. **界面部件只用双色**：面板 / 工具栏 / 按钮 / 标签页 / 输入框等处的
   背景与边框，取值必须落在 {INK, ASH} 里（含 rgba 派生的半透明灰阶）。
2. **文字保持可读**：文字不在这两个深色里（#2a2e34 压在 #1e1e1e 上对比度
   仅 1.2:1，读不出来），单独用浅色，且集中在 theme.py 一处。
3. **功能彩色不被动**：图标、触发器连线 / 端口 / 节点类型色、对象框、
   抓取虚影、小地图视口框、地图数据里的颜色都保持原样。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QLineEdit, QSpinBox, QStyleFactory,
)

from rwmap.theme import (
    APP_QSS, ASH, INK, PANEL_BORDER_COLOR, PANEL_BORDER_PX, TEXT, TEXT_DIM,
    WIN11_BLUE, apply_app_theme, qc,
)

ROOT = Path(__file__).resolve().parents[2]

# 允许出现在「界面区分」位置的颜色：双色本身 + 由双色派生的半透明灰阶。
_TWO_COLOR = {INK.lower(), ASH.lower()}


def _norm(color: str) -> str:
    """把 #rgb / #rrggbb / rgba(...) 统一成小写 #rrggbb（丢掉 alpha）。"""
    c = color.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", c)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return "#" + h
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*"
                     r"(?:\s*,\s*[\d.]+\s*)?\)", c)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        return "#%02x%02x%02x" % (r, g, b)
    return c


class ThemeConstantTest(unittest.TestCase):
    def test_two_base_colors(self):
        # 换色只需改 theme.py 常量；这里只锁「恰好两种、互不相同、都是深色」，
        # 不再写死具体 hex，避免以后调色就要改测试。
        self.assertEqual(len(_TWO_COLOR), 2)
        self.assertNotEqual(INK.lower(), ASH.lower())
        for value in (INK, ASH):
            c = QColor(value)
            self.assertLess(c.lightness(), 60, f"{value} 不是深色")
        # 面板线色必须是两色里较浅的 ASH，宽度 1px，不引入第三色。
        self.assertEqual(PANEL_BORDER_COLOR.lower(), ASH.lower())
        self.assertEqual(PANEL_BORDER_PX, 1)
        self.assertGreater(QColor(ASH).lightness(), QColor(INK).lightness())

    def test_text_is_readable(self):
        """文字必须与背景有足够对比度。"""
        for name, fg in (("TEXT", TEXT), ("TEXT_DIM", TEXT_DIM)):
            ratio = _ratio(fg, INK)
            self.assertGreater(ratio, 4.5, f"{name} 在 INK 底上对比度不足")

    def test_qc_factory_sets_alpha(self):
        c = qc(INK, 190)
        self.assertEqual(c.alpha(), 190)
        self.assertEqual((c.red(), c.green(), c.blue()),
                         (QColor(INK).red(), QColor(INK).green(), QColor(INK).blue()))
        self.assertEqual(qc(ASH).alpha(), 255)

    def test_app_qss_only_uses_two_colors(self):
        """应用级样式表里出现的所有**背景 / 边框**色都必须在双色集合内。

        文字色（TEXT / TEXT_DIM）单独放行 —— 它们不参与双色收敛，
        目的是保证可读性。
        """
        for prop, value in re.findall(
            r"(background|border[a-z-]*)\s*:\s*([^;]+);", APP_QSS
        ):
            for c in re.findall(r"#[0-9a-fA-F]{6}\b", value):
                self.assertIn(_norm(c), _TWO_COLOR,
                              f"应用样式表 {prop} 出现第三种色：{c}")
        # 文字色只允许 TEXT / TEXT_DIM 两个值。
        for value in re.findall(r"color\s*:\s*([^;]+);", APP_QSS):
            for c in re.findall(r"#[0-9a-fA-F]{6}\b", value):
                self.assertIn(_norm(c), {TEXT.lower(), TEXT_DIM.lower()},
                              f"应用样式表出现意外的文字色：{c}")

    def test_group_and_focus_borders_use_ash(self):
        """分组框与输入控件焦点框统一用 ASH，不出现系统浅灰 / 亮蓝。"""
        group = re.search(r"QGroupBox\s*\{([^}]*)\}", APP_QSS)
        self.assertIsNotNone(group, "缺少 QGroupBox 边框规则")
        self.assertIn(f"border: 1px solid {ASH}", group.group(1))
        focus = re.search(r"QLineEdit:focus[^{]*\{([^}]*)\}",
                          APP_QSS, re.S)
        self.assertIsNotNone(focus, "缺少输入控件焦点边框规则")
        self.assertIn(f"border: 1px solid {ASH}", focus.group(1))
        self.assertIn("color: " + TEXT, APP_QSS,
                      "样式表接管绘制后必须显式给文字色，否则黑字看不见")
        # Qt 6.12 Windows11：给 NoButtons 数字框设 QSS 边框会把内部
        # QLineEdit 压成 1px，2026-11-23 踩过，绝不能加回来。
        qss_no_comments = re.sub(r"/\*.*?\*/", "", APP_QSS, flags=re.S)
        self.assertNotIn("QAbstractSpinBox", qss_no_comments,
                         "数字框不能走 QSS 边框规则，改由 _FocusStyle 处理")
        for bright in ("#a6a6a6", "#4cc2ff", "#0078d4"):
            self.assertNotIn(bright, APP_QSS.lower(),
                             f"应用样式表仍有亮色边框 {bright}")


class FocusBorderPixelTest(unittest.TestCase):
    """Windows11 样式下焦点蓝框由 QStyle 画，必须用像素级用例锁住。"""

    @staticmethod
    def _use_style(app, name):
        # QStyle 对象由 QApplication 持有，切走后原对象会被释放；存名字再恢复。
        # 换样式必须清掉代理标记，否则 apply_app_theme 不会重新装代理。
        app.setStyle(QStyleFactory.create(name))
        app._rw_focus_style = None
        apply_app_theme(app)

    @staticmethod
    def _pixels(widget):
        img = widget.grab().toImage()
        blue = light = 0
        for y in range(img.height()):
            for x in range(img.width()):
                r, g, b, _ = img.pixelColor(x, y).getRgb()
                if b > 90 and b - r > 35 and b - g > 20:
                    blue += 1
                if r > 140 and g > 140 and b > 140:
                    light += 1
        return blue, light

    def test_windows11_focus_border_not_blue(self):
        app = QApplication.instance() or QApplication([])
        old_name = app.style().objectName() or "fusion"
        try:
            self._use_style(app, "windows11")
            spin = QSpinBox()
            spin.resize(200, 32)
            spin.show()
            spin.setFocus()
            app.processEvents()
            blue, _ = self._pixels(spin)
            self.assertLess(blue, 80, "Windows11 样式下仍画出大片亮蓝焦点框")
        finally:
            self._use_style(app, old_name)

    def test_nobutton_spinbox_text_visible(self):
        """回归：NoButtons 数字框内部行编辑被压成 1px 会导致「值在但看不见」。"""
        from rwmap.widgets.plain_spinbox import PlainSpinBox

        app = QApplication.instance() or QApplication([])
        old_name = app.style().objectName() or "fusion"
        old_palette = app.palette()
        try:
            self._use_style(app, "windows11")
            pal = app.palette()
            R = QPalette.ColorRole
            for role in (R.Base, R.Window, R.AlternateBase):
                pal.setColor(role, QColor(INK))
            app.setPalette(pal)

            spin = PlainSpinBox()
            spin.setRange(1, 1000)
            spin.setValue(64)
            spin.setSuffix(" 块")
            spin.resize(200, 32)
            spin.show()
            spin.setFocus()
            app.processEvents()

            line_edit = spin.findChild(QLineEdit)
            self.assertIsNotNone(line_edit)
            self.assertGreater(line_edit.width(), 100,
                               "内部行编辑被压扁，数值看不见")
            blue, light = self._pixels(spin)
            self.assertLess(blue, 80, "数字框仍有大片亮蓝焦点框")
            self.assertGreater(light, 0, "深色输入框里看不到文字")
        finally:
            app.setPalette(old_palette)
            self._use_style(app, old_name)


def _lum(hex_color: str) -> float:
    c = QColor(_norm(hex_color))
    vals = []
    for ch in (c.red(), c.green(), c.blue()):
        s = ch / 255.0
        vals.append(s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]


def _ratio(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


class PanelColorTest(unittest.TestCase):
    """抽查各面板实际设置出来的样式，确认只剩双色。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_app_theme(cls.app)

    def _panel_styles(self):
        from rwmap.widgets.dock_chrome import (
            PANEL_BG, TITLE_BAR_BG, TITLE_COLOR, TITLE_COLOR_FLAT,
        )
        from rwmap.widgets.layers_dock import _PANEL_STYLE
        from rwmap.widgets.tilesets_dock import _SCROLL_STYLE, _TAB_STYLE
        from rwmap.widgets.panel_settings_dialog import _TREE_STYLE
        yield "dock_chrome.TITLE_BAR_BG", TITLE_BAR_BG
        yield "dock_chrome.PANEL_BG", PANEL_BG
        yield "dock_chrome.TITLE_COLOR", TITLE_COLOR
        yield "dock_chrome.TITLE_COLOR_FLAT", TITLE_COLOR_FLAT
        yield "layers_dock._PANEL_STYLE", _PANEL_STYLE
        yield "tilesets_dock._TAB_STYLE", _TAB_STYLE
        yield "tilesets_dock._SCROLL_STYLE", _SCROLL_STYLE
        yield "panel_settings_dialog._TREE_STYLE", _TREE_STYLE

    def test_panel_styles_use_only_two_colors(self):
        """各面板样式里的**背景 / 边框**色必须只剩双色。"""
        text_ok = {TEXT.lower(), TEXT_DIM.lower()}
        for name, style in self._panel_styles():
            # Win11 蓝只用于透明度滑杆的拖动球（功能圆球），不属于双色底色。
            if name == "layers_dock._PANEL_STYLE":
                style = re.sub(r"QSlider::handle:horizontal\s*\{[^}]*\}", "", style)
            for prop, value in re.findall(
                r"(background|border[a-z-]*)\s*:\s*([^;{}]+);?", style
            ):
                for c in re.findall(r"#[0-9a-fA-F]{6}\b", value):
                    self.assertIn(_norm(c), _TWO_COLOR,
                                  f"{name} 的 {prop} 出现非双色值：{c}")
            # 文字色只允许 TEXT / TEXT_DIM。
            for value in re.findall(r"color\s*:\s*([^;{}]+);?", style):
                for c in re.findall(r"#[0-9a-fA-F]{6}\b", value):
                    self.assertIn(_norm(c), text_ok,
                                  f"{name} 出现意外的文字色：{c}")

    def test_layers_dock_row_colors(self):
        from rwmap.widgets import layers_dock
        self.assertEqual(layers_dock._BG.name().lower(), INK)
        self.assertEqual(layers_dock._ROW_HOVER.name().lower(), ASH)
        self.assertEqual(layers_dock._ROW_SELECT.name().lower(), ASH)

    def test_tilesets_dock_colors(self):
        from rwmap.widgets import tilesets_dock
        self.assertEqual(tilesets_dock._BG.name().lower(), INK)
        self.assertEqual(tilesets_dock._GRID.name().lower(), ASH)

    def test_tab_selection_is_dark_unselected_is_light(self):
        """文档标签栏和图块集标签栏：选中用深色 INK，未选中用浅色 ASH。"""
        from rwmap.app.main_window import _DOC_TAB_STYLE
        from rwmap.widgets.tilesets_dock import _TAB_STYLE
        for name, style in (("doc_tabs", _DOC_TAB_STYLE),
                            ("tileset_tabs", _TAB_STYLE)):
            self.assertIn(
                f"QTabBar::tab:selected {{ background: {INK};",
                style, f"{name} 选中标签应为深色 INK")
            self.assertRegex(
                style,
                r"QTabBar::tab\s*\{[^}]*background: " + re.escape(ASH) + r";",
                f"{name} 未选中标签应为浅色 ASH")
            self.assertNotIn(
                f"QTabBar::tab:selected {{ background: {ASH};",
                style, f"{name} 选中标签不应再是浅色 ASH")

    def test_map_view_background_is_ink(self):
        """画布外底用 INK，且去掉原生浅色边框。"""
        from rwmap.view.map_view import MapView
        from PySide6.QtWidgets import QGraphicsView
        view = MapView.__new__(MapView)          # 只测样式设置，不构造完整视图
        self.assertIsNotNone(MapView)
        # 直接检查源码里的两处设置，避免依赖完整视图构造。
        import inspect
        src = inspect.getsource(MapView.__init__)
        self.assertIn("QColor(INK)", src)
        self.assertIn("QGraphicsView.NoFrame", src)


class FunctionalColorKeptTest(unittest.TestCase):
    """功能彩色按需求保留，不能被双色收敛误伤。"""

    def test_trigger_semantic_colors_kept(self):
        from rwmap.widgets.trigger_inspector import (
            EDGE_COLORS, THEME, TYPE_COLORS,
        )
        self.assertEqual(THEME["port_out"], "#e09a3a")
        self.assertEqual(TYPE_COLORS["unitAdd"], "#2f8f52")
        self.assertEqual(EDGE_COLORS["alsoActivate"], "#57c878")

    def test_selection_color_kept(self):
        from rwmap.view.selection_style import SELECT
        self.assertEqual(SELECT.name().lower(), "#2f86dc")

    def test_minimap_viewport_kept(self):
        from rwmap.widgets.minimap import _VP
        self.assertEqual(_VP.name().lower(), "#78c8ff")

    def test_object_yellow_kept(self):
        from rwmap.view.object_layer_item import OBJECT_YELLOW
        self.assertEqual(OBJECT_YELLOW.name().lower(), "#ffc83c")

    def test_win11_blue_round_controls_kept(self):
        """透明度拖动球与单选选中球用 Win11 默认蓝，不污染双色底色。"""
        from rwmap.widgets import layers_dock, tileset_editor
        self.assertEqual(WIN11_BLUE.lower(), "#0078d4")
        self.assertIn(f"background: {WIN11_BLUE}", layers_dock._PANEL_STYLE)
        self.assertIn(WIN11_BLUE, tileset_editor._RADIO_CHECKED_STYLE)
        self.assertNotIn(WIN11_BLUE, APP_QSS)

    def test_icon_blue_kept(self):
        from rwmap.widgets.object_toolbar import _ICON_BLUE
        self.assertEqual(_ICON_BLUE.name().lower(), "#50aaf5")

    def test_map_data_colors_untouched(self):
        """地图数据里的默认文字色是写进 .tmx 的内容，不能改成主题色。"""
        from rwmap.document.map_object import TEXT_DEFAULTS
        self.assertEqual(TEXT_DEFAULTS["color"], "#000000")


if __name__ == "__main__":
    unittest.main()
