"""「插入随机事件」对话框：配置等概率抽签 + 矩形检测 + 删除单位。

只收集参数，真正生成对象是 :mod:`rwmap.rw.random_event` 的事。
界面和「新建图集」保持一致：QFormLayout + 中文标签 + 实时预览。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from ..rw.random_event import (
    DEFAULT_MARKERS, DEFAULT_UNIT_TYPE, RandomEventSpec, branch_labels,
    describe,
)
from ..rw import unit_names
from ..theme import TEXT, TEXT_DIM

_UNIT_PRESETS = (
    "heavyTank", "mammothTank", "experimentalTank", "tank", "laserTank",
    "builder", "scout", "hovertank", "turret",
)
_TEAM_PRESETS = tuple(str(i) for i in range(15)) + ("-1", "-2", "none")


class RandomEventDialog(QDialog):
    """收集一条等概率随机事件链的参数。"""

    def __init__(self, parent=None, doc=None):
        super().__init__(parent)
        self.setWindowTitle("插入随机事件（等概率）")
        self.setMinimumWidth(460)
        self.doc = doc

        # 默认把触发矩形放在地图中间，抽签区放左上角，避免两者重叠。
        if doc is not None and getattr(doc, "width", 0) and getattr(doc, "height", 0):
            map_w = doc.width * (doc.tile_w or 20)
            map_h = doc.height * (doc.tile_h or 20)
            rw = max(80, min(240, map_w // 5))
            rh = max(80, min(240, map_h // 5))
            default_rect = ((map_w - rw) // 2, (map_h - rh) // 2, rw, rh)
        else:
            default_rect = (400, 300, 120, 120)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.name_edit = QLineEdit("随机事件")
        form.addRow("事件名称", self.name_edit)

        self.unit_combo = QComboBox()
        self.unit_combo.setEditable(True)
        for raw in _UNIT_PRESETS:
            self.unit_combo.addItem(f"{unit_names.to_cn(raw)}（{raw}）", raw)
        idx = self.unit_combo.findData(DEFAULT_UNIT_TYPE)
        if idx >= 0:
            self.unit_combo.setCurrentIndex(idx)
        self.unit_combo.setToolTip("要检测 / 删除的单位（可输入中文或英文内部名）")
        form.addRow("目标兵种", self.unit_combo)

        self.team_combo = QComboBox()
        self.team_combo.addItems(list(_TEAM_PRESETS))
        self.team_combo.setToolTip("检测与删除都限定在该队伍")
        form.addRow("队伍 team", self.team_combo)

        self.branch_spin = QSpinBox()
        self.branch_spin.setRange(2, len(DEFAULT_MARKERS))
        self.branch_spin.setValue(2)
        self.branch_spin.setToolTip("N 个分支抽 1 个，每个概率都是 1/N")
        form.addRow("分支数 N", self.branch_spin)

        self.limit_check = QCheckBox("矩形内恰好一辆才触发（maxUnits=1）")
        self.limit_check.setToolTip("不勾 = 至少一辆；勾选 = 有且仅有一辆")
        form.addRow("", self.limit_check)

        self.reset_edit = QLineEdit("1s")
        self.reset_edit.setToolTip("触发后多久复位，可再次触发")
        form.addRow("复位时间", self.reset_edit)
        root.addLayout(form)

        rect_group = QGroupBox("触发 / 删除矩形（Tiled 像素坐标）")
        rg = QFormLayout(rect_group)
        self.rect_spins = []
        for lab, val in zip(("X", "Y", "宽 W", "高 H"), default_rect):
            sp = QSpinBox()
            sp.setRange(0, 100000)
            sp.setValue(int(val))
            sp.setToolTip("unitDetect 与 unitRemove 共用这个矩形")
            rg.addRow(lab, sp)
            self.rect_spins.append(sp)
        root.addWidget(rect_group)

        roll_group = QGroupBox("抽签区（生成标记单位的角落，别和触发矩形重叠）")
        rr = QFormLayout(roll_group)
        self.roll_spins = []
        for lab, val in zip(("X", "Y", "宽 W", "高 H"), (0, 0, 20, 20)):
            sp = QSpinBox()
            sp.setRange(0, 100000)
            sp.setValue(val)
            rr.addRow(lab, sp)
            self.roll_spins.append(sp)
        root.addWidget(roll_group)

        self.prob_label = QLabel()
        self.prob_label.setStyleSheet(
            f"color:{TEXT};font-size:11px;padding:4px 2px;")
        root.addWidget(self.prob_label)

        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet(
            f"color:{TEXT_DIM};font-size:10px;padding:2px 2px 6px 2px;")
        root.addWidget(self.note_label)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("插入到 Triggers 层")
        ok.setDefault(True)
        ok.clicked.connect(self._on_accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        root.addLayout(btns)

        for widget in (self.unit_combo, self.team_combo, self.rect_spins[2],
                       self.rect_spins[3]):
            if hasattr(widget, "currentTextChanged"):
                widget.currentTextChanged.connect(self._update_preview)
            else:
                widget.valueChanged.connect(self._update_preview)
        self.branch_spin.valueChanged.connect(self._update_preview)
        self.limit_check.toggled.connect(self._update_preview)
        self._update_preview()

    # ---------------- 预览 ----------------
    def _update_preview(self, *_args):
        spec = self.spec()
        labels = " ｜ ".join(branch_labels(spec))
        self.prob_label.setText(
            f"等概率 {spec.branch_count} 选 1，每支 "
            f"{100.0 / spec.branch_count:g}%：{labels}")
        self.note_label.setText(describe(spec))

    # ---------------- 结果 ----------------
    def spec(self) -> RandomEventSpec:
        return RandomEventSpec(
            name=self.name_edit.text().strip() or "随机事件",
            rect=tuple(sp.value() for sp in self.rect_spins),   # type: ignore[arg-type]
            unit_type=(self.unit_combo.currentData()
                       or unit_names.normalize_spawn_units(
                           self.unit_combo.currentText())
                       or DEFAULT_UNIT_TYPE),
            team=self.team_combo.currentText().strip() or "0",
            branch_count=self.branch_spin.value(),
            markers=DEFAULT_MARKERS,
            roll_rect=tuple(sp.value() for sp in self.roll_spins),  # type: ignore[arg-type]
            reset_after=self.reset_edit.text().strip() or "1s",
            limit_one=self.limit_check.isChecked(),
        )

    def _on_accept(self):
        spec = self.spec()
        if spec.rect[2] <= 0 or spec.rect[3] <= 0:
            self.note_label.setText("⚠ 触发矩形的宽和高必须大于 0")
            return
        if spec.roll_rect[2] <= 0 or spec.roll_rect[3] <= 0:
            self.note_label.setText("⚠ 抽签区的宽和高必须大于 0")
            return
        self.accept()
