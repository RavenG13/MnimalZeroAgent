"""
============================================================
  file_tools - 文件读写工具（用户隔离版）
  支持 Agent 读取、编辑项目源代码，实现自我修改能力
  所有路径限定在项目根目录内，防止越权访问
  禁止读取 data/ 下其他用户的数据（仅可访问公共 tools/ 目录）

  v2: 支持行号范围读写、文本匹配替换、search_in_file
============================================================
"""
import os
import re
import difflib
from typing import Optional

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

# 写保护：禁止 AI 修改服务器核心代码和公共 tools/ 目录
WRITE_PROTECTED = [
    os.path.abspath(os.path.join(PROJECT_ROOT, f)) for f in [
        "server.py", "auth.py", "client.py", "migrate_memory.py",
        "restart_service.sh", "requirements.txt", ".gitignore",
        "tools", "static", "client", "data",
    ]
]


def _safe_path(file_path: str) -> str:
    """将相对路径解析为绝对路径，并检查是否在项目根目录内，且不在禁止访问的目录中"""
    if ".." in file_path.split("/") or ".." in file_path.split("\\"):
        raise ValueError(f"禁止使用 '..' 访问上级目录: {file_path}")
    abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, file_path))
    if os.path.commonpath([abs_path, PROJECT_ROOT]) != os.path.abspath(PROJECT_ROOT):
        raise ValueError(f"禁止访问项目目录以外的文件: {file_path}")
    for blocked in BLOCKED_DIRECTORIES:
        if os.path.commonpath([abs_path, blocked]) == os.path.abspath(blocked):
            raise ValueError(f"禁止访问其他用户的私有数据: {file_path}")
    return abs_path


def _read_file_lines(abs_path: str) -> list[str]:
    """读取文件并返回行列表（每行含换行符）"""
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def _format_lines_with_numbers(lines: list[str], start: int = 1) -> str:
    """给行列表加上行号，格式: '  1: content'"""
    width = len(str(start + len(lines) - 1))
    result = []
    for i, line in enumerate(lines):
        num = start + i
        result.append(f"{num:>{width}}: {line.rstrip()}")
    return "\n".join(result)


def _make_diff_summary(old_lines: list[str], new_lines: list[str], start_line: int = 0) -> str:
    """生成 diff 摘要"""
    if start_line > 0:
        # 局部替换：只对比替换区域
        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile="old", tofile="new",
            lineterm=""
        ))
    else:
        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile="old", tofile="new",
            lineterm=""
        ))

    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

    # 取前 20 行 diff 作为预览
    preview_lines = diff[:20]
    preview = "\n".join(preview_lines)
    if len(diff) > 20:
        preview += f"\n... (还有 {len(diff) - 20} 行)"

    return added, removed, preview


# ============================================================
#  工具 Schema 定义
# ============================================================

file_tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取项目中的文件内容。支持两种模式：\n"
                "1. 整文件模式：不传 start_line/end_line，返回整个文件（大文件自动截断）\n"
                "2. 行号范围模式：传 start_line 和 end_line，返回指定行范围（含首尾，带行号）\n"
                "路径相对于项目根目录，如 'server.py' 或 'static/chat.html'。\n"
                "提示：先用不带行号的方式读取概览，再用行号范围精准读取需要修改的区域。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "相对于项目根目录的文件路径",
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
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "写入或修改项目中的文件。支持三种模式：\n"
                "1. 整文件模式：只传 content，覆盖整个文件（大文件禁止使用，超过30KB请用局部模式）\n"
                "2. 行号替换模式：传 content + start_line + end_line，替换指定行范围\n"
                "3. 文本匹配模式：传 old_text + new_text，按内容精确匹配替换（推荐，无需知道行号）\n"
                "文本匹配模式下 old_text 必须与文件中内容完全一致（含缩进空格），否则匹配失败。\n"
                "返回 diff 摘要（新增/删除行数和变更预览），而非完整文件内容。\n"
                "⚠ 写保护：禁止修改 server.py / tools/ / static/ / client/ 等服务器核心代码。"
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
                        "description": "文本匹配模式：old_text 应匹配的次数（默认1）。匹配次数不符则拒绝修改",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_file",
            "description": (
                "在文件中搜索文本或正则表达式，返回匹配的行号和内容。\n"
                "用于定位要修改的代码位置，然后配合 read_file(行号范围) 查看上下文，\n"
                "再用 write_file(行号模式或文本匹配模式) 精准修改。\n"
                "支持参数：pattern（搜索词或正则）、is_regex（是否正则，默认false）、case_sensitive（大小写，默认false）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "相对于项目根目录的文件路径",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "搜索关键词或正则表达式",
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "是否作为正则表达式处理（默认 false，纯文本匹配）",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写（默认 false，不区分）",
                    },
                },
                "required": ["file_path", "pattern"],
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


# ============================================================
#  工具实现
# ============================================================

