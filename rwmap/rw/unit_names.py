"""单位中文名映射：raw name（英文内部名）<-> 中文显示名。

Rusted Warfare 只认英文内部名（如 ``heavyTank``），界面却要显示 / 输入中文。
规则：
* 写回 TMX 永远是英文内部名；
* 读取单位（``unitType`` / ``spawnUnits``）显示时转中文；
* 用户输入中文时，落库前转回 raw。
* 「随机生成单位」独立属性用一串英文单位列表表达，导出时自动展开成
  原生等概率 ``spawnUnits``（链式 spawnChance），导入时再反推回来。
"""
from __future__ import annotations

import re

from ..utils.paths import CONFIG_DIR, load_json

# 兜底表：游戏文件缺失（没装游戏 / 换机器）时也能翻译常用单位。
_BUILTIN_CN: dict[str, str] = {
    "builder": "建造者",
    "worker": "工人",
    "tank": "坦克",
    "lightTank": "轻型坦克",
    "heavyTank": "重型坦克",
    "mammothTank": "猛犸坦克",
    "experimentalTank": "实验坦克",
    "laserTank": "激光坦克",
    "missileTank": "导弹坦克",
    "flameTank": "火焰坦克",
    "hoverTank": "悬浮坦克",
    "artillery": "火炮",
    "minelayer": "布雷车",
    "scout": "侦察车",
    "scoutTank": "侦察坦克",
    "turret": "炮塔",
    "heavyTurret": "重型炮塔",
    "samSite": "防空导弹",
    "flyingMech": "飞行机甲",
    "battleMech": "战斗机甲",
    "experimentalMech": "实验机甲",
    "airship": "飞艇",
    "fighter": "战斗机",
    "bomber": "轰炸机",
    "gunship": "武装直升机",
    "transport": "运输机",
    "commandCenter": "指挥中心",
    "commandPost": "指挥哨",
    "factory": "工厂",
    "heavyFactory": "重型工厂",
    "powerPlant": "发电厂",
    "supplyDepot": "补给库",
    "lab": "实验室",
    "repairBay": "维修舱",
    "extractor": "资源采集器",
    "missileSilo": "导弹发射井",
}

def _load_catalog() -> list[dict]:
    """读取 ``config/unit_names.json``（由 probe/build_unit_catalog.py 生成）。"""
    data = load_json(CONFIG_DIR / "unit_names.json", {})
    rows = data.get("units") if isinstance(data, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("raw")]


# 完整单位目录（含隐藏的内部 / 战役变体）
CATALOG: list[dict] = _load_catalog()
CATALOG_BY_RAW: dict[str, dict] = {r["raw"]: r for r in CATALOG}

# 游戏资源扫描带进来的开发 / 测试占位物：游戏本身不认这些内部名。
# 写进刷兵列表会让 Rusted Warfare 报 "Unknown wave format ..." 直接打不开地图，
# 所以既不能出现在选择器里（哪怕勾了「显示内部变体」），也不能通过单位名校验。
NON_GAME_UNITS: frozenset[str] = frozenset({
    "custom_tank",             # 游戏自带 test_tank 资源扫出来的测试车
    "example_faction_picker",  # 开发用阵营选择器
    "flare_10s",               # 杂项信号弹 / 特效物体，不是可刷单位
})

# 界面分组顺序（与 config/unit_names.json、docs 权威清单保持一致）
GROUPS: tuple[str, ...] = ("陆军", "海军", "空军", "机甲", "工厂",
                           "防御", "建筑", "虫族", "其他")

# raw -> 中文：兜底表打底，游戏官方翻译覆盖
RAW_TO_CN: dict[str, str] = dict(_BUILTIN_CN)
for _row in CATALOG:
    _cn = (_row.get("cn") or "").strip()
    if _cn:
        RAW_TO_CN[_row["raw"]] = _cn

CN_TO_RAW: dict[str, str] = {}
# 先登记可见单位，避免中文名撞车时指到隐藏的战役变体上
for _row in sorted(CATALOG, key=lambda r: bool(r.get("hidden"))):
    _cn = (_row.get("cn") or "").strip()
    if _cn:
        CN_TO_RAW.setdefault(_cn, _row["raw"])
for _raw, _cn in _BUILTIN_CN.items():
    CN_TO_RAW.setdefault(_cn, _raw)


