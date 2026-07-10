#!/usr/bin/env python3
"""
============================================================
  client.py - ZeroAgent Client Node
  连接服务器 AI 大脑，暴露本地工具供 Agent 调用。

  用法:
    python client.py
    python client.py --root ~/projects --mode auto
    python client.py --server http://192.168.1.100:8010
============================================================
"""
import os
import sys
import json
import uuid
import socket
import argparse
import asyncio
import traceback
import queue as sync_queue
from pathlib import Path
from datetime import datetime

# --- 将 node_tools 加入路径 ---
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
import websockets
from node_tools import NODE_TOOL_SCHEMAS, execute_tool

# ============================================================
#  终端颜色（跨平台）
# ============================================================
def _color(code: int, text: str) -> str:
    """终端颜色包装（仅在支持 ANSI 的终端生效）。"""
    if os.name == "nt":
        # Windows 10+ 终端支持 ANSI
        return f"\033[{code}m{text}\033[0m"
    return f"\033[{code}m{text}\033[0m"

C_GREEN  = lambda t: _color(32, t)
C_CYAN   = lambda t: _color(36, t)
C_YELLOW = lambda t: _color(33, t)
C_RED    = lambda t: _color(31, t)
C_BLUE   = lambda t: _color(34, t)
C_BOLD   = lambda t: _color(1, t)
C_DIM    = lambda t: _color(2, t)
C_RESET  = "\033[0m"

# 原始 ANSI 码，用于 f-string 内联拼接
_G = "\033[32m"   # green
_C = "\033[36m"   # cyan
_Y = "\033[33m"   # yellow
_R = "\033[31m"   # red
_B = "\033[34m"   # blue
_BD = "\033[1m"   # bold
_DM = "\033[2m"   # dim
_RS = "\033[0m"   # reset

# ============================================================
#  TUI 交互
# ============================================================
def print_banner():
    print()
    print(C_CYAN("  ╔" + "═" * 48 + "╗"))
    print(C_CYAN("  ║") + C_BOLD("  ZeroAgent Client Node".center(44)) + C_CYAN("║"))
    print(C_CYAN("  ║") + C_DIM("  连接到 AI 大脑，让 Agent 在本地工作".center(40)) + C_CYAN("║"))
    print(C_CYAN("  ╚" + "═" * 48 + "╝"))
    print()


def _get_password_star(prompt: str) -> str:
    """
    密码输入增强 — 用 * 号回显（Windows 风格），而不是 Linux 的无显示。
    """
    if os.name == "nt":
        import msvcrt
        sys.stdout.write(prompt)
        sys.stdout.flush()
        chars = []
        while True:
            ch = msvcrt.getwch()
            if ch == "\r" or ch == "\n":
                sys.stdout.write("\n")
                return "".join(chars)
            elif ch == "\x08":  # Backspace
                if chars:
                    chars.pop()
                    # 擦除最后一个星号: \b 回退, 空格覆盖, \b 再回退
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            elif ch == "\x03":  # Ctrl+C
                sys.stdout.write("\n")
                raise KeyboardInterrupt
            elif ord(ch) >= 32:  # 可打印字符
                chars.append(ch)
                sys.stdout.write("*")
                sys.stdout.flush()
    else:
        # Unix: 用 termios 实现 * 回显
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdout.write(prompt)
            sys.stdout.flush()
            chars = []
            while True:
                ch = sys.stdin.read(1)
                if ch == "\r" or ch == "\n":
                    sys.stdout.write("\r\n")
                    return "".join(chars)
                elif ch == "\x7f":  # Backspace on Unix
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                elif ch == "\x03":
                    sys.stdout.write("\r\n")
                    raise KeyboardInterrupt
                elif ord(ch) >= 32:
                    chars.append(ch)
                    sys.stdout.write("*")
                    sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _edit_with_default(prompt: str, default: str) -> str:
    """
    带预填默认值的输入。用户看到默认值已填入，可直接编辑或按 Enter 接受。
    Unix: 使用 readline 预填。
    Windows: 使用 msvcrt 逐字符编辑，实时显示修改结果。
    """
    # 方法 1: readline（类 Unix，macOS 等）
    try:
        import readline
        readline.set_startup_hook(lambda: readline.insert_text(default))
        try:
            return input(prompt)
        finally:
            readline.set_startup_hook()
    except (ImportError, AttributeError):
        pass

    # 方法 2: Windows msvcrt（可靠的可编辑预填）
    if os.name == "nt":
        try:
            import msvcrt
            return _windows_edit(prompt, default)
        except Exception:
            pass

    # 方法 3: 回退 — 显示默认值让用户输入
    sys.stdout.write(prompt)
    sys.stdout.write(default)
    sys.stdout.flush()
    # 发送退格键，让用户编辑
    user_input = input()
    return user_input if user_input.strip() else default


