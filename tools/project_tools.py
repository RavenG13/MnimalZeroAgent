"""
============================================================
  project_tools.py - ZeroAgent 项目管理工具（SQLite版）
  
  功能：
  - 管理项目、子任务、进度（SQLite 存储）
  - 支持导入 CSV/Excel 现有项目数据
  - 支持导出为 CSV 格式
  - 自动创建/更新/查询项目进度
============================================================
"""
import os
import sqlite3
import csv
import json
import io
import threading
from datetime import datetime

# 数据库文件路径（放在 cloud-agent 根目录）
_tools_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else "."
DB_DIR = os.path.join(os.path.dirname(_tools_dir), "data", "users")
os.makedirs(DB_DIR, exist_ok=True)

# 线程局部变量：存储当前用户的 username（由 server 层在每个请求中设置）
_thread_local = threading.local()


def set_current_user(username: str):
    """设置当前请求的用户名（由 server 调用）"""
    _thread_local.username = username


def get_current_user() -> str:
    """获取当前用户名"""
    return getattr(_thread_local, "username", None) or "default"


def _get_db_path(username: str = None) -> str:
    """根据用户名获取对应的数据库文件路径"""
    if username is None:
        username = get_current_user()
    user_dir = os.path.join(DB_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "projects.db")


def _get_conn(username: str = None):
    """获取用户专属数据库连接"""
    db_path = _get_db_path(username)
    conn = sqlite3.connect(db_path)
    conn.text_factory = str  # 显式确保 TEXT 字段以 Unicode str 返回（防止 Windows GBK 乱码）
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


DB_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT DEFAULT '',
        leader TEXT DEFAULT '',
        deadline TEXT DEFAULT '',
        goal TEXT DEFAULT '',
        status TEXT DEFAULT '待启动',
        progress REAL DEFAULT 0.0,
        progress_note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        parent_id INTEGER DEFAULT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        status TEXT DEFAULT '待启动',
        start_time TEXT DEFAULT '',
        end_time TEXT DEFAULT '',
        priority TEXT DEFAULT '中',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        time_slot TEXT DEFAULT '',
        content TEXT DEFAULT '',
        priority TEXT DEFAULT '中',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
    CREATE INDEX IF NOT EXISTS idx_schedule_date ON schedule(date);
