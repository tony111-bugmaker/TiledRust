"""有序、保真的自定义属性容器。

铁锈地图大量使用 <property name="x" value=""/> 空值，以及
<property name="embedded_png">BASE64...</property> 文本形式属性。
未知键一律原样保留，禁止「清洗」。
"""
from dataclasses import dataclass, field

# 本编辑器私有的「分组」属性键：只被本软件识别，铁锈战争（Rusted Warfare）
# 不会读取该名字（它只认自己的触发器属性，如 alsoActivate / team 等），
# 因此写入 TMX 后绝不影响游戏运行。请勿改名或在导出时清洗。
EDITOR_GROUP_PROPERTY = "rwmap_group"

# 本编辑器私有的「节点图位置」属性键：保存时把每个触发器对象在节点图里的
# 相对坐标（以左上角对象为原点）写进对象，供下次打开恢复布局。铁锈战争不读
# 该名字，绝不影响游戏运行。只在保存时写，拖动过程中不实时写。
EDITOR_NODE_POS_PROPERTY = "rwmap_node_pos"

# 旧版 / 手写 TMX 里也会用 ``rwmap_noc``（node coordinate）记录节点图位置。
# 和分组一样属于编辑器私有属性：必须原样写进文件、下次打开仍能恢复布局，
# 但绝不出现在右侧属性面板、添加属性下拉、节点摘要和搜索里。
LEGACY_EDITOR_NODE_POS_PROPERTY = "rwmap_noc"

# 「波次生成器」节点（waveGen）的私有属性：一个节点 = 生存模式的一波刷兵。
# 编排数据全部存在这些键里，导出时既从对象属性剔除、又收进地图根属性
# rwmap_layout，因此铁锈战争看不到它们，不会报 "Key was not used"；重开时
# 由 _apply_editor_layout 还原回对象，节点与连线和布局一起恢复。
WAVE_GEN_TYPE = "waveGen"
WAVE_MARK_PROPERTY = "rwmap_wave"          # 标记为波次节点（值 "1"）
WAVE_TIME_PROPERTY = "rwmap_wave_time"     # 波次时间，如 "0:10" / "10s"
WAVE_NAME_PROPERTY = "rwmap_wave_name"     # 波次名，如 "第一波"
WAVE_UNITS_PROPERTY = "rwmap_wave_units"   # 刷兵，如 "6 bugMelee,2 heavyTank"
WAVE_NEXT_PROPERTY = "rwmap_wave_next"     # 下一波节点 id（forward 连线）
# 波次节点 → 其它触发器（播报 / 奖励）的「关联」连线：编排走私有属性 +
# rwmap_layout；保存时另生成隐藏的 basic 调度触发器（delay + alsoActivate）
# 让游戏真正到点触发。不能直接把 alsoActivate 写在波次节点上——它是空 type
# 的编辑器私有对象，游戏会跳过，写了也无效。
WAVE_LINK_PROPERTY = "rwmap_wave_link"
# 波次关联在游戏里的落地：保存时按波次累计时间生成一个隐藏的 basic 触发器，
# 用 delay=<秒>s + alsoActivate=<目标 id> 真正触发播报 / 奖励等普通触发器。
# 这两个键只是编辑器的识别标记，跟着 rwmap_layout 走，游戏看不到。
WAVE_DISPATCH_MARK_PROPERTY = "rwmap_wave_dispatch"
WAVE_DISPATCH_WAVE_PROPERTY = "rwmap_wave_dispatch_wave"
# 游戏 TriggerEngine.load() 无条件跳过名为 attack_point 的对象，所以给这些
# 对象配的 unitAdd（生成单位）永远不会触发。保存时按每个 attack_point 复制
# 一份「影子触发器」：名字不叫 attack_point、坐标属性一致、由波次调度器激活，
# 这样同一批点既保留官方刷兵点身份，又能按连线真的生成单位。
WAVE_SPAWN_PROXY_MARK_PROPERTY = "rwmap_wave_spawn_proxy"
WAVE_SPAWN_PROXY_SOURCE_PROPERTY = "rwmap_wave_spawn_proxy_src"

