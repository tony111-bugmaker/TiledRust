"""触发器属性面板：网页版 trigger_graph.html 右侧功能的纯 PySide6 移植。

界面术语与「属性模板」都来自网页版（并与 docs/铁锈地图_属性与指令速查.md 对齐）：
* 类型下拉 / 属性名 / 属性值释义全部中文，但**写回文档的键与值仍是英文**；
* 切换类型时属性严格换成新类型的模板（不适配的一律移除）；
* 编辑走 ObjectCommand + QUndoStack，redo / undo 后由 Dock 统一刷新；
  撤销统一走 Ctrl+Z / 编辑菜单（主窗口动作转调当前文档的撤销栈），面板不再放撤销按钮。

本模块不 import 任何 WebEngine / HTML 相关组件。
"""
from __future__ import annotations

import re

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QWheelEvent
from rwmap.document.map_object import object_uid
from rwmap.document.properties import HIDDEN_EDITOR_PROPERTIES
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QStyleOptionViewItem,
    QStyledItemDelegate, QVBoxLayout, QWidget,
)

from ..rw import unit_names
from ..theme import ASH, INK, TEXT, TEXT_DIM

# ---------------------------------------------------------------- 主题
# 界面区分色收敛为双色：INK（近黑底层）+ ASH（偏黑灰抬起层）；
# 文字单独用可读的浅色。端口/连线等**语义色**不在双色范围内，保持原样。
THEME = {
    "bg": INK,               # 画布底
    "dot": ASH,              # 点阵网格
    "dot_gap": 24,           # 点阵间距
    "panel": INK,            # 右侧面板
    "top": ASH,              # 顶栏 / 状态栏
    "node": ASH,             # 节点主体
    "node_border": INK,
    "input": INK,
    "text": TEXT,
    "muted": TEXT_DIM,
    "accent": ASH,           # 聚焦边框 / 勾选态：不再是蓝色，统一用 ASH
    "vhint": TEXT_DIM,
    "port_in": "#8a8a8a",    # 语义色：端口类型，保持原样
    "port_out": "#e09a3a",
}

# 网页版 TYPE_COLORS（标题条）
TYPE_COLORS = {
    "unitDetect": "#2f6ea8", "unitAdd": "#2f8f52", "unitRemove": "#a04040",
    # 三种「造兵」类型统一用生成单位的绿色（用户要求），不另造颜色。
    "unitAddRandom": "#2f8f52", "mapUnit": "#2f8f52",
    "changeCredits": "#9a8a30", "move": "#6a4fa0", "point": "#3a7a7a",
    "basic": "#555555", "camera_pan": "#4a6a8a", "rotate": "#6a4a6a",
    "moveCamera": "#3a6a6a", "objective": "#8a6a2a", "set_team": "#5a4a8a",
    "ai_allow_full_use": "#4a5a4a", "team_info": "#5a5a3a",
    "map_info": "#3a4a5a", "mapText": "#3a3a3a", "info": "#444444",
}
DEFAULT_TYPE_COLOR = "#555555"

EDGE_COLORS = {
    "alsoActivate": "#57c878",
    "whenActivatedIds": "#5a8ad0",
    "whenActivatedlds": "#e0a44a",
    "activatedBy": "#6f9fd0",
    "allToActivate": "#6f9fd0",
    "deactivatedBy": "#d05a5a",
    "alsoDeactivate": "#57c8b0",
    "whenDeactivatedIds": "#d08a5a",
    "requiredFor": "#9a6fd0",
    "next": "#c8c84a",
    "nextUnit": "#b0c84a",
    "parentTrigger": "#6fd0c0",
}

# 右侧「输入 / 输出连线」行末尾删除叉：统一用醒目红色，不跟随线型颜色
# （线型颜色留给行首的圆点，避免深色 / 蓝色线上的叉看不清）。
EDGE_CROSS_COLOR = "#ff5555"

