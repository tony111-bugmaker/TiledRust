"""扫描本机官方 / 社区 TMX，统计铁锈战争地图制作里的属性与指令键名。

只读探针，不修改任何地图。用法：
    .venv/Scripts/python.exe -m rwmap.probe.probe_rw_reference
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

MAPS = Path("<游戏目录1.15>/mods/maps")


def _props(elem):
    """取一个元素下所有 <property name|value>，返回列表。"""
    out = []
    for p in elem.findall("./properties/property"):
        out.append((p.get("name", ""), (p.get("value") or "")))
    return out


def scan(path: Path, acc: dict):
    tree = etree.parse(str(path))
    root = tree.getroot()

    # 1) <map> 根属性
    for k, v in root.attrib.items():
        acc["root_attrs"][k][v] += 1
    for k, v in _props(root):
        acc["by_layer"]["<map>"][k][v[:60]] += 1

    # 2) tileset / tile 上的属性（图块属性）
    for ts in root.findall("tileset"):
        acc["tilesets"][ts.get("name") or ts.get("source") or ""] += 1
        for tile in ts.findall("tile"):
            for k, v in _props(tile):
                acc["by_layer"]["<tileset/tile>"][k][v[:60]] += 1

    # 3) 图层
    for layer in root.iter("layer"):
        name = layer.get("name", "")
        acc["layers"][name] += 1
        for k, v in _props(layer):
            acc["by_layer"][f"tilelayer:{name}"][k][v[:60]] += 1

    # 4) 对象层：对象 name / type 即「指令 / 事件类型」，属性即参数
    for group in root.iter("objectgroup"):
        gname = group.get("name", "")
        acc["objectgroups"][gname] += 1
        for k, v in _props(group):
            acc["by_layer"][f"objectgroup:{gname}"][k][v[:60]] += 1
        for obj in group.findall("object"):
            oname = obj.get("name", "")
            otype = obj.get("type", "")
            acc["object_names"][oname] += 1
            acc["object_types"][otype] += 1
            key = f"{gname}/{oname or otype or '(unnamed)'}"
            acc["object_props"][key] = acc["object_props"].get(key, Counter())
            for k, v in _props(obj):
                acc["by_layer"][f"objectgroup:{gname}"][k][v[:60]] += 1
                acc["object_props"][key][k] += 1
                acc["prop_values"][k][v[:60]] += 1
                acc["prop_layer"][k][gname] += 1


def main():
    acc = dict(
        root_attrs=defaultdict(Counter),
        by_layer=defaultdict(lambda: defaultdict(Counter)),
        prop_values=defaultdict(Counter),
        prop_layer=defaultdict(Counter),
        layers=Counter(), objectgroups=Counter(),
        object_names=Counter(), object_types=Counter(),
        object_props={}, tilesets=Counter(),
    )
    files = sorted(MAPS.glob("*.tmx"))
    for p in files:
        try:
            scan(p, acc)
        except Exception as exc:                       # 坏图不影响整体统计
            print(f"  skip {p.name}: {type(exc).__name__}: {exc}")
    print(f"scanned {len(files)} tmx files\n")

    print("=== <map> 根属性 ===")
    for k, vals in sorted(acc["root_attrs"].items()):
        print(f"  {k:18s} {len(vals):3d} 种值  样例 {list(vals)[:4]}")

    print("\n=== 图层名（tilelayer / objectgroup） ===")
    for name, n in acc["layers"].most_common():
        print(f"  tile  {name:24s} {n}")
    for name, n in acc["objectgroups"].most_common():
        print(f"  obj   {name:24s} {n}")

    print("\n=== 属性键（按所在层汇总，含取值样例） ===")
    for lname in sorted(acc["by_layer"]):
        props = acc["by_layer"][lname]
        if not props:
            continue
        print(f"\n  [{lname}]  {len(props)} 个键")
        for k, vals in sorted(props.items(), key=lambda kv: -sum(kv[1].values())):
            total = sum(vals.values())
            layers = ",".join(f"{g}×{n}" for g, n in acc["prop_layer"][k].most_common(4))
            print(f"    {k:30s} {total:6d}  样例={list(vals)[:3]}  层={layers}")

    print("\n=== 对象名 / type（Triggers 里的「指令 / 事件」） ===")
    for k, n in acc["object_names"].most_common(45):
        print(f"  name {k!r:36s} {n}")
    print("  --- type ---")
    for k, n in acc["object_types"].most_common(25):
        print(f"  type {k!r:36s} {n}")

    print("\n=== 对象类型 → 常用属性（前 25） ===")
    for key, props in sorted(acc["object_props"].items(),
                             key=lambda kv: -sum(kv[1].values()))[:25]:
        print(f"  {key:36s} {dict(props.most_common(10))}")

    out = Path("logs/rw_reference_scan.json")
    out.parent.mkdir(exist_ok=True)
    dump = {
        "root_attrs": {k: dict(v) for k, v in acc["root_attrs"].items()},
        "layers": dict(acc["layers"]),
        "objectgroups": dict(acc["objectgroups"]),
        "object_names": dict(acc["object_names"]),
        "object_types": dict(acc["object_types"]),
        "by_layer": {ln: {k: dict(v) for k, v in p.items()}
                     for ln, p in acc["by_layer"].items()},
        "object_props": {k: dict(v) for k, v in acc["object_props"].items()},
    }
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nsaved:", out.resolve())


if __name__ == "__main__":
    main()
