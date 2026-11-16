"""把用户那份「检测重型坦克 → 等概率三选一 → 删除重型坦克」的触发器，
改写成*真实可运行*的 Rusted Warfare 语法，并落成一例可打开的 .tmx + 点阵图 PNG。

和原 XML 的差别（都是「游戏不认」→「游戏认」的改写）：
  - type="basic" + spawnUnits  →  type="unitAdd"（basic 只管 globalMessage，不认 spawnUnits）
  - spawnUnits="if(rnd(...))then..else.."  →  builder(0.3333,max1),tank(0.5,max1),scout(1,max1)
        （RW 没有 rnd/if 脚本，等概率靠 spawnChance 链式抽签）
  - despawnUnits / despawnTriggerUnit  →  单独的 type="unitRemove" 对象（RW 没有这两个属性）
  - alsoActivate 引用 name  →  引用对象上的 id 属性值（RW 的连线规则）
  - 防止重复触发  →  gate 上加 resetActivationAfter（despawnTriggerUnit 不存在）

运行：``.venv\\Scripts\\python.exe examples\\make_three_way_demo.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from rwmap.document.layer import ObjectLayer, TileLayer
from rwmap.document.map_object import MapObject
from rwmap.document.properties import PropertyBag
from rwmap.io.tmx_reader import read_document
from rwmap.io.tmx_writer import create_blank_document
from rwmap.rw.version import active_profile
from rwmap.widgets.trigger_graph_dock import NODE_H, NODE_W, TriggerGraphDock

EXAMPLES = ROOT / "examples"
EXAMPLES.mkdir(exist_ok=True)
TMX = EXAMPLES / "随机事件_重型坦克_三选一.tmx"
PNG_GRAPH = EXAMPLES / "随机事件_三选一点阵图.png"
PNG_WINDOW = EXAMPLES / "随机事件_三选一节点图窗口.png"

GATE_RECT = (400, 300, 120, 120)   # 触发矩形（与原 XML 一致）


def _mk(object_id, type_, name, id_prop, rect, props):
    pb = PropertyBag()
    pb.set("id", id_prop)
    for k, v in props.items():
        pb.set(k, v)
    return MapObject(object_id=object_id, type_=type_, name=name,
                     x=rect[0], y=rect[1], width=rect[2], height=rect[3],
                     properties=pb)


def build_objects():
    # ① 闸门：检测矩形内的重型坦克
    gate = _mk(101, "unitDetect", "heavyTank_gate", "gate_heavy", GATE_RECT, {
        "unitType": "heavyTank",
        "minUnits": "1",
        "alsoActivate": "re_roll",
        "resetActivationAfter": "1s",   # 防抖：触发后 1 秒才允许再触发
    })
    # ② 等概率三选一：builder / tank / scout 各 1/3
    spawn = _mk(102, "unitAdd", "random_roll", "re_roll", (0, 0, 20, 20), {
        "activatedBy": "gate_heavy",
        "team": "0",
        "spawnUnits": ("builder(spawnChance=0.3333,maxSpawnLimit=1),"
                       "tank(spawnChance=0.5,maxSpawnLimit=1),"
                       "scout(spawnChance=1,maxSpawnLimit=1)"),
        "alsoActivate": "re_remove",
    })
    # ③ 删除触发矩形内的重型坦克（unitRemove 只能按矩形+队伍清，无法指定兵种）
    remove = _mk(103, "unitRemove", "remove_heavy", "re_remove", GATE_RECT, {
        "activatedBy": "re_roll",
        "team": "0",
    })
    return [gate, spawn, remove]


def build_map():
    doc = create_blank_document(active_profile(), 40, 30, 20, 20, path=TMX)
    doc.layers = [
        TileLayer(name="Ground", width=40, height=30, cells=[0] * (40 * 30)),
        ObjectLayer(name="Triggers", kind="object", objects=build_objects()),
    ]
    doc.save(TMX)
    return doc


def render_graph():
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei", 9))
    doc = read_document(TMX, active_profile())
    dock = TriggerGraphDock()
    dock.set_document(doc)
    dock.resize(1440, 900)
    dock.show()
    QApplication.processEvents()
    dock.refresh(fit=False)
    dock.auto_layout()
    dock.refresh(fit=True)
    QApplication.processEvents()
    dock.fit_view()
    QApplication.processEvents()

    bounds = QRectF()
    for node in dock.data.nodes.values():
        bounds |= QRectF(node.x, node.y, NODE_W, NODE_H)
    bounds = bounds.adjusted(-55, -55, 55, 55)
    scale = 2.0
    img = QImage(int(bounds.width() * scale), int(bounds.height() * scale),
                 QImage.Format_ARGB32)
    img.fill(QColor("#1e1e1e"))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    dock.scene.render(painter, QRectF(0, 0, img.width(), img.height()), bounds)
    painter.end()
    img.save(str(PNG_GRAPH))
    dock.grab().save(str(PNG_WINDOW))

    # 校验：连线是否真的成对出现
    links = [(e.src, e.dst, e.kind) for e in dock.data.edges]
    print(f"nodes={len(dock.data.nodes)} edges={len(dock.data.edges)}")
    print("links:", links)
    return dock


if __name__ == "__main__":
    build_map()
    render_graph()
    print(TMX)
    print(PNG_GRAPH)
    print(PNG_WINDOW)
