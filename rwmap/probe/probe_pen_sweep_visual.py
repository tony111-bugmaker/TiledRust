"""可视化验收：同一条钢笔线 + 同一个混合图章，
上排 = 第十八轮旧行为（跳过非地形标记 → 中间一段硬切），
下排 = 第十九轮新行为（正反双向补齐 → 过渡完整）。

旧行为只在内存里 monkeypatch `_pen_repair_markers`，不改动源码。
用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_pen_sweep_visual
输出： logs/钢笔过渡_双向补齐对比.png
"""
from __future__ import annotations

import os
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

COL, ROW, N = 32, 32, 8
OUT = Path("logs/钢笔过渡_双向补齐对比.png")
STAMP = [[126, 130, 813], [130, 126, 820], [186, 126, 126]]


def _events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def _old_repair(self, final, curve_cells):
    """第十八轮行为：归一失败的格直接跳过（在 set 层挖洞）。"""
    from rwmap.rw.automapping import normalize_marker_gid
    normalized, skipped = {}, 0
    for key, raw in final.items():
        gid = normalize_marker_gid(self.state.doc, raw)
        if gid < 0:
            skipped += 1
            continue
        normalized[key] = gid & 0xFFFFFFFF
    return normalized, 0, skipped


def main():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from PIL import Image, ImageDraw, ImageFont
    from rwmap.app.main_window import MainWindow
    from rwmap.probe.probe_pen_automap_visual import _render

    w = MainWindow(auto_open=False)
    sample = Path(w.state.profile.sample_map)
    cases = [
        ("旧行为：跳过过渡瓦片 → 中间一段边界硬切", True),
        ("新行为：正反双向补齐标记 → 过渡完整", False),
    ]
    shots = []
    for i, (label, old) in enumerate(cases):
        w._open(sample)
        _events()
        w.act_automap_while.setChecked(False)
        w.act_automap_while.setChecked(True)
        _events()
        if "_pen_repair_markers" in w.__dict__:
            del w._pen_repair_markers
        if old:
            w._pen_repair_markers = types.MethodType(_old_repair, w)

        doc = w.state.doc
        w._on_gid(126)
        w.state.stamp = STAMP
        w._set_object_tool("pen")
        tw, th = doc.tile_w, doc.tile_h
        y = (ROW + 0.5) * th
        x0, x1 = (COL + 0.5) * tw, (COL + (N - 0.5)) * tw
        for x in (x0, x1):
            w.view.objectToolRightPressed.emit(x, y)
            w.view.objectToolRightReleased.emit(x, y)
        w.view.objectToolPressed.emit(x0, y + 1000.0)
        w.view.objectToolReleased.emit(x0, y + 1000.0)
        _events()
        tmp = Path(f"logs/_sweep_cmp_{i}.png")
        _render(w, doc, tmp)
        shots.append((label, tmp))
        print(f"{label}: {w.statusBar().currentMessage()[:70]}")

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
    except OSError:
        font = ImageFont.load_default()
    imgs = [(label, Image.open(p).convert("RGB")) for label, p in shots]
    width = max(im.width for _, im in imgs)
    height = sum(im.height + 34 for _, im in imgs)
    out = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(out)
    top = 0
    for label, im in imgs:
        draw.text((10, top + 6), label, fill=(25, 25, 25), font=font)
        top += 34
        out.paste(im, (0, top))
        top += im.height
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(OUT))
    for _, p in shots:
        p.unlink(missing_ok=True)
    if "_pen_repair_markers" in w.__dict__:
        del w._pen_repair_markers
    w.state.stamp = None
    print("saved:", OUT.resolve())


if __name__ == "__main__":
    main()
