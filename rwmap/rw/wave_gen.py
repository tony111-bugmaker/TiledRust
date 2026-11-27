"""生存模式「波次生成器」：链条校验 + 编译成原版 survivalWaves 文本。

设计
----
每个 waveGen 节点代表生存模式里的一波刷兵，编排数据全部存在编辑器私有属性
（见 document.properties 的 WAVE_* 常量）里。用户像连触发器一样用 `rwmap_wave_next`
把节点串成一条单向链；保存时本模块：

1. :func:`validate_wave_chain` 校验链条完整性（断链 / 孤立 / 分叉 / 多入边 /
   成环 / 指向无效 / 多个起点），返回人类可读的问题列表；
2. :func:`compile_survival_waves` 按「起点 → next → next → …」顺序把每个节点
   编译成一行原版格式::

       +分:秒 波次名 - 数量 单位名,数量 单位名

   写进 map_info 对象的 ``survivalWaves`` 属性，游戏据此显示官方倒计时并刷兵。

本模块不依赖 Qt，便于纯逻辑单元测试。所有对文档的访问都走 duck typing，
避免与 document 层产生循环 import。
"""
from __future__ import annotations

import re
from typing import Any

from ..document.properties import (
    EDITOR_NODE_POS_PROPERTY,
    HIDDEN_EDITOR_PROPERTIES,
    WAVE_DISPATCH_MARK_PROPERTY,
    WAVE_DISPATCH_WAVE_PROPERTY,
    WAVE_SPAWN_PROXY_MARK_PROPERTY,
    WAVE_SPAWN_PROXY_SOURCE_PROPERTY,
    WAVE_GEN_TYPE,
    WAVE_LINK_PROPERTY,
    WAVE_MARK_PROPERTY,
    WAVE_NAME_PROPERTY,
    WAVE_NEXT_PROPERTY,
    WAVE_TIME_PROPERTY,
    WAVE_UNITS_PROPERTY,
    WAVE_TABLE_MARK_PROPERTY,
    WAVE_TABLE_TEXT_PROPERTY,
)

# 中文数字（1~20 够用；再多退化成阿拉伯数字，见 _cn_number）
_CN_DIGITS = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九")


def _cn_number(n: int) -> str:
    """1→一 … 10→十 … 20→二十；超出 20 直接用阿拉伯数字。"""
    if 1 <= n <= 9:
        return _CN_DIGITS[n]
    if n == 10:
        return "十"
    if 11 <= n <= 19:
        return "十" + _CN_DIGITS[n - 10]
    if 20 <= n <= 99:
        tens, ones = divmod(n, 10)
        return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    return str(n)


# survivalWaves 写在这个对象上；游戏按 name=map_info 识别它（type 可空）。
MAP_INFO_NAME = "map_info"
SURVIVAL_WAVES_PROPERTY = "survivalWaves"


class WaveNode:
    """一个波次节点的轻量视图。"""

    __slots__ = ("obj", "key", "ident", "time", "name", "units", "next_refs")

    def __init__(self, obj: Any, ident: str):
        self.obj = obj
        self.key = id(obj)
        self.ident = ident
        props = getattr(obj, "properties", None)
        values = getattr(props, "values", {}) or {}
        self.time = str(values.get(WAVE_TIME_PROPERTY, "") or "").strip()
        # 名字以节点标题为准：用户说的「名字就是顶上显示的字」。私有属性里的
        # 波次名只作兜底（重开 / 老数据），并在同步时写回，保证两者一致。
        self.name = (str(getattr(obj, "name", "") or "").strip()
                     or str(values.get(WAVE_NAME_PROPERTY, "") or "").strip())
        self.units = str(values.get(WAVE_UNITS_PROPERTY, "") or "").strip()
        self.next_refs = [
            r.strip() for r in _split_refs(values.get(WAVE_NEXT_PROPERTY, "")) if r.strip()
        ]

    @property
    def label(self) -> str:
        return self.name or self.ident or "未命名波次"


def _split_refs(raw) -> list[str]:
    """逗号 / 分号 / 空白分隔引用（与节点图 _parse_ids 的口径一致）。"""
    out: list[str] = []
    for token in str(raw or "").replace(";", ",").split(","):
        token = token.strip()
        if token:
            out.append(token)
    return out


def next_wave_name(doc) -> str:
    """生成下一波的名字（第一波 / 第二波 / …），跳过已存在的名字。

    连续编号：看现存波次里最大的「第N波」编号 +1；认不出编号时按波次数 +1。
    这样右键连续插入能依次得到 第一波 → 第二波 → 第三波 …
    """
    import re
    nodes = collect_wave_nodes(doc)
    used = {n.name for n in nodes}
    biggest = 0
    for n in nodes:
        m = re.search(r"第\s*([0-9]+)\s*波", n.name)
        if m:
            biggest = max(biggest, int(m.group(1)))
            continue
        for idx, ch in enumerate(_CN_DIGITS[1:], start=1):
            if n.name.startswith("第" + ch + "波"):
                biggest = max(biggest, idx)
                break
        if n.name == "第十波":
            biggest = max(biggest, 10)
    candidate = biggest + 1 if biggest else (len(nodes) + 1)
    name = f"第{_cn_number(candidate)}波"
    while name in used:
        candidate += 1
        name = f"第{_cn_number(candidate)}波"
    return name


