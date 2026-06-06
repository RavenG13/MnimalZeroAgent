"""
============================================================
  user_tools.py - 用户自定义工具管理器
  允许用户上传 Python 脚本作为自定义工具，AI agent 可调用
  每个用户隔离存储
============================================================
"""
import os
import sys
import json
import threading
import importlib.util
import traceback

from tools import project_tools as ptools

# 用户自定义工具存储目录
TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "user_tools")
os.makedirs(TOOLS_DIR, exist_ok=True)

# 线程局部变量：存储当前用户的 username（由 server 层在每个请求中设置）
# 和 project_tools、memory_tools 使用同一套机制，确保多用户并发不串
_thread_local = threading.local()

# 缓存：{username: (tools_schema_list, func_map)}
_user_tools_cache: dict[str, tuple] = {}


def set_current_user(username: str):
    """设置当前请求的用户名（由 server 在每个请求 + 每次工具调用前调用）"""
    _thread_local.username = username


def get_current_user() -> str:
    """获取当前用户名（从线程局部变量读取）"""
    return getattr(_thread_local, "username", None) or "default"


def _get_user_tools_dir(username: str) -> str:
    """获取某个用户的工具目录"""
    d = os.path.join(TOOLS_DIR, username)
    os.makedirs(d, exist_ok=True)
    return d


def _get_user_tools_manifest(username: str) -> list[dict]:
    """获取某个用户的所有自定义工具清单"""
    manifest_file = os.path.join(_get_user_tools_dir(username), "manifest.json")
    if os.path.exists(manifest_file):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_user_tools_manifest(username: str, tools: list[dict]):
    """保存用户自定义工具清单"""
    manifest_file = os.path.join(_get_user_tools_dir(username), "manifest.json")
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(tools, f, ensure_ascii=False, indent=2)


def add_user_tool(username: str, tool_name: str, code: str, description: str = "") -> dict:
    """
    用户注册一个自定义工具。
    code 是一个 Python 函数定义，函数名必须与 tool_name 一致。
    返回 {"success": True/False, "message": "..."}
    """
    if not tool_name.isidentifier():
        return {"success": False, "message": "工具名必须是有效的 Python 标识符"}
    
    if not code.strip():
        return {"success": False, "message": "代码不能为空"}
    
    # 验证代码：尝试编译
    try:
        compiled = compile(code, f"<user_tool_{tool_name}>", "exec")
    except SyntaxError as e:
        return {"success": False, "message": f"代码语法错误: {e}"}
    
    # 验证函数名存在
    namespace = {}
    try:
        exec(compiled, namespace)
    except Exception as e:
        return {"success": False, "message": f"代码执行错误: {e}"}
    
    if tool_name not in namespace:
        return {"success": False, "message": f"代码中未定义函数 '{tool_name}'"}
    
    if not callable(namespace[tool_name]):
        return {"success": False, "message": f"'{tool_name}' 不是可调用的函数"}
    
    # 保存代码文件
    user_dir = _get_user_tools_dir(username)
    code_file = os.path.join(user_dir, f"{tool_name}.py")
    with open(code_file, "w", encoding="utf-8") as f:
        f.write(code)
    
    # 更新 manifest
    tools = _get_user_tools_manifest(username)
    # 如果已存在同名工具，替换
    tools = [t for t in tools if t["name"] != tool_name]
    tools.append({
        "name": tool_name,
        "description": description or f"用户自定义工具: {tool_name}",
        "file": f"{tool_name}.py",
    })
    _save_user_tools_manifest(username, tools)
    
    # 清除缓存
    _user_tools_cache.pop(username, None)
    
    return {"success": True, "message": f"自定义工具 '{tool_name}' 已添加"}


def remove_user_tool(username: str, tool_name: str) -> dict:
    """删除用户的自定义工具"""
    user_dir = _get_user_tools_dir(username)
    code_file = os.path.join(user_dir, f"{tool_name}.py")
    if os.path.exists(code_file):
        os.remove(code_file)
    
    tools = _get_user_tools_manifest(username)
    tools = [t for t in tools if t["name"] != tool_name]
    _save_user_tools_manifest(username, tools)
    
    _user_tools_cache.pop(username, None)
    
    return {"success": True, "message": f"自定义工具 '{tool_name}' 已删除"}


def list_user_tools(username: str) -> list[dict]:
    """列出用户的所有自定义工具"""
    return _get_user_tools_manifest(username)