"""


def _init_db(username: str = None):
    """初始化某个用户的数据库表结构"""
    conn = _get_conn(username)
    conn.executescript(DB_SCHEMA_SQL)
    # 迁移：给已有数据库添加 parent_id 列（如果不存在）
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER DEFAULT NULL REFERENCES tasks(id) ON DELETE CASCADE")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)")
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.commit()
    conn.close()


# 仅在首次导入时初始化 default 用户的数据库（兼容旧逻辑）
_init_db("default")

# ============================================================
#  任务树形结构构建工具
# ============================================================

def _build_task_tree(tasks_rows: list) -> list:
    """
    将扁平的 tasks 行列表转为层级嵌套的树形结构。
    返回顶层任务列表，每个任务节点包含 children 数组。
    """
    tasks = []
    task_map = {}

    for r in tasks_rows:
        node = {
            "id": r["id"],
            "project_id": r["project_id"],
            "parent_id": r["parent_id"],
            "name": r["name"],
            "description": r["description"] or "",
            "status": r["status"],
            "start_time": r["start_time"] or "",
            "end_time": r["end_time"] or "",
            "priority": r["priority"] or "中",
            "children": [],
        }
        task_map[node["id"]] = node
        tasks.append(node)

    roots = []
    for node in tasks:
        if node["parent_id"] and node["parent_id"] in task_map:
            task_map[node["parent_id"]]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _count_task_status(tasks_tree: list) -> tuple:
    """递归统计任务树上各状态的任务数量"""
    total, done, active, pending = 0, 0, 0, 0
    for node in tasks_tree:
        total += 1
        if node["status"] == "已完成":
            done += 1
        elif node["status"] == "推进中":
            active += 1
        else:
            pending += 1
        if node["children"]:
            c_total, c_done, c_active, c_pending = _count_task_status(node["children"])
            total += c_total
            done += c_done
            active += c_active
            pending += c_pending
    return total, done, active, pending

# ============================================================
#  工具定义
# ============================================================

project_tools = [
    # ---- 项目管理 ----
    {
        "type": "function",
        "function": {
            "name": "project_list_all",
            "description": "列出所有项目及其概要信息（名称、状态、进度、截止时间等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "按状态筛选：推进中/已完成/未启动/待启动，不传则列出全部",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_add",
            "description": "新增一个项目",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "项目名称"},
                    "description": {"type": "string", "description": "项目描述/情况概述"},
                    "leader": {"type": "string", "description": "负责人"},
                    "deadline": {"type": "string", "description": "截止时间，如 2026-07-17"},
                    "goal": {"type": "string", "description": "项目目标"},
                    "status": {"type": "string", "description": "状态：待启动/推进中/已完成"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_update",
            "description": "更新项目的字段（名称/描述/状态/进度/截止时间等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要更新的项目名称"},
                    "new_name": {"type": "string", "description": "新项目名称（改名时使用）"},
                    "description": {"type": "string", "description": "新的描述"},
                    "status": {"type": "string", "description": "新状态：待启动/推进中/已完成"},
                    "progress": {"type": "number", "description": "进度百分比，0-100"},
                    "deadline": {"type": "string", "description": "截止时间"},
                    "goal": {"type": "string", "description": "项目目标"},
                    "progress_note": {"type": "string", "description": "项目进展说明"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_delete",
            "description": "删除一个项目及其所有任务",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的项目名称"},
                },
                "required": ["name"],
            },
        },
    },
    # ---- 任务管理（支持多级层级）----
    {
        "type": "function",
        "function": {
            "name": "task_tree",
            "description": (
                "查看某个项目的完整任务树形结构（多级层级）。"
                "显示项目→一级任务→子任务→孙任务的嵌套关系，"
                "每个节点标注状态/优先级/时间。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "项目名称"},
                },
                "required": ["project_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": (
                "查看某个项目的任务列表（含层级缩进）。"
                "按层级缩进显示任务名、状态、优先级，清晰展示父子关系。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "项目名称"},
                    "status": {"type": "string", "description": "按状态筛选：待启动/推进中/已完成"},
                },
                "required": ["project_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_add",
            "description": (
                "为某个项目添加任务（支持多级层级）。\n"
                "不传 parent_task 则添加为一级任务。\n"
                "传 parent_task 则作为该任务的子任务。\n"
                "例如：任务A 是父任务，任务A-1、任务A-2 是其子任务。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "所属项目名称"},
                    "task_name": {"type": "string", "description": "任务名称"},
                    "parent_task": {"type": "string", "description": "父任务名称（可选，不传则为一级任务）"},
                    "description": {"type": "string", "description": "任务详细描述"},
                    "status": {"type": "string", "description": "状态：待启动/推进中/已完成"},
                    "start_time": {"type": "string", "description": "开始时间，如 2026-06-01"},
                    "end_time": {"type": "string", "description": "结束时间，如 2026-06-10"},
                    "priority": {"type": "string", "description": "优先级：高/中/低"},
                },
                "required": ["project_name", "task_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_update",
            "description": "更新任务的状态/描述/时间/名称。可用 new_task_name 重命名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "所属项目名称"},
                    "task_name": {"type": "string", "description": "要更新的任务名称"},
                    "new_task_name": {"type": "string", "description": "新任务名称（改名时用）"},
                    "description": {"type": "string", "description": "新描述"},
                    "status": {"type": "string", "description": "新状态：待启动/推进中/已完成"},
                    "start_time": {"type": "string", "description": "开始时间"},
                    "end_time": {"type": "string", "description": "结束时间"},
                    "priority": {"type": "string", "description": "优先级：高/中/低"},
                },
                "required": ["project_name", "task_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_delete",
            "description": "删除一个任务及其所有子任务（级联删除）",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "所属项目名称"},
                    "task_name": {"type": "string", "description": "要删除的任务名称"},
                },
                "required": ["project_name", "task_name"],
            },
        },
    },
    # ---- 日程管理 ----
    {
        "type": "function",
        "function": {
            "name": "schedule_add",
            "description": "添加一条日程安排",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期，如 2026-06-05"},
                    "content": {"type": "string", "description": "日程内容"},
                    "time_slot": {"type": "string", "description": "时间段，如 早上/下午/晚上 或具体时间 14:00-16:00"},
                    "priority": {"type": "string", "description": "优先级：高/中/低"},
                },
                "required": ["date", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_list",
            "description": "查看某段时间的日程安排",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "开始日期，如 2026-06-01"},
                    "end_date": {"type": "string", "description": "结束日期，如 2026-06-07，不传则只查 start_date"},
                },
                "required": ["start_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_delete",
            "description": "删除某条日程安排",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期"},
                    "content": {"type": "string", "description": "日程内容关键词，用于匹配删除"},
                },
                "required": ["date", "content"],
            },
        },
    },
    # ---- 导入导出 ----
    {
        "type": "function",
        "function": {
            "name": "project_export_csv",
            "description": "将所有项目和任务导出为CSV格式的文本（可直接复制保存为文件），用于备份或迁移",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_import_csv",
            "description": "从CSV格式文本导入项目和任务（支持导入之前导出的CSV内容）。注意：如果项目已存在会跳过。",
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_content": {
                        "type": "string",
                        "description": "CSV格式的文本内容（包含表头：项目名称,项目描述,负责人,截止时间,目标,状态,进度,任务名称,任务描述,任务状态,开始时间,结束时间,优先级,父任务）",
                    },
                },
                "required": ["csv_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_summary",
            "description": "生成项目进度总览报告，包含各项目状态、进度、完成度等统计信息",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


# ============================================================
#  实现函数
# ============================================================

def _get_or_create_project(name: str) -> int:
    """按名称查找项目，不存在则创建，返回 project_id"""
    conn = _get_conn()
    row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
    if row:
        conn.close()
        return row["id"]
    conn.execute(
        "INSERT INTO projects (name) VALUES (?)",
        (name,),
    )
    conn.commit()
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return pid


def project_list_all(status: str = None) -> str:
    """列出所有项目"""
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM projects WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY CASE status WHEN '推进中' THEN 1 WHEN '待启动' THEN 2 WHEN '已完成' THEN 3 ELSE 4 END, updated_at DESC"
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return "[空] 暂无项目记录"

    lines = [f"共 {len(rows)} 个项目：", ""]
    for r in rows:
        progress_str = f"{r['progress']:.0f}%" if r["progress"] else "0%"
        deadline_str = f" | 截止: {r['deadline']}" if r["deadline"] else ""
        lines.append(
            f"【{r['name']}】 状态:{r['status']} | 进度:{progress_str}{deadline_str}"
        )
        if r["goal"]:
            lines.append(f"   目标: {r['goal']}")
        if r["progress_note"]:
            lines.append(f"   进展: {r['progress_note']}")
        lines.append("")
    return "\n".join(lines)


def project_add(name: str, description: str = "", leader: str = "", deadline: str = "", goal: str = "", status: str = "待启动", progress: float = None) -> str:
    """新增项目"""
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if existing:
            return f"[失败] 项目 '{name}' 已存在，请使用 project_update 更新或换一个名称"
        if progress is not None:
            conn.execute(
                "INSERT INTO projects (name, description, leader, deadline, goal, status, progress) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, description, leader, deadline, goal, status, progress),
            )
        else:
            conn.execute(
                "INSERT INTO projects (name, description, leader, deadline, goal, status) VALUES (?, ?, ?, ?, ?, ?)",
                (name, description, leader, deadline, goal, status),
            )
        conn.commit()
        return f"[成功] 项目 '{name}' 已创建 (状态: {status})"
    finally:
        conn.close()


def project_update(name: str, new_name: str = None, description: str = None, status: str = None, progress: float = None, deadline: str = None, goal: str = None, progress_note: str = None) -> str:
    """更新项目"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
        if not row:
            return f"[失败] 项目 '{name}' 不存在"

        updates = []
        params = []
        if new_name is not None:
            updates.append("name = ?")
            params.append(new_name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if deadline is not None:
            updates.append("deadline = ?")
            params.append(deadline)
        if goal is not None:
            updates.append("goal = ?")
            params.append(goal)
        if progress_note is not None:
            # 追加进展说明，不覆盖
            old_note = row["progress_note"] or ""
            new_note = f"{old_note}; {progress_note}" if old_note else progress_note
            updates.append("progress_note = ?")
            params.append(new_note)

        if not updates:
            return "[提示] 没有要更新的字段"

        updates.append("updated_at = datetime('now','localtime')")
        params.append(name)
        conn.execute(f"UPDATE projects SET {', '.join(updates)} WHERE name = ?", params)
        conn.commit()
        updated_fields = [k for k in ["new_name", "description", "status", "progress", "deadline", "goal", "progress_note"] if locals().get(k) is not None]
        return f"[成功] 项目 '{name}' 已更新 ({', '.join(updated_fields)})"
    finally:
        conn.close()


def project_delete(name: str) -> str:
    """删除项目及其任务"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if not row:
            return f"[失败] 项目 '{name}' 不存在"
        pid = row["id"]
        # 删除关联任务
        conn.execute("DELETE FROM tasks WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
        conn.commit()
        return f"[成功] 项目 '{name}' 及其所有任务已删除"
    finally:
        conn.close()


def task_tree(project_name: str) -> str:
    """查看项目的完整任务树形结构"""
    conn = _get_conn()
    try:
        proj = conn.execute("SELECT id, name FROM projects WHERE name = ?", (project_name,)).fetchone()
        if not proj:
            return f"[失败] 项目 '{project_name}' 不存在"

        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY priority DESC, created_at",
            (proj["id"],),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return f"[空] 项目 '{project_name}' 暂无任务"

    tree = _build_task_tree(rows)
    total, done, active, pending = _count_task_status(tree)
    status_map = {"待启动": "[ ]", "推进中": "[~]", "已完成": "[x]"}

    lines = [f"项目: {project_name} | 任务树 ({total}个, [x]{done}/[~]{active}/[ ]{pending})", "=" * 40]

    def render_node(nodes, level=0, prefix=""):
        for i, node in enumerate(nodes):
            is_last = (i == len(nodes) - 1)
            if level == 0:
                connector = "- "
            else:
                connector = "  L- " if is_last else "  |- "
            marker = status_map.get(node["status"], "[?]")
            prio = f"[{node['priority']}]" if node["priority"] and node["priority"] != "中" else ""
            time_str = f" {node['start_time']}~{node['end_time']}" if node["start_time"] or node["end_time"] else ""
            indent = "  " * (level - 1) if level > 1 else ""
            if level > 0:
                indent = "  |  " * (level - 1)
            lines.append(f"{indent}{connector}{marker} {node['name']} {prio}{time_str}")
            if node["description"]:
                desc_indent = indent + ("     " if level > 0 else "  ")
                lines.append(f"{desc_indent}  : {node['description'][:60]}")
            if node["children"]:
                render_node(node["children"], level + 1, prefix)

    render_node(tree)
    return "\n".join(lines)


def task_list(project_name: str, status: str = None) -> str:
    """查看项目的任务（含层级缩进）"""
    conn = _get_conn()
    try:
        proj = conn.execute("SELECT id, name FROM projects WHERE name = ?", (project_name,)).fetchone()
        if not proj:
            return f"[失败] 项目 '{project_name}' 不存在"

        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND status = ? ORDER BY priority DESC, created_at",
                (proj["id"], status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY CASE status WHEN '推进中' THEN 1 WHEN '待启动' THEN 2 WHEN '已完成' THEN 3 ELSE 4 END, priority DESC",
                (proj["id"],),
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return f"[空] 项目 '{project_name}' 暂无任务"

    tree = _build_task_tree(rows)
    status_map = {"待启动": "[ ]", "推进中": "[~]", "已完成": "[x]"}
    lines = [f"项目: {project_name} | 任务列表:", ""]

    def render_flat(nodes, level=0):
        for node in nodes:
            marker = status_map.get(node["status"], "[?]")
            prio = f" ({node['priority']}优先级)" if node["priority"] and node["priority"] != "中" else ""
            time_str = f" | {node['start_time'] or '?'}~{node['end_time'] or '?'}" if node["start_time"] or node["end_time"] else ""
            indent = "  " + "  " * level
            lines.append(f"{indent}{marker} {node['name']}{prio}{time_str}")
            if node["description"]:
                lines.append(f"{indent}   ┈ {node['description'][:80]}")
            render_flat(node["children"], level + 1)

    render_flat(tree)
    return "\n".join(lines)


def task_add(project_name: str, task_name: str, parent_task: str = None, description: str = "", status: str = "待启动", start_time: str = "", end_time: str = "", priority: str = "中") -> str:
    """添加任务（支持多级层级）"""
    pid = _get_or_create_project(project_name)
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT id FROM tasks WHERE project_id = ? AND name = ?", (pid, task_name)).fetchone()
        if existing:
            return f"[失败] 任务 '{task_name}' 已存在于项目 '{project_name}' 中"

        # 查找父任务
        parent_id = None
        parent_msg = ""
        if parent_task:
            parent_row = conn.execute(
                "SELECT id, name FROM tasks WHERE project_id = ? AND name = ?",
                (pid, parent_task),
            ).fetchone()
            if not parent_row:
                return f"[失败] 父任务 '{parent_task}' 在项目 '{project_name}' 中不存在"
            parent_id = parent_row["id"]
            parent_msg = f"，父任务: '{parent_task}'"

        conn.execute(
            "INSERT INTO tasks (project_id, parent_id, name, description, status, start_time, end_time, priority) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, parent_id, task_name, description, status, start_time, end_time, priority),
        )
        conn.commit()
        return f"[成功] 已为项目 '{project_name}' 添加任务: {task_name} (状态: {status}){parent_msg}"
    finally:
        conn.close()


def task_update(project_name: str, task_name: str, new_task_name: str = None, description: str = None, status: str = None, start_time: str = None, end_time: str = None, priority: str = None) -> str:
    """更新子任务"""
    conn = _get_conn()
    try:
        proj = conn.execute("SELECT id FROM projects WHERE name = ?", (project_name,)).fetchone()
        if not proj:
            return f"[失败] 项目 '{project_name}' 不存在"
        task = conn.execute("SELECT * FROM tasks WHERE project_id = ? AND name = ?", (proj["id"], task_name)).fetchone()
        if not task:
            return f"[失败] 任务 '{task_name}' 不存在于项目 '{project_name}' 中"

        updates = []
        params = []
        if new_task_name is not None:
            updates.append("name = ?")
            params.append(new_task_name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if start_time is not None:
            updates.append("start_time = ?")
            params.append(start_time)
        if end_time is not None:
            updates.append("end_time = ?")
            params.append(end_time)
        if priority is not None:
            updates.append("priority = ?")
            params.append(priority)

        if not updates:
            return "[提示] 没有要更新的字段"

        updates.append("updated_at = datetime('now','localtime')")
        params.append(task["id"])
        conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return f"[成功] 任务 '{task_name}' 已更新"
    finally:
        conn.close()


def task_delete(project_name: str, task_name: str) -> str:
    """删除任务及其所有子任务（级联删除）"""
    conn = _get_conn()
    try:
        proj = conn.execute("SELECT id FROM projects WHERE name = ?", (project_name,)).fetchone()
        if not proj:
            return f"[失败] 项目 '{project_name}' 不存在"
        task = conn.execute("SELECT id FROM tasks WHERE project_id = ? AND name = ?", (proj["id"], task_name)).fetchone()
        if not task:
            return f"[失败] 任务 '{task_name}' 不存在"

        # 递归删除所有子孙任务
        def _delete_children(parent_id):
            children = conn.execute("SELECT id FROM tasks WHERE parent_id = ?", (parent_id,)).fetchall()
            for child in children:
                _delete_children(child["id"])
                conn.execute("DELETE FROM tasks WHERE id = ?", (child["id"],))

        _delete_children(task["id"])
        conn.execute("DELETE FROM tasks WHERE id = ?", (task["id"],))
        conn.commit()
        affected = conn.total_changes
        return f"[成功] 任务 '{task_name}' 及其所有子任务已删除 (共 {affected} 条)"
    finally:
        conn.close()


def schedule_add(date: str, content: str, time_slot: str = "", priority: str = "中") -> str:
    """添加日程"""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO schedule (date, time_slot, content, priority) VALUES (?, ?, ?, ?)",
            (date, time_slot, content, priority),
        )
        conn.commit()
        return f"[成功] 已添加日程: {date} {time_slot} - {content}"
    finally:
        conn.close()


def schedule_list(start_date: str, end_date: str = None) -> str:
    """查看日程"""
    conn = _get_conn()
    try:
        if end_date:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE date >= ? AND date <= ? ORDER BY date, time_slot",
                (start_date, end_date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE date = ? ORDER BY time_slot",
                (start_date,),
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return f"[空] {start_date}{' ~ '+end_date if end_date else ''} 无日程安排"

    lines = []
    current_date = None
    for r in rows:
        if r["date"] != current_date:
            current_date = r["date"]
            lines.append(f"\n--- {current_date} ---")
        ts = f" ({r['time_slot']})" if r["time_slot"] else ""
        prio = f" [{r['priority']}]" if r["priority"] != "中" else ""
        lines.append(f"  {ts} {r['content']}{prio}")
    return "\n".join(lines)


def schedule_delete(date: str, content: str) -> str:
    """删除日程"""
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM schedule WHERE date = ? AND content LIKE ?",
            (date, f"%{content}%"),
        )
        conn.commit()
        affected = conn.total_changes
        if affected > 0:
            return f"[成功] 已删除 {date} 包含 '{content}' 的 {affected} 条日程"
        return f"[失败] 未找到匹配的日程"
    finally:
        conn.close()


def project_export_csv() -> str:
    """导出所有项目和任务为CSV格式文本"""
    conn = _get_conn()
    try:
        projects = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "项目名称", "项目描述", "负责人", "截止时间", "目标", "状态", "进度",
            "任务名称", "任务描述", "任务状态", "开始时间", "结束时间", "优先级", "父任务",
        ])

        for p in projects:
            tasks = conn.execute(
                "SELECT t.*, pt.name as parent_name FROM tasks t LEFT JOIN tasks pt ON t.parent_id = pt.id WHERE t.project_id = ? ORDER BY t.created_at",
                (p["id"],),
            ).fetchall()
            if tasks:
                for t in tasks:
                    writer.writerow([
                        p["name"], p["description"], p["leader"], p["deadline"],
                        p["goal"], p["status"], f"{p['progress']:.0f}%",
                        t["name"], t["description"], t["status"],
                        t["start_time"], t["end_time"], t["priority"],
                        t["parent_name"] or "",
                    ])
            else:
                writer.writerow([
                    p["name"], p["description"], p["leader"], p["deadline"],
                    p["goal"], p["status"], f"{p['progress']:.0f}%",
                    "", "", "", "", "", "", "",
                ])
    finally:
        conn.close()

    csv_text = output.getvalue()
    output.close()
    return f"[导出成功] 共 {len(projects)} 个项目\n\n```csv\n{csv_text}```"


def project_import_csv(csv_content: str) -> str:
    """从CSV文本导入项目和任务"""
    reader = csv.DictReader(io.StringIO(csv_content.strip()))
    conn = _get_conn()
    try:
        imported_projects = 0
        imported_tasks = 0
        skipped = 0

        for row in reader:
            proj_name = row.get("项目名称", "").strip()
            if not proj_name:
                continue

            # 检查项目是否已存在
            existing = conn.execute("SELECT id FROM projects WHERE name = ?", (proj_name,)).fetchone()
            if existing:
                pid = existing["id"]
                # 项目存在，跳过创建项目，但尝试导入任务
            else:
                conn.execute(
                    "INSERT INTO projects (name, description, leader, deadline, goal, status, progress) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        proj_name,
                        row.get("项目描述", ""),
                        row.get("负责人", ""),
                        row.get("截止时间", ""),
                        row.get("目标", ""),
                        row.get("状态", "待启动"),
                        float(row.get("进度", "0").replace("%", "") or 0),
                    ),
                )
                conn.commit()
                pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                imported_projects += 1

            # 导入任务
            task_name = row.get("任务名称", "").strip()
            if task_name:
                task_existing = conn.execute(
                    "SELECT id FROM tasks WHERE project_id = ? AND name = ?",
                    (pid, task_name),
                ).fetchone()
                if task_existing:
                    skipped += 1
                else:
                    # 处理父任务引用
                    parent_id = None
                    parent_task_name = row.get("父任务", "").strip()
                    if parent_task_name:
                        parent_row = conn.execute(
                            "SELECT id FROM tasks WHERE project_id = ? AND name = ?",
                            (pid, parent_task_name),
                        ).fetchone()
                        if parent_row:
                            parent_id = parent_row["id"]

                    conn.execute(
                        "INSERT INTO tasks (project_id, parent_id, name, description, status, start_time, end_time, priority) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            pid, parent_id,
                            task_name,
                            row.get("任务描述", ""),
                            row.get("任务状态", "待启动"),
                            row.get("开始时间", ""),
                            row.get("结束时间", ""),
                            row.get("优先级", "中"),
                        ),
                    )
                    imported_tasks += 1

        conn.commit()
        return f"[导入成功] 新增项目: {imported_projects} 个, 新增任务: {imported_tasks} 个, 跳过重复: {skipped} 个"
    finally:
        conn.close()


def project_summary() -> str:
    """生成项目进度总览报告"""
    conn = _get_conn()
    try:
        projects = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()

        if not projects:
            return "[空] 暂无项目"

        total = len(projects)
        active = sum(1 for p in projects if p["status"] == "推进中")
        done = sum(1 for p in projects if p["status"] == "已完成")
        pending = sum(1 for p in projects if p["status"] in ("待启动", "未启动"))
        avg_progress = sum(p["progress"] or 0 for p in projects) / total if total > 0 else 0

        # 统计任务（每个项目的任务树统计）
        total_tasks = 0
        done_tasks = 0
        active_tasks = 0
        for p in projects:
            p_rows = conn.execute("SELECT * FROM tasks WHERE project_id = ?", (p["id"],)).fetchall()
            if p_rows:
                p_tree = _build_task_tree(p_rows)
                c_total, c_done, c_active, _ = _count_task_status(p_tree)
                total_tasks += c_total
                done_tasks += c_done
                active_tasks += c_active
    finally:
        conn.close()

    lines = [
        "=" * 50,
        "  项目进度总览报告",
        f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 50,
        "",
        f"  项目总数: {total}",
        f"  推进中:   {active}",
        f"  已完成:   {done}",
        f"  待启动:   {pending}",
        f"  平均进度: {avg_progress:.1f}%",
        "",
        f"  任务总数:     {total_tasks}",
        f"  已完成任务:   {done_tasks} ({done_tasks/total_tasks*100:.0f}% 完成率)" if total_tasks > 0 else "",
        f"  推进中任务:   {active_tasks}",
        "",
        "-" * 50,
        "  各项目详情:",
        "",
    ]

    for p in projects:
        progress_str = f"{p['progress']:.0f}%" if p["progress"] else "0%"
        deadline_str = f" [截止: {p['deadline']}]" if p["deadline"] else ""
        lines.append(f"  [{p['status']}] {p['name']} - {progress_str}{deadline_str}")
        if p["goal"]:
            lines.append(f"    目标: {p['goal']}")
        if p["progress_note"]:
            lines.append(f"    进展: {p['progress_note']}")

    return "\n".join(lines)
