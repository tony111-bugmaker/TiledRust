"""后台地图加载器：把 TMX 解析挪出 GUI 线程，避免打开大地图时窗口冻结。

设计要点
--------
* ``read_document`` 纯解析、无 Qt GUI 依赖（已核查整条 io 链），可安全在工作线程运行。
* 信号桥（QObject）在 GUI 线程构造，worker emit → queued 回 GUI 线程，和
  ``view/object_layer_item.py`` 的 ``_TileResultBridge`` 同一套已验证模式。
* 异常绝不逸出工作线程：捕获后连同 token 一起 emit，GUI 线程负责弹框与恢复。
* token 用递增整数做「陈旧结果」判别：用户在加载中又发起新加载/关标签时，
  旧任务回传的结果会被丢弃，不会覆盖新文档。
* 零 numpy / 无新增依赖；offscreen 测试可用（线程池在 offscreen 下同样工作）。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..io.tmx_reader import read_document


class MapLoadSignals(QObject):
    """跨线程信号桥。必须在 GUI 线程构造，使 queued 连接的槽在 GUI 线程执行。"""

    progress = Signal(int, str)           # (percent, message)
    succeeded = Signal(int, object)       # (token, MapDocument)
    failed = Signal(int, str)             # (token, error message)


class MapLoadRunnable(QRunnable):
    """在线程池里跑 read_document，通过 signals 回传进度/结果/异常。"""

    def __init__(self, signals: MapLoadSignals, token: int, path: Path, profile):
        super().__init__()
        self._signals = signals
        self._token = token
        self._path = path
        self._profile = profile

    def run(self):
        try:
            def report(pct: int, msg: str):
                self._signals.progress.emit(int(pct), str(msg))

            doc = read_document(self._path, self._profile, progress=report)
        except Exception as exc:  # noqa: BLE001 - 异常绝不能逸出工作线程
            self._signals.failed.emit(self._token, str(exc))
            return
        self._signals.succeeded.emit(self._token, doc)


class MapLoadController:
    """GUI 线程持有的加载控制器：管理 token、信号桥与线程池任务。

    不继承 QObject（避免多继承元类冲突）；signals 由持有者负责在 GUI 线程构造。
    """

    def __init__(self, signals: MapLoadSignals | None = None):
        self.signals = signals if signals is not None else MapLoadSignals()
        self._token = 0
        self._active = False

    def begin(self, path: Path, profile) -> int:
        """发起一次异步加载，返回本次 token。"""
        self._token += 1
        self._active = True
        task = MapLoadRunnable(self.signals, self._token, path, profile)
        QThreadPool.globalInstance().start(task)
        return self._token

    def is_current(self, token: int) -> bool:
        return self._active and token == self._token

    def finish(self, token: int):
        """仅当 token 为当前任务时结束激活态（防止陈旧结果误清）。"""
        if token == self._token:
            self._active = False

    def cancel_all(self):
        """作废所有在途任务的结果（关窗口/重开时用）。"""
        self._token += 1
        self._active = False
