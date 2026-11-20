"""离屏 GUI 冒烟测试：最小闭环（打开→改一格→撤销/重做→另存→渲染）。

注意：QT_QPA_PLATFORM=offscreen 只作用于本进程；thumbnail.py 启动
tmxrasterizer 时会清理继承的 Qt 环境变量，子进程仍用 windows 插件。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from pathlib import Path  # noqa: E402


class GuiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from rwmap.app.main_window import MainWindow
        cls.window = MainWindow(auto_open=False)

    def test_minimum_closed_loop(self):
        from PySide6.QtWidgets import QApplication
        from rwmap.io.tmx_reader import read_document
        from rwmap.rw.exporter import export_map

        w = self.window
        w._open(Path(w.state.profile.sample_map))
        doc = w.state.doc
        self.assertIsNotNone(doc)
        self.assertGreater(len(doc.layers), 4)
        self.assertTrue(w.state.can_edit())

        layer = w.state.current_layer
        old = layer.get(10, 10)
        w._on_gid(1)
        w._on_press(10, 10)
        w._on_move(11, 10)
        w.view.cellReleased.emit(11, 10)
        self.assertEqual(layer.get(10, 10), 1)
        self.assertEqual(w.state.undo_stack.count(), 1)

        w.state.undo_stack.undo()
        self.assertEqual(layer.get(10, 10), old)
        w.state.undo_stack.redo()
        self.assertEqual(layer.get(10, 10), 1)

        w.show()
        QApplication.processEvents()
        w.view.repaint()
        QApplication.processEvents()

        result = export_map(
            doc, Path("logs/selftest"), w.state.profile,
            with_thumbnail=False, target_name="冒烟测试.tmx",
        )
        self.assertTrue(Path(result["tmx"]).is_file())
        again = read_document(Path(result["tmx"]), w.state.profile)
        ground = next(l for l in again.layers if l.name == "Ground")
        self.assertEqual(ground.get(10, 10), 1)

    def test_no_context_menu_on_view_and_viewport(self):
        """右键只用于抓取/拾取：view 与其 viewport 都不能弹空的原生右键菜单。"""
        from PySide6.QtCore import Qt
        w = self.window
        self.assertEqual(w.view.contextMenuPolicy(), Qt.NoContextMenu)
        self.assertEqual(w.view.viewport().contextMenuPolicy(),
                         Qt.NoContextMenu)

    def test_all_empty_viewport_menus_suppressed_real_menus_kept(self):
        """统一收口：所有无真实右键菜单的滚动控件 viewport 都设为
        NoContextMenu（不再弹空白小窗口）；有真实菜单的图层列表 / 图集面板
        策略保持不变。"""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QAbstractScrollArea, QWidget
        w = self.window

        # 真实菜单控件：策略不能被破坏
        self.assertEqual(w.layers_dock.list.contextMenuPolicy(),
                         Qt.CustomContextMenu)

        # 遍历所有滚动控件：Default 且承载内容没有自定义菜单 → 必须已收口
        leaked = []
        for area in w.findChildren(QAbstractScrollArea):
            vp = area.viewport()
            if vp is None or area.contextMenuPolicy() != Qt.DefaultContextMenu:
                continue
            content = area.widget() if hasattr(area, "widget") else None
            content_has_menu = content is not None and (
                type(content).contextMenuEvent is not QWidget.contextMenuEvent)
            if not content_has_menu and vp.contextMenuPolicy() == Qt.DefaultContextMenu:
                leaked.append(f"{area.metaObject().className()}/"
                              f"{area.objectName() or '-'}")
        self.assertEqual(leaked, [],
                         "这些 viewport 仍会弹空菜单，未被统一收口：" + ", ".join(leaked))


if __name__ == "__main__":
    unittest.main()
