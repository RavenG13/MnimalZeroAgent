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
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, Form
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
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-c50656ead7034c9f9f72fa94323b0d46")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("SERVER_PORT", "8010"))

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

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

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
print(f"[CLOUD] Loaded {len(tools)} safe tools")


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
def chat(req: ChatRequest, username: str = Depends(get_current_user)):
    """同步端点：发消息给 Agent。所有阻塞调用（subprocess/requests/OpenAI SDK）均在线程池执行，不会冻结事件循环。"""
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

            response = client.chat.completions.create(
                model=MODEL,
                messages=session.messages,
                tools=combined_tools,
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
                tool_func = combined_func_map.get(name)
                if tool_func:
                    try:
                        result = tool_func(**args)
                    except Exception as e:
                        # 像 subprocess 一样：工具执行失败也返回错误信息给 AI，
                        # AI 可以根据错误调整策略，而不是整个对话崩溃
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
        import traceback
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
#  Entry point
# ============================================================
if __name__ == "__main__":
    local_url = f"http://127.0.0.1:{PORT}"
    print("=" * 55)
    print("         ZeroAgent Cloud Brain")
    print("=" * 55)
    print(f"  Web UI:   {local_url}")
    print(f"  API Docs: {local_url}/docs")
    print(f"  AI Model: {MODEL}")
    print(f"  Tools:    {len(tools)} loaded")
    print(f"  Blocked:  {', '.join(CLOUD_BLOCKED_TOOLS) if CLOUD_BLOCKED_TOOLS else '(none)'}")
    print("=" * 55)
    print(f"  Open {local_url} in browser to start")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