def is_hidden(raw: str) -> bool:
    """内部 / 战役变体（默认不在选择器里露出）。"""
    row = CATALOG_BY_RAW.get(raw)
    return bool(row and row.get("hidden"))


def group_of(raw: str) -> str:
    row = CATALOG_BY_RAW.get(raw)
    return (row or {}).get("group") or "其他"


def catalog(include_hidden: bool = False) -> list[dict]:
    """按分组顺序返回单位条目（默认只给玩家能用的）。"""
    rows = [r for r in CATALOG
            if r["raw"].lower() not in NON_GAME_UNITS
            and (include_hidden or not r.get("hidden"))]
    order = {g: i for i, g in enumerate(GROUPS)}
    return sorted(rows, key=lambda r: (order.get(r.get("group") or "其他", 99),
                                       r["raw"].lower()))


def display_name(raw: str) -> str:
    """中文名优先，其次官方英文名，最后 raw。"""
    row = CATALOG_BY_RAW.get(raw) or {}
    return (row.get("cn") or row.get("en") or raw)

# 解析 spawnUnits 参数：spawnChance=0.3333 / maxSpawnLimit=1
_PARAM_RE = re.compile(r"spawnChance\s*=\s*([0-9.]+)|maxSpawnLimit\s*=\s*(\d+)")


def _split_top(text: str) -> list[str]:
    """按顶层逗号切分（括号里的逗号不算），用于 spawnUnits 多段。"""
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in text or "":
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [x for x in out if x]


def to_cn(raw: str) -> str:
    """raw 名 -> 中文；未知原样返回。"""
    return RAW_TO_CN.get(raw, raw)


def to_raw(text: str) -> str:
    """中文或英文 -> raw；未知原样返回（保留大小写）。"""
    text = (text or "").strip()
    if not text:
        return text
    if text in CN_TO_RAW:
        return CN_TO_RAW[text]
    low = text.lower()
    for raw in RAW_TO_CN:
        if raw.lower() == low:
            return raw
    return text


def is_known_unit(raw: str) -> bool:
    """raw 名是否在游戏单位目录里（大小写不敏感）。

    注意 ``config/unit_names.json`` 来自游戏本体资源扫描，不含玩家另外装的
    mod 里的自定义单位；这里只做「明显拼错」的提示，不阻断保存。
    """
    name = (raw or "").strip()
    if not name:
        return False
    if name.lower() in NON_GAME_UNITS:
        return False
    if name in CATALOG_BY_RAW:
        return True
    low = name.lower()
    return any(r.lower() == low for r in CATALOG_BY_RAW)


def unknown_units(text: str) -> list[str]:
    """从 spawnUnits / 波次刷兵文本里挑出游戏不认识的单位名。

    支持 ``lecT31*100``、``6 lecT31``、``lecT31(spawnChance=0.5)`` 等格式，
    返回原始写法的去重列表（保持出现顺序）。空列表表示全部有效。
    """
    bad: list[str] = []
    seen: set[str] = set()
    for part in _split_top(text or ""):
        body = part.strip()
        if not body:
            continue
        # 去括号参数
        body = body.partition("(")[0].strip()
        # 数量前缀：6 heavyTank
        m = re.match(r"^\d+\s+(.+)$", body)
        if m:
            body = m.group(1).strip()
        elif "*" in body:
            body = body.rpartition("*")[0].strip()
        if not body:
            continue
        raw = to_raw(body)
        if is_known_unit(raw):
            continue
        key = raw.lower()
        if key not in seen:
            seen.add(key)
            bad.append(raw)
    return bad


def split_units(text: str) -> list[str]:
    """中文 / 英文单位列表（逗号、顿号、空格分隔）-> raw 列表。"""
    pieces = re.split(r"[,，、\s]+", (text or "").strip())
    return [to_raw(p) for p in pieces if p.strip()]


def normalize_spawn_units(text: str) -> str:
    """把 spawnUnits 文本里的单位名统一成 raw（支持中文输入）。

    只替换各段括号前的单位名，括号里的参数（spawnChance 等）不动。
    """
    out = []
    for part in _split_top(text):
        if not part:
            continue
        name, sep, rest = part.partition("(")
        name = to_raw(name.strip())
        out.append(name + (sep + rest if sep else ""))
    return ",".join(out)


