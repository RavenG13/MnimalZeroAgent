"""
============================================================
  ZeroAgent Server - 云端大脑服务端
  基于 FastAPI，将本地 Agent 封装为 HTTP API 服务
  支持多用户隔离、会话管理、安全沙箱
============================================================
"""
import os
import sys
import json
import uuid
import importlib
import traceback
import asyncio
import time
import copy
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, Form, WebSocket
from starlette.websockets import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from pydantic import BaseModel
import json as _json

# ============================================================
#  自定义 UTF-8 JSONResponse（修复中文字符乱码）
#  默认的 Starlette JSONResponse 对 application/json 不会
#  附加 charset=utf-8，导致中国 Windows (GBK 系统) 的浏览器
#  可能错误解码 JSON 中的中文字符。
# ============================================================
class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
import uvicorn

# 导入认证模块
from auth import register, login, verify_token
from tools import project_tools as ptools
from tools import user_tools
from tools import file_storage
from tools import scheduler as task_scheduler

# ============================================================
#  配置
# ============================================================
# 服务器全局默认值（仅当用户未设置自己的 API 配置时使用）
DEFAULT_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEFAULT_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("SERVER_PORT", "8010"))

# 用户设置存储目录
SETTINGS_DIR = os.path.join(os.path.dirname(__file__) or ".", "data", "settings")
os.makedirs(SETTINGS_DIR, exist_ok=True)


def _get_user_settings_path(username: str) -> str:
    """获取用户设置 JSON 文件路径"""
    safe_name = username.replace("/", "_").replace("\\", "_")
    return os.path.join(SETTINGS_DIR, f"{safe_name}.json")


def _load_user_settings(username: str) -> dict:
    """加载用户 API 设置"""
    path = _get_user_settings_path(username)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_user_settings(username: str, settings: dict):
    """保存用户 API 设置"""
    path = _get_user_settings_path(username)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _get_user_client(username: str) -> OpenAI:
    """根据用户设置创建 OpenAI 客户端（若用户未设置则回退到全局默认）"""
    settings = _load_user_settings(username)
    api_key = settings.get("api_key") or DEFAULT_API_KEY
    base_url = settings.get("base_url") or DEFAULT_BASE_URL
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="未配置 API Key。请点击左下角⚙️设置图标，填入你的 API Key。\n"
                   "支持所有 OpenAI 兼容的服务商（DeepSeek / Ollama / vLLM / Groq 等）。",
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def _get_user_model(username: str) -> str:
    """获取用户设置的模型名"""
    settings = _load_user_settings(username)
    return settings.get("model") or DEFAULT_MODEL


# ============================================================
#  客户端节点持久化存储 & 内存注册表
#  持久化文件: data/nodes/<username>.json
#  内存注册表: node_registry = {username: {node_name: {ws, lock, tools, ...}}}
#  节点信息在连接/断开时自动持久化，即使服务器重启也不丢失已知节点。
# ============================================================
NODES_DIR = os.path.join(os.path.dirname(__file__) or ".", "data", "nodes")
os.makedirs(NODES_DIR, exist_ok=True)
node_registry: dict[str, dict[str, dict]] = {}  # 仅在线节点


def _get_nodes_path(username: str) -> str:
    safe = username.replace("/", "_").replace("\\", "_")
    return os.path.join(NODES_DIR, f"{safe}.json")


def _load_nodes(username: str) -> dict:
    """加载用户的所有已知节点（含在线/离线状态）。"""
    path = _get_nodes_path(username)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_nodes(username: str, data: dict):
    """保存用户的节点信息到磁盘。"""
    path = _get_nodes_path(username)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _persist_node_online(username: str, node_name: str, tools: list,
                          work_root: str, interactive: bool):
    """节点上线时持久化其信息。"""
    data = _load_nodes(username)
    tool_names = [t["function"]["name"] for t in tools]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data[node_name] = {
        "tools": tools,
        "tool_names": tool_names,
        "online": True,
        "work_root": work_root,
        "interactive": interactive,
        "first_seen": data.get(node_name, {}).get("first_seen", now_str),
        "last_seen": now_str,
    }
    _save_nodes(username, data)


def _persist_node_offline(username: str, node_name: str):
    """节点断开时标记为离线。"""
    data = _load_nodes(username)
    if node_name in data:
        data[node_name]["online"] = False
        data[node_name]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_nodes(username, data)


def _get_node_context(username: str) -> str:
    """
    生成 AI 系统提示的节点上下文。
    告诉 AI 有哪些在线节点及可用工具，以及如何调用。
    """
    nodes = node_registry.get(username, {})
    if not nodes:
        return ""

    lines = [
        "",
        "=== 已连接的客户端节点 ===",
        "你可以调用以下客户端本地工具（格式: 节点名__工具名），在本地设备上执行操作：",
    ]
    for node_name, node_data in nodes.items():
        tool_names = [t["function"]["name"] for t in node_data.get("tools", [])]
        wroot = node_data.get("work_root", "")
        inter = node_data.get("interactive", True)
        wroot_str = f" · 工作目录:{wroot}" if wroot else ""
        mode_str = " · 交互模式(需确认)" if inter else " · 自动执行"
        lines.append(f"\n  [{node_name}]{wroot_str}{mode_str}")
        for tn in tool_names:
            lines.append(f"    • {node_name}__{tn}")
    lines.append("")
    return "\n".join(lines)


