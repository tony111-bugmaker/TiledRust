# 铁锈战争专用地图编辑器（rwmap）· 首版框架

借鉴 Tiled 的文档模型 / 图层 / 图块集 / 撤销栈 / Dock 面板，专为铁锈战争 `.tmx` 适配。

## 启动

```bat
start.bat
```

或：

```bat
.venv\Scripts\python.exe run.py
```

- 依赖：Python 3.12 + PySide6 / Pillow / lxml（已装在项目 `.venv`）
- 首次启动自动打开 `1.15\mods\maps\[p2]example_skirmish_(2p).tmx`
- 不自动打开：`run.py --no-sample`
- 无 GUI 跑探针：`run.py --probe`

## 首版能力

| 能力 | 状态 |
| --- | --- |
| 打开 1.14 / 1.15 官方 `.tmx`，正确渲染 5 层 | ✅ |
| 图层 / 图块集 / 属性 / 单位 / Triggers Dock | ✅（Triggers 可编辑；图层面板 Tiled 风格；5 个面板共用统一深色标题栏） |
| 图层面板 Tiled 风格：原生图标 / 行内眼睛·锁 / 图标按钮栏 / 双击重命名 / 右键菜单（深色主题） | ✅ |
| 设置 → 面板列表设置 / 导入面板 / 保存当前面板（5 个面板清单、显隐勾选、布局 JSON 导入导出） | ✅ |
| 图块集面板：底部横向标签栏 + 整张图集原位置显示 + 矩形框选盖章 | ✅ |
| 绘制时自动映射（官方 `rules.txt`，set → Ground / Items；画笔逐格实时，填充 / 钢笔落笔时局部重算；任意基础地形瓦片归一到图集首格，钢笔线正反双向补齐标记空洞） | ✅ |
| 画笔 / 橡皮 / 临时吸管（I 键或 Alt+左键，不切工具）、拖拽聚合撤销、Ctrl+Z/Y | ✅ |
| 保真写回（只改被编辑层 `<data>`，不改层顺序/未知属性） | ✅ |
| 版本切换（1.14 只读优先 / 1.15 可写） | ✅ |
| 缩略图、导出到 `mods/maps` 并补资源副本、`.rwmod` 打包 | ✅ |
| 内建自测窗口（视图 → 自测 / 探针） | ✅ |
| Triggers 语义编辑（属性增删改、名称 / 类型 / 坐标） | ✅ |
| 对象可视化编辑（选中 / 拖动 / 8 手柄自由缩放 / Delete 删除，一步撤销） | ✅ |
| 对象默认自由坐标，按住 `Shift` 才吸附瓦片网格 | ✅ |
| 对象层保真写回 `<objectgroup>` | ✅ |
| Tiled 插入对象工具（矩形 / 椭圆 / 点 / 多边形 / 折线 / 图块 / 模板 / 文字 / 图片 + 顶点编辑） | ✅ |
| 对象形状保真（`<text>` / `<polygon>` / `<polyline>` / `<ellipse>` / `image` / `template`） | ✅ |
| Wang 地形笔刷 | 官方 24 张图均无 `<wangsets>`；地形过渡已由「绘制时自动地图」承担 |
| exe 打包 | 首版不做 |

## 第二轮修复（缩放 / 流畅度 / 配色）

三个问题的根因与处理，均带回归测试：

1. **缩放跳到左上角**：`MapView` 完全接管了 `mouseMoveEvent` 且没调用基类，
   Qt 内部的「最后鼠标位置」永远停在 `(0,0)`，`AnchorUnderMouse` 于是把左上角当锚点。
   改为 `NoAnchor` + 自算锚点（`zoom_at()`：缩放前后 `mapToScene` 求差再 `translate` 补偿），
   实测连续 5 次缩放锚点漂移为 0。缩放区间放宽到 `0.05~8`，
   避免 160×160 地图 fit 后约 0.26 被旧的 0.25 下限「弹回」。
2. **卡顿**：网格光标每次移动调用 `grid.update()`（覆盖全图的 boundingRect），
   配合 `BoundingRectViewportUpdate` 会让 Qt 重绘 3200×3200 场景，实测约 640ms/帧。
   改成只标脏「旧格 + 新格」两个小矩形 + `MinimalViewportUpdate`；
   密集层在缩放 ≤0.75 时用半分辨率预渲染缓存（编辑时只重画受影响的格）。
   实测 60 次「移动光标 + 强制重绘」从 ~38 秒降到 ~0.39 秒。
3. **紫色草地 / 粉色地块**：官方外部 `.tsx` 普遍不写 `tilecount` / `columns`，
   旧代码按 0 处理，导致 18 个图块集整段 GID 找不到图块 → 画成紫色占位图。
   现在按 `<image>` 实际 PNG 尺寸用 Tiled 规则补算
   `(宽 - 2*margin + spacing) / (tile + spacing)`；示例图 30 个图块集区间首尾相接、
   所有 GID 100% 命中。修复后与 `tmxrasterizer` 渲染逐像素比对，平均通道差 3.39/255。
   `Dirt`（深棕土块）等图块集也从「面板里一块都没有」变为正常显示 10 块。

> 画布上那些浅棕/偏粉的山地区块是游戏自带的 `Mountain` 图块，
> Tiled 渲染出来同样是这个颜色；不是渲染错误。真正变暗棕的是 `Dirt` 图块集。
> `set` 层（官方说明「游戏完全忽略」）现在默认隐藏，可在图层面板勾选显示。

## 第三轮：图块集面板改成 Tiled 样式

`rwmap/widgets/tilesets_dock.py` 从「下拉框 + 带序号的图标列表」重写为：

- 顶部 `QTabBar`：每个 tileset 一个标签，只显示名称（`Mountain` / `Dirt` / …），
  不带 `firstgid` 前缀、不带序号；放不下时用左右滚动按钮，不把名称压成首字母。
  完整名称与 gid 区间放在标签 tooltip 里。
- 图块区改为自绘 `TilePalette`：纯图块、无文字、2px 间隔、灰底；
  只绘制视口可见格，切换图块集不再为上千个图块创建 `QListWidgetItem` + `QIcon`。
  实测连续切换 30 个图块集约 15ms；悬停/选中只标脏两格。
  游戏图集里整块透明的空位（如 `Dirt` 的 3、4 号）画淡边框，避免误以为缺图。
- 默认停靠在**右侧最下层**（第十轮起；早期版本在底部横跨），标签栏仍可横向滚动；
  dock 仍可自由拖动。
- `gidSelected` / `tileSelected` 信号与 `select_tileset(firstgid)` 公开接口保持不变，
  隐藏的 `combo` 是旧调用方的兼容镜像。
- 顺带加固 `tile_cache.get_pixmap()` 的缓存键：只用 `id(ts)` 会在切换地图后
  CPython 复用对象地址时串图，现补上 firstgid / 名称 / 图集 / 切图参数。
- 图块集停靠在底部 / 右侧后画布会变窄，fit 后地图比视口窄，Qt 会强制居中并让滚动条范围归零，
  缩放锚点出现约 1px 漂移（回归测试抓到）。现在 `MapScene` 给 `sceneRect` 加一圈
  4 倍地图尺寸的虚拟边距，`MapView._fit_if_ready()` 仍只按地图矩形 fit；
  滚动条始终有行程，连续缩放 / 平移后缩放 / 缩到 0.05 下限的锚点漂移均为 0。

## 第四轮：整图集视图 / 矩形选区 / 自动映射

1. **配色回到深色**：图块集面板（标签栏、图块区、滚动条）改回与主界面一致的深色，
   不再是一条突兀的浅灰带。
2. **图集不再切开重排**：`TilePalette` 改为把整张 PNG 按原始像素 1:1 画出来，
   图块留在原来的位置，叠加 `margin/spacing/columns` 网格线。
   `trans="008a76"` 的青色在 `load_tileset_image()` 里已由 `ImageChops` 转成 alpha=0，
   透明处直接露出深色面板底 —— 例如 `ice2ice_flat.png`（60×140、3×7 共 21 块，
   青色占 42.6%）现在是冰蓝图块 + 透明空位，不再是青色方块。
3. **矩形选区**：在图集上按住左键拖拽即可框选任意 W×H 的矩形，蓝色高亮；
   松开后转成 2D gid 数组，画笔在画布上按该矩形一次盖章（`AppState.begin_stamp`
   复用 `DrawCommand` 的 cell diff，整笔一条撤销）。
4. **自动映射（Tiled `Ctrl+M` / 「绘制时自动地图」）**：
   - 规则资产用游戏自带 `mods/maps/rules.txt` → `rules/ruleset1~3.tmx`，
     共解析出 **274 条规则**；「地图」菜单：`添加 Automapping 规则文件…`、
     `自动映射 (Ctrl+M)`（全图）、`绘制时自动地图`（开关，工具栏也有按钮）。
     开启后自动显示半透明的 `set` 层并把当前层切过去；
     只改内存显示状态，`visible` / `opacity` 不写回 XML。
   - 规则语义与实时行为见下一节「第五轮」，本节的 94.82% 是修复前的旧基线。

## 第五轮：绘制时自动地图实时化 + 结果修正

用户反馈「结果不对、不是实时」：盖章后地图出现一条 2 格宽深色竖条，且要松开鼠标
才生效。定位到四个真实缺陷，逐条修掉：

1. **不是实时**：旧实现只在 `cellReleased` 之后跑一次 `compute()`。现在
   `AppState.begin_stroke(..., on_change=...)` 在每格（或矩形盖章的整块）写入后
   立刻回调 `MainWindow._automap_live()`，按下即出地形；松开时再按整笔
   `bounds` 做一次统一重算，收敛到与「一次性重算」完全相同的结果。
   整笔的「绘制 + 所有自动映射」用 `QUndoStack.beginMacro/endMacro` 合成
   **一条撤销**，Ctrl+Z 一次全部还原。
2. **深色竖条 = 空洞**：旧实现把 `DeleteTiles` 理解为「清空整条规则 bbox」，
   局部扫描又只向左上外扩，补写规则漏掉，于是 `Ground` 被清成 0，
   露出深色背景 + 半透明 `set` 层（截图里上蓝下绿的竖条）。
   现在局部重算窗口先用 `set` 层基础地形（`1/36/116/126/176/186/770…`
   各基础图集第 1 格，按地图动态推断）重建 `Ground`，再由规则输出覆盖，
   **任何情况下都不会写出 0**；输出严格裁剪到 `bounds ± AutomappingRadius`。
3. **规则解析不符合 Tiled 旧式规则**（查过 Tiled 1.8/1.12 官方文档）：
   - `regions_input` / `regions_output` 必须配对，`ruleset3` 第一条是
     1×1 输入 / 3×3 输出，旧实现只取了 1 格输出；
   - `input_set_decoration` 检查的是名为 `set_decoration` 的层（本作没有 →
     全空层），不是 `set` 层的索引，旧实现把 372 当成 `set` 条件导致规则冲突；
   - 旧式「空 input 格通配」：无 `inputnot` 层时，**区域内** input 全空的坐标
     仍要求「非空且不等于本规则用过的 gid」；注意只对真正属于区域的格子生效，
     外接矩形补出来的缺口不算（见第六轮）；
   - 多条规则写同一格时改为按规则图位置**确定性定优先级**，
     不再依赖扫描先后（逐格实时与全图重算因此结果一致）。