def draw_symmetric_cross(painter: QPainter, center: QPointF, radius: float,
                         color, width: float = 1.8):
    """画严格中心对称的「叉」：两条等长对角线，交点正好落在 ``center``。

    不用字体字符 ✕ / × —— 不同字体（尤其 U+2715）会把它渲染成一撇长、一捺短、
    交点偏移的歪叉。线宽设 cosmetic，缩放后仍是固定屏幕像素、两条线始终等长。
    只调用 painter 的 setPen / drawLine，方便用极简假 painter 做单元测试。
    """
    pen = QPen()
    pen.setColor(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setCosmetic(True)
    painter.setPen(pen)
    x, y, r = center.x(), center.y(), radius
    painter.drawLine(QPointF(x - r, y - r), QPointF(x + r, y + r))
    painter.drawLine(QPointF(x - r, y + r), QPointF(x + r, y - r))


class NoWheelComboBox(QComboBox):
    """滚轮不切换选项的下拉框：需求要求类型 / 属性下拉禁用滚轮切换。

    滚轮事件直接忽略（不接受）：值不会被滚轮改动，事件继续交给父级 QScrollArea
    滚动面板，和项目里 PlainSpinBox 的约定一致。
    """

    def wheelEvent(self, event: QWheelEvent):  # noqa: N802
        event.ignore()


class _TypeColorDelegate(QStyledItemDelegate):
    """按下拉项自带的 ForegroundRole 上色，画成对应类型的标题条色。

    QComboBox 的样式表 color / 应用 palette 会把 item 的 ForegroundRole
    覆盖成统一文字色（实测：下拉里全是 #d8d8d8）。这里在绘制前显式把
    option.palette 的文字色改成该项的 ForegroundRole，确保每个类型显示
    自己的颜色（生成=绿、检测=蓝、移除=红…）。
    """

    def paint(self, painter, option, index):
        color = index.data(Qt.ForegroundRole)
        if isinstance(color, QColor) and color.isValid():
            option = QStyleOptionViewItem(option)
            option.palette.setColor(option.palette.ColorRole.Text, color)
            option.palette.setColor(option.palette.ColorRole.HighlightedText, color)
        super().paint(painter, option, index)


class CrossButton(QPushButton):
    """属性行右侧的删除叉：按钮自身不写字，改用中心对称的自绘叉。

    底色 / 边框仍交给 QPushButton 原生样式绘制（super().paintEvent），
    只把「叉」这一层换成几何绘制，保证上下左右都对称、交点居中。
    """

    def __init__(self, parent=None, borderless: bool = False, color=None):
        super().__init__("", parent)
        self.setAccessibleName("删除")
        self._cross_color = QColor(color or "#ee8888")
        self._hover = False
        if borderless:
            # 连线列表里的叉：完全透明、零边框，只留一个中心对称的叉本身
            self.setStyleSheet(
                "QPushButton{background:transparent;border:0;padding:0;}"
                "QPushButton:hover{background:transparent;border:0;}"
                "QPushButton:pressed{background:transparent;border:0;}")

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)      # 文字为空，只画底色与边框（无边框时全透明）
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = min(self.width(), self.height()) * 0.22
        col = QColor(self._cross_color)
        if self._hover:
            col = col.lighter(150)
        draw_symmetric_cross(
            p, QPointF(self.width() / 2.0, self.height() / 2.0),
            r, col, 1.9)
        p.end()


# ---------------------------------------------------------------- 中文词典
# value = (中文名, 说明, 该类型适配的属性模板)
TYPE_DEFS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "unitAdd": ("生成单位", "刷兵 / 生成单位（固定单位列表）",
                ("spawnUnits", "team", "warmup", "repeatDelay")),
    "unitAddRandom": ("随机生成单位", "按权重随机刷兵；导出时展开为 spawnChance，"
                      "TMX 里仍写 unitAdd",
                ("randomUnits", "team", "warmup", "repeatDelay")),
    # 地图初始单位：TMX 里无 type，靠 unit + team 属性识别。内部用 mapUnit key，
    # 导出时映射回空 type；仅当空 type 且带 unit 属性时才识别为此类型。
    # 顺序紧跟「随机生成单位」，三种造兵类型排在一起。
    "mapUnit": ("地图初始单位", "开局就摆在地图上的单位；TMX 中无 type，靠 unit + team 属性识别",
                ("unit", "team")),
    "unitDetect": ("检测单位", "检测区域内单位数量，满足条件后触发",
                   ("unitType", "minUnits", "maxUnits", "team", "includeIncomplete")),
    "unitRemove": ("移除单位", "清空矩形内的单位（不支持指定兵种 / 数量）",
                   ("team", "activatedBy", "resetActivationAfter")),
    "changeCredits": ("修改资金", "给某队加钱（add）或直接设值（set）",
                      ("add", "set", "team")),
    "move": ("单位移动", "让某队单位移动到 point 锚点",
             ("target", "team", "delay")),
    "point": ("坐标锚点", "主要靠对象 name 与坐标，通常不需要属性", ()),
    "basic": ("通用事件", "全局播报 / 通用调度",
              ("globalMessage", "globalMessage_delayPerChar", "globalMessage_textColor")),
    "camera_pan": ("镜头平移", "开场镜头平移，顺序写在 index",
                   ("index", "delay")),
    "rotate": ("旋转单位", "起始单位朝向（dir，单位为度）",
               ("dir", "team")),
    "moveCamera": ("移动镜头", "把镜头移到该对象的坐标",
                   ("delay", "activatedBy")),
    "objective": ("任务目标", "目标文字 / 全局播报",
                  ("text", "globalMessage")),
    "set_team": ("设置队伍", "改变单位所属队伍",
                 ("team", "unitType")),
    "ai_allow_full_use": ("AI 全权", "允许 AI 完全使用（配合队伍 / 开关）",
                          ("team", "disabledAI")),
    "team_info": ("队伍配置", "队伍初始资金 / 结盟组 / AI 难度",
                  ("team", "credits", "allyGroup", "ai", "lockAiDifficulty")),
    "map_info": ("地图设置", "全局：模式 / 迷雾 / 开局简报 / 胜负条件",
                 ("type", "introText", "fog", "winCondition", "loseCondition")),
    "mapText": ("地图文字", "在地图上显示一段文字",
                ("text", "textColor", "textSize", "showOnMap")),
    "info": ("说明信息", "纯说明节点", ("text",)),
}

# 空 type 且带 unit 属性 => 地图初始单位；其余空 type（map_info / 矩形等）保持未分类
INITIAL_UNIT_TYPE = "mapUnit"


