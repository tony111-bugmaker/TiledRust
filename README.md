# 铁锈战争专用地图编辑器（rwmap）

基于 PySide6 的桌面地图编辑器。借鉴 Tiled 的文档模型 / 图层 / 图块集 / 撤销栈 / Dock 面板架构，专为《铁锈战争》(Rusted Warfare) 的 TMX 地图格式适配。

**保真写回**是核心设计：只改被编辑图层的 `<data>`，不改层顺序与未知属性，官方 1.14 / 1.15 地图可直接打开再保存。

> 完整开发记录（35+ 轮迭代、已知坑、验收细节）见 [README-开发日志.md](README-开发日志.md)。

## 功能

- 打开 / 编辑官方 `1.14`、`1.15` `.tmx` 地图，正确渲染 5 层
- 图层面板：Tiled 风格（行内眼睛/锁、图标按钮栏、双击重命名、右键菜单）
- 图块集面板：底部标签栏 + 整图集视图 + 矩形框选盖章
- 绘制时自动地图（官方 `rules.txt`，Ground / Items 实时过渡）；支持自定义规则导入
- 画笔 / 橡皮 / 临时吸管（I 键或 Alt+左键）、框选复制粘贴移动、Ctrl+Z/Y 撤销
- 钢笔 / 贝塞尔曲线，接入自动地图
- 对象编辑：矩形/椭圆/点/多边形/折线/图块/模板/文字/图片，8 手柄缩放
- 触发器语义编辑 + 节点图视图
- 随机生成单位（按分类筛选、权重）、单位检测
- 缩略图、导出到 `mods/maps`、`.rwmod` 打包
- 翻转/旋转整图、自动裁剪、新建地图、地图属性编辑、转到图块
- 面板布局自定义（显隐、导入/导出 JSON）
- 深色双色主题（黑 `#101010` + 深灰 `#242424`，详见 [docs/双色暗色主题.md](docs/双色暗色主题.md)）

## 运行

需要 **Python 3.10+**（开发用 3.12）。

```bash
python -m venv .venv
# Windows
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe run.py
# Linux / macOS
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

- 不自动打开示例地图：`python run.py --no-sample`
- 无 GUI 跑探针：`python run.py --probe`

依赖仅三项：`PySide6>=6.8`、`Pillow>=10`、`lxml>=5`（见 `requirements.txt`）。
**Tiled 不是运行依赖**——软件直接解析 TMX/TSX，无需安装 Tiled。

## 配置

首次运行前编辑 `config/profiles/rw115.json`（或 `rw114.json`），把占位符换成你本机的实际路径：

| 字段 | 含义 |
|---|---|
| `game_root` | 游戏本体目录（含 `assets/maps`） |
| `tilesets_root` | 图集目录（含 `units.tsx` 等） |
| `mods_maps` | 自定义地图目录 |
| `export_dir` | 导出目录 |
| `tiled_exe` / `rasterizer_exe` | 仅缩略图渲染用，可留空 |
| `sample_map` | 启动示例地图 |

`config/app_config.json` 的 `game_roots` 同样替换为实际路径。也可用环境变量 `RW_GAME_ROOTS`（多个目录用 `;` 或 `:` 分隔）辅助定位。

`config/unit_names.json` 是单位中文名对照表（随仓库附带，无需重新生成）。

## 目录

```
rwmap/
  app/        主窗口、状态、面板布局
  commands/   QUndoCommand 撤销命令
  document/   MapDocument / 图层 / 对象 / 属性 / 版本配置
  io/         TMX / TSX 读写、XML 保真
  rw/         自动映射、导出、触发器、单位、.rwmod 打包
  tilesets/   图集加载与缓存、游戏定位
  tools/      画笔 / 橡皮 / 填充 / 吸管 / 选择 / 钢笔
  view/       QGraphicsView 场景与渲染
  widgets/    各 Dock 面板与对话框
  probe/      开发探针（可视化自测用）
  tests/      unittest 回归套件
config/       版本配置与单位名表
docs/         功能对照、自动映射规则、主题、速查表
examples/     验证截图与演示脚本
```

## 文档

- [Tiled菜单与铁锈地图编辑器功能对照清单.md](docs/Tiled菜单与铁锈地图编辑器功能对照清单.md)
- [自动映射规则指南.md](docs/自动映射规则指南.md) — 规则写法与官方规则实测
- [双色暗色主题.md](docs/双色暗色主题.md)
- [软件UI色卡.md](docs/软件UI色卡.md)
- [铁锈地图_属性与指令速查.md](docs/铁锈地图_属性与指令速查.md)

## 测试

```bash
QT_QPA_PLATFORM=offscreen python -m unittest rwmap.tests.test_theme_two_color rwmap.tests.test_roundtrip
```

全量约 686 例（部分测试读取官方地图资产，需先配置游戏路径）。

## 许可

MIT License —— 见 [LICENSE](LICENSE)。

铁锈战争 (Rusted Warfare) 是 Corroding Games 的注册商标。本仓库为独立开发的第三方工具，不含游戏本体资产；地图与图集资源请从你合法持有的游戏安装目录读取。
