"""探针 2：对差异格，枚举「匹配且输出到该格」的所有规则。

用于判断拐角瓦片丢失是 (a) 规则根本没匹配，还是 (b) 匹配后被优先级覆盖。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from rwmap.document.gid import GID_MASK
from rwmap.io.tmx_reader import read_document
from rwmap.rw.automapping import AutoMapper
from rwmap.rw.version import load_profile

SAMPLES = [(86, 7), (95, 7), (65, 8), (54, 12), (117, 10)]
CORNER_GIDS = {812, 814, 817, 818, 820, 836, 838, 845, 847, 851, 853,
               919, 920, 922, 923, 925}


def main():
    profile = load_profile("1.15")
    mapper = AutoMapper(profile)
    mapper.load()
    doc = read_document(Path(profile.sample_map), profile)
    ground = doc.layer_by_name("Ground")
    cache = {}

    def layer_of(name):
        if name not in cache:
            cache[name] = doc.layer_by_name(name) if name else None
        return cache[name]

    def read(layer, x, y):
        if layer is None:
            return 0
        if not layer.in_bounds(x, y):
            return None
        return int(layer.get(x, y)) & GID_MASK

    # 哪些规则会输出拐角 gid，它们的输入条件是什么
    print("=== 输出拐角 gid 的规则 ===")
    owners = defaultdict(list)
    for r in mapper.rules:
        for target, variants in r.outputs.items():
            for cells in variants:
                for dx, dy, gid in cells:
                    if gid in CORNER_GIDS:
                        owners[gid].append(r)
    for gid in sorted(owners):
        rs = owners[gid]
        print(f"gid {gid}: {len(rs)} 条规则输出, 来源 {Counter(r.name.split('#')[0] for r in rs)}")
        for r in rs[:3]:
            print("    ", r.name, "order", r.order, "in", [(a, b, c, d) for a, b, c, d in r.inputs],
                  "gids", sorted(r.used_gids), "strict", r.strict_empty)

    print("\n=== 差异格上所有命中规则的输出 ===")
    for px, py in SAMPLES:
        print(f"\n--- ({px},{py}) 官方 Ground={int(ground.get(px,py)) & GID_MASK} ---")
        hits = []
        for r in mapper.rules:
            for y in range(max(0, py - r.h), min(doc.height - r.h, py + 2) + 1):
                for x in range(max(0, px - r.w), min(doc.width - r.w, px + 2) + 1):
                    ok = all(read(layer_of(t), x + dx, y + dy) == g
                             for dx, dy, g, t in r.inputs)
                    if ok:
                        ok = all(read(layer_of(t), x + dx, y + dy) != g
                                 for dx, dy, g, t in r.inputnots)
                    if ok:
                        for dx, dy, t in r.wildcards:
                            v = read(layer_of(t), x + dx, y + dy)
                            if v is None or v == 0 or v in r.used_gids:
                                ok = False
                                break
                    if not ok:
                        continue
                    for target, variants in r.outputs.items():
                        for dx, dy, gid in variants[0]:
                            if x + dx == px and y + dy == py:
                                hits.append((r.name, r.order, target, gid, (x, y)))
        for name, order, target, gid, origin in sorted(hits, key=lambda t: t[1]):
            print(f"   {name:<18} order={order!s:<26} {target}:{gid} origin={origin}")
        if not hits:
            print("   (没有任何命中规则输出到该格)")


if __name__ == "__main__":
    main()
