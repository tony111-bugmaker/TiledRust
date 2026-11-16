"""探针：找出「钢笔落笔后完全没有水陆过渡」的触发条件。

对比同一条线在不同状态下的过渡瓦片增量（落笔后 - 落笔前，统计线周边 ±3）。
用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_pen_automap
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def _neighbour_transitions(doc, cells, pad=3):
    from rwmap.rw.automapping import _base_gids
    g = doc.layer_by_name("Ground")
    base = _base_gids(doc)
    near = set()
    for c, r in cells:
        for dy in range(-pad, pad + 1):
            for dx in range(-pad, pad + 1):
                x, y = c + dx, r + dy
                if g.in_bounds(x, y):
                    near.add((x, y))
    return sum(1 for x, y in near
               if (int(g.get(x, y)) & 0xFFFFFFFF) not in base
               and (int(g.get(x, y)) & 0xFFFFFFFF) != 0)


def _pen_path(w, p0, p1, handle=None):
    """右键放两个锚点；handle 非空时第二个锚点用右键拖动出对称柄。"""
    w.view.objectToolRightPressed.emit(*p0)
    w.view.objectToolRightReleased.emit(*p0)
    if handle:
        w.view.objectToolRightPressed.emit(*p1)
        w.view.objectToolRightMoved.emit(*handle)
        w.view.objectToolRightReleased.emit(*handle)
    else:
        w.view.objectToolRightPressed.emit(*p1)
        w.view.objectToolRightReleased.emit(*p1)


def _finish(w, p0):
    w.view.objectToolPressed.emit(p0[0], p0[1] + 1000.0)
    w.view.objectToolReleased.emit(p0[0], p0[1] + 1000.0)
    _events()


def main():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from rwmap.app.main_window import MainWindow

    w = MainWindow(auto_open=False)
    sample = Path(w.state.profile.sample_map)
    w._open(sample)
    _events()
    doc = w.state.doc
    tw, th = doc.tile_w, doc.tile_h
    col, row = 32, 32
    p0 = ((col + 0.5) * tw, (row + 0.5) * th)
    p1 = ((col + 7.5) * tw, (row + 0.5) * th)
    cells = [(col + i, row) for i in range(8)]
    setl0 = doc.layer_by_name("set")
    print("map", doc.width, doc.height, "tile", tw, th)
    print("set 层起点值:", [setl0.get(col + i, row) for i in range(8)])
    for ts in doc.tilesets:
        if ts.firstgid < 145:
            print("  tileset", ts.firstgid, ts.last_gid, repr(ts.name), ts.count)
    print("  过渡图集:", [(ts.firstgid, ts.name) for ts in doc.tilesets
                    if " - " in (ts.name or "")][:12])

    scenarios = [
        ("A 开/层set/gid126", dict(on=True, layer="set", gid=126)),
        ("B 开/层set/gid130(非基础)", dict(on=True, layer="set", gid=130)),
        ("C 开/层Ground/gid126", dict(on=True, layer="Ground", gid=126)),
        ("D 关/层set/gid126", dict(on=False, layer="set", gid=126)),
        ("E 开/层set/3x3图章(Ground瓦片)", dict(on=True, layer="set", gid=126, stamp=True)),
        ("F 开/层set/曲线带柄", dict(on=True, layer="set", gid=126, curve=True)),
        ("G 开/层set/连画两条", dict(on=True, layer="set", gid=126, twice=True)),
    ]

    for label, cfg in scenarios:
        w._open(sample)                     # 每次从原始地图重新加载
        _events()
        w.act_automap_while.setChecked(False)
        w.act_automap_while.setChecked(cfg["on"])
        _events()
        doc = w.state.doc
        layer = doc.layer_by_name(cfg["layer"])
        w.state.current_layer = layer
        w.scene.current_layer = layer
        w._on_gid(cfg["gid"])
        w.state.stamp = None
        if cfg.get("stamp"):
            # 模拟「从 Ground 层抓来的图章」：基础草 + 同图集中间格 + 过渡瓦片
            w.state.stamp = [[126, 130, 813], [130, 126, 820], [186, 126, 126]]
            from rwmap.rw.automapping import normalize_marker_gid
            print("   E stamp 原值:", w.state.stamp)
            print("   E stamp 归一:", [[normalize_marker_gid(doc, v)
                                     for v in line] for line in w.state.stamp])
        w._set_object_tool("pen")

        before = _neighbour_transitions(doc, cells)
        if cfg.get("curve"):
            _pen_path(w, p0, p1, handle=(p1[0] + 60.0, p1[1] + 90.0))
        else:
            _pen_path(w, p0, p1)
        if cfg.get("twice"):
            _finish(w, p0)
            p2 = ((col + 0.5) * tw, (row + 4.5) * th)
            p3 = ((col + 7.5) * tw, (row + 4.5) * th)
            _pen_path(w, p2, p3)
        _finish(w, p0)
        after = _neighbour_transitions(doc, cells)
        setl = doc.layer_by_name("set")
        written = sum(1 for c, r in cells if int(setl.get(c, r)) & 0xFFFFFFFF != 0)
        print(f"{label:34s} 过渡增量={after - before:4d} "
              f"(前{before}→后{after})  set写入={written}  "
              f"undo={w.state.undo_stack.count()}  status={w.statusBar().currentMessage()[:60]}")
        w.state.stamp = None


    # 画笔 / 填充同样要归一，否则「选了同图集第 5 格」依旧没有过渡
    for label, tool in (("H 画笔/gid130", "brush"), ("I 填充/gid130", "fill")):
        w._open(sample)
        _events()
        w.act_automap_while.setChecked(False)
        w.act_automap_while.setChecked(True)
        _events()
        doc = w.state.doc
        w._on_gid(130)
        w.state.stamp = None
        w.state.tool_id = tool
        before = _neighbour_transitions(doc, cells)
        if tool == "brush":
            w._on_press(col, row)
            for i in range(1, 8):
                w._on_move(col + i, row)
            w.view.cellReleased.emit(col + 7, row)
        else:
            w._on_press(col, row)
        _events()
        after = _neighbour_transitions(doc, cells)
        setl = doc.layer_by_name("set")
        if tool == "brush":
            print(f"{label:34s} 过渡增量={after - before:4d} "
                  f"(前{before}→后{after})  set格={setl.get(col, row)}  "
                  f"status={w.statusBar().currentMessage()[:60]}")
        else:
            # 填充会整片连通水域一次改写，水陆边界消失 → 「过渡增量」不适用，
            # 这里只验证归一确实生效（set 层写进的是首格 126 而不是 130）。
            print(f"{label:34s} set格={setl.get(col, row)} "
                  f"(归一到首格 126 = {'OK' if setl.get(col, row) == 126 else 'FAIL'})"
                  f"  填充格数见状态栏；整片改地形后边界消失属预期")

    w.state.tool_id = "brush"


if __name__ == "__main__":
    main()