def _get_client_tool_schemas(username: str) -> list:
    """
    获取某用户所有已连接节点的工具 JSON Schema。
    每个工具名加上节点前缀：<节点名>__<工具名>（双下划线，
    因为 OpenAI API 工具名只允许 [a-zA-Z0-9_-]+，不能含点号）。
    """
    schemas = []
    for node_name, node_data in node_registry.get(username, {}).items():
        for tool in node_data.get("tools", []):
            prefixed = copy.deepcopy(tool)
            original_name = prefixed["function"]["name"]
            prefixed["function"]["name"] = f"{node_name}__{original_name}"
            old_desc = prefixed["function"].get("description", "")
            prefixed["function"]["description"] = (
                f"[节点:{node_name}] {old_desc}"
            )
            schemas.append(prefixed)
    return schemas


async def _call_node_tool(username: str, node_name: str, tool_name: str,
                           args: dict, timeout: int = 120) -> str:
    """通过 WebSocket 调用客户端节点的工具并等待结果。使用 asyncio.Lock 防止并发 recv。"""
    node_data = node_registry.get(username, {}).get(node_name)
    if not node_data:
        return f"[ERROR] 节点 '{node_name}' 不在线，请在客户端电脑上运行 client.py 连接"

    ws = node_data["ws"]
    lock = node_data["lock"]  # 每个连接一个锁，保证串行读写
    call_id = str(uuid.uuid4())

    try:
        async with lock:
            await ws.send_json({
                "type": "tool_call",
                "call_id": call_id,
                "tool": tool_name,
                "args": args,
            })
            response = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
        if response.get("type") == "tool_result" and response.get("call_id") == call_id:
            return response.get("result", "")
        return f"[ERROR] 节点返回了意外的响应"
    except asyncio.TimeoutError:
        return f"[ERROR] 节点 '{node_name}' 执行超时 ({timeout}s)"
    except WebSocketDisconnect:
        _cleanup_node(ws)
        return f"[ERROR] 节点 '{node_name}' 连接已断开，请在客户端重新连接"
    except Exception as e:
        return f"[ERROR] 与节点 '{node_name}' 通信失败: {e}"


def _cleanup_node(websocket):
    """清理断开连接的节点，持久化离线状态。"""
    for username in list(node_registry.keys()):
        for node_name, data in list(node_registry.get(username, {}).items()):
            if data.get("ws") is websocket:
                del node_registry[username][node_name]
                _persist_node_offline(username, node_name)
                print(f"[NODE] - {username}@{node_name} 已断开 (剩余节点: {list(node_registry.get(username, {}).keys())})")
                if not node_registry[username]:
                    del node_registry[username]
                return

# 云端模式下禁用的工具模块
CLOUD_BLOCKED_TOOLS = set()

