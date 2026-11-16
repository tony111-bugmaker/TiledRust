"""可视化验收：渲染同一区域，对比「关闭自动映射(硬切)」与「开启+非首格瓦片(有过渡)」。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_pen_automap_visual
输出： logs/钢笔过渡_验收对比.png
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
OUT = Path("logs/钢笔过渡_验收对比.png")
COL, ROW, N = 32, 32, 8
PAD_X, PAD_Y = 6, 5


def _events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def _render(w, doc, path):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    tw, th = doc.tile_w, doc.tile_h
    x0, y0 = (COL - PAD_X) * tw, (ROW - PAD_Y) * th
    w_px, h_px = (N + PAD_X * 2) * tw, (PAD_Y * 2 + 1) * th
    img = QImage(w_px, h_px, QImage.Format_RGB32)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    w.scene.render(p, QRectF(0, 0, w_px, h_px),
                   QRectF(x0, y0, w_px, h_px))
    p.end()
    img.save(str(path))
    return path


def _pen_line(w, doc, gid):
    tw, th = doc.tile_w, doc.tile_h
    w._on_gid(gid)
    w.state.stamp = None
    w._set_object_tool("pen")
    y = (ROW + 0.5) * th
    x0, x1 = (COL + 0.5) * tw, (COL + (N - 0.5)) * tw
    for x in (x0, x1):
        w.view.objectToolRightPressed.emit(x, y)
        w.view.objectToolRightReleased.emit(x, y)
    w.view.objectToolPressed.emit(x0, y + 1000.0)
    w.view.objectToolReleased.emit(x0, y + 1000.0)
    _events()


def main():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    from PIL import Image, ImageDraw, ImageFont

    w = MainWindow(auto_open=False)
    sample = Path(w.state.profile.sample_map)
    panels = []
    for label, on, gid in (("关闭自动映射：硬切、无过渡", False, 126),
                           ("开启 + 选 Long Grass 第5格(130)：有岸线过渡", True, 130)):
        w._open(sample)
        _events()
        w.act_automap_while.setChecked(False)
        w.act_automap_while.setChecked(on)
        _events()
        doc = w.state.doc
        if not on:
            w.state.current_layer = doc.layer_by_name("Ground")
            w.scene.current_layer = w.state.current_layer
            # 让两行「落笔前」显示条件一致：开关开启时会把 set 层设为半透明可见，
            # 关闭场景也照做，避免把显示差异误看成地形差异。
            s = doc.layer_by_name("set")
            s.visible = True
            s.opacity = 0.35
            w.scene.set_layer_visible(s, True)
            w.scene.set_layer_opacity(s, 0.35)
        before = _render(w, doc, Path(f"logs/_pen_cmp_{len(panels)}a.png"))
        _pen_line(w, doc, gid)
        after = _render(w, doc, Path(f"logs/_pen_cmp_{len(panels)}b.png"))
        panels.append((label, before, after))
        print(label, "| status:", w.statusBar().currentMessage())
        w._pen_cancel()
        w.state.undo_stack.clear()

    # 拼 2 行 × 2 列：每行「落笔前 / 落笔后」
    scale = 3
    imgs = [[Image.open(p).convert("RGB") for p in (b, a)] for _, b, a in panels]
    pw = max(im.width for row in imgs for im in row) * scale
    ph = max(im.height for row in imgs for im in row) * scale
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 22)
    except OSError:
        font = ImageFont.load_default()
    head = 44
    canvas = Image.new("RGB", (pw * 2 + 30, (ph + head) * 2 + 30), "#1b1b1b")
    d = ImageDraw.Draw(canvas)
    for i, (label, _b, _a) in enumerate(panels):
        y0 = 10 + i * (ph + head + 10)
        d.text((10, y0), label, fill="#f0f0f0", font=font)
        for j, im in enumerate(imgs[i]):
            up = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
            canvas.paste(up, (10 + j * (pw + 10), y0 + head))
            d.text((14 + j * (pw + 10), y0 + head + 4),
                   "落笔前" if j == 0 else "落笔后",
                   fill="#ffffff", font=font)
    OUT.parent.mkdir(exist_ok=True)
    canvas.save(OUT)
    for i in range(len(panels)):
        Path(f"logs/_pen_cmp_{i}a.png").unlink(missing_ok=True)
        Path(f"logs/_pen_cmp_{i}b.png").unlink(missing_ok=True)
    print("saved:", OUT.resolve(), canvas.size)


if __name__ == "__main__":
    main()