def effective_type(obj) -> str:
    """返回对象用于显示/属性模板的有效类型。

    地图初始单位在 TMX 里没有 type，靠 unit 属性区分；但 map_info、矩形等
    辅助对象同样没有 type，不能一概当初始单位。所以只有「空 type + 带 unit
    属性」才归为 mapUnit，其余空 type 原样返回（界面显示为未分类）。
    """
    t = getattr(obj, "type_", "") or ""
    if not t and "unit" in (getattr(obj, "properties", None)
                            and obj.properties.values or {}):
        return INITIAL_UNIT_TYPE
    return t

COMMON_PROPS = (
    "team", "warmup", "delay", "repeatDelay", "resetActivationAfter",
    "activatedBy", "deactivatedBy", "allToActivate", "whenActivatedIds",
    "globalMessage", "globalMessage_delayPerChar", "globalMessage_textColor",
)

LINK_PROPS = ("alsoActivate", "whenActivatedIds", "deactivatedBy")

# key -> (中文标签, 说明, 示例值)
PROP_DEFS: dict[str, tuple[str, str, str]] = {
    "id": ("触发器 ID", "被 alsoActivate / activatedBy 引用的标识", "3"),
    "team": ("作用队伍", "数字 / -1 / -2 / none", "1"),
    "spawnUnits": ("生成单位", "单位内部名，可写 *数量，多个用逗号分隔",
                   "heavyTank*10, builder*2"),
    "unit": ("初始单位", "单个单位的内部名（raw name），如 experimentalHoverTank",
             "experimentalHoverTank"),
    "randomUnits": ("随机生成单位", "逗号 / 顿号分隔的单位列表（可写中文），"
                    "导出时自动按等概率展开为 spawnUnits",
                    "heavyTank,mammothTank"),
    "unitType": ("单位类型", "要检测 / 筛选的单位内部名", "mammoth"),
    "minUnits": ("最少单位数", "区域内达到这个数量才触发", "2"),
    "maxUnits": ("最多单位数", "区域内数量上限", "5"),
    "includeIncomplete": ("计入未完工", "true / false，未建造完成的建筑也算", "true"),
    "onlyMainBuildings": ("仅主要建筑", "true / false", "false"),
    "onlyOnResourcePool": ("仅资源池建筑", "true / false，如 extractor", "false"),
    "onlyBuilders": ("仅建造单位", "true / false", "false"),
    "onlyBuildings": ("仅建筑", "true / false", "false"),
    "onlyAttack": ("仅攻击单位", "true / false", "false"),
    "add": ("资金增量", "整数，可为负数", "1000"),
    "set": ("设为资金", "整数，直接把资金设成这个值", "120000"),
    "credits": ("初始资金", "队伍开局资金", "120000"),
    "target": ("目标锚点名", "指向 point 对象的 name", "attack_point"),
    "warmup": ("预热时间", "数字 + s", "30s"),
    "delay": ("单次延迟", "数字 + s", "0.1s"),
    "repeatDelay": ("重复间隔", "数字 + s", "60s"),
    "resetActivationAfter": ("复位时间", "触发后多久可再次触发", "0.5s"),
    "activatedBy": ("由…激活", "逗号分隔的触发器 id", "A,Q,W"),
    "deactivatedBy": ("停用于", "这些 id 激活时禁用本触发", "C,E"),
    "allToActivate": ("需全部满足", "true / false", "false"),
    "whenActivatedIds": ("激活后通知", "id 列表", "2,3"),
    "alsoActivate": ("顺带激活", "由连线管理：拖节点右侧橙点连到目标", "2,3"),
    "globalMessage": ("全局播报", "左上角显示的文字", "敌军来袭！"),
    "globalMessage_delayPerChar": ("逐字速度", "0.1s / 0.2s / slow", "0.1s"),
    "globalMessage_textColor": ("播报颜色", "red / #00FFFF 等", "red"),
    "text": ("文本内容", "要显示的文字，\\n 换行", "坚守这里"),
    "textColor": ("文字颜色", "red / #RRGGBB", "#00FFFF"),
    "textSize": ("文字字号", "8 ~ 16", "14"),
    "showOnMap": ("地图可见", "true / false", "true"),
    "type": ("地图模式", "skirmish / mission / survival / challenge / endless", "mission"),
    "introText": ("开局简报", "\\n 换行", "摧毁敌方基地"),
    "fog": ("战争迷雾", "map / los / NONE", "los"),
    "winCondition": ("胜利条件", "none / commandCenter / mainBuildings / allUnitsAndBuildings",
                     "mainBuildings"),
    "loseCondition": ("失败条件", "同胜利条件，生存图常用", "allUnitsAndBuildings"),
    "shareFogWithAllies": ("盟友共享视野", "true / false", "true"),
    "survivalWaves": ("生存波次", "生存模式的波次文本", "wave1, wave2"),
    "allyGroup": ("结盟组", "同一组的队伍互为盟友", "0"),
    "ai": ("AI 类型", "如 survival", "survival"),
    "lockAiDifficulty": ("锁定 AI 难度", "数字，如 6", "6"),
    "disabledAI": ("禁用 AI", "true / false", "false"),
    "index": ("镜头顺序", "开场镜头平移的先后序号", "1"),
    "dir": ("旋转角度", "单位：度", "90"),
    "spawnX": ("生成偏移 X", "相对坐标", "0"),
    "spawnY": ("生成偏移 Y", "相对坐标", "0"),
}

