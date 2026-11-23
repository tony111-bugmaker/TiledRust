"""铁锈战争 automapping（自动映射），对齐 Tiled rules.txt 规则（旧式 + 新版）。

2026-11-19 按 Tiled 官方文档与 automapper.cpp 照搬了完整语义：rules.txt
嵌套 txt / [glob] 过滤器、tile MatchType、StrictEmpty/IgnoreFlip、边界策略、
MatchInOrder、NoOverlappingRules、随机输出、RuleOptions。官方三张规则图都未
使用这些新特性，故均为增量，100% 复现率不变。详见 docs/自动映射规则指南.md 第八节。

官方规则资产（1.15）：`mods/maps/rules.txt` → `rules/ruleset1~3.tmx`。
这三个规则图都带 `regions*` 层，走的是 Tiled 1.9 之前的**旧式**规则系统，
层名约定与新版不同，本机实测过的要点：

- `regions` 同时定义输入/输出区域；`regions_input` + `regions_output` 分别定义，
  输出区域可以比输入区域大（`ruleset3` 第一条是 1x1 输入 / 3x3 输出）。
  一个连通块 = 一条规则，非零格只表示「该坐标属于区域」。**规则区域是连通块
  本身的格子集合，不是它的外接矩形**：官方拐角规则大量是 2x2 里缺一角的 L 形，
  缺口那一格不属于规则，不能参与匹配。早期按 bbox 补齐并给它加「非空且不等于
  本规则用过的 gid」的通配条件，会让右下角恰好是草/水的拐角规则永远匹配不上，
  表现为「自动地图不考虑拐角」。
- `input[not][index]_<name>`：第一个 `_` 之后的**完整字符串**才是目标层名。
  所以 `input_set_decoration` 检查的是 `set_decoration` 层，不是 `set` 层的索引；
  工作地图没有该层时按全空层处理，这条规则因此不会误匹配。
- 旧式「空 input 格通配」：没有 `inputnot` 层时，区域里所有 input 层都为空的坐标
  仍要求「非空且不等于本规则用过的任何 gid」。缺了这条会让 2x2 规则在纯草地/纯水
  内部乱匹配，这是绘制时自动地图结果错乱的主因之一。
- `output[index]_<name>`：index 是随机输出变体，这里固定取第一套，结果可复现。
- `DeleteTiles`：先在受影响区域清掉旧输出，再写规则输出。本实现用「按 `set` 层
  基础地形重建」代替粗暴清空，既清掉了旧过渡瓦片，也不会留下空洞。
- `AutomappingRadius`（官方为 2）：绘制时自动地图重算的范围 = 改动格 ± N。

本模块只负责「算出应写的格子」，写入与撤销交给 AppState / DrawCommand。
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
import copy

from ..document.gid import GID_MASK, FLAG_H, FLAG_V, FLAG_D
from ..document.layer import TileLayer
from ..io.tmx_reader import read_document
from ..utils.paths import as_path

# set 层里直接对应 Ground 底图的地形 gid（官方示例图逐格统计 + 各基础图集首格）。
# 自动映射重算区域时用它兜底，避免 DeleteTiles 清出深色空洞。
BASE_GIDS = frozenset({1, 36, 116, 126, 176, 186, 192, 193, 770})

_LAYER_ALIASES = {
    "set": "set", "ground": "Ground", "items": "Items", "units": "Units",
}

# Tiled 新版 MatchType：规则图里 tile 的自定义属性 `MatchType`，
# 决定该输入格/输出格的匹配语义。默认（无该属性）= Tile。
MATCH_TILE = "tile"
MATCH_EMPTY = "empty"
MATCH_NONEMPTY = "nonempty"
MATCH_OTHER = "other"
MATCH_NEGATE = "negate"
MATCH_IGNORE = "ignore"
_MATCH_TYPES = frozenset({MATCH_TILE, MATCH_EMPTY, MATCH_NONEMPTY,
                          MATCH_OTHER, MATCH_NEGATE, MATCH_IGNORE})


def _tile_match_type(doc, raw_gid: int) -> str:
    """读取规则图中某 gid 对应 tile 的 MatchType 属性（大小写不敏感）。"""
    if not (int(raw_gid) & GID_MASK):
        return MATCH_EMPTY  # 空格子按 Tiled 的 Unknown/空 处理
    info = doc.local_tile(raw_gid) if hasattr(doc, "local_tile") else None
    if info is None:
        return MATCH_TILE
    ts, local_id = info
    props = ts.tile_props(local_id)
    value = props.get("MatchType")
    if value is None:
        for key, val in props.items():
            if str(key).lower() == "matchtype":
                value = val
                break
    value = str(value or MATCH_TILE).strip().lower()
    return value if value in _MATCH_TYPES else MATCH_TILE


def _layer_bool_prop(layer, *names: str) -> bool:
    """读取图层自定义布尔属性（StrictEmpty / AutoEmpty 等）。"""
    props = getattr(layer, "properties", None)
    if props is None:
        return False
    for key, val in props.items():
        if str(key).lower() in names:
            return str(val).strip().lower() in ("1", "true", "yes")
    return False


def _layer_flip_mask(layer) -> int:
    """IgnoreHorizontal/Vertical/DiagonalFlip 为 True 时清掉对应的比较位。"""
    mask = FLAG_H | FLAG_V | FLAG_D
    props = getattr(layer, "properties", None)
    if props is None:
        return mask
    pairs = (("ignorehorizontalflip", FLAG_H),
             ("ignoreverticalflip", FLAG_V),
             ("ignorediagonalflip", FLAG_D))
    for key, val in props.items():
        low = str(key).lower()
        on = str(val).strip().lower() in ("1", "true", "yes")
        for name, flag in pairs:
            if low == name and on:
                mask &= ~flag
    return mask


def _base_gids(doc) -> set[int]:
    """基础地形 = 各基础图集的第 1 格，外加已知常量；过渡图集（名字含 " - "）不算。"""
    gids = set(BASE_GIDS)
    for ts in getattr(doc, "tilesets", ()):
        name = (ts.name or "").lower()
        if not ts.firstgid or " - " in name or name in ("units", "misc"):
            continue
        gids.add(int(ts.firstgid))
    return gids


# 部分地形图集的「set 标记 gid」不是图集首格。它们由官方规则图里
# 「1x1 且 input==output」的自映射规则定义，是 Tiled 规则作者 selected 的
# 那一格；归一到首格会让对应过渡规则永远匹配不上。这里作为规则派生失败时
# 的兜底常量（正常路径由 AutoMapper 从规则动态派生 canonical_marker_gids）。
# 例：Stone 图集 938-973，规则用 945；Lava 图集 974-988，规则用 978。
_FALLBACK_CANONICAL = {938: 945, 974: 978}


def normalize_marker_gid(doc, raw: int,
                         canonical: dict[int, int] | None = None) -> int:
    """把瓦片归一到「set 层标记语义」所需的规范 gid。

    绝大多数地形图集的标记就是首格（草 126 / 水 186 / 山 1 / 沙 770），
    但官方规则为 Stone/Lava 选的标记是图集内特定格（945 / 978），不是首格。
    归一到首格会让 Stone-Sand 等过渡规则永远匹配不上 —— 表现为沙地上出现
    一串裸的岩石块、没有圆滑过渡。

    ``canonical`` 是「图集首格 -> 规范标记 gid」映射，由 AutoMapper 加载规则
    时自动派生（见 :meth:`AutoMapper._derive_canonical_markers`），不写死数字。
    调用方传了就用它；没传（如单元测试 / 探针）时退回兜底常量，保证行为一致。

    返回 -1 表示该 gid 不属于任何基础地形图集（过渡图集 / units / misc），
    调用方应原样写入并提示用户。
    """
    raw_int = int(raw) & 0xFFFFFFFF
    gid = raw_int & GID_MASK
    flags = raw_int & ~GID_MASK
    if not gid:
        return raw_int
    canon = dict(_FALLBACK_CANONICAL)
    if canonical:
        canon.update(canonical)
    # Stone/Lava 等图集可能不在当前工作地图里（官方示例图就没有），此时
    # gid 落不进任何图集区间。但只要是「首格或规范标记」本身，就按 canon
    # 归一：938/945 -> 945、974/978 -> 978。否则会把石头归一失败。
    canonical_values = set(canon.values())
    if gid in canon or gid in canonical_values:
        return canon.get(gid, gid) | flags
    bases = [ts for ts in getattr(doc, "tilesets", ())
             if ts.firstgid
             and " - " not in (ts.name or "").lower()
             and (ts.name or "").lower() not in ("units", "misc")]
    if not bases:
        # 没有可用图集信息（测试文档 / 极简地图）时无法判断，保守保持原值，
        # 绝不能因为「归一失败」而把用户落笔整片丢掉。
        return raw_int
    for ts in bases:
        if int(ts.firstgid) <= gid <= int(ts.last_gid):
            first = int(ts.firstgid)
            # 关键：归一到该图集「规则认可的标记 gid」，而非一味用首格。
            return canon.get(first, first) | flags
    return raw_int if gid in BASE_GIDS else -1


def _doc_tileset_ranges(doc) -> dict[str, list[tuple[int, int]]]:
    """工作地图的图集名 -> [(firstgid, lastgid), ...]（同名可能多段）。"""
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for ts in getattr(doc, "tilesets", ()):
        if ts.firstgid:
            out[(ts.name or "").lower()].append((int(ts.firstgid), int(ts.last_gid)))
    return dict(out)


def _gid_in_ranges(gid: int, ranges) -> int | None:
    """gid 落在哪一段就返回它的段首；否则 None。"""
    for first, last in ranges:
        if first <= gid <= last:
            return first
    return None


def _make_gid_mapper(source: tuple, target: dict[str, list[tuple[int, int]]],
                     keep_missing: bool = True, doc=None):
    """构造「规则图 gid -> 工作地图 gid」映射函数。

    按图集名配对，用图集内相对索引换算：规则图 Lava+4 -> 工作图 Lava+4。
    ``keep_missing`` 控制目标图缺该图集时：
      * True（输入匹配/set 层标记）：保留原 gid。示例图缺 Stone/Lava 图集
        定义，但历史行为与 100% 复现率依赖规则 gid 直接匹配。
      * False（输出写入 Ground）：若该 gid 落在工作图【其它图集】区间内
        （会造成冲突、显示成獠牙）则返回 None 丢弃；若不落在任何图集
        （示例图情况，写入本就被游戏忽略）则保留原 gid。
    source 为空（测试内存规则）时返回恒等函数。
    """
    if not source:
        return lambda g: g
    src_by_name: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for name, first, last in source:
        src_by_name[name].append((first, last))
    # 为规则图的每一段选定目标段：优先 firstgid 完全相同（示例图恒等），
    # 否则按出现顺序配对。避免同名双段图集（Sand 770/875）错位。
    pairs: list[tuple[int, int, int, int]] = []  # (src_first, src_last, dst_first, dst_last)
    for name, s_ranges in src_by_name.items():
        t_ranges = list(target.get(name, ()))
        for i, (sf, sl) in enumerate(s_ranges):
            exact = next((tr for tr in t_ranges if tr[0] == sf), None)
            if exact is not None:
                pairs.append((sf, sl, exact[0], exact[1]))
                t_ranges.remove(exact)
            elif i < len(t_ranges):
                df, dl = t_ranges.pop(0)
                pairs.append((sf, sl, df, dl))
            # 目标缺该段：不加入 pairs，交由 missing_ranges 返回 None。
    # 目标完全没有的图集名：标记整段不可映射（返回 None）。
    missing_names = set(src_by_name) - set(target)
    missing_ranges = [(sf, sl) for name in missing_names
                      for sf, sl in src_by_name[name]]
    # 工作图所有图集区间（含过渡图集）：用于判断缺图集的 gid 是否落在别的图集里。
    all_target_ranges = [r for ranges in target.values() for r in ranges]
    cache: dict[int, int | None] = {}

    def mapper(gid: int) -> int | None:
        gid = int(gid) & GID_MASK
        if not gid:
            return 0
        if gid in cache:
            return cache[gid]
        missing = any(sf <= gid <= sl for sf, sl in missing_ranges)
        result = gid
        if missing and not keep_missing:
            # 缺图集且 gid 落在工作图别的图集区间 -> 会写错瓦片，丢弃。
            if _gid_in_ranges(gid, all_target_ranges) is not None:
                result = None
        else:
            for sf, sl, df, dl in pairs:
                if sf <= gid <= sl:
                    if df + (gid - sf) <= dl:
                        result = df + (gid - sf)
                    break
        cache[gid] = result
        return result

    return mapper


@dataclass
class Rule:
    name: str
    w: int
    h: int
    # (dx, dy, gid, target_layer_name_or_None)
    inputs: list[tuple[int, int, int, str | None]] = field(default_factory=list)
    inputnots: list[tuple[int, int, int, str | None]] = field(default_factory=list)
    # 旧式通配条件 (dx, dy, target)
    wildcards: list[tuple[int, int, str | None]] = field(default_factory=list)
    used_gids: set[int] = field(default_factory=set)
    # target_layer -> list[variant]，variant 是 [(dx, dy, gid)]，坐标相对输入区域原点
    outputs: dict[str, list[list[tuple[int, int, int]]]] = field(default_factory=dict)
    # 随机输出权重：variant index -> probability（Tiled output 层 probability 属性）。
    # 为空表示各 variant 等概率。选择模式由 AutoMapper.random_variants / seed 控制。
    variant_weights: list[float] = field(default_factory=list)
    delete: bool = False
    radius: int = 0
    # DeleteTiles 擦除位置（输入区域内出现过 input tile 的坐标）
    erase: list[tuple[int, int]] = field(default_factory=list)
    strict_empty: bool = True       # 是否启用旧式空 input 通配
    # ---- Tiled 新版 MatchType 语义（仅当规则图 tile 带 MatchType 属性时启用）----
    # any_of / none_of：(dx, dy, 匹配值, target, flags_mask)
    #   匹配值为 gid(int) 或字符串标记 "empty"/"nonempty"/"other"
    any_of: list[tuple] = field(default_factory=list)
    none_of: list[tuple] = field(default_factory=list)
    ignore_cells: set[tuple[int, int]] = field(default_factory=set)  # MatchType::Ignore
    negate: bool = False            # MatchType::Negate：交换 any/none
    flip_masks: dict[str | None, int] = field(default_factory=dict)  # target -> flag mask
    # 输出格 MatchType::Empty 时要显式写空；普通空输出仍不覆盖（Tiled 语义）
    explicit_empty_outputs: list[tuple[str, int, int]] = field(default_factory=list)
    # Tiled 1.3+ 边界策略："" 默认(越界不匹配) / "wrap" / "overflow" / "outside"
    border: str = ""
    # Tiled NoOverlappingRules：同一规则的输出区不得与已应用区重叠
    no_overlapping: bool = False
    # Tiled RuleOptions（map 属性默认值）：规则级参数
    skip_chance: float = 0.0        # Probability：跳过概率（1 - 匹配概率）
    mod_x: int = 1                  # ModX：x 方向匹配步长
    mod_y: int = 1                  # ModY
    offset_x: int = 0               # OffsetX：匹配起点偏移
    offset_y: int = 0
    disabled: bool = False          # Disabled
    ignore_lock: bool = False       # IgnoreLock：Tiled 默认 False（尊重图层锁定）
    # 规则在 rules.txt / 规则图中的固定顺序，用于消解多条规则写同一格的冲突
    order: tuple = ()
    # 来源规则图的图集区间列表 [(name_lower, firstgid, lastgid), ...]。
    # 规则里的 gid 属于规则图空间，工作地图的 firstgid 可能不同，需要在
    # compute 时按「图集名 + 图集内相对索引」映射到工作地图空间。
    # 为空则映射退化为恒等（兼容内存构造的测试规则）。
    source_tilesets: tuple[tuple[str, int, int], ...] = ()
    # 输出格相对锚点的坐标范围，用于实时绘制时 O(1) 排除够不到窗口的规则
    min_rx: int = 0
    max_rx: int = 0
    min_ry: int = 0
    max_ry: int = 0

    def finish(self):
        """解析完成后算一次裁剪范围。"""
        anchor = self.anchor
        ax, ay = (anchor[0], anchor[1]) if anchor else (0, 0)
        offsets = [(dx - ax, dy - ay)
                   for variants in self.outputs.values()
                   for cells in variants
                   for dx, dy, _gid in cells]
        if offsets:
            self.min_rx = min(o[0] for o in offsets)
            self.max_rx = max(o[0] for o in offsets)
            self.min_ry = min(o[1] for o in offsets)
            self.max_ry = max(o[1] for o in offsets)
        return self

    @property
    def anchor(self):
        """用于建立 gid 索引的第一个输入条件 (dx, dy, gid, layer)。"""
        return min(self.inputs, key=lambda t: (t[1], t[0])) if self.inputs else None


def _components(layer: TileLayer, w: int, h: int):
    """4-邻接连通块 → (x0,y0,x1,y1, [绝对 cell index])。"""
    cells = layer.cells
    seen: set[int] = set()
    out = []
    for idx, gid in enumerate(cells):
        if not (int(gid) & GID_MASK) or idx in seen:
            continue
        queue = deque([idx])
        seen.add(idx)
        comp: list[int] = []
        while queue:
            i = queue.popleft()
            comp.append(i)
            x, y = i % w, i // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if j not in seen and (int(cells[j]) & GID_MASK):
                        seen.add(j)
                        queue.append(j)
        xs = [i % w for i in comp]
        ys = [i // w for i in comp]
        out.append((min(xs), min(ys), max(xs), max(ys), comp))
    return out


def _parse_input_layer(name: str) -> tuple[bool, str, str | None]:
    """`input[not][index]_<name>` → (是否 not, index, 目标层名或 None=dummy)。

    旧式规则里 `input_set_decoration` 的 name 是 `set_decoration`（含下划线），
    工作地图没有这一层时按全空层处理。
    """
    low = name.lower()
    is_not = low.startswith("inputnot")
    body = low[len("inputnot" if is_not else "input"):]
    if body.startswith("_"):
        index, raw = "", body[1:]
    else:
        index, _, raw = body.partition("_")
    return is_not, index, _LAYER_ALIASES.get(raw)


def _output_target(name: str) -> tuple[str, int]:
    """`output2_Ground` / `rule_Ground` / `Output_Items` → (目标层, 变体序号)。"""
    low = name.lower()
    for prefix in ("output", "rule_", "out_"):
        if low.startswith(prefix):
            rest = low[len(prefix):]
            break
    else:
        rest = low
    variant = 0
    head = rest.split("_", 1)
    if head[0].isdigit():
        variant = int(head[0]) - 1
        rest = head[1] if len(head) > 1 else ""
    if "_" in rest:
        rest = rest.rsplit("_", 1)[-1]
    mapping = {"ground": "Ground", "items": "Items", "units": "Units", "set": "set"}
    return mapping.get(rest, rest.capitalize()), max(0, variant)


class AutoMapper:
    def __init__(self, profile, rules_file=None):
        self.profile = profile
        self.rules_file = as_path(rules_file) if rules_file else (
            as_path(profile.mods_maps) / "rules.txt")
        # Tiled 1.9+：rules.txt 可用 [glob] 按当前工作地图文件名过滤规则。
        # 默认 None = 不过滤（加载全部规则图），保持旧调用与测试行为不变。
        self.target_map_name: str | None = None
        # Tiled 源码：显式 regions 层时默认 MatchInOrder=true（向后兼容）。
        # 实测铁锈官方规则图：MatchInOrder 开/关都是 100% 复现率，且局部实时
        # 重算与全图重建一致（规则间无输出依赖）。因此忠实照搬 Tiled，
        # load() 后按是否有显式 regions 层设默认值。
        # 可在 load() 前显式设 match_in_order 覆盖，或 compute(match_in_order=...) 临时覆盖。
        self.match_in_order: bool = False
        self._tiled_match_in_order_suggested: bool = False
        self._match_in_order_explicit: bool = False
        self._has_explicit_regions: bool = False
        # Tiled 随机输出：默认关闭（固定取 variant 0）以保持可复现的 100% 复现率。
        # 打开后用带 seed 的加权随机；seed=None 为真随机。
        self.random_variants: bool = False
        self.random_seed: int | None = 12345
        self.rules: list[Rule] = []
        self.rule_files: list[str] = []
        self.loaded = False
        self.errors: list[str] = []
        self._index_cache = None
        self._index_rules_ref = None
        # 图案索引：规则输入几乎全在 set 层，把「锚点 gid + 邻域图案」预建成
        # 哈希表，实时绘制/填充时一次查表命中适用规则，不再逐条规则试匹配。
        self._fast_index_cache = None
        self._fast_rules_ref = None
        # 图集首格 -> 规则认可的规范标记 gid（由规则派生，见 _derive_canonical_markers）
        self.canonical_marker_gids: dict[int, int] = {}
        #  gid 空间投影缓存：key=工作地图图集布局签名，value=投影后的规则列表。
        self._projected_cache: dict[tuple, list[Rule]] = {}
        # 「图集名 -> 规范标记相对索引」只依赖已加载规则与规范标记；绘制时
        # 每格都会算一次标记归一，缓存后避免逐格重扫全部规则（填充一大片
        # 水域时这里曾是最大热点）。rules / canonical_marker_gids 引用变化
        # （load() 重新赋值）即自动失效。
        self._canon_index_cache: dict[str, int] | None = None
        self._canon_index_rules_ref = None
        self._canon_index_gids_ref = None
        # 工作地图的图集区间同一张地图内不变；逐格归一时复用，换地图按身份失效。
        self._ranges_doc = None
        self._ranges_cache: dict[str, list[tuple[int, int]]] | None = None

    # ---------------- 加载规则 ----------------
    def load(self) -> dict:
        """读取 rules.txt 及其引用的规则图。

        Tiled 1.9+ 的 rules.txt 每行可以是：规则图(.tmx/.json)、嵌套的
        .txt（递归解析，可位于其它目录）、`[glob]` 文件名过滤器、
        或 `#` / `//` 注释。过滤器按出现顺序生效，直到下一个过滤器覆盖；
        ``target_map_name`` 为 None 时视为「匹配全部」（旧行为）。
        """
        self.rules, self.rule_files, self.errors = [], [], []
        self._index_cache = None
        self._fast_index_cache = None
        self._projected_cache = {}
        self._canon_index_cache = None
        self._canon_index_rules_ref = None
        self._canon_index_gids_ref = None
        self._ranges_doc = None
        self._ranges_cache = None
        if not self.rules_file.is_file():
            return {"ok": False, "error": f"规则索引不存在: {self.rules_file}"}
        self._load_rules_txt(self.rules_file, visited=set(), active_filter="*")
        self.loaded = True
        # Tiled 兼容：显式 regions 层默认 MatchInOrder=true；显式设置优先。
        self._tiled_match_in_order_suggested = self._has_explicit_regions
        if not self._match_in_order_explicit:
            self.match_in_order = self._has_explicit_regions
        self.canonical_marker_gids = self._derive_canonical_markers()
        return {
            "ok": bool(self.rules),
            "rules_file": str(self.rules_file),
            "rule_maps": self.rule_files,
            "rules": len(self.rules),
            "errors": self.errors,
        }

    def _load_rules_txt(self, txt_path: Path, visited: set[Path],
                        active_filter: str, depth: int = 0) -> None:
        """递归解析一个 rules.txt。见 :meth:`load`。"""
        txt_path = txt_path.resolve()
        if txt_path in visited:
            self.errors.append(f"rules.txt 循环引用: {txt_path}")
            return
        if depth > 16:
            self.errors.append(f"rules.txt 嵌套过深: {txt_path}")
            return
        visited.add(txt_path)
        try:
            lines = txt_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            self.errors.append(f"读 rules.txt 失败 {txt_path}: {exc}")
            return
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            # [glob] 文件名过滤器：作用于其后所有行，直到下一个过滤器。
            if line.startswith("[") and line.endswith("]"):
                active_filter = line[1:-1].strip() or "*"
                continue
            # 文件名过滤：target_map_name 为 None 时不过滤（向后兼容）。
            if (self.target_map_name is not None
                    and active_filter != "*"
                    and not fnmatch(self.target_map_name, active_filter)):
                continue
            path = (txt_path.parent / line).resolve()
            if not path.is_file():
                self.errors.append(f"缺规则文件 {path}")
                continue
            # 嵌套 txt：递归解析，沿用当前过滤器状态。
            if path.suffix.lower() == ".txt":
                self._load_rules_txt(path, visited, active_filter, depth + 1)
                continue
            try:
                doc = read_document(path, self.profile)
                self.rules.extend(self._parse_rulemap(doc, path))
                self.rule_files.append(str(path))
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"{path.name}: {exc}")

    def _derive_canonical_markers(self) -> dict[int, int]:
        """从规则派生「图集首格 -> 规则认可的标记 gid」。

        官方规则里 Stone / Lava 的标记不是图集首格，而是图集内某格
        （Stone 938→945、Lava 974→978）。判据：一条 1x1 规则，若其 input
        的 set gid 与 output 的 Ground gid 相同，该 gid 就是「基础地形标记」。
        再用规则图里的图集区间把它归到所属首格。这样规则换了数字也能自适应，
        不必在代码里写死 945 / 978。
        """
        # 1) 收集所有「1x1 自映射」规则里的地形标记 gid
        marker_gids: set[int] = set()
        for rule in self.rules:
            if rule.w != 1 or rule.h != 1 or len(rule.inputs) != 1:
                continue
            in_gid = rule.inputs[0][2]
            for variants in rule.outputs.values():
                if any(cells and len(cells) == 1 and cells[0][0] == 0
                       and cells[0][1] == 0 and cells[0][2] == in_gid
                       for cells in variants):
                    marker_gids.add(in_gid)
                    break
        if not marker_gids:
            return {}
        # 2) 取规则图图集区间，把标记 gid 映射到它所属图集的首格
        result: dict[int, int] = {}
        seen: set[tuple[int, int, int]] = set()
        for rule_file in self.rule_files:
            try:
                rule_doc = read_document(rule_file, self.profile)
            except Exception:  # noqa: BLE001
                continue
            for ts in rule_doc.tilesets:
                name = (ts.name or "").lower()
                if not ts.firstgid or " - " in name or name in ("units", "misc"):
                    continue
                first, last = int(ts.firstgid), int(ts.last_gid)
                for gid in marker_gids:
                    if first <= gid <= last:
                        key = (first, gid, last)
                        if key in seen:
                            continue
                        seen.add(key)
                        # 一个图集有多个候选时优先取首格本身，否则取最小标记，
                        # 保证同一图集在多张规则图里派生结果稳定。
                        if first not in result or gid == first or gid < result[first]:
                            if result.get(first) == first and gid != first:
                                continue
                            result[first] = gid
        return result

    def _parse_rulemap(self, doc, path: Path) -> list[Rule]:
        props = {str(k).lower(): str(v) for k, v in doc.properties.items()}

        def _bool_prop(key):
            return props.get(key, "").strip().lower() in ("1", "true", "yes")

        delete = _bool_prop("deletetiles")
        no_overlapping = _bool_prop("nooverlappingrules")

        def _num_prop(key, default, cast=int):
            try:
                return cast(props.get(key, default))
            except (TypeError, ValueError):
                return default

        # Tiled RuleOptions（map 属性作为所有规则的默认值）
        # Probability 默认 1.0（100% 执行）；Tiled 内部取反为 skipChance。
        # 缺失属性时必须返回 1.0，否则 skipChance=1 会让所有规则被跳过。
        ro_skip = 1.0 - _num_prop("probability", 1.0, float)
        ro_mod_x = max(1, _num_prop("modx", 1))
        ro_mod_y = max(1, _num_prop("mody", 1))
        ro_off_x = _num_prop("offsetx", 0)
        ro_off_y = _num_prop("offsety", 0)
        ro_disabled = _bool_prop("disabled")
        ro_ignore_lock = _bool_prop("ignorelock")
        # Tiled 1.3+ 边界策略：WrapBorder 优先 > OverflowBorder > MatchOutsideMap。
        # 后两者隐含 MatchOutsideMap（地图外按空/边界值处理，允许匹配）。
        border = ""
        if _bool_prop("wrapborder"):
            border = "wrap"
        elif _bool_prop("overflowborder"):
            border = "overflow"
        elif _bool_prop("matchoutsidemap"):
            border = "outside"
        try:
            radius = int(props.get("automappingradius", "0") or 0)
        except ValueError:
            radius = 0
        layers = {l.name.lower(): l for l in doc.layers if isinstance(l, TileLayer)}
        # 记录来源规则图的图集布局，供 compute 把规则 gid 映射到工作地图空间。
        source_tilesets = tuple(
            ((ts.name or "").lower(), int(ts.firstgid), int(ts.last_gid))
            for ts in doc.tilesets if ts.firstgid
        )

        # ---- 区域：输入连通块 + 输出连通块配对 ----
        # in_cells / out_cells 是「属于该规则的格子」相对输入区域原点的坐标集合。
        # 必须保留非矩形形状（拐角规则是 L 形），不能用外接矩形代替。
        def _rel(comp, x0, y0):
            return frozenset((i % doc.width - x0, i // doc.width - y0)
                             for i in comp)

        has_split_regions = "regions_input" in layers and "regions_output" in layers
        if has_split_regions or "regions" in layers or "ruleregions" in layers:
            self._has_explicit_regions = True
        if has_split_regions:
            in_comps = _components(layers["regions_input"], doc.width, doc.height)
            out_comps = _components(layers["regions_output"], doc.width, doc.height)
            regions = []
            for ix0, iy0, ix1, iy1, comp in in_comps:
                best = None
                for ox0, oy0, ox1, oy1, ocomp in out_comps:
                    if ox0 <= ix0 and oy0 <= iy0 and ox1 >= ix1 and oy1 >= iy1:
                        area = (ox1 - ox0 + 1) * (oy1 - oy0 + 1)
                        if best is None or area < best[0]:
                            best = (area, ocomp)
                if best is None:
                    out_cells = _rel(comp, ix0, iy0)
                else:
                    out_cells = _rel(best[1], ix0, iy0)
                regions.append(((ix0, iy0, ix1, iy1), _rel(comp, ix0, iy0), out_cells))
        else:
            reg_name = next(
                (n for n in ("regions", "ruleregions") if n in layers), None)
            if reg_name is None:
                return []
            regions = []
            for x0, y0, x1, y1, comp in _components(
                    layers[reg_name], doc.width, doc.height):
                cells = _rel(comp, x0, y0)
                regions.append(((x0, y0, x1, y1), cells, cells))

        input_layers: list[tuple[bool, str | None, TileLayer]] = []
        output_layers: list[tuple[str, int, TileLayer]] = []
        for name, layer in layers.items():
            if name.startswith("inputnot") or name.startswith("input"):
                is_not, _index, target = _parse_input_layer(name)
                input_layers.append((is_not, target, layer))
            elif name.startswith("output") or name.startswith("rule_"):
                target, variant = _output_target(name)
                output_layers.append((target, variant, layer))
        has_inputnot_layer = any(is_not for is_not, _t, _l in input_layers)

        # 先探测规则图是否使用了 Tiled 新版 MatchType / StrictEmpty / IgnoreFlip。
        # 官方规则图一个都没有 → uses_match_type=False → 完全走原来的解析路径，
        # 保证 100% 复现率不受影响。
        uses_match_type = False
        for _is_not, _target, _layer in input_layers:
            if _layer_bool_prop(_layer, "strictempty", "autoempty"):
                uses_match_type = True
            if _layer_flip_mask(_layer) != (FLAG_H | FLAG_V | FLAG_D):
                uses_match_type = True
            for _cell in _layer.cells:
                if int(_cell) & GID_MASK and _tile_match_type(doc, _cell) != MATCH_TILE:
                    uses_match_type = True
                    break
            if uses_match_type:
                break

        out: list[Rule] = []
        for ci, (in_box, in_cells, out_cells) in enumerate(regions):
            ix0, iy0, ix1, iy1 = in_box
            w, h = ix1 - ix0 + 1, iy1 - iy0 + 1
            rule = Rule(name=f"{path.stem}#{ci}", w=w, h=h,
                        delete=delete, radius=radius, border=border,
                        no_overlapping=no_overlapping,
                        skip_chance=ro_skip, mod_x=ro_mod_x, mod_y=ro_mod_y,
                        offset_x=ro_off_x, offset_y=ro_off_y,
                        disabled=ro_disabled, ignore_lock=ro_ignore_lock,
                        strict_empty=not has_inputnot_layer,
                        order=(path.stem, iy0, ix0),
                        source_tilesets=source_tilesets)
            covered: set[tuple[int, int]] = set()
            primary_target: str | None = None
            for is_not, target, layer in input_layers:
                if primary_target is None and not is_not:
                    primary_target = target
                mask = _layer_flip_mask(layer)
                if mask != (FLAG_H | FLAG_V | FLAG_D):
                    rule.flip_masks[target] = mask
                strict_here = _layer_bool_prop(layer, "strictempty", "autoempty")
                # 只检查属于规则区域的格子；外接矩形里多出来的缺口不参与匹配。
                for dx, dy in sorted(in_cells):
                    raw = int(layer.get(ix0 + dx, iy0 + dy))
                    gid = raw & GID_MASK
                    mtype = _tile_match_type(doc, raw) if uses_match_type else MATCH_TILE
                    if not gid:
                        # 空格子：StrictEmpty 时是「必须为空」条件；否则旧式通配。
                        if uses_match_type and (strict_here or rule.strict_empty):
                            rule.any_of.append((dx, dy, "empty", target, mask))
                        continue
                    if uses_match_type and mtype != MATCH_TILE:
                        # MatchType 特殊格 → any_of / none_of / ignore / negate
                        if mtype == MATCH_IGNORE:
                            rule.ignore_cells.add((dx, dy))
                        elif mtype == MATCH_NEGATE:
                            rule.negate = True
                        elif mtype == MATCH_EMPTY:
                            (rule.none_of if is_not else rule.any_of).append(
                                (dx, dy, "empty", target, mask))
                        elif mtype == MATCH_NONEMPTY:
                            (rule.any_of if is_not else rule.none_of).append(
                                (dx, dy, "empty", target, mask))
                        elif mtype == MATCH_OTHER:
                            # 「任意其它已用 tile」：none_of 里放本规则所有 used gid
                            for used in rule.used_gids:
                                (rule.any_of if is_not else rule.none_of).append(
                                    (dx, dy, used, target, mask))
                        continue
                    item = (dx, dy, gid, target)
                    if is_not:
                        rule.inputnots.append(item)
                        if uses_match_type:
                            rule.none_of.append((dx, dy, raw & (GID_MASK | mask), target, mask))
                    else:
                        rule.inputs.append(item)
                        rule.used_gids.add(gid)
                        covered.add((dx, dy))
                        if uses_match_type:
                            rule.any_of.append((dx, dy, raw & (GID_MASK | mask), target, mask))
            if rule.strict_empty and primary_target is not None and not uses_match_type:
                for dx, dy in sorted(in_cells - covered):
                    rule.wildcards.append((dx, dy, primary_target))
            # MatchType 规则的空格子语义已在上面 input 解析中按 strict_empty /
            # StrictEmpty 加入 any_of/none_of，这里不再叠加旧式 wildcards，
            # 否则「要求空」与「要求非空」会互相矛盾。
            rule.erase = sorted(covered)

            raw_weights: dict[int, float] = {}
            for target, variant, layer in output_layers:
                cells = []
                # 输出同样只在输出区域内取，避免串进相邻规则的瓦片。
                for dx, dy in sorted(out_cells):
                    raw = int(layer.get(ix0 + dx, iy0 + dy))
                    gid = raw & GID_MASK
                    if gid:
                        cells.append((dx, dy, gid))
                        if uses_match_type and _tile_match_type(doc, raw) == MATCH_EMPTY:
                            rule.explicit_empty_outputs.append((target, dx, dy))
                bucket = rule.outputs.setdefault(target, [])
                while len(bucket) <= variant:
                    bucket.append([])
                bucket[variant].extend(cells)
                # Tiled output 层 probability 属性（同 variant 各层取首个非默认值）。
                prob = 1.0
                props = getattr(layer, "properties", None)
                if props is not None:
                    for key, val in props.items():
                        if str(key).lower() == "probability":
                            try:
                                prob = float(val)
                            except (TypeError, ValueError):
                                prob = 1.0
                            break
                if variant not in raw_weights or prob != 1.0:
                    raw_weights[variant] = prob
            # 压缩掉空 variant 后，权重按保留下来的 variant 索引重映射。
            kept_indices = [i for i, v in enumerate(
                next(iter(rule.outputs.values()), [])) if v] \
                if rule.outputs else []
            rule.outputs = {
                k: [v for v in variants if v]
                for k, variants in rule.outputs.items()
            }
            rule.outputs = {k: v for k, v in rule.outputs.items() if v}
            if raw_weights and kept_indices:
                rule.variant_weights = [raw_weights.get(i, 1.0) for i in kept_indices]
            # MatchType 规则可能没有传统 inputs（全靠 any_of/none_of/ignore），
            # 只要有输入条件 + 输出就算有效规则。
            has_input = bool(rule.inputs or rule.any_of or rule.none_of
                             or rule.ignore_cells or rule.negate)
            if has_input and rule.outputs:
                out.append(rule.finish())
        return out

    # ---------------- 计算应写入的格子 ----------------
    def _project_rules(self, doc) -> list[Rule]:
        """把规则 gid 从规则图空间投影到工作地图空间。

        官方示例图的图集布局与规则图一致，投影是恒等，行为完全不变（保住
        100% 复现率）。当工作地图的图集 firstgid 不同（如 Large Lava Divide），
        按「图集名 + 图集内相对索引」换算，避免把 Lava+4 误当成 Stone 标记。
        """
        target = _doc_tileset_ranges(doc)
        # 工作地图没有图集信息（内存构造的测试地图）：无法按图集名换算，
        # 直接沿用原规则，避免把合法 gid 误判为「无法映射」而禁用。
        if not target:
            return list(self.rules)
        sig = tuple(sorted((n, tuple(r)) for n, r in target.items()))
        cached = self._projected_cache.get(sig)
        if cached is not None:
            return cached

        # 每套规则图布局两个 mapper：输入保留缺失图集原 gid（兼容示例图），
        # 输出丢弃缺失图集（避免写到别的图集形成獠牙）。
        in_mappers: dict[tuple, object] = {}
        out_mappers: dict[tuple, object] = {}
        out: list[Rule] = []
        for rule in self.rules:
            src = rule.source_tilesets
            if not src:
                out.append(rule)
                continue
            key = tuple(src)
            imap = in_mappers.get(key)
            if imap is None:
                imap = _make_gid_mapper(src, target, keep_missing=True)
                omap = _make_gid_mapper(src, target, keep_missing=False)
                in_mappers[key], out_mappers[key] = imap, omap
            else:
                omap = out_mappers[key]
            mapper = imap

            # 恒等投影：所有图集首格映射到自身，且没有任何输出会被丢弃，
            # 才直接复用原规则（零开销）。
            identity = all(imap(first) == first and omap(first) == first
                           for _name, first, _last in src)
            if identity:
                identity = all(
                    omap(g) == g
                    for _t, variants in rule.outputs.items()
                    for cells in variants for _dx, _dy, g in cells
                )
            if identity:
                out.append(rule)
                continue

            r = copy.copy(rule)
            r.inputs = []
            r.inputnots = []
            r.used_gids = set()
            r.any_of = []
            r.none_of = []
            r.outputs = {}
            r.variant_weights = list(rule.variant_weights)
            ok = True

            for dx, dy, gid, layer in rule.inputs:
                g = mapper(gid)
                if g is None:
                    ok = False
                    break
                r.inputs.append((dx, dy, g, layer))
                r.used_gids.add(g)
            if not ok:
                r.disabled = True
                out.append(r)
                continue
            for dx, dy, gid, layer in rule.inputnots:
                g = mapper(gid)
                if g is None:
                    continue  # 目标地图缺该图集，not 条件恒真，可丢弃
                r.inputnots.append((dx, dy, g, layer))
            for spec in rule.any_of:
                dx, dy, want, layer, mask = spec
                if isinstance(want, str):
                    r.any_of.append(spec)
                else:
                    g = mapper(want)
                    if g is None:
                        r.any_of.append((dx, dy, "nonempty", layer, mask))
                    else:
                        r.any_of.append((dx, dy, g, layer, mask))
            for spec in rule.none_of:
                dx, dy, want, layer, mask = spec
                if isinstance(want, str):
                    r.none_of.append(spec)
                else:
                    g = mapper(want)
                    if g is None:
                        continue
                    r.none_of.append((dx, dy, g, layer, mask))
            for layer, variants in rule.outputs.items():
                new_variants = []
                for cells in variants:
                    new_cells = []
                    for dx, dy, gid in cells:
                        g = omap(gid)   # 输出：缺图集的瓦片丢弃
                        if g is not None:
                            new_cells.append((dx, dy, g))
                    new_variants.append(new_cells)
                if any(new_variants):
                    r.outputs[layer] = new_variants
            if not r.inputs and not r.any_of and not r.none_of:
                r.disabled = True
            r.finish()
            out.append(r)

        self._projected_cache[sig] = out
        return out

    def _canonical_index_by_tileset(self) -> dict[str, int]:
        """图集名 -> 规范标记在图集内的相对索引。

        canonical_marker_gids 是「规则图首格 -> 规范标记」，结合规则的
        source_tilesets 可换算出「图集名 -> 规范相对索引」（Lava 为 4）。
        """
        if (self._canon_index_cache is not None
                and self._canon_index_rules_ref is self.rules
                and self._canon_index_gids_ref is self.canonical_marker_gids):
            return self._canon_index_cache
        result: dict[str, int] = {}
        seen: set[tuple[str, int]] = set()
        for rule in self.rules:
            for name, first, _last in rule.source_tilesets:
                if (name, first) in seen:
                    continue
                seen.add((name, first))
                canonical = self.canonical_marker_gids.get(first)
                if canonical is None:
                    result.setdefault(name, 0)
                else:
                    result[name] = canonical - first
        self._canon_index_cache = result
        self._canon_index_rules_ref = self.rules
        self._canon_index_gids_ref = self.canonical_marker_gids
        return result

    def normalize_marker(self, doc, raw: int) -> int:
        """把落笔瓦片归一到 set 层标记（工作图 gid 空间）。

        当工作图图集布局与规则图不同，按「图集名 + 相对索引」换算，
        例如工作图 Lava 首格 941 + 规范索引 4 = 945；否则退回旧的
        normalize_marker_gid（示例图布局一致时行为完全不变）。
        """
        idx_map = self._canonical_index_by_tileset()
        if idx_map and getattr(doc, "tilesets", None):
            raw_int = int(raw) & 0xFFFFFFFF
            gid = raw_int & GID_MASK
            flags = raw_int & ~GID_MASK
            if gid:
                if self._ranges_doc is not doc or self._ranges_cache is None:
                    self._ranges_cache = _doc_tileset_ranges(doc)
                    self._ranges_doc = doc
                for name, ranges in self._ranges_cache.items():
                    if " - " in name or name in ("units", "misc"):
                        continue
                    first = _gid_in_ranges(gid, ranges)
                    if first is not None:
                        return (first + idx_map.get(name, 0)) | flags
        return normalize_marker_gid(doc, raw, self.canonical_marker_gids)

    def _index(self, rules=None):
        # 实时绘制时每格都会调用，规则不变就复用索引。
        rules = self.rules if rules is None else rules
        if (self._index_cache is not None
                and self._index_rules_ref is rules):
            return self._index_cache
        index: dict[int, list[tuple[Rule, int, int]]] = defaultdict(list)
        loose: list[Rule] = []
        for rule in rules:
            anchor = rule.anchor
            if anchor is None:
                loose.append(rule)
            else:
                index[anchor[2]].append((rule, anchor[0], anchor[1]))
        self._index_cache = (index, loose)
        self._index_rules_ref = rules
        return self._index_cache

    def _fast_index(self, rules=None):
        """按「锚点gid → 相对邻域形状 → 期望图案 → 规则」预建查表。

        只对输入全部读 set 层（或固定 0 的 None 层）、无 any/none/wildcard/
        border/skip 的简单规则启用。官方 274 条规则里 267 条走这条快路。
        只要存在读其它层或写 set 层的规则就整体关闭，避免 MatchInOrder
        的 overlay 语义受影响。

        返回 ``(bucket, fast_ids)``：bucket[锚点gid][形状][图案] = 规则列表。
        """
        rules = self.rules if rules is None else rules
        if (self._fast_index_cache is not None
                and self._fast_rules_ref is rules):
            return self._fast_index_cache
        bucket: dict = {}
        fast_ids: set[int] = set()

        safe = True
        for r in rules:
            if "set" in r.outputs:
                safe = False
                break
            targets = ([t for *_, t in r.inputs]
                       + [s[3] for s in r.any_of]
                       + [s[3] for s in r.none_of]
                       + [t for _, _, t in r.wildcards])
            if any(t not in (None, "set") for t in targets):
                safe = False
                break
        if safe:
            for rule in rules:
                anchor = rule.anchor
                if (anchor is None or rule.disabled or rule.skip_chance > 0
                        or rule.border or rule.any_of or rule.none_of
                        or rule.ignore_cells or rule.negate or rule.wildcards
                        or rule.inputnots or not rule.inputs
                        or any(t != "set" for _, _, _, t in rule.inputs)):
                    continue
                adx, ady, gid = anchor[:3]
                ordered = sorted(rule.inputs,
                                 key=lambda it: (it[0] - adx, it[1] - ady))
                shape = tuple((it[0] - adx, it[1] - ady) for it in ordered)
                pattern = tuple(int(it[2]) & GID_MASK for it in ordered)
                bucket.setdefault(gid, {}).setdefault(shape, {}) \
                      .setdefault(pattern, []).append(rule)
                fast_ids.add(id(rule))

        self._fast_index_cache = (bucket, fast_ids)
        self._fast_rules_ref = rules
        return self._fast_index_cache

    def compute(self, doc, area: tuple[int, int, int, int] | None = None,
                radius: int | None = None, only_diff: bool = True,
                reset_base: bool | None = None,
                match_in_order: bool | None = None) -> dict:
        """返回 {层名: [(col,row,gid), ...]}；不修改 doc。

        area 为 (c0,r0,c1,r1)，None 表示全图。局部模式（area 非空）会把输出
        严格限制在 area±radius 内，并按 set 层基础地形重建 Ground，保证绘制时
        自动地图既不会挖洞、也不会把改动扩散到远处。
        """
        set_layer = doc.layer_by_name("set")
        if set_layer is None or not self.loaded or not self.rules:
            return {}
        rules = self._project_rules(doc)
        use_mio = self.match_in_order if match_in_order is None else match_in_order
        local = area is not None
        if not local:
            c0, r0, c1, r1 = 0, 0, doc.width - 1, doc.height - 1
        else:
            c0, r0, c1, r1 = area
        pad = int(radius if radius is not None else
                  (max((r.radius for r in rules), default=0) if local else 0))
        w0, h0 = max(0, c0 - pad), max(0, r0 - pad)
        w1, h1 = min(doc.width - 1, c1 + pad), min(doc.height - 1, r1 + pad)
        if reset_base is None:
            reset_base = local

        index, loose = self._index(rules)
        fast_bucket, fast_ids = self._fast_index(rules)
        base_gids = _base_gids(doc)
        result: dict[tuple[str, int, int], int] = {}
        winners: dict[tuple[str, int, int], tuple] = {}
        cleared: set[tuple[str, int, int]] = set()
        layers: dict[str, TileLayer | None] = {}

        # MatchInOrder：记录已应用输出，供后续规则的匹配读取（Tiled 语义）。
        # key = (层对象 id, x, y)。官方图 input 只读 set 层，输出写 Ground/Items，
        # 因此 overlay 不参与官方图的匹配，旧行为不受影响。
        overlay: dict[tuple[int, int, int], int] = {}
        # NoOverlappingRules：每条规则已应用的输出格，避免同一规则相邻重复匹配重叠。
        applied_by_rule: dict[tuple, set[tuple[str, int, int]]] = defaultdict(set)
        # 随机输出：每条规则选一个 variant（按 seed 确定性派生，保证可复现）。
        import random as _random
        _variant_rng = _random.Random(self.random_seed)

        variant_cache: dict[int, int] = {}
        empty_out_cache: dict[int, set] = {}

        def _pick_variant(rule) -> int:
            rid = id(rule)
            if rid in variant_cache:
                return variant_cache[rid]
            n = max((len(v) for v in rule.outputs.values()), default=0)
            if n <= 1 or not self.random_variants:
                chosen = 0
            else:
                weights = rule.variant_weights[:n] or [1.0] * n
                # 用规则名派生子随机源，保证不同规则独立且整体可复现。
                # 注意不能用内置 hash()（PYTHONHASHSEED 随机化会破坏复现）。
                import zlib
                rule_key = zlib.crc32(str(rule.order).encode("utf-8"))
                sub = _random.Random(rule_key ^ (self.random_seed or 0))
                chosen = sub.choices(range(n), weights=weights, k=1)[0]
            variant_cache[rid] = chosen
            return chosen

        def _put(key, gid, order=()):
            # 同一格被多条规则写时，按规则在规则图里的固定顺序决定胜负。
            # 不能靠扫描先后：逐格实时与整笔一次性重算的扫描起点不同。
            if key not in winners or order >= winners[key]:
                result[key] = gid
                winners[key] = order

        def _layer(name):
            if name not in layers:
                layers[name] = doc.layer_by_name(name) if name else None
            return layers[name]

        def _read_at(layer, x, y, border, raw=False):
            """读取 cell；越界按 border 策略处理。

            border="": 返回 None（越界不参与匹配，旧行为）
            "outside": 返回 0（地图外视为空）
            "overflow": 取最近的边界内 cell
            "wrap": 环绕取 cell
            """
            mask = 0xFFFFFFFF if raw else GID_MASK
            if layer is None:
                return 0
            if use_mio:
                ov = overlay.get((id(layer), x, y))
                if ov is not None:
                    return ov & mask
            if layer.in_bounds(x, y):
                return int(layer.get(x, y)) & mask
            if not border:
                return None
            W, H = layer.width, layer.height
            if W <= 0 or H <= 0:
                return 0
            if border == "outside":
                return 0
            if border == "overflow":
                cx = min(max(x, 0), W - 1)
                cy = min(max(y, 0), H - 1)
                return int(layer.get(cx, cy)) & mask
            if border == "wrap":
                cx = x % W
                cy = y % H
                return int(layer.get(cx, cy)) & mask
            return None

        def _read(layer, x, y, border=""):
            return _read_at(layer, x, y, border, raw=False)

        def _read_raw(layer, x, y, border=""):
            return _read_at(layer, x, y, border, raw=True)

        def _in_window(x, y):
            return w0 <= x <= w1 and h0 <= y <= h1

        # DeleteTiles：局部重算区域内先把 Ground 恢复成 set 层的基础地形，
        # 旧的过渡瓦片自然消失，规则输出随后覆盖；绝不写 0，避免深色空洞。
        if reset_base:
            for y in range(h0, h1 + 1):
                for x in range(w0, w1 + 1):
                    base = _read(set_layer, x, y)
                    if base in base_gids:
                        _put(("Ground", x, y), base)

        def _matches(rule, ox, oy):
            # ---- Tiled MatchType 语义（any_of / none_of / ignore / negate）----
            if rule.any_of or rule.none_of or rule.ignore_cells or rule.negate:
                ignored = rule.ignore_cells

                def _one(value, spec):
                    """判断某格是否满足一条 any/none 条件 spec。"""
                    _dx, _dy, want, target, mask = spec
                    if want == "empty":
                        return value is not None and (value & GID_MASK) == 0
                    if want == "nonempty":
                        return value is not None and (value & GID_MASK) != 0
                    # 具体 gid：gid 相同 + 翻转位按 mask 比较
                    if value is None:
                        return False
                    w = int(want)
                    return ((w & GID_MASK) == (int(value) & GID_MASK)
                            and (w & mask) == (int(value) & mask))

                # 按 (dx,dy) 聚合：同一格的 any 取「或」，none 取「与非」。
                by_cell: dict[tuple[int, int], tuple[list, list]] = defaultdict(
                    lambda: ([], []))
                for spec in rule.any_of:
                    by_cell[(spec[0], spec[1])][0].append(spec)
                for spec in rule.none_of:
                    by_cell[(spec[0], spec[1])][1].append(spec)
                for (dx, dy), (anys, nones) in by_cell.items():
                    if (dx, dy) in ignored:
                        continue
                    # target 取该格任一 spec 的目标层（同格通常同层）
                    target = (anys or nones)[0][3]
                    layer = _layer(target)
                    value = _read_raw(layer, ox + dx, oy + dy, rule.border)
                    ok = any(_one(value, s) for s in anys) if anys else True
                    if any(_one(value, s) for s in nones):
                        ok = False
                    if rule.negate:
                        ok = not ok
                    if not ok:
                        return False
                # 旧式 input/inputnot 也仍然生效（MatchType 规则里也可混用）。

            # ---- 旧式语义（官方规则图全部走这里）----
            for dx, dy, gid, target in rule.inputs:
                if _read(_layer(target), ox + dx, oy + dy, rule.border) != gid:
                    return False
            for dx, dy, gid, target in rule.inputnots:
                if _read(_layer(target), ox + dx, oy + dy, rule.border) == gid:
                    return False
            for dx, dy, target in rule.wildcards:
                value = _read(_layer(target), ox + dx, oy + dy, rule.border)
                if value is None or value == 0 or value in rule.used_gids:
                    return False
            return True

        def _apply_rule(rule, ox, oy, matched=False):
            if rule.disabled:
                return
            # Probability：以 skipChance 概率跳过本次应用（Tiled 语义）。
            if rule.skip_chance > 0:
                import random as _r
                if _r.random() < rule.skip_chance:
                    return
            # ModX/ModY/OffsetX/OffsetY：按规则网格约束匹配位置。
            ax = ox - rule.offset_x
            ay = oy - rule.offset_y
            if ax % rule.mod_x or ay % rule.mod_y:
                return
            if not matched and not _matches(rule, ox, oy):
                return
            # NoOverlappingRules：若本次应用的输出区与同规则已应用区重叠则跳过。
            if rule.no_overlapping:
                out_cells = {(t, ox + dx, oy + dy)
                             for t, variants in rule.outputs.items()
                             for dx, dy, _g in variants[0]}
                if out_cells & applied_by_rule[rule.order]:
                    return
                applied_by_rule[rule.order].update(out_cells)
            # 全图模式保留 DeleteTiles：被 input tile 覆盖却没有输出的格子要清掉。
            if rule.delete and not reset_base:
                for target in rule.outputs:
                    for dx, dy in rule.erase:
                        cleared.add((target, ox + dx, oy + dy))
            # 输出坐标已相对输入区域原点（含 regions_output 的偏移），
            # 只写重算窗口内的格子：局部绘制不会越界扩散。
            rid = id(rule)
            empty_out = empty_out_cache.get(rid)
            if empty_out is None:
                empty_out = ({(t, dx, dy) for t, dx, dy in rule.explicit_empty_outputs}
                             if rule.explicit_empty_outputs else set())
                empty_out_cache[rid] = empty_out
            chosen = _pick_variant(rule)
            for target, variants in rule.outputs.items():
                variant_cells = variants[chosen] if chosen < len(variants) else variants[0]
                for dx, dy, gid in variant_cells:
                    x, y = ox + dx, oy + dy
                    if 0 <= x < doc.width and 0 <= y < doc.height and _in_window(x, y):
                        # MatchType::Empty 输出格显式写空（0），其余保持 gid。
                        out_gid = 0 if (target, dx, dy) in empty_out else gid
                        _put((target, x, y), out_gid, rule.order)
                        if use_mio:
                            out_layer = _layer(target)
                            if out_layer is not None:
                                overlay[(id(out_layer), x, y)] = out_gid

        # 图案快路：一次查表得到该邻域真正适用的规则，省掉「逐条规则试匹配」。
        # 只在 _fast_index 判定 overlay 不参与匹配时启用，语义与慢路完全一致。
        set_cells = set_layer.cells
        doc_w, doc_h = doc.width, doc.height

        def _fast_apply(x, y):
            base = y * doc_w + x
            bucket = fast_bucket.get(int(set_cells[base]) & GID_MASK)
            if not bucket:
                return
            for shape, patterns in bucket.items():
                values = []
                for dx, dy in shape:
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < doc_w and 0 <= ny < doc_h):
                        break                     # 越界按旧语义 _read 返回 None，不匹配
                    values.append(int(set_cells[base + dy * doc_w + dx]) & GID_MASK)
                else:
                    for rule in patterns.get(tuple(values), ()):
                        ax, ay = rule.anchor[:2]
                        ox, oy = x - ax, y - ay
                        if (x + rule.max_rx < w0 or x + rule.min_rx > w1
                                or y + rule.max_ry < h0 or y + rule.min_ry > h1):
                            continue
                        if (ox < 0 or oy < 0 or ox + rule.w > doc_w
                                or oy + rule.h > doc_h):
                            continue
                        _apply_rule(rule, ox, oy, matched=True)

        # 锚点在窗口外、但输出落到窗口内的规则也要跑到，因此四周都外扩。
        margin = max(4, max((r.w for r in rules), default=1),
                     max((r.h for r in rules), default=1)) + 2
        sx0, sy0 = max(0, w0 - margin), max(0, h0 - margin)
        sx1, sy1 = min(doc.width - 1, w1 + margin), min(doc.height - 1, h1 + margin)

        if use_mio:
            # Tiled MatchInOrder：规则按 (规则图, y, x) 排序，逐条匹配并立即应用；
            # 后续规则读取前一条的输出（通过 overlay）。
            # 性能：先一次扫描把锚点 gid -> [(x,y)] 建好，每条规则直接查表，
            # 避免「规则数 × 全图」的重复扫描。
            anchor_positions: dict[int, list[tuple[int, int]]] = defaultdict(list)
            for y in range(sy0, sy1 + 1):
                for x in range(sx0, sx1 + 1):
                    if fast_bucket:
                        _fast_apply(x, y)
                    gid = _read(set_layer, x, y)
                    if gid is not None:
                        anchor_positions[gid].append((x, y))
            for rule in sorted(rules, key=lambda r: r.order):
                if id(rule) in fast_ids:
                    continue                      # 已由图案快路应用
                anchor = rule.anchor
                if anchor is not None:
                    for x, y in anchor_positions.get(anchor[2], ()):
                        if (x + rule.max_rx < w0 or x + rule.min_rx > w1
                                or y + rule.max_ry < h0 or y + rule.min_ry > h1):
                            continue
                        ox, oy = x - anchor[0], y - anchor[1]
                        if (ox < 0 or oy < 0 or ox + rule.w > doc.width
                                or oy + rule.h > doc.height):
                            continue
                        _apply_rule(rule, ox, oy)
                else:
                    for y in range(sy0, sy1 + 1):
                        for x in range(sx0, sx1 + 1):
                            _apply_rule(rule, x, y)
        else:
            # 默认并发匹配（官方规则图走这里，实测 100% 复现率）。
            for y in range(sy0, sy1 + 1):
                for x in range(sx0, sx1 + 1):
                    if fast_bucket:
                        _fast_apply(x, y)
                    gid = _read(set_layer, x, y)
                    if gid is None:
                        continue
                    for rule, dx0, dy0 in index.get(gid, ()):
                        if id(rule) in fast_ids:
                            continue              # 已由图案快路应用
                        # 该规则的输出够不到重算窗口就跳过，实时绘制时省掉绝大多数匹配。
                        if (x + rule.max_rx < w0 or x + rule.min_rx > w1
                                or y + rule.max_ry < h0 or y + rule.min_ry > h1):
                            continue
                        ox, oy = x - dx0, y - dy0
                        if ox < 0 or oy < 0 or ox + rule.w > doc.width or oy + rule.h > doc.height:
                            continue
                        _apply_rule(rule, ox, oy)
            for rule in loose:
                for y in range(sy0, sy1 + 1):
                    for x in range(sx0, sx1 + 1):
                        _apply_rule(rule, x, y)

        # 全图模式：清掉的格子用 set 基础地形兜底，避免官方图上按 Ctrl+M 挖出黑洞。
        # result 里已有规则输出的格子不会被覆盖。
        for target, x, y in cleared:
            if not (0 <= x < doc.width and 0 <= y < doc.height):
                continue
            base = _read(set_layer, x, y)
            _put((target, x, y), base if base in base_gids else 0)

        changes: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        for (target, x, y), gid in result.items():
            layer = _layer(target)
            if layer is None or not layer.in_bounds(x, y):
                continue
            if only_diff and (int(layer.get(x, y)) & GID_MASK) == gid:
                continue
            changes[target].append((x, y, gid))
        return dict(changes)
