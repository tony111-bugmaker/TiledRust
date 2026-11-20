"""Dock 面板。"""
from .layers_dock import LayersDock
from .tilesets_dock import TilesetsDock
from .tileset_settings_dock import TilesetPropsDock, TilesetTilePropsDock
from .properties_dock import PropertiesDock
from .triggers_dock import TriggersDock  # 兼容旧代码；主窗口已不再装配此面板
from .trigger_graph_dock import TriggerGraphDock
from .object_toolbar import ObjectToolBar
from .probe_window import ProbeWindow
from .panel_settings_dialog import PanelSettingsDialog
from .new_map_dialog import NewMapDialog
from .resize_map_dialog import ResizeMapDialog
from .dock_chrome import (
    DockTitleBar, apply_panel_theme, install_dock_border, install_title_bar,
    native_dock_icons,
)
from .dock_overlap import install_dock_tab_detach, overlap_panels

__all__ = [
    "LayersDock", "TilesetsDock", "TilesetPropsDock", "TilesetTilePropsDock",
    "PropertiesDock",
    "TriggersDock", "TriggerGraphDock", "ProbeWindow", "ObjectToolBar",
    "PanelSettingsDialog", "ResizeMapDialog", "NewMapDialog", "DockTitleBar", "install_title_bar", "install_dock_border", "apply_panel_theme",
    "native_dock_icons", "install_dock_tab_detach", "overlap_panels",
]
