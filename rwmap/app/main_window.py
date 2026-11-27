"""主窗口：装配菜单、工具栏、画布、Dock、状态栏与编辑闭环。"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea, QApplication, QComboBox, QDialog, QFileDialog,
    QInputDialog, QLabel, QProgressBar,
    QHBoxLayout, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy,
    QStackedWidget, QTabBar, QToolBar, QVBoxLayout, QWidget,
)

from ..app.app_state import AppState
from ..app.local_service import LocalControlService
from ..app.local_service_actions import LocalServiceActionsMixin
from ..app.map_loader import MapLoadController, MapLoadSignals
from ..app.panel_layout import (
    PANEL_SPECS, apply_default_layout, ensure_panel_object_names, iter_panels,
    load_layout, save_layout,
)
from ..commands.draw_command import DrawCommand
from ..commands.object_command import ObjectCommand, restore, snapshot
from ..document.gid import GID_MASK, split_gid
from ..document.layer import ObjectLayer, TileLayer
from ..document.map_object import SHAPE_LABELS, MapObject, object_uid
from ..document.tileset_document import TilesetDocument
from ..io.template_reader import read_template_object
from ..widgets.copy_group_dialog import ask_copy_group_name
from ..rw.automapping import AutoMapper, normalize_marker_gid
from ..rw.exporter import export_map
from ..rw.rwmod_pack import pack_rwmod
from ..rw.selection import rect_to_cells as cells_from_rect
from ..rw.thumbnail import make_thumbnail
from ..rw.resize import shift_cells, shift_objects
from ..theme import ASH, INK, TEXT, apply_app_theme
from ..tools.pen_tool import Anchor, PenPath
from ..tools.picker_tool import pick_gid
from ..utils.bezier import (
    nearest_on_curve, rasterize_polyline_to_cells, split_segment,
    tangent_handle,
)
from ..utils.paths import as_path
from ..view import MapScene, MapView
from ..widgets import (
    LayersDock, ObjectToolBar, PanelSettingsDialog, ProbeWindow,
    PropertiesDock, TilesetPropsDock, TilesetTilePropsDock, TilesetsDock,
    TriggerGraphDock, install_dock_tab_detach,
)
from ..widgets.minimap import MiniMapDock
from ..widgets.new_tileset_dialog import NewTilesetDialog
from ..widgets.object_toolbar import canonical_tool_id, make_select_cursor
from ..widgets.resize_map_dialog import ResizeMapDialog
from ..widgets.tileset_editor import TilesetEditorWidget


# 拖 8 个缩放箭头时对应的鼠标形状（斜角 / 上下 / 左右双向箭头）。
_RESIZE_CURSORS = {
    "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
    "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
    "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
    "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
}

# 顶部文档标签栏样式（双色暗色主题，对齐图块集标签）。
# 界面区分色只用 INK（近黑）+ ASH（偏黑灰）两色，文字用可读浅色。
# 2026-11-19 起：选中标签用深色 INK，未选中用浅色 ASH（与之前相反）。
_DOC_TAB_STYLE = f"""
QTabBar {{ background: {ASH}; border-bottom: 1px solid {INK}; }}
QTabBar::tab {{
    background: {ASH};
    border: 1px solid {INK};
    border-bottom: none;
    padding: 3px 12px 4px 12px;
    margin-right: 1px;
    color: {TEXT};
    font-size: 11px;
}}
QTabBar::tab:selected {{ background: {INK}; color: {TEXT}; }}
QTabBar::tab:hover:!selected {{ background: rgba(36, 36, 36, 160); }}
QTabBar::close-button {{ subcontrol-position: right; }}
QTabBar::scroller {{ width: 16px; }}
"""


class MainWindow(LocalServiceActionsMixin, QMainWindow):
    def __init__(self, auto_open: bool = True):
        super().__init__()
        # 双色暗色主题：菜单栏 / 状态栏 / 分隔条等原生部件不吃各面板自己的
        # 样式表，必须挂在 QApplication 上统一接管（放在这里是为了让
        # 测试和离屏截图也能拿到同样的效果）。
        app = QApplication.instance()
        if app is not None and not getattr(app, "_rwmap_themed", False):
            apply_app_theme(app)
            app._rwmap_themed = True
        self.setWindowTitle("铁锈战争专用地图编辑器")
        self.resize(1400, 900)
        self.state = AppState()                 # 当前激活文档的状态
        # 多文档：每个打开的地图一个 AppState（独立撤销栈 / 当前层 / 笔刷）。
        self._contexts = [self.state]
        self._active_index = 0
        self._doc_edited_conn = None
        self._displayed_ctx_id = None
        self._own_file = False
        # 异步地图加载：信号桥必须在 GUI 线程构造（此处即 GUI 线程）。
        self._load_signals = MapLoadSignals()
        self._load_controller = MapLoadController(self._load_signals)
        self._load_pending_token = 0      # >0 表示有一次加载在途
        self._load_pending_path = None
        self._load_ctx = None             # 本次加载对应的 AppState（发起时创建）
        self._zoom = 1.0
        self.probe_window = None
        self._automapper = None
        self._automap_while_drawing = False
        self._automap_macro = False
        self._automap_total = 0
        # 开启自动映射时临时显示 set 层；关闭时恢复原来的显隐与透明度。
        self._automap_set_saved = None   # (id(doc), visible, opacity, current_layer)
        self._hover_cell = (-1, -1)
        # 右键拖动抓取虚影缓存：(region, id(layer), cells_2d)。
        self._capture_cache = None
        self._sel_move_drag = None      # 拖动整组对象的状态（替代原单对象 _object_drag）
        self._sel_resize_drag = None    # 拖 8 个黄色箭头缩放选中对象的状态
        self._hover_resize_cursor = None
        self._object_tool = "brush"
        self._object_layer = None
        self._object_pending = None
        self._group_counter = 0          # 组合 id 生成器（右键「组合」用）
        # 标尺：多段测量状态，切工具不清，仅标尺工具右键清空
        self._ruler_segments: list[tuple[int, int, int, int]] = []
        self._ruler_pending: tuple[int, int] | None = None

        # ---- 钢笔 / 贝塞尔矢量曲线（独立于 _object_pending，避免与
        #      多边形 pending / 节点拖动的状态机互相误伤） ----
        self._pen_path = None            # PenPath | None
        self._pen_drag = None            # ("anchor"|"in"|"out", index) | None
        self._pen_rpress = None          # 右键序列信息
        self._pen_finish_on_release = False   # 左键确实点在空白处才允许完成

        # ---- 瓦片框选 / 复制 / 粘贴状态 ----
        self._sel_rect = None
        self._tile_selection = None
        self._tile_clipboard = None
        # ---- 对象复制粘贴：副本吸附鼠标，左键放置 ----
        self._paste_clones: list = []   # 模板对象（坐标已减掉锚点）
        self._paste_meta: dict = {}     # is_group / group_name / layers
        self._paste_anchor = (0.0, 0.0)
        self._paste_right_xy = (0.0, 0.0)
        self._sel_dragging = False
        self._sel_moving = False
        self._sel_start = (0.0, 0.0)
        # 拖动唯一的位移基准：始终是「按下点」，不随 mouseMove 更新。
        self._sel_move_start = (0.0, 0.0)
        # 当前在图层面板里选中的图层（任意类型）；合并后的「选择 / 框选」
        # 据此判断在空白处拖出的是瓦片选区还是对象框选。
        self._active_layer = None
        # 对象层框选状态：拖动中的蓝色选框 + 是否处于对象框选模式。
        self._obj_marquee = False
        act_copy = QAction("复制选区", self)
        act_copy.setShortcut("Ctrl+C")
        act_copy.triggered.connect(self._copy_selection)
        self.addAction(act_copy)
        act_paste = QAction("粘贴选区", self)
        act_paste.setShortcut("Ctrl+V")
        act_paste.triggered.connect(self._paste_selection)
        self.addAction(act_paste)

        self.scene = MapScene(self)
        self.view = MapView(self.scene, self)
        self.view.setContextMenuPolicy(Qt.NoContextMenu)  # 右键交给抓取/拾取，不弹系统菜单
        # 鼠标事件实际落在 viewport 子控件上：只关 view 的菜单，viewport 仍是
        # DefaultContextMenu，右键拖动结束会弹一个空白小菜单（一闪而过）。
        self.view.viewport().setContextMenuPolicy(Qt.NoContextMenu)

        # ---- 多文档标签栏（Tiled 风格：顶部标签 + 右侧「+」开新地图） ----
        self.doc_tabs = QTabBar()
        self.doc_tabs.setTabsClosable(True)
        self.doc_tabs.setMovable(False)
        self.doc_tabs.setDrawBase(False)
        self.doc_tabs.setExpanding(False)
        self.doc_tabs.setElideMode(Qt.ElideMiddle)
        self.doc_tabs.setStyleSheet(_DOC_TAB_STYLE)
        self.doc_tabs.tabCloseRequested.connect(self._close_tab)
        self.doc_tabs.currentChanged.connect(self._on_tab_changed)
        self._tab_plus = QPushButton(" + ")
        self._tab_plus.setFixedSize(24, 22)
        self._tab_plus.setToolTip("打开地图（新标签）")
        self._tab_plus.clicked.connect(self._open_dialog)

        # 文档标签栏做成独立、不可拖动 / 浮动的工具栏行，固定在菜单栏正下方；
        # 这样它既不会在切图集标签时随页面一起隐藏，位置也锁死（不可移动、
        # 不可浮动、只能待在顶部）。
        self.tab_toolbar = QToolBar("文档标签栏")
        self.tab_toolbar.setObjectName("DocTabBar")
        self.tab_toolbar.setMovable(False)
        self.tab_toolbar.setFloatable(False)
        self.tab_toolbar.setAllowedAreas(Qt.TopToolBarArea)
        self.tab_toolbar.setStyleSheet(
            f"QToolBar{{background:{INK};border:0;border-bottom:1px solid {ASH};"
            "spacing:0;padding:0;margin:0;}"
            "QToolBar::handle{width:0;}"
            "QToolBar::separator{width:0;}")
        self.doc_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.tab_toolbar.addWidget(self.doc_tabs)
        self.tab_toolbar.addWidget(self._tab_plus)
        self.addToolBar(Qt.TopToolBarArea, self.tab_toolbar)

        # 顶层中央容器 = 页面堆栈（标签栏已移到菜单栏正下方，不再塞进中央）。
        root_central = QWidget()
        root_layout = QVBoxLayout(root_central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        map_central = QWidget()
        map_layout = QVBoxLayout(map_central)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.setSpacing(0)
        map_layout.addWidget(self.view, 1)

        # 多文档：地图页（页 0）与图集编辑器页（页 1）共用中央区，按文档类型切换。
        self._tileset_editor = TilesetEditorWidget(self)
        # 图集画布滚轮缩放时同步状态栏缩放比例
        self._tileset_editor.atlas.zoomChanged.connect(self._on_zoom)
        self._central_stack = QStackedWidget()
        self._central_stack.addWidget(map_central)              # 页 0：地图
        self._central_stack.addWidget(self._tileset_editor)     # 页 1：图集
        self._map_page = 0
        self._tileset_page = 1
        self._map_central = map_central
        root_layout.addWidget(self._central_stack, 1)
        self.setCentralWidget(root_central)

        self.layers_dock = LayersDock(self)
        self.tilesets_dock = TilesetsDock(self)
        self.properties_dock = PropertiesDock(self)
        # 旧的「Triggers / 对象」树已移除：触发器统一在只读节点图里查看，
        # 而节点图默认隐藏、由「设置 → 触发器节点图」以独立窗口打开。
        self.trigger_graph_dock = TriggerGraphDock(self)
        self.minimap_dock = MiniMapDock(self)
        # 图集设置：常驻停靠面板（仿 RPG Maker 右侧属性面板），切图集标签即同步，
        # 不再以弹窗形式出现。注册进 panel_layout.PANEL_SPECS 后才参与默认布局。
        # 图集设置 / 瓦片属性：拆成两块独立停靠面板（右 / 左），
        # 只在「图集任务」里出现（地图任务隐藏）。注册进 panel_layout.PANEL_SPECS。
        self.tileset_props_dock = TilesetPropsDock(self)
        self.tileset_tile_props_dock = TilesetTilePropsDock(self)
        # 图集里点选的瓦片转发给左侧「瓦片属性」面板；只连一次避免重复连接。
        self._tileset_editor.tileClicked.connect(
            self.tileset_tile_props_dock.select_tile)
        # 瓦片属性编辑器的改动标脏图集任务（Ctrl+Z 走它的撤销栈）。
        self.tileset_tile_props_dock.changed.connect(self._on_tileset_edited)
        # 迷你地图编辑后防抖重渲染（绑定到激活文档的撤销栈 indexChanged）。
        self._minimap_timer = QTimer(self)
        self._minimap_timer.setSingleShot(True)
        self._minimap_timer.setInterval(300)
        self._minimap_timer.timeout.connect(self._minimap_refresh)
        # 默认布局（对齐 Tiled 习惯）：
        #   左列 = 单位；右列 = 图层 / 属性 / 图块集
        #   触发器节点图 = 不参与停靠的浮动窗口（初始隐藏）
        # 面板清单、默认顺序与 objectName 统一由 panel_layout.PANEL_SPECS 驱动，
        # 用 splitDockWidget 明确上下顺序，避免 Qt 把同侧面板折叠成标签页。
        ensure_panel_object_names(self)      # restoreState() 需要稳定 objectName
        apply_default_layout(self)
        # 面板重合后允许把标签拖出来：浮出面板左上角对齐鼠标；不限制重合数量。
        install_dock_tab_detach(self)
        QTimer.singleShot(0, self._apply_default_layout)

        self._build_actions()
        self._build_toolbar()
        self._build_status()
        self._connect_signals()
        # 本地 AI 控制服务：信号桥必须在 GUI 线程构造（此处即 GUI 线程）。
        # 请求用 QueuedConnection 投递回本窗口的 _on_local_request 执行，
        # 保证所有 doc/scene 操作都在 GUI 线程、且进撤销栈。
        self._local_service = LocalControlService(self)
        self._local_service.request_received.connect(
            self._on_local_request, Qt.ConnectionType.QueuedConnection)
        if self._local_service.config.get("enabled", True):
            # 延迟到事件循环起来后再监听，避免构造期阻塞；端口占用会写 last_error。
            QTimer.singleShot(0, self._start_local_service)
        # 统一收口「空白右键菜单」：所有滚动类控件的 viewport 默认策略是
        # DefaultContextMenu，右键拖动结束会弹一个空白小菜单（一闪而过）。
        # 这里在装配完成后一次性关掉，新增面板/视图无需各自记得处理。
        self._suppress_empty_context_menus()
        if auto_open:
            QTimer.singleShot(120, self._open_sample)

        # 顶层窗口自身也必须关掉默认右键菜单：右键释放时 Qt 派发的
        # QContextMenuEvent 会沿 viewport→view→…→顶层一路冒泡，即便各层视图
        # 都是 NoContextMenu，只要顶层还是 DefaultContextMenu，Qt 就会在最外层
        # 弹一个空的原生菜单（用户看到的「一闪而过的空框」，位置固定、与鼠标无关）。
        self.setContextMenuPolicy(Qt.NoContextMenu)

    def _suppress_empty_context_menus(self):
        """关掉所有「没有真实右键菜单」的 viewport 的原生空菜单。

        背景：鼠标事件实际落在 QAbstractScrollArea 的 viewport 子控件上；
        即使 view 自己设了 NoContextMenu，viewport 仍是 DefaultContextMenu，
        右键释放时 Qt 会派发一个没有任何内容的空菜单（用户看到的「小窗口」）。

        规则：
          * 控件自身 policy 已是 No / Custom / Actions → 有明确语义，不动；
          * 控件（或其承载的 widget）重写了 contextMenuEvent → 有真实菜单，不动；
          * 有 QAction 的控件（ActionsContextMenu 语义）→ 不动；
          * 其余 DefaultContextMenu 的 viewport → 设为 NoContextMenu。

        !! 以后新增视图 / 滚动控件请沿用本约定：要弹菜单就显式设 Custom 或
        重写 contextMenuEvent；不需要菜单就让它走这里自动收口，不要留默认的
        DefaultContextMenu。
        """
        from PySide6.QtWidgets import QAbstractItemView  # 局部导入，避免顶部依赖膨胀

        def has_real_menu(widget) -> bool:
            pol = widget.contextMenuPolicy()
            if pol in (Qt.NoContextMenu, Qt.CustomContextMenu,
                       Qt.ActionsContextMenu):
                return True
            # 重写了 contextMenuEvent 说明有自定义菜单逻辑（如图集面板的
            # TilePalette「设置图集」）；QWidget 基类的实现不算。
            try:
                if type(widget).contextMenuEvent is not QWidget.contextMenuEvent:
                    # QGraphicsView / QAbstractScrollArea 自身也重写了基类
                    # contextMenuEvent 做事件转发，但它们未必有真实菜单；
                    # 这类由下面的 viewport 分支单独判断。
                    base = getattr(widget, "viewport", None)
                    if base is None:  # 普通 QWidget：重写即视为真实菜单
                        return True
            except Exception:            # noqa: BLE001
                pass
            if isinstance(widget, QAbstractItemView) and widget.actions():
                return True
            return False

        seen = set()
        for area in self.findChildren(QAbstractScrollArea):
            vp = area.viewport()
            if vp is None or id(vp) in seen:
                continue
            seen.add(id(vp))
            # 宿主有真实右键菜单 → 必须保留其 viewport 的事件派发
            if has_real_menu(area):
                continue
            # 承载的 widget 重写了 contextMenuEvent（如 QScrollArea 内的
            # TilePalette）→ 也保留
            content = area.widget() if hasattr(area, "widget") else None
            if content is not None:
                try:
                    if (type(content).contextMenuEvent is not
                            QWidget.contextMenuEvent):
                        continue
                except Exception:        # noqa: BLE001
                    pass
            vp.setContextMenuPolicy(Qt.NoContextMenu)

    # ---------------- 装配 ----------------
    def _build_actions(self):
        self.act_open = QAction("打开 .tmx", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self._open_dialog)

        self.act_sample = QAction("打开示例地图", self)
        self.act_sample.triggered.connect(self._open_sample)

        self.act_save = QAction("保存", self)
        self.act_save.setShortcut(QKeySequence.Save)
        self.act_save.triggered.connect(self._save)

        self.act_save_as = QAction("另存为…", self)
        self.act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_as.triggered.connect(self._save_as)

        self.act_thumb = QAction("生成缩略图", self)
        self.act_thumb.triggered.connect(self._make_thumbnail)

        self.act_pack = QAction("打包 .rwmod", self)
        self.act_pack.triggered.connect(self._pack_rwmod)

        # 撤销 / 重做必须跟着「当前激活文档」的撤销栈走：每个标签页都有自己的
        # AppState + QUndoStack，绑死在初始栈上会让新标签的 Ctrl+Z 改到别的地图。
        # 这里用普通 QAction 转调 self.state.undo_stack，并随栈信号刷新文案与可用态。
        self.act_undo = QAction("撤销", self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.triggered.connect(self._undo)
        self.act_redo = QAction("重做", self)
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.act_redo.triggered.connect(self._redo)
        self._bind_undo_actions()

        self.act_brush = QAction("画笔 (B)", self)
        self.act_brush.setShortcut(QKeySequence("B"))
        self.act_brush.triggered.connect(lambda: self._set_tool("brush"))
        self.act_eraser = QAction("橡皮 (E)", self)
        self.act_eraser.setShortcut(QKeySequence("E"))
        self.act_eraser.triggered.connect(lambda: self._set_tool("eraser"))
        # 吸管不再是可切换工具：I 键按下即取光标下的瓦片，Alt+左键同理。
        # 只把 action 挂到窗口上注册快捷键，不放进菜单 / 工具栏。
        self.act_picker = QAction("临时吸管 (I / Alt+左键)", self)
        self.act_picker.setShortcut(QKeySequence("I"))
        self.act_picker.setToolTip("取光标下的图块作为笔刷，不切换当前工具")
        self.act_picker.triggered.connect(self._pick_at_cursor)
        self.addAction(self.act_picker)

        self.act_delete_object = QAction("删除选中对象", self)
        self.act_delete_object.setShortcut(QKeySequence("Delete"))
        self.act_delete_object.triggered.connect(self._delete_selected_object)
        self.addAction(self.act_delete_object)

        # 多边形 / 折线逐点绘制的结束键（双击画布同样可结束）。
        self.act_finish_poly = QAction("完成多边形 / 折线", self)
        self.act_finish_poly.setShortcut(QKeySequence("Ctrl+Return"))
        self.act_finish_poly.triggered.connect(self._finish_poly)
        self.addAction(self.act_finish_poly)

        # ---- 视图：显示开关（全部是纯视图状态，不写文档 / 不进撤销栈） ----
        self.act_grid = QAction("显示网格(&G)", self)
        self.act_grid.setCheckable(True)
        self.act_grid.setChecked(True)
        self.act_grid.setShortcut(QKeySequence("Ctrl+G"))
        self.act_grid.triggered.connect(self._toggle_grid)

        self.act_highlight_current = QAction("当前层高亮(&H)", self)
        self.act_highlight_current.setCheckable(True)
        self.act_highlight_current.setShortcut(QKeySequence("H"))
        self.act_highlight_current.setToolTip(
            "把非当前层的画面调暗，便于集中编辑单层；不改变 TMX 里的图层透明度")
        self.act_highlight_current.toggled.connect(self._on_view_layer_option)

        self.act_only_current = QAction("只显示当前层", self)
        self.act_only_current.setCheckable(True)
        self.act_only_current.setToolTip(
            "只显示当前选中的图层；只改画面，不写入 TMX 的 visible 字段")
        self.act_only_current.toggled.connect(self._on_view_layer_option)

        self.act_show_object_names = QAction("显示对象名称", self)
        self.act_show_object_names.setCheckable(True)
        self.act_show_object_names.setChecked(True)
        self.act_show_object_names.setToolTip(
            "在画布的对象框上方显示名称 / 类型标签")
        self.act_show_object_names.toggled.connect(self._on_view_layer_option)

        # ---- 视图：缩放（按当前标签页分发，图集页不会点到隐藏的地图视图） ----
        self.act_zoom_in = QAction("放大", self)
        self.act_zoom_in.setShortcuts(
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        self.act_zoom_in.triggered.connect(lambda: self._view_zoom("in"))
        self.act_zoom_out = QAction("缩小", self)
        self.act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        self.act_zoom_out.triggered.connect(lambda: self._view_zoom("out"))
        self.act_zoom_reset = QAction("正常大小 (1:1)", self)
        self.act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        self.act_zoom_reset.triggered.connect(lambda: self._view_zoom("reset"))
        self.act_zoom_fit = QAction("自动适应窗口", self)
        self.act_zoom_fit.setShortcut(QKeySequence("Ctrl+/"))
        self.act_zoom_fit.triggered.connect(lambda: self._view_zoom("fit"))

        self.act_fullscreen = QAction("全屏(&F)", self)
        self.act_fullscreen.setCheckable(True)
        self.act_fullscreen.setShortcut(QKeySequence("F11"))
        self.act_fullscreen.toggled.connect(self._toggle_fullscreen)

        self.act_probe = QAction("自测 / 探针", self)
        self.act_probe.triggered.connect(self._show_probe)

        self.act_load_rules = QAction("添加 Automapping 规则文件…", self)
        self.act_load_rules.triggered.connect(self._pick_rules_file)
        # Tiled「地图」菜单里 RW 用得上的通用功能（对应截图逐项补齐）。
        self.act_add_external_tileset = QAction("添加外部图块…", self)
        self.act_add_external_tileset.triggered.connect(self._add_external_tileset)
        self.act_add_rules_tileset = QAction("添加自动映射规则图集…", self)
        self.act_add_rules_tileset.triggered.connect(self._add_rules_tileset)
        self.act_autocrop = QAction("自动裁剪", self)
        self.act_autocrop.triggered.connect(self._autocrop)
        self.act_goto_tile = QAction("转到图块…", self)
        self.act_goto_tile.setShortcut(QKeySequence("Ctrl+Shift+G"))
        self.act_goto_tile.triggered.connect(self._goto_tile)
        self.act_prev_tileset = QAction("选择上一个图块集", self)
        self.act_prev_tileset.setShortcut(QKeySequence("["))
        self.act_prev_tileset.triggered.connect(lambda: self._switch_tileset(-1))
        self.act_next_tileset = QAction("选择下一个图块集", self)
        self.act_next_tileset.setShortcut(QKeySequence("]"))
        self.act_next_tileset.triggered.connect(lambda: self._switch_tileset(1))
        self.act_map_properties = QAction("地图属性…", self)
        self.act_map_properties.triggered.connect(self._edit_map_properties)
        self.act_automap_now = QAction("自动映射", self)
        self.act_automap_now.setShortcut(QKeySequence("Ctrl+M"))
        self.act_automap_now.triggered.connect(lambda: self._run_automap())
        self.act_automap_while = QAction("绘制时自动地图", self)
        self.act_automap_while.setCheckable(True)
        self.act_automap_while.toggled.connect(self._toggle_automap)
        self.act_automap_while.setToolTip(
            "开启后落笔固定写 set 层（画笔 / 填充 / 钢笔），"
            "任意基础地形瓦片自动归一到图集首格，"
            "Ground / Items 由官方 rules.txt 规则自动生成"
        )
        self.act_new_map = QAction("新建地图…", self)
        self.act_new_map.triggered.connect(self._new_map)
        self.act_new_tileset = QAction("新建瓦片图集…", self)
        self.act_new_tileset.setToolTip(
            "用一张图集大图生成铁锈瓦片图集（.tsx），在独立标签里逐格调属性")
        self.act_new_tileset.triggered.connect(self._new_tileset)
        # 这 4 个按钮不再变换整张地图，而是作用于「当前变换目标」：
        # 选中的插入图片 > 右键框选的图章内容；两者都无时按钮禁用（见
        # _refresh_transform_actions / _transform_target / _transform_content）。
        self.act_flip_h = QAction("水平翻转", self)
        self.act_flip_h.triggered.connect(lambda: self._transform_content("flip_h"))
        self.act_flip_v = QAction("垂直翻转", self)
        self.act_flip_v.triggered.connect(lambda: self._transform_content("flip_v"))
        self.act_rot_cw = QAction("顺时针旋转 90°", self)
        self.act_rot_cw.triggered.connect(lambda: self._transform_content("rot_cw"))
        self.act_rot_ccw = QAction("逆时针旋转 90°", self)
        self.act_rot_ccw.triggered.connect(lambda: self._transform_content("rot_ccw"))
        self.act_resize = QAction("调整地图尺寸…", self)
        self.act_resize.triggered.connect(self._resize_map)

        # ---- 设置：本地 AI 控制服务 ----
        self.act_local_service = QAction("本地 AI 控制服务", self)
        self.act_local_service.setCheckable(True)
        self.act_local_service.setToolTip(
            "仅监听 127.0.0.1:8765，需令牌鉴权。开启后 AI 可实时读写地图（可撤销）")
        self.act_local_service.triggered.connect(self._toggle_local_service)

        # ---- 设置：面板清单与布局导入 / 导出 ----
        self.act_panel_settings = QAction("面板列表设置", self)
        self.act_panel_settings.setToolTip("勾选显示 / 隐藏哪些面板，并查看面板清单")
        self.act_panel_settings.triggered.connect(self._open_panel_settings)
        self.act_import_panels = QAction("导入面板", self)
        self.act_import_panels.setToolTip("从 .json 布局文件恢复面板的显隐、停靠区与尺寸")
        self.act_import_panels.triggered.connect(self._import_panel_layout)
        self.act_save_panels = QAction("保存当前面板", self)
        self.act_save_panels.setToolTip("把当前面板的显隐、停靠区与尺寸保存为 .json 布局文件")
        self.act_save_panels.triggered.connect(self._save_panel_layout)

        # 触发器节点图：默认隐藏，勾选后以独立浮动窗口打开（只读）
        self.act_trigger_graph = QAction("触发器节点图", self)
        self.act_trigger_graph.setCheckable(True)
        self.act_trigger_graph.setChecked(False)
        self.act_trigger_graph.setToolTip(
            "以独立窗口打开 Triggers 对象层的只读节点图（矩形 / 点 / 线等全部对象）")
        self.act_trigger_graph.triggered.connect(self._toggle_trigger_graph)

        file_menu = self.menuBar().addMenu("文件")
        # Tiled 式「新建」置顶子菜单：点开先选新建地图 / 新建瓦片图集。
        new_menu = file_menu.addMenu("新建")
        new_menu.addAction(self.act_new_map)
        new_menu.addAction(self.act_new_tileset)
        for action in (self.act_open, self.act_sample,
                       self.act_save, self.act_save_as,
                       self.act_thumb, self.act_pack):
            file_menu.addAction(action)
        edit_menu = self.menuBar().addMenu("编辑")
        for action in (self.act_undo, self.act_redo, self.act_brush, self.act_eraser,
                       self.act_delete_object):
            edit_menu.addAction(action)
        view_menu = self.menuBar().addMenu("视图")
        view_menu.addMenu(self._build_views_toolbars_menu())
        self._view_menu = view_menu
        view_menu.addSeparator()
        view_menu.addAction(self.act_grid)
        view_menu.addAction(self.act_highlight_current)
        view_menu.addAction(self.act_only_current)
        view_menu.addAction(self.act_show_object_names)
        view_menu.addSeparator()
        view_menu.addAction(self.act_zoom_in)
        view_menu.addAction(self.act_zoom_out)
        view_menu.addAction(self.act_zoom_reset)
        view_menu.addAction(self.act_zoom_fit)
        view_menu.addSeparator()
        view_menu.addAction(self.act_fullscreen)
        view_menu.addSeparator()
        view_menu.addAction(self.act_probe)
        map_menu = self.menuBar().addMenu("地图")
        # Tiled「地图」菜单形态：外部图块 / 规则图集 → 尺寸类 → 自动映射类
        # → 定位 / 图块集切换 → 属性。绘制时自动地图只在此菜单出现。
        # 「新建地图 / 新建瓦片图集」已统一移到「文件 > 新建」置顶子菜单。
        map_menu.addAction(self.act_add_external_tileset)
        map_menu.addAction(self.act_add_rules_tileset)
        map_menu.addSeparator()
        map_menu.addAction(self.act_resize)
        map_menu.addAction(self.act_autocrop)
        # 翻转 / 旋转 4 项按用户要求移到对象工具栏最右端，地图菜单不再重复。
        map_menu.addSeparator()
        map_menu.addAction(self.act_load_rules)
        map_menu.addAction(self.act_automap_now)
        map_menu.addAction(self.act_automap_while)
        map_menu.addSeparator()
        map_menu.addAction(self.act_goto_tile)
        map_menu.addSeparator()
        map_menu.addAction(self.act_prev_tileset)
        map_menu.addAction(self.act_next_tileset)
        map_menu.addSeparator()
        map_menu.addAction(self.act_map_properties)
        # 「工具」顶级菜单和「单位图鉴」入口都按用户要求去掉。
        settings_menu = self.menuBar().addMenu("设置")
        settings_menu.addAction(self.act_local_service)
        settings_menu.addSeparator()
        settings_menu.addAction(self.act_panel_settings)
        settings_menu.addSeparator()
        settings_menu.addAction(self.act_import_panels)
        settings_menu.addAction(self.act_save_panels)
        settings_menu.addSeparator()
        settings_menu.addAction(self.act_trigger_graph)

    def _build_toolbar(self):
        # 原「主工具栏」在移除版本下拉后没有任何按钮，只剩一行空白占位，已整体
        # 删除。这里只保留对象工具栏，并让它独占标签栏下方一行。
        self.addToolBarBreak(Qt.TopToolBarArea)
        self.object_toolbar = ObjectToolBar(self)
        self.addToolBar(Qt.TopToolBarArea, self.object_toolbar)
        # 变换 4 按钮排在对象工具栏「瓦片画笔」文字之前（图标左、文字右），
        # 不再占用「地图」菜单。作用于选中图片 / 右键框选的图章，无目标禁用。
        # action 仍在 _build_actions 里统一创建。
        for action, tip in (
            (self.act_flip_h, "翻转选中的插入图片，或右键框选的图章内容（水平镜像）"),
            (self.act_flip_v, "翻转选中的插入图片，或右键框选的图章内容（垂直镜像）"),
            (self.act_rot_cw, "旋转选中的插入图片，或右键框选的图章内容（顺时针 90°）"),
            (self.act_rot_ccw, "旋转选中的插入图片，或右键框选的图章内容（逆时针 90°）"),
        ):
            action.setToolTip(tip)
        self.object_toolbar.add_transform_actions([
            (self.act_flip_h, "flip_h"),
            (self.act_flip_v, "flip_v"),
            (self.act_rot_cw, "rot_cw"),
            (self.act_rot_ccw, "rot_ccw"),
        ])
        # 标尺「分段」输入框排在工具栏最右（变换按钮之后）
        self.object_toolbar.add_ruler_controls()
        self.object_toolbar.segmentsChanged.connect(self._on_ruler_segments_changed)
        # 「绘制时自动地图」按用户要求只在「地图」菜单显示，不再放工具栏。
        # 对象工具栏此刻才存在，补进「视图 → 视图和工具栏」的勾选列表。
        self._append_toolbar_toggles()

    def _apply_default_layout(self):
        """默认面板尺寸：左列窄、右列窄，画布尽量宽；右列三层垂直堆叠。"""
        self.resizeDocks(
            [self.layers_dock, self.properties_dock, self.tilesets_dock],
            [300, 300, 230], Qt.Vertical)

    # ---------------- 视图菜单 ----------------
    def _build_views_toolbars_menu(self):
        """「视图 → 视图和工具栏」：按 PANEL_SPECS 逐项勾选面板显隐。

        勾选与 QDockWidget.visibilityChanged 双向同步：用户点面板自带的 ×，
        或切文档 / 恢复默认布局时面板被 show/hide，菜单里的勾都会跟着变。
        """
        menu = QMenu("视图和工具栏", self)
        self._panel_actions = {}
        for spec, dock in iter_panels(self):
            if spec.key == "panels.trigger_graph":
                # 浮动窗口有独立的尺寸 / 置前逻辑，直接复用「设置」里那个
                # action，两处菜单天然同步，也避免出现两个不一致的勾。
                act = self.act_trigger_graph
            else:
                act = QAction(spec.title, self)
                act.setCheckable(True)
                act.setChecked(not dock.isHidden())
                act.setToolTip(spec.description)
                act.toggled.connect(
                    lambda checked, d=dock: d.setVisible(bool(checked)))
            dock.visibilityChanged.connect(
                lambda visible, a=act: self._on_panel_visibility(a, visible))
            menu.addAction(act)
            self._panel_actions[spec.key] = act
        menu.addSeparator()
        self._vtb_toolbar_anchor = menu.addSeparator()
        act_reset = QAction("恢复默认布局", self)
        act_reset.setToolTip("把 8 个面板恢复到默认停靠区与默认显隐状态")
        act_reset.triggered.connect(self._reset_panel_layout)
        menu.addAction(act_reset)
        # 展开菜单时按 dock 真实状态校准一次。visibilityChanged 在窗口最小化、
        # 尚未 show、或布局批量重建时可能不触发，只靠信号会留下过期的勾。
        menu.aboutToShow.connect(self._sync_all_panel_actions)
        self._views_toolbars_menu = menu
        return menu

    def _sync_all_panel_actions(self):
        """把「视图和工具栏」里所有面板勾选校准成 dock 的真实显隐。"""
        for spec, dock in iter_panels(self):
            act = self._panel_actions.get(spec.key)
            if act is not None:
                self._on_panel_visibility(act, not dock.isHidden())

    def _append_toolbar_toggles(self):
        """工具栏在 _build_toolbar() 之后才存在，这里补进「视图和工具栏」。"""
        menu = self._views_toolbars_menu
        # 按 objectName 匹配（对象工具栏的标题是「对象工具」）；
        # 文档标签栏不列：它按用户要求锁定在顶部，不该提供隐藏入口。
        for name, label in (("ObjectToolBar", "对象工具栏"),):
            bar = self.findChild(QToolBar, name)
            if bar is None:
                continue
            act = bar.toggleViewAction()
            act.setText(label)
            act.setToolTip(f"显示 / 隐藏「{label}」")
            menu.insertAction(self._vtb_toolbar_anchor, act)

    def _on_panel_visibility(self, act, visible: bool):
        """面板被面板外的途径显示 / 隐藏时，同步菜单勾选（防回环）。"""
        if act.isChecked() == bool(visible):
            return
        act.blockSignals(True)
        act.setChecked(bool(visible))
        act.blockSignals(False)

    def _reset_panel_layout(self):
        apply_default_layout(self)
        self._sync_all_panel_actions()
        self.statusBar().showMessage("视图和工具栏：已恢复默认布局")

    def _on_view_layer_option(self, _checked=False):
        """三个纯视图显示开关统一重算；只改 QGraphicsItem，不写文档。"""
        self.scene.set_view_layer_options(
            highlight=self.act_highlight_current.isChecked(),
            only_current=self.act_only_current.isChecked(),
            show_names=self.act_show_object_names.isChecked())
        self.scene.view_current_layer = (
            self._active_layer or self.state.current_layer)
        # setVisible / setOpacity 由 Qt 标脏 item；只需重绘视口，
        # 绝不能调无 rect 的 scene.update()（README 明确禁止整图标脏）。
        self.view.viewport().update()

    def _view_zoom(self, kind: str):
        """缩放动作按当前标签页分发：地图页走 MapView，图集页走图集画布。

        不做分发的话，图集标签下点「放大」会作用在隐藏的地图视图上，
        用户看到的就是一个「点了没反应」的假按钮。
        """
        if self._central_stack.currentIndex() == self._tileset_page:
            atlas = self._tileset_editor.atlas
            z = float(getattr(atlas, "_zoom", 1.0))
            if kind == "in":
                atlas.set_zoom(z * 1.15)
            elif kind == "out":
                atlas.set_zoom(z / 1.15)
            elif kind == "reset":
                atlas.set_zoom(1.0)
            else:  # fit：按图集源图与滚动视口算一个能装下整张图的倍率
                scroll = getattr(atlas, "_pan_scroll", None)
                nw = float(getattr(atlas, "_native_w", 0) or 0)
                nh = float(getattr(atlas, "_native_h", 0) or 0)
                if scroll is not None and nw > 0 and nh > 0:
                    vp = scroll.viewport()
                    atlas.set_zoom(min(vp.width() / nw, vp.height() / nh))
                else:
                    atlas.set_zoom(1.0)
            return
        view = self.view
        if kind == "in":
            view.zoom_in()
        elif kind == "out":
            view.zoom_out()
        elif kind == "reset":
            view.zoom_reset()
        else:
            view.zoom_fit()

    def _toggle_fullscreen(self, checked: bool):
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()

    def changeEvent(self, event):
        """用户按 Esc / 系统退出全屏时，把 F11 的勾选同步回来。"""
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            act = getattr(self, "act_fullscreen", None)
            if act is not None:
                full = bool(self.windowState() & Qt.WindowFullScreen)
                if act.isChecked() != full:
                    act.blockSignals(True)
                    act.setChecked(full)
                    act.blockSignals(False)

    # ---------------- 设置：面板 ----------------
    def _open_panel_settings(self):
        dlg = PanelSettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.statusBar().showMessage(
                f"面板列表设置：当前显示 {dlg.applied_count} 个面板")
        else:
            self.statusBar().showMessage("面板列表设置：已取消，布局未改动")

    def _save_panel_layout(self):
        default = str(Path.cwd() / "面板布局.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存当前面板", default, "面板布局 (*.json)")
        if not path:
            return
        try:
            target = save_layout(self, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存面板失败", str(exc))
            return
        self.statusBar().showMessage(f"已保存当前面板布局：{target}")

    def _import_panel_layout(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入面板", str(Path.cwd()), "面板布局 (*.json)")
        if not path:
            return
        try:
            report = load_layout(self, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入面板失败", str(exc))
            return
        msg = f"已导入面板布局：{report['restored']} 个面板"
        if report["unknown"]:
            msg += f"，{len(report['unknown'])} 个未知面板已忽略"
        if report["warnings"]:
            msg += "；" + "；".join(report["warnings"])
        self.statusBar().showMessage(msg)

    # ---------------- 设置：触发器节点图（独立窗口） ----------------
    def _toggle_trigger_graph(self, checked: bool):
        dock = self.trigger_graph_dock
        if checked:
            if not dock.isFloating():
                dock.setFloating(True)
            if dock.width() < 700 or dock.height() < 480:
                dock.resize(1080, 700)
            dock.show()
            dock.raise_()
            dock.activateWindow()
            self.statusBar().showMessage(
                "已打开触发器节点图：只读视图，节点来自 Triggers 对象层；"
                "新增对象请在地图画布上操作")
        else:
            dock.hide()
            self.statusBar().showMessage("已关闭触发器节点图")

    def _on_graph_visibility(self, visible: bool):
        """用户直接点浮动窗口的 × 时，把设置菜单里的勾同步取消。"""
        act = getattr(self, "act_trigger_graph", None)
        if act is not None and act.isChecked() != visible:
            act.blockSignals(True)
            act.setChecked(bool(visible))
            act.blockSignals(False)

    def _focus_object_on_map(self, obj):
        """节点图双击 → 在地图画布上选中并居中对应对象。"""
        if obj is None:
            return
        self.scene.set_selected_object(obj)
        doc = self.state.doc
        tw = doc.tile_w if doc is not None else 20
        th = doc.tile_h if doc is not None else 20
        x, y, w, h = obj.bounds(tw, th)
        self.view.centerOn(QPointF(x + w / 2, y + h / 2))
        self.statusBar().showMessage(f"节点图 → 已定位到地图对象 {obj.label}")

    def _build_status(self):
        self.status_profile = QLabel(self.state.profile.version_id)
        self.status_zoom = QLabel("100%")
        # 地图加载进度条：常态隐藏，打开大地图时显示，避免窗口假死无反馈。
        self.load_progress = QProgressBar(self.statusBar())
        self.load_progress.setRange(0, 100)
        self.load_progress.setValue(0)
        self.load_progress.setFixedWidth(180)
        self.load_progress.setTextVisible(True)
        self.load_progress.setToolTip("地图加载进度")
        # 原生进度条在部分主题下是灰色矩形，这里统一成 Windows 蓝 + 圆角。
        # 边框用 palette(mid) 跟随明暗主题，填充固定 Windows 强调色。
        self.load_progress.setStyleSheet(
            "QProgressBar {"
            "  border: 1px solid palette(mid);"
            "  border-radius: 6px;"
            "  text-align: center;"
            "}"
            "QProgressBar::chunk {"
            "  background-color: #0078d4;"
            "  border-radius: 5px;"
            "}"
        )
        self.load_progress.hide()
        self.statusBar().addPermanentWidget(self.load_progress)
        self.statusBar().addPermanentWidget(self.status_profile)
        self.statusBar().addPermanentWidget(self.status_zoom)
        self.statusBar().showMessage("就绪")

    def _connect_signals(self):
        # 异步地图加载信号（worker emit → queued 回 GUI 线程）
        self._load_signals.progress.connect(self._on_load_progress)
        self._load_signals.succeeded.connect(self._on_load_succeeded)
        self._load_signals.failed.connect(self._on_load_failed)
        self.view.cellPressed.connect(self._on_press)
        self.view.cellMoved.connect(self._on_move)
        self.view.cellReleased.connect(self._on_release)
        self.view.hoverCell.connect(self._on_hover)
        self.view.pickRequested.connect(self._pick_gid)
        # 右键：拖动抓区域作图章 / 单击拾取单格
        self.view.regionCaptureRequested.connect(self._capture_region)
        self.view.rightPickRequested.connect(self._right_pick_gid)
        self.view.captureRectChanged.connect(self._on_capture_rect)
        self.view.objectPressed.connect(self._on_object_press)
        self.view.objectMoved.connect(self._on_object_move)
        self.view.objectReleased.connect(self._on_object_release)
        self.view.objectToolPressed.connect(self._on_object_tool_press)
        self.view.objectToolMoved.connect(self._on_object_tool_move)
        self.view.objectToolReleased.connect(self._on_object_tool_release)
        self.view.objectToolDoubleClicked.connect(self._on_object_tool_double_click)
        self.view.objectToolCancelled.connect(self._cancel_object_tool)
        self.view.objectHover.connect(self._on_object_hover)
        # 选中变化 → 刷新 4 个变换按钮的启用态
        self.scene.objectSelectionChanged.connect(
            lambda _objs: self._refresh_transform_actions())
        # 钢笔右键序列（其余对象工具收到后由 handler 首行直接忽略）
        self.view.objectToolRightPressed.connect(self._pen_right_press)
        self.view.objectToolRightMoved.connect(self._pen_right_move)
        self.view.objectToolRightReleased.connect(self._pen_right_release)
        self.view.objectToolRightPressed.connect(self._on_object_right_press)
        self.view.pasteMoveRequested.connect(self._on_paste_move)
        self.view.pastePlaceRequested.connect(self._on_paste_place)
        self.view.pasteCancelRequested.connect(self._cancel_object_paste)
        self.view.zoomChanged.connect(self._on_zoom)

        self.object_toolbar.toolChanged.connect(self._set_object_tool)
        self.object_toolbar.layerChanged.connect(self._on_object_layer_changed)

        self.layers_dock.layerSelected.connect(self._on_layer_selected)
        self.layers_dock.layerVisibilityChanged.connect(self._on_visibility)
        self.layers_dock.layersVisibilityChanged.connect(self._on_visibility_batch)
        self.layers_dock.layersLockChanged.connect(self._on_lock_batch)
        self.layers_dock.layerOpacityChanged.connect(self._on_opacity)
        self.layers_dock.layerRenameRequested.connect(self._rename_layer)
        self.layers_dock.addLayerRequested.connect(self._add_layer)
        self.layers_dock.deleteLayerRequested.connect(self._delete_layer)
        self.layers_dock.duplicateLayerRequested.connect(self._duplicate_layer)
        self.layers_dock.moveLayerRequested.connect(self._move_layer)
        self.layers_dock.lockLayerToggled.connect(self._toggle_layer_lock)
        self.tilesets_dock.gidSelected.connect(self._on_gid)
        self.tilesets_dock.tileSelected.connect(self.properties_dock.set_tile)
        self.tilesets_dock.stampSelected.connect(self._on_stamp)
        self.tilesets_dock.settingsRequested.connect(self._on_tileset_settings)
        self.tileset_props_dock.applied.connect(
            lambda _t: self._refresh_after_tileset_settings())
        self.trigger_graph_dock.objectSelected.connect(self._on_object_selected)
        self.trigger_graph_dock.objectsSelected.connect(self._on_objects_selected)
        self.trigger_graph_dock.objectActivateRequested.connect(self._focus_object_on_map)
        self.trigger_graph_dock.visibilityChanged.connect(self._on_graph_visibility)

    # ---------------- 文档 ----------------
    def _open_sample(self):
        sample = as_path(self.state.profile.sample_map)
        if sample.is_file():
            self._open_async(sample)
        else:
            self.statusBar().showMessage(f"示例地图不存在: {sample}")

    def _open_dialog_start_dir(self) -> str:
        """选一个真正能看到 .tmx 的目录作为「打开」对话框起点。

        1.14 的 ``mods/maps`` 只有 rules / terrain 等子目录，直接拿它当起点
        会让文件列表一片空白（其实地图都在 ``assets/maps/skirmish``）；
        1.15 的 ``mods/maps`` 本身就有很多 .tmx。这里优先用第一个「直接包含
        .tmx」的目录，两个都没有时仍回到 ``mods/maps``，不影响手动输入路径。
        """
        profile = self.state.profile
        candidates = [as_path(profile.mods_maps),
                      as_path(profile.sample_map).parent]
        seen: set[Path] = set()
        for base in candidates:
            if base in seen:
                continue
            seen.add(base)
            try:
                if base.is_dir() and any(
                        p.is_file() and p.suffix.lower() == ".tmx"
                        for p in base.iterdir()):
                    return str(base)
            except OSError:
                continue
        return str(candidates[0]) if candidates else ""

    def _open_dialog(self):
        start = self._open_dialog_start_dir()
        path, _ = QFileDialog.getOpenFileName(self, "打开 Tiled 地图", start, "TMX 地图 (*.tmx)")
        if path:
            self._open_async(as_path(path))

    def _open(self, path: Path):
        """同步打开地图（供测试与程序化调用；UI 入口走 _open_async）。"""
        ctx = self._new_or_reuse_context()
        try:
            ctx.open(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self._finish_open(ctx, path)

    def _finish_open(self, ctx: AppState, path: Path):
        """doc 已装入 ctx 后的公共收尾：attach + 状态栏。同步/异步路径共用。"""
        # 打开的文件就归当前编辑会话所有：用户按「保存」应直接写回这个文件，
        # 不能再弹「另存为」。只读版本由 profile.write_enabled 在 _save 里拦截，
        # 不会误覆盖只读/官方地图，所以这里无需用 _own_file 做二次保护。
        self._own_file = True
        self._attach_context(ctx)
        legacy_note = "（1.14 地图，已自动按 1.15 规则读取）" \
            if "1.14" in str(path) else ""
        self.statusBar().showMessage(
            f"已打开 {path.name}  {ctx.doc.width}x{ctx.doc.height}  "
            f"{len(ctx.doc.layers)} 层 / {len(ctx.doc.tilesets)} 图块集{legacy_note}"
        )

    def _open_async(self, path: Path):
        """异步打开地图：解析在后台线程进行，GUI 线程保持响应并显示进度。

        与 ``_open`` 的最终结果完全一致（同一套 ``_finish_open`` 收尾），
        只是把最耗时的 ``read_document`` 挪出了 GUI 线程。
        """
        if self._load_pending_token:
            # 已有加载在途：忽略重复请求，避免并发装载互相踩踏。
            self.statusBar().showMessage("正在打开上一张地图，请稍候…")
            return
        ctx = self._new_or_reuse_context()
        self._load_ctx = ctx
        self._load_pending_path = path
        self.load_progress.setValue(0)
        self.load_progress.show()
        self.statusBar().showMessage(f"正在打开 {path.name} …")
        token = self._load_controller.begin(path, ctx.profile)
        self._load_pending_token = token

    def _on_load_progress(self, percent: int, message: str):
        if not self._load_controller.is_current(self._load_pending_token):
            return
        self.load_progress.setValue(max(0, min(100, percent)))
        self.load_progress.setFormat(f"{message}  %p%")

    def _on_load_succeeded(self, token: int, doc):
        if not self._load_controller.is_current(token):
            return                       # 陈旧结果：已作废/被新加载取代
        self._load_controller.finish(token)
        path = self._load_pending_path
        ctx = self._load_ctx
        self._clear_load_state()
        ctx.open_document(doc)
        self._finish_open(ctx, path)

    def _on_load_failed(self, token: int, message: str):
        if not self._load_controller.is_current(token):
            return
        self._load_controller.finish(token)
        self._clear_load_state()
        QMessageBox.critical(self, "打开失败", message)
        self.statusBar().showMessage("打开失败")

    def _clear_load_state(self):
        self._load_pending_token = 0
        self._load_pending_path = None
        self._load_ctx = None
        self.load_progress.hide()
        self.load_progress.setFormat("")

    # ---------------- 本地 AI 控制服务 ----------------
    def _start_local_service(self):
        svc = self._local_service
        if svc.is_running():
            self.act_local_service.setChecked(True)
            return
        ok = svc.start()
        self.act_local_service.setChecked(ok)
        if ok:
            cfg = svc.config
            token_file = getattr(svc, "token_display", "")
            self.statusBar().showMessage(
                f"本地 AI 控制服务已启动：http://{cfg['host']}:{cfg['port']} "
                f"（令牌见 config/local_service_token.txt）")
        else:
            self.statusBar().showMessage(
                f"本地 AI 控制服务启动失败：{getattr(svc, 'last_error', '')}")

    def _toggle_local_service(self, checked: bool):
        svc = self._local_service
        if checked:
            ok = svc.start()
            if not ok:
                self.act_local_service.setChecked(False)
                self.statusBar().showMessage(
                    f"本地 AI 控制服务启动失败：{getattr(svc, 'last_error', '')}")
                return
            cfg = svc.config
            self.statusBar().showMessage(
                f"本地 AI 控制服务已启动：http://{cfg['host']}:{cfg['port']}")
        else:
            svc.stop()
            self.statusBar().showMessage("本地 AI 控制服务已停止")
        cfg = dict(svc.config)
        cfg["enabled"] = bool(checked)
        from ..app.local_service import save_service_config
        save_service_config(cfg)

    def closeEvent(self, event):
        # 关窗口时先停本地控制服务（HTTP 线程），避免残留端口占用。
        if getattr(self, "_local_service", None) is not None:
            self._local_service.stop()
        # 关窗口时作写在途加载：worker 回传的结果会被 is_current 判为陈旧丢弃，
        # 避免信号落到已销毁的 widget 上。随后正常关闭。
        self._load_controller.cancel_all()
        self._load_pending_token = 0
        super().closeEvent(event)

    # ---------------- 多文档标签 ----------------
    def _new_or_reuse_context(self) -> AppState:
        """打开文档时：若当前激活标签还没有文档（首开 / 上一页被清空），复用它；否则新建。"""
        if self.state is not None and self.state.doc is None:
            return self.state
        ctx = AppState()
        self._contexts.append(ctx)
        return ctx

    def _tab_title(self, ctx: AppState) -> str:
        if getattr(ctx, "kind", "map") == "tileset" and ctx.tileset_doc is not None:
            name = ctx.tileset_doc.name
            return name + (" *" if ctx.tileset_doc.dirty else "")
        if ctx.doc is None:
            return "无文档"
        name = ctx.doc.path.name
        return name + (" *" if ctx.doc.dirty else "")

    def _rebuild_tabs(self):
        self.doc_tabs.blockSignals(True)
        while self.doc_tabs.count():
            self.doc_tabs.removeTab(0)
        for ctx in self._contexts:
            self.doc_tabs.addTab(self._tab_title(ctx))
        if 0 <= self._active_index < self.doc_tabs.count():
            self.doc_tabs.setCurrentIndex(self._active_index)
        self.doc_tabs.blockSignals(False)

    def _update_active_tab_title(self):
        if 0 <= self._active_index < self.doc_tabs.count():
            self.doc_tabs.setTabText(self._active_index, self._tab_title(self.state))

    def _save_view_transform(self):
        """切走前，把当前视图的缩放 / 平移存进正在离开的文档。"""
        if self.state is not None and self.state.doc is not None:
            from PySide6.QtGui import QTransform
            self.state._view_matrix = QTransform(self.view.transform())

    def _restore_view_transform(self):
        xf = getattr(self.state, "_view_matrix", None)
        if xf is None:
            return
        self.view.setTransform(xf)
        self.view.camera.scale = xf.m11()
        self.view.zoomChanged.emit(self.view.camera.scale)

    def _attach_context(self, ctx: AppState):
        """把某个文档 context 切换为当前激活（重建场景 / 视图 / 面板并恢复视图变换）。"""
        # 已经是当前显示的文档（例如点击已激活的标签）→ 跳过，避免无谓 fit 丢缩放。
        if id(ctx) == getattr(self, "_displayed_ctx_id", None):
            return
        self._save_view_transform()              # 保存旧文档视图变换
        self._active_index = self._contexts.index(ctx)
        self.state = ctx
        self._active_layer = None          # 旧文档的层不能带到新文档（按钮启用态据此同步）
        self._rebuild_tabs()
        self._refresh_document()                 # 重建场景 / 视图 / 面板 + fit
        self._restore_view_transform()            # 恢复该文档的缩放 / 平移
        self._displayed_ctx_id = id(ctx)

    def _on_tab_changed(self, index: int):
        if self._load_pending_token:
            # 加载在途：切到尚未就绪的空 ctx 会让 _refresh_document 拿到 None doc。
            self._rebuild_tabs()             # 复位标签栏到当前激活项
            self.statusBar().showMessage("地图加载中，请稍候…")
            return
        if not (0 <= index < len(self._contexts)):
            return
        if index == self._active_index and self.state is not None \
                and self.state.doc is not None:
            return
        self._attach_context(self._contexts[index])

    def _close_tab(self, index: int):
        if not (0 <= index < len(self._contexts)):
            return
        if len(self._contexts) <= 1:
            self.statusBar().showMessage("至少保留一个文档标签")
            return
        ctx = self._contexts[index]
        if getattr(ctx, "kind", "map") == "tileset" and ctx.tileset_doc is not None:
            if ctx.tileset_doc.dirty:
                if QMessageBox.question(
                    self, "关闭图集",
                    f"「{ctx.tileset_doc.name}」有未保存的修改，确定关闭？",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                    return
        elif ctx.doc is not None and ctx.doc.dirty:
            if QMessageBox.question(
                self, "关闭文档",
                f"「{ctx.doc.path.name}」有未保存的修改，确定关闭？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        del self._contexts[index]
        if index == self._active_index:
            new_index = min(index, len(self._contexts) - 1)
            self._active_index = new_index
            self._displayed_ctx_id = None          # 强制刷新到相邻文档
            self._attach_context(self._contexts[new_index])
        else:
            if index < self._active_index:
                self._active_index -= 1
            self._rebuild_tabs()

    def _refresh_document(self):
        if getattr(self.state, "kind", "map") == "tileset" and \
                self.state.tileset_doc is not None:
            self._refresh_tileset_document()
            return
        # 切回地图文档：恢复地图专属面板与中央地图页。
        # 图集设置面板只在「图集任务」里出现，切回地图时隐藏。
        for dock in (self.layers_dock, self.tilesets_dock,
                     self.object_toolbar, self.minimap_dock, self.properties_dock):
            dock.show()
        self.tileset_props_dock.hide()
        self.tileset_tile_props_dock.hide()
        self._central_stack.setCurrentIndex(self._map_page)
        doc = self.state.doc
        # 老地图 / 早期脚本建的地图里，波次编排节点可能写成 visible=1，
        # 重开后会在画布左上角冒出几个点。打开时统一归一为隐藏；节点图
        # 渲染不读 visible，所以节点图里的连线和编辑完全不受影响。
        if doc is not None:
            from ..rw.wave_gen import hide_wave_nodes
            hide_wave_nodes(doc)
        self.scene.set_document(doc)
        self.view.set_document(doc)
        # 标尺覆盖层随 scene.clear() 销毁，标记也清掉（切工具不清）
        self._ruler_segments = []
        self._ruler_pending = None
        self._capture_cache = None      # 切文档后旧层的抓取虚影不能复用
        # set_document 每次新建 show_grid=True 的 grid，这里回写用户开关，
        # 否则「关掉网格 → 切文档」网格会自己回来。
        self.scene.set_grid_visible(self.act_grid.isChecked())
        # set_document 会重建全部 layer_items，视图开关必须在这里回写，
        # 否则「高亮当前层 / 只显示当前层 / 隐藏对象名」切文档后就失效。
        self.scene.set_view_layer_options(
            highlight=self.act_highlight_current.isChecked(),
            only_current=self.act_only_current.isChecked(),
            show_names=self.act_show_object_names.isChecked())
        self.scene.current_layer = self.state.current_layer
        self.scene.view_current_layer = (
            self._active_layer or self.state.current_layer)
        self.layers_dock.set_document(doc)
        self.layers_dock.select_layer(self.state.current_layer)
        self.tilesets_dock.set_document(doc)
        self.properties_dock.set_document(doc)
        self.trigger_graph_dock.set_document(
            doc, self.state.undo_stack, self._refresh_objects)
        self.object_toolbar.set_document(doc)
        # 切文档后当前层回到瓦片层：整排对象工具连同变换 4 键一起回暗。
        self.object_toolbar.set_object_layer_active(
            isinstance(self._active_layer, ObjectLayer))
        self._refresh_transform_actions()
        # 迷你地图：绑定当前视图，并跟随激活文档的编辑（防抖重渲染）。
        self.minimap_dock.minimap.set_view(self.view)
        self.minimap_dock.minimap.set_document(doc)
        if getattr(self, "_doc_edited_conn", None) is not None:
            try:
                self._doc_edited_conn.disconnect()
            except Exception:             # noqa: BLE001
                pass
        self._doc_edited_conn = self.state.undo_stack.indexChanged.connect(
            self._on_doc_edited)
        self._bind_undo_actions()        # 切换标签后 Ctrl+Z 必须作用到当前文档
        # 切换文档后清掉未完成的形状，避免把旧图的拖拽状态带过来。
        self._cancel_object_tool()
        self.status_profile.setText(self.state.profile.version_id)
        self._update_title()
        self._update_active_tab_title()

    # ---------------- 撤销 / 重做（跟随当前标签的撤销栈） ----------------
    def _bind_undo_actions(self):
        """把「撤销 / 重做」动作重新挂到当前激活文档的撤销栈上。

        每个标签页都有自己的 ``QUndoStack``；动作若绑死初始栈，
        在新标签里按 Ctrl+Z 会去撤销另一张地图的改动。
        """
        old = getattr(self, "_undo_stack_ref", None)
        if old is not None:
            for sig in (old.canUndoChanged, old.undoTextChanged,
                        old.canRedoChanged, old.redoTextChanged):
                try:
                    sig.disconnect(self._update_undo_actions)
                except (RuntimeError, TypeError):
                    pass
        stack = self.state.undo_stack
        for sig in (stack.canUndoChanged, stack.undoTextChanged,
                    stack.canRedoChanged, stack.redoTextChanged):
            sig.connect(self._update_undo_actions)
        self._undo_stack_ref = stack
        self._update_undo_actions()

    def _update_undo_actions(self, *_args):
        stack = getattr(self, "_undo_stack_ref", None) or self.state.undo_stack
        try:
            undo_text = stack.undoText()
            redo_text = stack.redoText()
            can_undo, can_redo = stack.canUndo(), stack.canRedo()
        except RuntimeError:
            # 窗口 / 文档销毁过程中栈的 C++ 对象可能已释放，信号仍会收到一次。
            return
        self.act_undo.setEnabled(can_undo)
        self.act_redo.setEnabled(can_redo)
        self.act_undo.setText(f"撤销 {undo_text}".strip() if undo_text else "撤销")
        self.act_redo.setText(f"重做 {redo_text}".strip() if redo_text else "重做")

    def _undo(self):
        stack = self.state.undo_stack
        if stack.canUndo():
            stack.undo()

    def _redo(self):
        stack = self.state.undo_stack
        if stack.canRedo():
            stack.redo()

    def _on_doc_edited(self, *_args):
        """激活文档发生编辑（含撤销 / 重做）：刷新标签标题 + 防抖重渲染迷你地图。"""
        self._update_active_tab_title()
        self._update_title()
        self._schedule_minimap_refresh()

    # ---------------- 图集文档（制作图集） ----------------
    def _new_tileset(self):
        """用一张图集大图生成铁锈瓦片图集，作为新标签打开。"""
        default_tile = self.state.doc.tile_w if self.state.doc is not None else 20
        dlg = NewTilesetDialog(self, default_tile=default_tile)
        if dlg.exec() != QDialog.Accepted:
            return
        tdoc = dlg.document
        if tdoc is None or tdoc.tileset is None:
            return
        ctx = AppState()
        ctx.kind = "tileset"
        ctx.tileset_doc = tdoc
        self._contexts.append(ctx)
        self._displayed_ctx_id = None          # 强制刷新到新标签
        self._attach_context(ctx)
        self.statusBar().showMessage(f"已创建图集「{tdoc.name}」：请「另存为」导出 .tsx")

    def _refresh_tileset_document(self):
        """图集标签：隐藏地图专属面板，中央切到只读图集 + 每瓦片属性编辑器。"""
        tdoc = self.state.tileset_doc
        for dock in (self.layers_dock, self.tilesets_dock,
                     self.object_toolbar, self.minimap_dock, self.properties_dock):
            dock.hide()
        self._central_stack.setCurrentIndex(self._tileset_page)
        self._tileset_editor.set_tileset(tdoc.tileset, tdoc, self.state.undo_stack)
        # 图集标签下，两块面板分别绑左（瓦片属性）/ 右（图集设置）。
        # 左侧随图集里的选格事件实时切换当前瓦片（连接建在 __init__ 里只连一次）。
        self.tileset_tile_props_dock.set_context(
            tdoc.tileset, tdoc, self.state.undo_stack)
        self.tileset_props_dock.set_context(tdoc.tileset, tdoc, self.state.undo_stack)
        self.tileset_tile_props_dock.show()
        self.tileset_props_dock.show()
        self._bind_undo_actions()        # 图集标签有自己的撤销栈
        self.status_profile.setText(
            self.state.profile.version_id if self.state.profile else "")
        self._update_title()
        self._update_active_tab_title()

    def _on_tileset_edited(self):
        if self.state.tileset_doc is not None:
            self.state.tileset_doc.dirty = True
        self._update_active_tab_title()
        self._update_title()

    def _save_tileset(self):
        tdoc = self.state.tileset_doc
        if tdoc is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存图集", str(tdoc.path), "Tiled 图集 (*.tsx)")
        if not path:
            return
        try:
            out = tdoc.save(Path(path))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._update_active_tab_title()
        self._update_title()
        self.statusBar().showMessage(f"已保存图集：{out.name}")

    def _schedule_minimap_refresh(self):
        """迷你地图面板可能已随窗口关闭被销毁，访问前先确认 C++ 对象仍存活。"""
        mm = getattr(self, "minimap_dock", None)
        if mm is None:
            return
        try:
            visible = mm.isVisible()
        except RuntimeError:
            return                      # C++ 对象已销毁（窗口/面板已关），直接跳过
        if visible:
            self._minimap_timer.start()

    def _minimap_refresh(self):
        mm = getattr(self, "minimap_dock", None)
        if mm is None:
            return
        try:
            if mm.isVisible():
                # 编辑后只重画脏格附近；结构 / 换图集等失效条件由
                # MiniMap.refresh_cells 自己判断并回退全量。
                mm.minimap.refresh_cells(mm.minimap.take_dirty_cells())
        except RuntimeError:
            return                      # 同上，面板已销毁时忽略

    def _update_title(self):
        if getattr(self.state, "kind", "map") == "tileset" and \
                self.state.tileset_doc is not None:
            tdoc = self.state.tileset_doc
            name = tdoc.name
            mark = " *" if tdoc.dirty else ""
            self.setWindowTitle(
                f"{name}{mark} — 铁锈战争地图编辑器 [图集]")
            return
        doc = self.state.doc
        name = doc.path.name if doc is not None else "无文档"
        mark = " *" if doc is not None and doc.dirty else ""
        self.setWindowTitle(f"{name}{mark} — 铁锈战争地图编辑器 [{self.state.profile.version_id}]")

    def _flush_graph_positions(self):
        """保存前把节点图布局写进对象位置属性（仅保存时，不入撤销栈）。

        节点图没打开过（data 为空）或文档类型不匹配时直接跳过。写入只碰私有
        属性并把相关对象层置 dirty，交给本次 write_document 一起落盘。
        """
        dock = getattr(self, "trigger_graph_dock", None)
        if dock is None or dock.doc is not self.state.doc:
            return
        try:
            dock.write_positions_to_properties()
        except Exception:  # noqa: BLE001 - 位置记录失败不该阻断地图保存
            pass

    def _warn_unknown_units(self, doc):
        """保存前提示刷兵列表里游戏不存在的单位名（不阻断保存）。

        游戏解析 unitAdd 时若 spawnUnits 含无效单位，会抛 UnitConfigException
        并把整个触发器丢弃，表现为「波次关联线都连好了但兵一个都不出」。
        """
        try:
            from ..rw.unit_names import unknown_units
        except Exception:  # noqa: BLE001
            return
        bad: dict[str, list[str]] = {}
        keys = ("spawnUnits", "randomUnits", "unit", "unitType", "unitAdd")
        for layer in getattr(doc, "layers", ()) or ():
            for obj in getattr(layer, "objects", ()) or ():
                props = getattr(obj, "properties", None)
                if props is None:
                    continue
                values = getattr(props, "values", props)
                for key in keys:
                    text = values.get(key) if isinstance(values, dict) else None
                    if not text:
                        continue
                    names = unknown_units(str(text))
                    if names:
                        label = f"{getattr(obj, 'name', '') or '对象'}#{getattr(obj, 'object_id', '?')}"
                        for name in names:
                            bad.setdefault(name, []).append(f"{label}.{key}")
        if not bad:
            return
        detail = "\n".join(
            f"• {name}  （{', '.join(locs[:4])}{' …' if len(locs) > 4 else ''}）"
            for name, locs in bad.items())
        QMessageBox.warning(
            self, "单位名可能写错",
            f"以下单位名在游戏单位目录里找不到，游戏会丢弃对应刷兵触发器，兵刷不出来：\n\n"
            f"{detail}\n\n请检查是否拼错（例如虫王的正确名字是 bugMeleeT31）。\n"
            f"若来自你另外安装的 mod，可忽略本提示。",
            QMessageBox.Ok)

    def _prepare_survival_waves(self) -> bool:
        """保存前：校验波次链 → 编译成原版 survivalWaves 写进 map_info。

        只有地图里存在 waveGen 节点时才动作。链条有问题时弹窗列出断点，
        让用户选「仍然保存」还是「返回修改」；返回 False 表示取消本次保存。
        编译写入本身失败不阻断保存（尽力而为），但会在状态栏提示。
        """
        doc = self.state.doc
        if doc is None:
            return True
        try:
            from ..rw.wave_gen import (
                apply_survival_waves,
                apply_wave_table,
                collect_wave_nodes,
                collect_wave_table_nodes,
                ensure_attack_points,
                sync_wave_dispatch_triggers,
                validate_wave_chain,
            )
        except Exception:  # noqa: BLE001
            return True
        has_table = bool(collect_wave_table_nodes(doc))
        has_chain = bool(collect_wave_nodes(doc))
        if not has_table and not has_chain:
            return True  # 没有波次节点，普通地图照常保存

        # 游戏只认 name=attack_point 的对象作为出兵点。整合体同步编辑可能
        # 把名字改掉，导致第一波 TriggerEngine.attackPoints 空列表越界闪退；
        # 保存前统一纠正，并在状态栏告知用户改了什么。
        fixed_points = ensure_attack_points(doc)

        # 单位名拼错时 unitAdd 触发器会被游戏整个丢弃（连锁导致波次关联
        # 报 target not found、虫王之类根本刷不出来）。保存前扫一遍明确提示。
        self._warn_unknown_units(doc)

        # 官方波次表节点优先：它承载的是用户直接写好的原版文本，最权威；
        # 存在时直接同步到 map_info，不再跑 waveGen 链的编译（避免互相覆盖）。
        if has_table:
            try:
                text = apply_wave_table(doc)
                if text:
                    extra = ""
                    if fixed_points:
                        extra = f"；已修复 {fixed_points} 个出兵点名字（游戏只认 attack_point）"
                    self.statusBar().showMessage(
                        f"已根据官方波次表节点更新生存波次表（{len(text.splitlines())} 波）{extra}",
                        4000)
                elif fixed_points:
                    self.statusBar().showMessage(
                        f"已修复 {fixed_points} 个出兵点名字（游戏只认 attack_point）", 4000)
            except Exception as exc:  # noqa: BLE001
                self.statusBar().showMessage(f"波次表同步失败（不影响本次保存）：{exc}", 5000)
            return True

        problems = validate_wave_chain(doc)
        if problems:
            detail = "\n".join(f"• {p}" for p in problems)
            msg = (f"生存波次链有 {len(problems)} 处问题：\n\n{detail}\n\n"
                   "仍要保存吗？（波次表可能不完整，游戏内倒计时异常）")
            answer = QMessageBox.warning(
                self, "波次链校验", msg,
                QMessageBox.No | QMessageBox.Yes, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return False

        try:
            text = apply_survival_waves(doc)
            dispatches = sync_wave_dispatch_triggers(doc)
            if text:
                extra = f"，{dispatches} 个波次关联已写成游戏触发器" if dispatches else ""
                if fixed_points:
                    extra += f"；已修复 {fixed_points} 个出兵点名字（游戏只认 attack_point）"
                self.statusBar().showMessage(
                    f"已根据波次生成器更新生存波次表（{len(text.splitlines())} 波）{extra}",
                    4000)
            elif fixed_points:
                self.statusBar().showMessage(
                    f"已修复 {fixed_points} 个出兵点名字（游戏只认 attack_point）", 4000)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"波次表编译失败（不影响本次保存）：{exc}", 5000)
        return True

    def _save(self):
        if getattr(self.state, "kind", "map") == "tileset" and \
                self.state.tileset_doc is not None:
            self._save_tileset()
            return
        if self.state.doc is None:
            return
        if not self.state.profile.write_enabled:
            self.statusBar().showMessage("当前版本为只读优先，请切换到 1.15 或另存为")
            return
        if not self._own_file:
            self.statusBar().showMessage("为避免覆盖官方地图，已转为「另存为」")
            self._save_as()
            return
        self._flush_graph_positions()
        if not self._prepare_survival_waves():
            return
        path = self.state.doc.save()
        self._update_title()
        self._update_active_tab_title()   # 标签上的文件名 / 未保存星号同步刷新
        self.statusBar().showMessage(f"已保存 {path}")

    def _save_as(self):
        if getattr(self.state, "kind", "map") == "tileset" and \
                self.state.tileset_doc is not None:
            self._save_tileset()
            return
        if self.state.doc is None:
            return
        default = str(as_path(self.state.profile.export_dir) / "验收用.tmx")
        path, _ = QFileDialog.getSaveFileName(self, "另存为", default, "TMX 地图 (*.tmx)")
        if not path:
            return
        target = as_path(path)
        self._flush_graph_positions()
        if not self._prepare_survival_waves():
            return
        try:
            result = export_map(
                self.state.doc, target.parent, self.state.profile,
                with_thumbnail=True, target_name=target.name,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "另存失败", str(exc))
            return
        self._own_file = True
        self._update_title()
        self._update_active_tab_title()   # 另存为改了 doc.path，标签名要跟着变
        thumb = result.get("thumbnail", {})
        self.statusBar().showMessage(
            f"已另存 {result['tmx']}  资源 {result['resources']} 项  "
            f"缩略图 {'OK' if thumb.get('ok') else thumb.get('error', '失败')}  "
            f"缺资源 {len(result['missing'])}"
        )

    def _make_thumbnail(self):
        if self.state.doc is None:
            return
        result = make_thumbnail(self.state.doc.path, self.state.profile)
        self.statusBar().showMessage(
            f"缩略图 {result.get('path', '')} {'OK' if result.get('ok') else result.get('error', '')}"
        )

    def _pack_rwmod(self):
        if self.state.doc is None:
            return
        default = str(as_path(self.state.profile.export_dir) / (self.state.doc.path.stem + ".rwmod"))
        path, _ = QFileDialog.getSaveFileName(self, "打包模组", default, "RW 模组 (*.rwmod)")
        if not path:
            return
        png = self.state.doc.path.with_name(self.state.doc.path.stem + "_map.png")
        result = pack_rwmod(self.state.doc.path, path, png if png.is_file() else None,
                            title=self.state.doc.path.stem)
        self.statusBar().showMessage(f"已打包 {result.get('path', '')}")

    # ---------------- 编辑 ----------------
    def _set_tool(self, tool_id: str):
        # B / E 与图标工具栏的「瓦片画笔 / 橡皮」共用同一条切换路径，
        # 避免两套按钮各选中一半。
        self._set_object_tool("eraser" if tool_id == "eraser" else "brush")

    def _on_gid(self, gid: int):
        # 从图块集点瓦片就是要用画笔/图章落笔：若之前停在钢笔、矩形等对象工具，
        # 这里自动切回画笔，否则 MapView 不会发出 cellPressed，看起来像"印章画不了"。
        self._set_tool("brush")
        self.state.brush_gid = int(gid)
        self.statusBar().showMessage(f"笔刷 gid={gid}")

    # ---- 临时吸管：不改变 state.tool_id，取完继续用当前工具画 ----
    def _pick_at_cursor(self):
        """I 键：直接取鼠标所在格；hover 未就绪时用全局光标位置兜底。"""
        col, row = self._hover_cell
        if col < 0 or row < 0 or self.state.doc is None:
            pos = self.view.mapFromGlobal(QCursor.pos())
            col, row = self.view._cell(pos)
        self._pick_gid(col, row)

    def _pick_gid(self, col: int, row: int):
        gid = pick_gid(self.state, self.state.doc, col, row)
        if gid is None:
            self.statusBar().showMessage(f"临时吸管 @({col},{row})：该格没有图块")
            return
        self.state.brush_gid = gid
        self.state.stamp = None                  # 取单格，取消矩形盖章
        self.tilesets_dock.select_gid(gid)       # 图块集面板同步高亮
        self.statusBar().showMessage(
            f"临时吸管 @({col},{row}) → gid={gid}；当前工具仍为 {self.state.tool.name}"
        )

    # ---- 右键拖动抓取地图区域 → 实时虚影 + 松手生成图章 ----
    @staticmethod
    def _capture_cells(layer, c0: int, r0: int, c1: int, r1: int):
        """读取抓取矩形：地图外一律当作空格。

        TileLayer.cells 是定长一维表，get() 不检查边界；负索引会绕回表尾、
        正越界会 IndexError。右键拖动允许拖到地图外，必须在这里兜住。
        """
        return [[layer.get(c, r) if layer.in_bounds(c, r) else 0
                 for c in range(c0, c1 + 1)]
                for r in range(r0, r1 + 1)]

    def _on_capture_rect(self, region):
        """右键拖动中：把抓取区域（当前层实时格）显示为半透明虚影。"""
        if region is None:
            self.scene.clear_capture_preview()
            self._capture_cache = None
            return
        layer = self.state.current_layer
        if not isinstance(layer, TileLayer):
            return
        cached = self._capture_cache
        if cached is not None and cached[0] == region and cached[1] == id(layer):
            return                      # 同区域同层：不重读层、不重建 2D 虚影
        c0, r0, c1, r1 = region
        cells = self._capture_cells(layer, c0, r0, c1, r1)
        self._capture_cache = (region, id(layer), cells)
        self.scene.set_capture_preview(cells, c0, r0)

    def _capture_region(self, c0: int, r0: int, c1: int, r1: int):
        layer = self.state.current_layer
        if not isinstance(layer, TileLayer):
            self.statusBar().showMessage("当前层不是瓦片层，无法抓取图章")
            return
        stamp = self._capture_cells(layer, c0, r0, c1, r1)
        self.state.stamp = stamp                  # 保留 raw gid（含翻转标志）
        self._refresh_transform_actions()
        self.statusBar().showMessage(
            f"已抓取 {len(stamp[0])}x{len(stamp)} 格区域作图章"
            "（翻转/旋转按钮已点亮，左键印下）"
        )

    # ---- 右键单击拾取当前层单格（严格当前层，空则不拾取）----
    def _right_pick_gid(self, col: int, row: int):
        layer = self.state.current_layer
        if not isinstance(layer, TileLayer):
            return
        raw = layer.get(col, row)
        gid, _, _, _ = split_gid(raw)
        if gid == 0:
            self.statusBar().showMessage(
                f"右键拾取 @({col},{row})：当前层该格为空，未改动笔刷")
            return
        self.state.brush_gid = raw                # 保留翻转标志
        self.state.stamp = None                   # 退回单格画笔
        self._refresh_transform_actions()
        self.tilesets_dock.select_gid(gid)        # 图块集面板高亮对应图集+瓦片
        self.statusBar().showMessage(f"右键拾取 @({col},{row}) → gid={gid}")

    def _on_stamp(self, gids):
        if gids:
            self._set_tool("brush")   # 选了矩形印章就直接进入可盖章状态
        self.state.stamp = gids
        self._refresh_transform_actions()
        if gids:
            self.statusBar().showMessage(
                f"图块选区 {len(gids[0])}x{len(gids)} 格"
                "（画布上可一次盖章，翻转/旋转按钮已点亮）"
            )

    def _on_tileset_settings(self, ts):
        """图块集面板右键「设置图集」：把该图集作为新任务（标签）打开，
        与打开地图一样的「多任务」方式；图集设置面板只在图集任务里出现。"""
        if ts is None or self.state.doc is None:
            return
        # 同名/同对象图集任务已打开则直接切过去，不重复开。
        for ctx in self._contexts:
            if getattr(ctx, "kind", "map") == "tileset" \
                    and ctx.tileset_doc is not None \
                    and ctx.tileset_doc.tileset is ts:
                self._displayed_ctx_id = None
                self._attach_context(ctx)
                return
        tdoc = TilesetDocument(
            path=ts.source_path or Path("."),
            tileset=ts,
            image_path=self._resolve_tileset_image(ts),
            profile=self.state.profile,
            dirty=False,
        )
        # 借地图的解析器让图集编辑器能读到图片（地图引用的 .tsx / 内嵌图集）。
        resolver = getattr(self.state.doc, "resolver", None)
        if resolver is not None:
            tdoc.resolver = resolver
        ctx = AppState()
        ctx.kind = "tileset"
        ctx.tileset_doc = tdoc
        self._contexts.append(ctx)
        self._displayed_ctx_id = None          # 强制刷新到新标签
        self._attach_context(ctx)
        self.statusBar().showMessage(f"已打开图集任务：「{tdoc.name}」（新标签）")

    def _resolve_tileset_image(self, ts) -> "Path | None":
        """把地图里引用的图集图片解析成绝对路径，供图集任务显示与导出。"""
        doc = self.state.doc
        resolver = getattr(doc, "resolver", None)
        if resolver is None or not ts.image_source:
            return None
        try:
            return resolver.resolve(
                ts.image_source, tsx_dir=ts.base_dir if ts.is_external else None)
        except Exception:  # noqa: BLE001
            return None

    def _refresh_after_tileset_settings(self):
        """图集设置写回后：重建图集视图、标记脏、更新标题与状态。"""
        if getattr(self.state, "kind", "map") == "tileset" \
                and self.state.tileset_doc is not None:
            tdoc = self.state.tileset_doc
            tdoc.dirty = True
            # 图集画布内部缓存 QPixmap；换图后只清全局缓存不够，必须重装一次
            # 才会立刻显示新图（顺带按新的瓦片尺寸 / 边距重算网格）。
            self._tileset_editor.set_tileset(
                tdoc.tileset, tdoc, self.state.undo_stack)
        elif getattr(self.state, "doc", None) is not None:
            self.state.doc.dirty = True
        self._update_title()
        self._update_active_tab_title()
        self.statusBar().showMessage("已修改图集设置（可撤销）")

    def _on_layer_selected(self, layer):
        self.state.current_layer = layer if isinstance(layer, TileLayer) else None
        self.scene.current_layer = self.state.current_layer
        # 视图开关的「当前层」允许是对象层（Tiled 的高亮当前层同样适用），
        # 与只接受 TileLayer 的编辑语义 current_layer 分开维护。
        self._active_layer = layer
        self.scene.view_current_layer = layer
        # 对象层：工具栏上的「对象层」下拉已移除，改由图层面板直接指定落点。
        if isinstance(layer, ObjectLayer):
            self.object_toolbar.set_target_layer(layer)
        # 切图层就清掉任何框选 / 选区：瓦片选框、正在拖动的框选状态、
        # 以及画布上的对象选中态——否则上一张图的框会残留在新层上。
        self._clear_tile_selection()
        self.scene.set_selected_objects([])
        # 用户要求：没选对象层时，对象工具整排 + 变换 4 键全部置灰。
        self.object_toolbar.set_object_layer_active(isinstance(layer, ObjectLayer))
        self._refresh_transform_actions()
        if layer is not None:
            self.statusBar().showMessage(
                f"当前层 {layer.name}{'（只读）' if layer.readonly else ''}  {layer.note}"
            )

    def _refresh_layer_view(self, layer):
        """图层属性（可见 / 透明度）变化后的轻量刷新：不重建整个场景。

        透明度滑杆每帧都会回调，走 ``_refresh_document``（重建场景 + 各面板）
        会明显卡顿，这里只更新该层的渲染状态与标题 / 迷你地图。
        """
        if layer is None:
            return
        self.scene.set_layer_visible(layer, layer.visible)
        self.scene.set_layer_opacity(layer, layer.opacity)
        self.layers_dock.list.viewport().update()
        self._update_title()
        # 可见性 / 透明度改变影响整张概览的合成，不能按脏格局部重画。
        mm = getattr(self, "minimap_dock", None)
        if mm is not None:
            mm.minimap.invalidate_full()
        self._schedule_minimap_refresh()

    def _on_visibility(self, layer, visible: bool):
        """眼睛图标切换图层可见性：写进 TMX 的 visible，必须可撤销。"""
        if layer is None or layer.visible == visible:
            return
        from ..commands.layer_command import LayerFlagCommand
        cmd = LayerFlagCommand(
            self.state.doc, layer, "visible", layer.visible, visible,
            f"{'显示' if visible else '隐藏'} {layer.name}",
            lambda l=layer: self._refresh_layer_view(l), dirty=False)
        self.state.undo_stack.push(cmd)

    def _on_visibility_batch(self, changes):
        """「显示 / 隐藏其它图层」：多层改动合成一条撤销记录。"""
        changes = [(layer, visible) for layer, visible in (changes or [])
                   if layer is not None and layer.visible != visible]
        if not changes:
            return
        from ..commands.layer_command import LayerFlagCommand
        stack = self.state.undo_stack
        stack.beginMacro("显示/隐藏其它图层")
        try:
            for layer, visible in changes:
                stack.push(LayerFlagCommand(
                    self.state.doc, layer, "visible", layer.visible, visible,
                    f"{'显示' if visible else '隐藏'} {layer.name}",
                    lambda l=layer: self._refresh_layer_view(l), dirty=False))
        finally:
            stack.endMacro()

    def _on_lock_batch(self, changes):
        """「锁定 / 解锁其它所有层」：多层锁定改动合成一条撤销记录。"""
        changes = [(layer, locked) for layer, locked in (changes or [])
                   if layer is not None
                   and bool(getattr(layer, "locked", False)) != bool(locked)]
        if not changes:
            return
        from ..commands.layer_command import LayerFlagCommand
        stack = self.state.undo_stack
        stack.beginMacro("锁定/解锁其它图层")
        try:
            for layer, locked in changes:
                stack.push(LayerFlagCommand(
                    self.state.doc, layer, "locked",
                    bool(getattr(layer, "locked", False)), bool(locked),
                    f"{'锁定' if locked else '解锁'} {layer.name}",
                    lambda l=layer: self._refresh_layer_view(l)))
        finally:
            stack.endMacro()

    def _on_opacity(self, layer, value: float):
        """透明度滑杆：拖动期间连续触发，靠 merge_key 合并成一条撤销记录。"""
        if layer is None:
            return
        value = round(float(value), 4)
        if abs(float(layer.opacity) - value) < 1e-4:
            return
        from ..commands.layer_command import LayerFlagCommand
        cmd = LayerFlagCommand(
            self.state.doc, layer, "opacity", layer.opacity, value,
            f"{layer.name} 透明度 → {int(round(value * 100))}%",
            lambda l=layer: self._refresh_layer_view(l),
            merge_key=f"opacity:{id(layer)}", dirty=False)
        self.state.undo_stack.push(cmd)

    def _refresh_layer(self, layer, cells=None):
        if cells is not None and not isinstance(cells, (list, tuple)):
            cells = list(cells)
        self.scene.refresh_layer(layer, cells)
        self._update_title()
        # 层内容变了，同区域的抓取虚影必须重新读取实时格，不能复用旧快照。
        self._capture_cache = None
        mm = getattr(self, "minimap_dock", None)
        if mm is not None:
            if cells is None:
                mm.minimap.invalidate_full()   # 整层失效 → 概览全量重建
            elif cells:
                mm.minimap.note_dirty_cells(cells)

    def _automap_write_target(self):
        """「绘制时自动地图」开启时，落笔固定写 set 层；关闭时返回 None 保持原行为。

        官方 rules.txt 只匹配 set 层里各基础图集的第 1 格。若用户把当前层
        切到 Ground / 用了抓来的渲染图章，写到别的层或非首格瓦片都不会生成
        过渡，且局部重算的 reset_base 还会把刚画的内容擦掉。
        """
        if not (self._automap_while_drawing and self._automapper is not None):
            return None
        doc = self.state.doc
        target = doc.layer_by_name("set") if doc is not None else None
        if (not isinstance(target, TileLayer) or target.readonly
                or getattr(target, "locked", False)):
            return None
        return target

    def _normalize_marker(self, raw):
        """归一化到 set 层标记，带上从规则派生的规范标记映射（Stone/Lava）。

        规则的规范标记属于规则图 gid 空间；当工作图图集 firstgid 不同时
        （如 Large Lava Divide），交给 AutoMapper 按「图集名 + 相对索引」
        换算到工作图空间，避免 Lava+4 被误当成 Stone 标记。
        """
        doc = self.state.doc
        if doc is None:
            return -1
        normalize = getattr(self._automapper, "normalize_marker", None) \
            if self._automapper is not None else None
        if normalize is not None:
            return normalize(doc, raw)
        canonical = getattr(self._automapper, "canonical_marker_gids", None) \
            if self._automapper is not None else None
        return normalize_marker_gid(doc, raw, canonical)

    def _automap_marker(self, raw):
        """把写入 set 层的瓦片归一到规则认可的标记 gid；无法归一时原样保留。"""
        gid = self._normalize_marker(raw)
        return (int(raw) & 0xFFFFFFFF) if gid < 0 else (gid & 0xFFFFFFFF)

    @staticmethod
    def _brush_has_content(state) -> bool:
        """笔刷是否真的有瓦片内容（gid 或图章里至少一个非零格）。

        ``brush_gid == 0`` 且没有图章时，画笔会写入 0 —— 等于橡皮擦。
        用户要求此时完全不落笔，直到右键地图 / 图块集瓦片或右键拖动
        产出了笔刷内容。带翻转标志的 raw gid 用 split_gid 取基础 gid 判定。
        """
        raw = int(getattr(state, "brush_gid", 0) or 0)
        if split_gid(raw)[0]:
            return True
        stamp = getattr(state, "stamp", None)
        return bool(stamp) and any(
            split_gid(int(v or 0))[0] for line in stamp for v in line)

    def _on_press(self, col: int, row: int):
        if not self.state.can_edit():
            self.statusBar().showMessage("当前层只读或版本只读，无法编辑")
            return
        if (self.state.tool_id == "brush"
                and not self._brush_has_content(self.state)):
            # 不拿空值代替、不开始笔划、不写撤销栈：等于「无法干任何事」。
            self.statusBar().showMessage(
                "画笔还没选瓦片：先点图块集 / 右键地图上的瓦片，或右键拖动抓取图章")
            return
        layer = self.state.current_layer
        if self.state.tool_id == "fill":
            self._do_fill(layer, col, row)
            return
        target = self._automap_write_target()
        write_layer = target if target is not None else layer
        raw = self.state.tool.desired_gid(self.state, write_layer, col, row)
        if raw is None:
            return                        # 桩工具 / 不写入，不开始笔划
        if target is not None:
            raw = self._automap_marker(raw)
        stamp = self.state.stamp if self.state.tool_id == "brush" else None
        if stamp and target is not None:
            stamp = [[self._automap_marker(v) if v else 0 for v in line]
                     for line in stamp]
        # 绘制时自动地图：整笔的「绘制 + 每一格的自动映射」合成一条撤销记录。
        live = (target is not None)
        if live:
            self.state.undo_stack.beginMacro("绘制并自动映射")
            self._automap_macro = True
            self._automap_total = 0
        if stamp and (len(stamp) > 1 or len(stamp[0]) > 1):
            # 图章以「中间格」对齐光标：原点 = 光标格 - (W//2, H//2)
            w, h = len(stamp[0]), len(stamp)
            oc, orow = col - w // 2, row - h // 2
            self.state.begin_stroke(write_layer, oc, orow, raw, self._refresh_layer,
                                    stamp=stamp,
                                    on_change=self._automap_live if live else None)
        else:
            self.state.begin_stroke(write_layer, col, row, raw, self._refresh_layer,
                                    on_change=self._automap_live if live else None)

    def _on_move(self, col: int, row: int):
        # 每一格的自动映射在 AppState 的 on_change 回调里同步完成，无需等松手。
        # 多格图章以「中间格」对齐光标，需把原点左移 (W//2, H//2)。
        stamp = self.state.stamp if self.state.tool_id == "brush" else None
        if stamp and (len(stamp) > 1 or len(stamp[0]) > 1):
            w, h = len(stamp[0]), len(stamp)
            self.state.stroke_continue(col - w // 2, row - h // 2)
        else:
            self.state.stroke_continue(col, row)

    def _do_fill(self, layer, col, row):
        target = self._automap_write_target()
        if target is not None:
            layer = target                    # 填充标记层而不是渲染层
        if not layer.in_bounds(col, row):
            return
        cells = self.state.tool.fill_region(self.state, layer, col, row)
        if not cells:
            self.statusBar().showMessage("填充：起点与笔刷同色或越界，未改动")
            return
        if target is not None:
            cells = [(c, r, self._automap_marker(new)) for c, r, new in cells]
        live = (target is not None)
        if live:
            self.state.undo_stack.beginMacro("填充并自动映射")
            self._automap_macro = True
        self.state.begin_region_stroke(layer, self._refresh_layer, None, text="填充")
        for c, r, new in cells:
            self.state.write_region_cell(c, r, new)
        result = self.state.end_stroke()
        if result and self._automap_while_drawing and self._automapper is not None:
            # 填充是一次性操作，松手式收敛做一次统一重算即可。
            self._apply_automap(area=tuple(result["bounds"]), macro=False)
        if self._automap_macro:
            self.state.undo_stack.endMacro()
            self._automap_macro = False
        if result:
            self.statusBar().showMessage(
                f"填充完成：{len(cells)} 格（一条撤销可还原）")

    def _on_release(self, *_):
        result = self.state.end_stroke()
        if (result and self._automap_while_drawing and self._automapper is not None
                and result["layer"].name.lower() == "set"):
            # 逐格实时反馈之后，按整笔范围再做一次统一重算：
            # 早期格子的邻域可能被后续笔画改变，松手时收敛到与「一次性重算」相同的结果。
            # 非 set 层不重算：set 层没有任何变化，reset_base 只会把刚画的内容擦掉。
            self._automap_total += self._apply_automap(
                area=tuple(result["bounds"]), macro=False)
        if self._automap_macro:
            self.state.undo_stack.endMacro()
            self._automap_macro = False
        if result and self._automap_while_drawing:
            if self._automap_total:
                self.statusBar().showMessage(
                    f"绘制并自动映射：{self._automap_total} 格地形已实时更新（一次撤销可还原）"
                )
            else:
                self.statusBar().showMessage(
                    "自动映射无输出：请在 set 层标记（任意基础地形瓦片会自动"
                    "归一到该图集首格，如 126 草 / 186 水）"
                    f"（当前笔刷 gid={self.state.brush_gid}）"
                )

    def _on_hover(self, col: int, row: int, raw: int):
        self._hover_cell = (col, row)
        self.scene.set_cursor_cell(col, row)
        # 多格图章跟随光标的半透明预览（以中间格对齐光标）
        stamp = self.state.stamp
        if (self.state.tool_id == "brush" and stamp
                and (len(stamp) > 1 or len(stamp[0]) > 1)):
            self.scene.set_stamp_preview(stamp, col, row)
        else:
            self.scene.clear_stamp_preview()
        layer = self.state.current_layer
        name = layer.name if layer is not None else "-"
        self.statusBar().showMessage(
            f"({col}, {row})  gid={raw}  层={name}  笔刷={self.state.brush_gid}"
        )

    # ---------------- Tiled 风格「插入对象」工具 ----------------
    _TOOL_TIPS = {
        "brush": "瓦片画笔：B / E 切画笔、橡皮，I 或 Alt+左键临时吸管",
        "pen": ("钢笔：右键单击放角点 / 右键拖动放带柄锚点；左键拖锚点或控制柄、"
                "点曲线插入锚点、点线外空白完成盖章；Esc 取消，Delete 删末点"),
        "fill": "填充：把点击格所在的同色连通区域一次性替换为当前笔刷（F）",
        "select": ("选择 / 框选：点对象选中 / 拖动 / 拖箭头缩放，拖空白框选；"
                   "拖动时 Ctrl 按格移动、Ctrl+箭头吸附瓦片边、Shift+箭头等比例；"
                   "瓦片层框选可 Ctrl+C / Ctrl+V / 拖动移动（S / M）"),
        "nodes": "编辑节点：先选中多边形 / 折线，再拖动顶点",
        "rect": "插入矩形：在画布上拖出大小（不拖则 40×40）",
        "point": "插入点：单击放置",
        "ellipse": "插入椭圆：在画布上拖出大小",
        "polygon": "插入多边形：逐点单击，双击 / Ctrl+回车结束，Esc 取消",
        "polyline": "插入折线：逐点单击，双击 / Ctrl+回车结束，Esc 取消",
        "tile": "插入图块：先在下方图块集选一个图块，再拖出大小",
        "template": "插入模板：选一个 Tiled .tx 模板文件（字段会展开到对象上）",
        "image": ("插入图片：选一张本地图片；保存时切成 20×20 瓦片并内嵌进地图，"
                  "游戏内可见（对象本身写成 info 注释节点）"),
        "text": "插入文字：拖出文字框输入内容；保存时转成 mapText 触发器，游戏内显示",
    }

    def _on_object_layer_changed(self, layer):
        self._object_layer = layer
        if layer is not None:
            self.statusBar().showMessage(f"新对象将放到对象层 {layer.name}")

    def _set_object_tool(self, tid: str):
        # 「框选 M」与「选择对象 S」已合并：旧 id 一律落到同一个状态机，
        # 旧测试 / 探针调用 _set_object_tool("tileselect") 也照常工作。
        tid = canonical_tool_id(tid)
        self._cancel_object_tool()
        self._cancel_object_paste(silent=True)   # 切工具时收起吸附中的副本
        # 分段输入框只在标尺工具下出现（brush/eraser/fill 走下面的早返回分支）
        self.object_toolbar.set_ruler_controls_visible(tid == "ruler")
        if tid in ("brush", "eraser", "fill"):
            # 瓦片画笔 / 橡皮是同一套瓦片编辑模式，只是笔刷内容不同。
            self.state.tool_id = tid
            self._object_tool = "brush"
            self.view.object_mode = "brush"
            self.object_toolbar.select_tool(tid)
            self.object_toolbar.set_hint(tid)
            self.scene.set_show_object_nodes(False)
            self.view.setCursor(self.state.tool.cursor)
            self.statusBar().showMessage(
                f"工具: {self.state.tool.name}；对象默认自由摆放，Ctrl 吸附网格 / Shift 正方形")
            return
        self._object_tool = tid
        self.view.object_mode = tid
        self.object_toolbar.select_tool(tid)
        self.object_toolbar.set_hint(tid)
        self.scene.set_show_object_nodes(tid == "nodes")
        if tid == "select":
            # 深色 / 绿色地图上看不清系统白箭头，改用蓝色描边的醒目光标。
            self.view.setCursor(make_select_cursor())
        else:
            self.view.setCursor(Qt.CrossCursor)
        self.statusBar().showMessage(f"对象工具：{self._TOOL_TIPS.get(tid, tid)}")
        self._hover_resize_cursor = None

    def _target_object_layer(self):
        doc = self.state.doc
        if doc is None:
            return None
        layer = self._object_layer
        if isinstance(layer, ObjectLayer) and any(l is layer for l in doc.layers):
            return layer
        return next((l for l in doc.layers if isinstance(l, ObjectLayer)), None)

    @staticmethod
    def _object_layer_of(doc, obj):
        if doc is None or obj is None:
            return None
        uid = object_uid(obj)
        # 重复 object_id 时按稳定身份找；o is obj 保留给无 xml_element 的新建对象
        hit = next((l for l in doc.layers if isinstance(l, ObjectLayer)
                    and any(o is obj or object_uid(o) == uid
                            for o in l.objects)), None)
        if hit is not None:
            return hit
        return next((l for l in doc.layers if isinstance(l, ObjectLayer)
                     and any(o.object_id == obj.object_id for o in l.objects)), None)

    def _new_object_id(self):
        doc = self.state.doc
        return max((o.object_id for l in doc.layers
                    if isinstance(l, ObjectLayer) for o in l.objects),
                   default=0) + 1

    @staticmethod
    def _mods():
        """当前键盘修饰键（集中一处，便于 Ctrl / Shift 语义切换与测试）。"""
        return QApplication.keyboardModifiers()

    def _snap_xy(self, x, y, snap: bool | None = None):
        """默认自由坐标；按住 Ctrl（或显式 snap=True）才吸附瓦片网格角点。

        铁锈战争的触发器对象并不要求落在整格上，Tiled 也是默认自由、
        需要对齐时才按修饰键。之前无条件 round 到 20px 会让矩形四角
        永远贴在瓦片边上，看起来像被「绑住」。

        修饰键语义（本轮重做）：
        - Ctrl：插入对象时把角点吸附到瓦片网格；拖缩放箭头时也吸附，但
          走 _do_selection_resize 里「吸附目标边」的专门逻辑（不是位移取整）；
        - Shift：插入时约束为正方形、拖箭头时等比例缩放（见 _square_xy /
          _do_selection_resize）。
        """
        if snap is None:
            snap = bool(self._mods() & Qt.ControlModifier)
        if not snap:
            return x, y
        doc = self.state.doc
        tw = doc.tile_w if doc is not None else 20
        th = doc.tile_h if doc is not None else 20
        return round(x / tw) * tw, round(y / th) * th

    def _square_xy(self, x0, y0, x1, y1):
        """插入拖动终点：按住 Shift 时把矩形约束成正方形（取较大边长）。"""
        if not (self._mods() & Qt.ShiftModifier):
            return x1, y1
        side = max(abs(x1 - x0), abs(y1 - y0))
        return (x0 + side if x1 >= x0 else x0 - side,
                y0 + side if y1 >= y0 else y0 - side)

    def _commit_new_object(self, layer, obj, label):
        doc = self.state.doc
        if obj.shape == "image":
            obj.properties.set("image", obj.image_source, "file")
        before = snapshot(layer)
        layer.objects.append(obj)
        after = snapshot(layer)
        layer.dirty = True
        doc.dirty = True
        self.scene.set_selected_object(obj)
        self.state.undo_stack.push(ObjectCommand(
            doc, layer, before, after, label, self._refresh_objects))
        # push 会 redo 并替换 layer.objects，选中态按 id 重新绑定。
        current = next((o for o in layer.objects
                        if o.object_id == obj.object_id), obj)
        self.scene.set_selected_object(current)
        self.trigger_graph_dock.select_object(current)
        self.statusBar().showMessage(
            f"{label} {current.label} → 对象层 {layer.name}（Ctrl+Z 可撤销）")

    @staticmethod
    def _preview_object(kind, x0, y0, x1, y1, extra=None):
        x, y = min(x0, x1), min(y0, y1)
        w, h = max(abs(x1 - x0), 4), max(abs(y1 - y0), 4)
        values = dict(object_id=-1, name="", shape=kind,
                      x=x, y=y, width=w, height=h)
        if extra:
            values.update(extra)
        return MapObject(**values)

    def _ruler_cell(self, x: float, y: float) -> tuple[int, int] | None:
        """场景坐标 → 格坐标，越界返回 None。"""
        doc = self.state.doc
        if doc is None:
            return None
        col, row = int(x // doc.tile_w), int(y // doc.tile_h)
        return (col, row) if 0 <= col < doc.width and 0 <= row < doc.height else None

    def _sync_ruler(self):
        """把多段状态与分段数推给覆盖层。"""
        ruler = self.scene.ruler_overlay()
        if ruler is None:
            return
        ruler.set_segments(self._ruler_segments, self.object_toolbar.segments())
        ruler.set_pending(self._ruler_pending)

    def _on_ruler_segments_changed(self, *_):
        self._sync_ruler()
        if self._ruler_segments:
            self.statusBar().showMessage(
                f"标尺：分 {self.object_toolbar.segments()} 段，"
                f"共 {len(self._ruler_segments)} 段测量")

    def _on_ruler_press(self, x: float, y: float):
        cell = self._ruler_cell(x, y)
        if cell is None:
            return
        if self._ruler_pending is None:
            self._ruler_pending = cell
            self.statusBar().showMessage(f"标尺起点 {cell}，点第二格完成测量")
        else:
            seg = (*self._ruler_pending, *cell)
            self._ruler_segments.append(seg)
            self._ruler_pending = None
            from rwmap.view.ruler_overlay import cell_distance
            dist = cell_distance(*seg)
            parts = self.object_toolbar.segments()
            msg = f"标尺：{seg[0]},{seg[1]} → {seg[2]},{seg[3]}，{dist} 格（含两端）"
            if parts > 1:
                msg += f"，分 {parts} 段每段 {dist / parts:.1f} 格"
            if len(self._ruler_segments) > 1:
                msg += f" · 已标 {len(self._ruler_segments)} 段"
            self.statusBar().showMessage(msg)
        self._sync_ruler()

    def _on_object_tool_press(self, x: float, y: float):
        tid = self._object_tool
        if tid == "brush":
            return
        if tid == "ruler":
            self._on_ruler_press(x, y)
            return
        if tid == "select":
            # 合并工具要在「没有对象层」的纯瓦片地图上也能用，所以必须排在
            # _target_object_layer() 的空层拦截之前。
            self._on_select_press(x, y)
            return
        if tid == "pen":
            # 必须在 _target_object_layer() 之前：钢笔只写瓦片层，
            # 没有 ObjectLayer 的地图同样要能画。
            self._pen_press(x, y)
            return
        layer = self._target_object_layer()
        if layer is None:
            self.statusBar().showMessage("当前地图没有对象层，无法插入对象")
            return
        sx, sy = self._snap_xy(x, y)
        if tid == "nodes":
            self._node_press(x, y)
            return
        if tid in ("polygon", "polyline"):
            pending = self._object_pending
            if pending and pending["kind"] == "poly" and pending["tool"] == tid:
                pending["points"].append((sx, sy))
            else:
                pending = {"kind": "poly", "tool": tid, "layer": layer,
                           "points": [(sx, sy)]}
                self._object_pending = pending
            self._poly_preview(pending, sx, sy)
            self.statusBar().showMessage(
                f"{SHAPE_LABELS[tid]}：已放 {len(pending['points'])} 个点，"
                "双击 / Ctrl+回车结束，Esc 取消")
            return
        if tid == "point":
            obj = MapObject(object_id=self._new_object_id(),
                            name=SHAPE_LABELS["point"], shape="point",
                            x=sx, y=sy)
            self._commit_new_object(layer, obj, "插入点")
            return
        self._object_pending = {"kind": "drag", "tool": tid, "layer": layer,
                                "x0": sx, "y0": sy, "extra": {}}
        self.scene.set_object_preview(self._preview_object(tid, sx, sy, sx, sy))

    def _poly_preview(self, pending, hover_x=None, hover_y=None):
        pts = list(pending["points"])
        if hover_x is not None and (hover_x, hover_y) != pts[-1]:
            pts.append((hover_x, hover_y))
        x0, y0 = pts[0]
        self.scene.set_object_preview(MapObject(
            object_id=-1, name="", shape=pending["tool"], x=x0, y=y0,
            points=[(px - x0, py - y0) for px, py in pts]))

    def _on_object_tool_move(self, x: float, y: float):
        # 标尺是纯点击式：移动鼠标不画预览线。点第一格只留一个点，点第二格
        # 才连线；否则第三点会被误当成"线又挪位置"。
        if self._object_tool == "ruler":
            return
        if self._sel_resize_drag is not None:
            self._do_selection_resize(x, y)
            return
        if self._sel_move_drag is not None:
            self._do_selection_move(x, y)
            return
        if self._object_tool == "select":
            self._on_select_move(x, y)
            return
        if self._object_tool == "pen":
            self._pen_move(x, y)
            return
        pending = self._object_pending
        if pending is None:
            return
        if pending["kind"] == "node":
            self._node_move(x, y)
            return
        sx, sy = self._snap_xy(x, y)
        if pending["kind"] == "poly":
            self._poly_preview(pending, sx, sy)
        elif pending["kind"] == "drag":
            px, py = self._square_xy(pending["x0"], pending["y0"], sx, sy)
            self.scene.set_object_preview(self._preview_object(
                pending["tool"], pending["x0"], pending["y0"], px, py,
                pending.get("extra")))

    def _on_object_tool_release(self, x: float, y: float):
        if self._sel_resize_drag is not None:
            self._end_selection_resize(x, y)
            return
        if self._sel_move_drag is not None:
            self._end_selection_move(x, y)
            return
        if self._object_tool == "select":
            self._on_select_release(x, y)
            return
        if self._object_tool == "pen":
            self._pen_release(x, y)
            return
        pending = self._object_pending
        if pending is None:
            return
        if pending["kind"] == "poly":
            return                      # 等双击 / 回车
        if pending["kind"] == "node":
            self._node_release()
            return
        self._finish_drag_object(x, y)

    def _finish_drag_object(self, x: float, y: float):
        pending = self._object_pending
        self._object_pending = None
        self.scene.set_object_preview(None)
        kind = pending["tool"]
        layer = pending["layer"]
        doc = self.state.doc
        x0, y0 = pending["x0"], pending["y0"]
        sx, sy = self._snap_xy(x, y)
        sx, sy = self._square_xy(x0, y0, sx, sy)
        left, top = min(x0, sx), min(y0, sy)
        w, h = abs(sx - x0), abs(sy - y0)
        defaults = {
            "rect": (40, 40), "ellipse": (40, 40), "tile": (20, 20),
            "text": (160, 40), "image": (80, 60),
        }
        if kind == "tile":
            defaults["tile"] = (doc.tile_w, doc.tile_h)
        if w < 4 or h < 4:
            w, h = defaults.get(kind, (40, 40))
        extra = dict(pending.get("extra") or {})

        if kind == "tile":
            gid = self.state.brush_gid & GID_MASK
            if not gid:
                self.statusBar().showMessage("先在下方图块集选一个图块，再插入图块对象")
                return
            extra["gid"] = gid
        elif kind == "text":
            text, ok = QInputDialog.getMultiLineText(
                self, "文字对象", "内容：", "文字")
            if not ok:
                return
            extra["text"] = text or "文字"
            extra["text_style"] = {
                "fontfamily": "sans-serif", "pixelsize": "16", "wrap": "1",
                "color": "#000000", "bold": "1",
                "halign": "center", "valign": "center",
            }
        elif kind == "template":
            start = str(doc.path.parent) if doc is not None else ""
            path, _ = QFileDialog.getOpenFileName(
                self, "选择 Tiled 模板", start, "Tiled 模板 (*.tx)")
            if not path:
                return
            try:
                ref = os.path.relpath(path, start).replace("\\", "/")
            except ValueError:
                ref = Path(path).as_posix()
            tpl = read_template_object(path, self._new_object_id(),
                                       x0, y0, ref)
            if tpl is None:
                self.statusBar().showMessage(f"模板文件 {path} 里没有 <object> 节点")
                return
            tpl.name = tpl.name or "模板"
            if tpl.width <= 0:
                tpl.width = doc.tile_w
            if tpl.height <= 0:
                tpl.height = doc.tile_h
            self._commit_new_object(layer, tpl, "插入模板")
            return
        elif kind == "image":
            start = str(doc.path.parent) if doc is not None else ""
            path, _ = QFileDialog.getOpenFileName(
                self, "选择图片对象", start,
                "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
            if not path:
                return
            try:
                rel = os.path.relpath(path, start).replace("\\", "/")
            except ValueError:
                rel = Path(path).as_posix()
            pix = QPixmap(path)
            if pix.isNull():
                self.statusBar().showMessage(f"无法读取图片 {path}")
                return
            extra["image_source"] = rel
            if w == defaults["image"][0] and h == defaults["image"][1]:
                w, h = pix.width(), pix.height()
                scale = min(1.0, 240.0 / max(w, h, 1))
                w, h = round(w * scale), round(h * scale)
                left, top = x0, y0
        obj = MapObject(object_id=self._new_object_id(),
                        name=SHAPE_LABELS.get(kind, kind), shape=kind,
                        x=left, y=top, width=w, height=h, **extra)
        # 文字在游戏里是 mapText 触发器的 text 属性；插入时就把内容同步进属性包，
        # 之后无论从「文字内容」字段还是属性面板改，写盘都能拿到最新值。
        if kind == "text":
            obj.properties.set("text", obj.text)
        self._commit_new_object(layer, obj,
                                f"插入{SHAPE_LABELS.get(kind, kind)}")

    def _on_object_tool_double_click(self, x: float, y: float):
        if self._object_tool == "pen":
            # 双击前会先收到一次 press+release；空白完成通常已在 release 触发，
            # 这里只作备用入口，路径已清空时直接忽略，避免刷无意义的状态栏提示。
            if self._pen_path is not None:
                self._pen_finish()
            return
        if self._object_pending and self._object_pending["kind"] == "poly":
            self._finish_poly()

    # ---------------- 钢笔 / 贝塞尔矢量曲线 ----------------
    # 拖动阈值用「屏幕像素 / 视图缩放」，与 map_view 的 _rpen_drag 判定同源。
    _PEN_DRAG_PX = 4.0

    def _pen_scale(self):
        return max(abs(self.view.transform().m11()) or 1.0, 0.05)

    def _pen_tol(self):
        doc = self.state.doc
        tw = doc.tile_w if doc is not None else 20
        th = doc.tile_h if doc is not None else 20
        return min(tw, th) / 8.0

    def _pen_threshold(self):
        return self._PEN_DRAG_PX / self._pen_scale()

    def _pen_refresh(self, active=None):
        self.scene.set_pen_path(self._pen_path, active)

    def _pen_ensure_path(self):
        if self._pen_path is None:
            self._pen_path = PenPath()
        return self._pen_path

    # ---- 右键：单击放角点 / 拖动放带对称柄的锚点 ----
    def _pen_right_press(self, x: float, y: float):
        if self._object_tool != "pen" or self.state.doc is None:
            return
        path = self._pen_ensure_path()
        path.anchors.append(Anchor.corner(x, y))    # 临时锚点，松开时定形
        self._pen_rpress = {"start": (x, y), "last": (x, y),
                            "index": len(path.anchors) - 1}
        self._pen_refresh(("anchor", self._pen_rpress["index"]))

    def _pen_right_move(self, x: float, y: float):
        if self._object_tool != "pen" or self._pen_rpress is None:
            return
        info = self._pen_rpress
        info["last"] = (x, y)
        dx, dy = x - info["start"][0], y - info["start"][1]
        anchor = self._pen_path.anchors[info["index"]]
        if (dx * dx + dy * dy) ** 0.5 > self._pen_threshold():
            anchor.set_symmetric(dx, dy)
        else:
            anchor.set_symmetric(0.0, 0.0)
        self._pen_refresh(("anchor", info["index"]))

    def _pen_right_release(self, x: float, y: float):
        if self._object_tool != "pen" or self._pen_rpress is None:
            return
        info = self._pen_rpress
        dx, dy = x - info["start"][0], y - info["start"][1]
        anchor = self._pen_path.anchors[info["index"]]
        # map_view 在拖动过时会补发一次 RightMoved(release 坐标)，
        # 所以这里的 x,y 一定等于松开位置，柄向量 = 松开 - 按下。
        if (dx * dx + dy * dy) ** 0.5 > self._pen_threshold():
            anchor.set_symmetric(dx, dy)
        else:
            anchor.set_symmetric(0.0, 0.0)       # 右键单击 → 无柄角点
        self._pen_rpress = None
        self._pen_refresh()
        self.statusBar().showMessage(
            f"钢笔：已放 {len(self._pen_path.anchors)} 个锚点；"
            "继续右键加点，左键拖点调节 / 点曲线插点 / 点线外空白完成")

    # ---- 左键：命中判定 → 调节 / 插点 / 完成 ----
    def _pen_hit(self, x: float, y: float):
        """返回 (距离, 类型, 索引, t)；类型 ∈ anchor/in/out/curve；空白返回 None。"""
        path = self._pen_path
        if path is None or not path.anchors:
            return None
        s = self._pen_scale()
        r_anchor, r_handle, r_curve = 7.0 / s, 7.0 / s, 6.0 / s
        best = None
        for i, a in enumerate(path.anchors):
            corner = a.hin == (0.0, 0.0) and a.hout == (0.0, 0.0)
            candidates = [("anchor", a.pos(), r_anchor)]
            if not corner:
                candidates += [("in", a.in_pt(), r_handle),
                               ("out", a.out_pt(), r_handle)]
            for kind, hp, radius in candidates:
                d = ((x - hp[0]) ** 2 + (y - hp[1]) ** 2) ** 0.5
                if d <= radius and (best is None or d < best[0]):
                    best = (d, kind, i, None)
        if best is not None:
            return best
        hit = nearest_on_curve((x, y), path.segments(), self._pen_tol())
        if hit is not None and hit[0] <= r_curve:
            return (hit[0], "curve", hit[1], hit[2])
        return None

    def _pen_insert_on_curve(self, seg_index: int, t: float):
        """在曲线上插锚点：切线方向生成一对**严格对称**的柄。

        de Casteljau 得到的 D/F/E 三点共线，取 h=(E-D)/2 令 hin=-h/hout=h。
        用户确认「全程严格对称」，因此不采用左右精确控制点（那会不对称），
        代价是插入瞬间曲线有极小的局部形变。两端锚点的柄保持不变。
        """
        seg = self._pen_path.segments()[seg_index]
        left, right = split_segment(seg, t)
        h = tangent_handle(left, right)
        f = left[3]
        new = Anchor(f[0], f[1])
        new.set_symmetric(h[0], h[1])
        self._pen_path.anchors.insert(seg_index + 1, new)

    def _pen_press(self, x: float, y: float):
        self._pen_finish_on_release = False
        if self.state.doc is None:
            return
        hit = self._pen_hit(x, y)
        if hit is None:
            self._pen_drag = None
            self._pen_finish_on_release = True     # 点在「线之外的空白区」
            return
        _dist, kind, i, t = hit
        if kind == "curve":
            self._pen_insert_on_curve(i, t)
            self._pen_drag = None
            self._pen_refresh()
            self.statusBar().showMessage("钢笔：已在曲线上插入一个锚点")
            return
        self._pen_drag = (kind, i)
        self._pen_refresh(self._pen_drag)

    def _pen_move(self, x: float, y: float):
        drag = self._pen_drag
        if drag is None or self._pen_path is None:
            return
        kind, i = drag
        a = self._pen_path.anchors[i]
        if kind == "anchor":
            a.x, a.y = x, y                       # 柄随锚点整体平移
        elif kind == "out":
            a.set_out(x - a.x, y - a.y)           # in 柄镜像
        else:
            a.set_in(x - a.x, y - a.y)            # out 柄镜像
        self._pen_refresh(drag)

    def _pen_release(self, x: float, y: float):
        if self._pen_drag is not None:
            self._pen_drag = None
            self._pen_refresh()
            return
        if self._pen_finish_on_release:
            self._pen_finish_on_release = False
            self._pen_finish()

    # ---- 沿曲线采样盖章（整条线一条 DrawCommand、一次撤销） ----
    def _pen_curve_cells(self):
        """曲线栅格化后的格序列（保序）。落笔与双向遍历共用同一份序列。"""
        doc = self.state.doc
        pts = self._pen_path.flatten(self._pen_tol())
        return rasterize_polyline_to_cells(pts, doc.tile_w, doc.tile_h)

    def _pen_stamp_cells(self, curve_cells=None):
        doc = self.state.doc
        cells = self._pen_curve_cells() if curve_cells is None else curve_cells
        # stamp 只在 tool_id=="brush" 时被 _on_press/_on_move 自动采用，
        # 钢笔必须显式读取。
        stamp = self.state.stamp
        has_stamp = bool(stamp) and (len(stamp) > 1 or len(stamp[0]) > 1)
        final = {}
        for col, row in cells:                    # 保序 → 结果确定、可单测
            if has_stamp:
                w, h = len(stamp[0]), len(stamp)
                oc, orow = col - w // 2, row - h // 2   # 中间格对齐
                for dy, line in enumerate(stamp):
                    for dx, raw in enumerate(line):
                        if not raw:               # 0 = 跳过，不清空已有瓦片
                            continue
                        key = (oc + dx, orow + dy)
                        if key not in final:
                            final[key] = int(raw) & 0xFFFFFFFF
            else:
                key = (col, row)
                if key not in final:
                    final[key] = int(self.state.brush_gid) & 0xFFFFFFFF
        return final

    def _pen_repair_markers(self, final, curve_cells):
        """沿曲线正反两遍遍历，修补 set 层的「非地形标记空洞」。

        官方 rules.txt 只认各基础图集首格。上一版把归一失败（-1，即过渡
        图集 / units / misc 瓦片）的格直接跳过，等于在标记层挖洞：探针实测
        8 格草线中间挖 2 格洞，过渡瓦片从 22 掉到 18，表现为「一段边界
        有过渡、相邻一段硬切」。

        正向（起点→终点）用左侧最近的有效标记补洞，反向（终点→起点）用
        右侧最近的有效标记补洞；两遍都给出候选时取距离更近的一侧，避免
        整段被一端的值覆盖。整条线都没有有效标记时才真正跳过。

        返回 (修补后的 final, 被补齐的格数, 仍跳过的格数)。
        """
        doc = self.state.doc
        if not final:
            return final, 0, 0

        # 把图章展开的格映射到曲线序列上最近的一站，得到「沿曲线」的遍历序。
        order = {}
        for key in final:
            best_i, best_d = 0, None
            for i, (c, r) in enumerate(curve_cells):
                d = abs(key[0] - c) + abs(key[1] - r)
                if best_d is None or d < best_d:
                    best_i, best_d = i, d
            order[key] = best_i
        keys = sorted(final, key=lambda k: (order[k], k[1], k[0]))

        fwd, fwd_dist = {}, {}
        last, last_i = None, None
        for key in keys:                       # 起点 → 终点
            gid = self._normalize_marker(final[key])
            if gid >= 0:
                last, last_i = gid & 0xFFFFFFFF, order[key]
            elif last is not None:
                fwd[key] = last
                fwd_dist[key] = order[key] - last_i

        rev, rev_dist = {}, {}
        nxt, nxt_i = None, None
        for key in reversed(keys):             # 终点 → 起点
            gid = self._normalize_marker(final[key])
            if gid >= 0:
                nxt, nxt_i = gid & 0xFFFFFFFF, order[key]
            elif nxt is not None:
                rev[key] = nxt
                rev_dist[key] = nxt_i - order[key]

        repaired, filled, skipped = {}, 0, 0
        for key, raw in final.items():
            gid = self._normalize_marker(raw)
            if gid >= 0:
                repaired[key] = gid & 0xFFFFFFFF
            elif key in fwd and (key not in rev
                                or fwd_dist[key] <= rev_dist[key]):
                repaired[key] = fwd[key]       # 正向补洞更近
                filled += 1
            elif key in rev:
                repaired[key] = rev[key]       # 反向补洞更近
                filled += 1
            else:
                skipped += 1                   # 整条线没有有效地形标记
        return repaired, filled, skipped

    @staticmethod
    def _pen_sweep_bounds(curve_cells, bounds):
        """把自动映射的重算范围从「值变化的格」扩展到整条曲线。

        _pen_finish 的 bounds 只统计 old != new 的格。若线大部分画在已有
        相同地形上，bounds 会只覆盖一小段，曲线经过的其它边界段得不到重算
        → 一段有过渡、一段没有。正反两遍沿曲线收集格（起点→终点 /
        终点→起点）并取并集，保证任意走向都不漏段。图章展开的格已在
        bounds 里，并集后原范围只增不减。
        """
        if not curve_cells:
            return bounds
        boxes = []
        for series in (curve_cells, list(reversed(curve_cells))):
            boxes.append((min(c for c, _ in series), min(r for _, r in series),
                          max(c for c, _ in series), max(r for _, r in series)))
        c0 = min(b[0] for b in boxes)
        r0 = min(b[1] for b in boxes)
        c1 = max(b[2] for b in boxes)
        r1 = max(b[3] for b in boxes)
        if bounds is None:
            return (c0, r0, c1, r1)
        return (min(bounds[0], c0), min(bounds[1], r0),
                max(bounds[2], c1), max(bounds[3], r1))

    def _pen_finish(self):
        path = self._pen_path
        if path is None or len(path.anchors) < 2:
            self.statusBar().showMessage("钢笔：至少需要 2 个锚点才能完成")
            return
        if not self.state.can_edit():
            self.statusBar().showMessage("当前层只读或版本只读，无法落笔")
            return
        layer = self.state.current_layer
        target = self._automap_write_target()
        write_layer = target if target is not None else layer
        if not isinstance(write_layer, TileLayer):
            self.statusBar().showMessage("钢笔：当前层不是瓦片层，无法落笔")
            return
        curve_cells = self._pen_curve_cells()   # 必须在 _pen_cancel() 之前取
        final = self._pen_stamp_cells(curve_cells)
        filled = 0
        unmatched = 0
        if target is not None:
            # set 层只认各基础图集首格：把「图集里随便点的中间格」和
            # 「从 Ground 抓来的渲染图章」都归一。过渡 / 非地形瓦片不再直接
            # 丢弃（那会在标记层挖洞、让那一段边界硬切），而是沿曲线正反
            # 两遍遍历，用最近的有效基础地形标记补齐。
            final, filled, unmatched = self._pen_repair_markers(final, curve_cells)
        changes = []
        bounds = None                         # 本次真正改动的格范围，供局部自动映射
        for (c, r), new in final.items():
            if not write_layer.in_bounds(c, r):
                continue
            idx = write_layer.index(c, r)
            old = write_layer.cells[idx]
            if old != new:
                changes.append((idx, old, new))
                bounds = (c, r, c, r) if bounds is None else (
                    min(bounds[0], c), min(bounds[1], r),
                    max(bounds[2], c), max(bounds[3], r))
        auto_area = self._pen_sweep_bounds(curve_cells, bounds)
        self._pen_cancel()                        # 清空路径、保留钢笔工具
        if not changes:
            self.statusBar().showMessage(
                f"钢笔线：曲线未覆盖任何新格（{unmatched} 格无可用标记已跳过）"
                if unmatched else "钢笔线：曲线未覆盖任何新格")
            return

        # 「绘制时自动地图」：钢笔没有逐格笔划（曲线只是预览，落笔是
        # 一次性批量写入），故在 push 之后按整条线的改动范围做一次局部
        # 重算，并与钢笔线合成同一条撤销记录（与画笔 / 填充一致的语义）。
        live = (target is not None)
        if live:
            self.state.undo_stack.beginMacro("钢笔线并自动映射")
        auto_total = 0
        try:
            cmd = DrawCommand(self.state.doc, write_layer, changes,
                              self._refresh_layer, text="钢笔线")
            self.state.undo_stack.push(cmd)       # 一次 push = 一次撤销单元
            if live:
                auto_total = self._apply_automap(area=auto_area, macro=False)
        finally:
            if live:
                self.state.undo_stack.endMacro()
        hint = ""
        if filled:
            hint += f"；{filled} 格非地形标记已双向补齐"
        if unmatched:
            hint += f"；{unmatched} 格无可用标记已跳过"
        if live and auto_total:
            self.statusBar().showMessage(
                f"钢笔线并自动映射：{len(changes)} 格标记 + "
                f"{auto_total} 格地形已更新（一次撤销可全部还原）{hint}")
        elif live:
            self.statusBar().showMessage(
                f"钢笔线：{len(changes)} 格；自动映射无输出——"
                f"请选基础地形瓦片并确认 set 层可写{hint}")
        else:
            self.statusBar().showMessage(
                f"钢笔线：{len(changes)} 格（一次撤销可全部还原）")

    def _pen_cancel(self):
        self._pen_path = None
        self._pen_drag = None
        self._pen_rpress = None
        self._pen_finish_on_release = False
        if hasattr(self, "scene") and self.scene is not None:
            self.scene.clear_pen_preview()

    def _pen_delete_last(self):
        if self._pen_path and self._pen_path.anchors:
            self._pen_path.anchors.pop()
            if not self._pen_path.anchors:
                self._pen_cancel()
            else:
                self._pen_refresh()

    # ---------------- 瓦片框选 / 复制 / 粘贴 / 移动 ----------------
    def _norm_rect(self, p0, p1):
        x0, y0 = min(p0[0], p1[0]), min(p0[1], p1[1])
        x1, y1 = max(p0[0], p1[0]), max(p0[1], p1[1])
        return (x0, y0, x1, y1)

    def _rect_to_cells(self, rect):
        doc = self.state.doc
        scale = abs(self.view.transform().m11()) or 1.0
        # 视口坐标先被取整再映射回场景：留 1 屏幕像素的容差，
        # 否则缩放到 126% 后拖到格线上会多选一行 / 一列。
        eps = min(1.0 / scale + 1e-6, 0.4 * min(doc.tile_w, doc.tile_h))
        col0, row0, cols, rows = cells_from_rect(
            rect, doc.tile_w, doc.tile_h, boundary_eps=eps)
        # 选区必须落在地图内：从地图外拖框时 col0/row0 可能是负数，右/下
        # 边界也可能超出地图。不钳制的话，_read_selection 的 layer.get()
        # 正越界会 IndexError（崩溃）、负索引会静默绕回表尾（读错瓦片）。
        # 在源头钳一次，复制 / 移动 / 粘贴 / 选框显示全都跟着安全。
        c1 = min(col0 + cols, doc.width)
        r1 = min(row0 + rows, doc.height)
        col0, row0 = max(col0, 0), max(row0, 0)
        cols, rows = max(c1 - col0, 0), max(r1 - row0, 0)
        if cols <= 0 or rows <= 0:
            # 整框都在地图外：退化为地图角落的 1 格，不产生空/负选区。
            col0 = min(col0, max(doc.width - 1, 0))
            row0 = min(row0, max(doc.height - 1, 0))
            cols = rows = 1
        return (col0, row0, cols, rows)

    def _cells_to_rect(self, sel):
        doc = self.state.doc
        col0, row0, cols, rows = sel
        return (col0 * doc.tile_w, row0 * doc.tile_h,
                (col0 + cols) * doc.tile_w, (row0 + rows) * doc.tile_h)

    def _in_selection(self, x, y):
        if not self._sel_rect:
            return False
        x0, y0, x1, y1 = self._sel_rect
        return x0 <= x <= x1 and y0 <= y <= y1

    def _selection_snapshot(self):
        """当前选区状态快照 ``(tile_selection, sel_rect)``。

        ``sel_rect`` 一律由格坐标重算：拖动过程中的显示框只是预览，
        可能已经跑到目标位置，直接拿来当「移动前」会记错。
        """
        sel = self._tile_selection
        rect = self._cells_to_rect(sel) if sel else None
        return (sel, rect)

    def _apply_selection_snapshot(self, snapshot):
        """把选区快照同步回内部状态与画布选框（撤销 / 重做用）。"""
        sel, rect = snapshot
        self._tile_selection = sel
        self._sel_rect = rect
        self.scene.set_selection_rect(rect)

    def _sel_move_cells(self, x, y):
        """「按下点 → 当前点」的总位移 → 整格偏移 (dc, dr)。

        只以 ``_sel_move_start`` 为基准，与中间收到多少次 mouseMove 无关；
        预览与最终写入共用这个结果，保证「看到移 1 格」就是「数据移 1 格」。
        同时把选区钳制在地图范围内，避免整块内容被清空却写不进目标格。
        """
        doc = self.state.doc
        if doc is None or not self._tile_selection:
            return 0, 0
        dx = x - self._sel_move_start[0]
        dy = y - self._sel_move_start[1]
        dc = int(round(dx / doc.tile_w))
        dr = int(round(dy / doc.tile_h))
        col0, row0, cols, rows = self._tile_selection
        lo_c, hi_c = -col0, doc.width - col0 - cols
        lo_r, hi_r = -row0, doc.height - row0 - rows
        if hi_c >= lo_c:
            dc = max(lo_c, min(hi_c, dc))
        if hi_r >= lo_r:
            dr = max(lo_r, min(hi_r, dr))
        return dc, dr

    def _read_selection(self, layer, sel):
        col0, row0, cols, rows = sel
        # 与 _capture_cells 同样的边界兜底：TileLayer.cells 是定长一维表，
        # get() 不查边界，负索引绕回表尾、正越界 IndexError。选区已在
        # _rect_to_cells 钳制，这里再兜一层，防历史文档 / 程序化设置的越界 sel。
        return [[layer.get(col0 + c, row0 + r)
                 if layer.in_bounds(col0 + c, row0 + r) else 0
                 for c in range(cols)]
                for r in range(rows)]

    def _compute_move_changes(self, layer, sel, dc, dr):
        col0, row0, cols, rows = sel
        content = self._read_selection(layer, sel)
        orig = {(col0 + c, row0 + r) for c in range(cols) for r in range(rows)}
        newpos = {(col0 + c + dc, row0 + r + dr) for c in range(cols) for r in range(rows)}
        final = {}
        for (nc, nr) in newpos:
            c = nc - dc - col0
            r = nr - dr - row0
            final[(nc, nr)] = content[r][c]
        for (oc, or_) in orig:
            if (oc, or_) not in newpos:
                final[(oc, or_)] = 0
        changes = []
        for (c, r), new in final.items():
            if not layer.in_bounds(c, r):
                continue
            idx = layer.index(c, r)
            old = layer.cells[idx]
            if old != new:
                changes.append((idx, old, new))
        return changes

    def _on_tile_select_press(self, x, y):
        if not self.state.can_edit():
            self.statusBar().showMessage("当前层只读或版本只读，无法选择 / 编辑")
            return
        if self._tile_selection and self._in_selection(x, y):
            self._sel_moving = True
            self._sel_move_start = (x, y)
            # 规整到格线，保证拖动前的显示框与实际选区一致。
            self._sel_rect = self._cells_to_rect(self._tile_selection)
            self.scene.set_selection_rect(self._sel_rect)
        else:
            self._sel_dragging = True
            self._sel_start = (x, y)

    def _on_tile_select_move(self, x, y):
        if self._sel_dragging:
            self._sel_rect = self._norm_rect(self._sel_start, (x, y))
            self.scene.set_selection_rect(self._sel_rect)
        elif self._sel_moving:
            # 按格吸附预览：dc/dr 由「按下点 → 当前点」一次性算出，
            # 绝不基于上一次的选框继续累加。
            dc, dr = self._sel_move_cells(x, y)
            col0, row0, cols, rows = self._tile_selection
            self._sel_rect = self._cells_to_rect(
                (col0 + dc, row0 + dr, cols, rows))
            self.scene.set_selection_rect(self._sel_rect)

    def _on_tile_select_release(self, x, y):
        if self._sel_dragging:
            self._sel_dragging = False
            rect = self._norm_rect(self._sel_start, (x, y))
            if rect[2] - rect[0] < 2 or rect[3] - rect[1] < 2:
                self._tile_selection = None
                self._sel_rect = None
                self.scene.set_selection_rect(None)
                self.statusBar().showMessage("已取消瓦片选区")
                return
            self._tile_selection = self._rect_to_cells(rect)
            # 显示框与实际选区统一吸附到格线，避免「看起来 3×2、实际 4×2」。
            self._sel_rect = self._cells_to_rect(self._tile_selection)
            self.scene.set_selection_rect(self._sel_rect)
            # 当前在瓦片层：对象对鼠标透明，拖框只框瓦片、绝不选中对象。
            # （要框对象请先切到对象层；Triggers 层拖框仍会同时框到瓦片。）
            self.scene.set_selected_objects([])
            self.statusBar().showMessage(
                f"已框选 {self._tile_selection[2]}×{self._tile_selection[3]} 区域；"
                "Ctrl+C 复制 / Ctrl+V 粘贴 / 拖动选框移动")
        elif self._sel_moving:
            self._sel_moving = False
            dc, dr = self._sel_move_cells(x, y)
            self._move_selection(dc, dr)
            # 只读 / 无变更 / 被钳制时也把选框拉回与实际选区一致的位置。
            if self._tile_selection:
                self._sel_rect = self._cells_to_rect(self._tile_selection)
                self.scene.set_selection_rect(self._sel_rect)

    # ---------------- 合并工具：选择 / 框选（S / M，一个鼠标图标） ----------------
    def _on_select_press(self, x: float, y: float):
        """按下时的优先级：缩放箭头 → 已选对象整组拖动 → 点中对象 → 空白框选。

        空白处的框选目标由「图层面板当前层」决定：对象层框选对象，瓦片层
        （或纯瓦片地图）框选瓦片。这样原 S 的「点一下对象就选中 + 8 箭头」
        和原 M 的「拖空白框出一片瓦片 / 多个对象」都在同一个工具里。
        """
        handle = self._selection_handle_at(x, y)
        if handle is not None:
            self._begin_selection_resize(x, y, handle)
            return
        box = self._obj_selection_box()
        if self.scene.selected_objects and box is not None and box.contains(x, y):
            self._begin_selection_move(x, y)
            return
        # _hit_object 内置守卫：当前在瓦片层（如 Ground）时对象透明，这里
        # 只会命中对象层的对象。选中对象时清掉可能残留的瓦片选区。
        _layer, obj = self._hit_object(x, y)
        if obj is not None:
            self._select_object_with_group(obj)
            self._begin_selection_move(x, y)
            if self._tile_selection:
                self._clear_tile_selection()
            return
        if isinstance(self._active_layer, ObjectLayer):
            self._obj_marquee = True
            self._sel_start = (x, y)
            self._sel_rect = (x, y, x, y)
            self.scene.set_selection_rect(self._sel_rect)
        else:
            self._on_tile_select_press(x, y)

    def _on_select_move(self, x: float, y: float):
        if self._sel_resize_drag is not None:
            self._do_selection_resize(x, y)
        elif self._sel_move_drag is not None:
            self._do_selection_move(x, y)
        elif self._obj_marquee:
            self._on_object_marquee_move(x, y)
        else:
            self._on_tile_select_move(x, y)

    def _on_select_release(self, x: float, y: float):
        if self._sel_resize_drag is not None:
            self._end_selection_resize(x, y)
        elif self._sel_move_drag is not None:
            self._end_selection_move(x, y)
        elif self._obj_marquee:
            self._on_object_marquee_release(x, y)
        else:
            self._on_tile_select_release(x, y)

    # ---------------- 对象层框选（合并工具在对象层拖空白时） ----------------
    def _on_object_marquee_press(self, x, y):
        """兼容入口：直接调用时等价于合并工具在对象层按下。"""
        self._on_select_press(x, y)

    def _on_object_marquee_move(self, x, y):
        if not self._obj_marquee:
            return
        self._sel_rect = self._norm_rect(self._sel_start, (x, y))
        self.scene.set_selection_rect(self._sel_rect)

    def _visible_objects_in_rect(self, rect: QRectF):
        """收集所有**可见对象层**里与 rect 相交的对象（跨层选择用）。

        选择 / 框选不应受「当前激活层」限制：点中对象（`_hit_object`）早已跨
        所有对象层，框选也要对齐同一语义。瓦片层本身不参与，只贡献可见性过滤。
        """
        if not self._objects_interactive():
            return []
        doc = self.state.doc
        if doc is None:
            return []
        out = []
        for layer in doc.layers:
            if not isinstance(layer, ObjectLayer) or not layer.visible:
                continue
            for o in layer.objects:
                if getattr(o, "visible", True) and self._object_rect(o).intersects(rect):
                    out.append(o)
        return out

    def _on_object_marquee_release(self, x, y):
        if not self._obj_marquee:
            return
        self._obj_marquee = False
        layer = self._active_layer
        if not isinstance(layer, ObjectLayer):
            self._sel_rect = None
            self.scene.set_selection_rect(None)
            return
        rect = self._norm_rect(self._sel_start, (x, y))
        # 极小的拖拽视为一次点击：选中指针下的单个对象（没有则清空）。
        if rect[2] - rect[0] < 3 and rect[3] - rect[1] < 3:
            _layer, obj = self._hit_object(x, y)
            self.scene.set_selected_objects([obj] if obj is not None else [])
            self._sel_rect = None
            self.scene.set_selection_rect(None)
            self._after_marquee_select([obj] if obj is not None else [], layer)
            return
        # 矩形框选：选中框内**所有可见对象层**的对象（已不受当前层限制）。
        sel_box = QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
        hits = self._visible_objects_in_rect(sel_box)
        self.scene.set_selected_objects(hits)
        # 对象层只选对象、不碰瓦片选区（用户要求：在对象层不能选瓦片）。
        # 蓝色虚线选框按命中对象数决定：
        #   0 个 / 1 个 → 选框消失；
        #   ≥2 个      → 选框贴合所有命中对象的外接矩形（不吸附格线，
        #                刚好包住对象本身）。
        if len(hits) >= 2:
            box = self._obj_selection_box()
            if box is not None:
                self._sel_rect = (box.left(), box.top(), box.right(), box.bottom())
                self.scene.set_selection_rect(self._sel_rect)
            else:
                self._sel_rect = None
                self.scene.set_selection_rect(None)
        else:
            self._sel_rect = None
            self.scene.set_selection_rect(None)
        self._after_marquee_select(hits, layer)

    def _after_marquee_select(self, objs, layer):
        if not objs:
            self.statusBar().showMessage("对象层框选：未选中任何对象")
            return
        self.trigger_graph_dock.select_objects(objs)
        names = "、".join(o.label for o in objs[:5])
        more = f" 等 {len(objs)} 个" if len(objs) > 5 else ""
        self.statusBar().showMessage(
            f"已框选 {len(objs)} 个对象：{names}{more}（可拖动 / 拖箭头缩放 / Delete 删除）")

    def _copy_selection(self):
        if not self._tile_selection:
            self.statusBar().showMessage("没有瓦片选区可复制：先用「选择 / 框选」拖出选区")
            return
        layer = self.state.current_layer
        if layer is None:
            return
        self._tile_clipboard = self._read_selection(layer, self._tile_selection)
        r = len(self._tile_clipboard)
        c = len(self._tile_clipboard[0]) if r else 0
        self.statusBar().showMessage(f"已复制 {r}×{c} 瓦片，Ctrl+V 粘贴")

    def _paste_selection(self):
        if not self._tile_clipboard:
            self.statusBar().showMessage("剪贴板为空：先框选并按 Ctrl+C 复制")
            return
        layer = self.state.current_layer
        if not self.state.can_edit():
            self.statusBar().showMessage("当前层只读或版本只读，无法粘贴")
            return
        if self._hover_cell and self._hover_cell != (-1, -1):
            col0, row0 = self._hover_cell
        elif self._tile_selection:
            col0, row0 = self._tile_selection[0], self._tile_selection[1]
        else:
            col0, row0 = 0, 0
        rows = len(self._tile_clipboard)
        cols = len(self._tile_clipboard[0])
        changes = []
        for r in range(rows):
            for c in range(cols):
                cc, rr = col0 + c, row0 + r
                if not layer.in_bounds(cc, rr):
                    continue
                new = int(self._tile_clipboard[r][c]) & 0xFFFFFFFF
                idx = layer.index(cc, rr)
                old = layer.cells[idx]
                if old != new:
                    changes.append((idx, old, new))
        if not changes:
            self.statusBar().showMessage("粘贴：目标区域与剪贴板内容相同，无需改动")
            return
        before = self._selection_snapshot()
        after_sel = (col0, row0, cols, rows)
        after = (after_sel, self._cells_to_rect(after_sel))
        cmd = DrawCommand(self.state.doc, layer, changes, self._refresh_layer,
                          text="粘贴选区", selection=(before, after),
                          apply_selection=self._apply_selection_snapshot)
        # push 会执行 redo：写数据的同时把选框落到 after；撤销时自动回到 before。
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage(
            f"粘贴完成：{len(changes)} 格（一条撤销可还原）")

    def _move_selection(self, dc, dr):
        """按整格偏移移动选区（``dc/dr`` 由 ``_sel_move_cells`` 算好并钳制）。

        这里的参数是「格数」而不是像素位移：拖动预览和最终写入共用同一个
        (dc, dr)，避免中途多次 mouseMove 造成累计放大。
        """
        if not self._tile_selection or (dc == 0 and dr == 0):
            return
        if not self.state.can_edit():
            self.statusBar().showMessage("当前层只读或版本只读，无法移动")
            return
        layer = self.state.current_layer
        if not isinstance(layer, TileLayer):
            self.statusBar().showMessage("当前层不是瓦片层，无法移动选区")
            return
        changes = self._compute_move_changes(layer, self._tile_selection, dc, dr)
        col0, row0, cols, rows = self._tile_selection
        after_sel = (col0 + dc, row0 + dr, cols, rows)
        after = (after_sel, self._cells_to_rect(after_sel))
        if changes:
            cmd = DrawCommand(self.state.doc, layer, changes,
                              self._refresh_layer, text="移动选区",
                              selection=(self._selection_snapshot(), after),
                              apply_selection=self._apply_selection_snapshot)
            # redo 写数据并把选框落到 after；undo 把选框一起带回 before。
            self.state.undo_stack.push(cmd)
        else:
            self._apply_selection_snapshot(after)
        self.statusBar().showMessage(
            f"移动选区 {dc:+d}, {dr:+d} 格（{len(changes)} 格变更，一条撤销可还原）")

    # ---------------- 图层结构操作（增 / 删 / 复制 / 排序 / 合并 / 锁定） ----------------
    def _can_edit_structure(self) -> bool:
        return self.state.doc is not None and self.state.profile.write_enabled

    def _next_layer_name(self, base="图层") -> str:
        doc = self.state.doc
        n = len(doc.layers) + 1
        return f"{base} {n}"

    def _add_layer(self, kind: str):
        if not self._can_edit_structure():
            self.statusBar().showMessage("当前版本只读，无法新建图层")
            return
        doc = self.state.doc
        sel = self.layers_dock.selected_layer()
        if kind == "tile":
            new = TileLayer(width=doc.width, height=doc.height,
                           name=self._next_layer_name(),
                           cells=[0] * doc.width * doc.height)
        elif kind == "object":
            new = ObjectLayer(name=self._next_layer_name("对象层"), objects=[])
        else:
            return
        before = list(doc.layers)
        after = list(doc.layers)
        # 图层面板按 doc.layers 倒序显示：数组 index 0 是面板最下侧（最底层）。
        # 新建层统一加到最下侧。
        after.insert(0, new)
        self.state.current_layer = new if kind == "tile" else self.state.current_layer
        from ..commands.layer_command import LayerStructureCommand
        cmd = LayerStructureCommand(doc, before, after,
                                   f"新建{('瓦片' if kind == 'tile' else '对象')}层",
                                   self._refresh_document)
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage(
            f"已新建「{new.name}」（已加到图层列表最下侧）")

    def _delete_layer(self, layer):
        if not self._can_edit_structure():
            self.statusBar().showMessage("当前版本只读，无法删除图层")
            return
        doc = self.state.doc
        if layer is None:
            return
        if len(doc.layers) <= 1:
            self.statusBar().showMessage("至少保留一个图层，无法删除")
            return
        before = list(doc.layers)
        after = [l for l in doc.layers if l is not layer]
        if self.state.current_layer is layer or self.state.current_layer not in after:
            # 重新选一个瓦片层作为当前层，避免绘制落到已删除的层上。
            idx = before.index(layer)
            self.state.current_layer = next(
                (l for l in after if isinstance(l, TileLayer)), None)
            if self.state.current_layer is None:
                self.state.current_layer = after[min(idx, len(after) - 1)]
        from ..commands.layer_command import LayerStructureCommand
        cmd = LayerStructureCommand(doc, before, after,
                                   f"删除 {layer.name}", self._refresh_document)
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage(f"已删除「{layer.name}」")

    def _duplicate_layer(self, layer):
        if not self._can_edit_structure():
            self.statusBar().showMessage("当前版本只读，无法复制图层")
            return
        doc = self.state.doc
        if layer is None:
            return
        from ..document.layer import copy_layer
        new = copy_layer(layer)
        if new is None:
            return
        before = list(doc.layers)
        idx = doc.layers.index(layer)
        after = list(doc.layers)
        after.insert(idx + 1, new)
        self.state.current_layer = new if isinstance(new, TileLayer) else self.state.current_layer
        from ..commands.layer_command import LayerStructureCommand
        cmd = LayerStructureCommand(doc, before, after,
                                   f"复制 {layer.name}", self._refresh_document)
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage(f"已复制为「{new.name}」")

    def _move_layer(self, layer, direction: int):
        if not self._can_edit_structure():
            self.statusBar().showMessage("当前版本只读，无法调整图层顺序")
            return
        doc = self.state.doc
        if layer is None:
            return
        idx = doc.layers.index(layer)
        nidx = idx + direction      # +1 上移（朝向顶部），-1 下移
        if not (0 <= nidx < len(doc.layers)):
            return
        before = list(doc.layers)
        after = list(doc.layers)
        after[idx], after[nidx] = after[nidx], after[idx]
        from ..commands.layer_command import LayerStructureCommand
        cmd = LayerStructureCommand(
            doc, before, after,
            f"{'上移' if direction > 0 else '下移'} {layer.name}",
            self._refresh_document)
        self.state.undo_stack.push(cmd)

    def _merge_layer_down(self, layer):
        if not self._can_edit_structure():
            self.statusBar().showMessage("当前版本只读，无法合并图层")
            return
        doc = self.state.doc
        if layer is None:
            return
        idx = doc.layers.index(layer)
        if idx == 0:
            self.statusBar().showMessage("已是最底层，无法向下合并")
            return
        target = doc.layers[idx - 1]
        if type(target) is not type(layer):
            self.statusBar().showMessage("只能合并相同类型的图层")
            return
        if isinstance(self.state.current_layer, TileLayer) and self.state.current_layer is layer:
            self.state.current_layer = target
        from ..commands.layer_command import MergeLayerCommand
        cmd = MergeLayerCommand(doc, target, layer, idx,
                               f"合并 {layer.name} 到 {target.name}",
                               self._refresh_document)
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage(
            f"已把「{layer.name}」合并到下方「{target.name}」")

    def _toggle_layer_lock(self, layer):
        if not self._can_edit_structure():
            self.statusBar().showMessage("当前版本只读，无法锁定图层")
            return
        if layer is None:
            return
        from ..commands.layer_command import LayerFlagCommand
        cmd = LayerFlagCommand(self.state.doc, layer, "locked",
                               layer.locked, not layer.locked,
                               f"{'锁定' if not layer.locked else '解锁'} {layer.name}",
                               self._refresh_document)
        self.state.undo_stack.push(cmd)

    def _rename_layer(self, layer, name: str):
        """Tiled 风格双击行内重命名：走 LayerFlagCommand，一次 Ctrl+Z 可还原。"""
        if not self._can_edit_structure() or layer is None:
            self.statusBar().showMessage("当前版本只读，无法重命名图层")
            return
        name = (name or "").strip()
        if not name or name == layer.name:
            return
        from ..commands.layer_command import LayerFlagCommand
        old = layer.name
        cmd = LayerFlagCommand(self.state.doc, layer, "name", old, name,
                               f"重命名图层 {old} → {name}",
                               self._refresh_document)
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage(f"图层已重命名为 {name}")

    # ---------------- 内容变换（翻转 / 旋转）：图章内容 / 插入图片 ----------------
    def _can_transform(self) -> bool:
        """变换键只看文档可写 + 有无目标，不受「当前层是否对象层」限制。

        图章是右键从**瓦片层**抓取的，抓取时当前层必然是瓦片层；若把变换键
        也绑到对象层，抓完图章 4 键就会熄灭（2026-11-17 用户反馈）。
        按层置灰的只是对象工具整排，不含这 4 个键。
        """
        return (self.state.doc is not None
                and self.state.profile.write_enabled)

    def _transform_target(self):
        """返回当前变换目标 ``(kind, payload)``，无目标返回 None。

        优先级：选中的插入图片 > 右键框选的图章内容。
        - 图片：kind="image"，payload=选中对象里的 image 对象列表（≥1）；
        - 图章：kind="stamp"，payload=state.stamp（2D raw gid 矩阵）。
        """
        images = [o for o in self.scene.selected_objects
                  if getattr(o, "shape", "") == "image"]
        if images:
            return "image", images
        stamp = getattr(self.state, "stamp", None)
        if stamp and stamp[0]:
            return "stamp", stamp
        return None

    def _refresh_transform_actions(self):
        """有变换目标才点亮 4 个按钮；否则禁用（暗色、点击无反应）。

        目标来自「选中的图片」或「右键框选的图章」，不依赖当前层——图章本身
        就是从瓦片层抓的。对象工具整排的按层置灰在 _on_layer_selected 里管。
        """
        enabled = self._can_transform() and self._transform_target() is not None
        for action in self.object_toolbar._transform_actions:
            action.setEnabled(enabled)

    def _transform_content(self, op: str):
        """4 个按钮的分发入口：按当前目标变换图片或图章内容。"""
        target = self._transform_target()
        if target is None or not self._can_transform():
            return
        kind, payload = target
        if kind == "image":
            self._transform_images(payload, op)
        else:
            self._transform_stamp(op)

    def _transform_images(self, images, op: str):
        """翻转 / 旋转选中的插入图片：翻转改标志位，旋转改 rotation 并交换宽高。"""
        from ..commands.object_command import capture, push_object_edit
        layers = self._layers_of_objects(images)
        before = capture(layers)
        for obj in images:
            if op == "flip_h":
                obj.image_flip_h = not obj.image_flip_h
            elif op == "flip_v":
                obj.image_flip_v = not obj.image_flip_v
            elif op in ("rot_cw", "rot_ccw"):
                obj.rotation = (obj.rotation + (90 if op == "rot_cw" else -90)) % 360
                # 90° 旋转：图片外接框宽高互换，中心保持不动。
                cx = obj.x + obj.width / 2.0
                cy = obj.y + obj.height / 2.0
                obj.width, obj.height = obj.height, obj.width
                obj.x, obj.y = cx - obj.width / 2.0, cy - obj.height / 2.0
        self._refresh_moved_objects(images)
        pushed = push_object_edit(
            self.state.doc, self.state.undo_stack, before,
            {"flip_h": "水平翻转图片", "flip_v": "垂直翻转图片",
             "rot_cw": "顺时针旋转图片", "rot_ccw": "逆时针旋转图片"}[op],
            self._refresh_objects)
        # push_object_edit 用快照重建 layer.objects（对象身份全换），images 已成旧引用。
        # refresh 回调 _refresh_objects 已按 uid 把选中态重绑到新对象；这里绝不能再
        # set_selected_objects(images) 写回旧引用——否则黄色手柄消失、拖动作用在
        # 脱离文档的旧对象上，必须重新点选才能移动（2026-11-21 用户反馈）。
        if pushed:
            self._rebind_object_selection(self.state.doc)
        else:
            # 无实际变化时层对象未被克隆，images 仍是有效引用。
            self.scene.set_selected_objects(list(images))
        self._refresh_transform_actions()
        label = {"flip_h": "水平翻转", "flip_v": "垂直翻转",
                 "rot_cw": "顺时针旋转 90°", "rot_ccw": "逆时针旋转 90°"}[op]
        self.statusBar().showMessage(
            f"{label} {len(images)} 张图片"
            + ("（Ctrl+Z 可撤销）" if pushed else "（无变化）"))

    def _transform_stamp(self, op: str):
        """翻转 / 旋转右键框选的图章内容（2D raw gid 矩阵）。"""
        from ..rw.transform import transform_stamp
        old = self.state.stamp
        if not old or not old[0]:
            return
        self.state.stamp = transform_stamp(old, op)
        # 刷新画布上的图章虚影（若正在预览）：沿用当前预览的锚点行列。
        rows, cols = len(self.state.stamp), len(self.state.stamp[0])
        preview = getattr(self.scene, "_stamp_preview", None)
        if preview is not None:
            self.scene.set_stamp_preview(self.state.stamp, preview.col, preview.row)
        self._refresh_transform_actions()
        label = {"flip_h": "水平翻转", "flip_v": "垂直翻转",
                 "rot_cw": "顺时针旋转 90°", "rot_ccw": "逆时针旋转 90°"}[op]
        self.statusBar().showMessage(
            f"{label}图章内容 → {cols}x{rows} 格（左键印下）")

    # ---------------- 地图变换（新建 / 改尺寸，翻转旋转已移到内容变换） ----------------
    def _transform_map(self, op: str):
        if not self._can_transform():
            self.statusBar().showMessage("当前版本只读，无法变换地图")
            return
        doc = self.state.doc
        from ..rw.transform import flip_cells, rotate_cells_90, transform_objects
        from ..commands.layer_command import MapTransformCommand
        from ..document.layer import copy_layer
        before = list(doc.layers)
        old_dims = (doc.width, doc.height, doc.tile_w, doc.tile_h)
        after = []
        if op in ("flip_h", "flip_v"):
            horizontal = op == "flip_h"
            for layer in doc.layers:
                if isinstance(layer, TileLayer):
                    cells = flip_cells(layer.cells, doc.width, doc.height, horizontal)
                    after.append(TileLayer(width=doc.width, height=doc.height,
                                          name=layer.name, cells=cells,
                                          readonly=layer.readonly,
                                          opacity=layer.opacity, visible=layer.visible,
                                          locked=layer.locked, offsetx=layer.offsetx,
                                          offsety=layer.offsety,
                                          encoding="base64", compression="gzip",
                                          dirty=True))
                else:
                    objs = transform_objects(layer.objects, doc.width, doc.height,
                                            doc.tile_w, doc.tile_h, op)
                    after.append(ObjectLayer(name=layer.name, objects=objs,
                                            opacity=layer.opacity, visible=layer.visible,
                                            locked=layer.locked, offsetx=layer.offsetx,
                                            offsety=layer.offsety))
            new_dims = old_dims
            label = "水平翻转" if horizontal else "垂直翻转"
        else:
            cw = op == "rot_cw"
            new_w, new_h = doc.height, doc.width
            for layer in doc.layers:
                if isinstance(layer, TileLayer):
                    cells, rw, rh = rotate_cells_90(layer.cells, doc.width,
                                                  doc.height, cw)
                    after.append(TileLayer(width=rw, height=rh, name=layer.name,
                                          cells=cells, readonly=layer.readonly,
                                          opacity=layer.opacity, visible=layer.visible,
                                          locked=layer.locked, offsetx=layer.offsetx,
                                          offsety=layer.offsety,
                                          encoding="base64", compression="gzip",
                                          dirty=True))
                else:
                    objs = transform_objects(layer.objects, doc.width, doc.height,
                                            doc.tile_w, doc.tile_h, op)
                    after.append(ObjectLayer(name=layer.name, objects=objs,
                                            opacity=layer.opacity, visible=layer.visible,
                                            locked=layer.locked, offsetx=layer.offsetx,
                                            offsety=layer.offsety))
            new_dims = (new_w, new_h, doc.tile_w, doc.tile_h)
            label = "顺时针旋转 90°" if cw else "逆时针旋转 90°"
        cur_idx = (before.index(self.state.current_layer)
                   if self.state.current_layer in before else None)
        if cur_idx is not None:
            self.state.current_layer = after[cur_idx]
        cmd = MapTransformCommand(doc, before, after, old_dims, new_dims,
                                 label, self._refresh_document)
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage(f"{label}完成（一条撤销可还原）")

    def _resize_map(self):
        """Tiled 风格调整大小：尺寸 + 挪动偏移 + 可选移除地图外对象。

        预览里可直接拖动地图改 X/Y，落在新地图外的瓦片用连通块红框标出；
        真正重建图层走 ``rw.resize`` 纯算法，整包塞进一条撤销命令。
        """
        if not self._can_transform():
            self.statusBar().showMessage("当前版本只读，无法调整地图尺寸")
            return
        doc = self.state.doc
        from ..commands.layer_command import MapTransformCommand
        dlg = ResizeMapDialog(doc, self)
        if dlg.exec() != QDialog.Accepted:
            return
        vals = dlg.values()
        nw, nh = vals["width"], vals["height"]
        dx, dy = vals["dx"], vals["dy"]
        remove_outside = vals["remove_outside"]

        from ..document.layer import ObjectLayer, TileLayer
        before = list(doc.layers)
        old_dims = (doc.width, doc.height, doc.tile_w, doc.tile_h)
        after = []
        overflow_total = 0
        removed_objs = 0
        for layer in doc.layers:
            if isinstance(layer, TileLayer):
                cells, overflow = shift_cells(layer.cells, doc.width, doc.height,
                                              nw, nh, dx, dy)
                overflow_total += len(overflow)
                after.append(TileLayer(width=nw, height=nh, name=layer.name,
                                      cells=cells, readonly=layer.readonly,
                                      opacity=layer.opacity, visible=layer.visible,
                                      locked=layer.locked, offsetx=layer.offsetx,
                                      offsety=layer.offsety,
                                      encoding="base64", compression="gzip",
                                      dirty=True))
            else:
                new_objs = shift_objects(layer.objects, dx, dy, doc.tile_w, doc.tile_h,
                                         nw, nh, remove_outside)
                removed_objs += len(layer.objects) - len(new_objs)
                after.append(ObjectLayer(name=layer.name, objects=new_objs,
                                        opacity=layer.opacity, visible=layer.visible,
                                        locked=layer.locked, offsetx=layer.offsetx,
                                        offsety=layer.offsety))
        new_dims = (nw, nh, doc.tile_w, doc.tile_h)
        cur_idx = (before.index(self.state.current_layer)
                   if self.state.current_layer in before else None)
        if cur_idx is not None:
            self.state.current_layer = after[cur_idx]
        cmd = MapTransformCommand(doc, before, after, old_dims, new_dims,
                                 "调整地图尺寸", self._refresh_document)
        self.state.undo_stack.push(cmd)
        extra = f"，挪动 ({dx:+d},{dy:+d})" if (dx or dy) else ""
        if overflow_total:
            extra += f"，裁掉溢出瓦片 {overflow_total} 格"
        if removed_objs:
            extra += f"，移除地图外对象 {removed_objs} 个"
        self.statusBar().showMessage(f"地图尺寸已调整为 {nw}×{nh}{extra}（一条撤销可还原）")

    def _new_map(self):
        from ..widgets.new_map_dialog import NewMapDialog, build_new_document
        dlg = NewMapDialog(self.state.profile, self)
        if dlg.exec() != QDialog.Accepted:
            return
        vals = dlg.values()
        w, h, tsx_paths = vals["width"], vals["height"], vals["tilesets"]
        doc = build_new_document(self.state.profile, w, h, tsx_paths)
        ctx = AppState()
        ctx.open_document(doc)
        self._contexts.append(ctx)
        self._own_file = True
        self._attach_context(ctx)
        self.statusBar().showMessage(
            f"已新建空白地图 {w}×{h}，包含 {len(tsx_paths)} 个图集（瓦片 {doc.tile_w}×{doc.tile_h}）")

    def _finish_poly(self):
        # Ctrl+Return 已被本 QAction 占用；钢笔模式下复用它作备用完成入口，
        # 不新增键盘信号，避免与多边形工具双触发。
        if self._object_tool == "pen":
            if self._pen_path is not None:
                self._pen_finish()
            return
        pending = self._object_pending
        if not pending or pending["kind"] != "poly":
            return
        pts = list(pending["points"])
        if len(pts) > 1 and abs(pts[-1][0] - pts[-2][0]) < 1 \
                and abs(pts[-1][1] - pts[-2][1]) < 1:
            pts.pop()                       # 双击的第二次点击与末点重复
        need = 3 if pending["tool"] == "polygon" else 2
        if len(pts) < need:
            self.statusBar().showMessage(
                f"至少需要 {need} 个点（已有 {len(pts)} 个）")
            return
        self._object_pending = None
        self.scene.set_object_preview(None)
        x0, y0 = pts[0]
        obj = MapObject(
            object_id=self._new_object_id(),
            name=SHAPE_LABELS[pending["tool"]], shape=pending["tool"],
            x=x0, y=y0,
            points=[(px - x0, py - y0) for px, py in pts])
        self._commit_new_object(pending["layer"], obj,
                                f"插入{SHAPE_LABELS[pending['tool']]}")

    def _cancel_object_tool(self):
        # Esc / 切工具 / 切文档：缩放拖动进行中要把对象还原，不能留下半截改动。
        if self._sel_resize_drag is not None:
            self._cancel_selection_resize()
        pending = self._object_pending
        self._object_pending = None
        if hasattr(self, "scene") and self.scene is not None:
            self.scene.set_object_preview(None)
        if pending and pending["kind"] == "node" and pending.get("moved"):
            restore(pending["layer"], pending["before"])
            self._refresh_objects()
        # Esc / 切换工具 / 切文档统一清掉钢笔状态（钢笔不写 _object_pending）。
        self._pen_cancel()
        # 对象层框选进行中也要取消，并清掉蓝色选框。
        if getattr(self, "_obj_marquee", False):
            self._obj_marquee = False
        # 同时进行中的框选拖动必须结束，否则残留的 _sel_moving 会在下次
        # mouseMove 时继续按旧起点推算位移。
        self._clear_tile_selection()

    # ---- 编辑多边形 / 折线顶点 ----
    def _clear_tile_selection(self):
        """复位瓦片框选的全部状态（切图层 / 取消工具 / 文档重载时调用）。"""
        self._tile_selection = None
        self._sel_rect = None
        self._sel_dragging = False
        self._sel_moving = False
        self._sel_start = (0.0, 0.0)
        self._sel_move_start = (0.0, 0.0)
        if getattr(self, "scene", None) is not None:
            self.scene.set_selection_rect(None)

    def _node_press(self, x, y):
        obj = self.scene.selected_object
        layer = self._object_layer_of(self.state.doc, obj)
        if obj is None or layer is None or obj.shape not in ("polygon", "polyline"):
            self.statusBar().showMessage("先用「选择对象」选中一个多边形 / 折线对象")
            return
        best = None
        for i, (px, py) in enumerate(obj.points):
            dist = ((obj.x + px - x) ** 2 + (obj.y + py - y) ** 2) ** 0.5
            if best is None or dist < best[0]:
                best = (dist, i)
        if best is None or best[0] > 10:
            self.statusBar().showMessage("请点在顶点附近（10 像素内）")
            return
        self._object_pending = {
            "kind": "node", "layer": layer, "obj": obj, "index": best[1],
            "before": snapshot(layer), "moved": False,
        }

    def _node_move(self, x, y):
        pending = self._object_pending
        obj = pending["obj"]
        index = pending["index"]
        nx, ny = x - obj.x, y - obj.y
        if obj.points[index] == (nx, ny):
            return
        old = self._object_rect(obj)
        pts = list(obj.points)
        pts[index] = (nx, ny)
        obj.points = pts
        pending["moved"] = True
        pending["layer"].dirty = True
        self.state.doc.dirty = True
        self.scene.refresh_object_rects(pending["layer"],
                                        [old, self._object_rect(obj)],
                                        [obj, obj])
        self._update_title()

    def _node_release(self):
        pending = self._object_pending
        self._object_pending = None
        if not pending.get("moved"):
            return
        after = snapshot(pending["layer"])
        self.state.undo_stack.push(ObjectCommand(
            self.state.doc, pending["layer"], pending["before"], after,
            "编辑对象节点", self._refresh_objects))

    # ---------------- 对象可视化编辑（Ctrl+左键选中 / 拖动，Delete 删除） ----------------
    def _refresh_objects(self):
        """对象数据变化后刷新画布，并把选中态重新绑定到同 id 的当前对象。

        ObjectCommand / MultiObjectCommand 会用快照替换 layer.objects，
        旧的对象引用会失效，这里按 object_id 重新绑定（含多选），
        撤销 / 重做后高亮才不会丢。
        """
        doc = self.state.doc
        # 改触发器 / 任意对象后只重建节点图，不重新居中：用户手动调好的
        # 视角不能被一次属性编辑弹走。需要重新对准时有「适应视图」按钮。
        self.trigger_graph_dock.refresh(fit=False)
        if doc is not None:
            self._rebind_object_selection(doc)
        self.scene.refresh_objects()
        self._update_title()

    def _rebind_object_selection(self, doc):
        current = list(self.scene.selected_objects)
        if not current:
            return
        # 铁锈地图里 Tiled object_id 大量重复，{o.object_id: o} 会把多选压成一个。
        # 按稳定身份（xml_element）建索引；无 xml_element 的旧引用按 object_id 兜底。
        by_uid = {}
        by_oid = {}
        for layer in doc.layers:
            if not isinstance(layer, ObjectLayer):
                continue
            for o in layer.objects:
                by_uid[object_uid(o)] = o
                by_oid.setdefault(o.object_id, o)
        fresh = [by_uid.get(object_uid(o)) or by_oid.get(o.object_id)
                 for o in current]
        fresh = [o for o in fresh if o is not None]
        if len(fresh) == len(current) and all(
                a is b for a, b in zip(fresh, current)):
            return
        self.scene.set_selected_objects(fresh)

    def _layers_of_objects(self, objs):
        doc = self.state.doc
        return [self._object_layer_of(doc, o) for o in objs]

    def _on_object_selected(self, obj):
        self.scene.set_selected_object(obj)

    def _on_objects_selected(self, objs):
        """节点图选中整合体 → 地图画布高亮它代表的全部对象。"""
        self.scene.set_selected_objects(list(objs or []))

    def _object_rect(self, obj) -> QRectF:
        doc = self.state.doc
        tw = doc.tile_w if doc is not None else 20
        th = doc.tile_h if doc is not None else 20
        x, y, w, h = obj.bounds(tw, th)
        return QRectF(x, y, w, h)

    def _objects_interactive(self) -> bool:
        """对象是否参与鼠标命中 / 交互。

        用户明确要求：当前在瓦片层（如 Ground）时，对象层（如 Triggers）的
        对象要对鼠标完全「透明」——点不到、框不到、悬停不出缩放箭头、右键
        也不弹对象菜单。

        判定用「当前层不是瓦片层」而非「必须是对象层」：活动层为 None（如
        刚建窗口 / 未指定层）时对象仍可交互，避免初始化流程里对象突然全失效。
        只有明确处于瓦片层时才让对象透明。

        注意 Triggers 层拖框框到 Ground 瓦片仍然保留（对象层框选会同时写
        瓦片选区），这里的守卫只管「对象能不能被鼠标命中」这一方向。
        """
        return not isinstance(self._active_layer, TileLayer)

    def _hit_object(self, x: float, y: float):
        if not self._objects_interactive():
            return None, None
        doc = self.state.doc
        if doc is None:
            return None, None
        for layer in reversed(doc.layers):
            if not isinstance(layer, ObjectLayer) or not layer.visible:
                continue
            for obj in reversed(layer.objects):
                if self._object_rect(obj).contains(x, y):
                    return layer, obj
        return None, None

    # ---------------- 对象选中包围框 / 整组拖动 / 组合 / 对齐 ----------------
    def _obj_selection_box(self):
        """所有选中对象的整体外接矩形（场景坐标）；无选中返回 None。"""
        objs = self.scene.selected_objects
        if not objs or self.state.doc is None:
            return None
        tw, th = self.state.doc.tile_w, self.state.doc.tile_h
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        for o in objs:
            x, y, w, h = o.bounds(tw, th)
            x0, y0 = min(x0, x), min(y0, y)
            x1, y1 = max(x1, x + w), max(y1, y + h)
        if x1 < x0 or y1 < y0:
            return None
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    def _select_object_with_group(self, obj):
        """选中对象：若它属于某个组合，则选中组内全部对象（跨对象层）。"""
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        gid = obj.properties.get(EDITOR_GROUP_PROPERTY) if obj.properties else None
        if gid:
            objs = [o for l in self.state.doc.layers
                    if isinstance(l, ObjectLayer)
                    for o in l.objects
                    if o.properties.get(EDITOR_GROUP_PROPERTY) == gid]
            if objs:
                self.scene.set_selected_objects(objs)
                self.trigger_graph_dock.select_objects(objs)
                return
        self.scene.set_selected_objects([obj])
        self.trigger_graph_dock.select_object(obj)

    # ---------------- 选中框的 8 个缩放箭头 ----------------
    def _selection_handle_at(self, x: float, y: float):
        """返回鼠标命中的黄色缩放箭头名；没有选中 / 没命中返回 None。

        点对象没有尺寸、不可缩放：纯点选区不提供箭头（连整体框也不画箭头），
        因此拖点对象只会移动、不会进入缩放状态。
        """
        if not self._objects_interactive():
            return None
        from ..view.object_layer_item import handle_hit_arrow
        box = self._obj_selection_box()
        objs = self.scene.selected_objects
        if box is None or not objs:
            return None
        # 只要选区里混有任意非点对象就仍可整组缩放；纯点选区禁止缩放。
        if all(getattr(o, "shape", None) == "point" for o in objs):
            return None
        scale = abs(self.view.transform().m11()) or 1.0
        return handle_hit_arrow(box, x, y, scale)

    def _restore_select_cursor(self):
        """缩放结束后把光标还给当前工具（选择对象用自定义鼠标箭头）。"""
        self._hover_resize_cursor = None
        if getattr(self, "view", None) is None:
            return
        if self._object_tool == "select":
            self.view.setCursor(make_select_cursor())
        elif getattr(self.state, "tool", None) is not None:
            self.view.setCursor(self.state.tool.cursor)

    def _on_object_hover(self, x: float, y: float):
        """悬停在缩放箭头上时把鼠标换成对应方向的双向箭头。"""
        if (self._object_tool != "select"
                or self._sel_resize_drag is not None
                or self._sel_move_drag is not None):
            return
        handle = self._selection_handle_at(x, y)
        want = _RESIZE_CURSORS.get(handle) if handle else None
        if want == self._hover_resize_cursor:
            return
        self._hover_resize_cursor = want
        self.view.setCursor(want if want is not None else make_select_cursor())

    def _begin_selection_resize(self, x: float, y: float, handle: str):
        objs = list(self.scene.selected_objects)
        box = self._obj_selection_box()
        if not objs or box is None or box.width() <= 0 or box.height() <= 0:
            return
        from ..commands.object_command import capture
        doc = self.state.doc
        tw = doc.tile_w if doc is not None else 20
        th = doc.tile_h if doc is not None else 20
        # 逐帧缩放都基于「按下瞬间」的原始几何重算，避免连续 mouseMove 累积误差。
        base = []
        for o in objs:
            bx, by, bw, bh = o.bounds(tw, th)
            base.append((o, o.x, o.y, o.width, o.height,
                         [tuple(p) for p in o.points], bx, by, bw, bh))
        self._sel_resize_drag = {
            "handle": handle, "sx": x, "sy": y, "box": QRectF(box),
            "base": base, "objs": objs, "doc": doc,
            "before": capture(self._layers_of_objects(objs)),
        }
        self.view.setCursor(_RESIZE_CURSORS.get(handle, Qt.SizeAllCursor))
        self.statusBar().showMessage(
            f"拖动缩放箭头「{handle}」（Ctrl 吸附瓦片边，Shift 等比例，Esc 取消）")

    def _do_selection_resize(self, x: float, y: float, drag=None):
        drag = drag if drag is not None else self._sel_resize_drag
        if drag is None or self.state.doc is None:
            return
        name, box = drag["handle"], drag["box"]
        left, top, right, bottom = (box.left(), box.top(),
                                    box.right(), box.bottom())
        min_w, min_h = min(8.0, box.width()), min(8.0, box.height())
        # 修订：Ctrl 吸附的是「被拖的那条边最终落在哪条瓦片线上」，不是
        # 「位移量取整成整格」。旧实现把按下点与鼠标点分别 round 后相减，
        # 只是让对象一格一格地跳；若矩形四边本来就不在瓦片边上，拖完仍是
        # 悬在瓦片中间（用户反馈的「只移动了一格，没吸附」）。
        # 新算法：先用自由位移算出目标边，再把目标边吸附到最近的网格线；
        # 未被拖的对边保持原样，所以轻微拖动也会先把这条边吸到最近交叉点。
        dx, dy = x - drag["sx"], y - drag["sy"]
        snap = bool(self._mods() & Qt.ControlModifier)
        tw = self.state.doc.tile_w if self.state.doc else 20
        th = self.state.doc.tile_h if self.state.doc else 20

        def snapped(v, step):
            return round(v / step) * step

        if name in ("nw", "w", "sw"):
            nv = left + dx
            left = min(snapped(nv, tw) if snap else nv, right - min_w)
        if name in ("ne", "e", "se"):
            nv = right + dx
            right = max(snapped(nv, tw) if snap else nv, left + min_w)
        if name in ("nw", "n", "ne"):
            nv = top + dy
            top = min(snapped(nv, th) if snap else nv, bottom - min_h)
        if name in ("sw", "s", "se"):
            nv = bottom + dy
            bottom = max(snapped(nv, th) if snap else nv, top + min_h)
        fx = (right - left) / max(box.width(), 1e-6)
        fy = (bottom - top) / max(box.height(), 1e-6)
        # Shift = 等比例缩放：以拖动位移较大的轴为准，另一轴按同一比例，
        # 固定边（对角 / 对边）保持不动。角手柄两轴都动，边手柄只动一轴。
        if self._mods() & Qt.ShiftModifier:
            f = fx if abs(fx - 1.0) >= abs(fy - 1.0) else fy
            w2, h2 = box.width() * f, box.height() * f
            cx, cy = (box.left() + box.right()) / 2, (box.top() + box.bottom()) / 2
            # 水平：拖左边则右边固定，拖右边则左边固定，上下边手柄水平居中扩展。
            if name in ("nw", "w", "sw"):
                left = box.right() - w2
            elif name in ("ne", "e", "se"):
                right = box.left() + w2
            else:                                   # n / s
                left, right = cx - w2 / 2, cx + w2 / 2
            # 垂直：拖上边则下边固定，拖下边则上边固定，左右边手柄垂直居中扩展。
            if name in ("nw", "n", "ne"):
                top = box.bottom() - h2
            elif name in ("sw", "s", "se"):
                bottom = box.top() + h2
            else:                                   # e / w
                top, bottom = cy - h2 / 2, cy + h2 / 2
            fx = fy = f
        for (o, ax, ay, aw, ah, pts, bx, by, bw, bh) in drag["base"]:
            nx = left + (bx - box.left()) * fx
            ny = top + (by - box.top()) * fy
            nw, nh = bw * fx, bh * fy
            if o.shape == "point":
                # 点对象恒为 12x12，缩放只按比例挪它的中心。
                o.x, o.y = nx + nw / 2, ny + nh / 2
            elif o.shape in ("polygon", "polyline") and pts:
                scaled = [(px * fx, py * fy) for px, py in pts]
                o.points = scaled
                o.x = nx - min(p[0] for p in scaled)
                o.y = ny - min(p[1] for p in scaled)
            else:
                o.x, o.y, o.width, o.height = nx, ny, nw, nh
        # 旧框也要标脏：缩小对象时新矩形盖不住原来的黄色框线 / 箭头。
        old_rects = [QRectF(bx, by, bw, bh) for (*_, bx, by, bw, bh) in drag["base"]]
        self._refresh_moved_objects(drag["objs"], old_rects)
        self.scene.set_object_selection_box(
            QRectF(left, top, right - left, bottom - top))

    def _end_selection_resize(self, x: float, y: float):
        drag = self._sel_resize_drag
        self._sel_resize_drag = None
        if drag is None:
            return
        self._do_selection_resize(x, y, drag)   # 落地最终尺寸
        from ..commands.object_command import push_object_edit
        pushed = push_object_edit(self.state.doc, self.state.undo_stack,
                                  drag["before"], "缩放对象", self._refresh_objects)
        self.scene.set_object_selection_box(self._obj_selection_box())
        objs = list(self.scene.selected_objects)
        if objs:
            self.trigger_graph_dock.select_objects(objs)
        self._restore_select_cursor()
        self.statusBar().showMessage(
            f"缩放 {len(drag['objs'])} 个对象"
            + ("（Ctrl+Z 可撤销）" if pushed else "（尺寸未变）"))

    def _cancel_selection_resize(self):
        """Esc / 切工具 / 切文档：丢弃拖动，把对象还原，且**不**进撤销栈。"""
        drag = self._sel_resize_drag
        self._sel_resize_drag = None
        if drag is None:
            return
        if self.state.doc is drag["doc"]:
            for (o, ax, ay, aw, ah, pts, *_rest) in drag["base"]:
                o.x, o.y, o.width, o.height = ax, ay, aw, ah
                o.points = [tuple(p) for p in pts]
            self._refresh_moved_objects(drag["objs"])
            self.scene.set_object_selection_box(self._obj_selection_box())
        self._restore_select_cursor()
        self.statusBar().showMessage("已取消缩放")

    def _begin_selection_move(self, x, y):
        objs = list(self.scene.selected_objects)
        if not objs:
            return
        from ..commands.object_command import capture
        self._sel_move_drag = {
            "objs": objs,
            "base": [(o, o.x, o.y) for o in objs],
            "sx": x, "sy": y,
            # 拖动前先给涉及的每个对象层拍快照：松手时一次性登记成一条撤销。
            "before": capture(self._layers_of_objects(objs)),
        }
        self.statusBar().showMessage("拖动移动选中对象（按住 Ctrl 按格移动）")

    def _do_selection_move(self, x, y):
        drag = self._sel_move_drag
        if drag is None or self.state.doc is None:
            return
        dx = x - drag["sx"]
        dy = y - drag["sy"]
        if self._mods() & Qt.ControlModifier:
            tw = self.state.doc.tile_w
            th = self.state.doc.tile_h
            dx = round(dx / tw) * tw
            dy = round(dy / th) * th
        for o, ox, oy in drag["base"]:
            o.x = ox + dx
            o.y = oy + dy
        self._refresh_moved_objects(drag["objs"])
        self.scene.set_object_selection_box(self._obj_selection_box())

    def _end_selection_move(self, x, y):
        drag = self._sel_move_drag
        self._sel_move_drag = None
        if drag is None:
            return
        self._do_selection_move(x, y)  # 落地最终位置
        moved = any((o.x, o.y) != (ox, oy) for o, ox, oy in drag["base"])
        pushed = False
        if moved:
            from ..commands.object_command import push_object_edit
            pushed = push_object_edit(
                self.state.doc, self.state.undo_stack, drag.get("before") or [],
                "移动对象", self._refresh_objects)
        objs = list(self.scene.selected_objects)
        if objs:
            self.trigger_graph_dock.select_objects(objs)
        if not moved and objs:
            # 纯点选（没拖动）：跨层选中时明确告诉用户选中了哪个层的对象。
            # Ground 层点 Triggers 对象如果没有任何反馈，用户会以为「点了没反应」。
            owner = self._object_layer_of(self.state.doc, objs[0])
            cur = self.state.current_layer
            if owner is not None and owner is not cur:
                self.statusBar().showMessage(
                    f"已选中 {owner.name} 的对象「{objs[0].name}」"
                    "（可拖动 / 拖箭头缩放 / Delete 删除）")
                return
        self.statusBar().showMessage(
            f"移动 {len(drag['objs'])} 个对象"
            + ("（Ctrl+Z 可撤销）" if pushed else "（位置未变）"))

    def _refresh_moved_objects(self, objs, extra_rects=()):
        """标脏受影响对象层 + 标脏文档（拖动 / 缩放逐帧调用，不在这里入撤销栈）。

        ``extra_rects`` 用于缩放：旧矩形比新矩形大时，只标脏新矩形会留下旧框残影。
        """
        doc = self.state.doc
        if doc is None:
            return
        rects = [self._object_rect(o) for o in objs] + list(extra_rects)
        seen = []  # ObjectLayer 是 dataclass，不可哈希，用列表去重
        for o in objs:
            layer = self._object_layer_of(doc, o)
            if layer is not None and layer not in seen:
                seen.append(layer)
                self.scene.refresh_object_rects(
                    layer, rects, list(objs) + [None] * len(extra_rects))
                layer.dirty = True
        doc.dirty = True
        self._update_title()

    def _finalize_object_changes(self, objs):
        """对齐 / 分布 / 组合后的统一收尾：刷新矩形 + 包围框 + 节点图高亮。"""
        self._refresh_moved_objects(objs)
        self.scene.set_object_selection_box(self._obj_selection_box())
        self.trigger_graph_dock.select_objects(objs)

    def _group_selected_objects(self, name: str | None = None):
        """把当前选中的多个对象分到同一个组：同组名 = 同一组。

        组名写在编辑器私有属性 EDITOR_GROUP_PROPERTY 上（铁锈战争不识别）。
        跨对象层的改动合成一条撤销记录。
        """
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        objs = list(self.scene.selected_objects)
        if len(objs) < 2:
            self.statusBar().showMessage("组合需要至少 2 个对象")
            return
        if name is None:
            from PySide6.QtWidgets import QInputDialog
            default = f"组{self._group_counter + 1}"
            name, ok = QInputDialog.getText(
                self, "新建组", "组名（同名的对象会被分到同一组）：", text=default)
            if not ok or not name.strip():
                return
            name = name.strip()
        self._group_counter += 1
        from ..commands.object_command import capture, push_object_edit
        before = capture(self._layers_of_objects(objs))
        for o in objs:
            o.properties.set(EDITOR_GROUP_PROPERTY, name, "string")
        push_object_edit(self.state.doc, self.state.undo_stack, before,
                         f"组合 {len(objs)} 个对象", self._refresh_objects)
        current = list(self.scene.selected_objects)
        self.trigger_graph_dock.select_objects(current or objs)
        self.trigger_graph_dock.set_group_page(name)
        self.statusBar().showMessage(
            f"已组合 {len(objs)} 个对象（{EDITOR_GROUP_PROPERTY}={name}，Ctrl+Z 可撤销）")

    def _ungroup_selected_objects(self):
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        objs = list(self.scene.selected_objects)
        if not objs:
            return
        from ..commands.object_command import capture, push_object_edit
        before = capture(self._layers_of_objects(objs))
        for o in objs:
            o.properties.values.pop(EDITOR_GROUP_PROPERTY, None)
            o.properties.types.pop(EDITOR_GROUP_PROPERTY, None)
        pushed = push_object_edit(self.state.doc, self.state.undo_stack, before,
                                  f"拆除组合 {len(objs)} 个对象",
                                  self._refresh_objects)
        if not pushed:
            self._refresh_objects()
        self.statusBar().showMessage(
            "已取消组合" + ("（Ctrl+Z 可撤销）" if pushed else "（本来就没有分组）"))

    def _align_selected(self, mode):
        objs = list(self.scene.selected_objects)
        if len(objs) < 2:
            self.statusBar().showMessage("对齐需要至少 2 个对象")
            return
        from ..commands.object_command import capture, push_object_edit
        before = capture(self._layers_of_objects(objs))
        rects = [self._object_rect(o) for o in objs]
        if mode == "left":
            t = min(r.left() for r in rects)
            for o, r in zip(objs, rects):
                o.x += t - r.left()
        elif mode == "right":
            t = max(r.right() for r in rects)
            for o, r in zip(objs, rects):
                o.x += t - r.right()
        elif mode == "top":
            t = min(r.top() for r in rects)
            for o, r in zip(objs, rects):
                o.y += t - r.top()
        elif mode == "bottom":
            t = max(r.bottom() for r in rects)
            for o, r in zip(objs, rects):
                o.y += t - r.bottom()
        elif mode == "hcenter":
            t = (min(r.left() for r in rects) + max(r.right() for r in rects)) / 2
            for o, r in zip(objs, rects):
                o.x += t - (r.left() + r.right()) / 2
        elif mode == "vcenter":
            t = (min(r.top() for r in rects) + max(r.bottom() for r in rects)) / 2
            for o, r in zip(objs, rects):
                o.y += t - (r.top() + r.bottom()) / 2
        push_object_edit(self.state.doc, self.state.undo_stack, before,
                         f"对齐对象（{mode}）", self._refresh_objects)
        self._finalize_object_changes(list(self.scene.selected_objects) or objs)
        self.statusBar().showMessage(f"已对齐：{mode}（Ctrl+Z 可撤销）")

    def _distribute_selected(self, axis):
        objs = list(self.scene.selected_objects)
        if len(objs) < 3:
            self.statusBar().showMessage("分布需要至少 3 个对象")
            return
        from ..commands.object_command import capture, push_object_edit
        before = capture(self._layers_of_objects(objs))
        rects = [self._object_rect(o) for o in objs]
        if axis == "h":
            order = sorted(range(len(objs)),
                          key=lambda i: rects[i].center().x())
            left, right = rects[order[0]].left(), rects[order[-1]].right()
            widths = [rects[i].width() for i in order]
            gap = (right - left - sum(widths)) / (len(order) - 1)
            cursor = left
            for i in order:
                dx = cursor - rects[i].left()
                objs[i].x += dx
                cursor += rects[i].width() + gap
        else:
            order = sorted(range(len(objs)),
                          key=lambda i: rects[i].center().y())
            top, bottom = rects[order[0]].top(), rects[order[-1]].bottom()
            heights = [rects[i].height() for i in order]
            gap = (bottom - top - sum(heights)) / (len(order) - 1)
            cursor = top
            for i in order:
                dy = cursor - rects[i].top()
                objs[i].y += dy
                cursor += rects[i].height() + gap
        push_object_edit(self.state.doc, self.state.undo_stack, before,
                         f"分布对象（{'水平' if axis == 'h' else '垂直'}）",
                         self._refresh_objects)
        self._finalize_object_changes(list(self.scene.selected_objects) or objs)
        self.statusBar().showMessage(f"已分布：{axis}（Ctrl+Z 可撤销）")

    def _current_groups(self):
        """当前文档里出现过的所有组名（升序）；不含空（未分组）。"""
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        doc = self.state.doc
        # 节点图里「没选中对象也能新建」的会话内空组也要能往里加对象，
        # 否则空组永远填不进东西、保存后自然消失（组名靠对象属性反推）。
        groups = set(self.trigger_graph_dock.empty_groups())
        if doc is None:
            return sorted(groups)
        for layer in doc.layers:
            if not isinstance(layer, ObjectLayer):
                continue
            for o in layer.objects:
                g = o.properties.get(EDITOR_GROUP_PROPERTY) if o.properties else None
                if g:
                    groups.add(g)
        return sorted(groups)

    def _join_selected_to_group(self, name: str):
        """把当前选中的对象加入指定组；name='' 表示回到「选择已有组」（取消分组）。"""
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        objs = list(self.scene.selected_objects)
        if not objs:
            return
        from ..commands.object_command import capture, push_object_edit
        before = capture(self._layers_of_objects(objs))
        for o in objs:
            if name == "":
                o.properties.values.pop(EDITOR_GROUP_PROPERTY, None)
                o.properties.types.pop(EDITOR_GROUP_PROPERTY, None)
            else:
                o.properties.set(EDITOR_GROUP_PROPERTY, name, "string")
        label = "选择已有组" if name == "" else name
        pushed = push_object_edit(self.state.doc, self.state.undo_stack, before,
                                  f"加入组「{label}」", self._refresh_objects)
        if not pushed:
            self._refresh_objects()
        self.trigger_graph_dock.set_group_page(name)
        self.statusBar().showMessage(
            f"已将 {len(objs)} 个对象加入「{label}」"
            + ("（Ctrl+Z 可撤销）" if pushed else "（无变化）"))

    def _on_object_right_press(self, x, y):
        """对象选择类工具下右键弹菜单；标尺工具右键清空标记。"""
        if canonical_tool_id(self._object_tool) == "ruler":
            had = bool(self._ruler_segments or self._ruler_pending)
            self._ruler_segments = []
            self._ruler_pending = None
            ruler = self.scene.ruler_overlay()
            if ruler is not None:
                ruler.clear()
            self.statusBar().showMessage(
                "已清除标尺标记" if had else "标尺：右键清除标记，左键点两格测距")
            return
        if canonical_tool_id(self._object_tool) != "select":
            return
        objs = list(self.scene.selected_objects)
        layer, hit = self._hit_object(x, y)
        if hit is not None and hit not in objs:
            self._select_object_with_group(hit)
            objs = list(self.scene.selected_objects)
        elif not objs:
            return
        self._paste_right_xy = (x, y)   # 复制后以右键点为吸附锚点
        self._show_object_context_menu(x, y)

    def _build_object_context_menu(self):
        """构造对象右键菜单（不弹窗），便于测试与复用。无选中返回 None。"""
        objs = list(self.scene.selected_objects)
        if not objs:
            return None
        from rwmap.document.properties import EDITOR_GROUP_PROPERTY
        has_group = any(
            (o.properties.get(EDITOR_GROUP_PROPERTY) if o.properties else None)
            for o in objs)
        menu = QMenu(self)
        menu.addAction("复制（吸附鼠标）", self._copy_objects_for_paste)
        menu.addSeparator()
        if len(objs) >= 2:
            menu.addAction("组合", self._group_selected_objects)
            if has_group:
                menu.addAction("拆除组合", self._ungroup_selected_objects)
            menu.addSeparator()
        join = menu.addMenu("加入组")
        join.addAction("选择已有组（取消分组）",
                       lambda: self._join_selected_to_group(""))
        for g in self._current_groups():
            join.addAction(g, lambda _g=g: self._join_selected_to_group(_g))
        menu.addSeparator()
        align = menu.addMenu("对齐")
        align.addAction("左对齐", lambda: self._align_selected("left"))
        align.addAction("水平居中", lambda: self._align_selected("hcenter"))
        align.addAction("右对齐", lambda: self._align_selected("right"))
        align.addSeparator()
        align.addAction("顶对齐", lambda: self._align_selected("top"))
        align.addAction("垂直居中", lambda: self._align_selected("vcenter"))
        align.addAction("底对齐", lambda: self._align_selected("bottom"))
        dist = menu.addMenu("分布")
        dist.addAction("水平均匀分布", lambda: self._distribute_selected("h"))
        dist.addAction("垂直均匀分布", lambda: self._distribute_selected("v"))
        menu.addSeparator()
        menu.addAction("删除选中对象", self._delete_selected_object)
        return menu

    # ---------------- 对象复制：吸附鼠标粘贴 ----------------
    def _copy_objects_for_paste(self):
        """把选区做成副本并吸附到鼠标：整组 / 单对象 / 框选区域都支持。

        属性（含 rwmap_group / rwmap_merge_group 等隐藏属性）原样保留；整合体
        的内部关系在粘贴时才换成新 gid，避免副本和原件意外绑在同一个整合体。

        来源组名只在这里记录，真正弹窗放到左键按下放置时；每个来源组依次问一次。
        """
        from copy import copy, deepcopy
        from ..document.properties import EDITOR_GROUP_PROPERTY

        objs = list(self.scene.selected_objects)
        if not objs or self.state.doc is None:
            return

        # 选区覆盖某组的全部成员 → 按「整组复制」
        is_group, group_name = False, ""
        groups = {(o.properties.get(EDITOR_GROUP_PROPERTY) or "")
                  for o in objs if o.properties is not None}
        groups.discard("")
        if len(groups) == 1:
            g = next(iter(groups))
            members = [o for layer in self.state.doc.layers
                       if hasattr(layer, "objects") for o in layer.objects
                       if o.properties is not None
                       and (o.properties.get(EDITOR_GROUP_PROPERTY) or "") == g]
            chosen = {self._obj_key(o) for o in objs}
            if members and all(self._obj_key(m) in chosen for m in members):
                is_group, group_name = True, g
                objs = members

        # 按选中顺序去重：框选里混了多个组时，放置时逐个组依次弹窗。
        group_names = list(dict.fromkeys(
            (o.properties.get(EDITOR_GROUP_PROPERTY) or "")
            for o in objs if o.properties is not None))
        group_names = [g for g in group_names if g]

        ax, ay = self._paste_right_xy
        templates: list = []
        layers: list = []
        for obj in objs:
            clone = copy(obj)
            clone.xml_element = None      # 复用原 DOM 会覆盖原对象
            clone.points = [tuple(p) for p in obj.points]
            clone.text_style = dict(obj.text_style)
            clone.properties = deepcopy(obj.properties)
            clone.x -= ax
            clone.y -= ay
            templates.append(clone)
            layers.append(next(iter(self._layers_of_objects([obj]) or [None]), None))

        self._paste_clones = templates
        self._paste_meta = {
            "is_group": is_group, "group_name": group_name,
            "group_names": group_names, "layers": layers}
        self._paste_anchor = (ax, ay)
        self.view.paste_mode = True
        self.view.setCursor(Qt.CrossCursor)
        # 静态选中框留在原对象上会和虚影形成两层框，粘贴期间只显示虚影。
        self.scene.set_paste_selection_hidden(True)
        self._update_paste_preview(ax, ay)
        label = f"整组「{group_name}」" if is_group else f"{len(templates)} 个对象"
        self.statusBar().showMessage(
            f"已复制{label}：左键放置，Shift 连续粘贴，Ctrl 并入原组，右键 / Esc 取消")

    @staticmethod
    def _obj_key(obj):
        from rwmap.document.map_object import object_uid
        return object_uid(obj)

    def _on_paste_move(self, x, y):
        self._update_paste_preview(x, y)

    def _update_paste_preview(self, x, y):
        if not self._paste_clones:
            return
        # 模板坐标已减掉锚点，直接加鼠标位置：锚点就是鼠标「抓住」的那一点
        dx, dy = x, y
        doc = self.state.doc
        tw = doc.tile_w if doc is not None else 20
        th = doc.tile_h if doc is not None else 20
        rects = []
        for t in self._paste_clones:
            bx, by, bw, bh = t.bounds(tw, th)
            rects.append((bx + dx, by + dy, bw, bh))
        self.scene.set_paste_preview(rects)

    def _on_paste_place(self, x, y, modifiers):
        if not self._paste_clones or self.state.doc is None:
            self._cancel_object_paste()
            return
        from copy import copy, deepcopy
        import time
        from ..document.properties import (
            EDITOR_GROUP_PROPERTY, MERGE_GROUP_PROPERTY)
        from ..commands.object_command import capture, push_object_edit

        meta = self._paste_meta
        # 左键按下（放置）时才逐组弹命名窗口；按住 Ctrl 则直接并入原来的组。
        from ..widgets.copy_group_dialog import CANCEL, KEEP_ORIGINAL
        group_names = list(meta.get("group_names") or [])
        if modifiers & Qt.ControlModifier:
            group_map = {g: g for g in group_names}
        else:
            group_map = {}
            for g in group_names:
                action, new_name = ask_copy_group_name(self, g)
                if action == CANCEL:
                    return          # 取消：本次不放置，仍保持粘贴模式
                group_map[g] = g if action == KEEP_ORIGINAL else (new_name or g)

        # 模板已减掉锚点，鼠标位置即新锚点
        dx, dy = x, y
        # 每次粘贴给副本一套新的整合体 gid：副本之间仍是同一整合体，
        # 但不会和原件（或上一次粘贴的副本）串到一起。
        gid_map: dict[str, str] = {}
        for t in self._paste_clones:
            old = str(t.properties.get(MERGE_GROUP_PROPERTY) or "")
            if old and old not in gid_map:
                gid_map[old] = f"m{time.time_ns()}_{len(gid_map)}"

        layers = [l for l in meta.get("layers", []) if l is not None]
        if not layers:
            self._cancel_object_paste()
            return
        before = capture(layers)
        # push_object_edit 会用 after 快照整层替换 objects，粘贴进来的实例会被
        # 换成快照副本；记下追加起点，push 后据此找回真正留在文档里的对象。
        base_len = {id(layer): len(layer.objects) for layer in layers}
        next_id = self._new_object_id()
        placed = []
        for template, layer in zip(self._paste_clones, meta.get("layers", [])):
            if layer is None:
                continue
            obj = copy(template)
            obj.object_id = next_id
            next_id += 1
            obj.xml_element = None
            obj.points = [tuple(p) for p in template.points]
            obj.text_style = dict(template.text_style)
            obj.properties = deepcopy(template.properties)
            obj.x += dx
            obj.y += dy
            old_group = str(obj.properties.get(EDITOR_GROUP_PROPERTY) or "")
            if old_group in group_map:
                obj.properties.set(
                    EDITOR_GROUP_PROPERTY, group_map[old_group], "string")
            old_gid = str(obj.properties.get(MERGE_GROUP_PROPERTY) or "")
            if old_gid in gid_map:
                obj.properties.set(MERGE_GROUP_PROPERTY, gid_map[old_gid], "string")
            layer.objects.append(obj)
            placed.append(obj)
        for layer in layers:
            layer.dirty = True
        self.state.doc.dirty = True
        pushed = push_object_edit(
            self.state.doc, self.state.undo_stack, before,
            f"粘贴 {len(placed)} 个对象", self._refresh_objects)
        if not pushed:
            self._refresh_objects()
        fresh = [o for layer in layers
                 for o in layer.objects[base_len[id(layer)]:]]
        self.scene.set_selected_objects(fresh)
        if modifiers & Qt.ShiftModifier:
            self._update_paste_preview(x, y)
            self.statusBar().showMessage(
                f"已粘贴 {len(placed)} 个对象：继续左键放置，右键 / Esc 结束")
        else:
            self._cancel_object_paste(silent=True)
            self.statusBar().showMessage(
                f"已粘贴 {len(placed)} 个对象（Ctrl+Z 可撤销）")

    def _cancel_object_paste(self, silent: bool = False):
        if not self._paste_clones:
            return
        self._paste_clones = []
        self._paste_meta = {}
        self.view.paste_mode = False
        self.scene.clear_paste_preview()
        self.scene.set_paste_selection_hidden(False)
        self.view.unsetCursor()
        if not silent:
            self.statusBar().showMessage("已取消对象复制")

    def _show_object_context_menu(self, x, y):
        menu = self._build_object_context_menu()
        if menu is None:
            return
        global_pos = self.view.mapToGlobal(self.view.mapFromScene(QPointF(x, y)))
        menu.exec(global_pos)

    def _on_object_press(self, x: float, y: float):
        """S 工具按下：先判缩放箭头，其次框内拖动整组，再判点选对象，空白清空。"""
        handle = self._selection_handle_at(x, y)
        if handle is not None:
            self._begin_selection_resize(x, y, handle)
            return
        box = self._obj_selection_box()
        if self.scene.selected_objects and box is not None and box.contains(x, y):
            self._begin_selection_move(x, y)
            return
        layer, obj = self._hit_object(x, y)
        if obj is not None:
            self._select_object_with_group(obj)
            self._begin_selection_move(x, y)
            return
        # 点空白：清空当前对象选区。
        self.scene.set_selected_objects([])
        self._sel_move_drag = None

    def _on_object_move(self, x: float, y: float):
        if self._sel_resize_drag is not None:
            self._do_selection_resize(x, y)
        elif self._sel_move_drag is not None:
            self._do_selection_move(x, y)

    def _on_object_release(self, _x: float, _y: float):
        if self._sel_resize_drag is not None:
            self._end_selection_resize(_x, _y)
        elif self._sel_move_drag is not None:
            self._end_selection_move(_x, _y)

    def _delete_selected_object(self):
        # Delete 已被本 QAction 占用；钢笔模式下改为删最后一个锚点，
        # 不能误删画布上选中的 MapObject。
        if self._object_tool == "pen" and self._pen_path is not None:
            self._pen_delete_last()
            return
        objs = list(self.scene.selected_objects)
        if not objs or self.state.doc is None:
            return
        # 按所在层分组删除；各层合成一条撤销记录。ObjectLayer 不可哈希，用 id 分组。
        by_layer: dict = {}
        for o in objs:
            layer = self._object_layer_of(self.state.doc, o)
            if layer is not None:
                by_layer.setdefault(id(layer), (layer, []))[1].append(o)
        from ..commands.object_command import capture, push_object_edit
        before = capture([layer for layer, _group in by_layer.values()])
        for _lid, (layer, group) in by_layer.items():
            # 旧代码 {o.object_id for o in group} 在重复 object_id 的地图上会一次
            # 删掉整层同 id 对象。改为按稳定身份精确收集要删的下标，每个选中
            # 对象最多删 1 个；无 xml_element 的旧引用才退回 object_id 兜底。
            drop = set()
            for sel in group:
                hit = next((i for i, o in enumerate(layer.objects)
                            if i not in drop
                            and object_uid(o) == object_uid(sel)), None)
                if hit is None:
                    hit = next((i for i, o in enumerate(layer.objects)
                                if i not in drop
                                and o.object_id == sel.object_id), None)
                if hit is not None:
                    drop.add(hit)
            layer.objects = [o for i, o in enumerate(layer.objects)
                             if i not in drop]
            layer.dirty = True
        self.scene.set_selected_objects([])
        push_object_edit(self.state.doc, self.state.undo_stack, before,
                         f"删除 {len(objs)} 个对象", self._refresh_objects)
        self.statusBar().showMessage(
            f"已删除 {len(objs)} 个对象（Ctrl+Z 可撤销）")

    def _on_zoom(self, value: float):
        self._zoom = value
        self.status_zoom.setText(f"{int(value * 100)}%")
        # 缩放箭头是屏幕固定像素，视图倍率变了要同步重算包围盒 / 箭头尺寸。
        self.scene.refresh_selection_handles_scale(value)

    def _toggle_grid(self, checked: bool):
        # setVisible 变化由 Qt 标脏该 item，只重绘视口即可。
        # 不再调用无 rect 的 scene.update()：覆盖全图的 overlay 整层
        # update() 会把整张地图标脏（README.md 明确禁止）。
        self.scene.set_grid_visible(checked)
        self.view.viewport().update()

    def _show_probe(self):
        if self.probe_window is None:
            self.probe_window = ProbeWindow(self)
        self.probe_window.show()
        self.probe_window.raise_()

    # ---------------- Tiled「地图」菜单的通用功能 ----------------
    def _add_tileset_files(self, paths, label="添加外部图块"):
        """把一个 / 多个 .tsx 作为外部图集引用加入当前地图（可一条撤销还原）。"""
        if not self._can_transform():
            self.statusBar().showMessage("当前版本只读，无法添加图集")
            return 0
        doc = self.state.doc
        if doc is None or not getattr(doc, "tree", None):
            self.statusBar().showMessage("请先打开一张地图")
            return 0
        from ..io.tsx_reader import read_tsx
        from ..commands.document_command import AddExternalTilesetCommand

        base_dir = as_path(doc.path).parent
        existing = {(ts.source or "") for ts in doc.tilesets}
        added = 0
        self.state.undo_stack.beginMacro(label)
        try:
            for raw in paths:
                tsx = as_path(raw)
                if not tsx.is_file():
                    continue
                # source 用相对地图目录的正斜杠路径，保真写回后 Tiled 能解析。
                try:
                    source = os.path.relpath(tsx, base_dir).replace("\\", "/")
                except ValueError:            # 跨盘符无法算相对路径
                    source = str(tsx).replace("\\", "/")
                if source in existing:
                    continue
                firstgid = max((t.last_gid for t in doc.tilesets), default=0) + 1
                try:
                    ts = read_tsx(tsx, firstgid, doc.resolver, source)
                except Exception as exc:      # noqa: BLE001
                    self.statusBar().showMessage(f"读取图集 {tsx.name} 失败: {exc}")
                    continue
                cmd = AddExternalTilesetCommand(
                    doc, ts, source, firstgid, self._refresh_document, label)
                self.state.undo_stack.push(cmd)
                existing.add(source)
                added += 1
        finally:
            self.state.undo_stack.endMacro()
        if added:
            self.statusBar().showMessage(f"已添加 {added} 个外部图集（一条撤销可还原）")
        return added

    def _add_external_tileset(self):
        doc = self.state.doc
        start = str(as_path(doc.path).parent) if doc is not None else ""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加外部图块（.tsx）", start, "Tiled 图集 (*.tsx)")
        if paths:
            self._add_tileset_files(paths, "添加外部图块")

    def _add_rules_tileset(self):
        """把自动映射规则依赖的地形图集加入当前地图。

        RW 规则靠固定的 gid 对应基础地形（126 草 / 186 水 等），因此默认定位到
        规则地形图集目录，让用户按需选取，避免整批塞入打乱现有 gid。
        """
        doc = self.state.doc
        start = str(as_path(self.state.profile.mods_maps) / "terrain")
        if not as_path(start).is_dir():
            start = str(as_path(self.state.profile.tilesets_root))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加自动映射规则图集（.tsx）", start,
            "Tiled 图集 (*.tsx)")
        if paths:
            n = self._add_tileset_files(paths, "添加自动映射规则图集")
            if n:
                self.statusBar().showMessage(
                    f"已添加 {n} 个规则图集——注意 RW 规则按固定 gid 匹配，"
                    "请确认图集 firstgid 与官方地形一致")

    def _autocrop(self):
        """自动裁剪：按瓦片层内容的 bbox 裁剪，对象层只跟着平移（一条撤销可还原）。"""
        if not self._can_transform():
            self.statusBar().showMessage("当前版本只读，无法裁剪地图")
            return
        from ..commands.layer_command import MapTransformCommand
        from ..document.layer import TileLayer
        import copy

        doc = self.state.doc
        tw, th = doc.tile_w, doc.tile_h
        min_c, min_r, max_c, max_r = doc.width, doc.height, -1, -1
        # 只按瓦片层非空格算边界；对象不参与，避免远处对象把地图撑大
        for layer in doc.tile_layers():
            for r in range(doc.height):
                base = r * doc.width
                for c in range(doc.width):
                    if int(layer.cells[base + c]) & GID_MASK:
                        min_c, max_c = min(min_c, c), max(max_c, c)
                        min_r, max_r = min(min_r, r), max(max_r, r)
        if max_c < min_c or max_r < min_r:
            self.statusBar().showMessage("地图为空，无需裁剪")
            return
        nw, nh = max_c - min_c + 1, max_r - min_r + 1
        if (nw, nh) == (doc.width, doc.height):
            self.statusBar().showMessage("内容已占满整图，无需裁剪")
            return

        def shifted(layer):
            if isinstance(layer, TileLayer):
                cells = [0] * (nw * nh)
                for r in range(min_r, max_r + 1):
                    for c in range(min_c, max_c + 1):
                        cells[(r - min_r) * nw + (c - min_c)] = layer.get(c, r)
                return TileLayer(
                    width=nw, height=nh, name=layer.name, cells=cells,
                    readonly=layer.readonly, opacity=layer.opacity,
                    visible=layer.visible, locked=layer.locked,
                    offsetx=layer.offsetx, offsety=layer.offsety,
                    encoding="base64", compression="gzip", dirty=True)
            objs = []
            for o in layer.objects:
                n = copy.deepcopy(o)
                n.xml_element = None
                n.x -= min_c * tw
                n.y -= min_r * th
                objs.append(n)
            return ObjectLayer(
                name=layer.name, objects=objs, opacity=layer.opacity,
                visible=layer.visible, locked=layer.locked,
                offsetx=layer.offsetx, offsety=layer.offsety)

        before = list(doc.layers)
        after = [shifted(layer) for layer in doc.layers]
        old_dims = (doc.width, doc.height, tw, th)
        new_dims = (nw, nh, tw, th)
        cmd = MapTransformCommand(
            doc, before, after, old_dims, new_dims,
            "自动裁剪", self._refresh_document)
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage(f"已自动裁剪为 {nw}×{nh}（一条撤销可还原）")

    def _goto_tile(self):
        """转到指定格坐标并居中（纯视图，不改文档）。X/Y 一次输入、一次确认。"""
        doc = self.state.doc
        if doc is None:
            self.statusBar().showMessage("请先打开一张地图")
            return
        from ..widgets.goto_tile_dialog import GotoTileDialog
        dlg = GotoTileDialog(self, max_col=doc.width - 1,
                             max_row=doc.height - 1)
        if dlg.exec() != QDialog.Accepted:
            return
        col, row = dlg.values()
        self.view.centerOn(QPointF(
            (col + 0.5) * doc.tile_w, (row + 0.5) * doc.tile_h))
        self.statusBar().showMessage(f"已转到图块 ({col}, {row})")

    def _switch_tileset(self, delta: int):
        """循环切换「图块集」面板的当前标签（纯视图）。"""
        tabs = self.tilesets_dock.tabs
        n = tabs.count()
        if n == 0:
            return
        tabs.setCurrentIndex((tabs.currentIndex() + delta) % n)

    def _edit_map_properties(self):
        """编辑地图根 <properties> 的键值对（可一条撤销还原）。"""
        from ..commands.document_command import EditMapPropertiesCommand
        from ..widgets.map_properties_dialog import MapPropertiesDialog

        doc = self.state.doc
        if doc is None:
            self.statusBar().showMessage("请先打开一张地图")
            return
        dlg = MapPropertiesDialog(dict(doc.properties.items()), self)
        if dlg.exec() != QDialog.Accepted:
            return
        before = dict(doc.properties.items())
        after = dlg.values()
        if after == before:
            return
        if not self._can_transform():
            self.statusBar().showMessage("当前版本只读，无法修改地图属性")
            return
        cmd = EditMapPropertiesCommand(
            doc, doc.properties, after, self._refresh_document)
        self.state.undo_stack.push(cmd)
        self.statusBar().showMessage("地图属性已更新（一条撤销可还原）")

    # ---------------- 自动映射 ----------------
    def _load_automap_rules(self, path=None) -> bool:
        mapper = AutoMapper(self.state.profile, path)
        info = mapper.load()
        if not info.get("ok"):
            detail = info.get("error") or "; ".join(info.get("errors") or []) or "无规则"
            self.statusBar().showMessage(f"自动映射规则加载失败: {detail}")
            return False
        self._automapper = mapper
        self.statusBar().showMessage(
            f"已加载 {info['rules']} 条自动映射规则（{len(info['rule_maps'])} 个规则图）"
        )
        return True

    def _pick_rules_file(self):
        start = str(as_path(self.state.profile.mods_maps) / "rules.txt")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 rules.txt", start, "Tiled 规则索引 (rules.txt *.txt)")
        if not path:
            return
        if self._load_automap_rules(path):
            self.statusBar().showMessage(
                f"已加载规则文件 {path}  规则 {len(self._automapper.rules)} 条"
            )

    def _restore_automap_set_layer(self):
        """关闭绘制时自动地图时，把 set 层的显示状态恢复成开启前的样子。"""
        saved = self._automap_set_saved
        self._automap_set_saved = None
        if not saved:
            return
        doc_id, visible, opacity, previous_layer = saved
        doc = self.state.doc
        if doc is None or id(doc) != doc_id:
            return
        set_layer = doc.layer_by_name("set")
        if set_layer is not None:
            set_layer.visible = bool(visible)
            set_layer.opacity = opacity
            self.scene.set_layer_visible(set_layer, bool(visible))
            self.scene.set_layer_opacity(set_layer, opacity)
        self.layers_dock.set_document(doc)
        if previous_layer in doc.layers:
            self.state.current_layer = previous_layer
            self.scene.current_layer = previous_layer
            self.scene.view_current_layer = previous_layer
            self.layers_dock.select_layer(previous_layer)

    def _toggle_automap(self, checked: bool):
        self._automap_while_drawing = bool(checked)
        if not checked:
            self._restore_automap_set_layer()
            self.statusBar().showMessage("绘制时自动地图：关")
            return
        if self._automapper is None and not self._load_automap_rules():
            self.act_automap_while.blockSignals(True)
            self.act_automap_while.setChecked(False)
            self.act_automap_while.blockSignals(False)
            self._automap_while_drawing = False
            return
        doc = self.state.doc
        set_layer = doc.layer_by_name("set") if doc is not None else None
        if set_layer is not None:
            if (self._automap_set_saved is None
                    or self._automap_set_saved[0] != id(doc)):
                self._automap_set_saved = (
                    id(doc), set_layer.visible, set_layer.opacity,
                    self.state.current_layer,
                )
            # 只改内存显示状态，保真写回不动 XML 的 visible/opacity。
            set_layer.visible = True
            set_layer.opacity = min(set_layer.opacity or 1.0, 0.35)
            self.scene.set_layer_visible(set_layer, True)
            self.scene.set_layer_opacity(set_layer, set_layer.opacity)
            self.layers_dock.set_document(doc)
            self.layers_dock.select_layer(set_layer)
            self.state.current_layer = set_layer
            self.scene.current_layer = set_layer
            self.scene.view_current_layer = set_layer
        self.statusBar().showMessage(
            "绘制时自动地图：开（实时）—— 落笔固定写 set 层，任意基础地形瓦片"
            "自动归一到该图集首格（126 草 / 186 水 / 1 山 / 36 雪 / 116 冰 / 176 土 / 770 沙），"
            "画笔 / 填充 / 钢笔落笔后 Ground 与 Items 自动生成过渡"
        )

    def _automap_live(self, bounds):
        """每画一格立刻重算 bounds±AutomappingRadius，不等松开鼠标。"""
        if not (self._automap_while_drawing and self._automapper is not None):
            return
        self._automap_total += self._apply_automap(area=bounds, macro=False)

    def _run_automap(self, area=None, source_layer=None):
        """菜单 / Ctrl+M 的手动全图自动映射；兼容旧的局部调用签名。"""
        if source_layer is not None and source_layer.name.lower() != "set":
            return
        self._apply_automap(area=area, macro=True)

    def _apply_automap(self, area=None, macro=True) -> int:
        """把自动映射结果写进文档并登记撤销，返回改动格数。"""
        doc = self.state.doc
        if doc is None:
            return 0
        if self._automapper is None and not self._load_automap_rules():
            return 0
        changes = self._automapper.compute(doc, area=area, only_diff=True)
        if not changes:
            if macro:
                self.statusBar().showMessage("自动映射：无变化")
            return 0
        stack = self.state.undo_stack
        if macro:
            stack.beginMacro("自动映射")
        total = 0
        for target, entries in changes.items():
            layer = doc.layer_by_name(target)
            if layer is None:
                continue
            diffs = []
            for col, row, gid in entries:
                index = layer.index(col, row)
                old = layer.cells[index]
                if (old & GID_MASK) != gid:
                    diffs.append((index, old, gid))
            if diffs:
                stack.push(DrawCommand(doc, layer, diffs, self._refresh_layer,
                                       text=f"自动映射 {target}",
                                       merge_id="rwmap-automap-draw"))
                total += len(diffs)
        if macro:
            stack.endMacro()
            self.statusBar().showMessage(
                f"自动映射：更新 {total} 格 / {len(changes)} 层"
                + ("（局部）" if area else "（全图）")
            )
        return total
