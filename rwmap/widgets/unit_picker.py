"""三种「造兵」共用的单位选择器：中文勾选 + 分类筛选 + 图鉴入口。

单位表来自 ``config/unit_names.json``（由 ``probe/build_unit_catalog.py``
从游戏 ``assets/units`` 与官方中文翻译生成），覆盖全部原版单位 / 建筑；
内部变体（战役 ``c_*``、形态中间态、模块零件、开发桩）默认折叠。

每个单位一行：最左侧是**游戏内外观预览**（units.tsx 头像 / 精灵条主图 /
占位问号），勾选后**单位名按钮变绿**（生成单位触发器 unitAdd 同款绿
``#2f8f52``）。顶部统一有「单位图鉴」按钮，可打开软件内图鉴看拼装外观。

四种模式（``mode``）：
- ``random``（默认）：权重 + 数量两列。权重 3:2:1 = 50%/33.3%/16.7%，
  RW 没有权重参数，导出时折算成链式 ``spawnChance`` + ``*数量``。
- ``spawn``（生成单位 unitAdd）：只有数量列，写回 ``单位*数量``。
- ``wave``（波次刷兵列表 rwmap_wave_units）：只有数量列，写回原版 ``数量 单位名``。
- ``initial``（地图初始单位 mapUnit）：单选，无权重无数量，写回单个 raw 名。

只有 ``random`` 有权重；其余模式必须注册在 MODES 里，否则会被当成非法
mode 回退到 ``random``，在刷兵列表里错误显示权重列。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..rw import unit_names, unit_preview
from ..rw.unit_names import CN_TO_RAW, RAW_TO_CN, to_cn, to_raw
from ..theme import ASH, INK, TEXT, TEXT_DIM
from ..widgets.plain_spinbox import PlainSpinBox

# 与「生成单位」触发器（unitAdd）同款绿，选中单位时高亮名字块。
SPAWN_GREEN = "#2f8f52"


def _canonical(raw: str) -> str:
    """把别名/大小写变体归到表里的规范写法。"""
    cn = RAW_TO_CN.get(to_raw(raw))
    return CN_TO_RAW.get(cn, raw) if cn else raw


class UnitPickerDialog(QDialog):
    """列出全部单位（分类 + 搜索），勾选并按需填权重、数量。

    ``mode`` 决定显示哪些列：``random`` 权重+数量 / ``spawn`` 和 ``wave``
    仅数量 / ``initial`` 单选无数值列。四种模式都带「单位图鉴」入口。

    current 兼容三种写法：``["heavyTank"]``、``[("heavyTank", 3.0)]``、
    ``[("heavyTank", 3.0, 2)]``（权重 / 数量省略视为 1）。

    出口 ``selected_units`` 返回 ``[(raw, 权重, 数量)]``（无权重模式权重恒 1）；
    ``selected_weighted`` 与 ``selected`` 是兼容出口。顺序为「已选的在前且保持原顺序」。
    """

    MODES = ("random", "spawn", "wave", "initial")

    def __init__(self, parent=None, current=None, mode="random"):
        super().__init__(parent)
        self.mode = mode if mode in self.MODES else "random"
        # 权重只属于 randomUnits；spawn/wave/initial 一律不显示权重列。
        self._with_weight = self.mode == "random"
        self._with_count = self.mode != "initial"
        titles = {
            "random": "选择随机生成的单位 · 设定权重 / 数量",
            "spawn": "选择生成的单位 · 设定数量",
            "wave": "选择刷兵列表单位 · 设定数量",
            "initial": "选择地图初始单位",
        }
        self.setWindowTitle(titles[self.mode])
        self.setMinimumWidth(470 if self._with_count else 400)
        self.setMinimumHeight(480)

        # 兼容字符串 / 二元组 / 三元组三种入参 -> raw -> (权重, 数量)
        weights: dict[str, tuple[float, int]] = {}
        for item in current or []:
            if isinstance(item, (tuple, list)):
                raw = item[0]
                weight = float(item[1]) if len(item) > 1 else 1.0
                count = max(1, int(item[2])) if len(item) > 2 else 1
            else:
                raw, weight, count = item, 1.0, 1
            weights[_canonical(str(raw))] = (weight, count)

        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self._count_label = QLabel("已选 0 个")
        self._count_label.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        bar.addWidget(self._count_label)
        bar.addStretch(1)
        all_btn = QPushButton("全选")
        all_btn.setFixedWidth(56)
        none_btn = QPushButton("清空")
        none_btn.setFixedWidth(56)
        bar.addWidget(all_btn)
        bar.addWidget(none_btn)
        gallery_btn = QPushButton("单位图鉴")
        gallery_btn.setToolTip("打开软件内图鉴：看单位拼装外观、中文名与贴图部件")
        gallery_btn.setFixedWidth(78)
        gallery_btn.clicked.connect(self._open_gallery)
        bar.addWidget(gallery_btn)
        root.addLayout(bar)

        line = QHBoxLayout()
        line.setSpacing(6)
        self.group_combo = QComboBox()
        self.group_combo.addItem("全部分类")
        for group in unit_names.GROUPS:
            self.group_combo.addItem(group)
        self.group_combo.setFixedWidth(110)
        line.addWidget(self.group_combo)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索单位（中文或英文内部名）")
        self.search.setClearButtonEnabled(True)
        line.addWidget(self.search, 1)
        root.addLayout(line)

        tip_line = QHBoxLayout()
        tips = {
            "random": "权重越大越容易被抽到（3 : 2 : 1 = 50% / 33.3% / 16.7%）",
            "spawn": "数量：这次触发一次生成几个该单位",
            "wave": "数量：这一波刷几个该单位（原版写法：6 heavyTank）",
            "initial": "开局就摆在地图上的单位，只能选一个",
        }
        tip = QLabel(tips[self.mode])
        tip.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        tip.setWordWrap(True)
        tip_line.addWidget(tip, 1)
        self.show_hidden = QCheckBox("显示内部变体")
        self.show_hidden.setStyleSheet(f"font-size:11px;color:{TEXT_DIM};")
        self.show_hidden.setToolTip(
            "战役版（c_*）、形态中间态、模块零件与开发用桩，普通玩法用不到")
        tip_line.addWidget(self.show_hidden, 0)
        root.addLayout(tip_line)

        # 表头：与每一行的「预览图标 / 单位名按钮 / 权重 / 数量」四列对齐。
        spin_w = 58
        icon_w = unit_preview.ICON_SIZE
        header = QHBoxLayout()
        header.setContentsMargins(6, 2, 6, 2)
        header.setSpacing(4)
        h_icon = QLabel("")
        h_icon.setFixedWidth(icon_w)
        h_icon.setToolTip("游戏内单位外观预览")
        header.addWidget(h_icon, 0)
        h_name = QLabel("单位")
        h_name.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        header.addWidget(h_name, 1)
        h_weight = QLabel("权重")
        h_weight.setFixedWidth(spin_w)
        h_weight.setAlignment(Qt.AlignCenter)
        h_weight.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        header.addWidget(h_weight, 0)
        h_count = QLabel("数量")
        h_count.setFixedWidth(spin_w)
        h_count.setAlignment(Qt.AlignCenter)
        h_count.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        header.addWidget(h_count, 0)
        root.addLayout(header)
        # 表头必须先等 root 挂到 self（addLayout）后才有 parent；
        # 仅 addWidget 到还没归属的 QHBoxLayout 时 parent() 仍是 None。
        h_weight.setVisible(self._with_weight)
        h_count.setVisible(self._with_count)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(3)

        # _boxes 保持 4 元素 (raw, cn, box, 权重spin) 以兼容既有测试；
        # 数量框单独放 _count_spins[raw]。
        self._boxes: list[tuple[str, str, QPushButton, PlainSpinBox]] = []
        self._count_spins: dict[str, PlainSpinBox] = {}
        self._icons: dict[str, QLabel] = {}            # raw -> 外观预览图标
        self._meta: dict[str, tuple[str, bool]] = {}   # raw -> (分组, 内部变体)
        rows = [(r["raw"], (r.get("cn") or r.get("en") or r["raw"]),
                 r.get("group") or "其他", bool(r.get("hidden")))
                for r in unit_names.catalog(include_hidden=True)]
        # 表里没有的单位（mod / 自定义 / 老版本残留）也要列出来并保持勾选，
        # 否则打开对话框点「确定」会把它们悄悄删掉。
        known = {r[0] for r in rows}
        for raw in weights:
            if raw not in known:
                rows.append((raw, to_cn(raw), "其他", False))
        # 已选单位排最前并保持原有顺序，避免改完权重列表顺序被打乱
        order = {raw: i for i, raw in enumerate(weights)}
        rows.sort(key=lambda rc: (0, order[rc[0]]) if rc[0] in order else (1, 0))

        for raw, cn, group, hidden in rows:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(4)

            w_default, c_default = weights.get(raw, (1.0, 1))

            # 单位外观预览：tsx 头像优先，精灵条兜底，再不行占位问号。
            icon = QLabel()
            icon.setFixedSize(icon_w, icon_w)
            icon.setAlignment(Qt.AlignCenter)
            icon.setPixmap(unit_preview.unit_preview_pixmap(raw))
            icon.setToolTip(f"{cn} 的外观（{raw}）")
            hl.addWidget(icon, 0)
            self._icons[raw] = icon

            suffix = "" if cn == raw else f"  ({raw})"
            box = QPushButton(f"{cn}{suffix}")
            # 长单位名（如 experimentalTank）默认 sizeHint 会把滚动面板撑到
            # ~538px，而 viewport 只有 ~434px，最右的「数量」框被整个挤出可视区。
            # 设 Ignored 让按钮不撑宽度、剩余空间按 stretch 分配，长名字自动省略号。
            box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            box.setCheckable(True)
            box.setChecked(raw in weights)
            box.setToolTip(f"{cn}\n内部名：{raw}\n分类：{group}"
                           + ("\n（内部变体）" if hidden else ""))
            box.setStyleSheet(
                f"QPushButton{{background:{ASH};color:{TEXT};border:1px solid {INK};"
                "border-radius:4px;padding:6px 8px;font-size:12px;text-align:left;}"
                f"QPushButton:checked{{background:{SPAWN_GREEN};color:#ffffff;"
                f"border:1px solid {SPAWN_GREEN};}}")
            hl.addWidget(box, 1)

            spin = PlainSpinBox()
            spin.setRange(1, 999)
            spin.setFixedWidth(spin_w)
            spin.setValue(max(1, int(round(w_default))))
            spin.setToolTip("权重：越大越容易被抽到")
            spin.setEnabled(raw in weights)
            # 绝不给 NoButtons 的 QSpinBox 设带 border/border-radius/padding 的局部
            # QSS：Qt 6.12 Windows11 样式下会把内部 QLineEdit 压成 1px 宽，值在但
            # 看不见（theme.APP_QSS 注释 + object_toolbar 都记过这个坑）。只允许
            # background/color/font-size；边框由 theme 的 _FocusStyle 代理绘制。
            spin.setStyleSheet(
                f"QSpinBox{{background:{ASH};color:{TEXT};font-size:11px;}}"
                f"QSpinBox:disabled{{color:{TEXT_DIM};background:{INK};}}")
            box.toggled.connect(spin.setEnabled)
            box.toggled.connect(self._update_count)
            spin.valueChanged.connect(self._update_count)
            hl.addWidget(spin, 0)
            spin.setVisible(self._with_weight)   # 必须先 addWidget 再 setVisible

            count_spin = PlainSpinBox()
            count_spin.setRange(1, 999)
            count_spin.setFixedWidth(spin_w)
            count_spin.setValue(c_default)
            count_spin.setToolTip("数量：抽中这个单位时一次刷几个")
            count_spin.setEnabled(raw in weights)
            count_spin.setStyleSheet(
                f"QSpinBox{{background:{ASH};color:{TEXT};font-size:11px;}}"
                f"QSpinBox:disabled{{color:{TEXT_DIM};background:{INK};}}")
            box.toggled.connect(count_spin.setEnabled)
            if self.mode == "initial":
                box.toggled.connect(
                    lambda checked, r=raw: self._only_one(r) if checked else None)
            hl.addWidget(count_spin, 0)
            count_spin.setVisible(self._with_count)   # 同上：先挂父级再显示

            vbox.addWidget(row)
            self._boxes.append((raw, cn, box, spin))
            self._count_spins[raw] = count_spin
            self._meta[raw] = (group, hidden)

        if self.mode == "initial":
            # current 可能带了多个（旧数据 / 手输）；初始就只保留第一个
            seen = False
            for raw, _cn, box, _spin in self._boxes:
                if box.isChecked():
                    if seen:
                        box.setChecked(False)
                    else:
                        seen = True

        self._rows = []
        for i in range(vbox.count()):
            widget = vbox.itemAt(i).widget()
            if widget is not None:
                self._rows.append(widget)
        vbox.addStretch(1)
        scroll.setWidget(panel)
        root.addWidget(scroll)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        for btn in btns.buttons():
            btn.setMinimumSize(76, 28)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        self._visible = {raw for raw, _cn, _b, _s in self._boxes}
        self.search.textChanged.connect(self._apply_filter)
        self.group_combo.currentTextChanged.connect(self._apply_filter)
        self.show_hidden.toggled.connect(self._apply_filter)
        self._apply_filter()          # 初始就把内部变体折叠起来
        self._update_count()

    # ------------------------------------------------------------- 筛选
    def _apply_filter(self, *_args):
        needle = (self.search.text() or "").strip().lower()
        group = self.group_combo.currentText()
        allow_hidden = self.show_hidden.isChecked()
        self._visible = set()
        for raw, cn, box, spin in self._boxes:
            row_group, hidden = self._meta.get(raw, ("其他", False))
            hit = ((not needle) or needle in cn.lower() or needle in raw.lower())
            if hit and group not in ("", "全部分类"):
                hit = row_group == group
            if hit and hidden and not allow_hidden:
                hit = False
            box.setVisible(hit)
            spin.setVisible(hit and self._with_weight)
            cspin = self._count_spins.get(raw)
            if cspin is not None:
                cspin.setVisible(hit and self._with_count)
            ico = self._icons.get(raw)
            if ico is not None:
                ico.setVisible(hit)
            parent = box.parentWidget()
            if parent is not None and parent is not box:
                parent.setVisible(hit)
            if hit:
                self._visible.add(raw)

    def _set_all(self, state: bool):
        """只作用于当前筛选可见的条目，全选不会误勾搜不到的单位。

        用 ``self._visible`` 而不是 ``isVisible()``：对话框没 show 时
        ``isVisible()`` 恒为 False，headless 测试会误判成「全都不可见」。
        """
        for raw, _cn, box, _spin in self._boxes:
            if raw in self._visible:
                box.setChecked(state)

    def _only_one(self, raw: str):
        """initial 模式单选：勾上一个就取消其它已勾选的单位。"""
        for other, _cn, box, _spin in self._boxes:
            if other != raw and box.isChecked():
                box.setChecked(False)

    def _update_count(self):
        n = sum(1 for _r, _c, b, _s in self._boxes if b.isChecked())
        self._count_label.setText(f"已选 {n} 个")

    def _open_gallery(self):
        """打开图鉴窗口（懒导入，避免选择器启动就扫描全部游戏贴图）。"""
        from .unit_gallery_dialog import UnitGalleryDialog

        dlg = getattr(self, "_gallery_dlg", None)
        if dlg is None:
            dlg = UnitGalleryDialog(self)
            self._gallery_dlg = dlg
        dlg.show()
        dlg.raise_()

    # ------------------------------------------------------------- 结果
    @property
    def selected_units(self) -> list[tuple[str, float, int]]:
        """按当前顺序返回 ``[(raw, 权重, 数量)]``。

        无权重模式权重恒 1；initial 模式只取第一个（单选）。数量列隐藏时
        数量恒 1。
        """
        out = []
        for raw, _cn, box, spin in self._boxes:
            if box.isChecked():
                cspin = self._count_spins.get(raw)
                count = int(cspin.value()) if (cspin is not None
                                              and self._with_count) else 1
                weight = float(spin.value()) if self._with_weight else 1.0
                out.append((raw, weight, count))
                if self.mode == "initial":
                    break
        return out

    @property
    def selected_weighted(self) -> list[tuple[str, float]]:
        """二元组兼容出口：丢弃数量。"""
        return [(raw, w) for raw, w, _n in self.selected_units]

    @property
    def selected(self) -> list[str]:
        """只要 raw 名时的兼容出口。"""
        return [raw for raw, _w, _n in self.selected_units]


__all__ = ["UnitPickerDialog", "SPAWN_GREEN"]