def _windows_edit(prompt: str, default: str) -> str:
    """
    Windows 控制台下带预填的编辑实现（仅支持尾部编辑）。
    默认文本已显示在提示符后，用户可:
      - 键入字符追加到末尾
      - Backspace 删除末尾字符
      - Enter 确认，Esc 清空
    """
    import msvcrt

    # 打印 prompt + 默认文本，光标留在末尾
    sys.stdout.write(prompt + default)
    sys.stdout.flush()

    buffer = list(default)

    while True:
        ch = msvcrt.getwch()

        if ch == "\r" or ch == "\n":  # Enter
            sys.stdout.write("\n")
            return "".join(buffer)

        elif ch == "\x08":  # Backspace — 删除末尾字符
            if buffer:
                buffer.pop()
                # \b 回退一格, 空格覆盖, \b 再回退
                sys.stdout.write("\b \b")
                sys.stdout.flush()

        elif ch == "\x1b":  # Esc — 清空全部
            count = len(buffer)
            # 回退 count 格, 写 count 个空格覆盖, 再回退 count 格
            sys.stdout.write("\b" * count + " " * count + "\b" * count)
            sys.stdout.flush()
            buffer = []

        elif ch == "\x03":  # Ctrl+C
            sys.stdout.write("\n")
            raise KeyboardInterrupt

        elif ch == "\xe0" or ch == "\x00":  # 方向键/功能键前缀
            _ = msvcrt.getwch()  # 丢弃键码，不处理

        elif ord(ch) >= 32:  # 可打印字符 — 追加到末尾
            buffer.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()


def collect_connection_info(args):
    """交互式收集连接信息。"""
    hostname = socket.gethostname()

    print(C_BOLD("▶ 连接设置"))
    print()

    # 服务器地址
    default_server = args.server or "http://127.0.0.1:8010"
    server = input(f"  服务器地址 [{default_server}]: ").strip()
    if not server:
        server = default_server
    server = server.rstrip("/")

    # 用户名
    username = ""
    while not username:
        username = input("  用户名: ").strip()
        if not username:
            print(C_RED("    用户名不能为空"))

    # 密码 — 用 * 号回显（Windows 风格，而不是 Linux 的无显示）
    password = ""
    while not password:
        password = _get_password_star("  密码: ").strip()
        if not password:
            print(C_RED("    密码不能为空"))

    # 设备名称（预填 hostname，用户可直接编辑或回车接受）
    default_device = args.device or hostname
    device = _edit_with_default(
        f"  设备名称 [{C_GREEN(default_device)}]: ",
        default_device,
    )
    device = device.strip() or default_device

    # 安全模式
    if args.mode:
        mode = args.mode
    else:
        print()
        print(C_BOLD("▶ 安全模式"))
        print(f"  [1] {C_YELLOW('交互模式')} — 每个命令执行前在终端确认 (y/n)")
        print(f"  [2] 自动模式   — 直接执行，免确认")
        choice = input(f"  选择 [1]: ").strip()
        mode = "interactive" if choice != "2" else "auto"
    interactive = (mode == "interactive")

    # 工作目录
    if args.root:
        work_root = args.root
    else:
        work_root = input(f"  工作目录限制 (留空=无限制): ").strip()
    if work_root:
        work_root = os.path.abspath(os.path.expanduser(work_root))
        print(f"  → 工作目录限制为: {C_YELLOW(work_root)}")
    else:
        work_root = ""
        print(f"  → {C_DIM('无目录限制')}")

    config = {
        "server": server,
        "username": username,
        "password": password,
        "device": device,
        "interactive": interactive,
        "work_root": work_root,
    }

    print()
    print(C_BOLD("▶ 配置摘要"))
    print(f"  服务器:   {C_CYAN(config['server'])}")
    print(f"  用户:     {config['username']}")
    print(f"  设备名称: {C_GREEN(config['device'])}")
    print(f"  安全模式: {C_YELLOW('交互确认' if config['interactive'] else '自动执行')}")
    print(f"  工作目录: {config['work_root'] or C_DIM('无限制')}")
    print()
    return config


