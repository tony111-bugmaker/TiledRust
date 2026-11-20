"""回归：软件所有数字输入框统一「无上下箭头 + 滚轮不改数值」。

- PlainSpinBox / PlainDoubleSpinBox 封装本身行为；
- 真实对话框（新建地图 / 新建图集 / 调整尺寸）里的实例都是 PlainSpinBox；
- 静态守护：源码里不再出现 QSpinBox(...) / QDoubleSpinBox(...) 直接实例化，
  防止以后新增视图时写回原生控件（check_spin_boxes.py 工具同步该约定）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtCore import Qt, QPoint  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication,  # noqa: E402
                               QSpinBox)

from rwmap.widgets.plain_spinbox import (PlainDoubleSpinBox,  # noqa: E402
                                          PlainSpinBox)

_app = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[2]


class PlainSpinBoxTest(unittest.TestCase):
    @staticmethod
    def _wheel_up_event():
        return QWheelEvent(QPoint(5, 5), QPoint(5, 5), QPoint(0, 0),
                           QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                           Qt.NoScrollPhase, False)

    def test_no_buttons_and_wheel_ignored(self):
        for cls in (PlainSpinBox, PlainDoubleSpinBox):
            box = cls()
            self.assertEqual(box.buttonSymbols(), QAbstractSpinBox.NoButtons,
                             f"{cls.__name__} 不应有上下箭头")
            box.setValue(50)
            QApplication.sendEvent(box, self._wheel_up_event())
            self.assertEqual(box.value(), 50,
                             f"{cls.__name__} 滚轮不应改变数值")

    def test_wheel_event_really_delivered(self):
        """对照：同一个滚轮事件会让原生 QSpinBox 变值，证明上例的事件确实送达。"""
        native = QSpinBox()
        native.setRange(0, 100)
        native.setValue(50)
        QApplication.sendEvent(native, self._wheel_up_event())
        self.assertNotEqual(native.value(), 50,
                            "原生 QSpinBox 应响应滚轮——否则对照测试失效")

    def test_is_qspinbox_subclass(self):
        # 保证上层 setRange/setValue/valueChanged/suffix 等接口完全兼容
        self.assertTrue(issubclass(PlainSpinBox, QSpinBox))


class DialogInstanceTest(unittest.TestCase):
    def test_new_map_dialog_spins(self):
        from rwmap.widgets.new_map_dialog import NewMapDialog
        from rwmap.rw.version import active_profile
        dlg = NewMapDialog(active_profile())
        for name in ("sp_width", "sp_height"):
            self.assertIsInstance(getattr(dlg, name), PlainSpinBox, name)

    def test_new_tileset_dialog_spins(self):
        from rwmap.widgets.new_tileset_dialog import NewTilesetDialog
        dlg = NewTilesetDialog()
        for name in ("tw_spin", "th_spin", "margin_spin", "spacing_spin"):
            spin = getattr(dlg, name)
            self.assertIsInstance(spin, PlainSpinBox, name)
            self.assertEqual(spin.buttonSymbols(), QAbstractSpinBox.NoButtons)


class StaticGuardTest(unittest.TestCase):
    def test_no_native_spinbox_instantiation(self):
        """源码里不应再有 QSpinBox(...) / QDoubleSpinBox(...) 直接实例化。"""
        bad = []
        src = ROOT / "rwmap"
        for path in src.rglob("*.py"):
            # 只守护应用源码；测试/probe 里为对照而故意实例化原生 SpinBox 是合法的
            rel = path.relative_to(ROOT).parts
            if "__pycache__" in path.parts or (
                    len(rel) > 1 and rel[1] in ("tests", "probe")):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"),
                                 filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else "")
                # plain_spinbox.py 里继承 QSpinBox 的类定义不算实例化
                if name in ("QSpinBox", "QDoubleSpinBox"):
                    bad.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
        self.assertEqual(bad, [],
                         "这些地方又用了原生 SpinBox，请改用 PlainSpinBox："
                         + ", ".join(bad))


if __name__ == "__main__":
    unittest.main()
