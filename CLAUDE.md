# ZeroAgent — 云端 AI 项目管理助手

## 启动

```bash
cd "C:/Users/user LAN/Desktop/python/cloud-agent"
python server.py
# 浏览器打开 http://127.0.0.1:8010
```

无需数据库/Redis 依赖，仅需 Python 3.10+ 和 `pip install` requirements.txt。

## 项目结构

```
cloud-agent/
├── server.py              # FastAPI 主程序（~1700行）
├── auth.py                # JWT 用户认证（SHA256 哈希密码）
├── static/
│   ├── chat.html          # 主前端 SPA（~2400行单文件，所有 CSS/JS 内联）
│   └── index.html         # 登录页
├── tools/
│   ├── project_tools.py   # 项目管理 SQLite（projects/tasks/schedule 三表）
│   ├── memory_tools.py    # AI 记忆存储
│   ├── user_tools.py      # 用户自定义 Python 工具（动态注册）
│   ├── file_storage.py    # 用户文件存储（1GB/人）
│   ├── scheduler.py       # 定时任务调度
│   ├── search_tools.py    # 网络搜索
│   ├── shell_tools.py     # shell 执行
│   ├── file_tools.py      # 文件操作
│   ├── stock_tools.py     # 股票查询
│   ├── table_viewer.py    # 表格查看
│   ├── current_date.py    # 当前日期
│   └── usa_time.py        # 美国时间
├── client/                # 客户端节点（通过 WebSocket 连接服务器）
│   ├── client.py          # 桌面客户端（P2P agent node）
│   ├── auto_client.py     # 自动执行模式
│   └── node_tools.py      # 本地工具（读文件、写文件、搜索、shell、opencode）
├── cli-csharp/            # C# 客户端（功能与 Python 客户端一致）
│   └── ZeroAgentCli/
│       ├── NodeTools.cs   # 工具实现（read/write/search/shell/opencode）
│       ├── WsClient.cs    # WebSocket 客户端
│       └── ...
├── data/                  # 所有持久化数据（用户隔离）
│   ├── users.json         # 用户账号
│   ├── users/<u>/         # 项目数据库 projects.db
│   ├── memory/<u>.db      # AI 记忆
│   ├── sessions/<u>.json  # 对话历史
│   ├── settings/<u>.json  # API 设置
│   ├── nodes/<u>.json     # 客户端节点
│   ├── user_tools/<u>/    # 用户自定义工具
│   ├── user_files/<u>/    # 用户文件存储
│   └── scheduler/         # 定时任务
└── sessions/              # 旧版会话（已迁移到 data/sessions/）
```

## 核心架构

### 多用户隔离

Flask/FastAPI 的 `Depends(get_current_user)` 从 JWT 解出 `username` → 通过 `threading.local` 注入到各工具模块 → 每个工具调用 `get_current_user()` 获取当前用户 → SQLite/JSON 路径自动路由到 `data/<type>/<username>.*`。

关键代码（`tools/project_tools.py`）：
```python
_thread_local = threading.local()
def set_current_user(username): _thread_local.username = username
def get_current_user(): return getattr(_thread_local, "username", None) or "default"
def _get_db_path(username): return f"data/users/{username}/projects.db"
```

`asyncio.to_thread` 线程池中通过闭包重新设置 username，确保线程切换不丢失上下文。

### JWT 认证

`auth.py` — 密码 SHA256 哈希，JWT 24 小时过期，`users.json` 存储。所有 API 请求通过 `Authorization: Bearer <token>` 头认证。

## 数据库 Schema（projects.db）

```sql
projects (id PK, name UNIQUE, description, leader, deadline, goal,
          status, progress REAL, progress_note, created_at, updated_at)
tasks (id PK, project_id FK, parent_id FK(self), name, description,
       status, start_time, end_time, priority, created_at, updated_at)
schedule (id PK, date, time_slot, content, priority, created_at)
```

- `tasks.parent_id` 自引用，支持无限层级任务嵌套
- SQLite WAL 模式 (`PRAGMA journal_mode=WAL`)
- 每用户独立 .db 文件

## 文件工具 v2（局部编辑 + 搜索）

服务端 `tools/file_tools.py` 和客户端 `client/node_tools.py` 均支持以下工具：

### read_file
```python
read_file(file_path, start_line=0, end_line=0)
# 整文件模式：不传行号，返回完整内容（大文件截断到前500行）
# 行号范围模式：传 start_line/end_line，返回指定行范围（带行号）
```

