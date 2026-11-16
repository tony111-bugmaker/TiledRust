"""探针 3：把 ruleset1 的 rules 区域按格打印，核对拐角规则 #187 的真实结构。"""
from __future__ import annotations

from pathlib import Path

from rwmap.document.gid import GID_MASK
from rwmap.io.tmx_reader import read_document
from rwmap.rw.version import load_profile

p = load_profile("1.15")
d = read_document(Path(p.mods_maps) / "rules" / "ruleset1.tmx", p)
L = {l.name: l for l in d.layers}


def g(name, x, y):
    l = L.get(name)
    if l is None or not hasattr(l, "get") or not l.in_bounds(x, y):
        return 0
    return int(l.get(x, y)) & GID_MASK


def dump(x0, y0, x1, y1, names=("regions", "input_set", "output_Ground")):
    print("\n     ", " ".join("%5d" % x for x in range(x0, x1 + 1)))
    for y in range(y0, y1 + 1):
        for name in names:
            row = " ".join("%5d" % g(name, x, y) for x in range(x0, x1 + 1))
            print(f"y={y:2d} {name[:7]:<8} {row}")
        print()


# #187 order=('ruleset1', 35, 35) —— 输入区域在 y=35, x=35 附近
dump(30, 27, 40, 38)

# 找 input_set 有值但 regions 无值的位置
extra = []
regions = L["regions"]
for i, v in enumerate(L["input_set"].cells):
    if int(v) & GID_MASK and not (int(regions.cells[i]) & GID_MASK):
        extra.append((i % d.width, i // d.width, int(v) & GID_MASK))
print("input_set 有值但 regions 无值:", len(extra), extra[:30])
miss = []
for i, v in enumerate(regions.cells):
    if int(v) & GID_MASK and not (int(L["input_set"].cells[i]) & GID_MASK):
        miss.append((i % d.width, i // d.width))
print("regions 有值但 input_set 无值:", len(miss), miss[:30])