def _chain_order(nodes: list[WaveNode]) -> list[WaveNode]:
    """按 rwmap_wave_next 拓扑排序：没有任何前置触发的节点就是第一波。

    断链 / 成环时「尽力而为」：走不到的节点按原顺序补在末尾，保证删除后
    仍能把所有波次编号排出来，不会静默丢波次。
    """
    by_name = {str(getattr(n.obj, "name", "") or "").strip().lower(): n
               for n in nodes if str(getattr(n.obj, "name", "") or "").strip()}
    target: dict[int, WaveNode | None] = {}
    indeg: dict[int, int] = {n.key: 0 for n in nodes}
    for n in nodes:
        hit = None
        for ref in n.next_refs:
            cand = _resolve(ref, nodes, by_name)
            if cand is not None and cand.key != n.key:
                hit = cand
                break
        target[n.key] = hit
        if hit is not None:
            indeg[hit.key] += 1

    ordered: list[WaveNode] = []
    seen: set[int] = set()
    for start in [n for n in nodes if not indeg[n.key]]:
        cur: WaveNode | None = start
        while cur is not None and cur.key not in seen:
            seen.add(cur.key)
            ordered.append(cur)
            cur = target.get(cur.key)
    for n in nodes:
        if n.key not in seen:
            ordered.append(n)
            seen.add(n.key)
    return ordered


def repair_wave_chain(doc, deleted: set[str],
                      deleted_next: dict[str, str]) -> int:
    """删除波次节点后接回断掉的链：前驱的 next 改为被删节点的后继。

    ``deleted`` 是被删节点的稳定标识（id / object_id / name），
    ``deleted_next`` 记录它们删除前指向的下一波标识。返回修复的引用条数。
    """
    if not deleted:
        return 0
    fixed = 0
    for n in collect_wave_nodes(doc):
        refs = n.next_refs
        new_refs: list[str] = []
        for ref in refs:
            if ref not in deleted:
                new_refs.append(ref)
                continue
            nxt = deleted_next.get(ref, "")
            if nxt and nxt not in deleted and nxt not in new_refs:
                new_refs.append(nxt)
                fixed += 1
        if new_refs != refs:
            n.obj.properties.set(WAVE_NEXT_PROPERTY, ", ".join(new_refs))
    return fixed


def reorder_wave_nodes(doc) -> int:
    """按链条顺序重排「第N波」编号：没有前置触发器的节点排第一波。

    只重排形如「第N波 / 第N 波」的名字，「最终波」这类自定义名原样保留。
    改名会同步修正引用它的 rwmap_wave_next / rwmap_wave_dispatch_wave，
    避免保存后链条因改名而断裂。返回实际改名的节点数。
    """
    nodes = collect_wave_nodes(doc)
    if len(nodes) < 2:
        return 0
    pattern = re.compile(r"^第\s*(?:\d+|[一二三四五六七八九十]+)\s*波$")
    renames: dict[str, str] = {}
    pending: list[tuple[WaveNode, str]] = []
    for idx, node in enumerate(_chain_order(nodes), start=1):
        old = node.name
        if not pattern.match(old):
            continue
        new = f"第{_cn_number(idx)}波"
        if new != old:
            renames[old] = new
            pending.append((node, new))
    if not pending:
        return 0

    # 先改引用再改名字，期间按旧名 → 新名映射
    for node in nodes:
        refs = node.next_refs
        new_refs = [renames.get(ref, ref) for ref in refs]
        if new_refs != refs:
            node.obj.properties.set(WAVE_NEXT_PROPERTY, ", ".join(new_refs))
        props = getattr(node.obj, "properties", None)
        values = getattr(props, "values", {}) or {}
        src = str(values.get(WAVE_DISPATCH_WAVE_PROPERTY, "") or "").strip()
        if src in renames:
            node.obj.properties.set(WAVE_DISPATCH_WAVE_PROPERTY, renames[src])
    for node, new in pending:
        node.obj.name = new
        node.obj.properties.set(WAVE_NAME_PROPERTY, new)
        # 节点对象改名后，以 name 作标识的节点需要同步引用自身的地方
    return len(pending)


def is_wave_node(obj) -> bool:
    """是不是波次生成器节点。

    内存里新建的节点 type_ = "waveGen"；从磁盘重开时写盘映射成空 type，
    靠私有标记 ``rwmap_wave`` 还原识别。两种都要认。
    """
    if getattr(obj, "type_", "") == WAVE_GEN_TYPE:
        return True
    props = getattr(obj, "properties", None)
    values = getattr(props, "values", {}) or {}
    return str(values.get(WAVE_MARK_PROPERTY, "") or "").strip() == "1"


def _object_ident(obj) -> str:
    """节点在连线里被引用的稳定标识：自定义 id → object_id → name。"""
    values = getattr(getattr(obj, "properties", None), "values", {}) or {}
    for key in ("id",):
        val = str(values.get(key, "") or "").strip()
        if val:
            return val
    oid = str(getattr(obj, "object_id", "") or "").strip()
    if oid:
        return oid
    return str(getattr(obj, "name", "") or "").strip()


