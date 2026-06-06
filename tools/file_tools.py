"""
============================================================
  file_tools - 文件读写工具（用户隔离版）
  支持 Agent 读取、编辑项目源代码，实现自我修改能力
  所有路径限定在项目根目录内，防止越权访问
  禁止读取 data/ 下其他用户的数据（仅可访问公共 tools/ 目录）
============================================================
"""
import os

# 项目根目录（cloud-agent 所在目录）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 禁止访问的敏感目录/文件模式（用户数据隔离）
BLOCKED_DIRECTORIES = [
    os.path.abspath(os.path.join(PROJECT_ROOT, "data", "user_files")),
    os.path.abspath(os.path.join(PROJECT_ROOT, "data", "user_tools")),
    os.path.abspath(os.path.join(PROJECT_ROOT, "data", "memory")),
    os.path.abspath(os.path.join(PROJECT_ROOT, "data", "sessions")),
    os.path.abspath(os.path.join(PROJECT_ROOT, "data", "users.json")),
    os.path.abspath(os.path.join(PROJECT_ROOT, "memory.db")),
]

# 但允许访问项目公共目录
ALLOWED_PUBLIC_DIRECTORIES = [
    os.path.abspath(os.path.join(PROJECT_ROOT, "tools")),      # 公共工具源码
    os.path.abspath(os.path.join(PROJECT_ROOT, "static")),     # 静态文件
    os.path.abspath(PROJECT_ROOT),                             # 根目录文件（如 server.py）
]


def _safe_path(file_path: str) -> str:
    """将相对路径解析为绝对路径，并检查是否在项目根目录内，且不在禁止访问的目录中"""
    # 拒绝包含 .. 的路径，防止目录遍历攻击
    if ".." in file_path.split("/") or ".." in file_path.split("\\"):
        raise ValueError(f"禁止使用 '..' 访问上级目录: {file_path}")
    abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, file_path))
    
    # 使用 commonpath 确保在项目根目录内
    if os.path.commonpath([abs_path, PROJECT_ROOT]) != os.path.abspath(PROJECT_ROOT):
        raise ValueError(f"禁止访问项目目录以外的文件: {file_path}")
    
    # 检查是否在禁止访问的敏感目录中
    for blocked in BLOCKED_DIRECTORIES:
        if os.path.commonpath([abs_path, blocked]) == os.path.abspath(blocked):
            raise ValueError(f"禁止访问其他用户的私有数据: {file_path}")
    
    return abs_path


file_tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取项目中的文件内容。可以用来查看源代码、配置文件、日志等。"
                "路径相对于项目根目录，例如 'server.py' 或 'static/chat.html'。"
                "注意：只能访问公共目录（根目录、tools/、static/），不能访问其他用户的私有数据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "相对于项目根目录的文件路径，如 'server.py' 或 'tools/search_tools.py'",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "写入或覆盖项目中的文件。可以修改源代码、创建新文件等。"
                "路径相对于项目根目录。此操作会覆盖已有文件，请谨慎使用。"
                "注意：只能写入公共目录，不能写入其他用户的私有数据目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "相对于项目根目录的文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文件内容",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "列出项目目录下的文件和子目录。可指定子目录路径，默认为项目根目录。"
                "用于了解项目结构和文件组织。"
                "注意：只能查看公共目录（根目录、tools/、static/），不能查看其他用户的私有数据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "相对于项目根目录的子目录，默认''表示根目录",
                    },
                },
                "required": [],
            },
        },
    },
]


def read_file(file_path: str) -> str:
    """读取项目文件内容（仅限公共目录）"""
    try:
        abs_path = _safe_path(file_path)
        if not os.path.isfile(abs_path):
            return f"[错误] 文件不存在: {file_path}"
        if abs_path.endswith(".db"):
            return f"[拒绝] 禁止直接读取数据库文件: {file_path}"
        # 限制文件大小（最大 1MB）
        size = os.path.getsize(abs_path)
        if size > 1024 * 1024:
            return f"[错误] 文件过大 ({size} 字节)，请使用分段读取"
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return f"[文件] {file_path}\n共 {len(content)} 个字符\n{'='*50}\n{content}"
    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 读取文件失败: {e}"


def write_file(file_path: str, content: str) -> str:
    """写入项目文件（仅限公共目录）"""
    try:
        abs_path = _safe_path(file_path)
        # 确保目录存在
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        # 如果文件已存在，做备份
        backup_info = ""
        if os.path.isfile(abs_path):
            backup_path = abs_path + ".bak"
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    old = f.read()
                with open(backup_path, "w", encoding="utf-8") as f:
                    f.write(old)
                backup_info = f"，原文件已备份至 {file_path}.bak"
            except Exception:
                pass
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[成功] 已写入文件: {file_path}，共 {len(content)} 个字符{backup_info}"
    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 写入文件失败: {e}"


def list_files(directory: str = "") -> str:
    """列出目录内容（仅限公共目录）"""
    try:
        abs_path = _safe_path(directory) if directory else PROJECT_ROOT
        if not os.path.isdir(abs_path):
            return f"[错误] 目录不存在: {directory}"
        items = []
        for name in sorted(os.listdir(abs_path)):
            full = os.path.join(abs_path, name)
            # 跳过备份文件和敏感文件
            if name.endswith(".bak") or name == "__pycache__" or name.startswith("."):
                continue
            # 跳过 data/ 下的用户私有数据目录（但允许列出 data/ 自身名称）
            if abs_path == PROJECT_ROOT and name == "data":
                # 只显示 data 目录名称，不展开其内容
                items.append(f"  📁 data (用户私有数据，不可访问)")
                continue
            # 如果是 data 子目录，不允许列出
            if "data" in abs_path.split(os.sep) and abs_path != os.path.join(PROJECT_ROOT, "data"):
                continue
            icon = "📁" if os.path.isdir(full) else "📄"
            size = os.path.getsize(full) if os.path.isfile(full) else 0
            size_str = f" ({size:,} 字节)" if size > 0 else ""
            items.append(f"  {icon} {name}{size_str}")
        if not items:
            return f"[目录] {directory or '根目录'}\n  (空目录)"
        return f"[目录] {directory or '根目录'}\n" + "\n".join(items)
    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 列出目录失败: {e}"