4. **性能**：规则索引缓存 + 按「输出相对锚点的范围」O(1) 排除够不到窗口的规则。
   局部重算 **1.6–3 ms/格**，12 格拖拽 + 松手收敛约 39 ms。

**验收数据（第六轮后已更新）**：清空官方示例图 `Ground` 后全图重建，与官方逐格比对
**100.000%**（第五轮 97.93%，最初 94.82%），`Ground` 零空洞；
在**未修改的官方原图**上直接按 `Ctrl+M` 改动格数为 **0**；
局部实时重算与「清空后全图重建」在窗口内 **100% 一致**；
逐格实时 + 松手收敛与一次性重算 100% 一致；
在水面盖 3×3 草会自动长出深色岸线过渡瓦片（见 `logs/实时自动映射_水塘验收.png`）。

使用要点：`set` 层要画**各基础地形图集的第 1 格**作为标记
（`1` 山 / `36` 雪 / `116` 冰 / `126` 草 / `176` 土 / `186` 水 / `770` 沙…）。
选了过渡图集或单位图集的图块不会有地形输出，状态栏会提示。
新图从 `set` 层画起体验最好；在官方图上按 `Ctrl+M` 现在也是安全的（零改动）。

## 第六轮：内凹拐角（「没考虑那个拐角」）

用户截图里 L 形 / T 形草地伸进水里，横条下沿与竖条右沿的深绿岸线各自画到拐角前
就断掉，中间露水色，没有包角瓦片。逐格差异分析显示丢失的 531 格全部集中在
水(186) ↔ 草(126) 的斜向 / 拐角边界：官方用 `812/814/818/820/845/847/851/853…`
这类拐角瓦片，旧实现只写出 `815/817/836/838…` 直边瓦片。

**根因**：`ruleset1.tmx` 的拐角规则区域是 **2×2 缺一角的 L 形连通块**
（如 y=35 那条：`regions` 只有 (35,35)(36,35)(35,36)，输入 186/126/126，
输出 820 在 (36,35)）。旧解析把连通块取成**外接矩形**，给凭空多出来的右下角
补了一条「非空且不等于本规则用过的 gid」的通配条件；而那个位置在实际地图上
几乎必然是草或水，于是 **62 条 L 形拐角规则永远匹配不上**。

**修复**（`rwmap/rw/automapping.py::_parse_rulemap`）：`_components()` 已经返回
连通块的绝对格子索引，现在把它转成相对坐标集合 `in_cells` / `out_cells`，
输入条件、通配条件、输出收集都只遍历**属于该区域的格子**，不再用 bbox 补齐。
`regions_input` / `regions_output` 分离的规则图同样按连通块配对（3×3 输出区
也保持原形状）。修复后带通配条件的规则从 134 条降到 2 条，其余全是精确条件。

**验收**：全图复现率 97.93% → **100.000%**；构造 L 形 + T 形场景的
`logs/拐角_修复前后对比.png` 里，修复前凹角是硬直角、岸线断开，修复后连续包成圆角；
逐格实时绘制 L 形也会长出包角瓦片（`(49,47)` = 812）。
新增 4 条回归：L 形规则不得被补 bbox 通配、官方全部拐角瓦片逐格一致、
局部重算不抹掉拐角、逐格实时绘制 + 松手收敛能生成拐角。
诊断脚本在 `rwmap/probe/probe_corner*.py`、`probe_ruleset_grid.py`。

## 第七轮：临时吸管 + Triggers / 对象可视化编辑

用户反馈「删掉吸管的按键，用键直接用这个功能，不需要再切换」，并要求做掉能力表里
`Wang 地形 / Triggers 语义编辑 / 对象可视化编辑` 那行「桩，未实现」。

**临时吸管**：工具栏和「编辑」菜单里的 `吸管 (I)` 按钮已删除，`picker` 从工具表移除，
不再需要「切到吸管 → 点一下 → 切回画笔」。现在：

- 按 **I** 直接取当前光标所在格的图块作为笔刷（去掉翻转标志）；当前层空格时
  会按图层绘制顺序向上找可见层的图块，不会吸出 0；
- **Alt + 左键** 同样临时取色，`MapView` 分流到 `pickRequested` 信号，
  不触发 `cellPressed`，因此不会开始绘制笔划；
- 取色后 `state.tool_id` 不变，同时清掉矩形盖章选区、让图块集面板自动切到
  对应图集并高亮那一格，取完继续用画笔/橡皮画。

**Triggers 语义编辑**：左侧面板从只读树改成可编辑属性表，按对象层 → 对象 → 字段分组。
双击「值」列可改名称 / 类型 / 位置 `x, y, w, h` / 任意自定义属性；
`+ 属性` / `- 属性` / `+ 对象` / `- 对象` 四个按钮做增删改。
所有改动走 `ObjectCommand`（对象层快照）+ `QUndoStack`，Ctrl+Z 一步还原。

**对象可视化编辑**：画布上的对象框现在带名称标签和选中高亮。
**Ctrl + 左键** 点中对象即选中并同步面板，**Ctrl + 拖动** 移动对象
（第九轮起默认自由坐标，按 `Shift` 才吸附网格；本轮最初实现为 20px 吸附），
**Delete** 删除；移动 / 删除都是一条可撤销命令，撤销后选中态按 `object_id` 重新绑定，
不会因为快照替换对象而丢失高亮。

**对象层保真写回**（关键前提）：旧 `tmx_writer` 只改 tile 层的 `<data>`，对象改了也存不下来。
现在对 `ObjectLayer.dirty` 的层同步 `<objectgroup>`：已有对象沿用原 `<object>` 元素
（保留未知属性 / 子节点），只更新已知字段并重建 `<properties>`
（保留 text 型属性和 `type` 标注）；新增对象补元素并推进 `nextobjectid`；
删除对象移除元素。没有编辑对象层时 `<objectgroup>` 字节完全不动。

**Wang 地形**：官方 `mods/maps` 下 24 张 `.tmx`（含 20 张带 `objectgroup`）**没有任何
`<wangsets>`**；铁锈战争的地形过渡由官方 `rules.txt` 自动映射规则承担，
也就是本编辑器已实现的「绘制时自动地图」，所以没有另造一套没有数据源的 Wang 集。
若需要 Tiled 式 Wang 笔刷，需要额外定义地形集，可作为后续独立功能。

**交互速查**：`B` 画笔 / `E` 橡皮 / `I` 或 `Alt+左键` 临时吸管 /
`Ctrl+左键` 选中对象 / `Ctrl+拖动` 移动 / `Delete` 删除 / `Ctrl+M` 自动映射 /
`Shift` 让对象创建、移动、缩放吸附网格；对象工具快捷键见下一节。

回归：新增 `test_picker_hotkey.py`（7 例）与 `test_object_edit.py`（10 例），
单测 48 → **65 全 OK**，包含对象属性写回 TMX 后重新读取、未编辑时字节保真、
新建 / 删除 / 移动 / 撤销链与 Ctrl 交互。

## 第八轮：Tiled「插入对象」工具栏（文字 / 图片 / 全形状）

按用户给的 Tiled 工具栏截图，在主工具栏下方加了**第二行对象工具栏**
（`rwmap/widgets/object_toolbar.py`，第九轮扩到 13 个自绘图标 + 目标对象层下拉；
第十轮把蓝色描边从按钮方块移到图标内部图形）。工具与快捷键：

| 按钮 | 快捷键 | 行为 |
| --- | --- | --- |
| 瓦片画笔（斜放铅笔轮廓） | `B` | 退出对象工具，回到普通瓦片绘制 |
| 橡皮 | `E` | 擦除瓦片，与瓦片画笔同属瓦片编辑模式 |
| 选择 / 框选（鼠标箭头） | `S` / `M` | 第三十四轮合并：点对象选中 / 拖动 / 拖箭头缩放，空白处拖出框选（瓦片层选瓦片、对象层选多个对象），不需要再按 Ctrl |
| 编辑节点 | `N` | 先选中多边形 / 折线，再拖动顶点；一步撤销 |
| 插入矩形 | `R` | 拖出大小（不拖默认 40×40） |
| 插入点 | `P` | 单击放置 |
| 插入椭圆 | `O` | 拖出大小 |
| 插入多边形 | `G` | 逐点单击，双击 / `Ctrl+回车` 结束，`Esc` 取消 |
| 插入折线 | `L` | 同上，至少 2 点 |
| 插入图块 | `T` | 用当前笔刷 `gid` 放一个 tile object |
| 插入模板 | `Shift+T` | 选 `.tx` 模板，字段展开到对象并保留 `template` 属性 |
| 插入图片 | `Shift+I` | 选本地图片，路径写进 `image` 属性，编辑器内真正画出图片 |
| 插入文字 | `X` | 拖出文字框并输入内容，支持字号 / 颜色 / 对齐 / 换行 |

**数据与保真**（`rwmap/document/map_object.py` + `tmx_reader` / `tmx_writer`）：

- `MapObject` 增加 `shape / points / text / text_style / image_source / template`，
  读取时按 `<ellipse/> <point/> <polygon> <polyline> <text>` 判定形状，
  `gid` 推断 tile object，`image` 属性推断图片对象。
- 写回时**形状没变就完全不动原子节点**（只改名字时 `<text>` 字节一致），
  变化时按 Tiled 格式重建；`<text>` 内容作为 character data，`points` 用 `x,y x,y …`。
- **TMX 原生没有 image object**（官方文档 `<object>` 子元素不含 `<image>`，
  Tiled 里图片是 tile object + 图集）。为了不破坏 TMX、又让编辑器能显示图片，
  图片路径存成 `image` 自定义属性（`type="file"`）；Tiled 打开会看到一个带
  `image` 属性的矩形对象，不会报错；若外部文件原本带 `<image>` 子节点则保留。
- 模板对象写回 `template` 属性；`.tx` 解析在 `rwmap/io/template_reader.py`。
- 新增对象沿用 `ObjectCommand` 快照撤销，插入 / 拖拽 / 节点编辑都是一步撤销。

**渲染**：`object_layer_item.py` 按形状分别画矩形 / 椭圆 / 十字点 / 填充多边形 /
折线 / 图块位图 / 文字（字体、颜色、对齐、换行）/ 图片位图，带名称标签、
拖拽虚线预览和顶点手柄。对象框恒为黄色，选中态见第十轮。图片缺失时画占位框，不会崩画布。

**属性面板**新增形状专属字段：文字内容与字号 / 字色 / 对齐 / 换行、图片路径、
点集、图块 `gid`、旋转；双击「值」列即可改，全部走撤销栈。
`TriggersDock._commit()` 现在允许显式传 layer，不依赖树控件当前选中项。

