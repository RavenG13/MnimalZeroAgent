# ZeroAgent 云端大脑

将 ZeroAgent 部署为云端 HTTP API 服务，支持多用户、会话管理、安全沙箱。

## 架构

```
用户(Client) --> [HTTP API] --> ZeroAgent 服务器 --> DeepSeek API
                                    |
                        [ SQLite 记忆存储 ]
                        [ 安全工具沙箱 ]
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 设置 API Key

```bash
# Linux/Mac
export DEEPSEEK_API_KEY="sk-your-key"

# Windows CMD
set DEEPSEEK_API_KEY=sk-your-key

# Windows PowerShell
$env:DEEPSEEK_API_KEY="sk-your-key"
```

### 3. 启动服务器

```bash
python server.py
```

服务器默认运行在 `http://0.0.0.0:8000`

### 4. 使用客户端测试

新开一个终端：

```bash
python client.py
```

### 5. 直接调用 API

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "小明", "message": "今天A股怎么样？"}'
```

## 安全特性

云端模式下自动禁用以下有安全风险的工具：
- `file_tools` - 文件读写
- `shell_tools` - 命令执行
- `pdf_reader` - PDF文件读取

仅保留安全工具：
- `search_tools` - 网页搜索
- `stock_tools` - 股票查询
- `memory_tools` - 记忆管理（SQLite + 多用户隔离）
- `table_viewer` - 表格查看

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务器信息 |
| GET | `/health` | 健康检查 |
| POST | `/chat` | 发送消息 |
| GET | `/sessions` | 列出会话 |
| DELETE | `/sessions/{id}` | 删除会话 |

### POST /chat

请求体：
```json
{
  "user_id": "小明",
  "message": "你好",
  "session_id": null
}
```

响应：
```json
{
  "reply": "你好！有什么可以帮你的？",
  "session_id": "uuid-string",
  "user_id": "小明"
}
```

## 部署到服务器

### 方式一：直接运行

```bash
python server.py
```

### 方式二：后台运行（screen）

```bash
screen -S zeroagent
python server.py
# Ctrl+A, D 脱离
```

### 方式三：systemd 服务

```ini
[Unit]
Description=ZeroAgent Cloud Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 /path/to/server.py
WorkingDirectory=/path/to/cloud-agent
Environment="DEEPSEEK_API_KEY=sk-your-key"
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| DEEPSEEK_API_KEY | - | API Key（必填） |
| DEEPSEEK_BASE_URL | https://api.deepseek.com | API 地址 |
| DEEPSEEK_MODEL | deepseek-v4-flash | 模型名称 |
| SERVER_HOST | 0.0.0.0 | 监听地址 |
| SERVER_PORT | 8000 | 监听端口 |
