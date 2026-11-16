"""验收探针：正反双向补齐后，「混了过渡瓦片的图章」与「纯基础地形图章」
沿同一条钢笔线生成的过渡瓦片数应当一致（旧行为会明显少一截）。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_pen_sweep_verify
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def main():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow
    from rwmap.probe.probe_pen_automap import (
        _finish, _neighbour_transitions, _pen_path)

    w = MainWindow(auto_open=False)
    sample = Path(w.state.profile.sample_map)
    tw = th = 20
    col, row = 32, 32
    p0 = ((col + 0.5) * tw, (row + 0.5) * th)
    p1 = ((col + 7.5) * tw, (row + 0.5) * th)
    cells = [(col + i, row) for i in range(8)]

    cases = [
        ("混合过渡图章", [[126, 130, 813], [130, 126, 820], [186, 126, 126]]),
        ("纯基础图章  ", [[126, 126, 126], [126, 126, 126], [186, 126, 126]]),
    ]
    results = {}
    for label, stamp in cases:
        w._open(sample)
        _events()
        w.act_automap_while.setChecked(False)
        w.act_automap_while.setChecked(True)
        _events()
        w._on_gid(126)
        w.state.stamp = stamp
        w._set_object_tool("pen")
        before = _neighbour_transitions(w.state.doc, cells)
        _pen_path(w, p0, p1)
        _finish(w, p0)
        after = _neighbour_transitions(w.state.doc, cells)
        results[label.strip()] = after - before
        print(f"{label} 过渡增量={after - before:3d} "
              f"(前{before}→后{after})  {w.statusBar().currentMessage()[:70]}")

    a, b = results.values()
    print(f"差值={abs(a - b)}  -> "
          f"{'一致，双向补齐生效' if a == b else '仍不一致，需继续排查'}")
    w.state.stamp = None


if __name__ == "__main__":
    main()