验收图：`logs/第八轮_对象工具_全窗口.png`、`第八轮_对象工具栏特写.png`、
`第八轮_对象形状特写.png`（8 种形状同框，含中文文字与彩色图片）。
回归：新增 `test_object_shapes.py`（15 例），单测 65 → **80 全 OK**。

## 第九轮：自由摆放 / 缩放手柄 / 工具栏精简

- **不再绑网格**：`_snap_xy()` 原来无条件把坐标 `round` 到 20px，矩形四角永远压在瓦片边线上。
  现在默认返回鼠标原始坐标（对象创建、Ctrl 拖动、手柄缩放都自由），**按住 `Shift` 才吸附**；
  `snap=` 参数可显式指定，方便测试。
- **选中即可缩放**：选中矩形 / 椭圆 / 多边形 / 折线 / 图块 / 文字 / 图片（**点对象除外**）时，
  出现四角 / 四边共 8 个可拖拽的缩放点，拖它们改 `x/y/width/height`，多边形 / 折线
  按比例缩放 `points`，一步撤销。当时的视觉是「深色外衬 + 蓝色实线轮廓 +
  白色虚线内框 + 9px 白色方块手柄」，**第十轮已全部换成 8 个纯黄色箭头**。
- **看得见的选择**：选择工具的系统白箭头换成「白箭头 + 蓝色描边 + 深色外衬」自定义光标
  （第十轮保留光标，但选中框不再有蓝色轮廓）。
- **工具栏精简**：主工具栏（「打开 .tmx」那行）把已在「文件 / 编辑 / 视图 / 地图」
  当时 4 个菜单里出现的按钮全部隐藏，只留版本下拉（`_HIDE_MENU_DUPLICATES = False` 可一键恢复）。
  常用开关「绘制时自动地图」挪到图标行右侧，功能不丢。
- **图标行补橡皮**：橡皮按钮放在瓦片画笔与选择对象之间；
  瓦片画笔图标重画成斜放铅笔轮廓。`B` / `E` 仍由主窗口 QAction 注册并同步图标行选中态，
  不会出现两套按钮各选一半。（第九轮曾给按钮方块加蓝色边框，第十轮已移除。）
- **不显示瓦片编号**：图块集面板不叠加编号（`SHOW_TILE_IDS = False`），
  悬停提示与标签 tooltip 都去掉 `gid=` / `firstgid`；画布上无名称的对象也不再画 `#id` 标签。

验收图：`logs/第九轮_工具栏_标注.png`、`logs/第九轮_缩放手柄_标注.png`、
`logs/第九轮_全窗口.png`。
回归：新增 `test_ui_round9.py`（17 例），单测 80 → **97 全 OK**。
（第十轮把其中「蓝色轮廓」「按钮 border」两条断言改成新语义。）

## 第十轮：纯黄色缩放箭头 / 图标内描边 / 默认布局

- **选中态只剩 8 个箭头**：`draw_selection()` 不再画深色外衬、蓝色实线轮廓、
  白色虚线内框和白色方块手柄；改为在四角 / 四边中点画 **8 个实心纯黄色双向箭头**
  （`OBJECT_YELLOW = #FFC83C`，与对象矩形框完全同色），对象框选中时也保持黄色不变量、
  名称标签同样黄色。箭头屏幕尺寸 `HANDLE_VIEW_SIZE` 从 9px 放大到 **13.5px（1.5 倍）**，
  `effective_handle_size()` 仍会在小对象 / 缩小视图时收窄；`handle_hit()` 命中范围
  按新尺寸同步放大，缩放拖拽和一步撤销不变。选中框不再有任何「线」，只靠箭头表达。
- **蓝色描边画进图标里**：按钮方块恢复第八轮的无边框样式（`border: none`，
  仅选中态用背景色区分；「绘制时自动地图」文字开关因用户截图保留蓝色边框）。
  `make_object_icon()` 统一用亮蓝 `_ICON_BLUE = #50AAF5` 描边所有 13 个图标的
  **内部图形**：斜铅笔、橡皮、选择对象鼠标箭头（深色实心 + 蓝描边）、节点、
  矩形、点、椭圆、多边形、折线、图块、模板、图片，文字工具由深色 `A` 改成
  `QPainterPath` 描边的蓝色 **`T`**。选择光标仍是白箭头 + 蓝色轮廓。
- **默认 UI 布局**：左侧「单位」在上、「Triggers / 对象」在下；右侧「图层」在上、
  「属性」居中、「图块集」在下（用 `splitDockWidget` 固定上下顺序，不再折叠成标签页）。
  图块集从底部横跨移到**右侧最下层**；主工具栏与插入对象图标栏**合并成同一行**
  （去掉 `addToolBarBreak`），版本下拉在最左。`_apply_default_layout()` 在窗口显示后
  设定左右列宽与三层高度比例，dock 仍可自由拖动。

验收图：`logs/第十轮_全窗口.png`、`第十轮_工具栏_标注.png`、
`第十轮_黄色箭头_标注.png`。
回归：新增 `test_ui_round10.py`（10 例，含「选中态一个蓝/白像素都没有」「8 个位置
都有黄箭头」「13 个图标内部都有蓝色描边」「文字图标是 T」「默认 dock 上下顺序」
「两个工具栏同一行」），同步更新 `test_tilesets_dock.py` 与 `test_ui_round9.py`，
单测 97 → **106 全 OK**。

## 第十一轮：填充工具（Flood Fill）+ 画笔图标改图章

- **填充工具（F）**：新增 Tiled 式洪水填充。点击画布任意格，把该格所在的
  **同色四连通区域**一次性替换为当前笔刷 GID（点击格与笔刷同色则不动）。
  一次填充合成**一条撤销**（Ctrl+Z 全部还原）；在 `set` 层填充会触发「绘制时自动地图」
  实时生成地形。算法在 `rwmap/tools/fill_tool.py`，区域写入复用
  `AppState.begin_region_stroke` / `write_region_cell` / `end_stroke`
  （与画笔、橡皮共用同一套撤销与刷新）。
- **画笔图标改图章**：瓦片画笔图标从「斜放铅笔」换成「图章」造型（方底座 + 顶部把手），
  匹配现有的矩形选区盖章功能，行为不变；`F` 填充分支在 `_on_press` 特判，不走单格写入。
- 工具栏现有 **14 个**图标（画笔 / 橡皮 / 填充 / 选择对象 / 编辑节点 / 矩形 / 点 / 椭圆 /
  多边形 / 折线 / 图块 / 模板 / 图片 / 文字）。

回归：新增 `test_fill_tool.py`（6 例：整片填充、仅连通区、同色不动作、
越界、被异色阻断、desired_gid 返回 None），更新 `test_object_shapes.py` 与
`test_ui_round9.py` 的工具数量 / 顺序断言，单测 106 → **112 全 OK**。

## 第十二轮：瓦片框选（M）+ 复制 / 粘贴 / 移动

补齐 Tiled 的矩形瓦片选区能力，与对象层的「选择对象」工具分开（互不干扰）：

- **框选（M）**：在瓦片层按下并拖出矩形选择区域，松手生成虚线蓝框（z=2000）；
  选区过小（< 2px）自动取消。选区是 `[x0, x1)` 半开区间，右/下边界正好落在格线上时
  不会多选一格。
- **复制 / 粘贴（Ctrl+C / Ctrl+V）**：复制把选区内容快照进内存剪贴板；
  粘贴以「鼠标悬停格」为左上角锚点写入，原目标处内容与剪贴板相同则跳过，
  否则合成**一条撤销**（Ctrl+Z 还原）；粘贴后选区框跟着落到新位置。
- **拖动移动**：在已有选框内部按下并拖动，整块区域随之平移，原地未被覆盖的格清空、
  重叠部分取移动后内容；同样一条撤销可还原。移动 delta 按瓦片尺寸取整到整格。
- 纯算法（`norm_rect` / `rect_to_cells` / `cells_to_rect` / `compute_move_changes` …）
  抽到 `rwmap/rw/selection.py`，与 Qt 解耦，便于单测；
  `MainWindow` 的对应方法逻辑保持一致。
- 工具栏现共 **15 个**图标（新增「框选」M：虚线矩形 + 两对角十字角点）。

回归：新增 `test_tile_select.py`（9 例纯算法）与 `test_tile_select_integration.py`
（5 例端到端：在真实 `MainWindow` 上跑「框选 → 复制 → 粘贴 → 撤销」、
「框选 → 拖动移动 → 撤销」、无选区复制 / 无剪贴板粘贴不崩溃），
单测 112 → **127 全 OK**。

## 第十三轮：图层增 / 删 / 复制 / 排序 / 合并 / 锁定

补齐 Tiled 图层面板的结构操作，全部走 `QUndoCommand`，一条撤销可还原，
并保真写回 TMX（新建层补建 `<layer>`/`<objectgroup>`、删除层移除 DOM 元素、
排序同步到 XML 子节点顺序）：

- **新建图层**：图层面板「新建▾」菜单选「瓦片层 / 对象层」，插到当前选中层之上，
  新瓦片层尺寸等同地图、内容全空。
- **删除图层**：保留至少一个图层时方可删除；若删掉的是当前绘制层，自动改选相邻瓦片层。
- **复制图层**：`copy_layer` 深拷贝单元格 / 对象，名字加「副本」，不携带原 DOM 元素。
- **排序**：上移 / 下移按钮与当前层交换位置（顶部 / 底部自动 no-op）。
- **合并到下层**：把选中层合并进其下方（render 顺序更低）的同类型层，
  瓦片层按「非空覆盖」，对象层追加对象；类型不同或已是最底层则拒绝。
- **锁定**：切换图层 `locked`，锁定后该层不可绘制 / 框选（与只读层同样拦截 `can_edit`）。
- 命令实现：`LayerStructureCommand`（增删复制排序，存前后图层列表）、
  `MergeLayerCommand`（合并，存目标层旧内容 + 源层原索引）、
  `LayerFlagCommand`（锁定等单属性）；写回由 `tmx_writer._sync_layer_order` 对齐 DOM。

回归：新增 `test_layers.py`（12 例：增删复制排序合并锁定的撤销 / 重做、
同类合并类型校验、以及新建 / 删除 / 排序后写回 TMX 重新读取层数一致），
单测 127 → **139 全 OK**。

## 第十四轮：翻转 / 旋转整图 + 新建地图 + 地图属性编辑

补齐 Tiled「地图」菜单里的全局几何变换与新建 / 改尺寸能力，全部走
`MapTransformCommand`（存前后图层列表 + 旧 / 新地图宽高），一条撤销可还原，
并保真写回 TMX（含 `<map>` 根属性的 `width/height/tilewidth/tileheight`）：

- **水平 / 垂直翻转**：`flip_cells` 对每张瓦片层逐行 / 逐列镜像；对象层矩形取镜像后
  左缘 / 上缘、多边形镜像各顶点相对地图中心的横 / 纵坐标。
- **顺时针 / 逆时针旋转 90°**：`rotate_cells_90` 旋转后宽高互换；对象层矩形取旋转后
  包围盒（宽 ↔ 高）、多边形绕中心旋转。旋转后地图宽高随之交换，撤销还原。
