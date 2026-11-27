"""地图变换 4 按钮迁移到对象工具栏的回归测试。

覆盖：
1. 水平/垂直翻转、顺/逆时针旋转 4 个按钮挂在对象工具栏最右端，带图标。
2. 4 个按钮前面有一条竖线分隔符（工具栏 separator）。
3. 这 4 项不再出现在「地图」菜单里（按用户要求迁移，不是复制）。
4. 点击按钮确实调用 _transform_content(op)，且 op 与按钮一一对应。
5. 按钮排在「瓦片画笔」文字之前；无目标时禁用，变换目标见 test_content_transform。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402

_app = QApplication.instance() or QApplication([])

_GAME = Path("<游戏目录1.15>")


class TransformToolbarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = MainWindow(auto_open=False)

    def map_menu(self):
        return next(m.menu() for m in self.w.menuBar().actions()
                    if m.text() == "地图")

    # ---- 1. 按钮就位 + 图标 ----
    def test_four_actions_on_object_toolbar(self):
        bar = self.w.object_toolbar
        acts = bar._transform_actions
        self.assertEqual(len(acts), 4)
        for action in acts:
            self.assertIn(action, bar.actions())
            self.assertFalse(action.icon().isNull())
            btn = bar.widgetForAction(action)
            self.assertIsNotNone(btn)
            self.assertTrue(btn.objectName().startswith("Transform_"))

    def test_action_texts_and_order(self):
        bar = self.w.object_toolbar
        self.assertEqual(
            [a.text() for a in bar._transform_actions],
            ["水平翻转", "垂直翻转", "顺时针旋转 90°", "逆时针旋转 90°"])

    # ---- 2. 前面有竖线分隔符 ----
    def test_separator_before_first_transform_button(self):
        bar = self.w.object_toolbar
        acts = bar.actions()
        idx = acts.index(bar._transform_actions[0])
        self.assertGreater(idx, 0)
        self.assertTrue(acts[idx - 1].isSeparator())

    def test_transform_buttons_before_tile_brush_label(self):
        """4 个变换按钮排在「瓦片画笔」文字之前（图标在左、文字在右）。"""
        bar = self.w.object_toolbar
        acts = bar.actions()
        first = acts.index(bar._transform_actions[0])
        last = acts.index(bar._transform_actions[-1])
        hint_idx = acts.index(bar._hint_action)
        self.assertLess(last, hint_idx, "变换按钮应在「瓦片画笔」文字之前")
        self.assertLess(first, hint_idx)
        # 按钮与文字之间只有一条分隔符，hint 是最后一项
        self.assertTrue(acts[last + 1].isSeparator())
        # 变换按钮之后又加了「标尺」按钮和分段输入框，中间不再是空的
        self.assertGreaterEqual(hint_idx, last + 2)
        non_sep = [a for a in acts if not a.isSeparator()]
        self.assertEqual(non_sep[-1], bar._hint_action)

    # ---- 3. 菜单里已移除 ----
    def test_not_in_map_menu(self):
        texts = [a.text() for a in self.map_menu().actions()]
        for name in ("水平翻转", "垂直翻转", "顺时针旋转 90°",
                     "逆时针旋转 90°"):
            self.assertNotIn(name, texts)

    def test_actions_not_added_to_map_menu(self):
        for action in self.w.object_toolbar._transform_actions:
            self.assertNotIn(action, self.map_menu().actions())

    # ---- 4. 点击触发正确的 op ----
    def test_each_button_triggers_its_op(self):
        ops = ("flip_h", "flip_v", "rot_cw", "rot_ccw")
        w = self.w
        for action, op in zip(w.object_toolbar._transform_actions, ops):
            with mock.patch.object(w, "_transform_content") as m:
                action.trigger()
                m.assert_called_once_with(op)

    def test_button_object_names_match_ops(self):
        bar = self.w.object_toolbar
        ops = ("flip_h", "flip_v", "rot_cw", "rot_ccw")
        names = [bar.widgetForAction(a).objectName()
                 for a in bar._transform_actions]
        self.assertEqual(names, [f"Transform_{op}" for op in ops])

    # ---- 5. 统一风格图标（亮蓝描边 + 淡蓝半透明填充 + 绿色箭头） ----
    def test_icons_use_app_unified_drawing_style(self):
        """图标按 Tiled 轮廓重绘为软件统一风格，不再是抠图位图。"""
        from rwmap.widgets import transform_icons
        from rwmap.widgets.object_toolbar import make_object_icon, _ICON_BLUE

        # 已经改为 QPainter 绘制，不应再有内嵌 base64 位图数据。
        self.assertFalse(hasattr(transform_icons, "_DATA"))

        def pixels(kind):
            img = make_object_icon(kind).pixmap(28, 28).toImage()
            out = []
            for y in range(28):
                for x in range(28):
                    c = img.pixelColor(x, y)
                    if c.alpha() > 20:
                        out.append((c.red(), c.green(), c.blue(), c.alpha()))
            return out

        def is_blue_stroke(p):
            r, g, b, a = p
            return a > 180 and b > 200 and b >= g + 50 and g >= r + 50

        def is_translucent_fill(p):
            r, g, b, a = p
            return 20 < a < 160 and b > 150 and b >= g >= r

        def is_green(p):
            r, g, b, _a = p
            return g > 120 and g > r + 25 and g > b + 40

        def is_dark_gray_outline(p):
            r, g, b, a = p
            return a > 180 and abs(r - g) < 20 and abs(g - b) < 20 and r < 140

        for kind in ("flip_h", "flip_v"):
            px = pixels(kind)
            self.assertGreater(sum(map(is_blue_stroke, px)), 40,
                               f"{kind} 缺少统一亮蓝描边")
            self.assertGreater(sum(map(is_translucent_fill, px)), 60,
                               f"{kind} 缺少淡蓝半透明填充")
            self.assertEqual(sum(map(is_green, px)), 0, kind)
            self.assertEqual(sum(map(is_dark_gray_outline, px)), 0,
                             f"{kind} 还带着位图的深灰描边")
        for kind in ("rot_cw", "rot_ccw"):
            img = make_object_icon(kind).pixmap(28, 28).toImage()
            px = pixels(kind)
            self.assertGreater(sum(map(is_blue_stroke, px)), 30, kind)
            self.assertGreater(sum(map(is_green, px)), 15,
                               f"{kind} 缺少绿色旋转箭头")
            # 关键回归：必须是「两个全等三角」——左侧竖放 + 右侧横放，
            # 逆时针是顺时针的水平镜像。左右两半都得有蓝描边。
            def strokes(x0, x1):
                n = 0
                for x in range(x0, x1):
                    for y in range(28):
                        c = img.pixelColor(x, y)
                        if is_blue_stroke((c.red(), c.green(),
                                           c.blue(), c.alpha())):
                            n += 1
                return n
            self.assertGreater(strokes(2, 13), 15,
                               f"{kind} 缺少左侧竖放的三角形")
            self.assertGreater(strokes(15, 28), 15,
                               f"{kind} 缺少右侧横放的三角形")

        # 描边色与工具栏其余工具图标保持一致。
        self.assertEqual(_ICON_BLUE.name(), "#50aaf5")


if __name__ == "__main__":
    unittest.main()
