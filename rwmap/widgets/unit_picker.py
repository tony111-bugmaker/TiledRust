"""「随机生成单位」选择器：中文多选 + 权重 + 分类筛选。

单位表来自 ``config/unit_names.json``（由 ``probe/build_unit_catalog.py``
从游戏 ``assets/units`` 与官方中文翻译生成），覆盖全部原版单位 / 建筑；
内部变体（战役 ``c_*``、形态中间态、模块零件、开发桩）默认折叠。

权重越大越容易被抽到：3:2:1 就是 50% / 33.3% / 16.7%。RW 没有权重参数，
导出时由 ``unit_names.build_spawn_units_weighted`` 折算成链式 ``spawnChance``。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..rw import unit_names
from ..rw.unit_names import CN_TO_RAW, RAW_TO_CN, to_cn, to_raw
from ..theme import ASH, INK, TEXT, TEXT_DIM
from ..widgets.plain_spinbox import PlainSpinBox


def _canonical(raw: str) -> str:
    """把别名/大小写变体归到表里的规范写法。"""
    cn = RAW_TO_CN.get(to_raw(raw))
    return CN_TO_RAW.get(cn, raw) if cn else raw


class UnitPickerDialog(QDialog):
    """列出全部单位（分类 + 搜索），勾选并填权重。

    返回 ``[(raw, 权重)]``，顺序为「已选的在前且保持原顺序」。
    """

    def __init__(self, parent=None, current=None):
        super().__init__(parent)
        self.setWindowTitle("选择随机生成的单位 · 设定权重")
        self.setMinimumWidth(430)
        self.setMinimumHeight(460)

        # 兼容两种入参：["heavyTank"] 或 [("heavyTank", 3.0)]
        weights: dict[str, float] = {}
        for item in current or []:
            if isinstance(item, (tuple, list)):
                raw, weight = item[0], item[1]
            else:
                raw, weight = item, 1
            weights[_canonical(str(raw))] = float(weight)

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
        tip = QLabel("权重越大越容易被抽到（3 : 2 : 1 = 50% / 33.3% / 16.7%）")
        tip.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        tip.setWordWrap(True)
        tip_line.addWidget(tip, 1)
        self.show_hidden = QCheckBox("显示内部变体")
        self.show_hidden.setStyleSheet(f"font-size:11px;color:{TEXT_DIM};")
        self.show_hidden.setToolTip(
            "战役版（c_*）、形态中间态、模块零件与开发用桩，普通玩法用不到")
        tip_line.addWidget(self.show_hidden, 0)
        root.addLayout(tip_line)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(3)

        self._boxes: list[tuple[str, str, QPushButton, PlainSpinBox]] = []
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

            suffix = "" if cn == raw else f"  ({raw})"
            box = QPushButton(f"{cn}{suffix}")
            box.setCheckable(True)
            box.setChecked(raw in weights)
            box.setToolTip(f"{cn}\n内部名：{raw}\n分类：{group}"
                           + ("\n（内部变体）" if hidden else ""))
            box.setStyleSheet(
                f"QPushButton{{background:{ASH};color:{TEXT};border:1px solid {INK};"
                "border-radius:4px;padding:6px 8px;font-size:12px;text-align:left;}"
                f"QPushButton:checked{{background:{ASH};color:{TEXT};"
                f"border:1px solid {INK};}}")
            hl.addWidget(box, 1)

            spin = PlainSpinBox()
            spin.setRange(1, 99)
            spin.setFixedWidth(58)
            spin.setValue(max(1, int(round(weights.get(raw, 1)))))
            spin.setSuffix(" ×")
            spin.setToolTip("权重：越大越容易被抽到")
            spin.setEnabled(raw in weights)
            spin.setStyleSheet(
                f"QSpinBox{{background:{ASH};color:{TEXT};border:1px solid {INK};"
                "border-radius:4px;padding:2px 4px;font-size:11px;}"
                f"QSpinBox:disabled{{color:{TEXT_DIM};background:{INK};}}")
            box.toggled.connect(spin.setEnabled)
            box.toggled.connect(self._update_count)
            spin.valueChanged.connect(self._update_count)
            hl.addWidget(spin, 0)

            vbox.addWidget(row)
            self._boxes.append((raw, cn, box, spin))
            self._meta[raw] = (group, hidden)

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
            spin.setVisible(hit)
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

    def _update_count(self):
        n = sum(1 for _r, _c, b, _s in self._boxes if b.isChecked())
        self._count_label.setText(f"已选 {n} 个")

    # ------------------------------------------------------------- 结果
    @property
    def selected_weighted(self) -> list[tuple[str, float]]:
        """按当前顺序返回 ``[(raw, 权重)]``。"""
        return [(raw, float(spin.value()))
                for raw, _cn, box, spin in self._boxes if box.isChecked()]

    @property
    def selected(self) -> list[str]:
        """只要 raw 名时的兼容出口。"""
        return [raw for raw, _w in self.selected_weighted]


__all__ = ["UnitPickerDialog"]