- **新建空白地图**：「地图 → 新建地图」询问宽 / 高 / 瓦片宽 / 瓦片高，经
  `tmx_writer.create_blank_document` 构建最小化 `<map>` DOM 的空文档，内置一层
  全空 `Ground`，清 undo 栈并设为自有文件（首次保存走另存为）。
- **调整地图尺寸**：「地图 → 调整地图尺寸」只问宽 / 高，新尺寸下裁剪 / 扩展各层
  （瓦片层按左上对齐拷贝旧单元格、对象层深拷贝对象），瓦片宽高沿用当前值。
- 纯算法与 Qt 解耦：`rwmap/rw/transform.py`（`flip_cells` / `rotate_cells_90` /
  `transform_objects`）；主窗口 `MainWindow._transform_map` / `_resize_map` / `_new_map`
  与菜单动作；写回补强 `tmx_writer.write_document` 同步 `<map>` 根宽高属性。

回归：新增 `test_transform.py`（12 例：翻转 / 旋转纯算法、矩形 / 多边形对象变换、
主窗口翻转 / 旋转 / 新建 / 改尺寸的撤销 + TMX 写回重载一致），
单测 139 → **151 全 OK**。

## 第十五轮：右键抓取区域作图章 + 拾取单格 + 图章浮动预览

补齐 Tiled 风格的「图章」交互（在瓦片画笔 / 图章模式下，右键接管画布）：

- **右键拖动 = 抓取区域作图章**：在画布上右键拖出矩形，松开后该区域成为多格图章
  （`state.stamp`，2D gid，保留翻转标志）；拖动时画布**实时显示抓取区域的半透明虚影**
  （读取当前层实时格，左上对齐），不再画蓝色虚线选框。
- **右键单击 = 拾取单格**：把光标下那一格设为当前笔刷——条件是**严格当前编辑层**
  （不回落到上方层）、且**该格非空**；空格则不改动笔刷。拾取后图块集面板自动
  **高亮并滚动到**对应图集与该瓦片（`TilesetsDock.select_gid` 新增 `ensureVisible`）。
- **图章 / 抓取浮动预览**：持有多格图章且悬停地图时，`MapScene` 显示一层半透明（`opacity 0.5`）
  的图章预览，**以「中间格」对齐光标**，与落点完全一致。预览与抓取虚影统一绘制
  **蓝色边框 + 半透明蓝滤镜**（蓝 `QColor(47,134,220)` 边框 + `alpha 85` 填充，对齐图块集选中样式）。
- 中心对齐：因 `AppState.stroke_to` 以图章左上角为原点，press / move / 预览三处统一
  减去 `(W//2, H//2)`，保证预览与实际落点不脱位。
- 右键菜单已用 `setContextMenuPolicy(NoContextMenu)` 屏蔽，避免系统菜单抢占。

改动：`rwmap/view/map_view.py`（右键输入，`captureRectChanged` 改发抓取区域格坐标）、
`rwmap/app/main_window.py`（`_capture_region` / `_right_pick_gid` / 新增 `_on_capture_rect`、
中心偏移、预览刷新、信号接线）、
`rwmap/view/map_scene.py`（`TileStampPreviewItem` 支持 center/topleft 对齐 + 蓝色边框/滤镜、
`set/clear_stamp_preview` + `set_capture_preview`；`set_selection_rect` 改为**原地更新**消除拖影）、
`rwmap/widgets/tilesets_dock.py`（`select_gid` 滚动定位）。

回归：新增 `test_map_rightclick.py`（11 例：拖动抓区域 / 单击拾取 / 空格不改笔刷 /
中心锚点 press·move / 预览显隐 / 抓取虚影读当前层实时格且 topleft 对齐 / 鼠标事件层单击·拖动路由），
单测 151 → **162 全 OK**。

> **补丁（拖影修复 + 蓝色选中样式 + 性能诊断）**：原抓取时画布的蓝色虚线选框
> （`set_selection_rect`）在每次鼠标移动时被「删除+重建」一个 `QGraphicsRectItem`，
> 而视图用 `MinimalViewportUpdate` 导致旧选框像素不重绘 → 留下拖影，且逐帧分配浪费。
> 改为：右键拖动时**不再画选框**，而是把要抓的那块**实际瓦片**以半透明虚影显示在抓取位置
> （`set_capture_preview`，topleft 对齐，无逐帧分配、无拖影）；图章/抓取虚影统一加
> 蓝色边框 + 半透明蓝滤镜（对齐图块集选中样式）。顺手把瓦片框选(M)工具的同类选框也
> 改为原地更新（消除其拖影）。性能诊断：瓦片解码已两级缓存、单格刷新只重画变更格，
> 非瓶颈；唯一真实耗时点为「自动映射」开启且在 `set` 层绘制时的实时重算（属特性本身）。
>
> **补丁②（悬停拖影根治 + 蓝框高度修正）**：实测发现「不按任何键、单纯悬停移动」时，
> 跟随光标的图章预览仍会留下一串蓝色横线，且蓝框只有宽度、拿不到框选矩形的高度。
> 两个现象同源：
> ① `TileStampPreviewItem.paint()` 里 `w, h = len(stamp[0]), len(stamp)`，随后
> `gid, h, v, d = split_gid(raw)` 把行数 `h` **覆盖成水平翻转位**(0/1)，导致
> `QRectF(..., w*tw, h*th)` 高度恒为 0~1 格 → `drawRect` 退化成一条横线，宽 `w` 正常。
> 现改为 `rows/cols` + `flip_h/flip_v/flip_d`，并新增像素级回归测试锁死底行边框。
> ② `boundingRect()` 原本依赖 `col/row/stamp`，item 靠改内容移动；`MapView` 用
> `MinimalViewportUpdate`，几何变化 + 手算双 `update()` 下旧位置像素未正确标脏 → 拖影。
> 现与 `GridOverlay` 同构：`boundingRect()` **固定为整张地图**，新增 `_content_rect()`
> 只显式标脏旧/新两块内容矩形；`set_cells` 内容未变时短路；清除改为复用同一 item
> （`clear_cells`，不 `removeItem`，避免大 boundingRect 触发整图脏区）。
> ③ 蓝框/滤镜改为**按 4-邻接连通块分别画外接矩形**，空格不涂蓝（不相连的瓦片各自成框）；
> 颜色抽到 `rwmap/view/selection_style.py`，图块集面板与地图虚影共用同一常量，杜绝漂移。
> 全部改动不引入全图重绘：局部脏区、短路、连通块缓存均保持 O(局部)。
> 回归：`test_map_rightclick.py` 增至 **16 例**（新增底行蓝框、连通块分框、
> boundingRect 稳定、悬停移动标脏旧/新区域），全量单测 **167 全 OK**。
>
> **补丁③（红框覆盖整块图章）**：图章/抓取虚影压在**地图边界**上时，录屏里会出现
> 整块被涂红、原本蓝色的部分全被盖掉。原因：红线性的「越界格」连通块用**外接矩形**
> 整块填充，而图章跨边界时出界格常连成 L 形（例如右 2 列 + 下 2 行同时出界），
> 其外接矩形几乎等于整个图章；又因为「先蓝后红」，红就压在地图内的蓝框上。
> 现改为：①蓝块只由**地图内**的格算连通块、红块只由**地图外**的格算（两者互不侵占）；
> ②绘制顺序改成**先红后蓝**，冲突处蓝色优先；③红填充额外用「地图矩形取反」做裁剪
> （`QPainterPath` OddEvenFill + `setClipPath`），红块外接矩形再大也不会涂进地图内部，
> 地图内的空白格同样不会被染红。整块在图内 / 图外时直接复用同一份连通块结果，
> 只有跨边界才逐格判定，hover 逐格移动的开销不变。
> 回归：`test_map_rightclick.py` 增至 **18 例**（`test_blue_wins_over_red_when_stamp_crosses_edge`
> 断言地图内无红像素、蓝框仍在、越界格仍有红框；`test_red_fill_never_bleeds_into_map_interior`
> 断言红框越界但不染地图内部），全量单测 **499 全 OK**。
>
> **补丁④（角落缺两条线 + 线别往里缩）**：补丁③用「外接矩形 + 裁掉地图内」画红区，
> 红色 L 形的**内侧两条边**正好落在地图边界上，被裁剪一起裁没了（录屏角落里那两条线）；
> 另外 `drawRect(box.adjusted(1,1,-1,-1))` 让描边整体往色块里缩了 1px，线不贴边。
> 现改为**按实际格子集合**画填充与外轮廓（`_region_geometry`）：同行连续格合并成一条
> 填充矩形，只画「相邻格不在这批格子里」的边并合并共线段，因此 L 形 / 凹角 / 空洞的
> 内侧边也会有线，环形图章中间的空格也不再被外接矩形涂色。顺序为
> 红填充 → 蓝填充+蓝轮廓 → 红轮廓（裁到地图外），共享边界上**蓝贴内侧半条、红贴外侧半条**，
> 两种线都看得见且都正好压在色块边缘（笔宽 1.6，去掉 1px 内缩）。
> 几何改存**图章局部坐标**并在 paint 里整体平移：整块在图内 / 图外时缓存复用，连通块
> （仅供诊断/测试）改成懒属性，逐格判定从 `QRectF.intersects` 换成整数比较 ——
> 80×80 大图章跨边界 hover 从 **107ms/次降到 6.4ms/次**（图内 0.85ms/次）。
> 回归：`test_map_rightclick.py` 增至 **23 例**（角落共享边蓝/红线都在、边框像素正好落在
> 格子边缘 x/y=20/80 而非 21/79、环形空洞不涂色），全量单测 **502 全 OK**。

## 第十六轮：钢笔 / 贝塞尔矢量曲线（A）

在工具栏「瓦片画笔（盖章）」**紧右侧**新增钢笔按钮，画一条可调的矢量曲线，
完成后用当前选中的瓦片沿曲线印到当前瓦片图层。

- **曲线模型**：每个锚点带一对**严格对称**的控制柄（`hin == -hout`），锚点画蓝色小方块、
  控制柄画小圆点 + 虚线（颜色复用 `selection_style.SELECT`，与图块集选中一致）。
- **交互**：右键单击放角点 → 右键按下并拖动放下一个锚点（拖动方向/长度即对称控制柄）
  → 左键拖锚点 / 控制柄调节 → 左键点曲线中段**插入新锚点** → 左键点「线之外的空白区」
  **完成并盖章**。完成后清空路径、仍停在钢笔工具，可直接画下一条；
  少于 2 个锚点不允许完成；`Esc` 取消整条线、`Delete` 删末点、`Ctrl+Return` 完成。
- **插入点**：用 de Casteljau 分割点处的切线方向生成对称柄，严格维持双柄对称不变式
  （对称与「曲线零形变」数学上不可兼得，按用户选择取严格对称，允许极小局部形变）。
- **沿线盖章**：自适应细分曲线 → DDA 逐格推进（4-连通、不跳格、保序去重）→
  无图章用 `brush_gid` 单格印，有多格图章按「中间格对齐」贴整块 2D 图章，
  图章里的 `0` 跳过不清空。整条线合成**一条 `DrawCommand`、一次撤销**。

