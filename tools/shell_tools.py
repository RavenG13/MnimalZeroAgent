"""
============================================================
  shell_tools - 命令执行工具（跨平台兼容）
  支持 Agent 执行系统命令，实现自我修改、重启服务等能力
  包含安全检查，拒绝明显危险的操作
============================================================
"""
import os
import subprocess
import platform

# 项目根目录
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IS_WINDOWS = platform.system() == "Windows"

# 危险命令黑名单（检测到则拒绝执行）
DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs.",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/sda",
    "format c:",
    "del /f /s /q C:",
    "shutdown /s",
    "shutdown -h now",
    "halt",
    "poweroff",
    "crontab -r",
    "chmod 777 /",
]


shell_tools = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "在项目目录下执行命令，返回标准输出和错误输出。\n"
                "⚠️ 重要规则：\n"
                " - 每轮对话尽量只调用一次此工具，拿到结果后先分析再决定下一步\n"
                " - 不要连续多次调用同一个失败的命令，先分析错误原因\n"
                " - 超时默认30秒，预计较慢的操作请传入更大的 timeout 值\n"
                " - 命令在项目根目录（cloud-agent）下执行\n"
                "用法示例：\n"
                " - Windows:   dir、type server.py、python -c \"print('test')\"\n"
                " - Linux/Mac: ls、cat server.py、python3 -c \"print('test')\"\n"
                " - 安装依赖: pip install xxx\n"
                " - 检查语法: python -m py_compile server.py\n"
                " - 查看进程: tasklist (Windows) 或 ps aux (Linux)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "要执行的命令字符串。注意当前运行环境为 "
                            + ("Windows (cmd)" if IS_WINDOWS else "Linux/Mac (bash)") + "。"
                            "例: 'dir' 或 'python -c \"print(1+1)\"'"
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认30，最大300",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": (
                "提示用户如何重启服务以应用代码更改。\n"
                "由于服务进程正在运行中，无法从内部替换自己。"
                "此工具会给出当前环境下的重启方法。\n"
                "⚠️ 不要反复调用此工具，调用一次告知用户即可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def _is_dangerous(command: str) -> str:
    """检查命令是否包含危险操作，返回空字符串表示安全"""
    cmd_lower = command.lower().replace(" ", "")
    for pattern in DANGEROUS_PATTERNS:
        if pattern.replace(" ", "") in cmd_lower:
            return pattern
    return ""


def run_shell(command: str, timeout: int = 30) -> str:
    """在项目根目录下执行 shell 命令（跨平台）"""
    danger = _is_dangerous(command)
    if danger:
        return f"[REJECTED] Dangerous pattern '{danger}' detected. Command blocked."

    if timeout > 300:
        timeout = 300
    if timeout < 1:
        timeout = 30

    try:
        # 跨平台命令执行
        result = subprocess.run(
            command,
            shell=True,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )

        out = f"[CMD] {command}\n[EXIT] {result.returncode}\n"

        if result.stdout:
            stdout = result.stdout
            if len(stdout) > 4000:
                stdout = stdout[:4000] + f"\n... (truncated, total {len(result.stdout)} chars)"
            out += f"[STDOUT]\n{stdout}\n"

        if result.stderr:
            stderr = result.stderr
            if len(stderr) > 2000:
                stderr = stderr[:2000] + f"\n... (truncated, total {len(result.stderr)} chars)"
            out += f"[STDERR]\n{stderr}"

        if not result.stdout and not result.stderr:
            out += "(no output)"

        return out.strip()
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] Command exceeded {timeout}s: {command}\nHint: increase timeout parameter or simplify the command."
    except FileNotFoundError:
        exe = command.split()[0] if command.split() else command
        return f"[ERROR] Program not found: '{exe}'\nHint: check spelling or install it first with pip/npm/etc."
    except Exception as e:
        return f"[ERROR] Command failed: {type(e).__name__}: {e}\nHint: check command syntax and try again."


def restart_service() -> str:
    """提示如何重启服务（跨平台）"""
    if IS_WINDOWS:
        return (
            "[INFO] Code changes have been made. To apply them:\n"
            "  1. Press Ctrl+C in the terminal running server.py\n"
            "  2. Run: python server.py\n"
            "  Or simply close and re-run the terminal.\n"
            f"  Working directory: {PROJECT_ROOT}"
        )
    else:
        # 检查是否有 systemd 服务
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "zeroagent"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                r2 = subprocess.run(
                    ["systemctl", "restart", "zeroagent"],
                    capture_output=True, text=True, timeout=10
                )
                if r2.returncode == 0:
                    return "[OK] zeroagent service restarted via systemctl. Changes are now live."
                return f"[WARN] systemctl restart failed: {r2.stderr.strip()}\nRestart manually: sudo systemctl restart zeroagent"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return (
            "[INFO] Code changes saved. To apply:\n"
            "  1. Press Ctrl+C to stop the server\n"
            "  2. Run: python3 server.py\n"
            f"  Working directory: {PROJECT_ROOT}"
        )