def parse_spawn_units(text: str) -> list[tuple[str, float | None, int | None]]:
    """解析 spawnUnits 为 [(name, spawnChance, maxSpawnLimit), ...]。"""
    out = []
    for part in _split_top(text):
        if not part:
            continue
        name, _sep, rest = part.partition("(")
        name = to_raw(name.strip())
        chance = None
        mx = None
        for m in _PARAM_RE.finditer(rest):
            if m.group(1) is not None:
                chance = float(m.group(1))
            if m.group(2) is not None:
                mx = int(m.group(2))
        out.append((name, chance, mx))
    return out


def equal_chances(n: int) -> list[float]:
    """N 选 1 的链式 spawnChance 序列：1/n, 1/(n-1), ..., 1.0。"""
    n = max(1, int(n))
    return [round(1.0 / (n - i), 6) for i in range(n)]


def build_spawn_units(raw_units: list[str]) -> str:
    """把 raw 单位列表拼成等概率 spawnUnits 串。"""
    units = [u for u in raw_units if u]
    if not units:
        return ""
    chances = equal_chances(len(units))
    return ",".join(
        f"{u}(spawnChance={c:g},maxSpawnLimit=1)" for u, c in zip(units, chances)
    )


def parse_fixed_units(text: str) -> list[tuple[str, int, str]]:
    """解析「生成单位」的 spawnUnits：``heavyTank*10,builder*2``。

    每段拆成 ``(raw, 数量, 括号参数原样)``；括号里的 spawnChance 等参数
    不在本函数解释，原样保留，避免重排单位时丢掉地图作者写的额外参数。
    """
    out: list[tuple[str, int, str]] = []
    for part in _split_top(text):
        name, sep, rest = part.partition("(")
        params = ("(" + rest) if sep else ""
        name = name.strip()
        m = re.match(r"^(.*?)\s*\*\s*(\d+)$", name)
        count = 1
        if m:
            name, count = m.group(1).strip(), int(m.group(2))
        raw = to_raw(name)
        if raw:
            out.append((raw, max(1, count), params))
    return out


def format_fixed_units(units: list[tuple[str, int, str]]) -> str:
    """``[(raw, 数量, 括号参数)]`` -> ``heavyTank*10,builder*2(...)``。"""
    parts = []
    for raw, count, params in units:
        base = raw if count <= 1 else f"{raw}*{count}"
        parts.append(base + (params or ""))
    return ",".join(parts)


def parse_wave_units(text: str) -> list[tuple[str, int]]:
    """解析生存波次的刷兵列表 -> ``[(raw, 数量), ...]``。

    同时接受原版 ``6 heavyTank,2 bugMelee``、生成器风格 ``heavyTank*6``
    和中文名；数量缺省为 1。写回时用 :func:`format_wave_units`，保证
    ``survivalWaves`` 编译出来的仍是游戏认识的原版格式。
    """
    out: list[tuple[str, int]] = []
    for part in _split_top(text):
        body = part.strip()
        if not body:
            continue
        count = 1
        m = re.match(r"^(\d+)\s+(.+)$", body)
        if m:
            count, body = max(1, int(m.group(1))), m.group(2).strip()
        elif "*" in body:
            name, _, num = body.rpartition("*")
            if name.strip() and num.strip().isdigit():
                body, count = name.strip(), max(1, int(num.strip()))
        raw = to_raw(body)
        if raw:
            out.append((raw, count))
    return out


def format_wave_units(units: list[tuple[str, int]]) -> str:
    """``[(raw, 数量)]`` -> ``6 heavyTank,1 bugMelee``（原版刷兵格式）。

    游戏要求**每段都带数量**：数量为 1 也要写 ``1 单位名``，裸单位名会报
    ``Unknown wave format`` 让整张地图加载失败。
    """
    return ",".join(f"{max(1, count)} {raw}" for raw, count in units)


# ---------------------------------------------------------------- 权重 / 数量
# 「随机生成单位」里写成 单位:权重#数量（权重分隔符也接受 ×/*，数量必须 #）。
# 例：重型坦克:3#2,坦克:2,侦察车:1  ->  权重 3:2:1，其中重型坦克每次刷 2 个。
# 权重省略视为 1，数量省略视为 1；# 是编辑器内部记号，写盘时展开成 RW 的
# `单位*数量(spawnChance=...,maxSpawnLimit=数量)`。
_WEIGHT_RE = re.compile(r"^(?P<name>.*?)\s*[:：×*xX]\s*(?P<w>\d+(?:\.\d+)?)$")
_COUNT_RE = re.compile(r"^(?P<body>.*?)\s*#\s*(?P<n>\d+)$")


