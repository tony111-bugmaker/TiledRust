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

# 统一隐藏列表：新增「只给编辑器自己看」的属性时，只需加到这里。
# 所有界面过滤都读这个集合，避免某一处漏改又把私有属性显示出来。
HIDDEN_EDITOR_PROPERTIES = frozenset({
    EDITOR_GROUP_PROPERTY,
    EDITOR_NODE_POS_PROPERTY,
    LEGACY_EDITOR_NODE_POS_PROPERTY,
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