改动：`rwmap/utils/bezier.py`（纯算法：求值/自适应细分/分割/切线柄/点到曲线距离/
DDA 栅格化，零 Qt）、`rwmap/tools/pen_tool.py`（`Anchor`/`PenPath` 数据模型）、
`rwmap/view/pen_preview.py`（`PenPathPreviewItem`）、`rwmap/view/map_scene.py`（预览接线）、
`rwmap/widgets/object_toolbar.py`（按钮 + 蓝色钢笔图标）、
`rwmap/view/map_view.py`（右键三信号 + 状态机）、`rwmap/app/main_window.py`（`_pen_*` 状态机 +
落笔 + 清理/键位）。`tools/tool_base.py` **刻意不改**：钢笔走对象工具的浮点场景坐标路径，
不经过瓦片 `desired_gid`。

> 三个必须记住的坑：
> ① `map_view.mouseReleaseEvent` 里 `_obj_tool_drag` 分支原本**不判断按键类型**且排在最前，
> 会把右键松开吞成「对象工具左键松开」→ 钢笔一个点都放不下。现把右键分支前置，
> 并给左右键分支都加 `event.button()` 判断。
> ② 对象工具分发里 `_target_object_layer()` 在**没有对象层**的地图上会提前 return，
> 钢笔分流必须排在它前面。
> ③ 预览 item 继续沿用「恒定整图 `boundingRect` + 复用同一 item + 只标脏旧/新内容矩形」，
> z=11000 压过网格，避免重演前两轮的拖影。

回归：新增 `test_pen_bezier.py`（10 例纯算法）、`test_pen_preview.py`（6 例渲染/脏区）、
`test_pen_tool.py`（18 例交互 / 盖章 / 撤销 / 右键鼠标事件路由）；
同步修正 `test_ui_round9.py`、`test_object_shapes.py` 里写死的工具按钮数量与前四项顺序。
单测 167 → **201 全 OK**。

## 第十七轮：钢笔接入「绘制时自动地图」

Tiled 画笔下拉里的 `绘制时自动地图`（本编辑器为主工具栏的 `绘制时自动地图` 开关），
原本只对画笔 / 填充生效；钢笔完成矢量线后只写瓦片、**不触发自动映射**。本轮补齐。

- **触发时机**：钢笔的曲线只是预览，落笔是一次性批量写入，没有「逐格笔划」，
  所以在 `_pen_finish()` 里 `push DrawCommand` **之后**，按整条线真正改动的格范围
  `(c0,r0,c1,r1)` 做**一次**局部重算（等价填充的「松手式收敛」，不做逐格重算）。
- **撤销语义**：`beginMacro("钢笔线并自动映射")` 包住钢笔线命令 +
  `_apply_automap(area=bounds, macro=False)` 产生的所有 `DrawCommand`，
  `endMacro()` 放在 `finally` 里（规则计算抛异常也不会把撤销栈留在宏中）。
  **一次 Ctrl+Z 同时还原标记与地形**；标题与状态栏明确区分「钢笔线」与「钢笔线并自动映射」。
- **范围准确性**：`bounds` 由 `changes` 里界内且值确实变化的格推导；多格图章时
  覆盖整块图章而非仅曲线采样格，越界格不计入。
- **不触发的情况**：开关关闭 / 规则未加载 / 曲线没覆盖任何新格（保持与画笔一致）。
  开关打开时会自动把当前层切到 `set`，和画笔一样。

改动：`rwmap/app/main_window.py`（`_pen_finish` 的 bounds 统计 + 宏 + 局部重算 +
状态栏；`act_automap_while` 提示与开启提示补上钢笔）。工具按钮、曲线算法、预览层均无需改动。

回归：新增 `test_pen_automapping.py`（7 例：开关切换 / 关闭不调用 / 局部 area 精确匹配 /
图章覆盖范围 / 一次撤销还原标记+地形 / Ctrl+Return 完成入口 / 计算异常不留宏），
并在 `test_automapping_live.py` 追加 1 例用**官方 rules.txt + 示例地图**的端到端用例
（钢笔画 8 格线 → Ground 变化 → 一次撤销全还原）。单测 201 → **209 全 OK**。

## 第十八轮：过渡瓦片「有的时候不出现」——set 层标记归一

**现象**：开「绘制时自动地图」后用钢笔画一条绿地曲线，绿带边缘硬切，
没有水→陆的岸线 / 浅滩过渡瓦片；换一块瓦片、有时又正常。

**根因**（`rwmap/probe/probe_pen_automap.py` 条件矩阵实测，同一条 8 格线只改状态）：

| 条件 | 过渡瓦片增量 |
| --- | --- |
| 开 / 层=set / gid=126（Long Grass 首格） | **+13** ✅ |
| 开 / 层=set / gid=130（同图集第 5 格） | **-1** ❌ |
| 开 / 手切到 Ground 层 | **0** ❌（且刚画的格被重算擦掉） |
| 开 / 3×3 图章来自 Ground 渲染层 | **+2** ❌ |
| 关 | 0（符合预期） |

官方 `rules.txt` 的 `input` 层**只认各基础图集的第 1 格**（`Long Grass` = 126、
`Mountain` = 1、`Water` = 186 …）。用户在图集面板点了中间某一格、或用右键从
Ground 抓了已渲染的图章，写进 set 层后一条规则都匹配不上；局部重算的
`reset_base` 还会把窗口内**原有的**过渡瓦片擦掉，于是整条边界硬切。
「有的时候」= 有时正好选中首格，有时没有。

**修复**：
- `rwmap/rw/automapping.py` 新增纯函数 `normalize_marker_gid(doc, raw)`：
  按 `tileset.firstgid..last_gid` 把任意基础地形瓦片归一到该图集首格；
  过渡图集 / units / misc 返回 `-1`；**无图集信息时保守返回原值**
  （绝不因归一失败把用户落笔整片丢掉）；翻转标志位保留。
- `rwmap/app/main_window.py`：
  - `_automap_write_target()`：开关开启时**落笔固定写 set 层**（不管当前层），
    画笔 / 填充 / 钢笔共用；关闭时完全保持原行为。
  - `_on_press` / `_do_fill` / `_pen_finish`：写入前归一 gid；多格图章逐格归一，
    钢笔对 `-1`（过渡瓦片）**跳过不写**，状态栏报「N 格非地形瓦片已跳过」。
  - `_on_release` 最终重算加「笔画层必须是 set」判断：非 set 层时 set 层没变化，
    原来的重算只会用 `reset_base` 把用户刚画的内容擦掉。
  - 开关提示 / tooltip 同步说明归一规则。

**修复后条件矩阵**：非首格 +13、手切 Ground +13、混合图章 +18（过渡瓦片跳过）、
画笔非首格 +13；开关关闭仍是 0（不改变原行为）。

两个可重跑探针（都在 `rwmap/probe/`，不写回地图文件）：

```bat
.venv\Scripts\python.exe -m rwmap.probe.probe_pen_automap
.venv\Scripts\python.exe -m rwmap.probe.probe_pen_automap_visual
```

前者打印条件矩阵，后者渲染 `logs/钢笔过渡_验收对比.png`：
上排「开关关 = 硬切」，下排「开关开 + 选 Long Grass 第 5 格 = 有深色岸线过渡」。

回归：`test_automapping.py` +2（归一纯函数、无图集兜底）、
`test_automapping_live.py` +3（官方规则端到端：非首格出过渡 / 手切层只写 set /
渲染图章跳过过渡瓦片）。单测 209 → **214 全 OK**。

## 第十九轮：钢笔线「一段有过渡、一段没有」——正反双向遍历补标记

**现象**：第十八轮修好「整条线完全没有过渡」后，仍有边界局部硬切：绿色带的
某一段有深色岸线，相邻一段却是干净的直边。

**探针先排除两个想当然的原因**（`rwmap/probe/probe_pen_transition_direction.py`、
`probe_pen_transition_edge.py`，只读、不写回地图）：

| 检查 | 结果 |
| --- | --- |
| 起点→终点 / 终点→起点 / 双向合并的纯计算结果 | 完全相同，差异 **0 格**（`compute()` 用 `rule.order` 仲裁冲突，与扫描顺序无关） |
| 局部 `area=bounds` 重算 vs 全图重算（示例地图 24 个草/水交界抽样） | 窗口内**逐格一致**，缺 0 格 |
| 8 格草线中间挖 2 格 set 空洞 / 放非地形 gid | 过渡瓦片 **22 → 18** ❌ |

**根因**：第十八轮把 `normalize_marker_gid` 返回 `-1`（过渡图集 / units / misc
瓦片）的格**直接跳过不写**，等于在标记层挖洞；规则按相对坐标严格匹配，洞周围的
2×2 / L 形组合全部失配，那一段边界就只剩基础地形 = 硬切。另外 `_pen_finish` 的
`area` 只统计 `old != new` 的格，线大部分画在已有相同地形上时，曲线经过的其它
边界段得不到重算。

**修复**（正反两遍遍历：「先从起点到终点，再从终点到起点」）：
- `_pen_curve_cells()`：曲线栅格化的保序格序列，落笔与遍历共用同一份。
- `_pen_repair_markers(final, curve_cells)`：正向用**左侧最近**的有效基础地形
  标记补洞，反向用**右侧最近**的补洞；两遍都有候选时取**距离更近**的一侧，
  避免整段被一端的值覆盖；整条线都没有有效标记时才真正跳过。图章展开的格按
  「距曲线最近的一站」映射进遍历序。状态栏区分「双向补齐」/「无可用标记已跳过」。
- `_pen_sweep_bounds(curve_cells, bounds)`：自动映射重算范围 = 整条曲线外接框
  ∪ 值变化格范围，正反两遍取并集；只增不减，图章场景保持原范围。
- 落笔仍是一条 `DrawCommand` + 一个 `beginMacro`，一次 Ctrl+Z 全还原。

**验收**（`probe_pen_sweep_verify.py`，官方 `rules.txt`）：混合过渡瓦片图章
**+14**、纯基础地形图章 **+14**，差值 **0**（旧行为中间缺一截）。

回归：新增 `test_pen_transition_sweep.py`（7 例：双向补洞 / 反向补起点侧 /
全无效兜底 / 无图集兜底 / sweep 范围 / set 层无空洞 / area 覆盖整条线），
`test_automapping_live.py` 的旧「跳过」用例改为「双向补齐」断言。
单测 214 → **221 全 OK**（131.6s）。

可重跑探针：

```bat
.venv\Scripts\python.exe -m rwmap.probe.probe_pen_transition_direction
.venv\Scripts\python.exe -m rwmap.probe.probe_pen_transition_edge
.venv\Scripts\python.exe -m rwmap.probe.probe_pen_sweep_verify
.venv\Scripts\python.exe -m rwmap.probe.probe_pen_sweep_visual
```

最后一个渲染 `logs/钢笔过渡_双向补齐对比.png`：上排用内存 monkeypatch 复现
第十八轮「跳过」旧行为（中间一段边界硬切），下排为双向补齐后的完整过渡。
实测旧行为「11 格标记 + 33 格地形」，新行为「24 格标记 + 45 格地形」。

