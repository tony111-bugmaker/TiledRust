"""框选拖动位移量的可视化验收：真实地图 + 真实 QMouseEvent + 126% 缩放。

复现用户录屏场景：框选一块区域后，鼠标只挪 1 格（20 场景单位），中间发出
30 次 mouseMove；修复前选框会被逐帧累加、飞出屏幕，修复后只走 1 格。

运行：
    python -m rwmap.probe.probe_select_move_visual
输出：
    logs/select_move_before.png / select_move_after.png / select_move_compare.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rwmap.app.main_window import MainWindow  # noqa: E402
from rwmap.rw.version import load_profile  # noqa: E402
from rwmap.utils.paths import as_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
FONT = r"C:\Windows\Fonts\msyh.ttc"


def grab(w, name):
    path = LOGS / name
    w.grab().save(str(path))
    return path


def labeled(path, title, out):
    im = Image.open(path).convert("RGB")
    band = 40
    canvas = Image.new("RGB", (im.width, im.height + band), (32, 36, 42))
    canvas.paste(im, (0, band))
    d = ImageDraw.Draw(canvas)
    d.text((14, 10), title, font=ImageFont.truetype(FONT, 18),
           fill=(150, 205, 250))
    canvas.save(out)
    return out


def main():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(auto_open=False)
    w.resize(1402, 932)
    w.show()
    QApplication.processEvents()
    w._open(as_path(load_profile("1.15").sample_map))
    QApplication.processEvents()
    w._set_object_tool("tileselect")

    view = w.view
    cur = view.transform().m11()
    view.scale(1.26 / cur, 1.26 / cur)
    view.camera.scale = view.transform().m11()
    view.centerOn(QPointF(560.0, 520.0))
    QApplication.processEvents()
    scale = view.transform().m11()

    def px(x, y):
        return QPointF(view.mapFromScene(QPointF(float(x), float(y))))

    def press(x, y):
        view.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, px(x, y),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    def move(x, y):
        view.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, px(x, y),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))

    def release(x, y):
        view.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, px(x, y),
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

    # 框选 (400,400)-(720,640) = 16x12 格。
    press(400.0, 400.0)
    move(720.0, 640.0)
    release(720.0, 640.0)
    before_sel = w._tile_selection
    before_rect = w._sel_rect
    before_gid = w.state.current_layer.get(before_sel[0], before_sel[1])
    before = labeled(grab(w, "select_move_before.png"),
                     f"拖动前：缩放 {scale * 100:.0f}%，选区 {before_sel}，框 {before_rect}",
                     LOGS / "select_move_before_labeled.png")

    # 选区内按下，30 个 mouseMove 匀速走 20 场景单位（1 格）。
    press(560.0, 520.0)
    seen = set()
    for i in range(1, 31):
        move(560.0 + 20.0 * i / 30, 520.0)
        seen.add(w._sel_rect)
    release(580.0, 520.0)
    after_sel = w._tile_selection
    after_rect = w._sel_rect
    message = w.statusBar().currentMessage()
    after = labeled(grab(w, "select_move_after.png"),
                    f"拖动后：{message}；选区 {after_sel}，框 {after_rect}",
                    LOGS / "select_move_after_labeled.png")
    moved_gid = w.state.current_layer.get(20, 20)

    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    canvas = Image.new("RGB", (a.width * 2 + 12, a.height), (32, 36, 42))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 12, 0))
    canvas.save(LOGS / "select_move_compare.png")

    # 撤销：数据回滚的同时，选区与画布选框也要一起回到移动前的位置。
    w.state.undo_stack.undo()
    QApplication.processEvents()
    undo_sel = w._tile_selection
    undo_rect = w._sel_rect
    layer = w.state.current_layer
    undo_gid = layer.get(20, 20)
    undo = labeled(grab(w, "select_move_undo.png"),
                   f"撤销后：选区 {undo_sel}，框 {undo_rect}（回到拖动前位置）",
                   LOGS / "select_move_undo_labeled.png")
    u = Image.open(undo).convert("RGB")
    c2 = Image.new("RGB", (b.width * 2 + 12, b.height), (32, 36, 42))
    c2.paste(b, (0, 0))
    c2.paste(u, (b.width + 12, 0))
    c2.save(LOGS / "select_move_undo_compare.png")

    print("缩放:", round(scale, 4))
    print("拖动前选区:", before_sel, before_rect, "格(20,20)=", before_gid)
    print("30 次 move 期间出现过的选框:", sorted(seen))
    print("拖动后选区:", after_sel, after_rect, "格(20,20)=", moved_gid)
    print("撤销后选区:", undo_sel, undo_rect, "格(20,20)=", undo_gid)
    print("状态栏:", message)
    print("截图:", LOGS / "select_move_compare.png",
          LOGS / "select_move_undo_compare.png")

    assert before_sel == (20, 20, 16, 12), before_sel
    assert after_sel == (21, 20, 16, 12), after_sel
    assert before_rect == (400, 400, 720, 640), before_rect
    assert after_rect == (420, 400, 740, 640), after_rect
    assert len(seen) <= 2, f"选框出现中间态，说明仍在累加：{seen}"
    assert undo_sel == before_sel, (undo_sel, before_sel)
    assert undo_rect == before_rect, (undo_rect, before_rect)
    assert undo_gid == before_gid, (undo_gid, before_gid)
    print("PASS：鼠标只挪 1 格；撤销后选框与数据都回到移动前。")


if __name__ == "__main__":
    main()
