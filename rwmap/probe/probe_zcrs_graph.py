"""探查：用编辑器自己的读取 + collect_graph 加载《尸如潮水》地图，
看也Activate（按 name 引用）能被识别成多少条连线、多少悬空。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rwmap.io import read_document
from rwmap.widgets.trigger_graph_dock import collect_graph

MAP = r"<游戏目录1.15>\mods\maps\尸如潮水9月21日版本.tmx"

doc = read_document(MAP)
print("layers:", [(l.name, l.kind, len(getattr(l, 'objects', []))) for l in doc.layers])
print("object layers:", [(l.name, len(l.objects)) for l in doc.layers if l.kind == "object"])

data = collect_graph(doc)
print("\n=== collect_graph 结果 ===")
print("nodes:", len(data.nodes))
print("edges:", len(data.edges))
print("dangling:", data.dangling)
print("layer_names:", data.layer_names)

# 抽样几条边，确认 src/dst 解析对了
shown = 0
for e in data.edges[:15]:
    n = data.nodes.get(e.src); m = data.nodes.get(e.dst)
    print(f"  edge {e.kind}: {n.obj.name if n else e.src!r} -> {m.obj.name if m else e.dst!r}")
    shown += 1
print(f"  ... 共 {len(data.edges)} 条边，已显示 {shown}")

# 悬空诊断：完全复刻 collect_graph 的 by_tid/by_oid/by_name + resolve
from rwmap.widgets.trigger_graph_dock import _parse_ids
by_tid={}; by_oid={}; by_name={}
for k_,node in data.nodes.items():
    o=node.obj
    tid=str(o.properties.get("id") or o.object_id)
    by_tid.setdefault(tid,k_)
    by_oid.setdefault(str(o.object_id),k_)
    for raw in (o.name,o.label,o.properties.get("name"),o.properties.get("label")):
        if raw: by_name.setdefault(str(raw).strip().lower(),k_)
def resolve(raw_id):
    r=str(raw_id).strip()
    return by_tid.get(r) or by_oid.get(r) or by_name.get(r.lower())

# 全 EDGE_SPECS 复刻 collect_graph 的悬空计数，定位是哪个关系类型悬空
from rwmap.widgets.trigger_graph_dock import EDGE_SPECS
missing_by_prop={}
for node in data.nodes.values():
    for prop in EDGE_SPECS:
        raw=node.obj.properties.get(prop)
        if not raw: continue
        for rid in _parse_ids(raw):
            if not rid: continue
            if resolve(rid) is None:
                missing_by_prop.setdefault(prop,{})
                missing_by_prop[prop][rid]=missing_by_prop[prop].get(rid,0)+1
print("\n=== 各关系类型悬空统计（复刻 collect_graph）===")
tot=0
for prop,m in missing_by_prop.items():
    print(f"  {prop}: distinct={len(m)} refs={sum(m.values())}")
    tot+=sum(m.values())
    for k,v in sorted(m.items(), key=lambda x:-x[1])[:15]:
        print(f"      {k!r} (x{v})")
print("  总悬空 refs =", tot, " (collect_graph 报告 dangling=", data.dangling, ")")

# 拓扑分层分布（复刻 _auto_positions 的逻辑，看每层多大）
keys = list(data.nodes)
adj = {k: [] for k in keys}; indeg = {k: 0 for k in keys}
for e in data.edges:
    if e.src in adj and e.dst in indeg:
        adj[e.src].append(e.dst); indeg[e.dst]+=1
remaining=set(keys); layers=[]
while remaining:
    ready=sorted(k for k in remaining if indeg[k]==0)
    if not ready: ready=sorted(remaining)
    layers.append(ready)
    for k in ready: remaining.discard(k)
    for k in ready:
        for nxt in adj[k]:
            if nxt in remaining: indeg[nxt]-=1
print("\n=== 拓扑分层 ===")
print("层数:", len(layers), " 各层节点数(前20):", [len(l) for l in layers[:20]], "...")
print("最大层节点数:", max(len(l) for l in layers))