def parse_units(text: str) -> list[tuple[str, float, int]]:
    """``重型坦克:3#2, 坦克:2`` -> ``[("heavyTank",3.0,2),("tank",2.0,1)]``。

    权重省略按 1、数量省略按 1；同一单位写两次合并权重（累加），数量取最大。
    """
    out: list[tuple[str, float, int]] = []
    index: dict[str, int] = {}
    for piece in re.split(r"[,，、\s]+", (text or "").strip()):
        piece = piece.strip()
        if not piece:
            continue
        count = 1
        cm = _COUNT_RE.match(piece)
        if cm:
            piece = cm.group("body").strip()
            count = max(1, int(cm.group("n")))
        wm = _WEIGHT_RE.match(piece)
        if wm:
            name, weight = wm.group("name").strip(), float(wm.group("w"))
        else:
            name, weight = piece, 1.0
        raw = to_raw(name)
        if not raw:
            continue
        if raw in index:                      # 重复项合并
            i = index[raw]
            prev_raw, prev_w, prev_n = out[i]
            out[i] = (raw, prev_w + weight, max(prev_n, count))
        else:
            index[raw] = len(out)
            out.append((raw, weight, count))
    return out


def parse_weighted(text: str) -> list[tuple[str, float]]:
    """二元组兼容出口：数量被丢弃（默认 1）。``[("heavyTank", 3.0)]``。"""
    return [(raw, w) for raw, w, _n in parse_units(text)]


def format_units(pairs: list[tuple[str, float, int]]) -> str:
    """``[("heavyTank",3,2)]`` -> ``heavyTank:3#2``；权重/数量为 1 时省略。"""
    parts = []
    for raw, weight, count in pairs:
        seg = raw
        if (weight or 0) != 1:
            seg += f":{('%g' % weight)}"
        if (count or 1) != 1:
            seg += f"#{count}"
        parts.append(seg)
    return ",".join(parts)


def format_weighted(pairs: list[tuple[str, float]]) -> str:
    """二元组兼容出口：输出永远不带数量。"""
    return format_units([(raw, w, 1) for raw, w in pairs])


def overall_chances(weights: list[float]) -> list[float]:
    """权重 -> 各自实际出现概率（归一化，和为 1）。"""
    ws = [max(0.0, float(w)) for w in weights]
    total = sum(ws)
    if total <= 0:
        return [1.0 / len(ws)] * len(ws) if ws else []
    return [w / total for w in ws]


def weighted_chances(weights: list[float]) -> list[float]:
    """权重 -> 链式 spawnChance 序列（条件概率，最后一个恒为 1）。

    RW 的「等概率」其实是这条链：第 i 个以 ``c_i`` 概率生成，抽中就把
    ``maxSpawnLimit=1`` 的名额用满、后面的不再生成。要让总体概率等于
    ``w_i / Σw``，条件概率就得是 ``p_i / (1 - Σ_{j<i} p_j)``。
    """
    ws = [max(0.0, float(w)) for w in weights]
    p = overall_chances(ws)
    out: list[float] = []
    remain = 1.0
    for i, pi in enumerate(p):
        if i == len(p) - 1:
            out.append(1.0)
            break
        c = pi / remain if remain > 1e-9 else 1.0
        c = round(min(1.0, max(0.0, c)), 4)
        out.append(c)
        remain *= (1.0 - c)
    return out


def build_spawn_units_weighted(pairs: list[tuple[str, float]],
                               counts: list[int] | None = None) -> str:
    """按权重生成 spawnUnits 串；权重 ≤0 的单位视为「不参与随机」被剔除。

    ``counts`` 可选：与 pairs 等长的数量列表。数量 n>1 时写成
    ``单位*n(spawnChance=c,maxSpawnLimit=n)``（RW 的真实刷兵语法，见
    「抽奖生存」等原图），n=1 时保持老的 ``maxSpawnLimit=1`` 写法。
    """
    live = [(u, w) for u, w in pairs if u and (w or 0) > 0]
    if not live:
        return ""
    chances = weighted_chances([w for _u, w in live])
    parts = []
    for i, ((u, _w), c) in enumerate(zip(live, chances)):
        n = int(counts[i]) if counts is not None and i < len(counts) else 1
        n = max(1, n)
        if n > 1:
            parts.append(f"{u}*{n}(spawnChance={c:g},maxSpawnLimit={n})")
        else:
            parts.append(f"{u}(spawnChance={c:g},maxSpawnLimit=1)")
    return ",".join(parts)


