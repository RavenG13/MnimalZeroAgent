"""
============================================================
  scheduler.py - 定时任务调度器
  支持用户创建一次性/重复定时任务
  使用 threading.Timer 实现轻量级调度
============================================================
"""
import os
import json
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Callable

from tools import project_tools as ptools

# 任务存储文件
SCHEDULER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scheduler")
os.makedirs(SCHEDULER_DIR, exist_ok=True)
TASKS_FILE = os.path.join(SCHEDULER_DIR, "tasks.json")

# 日志
logging.basicConfig(level=logging.INFO, format="[Scheduler] %(asctime)s %(message)s")

# 全局调度器
_scheduler_thread = None
_stop_event = threading.Event()
_active_timers: dict[str, threading.Timer] = {}


def _load_tasks() -> list[dict]:
    """从文件加载所有定时任务"""
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_tasks(tasks: list[dict]):
    """保存所有定时任务到文件"""
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def add_scheduled_task(
    username: str,
    name: str,
    cron_expr: str,
    action_type: str,
    action_params: dict,
    description: str = ""
) -> dict:
    """
    添加一个定时任务
    cron_expr: 简单格式 - "YYYY-MM-DD HH:MM" (一次性) 或 "HH:MM" (每天重复)
    action_type: "project_add", "project_update", "save_memory", "custom_message"
    action_params: 执行动作的参数
    """
    tasks = _load_tasks()
    
    # 检查重复名称
    for t in tasks:
        if t["name"] == name and t["username"] == username:
            return {"success": False, "message": f"定时任务 '{name}' 已存在"}
    
    task = {
        "id": str(int(time.time() * 1000))[-12:],
        "username": username,
        "name": name,
        "cron_expr": cron_expr,
        "action_type": action_type,
        "action_params": action_params,
        "description": description or f"定时任务: {name}",
        "enabled": True,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
        "next_run": _calculate_next_run(cron_expr),
    }
    
    tasks.append(task)
    _save_tasks(tasks)
    
    # 如果调度器正在运行，注册这个新任务
    if not _stop_event.is_set():
        _schedule_task(task)
    
    return {"success": True, "message": f"定时任务 '{name}' 已添加", "task": task}


def remove_scheduled_task(username: str, task_name: str) -> dict:
    """删除定时任务"""
    tasks = _load_tasks()
    tasks = [t for t in tasks if not (t["name"] == task_name and t["username"] == username)]
    _save_tasks(tasks)
    
    # 取消活跃的 timer
    task_id = None
    for t in tasks:
        if t.get("name") == task_name and t.get("username") == username:
            task_id = t.get("id")
            break
    if task_id and task_id in _active_timers:
        _active_timers[task_id].cancel()
        del _active_timers[task_id]
    
    return {"success": True, "message": f"定时任务 '{task_name}' 已删除"}


def list_scheduled_tasks(username: str = None) -> list[dict]:
    """列出定时任务"""
    tasks = _load_tasks()
    if username:
        return [t for t in tasks if t["username"] == username]
    return tasks


def _calculate_next_run(cron_expr: str) -> str:
    """计算下一次运行时间"""
    now = datetime.now()
    
    if " " in cron_expr:
        # 格式: "YYYY-MM-DD HH:MM" - 一次性任务
        try:
            dt = datetime.strptime(cron_expr, "%Y-%m-%d %H:%M")
            if dt > now:
                return dt.isoformat()
        except ValueError:
            pass
    
    # 格式: "HH:MM" - 每天重复
    try:
        parts = cron_expr.split(":")
        hour, minute = int(parts[0]), int(parts[1])
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run.isoformat()
    except (ValueError, IndexError):
        return ""
    
    return ""


def _get_delay_seconds(next_run_str: str) -> float:
    """计算距离下次运行的秒数"""
    if not next_run_str:
        return 3600  # 默认1小时后重试
    try:
        next_time = datetime.fromisoformat(next_run_str)
        delay = (next_time - datetime.now()).total_seconds()
        return max(delay, 1)
    except ValueError:
        return 3600


def _execute_task(task: dict):
    """执行定时任务"""
    username = task["username"]
    action_type = task["action_type"]
    params = task["action_params"]
    
    logging.info(f"执行定时任务: {task['name']} (用户: {username})")
    
    try:
        if action_type == "save_memory":
            # 保存一条自动记忆
            content = params.get("content", f"定时任务 '{task['name']}' 触发")
            from tools.memory_tools import save_memory
            save_memory(content, tags="scheduler,auto", user_id=username)
            
        elif action_type == "custom_message":
            # 记录一条自定义消息（可用于提醒等）
            from tools.memory_tools import save_memory
            save_memory(f"[定时提醒] {params.get('message', '无内容')}", tags="scheduler,reminder", user_id=username)
            
        elif action_type == "project_add":
            # 创建项目
            ptools.set_current_user(username)
            ptools._init_db(username)
            ptools.project_add(
                name=params.get("name", f"自动项目-{datetime.now().strftime('%m%d')}"),
                description=params.get("description", ""),
                status=params.get("status", "待启动"),
            )
            
        elif action_type == "project_update":
            # 更新项目进度
            ptools.set_current_user(username)
            ptools._init_db(username)
            ptools.project_update(
                name=params.get("name", ""),
                progress=params.get("progress"),
                progress_note=params.get("note", ""),
            )
            
        # 更新 last_run
        tasks = _load_tasks()
        for t in tasks:
            if t.get("id") == task["id"]:
                t["last_run"] = datetime.now().isoformat()
                # 如果是重复任务，计算下次运行
                if " " not in task.get("cron_expr", "") and ":" in task.get("cron_expr", ""):
                    t["next_run"] = _calculate_next_run(task["cron_expr"])
                else:
                    t["enabled"] = False  # 一次性任务执行后禁用
                break
        _save_tasks(tasks)
        
        logging.info(f"定时任务执行成功: {task['name']}")
        
    except Exception as e:
        logging.error(f"定时任务执行失败: {task['name']}: {e}")


def _schedule_task(task: dict):
    """注册一个定时任务到调度器"""
    if not task.get("enabled", True):
        return
    
    next_run = task.get("next_run", _calculate_next_run(task.get("cron_expr", "")))
    delay = _get_delay_seconds(next_run)
    
    timer = threading.Timer(delay, _execute_task, args=[task])
    timer.daemon = True
    timer.start()
    
    _active_timers[task["id"]] = timer
    logging.info(f"调度任务 '{task['name']}' 将在 {delay:.0f} 秒后执行 (下次: {next_run})")


def start_scheduler():
    """启动调度器主循环"""
    global _stop_event, _scheduler_thread
    
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    
    _stop_event.clear()
    
    def _loop():
        logging.info("定时任务调度器已启动")
        while not _stop_event.is_set():
            try:
                tasks = _load_tasks()
                for task in tasks:
                    task_id = task.get("id")
                    if task_id not in _active_timers and task.get("enabled", True):
                        _schedule_task(task)
                
                # 每 30 秒检查一次新任务
                _stop_event.wait(30)
                
            except Exception as e:
                logging.error(f"调度器循环错误: {e}")
                _stop_event.wait(60)
        
        logging.info("定时任务调度器已停止")
    
    _scheduler_thread = threading.Thread(target=_loop, daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    """停止调度器"""
    _stop_event.set()
    for timer in _active_timers.values():
        timer.cancel()
    _active_timers.clear()
    logging.info("调度器已停止")