# 保存会话的目录（内存会话临时目录）
SESSIONS_DIR = os.path.join(os.path.dirname(__file__) or ".", "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

# 会话持久化目录（按用户存储到磁盘，支持多端同步）
SESSIONS_DATA_DIR = os.path.join(os.path.dirname(__file__) or ".", "data", "sessions")
os.makedirs(SESSIONS_DATA_DIR, exist_ok=True)


def _get_user_sessions_path(username: str) -> str:
    """获取用户会话 JSON 文件路径"""
    safe_name = username.replace("/", "_").replace("\\", "_")
    return os.path.join(SESSIONS_DATA_DIR, f"{safe_name}.json")


def _load_user_sessions_from_disk(username: str) -> dict:
    """从磁盘加载用户的所有会话"""
    path = _get_user_sessions_path(username)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_user_sessions_to_disk(username: str, data: dict):
    """保存用户的所有会话到磁盘"""
    path = _get_user_sessions_path(username)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _persist_session(session: "Session"):
    """持久化单个会话到磁盘"""
    all_sessions = _load_user_sessions_from_disk(session.user_id)
    all_sessions[session.session_id] = session.to_dict()
    _save_user_sessions_to_disk(session.user_id, all_sessions)


def _delete_session_from_disk(username: str, session_id: str):
    """从磁盘删除指定会话"""
    all_sessions = _load_user_sessions_from_disk(username)
    if session_id in all_sessions:
        del all_sessions[session_id]
        _save_user_sessions_to_disk(username, all_sessions)

# 注意：不再使用全局 client。每个请求根据用户设置创建。
# 这样做是为了多用户隔离 —— 每人可以用自己的 API Key 和 Base URL。

# ============================================================
#  模块加载器（云端版，自动过滤不安全工具）
# ============================================================
def discover_modules():
    tools_list = []
    func_map = {}

    tools_dir = os.path.join(os.path.dirname(__file__) or ".", "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    if not os.path.isdir(tools_dir):
        print(f"[WARNING] tools directory not found: {tools_dir}")
        return tools_list, func_map

    module_files = [f for f in os.listdir(tools_dir)
                    if f.endswith(".py") and not f.startswith("__")]

    for file in module_files:
        module_name = file[:-3]
        if module_name in CLOUD_BLOCKED_TOOLS:
            print(f"[CLOUD] Skip unsafe tool module: {module_name}")
            continue

        try:
            mod = importlib.import_module(f"tools.{module_name}")
        except Exception as e:
            print(f"[WARNING] Cannot import module {module_name}: {e}")
            continue

        for var_name in dir(mod):
            if var_name.endswith("_tools"):
                val = getattr(mod, var_name)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict) and item.get("type") == "function":
                            tools_list.append(item)
                            func_name = item["function"]["name"]
                            if hasattr(mod, func_name):
                                func_map[func_name] = getattr(mod, func_name)

    return tools_list, func_map

tools, func_map = discover_modules()

# ---- 内置工具：列出已连接的客户端设备 ----
# 注意：此函数由 /chat 工具路由直接调用（不经过 asyncio.to_thread），
# 因此 thread-local 上下文可以正常传递。
def list_connected_devices(_username: str = None) -> str:
    """
    列出当前用户已连接到服务器的客户端设备及其可用工具。
    参数 _username 由 /chat 端点自动注入（不需要 AI 传入）。
    """
    from tools import project_tools as _pt
    username = _username or _pt.get_current_user()
    nodes = node_registry.get(username, {})
    if not nodes:
        return "当前没有设备在线。请在电脑上运行 client.py 连接。\n格式: python client.py --server <服务器地址>"
    lines = [f"已连接的设备 ({len(nodes)} 台):", ""]
    for node_name, nd in nodes.items():
        tool_names = [t["function"]["name"] for t in nd.get("tools", [])]
        lines.append(f"  [{node_name}]")
        lines.append(f"    模式: {'需确认' if nd.get('interactive', True) else '自动执行'}")
        if nd.get("work_root"):
            lines.append(f"    工作目录: {nd['work_root']}")
        lines.append(f"    工具: {', '.join(tool_names)}")
        lines.append("")
    # 追加调用说明
    lines.append("调用格式: <设备名>__<工具名>(参数)")
    lines.append("示例: ACER-BLUE__get_system_info()")
    return "\n".join(lines)

tools.append({
    "type": "function",
    "function": {
        "name": "list_connected_devices",
        "description": "查看当前有哪些客户端电脑连接到了AI大脑。返回在线设备名称、工具列表和调用格式。在操作客户端电脑之前先调用此工具了解设备名。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
})
func_map["list_connected_devices"] = list_connected_devices

print(f"[CLOUD] Loaded {len(tools)} safe tools (含 list_connected_devices)")

# ---- 工具调度器 ----
def execute_tool(name: str, args: dict) -> str:
    if name in func_map:
        return func_map[name](**args)
    return f"Unknown tool: {name}"


# ---- 会话管理 ----
class Session:
    """用户会话，包含独立的消息历史和配置"""
    def __init__(self, user_id: str, session_id: str, name: str = "New Session"):
        self.user_id = user_id
        self.session_id = session_id
        self.name = name
        self.messages = [
            {
                "role": "system",
                "content": (
                    "你是一个智能编程助手，部署在云端，可以调用工具完成任务。\n"
                    "\n"
                    "=== 工具使用规则 ===\n"
                    "1. 不要反复调用同一個失败的命令。如果工具返回错误，先分析原因再尝试不同方法。\n"
                    "2. 修改代码前先 read_file 查看当前内容，不要凭空猜测。\n"
                    "3. 修改代码后用 run_shell 检查语法（如 python -m py_compile xxx.py）。\n"
                    "4. restart_service 只调用一次，不要反复调用。\n"
                    "5. web_search 的 query 参数要简洁明确，不要输入整段话。\n"
                    "6. web_fetch 需要完整 URL（以 http:// 或 https:// 开头）。\n"
                    "7. 文件路径相对于项目根目录，用 list_files 了解结构。\n"
                    "8. 每次对话结束后调用 save_memory 保存摘要。\n"
                    "\n"
                    "=== 多级任务管理 ===\n"
                    "9. 使用 task_add 创建任务时，可通过 parent_task 参数指定父任务，实现多层嵌套（任务A→子任务A-1→孙任务A-1-1）。\n"
                    "10. 用 task_tree 查看项目的完整层级树。\n"
                    "11. 删除父任务会级联删除所有子任务，请先确认。\n"
                    "12. 项目概要面板左侧 activity bar 第二个图标，前端已支持树形折叠展示。\n"
                    "\n"
                    "=== 客户端节点工具（操作本地电脑）===\n"
                    "13. 操作用户本地电脑前，必须先调用 list_connected_devices 查看有哪些设备在线、设备名是什么。\n"
                    "14. 调用格式：<设备名>__<工具名>（双下划线分隔），如 DESKTOP-PC__read_file。\n"
                    "    设备名称由用户登录时设定，每次可能不同，不要猜测名称。\n"
                    "15. 调用客户端工具可能较慢（需要网络往返），耐心等待结果。\n"
                ),
            },
        ]
        self.created_at = __import__("time").time()

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "name": self.name,
            "messages": self.messages,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data):
        s = cls(data["user_id"], data["session_id"], data.get("name", "Unnamed"))
        s.messages = data["messages"]
        s.created_at = data.get("created_at", __import__("time").time())
        return s


# 内存中的会话存储
sessions: dict[str, Session] = {}


def get_or_create_session(user_id: str, session_id: str = None) -> Session:
    if session_id and session_id in sessions:
        return sessions[session_id]
    if session_id:
        disk_sessions = _load_user_sessions_from_disk(user_id)
        if session_id in disk_sessions:
            session = Session.from_dict(disk_sessions[session_id])
            sessions[session_id] = session
            return session
    sid = session_id or str(uuid.uuid4())
    session = Session(user_id, sid)
    sessions[sid] = session
    _persist_session(session)
    return session


@asynccontextmanager
async def lifespan(app: FastAPI):
    task_scheduler.start_scheduler()
    print(f"[START] Scheduler started")
    yield
    task_scheduler.stop_scheduler()
    print(f"[SHUTDOWN] Scheduler stopped")


app = FastAPI(
    title="ZeroAgent Cloud Brain",
    description="Multi-user AI Agent Service based on DeepSeek",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
#  中间件：强制所有响应使用 UTF-8 charset
#  在中国 Windows (GBK 系统编码) 上，浏览器可能错误解码
#  没有 charset 参数的 JSON/HTML 响应。
# ============================================================
@app.middleware("http")
async def enforce_utf8_charset(request, call_next):
    response = await call_next(request)
    ct = response.headers.get("content-type", "")
    if ct and "charset=" not in ct:
        # 对 text/* 和 application/* 附加 charset=utf-8
        if ct.startswith("text/") or ct.startswith("application/"):
            response.headers["content-type"] = f"{ct}; charset=utf-8"
    return response

static_dir = os.path.join(os.path.dirname(__file__) or ".", "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    print(f"[STATIC] Mounted: {static_dir}")
else:
    print(f"[WARNING] Static folder not found: {static_dir}")


class AuthRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    username: str
    tool_calls: Optional[list] = None


class SessionInfo(BaseModel):
    session_id: str
    user_id: str
    created_at: float
    message_count: int


async def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="No auth token provided")
    token = authorization
    if token.startswith("Bearer "):
        token = token[7:]
    result = verify_token(token)
    if not result["valid"]:
        raise HTTPException(status_code=401, detail=result.get("message", "Auth failed"))
    return result["username"]


@app.post("/api/register")
async def api_register(req: AuthRequest):
    result = register(req.username, req.password)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/api/login")
async def api_login(req: AuthRequest):
    result = login(req.username, req.password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["message"])
    return result


@app.post("/api/verify")
async def api_verify(username: str = Depends(get_current_user)):
    return {"valid": True, "username": username}


@app.get("/")
async def root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>ZeroAgent</h1><p>Login page missing.</p>")


@app.get("/api/me")
async def api_me(username: str = Depends(get_current_user)):
    return {"username": username}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, username: str = Depends(get_current_user)):
    """发送消息给 Agent。支持本地工具和远程客户端节点工具。"""
    try:
        user_id = username
        session = get_or_create_session(user_id, req.session_id)

        ptools.set_current_user(user_id)
        from tools import memory_tools as mt
        mt.set_current_user(user_id)
        user_tools.set_current_user(user_id)   # 让 create_tool 知道操作的是哪个用户
        ptools._init_db(user_id)

        user_tools_schemas, user_tools_funcs = user_tools.load_user_tools_for_agent(user_id)
        combined_tools = tools + user_tools_schemas
        combined_func_map = {**func_map, **user_tools_funcs}

        session.messages.append({"role": "user", "content": req.message})

        user_msg_count = sum(1 for m in session.messages if m["role"] == "user")
        if user_msg_count == 1:
            first_msg = req.message.strip()[:30]
            session.name = first_msg + ("..." if len(req.message.strip()) > 30 else "")

        _persist_session(session)

        max_rounds = 200
        round_count = 0
        all_tool_call_records = []

        while round_count < max_rounds:
            round_count += 1

            # 每次循环前获取用户专属 client（支持热切换 API Key）
            user_client = _get_user_client(user_id)
            user_model = _get_user_model(user_id)

            # 每轮刷新：客户端节点工具 schema（支持热插拔）
            client_schemas = _get_client_tool_schemas(user_id)
            all_tools = combined_tools + client_schemas

            # Debug: 打印每轮传给 AI 的工具列表
            client_tool_names = [t["function"]["name"] for t in client_schemas]
            print(f"[AI ROUND {round_count}] server_tools:{len(combined_tools)} "
                  f"node_tools:{len(client_schemas)}"
                  + (f" ({', '.join(client_tool_names)})" if client_schemas else " (无客户端节点在线)"))
            call_messages = session.messages

            # 调用 AI（OpenAI SDK 是同步阻塞的，放入线程池避免卡住事件循环）
            response = await asyncio.to_thread(
                user_client.chat.completions.create,
                model=user_model,
                messages=call_messages,
                tools=all_tools,
                tool_choice="auto",
                extra_body={"thinking": {"type": "disabled"}},
            )

            msg = response.choices[0].message

            if not msg.tool_calls:
                session.messages.append({"role": "assistant", "content": msg.content})
                summary = f"User({user_id}) asked: {req.message}\nAI answered: {msg.content}"
                save_func = combined_func_map.get("save_memory")
                if save_func:
                    save_func(summary, tags="cloud,chat")
                _persist_session(session)
                return ChatResponse(
                    reply=msg.content or "",
                    session_id=session.session_id,
                    username=user_id,
                    tool_calls=all_tool_call_records if all_tool_call_records else None,
                )

            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                args_raw = tool_call.function.arguments
                args = {}
                if args_raw and args_raw.strip():
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {}

                ptools.set_current_user(user_id)
                from tools import memory_tools as mt2
                mt2.set_current_user(user_id)
                user_tools.set_current_user(user_id)

                print(f"[TOOL CALL] {name}({json.dumps(args, ensure_ascii=False)})")

                # ---- 工具路由：节点工具 vs 本地工具 ----
                if "__" in name and name.split("__", 1)[0] in node_registry.get(user_id, {}):
                    # 客户端节点工具（格式: <节点名>__<工具名>，双下划线分隔）
                    node_name, original_name = name.split("__", 1)
                    result = await _call_node_tool(user_id, node_name, original_name, args)
                elif name == "list_connected_devices":
                    # 内置工具：纯内存查表，同步执行即可（不通过 asyncio.to_thread，
                    # 避免线程切换导致 threading.local 丢失）
                    try:
                        result = list_connected_devices(_username=user_id)
                    except Exception as e:
                        result = f"[TOOL ERROR] {type(e).__name__}: {e}"
                else:
                    # 本地工具
                    tool_func = combined_func_map.get(name)
                    if tool_func:
                        try:
                            result = await asyncio.to_thread(tool_func, **args)
                        except Exception as e:
                            error_detail = traceback.format_exc()
                            print(f"[TOOL ERROR] {name}: {error_detail[:300]}")
                            result = (
                                f"[TOOL ERROR] {type(e).__name__}: {e}\n\n"
                                f"Hint: The tool call failed. Analyze the error and try a different approach. "
                                f"Check your parameter types and values — are they correct for this function?"
                            )
                    else:
                        result = f"[ERROR] Unknown tool: {name}. Available: {', '.join(sorted(combined_func_map.keys()))}"
                print(f"[TOOL RESULT] {str(result)[:200]}")

                tool_record = {
                    "name": name,
                    "arguments": args,
                    "result_preview": str(result)[:300],
                }
                all_tool_call_records.append(tool_record)

                session.messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }],
                })
                session.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result),
                })

            # 热重载：如果 create_tool / delete_tool 刚被执行，立即刷新工具列表
            if user_tools.is_user_tools_dirty(user_id):
                user_tools.clear_user_tools_dirty(user_id)
                new_schemas, new_funcs = user_tools.load_user_tools_for_agent(user_id)
                combined_tools = tools + new_schemas
                combined_func_map = {**func_map, **new_funcs}

        _persist_session(session)
        return ChatResponse(
            reply=(
                f"[WARN] Reached max tool call rounds ({max_rounds}). "
                "The task may be too complex. Try breaking it into smaller steps, "
                "or check if there is a loop calling the same failing tool repeatedly."
            ),
            session_id=session.session_id,
            username=user_id,
            tool_calls=all_tool_call_records if all_tool_call_records else None,
        )

    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)}"
        print(f"[ERROR] chat: {err_msg}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=err_msg)


@app.get("/projects")
async def list_projects(username: str = Depends(get_current_user)):
    """返回所有项目及其任务树（多级层级结构）"""
    try:
        ptools.set_current_user(username)
        ptools._init_db(username)
        conn = ptools._get_conn(username)
        proj_rows = conn.execute(
            "SELECT * FROM projects ORDER BY CASE status WHEN '推进中' THEN 1 WHEN '待启动' THEN 2 WHEN '已完成' THEN 3 ELSE 4 END, updated_at DESC"
        ).fetchall()
        projects = []
        for p in proj_rows:
            task_rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY priority DESC, created_at",
                (p["id"],),
            ).fetchall()
            task_tree = ptools._build_task_tree(task_rows) if task_rows else []
            total_tasks, done_tasks, active_tasks, _ = ptools._count_task_status(task_tree) if task_tree else (0, 0, 0, 0)

            projects.append({
                "name": p["name"],
                "description": p["description"] or "",
                "leader": p["leader"] or "",
                "status": p["status"],
                "progress": round(p["progress"] or 0),
                "deadline": p["deadline"] or "",
                "goal": p["goal"] or "",
                "tasks": task_tree,
                "task_total": total_tasks,
                "task_done": done_tasks,
                "task_active": active_tasks,
            })
        conn.close()
        return {"projects": projects, "total": len(projects)}
    except Exception as e:
        return {"projects": [], "total": 0, "error": str(e)}


