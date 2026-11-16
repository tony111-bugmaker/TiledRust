"""自测窗口：一键运行探针并显示报告。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout,
)
from PySide6.QtCore import QCoreApplication

from ..probe.probe_report import format_report, run_all


class ProbeWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自测 / 探针")
        self.resize(760, 520)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.btn_all = QPushButton("运行全部探针")
        self.btn_quick = QPushButton("快速（不含批量）")
        row = QHBoxLayout()
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_quick)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.text)
        self.btn_all.clicked.connect(self._run_all)
        self.btn_quick.clicked.connect(self._run_quick)
        self.text.setPlainText("点击按钮运行探针。批量探针需约 30 秒。")

    def _run(self, selected):
        self.btn_all.setEnabled(False)
        self.btn_quick.setEnabled(False)
        self.text.setPlainText("运行中…")
        QCoreApplication.processEvents()
        try:
            report = run_all(selected)
            self.text.setPlainText(format_report(report))
        finally:
            self.btn_all.setEnabled(True)
            self.btn_quick.setEnabled(True)

    def _run_all(self):
        self._run(None)

    def _run_quick(self):
        self._run(["compression", "roundtrip", "resources", "units", "thumbnail"])