def build_spawn_units_full(pairs: list[tuple[str, float, int]]) -> str:
    """三元组入口：``[(raw, 权重, 数量)]`` -> spawnUnits 串。"""
    return build_spawn_units_weighted(
        [(u, w) for u, w, _n in pairs], [n for _u, _w, n in pairs])


_NAME_COUNT_RE = re.compile(r"^(?P<raw>.+?)\s*\*\s*(?P<n>\d+)$")


def units_from_spawn_units(text: str) -> list[tuple[str, float, int]] | None:
    """能安全反推时还原 ``[(raw, 权重, 数量)]``，否则 None。

    只接受「抽签链」：每段都带 ``spawnChance``、最后一段 c=1，且数量写法
    自洽——``单位*N`` 必须配 ``maxSpawnLimit=N``；无 ``*N`` 时必须是
    ``maxSpawnLimit=1``。并要求反推结果能**逐字还原**原串，避免把带
    ``offsetRandomX`` / ``falling`` 的普通刷兵串误判成权重表。
    """
    parsed = parse_spawn_units(text)
    if len(parsed) < 2:
        return None
    names: list[str] = []
    counts: list[int] = []
    for name, c, mx in parsed:
        m = _NAME_COUNT_RE.match(name)
        if m:
            n = max(1, int(m.group("n")))
            if mx != n:                       # *N 必须配 maxSpawnLimit=N
                return None
            names.append(m.group("raw"))
            counts.append(n)
        else:
            if mx != 1:                       # 没写 *N 就只能是 maxSpawnLimit=1
                return None
            names.append(name)
            counts.append(1)
        if c is None:
            return None
    chances = [float(c) for _n, c, _m in parsed]
    if abs(chances[-1] - 1.0) > 1e-6:
        return None
    if any(c <= 0 for c in chances[:-1]):
        return None
    probs: list[float] = []
    remain = 1.0
    for c in chances:
        probs.append(remain * c)
        remain *= (1.0 - c)
    base = min(probs)
    if base <= 1e-9:
        return None
    ratios = [p / base for p in probs]
    # 比值最小的一定是 1，但对应权重未必是 1（3:2 的比值是 1.5:1.0），
    # 所以从小到大找一个能让全部比值变整数的缩放系数。
    weights: list[float] = []
    for factor in range(1, 101):
        scaled = [r * factor for r in ratios]
        if all(abs(s - round(s)) <= 0.02 and 1 <= round(s) <= 999
               for s in scaled):
            weights = [float(round(s)) for s in scaled]
            break
    if not weights:
        return None
    triples = [(n, w, cnt) for n, w, cnt in zip(names, weights, counts)]
    rebuilt = parse_spawn_units(build_spawn_units_full(triples))
    # 用数值比对而不是字符串比对：概率可能写成 0.333333 也可能写成 0.3333
    if [n for n, _c, _m in rebuilt] != [name for name, _c, _m in parsed]:
        return None
    for (_n, c, _m), (_n2, c2, _m2) in zip(parsed, rebuilt):
        if c2 is None or abs(float(c) - float(c2)) > 1e-4:
            return None
    return triples


def weights_from_spawn_units(text: str) -> list[tuple[str, float]] | None:
    """二元组兼容出口：反推时丢弃数量。"""
    triples = units_from_spawn_units(text)
    return [(u, w) for u, w, _n in triples] if triples else None


def describe_weights(pairs: list[tuple[str, float]]) -> str:
    """``重型坦克 50%、坦克 33.3%、侦察车 16.7%``（给界面提示用）。"""
    live = [p for p in pairs if (p[1] or 0) > 0]
    if not live:
        return ""
    chances = overall_chances([w for _u, w in live])
    return "、".join(
        f"{to_cn(u)} {('%.4g' % (p * 100))}%" for (u, _w), p in zip(live, chances)
    )


def is_equal_chances(parsed: list[tuple[str, float | None, int | None]]) -> bool:
    """判断解析结果是否为「等概率 N 选 1」形式（可反推回 randomUnits）。"""
    if len(parsed) < 2:
        return False
    if any(mx != 1 for _n, _c, mx in parsed):
        return False
    n = len(parsed)
    for i, (name, chance, _mx) in enumerate(parsed):
        if chance is None:
            return False
        if abs(chance - 1.0 / (n - i)) > 1e-3:
            return False
    return True
