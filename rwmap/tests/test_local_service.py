"""本地 AI 控制服务回归：HTTP 鉴权、只读查询、编辑进撤销栈、批量导入。"""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.document.layer import ObjectLayer, TileLayer  # noqa: E402
from rwmap.document.map_document import MapDocument  # noqa: E402
from rwmap.document.map_object import MapObject  # noqa: E402

_app = QApplication.instance() or QApplication([])


class LocalServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = MainWindow(auto_open=False)
        self.w.resize(1000, 700)
        self.app.processEvents()
        svc = self.w._local_service
        if not svc.is_running():
            svc.start()
        self.app.processEvents()
        self.cfg = svc.config
        self.base = f"http://{self.cfg['host']}:{self.cfg['port']}"
        self.token = self.cfg["token"]

        tl = TileLayer(width=20, height=20, name="Ground",
                       cells=[0] * 400, readonly=False)
        ol = ObjectLayer(name="Objects", objects=[
            MapObject(object_id=1, name="玩家", type_="unitAdd",
                      x=0, y=0, width=20, height=20)])
        doc = MapDocument(width=20, height=20, tile_w=20, tile_h=20,
                          layers=[tl, ol], tilesets=[])
        ctx = self.w._new_or_reuse_context()
        ctx.open_document(doc)
        self.w._attach_context(ctx)
        self.w._refresh_document()
        self.app.processEvents()
        self.doc = doc

    def tearDown(self):
        self.w.close()
        self.app.processEvents()

    # ---- HTTP 辅助：后台发请求 + 主线程 pump 事件循环 ----
    def _request(self, path, payload=None, token="__default__"):
        if token == "__default__":
            token = self.token
        url = self.base + path
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-Auth-Token"] = token
        req = urllib.request.Request(url, data=data, headers=headers)
        box = {}

        def work():
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    box["status"] = resp.status
                    box["body"] = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                box["status"] = exc.code
                try:
                    box["body"] = json.loads(exc.read().decode("utf-8"))
                except Exception:
                    box["body"] = {}
            except Exception as exc:
                box["status"] = None
                box["body"] = {"error": str(exc)}

        t = threading.Thread(target=work)
        t.start()
        deadline = time.time() + 6
        while t.is_alive() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        t.join(1)
        return box.get("status"), box.get("body", {})

    def _action(self, action, **kwargs):
        return self._request("/action", {"action": action, **kwargs})

    # ---- 鉴权 ----
    def test_ping_no_token(self):
        status, body = self._request("/ping", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_bad_and_missing_token_rejected(self):
        for token in ("wrong", None):
            status, body = self._request("/summary", token=token)
            self.assertEqual(status, 401)
            self.assertFalse(body["ok"])

    def test_help_lists_actions(self):
        status, body = self._request("/help")
        self.assertEqual(status, 200)
        actions = {a["action"] for a in body["actions"]}
        self.assertIn("draw_cells", actions)
        self.assertIn("apply_ops", actions)

    # ---- 只读查询 ----
    def test_summary_and_layers(self):
        status, body = self._request("/summary")
        self.assertEqual(status, 200)
        self.assertEqual(body["summary"]["size"], "20x20")
        self.assertEqual(body["summary"]["objects"], 1)
        status, body = self._request("/layers")
        self.assertEqual(status, 200)
        names = {l["name"] for l in body["layers"]}
        self.assertEqual(names, {"Ground", "Objects"})

    def test_objects_triggers_and_cells(self):
        status, body = self._request("/objects?layer=Objects")
        self.assertEqual(status, 200)
        self.assertEqual(body["objects"][0]["name"], "玩家")
        status, body = self._request("/triggers")
        self.assertEqual(status, 200)
        self.assertEqual(body["triggers"][0]["type"], "unitAdd")
        status, body = self._request("/cells?layer=Ground&x=0&y=0&w=2&h=2")
        self.assertEqual(status, 200)
        self.assertEqual(body["cells"], [[0, 0], [0, 0]])

    # ---- 编辑 + 撤销 ----
    def test_draw_cells_and_undo(self):
        status, body = self._action("draw_cells", layer="Ground",
                                    cells=[[2, 3, 5], [3, 3, 5]])
        self.assertEqual(status, 200)
        self.assertEqual(body["changed"], 2)
        ground = self.doc.layer_by_name("Ground")
        self.assertEqual(ground.get(2, 3), 5)

        status, body = self._action("undo")
        self.assertEqual(status, 200)
        self.assertEqual(ground.get(2, 3), 0, "AI 绘制应能 Ctrl+Z 撤销")

    def test_add_edit_delete_object(self):
        status, body = self._action("add_object", layer="Objects", x=40, y=40,
                                    width=20, height=20, name="敌军", type="unitAdd")
        self.assertEqual(status, 200)
        oid = body["object_id"]
        layer = self.doc.layer_by_name("Objects")
        self.assertEqual(len(layer.objects), 2)

        status, body = self._action("edit_object", layer="Objects", object_id=oid,
                                    changes={"name": "敌军2", "hp": "100"})
        self.assertEqual(status, 200)
        obj = next(o for o in layer.objects if o.object_id == oid)
        self.assertEqual(obj.name, "敌军2")
        self.assertEqual(obj.properties.get("hp"), "100")

        status, body = self._action("delete_object", layer="Objects", object_id=oid)
        self.assertEqual(status, 200)
        self.assertEqual(len(layer.objects), 1)

    # ---- 批量导入 ----
    def test_apply_ops_batch(self):
        ops = [
            {"action": "set_gid", "gid": 9},
            {"action": "draw_cells", "layer": "Ground", "cells": [[1, 1, 9]]},
            {"action": "set_tool", "tool": "select"},
        ]
        status, body = self._request("/summary/import", {"ops": ops})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["results"]), 3)
        self.assertTrue(all(r["ok"] for r in body["results"]))
        self.assertEqual(self.doc.layer_by_name("Ground").get(1, 1), 9)
        self.assertEqual(self.w._object_tool, "select")


if __name__ == "__main__":
    unittest.main()