### write_file — 三种模式
| 模式 | 参数 | 用途 |
|------|------|------|
| 整文件 | `content` | 小文件覆盖（>30KB 自动拒绝） |
| 行号替换 | `content` + `start_line` + `end_line` | 精准替换指定行 |
| 文本匹配 | `old_text` + `new_text` + `expected_count` | 按内容匹配替换（推荐） |

所有模式返回 **diff 摘要**（新增/删除行数 + unified diff 预览）。

### search_in_file
```python
search_in_file(file_path, pattern, is_regex=False, case_sensitive=False)
# 返回匹配的行号和内容（最多50条）
```

### 安全改进
- 原子写入（临时文件 + `os.replace()`）
- 大文件整写拒绝（>30KB）
- 内容缩水检测（<30% 拒绝）
- 变更比例警告（>40%）
- `expected_count` 匹配次数校验

### 推荐工作流
```
search_in_file → read_file(行号) → write_file(old_text/new_text) → diff 确认
```

## 客户端工具（run_opencode）

客户端支持调用本地 opencode AI编程助手：

```python
run_opencode(
    message: str,           # 任务描述（必填）
    model: str = None,      # 模型，如 "anthropic/claude-sonnet-4-20250514"
    session_id: str = None, # 继续之前的会话
    cwd: str = None,        # 工作目录
    auto: bool = False,     # 自动批准权限
    timeout: int = 300,     # 超时（30-1800秒）
)
```

适用于需要复杂代码修改、多文件重构、项目级任务的场景。C# 客户端功能一致。

## 前端架构（chat.html 单文件 SPA）

纯原生 JS + CSS，无框架/构建工具。

### 布局（VS Code 风格）
- **activity-bar**（48px）: 左栏图标 — 聊天💬 / 数据表格📊 / 设置⚙️
- **side-panel**（260px）: 会话列表 / 项目树形视图
- **chat-area**: 消息列表 + 输入框
- **data-view**: 全屏 Excel 风格表格（点击📊切换）

### 核心功能模块
1. **流式 AI 输出**: `POST /chat/stream` SSE 端点，`token`/`tool_start`/`tool_end`/`done`/`error` 事件，`AbortController` 停止
2. **Excel 表格视图**: `GET/PUT/POST/DELETE /api/db/tables/{table}/rows`，可编辑单元格，Sheet 标签页
3. **主题系统**: 5 套配色（深海/极光/暗金/墨紫/经典灰/浅色），CSS 变量驱动，localStorage 持久化

### 消息渲染
- Markdown（marked.js + highlight.js 语法高亮）
- 工具调用可折叠卡片（参数/结果）
- 消息轮次追踪（`data-round` 属性，支持单轮删除）

## 关键 API 端点

### 认证
- `POST /api/register` / `POST /api/login` — 注册/登录
- `GET /api/me` — 当前用户

### 聊天
- `POST /chat` — 非流式（旧版，保留兼容）
- `POST /chat/stream` — SSE 流式（主要使用）

### 项目管理
- `GET /projects` — 项目任务树
- `GET/PUT/POST/DELETE /api/db/tables/{table}/rows[/{id}]` — 表格 CRUD
- `GET /api/db/tables` — 表列表及 schema

### 会话
- `GET/POST /api/sessions` — 列表/创建
- `GET/PUT/DELETE /api/sessions/{id}` — 详情/改名/删除
- `DELETE /api/sessions/{id}/messages/{round}` — 删除某轮对话

### 其他
- `GET/PUT /api/settings` — 用户 API Key/Base URL/Model
- `GET/POST/DELETE /api/user-tools` — 用户自定义工具
- `GET/DELETE /api/scheduler/tasks` — 定时任务
- `GET /api/nodes` — 客户端节点
- `GET /health` — 健康检查

## 开发注意事项

- **Windows GBK 编码**: Python `print()` 不能输出 emoji/中文特殊字符，改用 ASCII 占位。中文数据在 SQLite 中正确存储为 UTF-8（`conn.text_factory = str`）。
- **热键**: `static/chat.html` 中的 `api()` 函数是通用请求封装，自动附加 JWT。所有 API 调用使用它。
- **SQL 安全**: `/api/db/*` 端点的表名白名单 `{"projects","tasks","schedule"}`，列名通过 PRAGMA 动态校验。
- **主题 CSS**: 所有颜色在 `:root` 和 `body[data-theme="xxx"]` 块中定义为变量，组件样式引用 `var(--xxx)`。新增主题只需复制一个 `body[data-theme="..."]` 块。
- **流式输出**: 后端 `chat_stream` 使用 `AsyncOpenAI(stream=True)` + `StreamingResponse`，前端通过 `fetch` + `ReadableStream` 解析 SSE。`asyncio.CancelledError` 捕获客户端断开。
