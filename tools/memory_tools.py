import os
import json
import sqlite3
from datetime import datetime

# ============================================================
#  记忆管理工具 — 按用户分文件存储版
#  每个用户独立的 SQLite 数据库文件，位于 data/memory/<user_id>.db
#  彻底隔离不同用户之间的记忆
# ============================================================

MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memory")
os.makedirs(MEMORY_DIR, exist_ok=True)


# ---- 用户隔离：通过 threading.local 获取当前用户名 ----
import threading
_thread_local = threading.local()


def set_current_user(username: str):
    """设置当前请求的用户名（由 server 调用）"""
    _thread_local.username = username


def get_current_user() -> str:
    """获取当前用户名"""
    return getattr(_thread_local, "username", None) or "default"


def _get_db_path(user_id: str = None) -> str:
    """根据用户 ID 获取对应的记忆数据库文件路径"""
    if user_id is None:
        user_id = get_current_user()
    # 确保文件名安全
    safe_id = user_id.replace("/", "_").replace("\\", "_").replace(".", "_")
    return os.path.join(MEMORY_DIR, f"{safe_id}.db")


def _init_db(user_id: str = None):
    """初始化某用户的记忆数据库表"""
    db_path = _get_db_path(user_id)
    conn = sqlite3.connect(db_path)
    conn.text_factory = str  # 显式确保 TEXT 字段以 Unicode str 返回
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


# 不再自动初始化全局 memory.db，改为按需初始化


# ---- 工具定义 ----

memory_tools = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "将一段文本保存到你自己的记忆数据库。每次保存会追加一条带时间戳的记录。你只能保存到自己的记忆，无法操作其他用户的记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要保存的记忆内容",
                    },
                    "tags": {
                        "type": "string",
                        "description": "可选标签，用逗号分隔，如 'python,chat,tool'",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_memory",
            "description": "从数据库加载你自己的记忆记录。你只能查看自己的记忆，无法查看其他用户的记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回的最大记录数，默认 50",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_memory",
            "description": "清空你自己的记忆记录。你只能清空自己的记忆，无法操作其他用户的记忆。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "搜索你自己的记忆，按关键词匹配内容或标签。你只能搜索自己的记忆，无法搜索其他用户的。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回的最大记录数，默认 20",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
]


# ---- 数据库操作函数 ----

def _get_conn(user_id: str = None) -> sqlite3.Connection:
    """获取用户专属的记忆数据库连接"""
    _init_db(user_id)
    db_path = _get_db_path(user_id)
    conn = sqlite3.connect(db_path)
    conn.text_factory = str  # 显式确保 TEXT 字段以 Unicode str 返回
    conn.row_factory = sqlite3.Row
    return conn


def save_memory(content: str, tags: str = "") -> str:
    """保存一条记忆到当前用户的专属数据库"""
    try:
        uid = get_current_user()
        conn = _get_conn(uid)
        try:
            cursor = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT INTO memories (time, content, tags) VALUES (?, ?, ?)",
                (now, content, tags)
            )
            conn.commit()
            new_id = cursor.lastrowid

            # 统计该用户总记录数
            cursor.execute("SELECT COUNT(*) as cnt FROM memories")
            total = cursor.fetchone()["cnt"]

            return f"[成功] 记忆已保存，id={new_id}（共 {total} 条）"
        finally:
            conn.close()
    except Exception as e:
        return f"[错误] 保存记忆失败: {e}"


def load_memory(limit: int = 50) -> str:
    """加载当前用户的记忆记录（只能加载自己的）"""
    try:
        uid = get_current_user()
        conn = _get_conn(uid)
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT id, time, content, tags FROM memories ORDER BY id DESC LIMIT ?",
                (limit,)
            )

            rows = cursor.fetchall()

            if not rows:
                return f"[空] 当前没有任何记忆记录。"

            lines = [f"记忆记录 ({len(rows)} 条)"]
            lines.append("=" * 50)
            for row in rows:
                entry_id = row["id"]
                t = row["time"]
                tags_str = f" [{row['tags']}]" if row["tags"] else ""
                content_preview = (row["content"] or "")[:200]
                lines.append(f"\n  #{entry_id} [{t}]{tags_str}")
                lines.append(f"    {content_preview}")

            return "\n".join(lines)
        finally:
            conn.close()
    except Exception as e:
        return f"[错误] 加载记忆失败: {e}"


def clear_memory() -> str:
    """清空当前用户的记忆记录（只能清空自己的）"""
    try:
        uid = get_current_user()
        conn = _get_conn(uid)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories")
            deleted = cursor.rowcount
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='memories'")
            conn.commit()
            return f"[成功] 已清空 {deleted} 条记忆记录。"
        finally:
            conn.close()
    except Exception as e:
        return f"[错误] 清空记忆失败: {e}"


def search_memory(keyword: str, limit: int = 20) -> str:
    """搜索当前用户的记忆（只能搜索自己的）"""
    try:
        uid = get_current_user()
        conn = _get_conn(uid)
        try:
            cursor = conn.cursor()
            like_pattern = f"%{keyword}%"

            cursor.execute(
                """SELECT id, time, content, tags FROM memories
                   WHERE content LIKE ? OR tags LIKE ?
                   ORDER BY id DESC LIMIT ?""",
                (like_pattern, like_pattern, limit)
            )

            rows = cursor.fetchall()

            if not rows:
                return f"[搜索无结果] 未找到包含 \"{keyword}\" 的记忆记录。"

            lines = [f"搜索 \"{keyword}\" 结果 ({len(rows)} 条)"]
            lines.append("=" * 50)
            for row in rows:
                lines.append(f"\n  #{row['id']} [{row['time']}]")
                content_preview = (row["content"] or "")[:200]
                lines.append(f"    {content_preview}")

            return "\n".join(lines)
        finally:
            conn.close()
    except Exception as e:
        return f"[错误] 搜索记忆失败: {e}"
