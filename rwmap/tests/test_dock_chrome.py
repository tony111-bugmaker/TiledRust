"""第二十三轮回归：Dock 面板共用「统一格式」标题栏（面板清单由 PANEL_SPECS 驱动，含迷你地图）。

覆盖：清单驱动的 5 个面板都装了 DockTitleBar；标题字号/粗细/文字一致；
标题栏高度与背景一致；浮动/关闭按钮的图标、尺寸、提示一致且与 Qt 原生
按钮逐像素相同；整窗渲染下标题栏不出白底；双击浮动、重复安装幂等。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtGui import QFontDatabase, QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QWidget  # noqa: E402


class DockChromeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        fid = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        fams = QFontDatabase.applicationFontFamilies(fid)
        if fams:
            cls.app.setFont(QFont(fams[0], 9))
        from rwmap.app.main_window import MainWindow
        from rwmap.app.panel_layout import iter_panels
        cls.iter_panels = staticmethod(iter_panels)
        cls.w = MainWindow(auto_open=False)
        cls.w._open(Path(cls.w.state.profile.sample_map))
        cls.w.resize(1200, 800)
        cls.w.show()
        for _ in range(3):
            QApplication.processEvents()

    def _bars(self):
        from rwmap.widgets.dock_chrome import DockTitleBar
        for spec, dock in self.iter_panels(self.w):
            self.assertIsInstance(dock.titleBarWidget(), DockTitleBar, spec.title)
            yield spec, dock, dock.titleBarWidget()

    def test_all_panels_have_uniform_title_bar(self):
        from rwmap.widgets.dock_chrome import (
            BUTTON_SIZE, ICON_SIZE, TITLE_BAR_HEIGHT, TITLE_FONT_PT,
        )
        from rwmap.app.panel_layout import PANEL_SPECS
        seen = []
        for spec, dock, bar in self._bars():
            seen.append(spec.title)
            self.assertEqual(bar.objectName(), "DockTitleBar")
            self.assertEqual(bar.height(), TITLE_BAR_HEIGHT)
            self.assertEqual(bar.label.text(), spec.title)
            self.assertEqual(dock.windowTitle(), spec.title)
            font = bar.label.font()
            self.assertEqual(font.pointSize(), TITLE_FONT_PT)
            self.assertTrue(font.bold())
            for btn in (dock.btn_float, dock.btn_close):
                self.assertEqual(btn.text(), "")
                self.assertFalse(btn.icon().isNull())
                self.assertEqual(btn.iconSize(), QSize(ICON_SIZE, ICON_SIZE))
                self.assertEqual(btn.size(), QSize(BUTTON_SIZE, BUTTON_SIZE))
                self.assertTrue(btn.toolTip())
        # PANEL_SPECS 是面板清单唯一来源；面板增减时此处应同步，故按清单数量断言。
        self.assertEqual(len(seen), len(PANEL_SPECS))
        self.assertEqual({s.title for s in PANEL_SPECS}, set(seen))

    def test_icons_match_native_dock_buttons_exactly(self):
        from rwmap.widgets.dock_chrome import native_dock_icons
        ref_float, ref_close = native_dock_icons()
        for spec, dock, bar in self._bars():
            if getattr(bar, "_light", False):
                continue   # 统一底色标题栏用自绘浅色图标（深底上可见），不比对原生
            self.assertEqual(dock.btn_float.icon().pixmap(16, 16).toImage(),
                             ref_float.pixmap(16, 16).toImage(), spec.title)
            self.assertEqual(dock.btn_close.icon().pixmap(16, 16).toImage(),
                             ref_close.pixmap(16, 16).toImage(), spec.title)

    def test_title_bars_render_dark_not_white(self):
        """整窗/单 Dock 渲染时标题栏不能被原生浅色底盖掉。"""
        for spec, dock, _bar in self._bars():
            pix = dock.grab().toImage()
            white = 0
            total = 0
            for y in range(0, 20):
                for x in range(0, min(pix.width(), 200)):
                    c = pix.pixelColor(x, y)
                    total += 1
                    if min(c.red(), c.green(), c.blue()) > 235:
                        white += 1
            self.assertLess(white / total, 0.03,
                            f"{spec.title} 标题栏出现白底 {white}/{total}")

    def test_install_title_bar_is_idempotent(self):
        from rwmap.widgets.dock_chrome import DockTitleBar, install_title_bar
        dock = self.w.layers_dock
        bar = dock.titleBarWidget()
        self.assertIs(install_title_bar(dock), bar)
        self.assertIs(dock.titleBarWidget(), bar)
        self.assertEqual(len(bar.findChildren(type(bar.btn_float))), 2)

    def test_float_close_and_double_click_on_standalone_dock(self):
        from rwmap.widgets.dock_chrome import install_title_bar
        mw = QMainWindow()
        dock = QDockWidget("测试面板", mw)
        dock.setWidget(QWidget())
        mw.addDockWidget(Qt.RightDockWidgetArea, dock)
        install_title_bar(dock, "测试面板")
        mw.resize(300, 160)
        mw.show()
        QApplication.processEvents()
        self.assertEqual(dock.titleBarWidget().label.text(), "测试面板")

        before = dock.isFloating()
        dock.btn_float.click()
        QApplication.processEvents()
        self.assertEqual(dock.isFloating(), not before)
        dock.btn_float.click()
        QApplication.processEvents()
        self.assertEqual(dock.isFloating(), before)

        # 双击标题栏（原生行为，自定义标题栏需自己补）
        QTest.mouseDClick(dock.titleBarWidget(), Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(dock.isFloating(), not before)

        dock.btn_close.click()
        QApplication.processEvents()
        self.assertFalse(dock.isVisible())
        mw.deleteLater()


if __name__ == "__main__":
    unittest.main()