def get_user_tool_code(username: str, tool_name: str) -> str:
    """获取用户自定义工具的代码"""
    user_dir = _get_user_tools_dir(username)
    code_file = os.path.join(user_dir, f"{tool_name}.py")
    if os.path.exists(code_file):
        with open(code_file, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def load_user_tools_for_agent(username: str) -> tuple[list[dict], dict]:
    """
    加载某个用户的自定义工具，返回 (openai_tools_schema_list, func_map)
    供 server 动态注入到 AI 的工具列表中
    """
    if username in _user_tools_cache:
        return _user_tools_cache[username]
    
    tools_schema = []
    func_map = {}
    
    user_dir = _get_user_tools_dir(username)
    manifest = _get_user_tools_manifest(username)
    
    for tool_info in manifest:
        tool_name = tool_info["name"]
        code_file = os.path.join(user_dir, f"{tool_name}.py")
        if not os.path.exists(code_file):
            continue
        
        # 动态导入
        spec = importlib.util.spec_from_file_location(f"user_tool_{username}_{tool_name}", code_file)
        if spec is None or spec.loader is None:
            continue
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"[用户工具] 加载失败 {username}/{tool_name}: {e}")
            continue
        
        func = getattr(mod, tool_name, None)
        if func is None:
            continue
        
        # 尝试获取函数的类型注解来生成 schema
        import inspect
        sig = inspect.signature(func)
        properties = {}
        required = []
        
        for param_name, param in sig.parameters.items():
            param_type = "string"
            if param.annotation != inspect.Parameter.empty:
                if param.annotation == int:
                    param_type = "integer"
                elif param.annotation == float:
                    param_type = "number"
                elif param.annotation == bool:
                    param_type = "boolean"
                elif param.annotation == list or param.annotation == list:
                    param_type = "array"
                elif param.annotation == dict:
                    param_type = "object"
            
            properties[param_name] = {
                "type": param_type,
                "description": f"参数 {param_name}",
            }
            
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
        
        schema = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_info.get("description", f"用户自定义工具: {tool_name}"),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
        
        tools_schema.append(schema)
        func_map[tool_name] = func
    
    result = (tools_schema, func_map)
    _user_tools_cache[username] = result
    return result


def clear_user_tools_cache(username: str = None):
    """清除缓存"""
    if username:
        _user_tools_cache.pop(username, None)
    else:
        _user_tools_cache.clear()


# ============================================================
#  Agent 可调用的工具管理工具（让 AI 能给自己写新工具）
# ============================================================

custom_tool_tools = [
    {
        "type": "function",
        "function": {
            "name": "create_tool",
            "description": (
                "为自己创建一个新的工具函数，立即可用。\n"
                "你只需提供 Python 函数代码，系统会自动加载它。\n"
                "⚠️ 重要规则：\n"
                " - 函数名 = 工具名，必须是一个有效的 Python 标识符\n"
                " - 函数必须使用类型注解声明参数类型（str, int, float, bool, list, dict）\n"
                " - 返回值必须是字符串（API 要求）\n"
                " - 创建后立即生效，你可以在同一轮对话中调用它\n"
                "示例代码：\n"
                "def get_weather(city: str) -> str:\n"
                '    return f"{city}今日晴天，25°C"\n'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "工具名称，即函数名",
                    },
                    "code": {
                        "type": "string",
                        "description": (
                            "完整的 Python 函数定义代码。\n"
                            "必须定义返回值类型为 str 的函数。\n"
                            "可以 import 需要的标准库模块。\n"
                            "示例: def calc(a: int, b: int) -> str: return str(a+b)\n"
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "工具的功能描述，告诉 AI 何时调用此工具",
                    },
                },
                "required": ["tool_name", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_tool",
            "description": "删除之前创建的自定义工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "要删除的工具名称",
                    },
                },
                "required": ["tool_name"],
            },
        },
    },
]


def create_tool(tool_name: str, code: str, description: str = "") -> str:
    """Agent 调用：为自己创建一个新工具"""
    username = get_current_user()
    result = add_user_tool(
        username=username,
        tool_name=tool_name,
        code=code,
        description=description,
    )
    clear_user_tools_cache(username)
    _mark_user_tools_dirty(username)
    if result["success"]:
        return f"[OK] 工具 '{tool_name}' 已创建并可用。你现在可以调用它了。"
    return f"[FAIL] 创建工具失败: {result['message']}"


def delete_tool(tool_name: str) -> str:
    """Agent 调用：删除一个自定义工具"""
    username = get_current_user()
    result = remove_user_tool(username, tool_name)
    clear_user_tools_cache(username)
    _mark_user_tools_dirty(username)
    if result["success"]:
        return f"[OK] 工具 '{tool_name}' 已删除。"
    return f"[FAIL] 删除工具失败: {result['message']}"


# dirty 标记：server 用来判断是否需要在工具调用后重载
_dirty_users: set[str] = set()


def _mark_user_tools_dirty(username: str):
    _dirty_users.add(username)


def is_user_tools_dirty(username: str) -> bool:
    return username in _dirty_users


def clear_user_tools_dirty(username: str):
    _dirty_users.discard(username)
