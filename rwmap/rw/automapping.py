"""铁锈战争 automapping（自动映射），对齐 Tiled 旧式 rules.txt 规则。

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
from pathlib import Path

from ..document.gid import GID_MASK
from ..document.layer import TileLayer
from ..io.tmx_reader import read_document
from ..utils.paths import as_path

# set 层里直接对应 Ground 底图的地形 gid（官方示例图逐格统计 + 各基础图集首格）。
# 自动映射重算区域时用它兜底，避免 DeleteTiles 清出深色空洞。
BASE_GIDS = frozenset({1, 36, 116, 126, 176, 186, 192, 193, 770})

_LAYER_ALIASES = {
    "set": "set", "ground": "Ground", "items": "Items", "units": "Units",
}


def _base_gids(doc) -> set[int]:
    """基础地形 = 各基础图集的第 1 格，外加已知常量；过渡图集（名字含 " - "）不算。"""
    gids = set(BASE_GIDS)
    for ts in getattr(doc, "tilesets", ()):
        name = (ts.name or "").lower()
        if not ts.firstgid or " - " in name or name in ("units", "misc"):
            continue
        gids.add(int(ts.firstgid))
    return gids


def normalize_marker_gid(doc, raw: int) -> int:
    """把任意基础地形图集内的瓦片归一到该图集首格（set 层标记语义）。

    官方 rules.txt 的 input 层只认各基础图集的第 1 格。用户在图集面板点了
    中间某一格（如 Long Grass 的 130），或用右键从 Ground 层抓了已渲染的
    多格图章，写进 set 层后不会匹配任何规则 → 整条边界没有过渡瓦片。
    set 层在游戏里被忽略，只是标记层，归一到首格不改变地图语义。

    返回 -1 表示该 gid 不属于任何基础地形图集（过渡图集 / units / misc），
    调用方应原样写入并提示用户。
    """
    raw_int = int(raw) & 0xFFFFFFFF
    gid = raw_int & GID_MASK
    flags = raw_int & ~GID_MASK
    if not gid:
        return raw_int
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
            return int(ts.firstgid) | flags
    return raw_int if gid in BASE_GIDS else -1


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
    delete: bool = False
    radius: int = 0
    # DeleteTiles 擦除位置（输入区域内出现过 input tile 的坐标）
    erase: list[tuple[int, int]] = field(default_factory=list)
    strict_empty: bool = True       # 是否启用旧式空 input 通配
    # 规则在 rules.txt / 规则图中的固定顺序，用于消解多条规则写同一格的冲突
    order: tuple = ()
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
        self.rules: list[Rule] = []
        self.rule_files: list[str] = []
        self.loaded = False
        self.errors: list[str] = []
        self._index_cache = None
        self._index_rules_ref = None

    # ---------------- 加载规则 ----------------
    def load(self) -> dict:
        self.rules, self.rule_files, self.errors = [], [], []
        self._index_cache = None
        if not self.rules_file.is_file():
            return {"ok": False, "error": f"规则索引不存在: {self.rules_file}"}
        for line in self.rules_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            path = (self.rules_file.parent / line).resolve()
            if not path.is_file():
                self.errors.append(f"缺规则文件 {path}")
                continue
            try:
                doc = read_document(path, self.profile)
                self.rules.extend(self._parse_rulemap(doc, path))
                self.rule_files.append(str(path))
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"{path.name}: {exc}")
        self.loaded = True
        return {
            "ok": bool(self.rules),
            "rules_file": str(self.rules_file),
            "rule_maps": self.rule_files,
            "rules": len(self.rules),
            "errors": self.errors,
        }

    def _parse_rulemap(self, doc, path: Path) -> list[Rule]:
        props = {str(k).lower(): str(v) for k, v in doc.properties.items()}
        delete = props.get("deletetiles", "").lower() in ("1", "true", "yes")
        try:
            radius = int(props.get("automappingradius", "0") or 0)
        except ValueError:
            radius = 0
        layers = {l.name.lower(): l for l in doc.layers if isinstance(l, TileLayer)}

        # ---- 区域：输入连通块 + 输出连通块配对 ----
        # in_cells / out_cells 是「属于该规则的格子」相对输入区域原点的坐标集合。
        # 必须保留非矩形形状（拐角规则是 L 形），不能用外接矩形代替。
        def _rel(comp, x0, y0):
            return frozenset((i % doc.width - x0, i // doc.width - y0)
                             for i in comp)

        has_split_regions = "regions_input" in layers and "regions_output" in layers
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

        out: list[Rule] = []
        for ci, (in_box, in_cells, out_cells) in enumerate(regions):
            ix0, iy0, ix1, iy1 = in_box
            w, h = ix1 - ix0 + 1, iy1 - iy0 + 1
            rule = Rule(name=f"{path.stem}#{ci}", w=w, h=h,
                        delete=delete, radius=radius,
                        strict_empty=not has_inputnot_layer,
                        order=(path.stem, iy0, ix0))
            covered: set[tuple[int, int]] = set()
            primary_target: str | None = None
            for is_not, target, layer in input_layers:
                if primary_target is None and not is_not:
                    primary_target = target
                # 只检查属于规则区域的格子；外接矩形里多出来的缺口不参与匹配。
                for dx, dy in sorted(in_cells):
                    gid = int(layer.get(ix0 + dx, iy0 + dy)) & GID_MASK
                    if not gid:
                        continue
                    item = (dx, dy, gid, target)
                    if is_not:
                        rule.inputnots.append(item)
                    else:
                        rule.inputs.append(item)
                        rule.used_gids.add(gid)
                        covered.add((dx, dy))
            if rule.strict_empty and primary_target is not None:
                for dx, dy in sorted(in_cells - covered):
                    rule.wildcards.append((dx, dy, primary_target))
            rule.erase = sorted(covered)

            for target, variant, layer in output_layers:
                cells = []
                # 输出同样只在输出区域内取，避免串进相邻规则的瓦片。
                for dx, dy in sorted(out_cells):
                    gid = int(layer.get(ix0 + dx, iy0 + dy)) & GID_MASK
                    if gid:
                        cells.append((dx, dy, gid))
                bucket = rule.outputs.setdefault(target, [])
                while len(bucket) <= variant:
                    bucket.append([])
                bucket[variant].extend(cells)
            rule.outputs = {
                k: [v for v in variants if v]
                for k, variants in rule.outputs.items()
            }
            rule.outputs = {k: v for k, v in rule.outputs.items() if v}
            if rule.inputs and rule.outputs:
                out.append(rule.finish())
        return out

    # ---------------- 计算应写入的格子 ----------------
    def _index(self):
        # 实时绘制时每格都会调用，规则不变就复用索引。
        if self._index_cache is not None and self._index_rules_ref is self.rules:
            return self._index_cache
        index: dict[int, list[tuple[Rule, int, int]]] = defaultdict(list)
        loose: list[Rule] = []
        for rule in self.rules:
            anchor = rule.anchor
            if anchor is None:
                loose.append(rule)
            else:
                index[anchor[2]].append((rule, anchor[0], anchor[1]))
        self._index_cache = (index, loose)
        self._index_rules_ref = self.rules
        return self._index_cache

    def compute(self, doc, area: tuple[int, int, int, int] | None = None,
                radius: int | None = None, only_diff: bool = True,
                reset_base: bool | None = None) -> dict:
        """返回 {层名: [(col,row,gid), ...]}；不修改 doc。

        area 为 (c0,r0,c1,r1)，None 表示全图。局部模式（area 非空）会把输出
        严格限制在 area±radius 内，并按 set 层基础地形重建 Ground，保证绘制时
        自动地图既不会挖洞、也不会把改动扩散到远处。
        """
        set_layer = doc.layer_by_name("set")
        if set_layer is None or not self.loaded or not self.rules:
            return {}
        local = area is not None
        if not local:
            c0, r0, c1, r1 = 0, 0, doc.width - 1, doc.height - 1
        else:
            c0, r0, c1, r1 = area
        pad = int(radius if radius is not None else
                  (max((r.radius for r in self.rules), default=0) if local else 0))
        w0, h0 = max(0, c0 - pad), max(0, r0 - pad)
        w1, h1 = min(doc.width - 1, c1 + pad), min(doc.height - 1, r1 + pad)
        if reset_base is None:
            reset_base = local

        index, loose = self._index()
        base_gids = _base_gids(doc)
        result: dict[tuple[str, int, int], int] = {}
        winners: dict[tuple[str, int, int], tuple] = {}
        cleared: set[tuple[str, int, int]] = set()
        layers: dict[str, TileLayer | None] = {}

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

        def _read(layer, x, y):
            if layer is None:
                return 0                # dummy 全空层
            if not layer.in_bounds(x, y):
                return None
            return int(layer.get(x, y)) & GID_MASK

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
            for dx, dy, gid, target in rule.inputs:
                if _read(_layer(target), ox + dx, oy + dy) != gid:
                    return False
            for dx, dy, gid, target in rule.inputnots:
                if _read(_layer(target), ox + dx, oy + dy) == gid:
                    return False
            for dx, dy, target in rule.wildcards:
                value = _read(_layer(target), ox + dx, oy + dy)
                if value is None or value == 0 or value in rule.used_gids:
                    return False
            return True

        def _apply_rule(rule, ox, oy):
            if not _matches(rule, ox, oy):
                return
            # 全图模式保留 DeleteTiles：被 input tile 覆盖却没有输出的格子要清掉。
            if rule.delete and not reset_base:
                for target in rule.outputs:
                    for dx, dy in rule.erase:
                        cleared.add((target, ox + dx, oy + dy))
            # 输出坐标已相对输入区域原点（含 regions_output 的偏移），
            # 只写重算窗口内的格子：局部绘制不会越界扩散。
            for target, variants in rule.outputs.items():
                for dx, dy, gid in variants[0]:
                    x, y = ox + dx, oy + dy
                    if 0 <= x < doc.width and 0 <= y < doc.height and _in_window(x, y):
                        _put((target, x, y), gid, rule.order)

        # 锚点在窗口外、但输出落到窗口内的规则也要跑到，因此四周都外扩。
        margin = max(4, max((r.w for r in self.rules), default=1),
                     max((r.h for r in self.rules), default=1)) + 2
        sx0, sy0 = max(0, w0 - margin), max(0, h0 - margin)
        sx1, sy1 = min(doc.width - 1, w1 + margin), min(doc.height - 1, h1 + margin)
        for y in range(sy0, sy1 + 1):
            for x in range(sx0, sx1 + 1):
                gid = _read(set_layer, x, y)
                if gid is None:
                    continue
                for rule, dx0, dy0 in index.get(gid, ()):
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