# 常见属性值的中文释义（只显示，绝不改写实际值）
VALUE_HINTS = {
    "true": "是", "false": "否", "none": "无 / 不限", "NONE": "无", "all": "全部",
    "skirmish": "遭遇战", "mission": "战役任务", "survival": "生存模式",
    "challenge": "挑战模式", "endless": "无尽模式",
    "map": "探索后永久可见", "los": "视线迷雾",
    "commandCenter": "摧毁指挥中心", "mainBuildings": "摧毁主要建筑",
    "allBuildings": "摧毁全部建筑", "allUnitsAndBuildings": "消灭全部单位与建筑",
    "red": "红色", "green": "绿色", "blue": "蓝色", "yellow": "黄色",
    "white": "白色", "purple": "紫色", "black": "黑色", "slow": "慢速",
    "mammoth": "猛犸坦克", "tank": "坦克", "heavyTank": "重型坦克",
    "lightTank": "轻型坦克", "builder": "建造者",
    "extractor": "资源采集器", "flyingMech": "飞行机甲",
}


def type_label(type_name: str) -> str:
    return TYPE_DEFS.get(type_name, (type_name, "", ()))[0]


def type_hint(type_name: str) -> str:
    return TYPE_DEFS.get(type_name, ("", "未收录的类型，请手动添加属性", ()))[1]


def type_props(type_name: str) -> tuple[str, ...]:
    return TYPE_DEFS.get(type_name, ("", "", ()))[2]


def color_for(type_name: str) -> str:
    return TYPE_COLORS.get(type_name or "", DEFAULT_TYPE_COLOR)


def prop_label(key: str) -> str:
    return PROP_DEFS.get(key, (key, "", ""))[0]


def prop_desc(key: str) -> str:
    return PROP_DEFS.get(key, (key, "", ""))[1]


def prop_example(key: str) -> str:
    return PROP_DEFS.get(key, (key, "", ""))[2]


def summary_keys(type_name: str) -> tuple[str, ...]:
    """节点摘要按当前类型模板排序。"""
    keys = list(type_props(type_name))
    for k in COMMON_PROPS:
        if k not in keys:
            keys.append(k)
    return tuple(keys)


def _split_list(text: str) -> list[str]:
    """属性值列表切分：英文/中文逗号与顿号都算分隔符。

    界面用顿号展示单位（重型坦克、猛犸坦克），用户手输时也可能用中文逗号，
    切分不统一会导致中文释义整段查不到。
    """
    return [p for p in re.split(r"[,，、]", str(text or "")) if p.strip()]


def describe_value(key: str, value) -> str:
    """属性值的中文释义；数字 / 十六进制颜色不翻译。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+(\.\d+)?s", text):
        return text[:-1] + " 秒"
    # 单位类属性：把内部名翻译成中文（heavyTank -> 重型坦克）
    if key in ("unitType", "spawnUnits"):
        hints = []
        for part in _split_list(text):
            p = part.strip()
            if not p:
                continue
            name = re.split(r"[*(]", p)[0].strip()
            if name in unit_names.CN_TO_RAW:
                cn = name                      # 已经是中文，原样保留
            else:
                cn = unit_names.to_cn(name)    # raw -> 中文
                if cn == name:
                    continue                   # 查不到的英文名不硬翻
            if cn not in hints:
                hints.append(cn)
        return "、".join(hints)
    hints = []
    for part in _split_list(text):
        p = part.strip()
        if not p or re.fullmatch(r"-?\d+", p) or re.fullmatch(r"#?[0-9a-fA-F]{6}", p):
            continue
        clean = re.split(r"[*(]", p)[0].strip()
        hint = VALUE_HINTS.get(p) or VALUE_HINTS.get(clean)
        if hint and hint not in hints:
            hints.append(hint)
    return "、".join(hints)


# ---------------------------------------------------------------- 控件
def _clear_layout(layout):
    """立即把旧控件从父级摘掉再延迟销毁。

    只调用 deleteLater() 的话，旧控件在下一次事件循环前仍参与布局与绘制，
    重新渲染时会被拉伸成横条叠在新控件上（看起来像红色边框 / 重复标签）。
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            # 必须先 hide 再摘父级：对一个「当前可见」的控件调用 setParent(None)
            # 会让它瞬间变成顶层窗口，Windows 立刻给它配一套带最小化/最大化/
            # 关闭的系统标题栏（无标题 → 没有任何文字），一闪而过。
            # 先隐藏，摘下来时就是不可见的顶层窗，不会再闪。
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            _clear_layout(sub)


