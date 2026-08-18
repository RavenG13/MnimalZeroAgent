# -*- coding: utf-8 -*-
"""
============================================================
  calendar_tools.py - 日历与日程管理（飞书/Thunderbird 风格）
  - 周视图：横轴日期、纵轴时间、可拖选时间段创建日程
  - 月视图：显示每天的所有日程
  存储：SQLite schedule 表（扩展 start_time/end_time 字段）
============================================================
"""
import os
import sqlite3
import threading
from datetime import datetime, timedelta

# ---- 用户隔离 ----
_thread_local = threading.local()

def set_current_user(username: str):
    _thread_local.username = username

def get_current_user() -> str:
    return getattr(_thread_local, "username", None) or "default"


_tools_dir = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(os.path.dirname(_tools_dir), "data", "users")
os.makedirs(DB_DIR, exist_ok=True)


def _get_db_path(username: str = None) -> str:
    if username is None:
        username = get_current_user()
    user_dir = os.path.join(DB_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "projects.db")


def _get_conn(username: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path(username))
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db(username: str = None):
    """确保 schedule 表有 start_time / end_time 列"""
    conn = _get_conn(username)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(schedule)").fetchall()]
        if "start_time" not in cols:
            conn.execute("ALTER TABLE schedule ADD COLUMN start_time TEXT DEFAULT ''")
        if "end_time" not in cols:
            conn.execute("ALTER TABLE schedule ADD COLUMN end_time TEXT DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


# ============================================================
#  Agent 工具定义（供 AI 调用）
# ============================================================
calendar_tools = [
    {
        "type": "function",
        "function": {
            "name": "calendar_events_add",
            "description": "添加一条日历日程事件（支持具体起止时间）。用于周/月日历视图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期，如 2026-08-05"},
                    "start_time": {"type": "string", "description": "开始时间，如 09:00 或 09:30"},
                    "end_time": {"type": "string", "description": "结束时间，如 10:30，可空"},
                    "content": {"type": "string", "description": "日程内容/标题"},
                    "priority": {"type": "string", "description": "优先级：高/中/低"},
                },
                "required": ["date", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_events_list",
            "description": "查询某段时间内的所有日程事件（含起止时间）。返回事件列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "开始日期，如 2026-08-03"},
                    "end_date": {"type": "string", "description": "结束日期，如 2026-08-09"},
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_events_update",
            "description": "更新一条日历日程事件（修改时间/内容/优先级）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer", "description": "日程事件 ID"},
                    "date": {"type": "string", "description": "日期，如 2026-08-05"},
                    "start_time": {"type": "string", "description": "开始时间，如 09:00"},
                    "end_time": {"type": "string", "description": "结束时间"},
                    "content": {"type": "string", "description": "日程内容/标题"},
                    "priority": {"type": "string", "description": "优先级：高/中/低"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_events_delete",
            "description": "删除一条日历日程事件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer", "description": "日程事件 ID"},
                },
                "required": ["event_id"],
            },
        },
    },
]


# ============================================================
#  实现
# ============================================================

def _parse_dt(date: str, time_str: str) -> str:
    """将 date + time 解析为可排序的时间字符串"""
    if not date:
        return ""
    t = (time_str or "").strip()
    if not t:
        return date
    # 标准化 HH:MM
    try:
        parts = t.split(":")
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
        return f"{date} {hh:02d}:{mm:02d}"
    except Exception:
        return f"{date} {t}"


def calendar_events_add(date: str, content: str, start_time: str = "", end_time: str = "", priority: str = "中") -> str:
    """添加日程事件"""
    _init_db()
    conn = _get_conn()
    try:
        start_dt = _parse_dt(date, start_time)
        end_dt = _parse_dt(date, end_time) if end_time else ""
        conn.execute(
            "INSERT INTO schedule (date, time_slot, content, priority, start_time, end_time) VALUES (?, ?, ?, ?, ?, ?)",
            (date, start_time or "", content, priority, start_dt, end_dt),
        )
        conn.commit()
        ev_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return f"[成功] 已添加日程事件 #{ev_id}: {date} {start_time or '(全天)'} - {content}"
    finally:
        conn.close()