@app.get("/api/sessions")
async def api_list_sessions(username: str = Depends(get_current_user)):
    disk_sessions = _load_user_sessions_from_disk(username)
    result = []
    for sid, data in disk_sessions.items():
        display_msgs = [
            m for m in data.get("messages", [])
            if m["role"] in ("user", "assistant") and m.get("content")
        ]
        last_msg = display_msgs[-1]["content"] if display_msgs else ""
        if len(last_msg) > 50:
            last_msg = last_msg[:50] + "..."
        result.append({
            "session_id": sid,
            "name": data.get("name", "Unnamed"),
            "created_at": data.get("created_at", 0),
            "message_count": sum(1 for m in data.get("messages", []) if m["role"] == "user"),
            "last_message": last_msg,
        })
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return {"sessions": result}


@app.post("/api/sessions")
async def api_create_session(req: dict = None, username: str = Depends(get_current_user)):
    sid = str(uuid.uuid4())
    name = (req or {}).get("name", "New Session")
    session = Session(username, sid, name=name)
    sessions[sid] = session
    _persist_session(session)
    return {"session_id": sid, "name": name, "created_at": session.created_at}


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str, username: str = Depends(get_current_user)):
    session = sessions.get(session_id)
    if not session:
        disk_sessions = _load_user_sessions_from_disk(username)
        if session_id in disk_sessions:
            session = Session.from_dict(disk_sessions[session_id])
            sessions[session_id] = session
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != username:
        raise HTTPException(status_code=403, detail="Access denied")

    chat_messages = []
    i = 0
    while i < len(session.messages):
        m = session.messages[i]
        if m["role"] == "user":
            chat_messages.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant" and m.get("content"):
            chat_messages.append({"role": "assistant", "content": m["content"]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            tool_exec = {"role": "tool_exec", "calls": []}
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "?")
                tool_args_str = fn.get("arguments", "{}")
                result_content = ""
                if i + 1 < len(session.messages) and session.messages[i+1]["role"] == "tool":
                    result_content = session.messages[i+1].get("content", "")[:500]
                    i += 1
                tool_exec["calls"].append({
                    "name": tool_name,
                    "arguments": tool_args_str,
                    "result": result_content,
                })
            if tool_exec["calls"]:
                chat_messages.append(tool_exec)
        i += 1

    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "name": session.name,
        "messages": chat_messages,
        "created_at": session.created_at,
    }