## 第二十轮：设置菜单 → 面板列表 / 导入 / 保存面板

**需求**：菜单栏「文件 / 编辑 / 视图 / 地图」这一行加「设置」，下拉含
「面板列表设置」「导入面板」「保存当前面板」，并把现有面板整理成清单。

### 面板清单（整理结果）

`rwmap/app/panel_layout.py` 的 `PANEL_SPECS` 是唯一来源：主窗口装配、
面板列表对话框、布局导入导出都基于它，新增面板只需追加一行。

| 面板 | 默认停靠 | 默认显示 | 功能 |
| --- | --- | --- | --- |
| 图层 | 右侧（上） | 是 | 倒序列出图层；可见性 / 透明度 / 当前层，增、删、复制、排序、合并、锁定 |
| 属性 | 右侧（中） | 是 | 地图统计、地图属性、当前图块属性（只读） |
| 图块集 | 右侧（下） | 是 | 整张图集原坐标显示；网格框选多格图章、单击设笔刷、拾取后自动定位 |
| 单位 | 左侧（上） | 是 | 单位 raw name 列表，点击把对应单位图块设为笔刷 |
| Triggers / 对象 | 左侧（下） | 是 | 对象语义树；编辑名称 / 类型 / 位置 / 自定义属性，增删对象并走撤销栈 |

**非停靠界面**（不在「面板列表设置」里，不受显隐影响）：主工具栏（版本下拉）、
对象工具栏（画笔 / 盖章 / 钢笔 / 橡皮 / 形状 + 自动映射开关）、状态栏
（版本 / 缩放 / 提示）、「自测 / 探针」（视图菜单里的独立对话框）。

### 三个菜单项

- **面板列表设置**（`PanelSettingsDialog`）：列出 5 个面板的名称、当前停靠区、
  功能说明，勾选 = 显示；取消勾选只隐藏、不删除面板也不改地图数据。
  附带「全部显示 / 全部隐藏 / 恢复默认布局 / 复制清单」（清单复制为 Markdown 表格）。
- **保存当前面板**：把每个面板的显隐、停靠区、浮动与几何写成 UTF-8 JSON，
  同时保存 `QMainWindow.saveState()` 的 base64（标签页合并 / 精确尺寸）。
  文件名不带 `.json` 会自动补后缀。
- **导入面板**：先 `restoreState()` 恢复 Qt 内部状态，再按 `panels` 逐项兜底；
  格式不符 / 坏 JSON 弹提示，未知面板 key 忽略并在状态栏回报。

配套改动：所有 Dock 与两条工具栏都设了稳定 `objectName`（`restoreState()`
的必要前提）；默认布局从原来 4 行硬编码 `addDockWidget` 改为
`apply_default_layout()` 按清单驱动，第十轮的停靠区断言保持不变。

**验收图**：`logs/设置菜单_面板列表.png`（`probe_panel_settings_visual.py`，
offscreen 下手动挂载系统字体，避免中文渲染成方块）：
菜单五项、设置下拉三项、面板清单对话框逐项可见。

```bat
.venv\Scripts\python.exe -m rwmap.probe.probe_panel_settings_visual
```

**回归**：新增 `test_panel_settings.py` 17 例（菜单 / 清单 / 对话框显隐 /
恢复默认 / 采集与文件往返 / 异常文件 / 三个动作端到端）；
全量 **238 测试全 OK**。

## 第二十一轮：图层面板照搬 Tiled（图标 + 功能，保持深色）

**需求**：右侧图层面板照搬本机 Tiled 的功能与图标，但**不要变成白色**。

### 图标真的取自本机 Tiled

Tiled 安装目录里没有独立图标文件，图标编译进 `<Tiled目录>\tilededitor.dll`
的 Qt 资源（`:/images/14|16/*.png`，共 172 个内嵌 PNG，每段前 4 字节是大端长度）。
`rwmap/probe/extract_tiled_icons.py` 按资源编号提取 15 个图层面板图标：
`14/visible|hidden|locked|unlocked`（行内状态）、`16/layer-tile|layer-object`
（类型）、`16/document-new|go-up|go-down|stock-duplicate|edit-delete|merge-down|
locked|unlocked|show_hide_others`（按钮栏）。

原始图标是**亮色主题版**，自带不透明白底 / 黑底。生成时做深色适配：
接近纯白 / 纯黑的中性像素换成面板底色 `#2b2b2b`，中间灰提亮成亮灰蓝，
蓝 / 紫 / 黄 / 绿只提亮、保留色相；形状不变。
结果内嵌为 base64 写在 `rwmap/widgets/tiled_icons.py`，
**打包 exe 不需要附带图标文件**；`logs/tiled_icons_dark_sheet.png` 是图标验收图。

### 功能对照

| Tiled 元素 | 旧版 | 新版 |
| --- | --- | --- |
| 层类型 | `[tile]` / `[object]` 文字 | 蓝色网格 / 紫粉对象图标 |
| 可见性 | 左侧复选框 | 右侧眼睛图标，点击切换；隐藏层名字变灰 |
| 锁定 | 底部文字按钮 | 右侧锁图标点击切换 + 底部锁按钮（图标随状态变） |
| 当前层 | 蓝色左边框 | 整行蓝色高亮 + 粗体层名 |
| 工具栏 | 顶部一排文字按钮 | 底部 Tiled 顺序的图标按钮栏 |
| 重命名 | 无 | 双击层名行内编辑，`LayerFlagCommand` 一次撤销可还原 |
| 右键菜单 | 无 | 新建 / 上移 / 下移 / 复制 / 合并 / 删除 / 显隐 / 锁定 |
| 显示隐藏其它层 | 无 | 右下眼睛按钮（全隐藏后再点恢复） |
| 透明度 | 滑块 | 滑块保留在按钮栏上方 |

按钮栏顺序：新建（下拉瓦片层 / 对象层）→ 上移 → 下移 → 复制 → 删除 →
向下合并 → 锁定 →（右端）显示 / 隐藏其它层。所有原有信号与
`select_layer` / `set_document` 语义不变；主窗口新增
`layerRenameRequested → _rename_layer`。

**深色**：面板 / 列表 `#2b2b2b`，按钮栏与自定义标题栏 `#333333`
（系统标题栏是浅色，已用 `setTitleBarWidget` 换掉，保留浮动 / 隐藏按钮；
按钮图标在第二十二轮改为复用 Qt 原生 Dock 图标，见下）。
测试会渲染面板并断言近白像素占比 < 3%、蓝色选中行存在。

```bat
.venv\Scripts\python.exe -m rwmap.probe.extract_tiled_icons
.venv\Scripts\python.exe -m rwmap.probe.probe_layers_tiled_visual
```

验收图 `logs/图层面板_Tiled风格对比.png`：① Tiled 白底原版（仅作参照）、
② 旧版深色复选框、③ 新版深色 Tiled 图标版。
新增 `test_layers_dock_tiled.py` 14 例；全量 **252 测试全 OK**。

## 第二十二轮：图层标题栏浮动图标与原生 Dock 统一

**问题**：自定义深色标题栏里浮动按钮用的是文字字符 `◇`，关闭按钮用 `×`。
`◇` 在部分字体 / 主题下会被渲染成蓝橙色圆环，与「属性」等其它 Dock
原生标题栏的**小窗口图标**不一致。

**修复**（`rwmap/widgets/layers_dock.py`）：新增 `_native_dock_icons()`，
从一个不显示的 `QDockWidget` 里读出 `qt_dockwidget_floatbutton` /
`qt_dockwidget_closebutton` 的 `QIcon`，缓存后设给自定义标题栏的
`btn_float` / `btn_close`；`QIcon` 来自样式引擎，会随系统明暗主题自动变色，
比抓静态 `QPixmap` 更可靠。找不到时回退到
`SP_TitleBarNormalButton` / `SP_TitleBarCloseButton`。按钮不再有任何文字，
提示精简为「浮动」「关闭」，行为（浮动切换 / 隐藏面板）保持不变，
标题栏仍是深色的。（第二十三轮已把这段实现抽到 `dock_chrome.py` 供 5 个面板共用，
`layers_dock.py` 只调用 `install_title_bar()`。）

**验收**：`rwmap/probe/probe_dock_title_icons.py` 生成
`logs/图层标题栏_浮动图标统一.png`；脚本与
`test_layers_dock_tiled.py` 都断言「自定义按钮图标」与「原生按钮图标」
在 16×16 下**逐像素相同**。新增 3 例（图标同款、浮动切换、关闭隐藏），
`test_layers_dock_tiled.py` 14 → 17 例；全量 **255 测试全 OK**。

```bat
.venv\Scripts\python.exe -m rwmap.probe.probe_dock_title_icons
```

## 第二十三轮：面板统一格式（5 个 Dock 共用标题栏）

**需求**：各面板「通用的地方」统一——右上角图标大小、左上角字体大小；
让属性 / 图块集 / 单位 / Triggers 的右上角跟图层一样（照搬代码），
并抽出一个可复用的「面板统一格式」。

**实现**：新增 `rwmap/widgets/dock_chrome.py`，`PANEL_SPECS` 里的 5 个
`QDockWidget` 全部调用 `install_title_bar(self)`：

| 统一项 | 常量 / 取值 |
| --- | --- |
| 标题栏高度 / 背景 | `TITLE_BAR_HEIGHT=22`，`#333333` |
| 左上角标题 | `TITLE_FONT_PT=9`、粗体、`#e6e6e6`，文字取 `windowTitle()` |
| 右上角浮动 / 关闭图标 | `native_dock_icons()` 取 Qt 原生 Dock 按钮 `QIcon`，16×16 |
| 按钮尺寸 / 提示 | 18×18，「浮动」「关闭」，悬浮 / 按下反馈一致 |
| 双击标题栏 | 切换浮动（补回自定义标题栏取代系统标题栏后丢失的原生行为） |
| 内容区底色 | `apply_panel_theme()` 给属性 / 单位 / Triggers 统一 `#2b2b2b`（图块集已有自己的样式，不覆盖） |

`layers_dock.py` 删掉本轮的私有 `_native_dock_icons()` / `_build_title_bar()`，
改为共享实现；`btn_float` / `btn_close` 仍挂在 dock 上，旧测试与探针不变。
`widgets/__init__.py` 导出 `DockTitleBar` / `install_title_bar` /
`apply_panel_theme` / `native_dock_icons`。

**踩坑**：`setStyleSheet()` 的选择器规则**不会可靠地作用到子控件**，
且自定义标题栏作为 `QDockWidget` 子控件渲染时，`QPalette` / 样式表底色会被
原生浅色标题栏盖掉。最终做法是每个控件各自设样式 + `QPalette` 兜底，
并在 `DockTitleBar.paintEvent()` 里直接 `fillRect` 深灰底，保证离屏与
系统深色 / 浅色主题下都是深底浅字。

