"""探针：钢笔线局部自动映射「一段有过渡、一段没有」的成因。

做法：把 Ground 先整体重置成 set 层基础地形（等价 compute 的 reset_base
擦除之后的状态），再用四种方式算「应该写出的过渡瓦片」，对比漏了哪些格：

  full  全图一次重算                       —— 标准答案
  local 当前实现：整条线的 bounds 一次重算
  fwd   沿曲线格序列 起点→终点 逐格重算再合并
  rev   沿曲线格序列 终点→起点 逐格重算再合并

用法： .venv\\Scripts\\python.exe -m rwmap.probe.probe_pen_transition_direction
只读：只改内存里的 doc，不写任何文件。
"""
from __future__ import annotations

from pathlib import Path


def _transitions(changes, base_gids):
    """changes -> {(层名,x,y): gid}，只留过渡瓦片（非基础地形、非空）。"""
    out = {}
    for layer, entries in changes.items():
        for x, y, gid in entries:
            if gid and gid not in base_gids:
                out[(layer, x, y)] = gid
    return out


def _find_water(setl, doc, width=12, height=8):
    """找一块全是水(186)的矩形区域，用来在中间画草线。"""
    for r in range(4, doc.height - height - 4):
        for c in range(4, doc.width - width - 4):
            if all(int(setl.get(c + dx, r + dy)) & 0x0FFFFFFF == 186
                   for dy in range(height) for dx in range(width)):
                return c, r
    return None


def main():
    from rwmap.document.gid import GID_MASK
    from rwmap.io.tmx_reader import read_document
    from rwmap.rw.automapping import AutoMapper, _base_gids
    from rwmap.rw.version import load_profile

    profile = load_profile("1.15")
    mapper = AutoMapper(profile)
    info = mapper.load()
    print("rules:", info.get("rules"), "ok:", info.get("ok"))
    if not info.get("ok"):
        print("规则加载失败，无法探针")
        return

    doc = read_document(Path(profile.sample_map), profile)
    setl = doc.layer_by_name("set")
    ground = doc.layer_by_name("Ground")
    base_gids = _base_gids(doc)
    print("map:", doc.width, doc.height, "tile:", doc.tile_w, doc.tile_h)

    spot = _find_water(setl, doc)
    if spot is None:
        print("示例地图里找不到 12x8 纯水区域；改用固定区域 32,32")
        c0, r0 = 32, 32
    else:
        c0, r0 = spot
        print("纯水区域:", c0, r0)

    # 在水域中间画一条 8 格草线（模拟钢笔落笔后的 set 层）
    cells = [(c0 + 2 + i, r0 + 4) for i in range(8)]
    for c, r in cells:
        setl.cells[setl.index(c, r)] = 126
    bounds = (cells[0][0], cells[0][1], cells[-1][0], cells[-1][1])
    print("草线:", cells[0], "->", cells[-1], "bounds:", bounds)

    ground_original = list(ground.cells)
    # 把 Ground 全图重置成 set 基础地形（render 前的干净状态）
    for r in range(doc.height):
        for c in range(doc.width):
            base = int(setl.get(c, r)) & GID_MASK
            ground.cells[ground.index(c, r)] = base if base in base_gids else 0

    kw = dict(only_diff=False, reset_base=True)
    full = mapper.compute(doc, area=None, **kw)
    local = mapper.compute(doc, area=bounds, **kw)

    def per_cell(series):
        merged = {}
        for c, r in series:
            got = mapper.compute(doc, area=(c, r, c, r), **kw)
            for layer, entries in got.items():
                for x, y, gid in entries:
                    merged[(layer, x, y)] = gid      # 后写覆盖先写
        return merged

    fwd = per_cell(cells)
    rev = per_cell(list(reversed(cells)))
    ft = _transitions(full, base_gids)
    lt = _transitions(local, base_gids)
    fwd_t = {k: v for k, v in fwd.items() if v and v not in base_gids}
    rev_t = {k: v for k, v in rev.items() if v and v not in base_gids}

    def near(k):
        """只看线附近 bounds±3 的格，避免把全图其它区域的过渡算成「漏」。"""
        return (bounds[0] - 8 <= k[1] <= bounds[2] + 8
                and bounds[1] - 8 <= k[2] <= bounds[3] + 8)

    def report(name, got):
        std = {k: v for k, v in ft.items() if near(k)}
        cur = {k: v for k, v in got.items() if near(k)}
        missing = sorted(set(std) - set(cur))
        print(f"  {name:8s} 窗口内过渡={len(cur):3d}/{len(std):3d} "
              f"缺={len(missing):2d}  格值不同="
              f"{sum(1 for k in set(std) & set(cur) if std[k] != cur[k])}")
        return missing

    print("\n[A] 纯计算（Ground 已整体重置为基础地形）")
    report("local", lt)
    report("fwd", fwd_t)
    report("rev", rev_t)
    union = dict(fwd_t)
    union.update(rev_t)
    report("fwd+rev", union)
    diff_fr = sorted(k for k in set(fwd_t) | set(rev_t)
                     if fwd_t.get(k) != rev_t.get(k))
    print("  正序 / 反序 结果不同的格数:", len(diff_fr))

    # ---------- B 真实链路：原始 Ground（带官方过渡）→ 画线 → 局部重算写回 ----------
    def apply(changes):
        for name, entries in changes.items():
            layer = doc.layer_by_name(name)
            if layer is None:
                continue
            for x, y, gid in entries:
                layer.cells[layer.index(x, y)] = gid

    def window_trans(pad=8):
        out = {}
        for y in range(bounds[1] - pad, bounds[3] + pad + 1):
            for x in range(bounds[0] - pad, bounds[2] + pad + 1):
                if ground.in_bounds(x, y):
                    g = int(ground.get(x, y)) & GID_MASK
                    if g and g not in base_gids:
                        out[(x, y)] = g
        return out

    print("\n[B] 真实链路（原始 Ground 已有过渡）")
    ground.cells[:] = ground_original
    before = window_trans()
    apply(mapper.compute(doc, area=bounds, only_diff=True))
    after_local = window_trans()

    ground.cells[:] = ground_original
    apply(mapper.compute(doc, area=None, only_diff=True))
    after_full = window_trans()

    print(f"  落笔前窗口内过渡={len(before)}  "
          f"局部重算后={len(after_local)}  全图重算后={len(after_full)}")
    lost = sorted(set(after_full) - set(after_local))
    print(f"  局部相对全图缺的过渡格={len(lost)}")
    if lost:
        pos = [(x - bounds[0], y - bounds[1]) for x, y in lost]
        print("    相对线首坐标:", pos[:24])
        print(f"    dx=[{min(p[0] for p in pos)},{max(p[0] for p in pos)}] "
              f"dy=[{min(p[1] for p in pos)},{max(p[1] for p in pos)}]")
    erased = sorted(set(before) - set(after_local))
    print(f"  被擦掉且没补回的旧过渡格={len(erased)} 样例={erased[:12]}")


if __name__ == "__main__":
    main()
