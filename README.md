# ZeroAgent — 云端 AI 项目管理助手

多用户云端 AI 助手，支持项目管理、文件操作、定时任务、记忆存储，可通过 WebSocket 连接本地客户端节点扩展能力。

## 架构

```
浏览器/CLI
    │
    ▼
┌─────────────────────────────────────────┐
│  ZeroAgent 服务器 (FastAPI)             │
│  ├── JWT 认证（多用户隔离）              │
│  ├── 会话管理（SSE 流式输出）            │
│  ├── 工具沙箱（项目/记忆/文件/搜索...）  │
│  └── 客户端节点调度（WebSocket）         │
└─────────────────────────────────────────┘
    │                    │
    ▼                    ▼
AI API (DeepSeek等)    本地客户端节点
                       (Python / C#)
                           │
                           ▼
                      本地文件系统
                      opencode / Shell
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务器

```bash
python server.py
```

服务器默认运行在 `http://127.0.0.1:8010`

### 3. 注册账号并登录

浏览器打开 `http://127.0.0.1:8010`，注册账号后进入聊天界面。

### 4. 配置 API Key

在聊天界面的设置⚙️中填入：
- **API Key**: 你的 AI 服务商密钥
- **Base URL**: API 地址（默认 DeepSeek）
- **Model**: 模型名称

### 5. 连接客户端节点（可选）

```bash
# Python 客户端
python client/client.py --server ws://127.0.0.1:8010 --token YOUR_JWT_TOKEN

# C# 客户端
cd cli-csharp/ZeroAgentCli
dotnet run -- --server ws://127.0.0.1:8010 --token YOUR_JWT_TOKEN
```

客户端连接后，AI 可以调用本地文件操作、Shell 命令、opencode 等工具。

## 项目结构

```
cloud-agent/
├── server.py              # FastAPI 主程序
├── auth.py                # JWT 用户认证
├── static/
│   ├── chat.html          # 主前端 SPA（单文件，所有 CSS/JS 内联）
│   └── index.html         # 登录页
├── tools/
│   ├── project_tools.py   # 项目管理 SQLite（projects/tasks/schedule）
│   ├── memory_tools.py    # AI 记忆存储
│   ├── file_tools.py      # 文件操作（v2：局部编辑 + 搜索）
│   ├── user_tools.py      # 用户自定义 Python 工具
│   ├── file_storage.py    # 用户文件存储（1GB/人）
│   ├── scheduler.py       # 定时任务调度
│   ├── search_tools.py    # 网络搜索
│   ├── shell_tools.py     # shell 执行
│   └── ...                # 其他工具
├── client/
│   ├── client.py          # Python 桌面客户端
│   ├── auto_client.py     # 自动执行模式
│   └── node_tools.py      # 本地工具（read/write/search/shell/opencode）
├── cli-csharp/
│   └── ZeroAgentCli/      # C# 客户端（功能与 Python 客户端一致）
└── data/                  # 持久化数据（用户隔离）
```

## 核心功能

### 多用户隔离

- JWT 认证（SHA256 哈希密码，24小时过期）
- 每用户独立数据库、会话、设置、文件存储
- 工具调用自动注入当前用户上下文

### 文件工具 v2

支持三种写入模式，解决大文件修改问题：

| 模式 | 参数 | 说明 |
|------|------|------|
| 整文件 | `content` | 小文件覆盖（>30KB 自动拒绝） |
| 行号替换 | `content` + `start_line` + `end_line` | 精准替换指定行 |
| 文本匹配 | `old_text` + `new_text` | 按内容精确匹配替换（推荐） |

其他工具：
- `read_file` — 支持行号范围读取
- `search_in_file` — 文本/正则搜索，返回匹配行号

所有写入操作返回 diff 摘要，支持原子写入和内容安全校验。

### 客户端节点

客户端通过 WebSocket 连接服务器，暴露本地能力给云端 AI：

| 工具 | 说明 |
|------|------|
| `read_file` | 读取本地文件 |
| `write_file` | 写入本地文件（支持局部编辑） |
| `search_in_file` | 搜索文件内容 |
| `list_files` | 列出目录 |
| `run_shell` | 执行 Shell 命令 |
| `run_opencode` | 调用本地 opencode AI 编程助手 |
| `get_system_info` | 获取系统信息 |

### 前端

- 纯原生 JS + CSS，无框架依赖
- VS Code 风格布局（活动栏 + 侧边栏 + 聊天区）
- 流式 AI 输出（SSE）
- Excel 风格数据表格视图
- 6 套主题（深海/极光/暗金/墨紫/经典灰/浅色）
- Markdown 渲染 + 代码高亮

## API 接口

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/register` | 注册 |
| POST | `/api/login` | 登录，返回 JWT |
| GET | `/api/me` | 当前用户信息 |

### 聊天

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | 非流式聊天（兼容旧版） |
| POST | `/chat/stream` | SSE 流式聊天（主要使用） |

### 项目管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/projects` | 项目任务树 |
| GET | `/api/db/tables` | 表列表及 schema |
| GET/POST | `/api/db/tables/{table}/rows` | 表格行 CRUD |
| GET/PUT/DELETE | `/api/db/tables/{table}/rows/{id}` | 单行操作 |

### 会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/sessions` | 列表/创建 |
| GET/PUT/DELETE | `/api/sessions/{id}` | 详情/改名/删除 |
| DELETE | `/api/sessions/{id}/messages/{round}` | 删除某轮对话 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/PUT | `/api/settings` | 用户 API 设置 |
| GET/POST/DELETE | `/api/user-tools` | 用户自定义工具 |
| GET/DELETE | `/api/scheduler/tasks` | 定时任务 |
| GET | `/api/nodes` | 已连接的客户端节点 |
| GET | `/health` | 健康检查 |

## 部署

### 直接运行

```bash
python server.py
```

### 后台运行（screen）

```bash
screen -S zeroagent
python server.py
# Ctrl+A, D 脱离
```

### systemd 服务

```ini
[Unit]
Description=ZeroAgent Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 /path/to/server.py
WorkingDirectory=/path/to/cloud-agent
Restart=always

[Install]
WantedBy=multi-user.target
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| DEEPSEEK_API_KEY | - | API Key（可在界面设置） |
| DEEPSEEK_BASE_URL | https://api.deepseek.com | API 地址 |
| DEEPSEEK_MODEL | deepseek-v4-flash | 默认模型 |
| SERVER_HOST | 0.0.0.0 | 监听地址 |
| SERVER_PORT | 8010 | 监听端口 |

## 技术栈

- **后端**: Python 3.10+ / FastAPI / SQLite / asyncio
- **前端**: 原生 JS + CSS（SPA，单文件内联）
- **认证**: JWT（PyJWT）+ SHA256
- **AI**: OpenAI 兼容 API（DeepSeek / OpenAI / Claude 等）
- **客户端**: Python / C#（WebSocket）