**验收**：`rwmap/probe/probe_dock_chrome_visual.py` →
`logs/面板统一格式_标题栏对比.png`（上排原生白底小图标，下排 5 条统一深色标题栏）。
新增 `test_dock_chrome.py` 5 例（5 个面板尺寸 / 字体 / 图标一致、
与原生图标逐像素相同、整窗渲染无白底、重复安装幂等、浮动 / 关闭 / 双击）；
全量 **260 测试全 OK**。

```bat
.venv\Scripts\python.exe -m rwmap.probe.probe_dock_chrome_visual
```

## 验收（最小闭环）

1. `start.bat` → 主窗口 + 示例地图自动加载。
2. 图层面板选 `Ground`，图块集选一块地形，在画布点一格。
3. `Ctrl+Z` 复原、`Ctrl+Y` 重做。
4. 文件 → 另存为 → `验收用.tmx`（会同时复制资源副本并生成 `验收用_map.png`）。
5. `<Tiled目录>\tiled.exe` 打开 `验收用.tmx`，确认无「找不到图块集」报错。
6. 游戏「自定义地图」可见 `验收用`，开局后改动生效。

> 保存按钮不会覆盖官方地图：首次编辑官方文件时会转为「另存为」。

## 自测

```bat
.venv\Scripts\python.exe -m rwmap.probe.probe_report
.venv\Scripts\python.exe -m unittest discover -s rwmap\tests -v
```

探针基线：1.14 + 1.15 共 168 张官方图 100% 可解析；640 个 `<data>` 全为 base64+gzip；
`mods/maps/embedded_custom_tileset_example.tmx` 为 zlib；84 个 GID 带翻转标志；
两种 cliff 拼写（`large-cliff` / `large-clift`）都真实存在。报告写到 `logs/probe_report.json`。
单测 260 项，含 `test_dock_chrome.py`（5 个面板统一格式 / 图标逐像素一致 /
整窗渲染无白底 / 幂等安装 / 浮动·关闭·双击）、
`test_layers_dock_tiled.py`（Tiled 图标深色化 / 无白底像素 /
行布局 / 图标按钮 / 标题栏浮动·关闭图标与原生逐像素一致 / 行内眼睛·锁点击 /
双击重命名撤销 / 右键菜单 / 显隐其它层），
`test_panel_settings.py`（设置菜单三项 / 面板清单 / 显隐勾选 /
恢复默认布局 / Qt 状态与 JSON 往返 / 异常文件 / 动作端到端）、
`test_fill_tool.py`（填充算法与区域笔划）、
`test_tile_select.py`（框选纯算法：半开区间、重叠移动、越界）、
`test_tile_select_integration.py`（主窗口框选 → 复制/粘贴/移动 → 撤销闭环），
`test_layers.py`（图层增删复制排序合并锁定 → 撤销 / 写回 TMX），
`test_transform.py`（翻转 / 旋转 / 新建 / 改尺寸 → 撤销 / 写回 TMX），
`test_map_rightclick.py`（右键拖抓区域 / 单击拾取 / 中心对齐 / 预览 / 鼠标路由 /
蓝框高度·连通块分框·boundingRect 稳定的像素级回归），
`test_pen_bezier.py`（贝塞尔求值 / 自适应细分 / 分割 / 曲线命中 / DDA 逐格化），
`test_pen_preview.py`（钢笔预览恒定 boundingRect / 局部脏区 / 蓝色像素渲染 / 复用 item），
`test_pen_tool.py`（钢笔右键加点·拖柄 / 左键调点·插点·完成 / 图章中心对齐 / 一次撤销 /
Esc·Delete·切工具清理 / 右键鼠标事件路由），
`test_pen_transition_sweep.py`（钢笔线正反双向补标记 / 重算范围覆盖整条曲线 /
set 层不挖洞 / 官方规则端到端验收），
`test_tileset_counts.py`（隐含 tilecount / 配色）、
`test_view_behavior.py`（缩放锚点、无紫色占位像素、编辑后缓存局部失效），
`test_automapping*.py`（规则解析、100% 全图复现、L 形拐角、逐格实时一致性、
set 层 gid 归一、钢笔线落笔自动映射 + 一次撤销）、
`test_pen_automapping.py`（可注入假规则器锁死开关 / 范围 / 宏 / 全部完成入口），
`test_picker_hotkey.py`（临时吸管）、`test_object_edit.py`（对象写回 / 撤销 / 画布交互），
`test_object_shapes.py`（全形状 round-trip、文字 / 图片 / 模板、插入工具与顶点编辑），
`test_ui_round9.py`（自由坐标 / Shift 吸附、8 点缩放与撤销、工具栏与编号）、
`test_ui_round10.py`（纯黄箭头 / 图标内描边 / 默认布局）、
`test_undo_coverage.py`（16 例：可见性 / 透明度合并 / 隐显其它层、组合·拆组·加入组·
对齐·分布·跨层删除、节点图建线·删线·改线型、图集属性与逐字符输入合并、
无变化不产生空记录、多标签撤销跟随当前文档）、
`test_object_resize.py`（15 例：单击后 8 个黄箭头渲染、框外命中 / 框内排除、
缩小视图时点内部仍走拖动、拖 se/nw/e 改尺寸、整组缩放含多边形与点对象、
Shift 吸附、最小尺寸、Esc 取消、一步撤销 / 重做、hover 缩放光标）、
`test_toolbar_merge.py`（15 例：合并后 15 个按钮 / S·M 双快捷键 / 旧 id 别名 /
鼠标箭头图标像素、瓦片层框选瓦片与对象层框选多对象、点对象出 8 箭头、
拖动一条撤销、右键菜单、真实 M/S 按键与真实鼠标框选、斜桶滴漆像素）。

## 目录

```
run.py / start.bat / requirements.txt
config/                 版本配置、层规则、属性别名、unit_names.json（完整单位表）
rwmap/document/           MapDocument / Layer / Tileset / GID / PropertyBag（不依赖 Qt）
rwmap/io/                tmx 读写、层数据编解码、外部 tsx、五级资源路径解析
rwmap/tilesets/          切图、color-key 透明、embedded_png、Qt 图块缓存
rwmap/rw/                版本 profile、层/属性适配、单位 raw name、导出、缩略图、rwmod
rwmap/view/              QGraphicsScene / View（每层一个 item，视口裁剪）
rwmap/widgets/           各 Dock + 自测窗口
rwmap/tools|commands/    工具与 QUndoCommand
rwmap/probe|tests/       探针与单测
```

## 单位表（随机生成单位 / 单位检测）

`config/unit_names.json` 是从游戏文件生成的**完整单位表**（1.15 实测 160 条，
其中 109 条为玩家可用单位），字段：`raw` / `cn` / `en` / `group` / `hidden`。

- 数据源：`assets/units/**/*.ini` 的 `[core] name + displayLocaleKey`、
  `assets/units/units.tsx` 的图块属性、`assets/translations/Strings_zh_cn.properties`
  的 `units.<key>.name`（官方简中译名），无官方译名的虫族 / 战役单位用脚本内
  `MANUAL_CN` 兜底。
- 分组：按 ini 所在目录 + 关键字推断（陆军 / 机甲 / 空军 / 海军 / 防御 / 经济 /
  建筑 / 核武器 / 虫族），`GROUP_OVERRIDE` 处理官方名与目录打架的个例。
- `hidden`：战役变体 `c_*`、形态中间态（`*_afterburn` / `*Landed`）、模块零件、
  开发桩（`NULL` / `missing` / `example_faction_picker`），选择器默认折叠。
- 重新生成（换游戏版本或加 mod 后）：

  ```
  .venv\Scripts\python.exe rwmap\probeuild_unit_catalog.py
  .venv\Scripts\python.exe rwmap\probeuild_unit_catalog.py --game "<游戏目录1.14>"
  ```

## 第三十三轮：恢复选中对象的 8 个缩放箭头（回归修复）

**现象**：用「选择对象」（鼠标箭头图标）左键单击矩形后，四角 / 四边的黄色
「放大缩小」箭头不见了；而功能表仍写着「8 手柄自由缩放」。

**根因**：改成「PPT 风格整体包围框」时，把 `ObjectLayerItem._paint_object`
里的 `draw_selection()` 调用连同整条拖拽缩放交互一起删了，只剩浅蓝虚线框。

**恢复内容**：

- 整体选中框换成 `ObjectSelectionBoxItem`（`QGraphicsRectItem` 子类）：保留
  `setRect/rect/setVisible` 接口，`paint()` 里在框外画 8 个纯黄双向箭头；
  `boundingRect()` 外扩，避免 MinimalViewportUpdate 下框外箭头被裁 / 留残影。
  箭头是屏幕固定 13.5px，视图缩放经 `_on_zoom → refresh_selection_handles_scale()`
  同步（缩放是视图操作，不入撤销栈）。
- 拖拽手柄按方向改 `x/y/width/height`；多边形 / 折线按比例缩放 `points`，
  点对象只按比例挪中心；多选 / 组合时按**整体框**等比缩放所有选中对象。
  逐帧都从「按下瞬间」的原始几何重算，不累积浮点误差；最小 8px 防翻转。
- 一次拖动 = 一条 `MultiObjectCommand`（`缩放对象`）；没真动尺寸不产生空记录；
  `Shift` 吸附网格，`Esc` 取消恢复原状且不留撤销记录；拖动手柄时鼠标换成
  对应方向的双向箭头，悬停也有 `objectHover` 光标反馈。
- **坑**：视图缩到 23% 时，屏幕固定尺寸箭头在场景坐标里被放大到约 50px，
  命中方框会盖进对象内部，导致「点一下对象就变成缩放、拖不动」。
  `handle_hit_arrow()` 先把框内（离边 > 4 屏幕像素）整体排除，只允许贴边窄带
  与框外命中；`test_object_resize.py` 有专门用例锁死这个行为。

回归：新增 `test_object_resize.py`（15 例），全量单测 **517 全 OK**。
验收图：`examples/选中框缩放箭头.png`。

## 第三十四轮：合并「框选 / 选择对象」+ 填充图标改斜桶滴漆

**需求**：工具栏上相邻的「虚线框（框选 M）」和「鼠标箭头（选择对象 S）」
合并成一个按钮，图标用鼠标箭头，功能包含两者；填充图标改成
「油漆桶斜放 + 颜料往下掉」，**去掉提手**。

**实现**（`rwmap/widgets/object_toolbar.py` / `rwmap/app/main_window.py`）：

- `TOOLS` 去掉 ``tileselect``，保留 ``select``（改名为「选择 / 框选」），
  工具栏 16 → **15 个图标**；图标仍是原来的深色实心 + 蓝描边鼠标箭头。
  `make_object_icon("tileselect")` 的虚线框绘制保留，但不再有按钮使用它。
- 一个 `QAction` 用 `setShortcuts([S, M])` 挂**两个快捷键**，`Ctrl+M`
  （自动映射）不受影响；真实按键 M / S 都切到同一个工具。
- 旧 id 只作兼容别名：`canonical_tool_id("tileselect") → "select"`，
  `MainWindow._set_object_tool()` 与 `ObjectToolBar.select_tool()/set_hint()`
  统一归一，旧测试 / 探针调用 `_set_object_tool("tileselect")` 照常工作，
  不会再出现两套状态机。
