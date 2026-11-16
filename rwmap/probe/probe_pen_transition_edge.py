"""探针：批量搜索「钢笔局部重算丢过渡」的真实案例。

在示例地图所有草(126)/水(186)交界处抽样，每个位置画一条 8 格草线，
比较「局部 area=bounds 重算」与「全图重算」在该处 ±6 窗口内的过渡瓦片，
找出局部结果是哪一段边界漏了过渡（以及漏格相对线的方位）。

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_pen_transition_edge
只读：只改内存里的 doc，不写任何文件。
"""
from __future__ import annotations

from pathlib import Path


def main():
    from rwmap.document.gid import GID_MASK
    from rwmap.io.tmx_reader import read_document
    from rwmap.rw.automapping import AutoMapper, _base_gids
    from rwmap.rw.version import load_profile

    profile = load_profile("1.15")
    mapper = AutoMapper(profile)
    info = mapper.load()
    if not info.get("ok"):
        print("规则加载失败")
        return
    doc = read_document(Path(profile.sample_map), profile)
    setl = doc.layer_by_name("set")
    ground = doc.layer_by_name("Ground")
    base_gids = _base_gids(doc)
    set0 = list(setl.cells)
    ground0 = list(ground.cells)

    # 草/水交界格，按 14 格间距抽样，避免相邻位置重复
    edges = []
    for r in range(6, doc.height - 6, 2):
        for c in range(6, doc.width - 14, 2):
            here = int(setl.get(c, r)) & GID_MASK
            if here != 126:
                continue
            if any(int(setl.get(c + dx, r + dy)) & GID_MASK == 186
                   for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))):
                if all(abs(c - p[0]) > 14 or abs(r - p[1]) > 14 for p in edges):
                    edges.append((c, r))
        if len(edges) >= 24:
            break
    print("抽样边界位置:", len(edges), edges[:8])

    def apply(changes):
        for name, entries in changes.items():
            layer = doc.layer_by_name(name)
            if layer is None:
                continue
            for x, y, gid in entries:
                layer.cells[layer.index(x, y)] = gid

    def trans(pad, b):
        out = {}
        for y in range(max(0, b[1] - pad), min(doc.height, b[3] + pad + 1)):
            for x in range(max(0, b[0] - pad), min(doc.width, b[2] + pad + 1)):
                g = int(ground.get(x, y)) & GID_MASK
                if g and g not in base_gids:
                    out[(x, y)] = g
        return out

    findings = []
    for c, r in edges:
        setl.cells[:] = set0
        ground.cells[:] = ground0
        cells = [(c - 3 + i, r) for i in range(8)]
        changed = []
        for x, y in cells:
            idx = setl.index(x, y)
            if int(setl.cells[idx]) != 126:
                changed.append((x, y))
                setl.cells[idx] = 126
        if not changed:
            continue
        b = (min(p[0] for p in changed), min(p[1] for p in changed),
             max(p[0] for p in changed), max(p[1] for p in changed))

        before = trans(6, b)
        apply(mapper.compute(doc, area=b, only_diff=True))
        local = trans(6, b)

        ground.cells[:] = ground0
        apply(mapper.compute(doc, area=None, only_diff=True))
        full = trans(6, b)

        lost = set(full) - set(local)
        erased = set(before) - set(local)
        if lost or erased:
            findings.append((c, r, b, len(before), len(local), len(full),
                             len(lost), len(erased)))
            print(f"  位置({c},{r}) bounds={b} 前={len(before)} 局部={len(local)} "
                  f"全图={len(full)} 漏={len(lost)} 擦掉没补={len(erased)}")
            if lost:
                pos = sorted((x - b[0], y - b[1]) for x, y in lost)
                print("    漏格相对线首:", pos[:20])
            if erased:
                pos = sorted((x - b[0], y - b[1]) for x, y in erased)
                print("    擦掉格相对线首:", pos[:20])

    print("\n出现差异的位置数:", len(findings), "/", len(edges))
    if not findings:
        print("局部重算与全图重算在窗口内完全一致 —— 窗口裁剪不是缺过渡的原因")


if __name__ == "__main__":
    main()
