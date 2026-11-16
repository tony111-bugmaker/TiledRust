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