- 合并后的按下优先级：缩放箭头 → 已选对象整组拖动 → 点中对象（选中 + 拖动）
  → 空白处拖框。空白框选目标由**图层面板当前层**决定：对象层框选多个对象、
  瓦片层（或纯瓦片地图）框选瓦片并支持 `Ctrl+C/V` / 拖动移动。
  原 S 的「点一下矩形就出 8 个黄色缩放箭头」、右键组合/对齐菜单、
  自定义鼠标光标、悬停方向箭头全部保留；统一走
  `_on_select_press/move/release()`，旧的「点空白只清空选区」改成
  「点空白清空、拖空白框选」。
- **填充图标**：`QPainter.rotate(32°)` 画上宽下窄的敞口桶身 + 椭圆桶口，
  桶口右下接一条下坠液柱和两滴颜料；**删掉原来的 `drawArc` 提手和下三角箭头**。
  验收脚本 `rwmap/probe/probe_toolbar_merge_icons.py` 会锁死
  「右下有滴漆像素 / 桶身倾斜 / 左上无提手弧线」。

回归：新增 `test_toolbar_merge.py`（15 例：按钮数、双快捷键、别名、
图标像素、瓦片层/对象层两类框选、点对象出箭头、拖动一条撤销、右键菜单、
真实 M/S 按键、真实鼠标框选、滴漆像素），同步更新
`test_object_shapes.py` / `test_toolbar_flow.py` 的旧断言；
全量单测 **532 全 OK**。验收图：`examples/工具栏_合并选择与油漆桶.png`。

## 第三十五轮：让「插入图片 / 插入文字」真正适配游戏

**问题**：游戏加载地图时报

```
desync:Error: Unknown type: found on 图片
desync:Error: Skipping trigger:文字 - no properties found
```

根因是工具栏默认把新对象放进 `Triggers` 层，而游戏会把该层每个 `<object>` 当
触发器读：Tiled 的 `<text>` 对象没有 `<properties>`，图片对象只有编辑器自用的
`image` 属性、没有游戏认识的 `type`。两者都只是编辑器侧的装饰，游戏不认。

**游戏侧依据**（本机 `<游戏目录1.15>\mods\maps\` 实测）：

- `22P宋金战争剧情加强版.tmx`：`<object name="建康府" type="mapText">` +
  `text` / `textColor` / `textSize` —— 游戏内显示文字的正解。
- `embedded_custom_tileset_example.tmx`：tileset 的文本属性 `embedded_png`
  存 PNG base64，官方说明原文 "Embedded images are created by creating a
  string called 'embedded_png' ... paste that string into the text of this
  property."；游戏只读内嵌数据，`<image source>` 给 Tiled 看。社区图普遍用
  **RGBA PNG 且不写 `trans`**，不需要色键透明。
- 同示例的 Triggers 层里有 `type="info"` 的 `readme` 对象 —— 官方认可的
  「游戏忽略」注释节点，不会触发上面两条报错。

**实现**（新增 `rwmap/rw/game_decor.py`，改 `tmx_writer.py` / `tmx_reader.py` /
`main_window.py` / `triggers_dock.py`）：

- **文字 → `mapText`**：写出 `type="mapText"` + `text` / `textColor` / `textSize`
  属性，**不再写 `<text>` 子节点**；读回按 `type` 还原成编辑器里的文字对象。
  文字内容以属性包 `text` 为准（属性面板可直接改），插入时和旧面板编辑时
  都会同步进属性包，所以两条入口都生效。旧的 `<text>` 对象保存时自动升级。
- **图片 → `info` 注释 + 内嵌图集贴图**：对象本身写成 `type="info"`（保留
  `image` 属性以便编辑器读回后继续移动/缩放/删除）；保存时把图片按 20px
  网格切成 `cols × rows` 个瓦片，新建一个 `rwmap_decor_N` tileset，
  `embedded_png` 写入 PNG base64，并把对应 gid 铺进 `Items` 瓦片层，
  游戏里就能看到这张图。
- **firstgid 不撞车**：按所有现有 tileset 的 `firstgid + tilecount` 取末尾；
  外部 `.tsx` 缺 `tilecount` 时，用下一个 tileset 的 `firstgid` 反推上界。
- **全量重投影**：每次保存先删掉上一轮的 `rwmap_decor_*` 图集、清掉 `Items`
  层里落在旧 gid 区段的格子，再按当前图片对象重建。移动 / 缩放 / 删除图片
  都不会留下幽灵贴图；内存里的 `Items` 层 cells 在写盘后还原成用户编辑的
  干净数据，读盘时也会把装饰 gid 从内存抹掉（DOM 保留给游戏）。
- **降级安全**：图片源文件找不到、Pillow 不可用或地图只有文字对象时，
  不生成图集、不新建 `Items` 层，对象仍是 `info`，游戏照常加载不报错。

> 保存时会在 `.tmx` 同目录额外写 `rwmap_decor_0.png` 之类的副本，供 Tiled
> 预览；游戏只依赖嵌入的 `embedded_png`，单独分发 `.tmx` 依然能显示图片。
> 图片减少后残留的旧副本不会被自动删除。

回归：新增 `test_game_decor.py`（15 例：mapText 写出/读回/属性面板优先/
旧 `<text>` 升级、info 节点、embedded_png 可解码为 RGBA PNG、Items gid 排布、
firstgid 避让、内存还原、移动/删除/缩放重投影、多图 gid 不重叠），
同步更新 `test_object_shapes.py` 里针对 `<text>` 子节点的旧断言；
**全量单测 547 例 0 失败**。
验收脚本 `rwmap/probe/probe_game_decor.py` 可直接跑一遍完整写盘→读回→二次写盘。

## 撤销覆盖（Ctrl+Z / Ctrl+Shift+Z）

原则：**除「移动视角 / 缩放 / 平移」这类纯视图操作外，任何会改到文档内容的操作都进
`QUndoStack`**，而且一次用户动作只留一条撤销记录。多文档标签下撤销动作跟着
「当前激活文档」的栈走（`_bind_undo_actions()`），不会误改另一个标签的地图。

| 类别 | 操作 | 命令 / 说明 |
|---|---|---|
| 瓦片 | 画笔 / 橡皮 / 图章 / 填充 / 钢笔线 / 框选粘贴 / 移动 / 自动映射 | `DrawCommand`，整笔一条，自动映射走 `beginMacro` 合成一条 |
| 图层 | 可见性（眼睛）/ 透明度 / 锁定 / 重命名 | `LayerFlagCommand`；透明度拖动靠 `merge_key` 合并成一条；「显示/隐藏其它图层」整批一条宏 |
| 图层 | 新建 / 删除 / 复制 / 上下移 / 向下合并 | `LayerStructureCommand` / `MergeLayerCommand` |
| 地图 | 翻转 / 旋转 / 改尺寸 | `MapTransformCommand`（含宽高交换） |
| 对象 | 新建（拖框 / 多段 / 模板 / 图片 / 文字）/ 顶点编辑 / 属性编辑 / 删除 | `ObjectCommand`（整层对象快照） |
| 对象 | 拖动整组 / 组合 / 拆除组合 / 加入组 / 对齐 / 分布 / 删除（可跨层） / **拖 8 个箭头缩放** | `MultiObjectCommand`，多层也只算一条；数据没变化时不产生空记录 |
| 触发器 | 节点图建线（`alsoActivate`）/ 删线 / 改线型 / 建组 / 加入组 | 走 `ObjectCommand` / `MultiObjectCommand`，写进 TMX 的引用可原样撤回 |
| 触发器 | 插入随机事件（含自动新建 Triggers 层） | 宏：`新建 Triggers 层` + `插入随机事件`，一次 Ctrl+Z 全清 |
| 图集 | 每瓦片属性（地形 / 单位 / 其他 / 自定义键 / 改键名 / 删键 / 整理冲突） | `TilesetCommand`（整表快照）；输入框逐字符写回靠 `merge_key` 合并成一条 |

**不入撤销栈的**：缩放 / 平移 / 适配窗口、图层过滤与节点摆位（只影响本视图布局）、
打开 / 新建 / 切换版本（重建文档，栈会清空）、自动映射开关（只改显示）。

## 已知坑（都已规避）

- **gzip / zlib 混用**：读入按 magic 探测，写出沿用原文件压缩格式。
- **`trans="008a76"`**：色键透明，不是 alpha 通道；`units.tsx` 无 trans 且 `spacing/margin=1`。
  用 `ImageChops` 向量化处理，不做逐像素 Python 循环。
- **外部 `.tsx` 不写 `tilecount` / `columns`**：图块数量由图集真实尺寸隐含决定，
  且 `.tsx` 里的 `<image width/height>` 可能缺失或与 PNG 不符，必须读 PNG 头。
  按 0 处理会让整段 GID 变成紫色占位图（草地变紫、深棕土块消失）。
- **Qt 缩放锚点**：重写 `mouseMoveEvent` 后不能依赖 `AnchorUnderMouse`，需自算补偿。
- **全图重绘**：覆盖全图的 overlay 禁止整层 `update()`，只允许 `update(小矩形)`。
- **官方 `assets/maps` 同目录无资源目录**，PNG 平铺在 `assets/tilesets/bitmaps/`，五级候选兜底；导出时补齐资源副本目录树。
- **flipping flags**：`gid.py` 拆 H/V/D，写回重组 raw_gid，不能吞掉。
- **`tmxrasterizer.exe` 弹窗「no Qt platform plugin」**：它是 Qt 程序且只带 windows 插件。
  父进程若设置 `QT_QPA_PLATFORM=offscreen`（离屏自测）会被继承。
  `rw/thumbnail.py` 启动子进程时清理全部 Qt 环境变量并设 `cwd=<Tiled目录>`，已修复。
- **未证实的语义**：`team=-2`、`PathingOverride`、`set` 层在 NotTiled 与 Tiled 下的冲突，
  首版只做只读保真，不写入推测值。

## 数据来源（只读，不修改游戏本体）

`<游戏目录1.14>\`、`<游戏目录1.15>\`、`<Tiled目录>\`。

## 附：铁锈地图制作「属性 / 指令」速查

- `docs/铁锈地图_属性与指令速查.md` —— 完整版：地图根属性、图层约定、图块属性、
  单位/队伍、Triggers 指令总表（12 类 type + 按 name 的固定信息对象）、通用调度参数、
  `spawnUnits` 内联参数、自动映射规则属性、易错拼写表、项目内对应代码位置、最小建图清单。
- `docs/铁锈地图_速查表.html` —— 深色可搜索速查页（100 条，按类别过滤，`/` 聚焦搜索）。
- `rwmap/probe/probe_rw_reference.py` —— 只读扫描本机 TMX 统计属性/指令频次，
  重跑命令与输出路径写在速查文档末尾；换游戏版本或加地图后可直接刷新数据。

数据来自本机 23 张有效 TMX 实测 + 官方 `custom_maps_readme.txt` / Wiki / 社区教程，
`team=-2`、`-1`、`none` 等未证实语义仍按「只读保真、不写入推测值」处理。