class TriggerInspector(QWidget):
    """网页版右侧属性面板的 Python 版（可编辑，写回文档并走撤销栈）。"""

    objectDeleteRequested = Signal(object)
    # 右侧「输入 / 输出连线」列表：点叉删线、右键改线型（把原始 GraphEdge 交回 Dock）
    edgeDeleteRequested = Signal(object)
    edgeMenuRequested = Signal(object, object)   # (GraphEdge, 全局坐标 QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TriggerInspector")
        self.doc = None
        self.layer = None
        self.obj = None
        self.node = None
        self.edges: list[tuple] = []  # (方向, 标签, 对端名, 颜色[, GraphEdge])
        self.undo_stack = None
        self.on_change = None
        self._loading = False

        self.setStyleSheet(f"""
            QWidget#TriggerInspector{{background:{THEME['panel']};}}
            QScrollArea{{background:{THEME['panel']};border:0;}}
            QLineEdit{{background:{THEME['input']};color:{THEME['text']};
                border:1px solid {ASH};border-radius:4px;padding:4px 6px;font-size:9pt;}}
            /* QComboBox 不设 color：让每项用自己的 ForegroundRole 显示类型色。
               若在这里写 color，会覆盖 item 前景色，下拉里全是同一文字色。 */
            QComboBox{{background:{THEME['input']};
                border:1px solid {ASH};border-radius:4px;padding:4px 6px;font-size:9pt;}}
            QLineEdit:focus,QComboBox:focus{{border:1px solid {TEXT};}}
            QComboBox::drop-down{{border:0;}}
            QScrollBar:vertical{{background:{THEME['panel']};width:8px;}}
            QScrollBar::handle:vertical{{background:{THEME['node']};border-radius:4px;}}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setMinimumWidth(0)
        self.content.setStyleSheet(f"background:{THEME['panel']};")
        self.vbox = QVBoxLayout(self.content)
        self.vbox.setContentsMargins(12, 12, 12, 12)
        self.vbox.setSpacing(5)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)
        self._show_empty()

    # ---------------- 外部 API ----------------
    def set_context(self, doc, layer, undo_stack=None, on_change=None):
        self.doc = doc
        self.layer = layer
        self.undo_stack = undo_stack
        self.on_change = on_change

    def current_object_id(self):
        return getattr(self.obj, "object_id", None)

    @property
    def is_loading(self) -> bool:
        return self._loading

    def clear(self):
        """清空当前节点显示（refresh 会短暂触发空选中）。"""
        self.obj = None
        self.node = None
        self.edges = []
        self._show_empty()

    def reset(self):
        """切换文档 / 删除对象时清空当前显示。"""
        self.clear()

    def show_node(self, node, edges=None, layer=None):
        """node 需带 .obj / .tid；edges 由 Dock 反推好；layer 为所属对象层。"""
        self.node = node
        self.obj = getattr(node, "obj", None)
        self.edges = list(edges or [])
        if layer is not None:
            self.layer = layer
        self._render()

    # ---------------- 渲染 ----------------
    def _show_empty(self):
        self._loading = True
        _clear_layout(self.vbox)
        lab = QLabel("选中一个节点查看属性")
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet(f"color:{THEME['muted']};font-style:italic;font-size:12px;padding:20px 0;")
        self.vbox.addWidget(lab)
        self.vbox.addStretch(1)
        self._loading = False

    @staticmethod
    def _label(text: str, color: str = None, size: int = 11, bold: bool = False):
        lab = QLabel(text)
        font = QFont(lab.font())
        font.setPointSize(size)
        font.setBold(bold)
        lab.setFont(font)
        lab.setStyleSheet(f"color:{color or THEME['muted']};font-size:{size}pt;")
        return lab

    def _group(self, text: str):
        lab = self._label(text, THEME["muted"], 11)
        lab.setStyleSheet(
            f"color:{THEME['muted']};font-size:11px;border-bottom:1px solid {INK};"
            "padding-bottom:3px;margin-top:8px;")
        self.vbox.addWidget(lab)

    def _field(self, caption: str, widget: QWidget):
        self.vbox.addWidget(self._label(caption))
        self.vbox.addWidget(widget)

    def _render(self):
        self._loading = True
        _clear_layout(self.vbox)
        if self.obj is None:
            self._loading = False
            self._show_empty()
            return

        obj = self.obj
        props = dict(obj.properties.values)

        # 标题
        self.vbox.addWidget(self._label("触发器属性", THEME["text"], 12, True))

        # 名字
        self.f_name = QLineEdit(obj.name or "")
        self.f_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.f_name.editingFinished.connect(
            lambda: self._edit(lambda o, v=self.f_name.text(): setattr(o, "name", v),
                               "修改触发器名字"))
        self._field("名字", self.f_name)

        # 类型（中文（english））——滚轮不切换选项
        self.f_type = NoWheelComboBox()
        self.f_type.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.f_type.setItemDelegate(_TypeColorDelegate(self.f_type))

        def _add_typed_item(text, key, color_key=None):
            """加一个下拉项，并按该类型的标题条颜色给文字上色。"""
            row = self.f_type.count()
            self.f_type.addItem(text, key)
            self.f_type.setItemData(
                row, QColor(color_for(color_key if color_key is not None else key)),
                Qt.ForegroundRole)

        for key, (label, _hint, _tpl) in TYPE_DEFS.items():
            _add_typed_item(f"{label}（{key}）", key)
        # 纯空 type（map_info / 矩形等辅助对象）显示为「未分类」，不要误当初始单位
        if not any(self.f_type.itemData(i) == "" for i in range(self.f_type.count())):
            # 空 type 对应默认灰（未分类），与节点标题条一致
            _add_typed_item("未分类（空 type）", "", "")
        cur = effective_type(obj)
        idx = self.f_type.findData(cur)
        if idx < 0:
            _add_typed_item(f"未知类型（{cur}）", cur)
            idx = self.f_type.count() - 1
        self.f_type.setCurrentIndex(idx)
        self.f_type.currentIndexChanged.connect(self._on_type_changed)
        self._field("类型", self.f_type)

        hint = self._label(
            f"{type_hint(cur)}\nTMX 里仍写英文 {cur or '(空)'}，这里只改显示",
            THEME["muted"], 10)
        hint.setWordWrap(True)
        self.vbox.addWidget(hint)

        # 触发器 ID：按需求在界面隐藏。控件仍创建（保留数据绑定 / 兼容读取
        # .text() 的调用与测试），但不加入布局、不显示；文档里的 id 照常保留。
        # 不设父级、不加入布局、也不 show()：既不会闪顶层空窗，也不会每次
        # 渲染残留隐藏子控件（旧 f_id 随重新赋值被回收）。
        self.f_id = QLineEdit(props.get("id", ""))
        self.f_id.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.f_id.editingFinished.connect(
            lambda: self._edit(
                lambda o, v=self.f_id.text(): o.properties.set("id", v),
                "修改触发器 ID"))

        # 属性表
        self._group("属性（中文标签 · 导出仍为英文键）")
        ordered = list(type_props(cur)) + list(props) + list(COMMON_PROPS)
        seen = set()
        self._prop_rows: list[tuple[str, QLineEdit, QLabel]] = []
        count = 0
        for key in ordered:
            if (key in seen or key == "id" or key in LINK_PROPS
                    or key in HIDDEN_EDITOR_PROPERTIES or key not in props):
                continue
            seen.add(key)
            # 已有「随机生成单位」时，spawnUnits 完全由它派生，不再单独显示。
            if key == "spawnUnits" and "randomUnits" in props:
                continue
            if key == "randomUnits":
                self._add_random_units_row(key, props.get(key, ""))
            else:
                self._add_prop_row(key, props.get(key, ""))
            count += 1
        if count == 0:
            self.vbox.addWidget(self._label("该类型无需属性", THEME["muted"], 11))

        # 添加属性 ——滚轮不切换选项
        add_row = QHBoxLayout()
        self.prop_select = NoWheelComboBox()
        self._fill_prop_picker(props, cur)
        self.prop_select.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.prop_select.setMinimumWidth(0)
        add_row.addWidget(self.prop_select)
        btn_add = QPushButton("＋")
        btn_add.setFixedWidth(32)
        btn_add.setToolTip("添加选中的属性")
        btn_add.setStyleSheet(
            f"QPushButton{{background:{ASH};color:{TEXT};border:1px solid {INK};"
            "border-radius:4px;padding:4px;font-size:12px;}")
        btn_add.clicked.connect(self._add_picked_prop)
        add_row.addWidget(btn_add)
        self.vbox.addLayout(add_row)

        # 连线列表
        self._group("输出连线（激活 →）")
        self._render_edges("out")
        self._group("输入连线（← 前置 / 停用）")
        self._render_edges("in")

        # 删除节点
        btn_del = QPushButton("删除此节点")
        btn_del.setStyleSheet(
            "QPushButton{background:#4a1f1f;color:#f0b0b0;border:1px solid #111;"
            "border-radius:4px;padding:7px;margin-top:10px;font-size:11px;}"
            "QPushButton:hover{background:#5a2626;}")
        btn_del.clicked.connect(self._delete_node)
        self.vbox.addWidget(btn_del)
        self.vbox.addStretch(1)
        self._loading = False

    def _add_prop_row(self, key: str, value: str):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        lab = self._label(prop_label(key), THEME["muted"], 11)
        lab.setFixedWidth(82)
        lab.setToolTip(key + (" · " + prop_desc(key) if prop_desc(key) else ""))
        h.addWidget(lab)
        edit = QLineEdit(str(value))
        edit.setMinimumWidth(0)
        edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        edit.setPlaceholderText(prop_example(key) or key)
        edit.setToolTip(prop_desc(key) or key)
        edit.editingFinished.connect(
            lambda k=key, e=edit: self._edit(
                lambda o, kk=k, vv=unit_names.normalize_spawn_units(e.text())
                if k in ("unitType", "spawnUnits") else e.text():
                o.properties.set(kk, vv),
                f"修改属性 {k}"))
        h.addWidget(edit)
        btn = CrossButton()
        btn.setFixedWidth(26)
        btn.setToolTip("删除该属性")
        btn.setStyleSheet(
            "QPushButton{background:#3a2020;border:1px solid #111;color:#e88;"
            "border-radius:4px;font-size:11px;}")
        btn.clicked.connect(lambda _c=False, k=key: self._remove_prop(k))
        h.addWidget(btn)
        self.vbox.addWidget(row)

        vhint = self._label(describe_value(key, value), THEME["vhint"], 10)
        vhint.setContentsMargins(87, 0, 0, 3)
        vhint.setWordWrap(True)
        vhint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.vbox.addWidget(vhint)
        # 必须先 addWidget 再 setVisible：无 parent 的控件调用 setVisible(True)
        # 会被 Qt 当成顶层窗口显示（带系统三键、标题回退成 applicationName），
        # 随后 addWidget 才挂回父级 → 一闪而过的空窗。属性多时批量出现。
        vhint.setVisible(bool(vhint.text()))
        # 输入时实时更新中文释义
        edit.textEdited.connect(
            lambda text, k=key, l=vhint: (
                l.setText(describe_value(k, text)),
                l.setVisible(bool(describe_value(k, text)))))
        self._prop_rows.append((key, edit, vhint))

    def _cn_weighted(self, value: str) -> str:
        """randomUnits -> 中文展示串（重型坦克 ×3、坦克 ×2、侦察车）。"""
        pairs = unit_names.parse_weighted(value)
        parts = []
        for raw, weight in pairs:
            cn = unit_names.to_cn(raw)
            parts.append(cn if weight == 1 else f"{cn} ×{('%g' % weight)}")
        return "、".join(parts)

    def _weight_hint(self, value: str) -> str:
        pairs = unit_names.parse_weighted(value)
        live = [(u, w) for u, w in pairs if (w or 0) > 0]
        if not live:
            return ""
        if len(live) == 1:
            return "只有一个单位，生成概率 100%"
        return "按权重折算的实际概率：" + unit_names.describe_weights(live)

    def _add_random_units_row(self, key: str, value: str):
        """「随机生成单位」专用行：中文展示 + 权重 + 多选对话框。

        写法 ``重型坦克:3,坦克:2,侦察车:1``（省略权重 = 1）；导出 TMX 时由
        tmx_writer 按权重折算成链式 spawnChance，导入时若能安全反推则还原。
        """
        from .unit_picker import UnitPickerDialog
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        lab = self._label(prop_label(key), THEME["muted"], 11)
        lab.setFixedWidth(82)
        lab.setToolTip(key + (" · " + prop_desc(key) if prop_desc(key) else ""))
        h.addWidget(lab)

        edit = QLineEdit(self._cn_weighted(value))
        edit.setMinimumWidth(0)
        edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        edit.setPlaceholderText("重型坦克:3, 坦克:2（冒号后是权重，可省略）")
        edit.setToolTip(prop_desc(key) or key)
        h.addWidget(edit)

        btn = QPushButton("选择…")
        btn.setFixedWidth(62)
        btn.setStyleSheet(
            f"QPushButton{{background:{ASH};color:{TEXT};border:1px solid {INK};"
            "border-radius:4px;padding:4px;font-size:11px;}")
        h.addWidget(btn)

        cross = CrossButton()
        cross.setFixedWidth(26)
        cross.setToolTip("删除该属性")
        cross.setStyleSheet(
            "QPushButton{background:#3a2020;border:1px solid #111;color:#e88;"
            "border-radius:4px;font-size:11px;}")
        cross.clicked.connect(lambda _c=False, k=key: self._remove_prop(k))
        h.addWidget(cross)
        self.vbox.addWidget(row)

        vhint = self._label(self._weight_hint(value), THEME["vhint"], 10)
        vhint.setContentsMargins(87, 0, 0, 3)
        # 不换行的话这句长文案会把整个面板的最小宽度撑开，导致右边被裁掉
        vhint.setWordWrap(True)
        vhint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.vbox.addWidget(vhint)
        # 同上：先挂进布局再显示，避免无 parent 时闪出顶层空窗。
        vhint.setVisible(bool(vhint.text()))

        def _refresh_hint():
            text = self._weight_hint(edit.text())
            vhint.setText(text)
            vhint.setVisible(bool(text))

        edit.textEdited.connect(lambda _t: _refresh_hint())

        def _open_picker():
            dlg = UnitPickerDialog(
                self, current=[(u, w) for u, w in
                               unit_names.parse_weighted(edit.text())])
            if dlg.exec() == 1:  # QDialog.Accepted
                edit.setText(self._cn_weighted(
                    unit_names.format_weighted(dlg.selected_weighted)))
                _refresh_hint()

        def _commit():
            pairs = unit_names.parse_weighted(edit.text())
            text = unit_names.format_weighted(pairs)
            edit.setText(self._cn_weighted(text))
            _refresh_hint()
            self._edit(lambda o, v=text: o.properties.set(key, v),
                       "编辑随机生成单位")

        btn.clicked.connect(_open_picker)
        edit.editingFinished.connect(_commit)

    def _fill_prop_picker(self, props: dict, cur: str):
        used = set(props)
        groups = (
            ("本类型推荐", type_props(cur)),
            ("通用调度", COMMON_PROPS),
            ("其他属性", tuple(k for k in PROP_DEFS
                           if k not in type_props(cur) and k not in COMMON_PROPS)),
        )
        for title, keys in groups:
            candidates = [k for k in keys
                          if k not in used and k != "id"
                          and k not in LINK_PROPS
                          and k not in HIDDEN_EDITOR_PROPERTIES]
            if not candidates:
                continue
            self.prop_select.addItem(f"── {title} ──", "")
            i = self.prop_select.count() - 1
            item = self.prop_select.model().item(i)
            if item is not None:
                item.setEnabled(False)
            for k in candidates:
                self.prop_select.addItem(f"{prop_label(k)}（{k}）", k)
        if self.prop_select.count() == 0:
            self.prop_select.addItem("（已无常用属性）", "")
        self.prop_select.addItem("自定义属性名…", "__custom__")

    def _render_edges(self, direction: str):
        """右下角连线列表：每行 = 方向色点 + 对方名 / 线型 + 一个删除叉。

        叉是纯几何的中心对称 ×，**不加边框、不加底色**、统一红色（与属性行
        那个带红色底框的删除按钮区分开）；右键这一行可在 Dock 里换线型。
        """
        rows = [e for e in self.edges if e[0] == direction]
        if not rows:
            self.vbox.addWidget(self._label("无", THEME["muted"], 11))
            return
        for row in rows:
            label, other, color = row[1], row[2], row[3]
            edge = row[4] if len(row) > 4 else None
            line = QWidget()
            line.setObjectName("EdgeRow")
            line.setToolTip("点叉删除这条连线；右键可改连线类型")
            line.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            line.customContextMenuRequested.connect(
                lambda pos, ed=edge, w=line:
                self.edgeMenuRequested.emit(ed, w.mapToGlobal(pos)))
            h = QHBoxLayout(line)
            h.setContentsMargins(2, 1, 2, 1)
            h.setSpacing(6)
            dot = QLabel("●")
            dot.setFixedWidth(12)
            dot.setStyleSheet(f"color:{color};font-size:11px;")
            h.addWidget(dot)
            text = QLabel(f"{'→' if direction == 'out' else '←'} {other} （{label}）")
            text.setStyleSheet(f"color:{TEXT};font-size:11px;")
            text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            h.addWidget(text)
            cross = CrossButton(borderless=True, color=EDGE_CROSS_COLOR)
            cross.setFixedSize(20, 20)
            cross.setCursor(Qt.PointingHandCursor)
            cross.setToolTip("删除这条连线")
            cross.clicked.connect(
                lambda _checked=False, ed=edge:
                self.edgeDeleteRequested.emit(ed))
            h.addWidget(cross)
            self.vbox.addWidget(line)

    # ---------------- 编辑 / 撤销 ----------------
    def _commit(self, mutate, text: str):
        if self.doc is None or self.layer is None or self.obj is None:
            return
        from ..commands.object_command import ObjectCommand, snapshot

        uid = object_uid(self.obj)
        oid = self.obj.object_id
        before = snapshot(self.layer)

        def apply(current_layer):
            # 重复 object_id 的地图里按 id 找会静默改到第一个同 id 对象，
            # 必须按稳定身份精确命中；找不到才退回旧行为（无 xml_element 的测试对象）。
            target = next((o for o in current_layer.objects
                           if object_uid(o) == uid), None)
            if target is None:
                target = next((o for o in current_layer.objects
                               if o.object_id == oid), None)
            if target is not None:
                mutate(target)

        apply(self.layer)
        after = snapshot(self.layer)
        self.layer.dirty = True
        self.doc.dirty = True
        cmd = ObjectCommand(self.doc, self.layer, before, after, text,
                            self._after_change)
        if self.undo_stack is not None:
            self.undo_stack.push(cmd)
        else:
            cmd.redo()

    def _after_change(self):
        """redo / undo 后由 Dock 重新刷新并重新绑定对象。"""
        if self.on_change is not None:
            self.on_change()

    def _edit(self, mutate, text: str):
        if self._loading or self.obj is None:
            return
        # 值没变就不生成撤销命令
        self._commit(mutate, text)

    def _remove_prop(self, key: str):
        def mutate(o):
            o.properties.values.pop(key, None)
            o.properties.types.pop(key, None)
            o.properties.text_props.discard(key)
        self._commit(mutate, f"删除属性 {key}")

    def _add_picked_prop(self):
        key = self.prop_select.currentData()
        if not key:
            return
        if key == "__custom__":
            from PySide6.QtWidgets import QInputDialog
            key, ok = QInputDialog.getText(
                self, "添加属性", "属性名（导出 TMX 时原样写入，建议用英文键）：")
            if not ok or not key.strip():
                return
            key = key.strip()
            if key in LINK_PROPS:
                QMessageBox.information(
                    self, "提示", f"这是连线型属性（{key}），请在节点图上拖端口连线建立。")
                return
        self._commit(lambda o, k=key: o.properties.set(k, ""), f"添加属性 {key}")

    def _on_type_changed(self, _index):
        if self._loading or self.obj is None:
            return
        new_type = self.f_type.currentData()
        old_type = self.obj.type_ or ""
        # new_type 允许是空串（「地图初始单位」在 TMX 里 type 为空），
        # 只有 QComboBox 未选中返回 None 时才跳过。
        if new_type is None or new_type == old_type:
            return
        before_props = dict(self.obj.properties.values)
        # 类型切换只能重建「类型相关属性」；编辑器私有属性（分组 / 节点位置）
        # 必须原样保住，否则切一次类型分组就没了，节点图布局也会丢。
        hidden_before = {
            k: (before_props[k],
                self.obj.properties.types.get(k),
                k in self.obj.properties.text_props)
            for k in HIDDEN_EDITOR_PROPERTIES if k in before_props
        }
        def mutate(o):
            bag = o.properties
            bag.values.clear()
            bag.types.clear()
            bag.text_props.clear()
            for k in type_props(new_type):
                bag.set(k, before_props.get(k, ""))
            bag.set("id", before_props.get("id", ""))
            for k, (value, typ, is_text) in hidden_before.items():
                bag.set(k, value, typ)
                if is_text:
                    bag.text_props.add(k)
            o.type_ = new_type
            if not o.name or o.name in (old_type, type_label(old_type)):
                o.name = type_label(new_type)

        self._commit(mutate, f"切换触发器类型为 {type_label(new_type)}")

    def _delete_node(self):
        if self.obj is None or self.layer is None:
            return
        name = self.obj.name or self.obj.type_ or f"#{self.obj.object_id}"
        if QMessageBox.question(
                self, "删除节点", f"确定删除「{name}」及其在地图上的对象？\n（可撤销）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.objectDeleteRequested.emit(self.obj)