def read_file(file_path: str, start_line: int = 0, end_line: int = 0) -> str:
    """
    读取项目文件内容。
    - 整文件模式：start_line=0, end_line=0，返回完整内容（大文件截断到前500行）
    - 行号范围模式：start_line>0, end_line>0，返回指定行范围（带行号）
    """
    try:
        abs_path = _safe_path(file_path)
        if not os.path.isfile(abs_path):
            return f"[错误] 文件不存在: {file_path}"
        if abs_path.endswith(".db"):
            return f"[拒绝] 禁止直接读取数据库文件: {file_path}"

        size = os.path.getsize(abs_path)
        if size > 1024 * 1024:
            return f"[错误] 文件过大 ({size} 字节)，请使用行号范围读取"

        lines = _read_file_lines(abs_path)
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
                f"[文件] {file_path} 第{start_line}-{end_line}行（共{total_lines}行）\n"
                f"{'='*50}\n{numbered}"
            )

        # 整文件模式
        if total_lines > 500:
            preview = _format_lines_with_numbers(lines[:500])
            return (
                f"[文件] {file_path}（共{total_lines}行，文件较大，仅显示前500行）\n"
                f"提示：请用 start_line/end_line 读取特定区域\n"
                f"{'='*50}\n{preview}\n{'='*50}\n... 剩余 {total_lines - 500} 行未显示"
            )

        numbered = _format_lines_with_numbers(lines)
        return f"[文件] {file_path}（共{total_lines}行）\n{'='*50}\n{numbered}"

    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 读取文件失败: {e}"


