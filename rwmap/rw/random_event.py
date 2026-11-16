"""等概率「随机触发事件」模板：全部用铁锈原生触发器 type 拼出来。

铁锈战争**没有** ``random`` 这类随机触发器。等概率抽签靠 ``unitAdd`` 的
``spawnUnits`` 内联参数 ``spawnChance`` + ``maxSpawnLimit=1`` 实现：

* 第 1 个标记单位以 ``1/N`` 概率生成；一旦生成成功，本次生成的总上限 1
  就用满了，后面的单位都不再生成；
* 若第 1 个没生成，第 2 个以 ``1/(N-1)`` 概率生成……最后一个必生成。

于是 N 个分支各占 ``1/N``，这就是「概率相同」。本机 24 张真实铁锈地图里
已有同类写法，例如 ``tank(spawnChance=0.35,maxSpawnLimit=1),mechGun(...)``。

抽签结果 = 抽签区里出现哪一种标记单位。每个分支一个 ``unitDetect`` 检测
自己的标记，只有命中的那一支继续往下走；命中的分支再检测「指定矩形内是否
有目标兵种」，满足后用 ``unitRemove`` 清空同一个矩形。

.. note::
   ``unitRemove`` **不支持指定兵种 / 数量**，只能按矩形 + ``team`` 清空。
   所以动作节点与条件节点共用同一个矩形并靠 team 收窄；矩形里若还有其它
   同队单位会被一起删掉（游戏本身的限制）。

本模块只负责产出 ``MapObject``，不碰 Qt / 不碰 UI，方便测试与脚本复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..document.layer import ObjectLayer
from ..document.map_object import MapObject
from ..document.properties import PropertyBag
from ..rw import unit_names

# 抽签标记：必须是游戏里真实存在的单位 raw name（本机 units.tsx 实测）。
# 两个分支默认用 builder / tank；扩到 4 分支时追加 scout / turret。
DEFAULT_MARKERS: tuple[str, ...] = ("builder", "tank", "scout", "turret")
DEFAULT_UNIT_TYPE = "heavyTank"          # 重型坦克


@dataclass
class RandomEventSpec:
    """一条等概率随机事件链的可配参数。坐标都是 Tiled 像素坐标。"""

    name: str = "随机事件"
    rect: tuple[float, float, float, float] = (400.0, 300.0, 120.0, 120.0)
    unit_type: str = DEFAULT_UNIT_TYPE
    team: str = "0"
    branch_count: int = 2
    markers: tuple[str, ...] = field(default_factory=lambda: DEFAULT_MARKERS)
    roll_rect: tuple[float, float, float, float] = (0.0, 0.0, 20.0, 20.0)
    warmup: str = "0s"
    reset_after: str = "1s"
    limit_one: bool = False              # True = 恰好一辆（maxUnits=1）
    id_prefix: str = "re"

    def validated(self) -> "RandomEventSpec":
        n = max(2, min(int(self.branch_count), len(self.markers)))
        return RandomEventSpec(
            name=self.name or "随机事件",
            rect=tuple(float(v) for v in self.rect),      # type: ignore[arg-type]
            unit_type=self.unit_type or DEFAULT_UNIT_TYPE,
            team=str(self.team),
            branch_count=n,
            markers=tuple(self.markers[:n]),
            roll_rect=tuple(float(v) for v in self.roll_rect),  # type: ignore[arg-type]
            warmup=self.warmup, reset_after=self.reset_after,
            limit_one=self.limit_one, id_prefix=self.id_prefix,
        )


# ---------------------------------------------------------------- 概率
def equal_chances(n: int) -> list[float]:
    """N 个分支的条件概率：``1/N, 1/(N-1), …, 1/1``，最终各占 1/N。"""
    n = max(1, int(n))
    return [1.0 / (n - i) for i in range(n)]


def format_chance(p: float) -> str:
    """写成短小数，避免 ``0.3333333333`` 这种又长又不精确的值。"""
    return ("%.6f" % p).rstrip("0").rstrip(".")


def spawn_units_value(spec: RandomEventSpec) -> str:
    """生成 ``spawnUnits`` 值（逗号后不加空格，符合铁锈解析习惯）。"""
    s = spec.validated()
    chances = equal_chances(s.branch_count)
    return ",".join(
        f"{marker}(spawnChance={format_chance(chances[i])},maxSpawnLimit=1)"
        for i, marker in enumerate(s.markers)
    )


def branch_labels(spec: RandomEventSpec) -> list[str]:
    """给界面用的「标记单位 50%」列表（单位显示中文）。"""
    s = spec.validated()
    p = 100.0 / s.branch_count
    text = ("%g" % p) + "%"
    return [f"{unit_names.to_cn(marker)} · {text}" for marker in s.markers]


def describe(spec: RandomEventSpec) -> str:
    """中文机制说明（写进对话框 / 示例说明，不写进 TMX）。"""
    s = spec.validated()
    p = "%g" % (100.0 / s.branch_count)
    x, y, w, h = s.rect
    return (
        f"抽签 {s.branch_count} 选 1，每支 {p}%；"
        f"抽中后检测矩形 ({x:g},{y:g},{w:g},{h:g}) 内的 {unit_names.to_cn(s.unit_type)}，"
        f"满足条件即清空该矩形内 team={s.team} 的单位。"
        "注意 unitRemove 不支持指定兵种，矩形内其它同队单位会一起被删。"
    )


# ---------------------------------------------------------------- 对象
def _ids(spec: RandomEventSpec):
    p = spec.id_prefix or "re"
    return {
        "roll": f"{p}_roll",
        "check": f"{p}_check",
        "remove": f"{p}_remove",
        "hit": [f"{p}_b{i + 1}" for i in range(spec.branch_count)],
    }


def _make(oid: int, name: str, type_: str, rect, props: dict) -> MapObject:
    bag = PropertyBag()
    for key, value in props.items():
        bag.set(key, str(value))
    x, y, w, h = rect
    return MapObject(
        object_id=oid, name=name, type_=type_,
        x=float(x), y=float(y), width=float(w), height=float(h),
        shape="rect", properties=bag,
    )


def build_random_event_objects(spec: RandomEventSpec,
                               start_object_id: int = 1) -> list[MapObject]:
    """按规格生成整条链的对象列表（不含任何 Qt 依赖）。

    节点顺序即节点图的默认布局顺序：抽签器 → 各分支判定 → 条件 → 动作。
    所有连线都用铁锈真实识别的 ``activatedBy`` / ``alsoActivate``，
    不用自造属性，写进 TMX 后游戏能正常解析。
    """
    s = spec.validated()
    ids = _ids(s)
    oid = int(start_object_id)
    objs: list[MapObject] = []

    p = "%g" % (100.0 / s.branch_count)
    # 节点标题栏很窄，把「编号 + 关键信息」放前面，事件名放最后可安全截断。
    objs.append(_make(
        oid, f"① 抽签 {s.branch_count} 选 1（各 {p}%） · {s.name}",
        "unitAdd", s.roll_rect,
        {
            "id": ids["roll"],
            "spawnUnits": spawn_units_value(s),
            "team": s.team,
            "warmup": s.warmup,
            "comment": "等概率抽签：spawnChance=1/N,1/(N-1),…,1 + maxSpawnLimit=1",
        },
    ))
    oid += 1

    # ② 每个分支一个 unitDetect，检测自己那种标记单位
    for i, marker in enumerate(s.markers):
        hit_id = ids["hit"][i]
        props = {
            "id": hit_id,
            "unitType": marker,
            "minUnits": "1",
            "maxUnits": "1",
            "team": s.team,
            "activatedBy": ids["roll"],
            "resetActivationAfter": s.reset_after,
        }
        if i == 0:
            props["alsoActivate"] = ids["check"]
            label = f"② 抽中（{p}%）激活条件 · {s.name}"
        else:
            label = f"② 未抽中 {i + 1}（无动作） · {s.name}"
            props["comment"] = "抽签未命中：这条分支什么都不做"
        objs.append(_make(oid, label, "unitDetect", s.roll_rect, props))
        oid += 1

    # ③ 事件条件：指定矩形内有目标兵种
    check_props = {
        "id": ids["check"],
        "unitType": s.unit_type,
        "minUnits": "1",
        "team": s.team,
        "alsoActivate": ids["remove"],
        "resetActivationAfter": s.reset_after,
        "comment": f"条件：矩形内存在 {s.unit_type}",
    }
    if s.limit_one:
        check_props["maxUnits"] = "1"
    objs.append(_make(
        oid, f"③ 矩形内有 {s.unit_type} · {s.name}", "unitDetect", s.rect,
        check_props,
    ))
    oid += 1

    # ④ 动作：清空同一矩形（unitRemove 不支持指定兵种）
    objs.append(_make(
        oid, f"④ 删除 {s.unit_type} · {s.name}", "unitRemove", s.rect,
        {
            "id": ids["remove"],
            "team": s.team,
            "resetActivationAfter": s.reset_after,
            "comment": "unitRemove 不支持指定兵种：靠与条件相同的矩形 + team 收窄",
        },
    ))
    return objs


def find_or_create_triggers_layer(doc) -> ObjectLayer:
    """返回（必要时新建）名为 Triggers 的对象层。"""
    for layer in doc.layers:
        if isinstance(layer, ObjectLayer) and (layer.name or "").lower() == "triggers":
            return layer
    layer = ObjectLayer(name="Triggers", objects=[], dirty=True)
    doc.layers.append(layer)
    return layer


def unique_prefix(doc, base: str = "re") -> str:
    """给新插入的事件链挑一个不与已有自定义 id 冲突的前缀。"""
    used = {str(o.properties.get("id", "")) for l in doc.layers
            if isinstance(l, ObjectLayer) for o in l.objects}
    prefix, n = base or "re", 2
    while any(v.startswith(prefix + "_") for v in used):
        prefix, n = f"{base}{n}", n + 1
    return prefix


def insert_random_event(doc, spec: RandomEventSpec) -> list[MapObject]:
    """把整条链插进文档的 Triggers 层，返回新增对象（供 UI / 测试用）。"""
    layer = find_or_create_triggers_layer(doc)
    start = max((o.object_id for l in doc.layers
                 if isinstance(l, ObjectLayer) for o in l.objects), default=0) + 1
    s = spec.validated()
    s.id_prefix = unique_prefix(doc, s.id_prefix)
    objs = build_random_event_objects(s, start)
    layer.objects.extend(objs)
    layer.dirty = True
    doc.dirty = True
    return objs
