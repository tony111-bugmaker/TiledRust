"""探针：定位自动映射「内凹拐角」差异。

- 用官方示例地图做全图重建，与官方 Ground 逐格比对，按 gid 对统计差异；
- 打印差异点周围的 set 层邻域，判断是不是凹角；
- 统计每条规则的命中次数，找出从未命中的规则（尤其大输出区域的拐角规则）。
"""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from pathlib import Path

from rwmap.document.gid import GID_MASK
from rwmap.io.tmx_reader import read_document
from rwmap.rw.automapping import AutoMapper
from rwmap.rw.version import load_profile


def main():
    profile = load_profile("1.15")
    mapper = AutoMapper(profile)
    info = mapper.load()
    print("规则:", info["rules"], "文件:", [Path(p).name for p in info["rule_maps"]])
    doc = read_document(Path(profile.sample_map), profile)
    set_layer = doc.layer_by_name("set")
    ground = doc.layer_by_name("Ground")
    official = list(ground.cells)

    # ---------- 1. 全图重建差异 ----------
    for i in range(len(ground.cells)):
        ground.cells[i] = 0
    changes = mapper.compute(doc, only_diff=False)
    for target, entries in changes.items():
        layer = doc.layer_by_name(target)
        if layer is None:
            continue
        for x, y, gid in entries:
            layer.cells[layer.index(x, y)] = gid
    rebuilt = list(ground.cells)
    pairs = Counter()
    diffs = []
    for i, (a, b) in enumerate(zip(official, rebuilt)):
        a, b = int(a) & GID_MASK, int(b) & GID_MASK
        if a != b:
            pairs[(a, b)] += 1
            diffs.append((i % doc.width, i // doc.width, a, b))
    print("\n全图差异格:", len(diffs), "/", len(official),
          "复现率 %.2f%%" % (100 * (1 - len(diffs) / len(official))))
    print("官方gid -> 我们gid (前 25):")
    for (a, b), n in pairs.most_common(25):
        print("   %-5d -> %-5d  %d 格" % (a, b, n))

    def neighborhood(x, y, r=2):
        lines = []
        for dy in range(-r, r + 1):
            row = []
            for dx in range(-r, r + 1):
                if set_layer.in_bounds(x + dx, y + dy):
                    row.append("%4d" % (int(set_layer.get(x + dx, y + dy)) & GID_MASK))
                else:
                    row.append("   -")
            lines.append(" ".join(row))
        return "\n".join(lines)

    print("\n差异点样例 (前 12):")
    for x, y, a, b in diffs[:12]:
        print(f"@({x},{y}) 官方={a} 我们={b}")
        print(neighborhood(x, y))
    with open("logs/probe_corner_diffs.txt", "w", encoding="utf-8") as fp:
        for x, y, a, b in diffs:
            fp.write(f"{x},{y},{a},{b}\n")

    # ---------- 2. 规则命中统计 ----------
    # 恢复官方 Ground，逐规则在全图上单独匹配（只看 set 层条件）
    ground.cells[:] = official
    counts = Counter()
    matched_rules = set()
    from rwmap.rw.automapping import _components  # noqa
    layers_cache = {}

    def read(layer, x, y):
        if layer is None:
            return 0
        if not layer.in_bounds(x, y):
            return None
        return int(layer.get(x, y)) & GID_MASK

    def layer_of(name):
        if name not in layers_cache:
            layers_cache[name] = doc.layer_by_name(name) if name else None
        return layers_cache[name]

    for rule in mapper.rules:
        for y in range(0, doc.height - rule.h + 1):
            for x in range(0, doc.width - rule.w + 1):
                ok = True
                for dx, dy, gid, target in rule.inputs:
                    if read(layer_of(target), x + dx, y + dy) != gid:
                        ok = False
                        break
                if ok:
                    for dx, dy, gid, target in rule.inputnots:
                        if read(layer_of(target), x + dx, y + dy) == gid:
                            ok = False
                            break
                if ok:
                    for dx, dy, target in rule.wildcards:
                        v = read(layer_of(target), x + dx, y + dy)
                        if v is None or v == 0 or v in rule.used_gids:
                            ok = False
                            break
                if ok:
                    counts[rule.name] += 1
                    matched_rules.add(rule.name)
    print("\n命中规则数:", len(matched_rules), "/", len(mapper.rules))
    zero = [r for r in mapper.rules if r.name not in matched_rules]
    print("从未命中的规则:", len(zero))
    for r in zero[:20]:
        print("   ", r.name, "w/h", r.w, r.h, "inputs", len(r.inputs),
              "used_gids", sorted(r.used_gids)[:8],
              "outputs", {k: len(v[0]) for k, v in r.outputs.items()},
              "strict", r.strict_empty)

    # ---------- 3. 检查地图是否有 set_decoration 层 ----------
    print("\n地图层:", [(l.name, type(l).__name__) for l in doc.layers])


if __name__ == "__main__":
    main()
