"""第二十三轮回归：Dock 面板共用「统一格式」标题栏（面板清单由 PANEL_SPECS 驱动，含迷你地图）。

覆盖：清单驱动的 5 个面板都装了 DockTitleBar；标题字号/粗细/文字一致；
标题栏高度与背景一致；浮动/关闭按钮的图标、尺寸、提示一致且与 Qt 原生
按钮逐像素相同；整窗渲染下标题栏不出白底；双击浮动、重复安装幂等。
"""
from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtCore import QPoint, QSize, Qt  # noqa: E402
from PySide6.QtGui import QFontDatabase, QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDockWidget, QMainWindow, QTabBar, QWidget,
)


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

    def test_all_panels_have_unified_one_pixel_border(self):
        """所有面板外框统一 1px ASH 浅暗线；局部覆盖样式的面板也不能漏掉。

        统一规则写在 theme.APP_QSS 的 QDockWidget 选择器里，新增面板只要还是
        QDockWidget 就自动继承。少数面板单独写了 QDockWidget 规则，必须沿用
        PANEL_BORDER_QSS，不能把边框改回 0 或其它宽度。
        """
        from rwmap.theme import (
            APP_QSS, PANEL_BORDER_COLOR, PANEL_BORDER_PX,
        )
        from rwmap.widgets.dock_chrome import DockBorder
        expected = f"border: {PANEL_BORDER_PX}px solid {PANEL_BORDER_COLOR};"
        app_rule = re.search(r"QDockWidget\s*\{([^}]*)\}", APP_QSS)
        self.assertIsNotNone(app_rule)
        self.assertIn(expected, app_rule.group(1))
        for spec, dock in self.iter_panels(self.w):
            local_rule = re.search(r"QDockWidget\s*\{([^}]*)\}", dock.styleSheet())
            if local_rule is not None:
                self.assertIn(expected, local_rule.group(1), spec.title)
            # QSS 规则只是声明；停靠态真正可见的是统一的透明描边层。
            self.assertIsInstance(dock.findChild(DockBorder, "DockBorder"),
                                  DockBorder, spec.title)
            if dock.isHidden():
                continue
            img = dock.grab().toImage()
            self.assertGreaterEqual(img.width(), PANEL_BORDER_PX + 1, spec.title)
            # 取内容区的一行：标题栏本身是 ASH，取那里无法区分边框是否存在。
            y = min(60, img.height() - 1)
            for x in range(PANEL_BORDER_PX):
                self.assertEqual(img.pixelColor(x, y).name().lower(),
                                 PANEL_BORDER_COLOR.lower(),
                                 f"{spec.title} 左边第 {x + 1} 像素不是暗线")

    def test_three_overlapped_panels_drag_out_at_cursor(self):
        """多个面板可重合；把标签拖出范围后浮出，左上角对准鼠标。"""
        from rwmap.app.main_window import MainWindow
        from rwmap.widgets.dock_overlap import overlap_panels

        mw = MainWindow(auto_open=False)
        mw.resize(1200, 800)
        mw.show()
        for _ in range(3):
            QApplication.processEvents()

        docks = [mw.layers_dock, mw.properties_dock, mw.minimap_dock]
        self.assertEqual(overlap_panels(mw, docks), 3)
        for _ in range(3):
            QApplication.processEvents()

        bars = [bar for bar in mw.findChildren(QTabBar)
                if bar.count() == 3
                and {bar.tabText(i) for i in range(bar.count())}
                == {dock.windowTitle() for dock in docks}]
        self.assertEqual(len(bars), 1, "三块面板应重合到同一个标签栏")
        bar = bars[0]

        target = mw.properties_dock
        index = next(i for i in range(bar.count())
                     if bar.tabText(i) == target.windowTitle())
        local_press = bar.tabRect(index).center()
        start = bar.mapToGlobal(local_press)
        outside = start + QPoint(0, 80)     # 标签栏高约 24px，已明确拖出范围
        local_outside = bar.mapFromGlobal(outside)

        QTest.mousePress(bar, Qt.LeftButton, pos=local_press)
        QTest.mouseMove(bar, pos=local_outside)
        QApplication.processEvents()

        self.assertTrue(target.isFloating(), "拖出标签后应解除重合")
        self.assertFalse(mw.layers_dock.isFloating())
        self.assertFalse(mw.minimap_dock.isFloating())
        top_left = target.geometry().topLeft()
        self.assertLess((top_left - outside).manhattanLength(), 3,
                        f"浮出面板左上角未对准鼠标：{top_left} vs {outside}")

        # 松开左键前继续移动鼠标，面板应继续跟着走（不用松开再拖一次）
        later = outside + QPoint(120, 60)
        QTest.mouseMove(mw, pos=mw.mapFromGlobal(later))
        QApplication.processEvents()
        moved_top_left = target.geometry().topLeft()
        self.assertLess((moved_top_left - later).manhattanLength(), 3,
                        f"浮出后面板未继续跟随鼠标：{moved_top_left} vs {later}")
        QTest.mouseRelease(mw, Qt.LeftButton, pos=mw.mapFromGlobal(later))
        QApplication.processEvents()

        # 松开后再移动鼠标，面板不应再跟随
        after_release = later + QPoint(60, 0)
        QTest.mouseMove(mw, pos=mw.mapFromGlobal(after_release))
        QApplication.processEvents()
        self.assertEqual(target.geometry().topLeft(), moved_top_left,
                         "松开鼠标后面板不应继续跟随")

        # 剩下两块仍重合，证明逻辑不依赖「恰好两个面板」。
        remainders = [bar for bar in mw.findChildren(QTabBar)
                      if bar.count() == 2
                      and {bar.tabText(i) for i in range(bar.count())}
                      == {mw.layers_dock.windowTitle(),
                          mw.minimap_dock.windowTitle()}]
        self.assertEqual(len(remainders), 1)
        mw.deleteLater()

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
