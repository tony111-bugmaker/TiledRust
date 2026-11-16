"""属性面板：文档信息 / 当前图块属性（只读）。"""
from __future__ import annotations

from PySide6.QtWidgets import QDockWidget, QTreeWidget, QTreeWidgetItem

from .dock_chrome import apply_panel_theme, install_title_bar


class PropertiesDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("属性", parent)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["键", "值"])
        self.setWidget(self.tree)
        apply_panel_theme(self.tree)
        install_title_bar(self)
        self.doc = None

    def set_document(self, doc):
        self.doc = doc
        self.tree.clear()
        if doc is None:
            return
        map_node = QTreeWidgetItem(self.tree, ["地图", ""])
        for key, value in doc.stats().items():
            if isinstance(value, dict):
                QTreeWidgetItem(map_node, [key, ", ".join(value.keys())])
            else:
                QTreeWidgetItem(map_node, [key, str(value)])
        props_node = QTreeWidgetItem(self.tree, ["地图属性", ""])
        for key, value in doc.properties.items():
            QTreeWidgetItem(props_node, [key, value])
        self.tree.expandAll()

    def set_tile(self, ts, local_id, props):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.text(0) == "当前图块":
                self.tree.takeTopLevelItem(i)
                break
        node = QTreeWidgetItem(self.tree, ["当前图块", f"gid={ts.firstgid + local_id}"])
        QTreeWidgetItem(node, ["tileset", ts.name or ts.source or ""])
        QTreeWidgetItem(node, ["local_id", str(local_id)])
        for key, value in props.items():
            QTreeWidgetItem(node, [key, value])
        node.setExpanded(True)
