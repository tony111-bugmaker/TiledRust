"""重生成「等概率随机事件」示例：示例地图 .tmx + 触发器节点图（点阵）PNG。

运行：``.venv\\Scripts\\python.exe examples\\make_demo.py``

节点图直接用编辑器自己的 ``TriggerGraphDock`` 场景渲染，不是手画的示意图；
渲染时按 2 倍输出，节点文字清晰，点阵背景仍由 ``GraphScene.drawBackground`` 绘制。
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

from rwmap.document.layer import TileLayer
from rwmap.io.tmx_reader import read_document
from rwmap.io.tmx_writer import create_blank_document
from rwmap.rw.random_event import RandomEventSpec, insert_random_event
from rwmap.rw.version import active_profile
from rwmap.widgets.trigger_graph_dock import NODE_H, NODE_W, TriggerGraphDock

EXAMPLES = ROOT / "examples"
EXAMPLES.mkdir(exist_ok=True)
TMX = EXAMPLES / "随机事件_重型坦克.tmx"
PNG_GRAPH = EXAMPLES / "随机事件_点阵图.png"
PNG_WINDOW = EXAMPLES / "随机事件_节点图窗口.png"

SPEC = RandomEventSpec(
    name="坦克事件",
    rect=(320, 240, 160, 160),      # 指定矩形：8×8 格
    roll_rect=(0, 0, 20, 20),      # 抽签区放地图左上角
    unit_type="heavyTank",
    team="0",
    branch_count=2,                # 50 / 50
)


def build_map():
    doc = create_blank_document(active_profile(), 40, 30, 20, 20, path=TMX)
    doc.layers = [TileLayer(name="Ground", width=40, height=30,
                            cells=[0] * (40 * 30))]
    insert_random_event(doc, SPEC)
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

    # 场景 2 倍渲染：只取节点包围盒，画布不留大片空白。
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
    dock.scene.render(
        painter, QRectF(0, 0, img.width(), img.height()), bounds)
    painter.end()
    img.save(str(PNG_GRAPH))
    dock.grab().save(str(PNG_WINDOW))
    print(f"nodes={len(dock.data.nodes)} edges={len(dock.data.edges)} "
          f"live={len(dock.node_items)}")
    return dock


if __name__ == "__main__":
    build_map()
    render_graph()
    print(TMX)
    print(PNG_GRAPH)
    print(PNG_WINDOW)