def collect_wave_nodes(doc) -> list[WaveNode]:
    """跨所有对象层收集 waveGen 节点。"""
    nodes: list[WaveNode] = []
    for layer in getattr(doc, "layers", []) or []:
        if not hasattr(layer, "objects"):
            continue
        for obj in layer.objects:
            if is_wave_node(obj):
                nodes.append(WaveNode(obj, _object_ident(obj)))
    return nodes


def _resolve(ref: str, nodes: list[WaveNode], by_name: dict[str, WaveNode]) -> WaveNode | None:
    """把连线引用解析成波次节点：自定义 id → object_id → name（不区分大小写）。"""
    for n in nodes:
        if n.ident == ref:
            return n
    oid = str(getattr(n.obj, "object_id", "") or "").strip()
    for n in nodes:
        if oid == ref:
            return n
    return by_name.get(ref.strip().lower())


def validate_wave_chain(doc) -> list[str]:
    """校验波次链，返回问题列表（空列表 = 完整）。

    规则（「任一处断链就报」）：
    - 链必须恰好一个起点（没有任何人指向它）；
    - 每个节点最多一个 next（分叉）、最多一个入边（被多人指向）；
    - next 必须指向存在的波次节点（指向无效）；
    - 沿 next 从起点必须能走到全部节点（孤立 / 多段断链）；
    - 不能成环。
    """
    nodes = collect_wave_nodes(doc)
    if not nodes:
        return []   # 没有波次节点：不是生存编排图，无需校验
    if len(nodes) == 1:
        # 单节点：没有连线是合法的（就一波）
        n = nodes[0]
        if n.next_refs:
            return [f"「{n.label}」的下一波指向了不存在的节点"]
        return []

    by_name: dict[str, WaveNode] = {}
    for n in nodes:
        nm = str(getattr(n.obj, "name", "") or "").strip().lower()
        if nm:
            by_name[nm] = n

    problems: list[str] = []
    by_key = {n.key: n for n in nodes}
    out_deg: dict[int, list[WaveNode]] = {}
    in_deg: dict[int, list[WaveNode]] = {n.key: [] for n in nodes}
    next_target: dict[int, WaveNode | None] = {}

    for n in nodes:
        if len(n.next_refs) > 1:
            problems.append(
                f"「{n.label}」连了 {len(n.next_refs)} 条下一波（分叉）：一个波次只能有下一波")
        targets: list[WaveNode] = []
        for ref in n.next_refs:
            target = _resolve(ref, nodes, by_name)
            if target is None:
                problems.append(f"「{n.label}」的下一波「{ref}」找不到对应的波次节点")
                continue
            if target.key == n.key:
                problems.append(f"「{n.label}」的下一波指向了自己（成环）")
                continue
            targets.append(target)
            in_deg[target.key].append(n)
        out_deg[n.key] = targets
        next_target[n.key] = targets[0] if targets else None

    # 多入边
    for n in nodes:
        parents = in_deg[n.key]
        if len(parents) > 1:
            names = "、".join(p.label for p in parents)
            problems.append(f"「{n.label}」被多个波次指向（多入边）：{names}")

    # 起点
    starts = [n for n in nodes if not in_deg[n.key]]
    if not starts:
        problems.append("整条波次链首尾相接成环，没有起点（第一波）")
        return problems
    if len(starts) > 1:
        names = "、".join(n.label for n in starts)
        problems.append(f"有 {len(starts)} 个起点（断成多段）：{names}")
        return problems

    # 沿 next 走，检查能否覆盖全部节点 + 成环
    visited: set[int] = set()
    cur: WaveNode | None = starts[0]
    while cur is not None and cur.key not in visited:
        visited.add(cur.key)
        cur = next_target.get(cur.key)

    if cur is not None:
        problems.append("波次链在中途形成环，无法编译")

    missing = [n for n in nodes if n.key not in visited]
    if missing:
        names = "、".join(n.label for n in missing[:6])
        more = f" 等 {len(missing)} 个" if len(missing) > 6 else ""
        problems.append(f"这些波次没有串进主链（孤立 / 断链）：{names}{more}")

    return problems


# ---- 时间 / 单位格式化 ----

def _parse_time(raw: str) -> str:
    """把用户填的时间归一成 ``分:秒``（不带 +）。

    支持 ``0:10`` / ``+0:10`` / ``10s`` / ``70`` / ``1分30秒``。
    解析不出来时原样返回（让游戏去报错，总比悄悄改错好）。
    """
    t = str(raw or "").strip()
    if not t:
        return ""
    if t.startswith("+"):
        t = t[1:].strip()
    # m:ss
    if ":" in t:
        m, _, s = t.partition(":")
        if m.strip().isdigit() and s.strip().isdigit():
            return f"{int(m)}:{int(s):02d}"
    # 中文
    if "分" in t or "秒" in t:
        import re
        m = re.search(r"(\d+)\s*分", t)
        s = re.search(r"(\d+)\s*秒", t)
        mm = int(m.group(1)) if m else 0
        ss = int(s.group(1)) if s else 0
        return f"{mm}:{ss:02d}"
    # 纯秒数（可带 s）
    digits = t[:-1].strip() if t.lower().endswith("s") else t
    if digits.isdigit():
        total = int(digits)
        return f"{total // 60}:{total % 60:02d}"
    return t


