"""
============================================================
  node_tools.py - ZeroAgent Client 节点工具定义
  暴露给 AI Agent 的本地工具：文件操作 + Shell 执行 + opencode
  支持 --root 工作目录限制，防止越权访问

  v2: 支持行号范围读写、文本匹配替换、search_in_file
  v3: 新增 run_opencode 工具
============================================================
"""
import os
import re
import subprocess
import platform
import tempfile
import difflib
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
                "读取客户端本地文件内容。支持两种模式：\n"
                "1. 整文件模式：不传 start_line/end_line，返回整个文件\n"
                "2. 行号范围模式：传 start_line 和 end_line，返回指定行范围（带行号）"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对路径或相对于工作目录的路径）",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（从1开始，包含）。不传或传0表示从文件开头读取",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（从1开始，包含）。不传或传0表示读到文件末尾",
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
                "将内容写入客户端本地文件。支持三种模式：\n"
                "1. 整文件模式：只传 content，覆盖整个文件\n"
                "2. 行号替换模式：传 content + start_line + end_line，替换指定行范围\n"
                "3. 文本匹配模式：传 old_text + new_text，按内容精确匹配替换"
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
                        "description": "要写入的内容（整文件模式或行号替换模式使用）",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "行号替换模式：起始行号（从1开始，包含）",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "行号替换模式：结束行号（从1开始，包含）",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "文本匹配模式：要替换的旧文本（必须与文件中内容完全一致）",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "文本匹配模式：替换后的新文本",
                    },
                    "expected_count": {
                        "type": "integer",
                        "description": "文本匹配模式：old_text 应匹配的次数（默认1）",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_file",
            "description": (
                "在客户端本地文件中搜索文本或正则表达式，返回匹配的行号和内容。\n"
                "用于定位要修改的代码位置。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对路径或相对于工作目录的路径）",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "搜索关键词或正则表达式",
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "是否作为正则表达式处理（默认 false）",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写（默认 false）",
                    },
                },
                "required": ["path", "pattern"],
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
    {
        "type": "function",
        "function": {
            "name": "run_opencode",
            "description": (
                "调用本地 opencode AI编程助手执行任务。\n"
                "opencode 是一个强大的 AI 编程工具，具备代码读写、文件搜索、命令执行等能力。\n"
                "适用于需要复杂代码修改、多文件重构、项目级任务的场景。\n"
                "返回 opencode 的执行结果文本。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "要发送给 opencode 的任务描述（自然语言）",
                    },
                    "model": {
                        "type": "string",
                        "description": "指定模型，格式为 provider/model（如 anthropic/claude-sonnet-4-20250514）。留空使用默认模型",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "继续之前的会话 ID。留空则创建新会话",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "opencode 的工作目录（绝对路径或相对于客户端工作目录的路径）。留空使用客户端默认工作目录",
                    },
                    "auto": {
                        "type": "boolean",
                        "description": "是否自动批准权限（危险！仅限可信环境）。默认 false",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 300，最大 1800（30分钟）",
                    },
                },
                "required": ["message"],
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
        if not full.startswith(root + os.sep) and full != root:
            raise PermissionError(
                f"路径越界！允许的根目录是 {root}，"
                f"请求的路径解析为 {full}"
            )
        return full
    else:
        return os.path.abspath(os.path.expanduser(path))


def _read_file_lines(full_path: str) -> list[str]:
    """读取文件并返回行列表（每行含换行符）"""
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def _format_lines_with_numbers(lines: list[str], start: int = 1) -> str:
    """给行列表加上行号，格式: '  1: content'"""
    width = len(str(start + len(lines) - 1)) if lines else 1
    result = []
    for i, line in enumerate(lines):
        num = start + i
        result.append(f"{num:>{width}}: {line.rstrip()}")
    return "\n".join(result)


def _make_diff_summary(old_lines: list[str], new_lines: list[str]) -> tuple[int, int, str]:
    """生成 diff 摘要，返回 (added, removed, preview)"""
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="old", tofile="new",
        lineterm=""
    ))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    preview_lines = diff[:20]
    preview = "\n".join(preview_lines)
    if len(diff) > 20:
        preview += f"\n... (还有 {len(diff) - 20} 行)"
    return added, removed, preview


