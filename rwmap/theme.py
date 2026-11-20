"""全局双色主题（暗色模式）。

用户需求：暗色模式只保留两种颜色 —— 一个黑色（近黑 #1e1e1e）和一个偏黑的
灰色（#2a2e34），所有界面区分都靠这两个色 + 深浅/透明度来做。
2026-11-18 面板边界线定为 1px，使用两色里较浅的暗色 ASH，不新增第三种色。

为什么文字不在这两个色里
------------------------
``#2a2e34`` 的文字压在 ``#1e1e1e`` 的背景上，对比度只有 1.2:1
（WCAG 正文要求 4.5:1），实际几乎读不出来。硬把文字也塞进这两个色，
界面会变成"能看见字形、读不出内容"。

所以这里把**界面区分色**严格收敛成两个（背景/面板/边框/按钮/选中/悬停…），
文字单独用一个可读的浅色，并集中在这里定义，方便一处调整。

**功能彩色**（触发器连线按类型着色、对象标注框、选择虚线框、小地图视口框、
图标本身的彩色）属于"语义信息"，不在双色收敛范围内，保持原样。
Windows 11 默认强调蓝 ``#0078D4`` 也归入功能彩色，只用于透明度滑杆拖动球、
单选按钮选中球这类"当前操作点"，不参与面板 / 按钮底色的双色收敛。

常用角色
--------
``INK``   近黑，作为最底层背景（画布外底、面板底、工具栏底）
``ASH``   偏黑灰，作为"抬起一层"的表面（输入框、选中项、按钮、边框）
``WIN11_BLUE`` Win11 默认强调蓝，功能圆球（滑杆拖动球 / 单选选中球）
``TEXT``  主文字色（浅色，保证可读）
``TEXT_DIM`` 次要文字色
"""
from __future__ import annotations

# ---- 双色基底：整套暗色主题只用这两个颜色做界面区分 ----
INK = "#1e1e1e"      # 近黑：最底层（画布外底 / 面板底 / 工具栏底）
ASH = "#2a2e34"      # 偏黑灰：抬起一层（输入框 / 按钮 / 选中 / 边框 / 悬停）
WIN11_BLUE = "#0078d4"  # Windows 11 默认强调蓝：功能圆球（滑杆拖动球 / 单选选中球）

# 面板边界：所有 QDockWidget 统一 1 像素 ASH 描边（两色里较浅的暗色）。
# 放在主题常量里，新增面板不需要自己写样式；个别面板要单独覆盖 QDockWidget
# 规则时，必须沿用这组常量，不能再把边框改掉。
PANEL_BORDER_PX = 1
PANEL_BORDER_COLOR = ASH
PANEL_BORDER_QSS = f"border: {PANEL_BORDER_PX}px solid {PANEL_BORDER_COLOR};"

# ---- 文字（不参与双色收敛，单独保证可读） ----
TEXT = "#d8d8d8"      # 主文字
TEXT_DIM = "#8a8a8a"  # 次要文字 / 提示
TEXT_ON_ACCENT = "#1e1e1e"   # 压在 ASH 亮块上的文字

# ---- 派生的 QColor 工厂 ----
from PySide6.QtGui import QColor  # noqa: E402  （放在常量之后便于阅读）


def qc(value: str, alpha: int | None = None) -> QColor:
    """按主题色值取 QColor；给 alpha 时产出半透明版本。

    半透明是这套双色主题里唯一的"第三维度"：同样是 ASH，
    ``ASH`` 与 ``ASH@40`` 在黑底上呈现不同的灰阶，用来区分
    悬停 / 按下 / 半透明填充这类不需要再引入新色相的状态。
    """
    c = QColor(value)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


# ---- 应用级样式表：接管 Qt 原生部件（菜单栏 / 分隔条 / 滚动条…） ----
APP_QSS = f"""
QMainWindow, QDialog {{ background: {INK}; }}
QMenuBar {{
    background: {INK}; color: {TEXT}; border-bottom: 1px solid {ASH};
}}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {ASH}; }}
QMenuBar::item:pressed {{ background: {ASH}; }}

QMenu {{
    background: {INK}; color: {TEXT};
    border: 1px solid {ASH}; padding: 3px;
}}
QMenu::item {{ padding: 5px 26px 5px 22px; background: transparent; }}
QMenu::item:selected {{ background: {ASH}; }}
QMenu::item:disabled {{ color: {TEXT_DIM}; }}
QMenu::separator {{ height: 1px; background: {ASH}; margin: 4px 8px; }}

QStatusBar {{ background: {INK}; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}

QSplitter::handle {{ background: {INK}; }}
QSplitter::handle:hover {{ background: {ASH}; }}

QToolBar {{ background: {INK}; border: none; spacing: 2px; }}
QToolBar::separator {{ background: {ASH}; width: 1px; margin: 4px 3px; }}

QToolTip {{
    background: {ASH}; color: {TEXT};
    border: 1px solid {INK}; padding: 3px 5px;
}}

QScrollBar:vertical {{ background: {INK}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {ASH}; min-height: 20px; }}
QScrollBar:horizontal {{ background: {INK}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {ASH}; min-width: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: {INK}; }}

QProgressBar {{
    background: {INK}; color: {TEXT};
    border: 1px solid {ASH}; border-radius: 3px; text-align: center;
}}
QProgressBar::chunk {{ background: {ASH}; }}

QDockWidget {{ background: {INK}; {PANEL_BORDER_QSS} }}
QDockWidget::title {{ background: {ASH}; color: {TEXT}; padding: 3px; }}
"""


def apply_app_theme(app) -> None:
    """把应用级样式表装到 QApplication 上。

    菜单栏、状态栏、分隔条这些**原生部件**不吃各面板自己设的样式表，
    必须在这里统一接管，否则会漏出系统默认的浅色 / 白色。
    """
    app.setStyleSheet(APP_QSS)


__all__ = [
    "INK", "ASH", "WIN11_BLUE",
    "PANEL_BORDER_PX", "PANEL_BORDER_COLOR", "PANEL_BORDER_QSS",
    "TEXT", "TEXT_DIM", "TEXT_ON_ACCENT",
    "qc", "APP_QSS", "apply_app_theme",
]