# 「官方波次表」节点（waveTable）：把原版 survivalWaves 整段文本挂在一个
# 隐藏触发器节点上，像普通触发器一样可在节点图连线，但地图画布不显示。
# 编排数据同样走私有属性 + rwmap_layout，写盘从对象剔除；保存时把文本
# 同步回 map_info.survivalWaves，游戏据此显示官方倒计时。
WAVE_TABLE_TYPE = "waveTable"
WAVE_TABLE_MARK_PROPERTY = "rwmap_wave_table"          # 标记为官方波次表节点（值 "1"）
WAVE_TABLE_TEXT_PROPERTY = "rwmap_wave_table_text"     # 原版波次表整段文本
WAVE_TABLE_NEXT_PROPERTY = "rwmap_wave_table_next"     # 连线引用（forward）

# 「整合体」：节点图里把多个同配置对象折叠成一个节点显示，右边面板改一次
# 就同步改所有成员（名字 / 属性 / 连线）。成员都带同一个组 id，代表节点再带
# 一个 primary 标记；其余成员不在节点图里单独渲染。全部存私有属性，跟着
# rwmap_layout 进地图根属性，游戏看不到，不会报 "Key was not used"。
MERGE_GROUP_PROPERTY = "rwmap_merge_group"
MERGE_PRIMARY_PROPERTY = "rwmap_merge_primary"

# 统一隐藏列表：新增「只给编辑器自己看」的属性时，只需加到这里。
# 所有界面过滤都读这个集合，避免某一处漏改又把私有属性显示出来。
HIDDEN_EDITOR_PROPERTIES = frozenset({
    EDITOR_GROUP_PROPERTY,
    EDITOR_NODE_POS_PROPERTY,
    LEGACY_EDITOR_NODE_POS_PROPERTY,
    WAVE_MARK_PROPERTY,
    WAVE_TIME_PROPERTY,
    WAVE_NAME_PROPERTY,
    WAVE_UNITS_PROPERTY,
    WAVE_NEXT_PROPERTY,
    WAVE_LINK_PROPERTY,
    WAVE_DISPATCH_MARK_PROPERTY,
    WAVE_DISPATCH_WAVE_PROPERTY,
    WAVE_SPAWN_PROXY_MARK_PROPERTY,
    WAVE_SPAWN_PROXY_SOURCE_PROPERTY,
    WAVE_TABLE_MARK_PROPERTY,
    WAVE_TABLE_TEXT_PROPERTY,
    WAVE_TABLE_NEXT_PROPERTY,
    MERGE_GROUP_PROPERTY,
    MERGE_PRIMARY_PROPERTY,
})


@dataclass
class PropertyBag:
    values: dict[str, str] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)
    text_props: set[str] = field(default_factory=set)

    @classmethod
    def from_element(cls, elem) -> "PropertyBag":
        bag = cls()
        if elem is None:
            return bag
        for node in elem:
            if not hasattr(node, "tag") or not isinstance(node.tag, str):
                continue  # 注释节点
            if node.tag != "property":
                continue
            name = node.get("name", "")
            typ = node.get("type")
            if typ:
                bag.types[name] = typ
            if "value" in node.attrib:
                bag.values[name] = node.get("value", "")
            else:
                bag.values[name] = node.text or ""
                bag.text_props.add(name)
        return bag

    def get(self, name: str, default=None):
        return self.values.get(name, default)

    def set(self, name: str, value: str, typ: str | None = None):
        self.values[name] = str(value)
        if typ:
            self.types[name] = typ
        else:
            self.types.pop(name, None)

    def items(self):
        return self.values.items()

    def __contains__(self, name: str) -> bool:
        return name in self.values

    def __len__(self) -> int:
        return len(self.values)
