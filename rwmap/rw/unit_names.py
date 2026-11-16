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

# 界面分组顺序
GROUPS: tuple[str, ...] = ("陆军", "机甲", "空军", "海军", "防御",
                           "经济", "建筑", "核武器", "虫族", "其他")

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
    rows = [r for r in CATALOG if include_hidden or not r.get("hidden")]
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


# ---------------------------------------------------------------- 权重
# 「随机生成单位」里写成 单位:权重（也接受 ×/*），省略权重视为 1。
# 例：重型坦克:3,坦克:2,侦察车:1  ->  50% / 33.3% / 16.7%
_WEIGHT_RE = re.compile(r"^(?P<name>.*?)\s*[:：×*xX]\s*(?P<w>\d+(?:\.\d+)?)$")


def parse_weighted(text: str) -> list[tuple[str, float]]:
    """``重型坦克:3, 坦克:2`` -> ``[("heavyTank", 3.0), ("tank", 2.0)]``。

    省略权重按 1 处理；同一个单位写两次会合并权重（累加）。
    """
    out: list[tuple[str, float]] = []
    index: dict[str, int] = {}
    for piece in re.split(r"[,，、\s]+", (text or "").strip()):
        piece = piece.strip()
        if not piece:
            continue
        m = _WEIGHT_RE.match(piece)
        if m:
            name, weight = m.group("name").strip(), float(m.group("w"))
        else:
            name, weight = piece, 1.0
        raw = to_raw(name)
        if not raw:
            continue
        if raw in index:                      # 重复项合并
            i = index[raw]
            out[i] = (raw, out[i][1] + weight)
        else:
            index[raw] = len(out)
            out.append((raw, weight))
    return out


def format_weighted(pairs: list[tuple[str, float]]) -> str:
    """``[("heavyTank",3)]`` -> ``heavyTank:3``；权重 1 时省略后缀。"""
    parts = []
    for raw, weight in pairs:
        if (weight or 0) == 1:
            parts.append(raw)
        else:
            parts.append(f"{raw}:{('%g' % weight)}")
    return ",".join(parts)


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


def build_spawn_units_weighted(pairs: list[tuple[str, float]]) -> str:
    """按权重生成 spawnUnits 串；权重 ≤0 的单位视为「不参与随机」被剔除。"""
    live = [(u, w) for u, w in pairs if u and (w or 0) > 0]
    if not live:
        return ""
    chances = weighted_chances([w for _u, w in live])
    return ",".join(
        f"{u}(spawnChance={c:g},maxSpawnLimit=1)"
        for (u, _w), c in zip(live, chances)
    )


def weights_from_spawn_units(text: str) -> list[tuple[str, float]] | None:
    """能安全反推时，把链式 spawnUnits 还原成 ``[(raw, 权重)]``，否则 None。

    只有「每段都是 ``单位(spawnChance=c,maxSpawnLimit=1)``、最后一段 c=1」
    这种纯抽签串才反推；且要求反推出的权重能**逐字还原**原串，避免把带
    ``offsetRandomX`` / ``falling`` / ``单位*数量`` 的普通刷兵串误当权重。
    """
    parsed = parse_spawn_units(text)
    if len(parsed) < 2:
        return None
    if any(mx != 1 or c is None for _n, c, mx in parsed):
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
    pairs = [(n, w) for (n, _c, _m), w in zip(parsed, weights)]
    rebuilt = parse_spawn_units(build_spawn_units_weighted(pairs))
    # 用数值比对而不是字符串比对：概率可能写成 0.333333 也可能写成 0.3333
    if [n for n, _c, _m in rebuilt] != [n for n, _c, _m in parsed]:
        return None
    for (_n, c, _m), (_n2, c2, _m2) in zip(parsed, rebuilt):
        if c2 is None or abs(float(c) - float(c2)) > 1e-4:
            return None
    return pairs


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
