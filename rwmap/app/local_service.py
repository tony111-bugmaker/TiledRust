"""本地 AI 控制服务：让外部程序（AI）实时读写运行中的地图编辑器。

设计要点
========
* **零依赖**：标准库 ``http.server.ThreadingHTTPServer``，不引入 flask/fastapi。
* **仅本机**：只绑定 ``127.0.0.1``，所有请求（除 /ping）需带 ``X-Auth-Token``。
* **线程安全**：HTTP 线程绝不碰 doc / scene / 任何 Qt 对象。请求解析后通过
  ``request_received`` 信号（Qt.QueuedConnection）投递到 GUI 线程，GUI 线程执行
  完把结果塞回 ``queue.Queue`` 并 ``Event.set()``，HTTP 线程阻塞等待后序列化返回。
  这与 ``map_loader.py`` 已验证的「信号桥在 GUI 线程构造 + worker emit → queued 回
  GUI + 异常保护」模式一致。
* **进撤销栈**：所有编辑动作都走 MainWindow 现有的命令路径（DrawCommand /
  ObjectCommand / QUndoStack），AI 的操作能 Ctrl+Z 撤回。

配置（config/app_config.json 顶层 ``local_service``）：
    {"enabled": true, "host": "127.0.0.1", "port": 8765, "token": ""}

API
===
    GET  /ping                       连通性（不需要 token）
    GET  /help                       所有 action 及参数
    GET  /summary                    地图结构化摘要（供 AI 分析）
    GET  /state                      编辑器当前状态
    GET  /layers                     图层列表
    GET  /objects?layer=名字          某对象层的对象（含触发器）
    GET  /cells?layer=名字&...        瓦片层某区域 gid（支持矩形）
    POST /action                     {"action": "...", ...参数} 执行编辑操作
    GET  /config                     返回 host/port（不返回 token）

所有需鉴权的请求带 header：``X-Auth-Token: <token>``。
"""
from __future__ import annotations

import json
import queue
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, QThread, Signal

from ..utils.paths import CONFIG_DIR, load_json, save_json

DEFAULT_PORT = 8765
CONFIG_KEY = "local_service"
TOKEN_FILE = CONFIG_DIR / "local_service_token.txt"


def _load_config() -> dict:
    cfg = load_json(CONFIG_DIR / "app_config.json", {}) or {}
    svc = dict(cfg.get(CONFIG_KEY, {}) or {})
    changed = False
    if "enabled" not in svc:
        svc["enabled"] = True
        changed = True
    if "host" not in svc:
        svc["host"] = "127.0.0.1"
        changed = True
    if "port" not in svc:
        svc["port"] = DEFAULT_PORT
        changed = True
    if not svc.get("token"):
        svc["token"] = _load_or_create_token()
        changed = True
    if changed:
        # 首次运行补写配置，让用户能在 app_config.json 里看到/修改端口和开关
        full = dict(cfg)
        full[CONFIG_KEY] = svc
        save_json(CONFIG_DIR / "app_config.json", full)
    return svc


def _load_or_create_token() -> str:
    """token 单独存一个文件，避免污染 app_config.json；随软件首次启动生成。"""
    try:
        if TOKEN_FILE.exists():
            text = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text
    except OSError:
        pass
    token = secrets.token_hex(16)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token, encoding="utf-8")
    except OSError:
        pass
    return token


def save_service_config(svc: dict):
    """把 local_service 写回 app_config.json（保留其他键）。"""
    path = CONFIG_DIR / "app_config.json"
    cfg = load_json(path, {}) or {}
    cfg[CONFIG_KEY] = svc
    save_json(path, cfg)


