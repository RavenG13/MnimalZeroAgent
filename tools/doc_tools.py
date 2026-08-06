"""
============================================================
  doc_tools - 在线文档管理工具（用户隔离版）
  用于 AI 保存/编辑/读取/删除用户的在线文档（Markdown/文本）。

  安全设计：
  - 通过 threading.local 强制使用当前登录用户（不接受 AI 传入用户名）
  - 只允许操作 data/user_files/<当前用户>/docs/ 目录
  - 文件名严格校验：禁止路径穿越（..）、禁止分隔符、禁止绝对路径
  - 每个用户只能操作自己的文档，彻底隔离
============================================================
"""
import os
import re
import threading
from datetime import datetime

# ---- 用户隔离：通过 threading.local 获取当前用户名 ----
_thread_local = threading.local()

def set_current_user(username: str):
    """设置当前请求的用户名（由 server.py 调用）"""
    _thread_local.username = username

def get_current_user() -> str:
    """获取当前用户名"""
    return getattr(_thread_local, "username", None) or "default"

# ---- 存储路径 ----
USER_FILES_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "user_files",
)
os.makedirs(USER_FILES_ROOT, exist_ok=True)

# 合法文件名：字母数字、中文、下划线、横线、空格、点（不含扩展名分隔符风险）
_FILENAME_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff_\-\. ]+$")

# 允许的扩展名
ALLOWED_EXT = (".md", ".markdown", ".txt", ".text")


def _safe_user_dir() -> str:
    """获取当前用户的 docs 目录（自动创建）"""
    uid = get_current_user()
    # 防止用户名包含路径分隔符
    safe_uid = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", uid)
    user_dir = os.path.join(USER_FILES_ROOT, safe_uid)
    docs_dir = os.path.join(user_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    return docs_dir


def _validate_filename(filename: str) -> str:
    """
    校验文档文件名，只允许简单文件名（不含路径）。
    返回规范化后的文件名（如果没扩展名则补 .md）。
    """
    filename = (filename or "").strip().strip("/").strip("\\")
    if not filename:
        raise ValueError("文件名不能为空")

    # 禁止路径穿越和绝对路径
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        raise ValueError(f"文件名不能包含路径穿越或绝对路径: {filename}")
    if "/" in filename or "\\" in filename:
        raise ValueError(f"文件名不能包含目录分隔符: {filename}")

    # 规范化：无扩展名则补 .md
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        filename = filename + ".md"
        ext = ".md"
    if ext not in ALLOWED_EXT:
        raise ValueError(f"不支持的扩展名 '{ext}'，仅允许: {', '.join(ALLOWED_EXT)}")

    # 字符合法性
    if not _FILENAME_RE.match(filename):
        raise ValueError(f"文件名包含非法字符: {filename}")

    return filename


# ============================================================
#  工具定义（server.py discover_modules 会自动加载 *_tools 列表）
# ============================================================
doc_tools = [
    {
        "type": "function",
        "function": {
            "name": "save_doc",
            "description": (
                "将内容保存为当前用户的在线文档（Markdown 或文本文件）。"
                "用于保存 AI 与用户讨论后的总结、项目笔记、随笔等。"
                "文件保存在用户自己的 docs/ 目录，网页『在线文档』页面可见。"
                "如果文件已存在则覆盖。自动按用户隔离，只能操作当前用户自己的文档。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文档文件名，如 '项目笔记' 或 '2026-07-31_总结.md'。只允许文件名，不能含路径。支持 .md/.markdown/.txt",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文档完整内容（Markdown 或纯文本）",
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_doc",
            "description": (
                "读取当前用户的在线文档内容。只能读取当前用户自己的文档。"
                "用于查看之前保存的笔记、总结等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文档文件名（含扩展名），如 '项目笔记.md'",
                    },
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_docs",
            "description": (
                "列出当前用户的所有在线文档（docs/ 目录下的 .md/.txt 文件）。"
                "返回文件名列表和修改时间。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_doc",
            "description": (
                "删除当前用户的一个在线文档。只能删除当前用户自己的文档。"
                "删除前请与用户确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "要删除的文档文件名（含扩展名）",
                    },
                },
                "required": ["filename"],
            },
        },
    },
]


# ============================================================
#  实现
# ============================================================

def save_doc(filename: str, content: str) -> str:
    """保存/覆盖当前用户的在线文档"""
    try:
        name = _validate_filename(filename)
        docs_dir = _safe_user_dir()
        file_path = os.path.join(docs_dir, name)

        # 确保目录存在
        os.makedirs(docs_dir, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content or "")

        size_kb = len((content or "").encode("utf-8")) / 1024
        uid = get_current_user()
        return (
            f"[成功] 文档已保存\n"
            f"  用户: {uid}\n"
            f"  文件: docs/{name}\n"
            f"  大小: {size_kb:.1f} KB\n"
            f"  位置: {file_path}\n"
            f"  提示: 网页『在线文档』页面（📄）中可以看到此文档"
        )
    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 保存文档失败: {e}"


def read_doc(filename: str) -> str:
    """读取当前用户的在线文档"""
    try:
        name = _validate_filename(filename)
        docs_dir = _safe_user_dir()
        file_path = os.path.join(docs_dir, name)

        if not os.path.isfile(file_path):
            # 尝试无扩展名匹配
            candidates = [f for f in os.listdir(docs_dir)
                          if f.lower().startswith(name.split(".")[0].lower())]
            if candidates:
                file_path = os.path.join(docs_dir, candidates[0])
                name = candidates[0]
            else:
                return f"[未找到] 文档 '{filename}' 不存在。可用 list_docs 查看所有文档。"

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        return f"[文档] {name}\n{'='*50}\n{content}"
    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 读取文档失败: {e}"


def list_docs() -> str:
    """列出当前用户的在线文档"""
    try:
        docs_dir = _safe_user_dir()
        if not os.path.isdir(docs_dir):
            return "[空] 当前还没有任何文档"

        items = []
        for fname in sorted(os.listdir(docs_dir)):
            full = os.path.join(docs_dir, fname)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ALLOWED_EXT:
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M")
            size = os.path.getsize(full)
            items.append(f"  📄 {fname}  ({size}B, 修改于 {mtime})")

        uid = get_current_user()
        if not items:
            return f"[空] 用户 '{uid}' 当前没有任何文档"

        return f"用户 '{uid}' 的在线文档 ({len(items)} 个):\n" + "\n".join(items)
    except Exception as e:
        return f"[错误] 列出文档失败: {e}"


def delete_doc(filename: str) -> str:
    """删除当前用户的在线文档"""
    try:
        name = _validate_filename(filename)
        docs_dir = _safe_user_dir()
        file_path = os.path.join(docs_dir, name)

        if not os.path.isfile(file_path):
            return f"[未找到] 文档 '{filename}' 不存在"

        os.remove(file_path)
        return f"[成功] 文档已删除: {name}"
    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 删除文档失败: {e}"