@app.put("/api/sessions/{session_id}")
async def api_update_session(session_id: str, req: dict, username: str = Depends(get_current_user)):
    session = sessions.get(session_id)
    if not session:
        disk_sessions = _load_user_sessions_from_disk(username)
        if session_id in disk_sessions:
            session = Session.from_dict(disk_sessions[session_id])
            sessions[session_id] = session
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != username:
        raise HTTPException(status_code=403, detail="Access denied")
    if "name" in req:
        session.name = req["name"].strip() or session.name
    _persist_session(session)
    return {"session_id": session.session_id, "name": session.name, "created_at": session.created_at}


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str, username: str = Depends(get_current_user)):
    if session_id in sessions:
        if sessions[session_id].user_id != username:
            raise HTTPException(status_code=403, detail="Access denied")
        del sessions[session_id]
    disk_sessions = _load_user_sessions_from_disk(username)
    if session_id not in disk_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    _delete_session_from_disk(username, session_id)
    return {"status": "ok", "message": f"Session {session_id} deleted"}


@app.get("/health")
async def health():
    return {"status": "healthy", "sessions": len(sessions), "tools": len(tools)}


# ============================================================
#  User tools API
# ============================================================

@app.get("/api/user-tools")
async def api_list_user_tools(username: str = Depends(get_current_user)):
    tools_list = user_tools.list_user_tools(username)
    return {"tools": tools_list}