# ---------------------------------------------------------------------------
# 供 HTTP 线程使用的请求桥：只发信号 + 等结果，绝不碰 Qt 对象
# ---------------------------------------------------------------------------
class _RequestBridge:
    def __init__(self, service: "LocalControlService"):
        self.service = service

    def dispatch(self, action: str, payload: dict, timeout: float = 30.0) -> dict:
        """把请求投递到 GUI 线程执行，阻塞等结果。异常转成 {ok:false} 返回。"""
        req_id = self.service._next_id()
        result_q: queue.Queue = queue.Queue(maxsize=1)
        done = threading.Event()
        self.service._pending[req_id] = (result_q, done)
        try:
            self.service.request_received.emit(req_id, action, payload)
            if not done.wait(timeout):
                return {"ok": False, "error": f"处理超时（{timeout}s）"}
            return result_q.get_nowait()
        finally:
            self.service._pending.pop(req_id, None)


# ---------------------------------------------------------------------------
# HTTP handler（跑在 QThread 的 serve_forever 里）
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    # 类变量，start() 时注入
    service: "LocalControlService" = None  # type: ignore[assignment]

    def log_message(self, fmt, *args):
        # 静默：避免每个请求刷屏（需要调试时可打开）
        pass

    def _json(self, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = self.headers.get("X-Auth-Token", "")
        expect = self.service.config.get("token", "")
        return bool(expect) and secrets.compare_digest(token, expect)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path == "/ping":
            self._json(200, {"ok": True, "service": "rwmap-local", "version": 1})
            return
        if not self._authorized():
            self._json(401, {"ok": False, "error": "无效或缺失 X-Auth-Token"})
            return

        if path == "/config":
            self._json(200, {"ok": True, "host": self.service.config["host"],
                             "port": self.service.config["port"]})
            return
        if path == "/help":
            self._json(200, {"ok": True, "actions": ACTION_HELP})
            return

        # 其余 GET 统一走 GUI 线程查询
        action = {
            "/summary": "get_summary",
            "/state": "get_state",
            "/layers": "get_layers",
            "/objects": "get_objects",
            "/cells": "get_cells",
            "/triggers": "get_triggers",
            "/screen": "get_screen",
        }.get(path)
        if action is None:
            self._json(404, {"ok": False, "error": f"未知端点 {path}"})
            return
        result = self.service.bridge.dispatch(action, query)
        self._json(200 if result.get("ok") else 400, result)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._authorized():
            self._json(401, {"ok": False, "error": "无效或缺失 X-Auth-Token"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
        except Exception as exc:
            self._json(400, {"ok": False, "error": f"请求体解析失败: {exc}"})
            return

        if path == "/action":
            action = payload.pop("action", None)
            if not action or not isinstance(action, str):
                self._json(400, {"ok": False, "error": "缺少 action 字段"})
                return
            result = self.service.bridge.dispatch(action, payload)
            self._json(200 if result.get("ok") else 400, result)
        elif path == "/summary/import":
            # 导入 AI 给的编辑指令序列，逐条在主线程执行（进撤销栈）
            ops = payload.get("ops", [])
            result = self.service.bridge.dispatch("apply_ops", {"ops": ops})
            self._json(200 if result.get("ok") else 400, result)
        else:
            self._json(404, {"ok": False, "error": f"未知端点 {path}"})


# ---------------------------------------------------------------------------
# 服务器线程：只管 serve_forever，不碰 Qt
# ---------------------------------------------------------------------------
class _ServerThread(QThread):
    def __init__(self, server: ThreadingHTTPServer):
        super().__init__()
        self._server = server

    def run(self):
        try:
            self._server.serve_forever(poll_interval=0.2)
        except Exception:
            traceback.print_exc()

    def stop(self):
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        self.wait(3000)


class LocalControlService(QObject):
    """本地控制服务；信号桥必须在 GUI 线程构造。

    ``request_received`` 用 QueuedConnection 连到 MainWindow 的分发槽，
    保证编辑操作在 GUI 线程执行。
    """
    request_received = Signal(int, str, object)   # (req_id, action, payload)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = _load_config()
        self._pending: dict[int, tuple[queue.Queue, threading.Event]] = {}
        self._req_seq = 0
        self._seq_lock = threading.Lock()
        self.bridge = _RequestBridge(self)
        self._thread: _ServerThread | None = None
        self._server: ThreadingHTTPServer | None = None

    def _next_id(self) -> int:
        with self._seq_lock:
            self._req_seq += 1
            return self._req_seq

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self) -> bool:
        if self.is_running():
            return True
        host = self.config["host"]
        port = int(self.config["port"])
        try:
            handler = type("_BoundHandler", (_Handler,), {"service": self})
            self._server = ThreadingHTTPServer((host, port), handler)
            self._server.daemon_threads = True
        except OSError as exc:
            self.last_error = f"端口 {host}:{port} 监听失败: {exc}"
            return False
        self._thread = _ServerThread(self._server)
        self._thread.start()
        self.last_error = ""
        return True

    def stop(self):
        if self._thread is not None:
            self._thread.stop()
            self._thread = None
        self._server = None

    def resolve(self, req_id: int, result: dict):
        """GUI 线程调用：把结果投递回等待中的 HTTP 线程。"""
        entry = self._pending.get(req_id)
        if entry is None:
            return
        result_q, done = entry
        try:
            result_q.put_nowait(result)
        except queue.Full:
            pass
        done.set()


ACTION_HELP = [
    # 只读
    {"action": "get_summary", "method": "GET /summary", "desc": "地图结构化摘要：尺寸/图层/对象/触发器/图集"},
    {"action": "get_state", "method": "GET /state", "desc": "当前工具、图层、笔刷 gid、缩放"},
    {"action": "get_layers", "method": "GET /layers", "desc": "图层列表（名称/类型/可见/锁定）"},
    {"action": "get_objects", "method": "GET /objects?layer=名字", "desc": "某对象层的对象与属性"},
    {"action": "get_triggers", "method": "GET /triggers", "desc": "所有触发器（类型/属性/链接）"},
    {"action": "get_cells", "method": "GET /cells?layer=名字&x=0&y=0&w=10&h=10", "desc": "瓦片层区域 gid"},
    {"action": "get_screen", "method": "GET /screen", "desc": "编辑器窗口截图（base64 PNG）"},
    # 编辑（进撤销栈）
    {"action": "open_map", "method": "POST /action", "params": "path: 地图文件路径", "desc": "打开地图"},
    {"action": "save_map", "method": "POST /action", "desc": "保存当前地图"},
    {"action": "set_tool", "method": "POST /action", "params": "tool: brush/fill/select/ruler/pen/...", "desc": "切换工具"},
    {"action": "set_gid", "method": "POST /action", "params": "gid: 笔刷图块 id", "desc": "设置笔刷"},
    {"action": "draw_cells", "method": "POST /action", "params": "layer, cells: [[col,row,gid],...]", "desc": "批量绘制瓦片（一条撤销）"},
    {"action": "fill", "method": "POST /action", "params": "layer, col, row", "desc": "洪水填充（当前笔刷）"},
    {"action": "add_object", "method": "POST /action", "params": "layer, x, y, width, height, type, name, properties", "desc": "添加对象/触发器"},
    {"action": "edit_object", "method": "POST /action", "params": "layer, object_id, changes: {属性:值}", "desc": "修改对象属性"},
    {"action": "delete_object", "method": "POST /action", "params": "layer, object_id", "desc": "删除对象"},
    {"action": "set_map_props", "method": "POST /action", "params": "changes: {属性:值}", "desc": "修改地图属性"},
    {"action": "autocrop", "method": "POST /action", "desc": "自动裁剪到瓦片内容"},
    {"action": "transform", "method": "POST /action", "params": "op: flip_h/flip_v/rot_cw/rot_ccw", "desc": "翻转/旋转地图"},
    {"action": "undo", "method": "POST /action", "desc": "撤销"},
    {"action": "redo", "method": "POST /action", "desc": "重做"},
    {"action": "goto", "method": "POST /action", "params": "col, row", "desc": "视图定位到格子"},
    {"action": "apply_ops", "method": "POST /summary/import", "params": "ops: [action 列表]", "desc": "批量执行指令（AI 改完导入）"},
]


__all__ = [
    "LocalControlService", "ACTION_HELP", "DEFAULT_PORT",
    "_load_config", "_load_or_create_token", "save_service_config",
]