def _atomic_write(full_path: str, content: str) -> None:
    """原子写入：先写临时文件，再替换原文件"""
    dir_name = os.path.dirname(full_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, full_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_file(
    path: str,
    root: Optional[str] = None,
    start_line: int = 0,
    end_line: int = 0,
) -> str:
    """
    读取本地文件内容。
    - 整文件模式：start_line=0, end_line=0
    - 行号范围模式：start_line>0, end_line>0
    """
    full = _resolve_path(path, root)
    if not os.path.exists(full):
        return f"[错误] 文件不存在: {full}"
    if os.path.isdir(full):
        return f"[错误] 路径是目录而非文件: {full}"
    try:
        size = os.path.getsize(full)
        if size > 1024 * 1024 and start_line == 0 and end_line == 0:
            return f"[错误] 文件过大 ({size/1024/1024:.1f} MB)，请使用行号范围读取"

        lines = _read_file_lines(full)
        total_lines = len(lines)

        # 行号范围模式
        if start_line > 0 or end_line > 0:
            if start_line < 1:
                start_line = 1
            if end_line < 1 or end_line > total_lines:
                end_line = total_lines
            if start_line > end_line:
                return f"[错误] 起始行 {start_line} 大于结束行 {end_line}"

            selected = lines[start_line - 1 : end_line]
            numbered = _format_lines_with_numbers(selected, start_line)
            return (
                f"[文件] {path} 第{start_line}-{end_line}行（共{total_lines}行）\n"
                f"{'='*50}\n{numbered}"
            )

        # 整文件模式
        if total_lines > 500:
            preview = _format_lines_with_numbers(lines[:500])
            return (
                f"[文件] {path}（共{total_lines}行，仅显示前500行）\n"
                f"提示：请用 start_line/end_line 读取特定区域\n"
                f"{'='*50}\n{preview}\n{'='*50}\n... 剩余 {total_lines - 500} 行未显示"
            )

        numbered = _format_lines_with_numbers(lines)
        return f"[文件] {path}（共{total_lines}行）\n{'='*50}\n{numbered}"

    except Exception as e:
        return f"[错误] 读取文件失败: {e}"


def write_file(
    path: str,
    content: Optional[str] = None,
    root: Optional[str] = None,
    start_line: int = 0,
    end_line: int = 0,
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    expected_count: int = 1,
) -> str:
    """
    写入本地文件。支持三种模式：
    1. 整文件模式：只传 content
    2. 行号替换模式：传 content + start_line + end_line
    3. 文本匹配模式：传 old_text + new_text
    """
    full = _resolve_path(path, root)
    try:
        # 读取现有文件
        old_lines: list[str] = []
        file_exists = os.path.isfile(full)
        if file_exists:
            old_lines = _read_file_lines(full)

        # ---- 模式3：文本匹配替换 ----
        if old_text is not None:
            if new_text is None:
                return "[错误] 文本匹配模式需要同时提供 old_text 和 new_text"

            full_content = "".join(old_lines)
            match_count = full_content.count(old_text)

            if match_count == 0:
                snippet = old_text[:80].replace("\n", "\\n")
                return f"[匹配失败] 文件中未找到 old_text: '{snippet}'...请检查文本是否完全一致"

            if match_count != expected_count:
                return (
                    f"[匹配次数不符] old_text 出现 {match_count} 次，"
                    f"expected_count={expected_count}。"
                    f"请调整 old_text 或设置 expected_count={match_count}"
                )

            new_content = full_content.replace(old_text, new_text, 1 if expected_count == 1 else expected_count)
            new_lines_list = new_content.splitlines(keepends=True)
            added, removed, preview = _make_diff_summary(old_lines, new_lines_list)

            _atomic_write(full, new_content)

            return (
                f"[修改成功] {path}（文本匹配模式）\n"
                f"匹配次数: {match_count}\n"
                f"+ 新增: {added} 行\n"
                f"- 删除: {removed} 行\n"
                f"{'='*50}\n{preview}"
            )

        # ---- 模式2：行号范围替换 ----
        if start_line > 0 or end_line > 0:
            if content is None:
                return "[错误] 行号替换模式需要提供 content"

            if not file_exists:
                return f"[错误] 文件不存在，无法使用行号替换: {path}"

            total_lines = len(old_lines)
            if start_line < 1:
                start_line = 1
            if end_line < 1 or end_line > total_lines:
                end_line = total_lines
            if start_line > end_line:
                return f"[错误] 起始行 {start_line} 大于结束行 {end_line}"

            if content and not content.endswith("\n"):
                content += "\n"

            new_lines_list = old_lines[:start_line - 1] + [content] + old_lines[end_line:]
            new_content = "".join(new_lines_list)

            added, removed, preview = _make_diff_summary(
                old_lines[start_line - 1 : end_line],
                [content]
            )

            _atomic_write(full, new_content)

            return (
                f"[修改成功] {path}（第{start_line}-{end_line}行已替换）\n"
                f"+ 新增: {added} 行\n"
                f"- 删除: {removed} 行\n"
                f"{'='*50}\n{preview}"
            )

        # ---- 模式1：整文件写入 ----
        if content is None:
            return "[错误] 需要提供 content、old_text/new_text 或 start_line/end_line"

        # 安全阀
        if file_exists:
            old_size = len("".join(old_lines))
            new_size = len(content)
            if old_size > 30 * 1024:
                return (
                    f"[拒绝] 文件 {path} 为 {old_size//1024}KB，超过30KB。"
                    f"请使用行号替换模式或文本匹配模式。"
                )
            if old_size > 0 and new_size < old_size * 0.3:
                return (
                    f"[拒绝] 新内容仅为原文件的 {new_size/old_size*100:.0f}%，可能内容丢失。"
                    f"如确认覆盖，请使用行号替换模式。"
                )

        new_lines_list = content.splitlines(keepends=True)
        added, removed, preview = _make_diff_summary(old_lines, new_lines_list)

        os.makedirs(os.path.dirname(full) if os.path.dirname(full) else ".", exist_ok=True)

        # 备份
        backup_info = ""
        if file_exists:
            backup_path = full + ".bak"
            try:
                with open(backup_path, "w", encoding="utf-8") as f:
                    f.write("".join(old_lines))
                backup_info = f"，原文件已备份"
            except Exception:
                pass

        _atomic_write(full, content)

        return (
            f"[写入成功] {path}（整文件模式）共 {len(content)} 字符{backup_info}\n"
            f"+ 新增: {added} 行\n"
            f"- 删除: {removed} 行\n"
            f"{'='*50}\n{preview}"
        )

    except PermissionError as e:
        return f"[错误] 权限不足: {e}"
    except Exception as e:
        return f"[错误] 写入文件失败: {e}"


def search_in_file(
    path: str,
    pattern: str,
    root: Optional[str] = None,
    is_regex: bool = False,
    case_sensitive: bool = False,
) -> str:
    """在本地文件中搜索文本或正则表达式"""
    full = _resolve_path(path, root)
    if not os.path.isfile(full):
        return f"[错误] 文件不存在: {full}"
    try:
        size = os.path.getsize(full)
        if size > 1024 * 1024:
            return f"[错误] 文件过大 ({size/1024/1024:.1f} MB)，请缩小搜索范围"

        lines = _read_file_lines(full)
        total_lines = len(lines)

        flags = 0 if case_sensitive else re.IGNORECASE
        if is_regex:
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return f"[错误] 正则表达式无效: {e}"
        else:
            escaped = re.escape(pattern)
            regex = re.compile(escaped, flags)

        matches = []
        for i, line in enumerate(lines):
            m = regex.search(line)
            if m:
                matches.append({
                    "line": i + 1,
                    "content": line.rstrip(),
                    "start": m.start(),
                    "end": m.end(),
                })

        if not matches:
            return (
                f"[搜索结果] {path}\n"
                f"模式: {'正则' if is_regex else '文本'} '{pattern}'\n"
                f"结果: 未找到匹配项"
            )

        result_lines = []
        show_matches = matches[:50]
        for m in show_matches:
            result_lines.append(f"  第{m['line']:>5}行: {m['content']}")

        result = "\n".join(result_lines)
        extra = f"\n... 还有 {len(matches) - 50} 条匹配" if len(matches) > 50 else ""

        return (
            f"[搜索结果] {path}\n"
            f"模式: {'正则' if is_regex else '文本'} '{pattern}'\n"
            f"共找到 {len(matches)} 处匹配（文件共{total_lines}行）\n"
            f"{'='*50}\n{result}{extra}"
        )

    except Exception as e:
        return f"[错误] 搜索失败: {e}"


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
            prefix = "[DIR]" if os.path.isdir(item_path) else "[FILE]"
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
    timeout = min(max(timeout, 1), 300)
    cwd = os.path.abspath(os.path.expanduser(root)) if root else None

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


def run_opencode(
    message: str,
    root: Optional[str] = None,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    auto: bool = False,
    timeout: int = 300,
) -> str:
    """
    调用本地 opencode AI编程助手执行任务。
    opencode 是一个强大的 AI 编程工具，具备代码读写、文件搜索、命令执行等能力。
    """
    if not message or not message.strip():
        return "[错误] message 不能为空"

    timeout = min(max(timeout, 30), 1800)  # 30秒到30分钟

    # 确定工作目录
    work_dir = None
    if cwd:
        # 用户指定的 cwd，相对于 root 或绝对路径
        if root:
            if os.path.isabs(cwd):
                work_dir = os.path.abspath(os.path.expanduser(cwd))
            else:
                work_dir = os.path.abspath(os.path.join(root, cwd))
        else:
            work_dir = os.path.abspath(os.path.expanduser(cwd))
    elif root:
        work_dir = os.path.abspath(os.path.expanduser(root))

    # Windows 下使用 shell=True 以支持 npm 脚本/bat/cmd
    use_shell = platform.system() == "Windows"

    # 构建 opencode 命令
    # 对 message 进行引号包裹以处理空格和特殊字符
    cmd_parts = ["opencode", "run", f'"{message}"']

    if model:
        cmd_parts.extend(["--model", f'"{model}"'])

    if session_id:
        cmd_parts.extend(["--session", f'"{session_id}"'])

    if auto:
        cmd_parts.append("--auto")

    # Windows shell 模式下合并为字符串
    if use_shell:
        cmd = " ".join(cmd_parts)
    else:
        cmd = cmd_parts

    try:
        # 检查 opencode 是否可用
        check = subprocess.run(
            "opencode --version" if use_shell else ["opencode", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=use_shell,
        )
        if check.returncode != 0:
            return "[错误] opencode 未安装或不在 PATH 中。请先安装: npm install -g opencode"
    except FileNotFoundError:
        return "[错误] opencode 未找到。请先安装: npm install -g opencode"
    except Exception:
        pass

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=work_dir,
            shell=use_shell,
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

        # 截断过长输出
        if len(output) > 50000:
            output = output[:50000] + f"\n\n... [截断] 输出过长（{len(output)} 字符），仅显示前 50000 字符"

        return output.rstrip()

    except subprocess.TimeoutExpired:
        return f"[超时] opencode 在 {timeout}s 后未完成，已终止"
    except Exception as e:
        return f"[错误] 执行 opencode 失败: {e}"


# ============================================================
#  工具调度器
# ============================================================

NODE_FUNC_MAP = {
    "read_file": read_file,
    "write_file": write_file,
    "search_in_file": search_in_file,
    "list_files": list_files,
    "run_shell": run_shell,
    "get_system_info": get_system_info,
    "run_opencode": run_opencode,
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

    # 注入 root（文件相关操作和 opencode 需要）
    if name in ("read_file", "write_file", "search_in_file", "list_files", "get_system_info", "run_opencode"):
        args = {**args, "root": root}
    elif name == "run_shell":
        args = {**args, "root": root}

    try:
        return func(**args)
    except TypeError as e:
        return f"[错误] 工具参数错误 ({name}): {e}"
    except Exception as e:
        return f"[错误] 工具执行异常 ({name}): {e}"