def write_file(
    file_path: str,
    content: Optional[str] = None,
    start_line: int = 0,
    end_line: int = 0,
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    expected_count: int = 1,
) -> str:
    """
    写入或修改项目文件。支持三种模式：
    1. 整文件模式：只传 content
    2. 行号替换模式：传 content + start_line + end_line
    3. 文本匹配模式：传 old_text + new_text
    """
    try:
        abs_path = _safe_path(file_path)

        # 写保护检查
        for protected in WRITE_PROTECTED:
            if abs_path == protected or abs_path.startswith(protected + os.sep):
                return (
                    f"[写保护] 禁止修改服务器核心文件/目录: {file_path}\n"
                    f"AI 只能通过 data/user_tools/<用户名>/ 创建和修改自定义工具。\n"
                    f"如需修改服务器代码，请人工操作。"
                )

        # 读取现有文件内容（如果存在）
        old_lines: list[str] = []
        file_exists = os.path.isfile(abs_path)
        if file_exists:
            old_lines = _read_file_lines(abs_path)

        # ---- 模式3：文本匹配替换 ----
        if old_text is not None:
            if new_text is None:
                return "[错误] 文本匹配模式需要同时提供 old_text 和 new_text"

            # 在整个文件内容中搜索
            full_content = "".join(old_lines)
            match_count = full_content.count(old_text)

            if match_count == 0:
                # 尝试模糊提示
                snippet = old_text[:80].replace("\n", "\\n")
                return f"[匹配失败] 文件中未找到 old_text: '{snippet}'...请检查文本是否完全一致（含缩进空格）"

            if match_count != expected_count:
                return (
                    f"[匹配次数不符] old_text 在文件中出现 {match_count} 次，"
                    f"但 expected_count={expected_count}。\n"
                    f"请调整 old_text 使其唯一，或设置 expected_count={match_count}"
                )

            # 执行替换
            new_content = full_content.replace(old_text, new_text, 1 if expected_count == 1 else expected_count)

            # 计算 diff
            new_lines_list = new_content.splitlines(keepends=True)
            added, removed, preview = _make_diff_summary(old_lines, new_lines_list)

            # 安全阀：变更超过 40% 警告
            total_old = len(old_lines)
            if total_old > 0 and (added + removed) / total_old > 0.4:
                return (
                    f"[警告] 变更比例过大：新增{added}行，删除{removed}行，"
                    f"占原文件 {(added+removed)/total_old*100:.0f}%。\n"
                    f"请缩小修改范围或使用行号替换模式。\n"
                    f"diff 预览:\n{preview}"
                )

            # 写入
            _atomic_write(abs_path, new_content)

            return (
                f"[修改成功] {file_path}（文本匹配模式）\n"
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
                return f"[错误] 文件不存在，无法使用行号替换: {file_path}"

            total_lines = len(old_lines)
            if start_line < 1:
                start_line = 1
            if end_line < 1 or end_line > total_lines:
                end_line = total_lines
            if start_line > end_line:
                return f"[错误] 起始行 {start_line} 大于结束行 {end_line}"

            # 确保 content 以换行结尾
            if content and not content.endswith("\n"):
                content += "\n"

            # 拼接：保留前 + 新内容 + 保留后
            new_lines_list = old_lines[:start_line - 1] + [content] + old_lines[end_line:]
            new_content = "".join(new_lines_list)

            # 计算 diff
            added, removed, preview = _make_diff_summary(
                old_lines[start_line - 1 : end_line],
                [content],
                start_line
            )

            # 写入
            _atomic_write(abs_path, new_content)

            return (
                f"[修改成功] {file_path}（第{start_line}-{end_line}行已替换）\n"
                f"+ 新增: {added} 行\n"
                f"- 删除: {removed} 行\n"
                f"{'='*50}\n{preview}"
            )

        # ---- 模式1：整文件写入 ----
        if content is None:
            return "[错误] 需要提供 content、old_text/new_text 或 start_line/end_line"

        # 安全阀：大文件禁止整文件覆盖
        if file_exists:
            old_size = len("".join(old_lines))
            new_size = len(content)
            if old_size > 30 * 1024:
                return (
                    f"[拒绝] 文件 {file_path} 为 {old_size//1024}KB，超过30KB。\n"
                    f"禁止整文件覆盖，请使用行号替换模式或文本匹配模式。"
                )
            # 检查内容缩水
            if old_size > 0 and new_size < old_size * 0.3:
                return (
                    f"[拒绝] 新内容仅为原文件的 {new_size/old_size*100:.0f}%，可能内容丢失。\n"
                    f"原文件: {old_size} 字节, 新内容: {new_size} 字节。\n"
                    f"如确认要覆盖，请使用行号替换模式。"
                )

        # 计算 diff
        new_lines_list = content.splitlines(keepends=True)
        added, removed, preview = _make_diff_summary(old_lines, new_lines_list)

        # 确保目录存在
        os.makedirs(os.path.dirname(abs_path) if os.path.dirname(abs_path) else ".", exist_ok=True)

        # 写入
        _atomic_write(abs_path, content)

        # 备份（如果有旧内容且没有在原子写入中处理）
        backup_info = ""
        if file_exists:
            backup_path = abs_path + ".bak"
            try:
                with open(backup_path, "w", encoding="utf-8") as f:
                    f.write("".join(old_lines))
                backup_info = f"，原文件已备份至 {file_path}.bak"
            except Exception:
                pass

        return (
            f"[写入成功] {file_path}（整文件模式）共 {len(content)} 字符{backup_info}\n"
            f"+ 新增: {added} 行\n"
            f"- 删除: {removed} 行\n"
            f"{'='*50}\n{preview}"
        )

    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 写入文件失败: {e}"


def search_in_file(
    file_path: str,
    pattern: str,
    is_regex: bool = False,
    case_sensitive: bool = False,
) -> str:
    """
    在文件中搜索文本或正则表达式。
    返回匹配的行号、行内容和匹配位置。
    """
    try:
        abs_path = _safe_path(file_path)
        if not os.path.isfile(abs_path):
            return f"[错误] 文件不存在: {file_path}"

        size = os.path.getsize(abs_path)
        if size > 1024 * 1024:
            return f"[错误] 文件过大 ({size} 字节)，请缩小搜索范围"

        lines = _read_file_lines(abs_path)
        total_lines = len(lines)

        # 编译正则
        flags = 0 if case_sensitive else re.IGNORECASE
        if is_regex:
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return f"[错误] 正则表达式无效: {e}"
        else:
            # 纯文本：转义特殊字符
            escaped = re.escape(pattern)
            regex = re.compile(escaped, flags)

        # 搜索
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
                f"[搜索结果] {file_path}\n"
                f"模式: {'正则' if is_regex else '文本'} '{pattern}'\n"
                f"结果: 未找到匹配项"
            )

        # 格式化结果（最多显示 50 条）
        result_lines = []
        show_matches = matches[:50]
        for m in show_matches:
            # 高亮匹配部分（简单实现）
            line_content = m["content"]
            result_lines.append(f"  第{m['line']:>5}行: {line_content}")

        result = "\n".join(result_lines)
        extra = f"\n... 还有 {len(matches) - 50} 条匹配" if len(matches) > 50 else ""

        return (
            f"[搜索结果] {file_path}\n"
            f"模式: {'正则' if is_regex else '文本'} '{pattern}'\n"
            f"共找到 {len(matches)} 处匹配（文件共{total_lines}行）\n"
            f"{'='*50}\n{result}{extra}"
        )

    except ValueError as e:
        return f"[安全限制] {e}"
    except Exception as e:
        return f"[错误] 搜索失败: {e}"


def list_files(directory: str = "") -> str:
    """列出目录内容（仅限公共目录）"""
    try:
        abs_path = _safe_path(directory) if directory else PROJECT_ROOT
        if not os.path.isdir(abs_path):
            return f"[错误] 目录不存在: {directory}"
        items = []
        for name in sorted(os.listdir(abs_path)):
            full = os.path.join(abs_path, name)
            if name.endswith(".bak") or name == "__pycache__" or name.startswith("."):
                continue
            if abs_path == PROJECT_ROOT and name == "data":
                items.append(f"  [DIR] data (用户私有数据，不可访问)")
                continue
            if "data" in abs_path.split(os.sep) and abs_path != os.path.join(PROJECT_ROOT, "data"):
                continue
            icon = "[DIR]" if os.path.isdir(full) else "[FILE]"
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


def _atomic_write(abs_path: str, content: str) -> None:
    """原子写入：先写临时文件，再替换原文件"""
    import tempfile

    dir_name = os.path.dirname(abs_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    # 写入临时文件
    fd, tmp_path = tempfile.mkstemp(dir=dir_name or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # 原子替换
        os.replace(tmp_path, abs_path)
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
