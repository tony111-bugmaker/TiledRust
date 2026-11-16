"""铁锈瓦片属性词典：中文标签、互斥分组、可选预设值。

数据来源：
* ``logs/rw_reference_scan.txt``（本机 24 张真实铁锈地图的键名频次扫描）；
* ``docs/铁锈地图_属性与指令速查.md`` 第 3 节；
* Rusted Warfare Fandom ``Custom_Maps`` 的 Other Custom Properties 表。

写回 ``.tsx`` 时键名一律是这里的英文 key，中文只用于界面显示。
地形 / 通行类属性是**互斥**的：一格瓦片只应有一个，同时存在多个会互相冲突
（游戏实际只认其中一部分），所以界面用单选并在载入时检测冲突。

此外，一个瓦片在语义上只能属于「地形」或「单位」其中一类：
地形格（water / lava / cliff …）不该同时带 unit / team 这类单位标记。
界面按类别分页显示，写入某一类时自动清理另一类，读入旧文件时给出冲突告警。
「其他」是自定义 / 未知属性，可自由叠加，不参与互斥。
"""
from __future__ import annotations

# ---- 互斥组：地形 / 通行（键存在即生效，值留空） ----
# (key, 中文标签, 说明)
TERRAIN_PROPS: list[tuple[str, str, str]] = [
    ("water", "水面", "舰船 / 两栖可过；常规陆军与建筑不可"),
    ("water-bridge", "浅水桥", "陆军、海军、空军均可过且可建造；潜水单位强制上浮"),
    ("cliff", "悬崖", "挡常规陆地；两栖 / 大型机甲可过"),
    ("cliff-soft", "软悬崖", "挡常规陆地；官方说明舰船与陆地单位可过"),
    ("large-cliff", "大悬崖", "挡地面单位，另挡悬浮 / 小型机甲"),
    ("lava-cliff", "岩浆崖", "挡地面单位 + 禁止海军建筑"),
    ("lava", "岩浆", "全不可过，自带冒泡动画，死亡贴图下沉"),
    ("block-land", "阻挡陆地", "挡陆地单位 + 禁止建造"),
    ("large-rock", "大岩石", "挡大部分地面单位"),
    ("small-rock", "小岩石", "小型岩石装饰"),
    ("tree", "树", "挡常规陆地 / 两栖；大型机甲可过"),
    ("res_pool", "资源池", "可放资源抽取器；须 3×3 整块放在 Items 层"),
]
TERRAIN_KEYS: tuple[str, ...] = tuple(k for k, _, _ in TERRAIN_PROPS)
TERRAIN_LABELS: dict[str, str] = {k: label for k, label, _ in TERRAIN_PROPS}
TERRAIN_TIPS: dict[str, str] = {k: tip for k, _, tip in TERRAIN_PROPS}

# ---- 可多选组：单位 / 标记（需要值，给常用预设，允许自定义） ----
# (key, 中文标签, 预设值, 说明)
VALUE_PROPS: list[tuple[str, str, tuple[str, ...], str]] = [
    ("unit", "单位 raw name",
     ("builder", "tank", "builderShip", "commandCenter", "mechFactory",
      "extractor", "turret", "scout", "hovertank"),
     "units.tsx 每格的单位内部名；填错不会生成单位"),
    ("team", "队伍 / 换色",
     tuple(str(i) for i in range(15)) + ("-1", "-2", "none"),
     "0~14 为队伍；-2 / none 语义官方未定义，别用在关键逻辑"),
    ("type", "版本标记", ("1.0", "1.1", "1.2"),
     "图集 / 版本标记，多见于对象层"),
    ("showFog", "战争迷雾", ("5", "10", "20", "40"),
     "官方 units.tsx 有 4 格带此属性，语义官方文档未写明"),
]
VALUE_KEYS: tuple[str, ...] = tuple(k for k, _, _, _ in VALUE_PROPS)
VALUE_PRESETS: dict[str, tuple[str, ...]] = {k: v for k, _, v, _ in VALUE_PROPS}
VALUE_LABELS: dict[str, str] = {k: label for k, label, _, _ in VALUE_PROPS}
VALUE_TIPS: dict[str, str] = {k: tip for k, _, _, tip in VALUE_PROPS}

# ---- 面板里能直接「点选添加」的全部已知键 ----
KNOWN_TILE_KEYS: tuple[str, ...] = TERRAIN_KEYS + VALUE_KEYS

# ---- 属性类别：地形 / 单位 互斥，其他自由叠加 ----
CAT_TERRAIN = "terrain"
CAT_UNIT = "unit"
CAT_OTHER = "other"
CATEGORY_ORDER: tuple[str, ...] = (CAT_TERRAIN, CAT_UNIT, CAT_OTHER)
CATEGORY_LABELS: dict[str, str] = {
    CAT_TERRAIN: "地形",
    CAT_UNIT: "单位",
    CAT_OTHER: "其他",
}
CATEGORY_TIPS: dict[str, str] = {
    CAT_TERRAIN: "地形 / 通行：键存在即生效，一格只能选一个",
    CAT_UNIT: "单位 / 标记：可多选，需要填值",
    CAT_OTHER: "自定义 / 未知属性：可自由叠加，也能自己加键名",
}


def exclusive_group_of(key: str) -> str | None:
    """返回 key 所属的互斥组名；不属于任何互斥组返回 None。"""
    if key in TERRAIN_KEYS:
        return "地形 / 通行"
    return None


def conflicting_keys(keys) -> list[str]:
    """找出同一互斥组里同时出现的键（顺序保持输入顺序）。"""
    present = [k for k in keys if exclusive_group_of(k) is not None]
    return present if len(present) > 1 else []


def category_of(key: str) -> str:
    """一个属性键属于哪个类别；未知键一律归「其他」。"""
    if key in TERRAIN_KEYS:
        return CAT_TERRAIN
    if key in VALUE_KEYS:
        return CAT_UNIT
    return CAT_OTHER


def detect_category(keys, default: str = CAT_TERRAIN) -> str:
    """按瓦片已有属性推断面板初始类别；冲突时优先地形。"""
    cats = {category_of(k) for k in keys}
    if CAT_TERRAIN in cats:
        return CAT_TERRAIN
    if CAT_UNIT in cats:
        return CAT_UNIT
    if CAT_OTHER in cats:
        return CAT_OTHER
    return default


def cross_category_conflicts(keys) -> dict[str, list[str]]:
    """地形与单位同时存在时返回两类的键；否则返回空字典。"""
    terrain = [k for k in keys if category_of(k) == CAT_TERRAIN]
    unit = [k for k in keys if category_of(k) == CAT_UNIT]
    if terrain and unit:
        return {CAT_TERRAIN: terrain, CAT_UNIT: unit}
    return {}


def other_category_keys(category: str) -> tuple[str, ...]:
    """写入 category 时要清掉的互斥另一类键；「其他」不参与。"""
    if category == CAT_TERRAIN:
        return VALUE_KEYS
    if category == CAT_UNIT:
        return TERRAIN_KEYS
    return ()