@app.post("/api/user-tools")
async def api_add_user_tool(req: dict, username: str = Depends(get_current_user)):
    tool_name = req.get("name", "").strip()
    code = req.get("code", "")
    description = req.get("description", "")
    result = user_tools.add_user_tool(username, tool_name, code, description)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.delete("/api/user-tools/{tool_name}")
async def api_remove_user_tool(tool_name: str, username: str = Depends(get_current_user)):
    result = user_tools.remove_user_tool(username, tool_name)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@app.get("/api/user-tools/{tool_name}/code")
async def api_get_user_tool_code(tool_name: str, username: str = Depends(get_current_user)):
    code = user_tools.get_user_tool_code(username, tool_name)
    if not code:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"name": tool_name, "code": code}


# ============================================================
#  User file storage API
# ============================================================

@app.get("/api/files")
async def api_list_files(path: str = "", username: str = Depends(get_current_user)):
    items = file_storage.list_user_files(username, path)
    if items and isinstance(items[0], dict) and "error" in items[0]:
        raise HTTPException(status_code=403, detail=items[0]["error"])
    usage = file_storage.get_user_storage_usage(username)
    return {"items": items, "usage": usage, "path": path}


@app.get("/api/files/usage")
async def api_storage_usage(username: str = Depends(get_current_user)):
    return file_storage.get_user_storage_usage(username)