# ============================================================
#  终端聊天（异步 SSE 流 + 会话管理）
# ============================================================

async def _async_input(prompt: str) -> str:
    """非阻塞的终端输入（在 executor 中运行 input）。"""
    loop = asyncio.get_running_loop()
    return (await loop.run_in_executor(None, input, prompt)).strip()


def _api_sessions(server_url: str, token: str) -> list[dict]:
    """获取会话列表。"""
    try:
        resp = requests.get(f"{server_url}/api/sessions",
                            headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return resp.json().get("sessions", []) if resp.ok else []
    except Exception:
        return []


def _api_delete_round(server_url: str, token: str, session_id: str,
                      round_index: int) -> bool:
    """删除会话中指定轮次的问答。"""
    try:
        resp = requests.delete(
            f"{server_url}/api/sessions/{session_id}/messages/{round_index}",
            headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return resp.ok
    except Exception:
        return False


def _api_delete_session(server_url: str, token: str, session_id: str) -> bool:
    """删除整个会话。"""
    try:
        resp = requests.delete(f"{server_url}/api/sessions/{session_id}",
                               headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return resp.ok
    except Exception:
        return False


def _api_create_session(server_url: str, token: str, name: str) -> dict | None:
    """创建新会话。"""
    try:
        resp = requests.post(f"{server_url}/api/sessions", json={"name": name},
                             headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return resp.json() if resp.ok else None
    except Exception:
        return None

# ============================================================
#  设置管理 — 从服务端同步 thinking_enabled 等设置
# ============================================================
def _api_get_settings(server_url: str, token: str) -> dict:
    """从服务端获取用户设置。"""
    try:
        resp = requests.get(f"{server_url}/api/settings",
                            headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return resp.json() if resp.ok else {}
    except Exception:
        return {}

def _api_save_settings(server_url: str, token: str, updates: dict) -> bool:
    """保存用户设置到服务端。"""
    try:
        resp = requests.put(f"{server_url}/api/settings", json=updates,
                            headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return resp.ok
    except Exception:
        return False

def _show_settings_ui(server_url: str, token: str):
    """交互式设置界面，用户按数字选择要修改的设置项。"""
    settings = _api_get_settings(server_url, token)
    thinking = settings.get("thinking_enabled", True)  # 默认开启

    print()
    print(C_BOLD("  ⚙️  客户端设置"))
    print(C_DIM("  ─" + "─" * 40))
    print(f"    [1] 思考模式 (Thinking):  {C_GREEN('✓ 开启' if thinking else '✗ 关闭')}{_RS}")
    print(f"    [2] 模型名称:              {_DM}{settings.get('model', '(默认)')}{_RS}")
    print(f"    [3] API Base URL:          {_DM}{settings.get('base_url', '(默认)')}{_RS}")
    print(f"    [0] 返回")
    print(C_DIM("  ─" + "─" * 40))
    print()

    while True:
        try:
            choice = input(f"  {_C}请选择要修改的设置 >{_RS} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice == "0":
            return

        if choice == "1":
            current = "开启" if thinking else "关闭"
            ans = input(f"    思考模式当前为 [{_Y}{current}{_RS}]，输入 {_G}y{_RS} 开启 / {_R}n{_RS} 关闭: ").strip().lower()
            if ans in ("y", "yes", "是"):
                thinking = True
                _api_save_settings(server_url, token, {"thinking_enabled": True})
                print(f"    {_G}✓ 思考模式已开启{_RS}")
                print(f"    {_DM}(下次对话生效，AI 会先思考再回答){_RS}")
            elif ans in ("n", "no", "否"):
                thinking = False
                _api_save_settings(server_url, token, {"thinking_enabled": False})
                print(f"    {_Y}✓ 思考模式已关闭{_RS}")
            else:
                print(C_DIM("    未更改"))
            print()
            continue

        if choice == "2":
            current_model = settings.get("model", "(默认)")
            new_model = input(f"    模型名称 [{_DM}{current_model}{_RS}]: ").strip()
            if new_model:
                _api_save_settings(server_url, token, {"model": new_model})
                settings["model"] = new_model
                print(f"    {_G}✓ 模型已更新为: {new_model}{_RS}")
            else:
                print(C_DIM("    未更改"))
            print()
            continue

        if choice == "3":
            current_url = settings.get("base_url", "(默认)")
            new_url = input(f"    API Base URL [{_DM}{current_url}{_RS}]: ").strip()
            if new_url:
                _api_save_settings(server_url, token, {"base_url": new_url})
                settings["base_url"] = new_url
                print(f"    {_G}✓ Base URL 已更新{_RS}")
            else:
                print(C_DIM("    未更改"))
            print()
            continue

        print(C_RED(f"    无效选项: {choice}，请输入 0-3{_RS}"))



async def _sse_stream(server_url: str, token: str, session_id: str | None,
                      message: str):
    """
    SSE 流式聊天 —— 在后台线程中运行 HTTP 请求，
    通过 queue.Queue 将事件推送到异步侧，逐个 yield。
    """
    q: sync_queue.Queue = sync_queue.Queue()
    done = False

    def _fetch():
        nonlocal done
        try:
            payload = {"message": message}
            if session_id:
                payload["session_id"] = session_id
            resp = requests.post(
                f"{server_url}/chat/stream",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                stream=True, timeout=300,
            )
            if resp.status_code != 200:
                q.put({"type": "error", "content": f"服务器错误 ({resp.status_code})"})
                return
            for raw_line in resp.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line.strip()
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        q.put(event)
                    except json.JSONDecodeError:
                        pass
        except requests.exceptions.ConnectionError:
            q.put({"type": "error", "content": f"无法连接到 {server_url}"})
        except Exception as e:
            q.put({"type": "error", "content": f"{type(e).__name__}: {e}"})
        finally:
            done = True

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _fetch)

    # 轮询队列，yield 事件
    while not done or not q.empty():
        try:
            event = q.get_nowait()
            yield event
        except sync_queue.Empty:
            await asyncio.sleep(0.1)

    # 排空剩余事件
    while not q.empty():
        event = q.get_nowait()
        yield event


async def _chat_console(server_url: str, token: str, ws_task: asyncio.Task):
    """
    终端聊天控制台。
    在 WebSocket 连接成功后运行，让用户在终端与 AI 对话。
    """
    await asyncio.sleep(0.3)  # 等待 WS 就绪消息打印完

    # ---- 选择或创建会话 ----
    sessions = _api_sessions(server_url, token)
    chat_sid = None
    chat_name = "终端对话"
    if sessions:
        print()
        print(C_BOLD("  📋 已有会话:"))
        for i, s in enumerate(sessions[:8]):
            name = s.get("name", "未命名")[:28]
            count = s.get("message_count", 0)
            print(f"    {_G}[{i}]{_RS} {name}  {_DM}({count}轮){_RS}")
        print(f"    {_C}[n]{_RS} 新建会话")
        choice = await _async_input(f"  {_C}选择 >{_RS} ")
        if choice == "n":
            name = await _async_input(f"    会话名 (回车跳过): ")
            if not name:
                name = f"终端对话 {datetime.now().strftime('%m/%d %H:%M')}"
            r = _api_create_session(server_url, token, name)
            if r:
                chat_sid = r["session_id"]
                chat_name = name
        elif choice.isdigit() and int(choice) < len(sessions):
            chat_sid = sessions[int(choice)]["session_id"]
            chat_name = sessions[int(choice)].get("name", "终端对话")
        else:
            chat_sid = sessions[0]["session_id"]
            chat_name = sessions[0].get("name", "终端对话")
    else:
        r = _api_create_session(server_url, token, "终端对话")
        if r:
            chat_sid = r["session_id"]

    # ---- 获取当前会话轮数 ----
    msg_count = 0
    for s in (sessions if sessions else []):
        if s.get("session_id") == chat_sid:
            msg_count = s.get("message_count", 0)
            break

    # ---- 聊天提示 ----
    print()
    print(C_DIM("  ─" + "─" * 44))
    print(C_BOLD(f"  💬 开始对话 — 会话: {C_CYAN(chat_name)}"))
    print(C_DIM(f"  输入消息与 AI 对话 | /undo 撤回 | /settings 设置 | /delete 删会话 | /sessions | /new | /exit"))
    print(C_DIM("  ─" + "─" * 44))
    print()

    # ---- 聊天循环 ----
    while True:
        try:
            user_input = await _async_input(f"{_B}You:{_RS} ")
        except (EOFError, KeyboardInterrupt):
            print(f"\n{_Y}  退出聊天模式{_RS}")
            break

        if not user_input:
            continue

        # 特殊命令
        if user_input.startswith("/"):
            cmd = user_input.split(maxsplit=1)[0].lower()
            if cmd in ("/exit", "/quit", "/q"):
                break

            if cmd == "/sessions":
                sessions = _api_sessions(server_url, token)
                if sessions:
                    print(C_DIM("  会话列表:"))
                    for i, s in enumerate(sessions[:10]):
                        print(f"    {_G}[{i}]{_RS} {s.get('name','?')[:30]}  {_DM}{s.get('message_count',0)}轮{_RS}")
                    c = await _async_input(f"  {_C}选择编号 >{_RS} ")
                    if c.isdigit() and int(c) < len(sessions):
                        chat_sid = sessions[int(c)]["session_id"]
                        chat_name = sessions[int(c)].get("name", "终端对话")
                        msg_count = sessions[int(c)].get("message_count", 0)
                        print(f"  {_G}✓{_RS} 已切换到: {chat_name}")
                else:
                    print(C_DIM("  (暂无会话)"))
                continue

            if cmd == "/new":
                name = user_input[5:].strip() if len(user_input) > 4 else ""
                if not name:
                    name = f"终端对话 {datetime.now().strftime('%m/%d %H:%M')}"
                r = _api_create_session(server_url, token, name)
                if r:
                    chat_sid = r["session_id"]
                    chat_name = name
                    msg_count = 0
                    print(f"  {_G}✓{_RS} 已创建: {chat_name}")
                continue

            if cmd == "/undo":
                if msg_count <= 0 or not chat_sid:
                    print(C_DIM("  没有可撤回的消息"))
                else:
                    # 最后一轮的索引 = msg_count - 1
                    ok = _api_delete_round(server_url, token, chat_sid, msg_count - 1)
                    if ok:
                        msg_count -= 1
                        print(f"  {_G}✓{_RS} 已撤回最后一轮对话 (剩余 {msg_count} 轮)")
                    else:
                        print(C_RED("  撤回失败"))
                continue

            if cmd == "/settings":
                _show_settings_ui(server_url, token)
                continue

            if cmd == "/delete":
                if not chat_sid:
                    continue
                confirm = await _async_input(
                    f"  {_Y}确定删除会话「{chat_name}」？(y/N):{_RS} ")
                if confirm.lower() in ("y", "yes", "是"):
                    ok = _api_delete_session(server_url, token, chat_sid)
                    if ok:
                        print(f"  {_G}✓{_RS} 已删除: {chat_name}")
                        # 创建新会话继续
                        r = _api_create_session(server_url, token, "终端对话")
                        if r:
                            chat_sid = r["session_id"]
                            chat_name = "终端对话"
                            msg_count = 0
                            print(f"  {_G}✓{_RS} 已创建新会话")
                        else:
                            print(C_RED("  创建新会话失败，退出聊天"))
                            break
                    else:
                        print(C_RED("  删除失败"))
                else:
                    print(C_DIM("  已取消"))
                continue

            # 不是命令，作为普通消息发送

        # 空行美化
        print()

        # 检查 WebSocket 是否还活着
        if ws_task.done():
            print(C_RED("  WebSocket 连接已断开，无法发送消息"))
            break

        # ---- 发送消息并流式显示 ----
        sys.stdout.write(f"{_C}AI:{_RS} ")
        sys.stdout.flush()

        full_reply = ""
        tool_count = 0
        active_tool = ""

        async for event in _sse_stream(server_url, token, chat_sid, user_input):
            etype = event.get("type", "")

            if etype == "token":
                content = event.get("content", "")
                full_reply += content
                sys.stdout.write(content)
                sys.stdout.flush()

            elif etype == "tool_start":
                tool_count += 1
                name = event.get("name", "?")
                active_tool = name
                args_preview = (event.get("args", "") or "")[:60]
                if full_reply:
                    print()
                print(f"  {_Y}⏳ [{tool_count}] {name}{_RS} {_DM}{args_preview}{_RS}")
                sys.stdout.write(f"{_C}AI:{_RS} " if not full_reply else "   ")
                sys.stdout.flush()

            elif etype == "tool_end":
                name = event.get("name", "?")
                result = (event.get("result", "") or "")[:120].replace("\n", " ")
                print(f"\r  {_G}✓  [{tool_count}] {name}{_RS} → {_DM}{result}{_RS}")

            elif etype == "done":
                if event.get("session_id"):
                    chat_sid = event["session_id"]
                if active_tool:
                    print()
                if event.get("stopped"):
                    print(f"  {_Y}⏹ 已停止{_RS}")

            elif etype == "error":
                print(f"\n  {_R}[错误] {event.get('content', '未知')}{_RS}")

        if full_reply or tool_count:
            msg_count += 1  # 成功收到回复，递增轮数
            print()  # 换行
        else:
            print(C_DIM("(无回复)"))


# ============================================================
#  网络层
# ============================================================

def login_to_server(server_url: str, username: str, password: str) -> str:
    """通过 HTTP POST /api/login 获取 JWT token。"""
    try:
        resp = requests.post(
            f"{server_url}/api/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if resp.status_code == 401:
            raise RuntimeError(f"登录失败: {resp.json().get('detail', '用户名或密码错误')}")
        if resp.status_code != 200:
            raise RuntimeError(f"服务器返回错误 ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        return data["token"]
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"无法连接到服务器 {server_url}，请确认地址和端口是否正确")
    except requests.exceptions.Timeout:
        raise RuntimeError(f"连接服务器超时: {server_url}")
    except Exception as e:
        if "token" not in str(e).lower():
            raise RuntimeError(f"登录失败: {e}")
        raise


async def run_client(config: dict):
    """主事件循环：连接服务器，注册工具，等待并执行工具调用。"""
    server = config["server"]
    token = config["token"]
    device = config["device"]
    interactive = config["interactive"]
    work_root = config["work_root"]

    # WebSocket URL: http:// → ws://   https:// → wss://
    ws_url = server.replace("https://", "wss://").replace("http://", "ws://") + "/ws"

    tool_names = [t["function"]["name"] for t in NODE_TOOL_SCHEMAS]

    print(C_CYAN(f"  ⏳ 正在连接 {ws_url} ..."))

    try:
        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
            # ========== Step 1: 认证 ==========
            await ws.send(json.dumps({
                "type": "auth",
                "token": f"Bearer {token}",
            }))
            auth_resp = await _recv_json(ws, timeout=15)
            if auth_resp.get("type") == "auth_failed":
                raise RuntimeError(f"认证失败: {auth_resp.get('message', '未知')}")
            if auth_resp.get("type") != "auth_ok":
                raise RuntimeError(f"意外的认证响应: {auth_resp}")

            server_username = auth_resp.get("username", "?")

            # ========== Step 2: 注册工具 ==========
            await ws.send(json.dumps({
                "type": "tools_register",
                "node_name": device,
                "tools": NODE_TOOL_SCHEMAS,
                "work_root": work_root,
                "interactive": interactive,
            }))
            reg_resp = await _recv_json(ws, timeout=15)
            if reg_resp.get("type") != "tools_registered":
                raise RuntimeError(f"工具注册失败: {reg_resp}")

            # ========== Step 3: 打印就绪信息 ==========
            # 确保默认启用思考模式（首次连接时设置）
            settings = _api_get_settings(server, token)
            if "thinking_enabled" not in settings:
                _api_save_settings(server, token, {"thinking_enabled": True})
                thinking_default = True
            else:
                thinking_default = settings.get("thinking_enabled", True)

            print()
            print(C_GREEN("  ✓ 已连接到 AI 大脑！"))
            print(f"    用户: {C_CYAN(server_username)}")
            print(f"    节点: {C_GREEN(device)}")
            print(f"    工具: {C_DIM(', '.join(tool_names))}")
            if work_root:
                print(f"    目录: {C_YELLOW(work_root)}")
            print(f"    模式: {C_YELLOW('交互确认' if interactive else '自动执行')}")
            print(f"    思考: {C_GREEN('开启' if thinking_default else '关闭')}{_RS}{_DM} (用 /settings 切换){_RS}")

            # ========== Step 4: 并发运行 WS 工具处理器 + 终端聊天 ==========
            async def _ws_loop():
                """WebSocket 事件循环：处理工具调用、心跳等。"""
                try:
                    while True:
                        msg = await _recv_json(ws, timeout=3600)
                        msg_type = msg.get("type", "")

                        if msg_type == "tool_call":
                            await _handle_tool_call(ws, msg, interactive, work_root)

                        elif msg_type == "pong":
                            pass

                        elif msg_type == "ping":
                            await ws.send(json.dumps({"type": "pong"}))

                        else:
                            print(C_DIM(f"  [未知消息类型] {msg_type}"))
                except asyncio.CancelledError:
                    pass

            ws_task = asyncio.create_task(_ws_loop())
            chat_task = asyncio.create_task(
                _chat_console(server, token, ws_task))

            # 等待任一任务结束，然后取消另一个
            done, pending = await asyncio.wait(
                [ws_task, chat_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    except websockets.exceptions.ConnectionClosed as e:
        print()
        print(C_RED(f"  ✗ 与服务器断开连接 (code={e.code})"))
    except asyncio.CancelledError:
        print()
        print(C_YELLOW("  ⚠ 连接被取消"))
    except KeyboardInterrupt:
        print()
        print(C_YELLOW("  ⚠ 用户中断"))
    except Exception as e:
        print()
        print(C_RED(f"  ✗ 连接错误: {e}"))


async def _recv_json(ws, timeout: float = 60) -> dict:
    """接收 JSON 消息，带超时。"""
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"type": "error", "message": f"JSON 解析失败: {e}"}


async def _handle_tool_call(ws, msg: dict, interactive: bool, work_root: str):
    """处理工具调用请求。"""
    call_id = msg.get("call_id", "?")
    tool_name = msg.get("tool", "?")
    args = msg.get("args", {})

    print()
    print(C_CYAN(f"  🔧 [AI 调用] ") + C_BOLD(f"{tool_name}"))
    args_str = json.dumps(args, ensure_ascii=False)
    if len(args_str) > 120:
        args_str = args_str[:120] + "..."
    print(C_DIM(f"     参数: {args_str}"))

    # 交互模式：确认
    if interactive:
        print(C_YELLOW(f"     执行此操作？"), end=" ", flush=True)
        confirm = input().strip().lower()
        if confirm not in ("y", "yes", "是", ""):
            print(C_DIM("     → 已拒绝"))
            await ws.send(json.dumps({
                "type": "tool_result",
                "call_id": call_id,
                "result": "[已拒绝] 用户在终端拒绝了此操作",
            }))
            return

    # 执行
    try:
        loop = asyncio.get_running_loop()
        if tool_name == "run_shell":
            timeout = args.get("timeout", 60)
            # run_shell 可能很慢，增加线程执行超时
            result = await asyncio.wait_for(
                loop.run_in_executor(None, execute_tool, tool_name, args, work_root),
                timeout=min(timeout + 10, 320),
            )
        else:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, execute_tool, tool_name, args, work_root),
                timeout=60,
            )
    except asyncio.TimeoutError:
        result = "[超时] 工具执行超时"

    # 打印结果摘要
    result_preview = result[:200].replace("\n", " ")
    if len(result) > 200:
        result_preview += "..."
    print(C_DIM(f"     结果: {result_preview}"))

    # 返回结果
    await ws.send(json.dumps({
        "type": "tool_result",
        "call_id": call_id,
        "result": result,
    }, ensure_ascii=False))


# ============================================================
#  主入口
# ============================================================
def parse_args():
    p = argparse.ArgumentParser(
        description="ZeroAgent Client Node — 连接 AI 大脑，让 Agent 在本地工作",
    )
    p.add_argument("--server", "-s", help="服务器地址 (如 http://192.168.1.100:8010)")
    p.add_argument("--root", "-r", help="工作目录限制 (如 ~/projects)")
    p.add_argument("--device", "-d", help="设备名称 (默认自动获取主机名)")
    p.add_argument("--mode", "-m", choices=["interactive", "auto"],
                   help="安全模式: interactive=每次确认, auto=自动执行")
    return p.parse_args()


async def main():
    args = parse_args()

    print_banner()

    try:
        config = collect_connection_info(args)
    except (EOFError, KeyboardInterrupt):
        print()
        print(C_YELLOW("  已取消"))
        return

    # 登录获取 token
    print(C_DIM("  ⏳ 正在登录..."))
    try:
        token = login_to_server(
            config["server"],
            config["username"],
            config["password"],
        )
        config["token"] = token
        print(C_GREEN(f"  ✓ 登录成功"))
    except RuntimeError as e:
        print(C_RED(f"  ✗ {e}"))
        return

    # 运行客户端
    try:
        await run_client(config)
    except KeyboardInterrupt:
        print()
        print(C_YELLOW("  已断开连接"))

    print()
    print(C_DIM("  客户端已退出"))


if __name__ == "__main__":
    asyncio.run(main())