def _parse_units(raw: str) -> str:
    """把刷兵归一成原版 ``数量 单位名,数量 单位名``。

    同时接受 ``6 bugMelee,2 heavyTank``（原版）与 ``bugMelee*6,heavyTank*2``
    （编辑器 unitAdd 风格），以及括号参数 ``mechFlame(...) * 4``。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    parts = _split_refs(text)
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "*" in part:
            name, _, count = part.rpartition("*")
            name, count = name.strip(), count.strip()
            if name and count:
                out.append(f"{count} {name}")
                continue
        # 游戏要求每段都带数量；裸单位名（如 bugMeleeT31）会被判
        # "Unknown wave format"，必须补成 "1 bugMeleeT31"。
        if re.match(r"^\d+\s+", part):
            out.append(part)
        else:
            out.append(f"1 {part}")
    return ",".join(out)


def compile_line(node: WaveNode) -> str:
    """编译单个波次节点成原版一行。"""
    time = _parse_time(node.time)
    name = node.name or node.label
    units = _parse_units(node.units)
    return f"+{time} {name} - {units}"


def compile_survival_waves(doc) -> str:
    """按链顺序编译出完整 survivalWaves 文本（每行一波，\\n 分隔）。

    调用前应先 :func:`validate_wave_chain` 确认链条完整；本函数对断链采取
    「尽力而为」：从起点走能走到的节点依次编译，走不到的追加在末尾，保证
    用户即使忽略警告也不会静默丢波次。
    """
    nodes = collect_wave_nodes(doc)
    if not nodes:
        return ""
    by_name: dict[str, WaveNode] = {}
    for n in nodes:
        nm = str(getattr(n.obj, "name", "") or "").strip().lower()
        if nm:
            by_name[nm] = n
    next_target: dict[int, WaveNode | None] = {}
    indeg: dict[int, int] = {n.key: 0 for n in nodes}
    for n in nodes:
        target = None
        for ref in n.next_refs:
            t = _resolve(ref, nodes, by_name)
            if t is not None and t.key != n.key:
                target = t
                break
        next_target[n.key] = target
        if target is not None:
            indeg[target.key] += 1

    starts = [n for n in nodes if indeg[n.key] == 0]
    ordered: list[WaveNode] = []
    seen: set[int] = set()
    for start in starts:
        cur: WaveNode | None = start
        while cur is not None and cur.key not in seen:
            seen.add(cur.key)
            ordered.append(cur)
            cur = next_target.get(cur.key)
    # 环 / 漏网的补在末尾
    for n in nodes:
        if n.key not in seen:
            ordered.append(n)
            seen.add(n.key)

    # 模式切换自动建的第一波还没填刷兵列表；空波次不编译，避免写出
    # "+0:10 第一波 - " 这种游戏解析不了的行。用户填完单位后自然出现。
    ordered = [n for n in ordered if n.units.strip()]
    return "\n".join(compile_line(n) for n in ordered)


def _time_seconds(raw: str) -> int:
    """把波次时间归一成秒；解析不出来按 0 处理。"""
    text = _parse_time(raw)
    try:
        mm, _, ss = text.partition(":")
        return max(0, int(mm) * 60 + int(ss))
    except (TypeError, ValueError):
        return 0


def _wave_timeline(doc) -> list[tuple[WaveNode, int]]:
    """返回 (波次节点, 累计秒数)；游戏 Wave.e 就是把每行 time 累加出来的。"""
    nodes = collect_wave_nodes(doc)
    if not nodes:
        return []
    by_name: dict[str, WaveNode] = {}
    for n in nodes:
        nm = str(getattr(n.obj, "name", "") or "").strip().lower()
        if nm:
            by_name[nm] = n
    nxt: dict[int, WaveNode | None] = {}
    indeg: dict[int, int] = {n.key: 0 for n in nodes}
    for n in nodes:
        target = None
        for ref in n.next_refs:
            t = _resolve(ref, nodes, by_name)
            if t is not None and t.key != n.key:
                target = t
                break
        nxt[n.key] = target
        if target is not None:
            indeg[target.key] += 1
    starts = [n for n in nodes if indeg[n.key] == 0]
    ordered: list[WaveNode] = []
    seen: set[int] = set()
    for start in starts:
        cur = start
        while cur is not None and cur.key not in seen:
            seen.add(cur.key)
            ordered.append(cur)
            cur = nxt.get(cur.key)
    for n in nodes:
        if n.key not in seen:
            ordered.append(n)
            seen.add(n.key)
    total = 0
    result: list[tuple[WaveNode, int]] = []
    for n in ordered:
        if not n.units.strip():
            continue
        total += _time_seconds(n.time)
        result.append((n, total))
    return result


def find_map_info(doc):
    """找到 map_info 对象（按 name，其次 type）。"""
    for layer in getattr(doc, "layers", []) or []:
        if not hasattr(layer, "objects"):
            continue
        for obj in layer.objects:
            if str(getattr(obj, "name", "") or "").strip().lower() == MAP_INFO_NAME:
                return obj
    for layer in getattr(doc, "layers", []) or []:
        if not hasattr(layer, "objects"):
            continue
        for obj in layer.objects:
            if str(getattr(obj, "type_", "") or "").strip().lower() == MAP_INFO_NAME:
                return obj
    return None


def is_survival_map(doc) -> bool:
    """地图模式是否为生存（map_info 的 type 属性 = survival）。"""
    info = find_map_info(doc)
    if info is None:
        return False
    values = getattr(getattr(info, "properties", None), "values", {}) or {}
    return str(values.get("type", "") or "").strip().lower() == "survival"


def apply_survival_waves(doc) -> str:
    """把编译结果写进 map_info.survivalWaves，返回写入的文本。

    没有波次节点时不写（避免清空用户手写的波次表）。
    返回空串表示什么都没写。
    """
    nodes = collect_wave_nodes(doc)
    if not nodes:
        return ""
    info = find_map_info(doc)
    if info is None:
        return ""
    text = compile_survival_waves(doc)
    props = getattr(info, "properties", None)
    if props is None:
        return ""
    props.set(SURVIVAL_WAVES_PROPERTY, text, "string")
    return text


# ---- 波次关联 → 游戏内真触发 ----
# 游戏 Wave.activate() 只刷兵，不触发任何触发器；waveGen 自身是编辑器私有节点，
# 游戏根本不认识。保存时按波次累计时间生成一个隐藏 basic 触发器：
#   delay=<累计秒>s  →  到点激活
#   alsoActivate=<目标 id>  →  真正触发播报 / 奖励等普通触发器
# 这样「波次关联」才不只是编辑器里的装饰线。

# TriggerEngine.load() 里无条件跳过的对象：它们不是触发器，写进 alsoActivate
# 会得到 "linkedTo target not found" 报错。
_NON_TRIGGER_NAMES = frozenset({
    "map_info", "attack_point", "camera_start",
})
_NON_TRIGGER_TYPES = frozenset({
    "point", "team_info", "camera_pan", "rotate", "fall", "set_team",
    "ai_allow_full_use", "disable_unit_ai", "info", "wavegen", "wavetable",
})


def _object_key_map(doc):
    """复刻节点图的唯一 key 规则：层名::object_id[#重复序号] → 对象 / 所在层。"""
    from ..document.layer import ObjectLayer
    by_key: dict[str, object] = {}
    layer_of: dict[int, object] = {}
    seen: dict[str, int] = {}
    for layer in getattr(doc, "layers", []) or []:
        if not isinstance(layer, ObjectLayer):
            continue
        for obj in layer.objects:
            base = f"{layer.name}::{obj.object_id}"
            idx = seen.get(base, 0)
            seen[base] = idx + 1
            key = base if idx == 0 else f"{base}#{idx}"
            by_key[key] = obj
            layer_of[id(obj)] = layer
    return by_key, layer_of


def _build_link_index(doc, by_key):
    """「波次关联」的引用可能是唯一 key，也可能是自定义 id / 名字 / object id。

    节点图能按名字解析，保存时的调度器也要用同一口径，否则用户写了
    ``播报第1波`` 这种引用会静默丢失，游戏里播报就不会响。
    """
    index = dict(by_key)
    for layer in getattr(doc, "layers", []) or []:
        if not hasattr(layer, "objects"):
            continue
        for obj in layer.objects:
            props = getattr(obj, "properties", None)
            aliases = [
                getattr(obj, "name", ""), getattr(obj, "label", ""),
                str(getattr(obj, "object_id", "") or ""),
                props.get("id") if props is not None else "",
                props.get("name") if props is not None else "",
                props.get("label") if props is not None else "",
            ]
            for raw in aliases:
                alias = str(raw or "").strip()
                if alias:
                    index.setdefault(alias, obj)
                    index.setdefault(alias.lower(), obj)
    return index


def _is_dispatch(obj) -> bool:
    return str(getattr(getattr(obj, "properties", None), "values", {}).get(
        WAVE_DISPATCH_MARK_PROPERTY, "") or "").strip() == "1"


def _is_game_trigger_target(obj) -> bool:
    name = str(getattr(obj, "name", "") or "").strip().lower()
    typ = str(getattr(obj, "type_", "") or "").strip().lower()
    if name in _NON_TRIGGER_NAMES or typ in _NON_TRIGGER_TYPES:
        return False
    return getattr(obj, "properties", None) is not None


def _is_spawn_proxy(obj) -> bool:
    return str(getattr(getattr(obj, "properties", None), "values", {}).get(
        WAVE_SPAWN_PROXY_MARK_PROPERTY, "") or "").strip() == "1"


def _is_attack_point(obj) -> bool:
    return str(getattr(obj, "name", "") or "").strip().lower() == "attack_point"


def _survival_team(doc) -> str:
    """敌人队伍：优先取 ai=survival 的 team_X_info，兜底 team=1。"""
    for layer in getattr(doc, "layers", []) or []:
        if not hasattr(layer, "objects"):
            continue
        for obj in layer.objects:
            if not str(getattr(obj, "name", "") or "").strip().lower().startswith("team_"):
                continue
            props = getattr(obj, "properties", None)
            if props is None or str(props.get("ai") or "").strip().lower() != "survival":
                continue
            team = str(props.get("team") or "").strip()
            if team:
                return team
    return "1"


# 生成单位触发器只要「刷兵行为」属性；连线类属性会在影子触发器上重新接，
# 从原对象抄过来反而会留下原对象没有意义的 alsoActivate / activatedBy。
_PROXY_DROP_PROPS = frozenset({
    "id", "alsoActivate", "activateIds", "activatedBy", "disabledBy",
    "enabledBy", "linkedTo", "name", "label", "comment", "target",
})


def _make_spawn_proxy(src, object_id: int, used: set[str], team: str):
    """给一个 attack_point 制作影子刷兵触发器；没有实际刷兵配置时返回 None。"""
    from ..document.map_document import allocate_trigger_id
    from ..document.map_object import MapObject

    props = getattr(src, "properties", None)
    values = getattr(props, "values", {}) or {}
    clone: dict[str, str] = {}
    for raw_key, raw_val in values.items():
        key = str(raw_key).strip()
        if (not key or key in HIDDEN_EDITOR_PROPERTIES
                or key in _PROXY_DROP_PROPS or key.lower().startswith("rwmap_")):
            continue
        clone[key] = str(raw_val)
    if not str(clone.get("spawnUnits") or "").strip():
        return None
    typ = str(getattr(src, "type_", "") or "").strip()
    if not typ or typ.lower() in _NON_TRIGGER_TYPES:
        return None

    proxy = MapObject(
        object_id=object_id,
        name=f"wave_spawn_{object_id}",
        type_=typ,
        x=getattr(src, "x", 0.0), y=getattr(src, "y", 0.0),
        width=getattr(src, "width", 0.0), height=getattr(src, "height", 0.0),
        rotation=getattr(src, "rotation", 0.0),
        shape=getattr(src, "shape", "point"), visible=False,
    )
    target = proxy.properties
    for key, val in clone.items():
        target.set(key, val, "string")
    if not str(target.get("team") or "").strip():
        target.set("team", team, "string")
    target.set("id", allocate_trigger_id(proxy, used), "string")
    target.set(WAVE_SPAWN_PROXY_MARK_PROPERTY, "1", "string")
    target.set(WAVE_SPAWN_PROXY_SOURCE_PROPERTY, str(src.object_id), "string")
    return proxy


def ensure_attack_points(doc) -> int:
    """生存图保存前自动修复出兵点：游戏只认 ``name=attack_point``。

    反编译 ``TriggerEngine.load()`` 确认：只有 ``name == "attack_point"``
    的对象才会被收进 ``attackPoints``。一张开了官方倒计时的生存图若一个
    出兵点都没有，第一波 ``Wave.activate()`` 会调用
    ``updateWaveSpawnPoint()``，内部 ``deterministicRandomInt(0, -1)``
    打印 ``min>max`` 后 ``attackPoints.get(0)`` 越界 —— 游戏直接闪退。
    堆栈见 ``rwpp-log.txt`` 的 ``Activating wave / min>max /
    IndexOutOfBoundsException``。

    整合体同步编辑会把代表对象的名字写到每个成员（用户把出兵点改名成
    「刷虫子」时 25 个点全被改掉），这类名字漂移在保存时统一纠正回来：
    优先把「被波次关联引用、type=unitAdd」的对象恢复成 attack_point。

    返回本次改回名字的对象数量；幂等，已有 attack_point 时直接返回 0。
    """
    info = find_map_info(doc)
    if info is None:
        return 0
    if str(info.properties.get("type") or "").strip().lower() != "survival":
        return 0
    # 没有波次编排的节点就不插手（普通生存图可能故意用纯 unitAdd 刷兵）
    if not collect_wave_nodes(doc) and not collect_wave_table_nodes(doc):
        return 0

    all_objs = [
        o for layer in (getattr(doc, "layers", []) or [])
        if hasattr(layer, "objects")
        for o in layer.objects
    ]
    if any(_is_attack_point(o) for o in all_objs):
        return 0

    # 候选：被任一波次「波次关联」引用、且是刷兵类触发器的对象。
    refs: set[str] = set()
    for node in collect_wave_nodes(doc):
        refs.update(_split_refs(
            getattr(node.obj, "properties", None).get(WAVE_LINK_PROPERTY, "")))
    if not refs:
        # 没连线时也兜底：所有 unitAdd 对象都当作出兵点候选（官方图惯例）
        candidates = [
            o for o in all_objs
            if str(getattr(o, "type_", "") or "").strip().lower() == "unitadd"
            and getattr(o, "properties", None) is not None
        ]
    else:
        by_key, _ = _object_key_map(doc)
        index = _build_link_index(doc, by_key)
        seen: set[int] = set()
        candidates = []
        for ref in refs:
            obj = (index.get(ref) or index.get(str(ref).strip().lower())
                   or index.get(str(ref)))
            if obj is None or id(obj) in seen:
                continue
            if str(getattr(obj, "type_", "") or "").strip().lower() != "unitadd":
                continue
            seen.add(id(obj))
            candidates.append(obj)

    layers = [l for l in (getattr(doc, "layers", []) or [])
              if hasattr(l, "objects")]
    fixed = 0
    for obj in candidates:
        if _is_attack_point(obj):
            continue
        obj.name = "attack_point"
        for layer in layers:
            if any(o is obj for o in layer.objects):
                layer.dirty = True
                break
        fixed += 1
    if fixed:
        doc.dirty = True
    return fixed


def sync_wave_dispatch_triggers(doc) -> int:
    """把每个波次的「波次关联」同步成游戏可执行的定时触发器。

    普通触发器直接写进调度器的 alsoActivate；名为 attack_point 的对象被游戏
    按名字跳过，会复制一份影子刷兵触发器（同名属性 + 同坐标，team 默认取
    ai=survival 的队伍）挂到调度器上，这样 25 个点各自刷兵才真的生效。

    返回本次生成的调度触发器数量。幂等：先删掉旧的调度 / 影子对象再重建，
    改了波次时间 / 关联目标后保存会自动更新，不会叠加重复触发器。
    """
    from ..document.layer import ObjectLayer
    from ..document.map_document import allocate_trigger_id, used_trigger_ids
    from ..document.map_object import MapObject

    layers = [l for l in (getattr(doc, "layers", []) or [])
              if isinstance(l, ObjectLayer)]
    if not layers:
        return 0

    # 旧调度器 / 影子刷兵触发器整体清掉（只认私有标记，绝不碰用户自己的对象）
    for layer in layers:
        kept = [o for o in layer.objects
                if not _is_dispatch(o) and not _is_spawn_proxy(o)]
        if len(kept) != len(layer.objects):
            layer.objects[:] = kept
            layer.dirty = True
            doc.dirty = True

    timeline = _wave_timeline(doc)
    if not timeline:
        return 0

    by_key, layer_of = _object_key_map(doc)
    link_index = _build_link_index(doc, by_key)
    used = used_trigger_ids(doc)
    host = next((l for l in layers if l.name == "Triggers"), layers[0])
    max_oid = max((o.object_id for l in layers for o in l.objects), default=0)
    team = _survival_team(doc)
    created = 0

    for wave, seconds in timeline:
        refs = _split_refs(
            getattr(wave.obj, "properties", None).get(WAVE_LINK_PROPERTY, ""))
        targets: list[str] = []
        for ref in refs:
            ref = str(ref).strip()
            obj = by_key.get(ref) or link_index.get(ref) or link_index.get(ref.lower())
            if obj is None:
                continue
            # attack_point 虽然配了 unitAdd，但游戏加载触发器时会按名字跳过；
            # 复制一份影子触发器，让它保留刷兵点身份的同时真的生成单位。
            if _is_attack_point(obj):
                max_oid += 1
                proxy = _make_spawn_proxy(obj, max_oid, used, team)
                if proxy is None:
                    max_oid -= 1
                    continue
                host.objects.append(proxy)
                host.dirty = True
                doc.dirty = True
                tid = proxy.properties.get("id")
                if tid and tid not in targets:
                    targets.append(tid)
                continue
            if not _is_game_trigger_target(obj):
                continue
            tid = obj.properties.get("id") or allocate_trigger_id(obj, used)
            owner_layer = layer_of.get(id(obj))
            if owner_layer is not None:
                owner_layer.dirty = True
            if tid and tid not in targets:
                targets.append(tid)
        if not targets:
            continue

        max_oid += 1
        obj = MapObject(
            object_id=max_oid,
            name=f"wave_dispatch_{created + 1}",
            type_="basic", shape="point", x=0, y=0, visible=False,
        )
        props = obj.properties
        props.set("id", allocate_trigger_id(obj, used), "string")
        props.set("delay", f"{seconds}s", "string")
        props.set("alsoActivate", ", ".join(targets), "string")
        props.set(WAVE_DISPATCH_MARK_PROPERTY, "1", "string")
        props.set(WAVE_DISPATCH_WAVE_PROPERTY, wave.name, "string")
        host.objects.append(obj)
        host.dirty = True
        doc.dirty = True
        created += 1
    return created


# ---- 官方波次表节点（waveTable）----
# 与 waveGen（一波一节点串成链）不同，waveTable 是**一个节点承载整段原版
# survivalWaves 文本**，外观和普通触发器一样、可在节点图连线，但 visible=false
# 不在地图画布显示。文本存在私有属性里，保存时同步到 map_info.survivalWaves，
# 让游戏照常显示官方倒计时。两种编排方式可共存：waveTable 优先（见 _sync）。

def is_wave_table_node(obj) -> bool:
    """是不是「官方波次表」节点。

    内存里新建时 type_ = "waveTable"；重开时写盘映射成空 type，靠私有标记
    ``rwmap_wave_table`` 还原识别。
    """
    from ..document.properties import WAVE_TABLE_TYPE
    if getattr(obj, "type_", "") == WAVE_TABLE_TYPE:
        return True
    props = getattr(obj, "properties", None)
    values = getattr(props, "values", {}) or {}
    return str(values.get(WAVE_TABLE_MARK_PROPERTY, "") or "").strip() == "1"


def collect_wave_table_nodes(doc) -> list:
    """跨所有对象层收集 waveTable 节点。"""
    return [
        obj
        for layer in (getattr(doc, "layers", []) or [])
        if hasattr(layer, "objects")
        for obj in layer.objects
        if is_wave_table_node(obj)
    ]


def wave_table_text(doc) -> str:
    """取 waveTable 节点里的原版波次文本（多个节点则按收集顺序拼接）。"""
    chunks: list[str] = []
    for obj in collect_wave_table_nodes(doc):
        values = getattr(getattr(obj, "properties", None), "values", {}) or {}
        text = str(values.get(WAVE_TABLE_TEXT_PROPERTY, "") or "").strip("\n")
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def apply_wave_table(doc) -> str:
    """把 waveTable 节点的文本同步到 map_info.survivalWaves，返回写入文本。

    没有 waveTable 节点或文本为空时不写（避免清空原版波次表）。
    """
    text = wave_table_text(doc)
    if not text:
        return ""
    info = find_map_info(doc)
    if info is None:
        return ""
    props = getattr(info, "properties", None)
    if props is None:
        return ""
    props.set(SURVIVAL_WAVES_PROPERTY, text, "string")
    return text


# ---- 地图模式切换：自动增删第一波 ----
# 生存模式至少需要一个波次节点作为编排起点。切到 survival 时若一个都没有，
# 自动补一个空的「第一波」；切走时把编排节点全部摘掉，避免非生存图里残留
# 只在节点图里有意义的私有数据。两者只改文档对象，撤销由调用方的多层快照负责。

def _map_all_objects(doc):
    for layer in getattr(doc, "layers", []) or []:
        if not hasattr(layer, "objects"):
            continue
        for obj in layer.objects:
            yield layer, obj


def ensure_first_wave(doc):
    """生存模式自动补一个空的「第一波」节点；已有波次节点时不重复创建。

    返回新建的 MapObject；没有 map_info / 无可写对象层时返回 None。
    """
    if collect_wave_nodes(doc) or collect_wave_table_nodes(doc):
        return None
    info = find_map_info(doc)
    if info is None:
        return None
    layer = next((l for l, o in _map_all_objects(doc) if o is info), None)
    if layer is None:
        return None

    from ..document.map_object import MapObject
    new_id = max((o.object_id for _l, o in _map_all_objects(doc)), default=0) + 1
    name = "第一波"
    obj = MapObject(
        object_id=new_id, name=name, type_=WAVE_GEN_TYPE,
        shape="point", x=0, y=0, visible=False,
    )
    props = obj.properties
    props.set(WAVE_MARK_PROPERTY, "1", "string")
    props.set(WAVE_NAME_PROPERTY, name, "string")
    props.set(WAVE_TIME_PROPERTY, "0:10", "string")
    props.set(WAVE_UNITS_PROPERTY, "", "string")
    # 摆在现有节点右侧；已有节点时取最大 x，没有时用 300（避开 map_info 的 0,0）。
    xs: list[int] = []
    for _l, other in _map_all_objects(doc):
        raw = other.properties.get(EDITOR_NODE_POS_PROPERTY)
        if raw:
            try:
                xs.append(int(str(raw).split(",")[0]))
            except (TypeError, ValueError):
                pass
    x = (max(xs) + 308) if xs else 300
    props.set(EDITOR_NODE_POS_PROPERTY, f"{x},0", "string")
    layer.objects.append(obj)
    layer.dirty = True
    doc.dirty = True
    return obj


def hide_wave_nodes(doc) -> int:
    """把已有波次编排节点的可见性归一为 False，返回修正个数。

    老地图或早期用脚本建的地图可能写成 visible=1，重开后这些点会跑到地图
    画布左上角。节点图渲染不读 visible，所以这里只影响画布，不影响编排。
    已经隐藏的节点不动，不会无谓地把文档标脏。
    """
    targets = {n.key for n in collect_wave_nodes(doc)}
    targets.update(id(o) for o in collect_wave_table_nodes(doc))
    if not targets:
        return 0
    changed = 0
    for layer, obj in _map_all_objects(doc):
        if id(obj) in targets and obj.visible:
            obj.visible = False
            layer.dirty = True
            changed += 1
    if changed:
        doc.dirty = True
    return changed


def remove_wave_nodes(doc) -> int:
    """摘掉所有波次编排节点（waveGen + waveTable），返回删除个数。"""
    # collect_wave_nodes 返回 WaveNode 包装，其 key 才是底层对象的 id；
    # collect_wave_table_nodes 返回的直接是 MapObject，不能混用。
    targets = {n.key for n in collect_wave_nodes(doc)}
    targets.update(id(o) for o in collect_wave_table_nodes(doc))
    if not targets:
        return 0
    removed = 0
    for layer in getattr(doc, "layers", []) or []:
        if not hasattr(layer, "objects"):
            continue
        kept = [o for o in layer.objects if id(o) not in targets]
        removed += len(layer.objects) - len(kept)
        if len(kept) != len(layer.objects):
            layer.objects[:] = kept
            layer.dirty = True
    if removed:
        doc.dirty = True
    return removed
