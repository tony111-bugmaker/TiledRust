# 本地 AI 控制服务 API

软件启动即监听 `127.0.0.1:8765`（仅本机）。所有请求（除 `/ping`）需带：

```
X-Auth-Token: <config/local_service_token.txt 里的值>
```

在「设置 → 本地 AI 控制服务」可开关；端口/开关在 `config/app_config.json` 的
`local_service` 节点可改。所有编辑操作都进撤销栈，可 Ctrl+Z 撤回。

## 连通与说明

```bash
curl http://127.0.0.1:8765/ping                       # {"ok":true,...}  免 token
curl -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8765/help   # 全部 action 及参数
```

## 只读查询（GET）

| 端点 | 返回 | 用途 |
|---|---|---|
| `/summary` | `summary`: 尺寸/图层/对象数/图集统计 | 一眼看清地图结构 |
| `/state` | `state`: 当前工具/图层/笔刷/缩放/标尺段 | 知道编辑器当前在干嘛 |
| `/layers` | `layers`: 图层名/类型/可见/锁定 | 找要操作的层 |
| `/objects?layer=名字` | `objects`: 对象/属性 | 读对象与触发器参数 |
| `/triggers` | `triggers`: 所有 RW 触发器 | 分析刷兵/检测/链接 |
| `/cells?layer=名字&x=0&y=0&w=10&h=10` | `cells`: 二维 gid 数组 | 读地形，分析布局 |
| `/screen` | `base64`: 窗口截图 PNG | 目视当前画面 |

## 编辑操作（POST /action）

请求体是 JSON，`action` 指定操作，其余为参数：

```bash
curl -X POST http://127.0.0.1:8765/action \
  -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"action":"draw_cells","layer":"Ground","cells":[[2,3,5],[3,3,5]]}'
```

| action | 参数 | 说明 |
|---|---|---|
| `open_map` | `path` | 打开地图文件 |
| `save_map` | — | 保存 |
| `set_tool` | `tool` | brush/fill/select/ruler/pen/nodes/rect/... |
| `set_gid` | `gid` | 设笔刷图块 |
| `draw_cells` | `layer`, `cells:[[col,row,gid],...]` | 批量画瓦片，一条撤销 |
| `fill` | `layer`, `col`, `row` | 洪水填充 |
| `add_object` | `layer`, `x`,`y`,`width`,`height`, `name`,`type`,`shape`,`properties` | 加对象/触发器 |
| `edit_object` | `layer`, `object_id`, `changes:{属性:值}` | 改对象（含自定义属性） |
| `delete_object` | `layer`, `object_id` | 删对象 |
| `set_map_props` | `changes:{属性:值}` | 改地图属性 |
| `autocrop` | — | 自动裁剪到瓦片内容 |
| `transform` | `op`: flip_h/flip_v/rot_cw/rot_ccw | 翻转/旋转 |
| `undo` / `redo` | — | 撤销/重做 |
| `goto` | `col`, `row` | 视图定位 |

## AI 改完批量导入

`POST /summary/import`，体为 `{"ops":[{"action":"...",参数...}, ...]}`。
逐条在 GUI 线程执行、各自进撤销栈，返回每条的 `ok`/`error`。

## 典型流程（AI 分析 → 修改）

1. `GET /summary` + `GET /triggers` + `GET /cells` 拿全貌
2. 分析后构造 `ops` 指令序列
3. `POST /summary/import` 一次导入，`GET /screen` 目视确认
4. 不满意 `POST /action {"action":"undo"}`

## 实现位置

- `rwmap/app/local_service.py` — HTTP 服务、鉴权、线程桥、配置
- `rwmap/app/local_service_actions.py` — GUI 线程动作分发（进撤销栈）
- `rwmap/app/main_window.py` — 挂载点（构造 / 设置菜单 / closeEvent）
- 测试：`rwmap/tests/test_local_service.py`
