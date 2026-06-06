"""
============================================================
  node_tools.py - ZeroAgent Client 节点工具定义
  暴露给 AI Agent 的本地工具：文件操作 + Shell 执行
  支持 --root 工作目录限制，防止越权访问
============================================================
"""
import os
import subprocess
import platform
from typing import Optional

# ============================================================
#  OpenAI 工具 JSON Schema（上报给服务器，由服务器前缀节点名）
# ============================================================

NODE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取客户端本地文件的内容。"
                "支持文本文件和二进制文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对路径或相对于工作目录的路径）",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "将内容写入客户端本地文件。"
                "如果文件已存在则覆盖，父目录不存在则自动创建。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "写入路径（绝对路径或相对于工作目录的路径）",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "列出客户端本地目录中的文件和子目录。"
                "如果不传 path 则列出工作目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（可选，默认工作目录）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "在客户端本地执行 shell 命令并返回输出。"
                "可用于运行脚本、安装软件、管理系统等。"
                "支持指定 timeout（秒），默认 60 秒。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 60，最大 300",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": (
                "获取客户端设备的系统信息：操作系统、主机名、"
                "当前工作目录、Python 版本等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# ============================================================
#  工具实现
# ============================================================

def _resolve_path(path: str, root: Optional[str]) -> str:
    """
    解析路径并检查安全边界。
    - 如果 root 已设置，路径必须在 root 之内。
    - 相对路径以 root（或当前目录）为基准。
    """
    if root:
        root = os.path.abspath(os.path.expanduser(root))
        if os.path.isabs(path):
            full = os.path.abspath(os.path.expanduser(path))
        else:
            full = os.path.abspath(os.path.join(root, path))
        # 安全检查：确保解析后的路径在 root 之下
        if not full.startswith(root + os.sep) and full != root:
            raise PermissionError(
                f"路径越界！允许的根目录是 {root}，"
                f"请求的路径解析为 {full}"
            )
        return full
    else:
        return os.path.abspath(os.path.expanduser(path))


def read_file(path: str, root: Optional[str] = None) -> str:
    """读取本地文件内容。"""
    full = _resolve_path(path, root)
    if not os.path.exists(full):
        return f"[错误] 文件不存在: {full}"
    if os.path.isdir(full):
        return f"[错误] 路径是目录而非文件: {full}"
    try:
        # 先尝试 UTF-8 文本读取
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        size_mb = os.path.getsize(full) / (1024 * 1024)
        if size_mb > 1:
            preview = content[:3000]
            return (
                f"[已截断] 文件大小 {size_mb:.1f} MB，仅显示前 3000 字符:\n"
                f"{'-' * 40}\n{preview}\n{'-' * 40}"
            )
        return content
    except Exception as e:
        return f"[错误] 读取文件失败: {e}"


def write_file(path: str, content: str, root: Optional[str] = None) -> str:
    """写入内容到本地文件。"""
    full = _resolve_path(path, root)
    try:
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        size_kb = len(content.encode("utf-8")) / 1024
        return f"[成功] 已写入: {full} ({size_kb:.1f} KB)"
    except PermissionError as e:
        return f"[错误] 权限不足: {e}"
    except Exception as e:
        return f"[错误] 写入文件失败: {e}"


def list_files(path: str = "", root: Optional[str] = None) -> str:
    """列出本地目录内容。"""
    full = _resolve_path(path or ".", root)
    if not os.path.exists(full):
        return f"[错误] 目录不存在: {full}"
    if not os.path.isdir(full):
        return f"[错误] 不是目录: {full}"
    try:
        items = sorted(os.listdir(full))
        if not items:
            return f"[空] {full} 中没有文件"
        lines = [f"目录: {full}", f"共 {len(items)} 个项目", "-" * 40]
        for item in items:
            item_path = os.path.join(full, item)
            prefix = "📁" if os.path.isdir(item_path) else "📄"
            try:
                size = os.path.getsize(item_path)
                size_str = f" ({_format_size(size)})" if not os.path.isdir(item_path) else ""
            except OSError:
                size_str = ""
            lines.append(f"  {prefix} {item}{size_str}")
        return "\n".join(lines)
    except PermissionError:
        return f"[错误] 没有权限访问: {full}"
    except Exception as e:
        return f"[错误] 列出目录失败: {e}"


def run_shell(command: str, timeout: int = 60, root: Optional[str] = None) -> str:
    """在本地执行 shell 命令。"""
    timeout = min(max(timeout, 1), 300)  # 限制 1-300 秒
    cwd = os.path.abspath(os.path.expanduser(root)) if root else None

    # Windows: 使用 cmd.exe /c；其他: 使用 /bin/sh -c
    if platform.system() == "Windows":
        shell_cmd = ["cmd.exe", "/c", command]
    else:
        shell_cmd = ["/bin/sh", "-c", command]

    try:
        result = subprocess.run(
            shell_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output and not output.endswith("\n"):
                output += "\n"
            output += result.stderr
        if not output:
            output = f"[无输出] exit_code={result.returncode}"
        return output.rstrip()
    except subprocess.TimeoutExpired:
        return f"[超时] 命令在 {timeout}s 后未完成，已终止"
    except FileNotFoundError:
        return f"[错误] Shell 未找到: {shell_cmd[0]}"
    except Exception as e:
        return f"[错误] 执行命令失败: {e}"


def get_system_info(root: Optional[str] = None) -> str:
    """获取系统信息。"""
    info = {
        "操作系统": platform.system(),
        "系统版本": platform.version(),
        "主机名": platform.node(),
        "架构": platform.machine(),
        "Python 版本": platform.python_version(),
        "工作目录": os.path.abspath(os.path.expanduser(root)) if root else os.getcwd(),
    }
    lines = ["=== 客户端系统信息 ==="]
    for k, v in info.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


# ============================================================
#  工具调度器
# ============================================================

# 工具名 → 函数映射
NODE_FUNC_MAP = {
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "run_shell": run_shell,
    "get_system_info": get_system_info,
}


def _format_size(size_bytes: int) -> str:
    """格式化文件大小。"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.0f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.0f}TB"


def execute_tool(name: str, args: dict, root: Optional[str] = None) -> str:
    """根据工具名和参数执行对应的本地函数。"""
    func = NODE_FUNC_MAP.get(name)
    if not func:
        return f"[错误] 未知工具: {name}"

    # 注入 root（文件相关操作需要）
    if name in ("read_file", "write_file", "list_files", "get_system_info"):
        args = {**args, "root": root}
    elif name == "run_shell":
        # run_shell 的 root 用作 cwd
        args = {**args, "root": root}

    try:
        return func(**args)
    except TypeError as e:
        return f"[错误] 工具参数错误 ({name}): {e}"
    except Exception as e:
        return f"[错误] 工具执行异常 ({name}): {e}"