def calendar_events_list(start_date: str, end_date: str = None) -> str:
    """查询时间段内的日程事件"""
    _init_db()
    conn = _get_conn()
    try:
        if end_date:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE date >= ? AND date <= ? ORDER BY date, start_time, time_slot",
                (start_date, end_date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE date = ? ORDER BY start_time, time_slot",
                (start_date,),
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return f"[空] {start_date}~{end_date or start_date} 无日程事件"

    lines = [f"日程事件 ({len(rows)} 条):", "=" * 45]
    cur_date = None
    for r in rows:
        if r["date"] != cur_date:
            cur_date = r["date"]
            lines.append(f"\n--- {cur_date} ---")
        # 时间显示
        st = r["start_time"] or ""
        et = r["end_time"] or ""
        if st and et:
            time_str = f"{st.split(' ')[-1]}~{et.split(' ')[-1]}"
        elif st:
            time_str = st.split(" ")[-1]
        else:
            time_str = "全天"
        prio = f" [{r['priority']}]" if r["priority"] and r["priority"] != "中" else ""
        lines.append(f"  #{r['id']} {time_str} {r['content']}{prio}")
    return "\n".join(lines)


def calendar_events_update(event_id: int, date: str = None, start_time: str = None,
                           end_time: str = None, content: str = None, priority: str = None) -> str:
    """更新日程事件"""
    _init_db()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM schedule WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return f"[失败] 日程事件 #{event_id} 不存在"

        updates, params = [], []
        if date is not None:
            updates.append("date = ?"); params.append(date)
        if content is not None:
            updates.append("content = ?"); params.append(content)
        if priority is not None:
            updates.append("priority = ?"); params.append(priority)
        if start_time is not None:
            d = date if date is not None else row["date"]
            updates.append("start_time = ?"); params.append(_parse_dt(d, start_time))
            updates.append("time_slot = ?"); params.append(start_time)
        if end_time is not None:
            d = date if date is not None else row["date"]
            updates.append("end_time = ?"); params.append(_parse_dt(d, end_time))

        if not updates:
            return "[提示] 没有要更新的字段"

        params.append(event_id)
        conn.execute(f"UPDATE schedule SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return f"[成功] 日程事件 #{event_id} 已更新"
    finally:
        conn.close()


def calendar_events_delete(event_id: int) -> str:
    """删除日程事件"""
    _init_db()
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM schedule WHERE id = ?", (event_id,))
        conn.commit()
        if conn.total_changes > 0:
            return f"[成功] 日程事件 #{event_id} 已删除"
        return f"[失败] 日程事件 #{event_id} 不存在"
    finally:
        conn.close()


# ============================================================
#  REST API 辅助（供 server.py 调用，返回 JSON 友好结构）
# ============================================================

def api_get_events(start: str, end: str = None, username: str = None) -> list[dict]:
    """返回 JSON 格式的日程事件列表"""
    _init_db(username)
    conn = _get_conn(username)
    try:
        if end:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE date >= ? AND date <= ? ORDER BY date, start_time",
                (start, end),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE date = ? ORDER BY start_time",
                (start,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def api_add_event(data: dict, username: str = None) -> dict:
    """创建事件，返回 (success, event)"""
    _init_db(username)
    conn = _get_conn(username)
    try:
        date = data.get("date", "")
        content = data.get("content", "")
        start_time = data.get("start_time", "")
        end_time = data.get("end_time", "")
        priority = data.get("priority", "中")
        start_dt = _parse_dt(date, start_time)
        end_dt = _parse_dt(date, end_time) if end_time else ""
        cur = conn.execute(
            "INSERT INTO schedule (date, time_slot, content, priority, start_time, end_time) VALUES (?, ?, ?, ?, ?, ?)",
            (date, start_time or "", content, priority, start_dt, end_dt),
        )
        conn.commit()
        ev_id = cur.lastrowid
        row = conn.execute("SELECT * FROM schedule WHERE id = ?", (ev_id,)).fetchone()
        return {"success": True, "event": dict(row) if row else {"id": ev_id}}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def api_update_event(event_id: int, data: dict, username: str = None) -> dict:
    """更新事件"""
    _init_db(username)
    conn = _get_conn(username)
    try:
        row = conn.execute("SELECT * FROM schedule WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return {"success": False, "message": f"事件 #{event_id} 不存在"}

        updates, params = [], []
        if "date" in data:
            updates.append("date = ?"); params.append(data["date"])
        if "content" in data:
            updates.append("content = ?"); params.append(data["content"])
        if "priority" in data:
            updates.append("priority = ?"); params.append(data["priority"])
        if "start_time" in data:
            d = data.get("date", row["date"])
            updates.append("start_time = ?"); params.append(_parse_dt(d, data["start_time"]))
            updates.append("time_slot = ?"); params.append(data["start_time"])
        if "end_time" in data:
            d = data.get("date", row["date"])
            updates.append("end_time = ?"); params.append(_parse_dt(d, data["end_time"]))

        if not updates:
            return {"success": True, "event": dict(row)}

        params.append(event_id)
        conn.execute(f"UPDATE schedule SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        updated = conn.execute("SELECT * FROM schedule WHERE id = ?", (event_id,)).fetchone()
        return {"success": True, "event": dict(updated)}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def api_delete_event(event_id: int, username: str = None) -> dict:
    """删除事件"""
    _init_db(username)
    conn = _get_conn(username)
    try:
        conn.execute("DELETE FROM schedule WHERE id = ?", (event_id,))
        conn.commit()
        if conn.total_changes > 0:
            return {"success": True, "message": f"事件 #{event_id} 已删除"}
        return {"success": False, "message": f"事件 #{event_id} 不存在"}
    finally:
        conn.close()
