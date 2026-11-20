"""面板清单与面板布局配置（设置 → 面板列表设置 / 导入面板 / 保存当前面板）。

这里的「面板」专指 QMainWindow 里的 QDockWidget。PANEL_SPECS 是面板清单的
唯一来源：主窗口装配、「面板列表设置」对话框、布局导入导出都基于它，
避免新增面板时漏改某一处。

布局文件是 UTF-8 JSON，包含：
  - panels  : 每个面板的 key / 标题 / 显隐 / 停靠区 / 浮动与几何（可读、可改）
  - qt_state: QMainWindow.saveState() 的 base64（保存标签页合并、精确尺寸等
              Qt 内部状态；restoreState 要求每个 dock 有稳定 objectName）
导入时先恢复 qt_state，再按 panels 逐项兜底；未知 key 忽略并回报。
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, Qt
from PySide6.QtWidgets import QDockWidget

LAYOUT_FORMAT = "rwmap.panel-layout/1"
LAYOUT_SUFFIX = ".json"


@dataclass(frozen=True)
class PanelSpec:
    key: str            # objectName / 布局文件里的稳定 id
    attr: str           # MainWindow 上的属性名
    title: str          # 界面显示名
    description: str    # 一句话功能说明
    area: str           # 默认停靠区：left / right / top / bottom
    visible: bool = True
    order: int = 0      # 同一区域内自上而下的顺序
    floating: bool = False   # True = 永不参与停靠，始终以独立窗口出现


# ---- 面板清单（整理结果；新增面板只需在此追加一行） ----
PANEL_SPECS: tuple[PanelSpec, ...] = (
    PanelSpec("panels.layers", "layers_dock", "图层",
              "倒序列出图层；可见性 / 透明度 / 当前层，增、删、复制、排序、合并、锁定",
              "right", True, 1),
    PanelSpec("panels.properties", "properties_dock", "属性",
              "地图统计、地图属性、当前图块属性（只读）",
              "right", True, 2),
    PanelSpec("panels.tilesets", "tilesets_dock", "图块集",
              "整张图集原坐标显示；网格框选多格图章、单击设笔刷、拾取后自动定位",
              "right", True, 3),
    PanelSpec("panels.tileset_props", "tileset_props_dock", "图集设置",
              "当前图集的名称 / 瓦片尺寸 / 边距 / 间距 / 图像 / 透明色；"
              "图集任务右侧专属面板，地图任务隐藏",
              "right", True, 4),
    PanelSpec("panels.tileset_custom", "tileset_tile_props_dock", "瓦片属性",
              "每瓦片属性：点图集里的瓦片后按地形 / 单位 / 其他分页编辑该格；"
              "图集任务左侧专属面板，地图任务隐藏",
              "left", True, 1),
    PanelSpec("panels.minimap", "minimap_dock", "迷你地图",
              "整图缩小概览 + 当前视口框；点击 / 拖动可平移主视图，缩放与平移实时跟随",
              "right", True, 5),
    PanelSpec("panels.trigger_graph", "trigger_graph_dock", "触发器节点图",
              "只读节点图：Triggers 对象层里全部矩形/点/线等对象与激活连线；"
              "默认隐藏，在设置里打开为独立窗口，新增对象请在地图画布上操作",
              "right", False, 4, True),
)

# 非停靠面板（不可在「面板列表设置」里勾选，仅用于清单说明）
NON_DOCK_UI = (
    ("对象工具栏", "顶部第二行，画笔 / 盖章 / 钢笔 / 橡皮 / 形状等工具 + 自动映射开关"),
    ("状态栏", "底部，版本号 / 缩放比例 / 操作提示"),
    ("自测 / 探针", "独立对话框（视图 → 自测 / 探针），不是可停靠面板"),
)

_QT_AREAS = {
    "left": Qt.LeftDockWidgetArea,
    "right": Qt.RightDockWidgetArea,
    "top": Qt.TopDockWidgetArea,
    "bottom": Qt.BottomDockWidgetArea,
}
_AREA_LABELS = {
    "left": "左侧", "right": "右侧", "top": "上方", "bottom": "下方",
}
_AREA_BY_QT = {v: k for k, v in _QT_AREAS.items()}


def iter_panels(window):
    """产出 (spec, dock)，自动跳过窗口上不存在的属性。"""
    for spec in PANEL_SPECS:
        dock = getattr(window, spec.attr, None)
        if isinstance(dock, QDockWidget):
            yield spec, dock


def ensure_panel_object_names(window) -> int:
    """给每个 dock 设稳定 objectName —— restoreState() 的必要前提。"""
    count = 0
    for spec, dock in iter_panels(window):
        if dock.objectName() != spec.key:
            dock.setObjectName(spec.key)
        count += 1
    return count


def area_of(window, dock) -> str:
    if dock.isFloating():
        return "floating"
    return _AREA_BY_QT.get(window.dockWidgetArea(dock), "right")


def area_label(area: str) -> str:
    return {"floating": "浮动"}.get(area, _AREA_LABELS.get(area, area))


def apply_default_layout(window):
    """按 PANEL_SPECS 重建默认布局。

    普通面板按区域停靠；floating=True 的面板（触发器节点图）不参与停靠，
    直接变成独立浮动窗口，并按 spec.visible 决定是否初始隐藏。
    """
    groups: dict[str, list[tuple[int, QDockWidget]]] = {}
    floaters: list[tuple[PanelSpec, QDockWidget]] = []
    for spec, dock in iter_panels(window):
        dock.setFloating(False)
        window.removeDockWidget(dock)
        if spec.floating:
            floaters.append((spec, dock))
        else:
            groups.setdefault(spec.area, []).append((spec.order, dock))
    for area, items in groups.items():
        docks = [d for _order, d in sorted(items, key=lambda it: it[0])]
        if not docks:
            continue
        first = docks[0]
        window.addDockWidget(_QT_AREAS.get(area, Qt.RightDockWidgetArea), first)
        first.show()
        prev = first
        for dock in docks[1:]:
            window.splitDockWidget(prev, dock, Qt.Vertical)
            dock.show()
            prev = dock
    for spec, dock in floaters:
        # 先挂进主窗口再 setFloating，Qt 才会为它创建独立的工具窗口
        window.addDockWidget(_QT_AREAS.get(spec.area, Qt.RightDockWidgetArea), dock)
        dock.setFloating(True)
        dock.setVisible(spec.visible)
    if hasattr(window, "_apply_default_layout"):
        window._apply_default_layout()


def panel_inventory(window=None) -> list[dict]:
    """当前面板状态清单，供对话框、文档与测试使用。"""
    rows = []
    for spec in PANEL_SPECS:
        row = {
            "key": spec.key, "title": spec.title,
            "description": spec.description,
            "default_area": _AREA_LABELS.get(spec.area, spec.area),
            "default_visible": spec.visible,
            "default_floating": spec.floating,
            "area": _AREA_LABELS.get(spec.area, spec.area),
            "visible": spec.visible,
            "floating": spec.floating,
        }
        if window is not None:
            dock = getattr(window, spec.attr, None)
            if isinstance(dock, QDockWidget):
                row["area"] = area_label(area_of(window, dock))
                row["visible"] = not dock.isHidden()
                row["floating"] = bool(dock.isFloating())
        rows.append(row)
    return rows


def describe_panels(window=None) -> str:
    """Markdown 表格形式的面板清单（README / 复制清单用）。"""
    lines = [
        "| 面板 | 默认停靠 | 默认显示 | 功能 |",
        "| --- | --- | --- | --- |",
    ]
    for row in panel_inventory(window):
        lines.append(
            f"| {row['title']} | {row['default_area']} | "
            f"{'是' if row['default_visible'] else '否'} | {row['description']} |"
        )
    return "\n".join(lines)


def capture_layout(window) -> dict:
    """采集当前面板布局（纯数据，不写文件）。"""
    panels = []
    for spec, dock in iter_panels(window):
        rect = dock.geometry()
        panels.append({
            "key": spec.key,
            "title": spec.title,
            "visible": not dock.isHidden(),
            "area": area_of(window, dock),
            "floating": bool(dock.isFloating()),
            "geometry": [rect.x(), rect.y(), rect.width(), rect.height()],
        })
    return {
        "format": LAYOUT_FORMAT,
        "panels": panels,
        "qt_state": base64.b64encode(bytes(window.saveState())).decode("ascii"),
    }


def apply_layout(window, data: dict) -> dict:
    """把布局数据应用到窗口，返回 {restored, unknown, warnings}。"""
    if not isinstance(data, dict):
        raise ValueError("面板布局文件内容不是对象")
    fmt = data.get("format")
    if fmt and fmt != LAYOUT_FORMAT:
        raise ValueError(f"不支持的面板布局格式：{fmt}")
    if "panels" not in data and "qt_state" not in data:
        raise ValueError("面板布局文件缺少 panels / qt_state")

    warnings: list[str] = []
    known = {spec.key: (spec, getattr(window, spec.attr, None))
             for spec in PANEL_SPECS}
    state = data.get("qt_state")
    if state:
        try:
            ok = window.restoreState(QByteArray(base64.b64decode(state)))
            if not ok:
                warnings.append("Qt 未能还原停靠状态，已按面板逐项兜底")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Qt 状态损坏，已跳过：{exc}")
    else:
        warnings.append("文件没有 Qt 状态，仅按面板显隐恢复")

    restored = 0
    unknown: list[str] = []
    for item in data.get("panels", []):
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        entry = known.get(key)
        if entry is None:
            if key:
                unknown.append(str(key))
            continue
        _spec, dock = entry
        if not isinstance(dock, QDockWidget):
            continue
        # floating 类面板永远是独立窗口，不受布局文件里的停靠设置影响
        floating = bool(item.get("floating", _spec.floating)) or _spec.floating
        area = item.get("area", _spec.area)
        if floating:
            dock.setFloating(True)
            geom = item.get("geometry")
            if isinstance(geom, (list, tuple)) and len(geom) == 4:
                try:
                    dock.setGeometry(QRect(*[int(v) for v in geom]))
                except (TypeError, ValueError):
                    pass
        else:
            dock.setFloating(False)
            qt_area = _QT_AREAS.get(area)
            if qt_area is not None and window.dockWidgetArea(dock) != qt_area:
                window.addDockWidget(qt_area, dock)
        dock.setVisible(bool(item.get("visible", _spec.visible)))
        restored += 1

    # 文件里没列出的面板保持原样，不强行改动
    return {"restored": restored, "unknown": unknown, "warnings": warnings}


def save_layout(window, path) -> Path:
    target = Path(path)
    if target.suffix.lower() != LAYOUT_SUFFIX:
        target = target.with_name(target.name + LAYOUT_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(capture_layout(window), ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return target


def load_layout(window, path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return apply_layout(window, data)