@app.post("/api/files/upload")
async def api_upload_file(file: UploadFile = File(...), path: str = Form(""), username: str = Depends(get_current_user)):
    content = await file.read()
    file_path = os.path.join(path, file.filename) if path else file.filename
    result = file_storage.save_user_file(username, file_path, binary_content=content)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/api/files/save")
async def api_save_file(req: dict, username: str = Depends(get_current_user)):
    file_path = req.get("path", "").strip()
    content = req.get("content", "")
    result = file_storage.save_user_file(username, file_path, content=content)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.get("/api/files/read")
async def api_read_file(path: str = "", username: str = Depends(get_current_user)):
    result = file_storage.read_user_file(username, path)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@app.delete("/api/files/delete")
async def api_delete_file(path: str = "", username: str = Depends(get_current_user)):
    result = file_storage.delete_user_file(username, path)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


# ============================================================
#  Scheduler API
# ============================================================

@app.get("/api/scheduler/tasks")
async def api_list_scheduled_tasks(username: str = Depends(get_current_user)):
    tasks = task_scheduler.list_scheduled_tasks(username)
    return {"tasks": tasks}


@app.post("/api/scheduler/tasks")
async def api_add_scheduled_task(req: dict, username: str = Depends(get_current_user)):
    result = task_scheduler.add_scheduled_task(
        username=username,
        name=req.get("name", ""),
        cron_expr=req.get("cron_expr", ""),
        action_type=req.get("action_type", ""),
        action_params=req.get("action_params", {}),
        description=req.get("description", ""),
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.delete("/api/scheduler/tasks/{task_name}")
async def api_remove_scheduled_task(task_name: str, username: str = Depends(get_current_user)):
    result = task_scheduler.remove_scheduled_task(username, task_name)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


# ============================================================
#  User API settings（每个用户可配置自己的 API Key / Base URL / Model）
# ============================================================

@app.get("/api/settings")
async def api_get_settings(username: str = Depends(get_current_user)):
    """获取当前用户的 API 配置（api_key 仅返回后4位掩码）"""
    settings = _load_user_settings(username)
    masked_key = ""
    raw_key = settings.get("api_key", "")
    if raw_key and len(raw_key) > 4:
        masked_key = "*" * (len(raw_key) - 4) + raw_key[-4:]
    return {
        "api_key": masked_key,
        "base_url": settings.get("base_url") or DEFAULT_BASE_URL,
        "model": settings.get("model") or DEFAULT_MODEL,
        "has_api_key": bool(raw_key),
    }


class SettingsRequest(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


@app.put("/api/settings")
async def api_save_settings(req: SettingsRequest, username: str = Depends(get_current_user)):
    """保存当前用户的 API 配置"""
    settings = _load_user_settings(username)
    if req.api_key is not None:
        settings["api_key"] = req.api_key
    if req.base_url is not None:
        settings["base_url"] = req.base_url
    if req.model is not None:
        settings["model"] = req.model
    _save_user_settings(username, settings)

    # 验证配置是否可用
    try:
        test_client = OpenAI(
            api_key=settings.get("api_key") or DEFAULT_API_KEY,
            base_url=settings.get("base_url") or DEFAULT_BASE_URL,
            timeout=5.0,
        )
        test_client.models.list()
    except Exception as e:
        err_msg = str(e)
        # 不阻断保存，只是提醒用户
        return {
            "status": "saved_with_warning",
            "message": f"设置已保存，但测试连接失败: {err_msg}\n请检查 API Key 和 Base URL 是否正确。",
        }

    return {"status": "ok", "message": "设置已保存，连接验证通过 ✓"}


# ============================================================
#  Client Node API — 查询已连接/历史节点
# ============================================================

@app.get("/api/nodes")
async def api_list_nodes(username: str = Depends(get_current_user)):
    """返回当前用户的所有已知节点（在线/离线）及其工具。"""
    stored = _load_nodes(username)
    # 合并内存中的在线状态（优先级高于磁盘）
    online_nodes = node_registry.get(username, {})
    result = []
    for node_name, node_data in stored.items():
        if node_name in online_nodes:
            node_data["online"] = True
            node_data["interactive"] = online_nodes[node_name].get("interactive", True)
            node_data["work_root"] = online_nodes[node_name].get("work_root", "")
        else:
            node_data["online"] = False
        result.append({
            "name": node_name,
            "online": node_data["online"],
            "tool_names": node_data.get("tool_names", []),
            "interactive": node_data.get("interactive", True),
            "work_root": node_data.get("work_root", ""),
            "last_seen": node_data.get("last_seen", ""),
        })
    return {"nodes": result, "total": len(result)}


# ============================================================
#  Client Node WebSocket endpoint
#  客户端节点通过此端点连接服务器，注册本地工具供 AI 调用
# ============================================================

@app.websocket("/ws")
async def websocket_node_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_user = None
    connected_node = None

    try:
        # --- 握手阶段 1: 认证 ---
        msg = await asyncio.wait_for(websocket.receive_json(), timeout=15)
        if msg.get("type") != "auth":
            await websocket.send_json({"type": "error", "message": "Expected auth message"})
            return

        token = msg.get("token", "")
        if token.startswith("Bearer "):
            token = token[7:]
        result = verify_token(token)
        if not result["valid"]:
            await websocket.send_json({
                "type": "auth_failed",
                "message": result.get("message", "Invalid token"),
            })
            return

        connected_user = result["username"]
        await websocket.send_json({"type": "auth_ok", "username": connected_user})

        # --- 握手阶段 2: 注册工具 ---
        msg = await asyncio.wait_for(websocket.receive_json(), timeout=15)
        if msg.get("type") != "tools_register":
            await websocket.send_json({"type": "error", "message": "Expected tools_register"})
            return

        node_name = (msg.get("node_name") or "unknown").strip()
        connected_node = node_name
        tools_list = msg.get("tools", [])
        work_root = msg.get("work_root", "")
        interactive = msg.get("interactive", True)

        # 注册节点（如果同名节点已存在，先关闭旧连接）
        if connected_user not in node_registry:
            node_registry[connected_user] = {}
        old = node_registry[connected_user].get(node_name)
        if old:
            try:
                await old["ws"].close()
            except Exception:
                pass

        node_registry[connected_user][node_name] = {
            "ws": websocket,
            "lock": asyncio.Lock(),
            "tools": tools_list,
            "work_root": work_root,
            "interactive": interactive,
            "connected_at": time.time(),
        }

        # 持久化节点信息到磁盘
        _persist_node_online(connected_user, node_name, tools_list,
                             work_root, interactive)

        tool_names = [t["function"]["name"] for t in tools_list]
        print(f"[NODE] + {connected_user}@{node_name} "
              f"({len(tool_names)} tools: {', '.join(tool_names)})")
        await websocket.send_json({
            "type": "tools_registered",
            "node_name": node_name,
            "tool_count": len(tool_names),
        })

        # --- 保持连接存活（通过 lock 保护 recv，防止与 _call_node_tool 并发）---
        while True:
            lock = node_registry.get(connected_user, {}).get(node_name, {}).get("lock")
            if not lock:
                break  # 节点已被清理
            async with lock:
                try:
                    msg = await asyncio.wait_for(websocket.receive_json(), timeout=30)
                    if msg.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except asyncio.TimeoutError:
                    pass  # 心跳超时，继续循环

    except asyncio.TimeoutError:
        print(f"[NODE] Handshake timeout")
    except WebSocketDisconnect:
        if connected_user and connected_node:
            print(f"[NODE] - {connected_user}@{connected_node} 连接断开")
    except Exception as e:
        print(f"[NODE] WebSocket 错误 ({connected_user}@{connected_node}): {e}")

    _cleanup_node(websocket)


# ============================================================
#  Entry point
# ============================================================
if __name__ == "__main__":
    local_url = f"http://127.0.0.1:{PORT}"
    print("=" * 55)
    print("         ZeroAgent Cloud Brain")
    print("=" * 55)
    print(f"  Web UI:   {local_url}")
    print(f"  API Docs: {local_url}/docs")
    print(f"  Model:    {DEFAULT_MODEL}")
    print(f"  API URL:  {DEFAULT_BASE_URL}")
    print(f"  API Key:  {'✔ 全局默认已配置' if DEFAULT_API_KEY else '⚠ 需用户在设置中配置'}")
    print(f"  Tools:    {len(tools)} loaded")
    print(f"  Blocked:  {', '.join(CLOUD_BLOCKED_TOOLS) if CLOUD_BLOCKED_TOOLS else '(none)'}")
    print("=" * 55)
    print(f"  Open {local_url} in browser to start")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
